"""
Investor Intelligence Router — Phase 2

Provides investor-grade building and unit analysis:
  GET /intelligence/investor/summary/{unit_number}       → InvestorUnitSummary
  GET /intelligence/investor/building-health-report      → PDF health report
  GET /intelligence/investor/levy-trajectory             → levy trend data
  GET /intelligence/investor/risk-adjusted-yield/{unit_number}  → yield analysis

Role gating:
  - super_admin, chairman, strata_manager, ec_member: full access
  - owner: own unit only
"""
from datetime import datetime, timezone

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from database import db
from utils.auth import effective_role, get_current_user, get_current_building

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intelligence/investor", tags=["Investor Intelligence"])

_ADMIN_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member"}
_ALL_ROLES = _ADMIN_ROLES | {"owner"}


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/investor_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


async def gather_with_fallback(*coros, defaults=None):
    """Run coroutines concurrently; substitute default on individual failure."""
    results = []
    for i, coro in enumerate(coros):
        try:
            results.append(await coro)
        except Exception:
            default = (defaults or [])[i] if defaults and i < len(defaults) else None
            results.append(default)
    return results


# ── Models ─────────────────────────────────────────────────────────────────

class InvestorUnitSummary(BaseModel):
    unit_number: str
    building_id: str
    stress_score: float
    stress_category: str
    current_annual_tco_cents: int
    projected_5yr_tco_cents: int
    levy_cagr_5yr_pct: float
    reserve_adequacy_pct: float
    capital_shock_buffer_per_unit_cents: int
    building_arrears_rate_pct: float
    suburb_median_price: Optional[float] = None
    estimated_rental_yield_pct: Optional[float] = None
    ec_quorum_status: str
    last_agm_date: Optional[str] = None
    compliance_score_pct: float
    investor_grade: str
    investor_grade_rationale: list[str]
    red_flags: list[str]
    positive_signals: list[str]
    generated_at: datetime


class LevyTrajectoryPoint(BaseModel):
    financial_year: str
    admin_levy_cents: int
    sinking_levy_cents: int
    total_levy_cents: int
    yoy_change_pct: Optional[float] = None


class LevyTrajectoryResult(BaseModel):
    building_id: str
    history: list[LevyTrajectoryPoint]
    cagr_5yr_pct: Optional[float]
    trend_label: str
    generated_at: datetime


class RiskAdjustedYieldResult(BaseModel):
    unit_number: str
    building_id: str
    gross_rental_yield_pct: Optional[float]
    net_rental_yield_pct: Optional[float]
    tco_adjusted_yield_pct: Optional[float]
    stress_score: float
    stress_penalty_pct: float
    risk_adjusted_yield_pct: Optional[float]
    investor_grade: str
    notes: list[str]
    generated_at: datetime


# ── Investor Grade logic ───────────────────────────────────────────────────

