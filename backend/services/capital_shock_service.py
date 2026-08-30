"""
Capital Shock Detection Service — predicts reserve shortfalls for major replacements.
"""
from datetime import datetime, timezone, date
from statistics import median

import logging
from typing import Dict, Any, List, Optional, Tuple

from database import db
from services.gst_service import get_building_levy_gst_settings
from utils.finance_helpers import get_latest_levy_year

logger = logging.getLogger(__name__)

MAINTENANCE_RESERVE_SHARE = 0.2

# There is deliberately NO default reserve balance.
#
# This module used to define a constant here and substitute it whenever a building had
# no `annual_levies` document or a missing closing_balance. That is forbidden by the
# project rule "last-resort fallbacks must be 0.0 + logger.warning(), never a
# building-specific hardcoded amount", and it produced a visibly self-contradicting
# dashboard: East Gate (13195) has no financial data in either store, so
# /intelligence/capital-risk and /intelligence/building showed levy income $0,
# expenses $0 — and a reserve of exactly $150,000.00 labelled "Available for capital
# works", under a tooltip claiming it came from "the latest sinking fund transaction
# record".
#
# `reserve_balance` is now None when genuinely unknown. Callers must branch on
# `reserve_balance_available` rather than coercing None to a number: zero and unknown
# are different states and only one of them may be shown as a dollar figure.

CSI_TIERS: List[Tuple[float, str, str, str]] = [
    (0.5, "normal", "Normal maintenance", "normal_maintenance"),
    (2.0, "moderate", "Moderate spike", "moderate_spike"),
    (5.0, "major", "Major capital event", "major_capital_event"),
    (float("inf"), "severe", "Severe capital shock", "severe_capital_shock"),
]


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


async def _get_financial_baseline(building_id: str) -> Dict[str, float]:
    """Generated function header.

    Function: _get_financial_baseline
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    year = await get_latest_levy_year(building_id)
    if not year:
        # No levy year at all => no financial baseline exists for this building.
        logger.warning(
            "capital_shock: no levy year for building_id=%s — reserve balance is UNKNOWN, "
            "not zero. Returning reserve_balance=None; callers must not render a dollar figure.",
            building_id,
        )
        return {
            "year": None,
            "total_levy_income": 0.0,
            "sinking_levy_income": 0.0,
            "reserve_balance": None,
            "reserve_balance_available": False,
            "annual_expenses": 0.0,
        }
    levy = await db.annual_levies.find_one({"year": year, "building_id": building_id}, {"_id": 0}) or {}
    admin_income = levy.get("admin_fund", {}).get("levy_income", 0) or 0
    sinking_income = levy.get("sinking_fund", {}).get("levy_income", 0) or 0
    # GAP-FIN-065: annual_levies.*.levy_income is EX-GST (the get_levy_proposed_amounts
    # basis). The reserve projection compares annual_contribution against the inc-GST
    # capital-works schedule and the real (inc-GST) closing balance, and the
    # recommended_levy_increase_pct denominator is total_levy_income — so the CONTRIBUTION
    # figures must be the inc-GST payable amount owners actually pay in. Convert here, once,
    # matching forecast_service's `required_rate × gst_multiplier` pattern. Expenses/balance
    # are left as-is (their own basis is out of scope for this contribution fix).
    gst_multiplier = (await get_building_levy_gst_settings(building_id)).get("gst_multiplier", 1.0)
    admin_income = float(admin_income) * gst_multiplier
    sinking_income = float(sinking_income) * gst_multiplier
    # `.get(key, default)` only fires when the key is ABSENT; an explicitly stored None
    # must be caught separately. Both mean "unknown", not "zero".
    reserve_balance = levy.get("sinking_fund", {}).get("closing_balance")
    if reserve_balance is None:
        logger.warning(
            "capital_shock: building_id=%s year=%s has no sinking_fund.closing_balance — "
            "reserve balance is UNKNOWN. Returning reserve_balance=None.",
            building_id, year,
        )
    admin_expenses = levy.get("admin_fund", {}).get("total_expenses", 0) or 0
    sinking_expenses = levy.get("sinking_fund", {}).get("total_expenses", 0) or 0
    return {
        "year": year,
        "total_levy_income": float(admin_income + sinking_income),
        "sinking_levy_income": float(sinking_income),
        "reserve_balance": float(reserve_balance) if reserve_balance is not None else None,
        "reserve_balance_available": reserve_balance is not None,
        "annual_expenses": float(admin_expenses + sinking_expenses),
    }


def _risk_level(deficit_ratio: float) -> str:
    """Generated function header.

    Function: _risk_level
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if deficit_ratio >= 0.5:
        return "CRITICAL"
    if deficit_ratio >= 0.25:
        return "HIGH"
    if deficit_ratio >= 0.1:
        return "MEDIUM"
    return "LOW"


