"""
Special Levy Prediction Engine
"""
import uuid
from datetime import datetime, timezone, date

import numpy as np
from typing import Dict, Any, List, Optional

from database import db
from services.capital_shock_service import _get_financial_baseline
from services.facility_allocation_engine import calculate_facility_allocation, get_unit_attributes
from services.monte_carlo_engine import run_special_levy_simulation
from services.settings_service import get_general_settings_or_default


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


async def get_projection_horizon_years(building_id: str) -> int:
    """Generated function header.

    Function: get_projection_horizon_years
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    settings = await get_general_settings_or_default(building_id, {"_id": 0})
    try:
        return int(settings.get("projection_horizon_years", 10))
    except Exception:
        return 10


async def _load_units(building_id: str) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: _load_units
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    units = await db.units.find({"building_id": building_id}, {"_id": 0}).to_list(1000)
    return units


def _default_share_map(units: List[Dict[str, Any]]) -> Dict[str, float]:
    """Generated function header.

    Function: _default_share_map
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    total_ue = sum(float(u.get("entitlement", 0) or 0) for u in units)
    if total_ue > 0:
        return {
            u["unit_number"]: float(u.get("entitlement", 0) or 0) / total_ue
            for u in units
        }
    count = len(units) or 1
    return {u["unit_number"]: 1.0 / count for u in units}


def _apply_schedule_scenario(
        schedule: List[Dict[str, Any]],
        defer_years: int = 0,
) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: _apply_schedule_scenario
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if defer_years <= 0:
        return schedule
    adjusted = []
    for item in schedule:
        year = item.get("replacement_year")
        if year:
            adjusted.append({**item, "replacement_year": year + defer_years})
        else:
            adjusted.append(item)
    return adjusted


async def _build_schedule(
        building_id: str,
        horizon_years: int,
        defer_years: int = 0,
) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: _build_schedule
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    current_year = date.today().year
    schedule = await db.capital_replacement_schedule.find(
        {
            "building_id": building_id,
            "replacement_year": {"$lte": current_year + horizon_years},
        },
        {"_id": 0},
    ).to_list(500)

    if not schedule:
        plan_rows = await db.sinking_fund_plan.find(
            {"building_id": building_id},
            {"_id": 0, "year": 1, "line_items": 1, "expenditure": 1},
        ).to_list(100)
        for row in plan_rows:
            year = row.get("year")
            if not year or year > current_year + horizon_years:
                continue
            items = row.get("line_items") or []
            if items:
                for item in items:
                    cost = float(item.get("cost", 0) or 0)
                    if cost <= 0:
                        continue
                    schedule.append({
                        "building_id": building_id,
                        "asset_name": item.get("name", "Capital Item"),
                        "estimated_cost": cost,
                        "replacement_year": year,
                    })
            else:
                cost = float(row.get("expenditure", 0) or 0)
                if cost > 0:
                    schedule.append({
                        "building_id": building_id,
                        "asset_name": "Capital Works",
                        "estimated_cost": cost,
                        "replacement_year": year,
                    })

    schedule = _apply_schedule_scenario(schedule, defer_years)
    schedule.sort(key=lambda x: x.get("replacement_year", 9999))
    return schedule


