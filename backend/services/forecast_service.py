# @featuretrace:financial-forecast — multi-year expense/levy/cashflow forecast models.
# Layer: service
# Data flow: get_levy_fund_data() + financial_categories history → generate_category_forecast()/generate_levy_projection()/generate_cashflow_projection() → db.financial_forecasts (building-scoped).
# Related: backend/services/health_service.py (consumes cashflow/sinking-fund projections)
#          backend/utils/finance_helpers.py (get_levy_fund_data)
"""
Forecast Service — generates multi-year expense forecasts.

Models:
  1. Linear regression (numpy polyfit) - uses last 3 years of actuals
  2. Inflation model - applies 3.5% AUS CPI compounding
  3. Capital works model - spreads known capital items over 3 years

Stores results in financial_forecasts collection.
"""
import logging
import uuid
from datetime import datetime, timezone

import numpy as np
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

from database import db
from repositories.financial_repository import aggregate_actual_by_category
from services.settings_service import get_general_settings_or_default
from utils.finance_helpers import TOTAL_UOE, get_levy_fund_data, get_levy_rates, get_levy_proposed_amounts
from utils.finance_logger import timed

_CPI = 0.035  # 3.5% Australian CPI default


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/forecast_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _linear_forecast(values: List[float], n_years: int = 3) -> tuple:
    """
    Fit linear regression to historical values.
    Returns (projections_list, confidence_score).
    confidence_score is R² of the fit (0-1).
    """
    if len(values) < 2:
        return None, 0.0
    x = np.arange(len(values), dtype=float)
    coeffs = np.polyfit(x, values, deg=1)
    p = np.poly1d(coeffs)
    # R² computation
    y_mean = np.mean(values)
    ss_tot = sum((v - y_mean) ** 2 for v in values)
    ss_res = sum((values[i] - p(x[i])) ** 2 for i in range(len(values)))
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    next_x = len(values)
    projections = [max(0.0, float(p(next_x + i))) for i in range(n_years)]
    return projections, max(0.0, min(1.0, float(r2)))


def _inflation_forecast(last_value: float, n_years: int = 3, rate: float = _CPI) -> List[float]:
    """Apply compound inflation to project forward."""
    return [round(last_value * ((1 + rate) ** (i + 1)), 2) for i in range(n_years)]


async def _get_category_history(category: str, fund_type: str, building_id: str) -> Dict[str, float]:
    """
    Fetch actual amount per year for a category across all years.
    Reads from financial_categories (budgeted) and financial_transactions (actuals).
    """
    cats = await db.financial_categories.find(
        {"name": category, "fund_type": fund_type, "building_id": building_id},
        {"_id": 0, "financial_year": 1, "budgeted_amount": 1}
    ).sort("financial_year", 1).to_list(10)

    result: Dict[str, float] = {}
    for c in cats:
        yr = c["financial_year"]
        actuals = await aggregate_actual_by_category(yr, fund_type, building_id)
        value = actuals.get(category) or c.get("budgeted_amount") or 0
        result[yr] = value

    # Fallback: if no data in new collections, read budgeted_amount from levy_categories
    if not result:
        legacy_cats = await db.levy_categories.find(
            {"name": category, "fund_type": fund_type, "building_id": building_id},
            {"_id": 0, "year": 1, "budgeted_amount": 1}
        ).sort("year", 1).to_list(10)
        for c in legacy_cats:
            result[c["year"]] = c.get("budgeted_amount") or 0

    return result


async def generate_category_forecast(year: str, fund_type: Optional[str] = None, building_id: str = None) -> List[dict]:
    """
    Generate expense forecasts for all categories in the given year.
    Runs all 3 models and selects best based on data availability.

    Args:
        year: Base year for forecast (e.g. "2026")
        fund_type: "administrative" | "sinking" | None (all)

    Returns:
        List of forecast documents upserted into financial_forecasts.
    """
    with timed("forecast_generation", year=year, fund_type=fund_type or "all"):
        return await _generate_category_forecast_impl(year, fund_type, building_id)


