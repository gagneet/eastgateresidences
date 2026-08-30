# @featuretrace:powerhouse-foundation — First real PostgreSQL command service for Powerhouse conversations.
# Layer: service
# Data flow: powerhouse_conversation_service.create_thread/add_message → this module → PowerhouseCommandUnitOfWork → communications.* tables + core.audit_events + core.outbox (tenant-scoped).
# Related: backend/services/powerhouse_command_foundation.py
#          backend/services/powerhouse_conversation_service.py
#          backend/routers/powerhouse_conversations.py

"""PostgreSQL command implementations for the Powerhouse conversation surface.

create-conversation, add-message, update-status, assign, add/remove-watcher,
add-link, set-SLA, and add/remove-participant are implemented here. Everything
else in powerhouse_conversation_service.py's write paths (workflow conversion,
AI placeholders, inbox/email/workflow commands) still 501s in postgres_write
mode via _assert_write_target. See
docs/features/powerhouse_p2b_read_classification_and_gaps.md's gap register
for the remaining command surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text

from models.powerhouse_conversation import ConversationMessageCreate, ConversationThreadCreate
from services.powerhouse_command_foundation import (
    AuditEventSpec,
    CommandContext,
    CommandResult,
    CommandStatus,
    OutboxEventSpec,
    PowerhouseCommandUnitOfWork,
)

COMMAND_SCOPE = "powerhouse_conversations"

_uow = PowerhouseCommandUnitOfWork()


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    """Serialise a datetime for a JSON-safe command payload, or None."""
    return dt.isoformat() if dt else None


def _require_uuid(value: str, field_name: str) -> str:
    """Validate a legacy/user-supplied id is UUID-shaped before it reaches SQL.

    communications.* participant/watcher/creator columns are UUID NOT NULL.
    Real users always have proper UUID4 ids (verified live); a malformed id
    here is a genuine client input error and must surface as 422, not a raw
    DB CAST failure classified as an APPLICATION_ERROR 500.
    """
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} '{value}' is not a valid identifier for a PostgreSQL-backed conversation",
        ) from exc


def _dedupe_ids(values: list[str], add_value: str | None = None) -> list[str]:
    """Sort and deduplicate a list of string ids, optionally adding one more."""
    items = [str(v).strip() for v in values if str(v).strip()]
    if add_value and add_value.strip():
        items.append(add_value.strip())
    return sorted(set(items))


async def create_conversation_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    actor_user_id: str,
    payload: ConversationThreadCreate,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Create a conversation thread + its initial message in one PG transaction."""
    thread_id = str(uuid4())
    message_id = str(uuid4())
    now = _utc_now()

    created_by_uuid = _require_uuid(actor_user_id, "created_by")
    participant_ids = _dedupe_ids(payload.participant_ids, created_by_uuid)
    watcher_ids = _dedupe_ids(payload.watcher_ids)
    participant_uuids = [_require_uuid(p, "participant_id") for p in participant_ids]
    watcher_uuids = [_require_uuid(w, "watcher_id") for w in watcher_ids]
    assigned_to_uuid = _require_uuid(payload.assigned_to, "assigned_to") if payload.assigned_to else None

    linked_entity_type = payload.linked_entity.entity_type if payload.linked_entity else None
    linked_entity_id = payload.linked_entity.entity_id if payload.linked_entity else None

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        await session.execute(
            text(
                """
                INSERT INTO communications.conversation_threads (
                    id, tenant_id, scheme_id, building_id, inbox_id,
                    subject, source_channel, status, priority, visibility,
                    linked_entity_type, linked_entity_id, source_external_id,
                    assigned_to_user_id, sla_due_at, is_archived,
                    created_by_user_id, created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID), :building_id, NULL,
                    :subject, :source_channel, 'open', :priority, :visibility,
                    :linked_entity_type, :linked_entity_id, :source_external_id,
                    CAST(:assigned_to_user_id AS UUID), :sla_due_at, FALSE,
                    CAST(:created_by_user_id AS UUID), :created_at, :updated_at
                )
                """
            ),
            {
                "id": thread_id,
                "tenant_id": context.tenant_id,
                "scheme_id": context.scheme_id,
                "building_id": building_id,
                "subject": payload.subject,
                "source_channel": payload.source_channel,
                "priority": payload.priority,
                "visibility": payload.visibility,
                "linked_entity_type": linked_entity_type,
                "linked_entity_id": linked_entity_id,
                "source_external_id": payload.source_external_id,
                "assigned_to_user_id": assigned_to_uuid,
                "sla_due_at": payload.sla_due_at,
                "created_by_user_id": created_by_uuid,
                "created_at": now,
                "updated_at": now,
            },
        )

        for user_id in participant_uuids:
            await session.execute(
                text(
                    """
                    INSERT INTO communications.conversation_participants (
                        id, tenant_id, thread_id, user_id, role, created_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:thread_id AS UUID),
                        CAST(:user_id AS UUID), :role, :created_at
                    )
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": context.tenant_id,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "role": "creator" if user_id == created_by_uuid else "participant",
                    "created_at": now,
                },
            )

        for user_id in watcher_uuids:
            await session.execute(
                text(
                    """
                    INSERT INTO communications.conversation_watchers (
                        id, tenant_id, thread_id, user_id, created_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:thread_id AS UUID),
                        CAST(:user_id AS UUID), :created_at
                    )
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": context.tenant_id,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "created_at": now,
                },
            )

        await session.execute(
            text(
                """
                INSERT INTO communications.conversation_messages (
                    id, tenant_id, thread_id, message_type, visibility, body,
                    is_internal_note, source_external_id, created_by_user_id,
                    created_at, is_deleted
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:thread_id AS UUID),
                    'message', :visibility, :body,
                    FALSE, NULL, CAST(:created_by_user_id AS UUID),
                    :created_at, FALSE
                )
                """
            ),
            {
                "id": message_id,
                "tenant_id": context.tenant_id,
                "thread_id": thread_id,
                "visibility": payload.visibility,
                "body": payload.body,
                "created_by_user_id": created_by_uuid,
                "created_at": now,
            },
        )

        result_reference = {
            "id": thread_id,
            "building_id": building_id,
            "subject": payload.subject,
            "source_channel": payload.source_channel,
            "priority": payload.priority,
            "status": "open",
            "visibility": payload.visibility,
            "participant_ids": participant_ids,
            "watcher_ids": watcher_ids,
            "linked_entity": (
                {"entity_type": linked_entity_type, "entity_id": linked_entity_id} if linked_entity_type else None
            ),
            "unit_id": payload.unit_id,
            "lot_id": payload.lot_id,
            "source_external_id": payload.source_external_id,
            "assigned_to": assigned_to_uuid,
            "sla_due_at": _iso(payload.sla_due_at),
            "created_by": created_by_uuid,
            "updated_by": created_by_uuid,
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "is_archived": False,
            "initial_message_id": message_id,
        }
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="conversation_thread",
            aggregate_id=thread_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.create_thread",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    return await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread",
            action="conversation.thread.created",
            payload={
                "source_channel": payload.source_channel,
                "priority": payload.priority,
                "visibility": payload.visibility,
            },
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.conversation.created",
            payload={"thread_id": result.aggregate_id, "subject": payload.subject},
        ),
    )