def compute_investor_grade(
        stress_score: float,
        reserve_adequacy_pct: float,
        arrears_rate_pct: float,
        compliance_score_pct: float,
) -> tuple[str, list[str]]:
    """
    Returns (grade, rationale_list) based on Phase 2 spec:
      A: stress < 25, reserve > 100%, arrears < 5%, compliance > 90%
      B: stress < 50, reserve > 75%, arrears < 10%
      C: stress < 75 OR reserve < 75% OR arrears 10-20%
      D: stress >= 75 OR reserve < 50% OR arrears > 20%
    """
    rationale: list[str] = []

    if (stress_score < 25
            and reserve_adequacy_pct > 100
            and arrears_rate_pct < 5
            and compliance_score_pct > 90):
        grade = "A"
        rationale.append("Low stress score, strong reserve adequacy, minimal arrears, high compliance.")
    elif stress_score < 50 and reserve_adequacy_pct > 75 and arrears_rate_pct < 10:
        grade = "B"
        rationale.append("Moderate stress score with adequate reserves and acceptable arrears.")
        if stress_score >= 25:
            rationale.append(f"Stress score {stress_score:.1f} is above ideal threshold of 25.")
        if reserve_adequacy_pct <= 100:
            rationale.append(f"Reserve adequacy {reserve_adequacy_pct:.1f}% below 100% target.")
    elif (stress_score >= 75
          or reserve_adequacy_pct < 50
          or arrears_rate_pct > 20):
        # D is checked BEFORE C so that severely under-funded reserves (< 50%)
        # are not masked by the broader "reserve < 75%" C condition.
        grade = "D"
        if stress_score >= 75:
            rationale.append(f"Critical stress score {stress_score:.1f} ≥ 75.")
        if reserve_adequacy_pct < 50:
            rationale.append(f"Severely under-funded reserves: {reserve_adequacy_pct:.1f}%.")
        if arrears_rate_pct > 20:
            rationale.append(f"High arrears rate of {arrears_rate_pct:.1f}% exceeds 20% threshold.")
    elif (stress_score < 75
          and (reserve_adequacy_pct < 75 or 10 <= arrears_rate_pct <= 20)):
        grade = "C"
        if reserve_adequacy_pct < 75:
            rationale.append(f"Reserve adequacy {reserve_adequacy_pct:.1f}% is below 75% threshold.")
        if 10 <= arrears_rate_pct <= 20:
            rationale.append(f"Elevated arrears rate of {arrears_rate_pct:.1f}%.")
    else:
        grade = "C"
        rationale.append("Mixed indicators; graded conservatively.")

    if not rationale:
        rationale.append("Standard grading applied.")
    return grade, rationale


def _red_flags_and_signals(
        stress_score: float,
        stress_category: str,
        reserve_adequacy_pct: float,
        arrears_rate_pct: float,
        compliance_score_pct: float,
        grade: str,
) -> tuple[list[str], list[str]]:
    """Generated function header.

    Function: _red_flags_and_signals
    Path: backend/routers/investor_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    flags: list[str] = []
    signals: list[str] = []

    if stress_category in ("elevated", "critical"):
        flags.append(f"Building stress category is '{stress_category}'.")
    if reserve_adequacy_pct < 75:
        flags.append(f"Sinking fund under-funded ({reserve_adequacy_pct:.1f}% of target).")
    if arrears_rate_pct > 10:
        flags.append(f"Levy arrears rate {arrears_rate_pct:.1f}% exceeds 10%.")
    if compliance_score_pct < 70:
        flags.append(f"Low compliance score ({compliance_score_pct:.0f}%).")

    if stress_score < 25:
        signals.append("Building is in healthy financial condition.")
    if reserve_adequacy_pct >= 100:
        signals.append("Sinking fund fully funded for 12-month expenditure.")
    if arrears_rate_pct < 5:
        signals.append("Levy collection rate is strong (<5% arrears).")
    if compliance_score_pct >= 90:
        signals.append("High compliance score indicates well-governed building.")

    return flags, signals


# ── Helper: fetch Phase 2 stress score ────────────────────────────────────

async def _fetch_stress(building_id: str) -> dict:
    """Generated function header.

    Function: _fetch_stress
    Path: backend/routers/investor_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from services.building_stress_service import compute_phase2_stress_score
        return await compute_phase2_stress_score(building_id)
    except Exception as exc:
        logger.warning("Phase 2 stress score unavailable: %s", exc)
        return {"score": 50.0, "category": "watch", "components": {}, "data_completeness": 0.0}


async def _fetch_reserve_adequacy_pct(building_id: str, stress_data: dict) -> float:
    """Derive reserve adequacy % from stress components or direct calculation."""
    comp = stress_data.get("components", {}).get("reserve_adequacy", {})
    ratio = comp.get("raw_value", None)
    if ratio is not None:
        return round(float(ratio) * 100, 1)
    # Direct fallback
    try:
        from services.building_stress_service import compute_reserve_adequacy_ratio
        ra = await compute_reserve_adequacy_ratio(building_id)
        rar = ra.get("reserve_adequacy_ratio", 0.0)
        return round(float(rar) * 100, 1)
    except Exception:
        return 50.0


