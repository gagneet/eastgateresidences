# @featuretrace:powerhouse-foundation — PostgreSQL command service for the Powerhouse workflow/automation surface.
# Layer: service
# Data flow: powerhouse_conversation_service workflow/automation functions → this module → PowerhouseCommandUnitOfWork → workflow.* tables + core.audit_events + core.outbox (tenant-scoped).
# Related: backend/services/powerhouse_command_foundation.py
#          backend/services/powerhouse_communications_command_service.py
#          backend/services/powerhouse_conversation_service.py

"""PostgreSQL command implementations for the Powerhouse workflow/automation surface.

Distinct COMMAND_SCOPE ("powerhouse_workflows") from
powerhouse_communications_command_service.py ("powerhouse_conversations") —
these are separate cutover domains with independent promotion state
(core.domain_cutover_status one row per domain).

Built PostgreSQL-only where no existing Mongo implementation exists, following
the P2B-4 precedent (see docs/features/powerhouse_p2b_read_classification_and_gaps.md).
Where an existing Mongo handler in powerhouse_conversation_service.py already
covers a capability (create instance, complete step, add event, assign task,
execute automation rule), the dual-path P2B-2/P2B-3 pattern is used instead —
call sites branch on write_source exactly like the conversations domain does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text

from services.powerhouse_command_foundation import (
    AuditEventSpec,
    CommandContext,
    CommandResult,
    CommandStatus,
    OutboxEventSpec,
    PowerhouseCommandUnitOfWork,
)

COMMAND_SCOPE = "powerhouse_workflows"

_uow = PowerhouseCommandUnitOfWork()


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    """Serialise a datetime for a JSON-safe command payload, or None."""
    return dt.isoformat() if dt else None


def _require_uuid(value: str, field_name: str) -> str:
    """Validate a legacy/user-supplied id is UUID-shaped before it reaches SQL.

    workflow.* creator/assignee columns are UUID; a malformed id is a genuine
    client input error and must surface as 422, not a raw DB CAST failure
    classified as an APPLICATION_ERROR 500.
    """
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} '{value}' is not a valid identifier for a PostgreSQL-backed workflow",
        ) from exc


async def create_workflow_definition_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    template_key: str,
    name: str,
    description: str | None,
    definition_json: dict[str, Any],
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Create a new workflow definition (version 1) in one PG transaction.

    CONFLICT if template_key already has any version — use
    publish_workflow_definition_version_command to add a new version to an
    existing template_key instead.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    template_id = str(uuid4())
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        existing = await session.execute(
            text(
                """
                SELECT 1 FROM workflow.workflow_templates
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND template_key = :template_key
                LIMIT 1
                """
            ),
            {"tenant_id": context.tenant_id, "template_key": template_key},
        )
        if existing.fetchone() is not None:
            return CommandResult(status=CommandStatus.CONFLICT, aggregate_type="workflow_template", aggregate_id=None)

        await session.execute(
            text(
                """
                INSERT INTO workflow.workflow_templates (
                    id, tenant_id, building_id, template_key, name, description,
                    version, is_active, definition_json, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), :building_id, :template_key, :name, :description,
                    1, TRUE, CAST(:definition_json AS JSONB), :created_at
                )
                """
            ),
            {
                "id": template_id,
                "tenant_id": context.tenant_id,
                "building_id": building_id,
                "template_key": template_key,
                "name": name,
                "description": description,
                "definition_json": _json_dumps(definition_json),
                "created_at": now,
            },
        )
        result_reference = {
            "id": template_id,
            "building_id": building_id,
            "template_key": template_key,
            "name": name,
            "description": description,
            "version": 1,
            "is_active": True,
            "definition_json": definition_json,
            "created_at": _iso(now),
        }
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="workflow_template",
            aggregate_id=template_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="workflow.create_definition",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="workflow_template", action="workflow.definition.created", payload={"template_key": template_key}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="workflow.definition.created", payload={"template_id": result.aggregate_id, "template_key": template_key}
        ),
    )
    if result.status == CommandStatus.CONFLICT:
        raise HTTPException(status_code=409, detail=f"Workflow definition '{template_key}' already exists")
    return result


async def publish_workflow_definition_version_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    template_key: str,
    name: str,
    description: str | None,
    definition_json: dict[str, Any],
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Publish a new version of an existing workflow definition in one PG transaction.

    NOT_FOUND if template_key has no prior version — use
    create_workflow_definition_command to create the first version.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    template_id = str(uuid4())
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        current = await session.execute(
            text(
                """
                SELECT version FROM workflow.workflow_templates
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND template_key = :template_key AND is_active = TRUE
                ORDER BY version DESC
                LIMIT 1
                """
            ),
            {"tenant_id": context.tenant_id, "template_key": template_key},
        )
        current_row = current.fetchone()
        if current_row is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="workflow_template", aggregate_id=None)
        next_version = int(current_row.version) + 1

        await session.execute(
            text(
                """
                UPDATE workflow.workflow_templates
                SET is_active = FALSE
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND template_key = :template_key AND is_active = TRUE
                """
            ),
            {"tenant_id": context.tenant_id, "template_key": template_key},
        )
        await session.execute(
            text(
                """
                INSERT INTO workflow.workflow_templates (
                    id, tenant_id, building_id, template_key, name, description,
                    version, is_active, definition_json, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), :building_id, :template_key, :name, :description,
                    :version, TRUE, CAST(:definition_json AS JSONB), :created_at
                )
                """
            ),
            {
                "id": template_id,
                "tenant_id": context.tenant_id,
                "building_id": building_id,
                "template_key": template_key,
                "name": name,
                "description": description,
                "version": next_version,
                "definition_json": _json_dumps(definition_json),
                "created_at": now,
            },
        )
        result_reference = {
            "id": template_id,
            "building_id": building_id,
            "template_key": template_key,
            "name": name,
            "description": description,
            "version": next_version,
            "is_active": True,
            "definition_json": definition_json,
            "created_at": _iso(now),
        }
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="workflow_template",
            aggregate_id=template_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="workflow.publish_definition_version",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="workflow_template",
            action="workflow.definition.version_published",
            payload={"template_key": template_key},
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="workflow.definition.version_published",
            payload={"template_id": result.aggregate_id, "template_key": template_key},
        ),
    )
    if result.status == CommandStatus.NOT_FOUND:
        raise HTTPException(
            status_code=404,
            detail=f"No existing workflow definition '{template_key}' to publish a new version against",
        )
    return result


def _json_dumps(value: dict[str, Any]) -> str:
    """Serialise a dict for a JSONB bind param (SQLAlchemy text() doesn't auto-adapt dicts)."""
    import json

    return json.dumps(value)


async def _fetch_active_template(session: Any, *, tenant_id: str, template_key: str) -> dict[str, Any] | None:
    """Return {id, definition_json} for a template_key's active version, or None.

    None is a legitimate outcome, not an error: Mongo's UX has always allowed
    creating a workflow instance against a template_key with no real
    definition yet (list_workflow_templates' "placeholder" templates) — the
    PG path mirrors that by creating the instance with template_id=NULL and
    no materialised steps, rather than hard-404ing.
    """
    row = await session.execute(
        text(
            """
            SELECT id::text AS id, definition_json
            FROM workflow.workflow_templates
            WHERE tenant_id = CAST(:tenant_id AS UUID) AND template_key = :template_key AND is_active = TRUE
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "template_key": template_key},
    )
    found = row.fetchone()
    if not found:
        return None
    return {"id": found.id, "definition_json": found.definition_json or {}}


async def _fetch_instance_result_reference(session: Any, *, tenant_id: str, instance_id: str) -> dict[str, Any] | None:
    """Select a workflow instance (+ its template_key, via template_id) for a command result."""
    row = await session.execute(
        text(
            """
            SELECT
                i.id::text AS id,
                i.building_id,
                i.template_id::text AS template_id,
                t.template_key,
                i.title,
                i.status,
                i.linked_entity_type,
                i.linked_entity_id,
                i.payload,
                i.created_by_user_id::text AS created_by,
                i.created_at,
                i.updated_at
            FROM workflow.workflow_instances i
            LEFT JOIN workflow.workflow_templates t ON t.id = i.template_id AND t.tenant_id = i.tenant_id
            WHERE i.id = CAST(:instance_id AS UUID) AND i.tenant_id = CAST(:tenant_id AS UUID)
            """
        ),
        {"instance_id": instance_id, "tenant_id": tenant_id},
    )
    instance = row.fetchone()
    if not instance:
        return None
    linked_entity = None
    if instance.linked_entity_type:
        linked_entity = {"entity_type": instance.linked_entity_type, "entity_id": instance.linked_entity_id}
    return {
        "id": instance.id,
        "building_id": instance.building_id,
        "template_id": instance.template_id,
        "template_key": instance.template_key,
        "title": instance.title,
        "status": instance.status,
        "linked_entity": linked_entity,
        "payload": instance.payload or {},
        "created_by": instance.created_by,
        "created_at": _iso(instance.created_at),
        "updated_at": _iso(instance.updated_at),
    }


def _raise_404_if_not_found(result: CommandResult) -> None:
    """Convert a NOT_FOUND (or an idempotent replay of one) into HTTPException(404).

    Same result_reference-truthiness technique as
    powerhouse_communications_command_service.py's version — every command
    below shares aggregate_type="workflow_instance" on both success and
    NOT_FOUND, so aggregate_type alone can't distinguish them, but
    result_reference (only ever populated on success) survives idempotency
    replay correctly.
    """
    if not result.result_reference:
        raise HTTPException(status_code=404, detail="Workflow instance not found")


async def create_workflow_instance_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    template_key: str,
    title: str,
    linked_entity_type: str | None = None,
    linked_entity_id: str | None = None,
    initial_payload: dict[str, Any] | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Create a workflow instance in one PG transaction, materialising steps from the template if one exists.

    A template_key with no active definition yet creates the instance anyway
    (template_id=NULL, no steps) — see _fetch_active_template's docstring.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    instance_id = str(uuid4())
    now = _utc_now()
    payload = dict(initial_payload or {})

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        template = await _fetch_active_template(session, tenant_id=context.tenant_id, template_key=template_key)
        template_id = template["id"] if template else None

        await session.execute(
            text(
                """
                INSERT INTO workflow.workflow_instances (
                    id, tenant_id, building_id, template_id, title, status,
                    linked_entity_type, linked_entity_id, payload, created_by_user_id,
                    created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), :building_id, CAST(:template_id AS UUID), :title, 'open',
                    :linked_entity_type, :linked_entity_id, CAST(:payload AS JSONB), CAST(:created_by_user_id AS UUID),
                    :created_at, :updated_at
                )
                """
            ),
            {
                "id": instance_id,
                "tenant_id": context.tenant_id,
                "building_id": building_id,
                "template_id": template_id,
                "title": title,
                "linked_entity_type": linked_entity_type,
                "linked_entity_id": linked_entity_id,
                "payload": _json_dumps(payload),
                "created_by_user_id": created_by_uuid,
                "created_at": now,
                "updated_at": now,
            },
        )

        if template:
            steps = (template["definition_json"] or {}).get("steps") or []
            for position, step in enumerate(steps, start=1):
                await session.execute(
                    text(
                        """
                        INSERT INTO workflow.workflow_steps (
                            id, tenant_id, instance_id, step_key, step_name, status, position, created_at
                        ) VALUES (
                            CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:instance_id AS UUID),
                            :step_key, :step_name, 'pending', :position, :created_at
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "tenant_id": context.tenant_id,
                        "instance_id": instance_id,
                        "step_key": step.get("step_key") or f"step-{position}",
                        "step_name": step.get("step_name") or step.get("step_key") or f"Step {position}",
                        "position": step.get("position", position),
                        "created_at": now,
                    },
                )

        result_reference = await _fetch_instance_result_reference(session, tenant_id=context.tenant_id, instance_id=instance_id)
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="workflow_instance",
            aggregate_id=instance_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="workflow.create_instance",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    return await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="workflow_instance", action="workflow.instance.created", payload={"template_key": template_key}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="workflow.instance.created", payload={"instance_id": result.aggregate_id, "template_key": template_key}
        ),
    )


