"""Postgres-backed inbound email intake on top of the unified ops case layer."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text

from db_postgres.repos import config_repo, identity_repo
from db_postgres.session import async_session_context, set_tenant
from models.email_intake import (
    InboundEmailCreate,
    IntakeClassificationUpdate,
    IntakeConvertToCaseRequest,
    IntakeMarkDuplicateRequest,
    IntakeRedactRequest,
)
from models.user import UserRole
from services.ops_case_service import (
    _get_case_for_access,
    _insert_audit_event,
    _insert_case_event,
    _insert_status_history,
    _parse_uuid,
    _resolve_context,
    _row_to_dict,
    _transition_status,
)

_MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.ADMIN_STAFF,
    UserRole.EC_MEMBER,
}

_REDACT_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_REDACT_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_HTML_RE = re.compile(r"<[^>]+>")
_TOKEN_RE = re.compile(r"[A-Za-z0-9-]{4,}")

_CATEGORY_RULES: list[tuple[re.Pattern[str], str, float, str, str, str]] = [
    (re.compile(r"\b(fob|swipe|access card|garage remote|remote|key card)\b", re.IGNORECASE), "access_fob", 0.95, "Matched access device keywords", "high", "medium"),
    (re.compile(r"\b(leak|water damage|flood|plumb|pipe|ceiling)\b", re.IGNORECASE), "repair", 0.92, "Matched repair/leak keywords", "high", "medium"),
    (re.compile(r"\b(noise|disturbance|loud|party|music)\b", re.IGNORECASE), "noise_disturbance", 0.88, "Matched noise/disturbance keywords", "normal", "low"),
    (re.compile(r"\b(dumping|rubbish|garbage|skip bin|waste)\b", re.IGNORECASE), "dumping", 0.84, "Matched dumping/waste keywords", "normal", "low"),
    (re.compile(r"\b(water bill|electricity|power usage|utility|solar)\b", re.IGNORECASE), "utility", 0.86, "Matched utility/sustainability keywords", "normal", "medium"),
    (re.compile(r"\b(levy|arrears|invoice|payment|balance owing|finance)\b", re.IGNORECASE), "levy_finance", 0.88, "Matched levy/finance keywords", "normal", "medium"),
    (re.compile(r"\b(agm|committee|motion|governance|meeting papers)\b", re.IGNORECASE), "governance", 0.82, "Matched governance keywords", "normal", "high"),
    (re.compile(r"\b(newsletter|notice|announcement|content)\b", re.IGNORECASE), "newsletter_content", 0.80, "Matched newsletter/content keywords", "normal", "medium"),
    (re.compile(r"\b(complaint|unacceptable|frustrated|escalate)\b", re.IGNORECASE), "complaint", 0.78, "Matched complaint keywords", "high", "medium"),
    (re.compile(r"\b(contractor|invoice attached|site update|job update)\b", re.IGNORECASE), "contractor_update", 0.80, "Matched contractor update keywords", "normal", "low"),
]


@dataclass
class _InboundContext:
    tenant_id: str
    scheme_id: str
    role: str
    actor_user_id: str | None


def verify_inbound_email_signature(raw_body: bytes, signature: str | None) -> None:
    """Generated function header.

    Function: verify_inbound_email_signature
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    secret = os.getenv("COMMUNICATIONS_INBOUND_EMAIL_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Inbound email webhook secret is not configured.",
        )
    if not signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing inbound email signature.")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid inbound email signature.")


def _strip_html(value: str | None) -> str:
    """Generated function header.

    Function: _strip_html
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value:
        return ""
    return _HTML_RE.sub(" ", value)


def _redact_obvious_pii(value: str | None) -> tuple[str, bool]:
    """Generated function header.

    Function: _redact_obvious_pii
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value:
        return "", False
    redacted = _REDACT_EMAIL_RE.sub("[redacted-email]", value)
    redacted = _REDACT_PHONE_RE.sub("[redacted-phone]", redacted)
    return redacted, redacted != value