def _classify_csi(value: float) -> Dict[str, str]:
    """Generated function header.

    Function: _classify_csi
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for threshold, level, label, category in CSI_TIERS:
        if value < threshold:
            return {
                "risk_level": level,
                "label": label,
                "shock_category": category,
            }
    return {
        "risk_level": "severe",
        "label": "Severe capital shock",
        "shock_category": "severe_capital_shock",
    }


def _classify_collapse_risk(years_to_collapse: Optional[int]) -> Dict[str, str]:
    """Generated function header.

    Function: _classify_collapse_risk
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if years_to_collapse is None:
        return {"risk_level": "stable", "label": "15+ years"}
    if years_to_collapse < 5:
        return {"risk_level": "critical", "label": "<5 years"}
    if years_to_collapse < 10:
        return {"risk_level": "risk", "label": "5–10 years"}
    if years_to_collapse < 15:
        return {"risk_level": "watch", "label": "10–15 years"}
    return {"risk_level": "stable", "label": "15+ years"}


def _classify_sfar(value: Optional[float]) -> Dict[str, str]:
    """Generated function header.

    Function: _classify_sfar
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return {"status": "complete", "label": "Plan fully funded"}
    if value > 1.3:
        return {"status": "strong", "label": "Strong funding"}
    if value >= 1.0:
        return {"status": "adequate", "label": "Adequate"}
    if value >= 0.8:
        return {"status": "underfunded", "label": "Underfunded"}
    return {"status": "high_risk", "label": "High risk"}


def _median_baseline(values: List[float]) -> float:
    """Generated function header.

    Function: _median_baseline
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    clean = [v for v in values if v > 0]
    if not clean:
        return 0.0
    return float(median(clean))


def _build_plan_rows_from_schedule(
        year_map: Dict[int, List[Dict[str, Any]]],
        baseline: Dict[str, float],
        years: int = 10,
        inflation_rate: float = 0.03,
) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: _build_plan_rows_from_schedule
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    rows = []
    # For PROJECTION ARITHMETIC an unknown opening reserve is treated as 0.0 so the series
    # still computes; the response separately carries reserve_balance_available so the UI
    # can refuse to present the result as a real balance.
    reserve = float(baseline.get("reserve_balance") or 0.0)
    sinking_income = float(baseline.get("sinking_levy_income", 0.0))
    current_year = date.today().year
    for i in range(1, years + 1):
        year = current_year + i
        contribution = sinking_income * ((1 + inflation_rate) ** i)
        expenditure = sum(float(item.get("estimated_cost", 0) or 0) for item in year_map.get(year, []))
        opening = reserve
        closing = opening + contribution - expenditure
        rows.append({
            "year": year,
            "contribution": round(contribution, 2),
            "expenditure": round(expenditure, 2),
            "opening_balance": round(opening, 2),
            "closing_balance": round(closing, 2),
            "categories": {},
            "category_labels": {},
            "is_actual": False,
            "is_major_capital_year": expenditure >= (sinking_income * 1.5) if sinking_income > 0 else False,
        })
        reserve = closing
    return rows


def _year_row_description(row: Dict[str, Any], year_map: Optional[Dict[int, List[Dict[str, Any]]]]) -> Optional[str]:
    """Best-effort human-readable label for a shock-index row's capital spend.

    A single year can bundle several replacement items, so this is a joined list of
    asset/line-item names rather than a single project name. Falls back to None (the
    frontend already renders a generic "Capital works" label when description is absent)
    when neither source has named items — e.g. a sinking_fund_plan row that only has an
    aggregate expenditure figure.
    """
    line_items = row.get("line_items")
    if isinstance(line_items, list) and line_items:
        names = [item.get("name") for item in line_items if isinstance(item, dict) and item.get("name")]
        if names:
            return ", ".join(names)

    year = row.get("year")
    schedule_items = (year_map or {}).get(year) if year is not None else None
    if schedule_items:
        names = [item.get("asset_name") for item in schedule_items if isinstance(item, dict) and item.get("asset_name")]
        if names:
            return ", ".join(names)

    return None


