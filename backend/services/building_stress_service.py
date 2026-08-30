"""
Building Financial Stress Service — computes reserve adequacy, risk scores,
unit levy impact, mitigation options, timeline projections and snapshots.
"""
import logging
from datetime import datetime, timezone

import asyncio
from typing import Optional

from database import db
from utils.auth import DEFAULT_BUILDING_ID
from utils.helpers import get_current_utc_iso

logger = logging.getLogger(__name__)


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return get_current_utc_iso()


def _current_year() -> int:
    """Generated function header.

    Function: _current_year
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).year


# ── Reserve Adequacy ───────────────────────────────────────────────────────

async def compute_reserve_adequacy_ratio(building_id: str) -> dict:
    # Fetch annual_levies once as a common fallback source
    """Generated function header.

    Function: compute_reserve_adequacy_ratio
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    annual_levy_doc = await db.annual_levies.find_one(
        {"building_id": building_id},
        sort=[("year", -1)]
    )
    al_sf = (annual_levy_doc or {}).get("sinking_fund") or {}

    # Most-recent sinking fund closing balance — prefer financial_years, fall back to annual_levies
    ledger = await db.financial_years.find_one(
        {"building_id": building_id, "fund_type": "sinking"},
        sort=[("year", -1)]
    )
    sinking_balance = 0.0
    if ledger:
        sinking_balance = float(ledger.get("closing_balance", 0) or 0)
    if sinking_balance == 0.0 and al_sf:
        cb = al_sf.get("current_balance") or al_sf.get("closing_balance") or 0
        sinking_balance = float(cb)

    # 10-year capital works forecast (not yet completed)
    horizon = _current_year() + 10
    pipeline = [
        {"$match": {
            "building_id": building_id,
            "forecast_year": {"$lte": horizon},
            "status": {"$ne": "completed"}
        }},
        {"$group": {"_id": None, "total": {"$sum": "$estimated_cost"}}}
    ]
    res = await db.capital_works.aggregate(pipeline).to_list(1)
    ten_year_forecast = float(res[0]["total"]) if res else 0.0

    # Fallback: derive from annual sinking fund total_expenses × 10 years
    if ten_year_forecast == 0.0 and al_sf:
        annual_exp = float(al_sf.get("total_expenses") or 0)
        ten_year_forecast = round(annual_exp * 10, 2)

    if ten_year_forecast > 0:
        rar = round(sinking_balance / ten_year_forecast, 4)
    else:
        rar = 1.0  # No forecast = no risk

    deficit = round(ten_year_forecast - sinking_balance, 2)

    if rar >= 0.7:
        adequacy_level = "healthy"
    elif rar >= 0.4:
        adequacy_level = "moderate"
    elif rar >= 0.2:
        adequacy_level = "high"
    else:
        adequacy_level = "severe"

    return {
        "sinking_fund_balance": sinking_balance,
        "ten_year_capital_forecast": ten_year_forecast,
        "reserve_adequacy_ratio": rar,
        "deficit": max(0.0, deficit),
        "adequacy_level": adequacy_level,
    }


# ── Risk Score ─────────────────────────────────────────────────────────────

