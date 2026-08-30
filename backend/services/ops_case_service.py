"""Postgres-backed unified ops case service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from db_postgres.repos import config_repo, identity_repo
from db_postgres.session import async_session_context, set_tenant
from models.ops_case import (
    OpsCaseApprovalRequestCreate,
    OpsCaseAssignCreate,
    OpsCaseCommentCreate,
    OpsCaseCreate,
    OpsCaseLinkCreate,
    OpsCaseStatusCreate,
    OpsCaseUpdate,
)
from models.user import UserRole
from utils.auth import effective_role

_MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.ADMIN_STAFF,
    UserRole.EC_MEMBER,
}

_CASE_SELECT = """
    SELECT
        c.id::text AS id,
        c.scheme_id::text AS scheme_id,
        c.title,
        c.description,
        c.category,
        c.priority,
        c.risk_level,
        c.status,
        c.source_type,
        c.source_id,
        c.related_lot_ids,
        c.related_owner_party_ids,
        c.related_tenant_party_ids,
        c.related_asset_ids,
        c.assigned_to::text AS assigned_to,
        assignee.full_name AS assigned_to_name,
        c.due_at,
        c.sla_policy_code,
        c.approval_required,
        c.approval_request_id::text AS approval_request_id,
        approval.status AS approval_status,
        c.visibility_scope,
        c.pii_classification,
        c.created_by::text AS created_by,
        creator.full_name AS created_by_name,
        c.updated_by::text AS updated_by,
        updater.full_name AS updated_by_name,
        c.created_at,
        c.updated_at,
        COALESCE(comments.comment_count, 0) AS comment_count,
        COALESCE(links.link_count, 0) AS link_count
    FROM ops.cases c
    LEFT JOIN core.users creator ON creator.user_id = c.created_by
    LEFT JOIN core.users updater ON updater.user_id = c.updated_by
    LEFT JOIN core.users assignee ON assignee.user_id = c.assigned_to
    LEFT JOIN core.approval_requests approval ON approval.approval_request_id = c.approval_request_id
    LEFT JOIN (
        SELECT case_id, COUNT(*)::int AS comment_count
        FROM ops.task_comments
        GROUP BY case_id
    ) comments ON comments.case_id = c.id
    LEFT JOIN (
        SELECT case_id, COUNT(*)::int AS link_count
        FROM ops.case_links
        GROUP BY case_id
    ) links ON links.case_id = c.id
"""


@dataclass
class _CaseContext:
    tenant_id: str
    scheme_id: str
    role: str
    actor_user_id: str | None
    actor_email: str | None

    @property
    def is_manager(self) -> bool:
        """Generated function header.

        Function: _CaseContext.is_manager
        Path: backend/services/ops_case_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self.role in _MANAGER_ROLES


def _row_to_dict(row) -> dict[str, Any]:
    """Generated function header.

    Function: _row_to_dict
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _uuid_text(value: Any) -> str | None:
    """Generated function header.

    Function: _uuid_text
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(value) if value else None


def _uuid_list(values: Any) -> list[str]:
    """Generated function header.

    Function: _uuid_list
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return [str(value) for value in list(values or [])]


def _normalise_case(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_case
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    data["id"] = _uuid_text(data.get("id"))
    data["scheme_id"] = _uuid_text(data.get("scheme_id"))
    data["assigned_to"] = _uuid_text(data.get("assigned_to"))
    data["approval_request_id"] = _uuid_text(data.get("approval_request_id"))
    data["created_by"] = _uuid_text(data.get("created_by"))
    data["updated_by"] = _uuid_text(data.get("updated_by"))
    data["related_lot_ids"] = _uuid_list(data.get("related_lot_ids"))
    data["related_owner_party_ids"] = _uuid_list(data.get("related_owner_party_ids"))
    data["related_tenant_party_ids"] = _uuid_list(data.get("related_tenant_party_ids"))
    data["related_asset_ids"] = list(data.get("related_asset_ids") or [])
    data["comment_count"] = int(data.get("comment_count") or 0)
    data["link_count"] = int(data.get("link_count") or 0)
    return data


def _json_dump(payload: dict[str, Any] | None) -> str:
    """Generated function header.

    Function: _json_dump
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return json.dumps(payload or {}, default=str)


