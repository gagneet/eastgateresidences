# @featuretrace:powerhouse-foundation — Foundation shell APIs for conversations, inboxes, and workflow automation.
# Layer: router
# Data flow: dashboard powerhouse pages → /api/powerhouse/* endpoints → powerhouse service → Mongo shell stores + provider abstractions (building-scoped).
# Related: frontend/src/pages/powerhouse/PowerhouseConversationCenterPage.tsx
#           backend/services/powerhouse_conversation_service.py
#           docs/architecture/workflow-event-engine.md

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, HTTPException
from models.user import UserRole

from models.powerhouse_conversation import (
    AutomationRuleRunRequest,
    ConversationLinkCreate,
    ConversationMessageCreate,
    ConversationParticipantUpdate,
    ConversationThreadAssignment,
    ConversationThreadCreate,
    ConversationThreadSlaUpdate,
    ConversationThreadStatusUpdate,
    ConversationWatcherUpdate,
    ConvertMessageToWorkflowRequest,
    ConvertThreadToWorkflowRequest,
    InboundEmailEventCreate,
    InboxConfigUpsert,
    OutboundDraftCreate,
    WorkflowAssignmentCreate,
    WorkflowEventCreate,
    WorkflowInstanceCreate,
    WorkflowStepCompleteRequest,
)
from services import powerhouse_conversation_service as svc
from utils.auth import get_current_building, get_current_user
from utils.permissions import require_feature

# Umbrella feature gate: ALL /powerhouse/* endpoints require this toggle.
# Default is is_enabled=False globally — only enabled per building/scheme via
# core.feature_toggle_overrides after the readiness gates in
# docs/architecture/feature-toggle-governance.md are satisfied.
router = APIRouter(
    prefix="/powerhouse",
    tags=["Powerhouse Foundation"],
    dependencies=[Depends(require_feature("powerhouse_conversations"))],
)


