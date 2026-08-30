# @featuretrace:powerhouse-foundation — PostgreSQL-primary service layer for conversation, inbox, and workflow backbone.
# Layer: service
# Data flow: Powerhouse router endpoints → PostgreSQL repositories (communications/workflow) → controlled Mongo fallback when explicitly required (building-scoped).
# Related: backend/routers/powerhouse_conversations.py
#           backend/models/powerhouse_conversation.py
#           docs/architecture/conversation-engine.md

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any, Protocol
from uuid import uuid4

from fastapi import HTTPException

from database import db
from models.powerhouse_conversation import (
    AutomationRuleRunRequest,
    ConversationMessageCreate,
    ConversationThreadCreate,
    ConversationThreadStatusUpdate,
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
from models.user import UserRole
from services.powerhouse_command_foundation import (
    RepositoryReadResult,
    RepositoryReadStatus,
    continuity_reason_for_read_result,
    execute_pg_read,
)

logger = logging.getLogger(__name__)


MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
    UserRole.ADMIN_STAFF,
}

PARTICIPANT_ROLES = MANAGER_ROLES | {
    UserRole.OWNER,
    UserRole.TENANT,
    UserRole.GUEST,
    UserRole.REAL_ESTATE_AGENT,
    UserRole.SERVICE_PROVIDER,
}


def _utc_now() -> datetime:
    """Return current UTC time."""
    return datetime.now(UTC)


def _role_of(user: dict[str, Any]) -> str:
    """Return effective or raw user role."""
    return str(user.get("effective_role") or user.get("role") or UserRole.GUEST)


def _user_id_of(user: dict[str, Any]) -> str:
    """Extract standard user identifier."""
    return str(user.get("id") or user.get("_id") or "")


def ensure_participant_access(user: dict[str, Any]) -> None:
    """Verify user has participant level access."""
    role = _role_of(user)
    if role not in PARTICIPANT_ROLES:
        raise HTTPException(status_code=403, detail="Powerhouse access denied for this role")


def ensure_manager_access(user: dict[str, Any]) -> None:
    """Verify user has manager level access."""
    role = _role_of(user)
    if role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Manager access required")


def _normalize_ids(values: list[str], add_value: str | None = None) -> list[str]:
    """Sort and clean list of string IDs."""
    items = [str(v).strip() for v in values if str(v).strip()]
    if add_value and add_value.strip():
        items.append(add_value.strip())
    return sorted(set(items))


