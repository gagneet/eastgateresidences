# @featuretrace:scoped-capability-access — Persist authorisation decisions to the audit chain.
# Layer: service
# Data flow: decide() -> Decision -> audit_scope.should_audit() -> queue -> single writer
#            -> core.audit_events, hash-chained per tenant (building-scoped).
# Related: backend/services/capability_registry.py
#          backend/services/audit_scope.py
#          docs/security/audit_scope_statutory_research_2026_08_24.md
# Tests: tests/backend/test_authorisation_audit.py

"""Record authorisation decisions durably, without serialising every request.

## The problem this solves

``decide()`` has produced a reasoned ``Decision`` since Phase 3, and the only
record of it is a log line — neither tamper-evident nor retained for seven years.
"Why was this owner denied the levy report on 14 August" is unanswerable once
the log rotates.

``core.audit_events`` is the right home: it exists, it is hash-chained
(``prev_event_hash``/``event_hash``), and it is RLS-protected. GAP-SEC-003 is
explicit that no new audit table should be created.

## Why this is not just an INSERT

A hash chain has a single tip. Every writer must read the previous hash, compute
its own, and append — so concurrent writers contend on the tip, and the naive
implementation puts every guarded request in a queue behind it. That is
survivable at today's 27 guarded routes and not at the ~1400 Phase 5 will
produce.

So the chain has **exactly one writer**. Request handlers enqueue and move on;
a single background task drains the queue, computes the chain in order, and
writes in batches. No contention, no reordering, and the per-request cost is an
append to an in-memory deque.

## Losing records loudly

The queue is bounded. An unbounded one turns a database outage into a memory
exhaustion, which is a worse failure than a gap.

When it overflows, the dropped count is recorded and the **next** successfully
written event carries a ``dropped_before`` marker in its payload. The chain
therefore states that a gap exists and how large it was, rather than closing
seamlessly over missing rows — which would make the chain assert continuity it
does not have. GAP-SEC-003 names this directly: ``core.outbox`` silently
dead-lettered 15,256 events once, so delivery is not assumed here.

## What is never written

Scope **keys**, never scope **values**. A scope value can be another tenant's
resource id, and an audit trail is not a place to accumulate identifiers the
reader may not be entitled to. Reason codes are recorded — they are stable,
non-disclosing by design, and they are the whole point of being able to explain
a denial later.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID, uuid4

from services.audit_scope import audit_reason, should_audit

logger = logging.getLogger(__name__)

#: Bounded so a database outage degrades into a recorded gap rather than
#: unbounded memory growth. Sized to absorb a burst of a few seconds at Phase 5
#: volumes; beyond that the gap marker is the honest outcome.
MAX_QUEUED_EVENTS = 10_000

#: Rows per transaction. The chain is computed in Python, so a batch is a single
#: round trip rather than N.
BATCH_SIZE = 200

#: How long the writer sleeps when the queue is empty.
IDLE_SLEEP_SECONDS = 1.0

#: entity_type for the events record_decision() produces. Other event kinds pass
#: their own through record_event(); see PROVISIONING_ENTITY_TYPE below.
ENTITY_TYPE = "authorisation_decision"

#: Account provisioning: invitations sent, re-sent, and claimed. These share this
#: module's chain rather than getting their own writer, because core.audit_events
#: is hash-chained per tenant and a chain has exactly ONE tip. A second writer
#: reading the same prev_event_hash concurrently would fork the chain — which is
#: silent, and only discovered when someone tries to verify it. One writer is a
#: correctness requirement here, not just tidiness.
PROVISIONING_ENTITY_TYPE = "account_provisioning"

_queue: deque[dict[str, Any]] = deque()
_dropped_since_last_write = 0
_writer_task: asyncio.Task | None = None


def _as_uuid(value: Any) -> str | None:
    """Return ``value`` as a UUID string, or None when it is not one.

    Actor and scheme identifiers arrive from several stores in this codebase —
    Postgres UUIDs, Mongo string ids, legacy plan numbers like "13195". The audit
    columns are typed ``uuid``, so anything that is not one is recorded in the
    payload instead of being coerced into a column where it does not belong.
    """
    if value in (None, ""):
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _redact_scope(scope: Mapping[str, Any] | None) -> list[str]:
    """Return the scope's KEYS, sorted. Values are deliberately discarded.

    A scope value can be another tenant's resource id. Knowing that a decision
    was made about *a* building is enough to explain it; knowing *which* one is
    already in ``scheme_id`` when we could resolve it to a UUID.
    """
    if not isinstance(scope, Mapping):
        return []
    return sorted(str(key) for key, value in scope.items() if value not in (None, ""))


def record_decision(
    decision: Any,
    *,
    subject: Mapping[str, Any] | None = None,
    scope: Mapping[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> bool:
    """Queue a decision for the audit chain. Returns whether it was queued.

    Synchronous and non-blocking by contract: this is called from the request
    path, and ``decide()``'s no-I/O guarantee would be worthless if recording the
    result blocked on a database.

    Returns False when the decision is out of scope (see
    ``services.audit_scope``) or when the queue is full. A full queue increments
    the dropped counter, which the next written event reports.
    """
    global _dropped_since_last_write

    if not should_audit(decision):
        return False

    if len(_queue) >= MAX_QUEUED_EVENTS:
        _dropped_since_last_write += 1
        if _dropped_since_last_write == 1 or _dropped_since_last_write % 1000 == 0:
            logger.error(
                "authorisation audit queue full (%d) — %d decision(s) dropped; "
                "the next written event will carry a dropped_before marker",
                MAX_QUEUED_EVENTS, _dropped_since_last_write,
            )
        return False

    subject = subject or {}
    _queue.append({
        "entity_type": ENTITY_TYPE,
        "payload": None,  # built from the decision fields by _write_batch
        "decision_id": _as_uuid(getattr(decision, "decision_id", None)) or str(uuid4()),
        "capability": str(getattr(decision, "capability", "") or ""),
        "allowed": bool(getattr(decision, "allowed", False)),
        "reason_codes": list(getattr(decision, "reason_codes", ()) or ()),
        "obligations": list(getattr(decision, "obligations", ()) or ()),
        "policy_version": str(getattr(decision, "policy_version", "") or ""),
        "audit_reason": audit_reason(decision),
        "scope_keys": _redact_scope(scope),
        "tenant_id": _as_uuid(subject.get("tenant_id")),
        "scheme_id": _as_uuid((scope or {}).get("building_id")),
        "actor_user_id": _as_uuid(subject.get("id")),
        "actor_role": str(subject.get("role") or ""),
        "ip_address": ip_address,
        "user_agent": (user_agent or "")[:500] or None,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    })
    return True


def record_event(
    entity_type: str,
    action: str,
    *,
    tenant_id: Any,
    actor_user_id: Any = None,
    scheme_id: Any = None,
    entity_id: Any = None,
    payload: Mapping[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> bool:
    """Queue a non-decision event onto the same audit chain. Returns whether queued.

    Same contract as :func:`record_decision` — synchronous, non-blocking, bounded
    queue, drop accounting — and deliberately the same chain, because
    core.audit_events is hash-chained per tenant and forking it with a second
    writer would be undetectable until verification time.

    Unlike a decision, this is NOT filtered through ``services.audit_scope``.
    should_audit() answers "is this authorisation decision interesting enough to
    keep", which is a question about allow/deny and obligations; a caller reaching
    this function has already decided the event is worth recording. Account
    provisioning is the first user: an invitation is a grant of platform access,
    so every one of them is material.

    ``tenant_id`` MUST resolve to a UUID. The chain is per-tenant and RLS scopes
    reads that way, so an event without one cannot be written — flush_once()
    discards those, and returning False here surfaces it at the call site instead.

    ``payload`` is stored verbatim as JSONB, so the caller owns what goes in it.
    Do not put another tenant's identifiers in it (see _redact_scope for why).
    """
    global _dropped_since_last_write

    resolved_tenant = _as_uuid(tenant_id)
    if not resolved_tenant:
        logger.warning(
            "audit: %s/%s not recorded — tenant_id %r is not a UUID",
            entity_type, action, tenant_id,
        )
        return False

    if len(_queue) >= MAX_QUEUED_EVENTS:
        _dropped_since_last_write += 1
        if _dropped_since_last_write == 1 or _dropped_since_last_write % 1000 == 0:
            logger.error(
                "audit queue full (%d) — %d event(s) dropped; the next written "
                "event will carry a dropped_before marker",
                MAX_QUEUED_EVENTS, _dropped_since_last_write,
            )
        return False

    occurred_at = datetime.now(timezone.utc).isoformat()
    _queue.append({
        "entity_type": str(entity_type),
        "payload": {**(dict(payload) if payload else {}), "occurred_at": occurred_at},
        "decision_id": _as_uuid(entity_id),  # written to entity_id; None is fine
        "capability": str(action),           # written to the action column
        "tenant_id": resolved_tenant,
        "scheme_id": _as_uuid(scheme_id),
        "actor_user_id": _as_uuid(actor_user_id),
        "ip_address": ip_address,
        "user_agent": (user_agent or "")[:500] or None,
        "occurred_at": occurred_at,
        # Decision-shaped keys the writer reads only when payload is absent.
        # Present so a future change to _write_batch cannot KeyError on them.
        "allowed": True,
        "reason_codes": [],
        "obligations": [],
        "policy_version": "",
        "audit_reason": "",
        "scope_keys": [],
        "actor_role": "",
    })
    return True


def queue_depth() -> int:
    """Current number of queued events. Exposed for diagnostics and tests."""
    return len(_queue)


def dropped_count() -> int:
    """Decisions dropped since the last successful write."""
    return _dropped_since_last_write


def _compute_hash(previous_hash: str | None, payload: Mapping[str, Any]) -> str:
    """Chain link: sha256 over the previous hash plus this event's canonical form.

    ``sort_keys`` matters — a dict whose key order varies between runs would
    produce a different hash for identical content and break verification.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{previous_hash or ''}{canonical}".encode("utf-8")).hexdigest()