async def compute_risk_score(building_id: str) -> dict:
    """Generated function header.

    Function: compute_risk_score
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    current_year = _current_year()

    # ── Reserve gap (35%) ─────────────────────────────────────────────────
    adequacy = await compute_reserve_adequacy_ratio(building_id)
    deficit = adequacy["deficit"]
    forecast = adequacy["ten_year_capital_forecast"]
    if forecast <= 0:
        reserve_gap_score = 0.0
    elif deficit <= 0:
        reserve_gap_score = 0.0
    else:
        # Normalise: fund empty + 200% deficit = 100
        normalised = deficit / (forecast * 2)
        reserve_gap_score = min(100.0, normalised * 100)

    # ── Expense growth (20%) ───────────────────────────────────────────────
    def _year_label(y: int) -> str:
        """Generated function header.

        Function: _year_label
        Path: backend/services/building_stress_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return f"{y}-{y + 1}"

    this_year_label = _year_label(current_year - 1)  # e.g. current FY
    last_year_label = _year_label(current_year - 2)

    async def _year_expenses(year_label: str) -> float:
        """Generated function header.

        Function: _year_expenses
        Path: backend/services/building_stress_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pipeline = [
            {"$match": {"building_id": building_id, "financial_year": year_label,
                        "transaction_type": "expense"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]
        r = await db.financial_transactions.aggregate(pipeline).to_list(1)
        return float(r[0]["total"]) if r else 0.0

    this_expenses, last_expenses = await asyncio.gather(
        _year_expenses(this_year_label),
        _year_expenses(last_year_label)
    )
    if last_expenses > 0:
        growth_pct = (this_expenses - last_expenses) / last_expenses * 100
        expense_growth_score = min(100.0, max(0.0, growth_pct * 5))  # 20% growth = 100
    else:
        expense_growth_score = 0.0

    # ── Maintenance backlog (15%) ──────────────────────────────────────────
    open_count = await db.maintenance_requests.count_documents(
        {"building_id": building_id, "status": {"$nin": ["completed", "closed", "rejected"]}}
    )
    if open_count == 0:
        maint_backlog_score = 0.0
    elif open_count <= 5:
        maint_backlog_score = 30.0
    elif open_count <= 10:
        maint_backlog_score = 60.0
    else:
        maint_backlog_score = min(100.0, 60.0 + (open_count - 10) * 4)

    # ── Capital age (20%) ─────────────────────────────────────────────────
    asset_age_scores = []
    for coll_name in ["digital_twin", "assets"]:
        coll = getattr(db, coll_name)
        assets = await coll.find(
            {"building_id": building_id,
             "asset_type": {"$in": ["lift", "elevator", "roof", "pump", "fire_system", "hvac"]}},
            {"_id": 0, "install_year": 1, "commissioned_year": 1}
        ).to_list(50)
        for a in assets:
            install_yr = a.get("install_year") or a.get("commissioned_year")
            if install_yr:
                age = current_year - int(install_yr)
                if age < 5:
                    asset_age_scores.append(0)
                elif age < 10:
                    asset_age_scores.append(30)
                elif age < 15:
                    asset_age_scores.append(60)
                elif age < 20:
                    asset_age_scores.append(80)
                else:
                    asset_age_scores.append(100)

    capital_age_score = (sum(asset_age_scores) / len(asset_age_scores)) if asset_age_scores else 50.0

    # ── Levy collection rate (10%) ────────────────────────────────────────
    # Grace-aware canonical arrears count (GAP-FIN-040) as the stress INPUT; the
    # score formula below stays building-stress's own (governing doc: consume the
    # canonical arrears metric, retain the score).
    from utils.finance_helpers import get_building_arrears_summary
    total_units = await db.units.count_documents({"building_id": building_id})
    arrears_units = (await get_building_arrears_summary(building_id))["units_in_arrears"]
    if total_units > 0:
        arrears_pct = (arrears_units / total_units) * 100
        levy_collection_score = min(100.0, arrears_pct * 5)  # 20% arrears = 100
    else:
        levy_collection_score = 0.0

    # ── Weighted composite ────────────────────────────────────────────────
    overall_risk = round(
        0.35 * reserve_gap_score +
        0.20 * expense_growth_score +
        0.15 * maint_backlog_score +
        0.20 * capital_age_score +
        0.10 * levy_collection_score,
        1
    )

    if overall_risk < 30:
        stress_level = "healthy"
    elif overall_risk < 55:
        stress_level = "moderate"
    elif overall_risk < 75:
        stress_level = "high"
    else:
        stress_level = "severe"

    return {
        "reserve_gap_score": round(reserve_gap_score, 1),
        "expense_growth_score": round(expense_growth_score, 1),
        "maintenance_backlog_score": round(maint_backlog_score, 1),
        "capital_age_score": round(capital_age_score, 1),
        "levy_collection_score": round(levy_collection_score, 1),
        "overall_risk_score": overall_risk,
        "stress_level": stress_level,
    }


# ── Unit Levy Impact ───────────────────────────────────────────────────────

async def compute_unit_levy_impact(unit_number: str, building_id: str, total_deficit: float) -> dict:
    """Generated function header.

    Function: compute_unit_levy_impact
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    unit = await db.units.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"_id": 0, "entitlement": 1}
    )
    unit_entitlement = float(unit.get("entitlement", 1)) if unit else 1.0

    agg = await db.units.aggregate([
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": None, "total": {"$sum": "$entitlement"}}}
    ]).to_list(1)
    total_entitlement = float(agg[0]["total"]) if agg else 1.0

    proportional_share = (unit_entitlement / total_entitlement) if total_entitlement > 0 else 0.0
    projected_special_levy = round(total_deficit * proportional_share, 2)

    # Probability: higher deficit relative to balance = higher probability
    adequacy = await compute_reserve_adequacy_ratio(building_id)
    rar = adequacy.get("reserve_adequacy_ratio", 1.0)
    probability_pct = round(max(0.0, min(100.0, (1 - rar) * 100)), 1)

    # Estimated year: when fund might run out
    sinking_balance = adequacy["sinking_fund_balance"]
    risk = await compute_risk_score(building_id)
    stress = risk.get("stress_level", "moderate")
    estimated_year = None
    if stress in ("high", "severe"):
        estimated_year = str(_current_year() + 3)
    elif stress == "moderate":
        estimated_year = str(_current_year() + 7)

    return {
        "unit_number": unit_number,
        "entitlement_units": unit_entitlement,
        "total_entitlement_units": total_entitlement,
        "proportional_share_pct": round(proportional_share * 100, 3),
        "projected_special_levy": projected_special_levy,
        "probability_pct": probability_pct,
        "estimated_year": estimated_year,
    }


