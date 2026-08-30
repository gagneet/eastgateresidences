"""PostgreSQL-backed communications campaigns, newsletters, and tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text

from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant
from models.communications_campaigns import (
    CommunicationAcknowledgementCreate,
    CommunicationApprovalRequest,
    CommunicationCampaignCreate,
    CommunicationCampaignUpdate,
    CommunicationDeliveryEventCreate,
    CommunicationDraftCreate,
    CommunicationScheduleRequest,
    NewsletterCreate,
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
_ALWAYS_REVIEW_CAMPAIGN_TYPES = {"compliance_notice", "agm_notice"}


@dataclass
class _CommunicationsContext:
    tenant_id: str
    scheme_id: str
    role: str
    actor_user_id: str | None
    actor_email: str | None

    @property
    def is_manager(self) -> bool:
        """Generated function header.

        Function: _CommunicationsContext.is_manager
        Path: backend/services/communications_campaigns_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self.role in _MANAGER_ROLES


_NEWSLETTER_SELECT = """
    SELECT
        n.id::text AS newsletter_id,
        n.title,
        n.subject,
        n.summary,
        n.status,
        n.scheduled_for,
        n.sent_at,
        n.approval_required,
        n.approval_request_id::text AS approval_request_id,
        ar.status AS approval_status,
        n.created_by::text AS created_by,
        n.updated_by::text AS updated_by,
        n.created_at,
        n.updated_at,
        COALESCE(section_counts.section_count, 0) AS section_count,
        COALESCE(recipient_counts.recipient_count, 0) AS recipient_count,
        COALESCE(ack_counts.acknowledgement_count, 0) AS acknowledgement_count,
        COALESCE(delivery_counts.delivery_event_count, 0) AS delivery_event_count
    FROM communications.newsletters n
    LEFT JOIN core.approval_requests ar ON ar.approval_request_id = n.approval_request_id
    LEFT JOIN (
        SELECT newsletter_id, COUNT(*)::int AS section_count
        FROM communications.newsletter_sections
        GROUP BY newsletter_id
    ) section_counts ON section_counts.newsletter_id = n.id
    LEFT JOIN (
        SELECT newsletter_id, COUNT(*)::int AS recipient_count
        FROM communications.newsletter_recipients
        GROUP BY newsletter_id
    ) recipient_counts ON recipient_counts.newsletter_id = n.id
    LEFT JOIN (
        SELECT newsletter_id, COUNT(*)::int AS acknowledgement_count
        FROM communications.communication_acknowledgements
        WHERE newsletter_id IS NOT NULL
        GROUP BY newsletter_id
    ) ack_counts ON ack_counts.newsletter_id = n.id
    LEFT JOIN (
        SELECT newsletter_id, COUNT(*)::int AS delivery_event_count
        FROM communications.communication_delivery_events
        WHERE newsletter_id IS NOT NULL
        GROUP BY newsletter_id
    ) delivery_counts ON delivery_counts.newsletter_id = n.id
"""

_CAMPAIGN_SELECT = """
    SELECT
        c.id::text AS campaign_id,
        c.campaign_type,
        c.title,
        c.subject,
        c.body,
        c.status,
        c.audience_scope,
        c.scheduled_for,
        c.sent_at,
        c.approval_required,
        c.approval_request_id::text AS approval_request_id,
        ar.status AS approval_status,
        c.created_by::text AS created_by,
        c.updated_by::text AS updated_by,
        c.created_at,
        c.updated_at,
        COALESCE(segment_counts.audience_segment_count, 0) AS audience_segment_count,
        COALESCE(ack_counts.acknowledgement_count, 0) AS acknowledgement_count,
        COALESCE(delivery_counts.delivery_event_count, 0) AS delivery_event_count
    FROM communications.communication_campaigns c
    LEFT JOIN core.approval_requests ar ON ar.approval_request_id = c.approval_request_id
    LEFT JOIN (
        SELECT campaign_id, COUNT(*)::int AS audience_segment_count
        FROM communications.campaign_audience_segments
        GROUP BY campaign_id
    ) segment_counts ON segment_counts.campaign_id = c.id
    LEFT JOIN (
        SELECT campaign_id, COUNT(*)::int AS acknowledgement_count
        FROM communications.communication_acknowledgements
        WHERE campaign_id IS NOT NULL
        GROUP BY campaign_id
    ) ack_counts ON ack_counts.campaign_id = c.id
    LEFT JOIN (
        SELECT campaign_id, COUNT(*)::int AS delivery_event_count
        FROM communications.communication_delivery_events
        WHERE campaign_id IS NOT NULL
        GROUP BY campaign_id
    ) delivery_counts ON delivery_counts.campaign_id = c.id
"""


def _row_to_dict(row) -> dict[str, Any]:
    """Generated function header.

    Function: _row_to_dict
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _string(value: Any) -> str | None:
    """Generated function header.

    Function: _string
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(value) if value is not None else None


def _json_dump(payload: dict[str, Any] | None) -> str:
    """Generated function header.

    Function: _json_dump
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return json.dumps(payload or {}, default=str)


def _parse_uuid(value: str, *, label: str) -> UUID:
    """Generated function header.

    Function: _parse_uuid
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {label}. Expected UUID.",
        ) from exc


def _require_manager(ctx: _CommunicationsContext) -> None:
    """Generated function header.

    Function: _require_manager
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not ctx.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can access PostgreSQL-backed communications workflows.",
        )


def _require_policy_roles(policy: dict[str, Any] | None, *, detail: str) -> tuple[dict[str, Any], list[str]]:
    """Generated function header.

    Function: _require_policy_roles
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not policy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    required_roles = list(policy.get("required_roles") or [])
    if not required_roles:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return policy, required_roles