@router.get("/status")
async def get_powerhouse_operational_status(
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Retrieve detailed, honest operational status of Powerhouse domains."""
    from services.cutover_status_service import get_or_default_cutover_status, list_shadow_diffs
    from sqlalchemy import text
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant

    domains_status = {}
    scheme = await resolve_scheme_context(building_id)

    # 1. powerhouse_conversations
    conv_status = await get_or_default_cutover_status(building_id, "powerhouse_conversations")
    pg_conv_rows = 0
    if scheme:
        try:
            async with async_session_context() as session:
                await set_tenant(session, str(scheme["tenant_id"]))
                rows_res = await session.execute(
                    text("SELECT COUNT(*) AS n FROM communications.conversation_threads WHERE building_id = :building_id"),
                    {"building_id": building_id}
                )
                r = rows_res.fetchone()
                pg_conv_rows = r.n if r else 0
        except Exception:
            pass

    unresolved_conv_diffs = len(await list_shadow_diffs(building_id=building_id, domain="powerhouse_conversations", resolved=False))

    domains_status["powerhouse_conversations"] = {
        "mode": conv_status.mode.value,
        "read_source": conv_status.read_source.value,
        "write_source": conv_status.write_source.value,
        "readiness_status": conv_status.readiness_status.value,
        "postgres_rows": pg_conv_rows,
        "unresolved_differences": unresolved_conv_diffs,
        "last_compared_at": conv_status.last_shadow_diff_at.isoformat() if conv_status.last_shadow_diff_at else None,
    }

    # 2. powerhouse_workflows
    wf_status = await get_or_default_cutover_status(building_id, "powerhouse_workflows")
    pg_wf_rows = 0
    if scheme:
        try:
            async with async_session_context() as session:
                await set_tenant(session, str(scheme["tenant_id"]))
                rows_res = await session.execute(
                    text("SELECT COUNT(*) AS n FROM workflow.workflow_instances WHERE building_id = :building_id"),
                    {"building_id": building_id}
                )
                r = rows_res.fetchone()
                pg_wf_rows = r.n if r else 0
        except Exception:
            pass

    unresolved_wf_diffs = len(await list_shadow_diffs(building_id=building_id, domain="powerhouse_workflows", resolved=False))

    domains_status["powerhouse_workflows"] = {
        "mode": wf_status.mode.value,
        "read_source": wf_status.read_source.value,
        "write_source": wf_status.write_source.value,
        "readiness_status": wf_status.readiness_status.value,
        "postgres_rows": pg_wf_rows,
        "unresolved_differences": unresolved_wf_diffs,
        "last_compared_at": wf_status.last_shadow_diff_at.isoformat() if wf_status.last_shadow_diff_at else None,
    }

    return {
        "building_id": building_id,
        "domains": domains_status,
    }


@router.get("/status/divergences")
async def get_powerhouse_status_divergences(
    domain: str | None = Query(default=None),
    route: str | None = Query(default=None),
    resolved: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Retrieve normalized shadow-read comparison differences / divergences for authorized users."""
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in {UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER}:
        raise HTTPException(status_code=403, detail="Divergences are restricted to operators")

    from services.cutover_status_service import list_shadow_diffs, canonical_domain

    search_domain = "powerhouse_conversations"
    if domain:
        search_domain = canonical_domain(domain)

    diffs = await list_shadow_diffs(
        building_id=building_id,
        domain=search_domain,
        resolved=resolved,
        limit=limit,
    )

    if route:
        diffs = [d for d in diffs if d.route == route]

    return [
        {
            "id": d.id,
            "building_id": d.building_id,
            "domain": d.domain,
            "route": d.route,
            "diff_type": d.diff_type,
            "mongo_value": d.mongo_value,
            "pg_value": d.pg_value,
            "divergence_score": d.divergence_score,
            "resolved": d.resolved,
            "resolved_at": d.resolved_at,
            "notes": d.notes,
            "created_at": d.created_at,
        }
        for d in diffs
    ]


@router.get("/conversations/threads")
async def list_conversation_threads(
    status: str | None = Query(default=None),
    source_channel: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_test_data: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_conversation_threads
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.list_threads(
        building_id=building_id,
        current_user=current_user,
        status=status,
        source_channel=source_channel,
        limit=limit,
        offset=offset,
        include_test_data=include_test_data,
    )


@router.post("/conversations/threads")
async def create_conversation_thread(
    payload: ConversationThreadCreate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: create_conversation_thread
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.create_thread(
        building_id=building_id, current_user=current_user, payload=payload, idempotency_key=idempotency_key
    )


@router.get("/conversations/threads/{thread_id}")
async def get_conversation_thread_detail(
    thread_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_conversation_thread_detail
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.get_thread_detail(building_id=building_id, thread_id=thread_id, current_user=current_user)


@router.post("/conversations/threads/{thread_id}/messages")
async def add_thread_message(
    thread_id: str,
    payload: ConversationMessageCreate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: add_thread_message
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.add_message(
        building_id=building_id,
        thread_id=thread_id,
        current_user=current_user,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post("/conversations/threads/{thread_id}/notes")
async def add_thread_internal_note(
    thread_id: str,
    payload: ConversationMessageCreate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: add_thread_internal_note
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    internal_note_payload = payload.model_copy(update={"message_type": "internal_note"})
    return await svc.add_message(
        building_id=building_id,
        thread_id=thread_id,
        current_user=current_user,
        payload=internal_note_payload,
        idempotency_key=idempotency_key,
    )


@router.patch("/conversations/threads/{thread_id}/status")
async def update_conversation_thread_status(
    thread_id: str,
    payload: ConversationThreadStatusUpdate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: update_conversation_thread_status
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.update_thread_status(
        building_id=building_id, thread_id=thread_id, current_user=current_user, payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post("/conversations/threads/{thread_id}/assign")
async def assign_conversation_thread(
    thread_id: str,
    payload: ConversationThreadAssignment,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: assign_conversation_thread
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.assign_thread(
        building_id=building_id, thread_id=thread_id, current_user=current_user, assignee_user_id=payload.assigned_to,
        idempotency_key=idempotency_key,
    )


@router.post("/conversations/threads/{thread_id}/watchers")
async def add_conversation_watcher(
    thread_id: str,
    payload: ConversationWatcherUpdate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: add_conversation_watcher
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.add_watcher(
        building_id=building_id, thread_id=thread_id, current_user=current_user, watcher_id=payload.watcher_id,
        idempotency_key=idempotency_key,
    )


@router.delete("/conversations/threads/{thread_id}/watchers/{watcher_id}")
async def remove_conversation_watcher(
    thread_id: str,
    watcher_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: remove_conversation_watcher
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.remove_watcher(
        building_id=building_id, thread_id=thread_id, current_user=current_user, watcher_id=watcher_id,
        idempotency_key=idempotency_key,
    )


@router.post("/conversations/threads/{thread_id}/links")
async def add_conversation_link(
    thread_id: str,
    payload: ConversationLinkCreate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: add_conversation_link
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.add_link(
        building_id=building_id,
        thread_id=thread_id,
        current_user=current_user,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        idempotency_key=idempotency_key,
    )


@router.patch("/conversations/threads/{thread_id}/sla")
async def update_conversation_thread_sla(
    thread_id: str,
    payload: ConversationThreadSlaUpdate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Set (or clear) a conversation thread's SLA due date. PostgreSQL-only — see svc.set_thread_sla."""
    return await svc.set_thread_sla(
        building_id=building_id,
        thread_id=thread_id,
        current_user=current_user,
        sla_due_at=payload.sla_due_at,
        idempotency_key=idempotency_key,
    )


@router.post("/conversations/threads/{thread_id}/participants")
async def add_conversation_participant(
    thread_id: str,
    payload: ConversationParticipantUpdate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Add a participant to a conversation thread. PostgreSQL-only — see svc.add_participant."""
    return await svc.add_participant(
        building_id=building_id,
        thread_id=thread_id,
        current_user=current_user,
        participant_id=payload.participant_id,
        idempotency_key=idempotency_key,
    )


@router.delete("/conversations/threads/{thread_id}/participants/{participant_id}")
async def remove_conversation_participant(
    thread_id: str,
    participant_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Remove a participant from a conversation thread. PostgreSQL-only — see svc.remove_participant."""
    return await svc.remove_participant(
        building_id=building_id,
        thread_id=thread_id,
        current_user=current_user,
        participant_id=participant_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/conversations/threads/{thread_id}/convert-to-workflow",
    dependencies=[Depends(require_feature("powerhouse_workflow_engine"))],
)
async def convert_conversation_to_workflow(
    thread_id: str,
    payload: ConvertThreadToWorkflowRequest,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: convert_conversation_to_workflow
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.convert_to_workflow(building_id=building_id, thread_id=thread_id, current_user=current_user, payload=payload)


@router.post(
    "/conversations/messages/{message_id}/convert-to-workflow",
    dependencies=[Depends(require_feature("powerhouse_workflow_engine"))],
)
async def convert_conversation_message_to_workflow(
    message_id: str,
    payload: ConvertMessageToWorkflowRequest,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: convert_conversation_message_to_workflow
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.convert_message_to_workflow(
        building_id=building_id,
        message_id=message_id,
        current_user=current_user,
        payload=payload,
    )


@router.post(
    "/conversations/threads/{thread_id}/ai-summary",
    dependencies=[Depends(require_feature("powerhouse_ai_summary"))],
)
async def generate_thread_ai_summary(
    thread_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: generate_thread_ai_summary
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.generate_ai_summary_placeholder(building_id=building_id, thread_id=thread_id, current_user=current_user)


@router.post(
    "/conversations/threads/{thread_id}/ai-response-draft",
    dependencies=[Depends(require_feature("powerhouse_ai_summary"))],
)
async def generate_thread_ai_response_draft(
    thread_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: generate_thread_ai_response_draft
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.generate_ai_response_draft_placeholder(building_id=building_id, thread_id=thread_id, current_user=current_user)


@router.get("/inboxes")
async def list_building_inboxes(
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_building_inboxes
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.list_inboxes(building_id=building_id, current_user=current_user)


@router.post(
    "/inboxes/configure",
    dependencies=[Depends(require_feature("powerhouse_shared_inbox"))],
)
async def configure_inbox_placeholder(
    payload: InboxConfigUpsert,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: configure_inbox_placeholder
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.configure_inbox(building_id=building_id, current_user=current_user, payload=payload)


@router.post(
    "/inboxes/{inbox_id}/inbound-email",
    dependencies=[Depends(require_feature("powerhouse_email_intake"))],
)
async def receive_inbound_email_placeholder(
    inbox_id: str,
    payload: InboundEmailEventCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: receive_inbound_email_placeholder
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.process_inbound_email(
        building_id=building_id,
        inbox_id=inbox_id,
        current_user=current_user,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/inboxes/{inbox_id}/map-inbound",
    dependencies=[Depends(require_feature("powerhouse_email_intake"))],
)
async def map_inbound_email_to_thread(
    inbox_id: str,
    message_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: map_inbound_email_to_thread
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.map_inbound_to_thread(
        building_id=building_id, inbox_id=inbox_id, current_user=current_user, message_id=message_id
    )


@router.post(
    "/inboxes/{inbox_id}/outbound-drafts",
    dependencies=[Depends(require_feature("powerhouse_shared_inbox"))],
)
async def create_outbound_draft(
    inbox_id: str,
    payload: OutboundDraftCreate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_outbound_draft
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.create_outbound_draft(
        building_id=building_id, inbox_id=inbox_id, current_user=current_user, payload=payload
    )


@router.post(
    "/inboxes/{inbox_id}/send",
    dependencies=[Depends(require_feature("powerhouse_shared_inbox"))],
)
async def send_outbound_email_placeholder(
    inbox_id: str,
    draft_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: send_outbound_email_placeholder
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.send_outbound_email_placeholder(
        building_id=building_id, inbox_id=inbox_id, current_user=current_user, draft_id=draft_id
    )


@router.get("/inboxes/{inbox_id}/delivery-events")
async def list_inbox_delivery_events(
    inbox_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_inbox_delivery_events
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.list_delivery_events(building_id=building_id, inbox_id=inbox_id, current_user=current_user)


@router.get(
    "/workflows/templates",
    dependencies=[Depends(require_feature("powerhouse_workflow_engine"))],
)
async def list_workflow_templates(
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_workflow_templates
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.list_workflow_templates(building_id=building_id, current_user=current_user)


@router.post(
    "/workflows/instances",
    dependencies=[Depends(require_feature("powerhouse_workflow_engine"))],
)
async def create_workflow_instance(
    payload: WorkflowInstanceCreate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: create_workflow_instance
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.create_workflow_instance(
        building_id=building_id, current_user=current_user, payload=payload, idempotency_key=idempotency_key
    )


@router.get(
    "/workflows/instances/{instance_id}",
    dependencies=[Depends(require_feature("powerhouse_workflow_engine"))],
)
async def get_workflow_instance(
    instance_id: str,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_workflow_instance
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.get_workflow_instance(building_id=building_id, instance_id=instance_id, current_user=current_user)


@router.post(
    "/workflows/instances/{instance_id}/events",
    dependencies=[Depends(require_feature("powerhouse_workflow_engine"))],
)
async def add_workflow_event(
    instance_id: str,
    payload: WorkflowEventCreate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: add_workflow_event
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.add_workflow_event(
        building_id=building_id,
        instance_id=instance_id,
        current_user=current_user,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/workflows/instances/{instance_id}/complete-step",
    dependencies=[Depends(require_feature("powerhouse_workflow_engine"))],
)
async def complete_workflow_step(
    instance_id: str,
    payload: WorkflowStepCompleteRequest,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: complete_workflow_step
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.complete_workflow_step(
        building_id=building_id,
        instance_id=instance_id,
        current_user=current_user,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/workflows/instances/{instance_id}/assignments",
    dependencies=[Depends(require_feature("powerhouse_workflow_engine"))],
)
async def assign_workflow_task(
    instance_id: str,
    payload: WorkflowAssignmentCreate,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: assign_workflow_task
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.assign_workflow_task(
        building_id=building_id,
        instance_id=instance_id,
        current_user=current_user,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/workflows/automation-rules/run",
    dependencies=[Depends(require_feature("powerhouse_automation_rules"))],
)
async def run_automation_rule_placeholder(
    payload: AutomationRuleRunRequest,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generated function header.

    Function: run_automation_rule_placeholder
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.run_automation_rule_placeholder(
        building_id=building_id, current_user=current_user, payload=payload, idempotency_key=idempotency_key
    )


@router.get(
    "/workflows/automation-rule-runs",
    dependencies=[Depends(require_feature("powerhouse_automation_rules"))],
)
async def list_automation_rule_runs(
    include_test_data: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_automation_rule_runs
    Path: backend/routers/powerhouse_conversations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await svc.list_automation_rule_runs(
        building_id=building_id, current_user=current_user, include_test_data=include_test_data
    )
