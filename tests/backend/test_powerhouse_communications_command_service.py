from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from models.powerhouse_conversation import ConversationMessageCreate, ConversationThreadCreate
from services.powerhouse_command_foundation import (
    AuditEventSpec,
    CommandStatus,
    OutboxEventSpec,
    PowerhouseCommandUnitOfWork,
)
import services.powerhouse_communications_command_service as command_service

TENANT_ID = str(uuid4())
SCHEME_ID = str(uuid4())
USER_ID = str(uuid4())


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or ([] if row is None else [row])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def _default_thread_row(thread_id: str) -> SimpleNamespace:
    """Fake row shape matching _fetch_thread_result_reference's SELECT."""
    return SimpleNamespace(
        id=thread_id,
        building_id="13195",
        subject="Water leak in unit 12",
        source_channel="portal_message",
        priority="normal",
        status="open",
        visibility="participants_only",
        linked_entity_type=None,
        linked_entity_id=None,
        source_external_id=None,
        assigned_to=None,
        sla_due_at=None,
        created_by=USER_ID,
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        is_archived=False,
        participant_ids=[USER_ID],
        watcher_ids=[],
    )


class _FakeSession:
    def __init__(self, *, replay_row=None, thread_exists: bool = True, thread_row: SimpleNamespace | None = None):
        self.replay_row = replay_row
        self.thread_exists = thread_exists
        self.thread_row = thread_row
        self.calls: list[str] = []
        self.params: list[dict] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append(sql)
        self.params.append(params or {})
        if "INSERT INTO core.command_idempotency_records" in sql:
            return _Result(self.replay_row)
        if "SELECT id::text AS id" in sql and "FROM communications.conversation_threads" in sql:
            return _Result(SimpleNamespace(id=params.get("thread_id")) if self.thread_exists else None)
        if "SELECT 1 FROM communications.conversation_threads" in sql:
            return _Result(SimpleNamespace() if self.thread_exists else None)
        if "participant_ids" in sql:
            if not self.thread_exists:
                return _Result(None)
            row = self.thread_row or _default_thread_row(params.get("thread_id"))
            return _Result(row)
        return _Result()


class _FakeSessionContext:
    def __init__(self, session):
        self.session = session
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            self.rolled_back = True
        else:
            self.committed = True
        return False


async def _set_tenant(_session, _tenant_id):
    return None


def _fake_uow(session: _FakeSession) -> PowerhouseCommandUnitOfWork:
    ctx = _FakeSessionContext(session)
    return PowerhouseCommandUnitOfWork(session_context_factory=lambda: ctx, set_tenant_func=_set_tenant)


@pytest.fixture(autouse=True)
def _restore_uow(monkeypatch):
    """Ensure each test's monkeypatched _uow doesn't leak into other tests."""
    original = command_service._uow
    yield
    monkeypatch.setattr(command_service, "_uow", original, raising=True)


@pytest.mark.asyncio
async def test_create_conversation_command_writes_thread_participants_and_message(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    payload = ConversationThreadCreate(
        subject="Water leak in unit 12",
        body="Reported by owner this morning.",
        participant_ids=[str(uuid4())],
    )

    result = await command_service.create_conversation_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        payload=payload,
        idempotency_key="idem-create-1",
        is_test_data=True,
    )

    assert result.status == CommandStatus.CREATED
    assert result.aggregate_type == "conversation_thread"
    assert result.result_reference["subject"] == "Water leak in unit 12"
    assert USER_ID in result.result_reference["participant_ids"]
    assert result.result_reference["initial_message_id"]

    joined = "\n".join(session.calls)
    assert "INSERT INTO communications.conversation_threads" in joined
    assert "INSERT INTO communications.conversation_participants" in joined
    assert "INSERT INTO communications.conversation_messages" in joined
    assert "INSERT INTO core.audit_events" in joined
    assert "INSERT INTO core.outbox" in joined


@pytest.mark.asyncio
async def test_create_conversation_command_rejects_non_uuid_participant(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    payload = ConversationThreadCreate(
        subject="Test",
        body="Body",
        participant_ids=["mgr-1"],  # legacy non-UUID id, e.g. stale test data shape
    )

    with pytest.raises(HTTPException) as exc_info:
        await command_service.create_conversation_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            payload=payload,
            idempotency_key="idem-create-2",
        )

    assert exc_info.value.status_code == 422
    # Nothing should have been written to the DB before the id validation error.
    assert session.calls == []


