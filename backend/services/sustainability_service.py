# @featuretrace:sustainability — PostgreSQL-backed utility anomaly, sustainability recommendation, and project-planning workflows.
# Layer: service
# Data flow: /utilities/* and /sustainability/* endpoints → finance.utility_*, sustainability.*, ai_assist.ai_recommendations, core.approval_requests (building-scoped).
# Related: routers/utilities_workflows.py
#          services/ops_case_service.py
#          services/ai_review_service.py
"""PostgreSQL-backed utility anomaly and sustainability workflow service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import text

from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant
from models.ops_case import OpsCaseCreate
from models.sustainability_workflows import (
    SustainabilityAssessmentRunRequest,
    SustainabilityProjectApprovalRequest,
    UtilityBillImportRequest,
)
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
_SUSTAINABILITY_TYPES = {
    "sustainability_solar_readiness",
    "sustainability_lighting_automation",
    "sustainability_water_efficiency",
    "sustainability_ev_readiness",
}
_PROJECT_POLICY_CODE = "ai_recommendation_high_risk"

_BILL_SELECT = """
    SELECT
        b.id::text AS bill_id,
        b.lot_id::text AS lot_id,
        b.utility_type,
        b.billing_period_start,
        b.billing_period_end,
        b.usage_quantity,
        b.usage_unit,
        b.total_amount_cents,
        b.due_date,
        b.payment_status,
        b.provider_name,
        b.provider_reference,
        b.document_id,
        b.receipt_id::text AS receipt_id,
        b.created_by::text AS created_by,
        b.updated_by::text AS updated_by,
        b.created_at,
        b.updated_at,
        COALESCE(reading_counts.reading_count, 0) AS reading_count
    FROM finance.utility_bills b
    LEFT JOIN (
        SELECT utility_bill_id, COUNT(*)::int AS reading_count
        FROM finance.utility_usage_readings
        GROUP BY utility_bill_id
    ) reading_counts ON reading_counts.utility_bill_id = b.id
"""

_ANOMALY_SELECT = """
    SELECT
        a.id::text AS anomaly_id,
        a.utility_bill_id::text AS utility_bill_id,
        a.reading_id::text AS reading_id,
        a.anomaly_type,
        a.severity,
        a.status,
        a.title,
        a.description,
        a.detected_at,
        a.assigned_to::text AS assigned_to,
        a.approval_required,
        a.approval_request_id::text AS approval_request_id,
        approval.status AS approval_status,
        a.resolved_at,
        a.resolution_note,
        a.created_by::text AS created_by,
        a.updated_by::text AS updated_by,
        a.created_at,
        a.updated_at
    FROM finance.utility_anomalies a
    LEFT JOIN core.approval_requests approval ON approval.approval_request_id = a.approval_request_id
"""

_RECOMMENDATION_SELECT = """
    SELECT
        r.id::text AS recommendation_id,
        r.assessment_id::text AS assessment_id,
        r.recommendation_type,
        r.title,
        r.recommendation_text,
        r.status,
        r.risk_level,
        r.confidence_score,
        r.approval_required,
        r.approval_request_id::text AS approval_request_id,
        approval.status AS approval_status,
        r.proposed_action,
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
class _SustainabilityContext:
    tenant_id: str
    scheme_id: str
    role: str
    actor_user_id: str | None
    actor_email: str | None

    @property
    def is_manager(self) -> bool:
        """Generated function header.

        Function: _SustainabilityContext.is_manager
        Path: backend/services/sustainability_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self.role in _MANAGER_ROLES


def _row_to_dict(row) -> dict[str, Any]:
    """Generated function header.

    Function: _row_to_dict
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _json_dump(payload: dict[str, Any] | None) -> str:
    """Generated function header.

    Function: _json_dump
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return json.dumps(payload or {}, default=str)


def _string(value: Any) -> str | None:
    """Generated function header.

    Function: _string
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(value) if value is not None else None


def _parse_uuid(value: str, *, label: str) -> UUID:
    """Generated function header.

    Function: _parse_uuid
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {label}. Expected UUID.",
        ) from exc


def _require_manager(ctx: _SustainabilityContext) -> None:
    """Generated function header.

    Function: _require_manager
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not ctx.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and EC members can access sustainability workflows.",
        )


def _require_policy_roles(policy: dict[str, Any] | None, *, detail: str) -> tuple[dict[str, Any], list[str]]:
    """Generated function header.

    Function: _require_policy_roles
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not policy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    required_roles = list(policy.get("required_roles") or [])
    if not required_roles:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return policy, required_roles


def _score_band(score: float) -> str:
    """Generated function header.

    Function: _score_band
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


async def _resolve_context(building_id: str, current_user: dict) -> _SustainabilityContext:
    """Generated function header.

    Function: _resolve_context
    Path: backend/services/sustainability_service.py

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
    return _SustainabilityContext(
        tenant_id=str(scheme["tenant_id"]),
        scheme_id=str(scheme["scheme_id"]),
        role=role,
        actor_user_id=actor_user_id,
        actor_email=current_user.get("email"),
    )


async def _insert_audit_event(
    session,
    ctx: _SustainabilityContext,
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
    Path: backend/services/sustainability_service.py

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


async def _fetch_policy(session, ctx: _SustainabilityContext, policy_code: str | None) -> dict[str, Any] | None:
    """Generated function header.

    Function: _fetch_policy
    Path: backend/services/sustainability_service.py

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