# ── Mitigation Options ─────────────────────────────────────────────────────

async def generate_mitigation_options(building_id: str, deficit: float, risk_score: float) -> list:
    """Generated function header.

    Function: generate_mitigation_options
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    total_units = await db.units.count_documents({"building_id": building_id})
    if total_units == 0:
        total_units = 1

    options = []

    # 1. Increase sinking levy
    annual_cost_per_unit = round(deficit / total_units / 10, 2)  # spread over 10 years
    options.append({
        "option_type": "increase_levy",
        "title": "Increase Sinking Fund Levy",
        "description": (
            f"Spread the ${deficit:,.0f} deficit over 10 years by increasing "
            f"the quarterly sinking fund levy. Each unit pays an additional "
            f"${annual_cost_per_unit:,.2f}/year, avoiding any special levy call."
        ),
        "annual_cost_per_unit": annual_cost_per_unit,
        "total_building_cost": round(annual_cost_per_unit * total_units * 10, 2),
        "reduces_risk_by": round(risk_score * 0.30, 1),
        "avoids_special_levy": True,
        "feasibility": "high",
    })

    # 2. Defer low-priority works
    risk_data = await compute_risk_score(building_id)
    maint_is_driver = risk_data.get("maintenance_backlog_score", 0) > 50
    risk_reduction = round(risk_score * 0.15, 1) if maint_is_driver else round(risk_score * 0.08, 1)
    options.append({
        "option_type": "defer_works",
        "title": "Defer Low-Priority Capital Works",
        "description": (
            "Postpone non-critical capital works (cosmetic upgrades, non-safety "
            "items) for 2–3 years to allow the sinking fund to recover. "
            "Only recommended where deferred items do not pose safety or compliance risk."
        ),
        "annual_cost_per_unit": None,
        "total_building_cost": None,
        "reduces_risk_by": risk_reduction,
        "avoids_special_levy": False,
        "feasibility": "medium",
    })

    # 3. Strata loan
    options.append({
        "option_type": "strata_loan",
        "title": "Strata Finance Loan",
        "description": (
            f"Obtain a strata loan of ${deficit:,.0f} to fund capital works immediately "
            "without depleting the sinking fund. Repayments are passed through as an "
            "additional levy component. Typical terms: 5–10 years at ~7–9% p.a."
        ),
        "annual_cost_per_unit": round(deficit / total_units / 7 * 1.08, 2),  # 7-yr at 8%
        "total_building_cost": round(deficit * 1.08, 2),
        "reduces_risk_by": round(risk_score * 0.20, 1),
        "avoids_special_levy": True,
        "feasibility": "medium",
    })

    return options


# ── Reserve Timeline ───────────────────────────────────────────────────────

async def project_reserve_timeline(building_id: str, years: int = 10) -> list:
    """Generated function header.

    Function: project_reserve_timeline
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    adequacy = await compute_reserve_adequacy_ratio(building_id)
    balance = adequacy["sinking_fund_balance"]
    current_year = _current_year()

    # Annual contributions: sum levied sinking fund from most recent levy ledger
    ledger_agg = await db.unit_levy_ledger.aggregate([
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": None, "total": {"$sum": "$sinking_levied"}}}
    ]).to_list(1)
    annual_contributions = float(ledger_agg[0]["total"]) if ledger_agg else 0.0

    timeline = []
    for i in range(1, years + 1):
        yr = current_year + i
        # Forecast expenses for this year
        cap_works = await db.capital_works.find(
            {"building_id": building_id, "forecast_year": yr, "status": {"$ne": "completed"}},
            {"_id": 0, "estimated_cost": 1}
        ).to_list(100)
        annual_expenses = sum(c.get("estimated_cost", 0) for c in cap_works)

        balance = balance + annual_contributions - annual_expenses
        timeline.append({
            "year": yr,
            "projected_balance": round(balance, 2),
            "annual_contributions": round(annual_contributions, 2),
            "annual_expenses": round(annual_expenses, 2),
            "is_deficit": balance < 0,
        })

    return timeline


# ── Compute and Store Snapshot ─────────────────────────────────────────────