@pytest.mark.asyncio
async def test_create_conversation_command_replays_idempotent_key(monkeypatch):
    aggregate_id = str(uuid4())
    session = _FakeSession(
        replay_row=SimpleNamespace(
            result_status="created",
            aggregate_type="conversation_thread",
            aggregate_id=aggregate_id,
            result_reference={"id": aggregate_id, "subject": "Test"},
            completed_at="2026-08-10T00:00:00Z",
        )
    )
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    payload = ConversationThreadCreate(subject="Test", body="Body")

    result = await command_service.create_conversation_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        payload=payload,
        idempotency_key="idem-create-replay",
    )

    assert result.status == CommandStatus.IDEMPOTENT_REPLAY
    assert result.aggregate_id == aggregate_id
    assert "INSERT INTO communications.conversation_threads" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_add_message_command_appends_to_existing_thread(monkeypatch):
    session = _FakeSession(thread_exists=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    payload = ConversationMessageCreate(body="Follow-up message")

    result = await command_service.add_message_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=str(uuid4()),
        actor_user_id=USER_ID,
        payload=payload,
        idempotency_key="idem-msg-1",
        is_test_data=True,
    )

    assert result.status == CommandStatus.CREATED
    assert result.aggregate_type == "conversation_message"
    assert result.result_reference["body"] == "Follow-up message"
    joined = "\n".join(session.calls)
    assert "INSERT INTO communications.conversation_messages" in joined
    assert "UPDATE communications.conversation_threads" in joined
    assert "INSERT INTO core.audit_events" in joined
    assert "INSERT INTO core.outbox" in joined


@pytest.mark.asyncio
async def test_add_message_command_404s_when_thread_missing(monkeypatch):
    session = _FakeSession(thread_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    payload = ConversationMessageCreate(body="Orphan message")

    with pytest.raises(HTTPException) as exc_info:
        await command_service.add_message_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            payload=payload,
            idempotency_key="idem-msg-2",
        )

    assert exc_info.value.status_code == 404
    assert "INSERT INTO communications.conversation_messages" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_add_message_command_404s_on_replay_of_a_prior_not_found(monkeypatch):
    """A retried Idempotency-Key must not turn a 404 into a silent 200 on replay.

    PowerhouseCommandUnitOfWork's idempotency replay always returns
    status=IDEMPOTENT_REPLAY regardless of what the original call resolved to
    — status alone can't distinguish "replay of a create" from "replay of a
    404". Regression test for that gap.
    """
    replay_row = SimpleNamespace(
        result_status="not_found",
        aggregate_type="conversation_thread",  # only ever set on the NOT_FOUND path
        aggregate_id=str(uuid4()),
        result_reference={},
        completed_at="2026-08-10T00:00:00Z",
    )
    session = _FakeSession(replay_row=replay_row)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    payload = ConversationMessageCreate(body="retry after 404")

    with pytest.raises(HTTPException) as exc_info:
        await command_service.add_message_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            payload=payload,
            idempotency_key="reused-key-after-404",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_add_message_command_rejects_non_uuid_thread_id(monkeypatch):
    session = _FakeSession(thread_exists=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    payload = ConversationMessageCreate(body="Message")

    with pytest.raises(HTTPException) as exc_info:
        await command_service.add_message_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id="not-a-uuid",
            actor_user_id=USER_ID,
            payload=payload,
            idempotency_key="idem-msg-3",
        )

    assert exc_info.value.status_code == 422
    assert session.calls == []


# ---------------------------------------------------------------------------
# update_thread_status_command / assign_thread_command / add_watcher_command /
# remove_watcher_command / add_link_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_thread_status_command_updates_and_returns_thread(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    thread_id = str(uuid4())

    result = await command_service.update_thread_status_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=thread_id,
        actor_user_id=USER_ID,
        status="resolved",
        idempotency_key="idem-status-1",
    )

    assert result.status == CommandStatus.UPDATED
    assert result.aggregate_type == "conversation_thread"
    assert result.result_reference["updated_by"] == USER_ID
    joined = "\n".join(session.calls)
    assert "UPDATE communications.conversation_threads" in joined
    assert "SET status = :status" in joined
    assert "INSERT INTO core.audit_events" in joined
    assert "INSERT INTO core.outbox" in joined