def _normalise_subject(subject: str) -> str:
    """Generated function header.

    Function: _normalise_subject
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return " ".join(subject.lower().split())


def _classify(subject: str, body: str) -> dict[str, Any]:
    """Generated function header.

    Function: _classify
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    haystack = f"{subject}\n{body}"
    for pattern, category, confidence, reason, priority, risk_level in _CATEGORY_RULES:
        if pattern.search(haystack):
            return {
                "category": category,
                "confidence": confidence,
                "reason": reason,
                "priority": priority,
                "risk_level": risk_level,
            }
    return {
        "category": "general_enquiry",
        "confidence": 0.45,
        "reason": "No specific rule matched; requires human triage.",
        "priority": "normal",
        "risk_level": "low",
    }


async def _resolve_webhook_building_id(payload: InboundEmailCreate, header_building_id: str | None) -> str:
    """Generated function header.

    Function: _resolve_webhook_building_id
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if header_building_id:
        scheme = await config_repo.resolve_scheme_context(header_building_id)
        if scheme:
            return str(scheme.get("scheme_number") or scheme["scheme_id"])

    seen_candidates: list[str] = []
    for recipient in [*payload.to, *payload.cc]:
        for token in _TOKEN_RE.findall(recipient):
            candidate = token.strip("._").upper()
            if candidate not in seen_candidates:
                seen_candidates.append(candidate)
    for candidate in seen_candidates:
        scheme = await identity_repo.get_scheme_by_number(candidate)
        if scheme:
            return str(scheme.get("scheme_number") or scheme["scheme_id"])

    sender = await identity_repo.find_user_by_email_for_admin(payload.from_email)
    if sender and sender.get("id"):
        schemes = await identity_repo.list_schemes_for_user(sender["id"])
        if len(schemes) == 1:
            scheme = schemes[0]
            return str(scheme.get("scheme_number") or scheme["scheme_id"])

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Could not resolve building context from inbound email recipients or sender.",
    )


async def _ensure_feature_enabled(building_id: str) -> None:
    """Generated function header.

    Function: _ensure_feature_enabled
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    enabled = await config_repo.resolve_feature_toggle(building_id, "ops_case_management_pg_enabled", default=False)
    if not enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ops case management is disabled.")


async def _find_duplicate_candidate(session, *, scheme_id: str, message_id: str, sender_email: str, normalized_subject: str) -> str | None:
    """Generated function header.

    Function: _find_duplicate_candidate
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT c.id::text AS id
            FROM ops.cases c
            LEFT JOIN LATERAL (
                SELECT ce.event_payload
                FROM ops.case_events ce
                WHERE ce.case_id = c.id
                  AND ce.event_type = 'email_intake_received'
                ORDER BY ce.created_at DESC
                LIMIT 1
            ) intake ON TRUE
            WHERE c.scheme_id = :scheme_id
              AND c.deleted_at IS NULL
              AND c.source_type = 'email'
              AND c.status NOT IN ('closed', 'cancelled')
              AND (
                    c.source_id = :message_id
                 OR (
                        lower(COALESCE(intake.event_payload ->> 'sender_email', '')) = lower(:sender_email)
                    AND lower(COALESCE(intake.event_payload ->> 'normalized_subject', '')) = :normalized_subject
                    AND c.created_at >= now() - interval '14 days'
                 )
              )
            ORDER BY CASE WHEN c.source_id = :message_id THEN 0 ELSE 1 END, c.created_at DESC
            LIMIT 1
            """
        ),
        {
            "scheme_id": scheme_id,
            "message_id": message_id,
            "sender_email": sender_email,
            "normalized_subject": normalized_subject,
        },
    )
    row = result.first()
    return str(row.id) if row else None