async def add_message_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    payload: ConversationMessageCreate,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Append a message to an existing conversation thread in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "created_by")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    message_id = str(uuid4())
    now = _utc_now()
    is_internal_note = payload.message_type == "internal_note"
    visibility = "internal_only" if is_internal_note else "participants_only"

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        thread_row = await session.execute(
            text(
                """
                SELECT id::text AS id
                FROM communications.conversation_threads
                WHERE id = CAST(:thread_id AS UUID)
                  AND building_id = :building_id
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"thread_id": thread_uuid, "building_id": building_id},
        )
        if thread_row.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)

        await session.execute(
            text(
                """
                INSERT INTO communications.conversation_messages (
                    id, tenant_id, thread_id, message_type, visibility, body,
                    is_internal_note, source_external_id, created_by_user_id,
                    created_at, is_deleted
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:thread_id AS UUID),
                    :message_type, :visibility, :body,
                    :is_internal_note, NULL, CAST(:created_by_user_id AS UUID),
                    :created_at, FALSE
                )
                """
            ),
            {
                "id": message_id,
                "tenant_id": context.tenant_id,
                "thread_id": thread_uuid,
                "message_type": payload.message_type,
                "visibility": visibility,
                "body": payload.body,
                "is_internal_note": is_internal_note,
                "created_by_user_id": created_by_uuid,
                "created_at": now,
            },
        )
        await session.execute(
            text(
                """
                UPDATE communications.conversation_threads
                SET updated_at = :updated_at
                WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                """
            ),
            {"updated_at": now, "thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )

        result_reference = {
            "id": message_id,
            "thread_id": thread_uuid,
            "building_id": building_id,
            "message_type": payload.message_type,
            "visibility": visibility,
            "body": payload.body,
            "attachments": [a.model_dump() for a in payload.attachments],
            "created_by": created_by_uuid,
            "created_at": _iso(now),
            "is_deleted": False,
        }
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="conversation_message",
            aggregate_id=message_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.add_message",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_message",
            action=(
                "conversation.note.created" if payload.message_type == "internal_note" else "conversation.message.created"
            ),
            payload={"thread_id": thread_uuid, "message_type": payload.message_type},
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.message.created",
            payload={"thread_id": thread_uuid, "message_id": result.aggregate_id},
        ),
    )
    # aggregate_type is only ever "conversation_thread" on the NOT_FOUND path
    # (the CREATED path always sets "conversation_message") — checking it
    # instead of `result.status == CommandStatus.NOT_FOUND` also catches an
    # IDEMPOTENT_REPLAY of a previously-NOT_FOUND outcome: PowerhouseCommandUnitOfWork's
    # replay branch always returns status=IDEMPOTENT_REPLAY regardless of what
    # the original call resolved to, so status alone can't distinguish "replay
    # of a successful create" from "replay of a 404" — aggregate_type survives
    # the replay because it's read back from the stored idempotency row.
    if result.aggregate_type == "conversation_thread":
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    return result


async def _fetch_thread_result_reference(session: Any, *, tenant_id: str, thread_id: str) -> dict[str, Any] | None:
    """Select a conversation thread + its participant/watcher ids for a command result.

    Returns None if the thread doesn't exist (or is archived) for this tenant.
    Shared by every command below that mutates a thread and returns its new state.

    unit_id/lot_id are always None here: communications.conversation_threads has
    no such columns (0046_powerhouse_foundation_shell.py never added them, even
    though ConversationThreadCreate/the Mongo doc carry them) — a known,
    pre-existing schema gap, not something to paper over by inventing a value.
    """
    row = await session.execute(
        text(
            """
            SELECT
                t.id::text AS id,
                t.building_id,
                t.subject,
                t.source_channel,
                t.priority,
                t.status,
                t.visibility,
                t.linked_entity_type,
                t.linked_entity_id,
                t.source_external_id,
                t.assigned_to_user_id::text AS assigned_to,
                t.sla_due_at,
                t.created_by_user_id::text AS created_by,
                t.created_at,
                t.updated_at,
                t.is_archived,
                COALESCE(
                    (
                        SELECT jsonb_agg(cp.user_id::text)
                        FROM communications.conversation_participants cp
                        WHERE cp.thread_id = t.id AND cp.tenant_id = t.tenant_id
                    ),
                    '[]'::jsonb
                ) AS participant_ids,
                COALESCE(
                    (
                        SELECT jsonb_agg(cw.user_id::text)
                        FROM communications.conversation_watchers cw
                        WHERE cw.thread_id = t.id AND cw.tenant_id = t.tenant_id
                    ),
                    '[]'::jsonb
                ) AS watcher_ids
            FROM communications.conversation_threads t
            WHERE t.id = CAST(:thread_id AS UUID)
              AND t.tenant_id = CAST(:tenant_id AS UUID)
              AND COALESCE(t.is_archived, FALSE) = FALSE
            """
        ),
        {"thread_id": thread_id, "tenant_id": tenant_id},
    )
    thread = row.fetchone()
    if not thread:
        return None
    linked_entity = None
    if thread.linked_entity_type:
        linked_entity = {"entity_type": thread.linked_entity_type, "entity_id": thread.linked_entity_id}
    return {
        "id": thread.id,
        "building_id": thread.building_id,
        "subject": thread.subject,
        "source_channel": thread.source_channel,
        "priority": thread.priority,
        "status": thread.status,
        "visibility": thread.visibility,
        "participant_ids": list(thread.participant_ids or []),
        "watcher_ids": list(thread.watcher_ids or []),
        "linked_entity": linked_entity,
        "unit_id": None,
        "lot_id": None,
        "source_external_id": thread.source_external_id,
        "assigned_to": thread.assigned_to,
        "sla_due_at": _iso(thread.sla_due_at),
        "created_by": thread.created_by,
        "created_at": _iso(thread.created_at),
        "updated_at": _iso(thread.updated_at),
        "is_archived": bool(thread.is_archived),
    }


def _raise_404_if_not_found(result: CommandResult) -> None:
    """Convert a NOT_FOUND (or an idempotent replay of one) into HTTPException(404).

    Unlike add_message_command's aggregate_type check, every command below
    shares aggregate_type="conversation_thread" on BOTH the success and
    NOT_FOUND paths, so aggregate_type can't distinguish them. result_reference
    can: it's only ever populated on success, stays {} on NOT_FOUND, and
    PowerhouseCommandUnitOfWork's idempotency replay restores whatever was
    actually stored the first time — so this check is replay-safe without
    needing a status check that idempotent replay would mask anyway.
    """
    if not result.result_reference:
        raise HTTPException(status_code=404, detail="Conversation thread not found")


async def update_thread_status_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    status: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Update a conversation thread's status in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        await session.execute(
            text(
                """
                UPDATE communications.conversation_threads
                SET status = :status, updated_at = :updated_at
                WHERE id = CAST(:thread_id AS UUID)
                  AND tenant_id = CAST(:tenant_id AS UUID)
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"status": status, "updated_at": now, "thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_thread_result_reference(session, tenant_id=context.tenant_id, thread_id=thread_uuid)
        if not result_reference:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)
        result_reference["updated_by"] = created_by_uuid
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="conversation_thread",
            aggregate_id=thread_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.update_thread_status",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread", action="conversation.thread.status_changed", payload={"status": status}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.thread.status_changed", payload={"thread_id": thread_uuid, "status": status}
        ),
    )
    _raise_404_if_not_found(result)
    return result