async def _generate_category_forecast_impl(year: str, fund_type: Optional[str] = None, building_id: str = None) -> List[
    dict]:
    """Generated function header.

    Function: _generate_category_forecast_impl
    Path: backend/services/forecast_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from services.financial_service import get_legacy_levy_categories_shape
    categories = await get_legacy_levy_categories_shape(year, fund_type, building_id)
    if not categories:
        return []

    forecasts = []
    for cat in categories:
        cat_name = cat["name"]
        cat_fund = cat.get("fund_type", "")
        history = await _get_category_history(cat_name, cat_fund, building_id)
        sorted_years = sorted(history.keys())
        values = [history[y] for y in sorted_years]

        # Determine best method
        method = "inflation"
        projections = _inflation_forecast(values[-1] if values else 0)
        confidence = 0.5
        assumptions: Dict[str, Any] = {"cpi": _CPI, "base_value": values[-1] if values else 0}

        if len(values) >= 2:
            linear_proj, r2 = _linear_forecast(values)
            if linear_proj and r2 > 0.6:
                method = "linear"
                projections = linear_proj
                confidence = r2
                assumptions = {"r2": r2, "data_years": sorted_years, "slope": float(
                    np.polyfit(np.arange(len(values)), values, 1)[0]
                )}
            else:
                projections = _inflation_forecast(values[-1])
                confidence = 0.5

        doc_id = str(uuid.uuid4())
        forecast_doc = {
            "id": doc_id,
            "building_id": building_id,
            "financial_year": year,
            "fund_type": cat_fund,
            "category": cat_name,
            "projection_year_1": round(projections[0], 2),
            "projection_year_2": round(projections[1], 2),
            "projection_year_3": round(projections[2], 2),
            "method": method,
            "confidence_score": round(confidence, 4),
            "assumptions": assumptions,
            "created_at": _now(),
        }

        await db.financial_forecasts.update_one(
            {"building_id": building_id, "financial_year": year, "category": cat_name, "fund_type": cat_fund},
            {"$set": forecast_doc},
            upsert=True
        )
        forecasts.append(forecast_doc)

    return forecasts


async def generate_levy_projection(year: str, building_id: str) -> dict:
    """
    Project levy rates for the next 3 years based on expense forecasts and
    current levy plans (rate per UOE × total UOE = required collection).

    Returns per-fund projections with recommended levy rates and total collections.

    Note: internally calls detect_capital_shocks() to compute a capital-shock-adjusted
    levy projection. Callers do not need to call detect_capital_shocks() separately.
    """
    levy = await get_levy_fund_data(year, building_id)
    if not levy:
        return {"year": year, "admin": {}, "sinking": {}, "combined": {}}

    admin_fund = levy.get("admin_fund", {})
    sinking_fund = levy.get("sinking_fund", {})
    total_uoe = levy.get("total_uoe", TOTAL_UOE)
    rates = await get_levy_rates(year, building_id)
    gst_multiplier = 1 + float(rates.get("gst_rate", 0) or 0)
    admin_rate = rates.get("admin_annual_ex_gst", 0)
    sinking_rate = rates.get("sinking_annual_ex_gst", 0)

    admin_expenses = admin_fund.get("total_expenses", 0) or 0
    sinking_expenses = sinking_fund.get("total_expenses", 0) or 0

    def _project_fund(base_expenses: float, base_rate: float) -> dict:
        """Generated function header.

        Function: _project_fund
        Path: backend/services/forecast_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        projections = {}
        for i in range(1, 4):
            yr = str(int(year) + i)
            projected_expenses = round(base_expenses * ((1 + _CPI) ** i), 2)
            required_rate = round(projected_expenses / total_uoe, 4) if total_uoe > 0 else 0.0
            required_rate_payable = round(required_rate * gst_multiplier, 4)
            rate_change_pct = round(
                ((required_rate - base_rate) / base_rate * 100) if base_rate > 0 else 0.0, 2
            )
            total_collection = round(required_rate * total_uoe, 2)
            projections[yr] = {
                "projected_expenses": projected_expenses,
                "required_rate_per_uoe": required_rate,
                "required_rate_per_uoe_payable": required_rate_payable,
                "rate_change_pct": rate_change_pct,
                "total_collection": total_collection,
                "total_collection_payable": round(total_collection * gst_multiplier, 2),
                "model": "inflation",
                "cpi_rate": _CPI,
            }
        return projections

    admin_proj = _project_fund(admin_expenses, admin_rate)
    sinking_proj = _project_fund(sinking_expenses, sinking_rate)

    combined = {}
    for yr in admin_proj:
        combined[yr] = {
            "projected_expenses": round(
                admin_proj[yr]["projected_expenses"] + sinking_proj[yr]["projected_expenses"], 2
            ),
            "total_collection": round(
                admin_proj[yr]["total_collection"] + sinking_proj[yr]["total_collection"], 2
            ),
            "total_collection_payable": round(
                admin_proj[yr]["total_collection_payable"] + sinking_proj[yr]["total_collection_payable"], 2
            ),
            "combined_rate_per_uoe": round(
                admin_proj[yr]["required_rate_per_uoe"] + sinking_proj[yr]["required_rate_per_uoe"], 4
            ),
            "combined_rate_per_uoe_payable": round(
                admin_proj[yr]["required_rate_per_uoe_payable"]
                + sinking_proj[yr]["required_rate_per_uoe_payable"],
                4,
            ),
        }

    capital_shock_adjusted = None
    try:
        from services.capital_shock_service import detect_capital_shocks
        shock = await detect_capital_shocks(building_id)
        increase_pct = shock.get("recommended_levy_increase_pct")
        if increase_pct:
            adjusted = {}
            combined_rate = admin_rate + sinking_rate
            for i in range(1, 4):
                yr = str(int(year) + i)
                adjusted_rate = round(combined_rate * ((1 + (increase_pct / 100)) ** i), 4)
                adjusted[yr] = {
                    "adjusted_rate_per_uoe": adjusted_rate,
                    "total_collection": round(adjusted_rate * total_uoe, 2),
                    "increase_pct": increase_pct,
                }
            capital_shock_adjusted = {
                "increase_pct": increase_pct,
                "projection": adjusted,
                "recommendation": shock.get("recommendation"),
            }
    except Exception:
        capital_shock_adjusted = None

    result = {
        "year": year,
        "base_admin_rate": admin_rate,
        "base_sinking_rate": sinking_rate,
        "base_admin_rate_payable": rates.get("admin_annual", 0),
        "base_sinking_rate_payable": rates.get("sinking_annual", 0),
        "total_uoe": total_uoe,
        "gst_registered": bool(rates.get("gst_registered", False)),
        "gst_rate": float(rates.get("gst_rate", 0) or 0),
        "admin": admin_proj,
        "sinking": sinking_proj,
        "combined": combined,
        "capital_shock_adjusted_levy_projection": capital_shock_adjusted,
    }

    await db.financial_forecasts.update_one(
        {"building_id": building_id, "financial_year": year, "forecast_type": "levy_projection"},
        {"$set": {**result, "building_id": building_id, "forecast_type": "levy_projection", "created_at": _now()}},
        upsert=True
    )
    return result