async def start_workflow_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Transition a workflow instance from open to in_progress, in one PG transaction."""
    return await _transition_workflow_instance_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        to_status="in_progress",
        command_type="workflow.start",
        audit_action="workflow.instance.started",
        outbox_event_type="workflow.instance.started",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
    )


async def pause_workflow_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
    notes: str | None = None,
) -> CommandResult:
    """Transition a workflow instance to paused, in one PG transaction."""
    return await _transition_workflow_instance_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        to_status="paused",
        command_type="workflow.pause",
        audit_action="workflow.instance.paused",
        outbox_event_type="workflow.instance.paused",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        notes=notes,
    )


async def resume_workflow_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
    notes: str | None = None,
) -> CommandResult:
    """Transition a paused workflow instance back to in_progress, in one PG transaction."""
    return await _transition_workflow_instance_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        to_status="in_progress",
        command_type="workflow.resume",
        audit_action="workflow.instance.resumed",
        outbox_event_type="workflow.instance.resumed",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        notes=notes,
    )


async def cancel_workflow_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
    notes: str | None = None,
) -> CommandResult:
    """Transition a workflow instance to cancelled, in one PG transaction."""
    return await _transition_workflow_instance_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        to_status="cancelled",
        command_type="workflow.cancel",
        audit_action="workflow.instance.cancelled",
        outbox_event_type="workflow.instance.cancelled",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        notes=notes,
    )


async def escalate_workflow_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
    notes: str | None = None,
) -> CommandResult:
    """Transition a workflow instance to escalated, in one PG transaction.

    escalated is a distinct terminal-adjacent status from failed — an
    instance whose steps aren't resolving needs human attention, not a
    hard failure. Still reachable via retry/resume-style handling by
    future callers; this command only records the transition itself.
    """
    return await _transition_workflow_instance_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        to_status="escalated",
        command_type="workflow.escalate",
        audit_action="workflow.instance.escalated",
        outbox_event_type="workflow.instance.escalated",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        notes=notes,
    )


async def close_workflow_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
    notes: str | None = None,
) -> CommandResult:
    """Transition a workflow instance to closed (terminal), in one PG transaction."""
    return await _transition_workflow_instance_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        to_status="closed",
        command_type="workflow.close",
        audit_action="workflow.instance.closed",
        outbox_event_type="workflow.instance.closed",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        notes=notes,
    )


async def _transition_workflow_instance_status(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    to_status: str,
    command_type: str,
    audit_action: str,
    outbox_event_type: str,
    idempotency_key: str,
    is_test_data: bool = False,
    notes: str | None = None,
) -> CommandResult:
    """Shared implementation for every plain instance-level status transition.

    Used by start/pause/resume/cancel/escalate/close — every command that
    just moves workflow_instances.status and logs a workflow_status_history
    row, with no other side effect.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    instance_uuid = _require_uuid(instance_id, "instance_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        current = await session.execute(
            text("SELECT status FROM workflow.workflow_instances WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"),
            {"id": instance_uuid, "tenant_id": context.tenant_id},
        )
        current_row = current.fetchone()
        if current_row is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="workflow_instance", aggregate_id=instance_uuid)
        from_status = current_row.status

        await session.execute(
            text(
                """
                UPDATE workflow.workflow_instances
                SET status = :to_status, updated_at = :updated_at
                WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                """
            ),
            {"to_status": to_status, "updated_at": now, "id": instance_uuid, "tenant_id": context.tenant_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO workflow.workflow_status_history (
                    id, tenant_id, instance_id, step_key, from_status, to_status, notes, changed_by_user_id, changed_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:instance_id AS UUID),
                    NULL, :from_status, :to_status, :notes, CAST(:changed_by_user_id AS UUID), :changed_at
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": context.tenant_id,
                "instance_id": instance_uuid,
                "from_status": from_status,
                "to_status": to_status,
                "notes": notes,
                "changed_by_user_id": created_by_uuid,
                "changed_at": now,
            },
        )
        result_reference = await _fetch_instance_result_reference(session, tenant_id=context.tenant_id, instance_id=instance_uuid)
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="workflow_instance",
            aggregate_id=instance_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type=command_type,
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(entity_type="workflow_instance", action=audit_action, payload={"to_status": to_status}),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type=outbox_event_type, payload={"instance_id": instance_uuid, "to_status": to_status}
        ),
    )
    _raise_404_if_not_found(result)
    return result


