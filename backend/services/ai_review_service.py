# @featuretrace:ai-review — PostgreSQL-backed building-health assessment and AI recommendation review workflows.
# Layer: service
# Data flow: manager review actions → /ai/* endpoints → ai_assist.*, core.approval_requests, ops.cases (building-scoped).
# Related: routers/ai_review.py
#          services/ops_case_service.py
#          services/sustainability_service.py
"""PostgreSQL-backed AI building assessment and recommendation review service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import text

from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant
from models.ai_review import (
    AIRecommendationApprovalRequest,
    AIRecommendationDecisionRequest,
    BuildingAssessmentRunRequest,
)
from models.ops_case import OpsCaseCreate
from models.user import UserRole
from services import ops_case_service
from utils.auth import effective_role

_MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.ADMIN_STAFF,
    UserRole.EC_MEMBER,
}
_BLOCKED_ACTION_KEYS = {"legal_notice", "access_rights_change", "finance_posting", "owner_financial_change"}
_AI_POLICY_CODE = "ai_recommendation_high_risk"

_ASSESSMENT_SELECT = """
    SELECT
        a.id::text AS assessment_id,
        a.case_id::text AS case_id,
        a.assessment_type,
        a.subject_type,
        a.subject_id,
        a.summary,
        a.status,
        a.model_name,
        a.prompt_version,
        a.risk_level,
        a.confidence_score,
        a.pii_redacted,
        a.approval_required,
        a.approval_request_id::text AS approval_request_id,
        approval.status AS approval_status,
        a.created_by::text AS created_by,
        a.updated_by::text AS updated_by,
        a.created_at,
        a.updated_at
    FROM ai_assist.ai_assessments a
    LEFT JOIN core.approval_requests approval ON approval.approval_request_id = a.approval_request_id
"""

_RECOMMENDATION_SELECT = """
    SELECT
        r.id::text AS recommendation_id,
        r.assessment_id::text AS assessment_id,
        r.case_id::text AS case_id,
        r.recommendation_type,
        r.title,
        r.recommendation_text,
        r.status,
        r.risk_level,
        r.confidence_score,
        r.proposed_action,
        r.approval_required,
        r.approval_request_id::text AS approval_request_id,
        approval.status AS approval_status,
        r.created_by::text AS created_by,
        r.updated_by::text AS updated_by,
        r.created_at,
        r.updated_at,
        COALESCE(ev.evidence_count, 0) AS evidence_count
    FROM ai_assist.ai_recommendations r
    LEFT JOIN core.approval_requests approval ON approval.approval_request_id = r.approval_request_id
    LEFT JOIN (
        SELECT recommendation_id, COUNT(*)::int AS evidence_count
        FROM ai_assist.ai_recommendation_evidence
        GROUP BY recommendation_id
    ) ev ON ev.recommendation_id = r.id
"""


@dataclass
class _AIContext:
    tenant_id: str
    scheme_id: str
    role: str
    actor_user_id: str | None
    actor_email: str | None

    @property
    def is_manager(self) -> bool:
        """Generated function header.

        Function: _AIContext.is_manager
        Path: backend/services/ai_review_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self.role in _MANAGER_ROLES


def _row_to_dict(row) -> dict[str, Any]:
    """Generated function header.

    Function: _row_to_dict
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _json_dump(payload: dict[str, Any] | None) -> str:
    """Generated function header.

    Function: _json_dump
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return json.dumps(payload or {}, default=str)


def _string(value: Any) -> str | None:
    """Generated function header.

    Function: _string
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(value) if value is not None else None


def _parse_uuid(value: str, *, label: str) -> UUID:
    """Generated function header.

    Function: _parse_uuid
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {label}. Expected UUID.",
        ) from exc


def _require_manager(ctx: _AIContext) -> None:
    """Generated function header.

    Function: _require_manager
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not ctx.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and EC members can access AI review workflows.",
        )


def _require_policy_roles(policy: dict[str, Any] | None, *, detail: str) -> tuple[dict[str, Any], list[str]]:
    """Generated function header.

    Function: _require_policy_roles
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not policy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    required_roles = list(policy.get("required_roles") or [])
    if not required_roles:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return policy, required_roles


def _ensure_recommendation_action_allowed(recommendation: dict[str, Any], *, allowed_statuses: set[str], action_label: str) -> None:
    """Generated function header.

    Function: _ensure_recommendation_action_allowed
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if recommendation["status"] not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Recommendation cannot be {action_label} from status '{recommendation['status']}'.",
        )


def _score_band(score: float) -> str:
    """Generated function header.

    Function: _score_band
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def _risk_level_from_scores(*scores: float) -> str:
    """Generated function header.

    Function: _risk_level_from_scores
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    peak = max(scores) if scores else 0.0
    if peak >= 75:
        return "critical"
    if peak >= 55:
        return "high"
    if peak >= 30:
        return "medium"
    return "low"


def _confidence_from_counts(signal_count: int) -> float:
    """Generated function header.

    Function: _confidence_from_counts
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return round(min(0.95, 0.45 + (signal_count * 0.04)), 4)