def _drain(limit: int) -> list[dict[str, Any]]:
    """Pop up to ``limit`` events off the front of the queue."""
    batch: list[dict[str, Any]] = []
    while _queue and len(batch) < limit:
        batch.append(_queue.popleft())
    return batch


async def flush_once(limit: int = BATCH_SIZE) -> int:
    """Write one batch. Returns how many rows were written.

    Events whose tenant cannot be resolved are discarded and counted:
    ``core.audit_events.tenant_id`` is NOT NULL and RLS-scoped, so there is no
    correct row to write for a subject with no Postgres tenant. Recording them
    against a sentinel tenant would put one building's decisions where another
    building can read them, which is worse than not recording them.
    """
    global _dropped_since_last_write

    batch = _drain(limit)
    if not batch:
        return 0

    writable = [event for event in batch if event["tenant_id"]]
    skipped = len(batch) - len(writable)
    if skipped:
        _dropped_since_last_write += skipped
        logger.warning(
            "authorisation audit: %d decision(s) had no resolvable tenant and were "
            "not written; core.audit_events.tenant_id is NOT NULL and RLS-scoped",
            skipped,
        )
    if not writable:
        return 0

    # Snapshot the gap BEFORE the write, and subtract exactly that afterwards.
    #
    # Zeroing the counter here instead would lose any drop that happened DURING
    # the write. _write_batch awaits the database, and request handlers keep
    # calling record_decision on the same event loop across those awaits — so a
    # queue overflow mid-write would be silently forgotten, which is the precise
    # failure the dropped_before marker exists to prevent.
    reported = _dropped_since_last_write

    try:
        written = await _write_batch(writable, dropped_before=reported)
    except Exception:  # noqa: BLE001 — a failed write must not kill the writer
        _dropped_since_last_write += len(writable)
        logger.exception(
            "authorisation audit: batch of %d failed to write; counted as dropped",
            len(writable),
        )
        return 0

    _dropped_since_last_write = max(0, _dropped_since_last_write - reported)
    return written