async def complete_workflow_step_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    step_key: str,
    notes: str | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Mark a workflow step completed in one PG transaction."""
    return await _transition_workflow_step_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        step_key=step_key,
        to_status="completed",
        command_type="workflow.complete_step",
        audit_action="workflow.step.completed",
        outbox_event_type="workflow.step.completed",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        notes=notes,
    )


async def fail_workflow_step_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    step_key: str,
    notes: str | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Mark a workflow step failed in one PG transaction."""
    return await _transition_workflow_step_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        step_key=step_key,
        to_status="failed",
        command_type="workflow.fail_step",
        audit_action="workflow.step.failed",
        outbox_event_type="workflow.step.failed",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        notes=notes,
    )


async def retry_workflow_step_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    step_key: str,
    notes: str | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Reset a failed workflow step back to pending in one PG transaction."""
    return await _transition_workflow_step_status(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        instance_id=instance_id,
        actor_user_id=actor_user_id,
        step_key=step_key,
        to_status="pending",
        command_type="workflow.retry_step",
        audit_action="workflow.step.retried",
        outbox_event_type="workflow.step.retried",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        notes=notes,
    )


async def _transition_workflow_step_status(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    step_key: str,
    to_status: str,
    command_type: str,
    audit_action: str,
    outbox_event_type: str,
    idempotency_key: str,
    is_test_data: bool = False,
    notes: str | None = None,
) -> CommandResult:
    """Shared implementation for complete/fail/retry step — all move workflow_steps.status."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    instance_uuid = _require_uuid(instance_id, "instance_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        current = await session.execute(
            text(
                """
                SELECT status FROM workflow.workflow_steps
                WHERE instance_id = CAST(:instance_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID) AND step_key = :step_key
                """
            ),
            {"instance_id": instance_uuid, "tenant_id": context.tenant_id, "step_key": step_key},
        )
        current_row = current.fetchone()
        if current_row is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="workflow_instance", aggregate_id=instance_uuid)
        from_status = current_row.status

        await session.execute(
            text(
                """
                UPDATE workflow.workflow_steps
                SET status = :to_status
                WHERE instance_id = CAST(:instance_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID) AND step_key = :step_key
                """
            ),
            {"to_status": to_status, "instance_id": instance_uuid, "tenant_id": context.tenant_id, "step_key": step_key},
        )
        await session.execute(
            text(
                """
                INSERT INTO workflow.workflow_status_history (
                    id, tenant_id, instance_id, step_key, from_status, to_status, notes, changed_by_user_id, changed_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:instance_id AS UUID),
                    :step_key, :from_status, :to_status, :notes, CAST(:changed_by_user_id AS UUID), :changed_at
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": context.tenant_id,
                "instance_id": instance_uuid,
                "step_key": step_key,
                "from_status": from_status,
                "to_status": to_status,
                "notes": notes,
                "changed_by_user_id": created_by_uuid,
                "changed_at": now,
            },
        )
        await session.execute(
            text(
                "UPDATE workflow.workflow_instances SET updated_at = :updated_at "
                "WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
            ),
            {"updated_at": now, "id": instance_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_instance_result_reference(session, tenant_id=context.tenant_id, instance_id=instance_uuid)
        if result_reference:
            result_reference["step_key"] = step_key
            result_reference["step_status"] = to_status
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="workflow_instance",
            aggregate_id=instance_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type=command_type,
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="workflow_instance", action=audit_action, payload={"step_key": step_key, "to_status": to_status}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type=outbox_event_type, payload={"instance_id": instance_uuid, "step_key": step_key, "to_status": to_status}
        ),
    )
    _raise_404_if_not_found(result)
    return result


async def add_workflow_event_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    event_type: str,
    event_payload: dict[str, Any] | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Append an event to a workflow instance in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    instance_uuid = _require_uuid(instance_id, "instance_id")
    event_id = str(uuid4())
    now = _utc_now()
    payload = dict(event_payload or {})

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        exists = await session.execute(
            text("SELECT 1 FROM workflow.workflow_instances WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"),
            {"id": instance_uuid, "tenant_id": context.tenant_id},
        )
        if exists.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="workflow_instance", aggregate_id=instance_uuid)

        await session.execute(
            text(
                """
                INSERT INTO workflow.workflow_events (
                    id, tenant_id, instance_id, event_type, payload, created_by_user_id, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:instance_id AS UUID),
                    :event_type, CAST(:payload AS JSONB), CAST(:created_by_user_id AS UUID), :created_at
                )
                """
            ),
            {
                "id": event_id,
                "tenant_id": context.tenant_id,
                "instance_id": instance_uuid,
                "event_type": event_type,
                "payload": _json_dumps(payload),
                "created_by_user_id": created_by_uuid,
                "created_at": now,
            },
        )
        await session.execute(
            text(
                "UPDATE workflow.workflow_instances SET updated_at = :updated_at "
                "WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
            ),
            {"updated_at": now, "id": instance_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = {
            "id": event_id,
            "instance_id": instance_uuid,
            "building_id": building_id,
            "event_type": event_type,
            "payload": payload,
            "created_by": created_by_uuid,
            "created_at": _iso(now),
        }
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="workflow_event",
            aggregate_id=event_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="workflow.add_event",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="workflow_instance", action="workflow.event.added", payload={"event_type": event_type}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="workflow.event.added", payload={"instance_id": instance_uuid, "event_id": result.aggregate_id}
        ),
    )
    # add_workflow_event's aggregate is the event itself ("workflow_event"), distinct
    # from every status-transition command's "workflow_instance" — so the aggregate_type
    # trick (not result_reference truthiness) is what distinguishes NOT_FOUND here.
    if result.aggregate_type == "workflow_instance":
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    return result


async def assign_workflow_task_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    instance_id: str,
    actor_user_id: str,
    assignee_user_id: str,
    step_key: str | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Assign a workflow instance (or one of its steps) to a user in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    instance_uuid = _require_uuid(instance_id, "instance_id")
    assignee_uuid = _require_uuid(assignee_user_id, "assignee_user_id")
    assignment_id = str(uuid4())
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        instance_row = await session.execute(
            text("SELECT 1 FROM workflow.workflow_instances WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"),
            {"id": instance_uuid, "tenant_id": context.tenant_id},
        )
        if instance_row.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="workflow_instance", aggregate_id=instance_uuid)

        step_id = None
        if step_key:
            step_row = await session.execute(
                text(
                    """
                    SELECT id::text AS id FROM workflow.workflow_steps
                    WHERE instance_id = CAST(:instance_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID) AND step_key = :step_key
                    """
                ),
                {"instance_id": instance_uuid, "tenant_id": context.tenant_id, "step_key": step_key},
            )
            found_step = step_row.fetchone()
            step_id = found_step.id if found_step else None
            if step_key and step_id is None:
                return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="workflow_instance", aggregate_id=instance_uuid)
            await session.execute(
                text(
                    """
                    UPDATE workflow.workflow_steps SET assignee_user_id = CAST(:assignee_id AS UUID)
                    WHERE id = CAST(:step_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                    """
                ),
                {"assignee_id": assignee_uuid, "step_id": step_id, "tenant_id": context.tenant_id},
            )

        await session.execute(
            text(
                """
                INSERT INTO workflow.workflow_assignments (
                    id, tenant_id, instance_id, step_id, assignee_user_id, assigned_by_user_id, status, assigned_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:instance_id AS UUID), CAST(:step_id AS UUID),
                    CAST(:assignee_id AS UUID), CAST(:assigned_by AS UUID), 'assigned', :assigned_at
                )
                """
            ),
            {
                "id": assignment_id,
                "tenant_id": context.tenant_id,
                "instance_id": instance_uuid,
                "step_id": step_id,
                "assignee_id": assignee_uuid,
                "assigned_by": created_by_uuid,
                "assigned_at": now,
            },
        )
        await session.execute(
            text(
                "UPDATE workflow.workflow_instances SET updated_at = :updated_at "
                "WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
            ),
            {"updated_at": now, "id": instance_uuid, "tenant_id": context.tenant_id},
        )

        result_reference = {
            "id": assignment_id,
            "instance_id": instance_uuid,
            "building_id": building_id,
            "step_key": step_key,
            "assignee_user_id": assignee_uuid,
            "assigned_by": created_by_uuid,
            "status": "assigned",
            "assigned_at": _iso(now),
        }
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="workflow_assignment",
            aggregate_id=assignment_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="workflow.assign_task",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="workflow_instance",
            action="workflow.assignment.created",
            payload={"assignee_user_id": assignee_uuid, "step_key": step_key},
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="workflow.assignment.created",
            payload={"instance_id": instance_uuid, "assignment_id": result.aggregate_id},
        ),
    )
    # assign_workflow_task's aggregate is the assignment itself ("workflow_assignment"),
    # distinct from the "workflow_instance" NOT_FOUND path — same aggregate_type trick.
    if result.aggregate_type == "workflow_instance":
        raise HTTPException(status_code=404, detail="Workflow instance or step not found")
    return result


# ---------------------------------------------------------------------------
# P2B-5d — Automation rule CRUD (create, update, enable/disable)
# ---------------------------------------------------------------------------
#
# workflow.automation_rules / automation_rule_conditions / automation_rule_actions
# — same `workflow` schema as every table above, not a separate `automation`
# schema. Rules are keyed by rule_key (UNIQUE(tenant_id, rule_key)), mirroring
# template_key's role for workflow definitions. New rules are always created
# `enabled=FALSE` (the schema's own default) — a rule only ever goes live via
# an explicit enable_automation_rule_command call, never implicitly on create.


async def _fetch_automation_rule_result_reference(session: Any, *, tenant_id: str, rule_key: str) -> dict[str, Any] | None:
    """Select an automation rule plus its conditions/actions (via jsonb_agg) for a command result.

    Uses the same jsonb_agg-subquery technique as
    powerhouse_communications_command_service.py's _fetch_thread_result_reference
    to return the full rule shape in one round trip without needing a
    multi-row fetchall() (kept the fake-session test harness simple, matching
    every other command in this file).
    """
    row = await session.execute(
        text(
            """
            SELECT
                r.id::text AS id,
                r.building_id,
                r.rule_key,
                r.name,
                r.description,
                r.enabled,
                r.requires_human_approval,
                r.created_at,
                COALESCE(
                    (SELECT jsonb_agg(jsonb_build_object('condition_type', c.condition_type, 'expression', c.expression_json))
                     FROM workflow.automation_rule_conditions c WHERE c.rule_id = r.id),
                    '[]'::jsonb
                ) AS conditions,
                COALESCE(
                    (SELECT jsonb_agg(jsonb_build_object(
                        'action_type', a.action_type, 'payload', a.payload_json,
                        'requires_human_approval', a.requires_human_approval
                    ))
                     FROM workflow.automation_rule_actions a WHERE a.rule_id = r.id),
                    '[]'::jsonb
                ) AS actions
            FROM workflow.automation_rules r
            WHERE r.tenant_id = CAST(:tenant_id AS UUID) AND r.rule_key = :rule_key
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "rule_key": rule_key},
    )
    rule = row.fetchone()
    if not rule:
        return None
    return {
        "id": rule.id,
        "building_id": rule.building_id,
        "rule_key": rule.rule_key,
        "name": rule.name,
        "description": rule.description,
        "enabled": rule.enabled,
        "requires_human_approval": rule.requires_human_approval,
        "conditions": rule.conditions or [],
        "actions": rule.actions or [],
        "created_at": _iso(rule.created_at),
    }


