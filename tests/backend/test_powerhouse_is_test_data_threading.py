"""GAP-POWERHOUSE-006 blocking sub-gap — is_test_data threading.

Verifies that is_test_data is correctly threaded from the HTTP payload
through to (a) the Mongo document/PG command call on every write this
session scoped for k6 coverage, and (b) the read-side list filters that
must exclude is_test_data=True records from real users by default.

Scope: the 8 write functions the planned powerhouse_benchmark.ts k6 script
will exercise (create_thread, add_message, update_thread_status,
create_workflow_instance, add_workflow_event, complete_workflow_step,
assign_workflow_task, run_automation_rule_placeholder) plus the two list
endpoints whose results those writes would otherwise leak into
(list_threads, list_automation_rule_runs). Does NOT cover the PostgreSQL
domain tables themselves (communications.*, workflow.*) — those have no
is_test_data column at all (confirmed by grepping the 0046 migration), a
separate, larger, not-yet-fixed gap tracked in
tasks/GAP-POWERHOUSE-006-missing-k6-performance-coverage.md. is_test_data is
still correctly threaded to the PG command calls' own kwarg in this fix,
which tags core.command_idempotency_records even though the domain row
itself isn't taggable yet.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from models.cutover_status import CutoverMode, DataSource
from models.powerhouse_conversation import (
    AutomationRuleRunRequest,
    ConversationMessageCreate,
    ConversationThreadCreate,
    ConversationThreadStatusUpdate,
    WorkflowAssignmentCreate,
    WorkflowEventCreate,
    WorkflowInstanceCreate,
    WorkflowStepCompleteRequest,
)
from services import powerhouse_conversation_service as svc

TENANT_ID = str(uuid4())
SCHEME_ID = str(uuid4())
PG_ACTOR_ID = str(uuid4())
BUILDING_ID = "13195"
MANAGER_USER = {"id": "mongo-user-1", "email": "manager@eastgate.com", "role": "strata_manager"}
OWNER_USER = {"id": "mongo-owner-1", "email": "owner@eastgate.com", "role": "owner"}


def _fake_result(reference: dict):
    from services.powerhouse_command_foundation import CommandResult, CommandStatus

    return CommandResult(status=CommandStatus.CREATED, aggregate_type="x", aggregate_id="x", result_reference=reference)


# ---------------------------------------------------------------------------
# Write-side: is_test_data reaches the Mongo doc and the PG command call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_thread_mongo_path_tags_doc_and_propagates_to_add_message(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_write_source", AsyncMock(return_value=DataSource.mongo))
    inserted = {}

    async def _capture_insert(doc):
        inserted.update(doc)
        return MagicMock()

    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "insert_one", AsyncMock(side_effect=_capture_insert))
    monkeypatch.setattr(svc, "_record_audit_event", AsyncMock())

    message_calls = []
    original_add_message = svc.add_message

    async def _spy_add_message(**kwargs):
        message_calls.append(kwargs)
        return {"id": "msg-1"}

    monkeypatch.setattr(svc, "add_message", _spy_add_message)

    payload = ConversationThreadCreate(subject="Perf test thread", body="body text", is_test_data=True)
    await svc.create_thread(building_id=BUILDING_ID, current_user=MANAGER_USER, payload=payload)

    assert inserted["is_test_data"] is True
    assert message_calls[0]["payload"].is_test_data is True


@pytest.mark.asyncio
async def test_create_thread_pg_path_passes_is_test_data_to_command(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_write_source", AsyncMock(return_value=DataSource.postgres))
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=(TENANT_ID, SCHEME_ID)))
    monkeypatch.setattr(svc, "_resolve_pg_actor_user_id", AsyncMock(return_value=PG_ACTOR_ID))

    import services.powerhouse_communications_command_service as comms_svc

    mock_command = AsyncMock(return_value=_fake_result({"id": str(uuid4())}))
    monkeypatch.setattr(comms_svc, "create_conversation_command", mock_command)

    payload = ConversationThreadCreate(subject="Perf test thread", body="body text", is_test_data=True)
    await svc.create_thread(building_id=BUILDING_ID, current_user=MANAGER_USER, payload=payload)

    assert mock_command.await_args.kwargs["is_test_data"] is True


@pytest.mark.asyncio
async def test_add_message_defaults_is_test_data_false_when_unset(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_write_source", AsyncMock(return_value=DataSource.mongo))
    monkeypatch.setattr(
        svc.db.powerhouse_conversation_threads, "find_one", AsyncMock(return_value={"id": "t1", "building_id": BUILDING_ID})
    )
    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "update_one", AsyncMock())
    monkeypatch.setattr(svc, "_record_audit_event", AsyncMock())
    inserted = {}

    async def _capture_insert(doc):
        inserted.update(doc)
        return MagicMock()

    monkeypatch.setattr(svc.db.powerhouse_conversation_messages, "insert_one", AsyncMock(side_effect=_capture_insert))

    payload = ConversationMessageCreate(body="real reply")  # is_test_data omitted -> defaults False
    await svc.add_message(building_id=BUILDING_ID, thread_id="t1", current_user=MANAGER_USER, payload=payload)

    assert inserted["is_test_data"] is False


@pytest.mark.asyncio
async def test_update_thread_status_pg_path_passes_is_test_data(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_write_source", AsyncMock(return_value=DataSource.postgres))
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=(TENANT_ID, SCHEME_ID)))
    monkeypatch.setattr(svc, "_resolve_pg_actor_user_id", AsyncMock(return_value=PG_ACTOR_ID))

    import services.powerhouse_communications_command_service as comms_svc

    mock_command = AsyncMock(return_value=_fake_result({"id": "t1", "status": "resolved"}))
    monkeypatch.setattr(comms_svc, "update_thread_status_command", mock_command)

    payload = ConversationThreadStatusUpdate(status="resolved", is_test_data=True)
    await svc.update_thread_status(building_id=BUILDING_ID, thread_id="t1", current_user=MANAGER_USER, payload=payload)

    assert mock_command.await_args.kwargs["is_test_data"] is True


@pytest.mark.asyncio
async def test_create_workflow_instance_pg_path_passes_is_test_data(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_write_source", AsyncMock(return_value=DataSource.postgres))
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=(TENANT_ID, SCHEME_ID)))
    monkeypatch.setattr(svc, "_resolve_pg_actor_user_id", AsyncMock(return_value=PG_ACTOR_ID))

    import services.powerhouse_workflow_command_service as wf_svc

    mock_command = AsyncMock(return_value=_fake_result({"id": str(uuid4()), "status": "open"}))
    monkeypatch.setattr(wf_svc, "create_workflow_instance_command", mock_command)

    payload = WorkflowInstanceCreate(template_key="k", title="t", is_test_data=True)
    await svc.create_workflow_instance(building_id=BUILDING_ID, current_user=MANAGER_USER, payload=payload)

    assert mock_command.await_args.kwargs["is_test_data"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_fn_name", "command_module_attr", "payload_factory"),
    [
        (
            "add_workflow_event",
            "add_workflow_event_command",
            lambda: WorkflowEventCreate(event_type="note_added", is_test_data=True),
        ),
        (
            "complete_workflow_step",
            "complete_workflow_step_command",
            lambda: WorkflowStepCompleteRequest(step_key="review", is_test_data=True),
        ),
        (
            "assign_workflow_task",
            "assign_workflow_task_command",
            lambda: WorkflowAssignmentCreate(assignee_user_id=str(uuid4()), is_test_data=True),
        ),
    ],
)
async def test_workflow_instance_scoped_writes_pass_is_test_data(
    monkeypatch, service_fn_name, command_module_attr, payload_factory
):
    monkeypatch.setattr(svc, "_resolve_write_source", AsyncMock(return_value=DataSource.postgres))
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=(TENANT_ID, SCHEME_ID)))
    monkeypatch.setattr(svc, "_resolve_pg_actor_user_id", AsyncMock(return_value=PG_ACTOR_ID))

    import services.powerhouse_workflow_command_service as wf_svc

    mock_command = AsyncMock(return_value=_fake_result({"id": str(uuid4())}))
    monkeypatch.setattr(wf_svc, command_module_attr, mock_command)

    service_fn = getattr(svc, service_fn_name)
    await service_fn(
        building_id=BUILDING_ID, instance_id=str(uuid4()), current_user=MANAGER_USER, payload=payload_factory()
    )

    assert mock_command.await_args.kwargs["is_test_data"] is True


@pytest.mark.asyncio
async def test_run_automation_rule_placeholder_pg_path_passes_is_test_data(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_write_source", AsyncMock(return_value=DataSource.postgres))
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=(TENANT_ID, SCHEME_ID)))
    monkeypatch.setattr(svc, "_resolve_pg_actor_user_id", AsyncMock(return_value=PG_ACTOR_ID))

    import services.powerhouse_workflow_command_service as wf_svc

    mock_command = AsyncMock(return_value=_fake_result({"id": str(uuid4())}))
    monkeypatch.setattr(wf_svc, "execute_automation_rule_command", mock_command)

    payload = AutomationRuleRunRequest(rule_key="overdue-invoice-escalation", is_test_data=True)
    await svc.run_automation_rule_placeholder(building_id=BUILDING_ID, current_user=MANAGER_USER, payload=payload)

    assert mock_command.await_args.kwargs["is_test_data"] is True


# ---------------------------------------------------------------------------
# Read-side: is_test_data=True records excluded by default, manager-gated opt-in
# ---------------------------------------------------------------------------


def _mongo_read_status():
    return MagicMock(mode=CutoverMode.mongo_primary, read_source=DataSource.mongo)


def _cursor(items):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=items)
    return cursor


@pytest.mark.asyncio
async def test_list_threads_excludes_test_data_by_default(monkeypatch):
    monkeypatch.setattr(
        "services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_mongo_read_status())
    )
    captured_query = {}

    def _find(query, *_args, **_kwargs):
        captured_query.update(query)
        return _cursor([])

    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find", _find)

    await svc.list_threads(
        building_id=BUILDING_ID, current_user=OWNER_USER, status=None, source_channel=None, limit=25, offset=0
    )

    assert captured_query["is_test_data"] == {"$ne": True}


@pytest.mark.asyncio
async def test_list_threads_include_test_data_ignored_for_non_manager(monkeypatch):
    monkeypatch.setattr(
        "services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_mongo_read_status())
    )
    captured_query = {}

    def _find(query, *_args, **_kwargs):
        captured_query.update(query)
        return _cursor([])

    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find", _find)

    await svc.list_threads(
        building_id=BUILDING_ID,
        current_user=OWNER_USER,  # not a manager role
        status=None,
        source_channel=None,
        limit=25,
        offset=0,
        include_test_data=True,
    )

    # A non-manager cannot see test data even if they pass include_test_data=true
    assert captured_query["is_test_data"] == {"$ne": True}


@pytest.mark.asyncio
async def test_list_threads_include_test_data_honoured_for_manager(monkeypatch):
    monkeypatch.setattr(
        "services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_mongo_read_status())
    )
    captured_query = {}

    def _find(query, *_args, **_kwargs):
        captured_query.update(query)
        return _cursor([])

    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find", _find)

    await svc.list_threads(
        building_id=BUILDING_ID,
        current_user=MANAGER_USER,
        status=None,
        source_channel=None,
        limit=25,
        offset=0,
        include_test_data=True,
    )

    assert "is_test_data" not in captured_query


@pytest.mark.asyncio
async def test_list_automation_rule_runs_excludes_test_data_by_default(monkeypatch):
    captured_query = {}

    def _find(query, *_args, **_kwargs):
        captured_query.update(query)
        return _cursor([])

    monkeypatch.setattr(svc.db.powerhouse_automation_rule_runs, "find", _find)

    await svc.list_automation_rule_runs(building_id=BUILDING_ID, current_user=MANAGER_USER)

    assert captured_query["is_test_data"] == {"$ne": True}


@pytest.mark.asyncio
async def test_list_automation_rule_runs_include_test_data_honoured(monkeypatch):
    captured_query = {}

    def _find(query, *_args, **_kwargs):
        captured_query.update(query)
        return _cursor([])

    monkeypatch.setattr(svc.db.powerhouse_automation_rule_runs, "find", _find)

    await svc.list_automation_rule_runs(building_id=BUILDING_ID, current_user=MANAGER_USER, include_test_data=True)

    assert "is_test_data" not in captured_query