async def _fetch_arrears_rate(building_id: str, stress_data: dict) -> float:
    """Generated function header.

    Function: _fetch_arrears_rate
    Path: backend/routers/investor_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    comp = stress_data.get("components", {}).get("arrears_level", {})
    raw = comp.get("raw_value", None)
    if raw is not None:
        return round(float(raw) * 100, 2)
    return 0.0


async def _fetch_compliance_score(building_id: str) -> float:
    """Try to load compliance score from nsw_compliance or compliance_registers."""
    try:
        doc = await db.compliance_scores.find_one(
            {"building_id": building_id}, sort=[("computed_at", -1)]
        )
        if doc:
            return float(doc.get("score_pct", doc.get("overall_score", 75)) or 75)
    except Exception as exc:
        logger.warning("investor_intelligence: compliance score fetch failed: %s", exc)
    return 75.0  # neutral default


async def _fetch_ec_quorum_status(building_id: str) -> tuple[str, Optional[str]]:
    """Return (ec_quorum_status, last_agm_date_str)."""
    try:
        # Count active EC members
        # 'chairman' is not a top-level role (see rules/post-compact-critical.md) — a chairman
        # is a user with role='ec_member' and ec_position='CHAIRMAN', already covered below.
        ec_count = await db.users.count_documents(
            {"building_id": building_id, "role": {"$in": ["ec_member", "strata_admin"]}, "is_active": True}
        )
        status = "full" if ec_count >= 3 else ("inquorate" if ec_count > 0 else "unknown")

        # Last AGM from meetings/events
        agm = await db.meetings.find_one(
            {"building_id": building_id, "meeting_type": {"$regex": "agm", "$options": "i"}},
            sort=[("meeting_date", -1)]
        )
        if not agm:
            agm = await db.events.find_one(
                {"building_id": building_id, "title": {"$regex": "agm|annual general", "$options": "i"}},
                sort=[("date", -1)]
            )
        last_agm = None
        if agm:
            last_agm = agm.get("meeting_date") or agm.get("date") or agm.get("start_time")
            if last_agm and not isinstance(last_agm, str):
                last_agm = str(last_agm)
        return status, last_agm
    except Exception as exc:
        logger.warning("investor_intelligence: EC quorum/AGM fetch failed: %s", exc)
        return "unknown", None


async def _fetch_capital_shock_per_unit(building_id: str) -> int:
    """Generated function header.

    Function: _fetch_capital_shock_per_unit
    Path: backend/routers/investor_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from services.capital_shock_service import detect_capital_shocks
        shock = await detect_capital_shocks(building_id)
        total = float(shock.get("max_deficit", 0) or 0)
        total_units = await db.units.count_documents({"building_id": building_id})
        if total_units > 0 and total > 0:
            return int(total / total_units * 100)  # dollars to cents
    except Exception as exc:
        logger.warning("investor_intelligence: capital shock per-unit fetch failed: %s", exc)
    return 0


