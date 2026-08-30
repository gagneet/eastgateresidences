"""
Insurance & Lending Intelligence Router — Phase 2 (Workstream E5)

Endpoints:
  GET /intelligence/insurance/risk-signals          → InsuranceRiskSignals
  GET /intelligence/lending/valuation-signals/{unit_number}  → LendingValuationSignals

Role gating: super_admin, chairman, strata_manager, ec_member.
All endpoints scope to building_id from JWT.
"""
from datetime import datetime, timezone

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from database import db
from utils.auth import effective_role, get_current_user, get_current_building

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Insurance & Lending Intelligence"])

_AUTHORISED_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member"}


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


# ── Models ─────────────────────────────────────────────────────────────────

class InsuranceRiskSignals(BaseModel):
    building_id: str
    stress_score: float
    stress_category: str
    reserve_adequacy_pct: float
    arrears_rate_pct: float
    unreconciled_exposure_pct: float
    maintenance_pressure_pct: float
    capital_shock_index: float
    active_claims_count: int
    open_maintenance_high_priority: int
    last_building_inspection_date: Optional[str]
    building_age_years: Optional[int]
    risk_flags: list[str]
    insurance_risk_tier: str  # "low" | "medium" | "high" | "critical"
    recommended_review: bool
    review_rationale: list[str]
    data_completeness: float
    generated_at: datetime


class LendingValuationSignals(BaseModel):
    unit_number: str
    building_id: str
    investor_grade: str  # A | B | C | D
    lending_risk_tier: str  # "standard" | "elevated" | "high_risk"
    stress_score: float
    stress_category: str
    reserve_adequacy_pct: float
    arrears_rate_pct: float
    sinking_fund_deficit_cents: int
    has_active_litigation: bool
    litigation_notes: list[str]
    compliance_score_pct: float
    levy_cagr_5yr_pct: float
    capital_shock_buffer_per_unit_cents: int
    current_annual_tco_cents: int
    valuation_risk_flags: list[str]
    positive_signals: list[str]
    lender_advisory_notes: list[str]
    data_completeness: float
    generated_at: datetime


# ── Internal helpers ───────────────────────────────────────────────────────

async def _fetch_stress(building_id: str) -> dict:
    """Generated function header.

    Function: _fetch_stress
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from services.building_stress_service import compute_phase2_stress_score
        return await compute_phase2_stress_score(building_id)
    except Exception as exc:
        logger.warning("Stress score unavailable: %s", exc)
        return {
            "score": 50.0,
            "category": "watch",
            "components": {},
            "data_completeness": 0.0,
        }


def _extract_component_raw(stress: dict, component: str) -> Optional[float]:
    """Generated function header.

    Function: _extract_component_raw
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return stress.get("components", {}).get(component, {}).get("raw_value")


async def _get_reserve_adequacy_pct(building_id: str, stress: dict) -> float:
    """Generated function header.

    Function: _get_reserve_adequacy_pct
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = _extract_component_raw(stress, "reserve_adequacy")
    if raw is not None:
        return round(float(raw) * 100, 1)
    try:
        from services.building_stress_service import compute_reserve_adequacy_ratio
        ra = await compute_reserve_adequacy_ratio(building_id)
        return round(float(ra.get("reserve_adequacy_ratio", 0.5)) * 100, 1)
    except Exception:
        return 50.0


async def _get_sinking_deficit_cents(building_id: str) -> int:
    """Admin + sinking fund deficit in cents (positive = deficit, 0 = surplus)."""
    try:
        from services.building_stress_service import compute_reserve_adequacy_ratio
        ra = await compute_reserve_adequacy_ratio(building_id)
        deficit = float(ra.get("deficit", 0) or 0)
        return int(deficit * 100)  # dollars to cents
    except Exception:
        return 0


async def _get_active_claims(building_id: str) -> int:
    """Generated function header.

    Function: _get_active_claims
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return await db.insurance_claims.count_documents(
            {"building_id": building_id, "status": {"$in": ["open", "active", "pending"]}}
        )
    except Exception:
        return 0


