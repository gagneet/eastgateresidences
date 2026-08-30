# @featuretrace:financial_core — Transactional outbox relay (ADR-003). Async MongoDB propagation.
# Layer: worker
# Data flow: core.outbox → this worker → MongoDB events_archive (audit/event log, building-scoped).
# Related: backend/services/financial_core/service.py
#           backend/alembic/versions/0016_extend_outbox_for_relay.py (outbox table)
#           backend/alembic/versions/0086_outbox_per_dest_delivery.py (per-destination delivery cols;
#             redis_published_at/redis_error are now unused — see "Redis removed" note below)
#           backend/db_postgres/repos/identity_repo.py::get_scheme_by_id (scheme_id -> mongo building_id)
#           backend/server.py (startup: asyncio.create_task(run_relay_loop()))
"""Outbox relay worker — polls core.outbox and propagates events to MongoDB events_archive.

ADR-003: Transactional outbox pattern. This worker runs as a background task
started from backend/server.py @app.on_event("startup") when financial_core
is enabled for at least one scheme.

Redis removed (2026-08-11, right-sized-architecture pass): this relay used to publish to Redis
Streams as well, for `notification_worker.py`/`analytics_worker.py` to consume. Verified live that
neither consumer has any deployment mechanism (no systemd unit, no PM2 entry, not started
anywhere) — the Redis leg was pure unused complexity. `events_archive` was never the operational
financial DR mirror either way (see `docs/architecture/financial-postgres-cutover-status-2026-08-11.md`
§3) — the real DR mechanism is the periodic Postgres→Mongo snapshot in
`backend/scripts/dr_mongo_snapshot.py`, which reads Postgres directly and does not depend on this
relay at all. This relay's only remaining job is keeping `events_archive` as an audit/event log.
If a real Redis Streams consumer becomes a genuine product need later, re-add the Redis leg as its
own scoped decision — don't resurrect it "just in case."

Features:
- Polls core.outbox for unpublished events
- Publishes to MongoDB events_archive for historical/audit queries
- Exponential backoff retry logic (30s, 2m, 10m, 1h, 6h)
- Dead-letter tracking after 5 failed attempts
- SKIP LOCKED batch processing to avoid contention

Usage (standalone, for manual/local testing only):
    python -m workers.outbox_relay

In production this does NOT run as its own process/service -- audit finding (2026-08-11): no
`strataos-outbox-relay` systemd unit or PM2 entry exists anywhere in this repo's `deploy/` or
`ecosystem.config.js` (the previous docstring here claimed both existed; neither does). The relay
runs as an asyncio background task started from `backend/server.py`'s startup event
(`asyncio.create_task(run_relay_loop())`) inside the main `strataos-backend` process -- it lives
and dies with the backend, with no separate lifecycle to manage.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5
BATCH_SIZE = 50
MAX_RETRIES = 5
DEAD_LETTER_PREFIX = "[DEAD-LETTER]"
MAX_ERROR_LENGTH = 1024
BACKOFF_SCHEDULE_SECONDS = (30, 120, 600, 3600, 21600)

# core.outbox RLS (migration 0081) has a bypass clause for exactly this sentinel --
# same pattern as core.schemes/core.user_role_assignments. This worker is deliberately
# cross-tenant (polls every tenant's outbox events in one loop), so it must use the
# bypass, never a real tenant_id.
_RLS_BYPASS_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def _backoff_delay(attempt: int) -> int:
    """Calculate retry delay for a failed attempt number."""
    if attempt <= 0:
        return 0
    index = min(attempt, len(BACKOFF_SCHEDULE_SECONDS)) - 1
    return BACKOFF_SCHEDULE_SECONDS[index]


def _next_retry_at(last_attempt_at: datetime, attempt: int) -> datetime:
    """Return the earliest time the row should be retried.

    Backoff is relative to last_attempt_at so that worker downtime does not
    cause a burst of back-to-back retries when the worker restarts.
    """
    return last_attempt_at + timedelta(seconds=_backoff_delay(attempt))


def _is_retry_due(last_attempt_at: datetime, attempt: int, now: Optional[datetime] = None) -> bool:
    current_time = now or datetime.now(tz=timezone.utc)
    return current_time >= _next_retry_at(last_attempt_at, attempt)


def _truncate_error_message(error: Exception | str, limit: int = MAX_ERROR_LENGTH) -> str:
    return str(error)[:limit]


def _format_dead_letter_error(error: Exception | str) -> str:
    suffix_budget = MAX_ERROR_LENGTH - len(DEAD_LETTER_PREFIX) - 1
    truncated = _truncate_error_message(error, limit=max(suffix_budget, 0))
    return f"{DEAD_LETTER_PREFIX} {truncated}".rstrip()


def _is_dead_letter_row(attempts: int, last_error: Optional[str]) -> bool:
    return attempts >= MAX_RETRIES or (last_error or "").startswith(DEAD_LETTER_PREFIX)


async def _relay_batch(session) -> int:
    """Process up to BATCH_SIZE unpublished outbox rows. Returns count processed."""
    # core.outbox has RLS FORCED with no bypass by default (migration 0081 adds one for
    # this exact sentinel) -- without this, the SELECT below silently matches zero rows,
    # every poll, forever (confirmed live 2026-08-09: every outbox row since 2026-05-04
    # stuck at attempts=0, published_at=NULL). SET LOCAL is transaction-scoped, so this
    # must run in every call, not just once per session.
    await session.execute(text(f"SET LOCAL app.tenant_id = '{_RLS_BYPASS_TENANT_ID}'"))

    # Backoff is anchored to last_attempt_at (NULL on first attempt → use created_at).
    # Per-attempt delay prevents burst retries after worker downtime.
    result = await session.execute(
        text(
            "SELECT id, tenant_id, scheme_id, event_type, aggregate_id, aggregate_type, "
            "       payload, attempts, last_error, created_at, last_attempt_at "
            "FROM core.outbox "
            "WHERE published_at IS NULL "
            "  AND COALESCE(attempts, 0) < :max_retries "
            "  AND COALESCE(last_error, '') NOT LIKE :dead_letter_prefix "
            "  AND NOW() >= COALESCE(last_attempt_at, created_at) + CASE COALESCE(attempts, 0) "
            "       WHEN 0 THEN INTERVAL '0 seconds' "
            "       WHEN 1 THEN INTERVAL '30 seconds' "
            "       WHEN 2 THEN INTERVAL '120 seconds' "
            "       WHEN 3 THEN INTERVAL '600 seconds' "
            "       WHEN 4 THEN INTERVAL '3600 seconds' "
            "       ELSE INTERVAL '21600 seconds' "
            "   END "
            "ORDER BY created_at ASC "
            f"LIMIT {BATCH_SIZE} "
            "FOR UPDATE SKIP LOCKED"
        ),
        {"max_retries": MAX_RETRIES, "dead_letter_prefix": f"{DEAD_LETTER_PREFIX}%"},
    )
    rows = result.fetchall()
    if not rows:
        return 0

    processed = 0
    for row in rows:
        attempts = row.attempts or 0
        if _is_dead_letter_row(attempts, row.last_error):
            continue

        try:
            await _dispatch_event(
                event_id=row.id,
                event_type=row.event_type,
                aggregate_id=row.aggregate_id,
                aggregate_type=row.aggregate_type,
                payload=row.payload,
                scheme_id=str(row.scheme_id) if row.scheme_id else None,
            )
        except Exception as exc:
            new_attempts = attempts + 1
            error_msg = _truncate_error_message(exc)
            now_utc = datetime.now(tz=timezone.utc)

            if new_attempts >= MAX_RETRIES:
                dead_letter_error = _format_dead_letter_error(exc)
                logger.error(
                    f"Outbox event failed after {MAX_RETRIES} retries: "
                    f"id={row.id}, type={row.event_type}, error={error_msg}"
                )
                await session.execute(
                    text(
                        "UPDATE core.outbox "
                        "SET attempts = :attempts, last_error = :error, last_attempt_at = :now "
                        "WHERE id = :id"
                    ),
                    {"id": row.id, "attempts": new_attempts, "error": dead_letter_error, "now": now_utc},
                )
            else:
                delay = _backoff_delay(new_attempts)
                next_retry_at = _next_retry_at(now_utc, new_attempts)
                logger.warning(
                    f"Outbox relay failed (will retry): id={row.id}, "
                    f"attempt={new_attempts}/{MAX_RETRIES}, "
                    f"delay={delay}s, next_retry_at={next_retry_at.isoformat()}, error={error_msg}"
                )
                await session.execute(
                    text(
                        "UPDATE core.outbox "
                        "SET attempts = :attempts, last_error = :error, last_attempt_at = :now "
                        "WHERE id = :id"
                    ),
                    {"id": row.id, "attempts": new_attempts, "error": error_msg, "now": now_utc},
                )
            continue

        await session.execute(
            text("UPDATE core.outbox SET published_at = :now, last_error = NULL WHERE id = :id"),
            {"id": row.id, "now": datetime.now(tz=timezone.utc)},
        )
        processed += 1
        logger.debug(f"Outbox published: event_id={row.id}, type={row.event_type}")

    await session.commit()
    return processed


async def _dispatch_event(
        event_id,
        event_type: str,
        aggregate_id: Optional[str],
        aggregate_type: Optional[str],
        payload: dict,
        scheme_id: Optional[str],
) -> None:
    """Resolve the event's building_id and archive it to Mongo. Raises on failure so the caller
    can retry — a swallowed exception here previously meant a row could be marked published while
    Mongo silently never received it (GAP-FIN-061).
    """
    await _publish_to_mongo(
        event_id=str(event_id),
        event_type=event_type,
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_type,
        payload=payload,
        scheme_id=scheme_id,
    )


async def _resolve_building_id(payload: dict, scheme_id: Optional[str]) -> Optional[str]:
    """Resolve the Mongo/public building_id (Unit Plan number) an event belongs to.

    Never defaults to a real building. GAP-FIN-061 found `payload.get("mongo_building_id",
    "13195")` silently archiving EVERY event platform-wide under East Gate, because no writer of
    core.outbox has ever populated that payload key. An explicit `payload["mongo_building_id"]`
    (if a future writer sets one) still wins; otherwise resolve scheme_id -> scheme_number via the
    canonical PG lookup. Returns None if unresolvable so the caller refuses to dispatch rather
    than mislabeling the event.
    """
    explicit = payload.get("mongo_building_id")
    if explicit:
        return explicit
    if not scheme_id:
        return None
    try:
        from db_postgres.repos.identity_repo import get_scheme_by_id

        scheme = await get_scheme_by_id(scheme_id)
        if scheme and scheme.get("scheme_number"):
            return str(scheme["scheme_number"])
    except Exception:
        logger.exception("Failed to resolve building_id for scheme_id=%s", scheme_id)
    return None


async def _publish_to_mongo(
        event_id: str,
        event_type: str,
        aggregate_id: Optional[str],
        aggregate_type: Optional[str],
        payload: dict,
        scheme_id: Optional[str],
) -> None:
    """Write event to MongoDB events_archive collection for historical/audit queries.

    This is an audit/event log, not the operational financial DR mirror (see module docstring).
    RAISES on failure (including an unresolvable building mapping) so the caller can track and
    retry — never silently swallowed (GAP-FIN-061).
    """
    from database import db as mongo_db
    from request_context import set_ctx_building_id

    building_id = await _resolve_building_id(payload, scheme_id)
    if not building_id:
        raise ValueError(
            f"Cannot resolve building_id for scheme_id={scheme_id!r}; "
            "refusing to archive under a default building"
        )
    set_ctx_building_id(building_id)

    archive_doc = {
        "_id": event_id,
        "event_type": event_type,
        "aggregate_id": aggregate_id,
        "aggregate_type": aggregate_type,
        "payload": payload,
        "scheme_id": scheme_id,
        "building_id": building_id,
        "relayed_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    try:
        await mongo_db.events_archive.insert_one(archive_doc)
    except Exception as exc:
        # A duplicate _id (event_id) means this exact event was already archived on a prior
        # attempt whose success wasn't persisted (e.g. a crash between insert and commit) —
        # idempotent, not a real failure. Any other error is a genuine delivery failure.
        if "duplicate key" in str(exc).lower() or "E11000" in str(exc):
            logger.debug(f"MongoDB archive already present (idempotent replay): event_id={event_id}")
        else:
            raise
    logger.debug(f"MongoDB archive: event_id={event_id}, type={event_type}")


async def run_relay_loop() -> None:
    """Main relay loop — polls indefinitely until cancelled."""
    from db_postgres.session import make_session_factory

    # make_session_factory() takes no positional args — it pulls the
    # shared engine via get_engine() internally (see db_postgres/session.py).
    # Passing engine here was a long-standing TypeError that fired on every
    # backend startup (visible in journalctl) but didn't crash the app
    # because the relay task ran in a try/except. Fixed 2026-05-03.
    session_factory = make_session_factory()

    logger.info(
        f"Outbox relay worker started "
        f"(poll_interval={POLL_INTERVAL_SECONDS}s, max_retries={MAX_RETRIES}, "
        f"batch_size={BATCH_SIZE})"
    )

    while True:
        try:
            async with session_factory() as session:
                processed = await _relay_batch(session)
                if processed > 0:
                    logger.info(f"Outbox relay: processed {processed} events")
        except asyncio.CancelledError:
            logger.info("Outbox relay worker stopping")
            break
        except Exception as exc:
            logger.error(f"Outbox relay loop error: {exc}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)
    asyncio.run(run_relay_loop())