async def _write_batch(events: list[dict[str, Any]], *, dropped_before: int = 0) -> int:
    """Append one batch to core.audit_events, extending the hash chain in order.

    ``dropped_before`` is passed in rather than read from module state: the
    caller snapshots it before the write so a drop occurring mid-write is not
    swallowed. See :func:`flush_once`.
    """
    from sqlalchemy import text

    from db_postgres.session import async_session_context, set_tenant

    # Group by tenant: the chain is per-tenant because RLS scopes reads that way,
    # and a chain spanning tenants could not be verified by any single reader.
    by_tenant: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_tenant.setdefault(event["tenant_id"], []).append(event)

    written = 0
    for tenant_id, tenant_events in by_tenant.items():
        async with async_session_context() as session:
            await set_tenant(session, tenant_id)

            previous = await session.execute(text("""
                SELECT event_hash FROM core.audit_events
                 WHERE tenant_id = CAST(:tid AS UUID)
                 ORDER BY created_at DESC, audit_event_id DESC
                 LIMIT 1
            """), {"tid": tenant_id})
            previous_hash = previous.scalar()

            for event in tenant_events:
                # An event either brings its own payload (record_event) or is a
                # decision whose payload is assembled from its fields.
                payload = event.get("payload") or {
                    "allowed": event["allowed"],
                    "reason_codes": event["reason_codes"],
                    "obligations": event["obligations"],
                    "policy_version": event["policy_version"],
                    "audit_reason": event["audit_reason"],
                    "scope_keys": event["scope_keys"],
                    "actor_role": event["actor_role"],
                    "occurred_at": event["occurred_at"],
                }
                if dropped_before:
                    # State the gap in the chain rather than closing over it.
                    payload["dropped_before"] = dropped_before
                    dropped_before = 0

                event_hash = _compute_hash(previous_hash, payload)
                await session.execute(text("""
                    INSERT INTO core.audit_events (
                        audit_event_id, tenant_id, scheme_id, entity_type, entity_id,
                        action, actor_user_id, ip_address, user_agent,
                        event_payload, prev_event_hash, event_hash, created_at
                    ) VALUES (
                        gen_random_uuid(), CAST(:tid AS UUID),
                        CAST(NULLIF(:sid,'') AS UUID), :etype, CAST(NULLIF(:eid,'') AS UUID),
                        :action, CAST(NULLIF(:actor,'') AS UUID),
                        CAST(NULLIF(:ip,'') AS INET), NULLIF(:ua,''),
                        CAST(:payload AS JSONB), :prev, :hash, now()
                    )
                """), {
                    "tid": tenant_id,
                    "sid": event["scheme_id"] or "",
                    "etype": event.get("entity_type") or ENTITY_TYPE,
                    "eid": event["decision_id"] or "",
                    "action": event["capability"],
                    "actor": event["actor_user_id"] or "",
                    "ip": event["ip_address"] or "",
                    "ua": event["user_agent"] or "",
                    "payload": json.dumps(payload, sort_keys=True, default=str),
                    "prev": previous_hash,
                    "hash": event_hash,
                })
                previous_hash = event_hash
                written += 1

            await session.commit()
    return written