async def _get_open_high_priority_maintenance(building_id: str) -> int:
    """Generated function header.

    Function: _get_open_high_priority_maintenance
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return await db.maintenance_requests.count_documents({
            "building_id": building_id,
            "status": {"$nin": ["completed", "closed", "rejected"]},
            "priority": {"$in": ["high", "urgent", "critical"]},
        })
    except Exception:
        return 0


async def _get_last_inspection_date(building_id: str) -> Optional[str]:
    """Last building inspection from maintenance, audits, or work orders."""
    try:
        doc = await db.maintenance_requests.find_one(
            {"building_id": building_id, "category": {"$regex": "inspect", "$options": "i"},
             "status": "completed"},
            sort=[("completed_date", -1)]
        )
        if doc:
            val = doc.get("completed_date") or doc.get("updated_at")
            return str(val) if val else None
    except Exception as exc:
        logger.warning("insurance_lending: last inspection date fetch failed: %s", exc)
    return None


async def _get_building_age(building_id: str) -> Optional[int]:
    """Generated function header.

    Function: _get_building_age
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        building = await db.buildings.find_one({"id": building_id}, {"_id": 0, "built_year": 1, "year_built": 1})
        if not building:
            building = await db.buildings.find_one({"building_id": building_id},
                                                   {"_id": 0, "built_year": 1, "year_built": 1})
        if building:
            yr = building.get("built_year") or building.get("year_built")
            if yr:
                return datetime.now(timezone.utc).year - int(yr)
    except Exception as exc:
        logger.warning("insurance_lending: building age fetch failed: %s", exc)
    return None


async def _check_active_litigation(building_id: str) -> tuple[bool, list[str]]:
    """Check for active litigation in work_orders, decisions, or compliance_registers."""
    notes: list[str] = []
    try:
        lit_count = await db.work_orders.count_documents({
            "building_id": building_id,
            "category": {"$regex": "litigation|legal|dispute", "$options": "i"},
            "status": {"$nin": ["completed", "closed", "resolved"]},
        })
        if lit_count:
            notes.append(f"{lit_count} open legal/dispute work order(s).")
    except Exception as exc:
        logger.warning("insurance_lending: litigation work-order check failed: %s", exc)

    try:
        # Check decisions for unresolved legal matters
        legal_decisions = await db.decisions.count_documents({
            "building_id": building_id,
            "category": {"$regex": "legal|litigation|tribunal", "$options": "i"},
            "status": {"$in": ["open", "pending", "active"]},
        })
        if legal_decisions:
            notes.append(f"{legal_decisions} pending legal decision(s).")
    except Exception as exc:
        logger.warning("insurance_lending: litigation decisions check failed: %s", exc)

    return len(notes) > 0, notes


async def _get_compliance_score(building_id: str) -> float:
    """Generated function header.

    Function: _get_compliance_score
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        doc = await db.compliance_scores.find_one(
            {"building_id": building_id}, sort=[("computed_at", -1)]
        )
        if doc:
            return float(doc.get("score_pct", doc.get("overall_score", 75)) or 75)
    except Exception as exc:
        logger.warning("insurance_lending: compliance score fetch failed: %s", exc)
    return 75.0


async def _get_capital_shock_per_unit(building_id: str) -> tuple[float, int]:
    """Return (csi_value, per_unit_buffer_cents)."""
    try:
        from services.capital_shock_service import detect_capital_shocks
        shock = await detect_capital_shocks(building_id)
        csi_info = shock.get("capital_shock_index", {})
        if isinstance(csi_info, dict):
            csi_value = float(csi_info.get("capital_shock_index", 0) or 0)
        else:
            csi_value = float(csi_info or 0)
        total = float(shock.get("total_exposure", 0) or 0)
        total_units = max(await db.units.count_documents({"building_id": building_id}), 1)
        per_unit = int(total / total_units * 100)
        return csi_value, per_unit
    except Exception:
        return 0.0, 0


async def _get_levy_cagr(building_id: str) -> float:
    """Generated function header.

    Function: _get_levy_cagr
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        pipeline = [
            {"$match": {"building_id": building_id}},
            {"$sort": {"year": 1}},
            {"$limit": 6},
            {"$group": {
                "_id": "$year",
                "income": {"$sum": {
                    "$add": [
                        {"$ifNull": ["$admin_fund.levy_income", 0]},
                        {"$ifNull": ["$sinking_fund.levy_income", 0]},
                    ]
                }}
            }},
            {"$sort": {"_id": 1}},
        ]
        records = await db.annual_levies.aggregate(pipeline).to_list(6)
        if len(records) >= 2:
            first = float(records[0]["income"] or 1)
            last = float(records[-1]["income"] or 1)
            n = len(records) - 1
            if first > 0 and n > 0:
                return round(((last / first) ** (1 / n) - 1) * 100, 2)
    except Exception as exc:
        logger.warning("insurance_lending: levy CAGR computation failed: %s", exc)
    return 0.0