def _campaign_requires_human_approval(campaign: dict[str, Any]) -> bool:
    """Generated function header.

    Function: _campaign_requires_human_approval
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return bool(campaign.get("approval_required")) or campaign.get("campaign_type") in _ALWAYS_REVIEW_CAMPAIGN_TYPES


def _campaign_edit_invalidates_approval(campaign: dict[str, Any], updates: dict[str, Any]) -> bool:
    """Generated function header.

    Function: _campaign_edit_invalidates_approval
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if campaign.get("status") not in {"in_review", "approved"}:
        return False
    approval_relevant_fields = {
        "campaign_type",
        "title",
        "subject",
        "body",
        "audience_scope",
        "approval_required",
        "audience_segments",
    }
    return any(field in approval_relevant_fields for field in updates)


def _normalise_newsletter(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_newsletter
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("newsletter_id", "approval_request_id", "created_by", "updated_by"):
        data[key] = _string(data.get(key))
    for key in ("section_count", "recipient_count", "acknowledgement_count", "delivery_event_count"):
        data[key] = int(data.get(key) or 0)
    return data


def _normalise_campaign(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("campaign_id", "approval_request_id", "created_by", "updated_by"):
        data[key] = _string(data.get(key))
    for key in ("audience_segment_count", "acknowledgement_count", "delivery_event_count"):
        data[key] = int(data.get(key) or 0)
    return data


def _normalise_draft(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_draft
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("draft_id", "newsletter_id", "campaign_id", "created_by", "updated_by"):
        data[key] = _string(data.get(key))
    return data


def _normalise_section(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_section
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    data["section_id"] = _string(data.get("section_id"))
    data["sort_order"] = int(data.get("sort_order") or 0)
    return data


def _normalise_recipient(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_recipient
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("recipient_id", "party_id"):
        data[key] = _string(data.get(key))
    return data


def _normalise_segment(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_segment
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    data["segment_id"] = _string(data.get("segment_id"))
    data["segment_definition"] = dict(data.get("segment_definition") or {})
    return data


def _normalise_delivery_event(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_delivery_event
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("event_id", "draft_id", "newsletter_id", "campaign_id", "recipient_party_id"):
        data[key] = _string(data.get(key))
    data["event_payload"] = dict(data.get("event_payload") or {})
    return data


def _normalise_ack(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_ack
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("acknowledgement_id", "newsletter_id", "campaign_id", "party_id"):
        data[key] = _string(data.get(key))
    return data


async def _resolve_context(building_id: str, current_user: dict) -> _CommunicationsContext:
    """Generated function header.

    Function: _resolve_context
    Path: backend/services/communications_campaigns_service.py

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
    return _CommunicationsContext(
        tenant_id=str(scheme["tenant_id"]),
        scheme_id=str(scheme["scheme_id"]),
        role=role,
        actor_user_id=actor_user_id,
        actor_email=current_user.get("email"),
    )


async def _insert_audit_event(
    session,
    ctx: _CommunicationsContext,
    *,
    entity_type: str,
    entity_id: UUID,
    action: str,
    payload: dict[str, Any] | None,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """Generated function header.

    Function: _insert_audit_event
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            """
            INSERT INTO core.audit_events
                (tenant_id, scheme_id, entity_type, entity_id, action, actor_user_id,
                 ip_address, user_agent, event_payload)
            VALUES
                (:tenant_id, :scheme_id, :entity_type, :entity_id, :action, :actor_user_id,
                 :ip_address, :user_agent, CAST(:payload AS jsonb))
            """
        ),
        {
            "tenant_id": ctx.tenant_id,
            "scheme_id": ctx.scheme_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "actor_user_id": ctx.actor_user_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "payload": _json_dump(payload),
        },
    )


async def _fetch_policy(session, ctx: _CommunicationsContext, policy_code: str | None) -> dict[str, Any] | None:
    """Generated function header.

    Function: _fetch_policy
    Path: backend/services/communications_campaigns_service.py

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


async def _fetch_newsletter(session, ctx: _CommunicationsContext, newsletter_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_newsletter
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            f"""
            {_NEWSLETTER_SELECT}
            WHERE n.scheme_id = :scheme_id
              AND n.id = :newsletter_id
              AND n.deleted_at IS NULL
            """
        ),
        {"scheme_id": ctx.scheme_id, "newsletter_id": newsletter_uuid},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Newsletter not found.")
    return _normalise_newsletter(row)


async def _fetch_campaign(session, ctx: _CommunicationsContext, campaign_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            f"""
            {_CAMPAIGN_SELECT}
            WHERE c.scheme_id = :scheme_id
              AND c.id = :campaign_id
              AND c.deleted_at IS NULL
            """
        ),
        {"scheme_id": ctx.scheme_id, "campaign_id": campaign_uuid},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
    return _normalise_campaign(row)


async def _fetch_campaign_detail(session, ctx: _CommunicationsContext, campaign_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_campaign_detail
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    campaign = await _fetch_campaign(session, ctx, campaign_uuid)
    campaign["audience_segments"] = await _fetch_campaign_segments(session, campaign_uuid)
    return campaign


async def _fetch_newsletter_sections(session, newsletter_uuid: UUID) -> list[dict[str, Any]]:
    """Generated function header.

    Function: _fetch_newsletter_sections
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT id::text AS section_id, section_type, title, body, sort_order
            FROM communications.newsletter_sections
            WHERE newsletter_id = :newsletter_id
            ORDER BY sort_order ASC, created_at ASC
            """
        ),
        {"newsletter_id": newsletter_uuid},
    )
    return [_normalise_section(row) for row in result.fetchall()]


async def _fetch_newsletter_recipients(session, newsletter_uuid: UUID) -> list[dict[str, Any]]:
    """Generated function header.

    Function: _fetch_newsletter_recipients
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT id::text AS recipient_id,
                   party_id::text AS party_id,
                   recipient_email,
                   recipient_type,
                   delivery_status,
                   delivered_at,
                   opened_at,
                   acknowledged_at
            FROM communications.newsletter_recipients
            WHERE newsletter_id = :newsletter_id
            ORDER BY created_at ASC
            """
        ),
        {"newsletter_id": newsletter_uuid},
    )
    return [_normalise_recipient(row) for row in result.fetchall()]


async def _fetch_campaign_segments(session, campaign_uuid: UUID) -> list[dict[str, Any]]:
    """Generated function header.

    Function: _fetch_campaign_segments
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT id::text AS segment_id,
                   segment_type,
                   segment_definition,
                   estimated_recipient_count
            FROM communications.campaign_audience_segments
            WHERE campaign_id = :campaign_id
            ORDER BY created_at ASC
            """
        ),
        {"campaign_id": campaign_uuid},
    )
    return [_normalise_segment(row) for row in result.fetchall()]


async def _create_approval_request(
    session,
    ctx: _CommunicationsContext,
    *,
    entity_type: str,
    entity_id: UUID,
    link_payload: dict[str, Any],
    link_column: str,
    target_table: str,
    target_column: str,
    policy_request: CommunicationApprovalRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: _create_approval_request
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    policy = await _fetch_policy(session, ctx, policy_request.policy_code)
    policy, required_roles = _require_policy_roles(
        policy,
        detail="Approval policy is missing or has no approver roles for communications campaigns.",
    )
    result = await session.execute(
        text(
            """
            INSERT INTO core.approval_requests
                (tenant_id, scheme_id, entity_type, entity_id, approval_policy_id, status, requested_by)
            VALUES
                (:tenant_id, :scheme_id, :entity_type, :entity_id, :approval_policy_id, 'pending', :requested_by)
            RETURNING approval_request_id::text AS approval_request_id,
                      status,
                      requested_by::text AS requested_by,
                      created_at AS requested_at
            """
        ),
        {
            "tenant_id": ctx.tenant_id,
            "scheme_id": ctx.scheme_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "approval_policy_id": policy["approval_policy_id"],
            "requested_by": ctx.actor_user_id,
        },
    )
    approval_row = _row_to_dict(result.one())
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
                "approval_request_id": approval_row["approval_request_id"],
                "sequence_no": index,
                "required_role": required_role,
            },
        )

    await session.execute(
        text(
            f"""
            INSERT INTO communications.communication_approval_links
                (tenant_id, scheme_id, {link_column}, approval_request_id, approval_kind, source_correlation_id)
            VALUES
                (:tenant_id, :scheme_id, :linked_entity_id, :approval_request_id, :approval_kind, :correlation_id)
            """
        ),
        {
            "tenant_id": ctx.tenant_id,
            "scheme_id": ctx.scheme_id,
            "linked_entity_id": entity_id,
            "approval_request_id": approval_row["approval_request_id"],
            "approval_kind": link_payload["approval_kind"],
            "correlation_id": correlation_id,
        },
    )
    await session.execute(
        text(
            f"""
            UPDATE communications.{target_table}
            SET approval_required = TRUE,
                approval_request_id = :approval_request_id,
                status = 'in_review',
                updated_by = :updated_by,
                updated_at = now()
            WHERE {target_column} = :entity_id
            """
        ),
        {
            "approval_request_id": approval_row["approval_request_id"],
            "updated_by": ctx.actor_user_id,
            "entity_id": entity_id,
        },
    )
    await _insert_audit_event(
        session,
        ctx,
        entity_type=entity_type,
        entity_id=entity_id,
        action=f"{entity_type}.approval_requested",
        payload={
            "approval_request_id": approval_row["approval_request_id"],
            "policy_code": policy["policy_code"],
            "required_roles": required_roles,
            "note": policy_request.note,
            "correlation_id": correlation_id,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    approval_row["policy_code"] = policy["policy_code"]
    approval_row["required_roles"] = required_roles
    return approval_row


def _delivery_status_for_event(event_type: str) -> str | None:
    """Generated function header.

    Function: _delivery_status_for_event
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mapping = {
        "queued": "queued",
        "sent": "sent",
        "delivered": "delivered",
        "bounced": "bounced",
        "opened": "opened",
        "failed": "failed",
    }
    return mapping.get(event_type.lower())


async def list_drafts(*, building_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_drafts
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT id::text AS draft_id,
                       newsletter_id::text AS newsletter_id,
                       campaign_id::text AS campaign_id,
                       title,
                       subject,
                       body,
                       draft_kind,
                       is_template,
                       status,
                       risk_level,
                       pii_redacted,
                       created_by::text AS created_by,
                       updated_by::text AS updated_by,
                       created_at,
                       updated_at
                FROM communications.communication_drafts
                WHERE scheme_id = :scheme_id
                  AND deleted_at IS NULL
                ORDER BY created_at DESC
                """
            ),
            {"scheme_id": ctx.scheme_id},
        )
        return [_normalise_draft(row) for row in result.fetchall()]


async def create_draft(
    *,
    building_id: str,
    current_user: dict,
    payload: CommunicationDraftCreate,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: create_draft
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    values = payload.model_dump()

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                INSERT INTO communications.communication_drafts
                    (tenant_id, scheme_id, newsletter_id, campaign_id, title, subject, body,
                     draft_kind, is_template, risk_level, pii_redacted, created_by, updated_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :newsletter_id, :campaign_id, :title, :subject, :body,
                     :draft_kind, :is_template, :risk_level, :pii_redacted, :created_by, :updated_by,
                     :correlation_id)
                RETURNING id::text AS draft_id,
                          newsletter_id::text AS newsletter_id,
                          campaign_id::text AS campaign_id,
                          title,
                          subject,
                          body,
                          draft_kind,
                          is_template,
                          status,
                          risk_level,
                          pii_redacted,
                          created_by::text AS created_by,
                          updated_by::text AS updated_by,
                          created_at,
                          updated_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "newsletter_id": _parse_uuid(values["newsletter_id"], label="newsletter_id") if values.get("newsletter_id") else None,
                "campaign_id": _parse_uuid(values["campaign_id"], label="campaign_id") if values.get("campaign_id") else None,
                "title": values["title"].strip(),
                "subject": values.get("subject"),
                "body": values["body"],
                "draft_kind": values["draft_kind"],
                "is_template": values["is_template"],
                "risk_level": values["risk_level"],
                "pii_redacted": values["pii_redacted"],
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        row = result.one()
        draft = _normalise_draft(row)
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_draft",
            entity_id=_parse_uuid(draft["draft_id"], label="draft_id"),
            action="communications.draft.created",
            payload={"draft_kind": draft["draft_kind"], "risk_level": draft["risk_level"], "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return draft


async def get_draft(*, building_id: str, draft_id: str, current_user: dict) -> dict[str, Any]:
    """Generated function header.

    Function: get_draft
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    draft_uuid = _parse_uuid(draft_id, label="draft_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT id::text AS draft_id,
                       newsletter_id::text AS newsletter_id,
                       campaign_id::text AS campaign_id,
                       title,
                       subject,
                       body,
                       draft_kind,
                       is_template,
                       status,
                       risk_level,
                       pii_redacted,
                       created_by::text AS created_by,
                       updated_by::text AS updated_by,
                       created_at,
                       updated_at
                FROM communications.communication_drafts
                WHERE scheme_id = :scheme_id
                  AND id = :draft_id
                  AND deleted_at IS NULL
                """
            ),
            {"scheme_id": ctx.scheme_id, "draft_id": draft_uuid},
        )
        row = result.first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found.")
        return _normalise_draft(row)


async def list_newsletters(*, building_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_newsletters
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                {_NEWSLETTER_SELECT}
                WHERE n.scheme_id = :scheme_id
                  AND n.deleted_at IS NULL
                ORDER BY COALESCE(n.scheduled_for, n.created_at) DESC, n.created_at DESC
                """
            ),
            {"scheme_id": ctx.scheme_id},
        )
        return [_normalise_newsletter(row) for row in result.fetchall()]


async def create_newsletter(
    *,
    building_id: str,
    current_user: dict,
    payload: NewsletterCreate,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: create_newsletter
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    values = payload.model_dump()

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                INSERT INTO communications.newsletters
                    (tenant_id, scheme_id, title, subject, summary, approval_required,
                     created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :title, :subject, :summary, :approval_required,
                     :created_by, :updated_by, :correlation_id)
                RETURNING id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "title": values["title"].strip(),
                "subject": values["subject"].strip(),
                "summary": values.get("summary"),
                "approval_required": values["approval_required"],
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        newsletter_uuid = result.scalar_one()
        for index, section in enumerate(values["sections"], start=1):
            sort_order = section.get("sort_order")
            await session.execute(
                text(
                    """
                    INSERT INTO communications.newsletter_sections
                        (tenant_id, scheme_id, newsletter_id, section_type, title, body, sort_order, source_correlation_id)
                    VALUES
                        (:tenant_id, :scheme_id, :newsletter_id, :section_type, :title, :body, :sort_order, :correlation_id)
                    """
                ),
                {
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "newsletter_id": newsletter_uuid,
                    "section_type": section["section_type"],
                    "title": section.get("title"),
                    "body": section["body"],
                    "sort_order": sort_order if sort_order is not None else index - 1,
                    "correlation_id": correlation_id,
                },
            )
        for recipient in values["recipients"]:
            await session.execute(
                text(
                    """
                    INSERT INTO communications.newsletter_recipients
                        (tenant_id, scheme_id, newsletter_id, party_id, recipient_email, recipient_type, source_correlation_id)
                    VALUES
                        (:tenant_id, :scheme_id, :newsletter_id, :party_id, :recipient_email, :recipient_type, :correlation_id)
                    """
                ),
                {
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "newsletter_id": newsletter_uuid,
                    "party_id": _parse_uuid(recipient["party_id"], label="party_id") if recipient.get("party_id") else None,
                    "recipient_email": recipient["recipient_email"].strip().lower(),
                    "recipient_type": recipient["recipient_type"],
                    "correlation_id": correlation_id,
                },
            )

        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_newsletter",
            entity_id=newsletter_uuid,
            action="communications.newsletter.created",
            payload={
                "approval_required": values["approval_required"],
                "section_count": len(values["sections"]),
                "recipient_count": len(values["recipients"]),
                "correlation_id": correlation_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        newsletter = await _fetch_newsletter(session, ctx, newsletter_uuid)
        newsletter["sections"] = await _fetch_newsletter_sections(session, newsletter_uuid)
        newsletter["recipients"] = await _fetch_newsletter_recipients(session, newsletter_uuid)
        return newsletter


async def get_newsletter(*, building_id: str, newsletter_id: str, current_user: dict) -> dict[str, Any]:
    """Generated function header.

    Function: get_newsletter
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    newsletter_uuid = _parse_uuid(newsletter_id, label="newsletter_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        newsletter = await _fetch_newsletter(session, ctx, newsletter_uuid)
        newsletter["sections"] = await _fetch_newsletter_sections(session, newsletter_uuid)
        newsletter["recipients"] = await _fetch_newsletter_recipients(session, newsletter_uuid)
        return newsletter


async def request_newsletter_approval(
    *,
    building_id: str,
    newsletter_id: str,
    current_user: dict,
    payload: CommunicationApprovalRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: request_newsletter_approval
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    newsletter_uuid = _parse_uuid(newsletter_id, label="newsletter_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        newsletter = await _fetch_newsletter(session, ctx, newsletter_uuid)
        if newsletter.get("approval_request_id"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Newsletter already has an approval request.")
        return await _create_approval_request(
            session,
            ctx,
            entity_type="communications_newsletter",
            entity_id=newsletter_uuid,
            link_payload={"approval_kind": "newsletter_send"},
            link_column="newsletter_id",
            target_table="newsletters",
            target_column="id",
            policy_request=payload,
            correlation_id=correlation_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )


async def schedule_newsletter(
    *,
    building_id: str,
    newsletter_id: str,
    current_user: dict,
    payload: CommunicationScheduleRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: schedule_newsletter
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    newsletter_uuid = _parse_uuid(newsletter_id, label="newsletter_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        newsletter = await _fetch_newsletter(session, ctx, newsletter_uuid)
        if newsletter["status"] in {"sent", "cancelled", "archived"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Newsletter can no longer be scheduled.")
        if newsletter["approval_required"] and newsletter.get("approval_status") != "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Newsletter requires approved human review before scheduling.",
            )
        await session.execute(
            text(
                """
                UPDATE communications.newsletters
                SET status = 'scheduled',
                    scheduled_for = :scheduled_for,
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :newsletter_id
                """
            ),
            {
                "scheduled_for": payload.scheduled_for,
                "updated_by": ctx.actor_user_id,
                "newsletter_id": newsletter_uuid,
            },
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_newsletter",
            entity_id=newsletter_uuid,
            action="communications.newsletter.scheduled",
            payload={"scheduled_for": payload.scheduled_for, "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        scheduled = await _fetch_newsletter(session, ctx, newsletter_uuid)
        scheduled["sections"] = await _fetch_newsletter_sections(session, newsletter_uuid)
        scheduled["recipients"] = await _fetch_newsletter_recipients(session, newsletter_uuid)
        return scheduled


async def list_newsletter_delivery_events(*, building_id: str, newsletter_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_newsletter_delivery_events
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    newsletter_uuid = _parse_uuid(newsletter_id, label="newsletter_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_newsletter(session, ctx, newsletter_uuid)
        result = await session.execute(
            text(
                """
                SELECT id::text AS event_id,
                       draft_id::text AS draft_id,
                       newsletter_id::text AS newsletter_id,
                       campaign_id::text AS campaign_id,
                       recipient_party_id::text AS recipient_party_id,
                       recipient_email,
                       channel,
                       provider_message_id,
                       event_type,
                       occurred_at,
                       event_payload
                FROM communications.communication_delivery_events
                WHERE newsletter_id = :newsletter_id
                ORDER BY occurred_at DESC, created_at DESC
                """
            ),
            {"newsletter_id": newsletter_uuid},
        )
        return [_normalise_delivery_event(row) for row in result.fetchall()]


async def record_newsletter_delivery_event(
    *,
    building_id: str,
    newsletter_id: str,
    current_user: dict,
    payload: CommunicationDeliveryEventCreate,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: record_newsletter_delivery_event
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    newsletter_uuid = _parse_uuid(newsletter_id, label="newsletter_id")
    values = payload.model_dump()

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_newsletter(session, ctx, newsletter_uuid)
        result = await session.execute(
            text(
                """
                INSERT INTO communications.communication_delivery_events
                    (tenant_id, scheme_id, newsletter_id, recipient_party_id, recipient_email, channel,
                     provider_message_id, event_type, occurred_at, event_payload, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :newsletter_id, :recipient_party_id, :recipient_email, :channel,
                     :provider_message_id, :event_type, :occurred_at, CAST(:event_payload AS jsonb), :correlation_id)
                RETURNING id::text AS event_id,
                          draft_id::text AS draft_id,
                          newsletter_id::text AS newsletter_id,
                          campaign_id::text AS campaign_id,
                          recipient_party_id::text AS recipient_party_id,
                          recipient_email,
                          channel,
                          provider_message_id,
                          event_type,
                          occurred_at,
                          event_payload
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "newsletter_id": newsletter_uuid,
                "recipient_party_id": _parse_uuid(values["recipient_party_id"], label="recipient_party_id") if values.get("recipient_party_id") else None,
                "recipient_email": values.get("recipient_email"),
                "channel": values["channel"],
                "provider_message_id": values.get("provider_message_id"),
                "event_type": values["event_type"],
                "occurred_at": values.get("occurred_at"),
                "event_payload": _json_dump(values.get("event_payload")),
                "correlation_id": correlation_id,
            },
        )
        delivery_event = _normalise_delivery_event(result.one())
        delivery_status = _delivery_status_for_event(values["event_type"])
        if delivery_status and (values.get("recipient_party_id") or values.get("recipient_email")):
            await session.execute(
                text(
                    """
                    UPDATE communications.newsletter_recipients
                    SET delivery_status = :delivery_status,
                        delivered_at = CASE
                            WHEN :delivery_status = 'delivered' THEN COALESCE(:occurred_at, now())
                            ELSE delivered_at
                        END,
                        opened_at = CASE
                            WHEN :delivery_status = 'opened' THEN COALESCE(:occurred_at, now())
                            ELSE opened_at
                        END,
                        updated_at = now()
                    WHERE newsletter_id = :newsletter_id
                      AND (
                        (:recipient_party_id IS NOT NULL AND party_id = :recipient_party_id)
                        OR (
                            :recipient_party_id IS NULL
                            AND :recipient_email IS NOT NULL
                            AND lower(recipient_email) = lower(:recipient_email)
                        )
                      )
                    """
                ),
                {
                    "delivery_status": delivery_status,
                    "occurred_at": values.get("occurred_at"),
                    "newsletter_id": newsletter_uuid,
                    "recipient_party_id": _parse_uuid(values["recipient_party_id"], label="recipient_party_id") if values.get("recipient_party_id") else None,
                    "recipient_email": values.get("recipient_email"),
                },
            )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_newsletter",
            entity_id=newsletter_uuid,
            action="communications.newsletter.delivery_event_recorded",
            payload={"event_type": values["event_type"], "channel": values["channel"], "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return delivery_event


async def list_newsletter_acknowledgements(*, building_id: str, newsletter_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_newsletter_acknowledgements
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    newsletter_uuid = _parse_uuid(newsletter_id, label="newsletter_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_newsletter(session, ctx, newsletter_uuid)
        result = await session.execute(
            text(
                """
                SELECT id::text AS acknowledgement_id,
                       newsletter_id::text AS newsletter_id,
                       campaign_id::text AS campaign_id,
                       party_id::text AS party_id,
                       acknowledgement_type,
                       response_text,
                       acknowledged_at
                FROM communications.communication_acknowledgements
                WHERE newsletter_id = :newsletter_id
                ORDER BY acknowledged_at DESC, created_at DESC
                """
            ),
            {"newsletter_id": newsletter_uuid},
        )
        return [_normalise_ack(row) for row in result.fetchall()]


async def acknowledge_newsletter(
    *,
    building_id: str,
    newsletter_id: str,
    current_user: dict,
    payload: CommunicationAcknowledgementCreate,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: acknowledge_newsletter
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    newsletter_uuid = _parse_uuid(newsletter_id, label="newsletter_id")
    party_uuid = _parse_uuid(payload.party_id, label="party_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_newsletter(session, ctx, newsletter_uuid)
        result = await session.execute(
            text(
                """
                INSERT INTO communications.communication_acknowledgements
                    (tenant_id, scheme_id, newsletter_id, party_id, acknowledgement_type, response_text, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :newsletter_id, :party_id, :acknowledgement_type, :response_text, :correlation_id)
                RETURNING id::text AS acknowledgement_id,
                          newsletter_id::text AS newsletter_id,
                          campaign_id::text AS campaign_id,
                          party_id::text AS party_id,
                          acknowledgement_type,
                          response_text,
                          acknowledged_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "newsletter_id": newsletter_uuid,
                "party_id": party_uuid,
                "acknowledgement_type": payload.acknowledgement_type,
                "response_text": payload.response_text,
                "correlation_id": correlation_id,
            },
        )
        ack = _normalise_ack(result.one())
        await session.execute(
            text(
                """
                UPDATE communications.newsletter_recipients
                SET acknowledged_at = :acknowledged_at,
                    delivery_status = 'acknowledged',
                    updated_at = now()
                WHERE newsletter_id = :newsletter_id
                  AND party_id = :party_id
                """
            ),
            {
                "acknowledged_at": ack["acknowledged_at"],
                "newsletter_id": newsletter_uuid,
                "party_id": party_uuid,
            },
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_newsletter",
            entity_id=newsletter_uuid,
            action="communications.newsletter.acknowledged",
            payload={"party_id": payload.party_id, "acknowledgement_type": payload.acknowledgement_type, "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return ack


async def list_campaigns(*, building_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_campaigns
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                {_CAMPAIGN_SELECT}
                WHERE c.scheme_id = :scheme_id
                  AND c.deleted_at IS NULL
                ORDER BY COALESCE(c.scheduled_for, c.created_at) DESC, c.created_at DESC
                """
            ),
            {"scheme_id": ctx.scheme_id},
        )
        return [_normalise_campaign(row) for row in result.fetchall()]


async def create_campaign(
    *,
    building_id: str,
    current_user: dict,
    payload: CommunicationCampaignCreate,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: create_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    values = payload.model_dump()

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                INSERT INTO communications.communication_campaigns
                    (tenant_id, scheme_id, campaign_type, title, subject, body, audience_scope,
                     approval_required, created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :campaign_type, :title, :subject, :body, :audience_scope,
                     :approval_required, :created_by, :updated_by, :correlation_id)
                RETURNING id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "campaign_type": values["campaign_type"],
                "title": values["title"].strip(),
                "subject": values.get("subject"),
                "body": values.get("body"),
                "audience_scope": values["audience_scope"],
                "approval_required": values["approval_required"],
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        campaign_uuid = result.scalar_one()
        for segment in values["audience_segments"]:
            await session.execute(
                text(
                    """
                    INSERT INTO communications.campaign_audience_segments
                        (tenant_id, scheme_id, campaign_id, segment_type, segment_definition,
                         estimated_recipient_count, source_correlation_id)
                    VALUES
                        (:tenant_id, :scheme_id, :campaign_id, :segment_type, CAST(:segment_definition AS jsonb),
                         :estimated_recipient_count, :correlation_id)
                    """
                ),
                {
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "campaign_id": campaign_uuid,
                    "segment_type": segment["segment_type"],
                    "segment_definition": _json_dump(segment.get("segment_definition")),
                    "estimated_recipient_count": segment.get("estimated_recipient_count"),
                    "correlation_id": correlation_id,
                },
            )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_campaign",
            entity_id=campaign_uuid,
            action="communications.campaign.created",
            payload={
                "campaign_type": values["campaign_type"],
                "audience_scope": values["audience_scope"],
                "audience_segment_count": len(values["audience_segments"]),
                "correlation_id": correlation_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_campaign_detail(session, ctx, campaign_uuid)


async def get_campaign(*, building_id: str, campaign_id: str, current_user: dict) -> dict[str, Any]:
    """Generated function header.

    Function: get_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        return await _fetch_campaign_detail(session, ctx, campaign_uuid)


async def update_campaign(
    *,
    building_id: str,
    campaign_id: str,
    current_user: dict,
    payload: CommunicationCampaignUpdate,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: update_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No campaign fields were provided.")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        campaign = await _fetch_campaign(session, ctx, campaign_uuid)
        if campaign["status"] not in {"draft", "in_review", "approved"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only draft or reviewable campaigns can be edited.")
        approval_invalidated = _campaign_edit_invalidates_approval(campaign, values)

        update_sets: list[str] = ["updated_by = :updated_by", "updated_at = now()"]
        params: dict[str, Any] = {
            "campaign_id": campaign_uuid,
            "scheme_id": ctx.scheme_id,
            "updated_by": ctx.actor_user_id,
        }
        column_map = {
            "campaign_type": "campaign_type",
            "title": "title",
            "subject": "subject",
            "body": "body",
            "audience_scope": "audience_scope",
            "approval_required": "approval_required",
        }
        for field_name, column_name in column_map.items():
            if field_name in values:
                update_sets.append(f"{column_name} = :{field_name}")
                params[field_name] = values[field_name].strip() if field_name in {"title"} and values[field_name] is not None else values[field_name]
        if approval_invalidated:
            update_sets.extend(["approval_request_id = NULL", "status = 'draft'"])

        await session.execute(
            text(
                f"""
                UPDATE communications.communication_campaigns
                SET {", ".join(update_sets)}
                WHERE id = :campaign_id
                  AND scheme_id = :scheme_id
                """
            ),
            params,
        )
        if approval_invalidated and campaign.get("approval_request_id"):
            await session.execute(
                text(
                    """
                    UPDATE core.approval_requests
                    SET status = 'cancelled'
                    WHERE approval_request_id = :approval_request_id
                      AND tenant_id = :tenant_id
                      AND (scheme_id = :scheme_id OR scheme_id IS NULL)
                    """
                ),
                {
                    "approval_request_id": campaign["approval_request_id"],
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                },
            )
        if "audience_segments" in values:
            await session.execute(
                text(
                    """
                    DELETE FROM communications.campaign_audience_segments
                    WHERE campaign_id = :campaign_id
                      AND scheme_id = :scheme_id
                    """
                ),
                {"campaign_id": campaign_uuid, "scheme_id": ctx.scheme_id},
            )
            for segment in values["audience_segments"] or []:
                await session.execute(
                    text(
                        """
                        INSERT INTO communications.campaign_audience_segments
                            (tenant_id, scheme_id, campaign_id, segment_type, segment_definition,
                             estimated_recipient_count, source_correlation_id)
                        VALUES
                            (:tenant_id, :scheme_id, :campaign_id, :segment_type, CAST(:segment_definition AS jsonb),
                             :estimated_recipient_count, :correlation_id)
                        """
                    ),
                    {
                        "tenant_id": ctx.tenant_id,
                        "scheme_id": ctx.scheme_id,
                        "campaign_id": campaign_uuid,
                        "segment_type": segment["segment_type"],
                        "segment_definition": _json_dump(segment.get("segment_definition")),
                        "estimated_recipient_count": segment.get("estimated_recipient_count"),
                        "correlation_id": correlation_id,
                    },
                )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_campaign",
            entity_id=campaign_uuid,
            action="communications.campaign.updated",
            payload={
                "changed_fields": sorted(values.keys()),
                "approval_invalidated": approval_invalidated,
                "previous_approval_request_id": campaign.get("approval_request_id") if approval_invalidated else None,
                "correlation_id": correlation_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_campaign_detail(session, ctx, campaign_uuid)


async def request_campaign_approval(
    *,
    building_id: str,
    campaign_id: str,
    current_user: dict,
    payload: CommunicationApprovalRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: request_campaign_approval
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        campaign = await _fetch_campaign(session, ctx, campaign_uuid)
        if campaign.get("approval_request_id"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Campaign already has an approval request.")
        return await _create_approval_request(
            session,
            ctx,
            entity_type="communications_campaign",
            entity_id=campaign_uuid,
            link_payload={"approval_kind": "campaign_send"},
            link_column="campaign_id",
            target_table="communication_campaigns",
            target_column="id",
            policy_request=payload,
            correlation_id=correlation_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )


async def schedule_campaign(
    *,
    building_id: str,
    campaign_id: str,
    current_user: dict,
    payload: CommunicationScheduleRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: schedule_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        campaign = await _fetch_campaign(session, ctx, campaign_uuid)
        if campaign["status"] in {"sent", "cancelled", "archived"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Campaign can no longer be scheduled.")
        if _campaign_requires_human_approval(campaign) and campaign.get("approval_status") != "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Campaign requires approved human review before scheduling.",
            )
        await session.execute(
            text(
                """
                UPDATE communications.communication_campaigns
                SET status = 'scheduled',
                    scheduled_for = :scheduled_for,
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :campaign_id
                """
            ),
            {
                "scheduled_for": payload.scheduled_for,
                "updated_by": ctx.actor_user_id,
                "campaign_id": campaign_uuid,
            },
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_campaign",
            entity_id=campaign_uuid,
            action="communications.campaign.scheduled",
            payload={"scheduled_for": payload.scheduled_for, "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_campaign_detail(session, ctx, campaign_uuid)


async def preview_campaign(*, building_id: str, campaign_id: str, current_user: dict) -> dict[str, Any]:
    """Generated function header.

    Function: preview_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        campaign = await _fetch_campaign_detail(session, ctx, campaign_uuid)
        # Phase 2 preview is intentionally deterministic and audit-friendly: it
        # shows the persisted draft plus approval expectations, without inventing
        # a separate rendering pipeline that is not yet wired.
        return {
            "campaign_id": campaign["campaign_id"],
            "title": campaign["title"],
            "subject": campaign.get("subject"),
            "body": campaign.get("body"),
            "audience_scope": campaign["audience_scope"],
            "approval_required": campaign["approval_required"],
            "needs_human_approval": _campaign_requires_human_approval(campaign),
            "preview_channels": ["email"],
            "audience_segments": campaign["audience_segments"],
        }


async def send_campaign(
    *,
    building_id: str,
    campaign_id: str,
    current_user: dict,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: send_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        campaign = await _fetch_campaign(session, ctx, campaign_uuid)
        if campaign["status"] not in {"approved", "scheduled"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only approved or scheduled campaigns can be sent.")
        if _campaign_requires_human_approval(campaign) and campaign.get("approval_status") != "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Campaign requires approved human review before sending.",
            )
        result = await session.execute(
            text(
                """
                UPDATE communications.communication_campaigns
                SET status = 'sent',
                    sent_at = now(),
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :campaign_id
                  AND scheme_id = :scheme_id
                RETURNING id::text AS campaign_id, status, sent_at
                """
            ),
            {"campaign_id": campaign_uuid, "scheme_id": ctx.scheme_id, "updated_by": ctx.actor_user_id},
        )
        row = _row_to_dict(result.one())
        # Sending is recorded as an explicit human action here; downstream
        # provider outcomes are appended through the delivery-events endpoint so
        # the audit trail distinguishes "requested send" from provider delivery.
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_campaign",
            entity_id=campaign_uuid,
            action="communications.campaign.sent",
            payload={
                "delivery_tracking_mode": "provider_events_append_later",
                "correlation_id": correlation_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return row


async def list_campaign_delivery_events(*, building_id: str, campaign_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_campaign_delivery_events
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_campaign(session, ctx, campaign_uuid)
        result = await session.execute(
            text(
                """
                SELECT id::text AS event_id,
                       draft_id::text AS draft_id,
                       newsletter_id::text AS newsletter_id,
                       campaign_id::text AS campaign_id,
                       recipient_party_id::text AS recipient_party_id,
                       recipient_email,
                       channel,
                       provider_message_id,
                       event_type,
                       occurred_at,
                       event_payload
                FROM communications.communication_delivery_events
                WHERE campaign_id = :campaign_id
                ORDER BY occurred_at DESC, created_at DESC
                """
            ),
            {"campaign_id": campaign_uuid},
        )
        return [_normalise_delivery_event(row) for row in result.fetchall()]


async def record_campaign_delivery_event(
    *,
    building_id: str,
    campaign_id: str,
    current_user: dict,
    payload: CommunicationDeliveryEventCreate,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: record_campaign_delivery_event
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")
    values = payload.model_dump()

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_campaign(session, ctx, campaign_uuid)
        result = await session.execute(
            text(
                """
                INSERT INTO communications.communication_delivery_events
                    (tenant_id, scheme_id, campaign_id, recipient_party_id, recipient_email, channel,
                     provider_message_id, event_type, occurred_at, event_payload, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :campaign_id, :recipient_party_id, :recipient_email, :channel,
                     :provider_message_id, :event_type, :occurred_at, CAST(:event_payload AS jsonb), :correlation_id)
                RETURNING id::text AS event_id,
                          draft_id::text AS draft_id,
                          newsletter_id::text AS newsletter_id,
                          campaign_id::text AS campaign_id,
                          recipient_party_id::text AS recipient_party_id,
                          recipient_email,
                          channel,
                          provider_message_id,
                          event_type,
                          occurred_at,
                          event_payload
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "campaign_id": campaign_uuid,
                "recipient_party_id": _parse_uuid(values["recipient_party_id"], label="recipient_party_id") if values.get("recipient_party_id") else None,
                "recipient_email": values.get("recipient_email"),
                "channel": values["channel"],
                "provider_message_id": values.get("provider_message_id"),
                "event_type": values["event_type"],
                "occurred_at": values.get("occurred_at"),
                "event_payload": _json_dump(values.get("event_payload")),
                "correlation_id": correlation_id,
            },
        )
        delivery_event = _normalise_delivery_event(result.one())
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_campaign",
            entity_id=campaign_uuid,
            action="communications.campaign.delivery_event_recorded",
            payload={"event_type": values["event_type"], "channel": values["channel"], "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return delivery_event


async def list_campaign_acknowledgements(*, building_id: str, campaign_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_campaign_acknowledgements
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_campaign(session, ctx, campaign_uuid)
        result = await session.execute(
            text(
                """
                SELECT id::text AS acknowledgement_id,
                       newsletter_id::text AS newsletter_id,
                       campaign_id::text AS campaign_id,
                       party_id::text AS party_id,
                       acknowledgement_type,
                       response_text,
                       acknowledged_at
                FROM communications.communication_acknowledgements
                WHERE campaign_id = :campaign_id
                ORDER BY acknowledged_at DESC, created_at DESC
                """
            ),
            {"campaign_id": campaign_uuid},
        )
        return [_normalise_ack(row) for row in result.fetchall()]


async def acknowledge_campaign(
    *,
    building_id: str,
    campaign_id: str,
    current_user: dict,
    payload: CommunicationAcknowledgementCreate,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: acknowledge_campaign
    Path: backend/services/communications_campaigns_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    campaign_uuid = _parse_uuid(campaign_id, label="campaign_id")
    party_uuid = _parse_uuid(payload.party_id, label="party_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_campaign(session, ctx, campaign_uuid)
        result = await session.execute(
            text(
                """
                INSERT INTO communications.communication_acknowledgements
                    (tenant_id, scheme_id, campaign_id, party_id, acknowledgement_type, response_text, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :campaign_id, :party_id, :acknowledgement_type, :response_text, :correlation_id)
                RETURNING id::text AS acknowledgement_id,
                          newsletter_id::text AS newsletter_id,
                          campaign_id::text AS campaign_id,
                          party_id::text AS party_id,
                          acknowledgement_type,
                          response_text,
                          acknowledged_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "campaign_id": campaign_uuid,
                "party_id": party_uuid,
                "acknowledgement_type": payload.acknowledgement_type,
                "response_text": payload.response_text,
                "correlation_id": correlation_id,
            },
        )
        ack = _normalise_ack(result.one())
        await _insert_audit_event(
            session,
            ctx,
            entity_type="communications_campaign",
            entity_id=campaign_uuid,
            action="communications.campaign.acknowledged",
            payload={"party_id": payload.party_id, "acknowledgement_type": payload.acknowledgement_type, "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return ack
