from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.powerhouse_command_foundation import CommandStatus, PowerhouseCommandUnitOfWork
import services.powerhouse_workflow_command_service as command_service

TENANT_ID = str(uuid4())
SCHEME_ID = str(uuid4())
USER_ID = str(uuid4())


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    def __init__(
        self,
        *,
        replay_row=None,
        template_exists: bool = False,
        current_version=None,
        active_template: dict | None = None,
        instance_exists: bool = True,
        instance_status: str = "open",
        instance_id: str | None = None,
        step_exists: bool = True,
        step_status: str = "pending",
        step_id: str | None = None,
        rule_conflict: bool = False,
        rule_missing: bool = False,
        rule_id: str | None = None,
        rule_enabled: bool = False,
        rule_current_name: str = "Existing rule",
        rule_current_description: str | None = None,
        rule_current_requires_approval: bool = True,
        run_exists: bool = True,
        run_id: str | None = None,
        run_rule_id: str | None = None,
        run_rule_key: str = "test-rule",
        run_status: str = "running",
    ):
        self.replay_row = replay_row
        self.template_exists = template_exists
        self.current_version = current_version
        self.active_template = active_template
        self.instance_exists = instance_exists
        self.instance_status = instance_status
        self.instance_id = instance_id or str(uuid4())
        self.step_exists = step_exists
        self.step_status = step_status
        self.step_id = step_id or str(uuid4())
        self.rule_conflict = rule_conflict
        self.rule_missing = rule_missing
        self.rule_id = rule_id or str(uuid4())
        self.rule_enabled = rule_enabled
        self.rule_current_name = rule_current_name
        self.rule_current_description = rule_current_description
        self.rule_current_requires_approval = rule_current_requires_approval
        self.run_exists = run_exists
        self.run_id = run_id or str(uuid4())
        self.run_rule_id = run_rule_id or str(uuid4())
        self.run_rule_key = run_rule_key
        self.run_status = run_status
        self.calls: list[str] = []
        self.params: list[dict] = []

    def _instance_row(self):
        now = datetime.now(UTC)
        return SimpleNamespace(
            id=self.instance_id,
            building_id="13195",
            template_id=self.active_template["id"] if self.active_template else None,
            template_key=self.active_template.get("template_key") if self.active_template else None,
            title="Test instance",
            status=self.instance_status,
            linked_entity_type=None,
            linked_entity_id=None,
            payload={},
            created_by=USER_ID,
            created_at=now,
            updated_at=now,
        )

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append(sql)
        self.params.append(params or {})
        if "INSERT INTO core.command_idempotency_records" in sql:
            return _Result(self.replay_row)
        if "SELECT 1 FROM workflow.workflow_templates" in sql:
            return _Result(SimpleNamespace() if self.template_exists else None)
        if "SELECT version FROM workflow.workflow_templates" in sql:
            return _Result(SimpleNamespace(version=self.current_version) if self.current_version is not None else None)
        if "SELECT id::text AS id, definition_json" in sql:
            if not self.active_template:
                return _Result(None)
            return _Result(
                SimpleNamespace(id=self.active_template["id"], definition_json=self.active_template["definition_json"])
            )
        if "i.id::text AS id" in sql:
            if not self.instance_exists:
                return _Result(None)
            return _Result(self._instance_row())
        if "SELECT status FROM workflow.workflow_instances" in sql:
            return _Result(SimpleNamespace(status=self.instance_status) if self.instance_exists else None)
        if "SELECT status FROM workflow.workflow_steps" in sql:
            return _Result(SimpleNamespace(status=self.step_status) if self.step_exists else None)
        if "SELECT 1 FROM workflow.workflow_instances" in sql:
            return _Result(SimpleNamespace() if self.instance_exists else None)
        if "SELECT id::text AS id FROM workflow.workflow_steps" in sql:
            return _Result(SimpleNamespace(id=self.step_id) if self.step_exists else None)
        if "SELECT 1 FROM workflow.automation_rules" in sql:
            return _Result(SimpleNamespace() if self.rule_conflict else None)
        if "id::text AS id, name, description, requires_human_approval" in sql:
            if self.rule_missing:
                return _Result(None)
            return _Result(
                SimpleNamespace(
                    id=self.rule_id,
                    name=self.rule_current_name,
                    description=self.rule_current_description,
                    requires_human_approval=self.rule_current_requires_approval,
                )
            )
        if "SELECT id::text AS id FROM workflow.automation_rules" in sql:
            return _Result(SimpleNamespace(id=self.rule_id) if not self.rule_missing else None)
        if "rule_id::text AS rule_id, rule_key FROM" in sql:
            return _Result(
                SimpleNamespace(rule_id=self.run_rule_id, rule_key=self.run_rule_key) if self.run_exists else None
            )
        if "SELECT 1 FROM workflow.automation_rule_runs" in sql:
            return _Result(SimpleNamespace() if self.run_exists else None)
        if "rule_id::text AS rule_id" in sql:
            if not self.run_exists:
                return _Result(None)
            now = datetime.now(UTC)
            return _Result(
                SimpleNamespace(
                    id=self.run_id,
                    rule_id=self.run_rule_id,
                    rule_key=self.run_rule_key,
                    dry_run=True,
                    status=self.run_status,
                    result_json={},
                    created_by=USER_ID,
                    created_at=now,
                )
            )
        if "r.id::text AS id" in sql:
            now = datetime.now(UTC)
            return _Result(
                SimpleNamespace(
                    id=self.rule_id,
                    building_id="13195",
                    rule_key="test-rule",
                    name=self.rule_current_name,
                    description=self.rule_current_description,
                    enabled=self.rule_enabled,
                    requires_human_approval=self.rule_current_requires_approval,
                    conditions=[],
                    actions=[],
                    created_at=now,
                )
            )
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
    original = command_service._uow
    yield
    monkeypatch.setattr(command_service, "_uow", original, raising=True)