def _hash_text(value: str) -> str:
    """Generated function header.

    Function: _hash_text
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _resolve_context(building_id: str, current_user: dict) -> _AIContext:
    """Generated function header.

    Function: _resolve_context
    Path: backend/services/ai_review_service.py

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
    return _AIContext(
        tenant_id=str(scheme["tenant_id"]),
        scheme_id=str(scheme["scheme_id"]),
        role=role,
        actor_user_id=actor_user_id,
        actor_email=current_user.get("email"),
    )


async def _insert_audit_event(
    session,
    ctx: _AIContext,
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
    Path: backend/services/ai_review_service.py

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


async def _fetch_policy(session, ctx: _AIContext, policy_code: str | None) -> dict[str, Any] | None:
    """Generated function header.

    Function: _fetch_policy
    Path: backend/services/ai_review_service.py

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


def _normalise_assessment(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_assessment
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("assessment_id", "case_id", "approval_request_id", "created_by", "updated_by"):
        data[key] = _string(data.get(key))
    if data.get("confidence_score") is not None:
        data["confidence_score"] = float(data["confidence_score"])
    data.setdefault("risk_scores", [])
    data.setdefault("recommendations", [])
    return data


def _normalise_recommendation(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_recommendation
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("recommendation_id", "assessment_id", "case_id", "approval_request_id", "created_by", "updated_by"):
        data[key] = _string(data.get(key))
    if data.get("confidence_score") is not None:
        data["confidence_score"] = float(data["confidence_score"])
    data["evidence_count"] = int(data.get("evidence_count") or 0)
    data["proposed_action"] = dict(data.get("proposed_action") or {})
    return data


def _normalise_risk_score(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_risk_score
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    data["risk_score_id"] = _string(data.get("risk_score_id"))
    data["score"] = float(data["score"])
    return data


def _normalise_evidence(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_evidence
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    data["evidence_id"] = _string(data.get("evidence_id"))
    data["recommendation_id"] = _string(data.get("recommendation_id"))
    return data


async def _fetch_assessment(session, ctx: _AIContext, assessment_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_assessment
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            f"""
            {_ASSESSMENT_SELECT}
            WHERE a.scheme_id = :scheme_id
              AND a.id = :assessment_id
              AND a.deleted_at IS NULL
            """
        ),
        {"scheme_id": ctx.scheme_id, "assessment_id": assessment_uuid},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")
    return _normalise_assessment(row)


async def _fetch_recommendation(session, ctx: _AIContext, recommendation_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_recommendation
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            f"""
            {_RECOMMENDATION_SELECT}
            WHERE r.scheme_id = :scheme_id
              AND r.id = :recommendation_id
              AND r.deleted_at IS NULL
            """
        ),
        {"scheme_id": ctx.scheme_id, "recommendation_id": recommendation_uuid},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found.")
    return _normalise_recommendation(row)


async def _list_risk_scores(session, ctx: _AIContext, assessment_uuid: UUID) -> list[dict[str, Any]]:
    """Generated function header.

    Function: _list_risk_scores
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT id::text AS risk_score_id,
                   subject_type,
                   subject_id,
                   risk_category,
                   score,
                   score_band,
                   rationale,
                   created_at
            FROM ai_assist.ai_risk_scores
            WHERE scheme_id = :scheme_id
              AND assessment_id = :assessment_id
            ORDER BY created_at ASC
            """
        ),
        {"scheme_id": ctx.scheme_id, "assessment_id": assessment_uuid},
    )
    return [_normalise_risk_score(row) for row in result.fetchall()]


async def _list_assessment_recommendations(session, ctx: _AIContext, assessment_uuid: UUID) -> list[dict[str, Any]]:
    """Generated function header.

    Function: _list_assessment_recommendations
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            f"""
            {_RECOMMENDATION_SELECT}
            WHERE r.scheme_id = :scheme_id
              AND r.assessment_id = :assessment_id
              AND r.deleted_at IS NULL
            ORDER BY r.created_at ASC
            """
        ),
        {"scheme_id": ctx.scheme_id, "assessment_id": assessment_uuid},
    )
    return [_normalise_recommendation(row) for row in result.fetchall()]