def _raise_404_if_rule_not_found(result: CommandResult) -> None:
    """Same result_reference-truthiness NOT_FOUND-vs-replay technique as the instance commands."""
    if not result.result_reference:
        raise HTTPException(status_code=404, detail="Automation rule not found")


async def create_automation_rule_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    rule_key: str,
    name: str,
    description: str | None = None,
    requires_human_approval: bool = True,
    conditions: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Create a new automation rule (disabled by default) in one PG transaction.

    CONFLICT if rule_key already exists — use update_automation_rule_command
    to change an existing rule.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    rule_id = str(uuid4())
    now = _utc_now()
    condition_specs = list(conditions or [])
    action_specs = list(actions or [])

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        existing = await session.execute(
            text(
                """
                SELECT 1 FROM workflow.automation_rules
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND rule_key = :rule_key
                LIMIT 1
                """
            ),
            {"tenant_id": context.tenant_id, "rule_key": rule_key},
        )
        if existing.fetchone() is not None:
            return CommandResult(status=CommandStatus.CONFLICT, aggregate_type="automation_rule", aggregate_id=None)

        await session.execute(
            text(
                """
                INSERT INTO workflow.automation_rules (
                    id, tenant_id, building_id, rule_key, name, description,
                    enabled, requires_human_approval, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), :building_id, :rule_key, :name, :description,
                    FALSE, :requires_human_approval, :created_at
                )
                """
            ),
            {
                "id": rule_id,
                "tenant_id": context.tenant_id,
                "building_id": building_id,
                "rule_key": rule_key,
                "name": name,
                "description": description,
                "requires_human_approval": requires_human_approval,
                "created_at": now,
            },
        )
        for condition in condition_specs:
            await session.execute(
                text(
                    """
                    INSERT INTO workflow.automation_rule_conditions (
                        id, tenant_id, rule_id, condition_type, expression_json, created_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:rule_id AS UUID),
                        :condition_type, CAST(:expression_json AS JSONB), :created_at
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": context.tenant_id,
                    "rule_id": rule_id,
                    "condition_type": condition.get("condition_type"),
                    "expression_json": _json_dumps(condition.get("expression") or {}),
                    "created_at": now,
                },
            )
        for action in action_specs:
            await session.execute(
                text(
                    """
                    INSERT INTO workflow.automation_rule_actions (
                        id, tenant_id, rule_id, action_type, payload_json, requires_human_approval, created_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:rule_id AS UUID),
                        :action_type, CAST(:payload_json AS JSONB), :requires_human_approval, :created_at
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": context.tenant_id,
                    "rule_id": rule_id,
                    "action_type": action.get("action_type"),
                    "payload_json": _json_dumps(action.get("payload") or {}),
                    "requires_human_approval": action.get("requires_human_approval", True),
                    "created_at": now,
                },
            )

        result_reference = await _fetch_automation_rule_result_reference(session, tenant_id=context.tenant_id, rule_key=rule_key)
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="automation_rule",
            aggregate_id=rule_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="automation.create_rule",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="automation_rule", action="automation.rule.created", payload={"rule_key": rule_key}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="automation.rule.created", payload={"rule_id": result.aggregate_id, "rule_key": rule_key}
        ),
    )
    if result.status == CommandStatus.CONFLICT:
        raise HTTPException(status_code=409, detail=f"Automation rule '{rule_key}' already exists")
    return result