async def _record_audit_event(
    *,
    building_id: str,
    actor_user: dict[str, Any],
    event_type: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record an audit event for Powerhouse state mutations."""
    await db.powerhouse_audit_events.insert_one(
        {
            "id": str(uuid4()),
            "building_id": building_id,
            "event_type": event_type,
            "target_type": target_type,
            "target_id": target_id,
            "actor_user_id": _user_id_of(actor_user),
            "details": details or {},
            "created_at": _utc_now(),
        }
    )


@dataclass
class SendEmailResult:
    provider_message_id: str
    accepted: bool
    note: str


class EmailProvider(Protocol):
    async def send_email(self, *, inbox_address: str, recipients: list[str], subject: str, body: str) -> SendEmailResult:
        ...


class MockEmailProvider:
    async def send_email(self, *, inbox_address: str, recipients: list[str], subject: str, body: str) -> SendEmailResult:
        return SendEmailResult(
            provider_message_id=f"mock-{uuid4()}",
            accepted=True,
            note=f"placeholder send via {inbox_address} to {len(recipients)} recipient(s)",
        )


PROVIDER_REGISTRY: dict[str, EmailProvider] = {
    "mock": MockEmailProvider(),
}

PUBLIC_MONGO_PROJECTION = {"_id": False}


async def _resolve_pg_context(building_id: str) -> tuple[str, str] | None:
    """Resolve tenant/scheme context for PG reads; return None when unavailable."""
    try:
        from db_postgres.repos.config_repo import resolve_scheme_context
    except Exception as exc:  # pragma: no cover
        logger.info("powerhouse: postgres context import unavailable (%s)", exc)
        return None

    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        return None
    return str(scheme["tenant_id"]), str(scheme["scheme_id"])


async def _resolve_pg_actor_user_id(current_user: dict[str, Any]) -> str:
    """Resolve current_user's real core.users UUID for PG command attribution.

    Mongo user ids and Postgres core.users ids are NOT the same UUID for a
    given real person until identity_core is fully unified for this building
    (verified live 2026-08-10: East Gate's manager@eastgate.com has Mongo id
    61a859b9-... but core.users id 288196ce-...). core.audit_events.actor_user_id
    has a FK to core.users, so passing the raw Mongo id straight through
    fails with ForeignKeyViolationError. Resolve by email (the one field both
    stores share) rather than assuming id equality.
    """
    from db_postgres.repos.config_repo import resolve_actor_user_id

    email = current_user.get("email")
    mongo_user_id = _user_id_of(current_user)
    resolved = await resolve_actor_user_id(mongo_user_id, email, require_existing=False)
    if not resolved:
        raise HTTPException(
            status_code=503,
            detail="No PostgreSQL identity resolvable for this user; cannot post to write_source=postgres",
        )
    return resolved


async def _resolve_pg_mapped_entity_id(*, building_id: str, entity_type: str, candidate_id: str) -> str:
    """Resolve legacy IDs to PG IDs when coexistence mappings are available."""
    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        return candidate_id
    tenant_id, _scheme_id = ctx
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            mapping = await session.execute(
                text(
                    """
                    SELECT m.pg_id::text AS pg_id
                    FROM powerhouse.legacy_id_mappings m
                    WHERE m.building_id = :building_id
                      AND m.entity_type = :entity_type
                      AND m.legacy_id = :candidate_id
                      AND m.is_active = TRUE
                    LIMIT 1
                    """
                ),
                {
                    "building_id": building_id,
                    "entity_type": entity_type,
                    "candidate_id": candidate_id,
                },
            )
            row = mapping.fetchone()
            if row and row.pg_id:
                return str(row.pg_id)
            return candidate_id
    except Exception as exc:
        logger.info("powerhouse/id_mapping: lookup unavailable (%s), using raw id", exc)
        return candidate_id


# ---------------------------------------------------------------------------
# P1/P2 PostgreSQL-primary read execution and fallback comparison helpers
# ---------------------------------------------------------------------------

async def _execute_shadow_read(
    *,
    building_id: str,
    domain: str,
    route: str,
    mongo_read,
    pg_read,
    comparator_func,
) -> Any:
    """Dispatch Powerhouse reads with PostgreSQL as the preferred source.

    MongoDB fallback is allowed for continuity, but only when the configured
    PostgreSQL path is unavailable (`None`). An empty PostgreSQL result is a
    valid authoritative result and must not trigger fallback.
    """
    from services.cutover_status_service import get_or_default_cutover_status, record_shadow_diff
    from models.cutover_status import CutoverMode, DataSource

    status = await get_or_default_cutover_status(building_id, domain)
    mode = status.mode
    read_source = status.read_source

    if mode == CutoverMode.disabled:
        raise HTTPException(status_code=503, detail=f"Domain '{domain}' is disabled for building {building_id}")

    if read_source == DataSource.postgres:
        pg_result: RepositoryReadResult = await pg_read()
        if pg_result.status == RepositoryReadStatus.SUCCESS:
            return pg_result.value
        reason = continuity_reason_for_read_result(pg_result)
        if reason:
            await _record_mongo_fallback_event(
                building_id=building_id,
                domain=domain,
                route=route,
                reason=reason.value,
            )
            return await mongo_read()
        if pg_result.status == RepositoryReadStatus.NOT_FOUND:
            raise HTTPException(status_code=404, detail=pg_result.detail or "Powerhouse record not found")
        if pg_result.status == RepositoryReadStatus.PERMISSION_FAILURE:
            raise HTTPException(status_code=403, detail=pg_result.detail or "Powerhouse PostgreSQL permission failure")
        raise HTTPException(
            status_code=500,
            detail=pg_result.detail or f"Powerhouse PostgreSQL read failed: {pg_result.status.value}",
        )

    if mode != CutoverMode.postgres_shadow:
        # Legacy/migration modes may still be Mongo-primary. Do not inspect
        # PostgreSQL emptiness to decide source; `core.domain_cutover_status`
        # remains the routing authority until the domain is promoted.
        return await mongo_read()

    # Default is Mongo reads
    mongo_result = await mongo_read()

    if mode == CutoverMode.postgres_shadow:
        try:
            pg_result: RepositoryReadResult = await pg_read()
            if pg_result.status == RepositoryReadStatus.SUCCESS:
                is_match, diff_type, score, details = comparator_func(mongo_result, pg_result.value)
                if not is_match:
                    await record_shadow_diff(
                        building_id=building_id,
                        domain=domain,
                        route=route,
                        diff_type=diff_type,
                        mongo_value=mongo_result if isinstance(mongo_result, (dict, list)) else {"result": mongo_result},
                        pg_value=pg_result.value if isinstance(pg_result.value, (dict, list)) else {"result": pg_result.value},
                        divergence_score=score,
                        notes=details,
                    )
        except Exception as exc:
            logger.warning("Shadow read PG query or comparison failed for %s/%s: %s", domain, route, exc, exc_info=True)

    return mongo_result


async def _assert_write_target(building_id: str, domain: str) -> None:
    """Fail closed when a legacy Mongo write path is called in PG-write mode.

    Stage 1 write handlers below still persist through the Mongo fallback
    collections. Once `write_source=postgres`, these handlers must be replaced
    by PostgreSQL command services with transactional outbox writes; silently
    writing Mongo at that point would create split-brain canonical state.
    """
    from services.cutover_status_service import get_or_default_cutover_status
    from models.cutover_status import CutoverMode, DataSource

    status = await get_or_default_cutover_status(building_id, domain)
    if status.mode == CutoverMode.disabled:
        raise HTTPException(status_code=503, detail=f"Domain {domain} is disabled for building {building_id}")

    if status.write_source == DataSource.postgres:
        raise HTTPException(
            status_code=501,
            detail=(
                f"PostgreSQL writes for domain '{domain}' require the PG command service; "
                "legacy Mongo fallback writes are blocked in postgres write mode."
            ),
        )


async def _resolve_write_source(building_id: str, domain: str):
    """Return the domain's write_source without raising on postgres.

    Unlike _assert_write_target (which 501s any postgres write_source domain
    with no PG command implemented yet), this lets a caller that DOES have a
    real PG command branch between the Mongo and PostgreSQL write paths.
    """
    from services.cutover_status_service import get_or_default_cutover_status
    from models.cutover_status import CutoverMode

    status = await get_or_default_cutover_status(building_id, domain)
    if status.mode == CutoverMode.disabled:
        raise HTTPException(status_code=503, detail=f"Domain {domain} is disabled for building {building_id}")
    return status.write_source


async def _record_mongo_fallback_event(
    *,
    building_id: str,
    domain: str,
    route: str,
    reason: str,
) -> None:
    """Record an operator-visible event whenever runtime Mongo fallback activates."""
    try:
        await db.powerhouse_audit_events.insert_one(
            {
                "id": str(uuid4()),
                "building_id": building_id,
                "event_type": "powerhouse.mongo_fallback.activated",
                "target_type": "powerhouse_domain",
                "target_id": domain,
                "actor_user_id": None,
                "details": {
                    "domain": domain,
                    "route": route,
                    "reason": reason,
                    "source_order": ["postgres", "mongo_fallback"],
                },
                "created_at": _utc_now(),
            }
        )
    except Exception as exc:  # pragma: no cover - fallback telemetry must never mask the user request
        logger.warning(
            "powerhouse/fallback: failed to record Mongo fallback event for %s %s: %s",
            domain,
            route,
            exc,
        )


# ---------------------------------------------------------------------------
# Normalization and Comparison logic
# ---------------------------------------------------------------------------

def _normalize_datetime(dt_val: Any) -> str | None:
    if not dt_val:
        return None
    if isinstance(dt_val, str):
        return dt_val.split(".")[0].replace("Z", "+00:00")
    if isinstance(dt_val, datetime):
        return dt_val.isoformat().split(".")[0]
    return str(dt_val)


def _normalize_thread(thread: dict[str, Any]) -> dict[str, Any]:
    tid = str(thread.get("id") or thread.get("_id") or "")
    participants = sorted([str(p) for p in thread.get("participant_ids") or []])
    watchers = sorted([str(w) for w in thread.get("watcher_ids") or []])

    linked = thread.get("linked_entity")
    linked_entity = None
    if linked:
        linked_entity = {
            "entity_type": str(linked.get("entity_type") or ""),
            "entity_id": str(linked.get("entity_id") or ""),
        }

    return {
        "id": tid,
        "building_id": str(thread.get("building_id") or ""),
        "subject": str(thread.get("subject") or ""),
        "source_channel": str(thread.get("source_channel") or "portal_message"),
        "priority": str(thread.get("priority") or "normal"),
        "status": str(thread.get("status") or "open"),
        "visibility": str(thread.get("visibility") or "participants_only"),
        "participant_ids": participants,
        "watcher_ids": watchers,
        "linked_entity": linked_entity,
        "source_external_id": thread.get("source_external_id"),
        "assigned_to": thread.get("assigned_to"),
        "sla_due_at": _normalize_datetime(thread.get("sla_due_at")),
        "created_by": str(thread.get("created_by") or ""),
        "is_archived": bool(thread.get("is_archived")),
    }


def compare_threads_list(mongo_res: Any, pg_res: Any) -> tuple[bool, str, float, str]:
    mongo_items = mongo_res.get("items") if isinstance(mongo_res, dict) else mongo_res
    pg_items = pg_res.get("items") if isinstance(pg_res, dict) else pg_res

    if not isinstance(mongo_items, list) or not isinstance(pg_items, list):
        return False, "type_mismatch", 1.0, "Result items are not lists"

    if len(mongo_items) != len(pg_items):
        return False, "count_mismatch", 1.0, f"Thread count mismatch: Mongo {len(mongo_items)} vs PG {len(pg_items)}"

    mongo_sorted = sorted([_normalize_thread(t) for t in mongo_items], key=lambda x: x["id"])
    pg_sorted = sorted([_normalize_thread(t) for t in pg_items], key=lambda x: x["id"])

    mismatches = []
    for m, p in zip(mongo_sorted, pg_sorted):
        for k, mv in m.items():
            pv = p.get(k)
            if mv != pv:
                mismatches.append(f"Thread {m['id']} field {k} mismatch: Mongo {mv} vs PG {pv}")

    if mismatches:
        return False, "field_mismatch", 0.5, "; ".join(mismatches[:5])

    return True, "", 0.0, ""


def compare_thread_detail(mongo_res: Any, pg_res: Any) -> tuple[bool, str, float, str]:
    if not isinstance(mongo_res, dict) or not isinstance(pg_res, dict):
        return False, "type_mismatch", 1.0, "Result detail is not a dict"

    m_thread = mongo_res.get("thread") or {}
    p_thread = pg_res.get("thread") or {}

    m_norm = _normalize_thread(m_thread)
    p_norm = _normalize_thread(p_thread)

    for k, mv in m_norm.items():
        pv = p_norm.get(k)
        if mv != pv:
            return False, "field_mismatch", 0.5, f"Thread detail field {k} mismatch: Mongo {mv} vs PG {pv}"

    m_messages = mongo_res.get("messages") or []
    p_messages = pg_res.get("messages") or []

    if len(m_messages) != len(p_messages):
        return False, "count_mismatch", 0.5, f"Message count mismatch: Mongo {len(m_messages)} vs PG {len(p_messages)}"

    return True, "", 0.0, ""


def compare_inboxes(mongo_res: Any, pg_res: Any) -> tuple[bool, str, float, str]:
    if not isinstance(mongo_res, list) or not isinstance(pg_res, list):
        return False, "type_mismatch", 1.0, "Result inboxes is not a list"

    if len(mongo_res) != len(pg_res):
        return False, "count_mismatch", 1.0, f"Inbox count mismatch: Mongo {len(mongo_res)} vs PG {len(pg_res)}"

    m_sorted = sorted(mongo_res, key=lambda x: str(x.get("address") or ""))
    p_sorted = sorted(pg_res, key=lambda x: str(x.get("address") or ""))

    for m, p in zip(m_sorted, p_sorted):
        m_addr = str(m.get("address") or "")
        p_addr = str(p.get("address") or "")
        if m_addr != p_addr:
            return False, "field_mismatch", 0.5, f"Inbox address mismatch: Mongo {m_addr} vs PG {p_addr}"

    return True, "", 0.0, ""


def compare_workflow_templates(mongo_res: Any, pg_res: Any) -> tuple[bool, str, float, str]:
    if not isinstance(mongo_res, list) or not isinstance(pg_res, list):
        return False, "type_mismatch", 1.0, "Result templates is not a list"

    if len(mongo_res) != len(pg_res):
        return False, "count_mismatch", 1.0, f"Template count mismatch: Mongo {len(mongo_res)} vs PG {len(pg_res)}"

    return True, "", 0.0, ""


def compare_workflow_instances(mongo_res: Any, pg_res: Any) -> tuple[bool, str, float, str]:
    if not isinstance(mongo_res, dict) or not isinstance(pg_res, dict):
        return False, "type_mismatch", 1.0, "Result instances is not a dict"

    m_inst = mongo_res.get("instance") or {}
    p_inst = pg_res.get("instance") or {}

    m_title = m_inst.get("title")
    p_title = p_inst.get("title")
    if m_title != p_title:
        return False, "field_mismatch", 0.5, f"Instance title mismatch: Mongo {m_title} vs PG {p_title}"

    return True, "", 0.0, ""


def compare_automation_rule_runs(mongo_res: Any, pg_res: Any) -> tuple[bool, str, float, str]:
    if not isinstance(mongo_res, list) or not isinstance(pg_res, list):
        return False, "type_mismatch", 1.0, "Result runs is not a list"

    if len(mongo_res) != len(pg_res):
        return False, "count_mismatch", 1.0, f"Automation runs count mismatch: Mongo {len(mongo_res)} vs PG {len(pg_res)}"

    return True, "", 0.0, ""


# ---------------------------------------------------------------------------
# PostgreSQL query/repository methods
# ---------------------------------------------------------------------------

async def _list_threads_from_pg(
    *,
    building_id: str,
    current_user: dict[str, Any],
    status: str | None,
    source_channel: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]] | None:
    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        return None
    tenant_id, _scheme_id = ctx
    user_id = _user_id_of(current_user)
    role = _role_of(current_user)
    can_manage = role in MANAGER_ROLES
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            rows = await session.execute(
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
                    WHERE t.building_id = :building_id
                      AND COALESCE(t.is_archived, FALSE) = FALSE
                      AND (CAST(:status AS varchar) IS NULL OR t.status = CAST(:status AS varchar))
                      AND (CAST(:source_channel AS varchar) IS NULL OR t.source_channel = CAST(:source_channel AS varchar))
                      AND (
                          :can_manage
                          OR t.visibility = 'building_public'
                          OR t.created_by_user_id::text = :user_id
                          OR EXISTS (
                              SELECT 1
                              FROM communications.conversation_participants cp2
                              WHERE cp2.thread_id = t.id
                                AND cp2.tenant_id = t.tenant_id
                                AND cp2.user_id::text = :user_id
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM communications.conversation_watchers cw2
                              WHERE cw2.thread_id = t.id
                                AND cw2.tenant_id = t.tenant_id
                                AND cw2.user_id::text = :user_id
                          )
                      )
                    ORDER BY t.updated_at DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {
                    "building_id": building_id,
                    "status": status,
                    "source_channel": source_channel,
                    "can_manage": can_manage,
                    "user_id": user_id,
                    "limit": int(limit),
                    "offset": int(offset),
                },
            )
            mapped: list[dict[str, Any]] = []
            for row in rows.fetchall():
                linked_entity = None
                if row.linked_entity_type or row.linked_entity_id:
                    linked_entity = {"entity_type": row.linked_entity_type, "entity_id": row.linked_entity_id}
                mapped.append(
                    {
                        "id": row.id,
                        "building_id": row.building_id,
                        "subject": row.subject,
                        "source_channel": row.source_channel,
                        "priority": row.priority,
                        "status": row.status,
                        "visibility": row.visibility,
                        "participant_ids": list(row.participant_ids or []),
                        "watcher_ids": list(row.watcher_ids or []),
                        "linked_entity": linked_entity,
                        "source_external_id": row.source_external_id,
                        "assigned_to": row.assigned_to,
                        "sla_due_at": row.sla_due_at,
                        "created_by": row.created_by,
                        "created_at": row.created_at,
                        "updated_at": row.updated_at,
                        "is_archived": bool(row.is_archived),
                    }
                )
            return mapped
    except Exception as exc:
        logger.info("powerhouse/list_threads: postgres read failed (%s); classifying for controlled fallback", exc)
        raise


async def _get_thread_detail_from_pg(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
) -> dict[str, Any] | None:
    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        return None
    tenant_id, _scheme_id = ctx
    user_id = _user_id_of(current_user)
    role = _role_of(current_user)
    can_manage = role in MANAGER_ROLES
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        lookup_thread_id = await _resolve_pg_mapped_entity_id(
            building_id=building_id,
            entity_type="conversation_thread",
            candidate_id=thread_id,
        )

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            thread_row = await session.execute(
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
                    WHERE t.building_id = :building_id
                      AND (
                           t.id::text = :lookup_thread_id
                           OR t.source_external_id = :thread_id
                      )
                      AND COALESCE(t.is_archived, FALSE) = FALSE
                      AND (
                          :can_manage
                          OR t.visibility = 'building_public'
                          OR t.created_by_user_id::text = :user_id
                          OR EXISTS (
                              SELECT 1
                              FROM communications.conversation_participants cp2
                              WHERE cp2.thread_id = t.id
                                AND cp2.tenant_id = t.tenant_id
                                AND cp2.user_id::text = :user_id
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM communications.conversation_watchers cw2
                              WHERE cw2.thread_id = t.id
                                AND cw2.tenant_id = t.tenant_id
                                AND cw2.user_id::text = :user_id
                          )
                      )
                    LIMIT 1
                    """
                ),
                {
                    "building_id": building_id,
                    "thread_id": thread_id,
                    "lookup_thread_id": lookup_thread_id,
                    "can_manage": can_manage,
                    "user_id": user_id,
                },
            )
            thread = thread_row.fetchone()
            if not thread:
                if not can_manage:
                    exists_row = await session.execute(
                        text(
                            """
                            SELECT 1
                            FROM communications.conversation_threads t
                            WHERE t.building_id = :building_id
                              AND (
                                   t.id::text = :lookup_thread_id
                                   OR t.source_external_id = :thread_id
                              )
                              AND COALESCE(t.is_archived, FALSE) = FALSE
                            LIMIT 1
                            """
                        ),
                        {
                            "building_id": building_id,
                            "thread_id": thread_id,
                            "lookup_thread_id": lookup_thread_id,
                        },
                    )
                    if exists_row.fetchone():
                        raise HTTPException(status_code=403, detail="Thread visibility denied")
                return None

            resolved_thread_id = str(thread.id)
            message_rows = await session.execute(
                text(
                    """
                    SELECT
                        m.id::text AS id,
                        m.thread_id::text AS thread_id,
                        m.message_type,
                        m.visibility,
                        m.body,
                        m.created_by_user_id::text AS created_by,
                        m.created_at
                    FROM communications.conversation_messages m
                    WHERE m.thread_id::text = :resolved_thread_id
                      AND COALESCE(m.is_deleted, FALSE) = FALSE
                      AND (:can_manage OR m.visibility <> 'internal_only')
                    ORDER BY m.created_at ASC
                    LIMIT 200
                    """
                ),
                {"resolved_thread_id": resolved_thread_id, "can_manage": can_manage},
            )
            messages = [
                {
                    "id": row.id,
                    "thread_id": row.thread_id,
                    "building_id": building_id,
                    "message_type": row.message_type,
                    "visibility": row.visibility,
                    "body": row.body,
                    "attachments": [],
                    "created_by": row.created_by,
                    "created_at": row.created_at,
                    "is_deleted": False,
                }
                for row in message_rows.fetchall()
            ]

            linked_entity = None
            if thread.linked_entity_type or thread.linked_entity_id:
                linked_entity = {"entity_type": thread.linked_entity_type, "entity_id": thread.linked_entity_id}
            return {
                "thread": {
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
                    "source_external_id": thread.source_external_id,
                    "assigned_to": thread.assigned_to,
                    "sla_due_at": thread.sla_due_at,
                    "created_by": thread.created_by,
                    "created_at": thread.created_at,
                    "updated_at": thread.updated_at,
                    "is_archived": bool(thread.is_archived),
                },
                "messages": messages,
            }
    except HTTPException:
        raise
    except Exception as exc:
        logger.info("powerhouse/get_thread_detail: postgres read failed (%s); classifying for controlled fallback", exc)
        raise


