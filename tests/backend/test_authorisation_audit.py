"""Authorisation decisions must reach the audit chain — and say so when they don't.

GAP-SEC-003. ``decide()`` has produced a reasoned Decision since Phase 3 and the
only record was a log line: not tamper-evident, not retained seven years, gone on
rotation. These tests cover the write path and the three constraints the task
names as making it non-trivial — volume, no sensitive data in the payload, and a
chain that must not silently drop or reorder.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import services.authorisation_audit as audit  # noqa: E402
from models.user import UserRole  # noqa: E402
from services.capability_registry import decide  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"
SCHEME = "33333333-3333-3333-3333-333333333333"


@dataclass
class _Decision:
    allowed: bool
    capability: str = "building.finance.manage"
    reason_codes: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    policy_version: str = "act-r25-1"
    decision_id: str = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(autouse=True)
def _clean():
    """Generated function header.

    Function: _clean
    Path: tests/backend/test_authorisation_audit.py
    """
    audit._reset_for_tests()
    yield
    audit._reset_for_tests()


def _subject(**extra) -> dict:
    """Generated function header.

    Function: _subject
    Path: tests/backend/test_authorisation_audit.py
    """
    return {"id": USER, "tenant_id": TENANT, "role": UserRole.STRATA_MANAGER, **extra}


# ── Scope: what gets queued at all ───────────────────────────────────────────

def test_a_denial_is_always_queued():
    """Denials are the non-negotiable half of the scope (plan §8.8)."""
    assert audit.record_decision(_Decision(allowed=False), subject=_subject()) is True
    assert audit.queue_depth() == 1


def test_an_ordinary_allow_is_not_queued():
    """The volume constraint that produced the scope decision is respected."""
    allowed = _Decision(allowed=True, capability="building.dashboard.view")
    assert audit.record_decision(allowed, subject=_subject()) is False
    assert audit.queue_depth() == 0


def test_a_financial_allow_is_queued():
    """Generated function header.

    Function: test_a_financial_allow_is_queued
    Path: tests/backend/test_authorisation_audit.py
    """
    assert audit.record_decision(_Decision(allowed=True), subject=_subject()) is True


def test_an_allow_carrying_a_personal_information_obligation_is_queued():
    """The GAP-SEC-011 widening, end to end through the recorder."""
    decision = _Decision(
        allowed=True,
        capability="building.dashboard.view",
        obligations=("MASK_OWNER_CONTACT",),
    )
    assert audit.record_decision(decision, subject=_subject()) is True


# ── Constraint 2: no sensitive data in the payload ───────────────────────────

def test_scope_values_are_never_recorded_only_keys():
    """A scope value can be another tenant's resource id.

    The audit trail must be able to explain a decision without accumulating
    identifiers its reader may not be entitled to see.
    """
    scope = {"building_id": SCHEME, "unit_id": "SECRET-UNIT-42", "work_order_id": "WO-SECRET"}
    audit.record_decision(_Decision(allowed=False), subject=_subject(), scope=scope)

    event = audit._queue[0]
    assert event["scope_keys"] == ["building_id", "unit_id", "work_order_id"]
    serialised = str(event)
    assert "SECRET-UNIT-42" not in serialised
    assert "WO-SECRET" not in serialised


def test_empty_scope_values_are_not_reported_as_present():
    """A key with no value did not scope anything and must not claim to."""
    audit.record_decision(
        _Decision(allowed=False), subject=_subject(),
        scope={"building_id": SCHEME, "unit_id": None, "work_order_id": ""},
    )
    assert audit._queue[0]["scope_keys"] == ["building_id"]


def test_reason_codes_are_recorded():
    """They are non-disclosing by design and are the point of explaining a denial."""
    decision = _Decision(allowed=False, reason_codes=("DENY_BUILDING_NOT_ASSIGNED",))
    audit.record_decision(decision, subject=_subject())
    assert audit._queue[0]["reason_codes"] == ["DENY_BUILDING_NOT_ASSIGNED"]


def test_a_long_user_agent_is_truncated():
    """Unbounded client-supplied text does not belong in an append-only table."""
    audit.record_decision(
        _Decision(allowed=False), subject=_subject(), user_agent="x" * 5000
    )
    assert len(audit._queue[0]["user_agent"]) == 500


# ── Non-UUID identifiers ─────────────────────────────────────────────────────

def test_a_legacy_plan_number_is_not_coerced_into_the_scheme_uuid_column():
    """building_id is often "13195", which is not a UUID.

    core.audit_events.scheme_id is typed uuid; forcing a plan number in would
    either error or, worse, land in a column that means something else.
    """
    audit.record_decision(
        _Decision(allowed=False), subject=_subject(), scope={"building_id": "13195"}
    )
    event = audit._queue[0]
    assert event["scheme_id"] is None
    assert event["scope_keys"] == ["building_id"]


def test_a_mongo_style_actor_id_is_not_coerced():
    """Generated function header.

    Function: test_a_mongo_style_actor_id_is_not_coerced
    Path: tests/backend/test_authorisation_audit.py
    """
    audit.record_decision(_Decision(allowed=False), subject=_subject(id="user-87"))
    assert audit._queue[0]["actor_user_id"] is None


# ── Constraint 3: the chain must not silently drop ───────────────────────────

def test_queue_overflow_drops_loudly_and_is_counted(caplog):
    """An unbounded queue turns a DB outage into memory exhaustion.

    Bounded is the right call, but the gap must be recorded rather than closed
    over — GAP-SEC-003 names core.outbox's 15,256 silently dead-lettered events
    as the thing not to repeat.
    """
    original = audit.MAX_QUEUED_EVENTS
    audit.MAX_QUEUED_EVENTS = 3
    try:
        with caplog.at_level("ERROR"):
            for _ in range(6):
                audit.record_decision(_Decision(allowed=False), subject=_subject())
    finally:
        audit.MAX_QUEUED_EVENTS = original

    assert audit.queue_depth() == 3
    assert audit.dropped_count() == 3
    assert any("queue full" in r.message for r in caplog.records)


async def test_the_next_written_event_states_the_gap():
    """The chain must assert that a gap exists, not close seamlessly over it."""
    audit._dropped_since_last_write = 7
    audit.record_decision(_Decision(allowed=False), subject=_subject())

    captured: list[dict] = []

    async def fake_write(events, *, dropped_before=0):
        captured.append({'events': events, 'dropped_before': dropped_before})
        return len(events)

    with patch.object(audit, "_write_batch", side_effect=fake_write):
        written = await audit.flush_once()

    assert written == 1
    assert captured[0]["dropped_before"] == 7, (
        "the size of the gap must be handed to the writer so the next event can state it"
    )
    assert audit.dropped_count() == 0, "the reported gap must be cleared after a successful write"


async def test_a_failed_batch_counts_as_dropped_and_does_not_raise():
    """The writer must outlive a bad batch, and must not pretend it succeeded."""
    audit.record_decision(_Decision(allowed=False), subject=_subject())

    with patch.object(audit, "_write_batch", side_effect=RuntimeError("db down")):
        written = await audit.flush_once()

    assert written == 0
    assert audit.dropped_count() == 1


async def test_events_without_a_tenant_are_skipped_not_misfiled():
    """core.audit_events.tenant_id is NOT NULL and RLS-scoped.

    Writing a tenant-less decision against a sentinel tenant would put one
    building's decisions where a different building can read them. Skipping is
    the lesser harm, and it is counted.
    """
    audit.record_decision(_Decision(allowed=False), subject={"id": USER, "role": "owner"})
    assert audit.queue_depth() == 1

    with patch.object(audit, "_write_batch", side_effect=AssertionError("must not be called")):
        written = await audit.flush_once()

    assert written == 0
    assert audit.dropped_count() == 1


# ── The hash chain ───────────────────────────────────────────────────────────

def test_the_chain_hash_depends_on_the_previous_link():
    """Otherwise it is a list of hashes, not a chain."""
    payload = {"allowed": False, "reason_codes": ["DENY_X"]}
    first = audit._compute_hash(None, payload)
    second = audit._compute_hash(first, payload)

    assert first != second
    assert len(first) == 64


def test_the_chain_hash_is_stable_across_key_order():
    """Dict ordering must not change the hash of identical content."""
    a = audit._compute_hash("prev", {"allowed": True, "policy_version": "v1"})
    b = audit._compute_hash("prev", {"policy_version": "v1", "allowed": True})
    assert a == b


def test_a_changed_payload_changes_the_hash():
    """Generated function header.

    Function: test_a_changed_payload_changes_the_hash
    Path: tests/backend/test_authorisation_audit.py
    """
    a = audit._compute_hash("prev", {"allowed": True})
    b = audit._compute_hash("prev", {"allowed": False})
    assert a != b


# ── Integration with the real evaluator ──────────────────────────────────────

def test_a_real_denial_flows_through_to_the_queue():
    """End to end against decide(), not a hand-built stub."""
    subject = {"id": USER, "tenant_id": TENANT, "role": UserRole.OWNER}
    decision = decide(subject, "building.finance.manage", {"building_id": SCHEME})

    assert decision.allowed is False
    assert audit.record_decision(decision, subject=subject, scope={"building_id": SCHEME}) is True

    event = audit._queue[0]
    assert event["capability"] == "building.finance.manage"
    assert event["allowed"] is False
    assert "DENY_ROLE_NOT_PERMITTED" in event["reason_codes"]
    assert event["audit_reason"] == "SCOPE_DENIAL"


def test_recording_never_raises_on_a_malformed_decision():
    """The request path must not fail because auditing did."""
    class Opaque:
        pass

    audit.record_decision(Opaque(), subject=_subject())  # must not raise


def test_the_writer_is_wired_into_the_application_lifecycle():
    """A writer nobody starts means a queue that fills and drops forever."""
    source = (Path(__file__).resolve().parents[2] / "backend" / "server.py").read_text()
    assert "start_authorisation_audit_writer" in source
    assert "stop_authorisation_audit_writer" in source


def test_the_capability_dependency_records_before_it_raises():
    """A denial that raised before being recorded is the event nobody can reconstruct."""
    source = (
        Path(__file__).resolve().parents[2] / "backend" / "services" / "capability_registry.py"
    ).read_text()
    record_at = source.index("_audit_decision(")
    raise_at = source.index("raise_denied(decision)", record_at)
    assert record_at < raise_at, "the audit call must precede the raising guard"


# ── The correlation the whole design rests on ────────────────────────────────

async def test_the_403_decision_id_matches_the_audited_decision_id():
    """The id support is given must be the id in the audit trail.

    Regression. The dependency needs the decision twice — once to audit, once to
    deny with — and the first implementation audited the decision then called
    ``assert_capability``, which re-ran ``decide()``. Because ``decide()`` stamps
    a random ``decision_id`` per call, that minted a SECOND id: the 403 body
    quoted one id while ``core.audit_events`` recorded another, so an id a user
    reported matched nothing. ``raise_denied()`` exists to close that.
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import services.authorisation_context as ctx
    from services.capability_registry import require_capability
    from utils.auth import get_current_user

    subject = {
        "id": USER, "tenant_id": TENANT, "role": UserRole.OWNER,
        "building_id": SCHEME, "building_ids": [SCHEME],
    }

    original_hydrate = ctx.hydrate_authorisation_claims

    async def passthrough(user, scope, **_hydration_hints):
        return {**user}

    ctx.hydrate_authorisation_claims = passthrough
    try:
        app = FastAPI()

        @app.get("/probe")
        async def probe(
            _cap: dict = Depends(require_capability(
                "building.finance.manage", scope_values={"building_id": SCHEME}
            )),
        ):
            return {"ok": True}

        app.dependency_overrides[get_current_user] = lambda: subject

        audit._reset_for_tests()
        response = TestClient(app).get("/probe")
    finally:
        ctx.hydrate_authorisation_claims = original_hydrate

    assert response.status_code == 403
    body_id = response.json()["detail"].split("decision ")[-1].rstrip(")")

    assert audit.queue_depth() == 1, "the denial must have been queued for audit"
    assert audit._queue[0]["decision_id"] == body_id, (
        "the decision_id in the 403 body must be the one written to the audit "
        "trail, or support cannot correlate a user's report with the record"
    )