async def _fetch_assessment_detail(session, ctx: _AIContext, assessment_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_assessment_detail
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    assessment = await _fetch_assessment(session, ctx, assessment_uuid)
    assessment["risk_scores"] = await _list_risk_scores(session, ctx, assessment_uuid)
    assessment["recommendations"] = await _list_assessment_recommendations(session, ctx, assessment_uuid)
    return assessment


async def _list_recommendation_evidence(session, ctx: _AIContext, recommendation_uuid: UUID) -> list[dict[str, Any]]:
    """Generated function header.

    Function: _list_recommendation_evidence
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT id::text AS evidence_id,
                   recommendation_id::text AS recommendation_id,
                   evidence_type,
                   source_type,
                   source_id,
                   summary,
                   excerpt,
                   created_at,
                   source_system
            FROM ai_assist.ai_recommendation_evidence
            WHERE scheme_id = :scheme_id
              AND recommendation_id = :recommendation_id
            ORDER BY created_at ASC
            """
        ),
        {"scheme_id": ctx.scheme_id, "recommendation_id": recommendation_uuid},
    )
    return [_normalise_evidence(row) for row in result.fetchall()]


async def _record_review_decision(
    session,
    ctx: _AIContext,
    *,
    recommendation_uuid: UUID,
    decision: str,
    decision_reason: str | None,
    correlation_id: str | None,
) -> None:
    """Generated function header.

    Function: _record_review_decision
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            """
            INSERT INTO ai_assist.ai_review_decisions
                (tenant_id, scheme_id, recommendation_id, decision, decision_reason,
                 reviewer_user_id, source_correlation_id)
            VALUES
                (:tenant_id, :scheme_id, :recommendation_id, :decision, :decision_reason,
                 :reviewer_user_id, :correlation_id)
            """
        ),
        {
            "tenant_id": ctx.tenant_id,
            "scheme_id": ctx.scheme_id,
            "recommendation_id": recommendation_uuid,
            "decision": decision,
            "decision_reason": decision_reason,
            "reviewer_user_id": ctx.actor_user_id,
            "correlation_id": correlation_id,
        },
    )


async def _create_approval_request(
    session,
    ctx: _AIContext,
    *,
    recommendation_uuid: UUID,
    payload: AIRecommendationApprovalRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: _create_approval_request
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    policy = await _fetch_policy(session, ctx, payload.policy_code or _AI_POLICY_CODE)
    policy, required_roles = _require_policy_roles(
        policy,
        detail="Approval policy is missing or has no approver roles for AI recommendations.",
    )
    result = await session.execute(
        text(
            """
            INSERT INTO core.approval_requests
                (tenant_id, scheme_id, entity_type, entity_id, approval_policy_id, status, requested_by)
            VALUES
                (:tenant_id, :scheme_id, 'ai_recommendation', :entity_id, :approval_policy_id, 'pending', :requested_by)
            RETURNING approval_request_id::text AS approval_request_id,
                      status,
                      requested_by::text AS requested_by,
                      created_at AS requested_at
            """
        ),
        {
            "tenant_id": ctx.tenant_id,
            "scheme_id": ctx.scheme_id,
            "entity_id": recommendation_uuid,
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
    await session.execute(
        text(
            """
            UPDATE ai_assist.ai_recommendations
            SET approval_required = TRUE,
                approval_request_id = :approval_request_id,
                status = 'pending_review',
                updated_by = :updated_by,
                updated_at = now()
            WHERE id = :recommendation_id
              AND scheme_id = :scheme_id
            """
        ),
        {
            "approval_request_id": row["approval_request_id"],
            "updated_by": ctx.actor_user_id,
            "recommendation_id": recommendation_uuid,
            "scheme_id": ctx.scheme_id,
        },
    )
    await _insert_audit_event(
        session,
        ctx,
        entity_type="ai_recommendation",
        entity_id=recommendation_uuid,
        action="ai.recommendation.approval_requested",
        payload={
            "approval_request_id": row["approval_request_id"],
            "policy_code": policy["policy_code"],
            "required_roles": required_roles,
            "note": payload.note,
            "correlation_id": correlation_id,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    row["recommendation_id"] = str(recommendation_uuid)
    row["policy_code"] = policy["policy_code"]
    row["required_roles"] = required_roles
    return row


def _build_assessment_summary(metrics: dict[str, Any], payload: BuildingAssessmentRunRequest) -> str:
    """Generated function header.

    Function: _build_assessment_summary
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    summary_parts = [
        f"{metrics['open_case_count']} open cases",
        f"{metrics['high_priority_case_count']} high-priority cases",
        f"{metrics['open_utility_anomaly_count']} open utility anomalies",
        f"{metrics['open_compliance_event_count']} open compliance events",
    ]
    if payload.summary_hint:
        summary_parts.append(payload.summary_hint)
    return "Assessment generated from " + ", ".join(summary_parts) + "."


