# @featuretrace:financial-health-score — composite 0-100 financial health score for a building/year.
# Layer: service
# Data flow: get_levy_fund_data() + get_unit_ledger_stats() + forecast/cashflow projections → compute_financial_health() → HealthScoreResponse (report_service.py PDF, dashboard cards) (building-scoped).
# Related: backend/services/report_service.py (consumes compute_financial_health, shares get_levy_fund_data/compute_combined_fund_totals)
#          backend/utils/finance_helpers.py (get_levy_fund_data, compute_combined_fund_totals, get_unit_ledger_stats)
"""
Health Service — computes financial health score (0-100) for a given year.

Scoring components (100 pts total):
  1. Surplus ratio       20pts — closing_balance / total_income
  2. Arrears %           20pts — units_owing / total_units (inverted)
  3. Cashflow buffer     15pts — min_cashflow / monthly_expenses
  4. Forecast stability  15pts — forecast coefficient of variation (inverted)
  5. Budget discipline   15pts — avg |actual - budget| / budget (inverted)
  6. Expense volatility   5pts — year-over-year expense variance (inverted)
  7. Reserve adequacy    10pts — actual sinking fund reserve vs inflation-model target
"""

import numpy as np

from database import db
from services.forecast_service import generate_cashflow_projection, generate_sinking_fund_projection
from utils.finance_helpers import (
    compute_combined_fund_totals,
    get_budget_summary,
    get_levy_fund_data,
    get_unit_ledger_stats,
)
from utils.finance_logger import timed

# Kept as a module-level alias — this function used to be a private,
# byte-identical copy of report_service.py's own `_get_levy_data`.
# Both now delegate to the single source in finance_helpers.py (GAP-FIN-016 Phase 2).
_get_levy_data = get_levy_fund_data