async def compute_and_store_snapshot(building_id: str) -> dict:
    """Generated function header.

    Function: compute_and_store_snapshot
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = _now()

    adequacy_task = compute_reserve_adequacy_ratio(building_id)
    risk_task = compute_risk_score(building_id)
    timeline_task = project_reserve_timeline(building_id)

    adequacy, risk, timeline = await asyncio.gather(adequacy_task, risk_task, timeline_task)

    deficit = adequacy["deficit"]
    overall_risk_score = risk["overall_risk_score"]
    stress_level = risk["stress_level"]

    # Unit count
    total_units = await db.units.count_documents({"building_id": building_id})

    # Per-unit special levy
    projected_special_levy_per_unit = round(deficit / total_units, 2) if total_units > 0 else 0.0

    # Mitigation options
    mitigation = await generate_mitigation_options(building_id, deficit, overall_risk_score)

    # Projected deficit year from timeline
    projected_deficit_year = None
    for pt in timeline:
        if pt["is_deficit"]:
            projected_deficit_year = str(pt["year"])
            break

    # Special levy probability
    rar = adequacy.get("reserve_adequacy_ratio", 1.0)
    special_levy_probability_pct = round(max(0.0, min(100.0, (1 - rar) * 100)), 1)

    # Compare with previous snapshot for new risk flags
    prev_snap = await db.building_financial_health.find_one(
        {"building_id": building_id},
        sort=[("computed_at", -1)]
    )
    new_risk_flags = []
    if prev_snap:
        prev_score = prev_snap.get("overall_risk_score", overall_risk_score)
        if overall_risk_score > prev_score + 10:
            new_risk_flags.append(f"Risk score increased by {overall_risk_score - prev_score:.1f} points")
        if stress_level != prev_snap.get("stress_level"):
            new_risk_flags.append(f"Stress level changed to {stress_level}")

    snapshot = {
        "building_id": building_id,
        "computed_at": now,
        "reserve_adequacy": adequacy,
        "risk_components": risk,
        "overall_risk_score": overall_risk_score,
        "stress_level": stress_level,
        "projected_deficit_year": projected_deficit_year,
        "projected_special_levy_per_unit": projected_special_levy_per_unit,
        "special_levy_probability_pct": special_levy_probability_pct,
        "reserve_timeline": timeline,
        "mitigation_options": mitigation,
        "new_risk_flags": new_risk_flags,
        "total_units": total_units,
    }

    # Upsert keyed by building_id + month
    month_key = now[:7]  # "YYYY-MM"
    snapshot_set = {k: v for k, v in snapshot.items() if k not in ("building_id", "month")}
    snapshot_set["month"] = month_key
    await db.building_financial_health.update_one(
        {"building_id": building_id, "month": month_key},
        {"$set": snapshot_set},
        upsert=True
    )

    return snapshot


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — 7-Component BuildingFinancialStressScore
# ══════════════════════════════════════════════════════════════════════════════

STRESS_COMPONENTS = {
    "reserve_adequacy": {
        "weight": 0.25,
        "description": "Sinking fund balance vs 12-month expenditure",
    },
    "arrears_level": {
        "weight": 0.20,
        "description": "% of levies outstanding > 30 days",
    },
    "arrears_concentration": {
        "weight": 0.15,
        "description": "Largest single arrears as % of total fund",
    },
    "unreconciled_exposure": {
        "weight": 0.15,
        "description": "Unmatched cash as % of monthly receipts",
    },
    "maintenance_pressure": {
        "weight": 0.10,
        "description": "High-risk assets as % of total assets",
    },
    "liquidity_coverage": {
        "weight": 0.10,
        "description": "Admin fund balance / 3-month expenditure",
    },
    "capital_shock_indicator": {
        "weight": 0.05,
        "description": "Capital Shock Index normalised",
    },
}

_STRESS_THRESHOLDS = [
    (25, "healthy"),
    (50, "watch"),
    (75, "elevated"),
    (100, "critical"),
]


def _stress_category(score: float) -> str:
    """Generated function header.

    Function: _stress_category
    Path: backend/services/building_stress_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for threshold, label in _STRESS_THRESHOLDS:
        if score <= threshold:
            return label
    return "critical"


def _component_status(normalised: float) -> str:
    """Map a 0-100 normalised score into a 3-level status label."""
    if normalised <= 25:
        return "ok"
    if normalised <= 60:
        return "warning"
    return "critical"