def _normalise_bill(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_bill
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("bill_id", "lot_id", "receipt_id", "created_by", "updated_by"):
        data[key] = _string(data.get(key))
    if data.get("usage_quantity") is not None:
        data["usage_quantity"] = float(data["usage_quantity"])
    data["reading_count"] = int(data.get("reading_count") or 0)
    data.setdefault("readings", [])
    return data


def _normalise_reading(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_reading
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("reading_id", "utility_bill_id", "lot_id"):
        data[key] = _string(data.get(key))
    data["reading_value"] = float(data["reading_value"])
    return data


def _normalise_anomaly(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_anomaly
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("anomaly_id", "utility_bill_id", "reading_id", "assigned_to", "approval_request_id", "created_by", "updated_by"):
        data[key] = _string(data.get(key))
    return data


def _normalise_recommendation(row) -> dict[str, Any]:
    """Generated function header.

    Function: _normalise_recommendation
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    data = _row_to_dict(row)
    for key in ("recommendation_id", "assessment_id", "approval_request_id"):
        data[key] = _string(data.get(key))
    if data.get("confidence_score") is not None:
        data["confidence_score"] = float(data["confidence_score"])
    data["evidence_count"] = int(data.get("evidence_count") or 0)
    data["proposed_action"] = dict(data.get("proposed_action") or {})
    data["problem_statement"] = data["proposed_action"].get("problem_statement")
    data["estimated_cost_range"] = data["proposed_action"].get("estimated_cost_range")
    data["likely_owner_impact"] = data["proposed_action"].get("likely_owner_impact")
    data["likely_disruption"] = data["proposed_action"].get("likely_disruption")
    data["compliance_considerations"] = data["proposed_action"].get("compliance_considerations")
    data["suggested_approval_path"] = data["proposed_action"].get("suggested_approval_path")
    data["suggested_communication_plan"] = data["proposed_action"].get("suggested_communication_plan")
    return data


async def _list_bill_readings(session, ctx: _SustainabilityContext, bill_uuid: UUID) -> list[dict[str, Any]]:
    """Generated function header.

    Function: _list_bill_readings
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT id::text AS reading_id,
                   utility_bill_id::text AS utility_bill_id,
                   lot_id::text AS lot_id,
                   utility_type,
                   meter_identifier,
                   asset_reference,
                   reading_taken_at,
                   reading_value,
                   reading_unit,
                   source_type
            FROM finance.utility_usage_readings
            WHERE scheme_id = :scheme_id
              AND utility_bill_id = :bill_id
            ORDER BY reading_taken_at ASC
            """
        ),
        {"scheme_id": ctx.scheme_id, "bill_id": bill_uuid},
    )
    return [_normalise_reading(row) for row in result.fetchall()]


async def _fetch_bill_detail(session, ctx: _SustainabilityContext, bill_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_bill_detail
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            f"""
            {_BILL_SELECT}
            WHERE b.scheme_id = :scheme_id
              AND b.id = :bill_id
            """
        ),
        {"scheme_id": ctx.scheme_id, "bill_id": bill_uuid},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utility bill not found.")
    bill = _normalise_bill(row)
    bill["readings"] = await _list_bill_readings(session, ctx, bill_uuid)
    return bill


async def _fetch_anomaly(session, ctx: _SustainabilityContext, anomaly_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_anomaly
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            f"""
            {_ANOMALY_SELECT}
            WHERE a.scheme_id = :scheme_id
              AND a.id = :anomaly_id
            """
        ),
        {"scheme_id": ctx.scheme_id, "anomaly_id": anomaly_uuid},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utility anomaly not found.")
    return _normalise_anomaly(row)


async def _fetch_project(session, ctx: _SustainabilityContext, project_uuid: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _fetch_project
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT
                p.id::text AS project_id,
                p.profile_id::text AS profile_id,
                p.project_type,
                p.title,
                p.description,
                p.status,
                p.capex_estimate_cents,
                p.payback_months,
                p.emissions_savings_kg,
                p.created_by::text AS created_by,
                p.updated_by::text AS updated_by,
                p.created_at,
                p.updated_at,
                approval.approval_request_id::text AS approval_request_id,
                approval.status AS approval_status
            FROM sustainability.energy_efficiency_projects p
            LEFT JOIN core.approval_requests approval
              ON approval.scheme_id = p.scheme_id
             AND approval.entity_type = 'sustainability_project'
             AND approval.entity_id = p.id
            WHERE p.scheme_id = :scheme_id
              AND p.id = :project_id
              AND p.deleted_at IS NULL
            ORDER BY approval.created_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"scheme_id": ctx.scheme_id, "project_id": project_uuid},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sustainability project not found.")
    data = _row_to_dict(row)
    for key in ("project_id", "profile_id", "created_by", "updated_by", "approval_request_id"):
        data[key] = _string(data.get(key))
    if data.get("emissions_savings_kg") is not None:
        data["emissions_savings_kg"] = float(data["emissions_savings_kg"])
    return data


def _build_anomaly_spec(*, bill: dict[str, Any], average_usage: float | None) -> list[dict[str, Any]]:
    """Generated function header.

    Function: _build_anomaly_spec
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    usage_quantity = float(bill["usage_quantity"]) if bill.get("usage_quantity") is not None else None
    anomalies: list[dict[str, Any]] = []
    utility_type = str(bill["utility_type"]).lower()
    is_common_area = bill.get("lot_id") is None
    if usage_quantity is not None and average_usage and average_usage > 0 and usage_quantity >= average_usage * 1.35:
        if utility_type == "water":
            anomalies.append(
                {
                    "anomaly_type": "unexpected_water_increase",
                    "severity": "high" if usage_quantity >= average_usage * 1.75 else "medium",
                    "title": "Unexpected water increase detected",
                    "description": f"Imported water bill usage {usage_quantity:.2f} exceeds the recent average of {average_usage:.2f}.",
                    "approval_required": False,
                }
            )
        elif utility_type == "electricity" and is_common_area:
            anomalies.append(
                {
                    "anomaly_type": "common_area_electricity_spike",
                    "severity": "high",
                    "title": "Common-area electricity spike detected",
                    "description": f"Common-area electricity usage {usage_quantity:.2f} exceeds the recent average of {average_usage:.2f}.",
                    "approval_required": False,
                }
            )
        else:
            anomalies.append(
                {
                    "anomaly_type": "repeated_high_usage",
                    "severity": "medium",
                    "title": "Repeated high utility usage detected",
                    "description": f"Usage {usage_quantity:.2f} is materially above the recent average of {average_usage:.2f}.",
                    "approval_required": False,
                }
            )
    if bill["total_amount_cents"] >= 1 and ((usage_quantity is None and bill["total_amount_cents"] >= 75000) or (average_usage and average_usage <= 0.1 and bill["total_amount_cents"] >= 40000)):
        anomalies.append(
            {
                "anomaly_type": "billing_inconsistency",
                "severity": "medium",
                "title": "Utility billing inconsistency detected",
                "description": "The imported bill amount appears inconsistent with the available usage history.",
                "approval_required": False,
            }
        )
    return anomalies


def _recommendation_payload(
    *,
    project_type: str,
    problem_statement: str,
    evidence_summary: str,
    estimated_cost_range: str,
    owner_impact: str,
    disruption: str,
    compliance_considerations: str,
    approval_path: str,
    communication_plan: str,
    capex_estimate_cents: int,
    payback_months: int,
    emissions_savings_kg: float,
) -> dict[str, Any]:
    """Generated function header.

    Function: _recommendation_payload
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "project_type": project_type,
        "problem_statement": problem_statement,
        "evidence_summary": evidence_summary,
        "estimated_cost_range": estimated_cost_range,
        "likely_owner_impact": owner_impact,
        "likely_disruption": disruption,
        "compliance_considerations": compliance_considerations,
        "suggested_approval_path": approval_path,
        "suggested_communication_plan": communication_plan,
        "capex_estimate_cents": capex_estimate_cents,
        "payback_months": payback_months,
        "emissions_savings_kg": emissions_savings_kg,
    }


async def import_utility_bills(
    *,
    building_id: str,
    current_user: dict,
    payload: UtilityBillImportRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: import_utility_bills
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    imported_bills: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)

        for bill in payload.bills:
            bill_data = bill.model_dump()
            avg_usage_row = (
                await session.execute(
                    text(
                        """
                        SELECT AVG(usage_quantity)::numeric AS avg_usage
                        FROM finance.utility_bills
                        WHERE scheme_id = :scheme_id
                          AND utility_type = :utility_type
                          AND ((lot_id IS NULL AND :lot_id IS NULL) OR lot_id = :lot_id)
                          AND usage_quantity IS NOT NULL
                        """
                    ),
                    {"scheme_id": ctx.scheme_id, "utility_type": bill_data["utility_type"], "lot_id": bill_data["lot_id"]},
                )
            ).one()
            average_usage = float(avg_usage_row.avg_usage) if getattr(avg_usage_row, "avg_usage", None) is not None else None

            result = await session.execute(
                text(
                    """
                    INSERT INTO finance.utility_bills
                        (tenant_id, scheme_id, lot_id, utility_type, billing_period_start, billing_period_end,
                         usage_quantity, usage_unit, total_amount_cents, due_date, payment_status,
                         provider_name, provider_reference, document_id, created_by, updated_by, source_correlation_id)
                    VALUES
                        (:tenant_id, :scheme_id, :lot_id, :utility_type, :billing_period_start, :billing_period_end,
                         :usage_quantity, :usage_unit, :total_amount_cents, :due_date, :payment_status,
                         :provider_name, :provider_reference, :document_id, :created_by, :updated_by, :correlation_id)
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "lot_id": bill_data["lot_id"],
                    "utility_type": bill_data["utility_type"],
                    "billing_period_start": bill_data["billing_period_start"],
                    "billing_period_end": bill_data["billing_period_end"],
                    "usage_quantity": bill_data["usage_quantity"],
                    "usage_unit": bill_data["usage_unit"],
                    "total_amount_cents": bill_data["total_amount_cents"],
                    "due_date": bill_data["due_date"],
                    "payment_status": bill_data["payment_status"],
                    "provider_name": bill_data["provider_name"],
                    "provider_reference": bill_data["provider_reference"],
                    "document_id": bill_data["document_id"],
                    "created_by": ctx.actor_user_id,
                    "updated_by": ctx.actor_user_id,
                    "correlation_id": correlation_id,
                },
            )
            bill_uuid = result.one().id

            latest_reading_uuid: UUID | None = None
            for reading in bill_data["readings"]:
                reading_result = await session.execute(
                    text(
                        """
                        INSERT INTO finance.utility_usage_readings
                            (tenant_id, scheme_id, utility_bill_id, lot_id, utility_type, meter_identifier,
                             asset_reference, reading_taken_at, reading_value, reading_unit, source_type,
                             source_correlation_id)
                        VALUES
                            (:tenant_id, :scheme_id, :utility_bill_id, :lot_id, :utility_type, :meter_identifier,
                             :asset_reference, :reading_taken_at, :reading_value, :reading_unit, :source_type,
                             :correlation_id)
                        RETURNING id
                        """
                    ),
                    {
                        "tenant_id": ctx.tenant_id,
                        "scheme_id": ctx.scheme_id,
                        "utility_bill_id": bill_uuid,
                        "lot_id": reading["lot_id"] or bill_data["lot_id"],
                        "utility_type": bill_data["utility_type"],
                        "meter_identifier": reading["meter_identifier"],
                        "asset_reference": reading["asset_reference"],
                        "reading_taken_at": reading["reading_taken_at"],
                        "reading_value": reading["reading_value"],
                        "reading_unit": reading["reading_unit"],
                        "source_type": reading["source_type"],
                        "correlation_id": correlation_id,
                    },
                )
                latest_reading_uuid = reading_result.one().id

            imported_bills.append(await _fetch_bill_detail(session, ctx, bill_uuid))
            for anomaly_spec in _build_anomaly_spec(bill=bill_data, average_usage=average_usage):
                anomaly_result = await session.execute(
                    text(
                        """
                        INSERT INTO finance.utility_anomalies
                            (tenant_id, scheme_id, utility_bill_id, reading_id, anomaly_type, severity, status,
                             title, description, approval_required, created_by, updated_by, source_correlation_id)
                        VALUES
                            (:tenant_id, :scheme_id, :utility_bill_id, :reading_id, :anomaly_type, :severity, 'open',
                             :title, :description, :approval_required, :created_by, :updated_by, :correlation_id)
                        RETURNING id
                        """
                    ),
                    {
                        "tenant_id": ctx.tenant_id,
                        "scheme_id": ctx.scheme_id,
                        "utility_bill_id": bill_uuid,
                        "reading_id": latest_reading_uuid,
                        "anomaly_type": anomaly_spec["anomaly_type"],
                        "severity": anomaly_spec["severity"],
                        "title": anomaly_spec["title"],
                        "description": anomaly_spec["description"],
                        "approval_required": anomaly_spec["approval_required"],
                        "created_by": ctx.actor_user_id,
                        "updated_by": ctx.actor_user_id,
                        "correlation_id": correlation_id,
                    },
                )
                anomalies.append(await _fetch_anomaly(session, ctx, anomaly_result.one().id))

            await _insert_audit_event(
                session,
                ctx,
                entity_type="utility_bill",
                entity_id=bill_uuid,
                action="utilities.bill.imported",
                payload={"utility_type": bill_data["utility_type"], "correlation_id": correlation_id},
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return {
            "imported_count": len(imported_bills),
            "anomaly_count": len(anomalies),
            "bills": imported_bills,
            "anomalies": anomalies,
        }


async def list_utility_bills(
    *,
    building_id: str,
    current_user: dict,
    utility_type: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_utility_bills
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    clauses = ["b.scheme_id = :scheme_id"]
    params: dict[str, Any] = {"scheme_id": ctx.scheme_id, "limit": limit, "offset": offset}
    if utility_type:
        clauses.append("b.utility_type = :utility_type")
        params["utility_type"] = utility_type

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                {_BILL_SELECT}
                WHERE {' AND '.join(clauses)}
                ORDER BY b.billing_period_start DESC, b.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return [_normalise_bill(row) for row in result.fetchall()]


async def list_utility_anomalies(
    *,
    building_id: str,
    current_user: dict,
    status_filter: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_utility_anomalies
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    clauses = ["a.scheme_id = :scheme_id"]
    params: dict[str, Any] = {"scheme_id": ctx.scheme_id, "limit": limit, "offset": offset}
    if status_filter:
        clauses.append("a.status = :status_filter")
        params["status_filter"] = status_filter

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                {_ANOMALY_SELECT}
                WHERE {' AND '.join(clauses)}
                ORDER BY a.detected_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return [_normalise_anomaly(row) for row in result.fetchall()]


async def convert_anomaly_to_case(
    *,
    building_id: str,
    anomaly_id: str,
    current_user: dict,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: convert_anomaly_to_case
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    anomaly_uuid = _parse_uuid(anomaly_id, label="anomaly_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        anomaly = await _fetch_anomaly(session, ctx, anomaly_uuid)
        existing_case_row = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS case_id, status
                    FROM ops.cases
                    WHERE scheme_id = :scheme_id
                      AND source_type = 'utility_anomaly'
                      AND source_id = :source_id
                      AND deleted_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"scheme_id": ctx.scheme_id, "source_id": anomaly["anomaly_id"]},
            )
        ).first()
        if existing_case_row is not None:
            case_data = _row_to_dict(existing_case_row)
            return {
                "anomaly_id": anomaly["anomaly_id"],
                "case_id": case_data["case_id"],
                "case_status": case_data["status"],
                "anomaly_status": anomaly["status"],
            }

    case = await ops_case_service.create_case(
        building_id=building_id,
        current_user=current_user,
        payload=OpsCaseCreate(
            title=anomaly["title"],
            description=anomaly.get("description"),
            category="utility_anomaly",
            priority="high" if anomaly["severity"] in {"high", "critical"} else "normal",
            risk_level=anomaly["severity"],
            source_type="utility_anomaly",
            source_id=anomaly["anomaly_id"],
            visibility_scope="manager_ec_only",
            pii_classification="internal",
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
                UPDATE finance.utility_anomalies
                SET status = 'triaged',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :anomaly_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "anomaly_id": anomaly_uuid, "scheme_id": ctx.scheme_id},
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="utility_anomaly",
            entity_id=anomaly_uuid,
            action="utilities.anomaly.converted_to_case",
            payload={"case_id": case["id"], "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return {
        "anomaly_id": anomaly_id,
        "case_id": case["id"],
        "case_status": case["status"],
        "anomaly_status": "triaged",
    }


async def run_sustainability_assessment(
    *,
    building_id: str,
    current_user: dict,
    payload: SustainabilityAssessmentRunRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: run_sustainability_assessment
    Path: backend/services/sustainability_service.py

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
                        COALESCE((SELECT COUNT(*)::int FROM finance.utility_bills WHERE scheme_id = :scheme_id AND billing_period_start >= (CURRENT_DATE - INTERVAL '365 days')), 0) AS yearly_bill_count,
                        COALESCE((SELECT SUM(total_amount_cents)::bigint FROM finance.utility_bills WHERE scheme_id = :scheme_id AND billing_period_start >= (CURRENT_DATE - INTERVAL '365 days')), 0) AS yearly_cost_cents,
                        COALESCE((SELECT COUNT(*)::int FROM finance.utility_anomalies WHERE scheme_id = :scheme_id AND status NOT IN ('resolved', 'cancelled')), 0) AS open_anomaly_count,
                        COALESCE((SELECT COUNT(*)::int FROM finance.utility_anomalies WHERE scheme_id = :scheme_id AND anomaly_type IN ('unexpected_water_increase', 'billing_inconsistency')), 0) AS water_risk_count,
                        COALESCE((SELECT COUNT(*)::int FROM sustainability.common_area_lighting_assets WHERE scheme_id = :scheme_id AND status = 'active' AND COALESCE(control_type, '') NOT IN ('motion_sensor', 'dawn_dusk', 'timer_optimised')), 0) AS manual_lighting_asset_count
                    """
                ),
                {"scheme_id": ctx.scheme_id},
            )
        ).one()
        metrics = _row_to_dict(metrics_row)
        yearly_cost_cents = int(metrics["yearly_cost_cents"] or 0)
        opportunity_score = min(100.0, 35 + metrics["open_anomaly_count"] * 9 + metrics["manual_lighting_asset_count"] * 4)
        overall_score = max(15.0, 100.0 - opportunity_score)
        emissions_baseline_kg = payload.emissions_baseline_kg or round(max(12000.0, yearly_cost_cents / 180), 3)
        solar_level = payload.solar_readiness_level or ("high" if metrics["yearly_bill_count"] >= 6 else "medium")
        water_level = payload.water_efficiency_level or ("review_required" if metrics["water_risk_count"] >= 1 else "medium")
        lighting_level = payload.lighting_efficiency_level or ("low" if metrics["manual_lighting_asset_count"] >= 1 else "medium")

        profile_result = await session.execute(
            text(
                """
                INSERT INTO sustainability.building_sustainability_profiles
                    (tenant_id, scheme_id, overall_score, emissions_baseline_kg, solar_readiness_level,
                     water_efficiency_level, lighting_efficiency_level, notes, created_by, updated_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :overall_score, :emissions_baseline_kg, :solar_readiness_level,
                     :water_efficiency_level, :lighting_efficiency_level, :notes, :created_by, :updated_by,
                     :correlation_id)
                ON CONFLICT (scheme_id) DO UPDATE
                    SET overall_score = EXCLUDED.overall_score,
                        emissions_baseline_kg = EXCLUDED.emissions_baseline_kg,
                        solar_readiness_level = EXCLUDED.solar_readiness_level,
                        water_efficiency_level = EXCLUDED.water_efficiency_level,
                        lighting_efficiency_level = EXCLUDED.lighting_efficiency_level,
                        notes = EXCLUDED.notes,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now(),
                        source_correlation_id = EXCLUDED.source_correlation_id
                RETURNING id::text AS profile_id, overall_score, emissions_baseline_kg,
                          solar_readiness_level, water_efficiency_level, lighting_efficiency_level,
                          notes
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "overall_score": round(overall_score, 2),
                "emissions_baseline_kg": emissions_baseline_kg,
                "solar_readiness_level": solar_level,
                "water_efficiency_level": water_level,
                "lighting_efficiency_level": lighting_level,
                "notes": payload.notes or "Phase 2 sustainability assessment generated from utility activity and anomaly history.",
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        profile = _row_to_dict(profile_result.one())
        profile_uuid = _parse_uuid(profile["profile_id"], label="profile_id")

        await session.execute(
            text(
                """
                INSERT INTO sustainability.solar_readiness_assessments
                    (tenant_id, scheme_id, profile_id, assessment_status, roof_condition, switchboard_capacity,
                     metering_readiness, shading_rating, estimated_system_kw, created_by, updated_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :profile_id, 'review_required', :roof_condition, :switchboard_capacity,
                     :metering_readiness, :shading_rating, :estimated_system_kw, :created_by, :updated_by,
                     :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "profile_id": profile_uuid,
                "roof_condition": payload.roof_condition,
                "switchboard_capacity": payload.switchboard_capacity,
                "metering_readiness": payload.metering_readiness,
                "shading_rating": payload.shading_rating,
                "estimated_system_kw": payload.estimated_system_kw,
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )

        assessment_uuid = uuid4()
        confidence_score = round(min(0.92, 0.52 + ((metrics["yearly_bill_count"] + metrics["open_anomaly_count"]) * 0.03)), 4)
        risk_level = "high" if opportunity_score >= 70 else "medium" if opportunity_score >= 40 else "low"
        await session.execute(
            text(
                """
                INSERT INTO ai_assist.ai_assessments
                    (id, tenant_id, scheme_id, assessment_type, subject_type, subject_id, summary, status,
                     model_name, prompt_version, risk_level, confidence_score, pii_redacted, approval_required,
                     created_by, updated_by, source_correlation_id)
                VALUES
                    (:id, :tenant_id, :scheme_id, 'sustainability', 'building', :subject_id, :summary, 'pending_review',
                     'strataos-heuristic-v1', 'phase2-sustainability-v1', :risk_level, :confidence_score, TRUE, FALSE,
                     :created_by, :updated_by, :correlation_id)
                """
            ),
            {
                "id": assessment_uuid,
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "subject_id": ctx.scheme_id,
                "summary": f"Sustainability assessment generated from {metrics['yearly_bill_count']} utility bills and {metrics['open_anomaly_count']} open anomalies.",
                "risk_level": risk_level,
                "confidence_score": confidence_score,
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )

        recommendation_specs = [
            {
                "type": "sustainability_solar_readiness",
                "title": "Prepare a solar-readiness review pack",
                "text": "The current building profile suggests a formal solar-readiness review would clarify roof, switchboard, and metering constraints before any quote process starts.",
                "risk_level": "medium",
                "approval_required": False,
                "capex": 450000,
                "payback_months": 48,
                "emissions": 18500.0,
                "payload": _recommendation_payload(
                    project_type="solar_readiness",
                    problem_statement="The building has enough utility activity to justify confirming whether solar is viable.",
                    evidence_summary=f"{metrics['yearly_bill_count']} bills were available for the last year and solar readiness is currently '{solar_level}'.",
                    estimated_cost_range="$4,500-$25,000 planning and initial design scope",
                    owner_impact="Potential long-term levy savings; moderate need for owner communication during design and approval.",
                    disruption="Low during assessment, moderate if a later funded project proceeds.",
                    compliance_considerations="Any capital spend, roof works, or legal notices remain human-approved.",
                    approval_path="Manager review first, then EC approval before any funded design or procurement.",
                    communication_plan="Share a plain-language options summary with likely savings, disruption, and next approvals.",
                    capex_estimate_cents=450000,
                    payback_months=48,
                    emissions_savings_kg=18500.0,
                ),
            },
            {
                "type": "sustainability_lighting_automation",
                "title": "Scope common-area lighting automation",
                "text": "Common-area lighting assets still appear to rely on manual or non-optimised controls. A motion-sensor / dawn-dusk package should be evaluated.",
                "risk_level": "medium" if metrics["manual_lighting_asset_count"] >= 1 else "low",
                "approval_required": metrics["manual_lighting_asset_count"] >= 3,
                "capex": 1850000,
                "payback_months": 26,
                "emissions": 12450.0,
                "payload": _recommendation_payload(
                    project_type="lighting_upgrade",
                    problem_statement="Common-area lighting controls look under-optimised.",
                    evidence_summary=f"{metrics['manual_lighting_asset_count']} active lighting assets appear to be missing modern control types.",
                    estimated_cost_range="$8,000-$18,500 depending on control retrofits",
                    owner_impact="Likely positive if after-hours glare and power costs reduce.",
                    disruption="Low to medium because installation is usually short-duration but may affect common areas.",
                    compliance_considerations="Keep emergency-lighting and access-safety requirements in scope before implementation.",
                    approval_path="Manager review and EC approval before procurement or installation.",
                    communication_plan="Advise residents of installation windows and expected comfort / savings improvements.",
                    capex_estimate_cents=1850000,
                    payback_months=26,
                    emissions_savings_kg=12450.0,
                ),
            },
            {
                "type": "sustainability_water_efficiency",
                "title": "Investigate water-efficiency improvements",
                "text": "Water-related anomalies suggest the building would benefit from a water-efficiency review before future bills escalate further.",
                "risk_level": "high" if metrics["water_risk_count"] >= 2 else "medium",
                "approval_required": metrics["water_risk_count"] >= 2,
                "capex": 320000,
                "payback_months": 18,
                "emissions": 2100.0,
                "payload": _recommendation_payload(
                    project_type="water_efficiency",
                    problem_statement="Water anomalies indicate either waste or a developing leak / billing issue.",
                    evidence_summary=f"{metrics['water_risk_count']} water-related anomaly signals were detected.",
                    estimated_cost_range="$3,200-$12,000 depending on remediation and fixture scope",
                    owner_impact="Can reduce wastage and future cost shocks, but may require access to some lots or common services.",
                    disruption="Low during assessment; moderate if repairs or retrofits are needed.",
                    compliance_considerations="Avoid assigning resident fault without human investigation and evidence review.",
                    approval_path="Manager triage, then EC approval if funded remediation or retrofit is proposed.",
                    communication_plan="Use a resident-safe notice explaining that the investigation is preventative and evidence-led.",
                    capex_estimate_cents=320000,
                    payback_months=18,
                    emissions_savings_kg=2100.0,
                ),
            },
        ]

        recommendations: list[dict[str, Any]] = []
        top_anomalies = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS anomaly_id, title, anomaly_type
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
                        (id, tenant_id, scheme_id, assessment_id, recommendation_type, title, recommendation_text,
                         status, risk_level, confidence_score, proposed_action, approval_required,
                         created_by, updated_by, source_correlation_id)
                    VALUES
                        (:id, :tenant_id, :scheme_id, :assessment_id, :recommendation_type, :title, :recommendation_text,
                         'pending_review', :risk_level, :confidence_score, CAST(:proposed_action AS jsonb), :approval_required,
                         :created_by, :updated_by, :correlation_id)
                    """
                ),
                {
                    "id": recommendation_uuid,
                    "tenant_id": ctx.tenant_id,
                    "scheme_id": ctx.scheme_id,
                    "assessment_id": assessment_uuid,
                    "recommendation_type": spec["type"],
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
            if spec["type"] == "sustainability_lighting_automation":
                await session.execute(
                    text(
                        """
                        INSERT INTO sustainability.lighting_control_recommendations
                            (tenant_id, scheme_id, profile_id, title, recommendation_text, estimated_savings_kwh,
                             estimated_savings_cents, risk_level, status, approval_required, created_by, updated_by,
                             source_correlation_id)
                        VALUES
                            (:tenant_id, :scheme_id, :profile_id, :title, :recommendation_text, :estimated_savings_kwh,
                             :estimated_savings_cents, :risk_level, 'pending_review', :approval_required, :created_by, :updated_by,
                             :correlation_id)
                        """
                    ),
                    {
                        "tenant_id": ctx.tenant_id,
                        "scheme_id": ctx.scheme_id,
                        "profile_id": profile_uuid,
                        "title": spec["title"],
                        "recommendation_text": spec["text"],
                        "estimated_savings_kwh": round(spec["emissions"] / 0.8, 3),
                        "estimated_savings_cents": 240000,
                        "risk_level": spec["risk_level"],
                        "approval_required": spec["approval_required"],
                        "created_by": ctx.actor_user_id,
                        "updated_by": ctx.actor_user_id,
                        "correlation_id": correlation_id,
                    },
                )
            for anomaly_row in top_anomalies:
                anomaly = _row_to_dict(anomaly_row)
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
                        "source_id": anomaly["anomaly_id"],
                        "summary": anomaly["title"],
                        "excerpt": anomaly["anomaly_type"],
                        "correlation_id": correlation_id,
                    },
                )

            recommendation_row = (
                await session.execute(
                    text(
                        f"""
                        {_RECOMMENDATION_SELECT}
                        WHERE r.scheme_id = :scheme_id
                          AND r.id = :recommendation_id
                        """
                    ),
                    {"scheme_id": ctx.scheme_id, "recommendation_id": recommendation_uuid},
                )
            ).first()
            recommendations.append(_normalise_recommendation(recommendation_row))

        await _insert_audit_event(
            session,
            ctx,
            entity_type="sustainability_profile",
            entity_id=profile_uuid,
            action="sustainability.assessment.generated",
            payload={
                "assessment_id": str(assessment_uuid),
                "overall_score": profile["overall_score"],
                "recommendation_count": len(recommendations),
                "correlation_id": correlation_id,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return {
            "assessment_id": str(assessment_uuid),
            "profile_id": profile["profile_id"],
            "overall_score": float(profile["overall_score"]) if profile.get("overall_score") is not None else None,
            "emissions_baseline_kg": float(profile["emissions_baseline_kg"]) if profile.get("emissions_baseline_kg") is not None else None,
            "solar_readiness_level": profile.get("solar_readiness_level"),
            "water_efficiency_level": profile.get("water_efficiency_level"),
            "lighting_efficiency_level": profile.get("lighting_efficiency_level"),
            "notes": profile.get("notes"),
            "recommendations": recommendations,
        }


async def list_sustainability_recommendations(
    *,
    building_id: str,
    current_user: dict,
    status_filter: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Generated function header.

    Function: list_sustainability_recommendations
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)

    clauses = ["r.scheme_id = :scheme_id", "r.deleted_at IS NULL", "r.recommendation_type = ANY(:recommendation_types)"]
    params: dict[str, Any] = {
        "scheme_id": ctx.scheme_id,
        "recommendation_types": list(_SUSTAINABILITY_TYPES),
        "limit": limit,
        "offset": offset,
    }
    if status_filter:
        clauses.append("r.status = :status_filter")
        params["status_filter"] = status_filter

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


async def convert_recommendation_to_project(
    *,
    building_id: str,
    recommendation_id: str,
    current_user: dict,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: convert_recommendation_to_project
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    recommendation_uuid = _parse_uuid(recommendation_id, label="recommendation_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        result = await session.execute(
            text(
                f"""
                {_RECOMMENDATION_SELECT}
                WHERE r.scheme_id = :scheme_id
                  AND r.id = :recommendation_id
                  AND r.recommendation_type = ANY(:recommendation_types)
                """
            ),
            {
                "scheme_id": ctx.scheme_id,
                "recommendation_id": recommendation_uuid,
                "recommendation_types": list(_SUSTAINABILITY_TYPES),
            },
        )
        row = result.first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sustainability recommendation not found.")
        recommendation = _normalise_recommendation(row)
        if recommendation["status"] != "approved":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recommendation must be approved before project conversion.")
        if recommendation["approval_required"] and recommendation.get("approval_status") != "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Recommendation still requires approved human review before project conversion.",
            )

        proposed_action = recommendation["proposed_action"]
        profile_row = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS profile_id
                    FROM sustainability.building_sustainability_profiles
                    WHERE scheme_id = :scheme_id
                      AND deleted_at IS NULL
                    LIMIT 1
                    """
                ),
                {"scheme_id": ctx.scheme_id},
            )
        ).first()
        profile_uuid = _parse_uuid(profile_row.profile_id, label="profile_id") if profile_row else None
        project_result = await session.execute(
            text(
                """
                INSERT INTO sustainability.energy_efficiency_projects
                    (tenant_id, scheme_id, profile_id, project_type, title, description, status,
                     capex_estimate_cents, payback_months, emissions_savings_kg, created_by, updated_by,
                     source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :profile_id, :project_type, :title, :description, 'draft',
                     :capex_estimate_cents, :payback_months, :emissions_savings_kg, :created_by, :updated_by,
                     :correlation_id)
                RETURNING id
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "profile_id": profile_uuid,
                "project_type": proposed_action.get("project_type", "energy_efficiency"),
                "title": recommendation["title"],
                "description": recommendation["recommendation_text"],
                "capex_estimate_cents": proposed_action.get("capex_estimate_cents"),
                "payback_months": proposed_action.get("payback_months"),
                "emissions_savings_kg": proposed_action.get("emissions_savings_kg"),
                "created_by": ctx.actor_user_id,
                "updated_by": ctx.actor_user_id,
                "correlation_id": correlation_id,
            },
        )
        project_uuid = project_result.one().id
        await session.execute(
            text(
                """
                INSERT INTO sustainability.sustainability_project_benefit_cases
                    (tenant_id, scheme_id, project_id, scenario_name, assumptions, benefit_summary, npv_cents,
                     roi_percentage, source_correlation_id)
                VALUES
                    (:tenant_id, :scheme_id, :project_id, 'base_case', CAST(:assumptions AS jsonb), :benefit_summary,
                     :npv_cents, :roi_percentage, :correlation_id)
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "project_id": project_uuid,
                "assumptions": _json_dump(
                    {
                        "estimated_cost_range": proposed_action.get("estimated_cost_range"),
                        "payback_months": proposed_action.get("payback_months"),
                        "evidence_summary": proposed_action.get("evidence_summary"),
                    }
                ),
                "benefit_summary": proposed_action.get("suggested_communication_plan"),
                "npv_cents": int((proposed_action.get("capex_estimate_cents") or 0) * 0.55) if proposed_action.get("capex_estimate_cents") else None,
                "roi_percentage": round(100 / max(1, proposed_action.get("payback_months") or 1), 3),
                "correlation_id": correlation_id,
            },
        )
        await session.execute(
            text(
                """
                UPDATE ai_assist.ai_recommendations
                SET status = 'executed',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :recommendation_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "recommendation_id": recommendation_uuid, "scheme_id": ctx.scheme_id},
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="sustainability_project",
            entity_id=project_uuid,
            action="sustainability.project.created_from_recommendation",
            payload={"recommendation_id": recommendation["recommendation_id"], "correlation_id": correlation_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await _fetch_project(session, ctx, project_uuid)


async def submit_project_approval(
    *,
    building_id: str,
    project_id: str,
    current_user: dict,
    payload: SustainabilityProjectApprovalRequest,
    correlation_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """Generated function header.

    Function: submit_project_approval
    Path: backend/services/sustainability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    ctx = await _resolve_context(building_id, current_user)
    _require_manager(ctx)
    project_uuid = _parse_uuid(project_id, label="project_id")

    async with async_session_context() as session:
        await set_tenant(session, ctx.tenant_id)
        project = await _fetch_project(session, ctx, project_uuid)
        if project["status"] not in {"draft", "rejected"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project cannot be submitted for approval from its current status.")
        if project.get("approval_request_id"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project already has an approval request.")

        policy = await _fetch_policy(session, ctx, payload.policy_code or _PROJECT_POLICY_CODE)
        policy, required_roles = _require_policy_roles(
            policy,
            detail="Approval policy is missing or has no approver roles for sustainability projects.",
        )
        result = await session.execute(
            text(
                """
                INSERT INTO core.approval_requests
                    (tenant_id, scheme_id, entity_type, entity_id, approval_policy_id, status, requested_by)
                VALUES
                    (:tenant_id, :scheme_id, 'sustainability_project', :entity_id, :approval_policy_id, 'pending', :requested_by)
                RETURNING approval_request_id::text AS approval_request_id,
                          status,
                          requested_by::text AS requested_by,
                          created_at AS requested_at
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "scheme_id": ctx.scheme_id,
                "entity_id": project_uuid,
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
                UPDATE sustainability.energy_efficiency_projects
                SET status = 'in_review',
                    updated_by = :updated_by,
                    updated_at = now()
                WHERE id = :project_id
                  AND scheme_id = :scheme_id
                """
            ),
            {"updated_by": ctx.actor_user_id, "project_id": project_uuid, "scheme_id": ctx.scheme_id},
        )
        await _insert_audit_event(
            session,
            ctx,
            entity_type="sustainability_project",
            entity_id=project_uuid,
            action="sustainability.project.approval_requested",
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
        row["project_id"] = project_id
        row["policy_code"] = policy["policy_code"]
        row["required_roles"] = required_roles
        return row