async def _compute_levy_cagr(building_id: str, years: int = 5) -> float:
    """CAGR of total building levy income over `years` years."""
    try:
        pipeline = [
            {"$match": {"building_id": building_id}},
            {"$sort": {"year": 1}},
            {"$limit": years + 1},
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
        records = await db.annual_levies.aggregate(pipeline).to_list(years + 2)
        if len(records) >= 2:
            first = float(records[0]["income"] or 1)
            last = float(records[-1]["income"] or 1)
            n = len(records) - 1
            if first > 0 and n > 0:
                return round(((last / first) ** (1 / n) - 1) * 100, 2)
    except Exception as exc:
        logger.warning("investor_intelligence: levy CAGR computation failed: %s", exc)
    return 0.0


# ── TCO helpers ────────────────────────────────────────────────────────────

async def _tco_annual_cents(building_id: str, unit_number: str) -> int:
    """Generated function header.

    Function: _tco_annual_cents
    Path: backend/routers/investor_intelligence.py

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


async def _tco_5yr_cents(building_id: str, unit_number: str) -> int:
    """Generated function header.

    Function: _tco_5yr_cents
    Path: backend/routers/investor_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        from services.true_cost_service import TrueCostOfOwnershipInput, compute_true_cost_of_ownership
        from utils.finance_helpers import get_latest_levy_year
        year = await get_latest_levy_year(building_id) or str(datetime.now(timezone.utc).year)
        inp = TrueCostOfOwnershipInput(
            building_id=building_id,
            unit_number=unit_number,
            holding_period_years=5,
            financial_year=year,
        )
        result = await compute_true_cost_of_ownership(inp)
        return result.total_for_period_cents
    except Exception:
        return 0


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/summary/{unit_number}", response_model=InvestorUnitSummary)
async def get_investor_unit_summary(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Comprehensive investor-grade summary for a single unit."""
    role = effective_role(current_user)
    if role not in _ALL_ROLES:
        raise HTTPException(status_code=403, detail="Access denied.")

    # Owners may only see their own unit
    if role == "owner":
        owner_unit = current_user.get("unit_number") or current_user.get("lot_number")
        if owner_unit and owner_unit != unit_number:
            raise HTTPException(status_code=403, detail="You can only view your own unit.")

    stress = await _fetch_stress(building_id)
    stress_score = float(stress.get("score", 50))
    stress_category = stress.get("category", "watch")

    reserve_adequacy_pct, arrears_rate, compliance_score = await gather_with_fallback(
        _fetch_reserve_adequacy_pct(building_id, stress),
        _fetch_arrears_rate(building_id, stress),
        _fetch_compliance_score(building_id),
    )

    ec_quorum_status, last_agm_date = await _fetch_ec_quorum_status(building_id)
    capital_shock_per_unit = await _fetch_capital_shock_per_unit(building_id)
    levy_cagr = await _compute_levy_cagr(building_id)

    tco_annual, tco_5yr = await asyncio.gather(
        _tco_annual_cents(building_id, unit_number),
        _tco_5yr_cents(building_id, unit_number),
    )

    grade, rationale = compute_investor_grade(
        stress_score, reserve_adequacy_pct, arrears_rate, compliance_score
    )
    flags, signals = _red_flags_and_signals(
        stress_score, stress_category, reserve_adequacy_pct, arrears_rate, compliance_score, grade
    )

    return InvestorUnitSummary(
        unit_number=unit_number,
        building_id=building_id,
        stress_score=stress_score,
        stress_category=stress_category,
        current_annual_tco_cents=tco_annual,
        projected_5yr_tco_cents=tco_5yr,
        levy_cagr_5yr_pct=levy_cagr,
        reserve_adequacy_pct=reserve_adequacy_pct,
        capital_shock_buffer_per_unit_cents=capital_shock_per_unit,
        building_arrears_rate_pct=arrears_rate,
        ec_quorum_status=ec_quorum_status,
        last_agm_date=last_agm_date,
        compliance_score_pct=compliance_score,
        investor_grade=grade,
        investor_grade_rationale=rationale,
        red_flags=flags,
        positive_signals=signals,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/building-health-report")
async def get_building_health_report(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Trigger Phase 2 building health PDF report generation.
    Returns a StreamingResponse with the PDF or a JSON payload if PDF service unavailable.
    """
    role = effective_role(current_user)
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required.")

    stress = await _fetch_stress(building_id)
    reserve_adequacy_pct = await _fetch_reserve_adequacy_pct(building_id, stress)
    arrears_rate = await _fetch_arrears_rate(building_id, stress)
    compliance_score = await _fetch_compliance_score(building_id)
    grade, rationale = compute_investor_grade(
        stress["score"], reserve_adequacy_pct, arrears_rate, compliance_score
    )
    ec_quorum_status, last_agm_date = await _fetch_ec_quorum_status(building_id)

    try:
        from services.report_service import generate_full_report
        from utils.finance_helpers import get_latest_levy_year
        year = await get_latest_levy_year(building_id) or str(datetime.now(timezone.utc).year)
        pdf_bytes = await generate_full_report(year, building_id, generated_by_role=role)
        # generate_full_report() can return non-PDF placeholder bytes (e.g. b"ReportLab
        # not available") WITHOUT raising when REPORTLAB_AVAILABLE is False — streaming
        # that with media_type="application/pdf" anyway would defeat the frontend's
        # content-type guard, since the header would still say "application/pdf" even
        # though the bytes aren't one. Validate the PDF magic header before trusting it.
        if not pdf_bytes.startswith(b"%PDF"):
            raise ValueError(f"generate_full_report returned non-PDF bytes ({pdf_bytes[:32]!r})")
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="health_report_{building_id}.pdf"'},
        )
    except Exception as exc:
        logger.warning("PDF generation unavailable: %s", exc)

    return {
        "building_id": building_id,
        "investor_grade": grade,
        "stress_score": stress["score"],
        "stress_category": stress["category"],
        "reserve_adequacy_pct": reserve_adequacy_pct,
        "arrears_rate_pct": arrears_rate,
        "compliance_score_pct": compliance_score,
        "ec_quorum_status": ec_quorum_status,
        "last_agm_date": last_agm_date,
        "investor_grade_rationale": rationale,
        "generated_at": _now_iso(),
        "note": "PDF generation unavailable; returning JSON summary.",
    }


@router.get("/levy-trajectory", response_model=LevyTrajectoryResult)
async def get_levy_trajectory(
        years: int = Query(default=5, ge=1, le=20),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Building-level levy trend over historical years."""
    role = effective_role(current_user)
    if role not in _ALL_ROLES:
        raise HTTPException(status_code=403, detail="Access denied.")

    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$sort": {"year": -1}},
        {"$limit": years},
        {"$sort": {"year": 1}},
    ]
    records = await db.annual_levies.aggregate(pipeline).to_list(years)

    history: list[LevyTrajectoryPoint] = []
    prev_total: Optional[int] = None

    for rec in records:
        af = rec.get("admin_fund") or {}
        sf = rec.get("sinking_fund") or {}
        admin_inc = int(float(af.get("levy_income", 0) or 0) * 100)
        sinking_inc = int(float(sf.get("levy_income", 0) or 0) * 100)
        total = admin_inc + sinking_inc
        yoy = None
        if prev_total is not None and prev_total > 0:
            yoy = round((total - prev_total) / prev_total * 100, 2)
        prev_total = total
        history.append(LevyTrajectoryPoint(
            financial_year=str(rec.get("year", "")),
            admin_levy_cents=admin_inc,
            sinking_levy_cents=sinking_inc,
            total_levy_cents=total,
            yoy_change_pct=yoy,
        ))

    # CAGR
    cagr = None
    if len(history) >= 2:
        first = history[0].total_levy_cents
        last = history[-1].total_levy_cents
        n = len(history) - 1
        if first > 0 and n > 0:
            cagr = round(((last / first) ** (1 / n) - 1) * 100, 2)

    # Trend label
    avg_yoy = sum(h.yoy_change_pct for h in history if h.yoy_change_pct is not None)
    count_yoy = sum(1 for h in history if h.yoy_change_pct is not None)
    avg_yoy = avg_yoy / count_yoy if count_yoy > 0 else 0
    if avg_yoy > 5:
        trend_label = "rising"
    elif avg_yoy < -2:
        trend_label = "falling"
    else:
        trend_label = "stable"

    return LevyTrajectoryResult(
        building_id=building_id,
        history=history,
        cagr_5yr_pct=cagr,
        trend_label=trend_label,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/risk-adjusted-yield/{unit_number}", response_model=RiskAdjustedYieldResult)
async def get_risk_adjusted_yield(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Risk-adjusted yield analysis for a unit.
    Gross yield uses market_intelligence or units.estimated_rental_yield_pct.
    Net yield deducts annual TCO.
    Risk-adjusted yield applies a stress penalty.
    """
    role = effective_role(current_user)
    if role not in _ALL_ROLES:
        raise HTTPException(status_code=403, detail="Access denied.")
    if role == "owner":
        owner_unit = current_user.get("unit_number") or current_user.get("lot_number")
        if owner_unit and owner_unit != unit_number:
            raise HTTPException(status_code=403, detail="You can only view your own unit.")

    unit = await db.units.find_one(
        {"building_id": building_id, "unit_number": unit_number}, {"_id": 0}
    ) or {}
    property_value_cents = int(unit.get("property_value_cents", 0) or 0)

    stress = await _fetch_stress(building_id)
    stress_score = float(stress.get("score", 50))
    reserve_adequacy_pct = await _fetch_reserve_adequacy_pct(building_id, stress)
    arrears_rate = await _fetch_arrears_rate(building_id, stress)
    compliance_score = await _fetch_compliance_score(building_id)
    grade, _ = compute_investor_grade(stress_score, reserve_adequacy_pct, arrears_rate, compliance_score)

    notes: list[str] = []
    gross_yield: Optional[float] = None
    net_yield: Optional[float] = None
    tco_adjusted_yield: Optional[float] = None
    risk_adjusted_yield: Optional[float] = None

    # Try to get rental yield estimate
    stored_yield = unit.get("estimated_rental_yield_pct")
    if stored_yield:
        gross_yield = float(stored_yield)

    if gross_yield is None and property_value_cents > 0:
        # Try market_intelligence collection
        market = await db.market_intelligence.find_one(
            {"building_id": building_id, "unit_number": unit_number},
            sort=[("date", -1)]
        )
        if market and market.get("rental_yield_pct"):
            gross_yield = float(market["rental_yield_pct"])
            notes.append("Rental yield from market intelligence data.")
        else:
            notes.append("Rental yield not available in market data; gross yield omitted.")

    tco_annual = await _tco_annual_cents(building_id, unit_number)

    if gross_yield is not None and property_value_cents > 0:
        gross_income_cents = int(property_value_cents * gross_yield / 100)
        net_income_cents = gross_income_cents - tco_annual
        net_yield = round(net_income_cents / property_value_cents * 100, 3)
        tco_adjusted_yield = net_yield

    # Stress penalty: 0.1% per 10 stress score points above 25
    stress_penalty = max(0.0, (stress_score - 25) / 10 * 0.1)
    if tco_adjusted_yield is not None:
        risk_adjusted_yield = round(tco_adjusted_yield - stress_penalty, 3)
        notes.append(
            f"Stress penalty of {stress_penalty:.2f}% applied "
            f"(stress score {stress_score:.1f})."
        )

    if not notes:
        notes.append("Yield analysis requires property value and rental estimate in unit record.")

    return RiskAdjustedYieldResult(
        unit_number=unit_number,
        building_id=building_id,
        gross_rental_yield_pct=gross_yield,
        net_rental_yield_pct=net_yield,
        tco_adjusted_yield_pct=tco_adjusted_yield,
        stress_score=stress_score,
        stress_penalty_pct=round(stress_penalty, 3),
        risk_adjusted_yield_pct=risk_adjusted_yield,
        investor_grade=grade,
        notes=notes,
        generated_at=datetime.now(timezone.utc),
    )

# ── asyncio gather with fallback ───────────────────────────────────────────