async def _phase2_reserve_adequacy(building_id: str) -> dict:
    """
    raw_value  = sinking_balance / max(annual_sinking_expenditure, 1)
    Ideal ≥ 1.0 (12-month cover).  score = clamp(0, 100 * (1 - ratio), 100).
    """
    ledger = await db.financial_years.find_one(
        {"building_id": building_id, "fund_type": "sinking"},
        sort=[("year", -1)]
    )
    sinking_balance = float((ledger or {}).get("closing_balance", 0) or 0)

    # Annual sinking expenditure from annual_levies
    levy_doc = await db.annual_levies.find_one(
        {"building_id": building_id},
        sort=[("year", -1)]
    )
    annual_sinking_exp = 0.0
    if levy_doc:
        sf = levy_doc.get("sinking_fund") or {}
        annual_sinking_exp = float(sf.get("total_expenses", 0) or 0)

    # Fallback: financial_transactions sum
    if annual_sinking_exp == 0:
        pipeline = [
            {"$match": {"building_id": building_id, "transaction_type": "expense",
                        "fund": {"$in": ["sinking", "sinking_fund"]}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]
        res = await db.financial_transactions.aggregate(pipeline).to_list(1)
        annual_sinking_exp = float(res[0]["total"]) if res else 0.0

    ratio = sinking_balance / max(annual_sinking_exp, 1.0)
    normalised = max(0.0, min(100.0, (1.0 - ratio) * 100.0))
    threshold_breached = ratio < 1.0

    return {
        "raw_value": round(ratio, 4),
        "normalised_score": round(normalised, 1),
        "weight": STRESS_COMPONENTS["reserve_adequacy"]["weight"],
        "weighted_contribution": round(normalised * STRESS_COMPONENTS["reserve_adequacy"]["weight"], 2),
        "status": _component_status(normalised),
        "explanation": (
            f"Sinking fund balance ${sinking_balance:,.0f} covers "
            f"{ratio:.2f}x annual sinking expenditure (${annual_sinking_exp:,.0f}). "
            "Ideal ≥ 1.0."
        ),
        "threshold_breached": threshold_breached,
        "_available": True,
    }


async def _phase2_arrears_level(building_id: str) -> dict:
    """
    raw_value = total overdue > 30 days / total annual levy income.
    Ideal = 0.  score = clamp(0, raw * 500, 100)  (20% arrears → 100).
    """
    from datetime import timedelta
    from utils.finance_helpers import get_unit_arrears_map
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    # Overdue balances: grace-aware canonical per-unit arrears (GAP-FIN-040) whose
    # last payment is older than 30 days (or none) — the recency gate is this
    # stress signal's own; the arrears base is the canonical map, not raw net_balance.
    arrears_map = await get_unit_arrears_map(building_id)
    recency_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id},
        {"_id": 0, "unit_number": 1, "last_payment_date": 1},
    ).to_list(500)
    last_payment_by_unit = {d.get("unit_number"): d.get("last_payment_date") for d in recency_docs}
    total_overdue = 0.0
    for unit, row in arrears_map.items():
        if not row["in_arrears"]:
            continue
        lpd = last_payment_by_unit.get(unit)
        if lpd is None or str(lpd) < cutoff.isoformat():
            total_overdue += row["arrears"]
    total_overdue = round(total_overdue, 2)

    levy_doc = await db.annual_levies.find_one(
        {"building_id": building_id}, sort=[("year", -1)]
    )
    annual_income = 0.0
    if levy_doc:
        af = levy_doc.get("admin_fund") or {}
        sf = levy_doc.get("sinking_fund") or {}
        ex_gst_income = float((af.get("levy_income") or 0) + (sf.get("levy_income") or 0))
        # GAP-FIN-065: annual_levies.*.levy_income is EX-GST (the get_levy_proposed_amounts
        # basis, finance_helpers.py), but the numerator total_overdue is inc-GST owner
        # arrears (net_balance-derived). Dividing inc-GST arrears by an ex-GST denominator
        # inflated arrears_pct by ~the GST rate. Convert the denominator to the inc-GST
        # payable basis so the ratio is apples-to-apples — and consistent with the
        # total_levied fallback below, which is already inc-GST. Same gst_multiplier
        # pattern as levy_fairness_service / forecast_service.
        from services.gst_service import get_building_levy_gst_settings
        gst_multiplier = (await get_building_levy_gst_settings(building_id)).get("gst_multiplier", 1.0)
        annual_income = round(ex_gst_income * gst_multiplier, 2)

    if annual_income == 0:
        # Fallback: sum of all levied amounts
        agg = await db.unit_levy_ledger.aggregate([
            {"$match": {"building_id": building_id}},
            {"$group": {"_id": None, "total": {"$sum": "$total_levied"}}}
        ]).to_list(1)
        annual_income = float(agg[0]["total"]) if agg else 1.0

    arrears_pct = total_overdue / max(annual_income, 1.0)
    normalised = min(100.0, arrears_pct * 500.0)  # 20% → 100
    threshold_breached = arrears_pct > 0.05

    return {
        "raw_value": round(arrears_pct, 4),
        "normalised_score": round(normalised, 1),
        "weight": STRESS_COMPONENTS["arrears_level"]["weight"],
        "weighted_contribution": round(normalised * STRESS_COMPONENTS["arrears_level"]["weight"], 2),
        "status": _component_status(normalised),
        "explanation": (
            f"Overdue levies (>30 days): ${total_overdue:,.0f} = "
            f"{arrears_pct * 100:.1f}% of annual levy income (${annual_income:,.0f})."
        ),
        "threshold_breached": threshold_breached,
        "_available": True,
    }