def _build_queue_item(row) -> dict[str, Any]:
    """Generated function header.

    Function: _build_queue_item
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    intake_payload = data.get("intake_payload") or {}
    return {
        "intake_id": str(data["id"]),
        "case_id": str(data["id"]),
        "scheme_id": str(data["scheme_id"]),
        "status": data["status"],
        "category": data["category"],
        "priority": data["priority"],
        "risk_level": data["risk_level"],
        "title": data["title"],
        "description": data.get("description"),
        "sender_email": intake_payload.get("sender_email"),
        "sender_name": intake_payload.get("sender_name"),
        "received_at": intake_payload.get("received_at") or data["created_at"],
        "classification_confidence": intake_payload.get("classification_confidence"),
        "classification_reason": intake_payload.get("classification_reason"),
        "needs_review": bool(intake_payload.get("needs_review", False)),
        "duplicate_candidate_case_id": intake_payload.get("duplicate_candidate_case_id"),
        "duplicate_of_case_id": data.get("duplicate_of_case_id"),
        "provider": intake_payload.get("provider"),
        "message_id": intake_payload.get("message_id"),
        "recipient_emails": list(intake_payload.get("recipient_emails") or []),
        "attachment_count": int(intake_payload.get("attachment_count") or 0),
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


async def _fetch_queue_item(session, ctx, case_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_queue_item
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _get_case_for_access(session, ctx, case_uuid, mode="view")
    result = await session.execute(
        text(
            """
            SELECT
                c.id,
                c.scheme_id,
                c.status,
                c.category,
                c.priority,
                c.risk_level,
                c.title,
                c.description,
                c.created_at,
                c.updated_at,
                intake.event_payload AS intake_payload,
                dup.linked_case_id::text AS duplicate_of_case_id
            FROM ops.cases c
            LEFT JOIN LATERAL (
                SELECT ce.event_payload
                FROM ops.case_events ce
                WHERE ce.case_id = c.id
                  AND ce.event_type = 'email_intake_received'
                ORDER BY ce.created_at DESC
                LIMIT 1
            ) intake ON TRUE
            LEFT JOIN LATERAL (
                SELECT cl.linked_case_id
                FROM ops.case_links cl
                WHERE cl.case_id = c.id
                  AND cl.relationship_type = 'duplicate_of'
                ORDER BY cl.created_at DESC
                LIMIT 1
            ) dup ON TRUE
            WHERE c.id = :case_id
            """
        ),
        {"case_id": case_uuid},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Intake item not found.")
    return _build_queue_item(row)


async def create_inbound_email_case(payload: InboundEmailCreate, *, header_building_id: str | None, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: create_inbound_email_case
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_id = await _resolve_webhook_building_id(payload, header_building_id)
    await _ensure_feature_enabled(building_id)
    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Building not found in PostgreSQL.")

    sender = await identity_repo.find_user_by_email_for_admin(payload.from_email)
    actor_user_id = str(sender["id"]) if sender and sender.get("id") else None
    ctx = _InboundContext(
        tenant_id=str(scheme["tenant_id"]),
        scheme_id=str(scheme["scheme_id"]),
        role=UserRole.OWNER,
        actor_user_id=actor_user_id,
    )
    text_body = payload.text_body or _strip_html(payload.html_body)
    redacted_subject, subject_redacted = _redact_obvious_pii(payload.subject)
    redacted_body, body_redacted = _redact_obvious_pii(text_body)
    classification = _classify(redacted_subject, redacted_body)
    normalized_subject = _normalise_subject(payload.subject)
    received_at = payload.received_at or datetime.now(timezone.utc)

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        duplicate_candidate_case_id = await _find_duplicate_candidate(
            session,
            scheme_id=ctx.scheme_id,
            message_id=payload.message_id,
            sender_email=payload.from_email,
            normalized_subject=normalized_subject,
        )
        needs_review = classification["confidence"] < 0.70 or duplicate_candidate_case_id is not None
        initial_status = "new" if needs_review else "triaged"
        pii_classification = "restricted" if subject_redacted or body_redacted else "internal"

        result = await session.execute(
            text(
                """
                INSERT INTO ops.cases
                    (tenant_id, scheme_id, title, description, category, priority, risk_level, status,
                     source_type, source_id, visibility_scope, pii_classification,
                     created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :title, :description, :category, :priority, :risk_level, :status,
                     'email', :source_id, 'manager_ec_only', :pii_classification,
                     :created_by, :updated_by, :correlation_id)
                RETURNING id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "title": redacted_subject[:240],
                "description": redacted_body[:5000],
                "category": classification["category"],
                "priority": classification["priority"],
                "risk_level": classification["risk_level"],
                "status": initial_status,
                "source_id": payload.message_id,
                "pii_classification": pii_classification,
                "created_by": actor_user_id,
                "updated_by": actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        case_uuid = result.scalar_one()
        await _insert_status_history(
            session,
            ctx,
            case_uuid=case_uuid,
            from_status=None,
            to_status=initial_status,
            reason="Inbound email received",
            correlation_id=correlation_id,
        )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="email_intake_received",
            payload={
                "provider": payload.provider,
                "message_id": payload.message_id,
                "sender_email": payload.from_email,
                "sender_name": payload.from_name,
                "recipient_emails": payload.to,
                "cc_emails": payload.cc,
                "attachment_count": len(payload.attachments),
                "attachments": [attachment.model_dump() for attachment in payload.attachments],
                "received_at": received_at.isoformat(),
                "normalized_subject": normalized_subject,
                "classification_confidence": classification["confidence"],
                "classification_reason": classification["reason"],
                "needs_review": needs_review,
                "duplicate_candidate_case_id": duplicate_candidate_case_id,
                "subject_redacted": subject_redacted,
                "body_redacted": body_redacted,
            },
            to_status=initial_status,
            correlation_id=correlation_id,
        )
        await session.execute(
            text(
                """
                INSERT INTO ops.task_comments
                    (tenant_id, scheme_id, case_id, body, is_internal, pii_classification, created_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :body, TRUE, :pii_classification, :created_by,
                     :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "body": f"Inbound email linked: provider={payload.provider}, message_id={payload.message_id}, from={payload.from_email}",
                "pii_classification": pii_classification,
                "created_by": actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.email_intake.received",
            payload={
                "message_id": payload.message_id,
                "provider": payload.provider,
                "sender_email": payload.from_email,
                "classification_category": classification["category"],
                "classification_confidence": classification["confidence"],
                "duplicate_candidate_case_id": duplicate_candidate_case_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_queue_item(session, ctx, case_uuid)


async def list_intake_queue(*, building_id: str, current_user: dict, status_filter: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_intake_queue
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if ctx.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can view the intake queue.")

    params: dict[str, Any] = {
        "scheme_id": ctx.scheme_id,
        "status_filter": status_filter,
        "limit": limit,
        "offset": offset,
    }
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                """
                SELECT
                    c.id,
                    c.scheme_id,
                    c.status,
                    c.category,
                    c.priority,
                    c.risk_level,
                    c.title,
                    c.description,
                    c.created_at,
                    c.updated_at,
                    intake.event_payload AS intake_payload,
                    dup.linked_case_id::text AS duplicate_of_case_id
                FROM ops.cases c
                LEFT JOIN LATERAL (
                    SELECT ce.event_payload
                    FROM ops.case_events ce
                    WHERE ce.case_id = c.id
                      AND ce.event_type = 'email_intake_received'
                    ORDER BY ce.created_at DESC
                    LIMIT 1
                ) intake ON TRUE
                LEFT JOIN LATERAL (
                    SELECT cl.linked_case_id
                    FROM ops.case_links cl
                    WHERE cl.case_id = c.id
                      AND cl.relationship_type = 'duplicate_of'
                    ORDER BY cl.created_at DESC
                    LIMIT 1
                ) dup ON TRUE
                WHERE c.scheme_id = :scheme_id
                  AND c.deleted_at IS NULL
                  AND c.source_type = 'email'
                  AND (:status_filter IS NULL OR c.status = :status_filter)
                ORDER BY c.created_at DESC
                LIMIT :limit
                OFFSET :offset
                """
            ),
            params,
        )
        return [_build_queue_item(row) for row in result.fetchall()]


async def classify_intake(*, building_id: str, intake_id: str, current_user: dict, payload: IntakeClassificationUpdate, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: classify_intake
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if ctx.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can classify intake items.")
    case_uuid = _parse_uuid(intake_id, label="intake_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        queue_item = await _fetch_queue_item(session, ctx, case_uuid)
        await session.execute(
            text(
                """
                UPDATE ops.cases
                SET category = :category,
                    priority = :priority,
                    risk_level = :risk_level,
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :case_id
                """
            ),
            {
                "category": payload.category,
                "priority": payload.priority,
                "risk_level": payload.risk_level,
                "updated_by": ctx.actor_user_id,
                "case_id": case_uuid,
            },
        )
        if queue_item["status"] == "new":
            await _transition_status(
                session,
                ctx,
                case_uuid=case_uuid,
                from_status="new",
                to_status="triaged",
                reason="Email intake classified",
                correlation_id=correlation_id,
            )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="email_intake_classified",
            payload={
                "category": payload.category,
                "confidence": payload.confidence,
                "reason": payload.reason,
                "priority": payload.priority,
                "risk_level": payload.risk_level,
            },
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.email_intake.classified",
            payload={
                "category": payload.category,
                "confidence": payload.confidence,
                "reason": payload.reason,
                "priority": payload.priority,
                "risk_level": payload.risk_level,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_queue_item(session, ctx, case_uuid)


async def convert_intake_to_case(*, building_id: str, intake_id: str, current_user: dict, payload: IntakeConvertToCaseRequest, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: convert_intake_to_case
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if ctx.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can convert intake items.")
    case_uuid = _parse_uuid(intake_id, label="intake_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        case = await _get_case_for_access(session, ctx, case_uuid, mode="view")
        existing = await session.execute(
            text(
                """
                SELECT id::text AS id, request_kind, status
                FROM ops.service_requests
                WHERE case_id = :case_id
                LIMIT 1
                """
            ),
            {"case_id": case_uuid},
        )
        row = existing.first()
        if row:
            return {
                "case_id": str(case_uuid),
                "service_request_id": row.id,
                "request_kind": row.request_kind,
                "status": row.status,
            }
        intake_source = await session.execute(
            text(
                """
                SELECT id
                FROM ops.service_request_intake_sources
                WHERE scheme_id = :scheme_id
                  AND source_key = 'email_inbound'
                  AND is_active = TRUE
                LIMIT 1
                """
            ),
            {"scheme_id": ctx.scheme_id},
        )
        intake_row = intake_source.first()
        request_kind = payload.request_kind or case["category"]
        result = await session.execute(
            text(
                """
                INSERT INTO ops.service_requests
                    (tenant_id, scheme_id, case_id, intake_source_id, request_kind, status,
                     current_vendor_id, disturbance_level, preferred_contact_method,
                     created_by, updated_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :intake_source_id, :request_kind, 'received',
                     NULL, :disturbance_level, :preferred_contact_method,
                     :created_by, :updated_by, :correlation_id)
                RETURNING id::text AS id, request_kind, status
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "intake_source_id": getattr(intake_row, "id", None),
                "request_kind": request_kind,
                "disturbance_level": payload.disturbance_level,
                "preferred_contact_method": payload.preferred_contact_method,
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        created = result.one()
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="email_intake_converted",
            payload={"service_request_id": created.id, "request_kind": request_kind},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.email_intake.converted",
            payload={"service_request_id": created.id, "request_kind": request_kind},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return {
            "case_id": str(case_uuid),
            "service_request_id": created.id,
            "request_kind": created.request_kind,
            "status": created.status,
        }


async def mark_intake_duplicate(*, building_id: str, intake_id: str, current_user: dict, payload: IntakeMarkDuplicateRequest, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: mark_intake_duplicate
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if ctx.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can mark duplicates.")
    case_uuid = _parse_uuid(intake_id, label="intake_id")
    duplicate_of_case_uuid = _parse_uuid(payload.duplicate_of_case_id, label="duplicate_of_case_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _get_case_for_access(session, ctx, case_uuid, mode="view")
        await _get_case_for_access(session, ctx, duplicate_of_case_uuid, mode="view")
        await session.execute(
            text(
                """
                INSERT INTO ops.case_links
                    (tenant_id, scheme_id, case_id, linked_case_id, relationship_type, created_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :case_id, :linked_case_id, 'duplicate_of', :created_by,
                     :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "case_id": case_uuid,
                "linked_case_id": duplicate_of_case_uuid,
                "created_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="email_intake_marked_duplicate",
            payload={"duplicate_of_case_id": str(duplicate_of_case_uuid), "note": payload.note},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.email_intake.duplicate_marked",
            payload={"duplicate_of_case_id": str(duplicate_of_case_uuid), "note": payload.note},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_queue_item(session, ctx, case_uuid)


async def reject_intake(*, building_id: str, intake_id: str, current_user: dict, reason: str, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: reject_intake
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if ctx.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can reject intake items.")
    case_uuid = _parse_uuid(intake_id, label="intake_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        case = await _get_case_for_access(session, ctx, case_uuid, mode="view")
        if case["status"] != "cancelled":
            await _transition_status(
                session,
                ctx,
                case_uuid=case_uuid,
                from_status=case["status"],
                to_status="cancelled",
                reason=reason,
                correlation_id=correlation_id,
            )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="email_intake_rejected",
            payload={"reason": reason},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.email_intake.rejected",
            payload={"reason": reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_queue_item(session, ctx, case_uuid)


async def redact_intake(*, building_id: str, intake_id: str, current_user: dict, payload: IntakeRedactRequest, correlation_id: str | None, ip_address: str | None, user_agent: str | None) -> dict[str, Any]:
    """Generated function header.

    Function: redact_intake
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if ctx.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can redact intake items.")
    case_uuid = _parse_uuid(intake_id, label="intake_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        case = await _get_case_for_access(session, ctx, case_uuid, mode="view")
        subject, subject_redacted = _redact_obvious_pii(payload.subject if payload.subject is not None else case["title"])
        body, body_redacted = _redact_obvious_pii(payload.body if payload.body is not None else (case.get("description") or ""))
        pii_classification = "restricted" if subject_redacted or body_redacted else case["pii_classification"]
        await session.execute(
            text(
                """
                UPDATE ops.cases
                SET title = :title,
                    description = :description,
                    pii_classification = :pii_classification,
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :case_id
                """
            ),
            {
                "title": subject[:240],
                "description": body[:5000],
                "pii_classification": pii_classification,
                "updated_by": ctx.actor_user_id,
                "case_id": case_uuid,
            },
        )
        await _insert_case_event(
            session,
            ctx,
            case_uuid=case_uuid,
            event_type="email_intake_redacted",
            payload={"subject_redacted": subject_redacted, "body_redacted": body_redacted},
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            case_uuid=case_uuid,
            action="ops.email_intake.redacted",
            payload={"subject_redacted": subject_redacted, "body_redacted": body_redacted},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_queue_item(session, ctx, case_uuid)


async def get_intake_audit(*, building_id: str, intake_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: get_intake_audit
    Path: backend/services/email_intake_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    if ctx.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can view intake audit.")
    case_uuid = _parse_uuid(intake_id, label="intake_id")
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _get_case_for_access(session, ctx, case_uuid, mode="view")
        result = await session.execute(
            text(
                """
                SELECT *
                FROM (
                    SELECT
                        'case_event' AS entry_type,
                        ce.created_at AS happened_at,
                        ce.event_type AS action,
                        ce.actor_user_id::text AS actor_user_id,
                        actor.full_name AS actor_name,
                        ce.event_payload AS payload
                    FROM ops.case_events ce
                    LEFT JOIN core.users actor ON actor.user_id = ce.actor_user_id
                    WHERE ce.case_id = :case_id
                      AND ce.event_type LIKE 'email_intake%'

                    UNION ALL

                    SELECT
                        'audit_event' AS entry_type,
                        ae.created_at AS happened_at,
                        ae.action AS action,
                        ae.actor_user_id::text AS actor_user_id,
                        actor.full_name AS actor_name,
                        ae.event_payload AS payload
                    FROM core.audit_events ae
                    LEFT JOIN core.users actor ON actor.user_id = ae.actor_user_id
                    WHERE ae.entity_type = 'ops_case'
                      AND ae.entity_id = :case_id
                      AND ae.action LIKE 'ops.email_intake.%'
                ) events
                ORDER BY happened_at DESC
                """
            ),
            {"case_id": case_uuid},
        )
        rows = []
        for row in result.fetchall():
            data = _row_to_dict(row)
            data["actor_user_id"] = str(data["actor_user_id"]) if data.get("actor_user_id") else None
            data["payload"] = data.get("payload") or {}
            rows.append(data)
        return rows
