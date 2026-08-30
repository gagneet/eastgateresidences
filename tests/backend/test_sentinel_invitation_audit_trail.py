"""Invitations and role provisioning must reach the audit chain (audit report finding 2).

`POST /admin/invitations/send`, `.../{id}/resend` and `POST /onboarding/claim/{token}`
are the platform's access-granting path — an invitation creates a row in `core.users`
and assigns a role — and until this work none of them recorded anything at all. "Who
granted this person access, and when" was unanswerable.

## What these tests are actually guarding

Three separate things, because the fix has three places it can silently fail:

1. **The endpoints call the recorder.** Easy to lose in a refactor, and the loss is
   invisible: the invitation still succeeds.
2. **`record_event` queues the right shape.** Wrong `entity_type` or a non-UUID tenant
   and the row either lands under the wrong entity or is dropped by `flush_once`.
3. **`_write_batch` writes what was queued.** This is the one with no prior coverage —
   every pre-existing test in `test_authorisation_audit.py` mocks `_write_batch` out, so
   the SQL, the column mapping and the hash chain were entirely unexercised. The
   provisioning work changed that function (per-event `entity_type`, caller-supplied
   payload), so it needed real tests, and they retro-cover decisions too.

## Why not `create_audit_log`

The audit report recommended it. It writes to the Mongo `audit_logs` collection, which
is TENANT-SCOPED: `TenantCollection.insert_one` raises when there is no building context
and the document has no `building_id`, and `create_audit_log` swallows that and returns
"". On the claim endpoint — unauthenticated, so no building context is ever set — it
would have logged nothing while appearing to work.
`test_the_mongo_audit_helper_would_have_silently_dropped_the_claim_event` pins that,
so the recommendation is not "corrected" back in later.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_sentinel_invitation_audit_trail.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import routers.admin_invitations as invites  # noqa: E402
import services.authorisation_audit as audit  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
ACTOR = "22222222-2222-2222-2222-222222222222"
SCHEME = "33333333-3333-3333-3333-333333333333"
INVITE = "44444444-4444-4444-4444-444444444444"
NEW_USER = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(autouse=True)
def _clean_queue():
    audit._reset_for_tests()
    yield
    audit._reset_for_tests()


def _request(ip: str = "203.0.113.7", ua: str = "pytest-agent") -> MagicMock:
    req = MagicMock()
    req.client.host = ip
    req.headers = {"user-agent": ua}
    return req


def _caller(role: str = "super_admin") -> dict:
    return {"id": ACTOR, "tenant_id": TENANT, "role": role, "effective_role": role}


def _queued() -> list[dict]:
    return list(audit._queue)


# ─── record_event: the queued shape ──────────────────────────────────────────

def test_record_event_queues_with_the_provisioning_entity_type():
    """Decisions and provisioning share a chain but must stay distinguishable.

    They land in the same table; `entity_type` is the only thing separating "a
    role was granted" from "a request was authorised" when reading it back.
    """
    assert audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.sent",
        tenant_id=TENANT, actor_user_id=ACTOR, scheme_id=SCHEME, entity_id=INVITE,
        payload={"invited_role": "admin_staff"},
    ) is True

    (event,) = _queued()
    assert event["entity_type"] == audit.PROVISIONING_ENTITY_TYPE
    assert event["entity_type"] != audit.ENTITY_TYPE
    assert event["capability"] == "invitation.sent"   # -> the `action` column
    assert event["decision_id"] == INVITE             # -> the `entity_id` column
    assert event["tenant_id"] == TENANT
    assert event["actor_user_id"] == ACTOR
    assert event["scheme_id"] == SCHEME
    assert event["payload"]["invited_role"] == "admin_staff"
    assert event["payload"]["occurred_at"]


def test_record_event_refuses_a_tenant_that_is_not_a_uuid():
    """Fail at the call site, not silently inside the writer.

    `flush_once` discards events with no resolvable tenant — correctly, since
    `core.audit_events.tenant_id` is NOT NULL and RLS-scoped. Returning False here
    means the caller can log an AUDIT GAP instead of believing it was recorded.
    """
    assert audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.sent", tenant_id="13195",
    ) is False
    assert audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.sent", tenant_id=None,
    ) is False
    assert _queued() == []


def test_record_event_is_not_filtered_by_audit_scope():
    """should_audit() answers a question about allow/deny that does not apply here.

    Every provisioning event is material — an ordinary allow would be dropped by
    that filter, and an invitation is an allow.
    """
    with patch.object(audit, "should_audit", return_value=False):
        assert audit.record_event(
            audit.PROVISIONING_ENTITY_TYPE, "invitation.claimed", tenant_id=TENANT,
        ) is True
    assert len(_queued()) == 1


def test_record_event_carries_no_decision_semantics_into_the_payload():
    """A provisioning event has no allow/deny, so none must be implied.

    The decision-shaped keys exist on the queued dict only so `_write_batch`
    cannot KeyError; a reader must never see them presented as this event's
    payload.
    """
    audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.sent",
        tenant_id=TENANT, payload={"invited_role": "owner"},
    )
    (event,) = _queued()
    for decision_key in ("allowed", "reason_codes", "obligations", "policy_version"):
        assert decision_key not in event["payload"], decision_key


def test_record_event_truncates_an_oversized_user_agent():
    """core.audit_events.user_agent is TEXT, but an unbounded copy is still bloat."""
    audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.sent",
        tenant_id=TENANT, user_agent="x" * 5000,
    )
    assert len(_queued()[0]["user_agent"]) == 500


def test_record_event_drops_loudly_when_the_queue_is_full(caplog):
    """A gap must be counted so the next written event can state it."""
    for _ in range(audit.MAX_QUEUED_EVENTS):
        audit.record_event(audit.PROVISIONING_ENTITY_TYPE, "x", tenant_id=TENANT)
    assert audit.dropped_count() == 0

    assert audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.claimed", tenant_id=TENANT,
    ) is False
    assert audit.dropped_count() == 1


# ─── _write_batch: the SQL, previously untested entirely ─────────────────────

class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar(self):
        return self._value


class _FakeSession:
    """Captures INSERT parameters and answers the chain-tip SELECT.

    Faithful to what `_write_batch` actually does: one SELECT for the previous
    hash, then one INSERT per event, then commit. Nothing here interprets SQL —
    it only distinguishes the two statements so the captured parameters are the
    real ones the driver would receive.
    """

    def __init__(self, previous_hash: str | None = None):
        self.previous_hash = previous_hash
        self.inserts: list[dict] = []
        self.committed = False
        self.tenant_set: str | None = None

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT event_hash" in sql:
            return _FakeResult(self.previous_hash)
        assert "INSERT INTO core.audit_events" in sql, sql
        self.inserts.append(dict(params or {}))
        return _FakeResult()

    async def commit(self):
        self.committed = True


def _patched_session(session: _FakeSession):
    """Patch the session factory and set_tenant that `_write_batch` imports."""
    import contextlib

    @contextlib.asynccontextmanager
    async def _factory():
        yield session

    async def _set_tenant(_session, tenant_id):
        session.tenant_set = tenant_id

    return patch.multiple(
        "db_postgres.session",
        async_session_context=_factory,
        set_tenant=_set_tenant,
    )


@pytest.mark.asyncio
async def test_a_provisioning_event_is_written_with_its_own_entity_type_and_payload():
    """The regression that matters: `_write_batch` used a module-level constant.

    Before provisioning shared this chain, every row was written with
    entity_type=ENTITY_TYPE and a payload assembled from decision fields. A
    provisioning event pushed through unchanged would have been filed as an
    authorisation decision carrying an empty allow — indistinguishable, in the
    table, from a real one.
    """
    audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.claimed",
        tenant_id=TENANT, actor_user_id=NEW_USER, scheme_id=SCHEME, entity_id=INVITE,
        payload={"invited_email": "new.owner@example.com", "granted_role": "owner"},
        ip_address="203.0.113.7", user_agent="pytest-agent",
    )

    session = _FakeSession()
    with _patched_session(session):
        written = await audit.flush_once()

    assert written == 1
    assert session.committed is True
    assert session.tenant_set == TENANT

    (row,) = session.inserts
    assert row["etype"] == "account_provisioning"
    assert row["action"] == "invitation.claimed"
    assert row["tid"] == TENANT
    assert row["eid"] == INVITE
    assert row["sid"] == SCHEME
    assert row["actor"] == NEW_USER
    assert row["ip"] == "203.0.113.7"

    payload = json.loads(row["payload"])
    assert payload["invited_email"] == "new.owner@example.com"
    assert payload["granted_role"] == "owner"
    # No decision semantics leak into a provisioning row.
    assert "reason_codes" not in payload
    assert "policy_version" not in payload


@pytest.mark.asyncio
async def test_a_decision_still_writes_its_decision_shaped_payload():
    """Retro-cover the path the provisioning change had to keep intact.

    `_write_batch` now falls back to the decision-shaped payload only when the
    event carries none. This is the assertion that would fail if that fallback
    were dropped.
    """
    from dataclasses import dataclass

    @dataclass
    class _Denial:
        allowed: bool = False
        capability: str = "building.finance.manage"
        reason_codes: tuple = ("DENY_BUILDING_NOT_ASSIGNED",)
        obligations: tuple = ()
        policy_version: str = "act-r25-1"
        decision_id: str = "66666666-6666-6666-6666-666666666666"

    audit.record_decision(
        _Denial(), subject={"id": ACTOR, "tenant_id": TENANT, "role": "strata_manager"},
        scope={"building_id": SCHEME},
    )

    session = _FakeSession()
    with _patched_session(session):
        assert await audit.flush_once() == 1

    (row,) = session.inserts
    assert row["etype"] == "authorisation_decision"
    payload = json.loads(row["payload"])
    assert payload["allowed"] is False
    assert payload["reason_codes"] == ["DENY_BUILDING_NOT_ASSIGNED"]
    assert payload["policy_version"] == "act-r25-1"


@pytest.mark.asyncio
async def test_provisioning_and_decision_events_extend_one_chain_in_order():
    """The reason provisioning shares this writer rather than getting its own.

    The chain has exactly one tip per tenant. Each row's prev_event_hash must be
    the preceding row's event_hash, regardless of which kind of event it is — a
    second writer would fork this silently.
    """
    audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.sent",
        tenant_id=TENANT, payload={"invited_role": "admin_staff"},
    )
    audit.record_event(
        audit.PROVISIONING_ENTITY_TYPE, "invitation.claimed",
        tenant_id=TENANT, payload={"granted_role": "admin_staff"},
    )

    session = _FakeSession(previous_hash="genesis")
    with _patched_session(session):
        assert await audit.flush_once() == 2

    first, second = session.inserts
    assert first["prev"] == "genesis"
    assert second["prev"] == first["hash"]
    assert first["hash"] != second["hash"]
    assert len({first["hash"], second["hash"]}) == 2


@pytest.mark.asyncio
async def test_two_tenants_get_independent_chains():
    """A chain spanning tenants could not be verified by any single RLS reader."""
    other_tenant = "77777777-7777-7777-7777-777777777777"
    audit.record_event(audit.PROVISIONING_ENTITY_TYPE, "invitation.sent", tenant_id=TENANT)
    audit.record_event(audit.PROVISIONING_ENTITY_TYPE, "invitation.sent", tenant_id=other_tenant)

    sessions: list[_FakeSession] = []

    import contextlib

    @contextlib.asynccontextmanager
    async def _factory():
        session = _FakeSession(previous_hash=f"tip-{len(sessions)}")
        sessions.append(session)
        yield session

    async def _set_tenant(session, tenant_id):
        session.tenant_set = tenant_id

    with patch.multiple("db_postgres.session",
                        async_session_context=_factory, set_tenant=_set_tenant):
        assert await audit.flush_once() == 2

    assert len(sessions) == 2
    assert {s.tenant_set for s in sessions} == {TENANT, other_tenant}
    # Each chain starts from its OWN tip, not the other tenant's.
    assert sessions[0].inserts[0]["prev"] == "tip-0"
    assert sessions[1].inserts[0]["prev"] == "tip-1"


# ─── the endpoints actually record ───────────────────────────────────────────

def _actions() -> list[str]:
    return [event["capability"] for event in _queued()]


@pytest.mark.asyncio
async def test_send_records_the_grant():
    body = invites.SendInvitationRequest(
        email="invitee@example.com", role="admin_staff", scheme_id=SCHEME,
        first_name="Ann", last_name="Lee", expires_days=7,
    )
    with patch("routers.admin_invitations.create_invitation",
               new=AsyncMock(return_value=(INVITE, "raw-token"))), \
         patch("routers.admin_invitations.send_email_async", new=AsyncMock()):
        await invites.send_invitation(body, _caller("super_admin"), _request())

    assert _actions() == ["invitation.sent"]
    (event,) = _queued()
    assert event["entity_type"] == audit.PROVISIONING_ENTITY_TYPE
    assert event["payload"]["invited_email"] == "invitee@example.com"
    assert event["payload"]["invited_role"] == "admin_staff"
    assert event["payload"]["caller_role"] == "super_admin"
    assert event["actor_user_id"] == ACTOR
    assert event["ip_address"] == "203.0.113.7"


@pytest.mark.asyncio
async def test_send_records_the_grant_even_when_the_email_fails():
    """The grant has already happened by the time the email is attempted.

    Auditing after the send would omit the record in exactly the situation where
    it matters most — a mail outage — and leave a claimable role with no trail.
    """
    body = invites.SendInvitationRequest(email="invitee@example.com", role="admin_staff")
    with patch("routers.admin_invitations.create_invitation",
               new=AsyncMock(return_value=(INVITE, "raw-token"))), \
         patch("routers.admin_invitations.send_email_async",
               new=AsyncMock(side_effect=RuntimeError("smtp down"))):
        result = await invites.send_invitation(body, _caller("super_admin"), _request())

    assert result["invitation_id"] == str(INVITE)   # request still succeeds
    assert _actions() == ["invitation.sent", "invitation.email_failed"]


@pytest.mark.asyncio
async def test_a_cross_tenant_invite_is_flagged_as_such():
    """A super_admin inviting into someone else's tenant is the highest-privilege
    use of this endpoint, and the flag is what makes it findable later."""
    body = invites.SendInvitationRequest(
        email="invitee@example.com", role="strata_manager",
        tenant_id="88888888-8888-8888-8888-888888888888",
    )
    with patch("routers.admin_invitations.create_invitation",
               new=AsyncMock(return_value=(INVITE, "raw-token"))), \
         patch("routers.admin_invitations.send_email_async", new=AsyncMock()):
        await invites.send_invitation(body, _caller("super_admin"), _request())

    event = _queued()[0]
    assert event["payload"]["cross_tenant"] is True
    # Recorded against the TARGET tenant's chain, not the caller's.
    assert event["tenant_id"] == "88888888-8888-8888-8888-888888888888"


@pytest.mark.asyncio
async def test_resend_is_its_own_event_because_it_mints_a_new_token():
    """A resend invalidates the old token and issues a new credential.

    Folding it into the original send would lose the fact that a second, live
    claimable token existed — and when.
    """
    invite_row = {
        "invitation_id": INVITE, "email": "invitee@example.com",
        "invited_role": "admin_staff", "scheme_id": SCHEME,
        "prefill_first_name": "Ann", "prefill_last_name": "Lee",
    }
    with patch("routers.admin_invitations.find_invitation_by_id",
               new=AsyncMock(return_value=invite_row)), \
         patch("routers.admin_invitations.refresh_invitation_token",
               new=AsyncMock(return_value="fresh-token")), \
         patch("routers.admin_invitations.send_email_async", new=AsyncMock()):
        await invites.resend_invitation(str(INVITE), _caller("super_admin"), _request())

    assert _actions() == ["invitation.resent"]
    assert _queued()[0]["payload"]["new_ttl_hours"] == 72


@pytest.mark.asyncio
async def test_claim_records_the_account_creation_and_both_halves_of_the_chain():
    """The single most material event in the router.

    The actor is the new user — this endpoint is unauthenticated, so there is no
    other identity to attribute it to — and `invited_by` preserves who granted
    the access that was just taken up. Without both, the trail answers "an
    account appeared" but not "on whose authority".
    """
    invite_row = {
        "invitation_id": INVITE, "email": "invitee@example.com",
        "invited_role": "owner", "scheme_id": SCHEME, "tenant_id": TENANT,
        "invited_by": ACTOR, "prefill_first_name": "Ann", "prefill_last_name": "Lee",
    }
    body = invites.ClaimInvitationRequest(password="Str0ng!Passw0rd")

    with patch("routers.admin_invitations.find_invitation_by_token",
               new=AsyncMock(return_value=invite_row)), \
         patch("routers.admin_invitations.create_user", new=AsyncMock(return_value=NEW_USER)), \
         patch("routers.admin_invitations.add_role_assignment", new=AsyncMock()), \
         patch("routers.admin_invitations.claim_invitation", new=AsyncMock()):
        await invites.claim_invitation_endpoint("raw-token", body, _request())

    assert _actions() == ["invitation.claimed"]
    (event,) = _queued()
    assert event["tenant_id"] == TENANT
    assert event["actor_user_id"] == NEW_USER
    assert event["payload"]["granted_role"] == "owner"
    assert event["payload"]["created_user_id"] == str(NEW_USER)
    assert event["payload"]["invited_by"] == ACTOR


@pytest.mark.asyncio
async def test_a_rejected_claim_creates_no_account_and_no_event():
    """A 404 must not leave a provisioning record implying something happened."""
    from fastapi import HTTPException

    body = invites.ClaimInvitationRequest(password="Str0ng!Passw0rd")
    with patch("routers.admin_invitations.find_invitation_by_token", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as excinfo:
            await invites.claim_invitation_endpoint("bad-token", body, _request())

    assert excinfo.value.status_code == 404
    assert _queued() == []


@pytest.mark.asyncio
async def test_an_audit_failure_never_fails_the_invitation():
    """The grant is already committed by the time the recorder runs.

    Raising here would leave system state and the caller's view disagreeing — the
    account exists but the caller is told it does not. A gap is logged instead.
    """
    body = invites.SendInvitationRequest(email="invitee@example.com", role="admin_staff")
    with patch("routers.admin_invitations.create_invitation",
               new=AsyncMock(return_value=(INVITE, "raw-token"))), \
         patch("routers.admin_invitations.send_email_async", new=AsyncMock()), \
         patch("routers.admin_invitations.record_event", side_effect=RuntimeError("audit down")):
        result = await invites.send_invitation(body, _caller("super_admin"), _request())

    assert result["invitation_id"] == str(INVITE)


@pytest.mark.asyncio
async def test_an_unrecorded_event_is_logged_as_an_audit_gap(caplog):
    """A silent drop is the failure mode this whole finding was about."""
    import logging

    body = invites.SendInvitationRequest(email="invitee@example.com", role="admin_staff")
    with caplog.at_level(logging.ERROR), \
         patch("routers.admin_invitations.create_invitation",
               new=AsyncMock(return_value=(INVITE, "raw-token"))), \
         patch("routers.admin_invitations.send_email_async", new=AsyncMock()), \
         patch("routers.admin_invitations.record_event", return_value=False):
        await invites.send_invitation(body, _caller("super_admin"), _request())

    assert "AUDIT GAP" in caplog.text


@pytest.mark.asyncio
async def test_the_endpoints_work_without_a_request_object():
    """`request` is defaulted so the router's own suite can call these directly.

    That default must not become a crash path — the audit call reads
    request.client and request.headers.
    """
    body = invites.SendInvitationRequest(email="invitee@example.com", role="admin_staff")
    with patch("routers.admin_invitations.create_invitation",
               new=AsyncMock(return_value=(INVITE, "raw-token"))), \
         patch("routers.admin_invitations.send_email_async", new=AsyncMock()):
        await invites.send_invitation(body, _caller("super_admin"))

    assert _actions() == ["invitation.sent"]
    assert _queued()[0]["ip_address"] is None


# ─── the recommendation that would have failed silently ──────────────────────

@pytest.mark.asyncio
async def test_the_mongo_audit_helper_would_have_silently_dropped_the_claim_event():
    """Why the audit report's `create_audit_log` recommendation was not followed.

    `audit_logs` is tenant-scoped. On the unauthenticated claim endpoint there is
    no building context and the log document carries no building_id, so
    `TenantCollection.insert_one` raises — and `create_audit_log` catches every
    exception and returns "". It reports nothing wrong while recording nothing.
    """
    from utils.helpers import create_audit_log
    from request_context import set_ctx_building_id
    from database import TENANT_SCOPED_COLLECTIONS

    assert "audit_logs" in TENANT_SCOPED_COLLECTIONS

    set_ctx_building_id(None)
    try:
        log_id = await create_audit_log(
            action="invitation.claimed", resource_type="invitation",
            resource_id=str(INVITE), user_id=NEW_USER, user_name="Ann Lee",
            details={"granted_role": "owner"}, building_id=None,
        )
    finally:
        set_ctx_building_id(None)

    assert log_id == "", (
        "create_audit_log returned an id — if this now works, revisit the "
        "Postgres-only decision recorded in admin_invitations' module docstring"
    )