async def _phase2_arrears_concentration(building_id: str) -> dict:
    """
    raw_value = largest single unit arrears / total fund balance.
    Ideal = 0.  score = clamp(0, raw * 1000, 100)  (10% concentration → 100).
    """
    # Grace-aware canonical per-unit arrears (GAP-FIN-040) — concentration ratio
    # below stays this stress signal's own formula.
    from utils.finance_helpers import get_unit_arrears_map
    arrears_map = await get_unit_arrears_map(building_id)
    arrears_vals = [row["arrears"] for row in arrears_map.values() if row["in_arrears"]]
    max_single = max(arrears_vals) if arrears_vals else 0.0
    total_arrears = round(sum(arrears_vals), 2)

    # Total fund = admin + sinking closing balances
    ledger_a = await db.financial_years.find_one(
        {"building_id": building_id, "fund_type": "admin"}, sort=[("year", -1)]
    )
    ledger_s = await db.financial_years.find_one(
        {"building_id": building_id, "fund_type": "sinking"}, sort=[("year", -1)]
    )
    total_fund = (
            float((ledger_a or {}).get("closing_balance", 0) or 0)
            + float((ledger_s or {}).get("closing_balance", 0) or 0)
    )
    if total_fund == 0:
        total_fund = max(total_arrears, 1.0)

    concentration = max_single / total_fund
    normalised = min(100.0, concentration * 1000.0)  # 10% → 100
    threshold_breached = concentration > 0.05

    return {
        "raw_value": round(concentration, 4),
        "normalised_score": round(normalised, 1),
        "weight": STRESS_COMPONENTS["arrears_concentration"]["weight"],
        "weighted_contribution": round(normalised * STRESS_COMPONENTS["arrears_concentration"]["weight"], 2),
        "status": _component_status(normalised),
        "explanation": (
            f"Largest single unit arrears: ${max_single:,.0f} = "
            f"{concentration * 100:.1f}% of total fund balance (${total_fund:,.0f})."
        ),
        "threshold_breached": threshold_breached,
        "_available": True,
    }


async def _phase2_unreconciled_exposure(building_id: str) -> dict:
    """
    raw_value = unmatched/unreconciled receipts / monthly receipt total.
    Ideal = 0.  score = clamp(0, raw * 500, 100)  (20% → 100).
    """
    # Unreconciled items: status = "unmatched" or "pending"
    pipeline_unmatched = [
        {"$match": {
            "building_id": building_id,
            "transaction_type": {"$in": ["receipt", "credit", "payment"]},
            "reconciliation_status": {"$in": ["unmatched", "pending", None]},
        }},
        {"$group": {"_id": None, "total": {"$sum": {"$abs": "$amount"}}}}
    ]
    res_u = await db.financial_transactions.aggregate(pipeline_unmatched).to_list(1)
    unmatched_total = float(res_u[0]["total"]) if res_u else 0.0

    # Monthly receipts: last 30 days
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    pipeline_monthly = [
        {"$match": {
            "building_id": building_id,
            "transaction_type": {"$in": ["receipt", "credit"]},
            "transaction_date": {"$gte": cutoff},
        }},
        {"$group": {"_id": None, "total": {"$sum": {"$abs": "$amount"}}}}
    ]
    res_m = await db.financial_transactions.aggregate(pipeline_monthly).to_list(1)
    monthly_receipts = float(res_m[0]["total"]) if res_m else 0.0

    # Try bank_reconciliations collection as fallback
    if unmatched_total == 0 and monthly_receipts == 0:
        ur = await db.bank_reconciliations.find_one(
            {"building_id": building_id, "status": {"$in": ["pending", "unmatched"]}},
            sort=[("created_at", -1)]
        )
        if ur:
            unmatched_total = float(ur.get("unmatched_amount", 0) or 0)
            monthly_receipts = float(ur.get("total_receipts", 1) or 1)

    ratio = unmatched_total / max(monthly_receipts, 1.0)
    normalised = min(100.0, ratio * 500.0)
    available = monthly_receipts > 0
    threshold_breached = ratio > 0.10

    return {
        "raw_value": round(ratio, 4),
        "normalised_score": round(normalised, 1),
        "weight": STRESS_COMPONENTS["unreconciled_exposure"]["weight"],
        "weighted_contribution": round(normalised * STRESS_COMPONENTS["unreconciled_exposure"]["weight"], 2),
        "status": _component_status(normalised),
        "explanation": (
            f"Unreconciled cash: ${unmatched_total:,.0f} = "
            f"{ratio * 100:.1f}% of monthly receipts (${monthly_receipts:,.0f})."
        ),
        "threshold_breached": threshold_breached,
        "_available": available,
    }