async def assign_thread_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    assignee_user_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Assign a conversation thread to a user in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    assignee_uuid = _require_uuid(assignee_user_id, "assignee_user_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        await session.execute(
            text(
                """
                UPDATE communications.conversation_threads
                SET assigned_to_user_id = CAST(:assignee_id AS UUID), updated_at = :updated_at
                WHERE id = CAST(:thread_id AS UUID)
                  AND tenant_id = CAST(:tenant_id AS UUID)
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"assignee_id": assignee_uuid, "updated_at": now, "thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_thread_result_reference(session, tenant_id=context.tenant_id, thread_id=thread_uuid)
        if not result_reference:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)
        result_reference["updated_by"] = created_by_uuid
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="conversation_thread",
            aggregate_id=thread_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.assign_thread",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread",
            action="conversation.thread.assigned",
            payload={"assigned_to": assignee_uuid},
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.thread.assigned", payload={"thread_id": thread_uuid, "assigned_to": assignee_uuid}
        ),
    )
    _raise_404_if_not_found(result)
    return result


async def add_watcher_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    watcher_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Add a watcher to a conversation thread in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    watcher_uuid = _require_uuid(watcher_id, "watcher_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        exists = await session.execute(
            text(
                """
                SELECT 1 FROM communications.conversation_threads
                WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        if exists.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)

        await session.execute(
            text(
                """
                INSERT INTO communications.conversation_watchers (id, tenant_id, thread_id, user_id, created_at)
                VALUES (CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:thread_id AS UUID), CAST(:user_id AS UUID), :created_at)
                ON CONFLICT DO NOTHING
                """
            ),
            {"id": str(uuid4()), "tenant_id": context.tenant_id, "thread_id": thread_uuid, "user_id": watcher_uuid, "created_at": now},
        )
        await session.execute(
            text(
                "UPDATE communications.conversation_threads SET updated_at = :updated_at "
                "WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
            ),
            {"updated_at": now, "thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_thread_result_reference(session, tenant_id=context.tenant_id, thread_id=thread_uuid)
        result_reference["updated_by"] = created_by_uuid
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="conversation_thread",
            aggregate_id=thread_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.add_watcher",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread", action="conversation.watcher.added", payload={"watcher_id": watcher_uuid}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.watcher.added", payload={"thread_id": thread_uuid, "watcher_id": watcher_uuid}
        ),
    )
    _raise_404_if_not_found(result)
    return result


async def remove_watcher_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    watcher_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Remove a watcher from a conversation thread in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    watcher_uuid = _require_uuid(watcher_id, "watcher_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        exists = await session.execute(
            text(
                """
                SELECT 1 FROM communications.conversation_threads
                WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        if exists.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)

        await session.execute(
            text(
                """
                DELETE FROM communications.conversation_watchers
                WHERE thread_id = CAST(:thread_id AS UUID)
                  AND tenant_id = CAST(:tenant_id AS UUID)
                  AND user_id = CAST(:user_id AS UUID)
                """
            ),
            {"thread_id": thread_uuid, "tenant_id": context.tenant_id, "user_id": watcher_uuid},
        )
        await session.execute(
            text(
                "UPDATE communications.conversation_threads SET updated_at = :updated_at "
                "WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
            ),
            {"updated_at": now, "thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_thread_result_reference(session, tenant_id=context.tenant_id, thread_id=thread_uuid)
        result_reference["updated_by"] = created_by_uuid
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="conversation_thread",
            aggregate_id=thread_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.remove_watcher",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread", action="conversation.watcher.removed", payload={"watcher_id": watcher_uuid}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.watcher.removed", payload={"thread_id": thread_uuid, "watcher_id": watcher_uuid}
        ),
    )
    _raise_404_if_not_found(result)
    return result


async def add_link_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    entity_type: str,
    entity_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Link a conversation thread to another entity in one PG transaction.

    Unlike the other four commands here, this one creates a new distinct row
    (communications.conversation_links) and returns THAT row — not the
    thread — matching the Mongo path's `return link_doc` contract exactly.
    """
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    link_id = str(uuid4())
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        exists = await session.execute(
            text(
                """
                SELECT 1 FROM communications.conversation_threads
                WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        if exists.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)

        await session.execute(
            text(
                """
                INSERT INTO communications.conversation_links (
                    id, tenant_id, thread_id, linked_entity_type, linked_entity_id, created_by_user_id, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:thread_id AS UUID),
                    :entity_type, :entity_id, CAST(:created_by_user_id AS UUID), :created_at
                )
                """
            ),
            {
                "id": link_id,
                "tenant_id": context.tenant_id,
                "thread_id": thread_uuid,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "created_by_user_id": created_by_uuid,
                "created_at": now,
            },
        )
        await session.execute(
            text(
                """
                UPDATE communications.conversation_threads
                SET linked_entity_type = :entity_type, linked_entity_id = :entity_id, updated_at = :updated_at
                WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                """
            ),
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "updated_at": now,
                "thread_id": thread_uuid,
                "tenant_id": context.tenant_id,
            },
        )

        result_reference = {
            "id": link_id,
            "thread_id": thread_uuid,
            "building_id": building_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "created_by": created_by_uuid,
            "created_at": _iso(now),
        }
        return CommandResult(
            status=CommandStatus.CREATED,
            aggregate_type="conversation_link",
            aggregate_id=link_id,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.add_link",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread",
            action="conversation.linked_entity.added",
            payload={"entity_type": entity_type, "entity_id": entity_id},
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.link.added",
            payload={"thread_id": thread_uuid, "entity_type": entity_type, "entity_id": entity_id},
        ),
    )
    # add_link's aggregate is the link itself (aggregate_type="conversation_link"),
    # distinct from every other command's "conversation_thread" — so the same
    # aggregate_type trick used in add_message_command works here too.
    if result.aggregate_type == "conversation_thread":
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    return result


async def set_thread_sla_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    sla_due_at: datetime | None,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Set (or clear) a conversation thread's SLA due date in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        await session.execute(
            text(
                """
                UPDATE communications.conversation_threads
                SET sla_due_at = :sla_due_at, updated_at = :updated_at
                WHERE id = CAST(:thread_id AS UUID)
                  AND tenant_id = CAST(:tenant_id AS UUID)
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"sla_due_at": sla_due_at, "updated_at": now, "thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_thread_result_reference(session, tenant_id=context.tenant_id, thread_id=thread_uuid)
        if not result_reference:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)
        result_reference["updated_by"] = created_by_uuid
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="conversation_thread",
            aggregate_id=thread_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.set_thread_sla",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread",
            action="conversation.thread.sla_updated",
            payload={"sla_due_at": _iso(sla_due_at)},
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.thread.sla_updated",
            payload={"thread_id": thread_uuid, "sla_due_at": _iso(sla_due_at)},
        ),
    )
    _raise_404_if_not_found(result)
    return result


async def add_participant_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    participant_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Add a participant to a conversation thread in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    participant_uuid = _require_uuid(participant_id, "participant_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        exists = await session.execute(
            text(
                """
                SELECT 1 FROM communications.conversation_threads
                WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        if exists.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)

        await session.execute(
            text(
                """
                INSERT INTO communications.conversation_participants (id, tenant_id, thread_id, user_id, role, created_at)
                VALUES (CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:thread_id AS UUID), CAST(:user_id AS UUID), :role, :created_at)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": context.tenant_id,
                "thread_id": thread_uuid,
                "user_id": participant_uuid,
                "role": "participant",
                "created_at": now,
            },
        )
        await session.execute(
            text(
                "UPDATE communications.conversation_threads SET updated_at = :updated_at "
                "WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
            ),
            {"updated_at": now, "thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_thread_result_reference(session, tenant_id=context.tenant_id, thread_id=thread_uuid)
        result_reference["updated_by"] = created_by_uuid
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="conversation_thread",
            aggregate_id=thread_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.add_participant",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread", action="conversation.participant.added", payload={"participant_id": participant_uuid}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.participant.added", payload={"thread_id": thread_uuid, "participant_id": participant_uuid}
        ),
    )
    _raise_404_if_not_found(result)
    return result