async def generate_cashflow_projection(year: str, building_id: str) -> dict:
    """
    Project monthly cashflow for the given year.
    Levy income assumed quarterly (Q1=Jul, Q2=Oct, Q3=Jan, Q4=Apr).
    Expenses spread evenly with known seasonal adjustments.
    """
    levy = await get_levy_fund_data(year, building_id)
    if not levy:
        return {"year": year, "months": [], "annual_income": 0, "annual_expenses": 0,
                "min_balance": 0, "risk_months": [], "opening_balance": 0}

    admin_fund = levy.get("admin_fund", {})
    sinking_fund = levy.get("sinking_fund", {})

    # .get(key, 0) only defaults when the key is MISSING, not when its stored value is
    # explicitly None (e.g. East Gate FY2026, where levy_income is None -- unknown, not
    # separable from expense activity) -- `or 0` guards against that case too.
    annual_income = ((admin_fund.get("levy_income", 0) or 0) + (sinking_fund.get("levy_income", 0) or 0) +
                     (admin_fund.get("other_income", 0) or 0) + (sinking_fund.get("other_income", 0) or 0))
    annual_expenses = (admin_fund.get("total_expenses", 0) + sinking_fund.get("total_expenses", 0))
    opening_balance = (admin_fund.get("opening_balance", 0) + sinking_fund.get("opening_balance", 0))

    quarterly_income = annual_income / 4
    # Australian financial year (Jul–Jun). Levy quarters: Q1 starts Jul, Q2 Oct, Q3 Jan, Q4 Apr.
    # Income received at the start of each quarter (Jul=7, Oct=10, Jan=1, Apr=4).
    income_months = {1: quarterly_income, 4: quarterly_income,
                     7: quarterly_income, 10: quarterly_income}
    monthly_expense = annual_expenses / 12

    months_data = []
    cumulative = opening_balance
    risk_months = []

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    for m in range(1, 13):
        inc = income_months.get(m, 0)
        exp = monthly_expense
        net = inc - exp
        cumulative += net
        is_risk = cumulative < 0

        month_str = f"{month_names[m - 1]} {year}"
        if is_risk:
            risk_months.append(month_str)

        months_data.append({
            "month": month_str,
            "month_number": m,
            "expected_income": round(inc, 2),
            "expected_expenses": round(exp, 2),
            "net_cashflow": round(net, 2),
            "cumulative_balance": round(cumulative, 2),
            "is_risk_month": is_risk,
        })

    return {
        "year": year,
        "months": months_data,
        "annual_income": round(annual_income, 2),
        "annual_expenses": round(annual_expenses, 2),
        "min_balance": round(min((m["cumulative_balance"] for m in months_data), default=0), 2),
        "risk_months": risk_months,
        "opening_balance": round(opening_balance, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sinking Fund Inflation Projection
# ─────────────────────────────────────────────────────────────────────────────

#: Smoothed annual collection from the 15-year plan ($106,763).
#: Computed as sum(inflated_expenditures 2022-2035) / 14 years at 3.5% CPI.
_SINKING_ANNUAL_COLLECTION_MODEL = 106_763.0


async def generate_sinking_fund_projection(building_id: str, inflation_rate: float = _CPI) -> dict:
    """
    Apply CPI inflation to the 15-year sinking fund capital works plan.

    Algorithm (mirrors the strata manager's model):
      1. Base expenditure per year = raw planned spend from sinking_fund_plan.
      2. Inflate each year's expenditure by (1 + inflation_rate)^i where
         i = 0 for 2022 (the first active year), i = 1 for 2023, etc.
      3. Smooth total inflated cost over 14 years → flat annual_collection.
      4. Compute cumulative reserve: reserve += annual_collection - inflated_spend.

    Returns:
        rows: list per year with base_expenditure, inflated_expenditure,
              annual_collection (flat), projected_reserve
        annual_collection: the flat annual collection needed (≈$106,763 at 3.5%)
        inflation_rate: rate used
        total_inflated_expenditure: sum of all inflated amounts
    """
    rows = await db.sinking_fund_plan.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("year", 1).to_list(20)

    if not rows:
        return {
            "rows": [], "annual_collection": 0, "inflation_rate": inflation_rate,
            "total_inflated_expenditure": 0, "seeded": False,
        }

    # 2022 is year 0 for the inflation index (no inflation applied to base year)
    active = [r for r in rows if r["year"] >= 2022]
    inflated_amounts = []
    for i, row in enumerate(active):
        base = row.get("expenditure", 0)
        inflated_amounts.append(round(base * ((1 + inflation_rate) ** i), 2))

    total_inflated = round(sum(inflated_amounts), 2)
    n_years = len(active)
    annual_collection = round(total_inflated / n_years, 2) if n_years > 0 else 0

    # Build reserve projection row by row
    reserve = 0.0
    projection_rows = []
    for i, row in enumerate(active):
        reserve = round(reserve + annual_collection - inflated_amounts[i], 2)
        projection_rows.append({
            "year": row["year"],
            "base_expenditure": round(row.get("expenditure", 0), 2),
            "inflated_expenditure": inflated_amounts[i],
            "annual_collection": annual_collection,
            "projected_reserve": reserve,
            "is_actual": row.get("is_actual", row["year"] <= 2026),
            "is_major_capital_year": row.get("is_major_capital_year",
                                             row["year"] in {2029, 2032, 2035}),
        })

    return {
        "rows": projection_rows,
        "annual_collection": annual_collection,
        "inflation_rate": inflation_rate,
        "total_inflated_expenditure": total_inflated,
        "n_years": n_years,
        "seeded": True,
    }


async def simulate_levy_adjustment(year: str, increase_pct: float, building_id: str) -> dict:
    """
    Model the impact of a levy increase on all units.
    Returns per-unit impact and aggregate collection change.
    Also shows sinking fund reserve adequacy gap vs the inflation model.
    """
    levy = await get_levy_fund_data(year, building_id)
    if not levy:
        return {"year": year, "increase_pct": increase_pct, "current_total_collection": 0,
                "new_total_collection": 0, "surplus_deficit": 0, "per_unit_samples": [], "all_units": []}

    multiplier = 1 + (increase_pct / 100)
    rates = await get_levy_rates(year, building_id)
    total_rate = rates.get("admin_annual_ex_gst", 0) + rates.get("sinking_annual_ex_gst", 0)
    total_rate_payable = rates.get("admin_annual", 0) + rates.get("sinking_annual", 0)
    new_rate = total_rate * multiplier
    new_rate_payable = total_rate_payable * multiplier

    units = await db.units.find(
        {"building_id": building_id}, {"_id": 0, "unit_number": 1, "entitlement": 1}
    ).to_list(200)

    all_units = []
    total_current = 0
    total_new = 0

    for u in units:
        ue = u.get("entitlement", 0)
        current = round(total_rate * ue, 2)
        new_annual = round(new_rate * ue, 2)
        current_payable = round(total_rate_payable * ue, 2)
        new_annual_payable = round(new_rate_payable * ue, 2)
        impact = round(new_annual - current, 2)
        total_current += current
        total_new += new_annual
        all_units.append({
            "unit_number": u.get("unit_number", ""),
            "current_annual": current,
            "new_annual": new_annual,
            "current_annual_payable": current_payable,
            "new_annual_payable": new_annual_payable,
            "impact": impact,
            "impact_payable": round(new_annual_payable - current_payable, 2),
            "entitlement": ue,
        })

    # Surplus/deficit: new collection vs current expenses
    admin_fund = levy.get("admin_fund", {})
    sinking_fund = levy.get("sinking_fund", {})
    total_expenses = admin_fund.get("total_expenses", 0) + sinking_fund.get("total_expenses", 0)
    surplus_deficit = round(total_new - total_expenses, 2)

    samples = sorted(all_units, key=lambda x: x["entitlement"], reverse=True)[:10]

    # ── Sinking Fund Reserve Adequacy (inflation model) ────────────────────
    # The inflation model requires $106,763/yr. Compare current sinking levy vs model.
    sinking_levy = levy.get("sinking_fund", {}).get("levy_income", 0) or 0
    # Approximate current annual sinking collection from levy data
    current_sinking_annual = sinking_levy  # full-year income from sinking levy
    new_sinking_annual = round(current_sinking_annual * multiplier, 2)
    recommended = _SINKING_ANNUAL_COLLECTION_MODEL
    gap_current = round(recommended - current_sinking_annual, 2)
    gap_new = round(recommended - new_sinking_annual, 2)
    on_track_after_increase = new_sinking_annual >= recommended

    sinking_reserve_impact = {
        "recommended_annual_collection": recommended,
        "current_sinking_annual": round(current_sinking_annual, 2),
        "new_sinking_annual": new_sinking_annual,
        "annual_gap_current": gap_current,
        "annual_gap_new": gap_new,
        "on_track_after_increase": on_track_after_increase,
        "required_pct_increase": round(
            ((recommended / current_sinking_annual) - 1) * 100, 1
        ) if current_sinking_annual > 0 else None,
        "note": (
            "On track with this levy increase"
            if on_track_after_increase else
            f"Still ${abs(gap_new):,.0f}/yr short of the inflation-adjusted model target"
        ),
    }

    return {
        "year": year,
        "increase_pct": increase_pct,
        "current_total_collection": round(total_current, 2),
        "new_total_collection": round(total_new, 2),
        "current_total_collection_payable": round(sum(u["current_annual_payable"] for u in all_units), 2),
        "new_total_collection_payable": round(sum(u["new_annual_payable"] for u in all_units), 2),
        "surplus_deficit": surplus_deficit,
        "per_unit_samples": samples,
        "all_units": all_units,
        "sinking_fund_reserve_impact": sinking_reserve_impact,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Data-driven fund projection readers (NO static % growth)
# ─────────────────────────────────────────────────────────────────────────────

async def get_admin_fund_projections(building_id: str, base_year: int, extra_years: int = 2) -> List[dict]:
    """
    Return admin fund annual budget projections for base_year through
    base_year + 3 + extra_years.

    Algorithm:
      • base_year: use proposed_admin_expenses from annual_levies
      • Y+1 / Y+2 / Y+3: aggregate projection_year_1/2/3 from financial_forecasts
        (per-category CPI/linear regression, stored by forecast_service.generate_category_forecast)
      • Y+4 and beyond: apply CPI 3.5% compounded from Y+3
      • Falls back to CPI from base if financial_forecasts has no data

    The admin fund "balance" field = projected annual levy/budget for that year.
    """
    base_year_str = str(base_year)

    # Base: proposed annual admin budget
    levy_doc = await db.annual_levies.find_one(
        {"building_id": building_id, "year": base_year_str}, {"_id": 0}
    )
    admin_base, _ = get_levy_proposed_amounts(levy_doc) if levy_doc else (0.0, 0.0)
    if admin_base == 0.0:
        # base_year doc missing or has no proposed amounts — walk back to find the most recent levy year
        fallback_doc = await db.annual_levies.find_one(
            {"building_id": building_id, "year": {"$lt": base_year_str}},
            {"_id": 0},
            sort=[("year", -1)]
        )
        if fallback_doc:
            admin_base, _ = get_levy_proposed_amounts(fallback_doc)
            logger.warning(
                f"No admin levy data for {building_id} year={base_year_str}; "
                f"using fallback year={fallback_doc.get('year')} (admin_base={admin_base})"
            )
        else:
            logger.warning(f"No annual_levies data at all for building {building_id}; admin_base=0.0")
            admin_base = 0.0

    # Read per-category forecasts for Y1/Y2/Y3 using aggregation to avoid
    # truncation when the result set exceeds a fixed to_list limit (can be 230+).
    forecast_totals = await db.financial_forecasts.aggregate(
        [
            {
                "$match": {
                    "building_id": building_id,
                    "financial_year": base_year_str,
                    "fund_type": {"$in": ["administrative", "admin"]},
                    "forecast_type": {"$exists": False},  # exclude levy_projection docs
                }
            },
            {
                "$group": {
                    "_id": None,
                    "y1": {"$sum": {"$ifNull": ["$projection_year_1", 0]}},
                    "y2": {"$sum": {"$ifNull": ["$projection_year_2", 0]}},
                    "y3": {"$sum": {"$ifNull": ["$projection_year_3", 0]}},
                }
            },
        ]
    ).to_list(1)

    totals = forecast_totals[0] if forecast_totals else {}
    y1 = round(totals.get("y1", 0), 2)
    y2 = round(totals.get("y2", 0), 2)
    y3 = round(totals.get("y3", 0), 2)

    y1_is_fallback = y1 == 0
    y2_is_fallback = y2 == 0
    y3_is_fallback = y3 == 0

    # Fallback to CPI if no category forecast data exists
    if y1_is_fallback:
        y1 = round(admin_base * (1 + _CPI), 2)
    if y2_is_fallback:
        y2 = round(admin_base * ((1 + _CPI) ** 2), 2)
    if y3_is_fallback:
        y3 = round(admin_base * ((1 + _CPI) ** 3), 2)

    result = [
        {"year": base_year_str, "balance": admin_base, "type": "actual_budget", "source": "proposed_budget"},
        {
            "year": str(base_year + 1),
            "balance": y1,
            "type": "estimate" if y1_is_fallback else "forecast",
            "source": "cpi_fallback" if y1_is_fallback else "category_forecasts",
        },
        {
            "year": str(base_year + 2),
            "balance": y2,
            "type": "estimate" if y2_is_fallback else "forecast",
            "source": "cpi_fallback" if y2_is_fallback else "category_forecasts",
        },
        {
            "year": str(base_year + 3),
            "balance": y3,
            "type": "estimate" if y3_is_fallback else "forecast",
            "source": "cpi_fallback" if y3_is_fallback else "category_forecasts",
        },
    ]

    for i in range(1, extra_years + 1):
        result.append({
            "year": str(base_year + 3 + i),
            "balance": round(y3 * ((1 + _CPI) ** i), 2),
            "type": "estimate",
            "source": f"cpi_{int(_CPI * 100)}pct_from_y3"
        })

    return result


async def get_sinking_fund_projections(building_id: str, max_years: int = 15) -> List[dict]:
    """
    Return sinking fund closing balance projections from the 15-year capital plan
    stored in `sinking_fund_plan`.

    The sinking fund "balance" field = closing fund balance for that year —
    this shows how healthy the reserve is, including capital event drawdowns.

    Returns an empty list when no `sinking_fund_plan` rows exist for the building.
    Callers are responsible for implementing any desired CPI fallback.
    """
    rows = await db.sinking_fund_plan.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("year", 1).to_list(max_years)

    if not rows:
        return []

    result = []
    for row in rows:
        # capital events are stored as a keyed dict in "categories" + human labels in "category_labels"
        # (not as a "line_items" array — that field does not exist in sinking_fund_plan documents)
        categories = row.get("categories", {}) or {}
        category_labels = row.get("category_labels", {}) or {}
        capital_events = [
            {"item": category_labels.get(k, k), "cost": round(float(v), 2)}
            for k, v in categories.items()
            if float(v or 0) > 0
        ]
        result.append({
            "year": str(row["year"]),
            "balance": round(row.get("closing_balance", 0), 2),
            "opening_balance": round(row.get("opening_balance", 0), 2),
            "contribution": round(row.get("contribution", 0), 2),  # singular — matches seed field name
            "expenditure": round(row.get("expenditure", 0), 2),
            "capital_events": capital_events,
            "is_actual": row.get("is_actual", False),
            "type": "actual" if row.get("is_actual", False) else "capital_plan",
            "source": "sinking_fund_plan",
        })

    return result


# @featuretrace:finance-reserve-forecast — Reserve projection from the per-asset capital schedule.
# Layer: service
# Data flow: GET /analytics/sinking-fund-forecast -> get_capital_replacement_projection()
#            -> capital_replacement_schedule + annual_levies.sinking_fund (building-scoped)
#            -> normaliseReserveProjection() -> ReserveRunwayChart.
# Related: backend/routers/analytics.py::get_sinking_fund_forecast
#          frontend/src/lib/reserve-projection.ts
#          tests/backend/test_sinking_fund_capital_projection.py
#
# LESSON (2026-08-27): `sinking_fund_plan` is the DOCUMENTED source, but East Gate has never
# had a row in it, so the endpoint fell through to a flat CPI line emitting contributions
# 0.0 / expenses 0.0 for every year -- while 18 real assets sat in
# capital_replacement_schedule, already treated as authoritative by capital_shock_service and
# levy_fairness_service. Refusing to fabricate a figure is right; not looking for the figure
# that exists is not. A year with no asset due is a REAL 0.0; an absent approved levy is
# None. Those must not both render as "$0.00".
async def get_capital_replacement_projection(
    building_id: str,
    start_year: int,
    years: int,
    opening_balance: float,
) -> Optional[dict]:
    """
    Build a sinking fund projection from the per-asset `capital_replacement_schedule`.

    This is the fallback source used when a building has no 15-year `sinking_fund_plan`
    (the documented primary source, see `get_sinking_fund_projections`). East Gate is
    exactly this case: it has never had a `sinking_fund_plan` document, but it does hold
    a real per-asset replacement schedule (asset_name / replacement_year / estimated_cost).

    Before this existed, that building fell through to a flat CPI projection that emitted
    `contributions: 0.0` and `expenses: 0.0` for every year — honest in intent (the code
    deliberately refused to fabricate figures) but indistinguishable in the UI from a
    genuine zero, and it discarded a real capital schedule that was sitting in Mongo.

    Both sides of the projection are sourced, never invented:

      expenses      per-asset `estimated_cost` summed by `replacement_year`. A year with no
                    asset due is a REAL zero (the schedule exists and lists nothing), not a
                    missing value — so it is emitted as 0.0, not None.
      contributions the latest approved sinking fund levy
                    (`annual_levies.sinking_fund.proposed_amount_cents`), held FLAT across
                    the horizon. Holding it flat is an explicit modelling choice, not a
                    forecast: no CPI escalation is applied because a future levy is set by
                    resolution at each AGM, not by indexation. When no approved levy exists
                    the contribution is genuinely unknown and is emitted as None — the
                    caller must render that as "—", never as $0.00.

    Returns None when the building has no capital schedule at all, so the caller can fall
    through to its own last-resort branch. Never raises for missing data.
    """
    rows = await db.capital_replacement_schedule.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(500)
    if not rows:
        return None

    # Sum real asset costs into the year each asset is actually due.
    by_year: Dict[int, List[dict]] = {}
    for row in rows:
        try:
            ry = int(row.get("replacement_year"))
        except (TypeError, ValueError):
            # An asset with no usable year cannot be placed on the timeline. Skipping it
            # would silently understate the drawdown, so surface it rather than drop it.
            logger.warning(
                "capital_replacement_schedule: unusable replacement_year %r for asset %r "
                "(building %s) — excluded from projection",
                row.get("replacement_year"), row.get("asset_id"), building_id,
            )
            continue
        by_year.setdefault(ry, []).append(row)

    # Contribution basis: the latest APPROVED sinking fund levy, not a derived/indexed figure.
    contribution: Optional[float] = None
    contribution_year: Optional[int] = None
    latest_levy = await db.annual_levies.find_one(
        {"building_id": building_id}, sort=[("year", -1)]
    )
    if latest_levy:
        sf = latest_levy.get("sinking_fund") or {}
        proposed_cents = sf.get("proposed_amount_cents")
        if proposed_cents is not None:
            # proposed_amount_cents is genuine integer cents (unlike the sibling
            # closing_balance/levy_income fields on this same document, which are dollar
            # floats — see CLAUDE.md's ledger-precision rule). Convert once, here.
            contribution = round(float(proposed_cents) / 100.0, 2)
            try:
                contribution_year = int(str(latest_levy.get("year")).split("-")[0])
            except (TypeError, ValueError):
                contribution_year = None

    projection = []
    balance = float(opening_balance)
    for i in range(1, years + 1):
        year = start_year + i
        assets = by_year.get(year, [])
        expenses = round(sum(float(a.get("estimated_cost") or 0.0) for a in assets), 2)
        # An unknown contribution must not silently become 0 in the running balance.
        # Track the balance on what is actually known and let the flag carry the caveat.
        contrib_applied = contribution if contribution is not None else 0.0
        closing = round(balance + contrib_applied - expenses, 2)
        projection.append({
            "year": year,
            "opening_balance": round(balance, 2),
            "contributions": contribution,
            "expenses": expenses,
            "closing_balance": closing,
            "events": [
                {
                    "item": a.get("asset_name") or a.get("asset_id") or "Unnamed asset",
                    "cost": round(float(a.get("estimated_cost") or 0.0), 2),
                }
                for a in sorted(assets, key=lambda x: -float(x.get("estimated_cost") or 0.0))
            ],
        })
        balance = closing

    return {
        "projection": projection,
        "assets_total": len(rows),
        "assets_scheduled": sum(len(v) for v in by_year.values()),
        "contribution": contribution,
        "contribution_year": contribution_year,
    }