async def _phase2_maintenance_pressure(building_id: str) -> dict:
    """
    raw_value = count of high-risk assets / total assets.
    Ideal = 0.  score = clamp(0, raw * 200, 100)  (50% high-risk → 100).
    """
    HIGH_RISK_CONDITIONS = {"poor", "critical", "end_of_life", "failed"}

    total_count = 0
    high_risk_count = 0
    for coll_name in ["building_assets", "digital_twin"]:
        coll = getattr(db, coll_name, None)
        if coll is None:
            continue
        all_assets = await coll.find(
            {"building_id": building_id},
            {"_id": 0, "condition": 1, "health_status": 1}
        ).to_list(500)
        total_count += len(all_assets)
        for a in all_assets:
            condition = (a.get("condition") or a.get("health_status") or "").lower()
            if condition in HIGH_RISK_CONDITIONS:
                high_risk_count += 1

    ratio = high_risk_count / max(total_count, 1)
    normalised = min(100.0, ratio * 200.0)
    available = total_count > 0
    threshold_breached = ratio > 0.20

    return {
        "raw_value": round(ratio, 4),
        "normalised_score": round(normalised, 1),
        "weight": STRESS_COMPONENTS["maintenance_pressure"]["weight"],
        "weighted_contribution": round(normalised * STRESS_COMPONENTS["maintenance_pressure"]["weight"], 2),
        "status": _component_status(normalised),
        "explanation": (
            f"{high_risk_count} of {total_count} assets in high-risk condition "
            f"({ratio * 100:.1f}%)."
        ),
        "threshold_breached": threshold_breached,
        "_available": available,
    }