async def update_automation_rule_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    rule_key: str,
    name: str | None = None,
    description: str | None = None,
    requires_human_approval: bool | None = None,
    conditions: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Update an existing automation rule's fields/conditions/actions in one PG transaction.

    NOT_FOUND if rule_key doesn't exist. Only fields explicitly passed are
    changed — name/description/requires_human_approval default to their
    current stored value when omitted (None); conditions/actions are only
    replaced (delete-then-reinsert) when explicitly provided as a list — an
    omitted (None) conditions/actions list leaves the existing rows
    untouched. Never touches `enabled` — that is exclusively
    enable_automation_rule_command's / disable_automation_rule_command's job.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        current = await session.execute(
            text(
                """
                SELECT id::text AS id, name, description, requires_human_approval
                FROM workflow.automation_rules
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND rule_key = :rule_key
                """
            ),
            {"tenant_id": context.tenant_id, "rule_key": rule_key},
        )
        current_row = current.fetchone()
        if current_row is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="automation_rule", aggregate_id=None)
        rule_id = current_row.id

        await session.execute(
            text(
                """
                UPDATE workflow.automation_rules
                SET name = :name, description = :description, requires_human_approval = :requires_human_approval
                WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                """
            ),
            {
                "name": name if name is not None else current_row.name,
                "description": description if description is not None else current_row.description,
                "requires_human_approval": (
                    requires_human_approval if requires_human_approval is not None else current_row.requires_human_approval
                ),
                "id": rule_id,
                "tenant_id": context.tenant_id,
            },
        )

        if conditions is not None:
            await session.execute(
                text(
                    "DELETE FROM workflow.automation_rule_conditions "
                    "WHERE rule_id = CAST(:rule_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
                ),
                {"rule_id": rule_id, "tenant_id": context.tenant_id},
            )
            for condition in conditions:
                await session.execute(
                    text(
                        """
                        INSERT INTO workflow.automation_rule_conditions (
                            id, tenant_id, rule_id, condition_type, expression_json, created_at
                        ) VALUES (
                            CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:rule_id AS UUID),
                            :condition_type, CAST(:expression_json AS JSONB), :created_at
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "tenant_id": context.tenant_id,
                        "rule_id": rule_id,
                        "condition_type": condition.get("condition_type"),
                        "expression_json": _json_dumps(condition.get("expression") or {}),
                        "created_at": now,
                    },
                )

        if actions is not None:
            await session.execute(
                text(
                    "DELETE FROM workflow.automation_rule_actions "
                    "WHERE rule_id = CAST(:rule_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
                ),
                {"rule_id": rule_id, "tenant_id": context.tenant_id},
            )
            for action in actions:
                await session.execute(
                    text(
                        """
                        INSERT INTO workflow.automation_rule_actions (
                            id, tenant_id, rule_id, action_type, payload_json, requires_human_approval, created_at
                        ) VALUES (
                            CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:rule_id AS UUID),
                            :action_type, CAST(:payload_json AS JSONB), :requires_human_approval, :created_at
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "tenant_id": context.tenant_id,
                        "rule_id": rule_id,
                        "action_type": action.get("action_type"),
                        "payload_json": _json_dumps(action.get("payload") or {}),
                        "requires_human_approval": action.get("requires_human_approval", True),
                        "created_at": now,
                    },
                )

        result_reference = await _fetch_automation_rule_result_reference(session, tenant_id=context.tenant_id, rule_key=rule_key)
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="automation_rule",
            aggregate_id=rule_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="automation.update_rule",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="automation_rule", action="automation.rule.updated", payload={"rule_key": rule_key}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="automation.rule.updated", payload={"rule_id": result.aggregate_id, "rule_key": rule_key}
        ),
    )
    _raise_404_if_rule_not_found(result)
    return result