def _compute_capital_shock_index(
        plan_rows: List[Dict[str, Any]],
        year_map: Optional[Dict[int, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Generated function header.

    Function: _compute_capital_shock_index
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    spend_values = [float(r.get("expenditure", 0) or 0) for r in plan_rows]
    baseline_spend = _median_baseline(spend_values)
    rows = []
    for row in plan_rows:
        spend = float(row.get("expenditure", 0) or 0)
        csi = ((spend - baseline_spend) / baseline_spend) if baseline_spend > 0 else 0.0
        tier = _classify_csi(csi)
        rows.append({
            "year": row.get("year"),
            "capital_spend": round(spend, 2),
            "baseline_spend": round(baseline_spend, 2),
            "capital_shock_index": round(csi, 2),
            "risk_level": tier["risk_level"],
            "shock_category": tier["shock_category"],
            # Aliases so frontend consumers (CapitalForMeCard, ReserveRunwayChart) that
            # expect the Postgres-path field names (description/estimated_cost/severity)
            # get real values on the Mongo path too, instead of silently rendering blank.
            "description": _year_row_description(row, year_map),
            "estimated_cost": round(spend, 2),
            "severity": tier["risk_level"],
        })

    current_year = date.today().year
    next_shock = next(
        (r for r in rows if r.get("year", 0) >= current_year and r["risk_level"] in {"major", "severe"}),
        None,
    )
    return {
        "baseline_spend": round(baseline_spend, 2),
        "rows": rows,
        "next_shock": next_shock,
        "next_shock_year": next_shock.get("year") if next_shock else None,
        "tiers": [
            {"max": None if t[0] == float("inf") else t[0], "risk_level": t[1], "label": t[2], "shock_category": t[3]}
            for t in CSI_TIERS
        ],
    }


def _select_projection_rows(plan_rows: List[Dict[str, Any]], start_year: int) -> List[Dict[str, Any]]:
    """Generated function header.

    Function: _select_projection_rows
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    years = sorted({int(r.get("year")) for r in plan_rows if r.get("year") is not None})
    if not years:
        return []
    if start_year in years:
        start = start_year + 1
    else:
        start = min((y for y in years if y >= start_year), default=years[0])
    return [r for r in plan_rows if int(r.get("year")) >= start]


def _compute_reserve_projection(
        plan_rows: List[Dict[str, Any]],
        start_year: int,
        initial_reserve: float,
) -> Dict[str, Any]:
    """Generated function header.

    Function: _compute_reserve_projection
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    projection_rows = _select_projection_rows(plan_rows, start_year)
    balance = float(initial_reserve)
    timeline = []
    collapse_year = None
    minimum_balance = balance
    minimum_year = None

    for row in projection_rows:
        year = int(row.get("year"))
        contribution = float(row.get("contribution", 0) or 0)
        expenditure = float(row.get("expenditure", 0) or 0)
        opening = balance
        closing = opening + contribution - expenditure
        timeline.append({
            "year": year,
            "opening_balance": round(opening, 2),
            "contribution": round(contribution, 2),
            "expenditure": round(expenditure, 2),
            "closing_balance": round(closing, 2),
            "is_actual": row.get("is_actual", False),
            "is_major_capital_year": row.get("is_major_capital_year", False),
        })
        balance = closing
        if closing < minimum_balance:
            minimum_balance = closing
            minimum_year = year
        if collapse_year is None and closing < 0:
            collapse_year = year

    years_to_collapse = (collapse_year - start_year) if collapse_year is not None else None
    risk = _classify_collapse_risk(years_to_collapse)
    return {
        "start_year": start_year,
        "initial_reserve": round(initial_reserve, 2),
        "collapse_year": collapse_year,
        "years_to_collapse": years_to_collapse,
        "risk_level": risk["risk_level"],
        "risk_label": risk["label"],
        "minimum_balance": round(minimum_balance, 2),
        "minimum_balance_year": minimum_year,
        "timeline": timeline,
    }


def _compute_sfar(
        plan_rows: List[Dict[str, Any]],
        reserve_timeline: List[Dict[str, Any]],
        start_year: int,
        current_reserve: float,
) -> Dict[str, Any]:
    """Generated function header.

    Function: _compute_sfar
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    future_rows = _select_projection_rows(plan_rows, start_year)
    future_contributions = sum(float(r.get("contribution", 0) or 0) for r in future_rows)
    future_capital = sum(float(r.get("expenditure", 0) or 0) for r in future_rows)
    available = float(current_reserve) + future_contributions
    sfar = (available / future_capital) if future_capital > 0 else None
    status = _classify_sfar(sfar)

    remaining_map: Dict[int, float] = {}
    running_remaining = 0.0
    for row in sorted(future_rows, key=lambda r: r.get("year", 0), reverse=True):
        year = int(row.get("year"))
        remaining_map[year] = running_remaining
        running_remaining += float(row.get("expenditure", 0) or 0)

    timeline = []
    for row in reserve_timeline:
        year = row["year"]
        remaining = remaining_map.get(year, 0.0)
        sfar_year = (row["closing_balance"] / remaining) if remaining > 0 else None
        status_year = _classify_sfar(sfar_year)
        timeline.append({
            "year": year,
            "reserve_balance": row["closing_balance"],
            "remaining_capital": round(remaining, 2),
            "sfar": round(sfar_year, 2) if sfar_year is not None else None,
            "status": status_year["status"],
            "status_label": status_year["label"],
        })

    return {
        "sfar": round(sfar, 2) if sfar is not None else None,
        "status": status["status"],
        "status_label": status["label"],
        "current_reserve": round(current_reserve, 2),
        "future_contributions": round(future_contributions, 2),
        "capital_required": round(future_capital, 2),
        "available_funding": round(available, 2),
        "funding_gap": round((future_capital - available), 2) if future_capital > 0 else 0.0,
        "timeline": timeline,
    }


async def _get_trust_signals(building_id: str) -> Dict[str, Any]:
    """
    Pull trust accounting signals to enrich capital shock analysis.

    Returns trust cash balances, arrears, unreconciled items, and payment plan stress.
    These signals feed the Capital Shock engine for trust-aware risk scoring.
    """
    signals: Dict[str, Any] = {
        "trust_cash_admin_cents": 0,
        "trust_cash_sinking_cents": 0,
        "trust_cash_special_levy_cents": 0,
        "total_unreconciled_cents": 0,
        "unreconciled_count": 0,
        "arrears_total_cents": 0,
        "arrears_90d_cents": 0,
        "overdue_levy_count": 0,
        "payment_plan_outstanding_cents": 0,
        "last_reconciled_at": None,
        "reserve_liquidity_coverage_ratio": None,
        "contribution_realization_rate": None,
        "arrears_concentration_index": None,
        "collection_status": "ok",
    }
    try:
        # Trust account balances by fund type (Phase 1 accounts)
        async for acct in db.trust_accounts.find(
                {"building_id": building_id, "is_active": True},
                {"account_type": 1, "current_balance_cents": 1, "last_reconciled_at": 1}
        ):
            acct_type = acct.get("account_type", "")
            bal = int(acct.get("current_balance_cents", 0) or 0)
            if "admin" in acct_type:
                signals["trust_cash_admin_cents"] += bal
            elif "sinking" in acct_type or "capital" in acct_type:
                signals["trust_cash_sinking_cents"] += bal
            elif "special" in acct_type:
                signals["trust_cash_special_levy_cents"] += bal
            if acct.get("last_reconciled_at"):
                signals["last_reconciled_at"] = acct["last_reconciled_at"]

        # Unreconciled bank statement lines
        unreconciled_pipeline = [
            {"$match": {"building_id": building_id, "matched_status": "unmatched"}},
            {"$group": {"_id": None, "total": {"$sum": {"$abs": "$amount_cents"}}, "count": {"$sum": 1}}}
        ]
        unrec = await db.bank_statement_lines.aggregate(unreconciled_pipeline).to_list(1)
        if unrec:
            signals["total_unreconciled_cents"] = unrec[0].get("total", 0)
            signals["unreconciled_count"] = unrec[0].get("count", 0)

        # Arrears from levy schedules
        arrears_pipeline = [
            {"$match": {"building_id": building_id, "status": {"$in": ["overdue", "partial"]}}},
            {"$group": {"_id": None, "total": {"$sum": "$outstanding_cents"}, "count": {"$sum": 1}}}
        ]
        arrears = await db.trust_levy_schedules.aggregate(arrears_pipeline).to_list(1)
        if arrears:
            signals["arrears_total_cents"] = arrears[0].get("total", 0)
            signals["overdue_levy_count"] = arrears[0].get("count", 0)

        # Reserve liquidity coverage ratio: trust cash / 3-month expenses.
        # GAP-FIN-065 Item C: both sides are inc-GST actual dollars — `trust_cash_*` is
        # real bank cash and `annual_levies.*.total_expenses` is the GST-INCLUSIVE gross
        # amount paid to suppliers (portal-scraped/imported actuals, never net of input-tax
        # credits — confirmed against run_scraper.py's `admin_actual` write path). So no GST
        # adjustment is needed here, and this is the anchor confirming the expense basis is
        # inc-GST throughout capital_shock_service.
        trust_total = (signals["trust_cash_admin_cents"] + signals["trust_cash_sinking_cents"])
        if trust_total > 0:
            # Get annual expenses for RLCR calculation
            year = await get_latest_levy_year(building_id)
            if year:
                levy = await db.annual_levies.find_one({"year": year, "building_id": building_id})
                if levy:
                    annual_exp = float(levy.get("admin_fund", {}).get("total_expenses", 0) or 0)
                    three_month_expenses = (annual_exp / 4) * 100  # convert to cents
                    if three_month_expenses > 0:
                        signals["reserve_liquidity_coverage_ratio"] = round(
                            trust_total / three_month_expenses, 3
                        )

        # Arrears concentration index: % of total levy income that is in arrears.
        # GAP-FIN-065: the numerator (`arrears_total_cents`, from trust_levy_schedules
        # outstanding_cents) is inc-GST owner-payable dollars, so the denominator must be
        # the inc-GST payable levy income too. `annual_levies.*.levy_income` is EX-GST, so
        # convert with gst_multiplier (× 1.0 no-op for unregistered buildings) — the same
        # fix applied to building_stress_service's arrears ratio. Comparing inc-GST arrears
        # against an ex-GST denominator overstates the index by the GST factor.
        year = await get_latest_levy_year(building_id)
        if year:
            levy_doc = await db.annual_levies.find_one({"year": year, "building_id": building_id})
            if levy_doc:
                gst_multiplier = (await get_building_levy_gst_settings(building_id)).get("gst_multiplier", 1.0)
                total_income = (
                                       float(levy_doc.get("admin_fund", {}).get("levy_income", 0) or 0) +
                                       float(levy_doc.get("sinking_fund", {}).get("levy_income", 0) or 0)
                               ) * gst_multiplier * 100  # ex-GST dollars → inc-GST cents
                if total_income > 0 and signals["arrears_total_cents"] > 0:
                    signals["arrears_concentration_index"] = round(
                        signals["arrears_total_cents"] / total_income, 4
                    )

    except Exception as exc:
        logger.warning("Trust signal collection failed (non-fatal): %s", exc)
        signals["collection_status"] = "partial_failure"

    return signals


async def detect_capital_shocks(building_id: str) -> Dict[str, Any]:
    """Generated function header.

    Function: detect_capital_shocks
    Path: backend/services/capital_shock_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    schedule = await db.capital_replacement_schedule.find(
        {"building_id": building_id, "replacement_year": {"$lte": date.today().year + 10}},
        {"_id": 0}
    ).to_list(200)
    schedule.sort(key=lambda x: x.get("replacement_year", 9999))

    baseline = await _get_financial_baseline(building_id)
    # `reserve_balance` is None when unknown. Split the two concerns explicitly:
    #   reserve_available -> the honesty flag the API and UI branch on
    #   reserve           -> a number, because everything below does arithmetic on it
    # Coercing to 0.0 here is safe ONLY because the flag travels with it in the response;
    # without the flag this would silently reintroduce the fabricated-figure bug as a
    # fabricated $0.00 instead of a fabricated $150,000.
    reserve_available = baseline.get("reserve_balance_available",
                                     baseline["reserve_balance"] is not None)
    reserve = float(baseline["reserve_balance"] or 0.0)
    sinking_income = baseline["sinking_levy_income"]
    total_levy_income = baseline["total_levy_income"] or 1.0
    annual_expenses = baseline.get("annual_expenses", 0.0)

    try:
        from services.maintenance_intelligence_service import get_intelligence_settings
        settings = await get_intelligence_settings(building_id)
        reserve_target_months = settings.get("reserve_target_months", 12)
    except Exception:
        reserve_target_months = 12

    maintenance_forecast = await db.maintenance_forecasts.find_one(
        {"building_id": building_id, "year": date.today().year}, {"_id": 0, "predicted_cost": 1}
    )
    maintenance_drag = float(
        maintenance_forecast.get("predicted_cost", 0) or 0) * MAINTENANCE_RESERVE_SHARE if maintenance_forecast else 0.0

    year_map: Dict[int, List[Dict[str, Any]]] = {}
    for item in schedule:
        year = item.get("replacement_year")
        if not year:
            continue
        year_map.setdefault(year, []).append(item)

    risks = []
    reserve_projection = []
    max_deficit = 0.0
    first_deficit_year = None

    for i in range(1, 11):
        year = date.today().year + i
        annual_contribution = sinking_income * ((1 + 0.03) ** i)
        capital_cost = sum(item.get("estimated_cost", 0) or 0 for item in year_map.get(year, []))

        reserve_before = reserve + annual_contribution - maintenance_drag
        reserve_after = reserve_before - capital_cost
        buffer_target = (annual_expenses * (reserve_target_months / 12)) if annual_expenses else 0.0
        buffer_gap = max(0.0, buffer_target - reserve_after)

        reserve_projection.append({
            "year": year,
            "reserve_before_capital": round(reserve_before, 2),
            "capital_cost": round(capital_cost, 2),
            "reserve_after_capital": round(reserve_after, 2),
        })

        if reserve_after < 0 or buffer_gap > 0:
            deficit = abs(reserve_after) if reserve_after < 0 else buffer_gap
            max_deficit = max(max_deficit, deficit)
            if first_deficit_year is None:
                first_deficit_year = year
            risks.append({
                "year": year,
                "capital_items": year_map.get(year, []),
                "projected_reserve": round(reserve_before, 2),
                "deficit": round(deficit, 2),
                "buffer_target": round(buffer_target, 2),
            })

        reserve = reserve_after

    plan_rows = await db.sinking_fund_plan.find({"building_id": building_id}, {"_id": 0}).sort("year", 1).to_list(40)
    plan_source = "sinking_fund_plan"
    if not plan_rows:
        plan_rows = _build_plan_rows_from_schedule(year_map, baseline)
        plan_source = "capital_replacement_schedule"

    capital_events = await db.sinking_fund_capital_events.find({"building_id": building_id}, {"_id": 0}).sort("year",
                                                                                                              1).to_list(
        20)

    reference_year = date.today().year
    if baseline.get("year"):
        try:
            reference_year = int(baseline["year"])
        except Exception:
            reference_year = date.today().year

    plan_years = [int(r.get("year")) for r in plan_rows if r.get("year")]
    plan_start = min(plan_years) if plan_years else None
    plan_end = max(plan_years) if plan_years else None
    if plan_start is not None and reference_year < plan_start:
        reference_year = plan_start - 1

    initial_reserve = float(baseline.get("reserve_balance") or 0.0)
    if plan_rows:
        matching = next((r for r in plan_rows if int(r.get("year")) == reference_year), None)
        if matching and (matching.get("closing_balance") or 0) > 0:
            initial_reserve = float(matching.get("closing_balance") or initial_reserve)

    capital_shock_index = _compute_capital_shock_index(plan_rows, year_map)
    reserve_collapse = _compute_reserve_projection(plan_rows, reference_year, initial_reserve)
    funding_adequacy = _compute_sfar(
        plan_rows,
        reserve_collapse["timeline"],
        reserve_collapse["start_year"],
        initial_reserve,
    )

    # Derive risk level and recommendation from sinking_fund_plan (reserve_collapse) — the
    # authoritative long-range plan. capital_replacement_schedule (reserve_projection) is used
    # only for 10-year risk flagging; its year assignments can diverge from the plan.
    plan_collapse_year = reserve_collapse.get("collapse_year")
    plan_minimum_balance = float(reserve_collapse.get("minimum_balance", 0.0))
    plan_minimum_year = reserve_collapse.get("minimum_balance_year")
    plan_buffer_target = (annual_expenses * (reserve_target_months / 12)) if annual_expenses else 0.0

    # Map reserve_collapse risk_level to the uppercase convention used in this response
    _collapse_to_risk = {"critical": "CRITICAL", "risk": "HIGH", "watch": "MEDIUM", "stable": "LOW"}
    risk_level = _collapse_to_risk.get(reserve_collapse.get("risk_level", "stable"), "LOW")

    # Elevate risk when near-term deficits/buffer gaps are material even if the long-range
    # plan doesn't collapse (real-world shocks should still surface as HIGH/CRITICAL).
    if max_deficit > 0:
        deficit_ratio = max_deficit / max(float(baseline.get("reserve_balance", 0) or 0), 1.0)
        shock_risk = _risk_level(deficit_ratio)
        _risk_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        if _risk_rank.get(shock_risk, 0) > _risk_rank.get(risk_level, 0):
            risk_level = shock_risk

    recommended_pct = None
    recommendation = "Reserve position is stable with current levies."

    if plan_collapse_year is not None:
        # Plan shows reserve goes negative — compute increase needed to bridge the deficit
        years_to_deficit = max(1, plan_collapse_year - date.today().year)
        annual_gap = abs(plan_minimum_balance) / years_to_deficit
        recommended_pct = round((annual_gap / total_levy_income) * 100, 1) if total_levy_income > 0 else None
        recommendation = (
            f"Increase annual levy by {recommended_pct}% starting {date.today().year + 1} "
            "to avoid special levies."
            if recommended_pct is not None else "Increase annual levy to cover projected deficit."
        )
    elif plan_buffer_target > 0 and plan_minimum_balance < plan_buffer_target and plan_minimum_year:
        # Reserve dips below the buffer target — suggest a levy increase to maintain the safety margin
        buffer_shortfall = plan_buffer_target - plan_minimum_balance
        years_to_minimum = max(1, plan_minimum_year - date.today().year)
        annual_gap = buffer_shortfall / years_to_minimum
        recommended_pct = round((annual_gap / total_levy_income) * 100,
                                1) if total_levy_income > 0 and annual_gap > 0 else None
        if recommended_pct is not None and recommended_pct >= 1.0:
            recommendation = (
                f"Consider increasing levy by {recommended_pct}% to maintain the "
                f"{reserve_target_months}-month reserve buffer."
            )

    result = {
        "building_id": building_id,
        "computed_at": _now(),
        "risk_level": risk_level,
        # None when unknown — round() would raise TypeError on None, and coercing to 0.0
        # here would re-create the original bug in a quieter form (a fabricated figure the
        # UI cannot tell apart from a real zero balance).
        "reserve_balance": (
            round(baseline["reserve_balance"], 2)
            if baseline.get("reserve_balance") is not None else None
        ),
        # The contract the UI must branch on. False => render "unavailable", never a
        # dollar amount, and never $0.00.
        "reserve_balance_available": reserve_available,
        "plan_source": plan_source,
        "plan_start_year": plan_start,
        "plan_end_year": plan_end,
        "capital_events": capital_events,
        "reserve_target_months": reserve_target_months,
        "annual_expenses": round(annual_expenses, 2),
        "reserve_projection": reserve_projection,
        "capital_shock_index": capital_shock_index,
        "reserve_collapse": reserve_collapse,
        "funding_adequacy": funding_adequacy,
        "risks": risks,
        "max_deficit": round(max_deficit, 2),
        "recommended_levy_increase_pct": recommended_pct,
        "recommendation": recommendation,
    }

    # Collect trust accounting signals (non-fatal — service degrades gracefully)
    try:
        trust_signals = await _get_trust_signals(building_id)
    except Exception:
        trust_signals = {}
    result["trust_signals"] = trust_signals

    update_doc = {k: v for k, v in result.items() if k != "building_id"}
    await db.capital_shock_risks.update_one(
        {"building_id": building_id},
        {"$set": update_doc},
        upsert=True
    )
    return result