async def _get_tco_annual(building_id: str, unit_number: str) -> int:
    """Generated function header.

    Function: _get_tco_annual
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from services.true_cost_service import TrueCostOfOwnershipInput, compute_true_cost_of_ownership
        from utils.finance_helpers import get_latest_levy_year
        # "current" never matched unit_levy_ledger.year, silently defeating
        # _get_building_average's year-scoped query — resolve a real year instead.
        year = await get_latest_levy_year(building_id) or str(datetime.now(timezone.utc).year)
        inp = TrueCostOfOwnershipInput(
            building_id=building_id,
            unit_number=unit_number,
            holding_period_years=1,
            financial_year=year,
        )
        result = await compute_true_cost_of_ownership(inp)
        return result.total_annual_cents
    except Exception:
        return 0


def _insurance_risk_tier(
        stress_score: float,
        stress_category: str,
        reserve_adequacy_pct: float,
        active_claims: int,
        open_high_prio: int,
        csi: float,
) -> tuple[str, list[str]]:
    """Generated function header.

    Function: _insurance_risk_tier
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    flags: list[str] = []
    if stress_category == "critical" or stress_score >= 75:
        flags.append(f"Critical stress score ({stress_score:.1f}).")
    if reserve_adequacy_pct < 50:
        flags.append(f"Sinking fund severely under-funded ({reserve_adequacy_pct:.1f}%).")
    elif reserve_adequacy_pct < 75:
        flags.append(f"Sinking fund under-funded ({reserve_adequacy_pct:.1f}%).")
    if active_claims > 0:
        flags.append(f"{active_claims} active insurance claim(s).")
    if open_high_prio > 5:
        flags.append(f"{open_high_prio} high-priority maintenance items outstanding.")
    if csi >= 2.0:
        flags.append(f"Capital Shock Index {csi:.2f} indicates major capital event risk.")

    if stress_category == "critical" or reserve_adequacy_pct < 50 or active_claims >= 3:
        tier = "critical"
    elif stress_category == "elevated" or reserve_adequacy_pct < 75 or csi >= 2.0:
        tier = "high"
    elif stress_category == "watch" or active_claims > 0 or open_high_prio > 3:
        tier = "medium"
    else:
        tier = "low"

    return tier, flags


def _lending_risk_tier(
        grade: str,
        deficit_cents: int,
        has_litigation: bool,
        arrears_rate_pct: float,
) -> str:
    """
    standard:  grade A/B, no deficit, no active litigation
    elevated:  grade C, minor deficit (<$50k), or arrears 10-20%
    high_risk: grade D, persistent deficit (≥$50k), or arrears >20%
    """
    ELEVATED_RISK_DEFICIT_THRESHOLD = 5_000_000  # 50,000 dollars in cents
    if (grade in ("A", "B")
            and deficit_cents == 0
            and not has_litigation
            and arrears_rate_pct < 10):
        return "standard"
    if (grade == "D"
            or deficit_cents >= ELEVATED_RISK_DEFICIT_THRESHOLD
            or arrears_rate_pct > 20):
        return "high_risk"
    return "elevated"


