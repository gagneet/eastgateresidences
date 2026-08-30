from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.powerhouse_command_foundation import (
    AuditEventSpec,
    CommandContext,
    CommandResult,
    CommandStatus,
    ContinuityReason,
    OutboxEventSpec,
    PowerhouseCommandUnitOfWork,
    RepositoryReadResult,
    RepositoryReadStatus,
    continuity_reason_for_read_result,
    seed_powerhouse_control_plane_statuses,
)


TENANT_ID = str(uuid4())
SCHEME_ID = str(uuid4())
USER_ID = str(uuid4())


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    def __init__(self, *, replay_row=None, fail_on_outbox: bool = False):
        self.replay_row = replay_row
        self.fail_on_outbox = fail_on_outbox
        self.calls: list[str] = []
        self.params: list[dict] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append(sql)
        self.params.append(params or {})
        if "INSERT INTO core.command_idempotency_records" in sql:
            return _Result(self.replay_row)
        if self.fail_on_outbox and "INSERT INTO core.outbox" in sql:
            raise RuntimeError("outbox insert failed")
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


def _context() -> CommandContext:
    return CommandContext(
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        building_id="13195",
        command_scope="powerhouse_conversations",
        command_type="create_conversation",
        idempotency_key="idem-1",
        actor_user_id=USER_ID,
        correlation_id="corr-1",
        is_test_data=True,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (RepositoryReadStatus.SUCCESS, None),
        (RepositoryReadStatus.NOT_FOUND, None),
        (RepositoryReadStatus.PERMISSION_FAILURE, None),
        (RepositoryReadStatus.APPLICATION_ERROR, None),
        (RepositoryReadStatus.UNAVAILABLE, ContinuityReason.POSTGRES_UNAVAILABLE),
        (RepositoryReadStatus.TIMEOUT, ContinuityReason.POSTGRES_TIMEOUT),
        (RepositoryReadStatus.SCHEMA_NOT_READY, ContinuityReason.POSTGRES_SCHEMA_NOT_READY),
    ],
)
def test_read_outcome_continuity_mapping_is_explicit(status, expected):
    result = RepositoryReadResult(status=status, value=[])

    assert continuity_reason_for_read_result(result) == expected
    assert result.can_activate_continuity is (expected is not None)


@pytest.mark.asyncio
async def test_unit_of_work_writes_domain_audit_outbox_and_idempotency_atomically():
    session = _FakeSession()
    ctx = _FakeSessionContext(session)
    aggregate_id = str(uuid4())

    async def mutate(fake_session, _context):
        await fake_session.execute("INSERT INTO communications.conversation_threads")
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="conversation_thread",
            aggregate_id=aggregate_id,
            result_reference={"thread_id": aggregate_id},
        )

    uow = PowerhouseCommandUnitOfWork(
        session_context_factory=lambda: ctx,
        set_tenant_func=_set_tenant,
    )

    result = await uow.execute(
        context=_context(),
        mutate=mutate,
        audit_factory=lambda _result: AuditEventSpec(
            entity_type="conversation_thread",
            action="powerhouse.conversation.created",
            payload={"source": "test"},
        ),
        outbox_factory=lambda _result: OutboxEventSpec(
            event_type="powerhouse.conversation.created",
            payload={"source": "test"},
        ),
    )

    assert result.status == CommandStatus.CREATED
    assert ctx.committed is True
    assert ctx.rolled_back is False
    joined = "\n".join(session.calls)
    assert "INSERT INTO communications.conversation_threads" in joined
    assert "INSERT INTO core.audit_events" in joined
    assert "INSERT INTO core.outbox" in joined
    assert "UPDATE core.command_idempotency_records" in joined


@pytest.mark.asyncio
async def test_unit_of_work_replays_completed_idempotency_without_mutation():
    aggregate_id = str(uuid4())
    session = _FakeSession(
        replay_row=SimpleNamespace(
            result_status="created",
            aggregate_type="conversation_thread",
            aggregate_id=aggregate_id,
            result_reference={"thread_id": aggregate_id},
            completed_at="2026-07-20T00:00:00Z",
        )
    )
    ctx = _FakeSessionContext(session)

    async def mutate(_session, _context):
        raise AssertionError("mutation must not run for completed idempotency replay")

    uow = PowerhouseCommandUnitOfWork(
        session_context_factory=lambda: ctx,
        set_tenant_func=_set_tenant,
    )

    result = await uow.execute(
        context=_context(),
        mutate=mutate,
        audit_factory=lambda _result: AuditEventSpec(entity_type="conversation_thread", action="created"),
        outbox_factory=lambda _result: OutboxEventSpec(event_type="powerhouse.conversation.created"),
    )

    assert result.status == CommandStatus.IDEMPOTENT_REPLAY
    assert result.aggregate_id == aggregate_id
    assert "INSERT INTO core.audit_events" not in "\n".join(session.calls)
    assert "INSERT INTO core.outbox" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_when_outbox_write_fails():
    session = _FakeSession(fail_on_outbox=True)
    ctx = _FakeSessionContext(session)
    aggregate_id = str(uuid4())

    async def mutate(fake_session, _context):
        await fake_session.execute("INSERT INTO communications.conversation_threads")
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="conversation_thread",
            aggregate_id=aggregate_id,
        )

    uow = PowerhouseCommandUnitOfWork(
        session_context_factory=lambda: ctx,
        set_tenant_func=_set_tenant,
    )

    with pytest.raises(RuntimeError, match="outbox insert failed"):
        await uow.execute(
            context=_context(),
            mutate=mutate,
            audit_factory=lambda _result: AuditEventSpec(entity_type="conversation_thread", action="created"),
            outbox_factory=lambda _result: OutboxEventSpec(event_type="powerhouse.conversation.created"),
        )

    assert ctx.committed is False
    assert ctx.rolled_back is True


@pytest.mark.asyncio
async def test_seed_powerhouse_control_plane_statuses_sets_postgres_primary_and_mongo_continuity():
    session = _FakeSession()

    domains = await seed_powerhouse_control_plane_statuses(
        session,
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        building_id="13195",
        is_test_data=True,
    )

    assert domains == [
        "powerhouse_conversations",
        "powerhouse_inbox",
        "powerhouse_workflows",
        "powerhouse_automation",
    ]
    assert len(session.calls) == 4
    assert all(params["tenant_id"] == TENANT_ID for params in session.params)
    assert all(params["is_test_data"] is True for params in session.params)
    joined = "\n".join(session.calls)
    assert "'postgres', 'postgres', 'postgres_write'" in joined
    assert "'mongo', 'classified_read_only'" in joined


def test_powerhouse_sqlalchemy_models_import_and_map_tables():
    from services.financial_core.adapters.db_postgres.models import PgOutbox  # noqa: PLC0415
    from db_postgres.powerhouse_models import (  # noqa: PLC0415
        PgPowerhouseCommandIdempotencyRecord,
        PgPowerhouseDomainCutoverStatusContinuity,
    )

    assert PgPowerhouseCommandIdempotencyRecord.__table__.schema == "core"
    assert PgPowerhouseCommandIdempotencyRecord.__tablename__ == "command_idempotency_records"
    assert PgPowerhouseDomainCutoverStatusContinuity.__table__.schema == "core"
    assert "continuity_policy" in PgPowerhouseDomainCutoverStatusContinuity.__table__.columns
    assert "correlation_id" in PgOutbox.__table__.columns
    assert "dead_lettered_at" in PgOutbox.__table__.columns