async def enable_automation_rule_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    rule_key: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Set an automation rule's enabled flag to TRUE, in one PG transaction."""
    return await _set_automation_rule_enabled(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        actor_user_id=actor_user_id,
        rule_key=rule_key,
        enabled=True,
        command_type="automation.enable_rule",
        audit_action="automation.rule.enabled",
        outbox_event_type="automation.rule.enabled",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
    )


async def disable_automation_rule_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    rule_key: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Set an automation rule's enabled flag to FALSE, in one PG transaction."""
    return await _set_automation_rule_enabled(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        actor_user_id=actor_user_id,
        rule_key=rule_key,
        enabled=False,
        command_type="automation.disable_rule",
        audit_action="automation.rule.disabled",
        outbox_event_type="automation.rule.disabled",
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
    )


async def _set_automation_rule_enabled(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    rule_key: str,
    enabled: bool,
    command_type: str,
    audit_action: str,
    outbox_event_type: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Shared implementation for enable_automation_rule_command / disable_automation_rule_command."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        current = await session.execute(
            text(
                """
                SELECT id::text AS id FROM workflow.automation_rules
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND rule_key = :rule_key
                """
            ),
            {"tenant_id": context.tenant_id, "rule_key": rule_key},
        )
        current_row = current.fetchone()
        if current_row is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="automation_rule", aggregate_id=None)
        rule_id = current_row.id

        await session.execute(
            text(
                """
                UPDATE workflow.automation_rules SET enabled = :enabled
                WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                """
            ),
            {"enabled": enabled, "id": rule_id, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_automation_rule_result_reference(session, tenant_id=context.tenant_id, rule_key=rule_key)
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="automation_rule",
            aggregate_id=rule_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type=command_type,
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(entity_type="automation_rule", action=audit_action, payload={"enabled": enabled}),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type=outbox_event_type, payload={"rule_id": result.aggregate_id, "rule_key": rule_key}
        ),
    )
    _raise_404_if_rule_not_found(result)
    return result