async def get_capital_allocation_context(
        building_id: str,
        horizon_years: int,
        defer_years: int = 0,
) -> Dict[str, Any]:
    """Generated function header.

    Function: get_capital_allocation_context
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    units = await _load_units(building_id)
    if not units:
        return {}

    unit_attributes = await get_unit_attributes(building_id)
    benefit_groups_list = await db.benefit_groups.find({"building_id": building_id}, {"_id": 0}).to_list(500)
    benefit_groups = {bg["id"]: bg for bg in benefit_groups_list}
    assets = await db.building_assets.find({"building_id": building_id}, {"_id": 0}).to_list(2000)
    facilities = await db.facilities.find({"building_id": building_id}, {"_id": 0}).to_list(500)

    assets_by_id = {a.get("id"): a for a in assets if a.get("id")}
    facilities_by_id = {f.get("id"): f for f in facilities if f.get("id")}

    schedule = await _build_schedule(building_id, horizon_years, defer_years=defer_years)
    schedule_by_year: Dict[int, List[Dict[str, Any]]] = {}
    for item in schedule:
        year = item.get("replacement_year")
        if not year:
            continue
        schedule_by_year.setdefault(int(year), []).append(item)

    default_shares = _default_share_map(units)
    allocation_shares_by_year: Dict[int, Dict[str, float]] = {}
    capital_schedule_by_year: Dict[int, float] = {}

    base_year = date.today().year
    for year in range(base_year + 1, base_year + horizon_years + 1):
        items = schedule_by_year.get(year, [])
        total_cost = sum(float(item.get("estimated_cost", 0) or 0) for item in items)
        capital_schedule_by_year[year] = total_cost

        if not items or total_cost <= 0:
            allocation_shares_by_year[year] = default_shares
            continue

        allocation_totals: Dict[str, float] = {u["unit_number"]: 0.0 for u in units}
        for item in items:
            cost = float(item.get("estimated_cost", 0) or 0)
            if cost <= 0:
                continue
            asset = assets_by_id.get(item.get("asset_id"))
            facility = facilities_by_id.get(item.get("facility_id"))
            bg_id = (
                    item.get("benefit_group_id")
                    or (asset or {}).get("benefit_group_id")
                    or (facility or {}).get("benefit_group_id")
            )
            facility_stub = {
                "annual_cost": cost,
                "benefit_group_id": bg_id,
                "allocation_driver": None,
            }
            allocation = await calculate_facility_allocation(
                facility_stub, units, benefit_groups, unit_attributes
            )
            if not allocation:
                allocation = {un: cost * default_shares.get(un, 0) for un in default_shares}
            for un, amount in allocation.items():
                allocation_totals[un] = allocation_totals.get(un, 0.0) + amount

        allocation_shares_by_year[year] = {
            un: (allocation_totals.get(un, 0.0) / total_cost) if total_cost > 0 else default_shares.get(un, 0.0)
            for un in allocation_totals
        }

    group_units: Dict[str, List[str]] = {"All Lots": [u["unit_number"] for u in units]}
    for bg in benefit_groups_list:
        fac = {
            "annual_cost": 1.0,
            "benefit_group_id": bg.get("id"),
            "allocation_driver": bg.get("allocation_driver", "unit_entitlement"),
        }
        allocation = await calculate_facility_allocation(fac, units, benefit_groups, unit_attributes)
        unit_numbers = [u for u, val in allocation.items() if val > 0]
        if unit_numbers:
            group_units[bg.get("name", bg.get("id", "Group"))] = unit_numbers

    return {
        "units": units,
        "schedule": schedule,
        "schedule_by_year": schedule_by_year,
        "capital_schedule_by_year": capital_schedule_by_year,
        "allocation_shares_by_year": allocation_shares_by_year,
        "group_units": group_units,
        "default_shares": default_shares,
    }


def _percentile(values: List[float], percentile: float) -> float:
    """Generated function header.

    Function: _percentile
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not values:
        return 0.0
    return float(np.percentile(values, percentile))


