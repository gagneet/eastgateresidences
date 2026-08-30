"""
Levy Stability Score (LSS) Engine
"""
import uuid
from datetime import datetime, timezone, date

import numpy as np
from typing import Dict, Any, Optional

from database import db
from services.capital_shock_service import _get_financial_baseline
from services.maintenance_intelligence_service import get_intelligence_settings
from services.special_levy_service import compute_special_levy_forecast, get_capital_allocation_context, \
    get_projection_horizon_years


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/levy_stability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _reserve_adequacy_score(ratio: float) -> float:
    """Generated function header.

    Function: _reserve_adequacy_score
    Path: backend/services/levy_stability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if ratio >= 1.5:
        return 100
    if ratio >= 1.2:
        return 90
    if ratio >= 1.0:
        return 75
    if ratio >= 0.8:
        return 60
    return 40


def _volatility_score(volatility: float) -> float:
    """Generated function header.

    Function: _volatility_score
    Path: backend/services/levy_stability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if volatility < 0.05:
        return 100
    if volatility < 0.10:
        return 85
    if volatility < 0.20:
        return 70
    if volatility < 0.30:
        return 50
    return 30


def _shock_score(probability: float) -> float:
    """Generated function header.

    Function: _shock_score
    Path: backend/services/levy_stability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if probability < 5:
        return 100
    if probability < 10:
        return 90
    if probability < 20:
        return 75
    if probability < 35:
        return 55
    return 35


def _funding_score(ratio: float) -> float:
    """Generated function header.

    Function: _funding_score
    Path: backend/services/levy_stability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if ratio >= 1.2:
        return 100
    if ratio >= 1.0:
        return 85
    if ratio >= 0.9:
        return 70
    if ratio >= 0.75:
        return 50
    return 30