def test_decide_is_not_called_twice_on_a_denial():
    """Guards the fix structurally as well as behaviourally.

    Two decide() calls per denial is both the id-mismatch bug and needless work
    on the hot path.
    """
    source = (
        Path(__file__).resolve().parents[2] / "backend" / "services" / "capability_registry.py"
    ).read_text()
    checker = source[source.index("async def _evaluate("): source.index("if not building_from_context")]

    assert checker.count("decide(subject, capability, scope)") == 1
    assert "assert_capability(subject, capability, scope)" not in checker, (
        "the dependency must deny with raise_denied(decision), not by re-running "
        "the evaluator via assert_capability"
    )


async def test_a_drop_during_a_write_is_not_swallowed():
    """The counter must be decremented by what was reported, never zeroed.

    ``_write_batch`` awaits the database, and request handlers keep calling
    ``record_decision`` on the same event loop across those awaits. Zeroing the
    counter after the write would forget any overflow that happened DURING it —
    exactly the silent gap the dropped_before marker exists to prevent.
    """
    audit._dropped_since_last_write = 2
    audit.record_decision(_Decision(allowed=False), subject=_subject())

    async def slow_write(events, *, dropped_before=0):
        # A drop lands while the write is in flight.
        audit._dropped_since_last_write += 5
        return len(events)

    with patch.object(audit, "_write_batch", side_effect=slow_write):
        await audit.flush_once()

    assert audit.dropped_count() == 5, (
        "the 2 reported drops are cleared; the 5 that arrived mid-write must survive"
    )