async def _list_inboxes_from_pg(*, building_id: str, current_user: dict[str, Any]) -> list[dict[str, Any]] | None:
    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        return None
    tenant_id, _scheme_id = ctx
    role = _role_of(current_user)
    can_manage = role in MANAGER_ROLES
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            rows = await session.execute(
                text(
                    """
                    SELECT
                        i.id::text AS id,
                        i.building_id,
                        i.inbox_name,
                        i.address,
                        i.provider_key,
                        i.enabled,
                        i.allowed_roles,
                        i.created_at,
                        i.updated_at
                    FROM communications.inboxes i
                    WHERE i.building_id = :building_id
                      AND i.enabled = TRUE
                      AND (
                          :can_manage
                          OR EXISTS (
                              SELECT 1
                              FROM jsonb_array_elements_text(i.allowed_roles) AS ar(role_name)
                              WHERE ar.role_name = :role
                          )
                      )
                    ORDER BY i.inbox_name ASC
                    """
                ),
                {
                    "building_id": building_id,
                    "can_manage": can_manage,
                    "role": role,
                },
            )
            return [
                {
                    "id": row.id,
                    "building_id": row.building_id,
                    "inbox_name": row.inbox_name,
                    "address": row.address,
                    "provider_key": row.provider_key,
                    "enabled": bool(row.enabled),
                    "allowed_roles": list(row.allowed_roles or []),
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows.fetchall()
            ]
    except Exception as exc:
        logger.info("powerhouse/list_inboxes: postgres read failed (%s); classifying for controlled fallback", exc)
        raise


async def _list_workflow_templates_from_pg(*, building_id: str) -> list[dict[str, Any]] | None:
    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        return None
    tenant_id, _scheme_id = ctx
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            rows = await session.execute(
                text(
                    """
                    SELECT
                        wt.id::text AS id,
                        wt.template_key,
                        wt.name,
                        wt.description,
                        wt.version
                    FROM workflow.workflow_templates wt
                    WHERE wt.building_id = :building_id
                      AND wt.is_active = TRUE
                    ORDER BY wt.name ASC
                    """
                ),
                {"building_id": building_id},
            )
            return [
                {
                    "id": row.id,
                    "key": row.template_key,
                    "name": row.name,
                    "description": row.description,
                    "version": row.version,
                    "is_placeholder": False,
                }
                for row in rows.fetchall()
            ]
    except Exception as exc:
        logger.info("powerhouse/list_workflow_templates: postgres read failed (%s); classifying for controlled fallback", exc)
        raise


async def _get_workflow_instance_from_pg(
    *,
    building_id: str,
    instance_id: str,
    current_user: dict[str, Any],
) -> dict[str, Any] | None:
    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        return None
    tenant_id, _scheme_id = ctx
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        lookup_instance_id = await _resolve_pg_mapped_entity_id(
            building_id=building_id,
            entity_type="workflow_instance",
            candidate_id=instance_id,
        )

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            instance_row = await session.execute(
                text(
                    """
                    SELECT
                        i.id::text AS id,
                        i.building_id,
                        i.template_id::text AS template_id,
                        i.title,
                        i.status,
                        i.linked_entity_type,
                        i.linked_entity_id,
                        i.payload,
                        i.created_by_user_id::text AS created_by,
                        i.created_at,
                        i.updated_at
                    FROM workflow.workflow_instances i
                    WHERE i.building_id = :building_id
                      AND i.id::text = :lookup_instance_id
                    LIMIT 1
                    """
                ),
                {"building_id": building_id, "lookup_instance_id": lookup_instance_id},
            )
            instance = instance_row.fetchone()
            if not instance:
                return None

            event_rows = await session.execute(
                text(
                    """
                    SELECT
                        e.id::text AS id,
                        e.instance_id::text AS instance_id,
                        e.event_type,
                        e.payload,
                        e.created_by_user_id::text AS created_by,
                        e.created_at
                    FROM workflow.workflow_events e
                    WHERE e.instance_id::text = :resolved_instance_id
                    ORDER BY e.created_at ASC
                    """
                ),
                {"resolved_instance_id": str(instance.id)},
            )
            events = [
                {
                    "id": row.id,
                    "instance_id": row.instance_id,
                    "building_id": building_id,
                    "event_type": row.event_type,
                    "payload": row.payload or {},
                    "created_by": row.created_by,
                    "created_at": row.created_at,
                }
                for row in event_rows.fetchall()
            ]

            linked_entity = None
            if instance.linked_entity_type or instance.linked_entity_id:
                linked_entity = {"entity_type": instance.linked_entity_type, "entity_id": instance.linked_entity_id}

            return {
                "instance": {
                    "id": instance.id,
                    "building_id": instance.building_id,
                    "template_id": instance.template_id,
                    "title": instance.title,
                    "status": instance.status,
                    "linked_entity": linked_entity,
                    "payload": instance.payload or {},
                    "created_by": instance.created_by,
                    "created_at": instance.created_at,
                    "updated_at": instance.updated_at,
                },
                "events": events,
            }
    except Exception as exc:
        logger.info("powerhouse/get_workflow_instance_from_pg: postgres read failed (%s); classifying for controlled fallback", exc)
        raise


async def _list_automation_rule_runs_from_pg(
    *,
    building_id: str,
    current_user: dict[str, Any],
) -> list[dict[str, Any]] | None:
    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        return None
    tenant_id, _scheme_id = ctx
    try:
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant

        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            rows = await session.execute(
                text(
                    """
                    SELECT
                        r.id::text AS id,
                        ar.building_id,
                        r.rule_id::text AS rule_id,
                        r.rule_key,
                        r.dry_run,
                        r.status,
                        r.result_json,
                        r.created_by_user_id::text AS created_by,
                        r.created_at
                    FROM workflow.automation_rule_runs r
                    JOIN workflow.automation_rules ar
                      ON ar.id = r.rule_id AND ar.tenant_id = r.tenant_id
                    WHERE r.tenant_id = :tenant_id
                      AND ar.building_id = :building_id
                    ORDER BY r.created_at DESC
                    LIMIT 200
                    """
                ),
                {"tenant_id": tenant_id, "building_id": building_id},
            )
            return [
                {
                    "id": row.id,
                    "building_id": row.building_id,
                    "rule_id": row.rule_id,
                    "rule_key": row.rule_key,
                    "dry_run": bool(row.dry_run),
                    "status": row.status,
                    "result_json": row.result_json or {},
                    "created_by": row.created_by,
                    "created_at": row.created_at,
                }
                for row in rows.fetchall()
            ]
    except Exception as exc:
        logger.info("powerhouse/list_automation_rule_runs_from_pg: postgres read failed (%s); classifying for controlled fallback", exc)
        raise


# ---------------------------------------------------------------------------
# Primary Powerhouse Service APIs
# ---------------------------------------------------------------------------

async def list_threads(
    *,
    building_id: str,
    current_user: dict[str, Any],
    status: str | None,
    source_channel: str | None,
    limit: int,
    offset: int,
    include_test_data: bool = False,
) -> dict[str, Any]:
    ensure_participant_access(current_user)

    async def _mongo_coro():
        user_id = _user_id_of(current_user)
        role = _role_of(current_user)
        query: dict[str, Any] = {"building_id": building_id, "is_archived": {"$ne": True}}
        # Real users must never see perf/test-created threads by default —
        # matching the {"is_test_data": {"$ne": True}} convention used
        # elsewhere in this repo (e.g. routers/maintenance.py). include_test_data
        # is manager-gated, matching that same file's is_admin gate, so a k6
        # teardown script (or a manager checking for leftover test debris) can
        # opt back in explicitly.
        if not (include_test_data and role in MANAGER_ROLES):
            query["is_test_data"] = {"$ne": True}
        if status:
            query["status"] = status
        if source_channel:
            query["source_channel"] = source_channel
        if role not in MANAGER_ROLES:
            query["$or"] = [
                {"participant_ids": user_id},
                {"watcher_ids": user_id},
                {"created_by": user_id},
                {"visibility": "building_public"},
            ]
        cursor = (
            db.powerhouse_conversation_threads.find(query, PUBLIC_MONGO_PROJECTION)
            .sort("updated_at", -1)
            .skip(offset)
            .limit(limit)
        )
        items = await cursor.to_list(length=limit)
        return {"items": items, "limit": limit, "offset": offset}

    async def _pg_coro():
        return await execute_pg_read(
            lambda: _list_threads_from_pg(
                building_id=building_id,
                current_user=current_user,
                status=status,
                source_channel=source_channel,
                limit=limit,
                offset=offset,
            ),
            wrap_value=lambda items: {"items": items, "limit": limit, "offset": offset},
            none_detail="PostgreSQL thread list unavailable",
        )

    return await _execute_shadow_read(
        building_id=building_id,
        domain="powerhouse_conversations",
        route=f"GET /conversations/threads?status={status}&channel={source_channel}",
        mongo_read=_mongo_coro,
        pg_read=_pg_coro,
        comparator_func=compare_threads_list,
    )


async def create_thread(
    *,
    building_id: str,
    current_user: dict[str, Any],
    payload: ConversationThreadCreate,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_participant_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_conversations")
    user_id = _user_id_of(current_user)

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_communications_command_service import create_conversation_command

        result = await create_conversation_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            actor_user_id=pg_actor_user_id,
            payload=payload,
            idempotency_key=idempotency_key or str(uuid4()),
            is_test_data=payload.is_test_data,
        )
        return result.result_reference

    now = _utc_now()
    thread_id = str(uuid4())

    thread_doc = {
        "id": thread_id,
        "building_id": building_id,
        "subject": payload.subject,
        "source_channel": payload.source_channel,
        "priority": payload.priority,
        "status": "open",
        "visibility": payload.visibility,
        "participant_ids": _normalize_ids(payload.participant_ids, user_id),
        "watcher_ids": _normalize_ids(payload.watcher_ids),
        "linked_entity": payload.linked_entity.model_dump() if payload.linked_entity else None,
        "unit_id": payload.unit_id,
        "lot_id": payload.lot_id,
        "source_external_id": payload.source_external_id,
        "assigned_to": payload.assigned_to,
        "sla_due_at": payload.sla_due_at,
        "created_by": user_id,
        "updated_by": user_id,
        "created_at": now,
        "updated_at": now,
        "is_archived": False,
        "is_test_data": payload.is_test_data,
    }
    # insert_one() mutates its argument in place, injecting a raw Mongo
    # ObjectId under "_id" — pass a copy so the dict we return to the router
    # (and hand to jsonable_encoder) stays free of a non-JSON-serialisable
    # value. Pre-existing bug across this file's write handlers; only fixed
    # here per the specific request that surfaced it (create_thread), not
    # applied file-wide in this change.
    await db.powerhouse_conversation_threads.insert_one(dict(thread_doc))
    await add_message(
        building_id=building_id,
        thread_id=thread_id,
        current_user=current_user,
        payload=ConversationMessageCreate(
            body=payload.body,
            message_type="message",
            attachments=payload.attachments,
            is_test_data=payload.is_test_data,
        ),
    )
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="conversation.thread.created",
        target_type="conversation_thread",
        target_id=thread_id,
        details={"source_channel": payload.source_channel, "priority": payload.priority, "visibility": payload.visibility},
    )
    return thread_doc