async def _writer_loop() -> None:
    """The single chain writer. One task, so the tip is never contended."""
    logger.info("authorisation audit writer started")
    while True:
        try:
            written = await flush_once()
            if written == 0:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
        except asyncio.CancelledError:
            # Best-effort final drain so a graceful shutdown does not lose the tail.
            try:
                await flush_once()
            except Exception:  # noqa: BLE001 — shutting down regardless
                logger.exception("authorisation audit: final flush failed")
            logger.info("authorisation audit writer stopped")
            raise
        except Exception:  # noqa: BLE001 — the writer must outlive one bad batch
            logger.exception("authorisation audit writer error; continuing")
            await asyncio.sleep(IDLE_SLEEP_SECONDS)


def start_writer() -> asyncio.Task | None:
    """Start the background writer if it is not already running."""
    global _writer_task
    if _writer_task is not None and not _writer_task.done():
        return _writer_task
    try:
        _writer_task = asyncio.get_running_loop().create_task(_writer_loop())
    except RuntimeError:
        logger.warning("authorisation audit writer not started: no running event loop")
        return None
    return _writer_task


async def stop_writer() -> None:
    """Cancel the writer and let it perform its final drain."""
    global _writer_task
    if _writer_task is None or _writer_task.done():
        _writer_task = None
        return
    _writer_task.cancel()
    try:
        await _writer_task
    except asyncio.CancelledError:
        pass
    _writer_task = None


def _reset_for_tests() -> None:
    """Clear module state. Tests only."""
    global _dropped_since_last_write, _writer_task
    _queue.clear()
    _dropped_since_last_write = 0
    _writer_task = None