async def compute_special_levy_forecast(
        building_id: str,
        force: bool = False,
        scenario: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generated function header.

    Function: compute_special_levy_forecast
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    horizon_years = (scenario or {}).get("horizon_years") or await get_projection_horizon_years(building_id)
    if not force and not scenario:
        cached = await db.special_levy_forecasts.find_one(
            {"building_id": building_id, "horizon_years": horizon_years},
            {"_id": 0},
        )
        if cached:
            return cached

    defer_years = int((scenario or {}).get("defer_capital_years", 0) or 0)
    context = await get_capital_allocation_context(building_id, horizon_years, defer_years=defer_years)
    units = context.get("units") or []
    if not units:
        return {"error": "No units found"}

    baseline = await _get_financial_baseline(building_id)
    annual_budget = float(baseline.get("total_levy_income", 0) or 0)
    current_reserve = float(baseline.get("reserve_balance", 0) or 0)
    capital_required = sum(context.get("capital_schedule_by_year", {}).values())
    capital_shock = capital_required - current_reserve

    increase_per_unit = float((scenario or {}).get("sinking_fund_increase_per_unit", 0) or 0)
    if increase_per_unit > 0:
        annual_budget += increase_per_unit * len(units)

    loan = None
    if scenario:
        loan_amount = float(scenario.get("loan_amount", 0) or 0)
        if loan_amount > 0:
            loan = {
                "amount": loan_amount,
                "interest_rate": float(scenario.get("loan_interest_rate", 0) or 0),
                "term_years": int(scenario.get("loan_term_years", 0) or 0),
            }

    sim = run_special_levy_simulation(
        annual_budget=annual_budget,
        current_reserve=current_reserve,
        capital_schedule_by_year=context.get("capital_schedule_by_year", {}),
        allocation_shares_by_year=context.get("allocation_shares_by_year", {}),
        group_units=context.get("group_units", {}),
        num_simulations=1000,
        years=horizon_years,
        base_year=date.today().year,
        loan=loan,
    )

    per_unit_amounts = sim["per_unit_amounts"]
    total_shock_amounts = sim["total_shock_amounts"]
    shock_count = sum(1 for v in per_unit_amounts if v > 0)
    probability = round((shock_count / 1000) * 100, 1)

    shock_year_distribution = {
        str(year): round((count / 1000) * 100, 1)
        for year, count in sim["shock_year_counts"].items()
    }

    thresholds = {
        "over_5000": round((sum(1 for v in per_unit_amounts if v > 5000) / 1000) * 100, 1),
        "over_10000": round((sum(1 for v in per_unit_amounts if v > 10000) / 1000) * 100, 1),
    }

    group_summary = []
    for group, values in sim["group_amounts"].items():
        group_summary.append({
            "group": group,
            "unit_count": len(context.get("group_units", {}).get(group, [])),
            "probability": round((sum(1 for v in values if v > 0) / 1000) * 100, 1),
            "median_amount": _percentile(values, 50),
            "p90_amount": _percentile(values, 90),
            "p95_amount": _percentile(values, 95),
            "worst_case": max(values) if values else 0.0,
        })

    explanation = (
        f"Based on current budgets and capital forecasts, there is a {probability}% chance of "
        f"a special levy within the next {horizon_years} years. The most likely levy is "
        f"approximately ${_percentile(per_unit_amounts, 50):,.0f} per unit."
    )

    result = {
        "forecast_id": str(uuid.uuid4()),
        "building_id": building_id,
        "year": date.today().year,
        "probability": probability,
        "median_amount": _percentile(per_unit_amounts, 50),
        "p90_amount": _percentile(per_unit_amounts, 90),
        "p95_amount": _percentile(per_unit_amounts, 95),
        "worst_case": max(per_unit_amounts) if per_unit_amounts else 0.0,
        "simulation_runs": 1000,
        "generated_at": _now(),
        "horizon_years": horizon_years,
        "capital_requirement": round(capital_required, 2),
        "reserve_balance": round(current_reserve, 2),
        "capital_shock": round(capital_shock, 2),
        "special_levy_required": capital_shock > 0,
        "shock_year_distribution": shock_year_distribution,
        "reserve_fan": sim["reserve_fan"],
        "per_unit_distribution": per_unit_amounts,
        "thresholds": thresholds,
        "group_summary": group_summary,
        "median_total_shock": _percentile(total_shock_amounts, 50),
        "worst_case_total_shock": max(total_shock_amounts) if total_shock_amounts else 0.0,
        "explanation": explanation,
        "scenario": scenario or None,
    }

    if not scenario:
        await db.special_levy_forecasts.update_one(
            {"building_id": building_id, "horizon_years": horizon_years},
            {"$set": result},
            upsert=True,
        )

    return result


async def recompute_special_levy_forecast(
        building_id: str,
        force: bool = True,
) -> Dict[str, Any]:
    """Generated function header.

    Function: recompute_special_levy_forecast
    Path: backend/services/special_levy_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await compute_special_levy_forecast(building_id=building_id, force=force)