async def get_thread_detail(*, building_id: str, thread_id: str, current_user: dict[str, Any]) -> dict[str, Any]:
    ensure_participant_access(current_user)

    async def _mongo_coro():
        thread = await db.powerhouse_conversation_threads.find_one(
            {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}},
            PUBLIC_MONGO_PROJECTION,
        )
        if not thread:
            raise HTTPException(status_code=404, detail="Conversation thread not found")

        role = _role_of(current_user)
        user_id = _user_id_of(current_user)
        if role not in MANAGER_ROLES:
            visible = (
                thread.get("visibility") == "building_public"
                or user_id in set(thread.get("participant_ids", []))
                or user_id in set(thread.get("watcher_ids", []))
                or thread.get("created_by") == user_id
            )
            if not visible:
                raise HTTPException(status_code=403, detail="Thread visibility denied")

        messages = await (
            db.powerhouse_conversation_messages.find(
                {"thread_id": thread_id, "building_id": building_id, "is_deleted": {"$ne": True}},
                PUBLIC_MONGO_PROJECTION,
            )
            .sort("created_at", 1)
            .limit(200)
            .to_list(length=200)
        )

        if role not in MANAGER_ROLES:
            messages = [m for m in messages if m.get("visibility") != "internal_only"]

        return {"thread": thread, "messages": messages}

    async def _pg_coro():
        return await execute_pg_read(
            lambda: _get_thread_detail_from_pg(building_id=building_id, thread_id=thread_id, current_user=current_user),
            none_detail="PostgreSQL thread detail unavailable",
        )

    return await _execute_shadow_read(
        building_id=building_id,
        domain="powerhouse_conversations",
        route=f"GET /conversations/threads/{thread_id}",
        mongo_read=_mongo_coro,
        pg_read=_pg_coro,
        comparator_func=compare_thread_detail,
    )