@pytest.mark.asyncio
async def test_create_workflow_definition_command_creates_version_1(monkeypatch):
    session = _FakeSession(template_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.create_workflow_definition_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        template_key="maintenance-from-conversation",
        name="Maintenance from conversation",
        description=None,
        definition_json={"steps": []},
        idempotency_key="idem-def-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.result_reference["version"] == 1
    assert result.result_reference["is_active"] is True
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.workflow_templates" in joined
    assert "INSERT INTO core.audit_events" in joined
    assert "INSERT INTO core.outbox" in joined


@pytest.mark.asyncio
async def test_create_workflow_definition_command_409s_when_template_key_exists(monkeypatch):
    session = _FakeSession(template_exists=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.create_workflow_definition_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            template_key="maintenance-from-conversation",
            name="dup",
            description=None,
            definition_json={},
            idempotency_key="idem-def-2",
        )

    assert exc_info.value.status_code == 409
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.workflow_templates" not in joined


@pytest.mark.asyncio
async def test_publish_workflow_definition_version_command_bumps_version(monkeypatch):
    session = _FakeSession(current_version=1)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.publish_workflow_definition_version_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        template_key="maintenance-from-conversation",
        name="Maintenance from conversation v2",
        description=None,
        definition_json={"steps": [{"step_key": "review"}]},
        idempotency_key="idem-pub-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.result_reference["version"] == 2
    joined = "\n".join(session.calls)
    assert "SET is_active = FALSE" in joined
    assert "INSERT INTO workflow.workflow_templates" in joined


@pytest.mark.asyncio
async def test_publish_workflow_definition_version_command_404s_when_no_prior_version(monkeypatch):
    session = _FakeSession(current_version=None)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.publish_workflow_definition_version_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            template_key="never-created",
            name="x",
            description=None,
            definition_json={},
            idempotency_key="idem-pub-2",
        )

    assert exc_info.value.status_code == 404
    joined = "\n".join(session.calls)
    assert "SET is_active = FALSE" not in joined