def _parse_uuid(value: str, *, label: str) -> UUID:
    """Generated function header.

    Function: _parse_uuid
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {label}. Expected UUID.",
        ) from exc


def _access_sql(ctx: _CaseContext, alias: str, *, mode: str) -> tuple[str, dict[str, Any]]:
    """Generated function header.

    Function: _access_sql
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if ctx.is_manager:
        return "TRUE", {}
    if not ctx.actor_user_id:
        return "FALSE", {}
    if mode == "view":
        return f"({alias}.created_by = :actor_user_id OR {alias}.assigned_to = :actor_user_id)", {
            "actor_user_id": ctx.actor_user_id,
        }
    if mode == "edit":
        return f"{alias}.created_by = :actor_user_id", {"actor_user_id": ctx.actor_user_id}
    if mode == "status":
        return f"{alias}.assigned_to = :actor_user_id", {"actor_user_id": ctx.actor_user_id}
    raise ValueError(f"Unknown case access mode: {mode}")


async def _resolve_context(building_id: str, current_user: dict) -> _CaseContext:
    """Generated function header.

    Function: _resolve_context
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Building not found in PostgreSQL.")

    role = effective_role(current_user)
    actor_user_id = await config_repo.resolve_actor_user_id(
        str(current_user.get("id")) if current_user.get("id") else None,
        current_user.get("email"),
        require_existing=False,
    )
    if actor_user_id is None and role not in _MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is not mapped to the Postgres identity store yet. Please sign in again.",
        )
    return _CaseContext(
        tenant_id=str(scheme["tenant_id"]),
        scheme_id=str(scheme["scheme_id"]),
        role=role,
        actor_user_id=actor_user_id,
        actor_email=current_user.get("email"),
    )


async def _get_case_for_access(session, ctx: _CaseContext, case_uuid: UUID, *, mode: str) -> dict[str, Any]:
    """Generated function header.

    Function: _get_case_for_access
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    access_sql, access_params = _access_sql(ctx, "c", mode=mode)
    result = await session.execute(
        text(
            f"""
            {_CASE_SELECT}
            WHERE c.scheme_id = :scheme_id
              AND c.id = :case_id
              AND c.deleted_at IS NULL
              AND {access_sql}
            """
        ),
        {"scheme_id": ctx.scheme_id, "case_id": case_uuid, **access_params},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found.")
    return _normalise_case(row)


async def _insert_case_event(
    session,
    ctx: _CaseContext,
    *,
    case_uuid: UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Generated function header.

    Function: _insert_case_event
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            """
            INSERT INTO ops.case_events
                (tenant_id, scheme_id, case_id, event_type, from_status, to_status,
                 event_payload, actor_user_id, source_correlation_id)
            VALUES
                (:tenant_id, :scheme_id, :case_id, :event_type, :from_status, :to_status,
                 CAST(:event_payload AS jsonb), :actor_user_id, :correlation_id)
            """
        ),
        {
            "tenant_id": ctx.tenant_id,
            "scheme_id": ctx.scheme_id,
            "case_id": case_uuid,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "event_payload": _json_dump(payload),
            "actor_user_id": ctx.actor_user_id,
            "correlation_id": correlation_id,
        },
    )