async def add_message(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    payload: ConversationMessageCreate,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_participant_access(current_user)
    if payload.message_type == "internal_note":
        ensure_manager_access(current_user)

    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_conversations")
    user_id = _user_id_of(current_user)

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_communications_command_service import add_message_command

        result = await add_message_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            thread_id=thread_id,
            actor_user_id=pg_actor_user_id,
            payload=payload,
            idempotency_key=idempotency_key or str(uuid4()),
            is_test_data=payload.is_test_data,
        )
        return result.result_reference

    thread = await db.powerhouse_conversation_threads.find_one({"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}})
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation thread not found")

    now = _utc_now()
    message_doc = {
        "id": str(uuid4()),
        "thread_id": thread_id,
        "building_id": building_id,
        "message_type": payload.message_type,
        "visibility": "internal_only" if payload.message_type == "internal_note" else "participants_only",
        "body": payload.body,
        "attachments": [a.model_dump() for a in payload.attachments],
        "created_by": _user_id_of(current_user),
        "created_at": now,
        "is_deleted": False,
        "is_test_data": payload.is_test_data,
    }
    await db.powerhouse_conversation_messages.insert_one(dict(message_doc))
    await db.powerhouse_conversation_threads.update_one(
        {"id": thread_id, "building_id": building_id},
        {"$set": {"updated_at": now, "updated_by": _user_id_of(current_user), "last_message_at": now}},
    )
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type=(
            "conversation.note.created"
            if payload.message_type == "internal_note"
            else "conversation.message.created"
        ),
        target_type="conversation_message",
        target_id=message_doc["id"],
        details={"thread_id": thread_id, "message_type": payload.message_type},
    )
    return message_doc


async def update_thread_status(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    payload: ConversationThreadStatusUpdate,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_conversations")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_communications_command_service import update_thread_status_command

        result = await update_thread_status_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            thread_id=thread_id,
            actor_user_id=pg_actor_user_id,
            status=payload.status,
            idempotency_key=idempotency_key or str(uuid4()),
            is_test_data=payload.is_test_data,
        )
        return result.result_reference

    now = _utc_now()
    await db.powerhouse_conversation_threads.update_one(
        {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}},
        {"$set": {"status": payload.status, "updated_at": now, "updated_by": _user_id_of(current_user)}},
    )
    result = await db.powerhouse_conversation_threads.find_one(
        {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}}, PUBLIC_MONGO_PROJECTION
    )
    if not result:
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="conversation.thread.status_changed",
        target_type="conversation_thread",
        target_id=thread_id,
        details={"status": payload.status},
    )
    return result


async def assign_thread(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    assignee_user_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_conversations")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_communications_command_service import assign_thread_command

        result = await assign_thread_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            thread_id=thread_id,
            actor_user_id=pg_actor_user_id,
            assignee_user_id=assignee_user_id,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return result.result_reference

    now = _utc_now()
    await db.powerhouse_conversation_threads.update_one(
        {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}},
        {"$set": {"assigned_to": assignee_user_id, "updated_at": now, "updated_by": _user_id_of(current_user)}},
    )
    result = await db.powerhouse_conversation_threads.find_one(
        {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}}, PUBLIC_MONGO_PROJECTION
    )
    if not result:
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="conversation.thread.assigned",
        target_type="conversation_thread",
        target_id=thread_id,
        details={"assigned_to": assignee_user_id},
    )
    return result


async def add_watcher(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    watcher_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_conversations")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_communications_command_service import add_watcher_command

        result = await add_watcher_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            thread_id=thread_id,
            actor_user_id=pg_actor_user_id,
            watcher_id=watcher_id,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return result.result_reference

    now = _utc_now()
    await db.powerhouse_conversation_threads.update_one(
        {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}},
        {"$addToSet": {"watcher_ids": watcher_id}, "$set": {"updated_at": now, "updated_by": _user_id_of(current_user)}},
    )
    result = await db.powerhouse_conversation_threads.find_one(
        {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}}, PUBLIC_MONGO_PROJECTION
    )
    if not result:
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="conversation.watcher.added",
        target_type="conversation_thread",
        target_id=thread_id,
        details={"watcher_id": watcher_id},
    )
    return result


async def remove_watcher(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    watcher_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_conversations")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_communications_command_service import remove_watcher_command

        result = await remove_watcher_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            thread_id=thread_id,
            actor_user_id=pg_actor_user_id,
            watcher_id=watcher_id,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return result.result_reference

    now = _utc_now()
    await db.powerhouse_conversation_threads.update_one(
        {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}},
        {"$pull": {"watcher_ids": watcher_id}, "$set": {"updated_at": now, "updated_by": _user_id_of(current_user)}},
    )
    result = await db.powerhouse_conversation_threads.find_one(
        {"id": thread_id, "building_id": building_id, "is_archived": {"$ne": True}}, PUBLIC_MONGO_PROJECTION
    )
    if not result:
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="conversation.watcher.removed",
        target_type="conversation_thread",
        target_id=thread_id,
        details={"watcher_id": watcher_id},
    )
    return result