# ---------------------------------------------------------------------------
# P2B-5b — instance creation + step transition commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow_instance_command_without_template_creates_no_steps(monkeypatch):
    session = _FakeSession(active_template=None, instance_status="open")
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.create_workflow_instance_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        template_key="unregistered-template",
        title="New maintenance workflow",
        idempotency_key="idem-inst-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.result_reference["template_id"] is None
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.workflow_instances" in joined
    assert "INSERT INTO workflow.workflow_steps" not in joined


@pytest.mark.asyncio
async def test_create_workflow_instance_command_with_template_materialises_steps(monkeypatch):
    template_id = str(uuid4())
    session = _FakeSession(
        active_template={
            "id": template_id,
            "template_key": "maintenance-from-conversation",
            "definition_json": {
                "steps": [
                    {"step_key": "review", "step_name": "Review"},
                    {"step_key": "approve", "step_name": "Approve"},
                ]
            },
        },
    )
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.create_workflow_instance_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        template_key="maintenance-from-conversation",
        title="New maintenance workflow",
        idempotency_key="idem-inst-2",
    )

    assert result.status == CommandStatus.CREATED
    step_inserts = [c for c in session.calls if "INSERT INTO workflow.workflow_steps" in c]
    assert len(step_inserts) == 2
    step_keys = [p["step_key"] for p in session.params if "step_key" in p and "instance_id" in p and "position" in p]
    assert step_keys == ["review", "approve"]


@pytest.mark.asyncio
async def test_start_workflow_command_transitions_to_in_progress(monkeypatch):
    instance_id = str(uuid4())
    session = _FakeSession(instance_exists=True, instance_status="open", instance_id=instance_id)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.start_workflow_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        instance_id=instance_id,
        actor_user_id=USER_ID,
        idempotency_key="idem-start-1",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.workflow_instances" in joined
    assert "INSERT INTO workflow.workflow_status_history" in joined
    update_params = [p for p in session.params if p.get("to_status") == "in_progress"]
    assert update_params