async def compute_levy_stability_snapshot(
        building_id: str,
        force: bool = False,
        scenario: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generated function header.

    Function: compute_levy_stability_snapshot
    Path: backend/services/levy_stability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    horizon_years = (scenario or {}).get("horizon_years") or await get_projection_horizon_years(building_id)
    if not force and not scenario:
        cached = await db.levy_stability_snapshots.find_one(
            {"building_id": building_id, "horizon_years": horizon_years},
            {"_id": 0},
        )
        if cached:
            return cached

    settings = await get_intelligence_settings(building_id)
    inflation_rate = float(settings.get("inflation_rate", 0.03))

    special_levy = await compute_special_levy_forecast(
        building_id=building_id, force=force, scenario=scenario
    )
    defer_years = int((scenario or {}).get("defer_capital_years", 0) or 0)
    context = await get_capital_allocation_context(building_id, horizon_years, defer_years=defer_years)

    baseline = await _get_financial_baseline(building_id)
    reserve_balance = float(baseline.get("reserve_balance", 0) or 0)
    sinking_income = float(baseline.get("sinking_levy_income", 0) or 0)
    increase_per_unit = float((scenario or {}).get("sinking_fund_increase_per_unit", 0) or 0)
    if increase_per_unit > 0 and context.get("units"):
        sinking_income += increase_per_unit * len(context["units"])

    loan_amount = float((scenario or {}).get("loan_amount", 0) or 0)
    loan_rate = float((scenario or {}).get("loan_interest_rate", 0) or 0)
    loan_term = int((scenario or {}).get("loan_term_years", 0) or 0)
    if loan_amount > 0 and loan_term > 0:
        reserve_balance += loan_amount

    capital_required = sum(context.get("capital_schedule_by_year", {}).values())
    adequacy_ratio = (reserve_balance / capital_required) if capital_required > 0 else 1.5
    reserve_score = _reserve_adequacy_score(adequacy_ratio)

    projected_contributions = 0.0
    for i in range(1, horizon_years + 1):
        projected_contributions += sinking_income * ((1 + inflation_rate) ** i)

    if loan_amount > 0 and loan_term > 0:
        if loan_rate > 0:
            r = loan_rate
            annual_payment = loan_amount * (r * (1 + r) ** loan_term) / ((1 + r) ** loan_term - 1)
        else:
            annual_payment = loan_amount / loan_term
        projected_contributions = max(
            0.0,
            projected_contributions - (annual_payment * min(horizon_years, loan_term)),
        )
    funding_ratio = (projected_contributions / capital_required) if capital_required > 0 else 1.2
    funding_score = _funding_score(funding_ratio)

    per_unit_distribution = special_levy.get("per_unit_distribution", [])
    volatility = 0.0
    if per_unit_distribution:
        mean_val = float(np.mean(per_unit_distribution))
        if mean_val > 0:
            volatility = float(np.std(per_unit_distribution) / mean_val)
    volatility_score = _volatility_score(volatility)

    shock_probability = float(special_levy.get("probability", 0) or 0)
    shock_score = _shock_score(shock_probability)

    levy_stability_score = round(
        (0.35 * reserve_score) +
        (0.30 * shock_score) +
        (0.20 * volatility_score) +
        (0.15 * funding_score),
        1
    )

    group_scores = []
    units = context.get("units") or []
    total_ue = sum(float(u.get("entitlement", 0) or 0) for u in units) or 1.0
    group_units = context.get("group_units", {})
    group_probabilities = {
        g["group"]: g.get("probability", 0)
        for g in special_levy.get("group_summary", [])
    }

    for group, members in group_units.items():
        group_ue = sum(
            float(u.get("entitlement", 0) or 0)
            for u in units
            if u.get("unit_number") in members
        )
        group_share = group_ue / total_ue if total_ue > 0 else 0
        group_reserve = reserve_balance * group_share
        group_contrib = projected_contributions * group_share

        group_capital_required = 0.0
        for year, total_cost in context.get("capital_schedule_by_year", {}).items():
            share_map = context.get("allocation_shares_by_year", {}).get(year, {})
            group_year_share = sum(share_map.get(u, 0) for u in members)
            group_capital_required += total_cost * group_year_share

        group_adequacy = (group_reserve / group_capital_required) if group_capital_required > 0 else 1.5
        group_funding_ratio = (group_contrib / group_capital_required) if group_capital_required > 0 else 1.2
        group_shock_score = _shock_score(float(group_probabilities.get(group, shock_probability)))
        group_reserve_score = _reserve_adequacy_score(group_adequacy)
        group_funding_score = _funding_score(group_funding_ratio)

        group_lss = round(
            (0.35 * group_reserve_score) +
            (0.30 * group_shock_score) +
            (0.20 * volatility_score) +
            (0.15 * group_funding_score),
            1
        )

        group_scores.append({
            "group": group,
            "levy_stability_score": group_lss,
            "reserve_score": group_reserve_score,
            "shock_score": group_shock_score,
            "volatility_score": volatility_score,
            "funding_score": group_funding_score,
        })

    explanation = (
        f"The building currently has a Levy Stability Score of {levy_stability_score}/100, "
        f"with a {shock_probability}% probability of a special levy within {horizon_years} years."
    )

    result = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "year": date.today().year,
        "reserve_score": reserve_score,
        "shock_score": shock_score,
        "volatility_score": volatility_score,
        "funding_score": funding_score,
        "levy_stability_score": levy_stability_score,
        "generated_at": _now(),
        "horizon_years": horizon_years,
        "components": {
            "reserve_adequacy_ratio": round(adequacy_ratio, 2),
            "funding_ratio": round(funding_ratio, 2),
            "volatility": round(volatility, 4),
            "shock_probability": shock_probability,
        },
        "group_scores": group_scores,
        "explanation": explanation,
        "scenario": scenario or None,
    }

    if not scenario:
        await db.levy_stability_snapshots.update_one(
            {"building_id": building_id, "horizon_years": horizon_years},
            {"$set": result},
            upsert=True,
        )

    return result


async def recompute_levy_stability_snapshot(
        building_id: str,
        force: bool = True,
) -> Dict[str, Any]:
    """Generated function header.

    Function: recompute_levy_stability_snapshot
    Path: backend/services/levy_stability_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await compute_levy_stability_snapshot(building_id=building_id, force=force)