async def add_link(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    entity_type: str,
    entity_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_conversations")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_communications_command_service import add_link_command

        result = await add_link_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            thread_id=thread_id,
            actor_user_id=pg_actor_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return result.result_reference

    now = _utc_now()
    link_doc = {
        "id": str(uuid4()),
        "thread_id": thread_id,
        "building_id": building_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "created_by": _user_id_of(current_user),
        "created_at": now,
    }
    await db.powerhouse_conversation_links.insert_one(dict(link_doc))
    await db.powerhouse_conversation_threads.update_one(
        {"id": thread_id, "building_id": building_id},
        {"$set": {"linked_entity": {"entity_type": entity_type, "entity_id": entity_id}, "updated_at": now}},
    )
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="conversation.linked_entity.added",
        target_type="conversation_thread",
        target_id=thread_id,
        details={"entity_type": entity_type, "entity_id": entity_id},
    )
    return link_doc


async def _require_postgres_write_source(building_id: str, domain: str, capability: str):
    """Return DataSource.postgres or raise 501 — for commands with no Mongo implementation.

    Unlike _resolve_write_source (used by create_thread/add_message/status/assign/
    watchers/links, which all have an existing Mongo path to fall back to),
    SLA management and the participants roster were never implemented in Mongo —
    per explicit direction, new Powerhouse capabilities are built PostgreSQL-first
    with no throwaway Mongo implementation. If write_source isn't postgres yet,
    there is nothing to fall back to; fail clearly rather than silently no-op.
    """
    write_source = await _resolve_write_source(building_id, domain)
    from models.cutover_status import DataSource

    if write_source != DataSource.postgres:
        raise HTTPException(
            status_code=501,
            detail=(
                f"{capability} requires domain '{domain}' to be promoted to postgres write_source — "
                "no MongoDB implementation exists for this capability (PostgreSQL-first by design)."
            ),
        )
    return DataSource.postgres