def test_the_direct_assert_capability_path_is_audited_too():
    """bi.py, cutover_admin.py and finance_intelligence.py never declare the dependency.

    They call ``assert_capability`` from the handler body. Auditing only inside
    ``require_capability``'s dependency left roughly a dozen live call sites —
    including every ``building.finance.*`` check in finance_intelligence —
    producing decisions that reached no audit trail. Found in the
    post-implementation audit.
    """
    from fastapi import HTTPException

    from services.capability_registry import assert_capability

    subject = {"id": USER, "tenant_id": TENANT, "role": UserRole.OWNER}

    audit._reset_for_tests()
    with pytest.raises(HTTPException):
        assert_capability(subject, "building.finance.view", {"building_id": SCHEME})

    assert audit.queue_depth() == 1, "a direct assert_capability denial must be audited"
    assert audit._queue[0]["capability"] == "building.finance.view"


def test_can_is_deliberately_not_audited():
    """can() answers menu visibility, not enforcement.

    Auditing it would bury real decisions under navigation rendering — every page
    load asks it once per menu item.
    """
    from services.capability_registry import can

    audit._reset_for_tests()
    can({"id": USER, "tenant_id": TENANT, "role": UserRole.OWNER},
        "building.finance.view", {"building_id": SCHEME})

    assert audit.queue_depth() == 0


def test_the_two_enforcement_paths_do_not_double_record():
    """require_capability must not route through assert_capability, or denials duplicate."""
    source = (
        Path(__file__).resolve().parents[2] / "backend" / "services" / "capability_registry.py"
    ).read_text()
    evaluate = source[source.index("async def _evaluate("): source.index("if not building_from_context")]

    # Strip comment lines first. An earlier version of this test matched the
    # PROSE explaining why assert_capability is not called, and failed on the
    # explanation rather than the code — the same mistake the group-2 route
    # classifier made when a parameter description set a risk tier.
    code = "\n".join(
        line for line in evaluate.splitlines() if not line.strip().startswith("#")
    )

    assert "assert_capability(" not in code, (
        "the dependency calls decide() + raise_denied() directly; routing it "
        "through assert_capability would record every decision twice"
    )
    assert "raise_denied(decision)" in code