def _recommendation_payload(
    *,
    action_type: str,
    problem_statement: str,
    evidence_summary: str,
    owner_impact: str,
    disruption: str,
    compliance_considerations: str,
    approval_path: str,
    communication_plan: str,
    estimated_cost_range: str,
    blocked_action: bool = False,
) -> dict[str, Any]:
    """Generated function header.

    Function: _recommendation_payload
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "action_type": action_type,
        "problem_statement": problem_statement,
        "evidence_summary": evidence_summary,
        "estimated_cost_range": estimated_cost_range,
        "likely_owner_impact": owner_impact,
        "likely_disruption": disruption,
        "compliance_considerations": compliance_considerations,
        "suggested_approval_path": approval_path,
        "suggested_communication_plan": communication_plan,
        "guardrails": {
            "blocked_action": blocked_action,
            "approval_required_before_execution": blocked_action,
            "forbidden_action_keys": sorted(_BLOCKED_ACTION_KEYS),
        },
    }


async def run_building_assessment(
    *,
    building_id: str,
    current_user: dict,
    payload: BuildingAssessmentRunRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: run_building_assessment
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)

        metrics_row = (
            await session.execute(
                text(
                    """
                    SELECT
                        COALESCE((SELECT COUNT(*)::int FROM ops.cases WHERE scheme_id = :scheme_id AND deleted_at IS NULL AND status NOT IN ('completed', 'closed', 'cancelled')), 0) AS open_case_count,
                        COALESCE((SELECT COUNT(*)::int FROM ops.cases WHERE scheme_id = :scheme_id AND deleted_at IS NULL AND priority IN ('high', 'urgent', 'critical') AND status NOT IN ('completed', 'closed', 'cancelled')), 0) AS high_priority_case_count,
                        COALESCE((SELECT COUNT(*)::int FROM ops.service_requests WHERE scheme_id = :scheme_id AND status IN ('received', 'triaged', 'quoted', 'scheduled', 'in_progress', 'awaiting_review')), 0) AS active_service_request_count,
                        COALESCE((SELECT COUNT(*)::int FROM finance.utility_anomalies WHERE scheme_id = :scheme_id AND status NOT IN ('resolved', 'cancelled')), 0) AS open_utility_anomaly_count,
                        COALESCE((SELECT COUNT(*)::int FROM finance.utility_anomalies WHERE scheme_id = :scheme_id AND severity IN ('high', 'critical') AND status NOT IN ('resolved', 'cancelled')), 0) AS severe_utility_anomaly_count,
                        COALESCE((SELECT COUNT(*)::int FROM compliance.compliance_events WHERE scheme_id = :scheme_id AND status IN ('logged', 'pending', 'scheduled')), 0) AS open_compliance_event_count,
                        COALESCE((SELECT COUNT(*)::int FROM access.access_device_requests WHERE scheme_id = :scheme_id AND request_type IN ('lost', 'stolen') AND status NOT IN ('completed', 'cancelled')), 0) AS access_risk_count,
                        COALESCE((SELECT COUNT(*)::int FROM finance.utility_bills WHERE scheme_id = :scheme_id AND billing_period_start >= (CURRENT_DATE - INTERVAL '365 days')), 0) AS recent_utility_bill_count
                    """
                ),
                {"scheme_id": ctx.scheme_id},
            )
        ).one()
        metrics = _row_to_dict(metrics_row)

        building_health_score = min(100.0, metrics["open_case_count"] * 6 + metrics["high_priority_case_count"] * 12 + metrics["open_compliance_event_count"] * 9)
        maintenance_recurrence_score = min(100.0, metrics["active_service_request_count"] * 8)
        utility_anomaly_score = min(100.0, metrics["open_utility_anomaly_count"] * 18 + metrics["severe_utility_anomaly_count"] * 10)
        dumping_hotspot_score = min(100.0, metrics["open_utility_anomaly_count"] * 10)
        sustainability_opportunity_score = min(100.0, max(20, 65 - metrics["recent_utility_bill_count"] * 2 + metrics["open_utility_anomaly_count"] * 6))
        asset_risk_score = min(100.0, metrics["active_service_request_count"] * 10 + metrics["access_risk_count"] * 8)
        signal_count = sum(int(value or 0) for value in metrics.values())
        confidence_score = _confidence_from_counts(signal_count)
        risk_level = _risk_level_from_scores(building_health_score, maintenance_recurrence_score, utility_anomaly_score, asset_risk_score)
        approval_required = risk_level in {"high", "critical"} or utility_anomaly_score >= 55

        assessment_uuid = uuid4()
        summary = _build_assessment_summary(metrics, payload)
        subject_type = "building"
        subject_id = ctx.scheme_id

        await session.execute(
            text(
                """
                INSERT INTO ai_assist.ai_assessments
                    (id, tenant_id, scheme_id, assessment_type, subject_type, subject_id, summary,
                     status, model_name, prompt_version, risk_level, confidence_score, pii_redacted,
                     approval_required, created_by, updated_by, source_correlation_id)
                VALUES
                    (:id, :tenant_id, :scheme_id, :assessment_type, :subject_type, :subject_id, :summary,
                     'pending_review', 'strataos-heuristic-v1', 'phase2-building-health-v1', :risk_level,
                     :confidence_score, TRUE, :approval_required, :created_by, :updated_by, :correlation_id)
                """
            ),
            {
                "id": assessment_uuid,
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "assessment_type": payload.assessment_type,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "summary": summary,
                "risk_level": risk_level,
                "confidence_score": confidence_score,
                "approval_required": approval_required,
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )

        risk_rows = [
            ("building_health", building_health_score, "Open operational workload and unresolved issues."),
            ("asset_risk", asset_risk_score, "Repeat service activity and access incidents increase asset risk."),
            ("utility_anomaly", utility_anomaly_score, "Utility anomalies and spikes suggest investigation is needed."),
            ("dumping_hotspot", dumping_hotspot_score, "Utility irregularities and recurrence patterns indicate possible hotspot behaviour."),
            ("maintenance_recurrence", maintenance_recurrence_score, "Active and repeat service requests indicate recurring maintenance pressure."),
            ("sustainability_opportunity", sustainability_opportunity_score, "Utility patterns indicate there may be savings or upgrade opportunities."),
        ]
        for category, score, rationale in risk_rows:
            await session.execute(
                text(
                    """
                    INSERT INTO ai_assist.ai_risk_scores
                        (tenant_id, scheme_id, assessment_id, subject_type, subject_id, risk_category, score,
                         score_band, rationale, source_correlation_id)
                    VALUES
                        (:tenant_id, :scheme_id, :assessment_id, :subject_type, :subject_id, :risk_category, :score,
                         :score_band, :rationale, :correlation_id)
                    """
                ),
                {
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "assessment_id": assessment_uuid,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "risk_category": category,
                    "score": round(score, 3),
                    "score_band": _score_band(score),
                    "rationale": rationale,
                    "correlation_id": correlation_id,
                },
            )

        prompt_text = (
            f"assessment_type={payload.assessment_type};subject_type={subject_type};"
            f"subject_id={subject_id};summary_hint={payload.summary_hint or ''};metrics={json.dumps(metrics, default=str, sort_keys=True)}"
        )
        response_text = json.dumps({"summary": summary, "risk_level": risk_level, "confidence_score": confidence_score}, default=str, sort_keys=True)
        await session.execute(
            text(
                """
                INSERT INTO ai_assist.ai_prompt_audit
                    (tenant_id, scheme_id, assessment_id, prompt_template_key, prompt_hash, redacted_prompt,
                     model_name, response_hash, token_count_input, token_count_output, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :assessment_id, :prompt_template_key, :prompt_hash, :redacted_prompt,
                     :model_name, :response_hash, :token_count_input, :token_count_output, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "assessment_id": assessment_uuid,
                "prompt_template_key": "phase2-building-health-v1",
                "prompt_hash": _hash_text(prompt_text),
                "redacted_prompt": "PII minimised building-health assessment prompt",
                "model_name": "strataos-heuristic-v1",
                "response_hash": _hash_text(response_text),
                "token_count_input": max(1, len(prompt_text.split())),
                "token_count_output": max(1, len(response_text.split())),
                "correlation_id": correlation_id,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO ai_assist.ai_redaction_events
                    (tenant_id, scheme_id, assessment_id, source_type, source_id, redaction_profile,
                     redacted_fields, created_by, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :assessment_id, 'building_assessment', :source_id, :redaction_profile,
                     :redacted_fields, :created_by, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "assessment_id": assessment_uuid,
                "source_id": subject_id,
                "redaction_profile": "phase2-minimal-pii",
                "redacted_fields": ["full_name", "email", "phone", "free_text_pii"],
                "created_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )

        recommendation_specs: list[dict[str, Any]] = []
        if utility_anomaly_score >= 30:
            recommendation_specs.append(
                {
                    "recommendation_type": "utility_investigation",
                    "title": "Investigate utility anomaly patterns",
                    "text": "Review recent utility anomalies, confirm whether there is a leak, meter fault, or billing discrepancy, and prepare a resident-safe response plan.",
                    "risk_level": _risk_level_from_scores(utility_anomaly_score),
                    "approval_required": utility_anomaly_score >= 55,
                    "payload": _recommendation_payload(
                        action_type="utility_investigation_case",
                        problem_statement="Utility usage and/or cost patterns are outside the recent baseline.",
                        evidence_summary=f"{metrics['open_utility_anomaly_count']} open utility anomalies, including {metrics['severe_utility_anomaly_count']} severe anomalies.",
                        owner_impact="May lead to follow-up access coordination or usage guidance.",
                        disruption="Low until on-site investigation is scheduled.",
                        compliance_considerations="Avoid assigning fault before human review; keep resident communication factual.",
                        approval_path="Manager review, then EC approval if expenditure or major works are proposed.",
                        communication_plan="Prepare a draft update for affected residents once the cause is confirmed.",
                        estimated_cost_range="$0-$2,500 investigative scope",
                    ),
                }
            )
        if maintenance_recurrence_score >= 30:
            recommendation_specs.append(
                {
                    "recommendation_type": "maintenance_follow_up",
                    "title": "Escalate recurring maintenance pressure",
                    "text": "Open a tracked follow-up case to consolidate repeat work-order patterns and identify whether preventive maintenance or replacement planning is needed.",
                    "risk_level": _risk_level_from_scores(maintenance_recurrence_score),
                    "approval_required": maintenance_recurrence_score >= 55,
                    "payload": _recommendation_payload(
                        action_type="ops_case_follow_up",
                        problem_statement="Repeat repairs are increasing workload and may signal a failing asset or incomplete prior remediation.",
                        evidence_summary=f"{metrics['active_service_request_count']} active service requests are still open.",
                        owner_impact="May require temporary access or coordination with affected lots.",
                        disruption="Medium if on-site remedial works are scheduled.",
                        compliance_considerations="Preserve case chronology and service-provider evidence before recommending spend.",
                        approval_path="Manager triage; EC approval only if significant replacement or expenditure is proposed.",
                        communication_plan="Use case-linked notices for affected residents before scheduling works.",
                        estimated_cost_range="$0-$5,000 triage / investigative scope",
                    ),
                }
            )
        if sustainability_opportunity_score >= 40:
            recommendation_specs.append(
                {
                    "recommendation_type": "sustainability_opportunity",
                    "title": "Assess a sustainability upgrade package",
                    "text": "Review building utility patterns and baseline efficiency levels to identify whether a lighting, solar, or water-efficiency project should move into planning.",
                    "risk_level": "medium",
                    "approval_required": False,
                    "payload": _recommendation_payload(
                        action_type="sustainability_assessment",
                        problem_statement="Utility trends suggest there may be a worthwhile efficiency opportunity.",
                        evidence_summary=f"{metrics['recent_utility_bill_count']} utility bills were available for the last 12 months.",
                        owner_impact="Potential long-term levy savings but may require owner communication and consultation.",
                        disruption="Low during assessment; medium if the recommendation later becomes a funded project.",
                        compliance_considerations="Any spend, access changes, or legal notices remain human-approved.",
                        approval_path="Manager review first; EC approval if a funded project is proposed.",
                        communication_plan="Prepare a plain-language summary explaining why the opportunity was identified.",
                        estimated_cost_range="$1,500-$25,000 depending on project scope",
                    ),
                }
            )

        top_case_rows = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS case_id, title, priority
                    FROM ops.cases
                    WHERE scheme_id = :scheme_id
                      AND deleted_at IS NULL
                      AND status NOT IN ('completed', 'closed', 'cancelled')
                    ORDER BY CASE priority
                        WHEN 'critical' THEN 5
                        WHEN 'urgent' THEN 4
                        WHEN 'high' THEN 3
                        WHEN 'normal' THEN 2
                        ELSE 1
                    END DESC, created_at DESC
                    LIMIT 3
                    """
                ),
                {"scheme_id": ctx.scheme_id},
            )
        ).fetchall()
        top_anomaly_rows = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS anomaly_id, title, anomaly_type, severity
                    FROM finance.utility_anomalies
                    WHERE scheme_id = :scheme_id
                      AND status NOT IN ('resolved', 'cancelled')
                    ORDER BY detected_at DESC
                    LIMIT 3
                    """
                ),
                {"scheme_id": ctx.scheme_id},
            )
        ).fetchall()

        for spec in recommendation_specs:
            recommendation_uuid = uuid4()
            await session.execute(
                text(
                    """
                    INSERT INTO ai_assist.ai_recommendations
                        (id, tenant_id, scheme_id, assessment_id, recommendation_type, title,
                         recommendation_text, status, risk_level, confidence_score, proposed_action,
                         approval_required, created_by, updated_by, source_correlation_id)
                    VALUES
                        (:id, :tenant_id, :scheme_id, :assessment_id, :recommendation_type, :title,
                         :recommendation_text, 'pending_review', :risk_level, :confidence_score,
                         CAST(:proposed_action AS jsonb), :approval_required, :created_by, :updated_by,
                         :correlation_id)
                    """
                ),
                {
                    "id": recommendation_uuid,
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "assessment_id": assessment_uuid,
                    "recommendation_type": spec["recommendation_type"],
                    "title": spec["title"],
                    "recommendation_text": spec["text"],
                    "risk_level": spec["risk_level"],
                    "confidence_score": confidence_score,
                    "proposed_action": _json_dump(spec["payload"]),
                    "approval_required": spec["approval_required"],
                    "created_by": ctx.actor_user_id,
                    "updated_by": ctx.actor_user_id,
                    "correlation_id": correlation_id,
                },
            )
            for case_row in top_case_rows:
                case_data = _row_to_dict(case_row)
                await session.execute(
                    text(
                        """
                        INSERT INTO ai_assist.ai_recommendation_evidence
                            (tenant_id, scheme_id, recommendation_id, evidence_type, source_type, source_id,
                             summary, excerpt, source_correlation_id)
                        VALUES
                            (:tenant_id, :scheme_id, :recommendation_id, 'ops_case', 'ops_case', :source_id,
                             :summary, :excerpt, :correlation_id)
                        """
                    ),
                    {
                        "tenant_id": ctx.tenant_id,
                        "scheme_id": ctx.scheme_id,
                        "recommendation_id": recommendation_uuid,
                        "source_id": case_data["case_id"],
                        "summary": case_data["title"],
                        "excerpt": f"Priority: {case_data['priority']}",
                        "correlation_id": correlation_id,
                    },
                )
            for anomaly_row in top_anomaly_rows:
                anomaly_data = _row_to_dict(anomaly_row)
                await session.execute(
                    text(
                        """
                        INSERT INTO ai_assist.ai_recommendation_evidence
                            (tenant_id, scheme_id, recommendation_id, evidence_type, source_type, source_id,
                             summary, excerpt, source_correlation_id)
                        VALUES
                            (:tenant_id, :scheme_id, :recommendation_id, 'utility_anomaly', 'utility_anomaly', :source_id,
                             :summary, :excerpt, :correlation_id)
                        """
                    ),
                    {
                        "tenant_id": ctx.tenant_id,
                        "scheme_id": ctx.scheme_id,
                        "recommendation_id": recommendation_uuid,
                        "source_id": anomaly_data["anomaly_id"],
                        "summary": anomaly_data["title"],
                        "excerpt": f"{anomaly_data['anomaly_type']} ({anomaly_data['severity']})",
                        "correlation_id": correlation_id,
                    },
                )

        await _insert_audit_event(
            session,
            ctx,
            entity_type="ai_assessment",
            entity_id=assessment_uuid,
            action="ai.assessment.generated",
            payload={
                "assessment_type": payload.assessment_type,
                "risk_level": risk_level,
                "confidence_score": confidence_score,
                "recommendation_count": len(recommendation_specs),
                "correlation_id": correlation_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_assessment_detail(session, ctx, assessment_uuid)


async def list_building_assessments(
    *,
    building_id: str,
    current_user: dict,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_building_assessments
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                {_ASSESSMENT_SELECT}
                WHERE a.scheme_id = :scheme_id
                  AND a.deleted_at IS NULL
                ORDER BY a.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"scheme_id": ctx.scheme_id, "limit": limit, "offset": offset},
        )
        return [_normalise_assessment(row) for row in result.fetchall()]


async def get_building_assessment(*, building_id: str, assessment_id: str, current_user: dict) -> dict[str, Any]:
    """Generated function header.

    Function: get_building_assessment
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    assessment_uuid = _parse_uuid(assessment_id, label="assessment_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        return await _fetch_assessment_detail(session, ctx, assessment_uuid)


async def list_recommendations(
    *,
    building_id: str,
    current_user: dict,
    status_filter: str | None,
    recommendation_type: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_recommendations
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    clauses = ["r.scheme_id = :scheme_id", "r.deleted_at IS NULL"]
    params: dict[str, Any] = {"scheme_id": ctx.scheme_id, "limit": limit, "offset": offset}
    if status_filter:
        clauses.append("r.status = :status_filter")
        params["status_filter"] = status_filter
    if recommendation_type:
        clauses.append("r.recommendation_type = :recommendation_type")
        params["recommendation_type"] = recommendation_type

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                {_RECOMMENDATION_SELECT}
                WHERE {' AND '.join(clauses)}
                ORDER BY r.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return [_normalise_recommendation(row) for row in result.fetchall()]


async def accept_recommendation(
    *,
    building_id: str,
    recommendation_id: str,
    current_user: dict,
    payload: AIRecommendationDecisionRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: accept_recommendation
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    recommendation_uuid = _parse_uuid(recommendation_id, label="recommendation_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        recommendation = await _fetch_recommendation(session, ctx, recommendation_uuid)
        _ensure_recommendation_action_allowed(recommendation, allowed_statuses={"pending_review"}, action_label="accepted")
        if recommendation["approval_required"] and recommendation.get("approval_status") != "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Recommendation requires approved human review before it can be accepted.",
            )
        await session.execute(
            text(
                """
                UPDATE ai_assist.ai_recommendations
                SET status = 'approved',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :recommendation_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "recommendation_id": recommendation_uuid, "scheme_id": ctx.scheme_id},
        )
        await _record_review_decision(
            session,
            ctx,
            recommendation_uuid=recommendation_uuid,
            decision="approved",
            decision_reason=payload.decision_reason,
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="ai_recommendation",
            entity_id=recommendation_uuid,
            action="ai.recommendation.accepted",
            payload={"decision_reason": payload.decision_reason, "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_recommendation(session, ctx, recommendation_uuid)


async def reject_recommendation(
    *,
    building_id: str,
    recommendation_id: str,
    current_user: dict,
    payload: AIRecommendationDecisionRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: reject_recommendation
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    recommendation_uuid = _parse_uuid(recommendation_id, label="recommendation_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        recommendation = await _fetch_recommendation(session, ctx, recommendation_uuid)
        _ensure_recommendation_action_allowed(recommendation, allowed_statuses={"pending_review"}, action_label="rejected")
        await session.execute(
            text(
                """
                UPDATE ai_assist.ai_recommendations
                SET status = 'rejected',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :recommendation_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "recommendation_id": recommendation_uuid, "scheme_id": ctx.scheme_id},
        )
        await _record_review_decision(
            session,
            ctx,
            recommendation_uuid=recommendation_uuid,
            decision="rejected",
            decision_reason=payload.decision_reason,
            correlation_id=correlation_id,
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="ai_recommendation",
            entity_id=recommendation_uuid,
            action="ai.recommendation.rejected",
            payload={"decision_reason": payload.decision_reason, "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_recommendation(session, ctx, recommendation_uuid)


async def convert_recommendation_to_case(
    *,
    building_id: str,
    recommendation_id: str,
    current_user: dict,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: convert_recommendation_to_case
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    recommendation_uuid = _parse_uuid(recommendation_id, label="recommendation_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        recommendation = await _fetch_recommendation(session, ctx, recommendation_uuid)
        if recommendation["status"] != "approved":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recommendation must be approved before conversion.")
        if recommendation["approval_required"] and recommendation.get("approval_status") != "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Recommendation still requires approved human review before conversion.",
            )
        if recommendation.get("case_id"):
            case = await ops_case_service.get_case(building_id=building_id, case_id=recommendation["case_id"], current_user=current_user)
            return {
                "recommendation_id": recommendation["recommendation_id"],
                "case_id": case["id"],
                "case_status": case["status"],
                "recommendation_status": recommendation["status"],
            }
        evidence = await _list_recommendation_evidence(session, ctx, recommendation_uuid)

    case = await ops_case_service.create_case(
        building_id=building_id,
        current_user=current_user,
        payload=OpsCaseCreate(
            title=recommendation["title"],
            description=recommendation["recommendation_text"],
            category="ai_recommendation",
            priority="high" if recommendation["risk_level"] in {"high", "critical"} else "normal",
            risk_level=recommendation["risk_level"],
            source_type="ai_recommendation",
            source_id=recommendation["recommendation_id"],
            approval_required=False,
            visibility_scope="manager_ec_only",
            pii_classification="internal",
            related_asset_ids=[item["source_id"] for item in evidence if item["source_type"] == "utility_anomaly"][:10],
        ),
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await session.execute(
            text(
                """
                UPDATE ai_assist.ai_recommendations
                SET case_id = :case_id,
                    status = 'executed',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :recommendation_id
                  AND scheme_id = :scheme_id
                """
            ),
            {
                "case_id": _parse_uuid(case["id"], label="case_id"),
                "updated_by": ctx.actor_user_id,
                "recommendation_id": recommendation_uuid,
                "scheme_id": ctx.scheme_id,
            },
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="ai_recommendation",
            entity_id=recommendation_uuid,
            action="ai.recommendation.converted_to_case",
            payload={"case_id": case["id"], "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return {
        "recommendation_id": recommendation["recommendation_id"],
        "case_id": case["id"],
        "case_status": case["status"],
        "recommendation_status": "executed",
    }


async def request_recommendation_approval(
    *,
    building_id: str,
    recommendation_id: str,
    current_user: dict,
    payload: AIRecommendationApprovalRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: request_recommendation_approval
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    recommendation_uuid = _parse_uuid(recommendation_id, label="recommendation_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        recommendation = await _fetch_recommendation(session, ctx, recommendation_uuid)
        _ensure_recommendation_action_allowed(
            recommendation,
            allowed_statuses={"pending_review"},
            action_label="sent for approval",
        )
        if recommendation.get("approval_request_id"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Recommendation already has an approval request.")
        return await _create_approval_request(
            session,
            ctx,
            recommendation_uuid=recommendation_uuid,
            payload=payload,
            correlation_id=correlation_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )


async def get_recommendation_evidence(*, building_id: str, recommendation_id: str, current_user: dict) -> list[dict[str, Any]]:
    """Generated function header.

    Function: get_recommendation_evidence
    Path: backend/services/ai_review_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    recommendation_uuid = _parse_uuid(recommendation_id, label="recommendation_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        await _fetch_recommendation(session, ctx, recommendation_uuid)
        return await _list_recommendation_evidence(session, ctx, recommendation_uuid)