async def set_thread_sla(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    sla_due_at: datetime | None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _require_postgres_write_source(building_id, "powerhouse_conversations", "Setting a thread's SLA")

    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
    tenant_id, scheme_id = ctx
    pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
    from services.powerhouse_communications_command_service import set_thread_sla_command

    result = await set_thread_sla_command(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        thread_id=thread_id,
        actor_user_id=pg_actor_user_id,
        sla_due_at=sla_due_at,
        idempotency_key=idempotency_key or str(uuid4()),
    )
    return result.result_reference


async def add_participant(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    participant_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _require_postgres_write_source(building_id, "powerhouse_conversations", "Adding a participant")

    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
    tenant_id, scheme_id = ctx
    pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
    from services.powerhouse_communications_command_service import add_participant_command

    result = await add_participant_command(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        thread_id=thread_id,
        actor_user_id=pg_actor_user_id,
        participant_id=participant_id,
        idempotency_key=idempotency_key or str(uuid4()),
    )
    return result.result_reference


async def remove_participant(
    *,
    building_id: str,
    thread_id: str,
    current_user: dict[str, Any],
    participant_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _require_postgres_write_source(building_id, "powerhouse_conversations", "Removing a participant")

    ctx = await _resolve_pg_context(building_id)
    if not ctx:
        raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
    tenant_id, scheme_id = ctx
    pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
    from services.powerhouse_communications_command_service import remove_participant_command

    result = await remove_participant_command(
        building_id=building_id,
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        thread_id=thread_id,
        actor_user_id=pg_actor_user_id,
        participant_id=participant_id,
        idempotency_key=idempotency_key or str(uuid4()),
    )
    return result.result_reference


async def convert_to_workflow(
    *, building_id: str, thread_id: str, current_user: dict[str, Any], payload: ConvertThreadToWorkflowRequest
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _assert_write_target(building_id, "powerhouse_workflows")
    now = _utc_now()
    instance_doc = {
        "id": str(uuid4()),
        "building_id": building_id,
        "template_key": payload.workflow_template_key,
        "title": f"Workflow for thread {thread_id}",
        "source_thread_id": thread_id,
        "status": "open",
        "created_by": _user_id_of(current_user),
        "created_at": now,
        "updated_at": now,
        "payload": {"reason": payload.reason},
    }
    await db.powerhouse_workflow_instances.insert_one(dict(instance_doc))
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="workflow.instance.created_from_thread",
        target_type="workflow_instance",
        target_id=instance_doc["id"],
        details={"thread_id": thread_id, "workflow_template_key": payload.workflow_template_key},
    )
    return instance_doc


async def convert_message_to_workflow(
    *,
    building_id: str,
    message_id: str,
    current_user: dict[str, Any],
    payload: ConvertMessageToWorkflowRequest,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _assert_write_target(building_id, "powerhouse_workflows")
    message = await db.powerhouse_conversation_messages.find_one(
        {"id": message_id, "building_id": building_id, "is_deleted": {"$ne": True}}
    )
    if not message:
        raise HTTPException(status_code=404, detail="Conversation message not found")

    now = _utc_now()
    instance_doc = {
        "id": str(uuid4()),
        "building_id": building_id,
        "template_key": payload.workflow_template_key,
        "title": f"Workflow for message {message_id}",
        "source_thread_id": message.get("thread_id"),
        "source_message_id": message_id,
        "status": "open",
        "created_by": _user_id_of(current_user),
        "created_at": now,
        "updated_at": now,
        "payload": {"reason": payload.reason},
    }
    await db.powerhouse_workflow_instances.insert_one(dict(instance_doc))
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="workflow.instance.created_from_message",
        target_type="workflow_instance",
        target_id=instance_doc["id"],
        details={"message_id": message_id, "thread_id": message.get("thread_id"), "workflow_template_key": payload.workflow_template_key},
    )
    return instance_doc


async def generate_ai_summary_placeholder(*, building_id: str, thread_id: str, current_user: dict[str, Any]) -> dict[str, Any]:
    ensure_participant_access(current_user)
    return {
        "thread_id": thread_id,
        "summary": "AI summary placeholder. Human review is required before any action.",
        "suggested_next_actions": ["Review context", "Confirm category", "Assign owner"],
        "confidence": 0.0,
        "requires_human_approval": True,
    }


async def generate_ai_response_draft_placeholder(*, building_id: str, thread_id: str, current_user: dict[str, Any]) -> dict[str, Any]:
    ensure_participant_access(current_user)
    return {
        "thread_id": thread_id,
        "draft_response": "This is a placeholder draft. A user must review/edit before sending.",
        "confidence": 0.0,
        "requires_human_approval": True,
        "safe_to_send": False,
    }


async def list_inboxes(*, building_id: str, current_user: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_participant_access(current_user)

    async def _mongo_coro():
        role = _role_of(current_user)
        query = {"building_id": building_id, "enabled": True}
        if role not in MANAGER_ROLES:
            query["allowed_roles"] = role
        return await db.powerhouse_inboxes.find(query, PUBLIC_MONGO_PROJECTION).sort("inbox_name", 1).to_list(length=200)

    async def _pg_coro():
        return await execute_pg_read(
            lambda: _list_inboxes_from_pg(building_id=building_id, current_user=current_user),
            none_detail="PostgreSQL inbox list unavailable",
        )

    return await _execute_shadow_read(
        building_id=building_id,
        domain="powerhouse_conversations",
        route="GET /inboxes",
        mongo_read=_mongo_coro,
        pg_read=_pg_coro,
        comparator_func=compare_inboxes,
    )


async def configure_inbox(*, building_id: str, current_user: dict[str, Any], payload: InboxConfigUpsert) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _assert_write_target(building_id, "powerhouse_conversations")
    now = _utc_now()
    inbox_id = f"{building_id}:{payload.address.lower()}"
    doc = {
        "id": inbox_id,
        "building_id": building_id,
        "inbox_name": payload.inbox_name,
        "address": payload.address.lower(),
        "provider_key": payload.provider_key,
        "enabled": payload.enabled,
        "allowed_roles": payload.allowed_roles or [str(UserRole.OWNER), str(UserRole.TENANT), str(UserRole.STRATA_MANAGER)],
        "updated_at": now,
        "updated_by": _user_id_of(current_user),
    }
    await db.powerhouse_inboxes.update_one({"id": inbox_id, "building_id": building_id}, {"$set": doc}, upsert=True)
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="email.inbox.configured",
        target_type="inbox",
        target_id=inbox_id,
        details={"provider_key": payload.provider_key, "enabled": payload.enabled},
    )
    return doc


async def process_inbound_email(
    *,
    building_id: str,
    inbox_id: str,
    current_user: dict[str, Any],
    payload: InboundEmailEventCreate,
    idempotency_key: str | None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _assert_write_target(building_id, "powerhouse_conversations")
    now = _utc_now()
    if idempotency_key:
        existing = await db.powerhouse_inbound_email_events.find_one(
            {"building_id": building_id, "inbox_id": inbox_id, "idempotency_key": idempotency_key}
        )
        if existing:
            return {"event_id": existing["id"], "idempotent": True, "status": existing.get("status", "received")}

    event_doc = {
        "id": str(uuid4()),
        "building_id": building_id,
        "inbox_id": inbox_id,
        "idempotency_key": idempotency_key,
        "message_id": payload.message_id,
        "references": payload.references,
        "subject": payload.subject,
        "from_email": payload.from_email,
        "to": payload.to,
        "cc": payload.cc,
        "text_body": payload.text_body,
        "html_body": payload.html_body,
        "attachments": [a.model_dump() for a in payload.attachments],
        "source_external_id": payload.source_external_id,
        "status": "received",
        "created_at": now,
        "created_by": _user_id_of(current_user),
    }
    await db.powerhouse_inbound_email_events.insert_one(event_doc)
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="email.inbound.received",
        target_type="inbound_email_event",
        target_id=event_doc["id"],
        details={"inbox_id": inbox_id, "message_id": payload.message_id, "idempotency_key": idempotency_key},
    )
    return {"event_id": event_doc["id"], "idempotent": False, "status": "received"}


async def map_inbound_to_thread(*, building_id: str, inbox_id: str, current_user: dict[str, Any], message_id: str) -> dict[str, Any]:
    ensure_manager_access(current_user)
    return {
        "mapped": False,
        "reason": "Placeholder mapper not yet connected to provider references",
        "requires_human_approval": True,
    }


async def create_outbound_draft(*, building_id: str, inbox_id: str, current_user: dict[str, Any], payload: OutboundDraftCreate) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _assert_write_target(building_id, "powerhouse_conversations")
    now = _utc_now()
    draft_doc = {
        "id": str(uuid4()),
        "building_id": building_id,
        "inbox_id": inbox_id,
        "subject": payload.subject,
        "body": payload.body,
        "recipients": payload.recipients,
        "cc": payload.cc,
        "thread_id": payload.thread_id,
        "status": "draft",
        "created_at": now,
        "created_by": _user_id_of(current_user),
    }
    await db.powerhouse_outbound_drafts.insert_one(dict(draft_doc))
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="email.outbound.draft_created",
        target_type="outbound_draft",
        target_id=draft_doc["id"],
        details={"inbox_id": inbox_id, "thread_id": payload.thread_id},
    )
    return draft_doc


async def send_outbound_email_placeholder(*, building_id: str, inbox_id: str, current_user: dict[str, Any], draft_id: str) -> dict[str, Any]:
    ensure_manager_access(current_user)
    await _assert_write_target(building_id, "powerhouse_conversations")
    draft = await db.powerhouse_outbound_drafts.find_one({"id": draft_id, "building_id": building_id, "inbox_id": inbox_id})
    if not draft:
        raise HTTPException(status_code=404, detail="Outbound draft not found")

    inbox = await db.powerhouse_inboxes.find_one({"id": inbox_id, "building_id": building_id})
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    provider = PROVIDER_REGISTRY.get(str(inbox.get("provider_key", "mock")))
    if not provider:
        raise HTTPException(status_code=400, detail="No provider configured for inbox")

    result = await provider.send_email(
        inbox_address=str(inbox.get("address")),
        recipients=list(draft.get("recipients") or []),
        subject=str(draft.get("subject") or ""),
        body=str(draft.get("body") or ""),
    )
    now = _utc_now()
    delivery_doc = {
        "id": str(uuid4()),
        "building_id": building_id,
        "inbox_id": inbox_id,
        "draft_id": draft_id,
        "provider_message_id": result.provider_message_id,
        "accepted": result.accepted,
        "note": result.note,
        "created_by": _user_id_of(current_user),
        "created_at": now,
    }
    await db.powerhouse_message_delivery_events.insert_one(delivery_doc)
    await db.powerhouse_outbound_drafts.update_one(
        {"id": draft_id, "building_id": building_id},
        {"$set": {"status": "sent_placeholder", "sent_at": now, "sent_by": _user_id_of(current_user)}},
    )
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="email.outbound.placeholder_sent",
        target_type="outbound_draft",
        target_id=draft_id,
        details={"inbox_id": inbox_id, "delivery_event_id": delivery_doc["id"], "provider_message_id": result.provider_message_id},
    )
    return {
        "draft_id": draft_id,
        "safe_to_send": False,
        "requires_human_approval": True,
        "delivery_event_id": delivery_doc["id"],
        "provider_message_id": result.provider_message_id,
    }


async def list_delivery_events(*, building_id: str, inbox_id: str, current_user: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_manager_access(current_user)
    return await (
        db.powerhouse_message_delivery_events.find(
            {"building_id": building_id, "inbox_id": inbox_id},
            PUBLIC_MONGO_PROJECTION,
        )
        .sort("created_at", -1)
        .limit(200)
        .to_list(length=200)
    )


async def list_workflow_templates(*, building_id: str, current_user: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_participant_access(current_user)

    async def _mongo_coro():
        templates = await db.powerhouse_workflow_templates.find(
            {"building_id": building_id, "is_archived": {"$ne": True}},
            PUBLIC_MONGO_PROJECTION,
        ).to_list(length=200)
        if templates:
            return templates
        return [
            {"key": "maintenance-from-conversation", "name": "Maintenance from conversation", "is_placeholder": True},
            {"key": "document-request-approval", "name": "Document request approval", "is_placeholder": True},
            {"key": "levy-follow-up", "name": "Levy follow-up", "is_placeholder": True},
        ]

    async def _pg_coro():
        return await execute_pg_read(
            lambda: _list_workflow_templates_from_pg(building_id=building_id),
            none_detail="PostgreSQL workflow templates unavailable",
        )

    return await _execute_shadow_read(
        building_id=building_id,
        domain="powerhouse_workflows",
        route="GET /workflows/templates",
        mongo_read=_mongo_coro,
        pg_read=_pg_coro,
        comparator_func=compare_workflow_templates,
    )


async def create_workflow_instance(
    *,
    building_id: str,
    current_user: dict[str, Any],
    payload: WorkflowInstanceCreate,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_workflows")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_workflow_command_service import create_workflow_instance_command

        result = await create_workflow_instance_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            actor_user_id=pg_actor_user_id,
            template_key=payload.template_key,
            title=payload.title,
            linked_entity_type=payload.linked_entity.entity_type if payload.linked_entity else None,
            linked_entity_id=payload.linked_entity.entity_id if payload.linked_entity else None,
            initial_payload=payload.initial_payload,
            idempotency_key=idempotency_key or str(uuid4()),
            is_test_data=payload.is_test_data,
        )
        return result.result_reference

    now = _utc_now()
    instance_doc = {
        "id": str(uuid4()),
        "building_id": building_id,
        "template_key": payload.template_key,
        "title": payload.title,
        "linked_entity": payload.linked_entity.model_dump() if payload.linked_entity else None,
        "status": "open",
        "initial_payload": payload.initial_payload,
        "created_at": now,
        "updated_at": now,
        "created_by": _user_id_of(current_user),
        "is_test_data": payload.is_test_data,
    }
    await db.powerhouse_workflow_instances.insert_one(dict(instance_doc))
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="workflow.instance.created",
        target_type="workflow_instance",
        target_id=instance_doc["id"],
        details={"template_key": payload.template_key},
    )
    return instance_doc


async def get_workflow_instance(*, building_id: str, instance_id: str, current_user: dict[str, Any]) -> dict[str, Any]:
    ensure_participant_access(current_user)

    async def _mongo_coro():
        instance = await db.powerhouse_workflow_instances.find_one(
            {"id": instance_id, "building_id": building_id},
            PUBLIC_MONGO_PROJECTION,
        )
        if not instance:
            raise HTTPException(status_code=404, detail="Workflow instance not found")
        events = await (
            db.powerhouse_workflow_events.find(
                {"instance_id": instance_id, "building_id": building_id},
                PUBLIC_MONGO_PROJECTION,
            )
            .sort("created_at", 1)
            .to_list(length=200)
        )
        return {"instance": instance, "events": events}

    async def _pg_coro():
        return await execute_pg_read(
            lambda: _get_workflow_instance_from_pg(building_id=building_id, instance_id=instance_id, current_user=current_user),
            none_detail="PostgreSQL workflow instance unavailable",
        )

    return await _execute_shadow_read(
        building_id=building_id,
        domain="powerhouse_workflows",
        route=f"GET /workflows/instances/{instance_id}",
        mongo_read=_mongo_coro,
        pg_read=_pg_coro,
        comparator_func=compare_workflow_instances,
    )


async def add_workflow_event(
    *,
    building_id: str,
    instance_id: str,
    current_user: dict[str, Any],
    payload: WorkflowEventCreate,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_workflows")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_workflow_command_service import add_workflow_event_command

        result = await add_workflow_event_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            instance_id=instance_id,
            actor_user_id=pg_actor_user_id,
            event_type=payload.event_type,
            event_payload=payload.payload,
            idempotency_key=idempotency_key or str(uuid4()),
            is_test_data=payload.is_test_data,
        )
        return result.result_reference

    now = _utc_now()
    event_doc = {
        "id": str(uuid4()),
        "instance_id": instance_id,
        "building_id": building_id,
        "event_type": payload.event_type,
        "payload": payload.payload,
        "created_at": now,
        "created_by": _user_id_of(current_user),
        "is_test_data": payload.is_test_data,
    }
    await db.powerhouse_workflow_events.insert_one(dict(event_doc))
    await db.powerhouse_workflow_instances.update_one(
        {"id": instance_id, "building_id": building_id},
        {"$set": {"updated_at": now}},
    )
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="workflow.event.added",
        target_type="workflow_instance",
        target_id=instance_id,
        details={"event_type": payload.event_type, "event_id": event_doc["id"]},
    )
    return event_doc


async def complete_workflow_step(
    *,
    building_id: str,
    instance_id: str,
    current_user: dict[str, Any],
    payload: WorkflowStepCompleteRequest,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_workflows")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_workflow_command_service import complete_workflow_step_command

        # Behavioral difference from the Mongo path below, documented in
        # tasks/GAP-POWERHOUSE-001: the PG command requires step_key to
        # already exist in workflow.workflow_steps (materialised from the
        # instance's template at creation) and 404s otherwise. The Mongo
        # path writes a status-history row unconditionally, even for a
        # step_key that was never part of any template. This is treated as
        # the PG behaviour being the correct one going forward, not a
        # regression to work around.
        result = await complete_workflow_step_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            instance_id=instance_id,
            actor_user_id=pg_actor_user_id,
            step_key=payload.step_key,
            notes=payload.notes,
            idempotency_key=idempotency_key or str(uuid4()),
            is_test_data=payload.is_test_data,
        )
        return result.result_reference

    now = _utc_now()
    history_doc = {
        "id": str(uuid4()),
        "instance_id": instance_id,
        "building_id": building_id,
        "step_key": payload.step_key,
        "status": "completed",
        "notes": payload.notes,
        "changed_at": now,
        "changed_by": _user_id_of(current_user),
        "is_test_data": payload.is_test_data,
    }
    await db.powerhouse_workflow_status_history.insert_one(dict(history_doc))
    await db.powerhouse_workflow_instances.update_one(
        {"id": instance_id, "building_id": building_id},
        {"$set": {"updated_at": now}},
    )
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="workflow.step.completed",
        target_type="workflow_instance",
        target_id=instance_id,
        details={"step_key": payload.step_key, "history_id": history_doc["id"]},
    )
    return history_doc