async def _insert_status_history(
    session,
    ctx: _CaseContext,
    *,
    case_uuid: UUID,
    from_status: str | None,
    to_status: str,
    reason: str | None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: _insert_status_history
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            INSERT INTO ops.task_status_history
                (tenant_id, scheme_id, case_id, from_status, to_status, change_reason,
                 changed_by, source_correlation_id)
            VALUES
                (:tenant_id, :scheme_id, :case_id, :from_status, :to_status, :change_reason,
                 :changed_by, :correlation_id)
            RETURNING case_id::text AS case_id,
                      from_status,
                      to_status,
                      change_reason,
                      changed_by::text AS changed_by,
                      changed_at
            """
        ),
        {
            "tenant_id": ctx.tenant_id,
            "scheme_id": ctx.scheme_id,
            "case_id": case_uuid,
            "from_status": from_status,
            "to_status": to_status,
            "change_reason": reason,
            "changed_by": ctx.actor_user_id,
            "correlation_id": correlation_id,
        },
    )
    return _row_to_dict(result.one())


async def _insert_audit_event(
    session,
    ctx: _CaseContext,
    *,
    case_uuid: UUID,
    action: str,
    payload: dict[str, Any] | None,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """Generated function header.

    Function: _insert_audit_event
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            """
            INSERT INTO core.audit_events
                (tenant_id, scheme_id, entity_type, entity_id, action, actor_user_id,
                 ip_address, user_agent, event_payload)
            VALUES
                (:tenant_id, :scheme_id, 'ops_case', :entity_id, :action, :actor_user_id,
                 :ip_address, :user_agent, CAST(:event_payload AS jsonb))
            """
        ),
        {
            "tenant_id": ctx.tenant_id,
            "scheme_id": ctx.scheme_id,
            "entity_id": case_uuid,
            "action": action,
            "actor_user_id": ctx.actor_user_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "event_payload": _json_dump(payload),
        },
    )


async def _fetch_policy(session, ctx: _CaseContext, policy_code: str | None) -> dict[str, Any] | None:
    """Generated function header.

    Function: _fetch_policy
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    where = ""
    params: dict[str, Any] = {"tenant_id": ctx.tenant_id, "scheme_id": ctx.scheme_id}
    if policy_code:
        where = "AND policy_code = :policy_code"
        params["policy_code"] = policy_code

    result = await session.execute(
        text(
            f"""
            SELECT approval_policy_id::text AS approval_policy_id,
                   policy_code,
                   required_roles
            FROM core.approval_policies
            WHERE tenant_id = :tenant_id
              AND (scheme_id = :scheme_id OR scheme_id IS NULL)
              AND status = 'active'
              {where}
            ORDER BY CASE WHEN scheme_id = :scheme_id THEN 0 ELSE 1 END,
                     created_at DESC
            LIMIT 1
            """
        ),
        params,
    )
    row = result.first()
    return _row_to_dict(row) if row else None


def _require_policy_roles(policy: dict[str, Any] | None, *, detail: str) -> tuple[dict[str, Any], list[str]]:
    """Generated function header.

    Function: _require_policy_roles
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not policy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    required_roles = list(policy.get("required_roles") or [])
    if not required_roles:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return policy, required_roles


async def _transition_status(
    session,
    ctx: _CaseContext,
    *,
    case_uuid: UUID,
    from_status: str,
    to_status: str,
    reason: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: _transition_status
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        await session.execute(
            text(
                """
                UPDATE ops.cases
                SET status = :to_status,
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :case_id
                """
            ),
            {
                "to_status": to_status,
                "updated_by": ctx.actor_user_id,
                "case_id": case_uuid,
            },
        )
    except (IntegrityError, DBAPIError) as exc:
        message = str(exc)
        if "invalid ops.cases status transition" in message:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid case status transition: {from_status} -> {to_status}",
            ) from exc
        raise

    history = await _insert_status_history(
        session,
        ctx,
        case_uuid=case_uuid,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        correlation_id=correlation_id,
    )
    await _insert_case_event(
        session,
        ctx,
        case_uuid=case_uuid,
        event_type="status_changed",
        payload={"reason": reason},
        from_status=from_status,
        to_status=to_status,
        correlation_id=correlation_id,
    )
    return history