async def remove_participant_command(
    *,
    building_id: str,
    tenant_id: str,
    scheme_id: str,
    thread_id: str,
    actor_user_id: str,
    participant_id: str,
    idempotency_key: str,
    is_test_data: bool = False,
) -> CommandResult:
    """Remove a participant from a conversation thread in one PG transaction."""
    created_by_uuid = _require_uuid(actor_user_id, "actor")
    thread_uuid = _require_uuid(thread_id, "thread_id")
    participant_uuid = _require_uuid(participant_id, "participant_id")
    now = _utc_now()

    async def _mutate(session: Any, context: CommandContext) -> CommandResult:
        exists = await session.execute(
            text(
                """
                SELECT 1 FROM communications.conversation_threads
                WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)
                  AND COALESCE(is_archived, FALSE) = FALSE
                """
            ),
            {"thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        if exists.fetchone() is None:
            return CommandResult(status=CommandStatus.NOT_FOUND, aggregate_type="conversation_thread", aggregate_id=thread_uuid)

        await session.execute(
            text(
                """
                DELETE FROM communications.conversation_participants
                WHERE thread_id = CAST(:thread_id AS UUID)
                  AND tenant_id = CAST(:tenant_id AS UUID)
                  AND user_id = CAST(:user_id AS UUID)
                """
            ),
            {"thread_id": thread_uuid, "tenant_id": context.tenant_id, "user_id": participant_uuid},
        )
        await session.execute(
            text(
                "UPDATE communications.conversation_threads SET updated_at = :updated_at "
                "WHERE id = CAST(:thread_id AS UUID) AND tenant_id = CAST(:tenant_id AS UUID)"
            ),
            {"updated_at": now, "thread_id": thread_uuid, "tenant_id": context.tenant_id},
        )
        result_reference = await _fetch_thread_result_reference(session, tenant_id=context.tenant_id, thread_id=thread_uuid)
        result_reference["updated_by"] = created_by_uuid
        return CommandResult(
            status=CommandStatus.UPDATED,
            aggregate_type="conversation_thread",
            aggregate_id=thread_uuid,
            result_reference=result_reference,
        )

    context = CommandContext(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        building_id=building_id,
        command_scope=COMMAND_SCOPE,
        command_type="communications.remove_participant",
        idempotency_key=idempotency_key,
        actor_user_id=created_by_uuid,
        is_test_data=is_test_data,
    )

    result = await _uow.execute(
        context=context,
        mutate=_mutate,
        audit_factory=lambda result: AuditEventSpec(
            entity_type="conversation_thread", action="conversation.participant.removed", payload={"participant_id": participant_uuid}
        ),
        outbox_factory=lambda result: OutboxEventSpec(
            event_type="communications.participant.removed", payload={"thread_id": thread_uuid, "participant_id": participant_uuid}
        ),
    )
    _raise_404_if_not_found(result)
    return result