def _score_clamp(val: float) -> float:
    """Generated function header.

    Function: _score_clamp
    Path: backend/services/health_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return max(0.0, min(val, 1.0))


async def compute_financial_health(year: str, building_id: str) -> dict:
    """
    Compute comprehensive financial health score. Scoped to building.
    Returns HealthScoreResponse-compatible dict.
    """
    with timed("health_compute", year=year, building_id=building_id):
        return await _compute_financial_health_impl(year, building_id)


async def _compute_financial_health_impl(year: str, building_id: str) -> dict:
    """Generated function header.

    Function: _compute_financial_health_impl
    Path: backend/services/health_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    levy = await _get_levy_data(year, building_id)
    if not levy:
        return {
            "year": year,
            "score": 0.0,
            "breakdown": {
                "surplus_ratio": 0, "arrears_pct": 0, "cashflow_buffer": 0,
                "forecast_stability": 0, "budget_discipline": 0, "expense_volatility": 0
            },
            "risk_level": "critical",
            "details": {}
        }

    fund_totals = compute_combined_fund_totals(levy)
    total_income = fund_totals["total_income"]
    total_expenses = fund_totals["total_expenses"]
    closing_balance = fund_totals["closing_balance"]

    breakdown = {}
    details = {}

    # ── 1. Surplus ratio (20 pts) ──────────────────────────────────────────
    # Target: closing_balance / total_income >= 10% → full marks
    surplus_ratio = (closing_balance / total_income) if total_income > 0 else 0
    surplus_score = min(surplus_ratio / 0.10, 1.0)  # 10% = full marks
    breakdown["surplus_ratio"] = round(surplus_score * 20, 2)
    details["surplus_ratio_raw"] = round(surplus_ratio, 4)
    details["closing_balance"] = closing_balance
    details["total_income"] = total_income

    # ── 2. Arrears % (20 pts) ─────────────────────────────────────────────
    ledger_stats = await get_unit_ledger_stats(year, building_id)
    total_units_count = await db.units.count_documents({"building_id": building_id})
    # Grace-aware canonical arrears count (GAP-FIN-040) as the health-score input —
    # not the raw net_balance>0 units_owing from get_unit_ledger_stats.
    from utils.finance_helpers import get_building_arrears_summary
    units_owing = (await get_building_arrears_summary(building_id, year))["units_in_arrears"]
    arrears_pct = (units_owing / total_units_count) if total_units_count > 0 else 0
    # 0% arrears = full marks, 20%+ arrears = 0 marks
    arrears_score = max(0.0, 1 - (arrears_pct / 0.20))
    breakdown["arrears_pct"] = round(arrears_score * 20, 2)
    details["units_owing"] = units_owing
    details["total_units"] = total_units_count
    details["arrears_pct_raw"] = round(arrears_pct * 100, 2)

    # ── 3. Cashflow buffer (15 pts) ───────────────────────────────────────
    cashflow = await generate_cashflow_projection(year, building_id)
    min_balance = cashflow.get("min_balance", 0)
    monthly_expenses = total_expenses / 12 if total_expenses > 0 else 1
    # Buffer = min_balance / monthly_expenses; target >= 2 months
    buffer_months = min_balance / monthly_expenses if monthly_expenses > 0 else 0
    buffer_score = min(buffer_months / 2.0, 1.0)  # 2 months = full marks
    if min_balance < 0:
        buffer_score = 0.0
    breakdown["cashflow_buffer"] = round(max(0, buffer_score) * 15, 2)
    details["min_cashflow_balance"] = round(min_balance, 2)
    details["buffer_months"] = round(buffer_months, 2)

    # ── 4. Forecast stability (15 pts) ────────────────────────────────────
    forecasts = await db.financial_forecasts.find(
        {"building_id": building_id, "financial_year": year}, {"_id": 0, "projection_year_1": 1, "projection_year_2": 1,
                                                               "projection_year_3": 1}
    ).to_list(100)
    if forecasts:
        all_vals = []
        for f in forecasts:
            vals = [f.get("projection_year_1", 0), f.get("projection_year_2", 0),
                    f.get("projection_year_3", 0)]
            for v in vals:
                if v > 0:
                    all_vals.append(v)
        if all_vals:
            cv = np.std(all_vals) / np.mean(all_vals) if np.mean(all_vals) > 0 else 0
            # Low CV = stable = high score; CV >= 1.0 = 0 marks
            stability_score = max(0.0, 1.0 - cv)
        else:
            stability_score = 0.5
    else:
        stability_score = 0.5
    breakdown["forecast_stability"] = round(stability_score * 15, 2)
    details["forecast_count"] = len(forecasts)

    # ── 5. Budget discipline (15 pts) ─────────────────────────────────────
    budget_summary = await get_budget_summary(year, building_id)
    variances = []
    for fund_type, sums in budget_summary.items():
        budgeted = sums.get("budgeted", 0)
        actual = sums.get("actual", 0)
        if budgeted > 0:
            variances.append(abs(actual - budgeted) / budgeted)
    if variances:
        avg_variance = sum(variances) / len(variances)
        # 0% avg variance = full marks; 30%+ = 0
        discipline_score = max(0.0, 1.0 - (avg_variance / 0.30))
    else:
        discipline_score = 0.5
    breakdown["budget_discipline"] = round(discipline_score * 15, 2)
    details["avg_budget_variance_pct"] = round((avg_variance * 100) if variances else 0, 2)

    # ── 6. Expense volatility (15 pts) ────────────────────────────────────
    # Compare total expenses vs prior 2 years
    prev_years = [str(int(year) - 1), str(int(year) - 2)]
    prior_expenses = []
    for py in prev_years:
        prior_levy = await _get_levy_data(py, building_id)
        if prior_levy:
            pa = prior_levy.get("admin_fund", {})
            ps = prior_levy.get("sinking_fund", {})
            prior_expenses.append((pa.get("total_expenses", 0) or 0) + (ps.get("total_expenses", 0) or 0))

    if prior_expenses and total_expenses > 0:
        yoy_changes = [abs(total_expenses - p) / p for p in prior_expenses if p > 0]
        avg_change = sum(yoy_changes) / len(yoy_changes) if yoy_changes else 0
        # 0% change = full marks; 25%+ change = 0
        volatility_score = max(0.0, 1.0 - (avg_change / 0.25))
    else:
        volatility_score = 0.5
    breakdown["expense_volatility"] = round(volatility_score * 5, 2)  # reduced: 15→5pts
    details["expense_yoy_change_pct"] = round((yoy_changes[0] * 100) if 'yoy_changes' in dir() and yoy_changes else 0,
                                              2)

    # ── 7. Reserve adequacy (10 pts) ──────────────────────────────────────
    # Compare actual sinking fund balance for this year vs the inflation-model
    # projected reserve. Uses the 15-year plan projection at 3.5% CPI.
    try:
        sf_proj = await generate_sinking_fund_projection(building_id)
        # Find the projected reserve row matching this year
        year_int = int(year) if year.isdigit() else 0
        target_row = next(
            (r for r in sf_proj.get("rows", []) if r["year"] == year_int), None
        )
        if target_row:
            model_reserve = target_row["projected_reserve"]
            # Get actual sinking fund balance from levy data
            sf = levy.get("sinking_fund", {}) if levy else {}
            actual_reserve = sf.get("closing_balance", 0) or 0
            if actual_reserve <= 0:
                # Fallback: use sinking fund plan's closing_balance for this year
                plan_row = await db.sinking_fund_plan.find_one(
                    {"building_id": building_id, "year": year_int}, {"_id": 0, "closing_balance": 1}
                )
                actual_reserve = (plan_row or {}).get("closing_balance", 0) or 0

            if model_reserve > 0:
                adequacy_ratio = actual_reserve / model_reserve
                # Full marks at 100% adequacy; 0 marks below 40%
                reserve_score = max(0.0, min(1.0, (adequacy_ratio - 0.40) / 0.60))
            else:
                reserve_score = 0.5

            details["sinking_model_reserve"] = round(model_reserve, 2)
            details["sinking_actual_reserve"] = round(actual_reserve, 2)
            details["sinking_adequacy_pct"] = round(
                (actual_reserve / model_reserve * 100) if model_reserve > 0 else 0, 1
            )
        else:
            reserve_score = 0.5
            details["sinking_model_reserve"] = 0
            details["sinking_actual_reserve"] = 0
            details["sinking_adequacy_pct"] = 0
    except Exception:
        reserve_score = 0.5

    breakdown["reserve_adequacy"] = round(reserve_score * 10, 2)

    # ── Final score ────────────────────────────────────────────────────────
    total_score = sum(breakdown.values())
    total_score = round(max(0.0, min(100.0, total_score)), 2)

    # Risk level
    if total_score >= 90:
        risk_level = "excellent"
    elif total_score >= 75:
        risk_level = "good"
    elif total_score >= 55:
        risk_level = "moderate"
    elif total_score >= 35:
        risk_level = "at_risk"
    else:
        risk_level = "critical"

    return {
        "year": year,
        "score": total_score,
        "breakdown": breakdown,
        "risk_level": risk_level,
        "details": details,
    }
