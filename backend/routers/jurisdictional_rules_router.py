"""
# @featuretrace:jurisdictional-rules — FastAPI router exposing statutory rule data per building.
# Layer: router
# Data flow: JurisdictionalRuleEngine + JurisdictionService → GET /api/jurisdictional-rules/
# Related: backend/domain/jurisdictional_rules.py
#           backend/services/jurisdiction_service.py
#           frontend/src/pages/dashboard/admin/JurisdictionalRulesPage.tsx
# Scope: (building-scoped)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from database import db
from domain.jurisdictional_rules import rule_engine
from models.user import UserRole
from services.capability_registry import require_capability
from services.jurisdiction_service import JurisdictionService
from utils.auth import effective_role, get_approved_user, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jurisdictional-rules", tags=["Jurisdictional Rules"])


def _require_admin_or_manager(current_user: dict = Depends(get_approved_user)) -> dict:
    """Rank-only gate for the endpoints that expose NO building-scoped data.

    This checks *what role you are*, never *which building you may see*. It is
    therefore only safe on routes whose response is identical for every tenant —
    ``GET /all``, which returns the statutory rule set for each supported
    jurisdiction straight from the pure domain engine and touches no
    building-scoped collection.

    ``GET /`` must NOT use this. That route reads
    ``jurisdiction_config.rule_overrides`` for a caller-supplied ``building_id``
    and so needs an object-level authorisation check against that specific
    building — see ``require_capability("building.jurisdiction.view", ...)`` on
    the route itself (OWASP API1:2023). Role rank alone let a strata manager of
    building A read building B's overrides.
    """
    _role = effective_role(current_user)
    # ec_member and strata_admin are deliberately absent — see
    # tests/backend/routers/test_jurisdictional_rules_router.py::TestRoleGuard.
    allowed = {UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER}
    if _role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin or manager required.")
    return current_user


@router.get("/")
async def get_building_rules(
        building_id: str = Query(..., description="Building ID to resolve effective jurisdiction"),
        current_user: dict = Depends(
            require_capability(
                "building.jurisdiction.view",
                scope_params={"building_id": "building_id"},
            )
        ),
):
    """Return the effective statutory rules for a building.

    Resolves the jurisdiction for the building (ACT default for existing buildings),
    then returns the full rules dict including per-building overrides.
    Includes engine-typed method results for the common domain queries.

    Authorisation (BOLA / OWASP API1:2023): ``building_id`` is caller-supplied,
    so the decision is made against *that* building, not against the caller's
    session building. ``require_capability`` reads the value out of the query
    string, hydrates the caller's verified building assignments from
    ``core.user_role_assignments``, and denies unless the caller is assigned to
    it (super_admin excepted). Do not swap this for a rank-only role check:
    ``jurisdiction_config`` is tenant-scoped, but ``TenantScopedDatabase``
    deliberately does not re-inject a building filter when the query already
    names one, so nothing downstream would catch a foreign ``building_id``.
    """
    js = JurisdictionService(db)
    # Single DB call: resolves jurisdiction and fetches overrides in one find_one
    try:
        jurisdiction, effective = await js.get_state_and_all_rules(building_id)
        meta = rule_engine.get_meta(jurisdiction)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    # Typed domain method results for common queries
    proxy_cap_scheme_pct = rule_engine.proxy_cap_scheme_pct(jurisdiction)
    typed = {
        "max_interest_rate_pa": str(rule_engine.max_interest_rate_pa(jurisdiction)),
        "interest_grace_period_days": rule_engine.interest_grace_period_days(jurisdiction),
        "can_transfer_between_funds": rule_engine.can_transfer_between_funds(jurisdiction),
        "inter_fund_transfer_resolution_required": rule_engine.inter_fund_transfer_resolution_required(jurisdiction),
        "overbudget_threshold_pct": (
            str(rule_engine.overbudget_threshold_pct(jurisdiction))
            if rule_engine.overbudget_threshold_pct(jurisdiction) is not None
            else None
        ),
        "trust_withdrawal_authority_roles": rule_engine.trust_withdrawal_authority_roles(jurisdiction),
        "trial_balance_deadline_days": rule_engine.trial_balance_deadline_days(jurisdiction),
        "audit_deadline": rule_engine.audit_deadline(jurisdiction),
        "pooled_trust_permitted": rule_engine.pooled_trust_permitted(jurisdiction),
        "interest_belongs_to": rule_engine.interest_belongs_to(jurisdiction),
        "committee_spending_cap_formula": rule_engine.committee_spending_cap_formula(jurisdiction),
        "min_withholding_pct_no_abn": str(rule_engine.min_withholding_pct_no_abn(jurisdiction)),
        # GAP-GOV-001: AGM / e-voting
        "agm_notice_period_days": rule_engine.agm_notice_period_days(jurisdiction),
        "evoting_permitted": rule_engine.evoting_permitted(jurisdiction),
        # GAP-GOV-001: Proxy caps
        "proxy_cap_scheme_pct": str(proxy_cap_scheme_pct) if proxy_cap_scheme_pct is not None else None,
        "proxy_cap_small_scheme_lots": rule_engine.proxy_cap_small_scheme_lots(jurisdiction),
        "proxy_cap_scheme_size_threshold": rule_engine.proxy_cap_scheme_size_threshold(jurisdiction),
        # GAP-JUR-NSW-013: Building manager duties register
        "building_manager_duties_register_required": rule_engine.building_manager_duties_register_required(jurisdiction),
        # GAP-JUR-NSW-005: Initial maintenance schedule
        "initial_maintenance_schedule_required": rule_engine.initial_maintenance_schedule_required(jurisdiction),
        "initial_maintenance_schedule_deadline_months": rule_engine.initial_maintenance_schedule_deadline_months(jurisdiction),
        # GAP-JUR-NSW-003: Manager report
        "manager_report_period_months": rule_engine.manager_report_period_months(jurisdiction),
        "manager_report_retention_years": rule_engine.manager_report_retention_years(jurisdiction),
        # GAP-SEC-004: Privacy / DSAR
        "privacy_dsar_response_days": rule_engine.privacy_dsar_response_days(jurisdiction),
    }

    return {
        "building_id": building_id,
        "jurisdiction": jurisdiction,
        "meta": meta,
        "typed_rules": typed,
        "full_rules": effective,
    }


@router.get("/all")
async def get_all_jurisdictions(
        current_user: dict = Depends(_require_admin_or_manager),
):
    """Return the statutory rules for all supported jurisdictions.

    Useful for admin comparison views and multi-jurisdiction onboarding.
    """
    result = {}
    for jur in rule_engine.valid_jurisdictions():
        try:
            result[jur] = {
                "meta": rule_engine.get_meta(jur),
                "typed_rules": {
                    "max_interest_rate_pa": str(rule_engine.max_interest_rate_pa(jur)),
                    "interest_grace_period_days": rule_engine.interest_grace_period_days(jur),
                    "can_transfer_between_funds": rule_engine.can_transfer_between_funds(jur),
                    "inter_fund_transfer_resolution_required": rule_engine.inter_fund_transfer_resolution_required(jur),
                    "overbudget_threshold_pct": (
                        str(rule_engine.overbudget_threshold_pct(jur))
                        if rule_engine.overbudget_threshold_pct(jur) is not None
                        else None
                    ),
                    "trust_withdrawal_authority_roles": rule_engine.trust_withdrawal_authority_roles(jur),
                    "trial_balance_deadline_days": rule_engine.trial_balance_deadline_days(jur),
                    "audit_deadline": rule_engine.audit_deadline(jur),
                    "pooled_trust_permitted": rule_engine.pooled_trust_permitted(jur),
                    "interest_belongs_to": rule_engine.interest_belongs_to(jur),
                    "committee_spending_cap_formula": rule_engine.committee_spending_cap_formula(jur),
                },
            }
        except ValueError:
            continue
    return {"jurisdictions": result}


@router.get("/supported")
async def list_supported_jurisdictions(
        current_user: dict = Depends(get_current_user),
):
    """Return the list of supported jurisdiction codes."""
    return {"jurisdictions": rule_engine.valid_jurisdictions()}