@pytest.mark.asyncio
async def test_start_workflow_command_404s_when_instance_missing(monkeypatch):
    session = _FakeSession(instance_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.start_workflow_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            instance_id=str(uuid4()),
            actor_user_id=USER_ID,
            idempotency_key="idem-start-2",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("command_name", "expected_status"),
    [
        ("pause_workflow_command", "paused"),
        ("resume_workflow_command", "in_progress"),
        ("cancel_workflow_command", "cancelled"),
        ("escalate_workflow_command", "escalated"),
        ("close_workflow_command", "closed"),
    ],
)
@pytest.mark.asyncio
async def test_instance_transition_commands_update_instance_status(monkeypatch, command_name, expected_status):
    instance_id = str(uuid4())
    session = _FakeSession(instance_exists=True, instance_status="in_progress", instance_id=instance_id)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    command_fn = getattr(command_service, command_name)

    result = await command_fn(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        instance_id=instance_id,
        actor_user_id=USER_ID,
        idempotency_key=f"idem-{command_name}",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.workflow_instances" in joined
    assert "INSERT INTO workflow.workflow_status_history" in joined
    update_params = [p for p in session.params if p.get("to_status") == expected_status]
    assert update_params


@pytest.mark.parametrize(
    "command_name",
    [
        "pause_workflow_command",
        "resume_workflow_command",
        "cancel_workflow_command",
        "escalate_workflow_command",
        "close_workflow_command",
    ],
)
@pytest.mark.asyncio
async def test_instance_transition_commands_404_when_instance_missing(monkeypatch, command_name):
    session = _FakeSession(instance_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    command_fn = getattr(command_service, command_name)

    with pytest.raises(HTTPException) as exc_info:
        await command_fn(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            instance_id=str(uuid4()),
            actor_user_id=USER_ID,
            idempotency_key=f"idem-{command_name}-404",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("command_name", "expected_status"),
    [
        ("complete_workflow_step_command", "completed"),
        ("fail_workflow_step_command", "failed"),
        ("retry_workflow_step_command", "pending"),
    ],
)
@pytest.mark.asyncio
async def test_step_transition_commands_update_step_status(monkeypatch, command_name, expected_status):
    instance_id = str(uuid4())
    session = _FakeSession(instance_exists=True, step_exists=True, instance_id=instance_id)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    command_fn = getattr(command_service, command_name)

    result = await command_fn(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        instance_id=instance_id,
        actor_user_id=USER_ID,
        step_key="review",
        idempotency_key=f"idem-{command_name}",
    )

    assert result.status == CommandStatus.UPDATED
    assert result.result_reference["step_key"] == "review"
    assert result.result_reference["step_status"] == expected_status
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.workflow_steps" in joined
    assert "INSERT INTO workflow.workflow_status_history" in joined


@pytest.mark.parametrize(
    "command_name",
    ["complete_workflow_step_command", "fail_workflow_step_command", "retry_workflow_step_command"],
)
@pytest.mark.asyncio
async def test_step_transition_commands_404_when_step_missing(monkeypatch, command_name):
    session = _FakeSession(instance_exists=True, step_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    command_fn = getattr(command_service, command_name)

    with pytest.raises(HTTPException) as exc_info:
        await command_fn(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            instance_id=str(uuid4()),
            actor_user_id=USER_ID,
            step_key="missing-step",
            idempotency_key=f"idem-{command_name}-404",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_add_workflow_event_command_creates_event(monkeypatch):
    session = _FakeSession(instance_exists=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.add_workflow_event_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        instance_id=str(uuid4()),
        actor_user_id=USER_ID,
        event_type="note_added",
        event_payload={"note": "Escalated to committee"},
        idempotency_key="idem-event-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.aggregate_type == "workflow_event"
    assert result.result_reference["event_type"] == "note_added"
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.workflow_events" in joined


@pytest.mark.asyncio
async def test_add_workflow_event_command_404s_when_instance_missing(monkeypatch):
    session = _FakeSession(instance_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.add_workflow_event_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            instance_id=str(uuid4()),
            actor_user_id=USER_ID,
            event_type="note_added",
            idempotency_key="idem-event-2",
        )

    assert exc_info.value.status_code == 404
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.workflow_events" not in joined


@pytest.mark.asyncio
async def test_assign_workflow_task_command_without_step_key_creates_assignment(monkeypatch):
    session = _FakeSession(instance_exists=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.assign_workflow_task_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        instance_id=str(uuid4()),
        actor_user_id=USER_ID,
        assignee_user_id=str(uuid4()),
        idempotency_key="idem-assign-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.aggregate_type == "workflow_assignment"
    assert result.result_reference["step_key"] is None
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.workflow_assignments" in joined
    assert "UPDATE workflow.workflow_steps" not in joined


@pytest.mark.asyncio
async def test_assign_workflow_task_command_with_step_key_updates_step_assignee(monkeypatch):
    step_id = str(uuid4())
    session = _FakeSession(instance_exists=True, step_exists=True, step_id=step_id)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    assignee_id = str(uuid4())

    result = await command_service.assign_workflow_task_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        instance_id=str(uuid4()),
        actor_user_id=USER_ID,
        assignee_user_id=assignee_id,
        step_key="review",
        idempotency_key="idem-assign-2",
    )

    assert result.status == CommandStatus.CREATED
    assert result.result_reference["step_key"] == "review"
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.workflow_steps" in joined
    step_update_params = [p for p in session.params if p.get("step_id") == step_id]
    assert step_update_params
    # tenant-scoping regression check (deep-dive audit, 2026-08-11): the step
    # assignee UPDATE must filter by tenant_id, not just id — step_id alone
    # is not attacker-controlled here (it's resolved from a tenant-scoped
    # SELECT just above), but every mutation in this file must still carry
    # its own tenant_id filter as defense-in-depth, matching every other
    # UPDATE/DELETE statement in this module.
    assert step_update_params[0].get("tenant_id") == TENANT_ID
    update_step_sql = next(c for c in session.calls if "UPDATE workflow.workflow_steps" in c and "assignee_user_id" in c)
    assert "tenant_id" in update_step_sql


@pytest.mark.asyncio
async def test_assign_workflow_task_command_404s_when_instance_missing(monkeypatch):
    session = _FakeSession(instance_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.assign_workflow_task_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            instance_id=str(uuid4()),
            actor_user_id=USER_ID,
            assignee_user_id=str(uuid4()),
            idempotency_key="idem-assign-3",
        )

    assert exc_info.value.status_code == 404
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.workflow_assignments" not in joined


@pytest.mark.asyncio
async def test_assign_workflow_task_command_404s_when_step_key_missing(monkeypatch):
    session = _FakeSession(instance_exists=True, step_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.assign_workflow_task_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            instance_id=str(uuid4()),
            actor_user_id=USER_ID,
            assignee_user_id=str(uuid4()),
            step_key="missing-step",
            idempotency_key="idem-assign-4",
        )

    assert exc_info.value.status_code == 404
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.workflow_assignments" not in joined


# ---------------------------------------------------------------------------
# P2B-5d — automation rule CRUD (create, update, enable/disable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_automation_rule_command_creates_disabled_rule_with_conditions_and_actions(monkeypatch):
    session = _FakeSession(rule_conflict=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.create_automation_rule_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        rule_key="overdue-invoice-escalation",
        name="Overdue invoice escalation",
        description=None,
        conditions=[{"condition_type": "invoice_overdue_days", "expression": {"gte": 30}}],
        actions=[{"action_type": "notify_ec", "payload": {"channel": "email"}}],
        idempotency_key="idem-rule-create-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.result_reference["enabled"] is False
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.automation_rules" in joined
    condition_inserts = [c for c in session.calls if "INSERT INTO workflow.automation_rule_conditions" in c]
    action_inserts = [c for c in session.calls if "INSERT INTO workflow.automation_rule_actions" in c]
    assert len(condition_inserts) == 1
    assert len(action_inserts) == 1


@pytest.mark.asyncio
async def test_create_automation_rule_command_409s_when_rule_key_exists(monkeypatch):
    session = _FakeSession(rule_conflict=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.create_automation_rule_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            rule_key="overdue-invoice-escalation",
            name="dup",
            idempotency_key="idem-rule-create-2",
        )

    assert exc_info.value.status_code == 409
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.automation_rules" not in joined


@pytest.mark.asyncio
async def test_update_automation_rule_command_updates_fields_and_replaces_actions(monkeypatch):
    session = _FakeSession(
        rule_missing=False,
        rule_current_name="Old name",
        rule_current_description="Old description",
        rule_current_requires_approval=True,
    )
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.update_automation_rule_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        rule_key="overdue-invoice-escalation",
        name="New name",
        actions=[{"action_type": "notify_manager", "payload": {}}],
        idempotency_key="idem-rule-update-1",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.automation_rules" in joined
    assert "DELETE FROM workflow.automation_rule_actions" in joined
    assert "DELETE FROM workflow.automation_rule_conditions" not in joined
    update_params = [p for p in session.params if p.get("name") == "New name"]
    assert update_params
    # description wasn't passed — must retain the current stored value, not be nulled
    assert update_params[0]["description"] == "Old description"


@pytest.mark.asyncio
async def test_update_automation_rule_command_404s_when_rule_missing(monkeypatch):
    session = _FakeSession(rule_missing=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.update_automation_rule_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            rule_key="never-created",
            name="x",
            idempotency_key="idem-rule-update-2",
        )

    assert exc_info.value.status_code == 404
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.automation_rules" not in joined


@pytest.mark.parametrize(
    ("command_name", "expected_enabled"),
    [
        ("enable_automation_rule_command", True),
        ("disable_automation_rule_command", False),
    ],
)
@pytest.mark.asyncio
async def test_enable_disable_automation_rule_command_updates_flag(monkeypatch, command_name, expected_enabled):
    session = _FakeSession(rule_missing=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    command_fn = getattr(command_service, command_name)

    result = await command_fn(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        rule_key="overdue-invoice-escalation",
        idempotency_key=f"idem-{command_name}",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.automation_rules SET enabled" in joined
    update_params = [p for p in session.params if p.get("enabled") == expected_enabled]
    assert update_params


@pytest.mark.parametrize(
    "command_name",
    ["enable_automation_rule_command", "disable_automation_rule_command"],
)
@pytest.mark.asyncio
async def test_enable_disable_automation_rule_command_404s_when_rule_missing(monkeypatch, command_name):
    session = _FakeSession(rule_missing=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))
    command_fn = getattr(command_service, command_name)

    with pytest.raises(HTTPException) as exc_info:
        await command_fn(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            rule_key="never-created",
            idempotency_key=f"idem-{command_name}-404",
        )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# P2B-5e — automation execution (execute, record execution, retry, suppression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_automation_rule_command_dry_run_creates_simulated_run(monkeypatch):
    session = _FakeSession(rule_missing=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.execute_automation_rule_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        rule_key="overdue-invoice-escalation",
        dry_run=True,
        idempotency_key="idem-exec-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.aggregate_type == "automation_run"
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.automation_rule_runs" in joined
    insert_params = [p for p in session.params if p.get("status") == "simulated"]
    assert insert_params


@pytest.mark.asyncio
async def test_execute_automation_rule_command_non_dry_run_status_running(monkeypatch):
    session = _FakeSession(rule_missing=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.execute_automation_rule_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        rule_key="overdue-invoice-escalation",
        dry_run=False,
        idempotency_key="idem-exec-2",
    )

    assert result.status == CommandStatus.CREATED
    insert_params = [p for p in session.params if p.get("status") == "running"]
    assert insert_params


@pytest.mark.asyncio
async def test_execute_automation_rule_command_404s_when_rule_missing(monkeypatch):
    session = _FakeSession(rule_missing=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.execute_automation_rule_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            rule_key="never-created",
            idempotency_key="idem-exec-3",
        )

    assert exc_info.value.status_code == 404
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.automation_rule_runs" not in joined


@pytest.mark.asyncio
async def test_record_automation_execution_command_updates_run(monkeypatch):
    run_id = str(uuid4())
    session = _FakeSession(run_exists=True, run_id=run_id)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.record_automation_execution_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        run_id=run_id,
        status="succeeded",
        result={"actions_dispatched": 1},
        idempotency_key="idem-record-1",
    )

    assert result.status == CommandStatus.UPDATED
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.automation_rule_runs" in joined
    update_params = [p for p in session.params if p.get("status") == "succeeded"]
    assert update_params


@pytest.mark.asyncio
async def test_record_automation_execution_command_404s_when_run_missing(monkeypatch):
    session = _FakeSession(run_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.record_automation_execution_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            run_id=str(uuid4()),
            status="failed",
            idempotency_key="idem-record-2",
        )

    assert exc_info.value.status_code == 404
    joined = "\n".join(session.calls)
    assert "UPDATE workflow.automation_rule_runs" not in joined


@pytest.mark.asyncio
async def test_retry_failed_automation_action_command_creates_new_run(monkeypatch):
    original_run_id = str(uuid4())
    rule_id = str(uuid4())
    session = _FakeSession(run_exists=True, run_rule_id=rule_id, run_rule_key="overdue-invoice-escalation")
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.retry_failed_automation_action_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        run_id=original_run_id,
        idempotency_key="idem-retry-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.result_reference["retried_from_run_id"] == original_run_id
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.automation_rule_runs" in joined
    insert_params = [p for p in session.params if p.get("rule_id") == rule_id and "created_by_user_id" in p]
    assert insert_params


@pytest.mark.asyncio
async def test_retry_failed_automation_action_command_404s_when_original_run_missing(monkeypatch):
    session = _FakeSession(run_exists=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.retry_failed_automation_action_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            run_id=str(uuid4()),
            idempotency_key="idem-retry-2",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_record_automation_suppression_command_creates_suppressed_run(monkeypatch):
    session = _FakeSession(rule_missing=False)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.record_automation_suppression_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        rule_key="overdue-invoice-escalation",
        reason="duplicate trigger within de-dup window",
        matched_entity_type="invoice",
        matched_entity_id=str(uuid4()),
        idempotency_key="idem-suppress-1",
    )

    assert result.status == CommandStatus.CREATED
    assert result.aggregate_type == "automation_run"
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.automation_rule_runs" in joined
    insert_params = [p for p in session.params if p.get("result_json") and "duplicate trigger" in p["result_json"]]
    assert insert_params


@pytest.mark.asyncio
async def test_record_automation_suppression_command_404s_when_rule_missing(monkeypatch):
    session = _FakeSession(rule_missing=True)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.record_automation_suppression_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            rule_key="never-created",
            reason="duplicate trigger",
            idempotency_key="idem-suppress-2",
        )

    assert exc_info.value.status_code == 404
    joined = "\n".join(session.calls)
    assert "INSERT INTO workflow.automation_rule_runs" not in joined


# ---------------------------------------------------------------------------
# Idempotency-replay regression tests (deep-dive audit, 2026-08-11)
#
# Every command in this file was documented with a discriminator technique
# ("aggregate_type trick" or "result_reference truthiness trick") but NONE
# were actually exercised against a simulated replay before this audit — a
# real coverage gap, since PowerhouseCommandUnitOfWork.execute() always
# returns status=IDEMPOTENT_REPLAY regardless of the original outcome, and
# it is entirely the per-command discriminator logic's job to turn a
# replayed 404 back into a 404 rather than a silent 200/201. This class of
# bug was already caught once for real in P2B-2/P2B-3 (see git history) —
# these tests close the same gap for the P2B-5 commands.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow_instance_command_replays_without_reinserting(monkeypatch):
    """Replay of a successful create must not re-run the INSERT."""
    aggregate_id = str(uuid4())
    replay_row = SimpleNamespace(
        result_status="created",
        aggregate_type="workflow_instance",
        aggregate_id=aggregate_id,
        result_reference={"id": aggregate_id, "status": "open", "title": "Replayed instance"},
        completed_at="2026-08-10T00:00:00Z",
    )
    session = _FakeSession(replay_row=replay_row)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.create_workflow_instance_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        actor_user_id=USER_ID,
        template_key="maintenance-from-conversation",
        title="New maintenance workflow",
        idempotency_key="idem-inst-replay",
    )

    assert result.status == CommandStatus.IDEMPOTENT_REPLAY
    assert result.aggregate_id == aggregate_id
    assert "INSERT INTO workflow.workflow_instances" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_start_workflow_command_404s_on_replay_of_a_prior_not_found(monkeypatch):
    """Regression test for the result_reference-truthiness discriminator.

    start_workflow_command shares aggregate_type="workflow_instance" on both
    its success and NOT_FOUND paths, so only result_reference truthiness
    (empty dict on the stored NOT_FOUND row) can tell a replayed 404 apart
    from a replayed success.
    """
    replay_row = SimpleNamespace(
        result_status="not_found",
        aggregate_type="workflow_instance",
        aggregate_id=str(uuid4()),
        result_reference={},
        completed_at="2026-08-10T00:00:00Z",
    )
    session = _FakeSession(replay_row=replay_row)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.start_workflow_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            instance_id=str(uuid4()),
            actor_user_id=USER_ID,
            idempotency_key="idem-start-replay-404",
        )

    assert exc_info.value.status_code == 404
    assert "UPDATE workflow.workflow_instances" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_add_workflow_event_command_404s_on_replay_of_a_prior_not_found(monkeypatch):
    """Regression test for the aggregate_type discriminator.

    add_workflow_event_command's success aggregate_type ("workflow_event")
    differs from its NOT_FOUND aggregate_type ("workflow_instance") — a
    replayed NOT_FOUND record must still read back aggregate_type=
    "workflow_instance" and 404, not fall through to a 200 with an empty body.
    """
    replay_row = SimpleNamespace(
        result_status="not_found",
        aggregate_type="workflow_instance",  # only ever set on the NOT_FOUND path
        aggregate_id=str(uuid4()),
        result_reference={},
        completed_at="2026-08-10T00:00:00Z",
    )
    session = _FakeSession(replay_row=replay_row)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.add_workflow_event_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            instance_id=str(uuid4()),
            actor_user_id=USER_ID,
            event_type="note_added",
            idempotency_key="idem-event-replay-404",
        )

    assert exc_info.value.status_code == 404
    assert "INSERT INTO workflow.workflow_events" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_add_workflow_event_command_replays_success_without_reinserting(monkeypatch):
    """Replay of a successful add-event must return the original event, not a fresh 404 or a duplicate insert."""
    aggregate_id = str(uuid4())
    replay_row = SimpleNamespace(
        result_status="created",
        aggregate_type="workflow_event",
        aggregate_id=aggregate_id,
        result_reference={"id": aggregate_id, "event_type": "note_added"},
        completed_at="2026-08-10T00:00:00Z",
    )
    session = _FakeSession(replay_row=replay_row)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    result = await command_service.add_workflow_event_command(
        building_id="13195",
        tenant_id=TENANT_ID,
        scheme_id=SCHEME_ID,
        instance_id=str(uuid4()),
        actor_user_id=USER_ID,
        event_type="note_added",
        idempotency_key="idem-event-replay-success",
    )

    assert result.status == CommandStatus.IDEMPOTENT_REPLAY
    assert result.aggregate_type == "workflow_event"
    assert "INSERT INTO workflow.workflow_events" not in "\n".join(session.calls)


@pytest.mark.asyncio
async def test_enable_automation_rule_command_404s_on_replay_of_a_prior_not_found(monkeypatch):
    """Regression test covering the automation-rule result_reference-truthiness discriminator."""
    replay_row = SimpleNamespace(
        result_status="not_found",
        aggregate_type="automation_rule",
        aggregate_id=None,
        result_reference={},
        completed_at="2026-08-10T00:00:00Z",
    )
    session = _FakeSession(replay_row=replay_row)
    monkeypatch.setattr(command_service, "_uow", _fake_uow(session))

    with pytest.raises(HTTPException) as exc_info:
        await command_service.enable_automation_rule_command(
            building_id="13195",
            tenant_id=TENANT_ID,
            scheme_id=SCHEME_ID,
            actor_user_id=USER_ID,
            rule_key="never-created",
            idempotency_key="idem-enable-replay-404",
        )

    assert exc_info.value.status_code == 404
    assert "UPDATE workflow.automation_rules" not in "\n".join(session.calls)


# ---------------------------------------------------------------------------
# Live integration test — real Postgres, real East Gate (13195) tenant/scheme
# context, cleaned up afterward.
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
async def test_live_create_and_publish_workflow_definition_against_real_postgres(_live_east_gate_context):
    tenant_id, scheme_id = _live_east_gate_context
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    actor_user_id = None
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        row = await session.execute(text("SELECT id::text AS id FROM core.users WHERE email = 'manager@eastgate.com' LIMIT 1"))
        found = row.fetchone()
        actor_user_id = found.id if found else None
    if not actor_user_id:
        pytest.skip("No manager@eastgate.com user resolvable in core.users for this environment")

    template_key = f"live-test-template-{uuid4()}"
    result = await command_service.create_workflow_definition_command(
        building_id="13195",
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        actor_user_id=actor_user_id,
        template_key=template_key,
        name="Live test template",
        description=None,
        definition_json={"steps": []},
        idempotency_key=f"live-test-create-{uuid4()}",
    )
    assert result.status == CommandStatus.CREATED
    assert result.result_reference["version"] == 1

    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            text("DELETE FROM workflow.workflow_templates WHERE tenant_id = CAST(:tid AS UUID) AND template_key = :tk"),
            {"tid": tenant_id, "tk": template_key},
        )
        await session.commit()