# ---------------------------------------------------------------------------
# P2B-5e — Automation execution (execute, record execution, retry, suppression)
# ---------------------------------------------------------------------------
#
# These commands record the automation runtime's audit trail
# (workflow.automation_rule_runs) — they do NOT evaluate conditions or
# dispatch actions themselves. Per gap-register item 6 ("Workflow runtime —
# the shell records 'running' but does not execute"), there is no real
# ARQ/Temporal automation dispatcher yet; execute_automation_rule_command
# records the ATTEMPT (dry_run=True -> 'simulated', dry_run=False ->
# 'running'), and a future runtime is expected to call
# record_automation_execution_command with the real outcome once it exists.


async def _fetch_automation_run_result_reference(session: Any, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    """Select an automation run for a command result."""
    row = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                rule_id::text AS rule_id,
                rule_key,
                dry_run,
                status,
                result_json,
                created_by_user_id::text AS created_by,
                created_at
            FROM workflow.automation_rule_runs
            WHERE id = CAST(:run_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
            """
        ),
        {"run_id": run_id, "tenant_id": tenant_id},
    )
    run = row.fetchone()
    if not run:
        return None
    return {
        "id": run.id,
        "rule_id": run.rule_id,
        "rule_key": run.rule_key,
        "dry_run": run.dry_run,
        "status": run.status,
        "result": run.result_json or {},
        "created_by": run.created_by,
        "created_at": _iso(run.created_at),
    }


def _raise_404_if_run_not_found(result: CommandResult) -> None:
    """Same result_reference-truthiness NOT_FOUND-vs-replay technique as every other shared helper here."""
    if not result.result_reference:
        raise HTTPException(status_code=404, detail="Automation run not found")


async def execute_automation_rule_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    rule_key: str,
    dry_run: bool = True,
    initial_result: dict[str, Any] | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Record an automation rule execution attempt in one PG transaction.

    dry_run=True (the schema's own default) sets status='simulated';
    dry_run=False sets status='running' — real execution happens out-of-band,
    not synchronously in this command (see module note above).

    NOT_FOUND if rule_key doesn't exist — use create_automation_rule_command
    first.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    run_id = str(uuid4())
    now = _utc_now()
    result_payload = dict(initial_result or {})
    status = "simulated" if dry_run else "running"

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        rule_row = await session.execute(
            text(
                """
                SELECT id::text AS id FROM workflow.automation_rules
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND rule_key = :rule_key
                """
            ),
            {"tenant_id": context.tenant_id, "rule_key": rule_key},
        )
        found_rule = rule_row.fetchone()
        if found_rule is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="automation_rule", aggregate_id=None)
        rule_id = found_rule.id

        await session.execute(
            text(
                """
                INSERT INTO workflow.automation_rule_runs (
                    id, tenant_id, rule_id, rule_key, dry_run, status, result_json, created_by_user_id, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:rule_id AS UUID), :rule_key,
                    :dry_run, :status, CAST(:result_json AS JSONB), CAST(:created_by_user_id AS UUID), :created_at
                )
                """
            ),
            {
                "id": run_id,
                "tenant_id": context.tenant_id,
                "rule_id": rule_id,
                "rule_key": rule_key,
                "dry_run": dry_run,
                "status": status,
                "result_json": _json_dumps(result_payload),
                "created_by_user_id": created_by_uuid,
                "created_at": now,
            },
        )
        result_reference = await _fetch_automation_run_result_reference(session, tenant_id=context.tenant_id, run_id=run_id)
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="automation_run",
            aggregate_id=run_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="automation.execute_rule",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="automation_run", action="automation.rule.executed", payload={"rule_key": rule_key, "dry_run": dry_run}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="automation.rule.executed", payload={"run_id": result.aggregate_id, "rule_key": rule_key}
        ),
    )
    # execute_automation_rule's aggregate is the run itself ("automation_run"),
    # distinct from the "automation_rule" NOT_FOUND path — aggregate_type trick.
    if result.aggregate_type == "automation_rule":
        raise HTTPException(status_code=404, detail=f"Automation rule '{rule_key}' not found")
    return result


async def record_automation_execution_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    run_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Record the real outcome of a previously-started automation run, in one PG transaction.

    Called by the future automation runtime (see module note above) to close
    out a run created by execute_automation_rule_command — e.g.
    status='succeeded'/'failed' with the real result payload. NOT_FOUND if
    run_id doesn't exist.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    run_uuid = _require_uuid(run_id, "run_id")
    result_payload = dict(result or {})

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        current = await session.execute(
            text(
                "SELECT 1 FROM workflow.automation_rule_runs WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
            ),
            {"id": run_uuid, "tenant_id": context.tenant_id},
        )
        if current.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="automation_run", aggregate_id=run_uuid)

        await session.execute(
            text(
                """
                UPDATE workflow.automation_rule_runs
                SET status = :status, result_json = CAST(:result_json AS JSONB)
                WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                """
            ),
            {"status": status, "result_json": _json_dumps(result_payload), "id": run_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_automation_run_result_reference(session, tenant_id=context.tenant_id, run_id=run_uuid)
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="automation_run",
            aggregate_id=run_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="automation.record_execution",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result_obj = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="automation_run", action="automation.run.recorded", payload={"status": status}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="automation.run.recorded", payload={"run_id": run_uuid, "status": status}
        ),
    )
    _raise_404_if_run_not_found(result_obj)
    return result_obj


async def retry_failed_automation_action_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    run_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Create a fresh retry run for a previously-failed automation run, in one PG transaction.

    Preserves the original run row as history rather than mutating it — a
    retry is a new attempt, referencing the same rule_id/rule_key, starting
    at status='running'. NOT_FOUND if the original run_id doesn't exist.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    original_run_uuid = _require_uuid(run_id, "run_id")
    new_run_id = str(uuid4())
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        original = await session.execute(
            text(
                """
                SELECT rule_id::text AS rule_id, rule_key FROM workflow.automation_rule_runs
                WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                """
            ),
            {"id": original_run_uuid, "tenant_id": context.tenant_id},
        )
        original_row = original.fetchone()
        if original_row is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="automation_run", aggregate_id=None)

        await session.execute(
            text(
                """
                INSERT INTO workflow.automation_rule_runs (
                    id, tenant_id, rule_id, rule_key, dry_run, status, result_json, created_by_user_id, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:rule_id AS UUID), :rule_key,
                    FALSE, 'running', '{}'::jsonb, CAST(:created_by_user_id AS UUID), :created_at
                )
                """
            ),
            {
                "id": new_run_id,
                "tenant_id": context.tenant_id,
                "rule_id": original_row.rule_id,
                "rule_key": original_row.rule_key,
                "created_by_user_id": created_by_uuid,
                "created_at": now,
            },
        )
        result_reference = await _fetch_automation_run_result_reference(session, tenant_id=context.tenant_id, run_id=new_run_id)
        if result_reference:
            result_reference["retried_from_run_id"] = original_run_uuid
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="automation_run",
            aggregate_id=new_run_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="automation.retry_action",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result_obj = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="automation_run", action="automation.run.retried", payload={"retried_from_run_id": original_run_uuid}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="automation.run.retried",
            payload={"run_id": result.aggregate_id, "retried_from_run_id": original_run_uuid},
        ),
    )
    _raise_404_if_run_not_found(result_obj)
    return result_obj


async def record_automation_suppression_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    rule_key: str,
    reason: str,
    matched_entity_type: str | None = None,
    matched_entity_id: str | None = None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Record a suppressed/duplicate-detected automation trigger, in one PG transaction.

    Creates an automation_rule_runs row with status='suppressed' — the audit
    trail proving a matching trigger was correctly NOT re-executed (e.g. the
    same condition firing twice for the same entity within a de-dup window),
    distinct from a real run. NOT_FOUND if rule_key doesn't exist.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    run_id = str(uuid4())
    now = _utc_now()
    result_payload = {
        "reason": reason,
        "matched_entity_type": matched_entity_type,
        "matched_entity_id": matched_entity_id,
    }

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        rule_row = await session.execute(
            text(
                """
                SELECT id::text AS id FROM workflow.automation_rules
                WHERE tenant_id = CAST(:tenant_id AS UUID) AND rule_key = :rule_key
                """
            ),
            {"tenant_id": context.tenant_id, "rule_key": rule_key},
        )
        found_rule = rule_row.fetchone()
        if found_rule is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="automation_rule", aggregate_id=None)
        rule_id = found_rule.id

        await session.execute(
            text(
                """
                INSERT INTO workflow.automation_rule_runs (
                    id, tenant_id, rule_id, rule_key, dry_run, status, result_json, created_by_user_id, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:rule_id AS UUID), :rule_key,
                    FALSE, 'suppressed', CAST(:result_json AS JSONB), CAST(:created_by_user_id AS UUID), :created_at
                )
                """
            ),
            {
                "id": run_id,
                "tenant_id": context.tenant_id,
                "rule_id": rule_id,
                "rule_key": rule_key,
                "result_json": _json_dumps(result_payload),
                "created_by_user_id": created_by_uuid,
                "created_at": now,
            },
        )
        result_reference = await _fetch_automation_run_result_reference(session, tenant_id=context.tenant_id, run_id=run_id)
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="automation_run",
            aggregate_id=run_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="automation.record_suppression",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="automation_run", action="automation.run.suppressed", payload={"rule_key": rule_key, "reason": reason}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="automation.run.suppressed", payload={"run_id": result.aggregate_id, "rule_key": rule_key}
        ),
    )
    # record_automation_suppression's aggregate is the run itself
    # ("automation_run"), distinct from the "automation_rule" NOT_FOUND path.
    if result.aggregate_type == "automation_rule":
        raise HTTPException(status_code=404, detail=f"Automation rule '{rule_key}' not found")
    return result
