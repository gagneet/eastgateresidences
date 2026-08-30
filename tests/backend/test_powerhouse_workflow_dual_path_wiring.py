"""GAP-POWERHOUSE-001 Part A — dual-path write_source branch tests.

Verifies that the 5 workflow/automation Mongo-write functions in
powerhouse_conversation_service.py correctly branch to their PostgreSQL
command counterpart (in powerhouse_workflow_command_service.py) when
write_source=postgres, calling it with correctly mapped parameters, and
that the Mongo write path is never touched on that branch.

No equivalent test previously existed for this dual-path pattern anywhere in
this repo (create_thread/assign_thread's P2B-2/P2B-3 wiring was validated
only via live HTTP/DB testing, never a checked-in unit test) — this file
establishes that coverage for both the new functions here and as a template
for the older ones if backfilled later.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from models.cutover_status import DataSource
from models.powerhouse_conversation import (
    AutomationRuleRunRequest,
    LinkedEntityRef,
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


def _fake_result(reference: dict):
    from services.powerhouse_command_foundation import CommandResult, CommandStatus

    return CommandResult(status=CommandStatus.CREATED, aggregate_type="x", aggregate_id="x", result_reference=reference)


@pytest.fixture(autouse=True)
def _pg_context(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_write_source", AsyncMock(return_value=DataSource.postgres))
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=(TENANT_ID, SCHEME_ID)))
    monkeypatch.setattr(svc, "_resolve_pg_actor_user_id", AsyncMock(return_value=PG_ACTOR_ID))
    # Any Mongo db access on this branch is a bug — make it explode loudly rather
    # than silently succeed, so a regression that falls through to the Mongo
    # path is caught immediately instead of masked by a lenient mock.
    monkeypatch.setattr(svc.db.powerhouse_workflow_instances, "insert_one", AsyncMock(side_effect=AssertionError("Mongo path hit")))
    monkeypatch.setattr(svc.db.powerhouse_workflow_events, "insert_one", AsyncMock(side_effect=AssertionError("Mongo path hit")))
    monkeypatch.setattr(
        svc.db.powerhouse_workflow_status_history, "insert_one", AsyncMock(side_effect=AssertionError("Mongo path hit"))
    )
    monkeypatch.setattr(
        svc.db.powerhouse_workflow_assignments, "insert_one", AsyncMock(side_effect=AssertionError("Mongo path hit"))
    )
    monkeypatch.setattr(
        svc.db.powerhouse_automation_rule_runs, "insert_one", AsyncMock(side_effect=AssertionError("Mongo path hit"))
    )


@pytest.mark.asyncio
async def test_create_workflow_instance_branches_to_pg_command(monkeypatch):
    import services.powerhouse_workflow_command_service as wf_svc

    expected_reference = {"id": str(uuid4()), "status": "open", "title": "New instance"}
    mock_command = AsyncMock(return_value=_fake_result(expected_reference))
    monkeypatch.setattr(wf_svc, "create_workflow_instance_command", mock_command)

    payload = WorkflowInstanceCreate(
        template_key="maintenance-from-conversation",
        title="New instance",
        linked_entity=LinkedEntityRef(entity_type="maintenance_request", entity_id="mr-1"),
        initial_payload={"note": "urgent"},
    )

    result = await svc.create_workflow_instance(
        building_id=BUILDING_ID, current_user=MANAGER_USER, payload=payload, idempotency_key="idem-1"
    )

    assert result == expected_reference
    mock_command.assert_awaited_once()
    call_kwargs = mock_command.await_args.kwargs
    assert call_kwargs["building_id"] == BUILDING_ID
    assert call_kwargs["tenant_id"] == TENANT_ID
    assert call_kwargs["scheme_id"] == SCHEME_ID
    assert call_kwargs["actor_user_id"] == PG_ACTOR_ID
    assert call_kwargs["template_key"] == "maintenance-from-conversation"
    assert call_kwargs["title"] == "New instance"
    assert call_kwargs["linked_entity_type"] == "maintenance_request"
    assert call_kwargs["linked_entity_id"] == "mr-1"
    assert call_kwargs["initial_payload"] == {"note": "urgent"}
    assert call_kwargs["idempotency_key"] == "idem-1"


@pytest.mark.asyncio
async def test_create_workflow_instance_generates_idempotency_key_when_absent(monkeypatch):
    import services.powerhouse_workflow_command_service as wf_svc

    mock_command = AsyncMock(return_value=_fake_result({"id": "x"}))
    monkeypatch.setattr(wf_svc, "create_workflow_instance_command", mock_command)

    payload = WorkflowInstanceCreate(template_key="k", title="t")
    await svc.create_workflow_instance(building_id=BUILDING_ID, current_user=MANAGER_USER, payload=payload)

    assert mock_command.await_args.kwargs["idempotency_key"]  # non-empty, server-generated


@pytest.mark.asyncio
async def test_add_workflow_event_branches_to_pg_command(monkeypatch):
    import services.powerhouse_workflow_command_service as wf_svc

    instance_id = str(uuid4())
    expected_reference = {"id": str(uuid4()), "event_type": "note_added"}
    mock_command = AsyncMock(return_value=_fake_result(expected_reference))
    monkeypatch.setattr(wf_svc, "add_workflow_event_command", mock_command)

    payload = WorkflowEventCreate(event_type="note_added", payload={"note": "hi"})

    result = await svc.add_workflow_event(
        building_id=BUILDING_ID, instance_id=instance_id, current_user=MANAGER_USER, payload=payload, idempotency_key="idem-2"
    )

    assert result == expected_reference
    call_kwargs = mock_command.await_args.kwargs
    assert call_kwargs["instance_id"] == instance_id
    assert call_kwargs["event_type"] == "note_added"
    assert call_kwargs["event_payload"] == {"note": "hi"}
    assert call_kwargs["idempotency_key"] == "idem-2"


@pytest.mark.asyncio
async def test_complete_workflow_step_branches_to_pg_command(monkeypatch):
    import services.powerhouse_workflow_command_service as wf_svc

    instance_id = str(uuid4())
    expected_reference = {"id": instance_id, "step_key": "review", "step_status": "completed"}
    mock_command = AsyncMock(return_value=_fake_result(expected_reference))
    monkeypatch.setattr(wf_svc, "complete_workflow_step_command", mock_command)

    payload = WorkflowStepCompleteRequest(step_key="review", notes="done")

    result = await svc.complete_workflow_step(
        building_id=BUILDING_ID, instance_id=instance_id, current_user=MANAGER_USER, payload=payload, idempotency_key="idem-3"
    )

    assert result == expected_reference
    call_kwargs = mock_command.await_args.kwargs
    assert call_kwargs["instance_id"] == instance_id
    assert call_kwargs["step_key"] == "review"
    assert call_kwargs["notes"] == "done"


@pytest.mark.asyncio
async def test_complete_workflow_step_propagates_404_through_the_wrapper(monkeypatch):
    """Regression check for a gap found during the 2026-08-11 deep-dive audit.

    complete_workflow_step_command's own 404-on-unknown-step_key behavior was
    already unit-tested directly in test_powerhouse_workflow_command_service.py
    (P2B-5b) and this documented behavioral difference from the Mongo path was
    called out in the wiring commit — but nothing confirmed the 404 actually
    propagates cleanly through THIS wrapper (svc.complete_workflow_step) with
    no swallowing/reshaping in between. It does (a bare `return
    result.result_reference` after an awaited call that raises has nothing to
    catch it), but that was previously asserted only by code reading, not a
    test.
    """
    import services.powerhouse_workflow_command_service as wf_svc
    from fastapi import HTTPException

    instance_id = str(uuid4())
    mock_command = AsyncMock(side_effect=HTTPException(status_code=404, detail="Workflow instance not found"))
    monkeypatch.setattr(wf_svc, "complete_workflow_step_command", mock_command)

    payload = WorkflowStepCompleteRequest(step_key="never-materialised-step")

    with pytest.raises(HTTPException) as exc_info:
        await svc.complete_workflow_step(
            building_id=BUILDING_ID, instance_id=instance_id, current_user=MANAGER_USER, payload=payload
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_assign_workflow_task_branches_to_pg_command(monkeypatch):
    import services.powerhouse_workflow_command_service as wf_svc

    instance_id = str(uuid4())
    assignee_id = str(uuid4())
    expected_reference = {"id": str(uuid4()), "assignee_user_id": assignee_id}
    mock_command = AsyncMock(return_value=_fake_result(expected_reference))
    monkeypatch.setattr(wf_svc, "assign_workflow_task_command", mock_command)

    payload = WorkflowAssignmentCreate(assignee_user_id=assignee_id, step_key="review")

    result = await svc.assign_workflow_task(
        building_id=BUILDING_ID, instance_id=instance_id, current_user=MANAGER_USER, payload=payload, idempotency_key="idem-4"
    )

    assert result == expected_reference
    call_kwargs = mock_command.await_args.kwargs
    assert call_kwargs["instance_id"] == instance_id
    # assignee_user_id is passed straight through with no Mongo->PG resolution
    # (matching assign_thread_command's existing precedent) — regression
    # check for the id-resolution mistake caught and fixed while wiring this.
    assert call_kwargs["assignee_user_id"] == assignee_id
    assert call_kwargs["step_key"] == "review"


@pytest.mark.asyncio
async def test_run_automation_rule_branches_to_pg_execute_command(monkeypatch):
    import services.powerhouse_workflow_command_service as wf_svc

    expected_reference = {"id": str(uuid4()), "status": "simulated"}
    mock_command = AsyncMock(return_value=_fake_result(expected_reference))
    monkeypatch.setattr(wf_svc, "execute_automation_rule_command", mock_command)

    payload = AutomationRuleRunRequest(rule_key="overdue-invoice-escalation", dry_run=True)

    result = await svc.run_automation_rule_placeholder(
        building_id=BUILDING_ID, current_user=MANAGER_USER, payload=payload, idempotency_key="idem-5"
    )

    assert result == expected_reference
    call_kwargs = mock_command.await_args.kwargs
    assert call_kwargs["rule_key"] == "overdue-invoice-escalation"
    assert call_kwargs["dry_run"] is True


@pytest.mark.asyncio
async def test_pg_context_unavailable_raises_503(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=None))

    from fastapi import HTTPException

    payload = WorkflowInstanceCreate(template_key="k", title="t")
    with pytest.raises(HTTPException) as exc_info:
        await svc.create_workflow_instance(building_id=BUILDING_ID, current_user=MANAGER_USER, payload=payload)

    assert exc_info.value.status_code == 503