async def list_cases(
    *,
    building_id: str,
    current_user: dict,
    status_filter: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    assigned_to: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_cases
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    access_sql, access_params = _access_sql(ctx, "c", mode="view")
    filters = ["c.scheme_id = :scheme_id", "c.deleted_at IS NULL", access_sql]
    params: dict[str, Any] = {
        "scheme_id": ctx.scheme_id,
        "limit": limit,
        "offset": offset,
        **access_params,
    }
    if status_filter:
        filters.append("c.status = :status_filter")
        params["status_filter"] = status_filter
    if category:
        filters.append("c.category = :category")
        params["category"] = category
    if priority:
        filters.append("c.priority = :priority")
        params["priority"] = priority
    if assigned_to:
        params["assigned_to"] = _parse_uuid(assigned_to, label="assigned_to")
        filters.append("c.assigned_to = :assigned_to")
    if search:
        filters.append("(c.title ILIKE :search OR COALESCE(c.description, '') ILIKE :search)")
        params["search"] = f"%{search.strip()}%"

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                {_CASE_SELECT}
                WHERE {' AND '.join(filters)}
                ORDER BY c.created_at DESC
                LIMIT :limit
                OFFSET :offset
                """
            ),
            params,
        )
        return [_normalise_case(row) for row in result.fetchall()]


async def get_case(*, building_id: str, case_id: str, current_user: dict) -> dict[str, Any]:
    """Generated function header.

    Function: get_case
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    case_uuid = _parse_uuid(case_id, label="case_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        return await _get_case_for_access(session, ctx, case_uuid, mode="view")


async def create_case(
    *,
    building_id: str,
    current_user: dict,
    payload: OpsCaseCreate,
    correlation_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: create_case
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    values = payload.model_dump()

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                INSERT INTO ops.cases
                    (tenant_id, scheme_id, title, description, category, priority, risk_level, status,
                     source_type, source_id, related_lot_ids, related_owner_party_ids,
                     related_tenant_party_ids, related_asset_ids, due_at, sla_policy_code,
                     approval_required, visibility_scope, pii_classification, created_by, updated_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :title, :description, :category, :priority, :risk_level, 'new',
                     :source_type, :source_id, :related_lot_ids, :related_owner_party_ids,
                     :related_tenant_party_ids, :related_asset_ids, :due_at, :sla_policy_code,
                     :approval_required, :visibility_scope, :pii_classification, :created_by, :updated_by,
                     :correlation_id)
                RETURNING id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "title": values["title"].strip(),
                "description": values.get("description"),
                "category": values["category"].strip(),
                "priority": values["priority"],
                "risk_level": values["risk_level"],
                "source_type": values.get("source_type"),
                "source_id": values.get("source_id"),
                "related_lot_ids": values["related_lot_ids"],
                "related_owner_party_ids": values["related_owner_party_ids"],
                "related_tenant_party_ids": values["related_tenant_party_ids"],
                "related_asset_ids": values["related_asset_ids"],
                "due_at": values.get("due_at"),
                "sla_policy_code": values.get("sla_policy_code"),
                "approval_required": values["approval_required"],
                "visibility_scope": values["visibility_scope"],
                "pii_classification": values["pii_classification"],
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        case_uuid = result.scalar_one()
        await _insert_status_history(
            session,
            ctx,
            case_uuid=case_uuid,
            from_status=None,
            to_status="new",
            reason="Case created",
            correlation_id=correlation_id,
        )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="case_created",
            payload={
                "category": values["category"],
                "priority": values["priority"],
                "risk_level": values["risk_level"],
                "approval_required": values["approval_required"],
            },
            to_status="new",
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.case.created",
            payload={"title": values["title"], "category": values["category"]},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _get_case_for_access(session, ctx, case_uuid, mode="view")


async def update_case(
    *,
    building_id: str,
    case_id: str,
    current_user: dict,
    payload: OpsCaseUpdate,
    correlation_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: update_case
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    case_uuid = _parse_uuid(case_id, label="case_id")
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No case fields were provided.")

    db_fields = {
        "title": "title",
        "description": "description",
        "category": "category",
        "priority": "priority",
        "risk_level": "risk_level",
        "source_type": "source_type",
        "source_id": "source_id",
        "related_lot_ids": "related_lot_ids",
        "related_owner_party_ids": "related_owner_party_ids",
        "related_tenant_party_ids": "related_tenant_party_ids",
        "related_asset_ids": "related_asset_ids",
        "due_at": "due_at",
        "sla_policy_code": "sla_policy_code",
        "approval_required": "approval_required",
        "visibility_scope": "visibility_scope",
        "pii_classification": "pii_classification",
    }

    set_clauses = []
    params: dict[str, Any] = {"case_id": case_uuid, "updated_by": ctx.actor_user_id}
    changed_fields: dict[str, Any] = {}
    for field_name, column_name in db_fields.items():
        if field_name not in updates:
            continue
        set_clauses.append(f"{column_name} = :{field_name}")
        params[field_name] = updates[field_name]
        changed_fields[field_name] = updates[field_name]

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _get_case_for_access(session, ctx, case_uuid, mode="edit")
        await session.execute(
            text(
                f"""
                UPDATE ops.cases
                SET {', '.join(set_clauses)},
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :case_id
                """
            ),
            params,
        )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="case_updated",
            payload={"changed_fields": changed_fields},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.case.updated",
            payload={"changed_fields": changed_fields},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _get_case_for_access(session, ctx, case_uuid, mode="view")


async def add_case_comment(
    *,
    building_id: str,
    case_id: str,
    current_user: dict,
    payload: OpsCaseCommentCreate,
    correlation_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: add_case_comment
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    case_uuid = _parse_uuid(case_id, label="case_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _get_case_for_access(session, ctx, case_uuid, mode="view")
        result = await session.execute(
            text(
                """
                INSERT INTO ops.task_comments
                    (tenant_id, scheme_id, case_id, body, is_internal, pii_classification,
                     created_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :body, :is_internal, :pii_classification,
                     :created_by, :correlation_id)
                RETURNING id::text AS id, case_id::text AS case_id, body, is_internal,
                          pii_classification, created_by::text AS created_by, created_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "body": payload.body.strip(),
                "is_internal": payload.is_internal,
                "pii_classification": payload.pii_classification,
                "created_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        row = _row_to_dict(result.one())
        row["created_by_name"] = current_user.get("full_name")
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="comment_added",
            payload={"is_internal": payload.is_internal, "pii_classification": payload.pii_classification},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.case.comment_added",
            payload={"is_internal": payload.is_internal, "pii_classification": payload.pii_classification},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return row


async def assign_case(
    *,
    building_id: str,
    case_id: str,
    current_user: dict,
    payload: OpsCaseAssignCreate,
    correlation_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: assign_case
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if not ctx.is_manager:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can assign cases.")

    case_uuid = _parse_uuid(case_id, label="case_id")
    assignee_user_id = str(payload.assigned_to)
    if not await identity_repo.is_user_in_scheme(assignee_user_id, ctx.scheme_id, ctx.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assigned user does not have access to this building.",
        )
    assignee = await identity_repo.find_user_by_id_for_admin(assignee_user_id)

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        case = await _get_case_for_access(session, ctx, case_uuid, mode="view")
        new_status = case["status"]
        if case["status"] in {"new", "triaged", "waiting_for_info", "approved"}:
            await _transition_status(
                session,
                ctx,
                case_uuid=case_uuid,
                from_status=case["status"],
                to_status="assigned",
                reason="Case assigned",
                correlation_id=correlation_id,
            )
            new_status = "assigned"

        await session.execute(
            text(
                """
                UPDATE ops.cases
                SET assigned_to = :assigned_to,
                    due_at = COALESCE(:due_at, due_at),
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :case_id
                """
            ),
            {
                "assigned_to": assignee_user_id,
                "due_at": payload.due_at,
                "updated_by": ctx.actor_user_id,
                "case_id": case_uuid,
            },
        )
        result = await session.execute(
            text(
                """
                INSERT INTO ops.task_assignments
                    (tenant_id, scheme_id, case_id, assigned_to, assigned_by, due_at, status,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :assigned_to, :assigned_by, :due_at, 'assigned',
                     :correlation_id)
                RETURNING id::text AS id, case_id::text AS case_id, assigned_to::text AS assigned_to,
                          assigned_by::text AS assigned_by, due_at, status, created_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "assigned_to": assignee_user_id,
                "assigned_by": ctx.actor_user_id,
                "due_at": payload.due_at,
                "correlation_id": correlation_id,
            },
        )
        row = _row_to_dict(result.one())
        row["assigned_to_name"] = assignee.get("full_name") if assignee else None

        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="assigned",
            payload={"assigned_to": assignee_user_id, "due_at": payload.due_at, "status": new_status},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.case.assigned",
            payload={"assigned_to": assignee_user_id, "due_at": payload.due_at},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if payload.note:
            await session.execute(
                text(
                    """
                    INSERT INTO ops.task_comments
                        (tenant_id, scheme_id, case_id, body, is_internal, pii_classification,
                         created_by, source_correlation_id)
                    VALUES
                        (:tenant_id, :scheme_id, :case_id, :body, TRUE, 'internal',
                         :created_by, :correlation_id)
                    """
                ),
                {
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "case_id": case_uuid,
                    "body": payload.note,
                    "created_by": ctx.actor_user_id,
                    "correlation_id": correlation_id,
                },
            )
        return row


async def change_case_status(
    *,
    building_id: str,
    case_id: str,
    current_user: dict,
    payload: OpsCaseStatusCreate,
    correlation_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: change_case_status
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    case_uuid = _parse_uuid(case_id, label="case_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        case = await _get_case_for_access(session, ctx, case_uuid, mode="status" if not ctx.is_manager else "view")
        history = await _transition_status(
            session,
            ctx,
            case_uuid=case_uuid,
            from_status=case["status"],
            to_status=payload.status,
            reason=payload.change_reason,
            correlation_id=correlation_id,
        )
        if payload.comment:
            await session.execute(
                text(
                    """
                    INSERT INTO ops.task_comments
                        (tenant_id, scheme_id, case_id, body, is_internal, pii_classification,
                         created_by, source_correlation_id)
                    VALUES
                        (:tenant_id, :scheme_id, :case_id, :body, TRUE, 'internal',
                         :created_by, :correlation_id)
                    """
                ),
                {
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "case_id": case_uuid,
                    "body": payload.comment,
                    "created_by": ctx.actor_user_id,
                    "correlation_id": correlation_id,
                },
            )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.case.status_changed",
            payload={"from_status": case["status"], "to_status": payload.status, "reason": payload.change_reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return history


async def link_case(
    *,
    building_id: str,
    case_id: str,
    current_user: dict,
    payload: OpsCaseLinkCreate,
    correlation_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: link_case
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if not ctx.is_manager:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can link cases.")

    case_uuid = _parse_uuid(case_id, label="case_id")
    linked_case_uuid = None
    if payload.linked_case_id:
        linked_case_uuid = payload.linked_case_id

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _get_case_for_access(session, ctx, case_uuid, mode="view")
        if linked_case_uuid:
            result = await session.execute(
                text("SELECT 1 FROM ops.cases WHERE id = :linked_case_id AND scheme_id = :scheme_id AND deleted_at IS NULL"),
                {"linked_case_id": linked_case_uuid, "scheme_id": ctx.scheme_id},
            )
            if result.first() is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linked case not found.")

        result = await session.execute(
            text(
                """
                INSERT INTO ops.case_links
                    (tenant_id, scheme_id, case_id, linked_case_id, linked_entity_type,
                     linked_entity_id, relationship_type, created_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :linked_case_id, :linked_entity_type,
                     :linked_entity_id, :relationship_type, :created_by, :correlation_id)
                RETURNING id::text AS id, case_id::text AS case_id, relationship_type,
                          linked_case_id::text AS linked_case_id, linked_entity_type,
                          linked_entity_id, created_by::text AS created_by, created_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "linked_case_id": linked_case_uuid,
                "linked_entity_type": payload.linked_entity_type,
                "linked_entity_id": payload.linked_entity_id,
                "relationship_type": payload.relationship_type,
                "created_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        row = _row_to_dict(result.one())
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="linked",
            payload={
                "relationship_type": payload.relationship_type,
                "linked_case_id": linked_case_uuid,
                "linked_entity_type": payload.linked_entity_type,
                "linked_entity_id": payload.linked_entity_id,
            },
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.case.linked",
            payload={
                "relationship_type": payload.relationship_type,
                "linked_case_id": linked_case_uuid,
                "linked_entity_type": payload.linked_entity_type,
                "linked_entity_id": payload.linked_entity_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return row


async def request_case_approval(
    *,
    building_id: str,
    case_id: str,
    current_user: dict,
    payload: OpsCaseApprovalRequestCreate,
    correlation_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: request_case_approval
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if not ctx.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can request case approvals.",
        )

    case_uuid = _parse_uuid(case_id, label="case_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        case = await _get_case_for_access(session, ctx, case_uuid, mode="view")
        if case.get("approval_request_id"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Case already has an approval request.")

        if case["status"] == "new":
            await _transition_status(
                session,
                ctx,
                case_uuid=case_uuid,
                from_status="new",
                to_status="triaged",
                reason="Approval requested",
                correlation_id=correlation_id,
            )
            case["status"] = "triaged"
        elif case["status"] == "waiting_for_info":
            await _transition_status(
                session,
                ctx,
                case_uuid=case_uuid,
                from_status="waiting_for_info",
                to_status="triaged",
                reason="Approval requested",
                correlation_id=correlation_id,
            )
            case["status"] = "triaged"

        if case["status"] not in {"triaged", "assigned", "in_progress"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot request approval from status '{case['status']}'.",
            )

        policy = await _fetch_policy(session, ctx, payload.policy_code)
        policy, required_roles = _require_policy_roles(
            policy,
            detail="Approval policy is missing or has no approver roles for ops cases.",
        )
        result = await session.execute(
            text(
                """
                INSERT INTO core.approval_requests
                    (tenant_id, scheme_id, entity_type, entity_id, approval_policy_id, status, requested_by)
                VALUES
                    (:tenant_id, :scheme_id, 'ops_case', :entity_id, :approval_policy_id, 'pending', :requested_by)
                RETURNING approval_request_id::text AS approval_request_id,
                          approval_policy_id::text AS approval_policy_id,
                          status,
                          requested_by::text AS requested_by,
                          created_at AS requested_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "entity_id": case_uuid,
                "approval_policy_id": policy["approval_policy_id"],
                "requested_by": ctx.actor_user_id,
            },
        )
        row = _row_to_dict(result.one())
        for index, required_role in enumerate(required_roles, start=1):
            await session.execute(
                text(
                    """
                    INSERT INTO core.approval_steps
                        (approval_request_id, sequence_no, required_role)
                    VALUES
                        (:approval_request_id, :sequence_no, :required_role)
                    """
                ),
                {
                    "approval_request_id": row["approval_request_id"],
                    "sequence_no": index,
                    "required_role": required_role,
                },
            )

        await _transition_status(
            session,
            ctx,
            case_uuid=case_uuid,
            from_status=case["status"],
            to_status="waiting_for_approval",
            reason=payload.note or "Approval requested",
            correlation_id=correlation_id,
        )
        await session.execute(
            text(
                """
                UPDATE ops.cases
                SET approval_required = TRUE,
                    approval_request_id = :approval_request_id,
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :case_id
                """
            ),
            {
                "approval_request_id": row["approval_request_id"],
                "updated_by": ctx.actor_user_id,
                "case_id": case_uuid,
            },
        )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="approval_requested",
            payload={
                "approval_request_id": row["approval_request_id"],
                "approval_policy_id": row.get("approval_policy_id"),
                "policy_code": policy["policy_code"],
                "required_roles": required_roles,
            },
            from_status=case["status"],
            to_status="waiting_for_approval",
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.case.approval_requested",
            payload={
                "approval_request_id": row["approval_request_id"],
                "approval_policy_id": row.get("approval_policy_id"),
                "policy_code": policy["policy_code"],
                "required_roles": required_roles,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        row["case_id"] = str(case_uuid)
        row["policy_code"] = policy["policy_code"]
        row["required_roles"] = required_roles
        return row


async def get_case_timeline(*, building_id: str, case_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: get_case_timeline
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    case_uuid = _parse_uuid(case_id, label="case_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _get_case_for_access(session, ctx, case_uuid, mode="view")
        result = await session.execute(
            text(
                """
                SELECT * FROM (
                    SELECT
                        'event' AS entry_type,
                        ce.created_at AS happened_at,
                        ce.event_type AS title,
                        ce.actor_user_id::text AS actor_user_id,
                        actor.full_name AS actor_name,
                        ce.event_payload AS payload
                    FROM ops.case_events ce
                    LEFT JOIN core.users actor ON actor.user_id = ce.actor_user_id
                    WHERE ce.case_id = :case_id

                    UNION ALL

                    SELECT
                        'status_change' AS entry_type,
                        tsh.changed_at AS happened_at,
                        COALESCE(tsh.from_status, 'new') || ' -> ' || tsh.to_status AS title,
                        tsh.changed_by::text AS actor_user_id,
                        actor.full_name AS actor_name,
                        jsonb_build_object(
                            'from_status', tsh.from_status,
                            'to_status', tsh.to_status,
                            'reason', tsh.change_reason
                        ) AS payload
                    FROM ops.task_status_history tsh
                    LEFT JOIN core.users actor ON actor.user_id = tsh.changed_by
                    WHERE tsh.case_id = :case_id

                    UNION ALL

                    SELECT
                        'comment' AS entry_type,
                        tc.created_at AS happened_at,
                        CASE WHEN tc.is_internal THEN 'Internal comment' ELSE 'Comment' END AS title,
                        tc.created_by::text AS actor_user_id,
                        actor.full_name AS actor_name,
                        jsonb_build_object(
                            'body', tc.body,
                            'is_internal', tc.is_internal,
                            'pii_classification', tc.pii_classification
                        ) AS payload
                    FROM ops.task_comments tc
                    LEFT JOIN core.users actor ON actor.user_id = tc.created_by
                    WHERE tc.case_id = :case_id

                    UNION ALL

                    SELECT
                        'assignment' AS entry_type,
                        ta.created_at AS happened_at,
                        'Assignment created' AS title,
                        ta.assigned_by::text AS actor_user_id,
                        actor.full_name AS actor_name,
                        jsonb_build_object(
                            'assigned_to', ta.assigned_to::text,
                            'status', ta.status,
                            'due_at', ta.due_at
                        ) AS payload
                    FROM ops.task_assignments ta
                    LEFT JOIN core.users actor ON actor.user_id = ta.assigned_by
                    WHERE ta.case_id = :case_id

                    UNION ALL

                    SELECT
                        'approval_request' AS entry_type,
                        ar.created_at AS happened_at,
                        'Approval requested' AS title,
                        ar.requested_by::text AS actor_user_id,
                        actor.full_name AS actor_name,
                        jsonb_build_object(
                            'approval_request_id', ar.approval_request_id::text,
                            'status', ar.status
                        ) AS payload
                    FROM core.approval_requests ar
                    LEFT JOIN core.users actor ON actor.user_id = ar.requested_by
                    WHERE ar.entity_type = 'ops_case'
                      AND ar.entity_id = :case_id
                ) timeline
                ORDER BY happened_at DESC
                """
            ),
            {"case_id": case_uuid},
        )
        rows = []
        for row in result.fetchall():
            data = _row_to_dict(row)
            data["actor_user_id"] = _uuid_text(data.get("actor_user_id"))
            data["payload"] = data.get("payload") or {}
            rows.append(data)
        return rows


async def get_case_audit(*, building_id: str, case_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: get_case_audit
    Path: backend/services/ops_case_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if not ctx.is_manager:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can view case audit events.")

    case_uuid = _parse_uuid(case_id, label="case_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _get_case_for_access(session, ctx, case_uuid, mode="view")
        result = await session.execute(
            text(
                """
                SELECT ae.audit_event_id::text AS audit_event_id,
                       ae.action,
                       ae.actor_user_id::text AS actor_user_id,
                       actor.full_name AS actor_name,
                       ae.ip_address::text AS ip_address,
                       ae.user_agent,
                       ae.event_payload,
                       ae.created_at
                FROM core.audit_events ae
                LEFT JOIN core.users actor ON actor.user_id = ae.actor_user_id
                WHERE ae.entity_type = 'ops_case'
                  AND ae.entity_id = :case_id
                  AND ae.scheme_id = :scheme_id
                ORDER BY ae.created_at DESC
                """
            ),
            {"case_id": case_uuid, "scheme_id": ctx.scheme_id},
        )
        rows = []
        for row in result.fetchall():
            data = _row_to_dict(row)
            data["actor_user_id"] = _uuid_text(data.get("actor_user_id"))
            data["event_payload"] = data.get("event_payload") or {}
            rows.append(data)
        return rows
