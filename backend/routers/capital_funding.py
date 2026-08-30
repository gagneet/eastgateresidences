"""
Capital Funding & Special Levy Workspace — preview-only calculation API.

# @featuretrace:capital-funding — pure preview calculator for capital-works/special-levy funding
# scenarios (special levy, staged levy, strata loan, mixed). Never mutates a live ledger.
# Layer: router
# Data flow: CapitalFundingPage.tsx -> POST /finance/capital-funding/preview -> domain/capital_funding.py
#            (pure calculation) + domain/jurisdictional_rules.py (rule summary) -> response, no DB writes.
# Related: backend/domain/capital_funding.py
#          backend/domain/jurisdictional_rules.py
#          tasks/GAP-FIN-034-capital-funding-special-levy-workspace.md

GAP-FIN-034 "Next deliverable A": replaces the staff page's local-only calculator with a
backend preview contract. Performs NO writes of any kind — no `finance.levy_items`, no owner
credits, no receipts, no journals. Gated behind `capital_funding_workspace` (default-disabled).

Endpoints:
  POST /finance/capital-funding/preview — model a funding scenario, no writes
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from domain.capital_funding import (
    FundingFrequency,
    FundingRequirement,
    LotFundingInput,
    funding_requirement_cents,
    model_lot_impacts,
)
from domain.jurisdictional_rules import rule_engine
from utils.permissions import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

# Matches GAP-FIN-034's capital_funding_workspace toggle table (staff workspace roles).
# require_feature() only gates the toggle itself -- its allowed_roles metadata is
# descriptive, not enforced -- so this is an explicit, separate check.
_ALLOWED_ROLES = {"super_admin", "strata_admin", "strata_manager", "admin_staff"}


def _effective_role(user: dict) -> str:
    return user.get("effective_role") or user.get("role", "guest")


def _require_capital_funding_role(user: dict) -> None:
    if _effective_role(user) not in _ALLOWED_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Capital funding workspace access requires super_admin, strata_admin, "
                   "strata_manager, or admin_staff role.",
        )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CapitalFundingLotChoiceRequest(BaseModel):
    unit_number: str
    entitlement: int = Field(gt=0)
    owner_choice: Literal["loan", "upfront"] = "loan"


class CapitalFundingPreviewRequest(BaseModel):
    jurisdiction: str = Field(min_length=2, max_length=3)
    project_cost_cents: int = Field(ge=0)
    gst_cents: int = Field(default=0, ge=0)
    professional_fees_cents: int = Field(default=0, ge=0)
    contingency_cents: int = Field(default=0, ge=0)
    insurance_recoveries_cents: int = Field(default=0, ge=0)
    grants_cents: int = Field(default=0, ge=0)
    existing_fund_contribution_cents: int = Field(default=0, ge=0)
    lots: list[CapitalFundingLotChoiceRequest] = Field(min_length=1)
    annual_interest_bps: int = Field(default=0, ge=0)
    term_months: int = Field(default=0, ge=0)
    repayment_frequency: FundingFrequency = "quarterly"
    establishment_fee_cents: int = Field(default=0, ge=0)
    annual_fee_cents: int = Field(default=0, ge=0)
    hybrid_requested: bool = False
    allocation_basis: Literal["entitlement", "benefit_principle"] = "entitlement"

    @field_validator("jurisdiction")
    @classmethod
    def _uppercase_jurisdiction(cls, v: str) -> str:
        return v.upper()


class CapitalFundingLotImpactResponse(BaseModel):
    unit_number: str
    entitlement: int
    owner_choice: str
    project_share_cents: int
    upfront_cents: int
    financed_principal_cents: int
    interest_cents: int
    fees_cents: int
    periodic_payment_cents: int
    payment_count: int
    total_cost_cents: int


class CapitalFundingPreviewResponse(BaseModel):
    preview_only: bool = True
    funding_requirement_cents: int
    lot_impacts: list[CapitalFundingLotImpactResponse]
    reconciliation_total_cents: int
    rule_summary: dict
    warnings: list[str]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/finance/capital-funding/preview", response_model=CapitalFundingPreviewResponse)
async def preview_capital_funding(
        payload: CapitalFundingPreviewRequest,
        current_user: dict = Depends(require_feature("capital_funding_workspace")),
) -> CapitalFundingPreviewResponse:
    """Model a capital-funding scenario (special levy / staged levy / strata loan / mixed).

    Preview-only: performs NO writes. Never touches `finance.levy_items`, owner credits,
    receipts, or journals, and never sends a notice/email — this is a pure calculation over
    caller-supplied inputs, per CLAUDE.md's "Preview generation must not mutate live ledgers"
    rule and GAP-FIN-034's own non-negotiable rules.

    Deliberately does NOT depend on get_current_building(): this endpoint reads no
    building-scoped data (every figure is caller-supplied or a pure domain calculation), and
    require_feature()'s own toggle check already resolves building scope internally from
    current_user when needed (get_effective_feature_access() reads user["building_id"]
    directly, and unconditionally allows super_admin regardless of building context). Adding a
    separate get_current_building() dependency here (an earlier version of this file did) would
    only add an extra, unnecessary failure mode -- it would 403 a super_admin with no single
    resolvable default building and no X-Building-ID header set, even though nothing in this
    handler actually needs a building_id. Found and removed during a 2026-08-19 audit.
    """
    _require_capital_funding_role(current_user)

    warnings: list[str] = []

    requirement = FundingRequirement(
        project_cost_cents=payload.project_cost_cents,
        gst_cents=payload.gst_cents,
        professional_fees_cents=payload.professional_fees_cents,
        contingency_cents=payload.contingency_cents,
        insurance_recoveries_cents=payload.insurance_recoveries_cents,
        grants_cents=payload.grants_cents,
        existing_fund_contribution_cents=payload.existing_fund_contribution_cents,
    )
    requirement_cents = funding_requirement_cents(requirement)

    lots = [
        LotFundingInput(
            unit_number=lot.unit_number,
            entitlement=lot.entitlement,
            owner_choice=lot.owner_choice,
        )
        for lot in payload.lots
    ]

    impacts = model_lot_impacts(
        requirement_cents=requirement_cents,
        lots=lots,
        annual_interest_bps=payload.annual_interest_bps,
        term_months=payload.term_months,
        repayment_frequency=payload.repayment_frequency,
        establishment_fee_cents=payload.establishment_fee_cents,
        annual_fee_cents=payload.annual_fee_cents,
    )

    rule_summary = rule_engine.capital_funding_rules_summary(payload.jurisdiction)

    # Use the dedicated typed accessors for warning LOGIC (capital_funding_rules_summary()
    # returns each rule as a {value, unit, legislation, effective, ...} citation dict for
    # UI/docs display, not a bare scalar -- reading .get(key) is None directly against that
    # would never trigger, since the dict itself is always present even when its own
    # "value" is None).
    notice_days = rule_engine.levy_notice_period_days(payload.jurisdiction)
    if notice_days is None:
        warnings.append(
            f"{payload.jurisdiction} has no platform-verified capital-funding notice period — "
            "legal-review evidence and approved motion text are required before this jurisdiction's "
            "notice period can be used, never a fallback number."
        )

    if payload.hybrid_requested:
        warnings.append(
            "Hybrid/self-funding structures require legal-review evidence, tax/accounting review, "
            "lender terms, and dual approval before use beyond this preview — enable "
            "hybrid_funding_structures explicitly once that evidence exists."
        )

    if payload.allocation_basis != "entitlement" and not rule_engine.capital_funding_benefit_principle_available(
        payload.jurisdiction
    ):
        warnings.append(
            f"{payload.jurisdiction}'s rule set does not confirm a benefit-principle allocation basis "
            "is available — a non-entitlement allocation needs its own statutory basis and legal "
            "evidence before use beyond this preview."
        )

    reconciliation_total_cents = sum(impact.project_share_cents for impact in impacts)
    if reconciliation_total_cents != requirement_cents:
        # Should be unreachable given allocate_cents_by_entitlement's own largest-remainder
        # guarantee -- surfaced as a warning, not silently swallowed, if it ever isn't.
        warnings.append(
            f"Per-lot project-share allocation (${reconciliation_total_cents / 100:,.2f}) does not "
            f"sum to the funding requirement (${requirement_cents / 100:,.2f}) — do not trust this preview."
        )

    return CapitalFundingPreviewResponse(
        funding_requirement_cents=requirement_cents,
        lot_impacts=[
            CapitalFundingLotImpactResponse(
                unit_number=impact.unit_number,
                entitlement=impact.entitlement,
                owner_choice=impact.owner_choice,
                project_share_cents=impact.project_share_cents,
                upfront_cents=impact.upfront_cents,
                financed_principal_cents=impact.financed_principal_cents,
                interest_cents=impact.interest_cents,
                fees_cents=impact.fees_cents,
                periodic_payment_cents=impact.periodic_payment_cents,
                payment_count=impact.payment_count,
                total_cost_cents=impact.total_cost_cents,
            )
            for impact in impacts
        ],
        reconciliation_total_cents=reconciliation_total_cents,
        rule_summary=rule_summary,
        warnings=warnings,
    )