def _lender_advisory_notes(
        lending_tier: str,
        grade: str,
        reserve_adequacy_pct: float,
        arrears_rate_pct: float,
        has_litigation: bool,
        compliance_score_pct: float,
) -> list[str]:
    """Generated function header.

    Function: _lender_advisory_notes
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    notes: list[str] = []
    if lending_tier == "standard":
        notes.append("Building financials support standard lending assessment.")
    elif lending_tier == "elevated":
        notes.append("Elevated risk; recommend lender review of strata financials pre-settlement.")
        if reserve_adequacy_pct < 75:
            notes.append("Reserve under-funding may indicate future special levy risk.")
    else:
        notes.append("High-risk profile; independent strata inspection recommended.")
        notes.append("Lender should request last 2 years of audited strata accounts.")

    if has_litigation:
        notes.append("Active litigation present; legal advice recommended before settlement.")
    if arrears_rate_pct > 15:
        notes.append(f"Arrears rate of {arrears_rate_pct:.1f}% may indicate cash flow issues.")
    if compliance_score_pct < 70:
        notes.append("Low compliance score; verify outstanding orders with local council.")
    return notes


def _valuation_flags_and_signals(
        grade: str,
        reserve_adequacy_pct: float,
        arrears_rate_pct: float,
        compliance_score_pct: float,
        stress_score: float,
) -> tuple[list[str], list[str]]:
    """Generated function header.

    Function: _valuation_flags_and_signals
    Path: backend/routers/insurance_lending.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    flags: list[str] = []
    signals: list[str] = []
    if grade == "D":
        flags.append("Investor grade D — significant financial risk.")
    if reserve_adequacy_pct < 50:
        flags.append(f"Reserve adequacy critically low ({reserve_adequacy_pct:.1f}%).")
    if arrears_rate_pct > 10:
        flags.append(f"Arrears rate {arrears_rate_pct:.1f}% may affect buyer perception.")
    if compliance_score_pct < 70:
        flags.append("Low building compliance score.")
    if grade in ("A", "B"):
        signals.append(f"Strong investor grade ({grade}) supports valuation.")
    if reserve_adequacy_pct >= 100:
        signals.append("Sinking fund fully funded — no near-term special levy risk.")
    if stress_score < 30:
        signals.append("Low financial stress supports market value stability.")
    return flags, signals


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/intelligence/insurance/risk-signals", response_model=InsuranceRiskSignals)
async def get_insurance_risk_signals(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Building-level insurance risk signals for underwriters and brokers."""
    role = effective_role(current_user)
    if role not in _AUTHORISED_ROLES:
        raise HTTPException(status_code=403, detail="Access denied.")

    stress = await _fetch_stress(building_id)
    stress_score = float(stress.get("score", 50))
    stress_category = stress.get("category", "watch")
    data_completeness = float(stress.get("data_completeness", 0.5))

    (
        reserve_adequacy_pct,
        active_claims,
        open_high_prio,
        last_inspection,
        building_age,
        (csi_value, _),
    ) = await asyncio.gather(
        _get_reserve_adequacy_pct(building_id, stress),
        _get_active_claims(building_id),
        _get_open_high_priority_maintenance(building_id),
        _get_last_inspection_date(building_id),
        _get_building_age(building_id),
        _get_capital_shock_per_unit(building_id),
    )

    arrears_raw = _extract_component_raw(stress, "arrears_level")
    arrears_rate_pct = round(float(arrears_raw) * 100, 2) if arrears_raw is not None else 0.0

    unrec_raw = _extract_component_raw(stress, "unreconciled_exposure")
    unrec_pct = round(float(unrec_raw) * 100, 2) if unrec_raw is not None else 0.0

    maint_raw = _extract_component_raw(stress, "maintenance_pressure")
    maint_pct = round(float(maint_raw) * 100, 2) if maint_raw is not None else 0.0

    tier, risk_flags = _insurance_risk_tier(
        stress_score, stress_category, reserve_adequacy_pct,
        active_claims, open_high_prio, csi_value
    )

    review_rationale: list[str] = []
    recommended_review = tier in ("high", "critical")
    if recommended_review:
        review_rationale.append(f"Insurance risk tier is '{tier}' — review recommended.")
        review_rationale.extend(risk_flags[:3])

    return InsuranceRiskSignals(
        building_id=building_id,
        stress_score=stress_score,
        stress_category=stress_category,
        reserve_adequacy_pct=reserve_adequacy_pct,
        arrears_rate_pct=arrears_rate_pct,
        unreconciled_exposure_pct=unrec_pct,
        maintenance_pressure_pct=maint_pct,
        capital_shock_index=csi_value,
        active_claims_count=active_claims,
        open_maintenance_high_priority=open_high_prio,
        last_building_inspection_date=last_inspection,
        building_age_years=building_age,
        risk_flags=risk_flags,
        insurance_risk_tier=tier,
        recommended_review=recommended_review,
        review_rationale=review_rationale,
        data_completeness=data_completeness,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/intelligence/lending/valuation-signals/{unit_number}", response_model=LendingValuationSignals)
async def get_lending_valuation_signals(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Unit-level signals for lenders and valuers — lending risk tier and valuation flags."""
    role = effective_role(current_user)
    if role not in _AUTHORISED_ROLES:
        raise HTTPException(status_code=403, detail="Access denied.")

    stress = await _fetch_stress(building_id)
    stress_score = float(stress.get("score", 50))
    stress_category = stress.get("category", "watch")
    data_completeness = float(stress.get("data_completeness", 0.5))

    (
        reserve_adequacy_pct,
        deficit_cents,
        compliance_score,
        levy_cagr,
        (csi_value, capital_shock_per_unit),
        tco_annual,
        (has_litigation, litigation_notes),
    ) = await asyncio.gather(
        _get_reserve_adequacy_pct(building_id, stress),
        _get_sinking_deficit_cents(building_id),
        _get_compliance_score(building_id),
        _get_levy_cagr(building_id),
        _get_capital_shock_per_unit(building_id),
        _get_tco_annual(building_id, unit_number),
        _check_active_litigation(building_id),
    )

    arrears_raw = _extract_component_raw(stress, "arrears_level")
    arrears_rate_pct = round(float(arrears_raw) * 100, 2) if arrears_raw is not None else 0.0

    # Investor grade
    from routers.investor_intelligence import compute_investor_grade
    grade, _ = compute_investor_grade(
        stress_score, reserve_adequacy_pct, arrears_rate_pct, compliance_score
    )

    lending_tier = _lending_risk_tier(grade, deficit_cents, has_litigation, arrears_rate_pct)
    val_flags, pos_signals = _valuation_flags_and_signals(
        grade, reserve_adequacy_pct, arrears_rate_pct, compliance_score, stress_score
    )
    lender_notes = _lender_advisory_notes(
        lending_tier, grade, reserve_adequacy_pct, arrears_rate_pct, has_litigation, compliance_score
    )

    return LendingValuationSignals(
        unit_number=unit_number,
        building_id=building_id,
        investor_grade=grade,
        lending_risk_tier=lending_tier,
        stress_score=stress_score,
        stress_category=stress_category,
        reserve_adequacy_pct=reserve_adequacy_pct,
        arrears_rate_pct=arrears_rate_pct,
        sinking_fund_deficit_cents=deficit_cents,
        has_active_litigation=has_litigation,
        litigation_notes=litigation_notes,
        compliance_score_pct=compliance_score,
        levy_cagr_5yr_pct=levy_cagr,
        capital_shock_buffer_per_unit_cents=capital_shock_per_unit,
        current_annual_tco_cents=tco_annual,
        valuation_risk_flags=val_flags,
        positive_signals=pos_signals,
        lender_advisory_notes=lender_notes,
        data_completeness=data_completeness,
        generated_at=datetime.now(timezone.utc),
    )