async def assign_workflow_task(
    *,
    building_id: str,
    instance_id: str,
    current_user: dict[str, Any],
    payload: WorkflowAssignmentCreate,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_workflows")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_workflow_command_service import assign_workflow_task_command

        # assignee_user_id is passed straight through, matching assign_thread's
        # existing dual-path wiring above (no server-side Mongo->PG id
        # resolution exists for an arbitrary target user — _resolve_pg_actor_user_id
        # only works for current_user, whose email is available from their
        # session). The caller is responsible for supplying a real
        # core.users UUID once write_source=postgres; assign_workflow_task_command's
        # own _require_uuid() validation returns a clean 422 on a malformed id.
        result = await assign_workflow_task_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            instance_id=instance_id,
            actor_user_id=pg_actor_user_id,
            assignee_user_id=payload.assignee_user_id,
            step_key=payload.step_key,
            idempotency_key=idempotency_key or str(uuid4()),
            is_test_data=payload.is_test_data,
        )
        return result.result_reference

    now = _utc_now()
    assignment_doc = {
        "id": str(uuid4()),
        "instance_id": instance_id,
        "building_id": building_id,
        "assignee_user_id": payload.assignee_user_id,
        "step_key": payload.step_key,
        "assigned_by": _user_id_of(current_user),
        "assigned_at": now,
        "status": "assigned",
        "is_test_data": payload.is_test_data,
    }
    await db.powerhouse_workflow_assignments.insert_one(dict(assignment_doc))
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="workflow.assignment.created",
        target_type="workflow_instance",
        target_id=instance_id,
        details={"assignment_id": assignment_doc["id"], "assignee_user_id": payload.assignee_user_id},
    )
    return assignment_doc


async def run_automation_rule_placeholder(
    *,
    building_id: str,
    current_user: dict[str, Any],
    payload: AutomationRuleRunRequest,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_manager_access(current_user)
    from models.cutover_status import DataSource

    write_source = await _resolve_write_source(building_id, "powerhouse_workflows")

    if write_source == DataSource.postgres:
        ctx = await _resolve_pg_context(building_id)
        if not ctx:
            raise HTTPException(status_code=503, detail="PostgreSQL context unavailable for write_source=postgres")
        tenant_id, scheme_id = ctx
        pg_actor_user_id = await _resolve_pg_actor_user_id(current_user)
        from services.powerhouse_workflow_command_service import execute_automation_rule_command

        # Behavioral difference from the Mongo path below, documented in
        # tasks/GAP-POWERHOUSE-001: the PG command 404s when rule_key doesn't
        # exist in workflow.automation_rules. The Mongo path blindly inserts
        # a run row for any rule_key with no existence check at all. Treated
        # as the PG behaviour being the correct one going forward, not a
        # regression to work around — a "run" for a rule that was never
        # created is a bug, not a feature.
        result = await execute_automation_rule_command(
            building_id=building_id,
            tenant_id=tenant_id,
            scheme_id=scheme_id,
            actor_user_id=pg_actor_user_id,
            rule_key=payload.rule_key,
            dry_run=payload.dry_run,
            idempotency_key=idempotency_key or str(uuid4()),
            is_test_data=payload.is_test_data,
        )
        return result.result_reference

    now = _utc_now()
    run_doc = {
        "id": str(uuid4()),
        "building_id": building_id,
        "rule_key": payload.rule_key,
        "dry_run": payload.dry_run,
        "status": "simulated",
        "requires_human_approval": True,
        "created_at": now,
        "created_by": _user_id_of(current_user),
        "is_test_data": payload.is_test_data,
    }
    await db.powerhouse_automation_rule_runs.insert_one(dict(run_doc))
    await _record_audit_event(
        building_id=building_id,
        actor_user=current_user,
        event_type="workflow.automation_rule.run_placeholder",
        target_type="automation_rule_run",
        target_id=run_doc["id"],
        details={"rule_key": payload.rule_key, "dry_run": payload.dry_run},
    )
    return run_doc


async def list_automation_rule_runs(
    *, building_id: str, current_user: dict[str, Any], include_test_data: bool = False
) -> list[dict[str, Any]]:
    ensure_manager_access(current_user)

    async def _mongo_coro():
        query: dict[str, Any] = {"building_id": building_id}
        if not include_test_data:
            query["is_test_data"] = {"$ne": True}
        return await (
            db.powerhouse_automation_rule_runs.find(query, PUBLIC_MONGO_PROJECTION)
            .sort("created_at", -1)
            .limit(200)
            .to_list(length=200)
        )

    async def _pg_coro():
        return await execute_pg_read(
            lambda: _list_automation_rule_runs_from_pg(building_id=building_id, current_user=current_user),
            none_detail="PostgreSQL automation runs unavailable",
        )

    return await _execute_shadow_read(
        building_id=building_id,
        domain="powerhouse_workflows",
        route="GET /workflows/automation-rule-runs",
        mongo_read=_mongo_coro,
        pg_read=_pg_coro,
        comparator_func=compare_automation_rule_runs,
    )