async def _phase2_liquidity_coverage(building_id: str) -> dict:
    """
    raw_value = admin_balance / (annual_admin_expenses / 4).
    Ideal ≥ 1.0 (3-month cover).  score = clamp(0, 100 * (1 - ratio), 100).
    """
    ledger_a = await db.financial_years.find_one(
        {"building_id": building_id, "fund_type": "admin"}, sort=[("year", -1)]
    )
    admin_balance = float((ledger_a or {}).get("closing_balance", 0) or 0)

    levy_doc = await db.annual_levies.find_one(
        {"building_id": building_id}, sort=[("year", -1)]
    )
    annual_admin_exp = 0.0
    if levy_doc:
        af = levy_doc.get("admin_fund") or {}
        annual_admin_exp = float(af.get("total_expenses", 0) or 0)

    if annual_admin_exp == 0:
        pipeline = [
            {"$match": {"building_id": building_id, "transaction_type": "expense",
                        "fund": {"$in": ["admin", "admin_fund", "administrative"]}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]
        res = await db.financial_transactions.aggregate(pipeline).to_list(1)
        annual_admin_exp = float(res[0]["total"]) if res else 0.0

    three_month_exp = annual_admin_exp / 4.0
    ratio = admin_balance / max(three_month_exp, 1.0)
    normalised = max(0.0, min(100.0, (1.0 - ratio) * 100.0))
    threshold_breached = ratio < 1.0

    return {
        "raw_value": round(ratio, 4),
        "normalised_score": round(normalised, 1),
        "weight": STRESS_COMPONENTS["liquidity_coverage"]["weight"],
        "weighted_contribution": round(normalised * STRESS_COMPONENTS["liquidity_coverage"]["weight"], 2),
        "status": _component_status(normalised),
        "explanation": (
            f"Admin fund balance ${admin_balance:,.0f} covers "
            f"{ratio:.2f}x three-month expenditure (${three_month_exp:,.0f}/quarter). "
            "Ideal ≥ 1.0."
        ),
        "threshold_breached": threshold_breached,
        "_available": True,
    }


async def _phase2_capital_shock_indicator(building_id: str) -> dict:
    """
    Fetch CSI from capital_shock_service; normalise to 0-100.
    CSI ≥ 5.0 → severe (100).  CSI 0 → 0.
    """
    try:
        from services.capital_shock_service import detect_capital_shocks
        shock_data = await detect_capital_shocks(building_id)
        csi_info = shock_data.get("capital_shock_index", {})
        # capital_shock_index may be a dict or float depending on version
        if isinstance(csi_info, dict):
            csi_value = float(csi_info.get("capital_shock_index", 0) or 0)
        else:
            csi_value = float(csi_info or 0)
        available = True
    except Exception:
        csi_value = 0.0
        available = False

    # Normalise: CSI of 5.0 = 100; linear
    normalised = min(100.0, csi_value / 5.0 * 100.0)
    threshold_breached = csi_value >= 2.0

    return {
        "raw_value": round(csi_value, 4),
        "normalised_score": round(normalised, 1),
        "weight": STRESS_COMPONENTS["capital_shock_indicator"]["weight"],
        "weighted_contribution": round(normalised * STRESS_COMPONENTS["capital_shock_indicator"]["weight"], 2),
        "status": _component_status(normalised),
        "explanation": (
            f"Capital Shock Index: {csi_value:.2f} "
            f"({'severe' if csi_value >= 5 else 'major' if csi_value >= 2 else 'moderate' if csi_value >= 0.5 else 'normal'})."
        ),
        "threshold_breached": threshold_breached,
        "_available": available,
    }


async def _phase2_trend(building_id: str, current_score: float) -> Optional[str]:
    """Compare to previous stored Phase 2 score to determine trend."""
    prev = await db.building_financial_health_p2.find_one(
        {"building_id": building_id},
        sort=[("computed_at", -1)]
    )
    if not prev:
        return None
    prev_score = prev.get("score", current_score)
    delta = current_score - prev_score
    if abs(delta) < 2:
        return "stable"
    return "deteriorating" if delta > 0 else "improving"


async def compute_phase2_stress_score(building_id: str) -> dict:
    """
    Compute the Phase 2 7-component BuildingFinancialStressScore.

    Returns a structured dict with score, category, per-component breakdown,
    data completeness, optional graceful-degradation note, and trend.
    """
    now = _now()

    # Run all 7 component coroutines in parallel
    (
        ra, al, ac, ue, mp, lc, csi
    ) = await asyncio.gather(
        _phase2_reserve_adequacy(building_id),
        _phase2_arrears_level(building_id),
        _phase2_arrears_concentration(building_id),
        _phase2_unreconciled_exposure(building_id),
        _phase2_maintenance_pressure(building_id),
        _phase2_liquidity_coverage(building_id),
        _phase2_capital_shock_indicator(building_id),
    )

    component_results = {
        "reserve_adequacy": ra,
        "arrears_level": al,
        "arrears_concentration": ac,
        "unreconciled_exposure": ue,
        "maintenance_pressure": mp,
        "liquidity_coverage": lc,
        "capital_shock_indicator": csi,
    }

    # Data completeness: fraction of components with real data
    available_count = sum(1 for c in component_results.values() if c.get("_available", True))
    data_completeness = round(available_count / len(component_results), 3)

    # Weighted score — use available components only, re-normalise weights.
    # If no components have data, score defaults to 0 (treated as "healthy").
    total_weight = sum(
        STRESS_COMPONENTS[k]["weight"]
        for k, c in component_results.items()
        if c.get("_available", True)
    )
    weighted_score = 0.0
    if total_weight > 0:
        for key, comp in component_results.items():
            if comp.get("_available", True):
                weighted_score += (
                        comp["normalised_score"]
                        * STRESS_COMPONENTS[key]["weight"]
                        / total_weight
                )
    score = round(min(100.0, max(0.0, weighted_score)), 1)
    category = _stress_category(score)

    # Strip internal _available flag from output
    clean_components: dict = {}
    for key, comp in component_results.items():
        entry = {k: v for k, v in comp.items() if not k.startswith("_")}
        entry["description"] = STRESS_COMPONENTS[key]["description"]
        clean_components[key] = entry

    # Top drivers for human-readable explanation
    drivers = sorted(
        clean_components.items(),
        key=lambda kv: kv[1]["weighted_contribution"],
        reverse=True
    )[:3]
    driver_names = ", ".join(k.replace("_", " ") for k, _ in drivers)
    explanation = (
        f"Overall stress score {score:.1f}/100 ({category}). "
        f"Top contributors: {driver_names}."
    )
    if data_completeness < 0.6:
        explanation += " Score based on partial data — confidence is reduced."

    trend = await _phase2_trend(building_id, score)

    degradation_note: Optional[str] = None
    if data_completeness < 0.6:
        missing = [
            k for k, c in component_results.items()
            if not c.get("_available", True)
        ]
        degradation_note = (
            f"Data completeness {data_completeness:.0%}. "
            f"Missing components: {', '.join(missing)}. "
            "Score should be treated as indicative only."
        )

    result = {
        "building_id": building_id,
        "score": score,
        "category": category,
        "components": clean_components,
        "explanation": explanation,
        "trend": trend,
        "computed_at": now,
        "data_completeness": data_completeness,
        "graceful_degradation_note": degradation_note,
    }

    # Persist to dedicated Phase 2 collection
    try:
        month_key = now[:7]
        await db.building_financial_health_p2.update_one(
            {"building_id": building_id, "month": month_key},
            {"$set": {**result, "month": month_key}},
            upsert=True
        )
    except Exception as exc:
        logger.warning("building_stress_service: snapshot cache write failed for %s/%s: %s", building_id, month_key,
                       exc)

    return result