@pytest.mark.asyncio
async def test_update_thread_status_command_404s_when_thread_missing(monkeypatch):
    session = _FakeSession(thread_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.update_thread_status_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            status="resolved",
            idempotency_key="idem-status-2",
        )

    assert exc_info.value.status_code == 404
    assert "INSERT INTO core.audit_events" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_update_thread_status_command_404s_on_replay_of_a_prior_not_found(monkeypatch):
    """Same replay-safety requirement as add_message_command, via result_reference this time."""
    replay_row = SimpleNamespace(
        result_status="not_found",
        aggregate_type="conversation_thread",
        aggregate_id=str(uuid4()),
        result_reference={},
        completed_at="2026-08-10T00:00:00Z",
    )
    session = _FakeSession(replay_row=replay_row)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.update_thread_status_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            status="resolved",
            idempotency_key="reused-status-key",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_assign_thread_command_updates_and_returns_thread(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    assignee = str(uuid4())

    result = await command_service.assign_thread_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=str(uuid4()),
        actor_user_id=USER_ID,
        assignee_user_id=assignee,
        idempotency_key="idem-assign-1",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "SET assigned_to_user_id = CAST(:assignee_id AS UUID)" in joined


@pytest.mark.asyncio
async def test_assign_thread_command_rejects_non_uuid_assignee(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.assign_thread_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            assignee_user_id="mgr-1",
            idempotency_key="idem-assign-2",
        )

    assert exc_info.value.status_code == 422
    assert session.calls == []


@pytest.mark.asyncio
async def test_add_watcher_command_inserts_and_returns_thread(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    watcher = str(uuid4())

    result = await command_service.add_watcher_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=str(uuid4()),
        actor_user_id=USER_ID,
        watcher_id=watcher,
        idempotency_key="idem-watcher-1",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "INSERT INTO communications.conversation_watchers" in joined
    assert "ON CONFLICT DO NOTHING" in joined


@pytest.mark.asyncio
async def test_add_watcher_command_404s_when_thread_missing(monkeypatch):
    session = _FakeSession(thread_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.add_watcher_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            watcher_id=str(uuid4()),
            idempotency_key="idem-watcher-2",
        )

    assert exc_info.value.status_code == 404
    assert "INSERT INTO communications.conversation_watchers" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_remove_watcher_command_deletes_and_returns_thread(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    watcher = str(uuid4())

    result = await command_service.remove_watcher_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=str(uuid4()),
        actor_user_id=USER_ID,
        watcher_id=watcher,
        idempotency_key="idem-unwatch-1",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "DELETE FROM communications.conversation_watchers" in joined


@pytest.mark.asyncio
async def test_remove_watcher_command_404s_when_thread_missing(monkeypatch):
    session = _FakeSession(thread_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.remove_watcher_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            watcher_id=str(uuid4()),
            idempotency_key="idem-unwatch-2",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_add_link_command_inserts_and_returns_link_doc(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    thread_id = str(uuid4())

    result = await command_service.add_link_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=thread_id,
        actor_user_id=USER_ID,
        entity_type="maintenance_request",
        entity_id="mr-42",
        idempotency_key="idem-link-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.aggregate_type == "conversation_link"
    assert result.result_reference["thread_id"] == thread_id
    assert result.result_reference["entity_type"] == "maintenance_request"
    assert result.result_reference["entity_id"] == "mr-42"
    joined = "\n".join(session.calls)
    assert "INSERT INTO communications.conversation_links" in joined
    assert "UPDATE communications.conversation_threads" in joined
    assert "SET linked_entity_type = :entity_type" in joined


@pytest.mark.asyncio
async def test_add_link_command_404s_when_thread_missing(monkeypatch):
    session = _FakeSession(thread_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.add_link_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            entity_type="maintenance_request",
            entity_id="mr-42",
            idempotency_key="idem-link-2",
        )

    assert exc_info.value.status_code == 404
    assert "INSERT INTO communications.conversation_links" not in "\n".join(session.calls)


# ---------------------------------------------------------------------------
# set_thread_sla_command / add_participant_command / remove_participant_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_thread_sla_command_updates_and_returns_thread(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    thread_id = str(uuid4())
    due = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    result = await command_service.set_thread_sla_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=thread_id,
        actor_user_id=USER_ID,
        sla_due_at=due,
        idempotency_key="idem-sla-1",
    )

    assert result.status == CommandStatus.UPDATED
    assert result.result_reference["updated_by"] == USER_ID
    joined = "\n".join(session.calls)
    assert "SET sla_due_at = :sla_due_at" in joined
    assert "INSERT INTO core.audit_events" in joined
    assert "INSERT INTO core.outbox" in joined


@pytest.mark.asyncio
async def test_set_thread_sla_command_accepts_none_to_clear(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.set_thread_sla_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=str(uuid4()),
        actor_user_id=USER_ID,
        sla_due_at=None,
        idempotency_key="idem-sla-2",
    )

    assert result.status == CommandStatus.UPDATED
    # find the SLA UPDATE call's bound params specifically (not the idempotency INSERT's)
    sla_call_params = next(p for c, p in zip(session.calls, session.params) if "SET sla_due_at" in c)
    assert sla_call_params["sla_due_at"] is None


@pytest.mark.asyncio
async def test_set_thread_sla_command_404s_when_thread_missing(monkeypatch):
    session = _FakeSession(thread_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.set_thread_sla_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            sla_due_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            idempotency_key="idem-sla-3",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_add_participant_command_inserts_and_returns_thread(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    participant = str(uuid4())

    result = await command_service.add_participant_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=str(uuid4()),
        actor_user_id=USER_ID,
        participant_id=participant,
        idempotency_key="idem-participant-1",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "INSERT INTO communications.conversation_participants" in joined
    assert "ON CONFLICT DO NOTHING" in joined


@pytest.mark.asyncio
async def test_add_participant_command_404s_when_thread_missing(monkeypatch):
    session = _FakeSession(thread_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.add_participant_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            participant_id=str(uuid4()),
            idempotency_key="idem-participant-2",
        )

    assert exc_info.value.status_code == 404
    assert "INSERT INTO communications.conversation_participants" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_remove_participant_command_deletes_and_returns_thread(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    participant = str(uuid4())

    result = await command_service.remove_participant_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        thread_id=str(uuid4()),
        actor_user_id=USER_ID,
        participant_id=participant,
        idempotency_key="idem-unparticipant-1",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "DELETE FROM communications.conversation_participants" in joined


@pytest.mark.asyncio
async def test_remove_participant_command_404s_when_thread_missing(monkeypatch):
    session = _FakeSession(thread_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.remove_participant_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            thread_id=str(uuid4()),
            actor_user_id=USER_ID,
            participant_id=str(uuid4()),
            idempotency_key="idem-unparticipant-2",
        )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Live integration test — real Postgres, real East Gate (13195) tenant/scheme
# context, every row tagged is_test_data=True and cleaned up afterward.
# ---------------------------------------------------------------------------


@pytest.fixture
async def _live_east_gate_context():
    if not os.getenv("RUN_INTEGRATION_TESTS"):
        pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1")

    from db_postgres.repos.config_repo import resolve_scheme_context

    scheme = await resolve_scheme_context("13195")
    if not scheme:
        pytest.skip("East Gate (13195) has no resolvable PostgreSQL scheme context in this environment")
    yield str(scheme["tenant_id"]), str(scheme["scheme_id"])


@pytest.mark.asyncio
async def test_live_create_conversation_and_add_message_against_real_postgres(_live_east_gate_context):
    tenant_id, scheme_id = _live_east_gate_context
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    actor_user_id = None
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        row = await session.execute(
            text("SELECT id::text AS id FROM core.users WHERE email = 'manager@eastgate.com' LIMIT 1")
        )
        found = row.fetchone()
        actor_user_id = found.id if found else None
    if not actor_user_id:
        pytest.skip("No manager@eastgate.com user resolvable in core.users for this environment")

    payload = ConversationThreadCreate(subject="P2B-2 live test", body="Live integration test message")
    create_key = f"p2b2-live-test-{uuid4()}"

    thread_result = await command_service.create_conversation_command(
        building_id="13195",
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        actor_user_id=actor_user_id,
        payload=payload,
        idempotency_key=create_key,
        is_test_data=True,
    )
    thread_id = thread_result.aggregate_id
    assert thread_result.status == CommandStatus.CREATED

    replay_result = await command_service.create_conversation_command(
        building_id="13195",
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        actor_user_id=actor_user_id,
        payload=payload,
        idempotency_key=create_key,
        is_test_data=True,
    )
    assert replay_result.status == CommandStatus.IDEMPOTENT_REPLAY
    assert replay_result.aggregate_id == thread_id

    message_result = await command_service.add_message_command(
        building_id="13195",
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        thread_id=thread_id,
        actor_user_id=actor_user_id,
        payload=ConversationMessageCreate(body="Live reply"),
        idempotency_key=f"p2b2-live-test-msg-{uuid4()}",
        is_test_data=True,
    )
    assert message_result.status == CommandStatus.CREATED

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        messages = await session.execute(
            text("SELECT COUNT(*) AS n FROM communications.conversation_messages WHERE thread_id = CAST(:tid AS UUID)"),
            {"tid": thread_id},
        )
        assert messages.fetchone().n == 2

        # Teardown: this test tags every row is_test_data=True but East Gate
        # is a real building, not a disposable test tenant — clean up
        # explicitly rather than relying on the pytest_sessionfinish sweep.
        await session.execute(
            text("DELETE FROM communications.conversation_messages WHERE thread_id = CAST(:tid AS UUID)"),
            {"tid": thread_id},
        )
        await session.execute(
            text("DELETE FROM communications.conversation_participants WHERE thread_id = CAST(:tid AS UUID)"),
            {"tid": thread_id},
        )
        await session.execute(
            text("DELETE FROM communications.conversation_threads WHERE id = CAST(:tid AS UUID)"),
            {"tid": thread_id},
        )
        await session.execute(
            text("DELETE FROM core.command_idempotency_records WHERE idempotency_key LIKE 'p2b2-live-test%'")
        )
        await session.execute(
            text("DELETE FROM core.outbox WHERE payload->>'thread_id' = :tid"),
            {"tid": thread_id},
        )
        await session.execute(
            text("DELETE FROM core.audit_events WHERE entity_id = CAST(:tid AS UUID)"),
            {"tid": thread_id},
        )
        await session.commit()
