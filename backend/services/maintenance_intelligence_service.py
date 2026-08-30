"""
Maintenance Intelligence Service

Responsibilities:
- Asset risk and health scoring
- Maintenance cost forecasting (12-month predictive)
- Capital replacement forecasting (10-year timeline)
- Levy stabilization simulation
- Aggregating data for the Precomputed Intelligence Layer
"""

from datetime import datetime, timezone, date, timedelta

import logging
from pymongo import UpdateOne
import re
from typing import List, Dict, Any, Optional

from database import db
from repositories.digital_twin_repository import (
    upsert_intelligence_summary,
    upsert_maintenance_forecast,
    update_capital_schedule
)
from services.settings_service import get_general_settings_or_default

logger = logging.getLogger(__name__)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Generated function header.

    Function: _parse_iso
    Path: backend/services/maintenance_intelligence_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


async def get_intelligence_settings(building_id: str) -> Dict[str, Any]:
    """Fetch configurable weights and rates from SiteSettings. Scoped to building."""
    settings = await get_general_settings_or_default(building_id, {"_id": 0})
    if not settings:
        return {
            "inflation_rate": 0.03,
            "repair_weight": 3.0,
            "age_weight": 20.0,
            "maintenance_overdue_weight": 2.0,
            "levy_change_limit_percent": 5.0,
            "reserve_target_months": 12,
            "projection_horizon_years": 10,
        }

    return {
        "inflation_rate": settings.get("inflation_rate", 0.03),
        "repair_weight": settings.get("repair_weight", 3.0),
        "age_weight": settings.get("age_weight", 20.0),
        "maintenance_overdue_weight": settings.get("maintenance_overdue_weight", 2.0),
        "levy_change_limit_percent": settings.get("levy_change_limit_percent", 5.0),
        "reserve_target_months": settings.get("reserve_target_months", 12),
        "projection_horizon_years": settings.get("projection_horizon_years", 10),
    }


def _calculate_age_ratio(installation_date_str: Optional[str], expected_lifespan_years: int) -> float:
    """Generated function header.

    Function: _calculate_age_ratio
    Path: backend/services/maintenance_intelligence_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not installation_date_str:
        return 0.5  # Neutral assumption if unknown

    try:
        # Handle trailing Z or offset
        ds = installation_date_str.replace('Z', '+00:00')
        install_date = datetime.fromisoformat(ds).date()
        age_years = (date.today() - install_date).days / 365.25
        return min(1.0, age_years / max(1, expected_lifespan_years))
    except Exception:
        return 0.5


async def compute_asset_risk_score(
        asset: Dict[str, Any],
        settings: Dict[str, Any],
        building_id: str,
        repair_count: Optional[int] = None
) -> float:
    """
    Algorithm:
    risk_score = (repair_count_last_12m * repair_weight)
               + (age_ratio * age_weight)
               + (maintenance_overdue_factor * overdue_weight)
    Normalized and capped at 100. Scoped to building.
    """
    asset_id = asset.get("id")

    # 1. Repair frequency (last 12 months)
    if repair_count is None:
        from datetime import timedelta
        year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        repair_count = 0
        if asset_id:
            repair_count = await db.work_orders.count_documents({
                "building_id": building_id,
                "asset_id": asset_id,
                "status": "completed",
                "created_at": {"$gte": year_ago}
            })

    # 2. Age ratio
    age_ratio = _calculate_age_ratio(asset.get("installation_date"), asset.get("expected_lifespan_years", 20))

    # 3. Maintenance overdue factor
    overdue_factor = 0
    last_service = asset.get("last_service_date")
    freq_months = asset.get("maintenance_frequency_months", 12)
    if last_service:
        try:
            ls = last_service.replace('Z', '+00:00')
            last_date = datetime.fromisoformat(ls).date()
            months_since = (date.today() - last_date).days / 30.44
            if months_since > freq_months:
                # Max out at 1.0 if 12 months overdue
                overdue_factor = min(1.0, (months_since - freq_months) / 12)
        except Exception as exc:
            logger.warning("maintenance_intelligence: last_service date parse failed: %s", exc)
    else:
        overdue_factor = 1.0  # Never serviced? High risk.

    raw_score = (repair_count * settings["repair_weight"]) + \
                (age_ratio * settings["age_weight"]) + \
                (overdue_factor * settings["maintenance_overdue_weight"])

    # Simple normalization for v1 - assume max sensible raw score is around 50
    # and map to 0-100.
    normalized = (raw_score / 50.0) * 100
    return min(100.0, float(normalized))


async def _get_asset_repair_window(asset_id: str, building_id: str) -> Dict[str, Any]:
    """Return repair counts and costs for the last 12 months relative to last repair date."""
    cursor = db.work_orders.find(
        {"asset_id": asset_id, "building_id": building_id, "status": "completed"},
        {"_id": 0, "created_at": 1, "estimated_cost": 1}
    )
    repairs = await cursor.to_list(500)
    if not repairs:
        return {
            "repairs_last_12m": 0,
            "repair_cost_last_12m": 0.0,
            "last_repair_date": None,
            "window_start": None,
            "window_end": None,
        }

    dated = []
    for r in repairs:
        dt = _parse_iso(r.get("created_at"))
        if dt:
            dated.append((dt, float(r.get("estimated_cost", 0) or 0)))

    if not dated:
        return {
            "repairs_last_12m": 0,
            "repair_cost_last_12m": 0.0,
            "last_repair_date": None,
            "window_start": None,
            "window_end": None,
        }

    dated.sort(key=lambda x: x[0])
    last_date = dated[-1][0]
    # Anchor the 12-month window to now so assets with no recent repairs
    # don't falsely show positive repair counts from years ago.
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=365)
    repairs_last_12m = 0
    repair_cost_last_12m = 0.0

    for dt, cost in dated:
        if dt >= window_start:
            repairs_last_12m += 1
            repair_cost_last_12m += cost

    return {
        "repairs_last_12m": repairs_last_12m,
        "repair_cost_last_12m": round(repair_cost_last_12m, 2),
        "last_repair_date": last_date.isoformat().replace("+00:00", "Z"),
        "window_start": window_start.isoformat().replace("+00:00", "Z"),
        "window_end": now.isoformat().replace("+00:00", "Z"),
    }


async def _fetch_building_work_orders(building_id: str) -> Dict[str, List]:
    """Fetch all completed work orders for the building grouped by asset_id (avoids N+1 queries).

    The 10 000 cap is a practical safety ceiling; a building with >1 000 assets and
    an average of 10 completed work orders each would produce 10 000 records, which is
    already well above any realistic strata complex. Increase this constant if needed.
    """
    cursor = db.work_orders.find(
        {"building_id": building_id, "status": "completed"},
        {"_id": 0, "asset_id": 1, "created_at": 1, "estimated_cost": 1}
    )
    all_orders = await cursor.to_list(10000)
    grouped: Dict[str, List] = {}
    for order in all_orders:
        asset_id = order.get("asset_id")
        if asset_id:
            grouped.setdefault(asset_id, []).append(order)
    return grouped


def _compute_repair_window(repairs: List[Dict]) -> Dict[str, Any]:
    """Compute repair stats for the last 12 months relative to now (no DB call)."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=365)

    dated = []
    for r in repairs:
        dt = _parse_iso(r.get("created_at"))
        if dt:
            dated.append((dt, float(r.get("estimated_cost", 0) or 0)))

    if not dated:
        return {
            "repairs_last_12m": 0,
            "repair_cost_last_12m": 0.0,
            "last_repair_date": None,
            "window_start": window_start.isoformat().replace("+00:00", "Z"),
            "window_end": now.isoformat().replace("+00:00", "Z"),
        }

    dated.sort(key=lambda x: x[0])
    last_date = dated[-1][0]
    repairs_last_12m = 0
    repair_cost_last_12m = 0.0

    for dt, cost in dated:
        if dt >= window_start:
            repairs_last_12m += 1
            repair_cost_last_12m += cost

    return {
        "repairs_last_12m": repairs_last_12m,
        "repair_cost_last_12m": round(repair_cost_last_12m, 2),
        "last_repair_date": last_date.isoformat().replace("+00:00", "Z"),
        "window_start": window_start.isoformat().replace("+00:00", "Z"),
        "window_end": now.isoformat().replace("+00:00", "Z"),
    }


async def compute_maintenance_attention(building_id: str) -> List[Dict[str, Any]]:
    """Compute repair-to-replacement anomalies and recurring repair risks. Scoped to building."""
    assets = await db.building_assets.find({"building_id": building_id}).to_list(1000)
    if not assets:
        return []

    # Batch-fetch all completed work orders for the building to avoid N+1 DB queries
    work_orders_by_asset = await _fetch_building_work_orders(building_id)

    attention: List[Dict[str, Any]] = []
    for asset in assets:
        asset_id = asset.get("id")
        if not asset_id:
            continue
        repair_window = _compute_repair_window(work_orders_by_asset.get(asset_id, []))
        repairs_last_12m = repair_window["repairs_last_12m"]
        repair_cost = repair_window["repair_cost_last_12m"]
        replacement_cost = float(asset.get("replacement_cost_estimate", 0) or 0)
        ratio = (repair_cost / replacement_cost) if replacement_cost else 0.0
        risk_score = float(asset.get("risk_score", 0) or 0)

        anomaly_type = None
        recommendation = None
        if replacement_cost and ratio >= 0.4:
            anomaly_type = "repair_to_replacement"
            recommendation = f"Replace {asset.get('name', 'asset')} instead of further repairs."
        elif repairs_last_12m >= 3:
            anomaly_type = "recurring_repairs"
            recommendation = "Investigate recurring fault pattern."
        elif risk_score >= 80:
            anomaly_type = "high_risk_asset"
            recommendation = "Schedule inspection and plan replacement."

        if anomaly_type:
            attention.append({
                "building_id": building_id,
                "asset_id": asset_id,
                "asset_name": asset.get("name", "Unknown Asset"),
                "facility_id": asset.get("facility_id"),
                "benefit_group_id": asset.get("benefit_group_id"),
                "anomaly_type": anomaly_type,
                "repairs_last_12m": repairs_last_12m,
                "repair_cost_last_12m": repair_cost,
                "repair_to_replacement_ratio": round(ratio, 2),
                "risk_score": round(risk_score, 1),
                "recommendation": recommendation,
                "last_repair_date": repair_window.get("last_repair_date"),
                "window_start": repair_window.get("window_start"),
                "window_end": repair_window.get("window_end"),
                "updated_at": datetime.utcnow().isoformat(),
            })

    await db.maintenance_anomalies.delete_many({"building_id": building_id})
    if attention:
        await db.maintenance_anomalies.insert_many(attention)
    return attention


MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Below this many costed, dated work orders there is not enough history to claim a
# seasonal shape. The number is deliberately low-but-not-one: a single expensive
# July repair would otherwise produce a forecast that puts 100% of next year's
# maintenance in July, which is worse than saying nothing.
_MIN_WORK_ORDERS_FOR_SEASONALITY = 12
# And they must be spread across the year, not clustered. Six distinct months is
# half a year of evidence — enough to distinguish a real seasonal pattern from one
# busy quarter.
_MIN_DISTINCT_MONTHS_FOR_SEASONALITY = 6


async def _monthly_maintenance_shape(building_id: str) -> Dict[str, Any]:
    """Twelve monthly weights for spreading an annual maintenance forecast.

    WHY THIS IS IN THE BACKEND
    The building-intelligence page used to do this in the browser, multiplying one
    annual `predicted_cost` by a hardcoded `SEASONAL_WEIGHTS` array that had been
    invented in the frontend ("slightly higher in winter"). That is a component
    constructing financial truth: the chart looked modelled, and nothing behind it
    was. This returns a shape that is either MEASURED from the building's own
    completed work orders or explicitly declared flat — and says which.

    Returns `{weights, basis, sample_size, distinct_months}`. `basis` is one of:

      historical_seasonality — weights are this building's own completed-work-order
                               spend per calendar month. A real pattern.
      even_spread            — not enough history to see a pattern, so the annual
                               figure is divided by twelve. A stated assumption, not
                               a pretend forecast; the UI is expected to label it.

    Weights always sum to 1.0 so a caller can multiply an annual total by them.
    """
    cursor = db.work_orders.find(
        {"building_id": building_id, "status": "completed"},
        {"_id": 0, "created_at": 1, "completed_at": 1, "actual_cost": 1, "estimated_cost": 1},
    )

    # Iterated, not `.to_list(N)`.
    #
    # A capped to_list here would be the same bug the repo already documents for
    # financial_forecasts: past the cap the read silently truncates and the seasonal
    # shape is then computed from an arbitrary subset of the building's history, with
    # nothing in the output saying so. Only twelve running totals are accumulated, so
    # iterating the full cursor costs no more memory than the cap did.
    monthly_totals = [0.0] * 12
    counted = 0
    async for wo in cursor:
        when = _parse_iso(wo.get("completed_at")) or _parse_iso(wo.get("created_at"))
        if not when:
            continue
        cost = wo.get("actual_cost")
        if cost is None:
            cost = wo.get("estimated_cost")
        try:
            cost = float(cost or 0)
        except (TypeError, ValueError):
            continue
        if cost <= 0:
            continue
        monthly_totals[when.month - 1] += cost
        counted += 1

    distinct_months = sum(1 for t in monthly_totals if t > 0)
    total = sum(monthly_totals)

    if (
        counted >= _MIN_WORK_ORDERS_FOR_SEASONALITY
        and distinct_months >= _MIN_DISTINCT_MONTHS_FOR_SEASONALITY
        and total > 0
    ):
        weights = [t / total for t in monthly_totals]
        basis = "historical_seasonality"
    else:
        weights = [1.0 / 12] * 12
        basis = "even_spread"

    return {
        "weights": weights,
        "basis": basis,
        "sample_size": counted,
        "distinct_months": distinct_months,
    }


# ─── Maintenance spend history ────────────────────────────────────────────────
#
# THE SINGLE HOME for "is this expense category maintenance/repair spend?".
# If you need this classification elsewhere, import `is_maintenance_category` —
# do not re-derive the pattern. A second copy that classifies "Gardens & Grounds"
# differently would silently produce a different maintenance total on a different
# page, which is the duplicate-concept failure documented in
# tasks/P0-CANONICAL-OWNER-REGISTRY.md.
#
# Building-agnostic: it matches on category NAME, not on any building's chart of
# accounts, because category naming is per-manager and drifts over time (East Gate
# alone carries "Lift Maintenance Contract", "Lift maintenance", "Lift Repairs" and
# "Lift maintenance (pre-Civium)" for the same real-world spend).
_MAINTENANCE_CATEGORY_RE = re.compile(
    r"repair|maintenance|lift|garage|roof|waterproof|plumb|electric|fire|garden|"
    r"clean|pest|door|paint|drain|hvac|carpet|driveway|cctv|access|antenna|"
    r"balustrade|handrail|screen|signage|anchor",
    re.IGNORECASE,
)

# Categories whose NAME can contain a maintenance word but which are not
# maintenance spend — insurance premiums, management fees, utilities consumption,
# taxes, banking and professional services. Checked SECOND, so it wins.
_NON_MAINTENANCE_CATEGORY_RE = re.compile(
    r"insurance|management fee|manage|tax|bank|audit|account|legal|postage|portal|"
    r"consultant|venue|seal|disburse|arrears|gst|utility|utilities|"
    r"water consumption|electricity consumption|sinking fund|income tax|"
    r"regulatory|certificate|registration",
    re.IGNORECASE,
)


def is_maintenance_category(name: str) -> bool:
    """True when an expense category name denotes maintenance/repair spend."""
    if not name:
        return False
    if _NON_MAINTENANCE_CATEGORY_RE.search(name):
        return False
    return bool(_MAINTENANCE_CATEGORY_RE.search(name))


# Completed years needed before a trailing mean is a defensible forecast anchor.
# Two years cannot distinguish a trend from a one-off major work item.
_MIN_YEARS_FOR_HISTORY_FORECAST = 3
# How many completed years the trailing mean averages over.
_HISTORY_FORECAST_WINDOW = 3


async def maintenance_spend_history(building_id: str) -> List[Dict[str, Any]]:
    """Actual maintenance spend per financial year, from this building's own ledger.

    Reads `levy_categories` (the per-year, per-category actuals imported from the
    building's audited financials), excluding archived/consolidated rows so a
    category merged into a canonical name is not counted twice.

    This is REAL money the building spent, not a model. It exists because the
    asset-derived forecast had no relationship to it: East Gate's forecast was
    $26,765/yr against actual maintenance spend of $147,000-$191,000/yr — a ~6x
    understatement that no chart surfaced, because the only thing rendered was a
    flat twelve-month line derived from that same wrong annual figure.
    """
    rows = await db.levy_categories.find(
        {"building_id": building_id, "is_archived": {"$ne": True}},
        {"_id": 0, "name": 1, "year": 1, "actual_amount": 1},
    ).to_list(2000)

    totals: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for row in rows:
        if not is_maintenance_category(row.get("name") or ""):
            continue
        year = str(row.get("year") or "").strip()
        if not year:
            continue
        try:
            amount = float(row.get("actual_amount") or 0)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        totals[year] = totals.get(year, 0.0) + amount
        counts[year] = counts.get(year, 0) + 1

    return [
        {"year": y, "actual_cost": round(totals[y], 2), "category_count": counts[y]}
        for y in sorted(totals)
    ]


async def generate_maintenance_forecast(building_id: str) -> Dict[str, Any]:
    """
    Predicts maintenance for next 12 months. Scoped to building.
    Uses historical averages and applies risk-based escalations.
    """
    settings = await get_intelligence_settings(building_id)
    assets = await db.building_assets.find({"building_id": building_id}).to_list(1000)

    total_predicted_cost = 0.0
    predicted_repairs_count = 0
    high_risk_asset_names = []

    RISK_THRESHOLD_HIGH = 70
    RISK_THRESHOLD_MEDIUM = 40

    for asset in assets:
        risk_score = asset.get("risk_score", 0)
        # Rule of thumb: Annual maintenance is 2% of replacement cost
        base_annual_maint = asset.get("replacement_cost_estimate", 0) * 0.02

        # Risk escalation
        multiplier = 1.0
        if risk_score > RISK_THRESHOLD_HIGH:
            multiplier = 2.5
            high_risk_asset_names.append(asset.get("name", "Unknown Asset"))
        elif risk_score > RISK_THRESHOLD_MEDIUM:
            multiplier = 1.5

        predicted_cost = base_annual_maint * multiplier
        total_predicted_cost += predicted_cost

        if predicted_cost > 0:
            predicted_repairs_count += 1 if risk_score < 60 else 2

    # Confidence Score calculation
    # confidence = data_points / ideal_data_points (assume 5+ completed WOs in history is ideal)
    historical_count = await db.work_orders.count_documents({
        "building_id": building_id,
        "status": "completed"
    })
    confidence = min(1.0, historical_count / 10.0)  # 10+ WOs = 100% confidence

    # Monthly distribution of the annual figure, measured where the history supports
    # it. Absent when there is no annual figure to distribute: an all-zero twelve
    # months reads as a confident "no maintenance expected next year", which is a
    # different and much stronger claim than "we do not know yet".
    shape = await _monthly_maintenance_shape(building_id)
    monthly_breakdown = None
    if total_predicted_cost > 0:
        monthly_breakdown = [
            {
                "month": i + 1,
                "label": MONTH_LABELS[i],
                "predicted_cost": round(total_predicted_cost * w, 2),
            }
            for i, w in enumerate(shape["weights"])
        ]

    # Actual maintenance spend, per year, from the building's own ledger — and a
    # forecast anchored to it.
    #
    # `predicted_cost` above is an ASSET-RISK MODEL (2% of replacement cost, scaled
    # by risk score). It is deliberately left unchanged: it is a different estimator
    # answering a different question, and silently redefining a shipped KPI is not
    # this change's job. But on its own it was the only number the page had, and for
    # East Gate it reads $26,765/yr against $147k-$191k of real spend.
    #
    # The trailing mean is over COMPLETED years only. Including the current year
    # would drag the average down by however much of it has not happened yet —
    # East Gate's 2026 row is year-to-date, not an annual total.
    history = await maintenance_spend_history(building_id)
    current_year = str(date.today().year)
    completed_years = [h for h in history if h["year"] != current_year]

    history_based_forecast = None
    history_forecast_basis = None
    if len(completed_years) >= _MIN_YEARS_FOR_HISTORY_FORECAST:
        window = completed_years[-_HISTORY_FORECAST_WINDOW:]
        history_based_forecast = round(
            sum(h["actual_cost"] for h in window) / len(window), 2
        )
        history_forecast_basis = (
            f"mean of actual maintenance spend for "
            f"{window[0]['year']}-{window[-1]['year']}"
        )

    forecast_data = {
        "building_id": building_id,
        "year": date.today().year,
        "predicted_repairs_count": int(predicted_repairs_count),
        "predicted_cost": round(total_predicted_cost, 2),
        "high_risk_assets": high_risk_asset_names[:5],
        "confidence_score": round(confidence, 2),
        "monthly_breakdown": monthly_breakdown,
        # How the monthly split was arrived at, so the chart can say so rather than
        # presenting an assumption as a model.
        "monthly_basis": shape["basis"],
        "monthly_sample_size": shape["sample_size"],
        # Real spend, and a forecast derived from it. `annual_history` is [] when a
        # building has no imported category actuals — an empty list is "we have no
        # history", which the UI must render differently from "history of zero".
        "annual_history": history,
        "history_based_forecast": history_based_forecast,
        "history_forecast_basis": history_forecast_basis,
    }

    await upsert_maintenance_forecast(forecast_data)
    return forecast_data


async def generate_capital_replacement_schedule(building_id: str) -> List[Dict[str, Any]]:
    """10-year capital works plan based on asset lifespans and inflation. Scoped to building."""
    settings = await get_intelligence_settings(building_id)
    assets = await db.building_assets.find({"building_id": building_id}).to_list(1000)
    current_year = date.today().year
    inflation_rate = settings["inflation_rate"]

    schedule = []
    for asset in assets:
        install_date_str = asset.get("installation_date")
        lifespan = asset.get("expected_lifespan_years", 20)

        if not install_date_str:
            continue

        try:
            install_year = datetime.fromisoformat(install_date_str.replace('Z', '+00:00')).year
            replacement_year = install_year + lifespan

            if current_year <= replacement_year <= current_year + 10:
                years_to_replacement = replacement_year - current_year
                base_cost = asset.get("replacement_cost_estimate", 0)
                inflated_cost = base_cost * ((1 + inflation_rate) ** years_to_replacement)

                schedule.append({
                    "building_id": building_id,
                    "asset_id": asset.get("id", "unknown"),
                    "asset_name": asset.get("name", "Unknown Asset"),
                    "replacement_year": replacement_year,
                    "estimated_cost": round(inflated_cost, 2)
                })
        except Exception:
            continue

    # Sort by year
    schedule.sort(key=lambda x: x["replacement_year"])
    await update_capital_schedule(building_id, schedule)
    return schedule


async def update_building_intelligence_summary(building_id: str) -> Dict[str, Any]:
    """Aggregates all precomputed data into a single fast-loading summary. Scoped to building."""
    assets = await db.building_assets.find({"building_id": building_id}).to_list(1000)
    if not assets:
        return {}

    avg_health = sum(a.get("health_score", 100) for a in assets) / len(assets)
    high_risk_count = sum(1 for a in assets if a.get("risk_score", 0) > 70)

    forecast = await db.maintenance_forecasts.find_one({"building_id": building_id, "year": date.today().year},
                                                       {"_id": 0})
    cap_works = await db.capital_replacement_schedule.find({
        "building_id": building_id,
        "replacement_year": {"$lte": date.today().year + 5}
    }, {"_id": 0}).to_list(100)
    cap_works.sort(key=lambda x: x.get("replacement_year", 9999))
    total_cap_5y = sum(item.get("estimated_cost", 0) for item in cap_works if item.get("estimated_cost"))
    # Fetch top-3 anomalies sorted by ratio in the query to avoid incorrect top-3 when more than 50 exist
    attention_top3 = await db.maintenance_anomalies.find(
        {"building_id": building_id}, {"_id": 0}
    ).sort("repair_to_replacement_ratio", -1).to_list(3)
    attention_count = await db.maintenance_anomalies.count_documents({"building_id": building_id})
    capital_shock = await db.capital_shock_risks.find_one({"building_id": building_id}, {"_id": 0})
    levy_fairness = await db.levy_fairness_results.find_one({"building_id": building_id}, {"_id": 0})

    # Anomaly count (placeholder - will be linked to maintenance anomalies)
    anomaly_count = await db.financial_anomalies.count_documents({
        "building_id": building_id,
        "resolved": False,
        "anomaly_type": {"$in": ["maintenance", "asset", "repeat_repairs", "repair_cost_high"]}
    })

    summary = {
        "building_id": building_id,
        "asset_health_score": round(avg_health, 1),
        "high_risk_assets_count": high_risk_count,
        "expected_maintenance_12m": forecast.get("predicted_cost", 0) if forecast else 0,
        "capital_replacement_5y": round(total_cap_5y, 2),
        "levy_risk": "high" if avg_health < 50 or total_cap_5y > 200000 else ("medium" if avg_health < 75 else "low"),
        "anomalies_detected_count": anomaly_count,
        "maintenance_anomalies_count": attention_count,
        "attention_needed": attention_top3,
        "upcoming_capital_items": cap_works[:3],
        "capital_shock_risk": capital_shock.get("risk_level") if capital_shock else None,
        "capital_shock_recommendation": capital_shock.get("recommendation") if capital_shock else None,
        "levy_fairness_score": levy_fairness.get("benefit_fairness_score") if levy_fairness else None,
    }

    await upsert_intelligence_summary(summary)
    return summary


async def recompute_all_maintenance_intelligence(building_id: str):
    """
    Full pipeline for recomputing maintenance intelligence. Scoped to building.
    Performance Optimization⚡: Implemented batch fetching and bulk writing to reduce
    database round-trips from O(N) to O(1) concurrent batches.
    """
    settings = await get_intelligence_settings(building_id)
    assets = await db.building_assets.find(
        {"building_id": building_id}
    ).to_list(1000)

    if not assets:
        return

    # 1. Batch fetch repair counts for all assets in one aggregation query
    year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    repair_counts_agg = await db.work_orders.aggregate([
        {"$match": {
            "building_id": building_id,
            "status": "completed",
            "created_at": {"$gte": year_ago}
        }},
        {"$group": {
            "_id": "$asset_id",
            "count": {"$sum": 1}
        }}
    ]).to_list(None)
    repair_counts_map = {r["_id"]: r["count"] for r in repair_counts_agg if r["_id"]}

    # 2. Collect updates for bulk writing
    now_iso = datetime.now(timezone.utc).isoformat()
    asset_updates = []
    health_score_updates = []

    for asset in assets:
        asset_id = asset.get("id")
        if not asset_id:
            continue

        # Use pre-calculated repair count to avoid N+1 queries
        repair_count = repair_counts_map.get(asset_id, 0)
        risk = await compute_asset_risk_score(
            asset, settings, building_id, repair_count=repair_count
        )
        health = 100.0 - risk

        # Prepare update for building_assets
        asset_updates.append(UpdateOne(
            {"building_id": building_id, "id": asset_id},
            {"$set": {"risk_score": risk, "health_score": health, "updated_at": now_iso}}
        ))

        # Prepare update for asset_health_scores
        health_score_updates.append(UpdateOne(
            {"asset_id": asset_id, "building_id": building_id},
            {"$set": {
                "asset_id": asset_id,
                "building_id": building_id,
                "risk_score": risk,
                "health_score": health,
                "replacement_cost": asset.get("replacement_cost_estimate", 0),
                "updated_at": now_iso
            }},
            upsert=True
        ))

    # 3. Execute bulk updates
    if asset_updates:
        await db.building_assets.bulk_write(asset_updates)
    if health_score_updates:
        await db.asset_health_scores.bulk_write(health_score_updates)

    # 4. Roll up health to facilities using a single aggregation pipeline
    facility_health_agg = await db.building_assets.aggregate([
        {"$match": {"building_id": building_id, "facility_id": {"$ne": None}}},
        {"$group": {
            "_id": "$facility_id",
            "avg_health": {"$avg": "$health_score"}
        }}
    ]).to_list(None)

    facility_ops = [
        UpdateOne(
            {"building_id": building_id, "id": row["_id"]},
            {"$set": {
                "health_score": round(row["avg_health"], 1),
                "updated_at": now_iso
            }}
        )
        for row in facility_health_agg
    ]
    if facility_ops:
        await db.facilities.bulk_write(facility_ops)

    await compute_maintenance_attention(building_id)
    await generate_maintenance_forecast(building_id)
    await generate_capital_replacement_schedule(building_id)
    await update_building_intelligence_summary(building_id)

    logger.info(f"Maintenance Intelligence recompute completed for building {building_id}")


async def simulate_levy_stabilization(
        building_id: str,
        reserve_target_months: int = 12,
        levy_change_limit: float = 5.0,
        inflation_rate: float = 0.03,
        capital_buffer: float = 0.05
) -> Dict[str, Any]:
    """
    Simulates levy paths over 10 years to reach reserve target with minimal volatility.
    """
    # 1. Fetch current financial state
    # Simplified for v1: Get current annual levy and sinking fund balance
    # (In real implementation, would pull from annual_levies and unit_levy_ledger)
    from utils.finance_helpers import get_latest_levy_year, get_levy_proposed_amounts
    latest_year = await get_latest_levy_year(building_id) or str(date.today().year)
    levy_doc = await db.annual_levies.find_one({"year": latest_year, "building_id": building_id})

    current_levy = 0
    current_admin_levy = 0
    current_reserve = 0
    if levy_doc:
        # The annual levy baseline comes from get_levy_proposed_amounts(), NOT from
        # admin_fund/sinking_fund.levy_income.
        #
        # levy_income is YTD *collected* — a partial figure for the current year — and
        # CLAUDE.md names using it as an annual amount as a recurring bug. Reading it here
        # was wrong in both directions:
        #
        #   * Understated wherever it is populated: East Gate FY2025 records $466,976.27
        #     collected against a $361,338.00 proposed annual levy. Different quantities,
        #     not two estimates of one.
        #   * ZERO wherever the import did not populate it. East Gate's FY2026 doc is
        #     PDF-sourced and carries proposed_amount_cents with levy_income NULL — the
        #     exact case get_levy_proposed_amounts()'s own docstring documents. `or 0`
        #     turned that unknown into a hard 0, so a document that HAS the annual budget
        #     produced a $0 baseline while baseline_available stayed True.
        #
        # The consequence was a live ten-year projection of levy_required = $0.00 for
        # every year and a reserve falling to -$991,865.93, rendered as a chart under the
        # recommendation "Increase levies by 5.0% annually" — advice derived from nothing.
        current_admin_levy, current_sinking_levy = get_levy_proposed_amounts(levy_doc)
        current_levy = current_admin_levy + current_sinking_levy
        current_reserve = levy_doc.get("sinking_fund", {}).get("closing_balance", 0) or 0
        # A document that exists but yields no usable annual levy is NOT a baseline.
        # Existence was the old test, which is why the $0 projection above was presented
        # as real. Every downstream figure scales off current_levy, so if it is 0 the
        # whole series is 0 and must be declared unavailable.
        baseline_available = current_levy > 0
        if not baseline_available:
            logger.warning(
                "maintenance_intelligence: annual_levies doc for building_id=%s year=%s "
                "yields no proposed annual levy (admin=%s sinking=%s) — projecting from "
                "0.0 and flagging baseline_available=False.",
                building_id, latest_year, current_admin_levy, current_sinking_levy,
            )
    else:
        # No levy document for this building. Both figures are UNKNOWN.
        #
        # This branch previously substituted current_levy = 350000 and
        # current_reserve = 150000 — invented, building-shaped constants that the
        # project rule explicitly forbids ("last-resort fallbacks must be 0.0 +
        # logger.warning(), never a building-specific hardcoded amount"). They fed a
        # 10-year projection that then looked plausible while being entirely fictional.
        #
        # Zero is used for the arithmetic so the simulation still runs, and the caller
        # is told the inputs were unavailable so it can refuse to present the output as
        # a real forecast.
        logger.warning(
            "maintenance_intelligence: no annual_levies document for building_id=%s — "
            "current levy and reserve are UNKNOWN. Projecting from 0.0 and flagging "
            "baseline_available=False; do not present this as a real forecast.",
            building_id,
        )
        current_levy = 0.0
        current_reserve = 0.0
        baseline_available = False

    # 2. Get 10-year capital expenditure schedule
    cap_works = await db.capital_replacement_schedule.find({"building_id": building_id}).to_list(100)
    cap_map = {}
    for item in cap_works:
        year = item.get("replacement_year")
        if year:
            cap_map[year] = cap_map.get(year, 0) + item.get("estimated_cost", 0)

    # 3. Simulate
    years = []
    projected_levy = current_levy
    projected_reserve = current_reserve
    # Operating cost baseline is the ADMIN fund's own annual levy, not a fraction of the
    # total. This previously read `current_levy * 0.8` — an invented building-agnostic
    # constant ("assume 80% of levy is for ops") standing in for a figure the same
    # document already carries. The administrative fund IS the operating fund by
    # definition; the sinking fund is capital and must not be counted as recurring ops,
    # or the projection double-counts capital against the capital schedule below.
    base_op_cost = current_admin_levy

    results = []
    for i in range(1, 11):
        year = date.today().year + i
        op_cost = base_op_cost * ((1 + inflation_rate) ** i)
        capital_cost = cap_map.get(year, 0)

        # Simple smoothing: increase levy by limit until target reserve reached
        target_reserve = op_cost * (reserve_target_months / 12)

        if projected_reserve < target_reserve or capital_cost > projected_reserve * 0.5:
            growth = levy_change_limit / 100
        else:
            growth = inflation_rate  # Keep up with inflation

        projected_levy *= (1 + growth)
        projected_reserve = projected_reserve + projected_levy - op_cost - capital_cost

        results.append({
            "year": year,
            "levy_required": round(projected_levy, 2),
            "operating_cost": round(op_cost, 2),
            "capital_expenditure": round(capital_cost, 2),
            "projected_reserve": round(projected_reserve, 2),
            "levy_increase_pct": round(growth * 100, 2)
        })

    return {
        "building_id": building_id,
        # When the baseline was unavailable every projected figure derives from 0.0, so a
        # levy-increase recommendation would be advice computed from nothing. Say so
        # instead of issuing it.
        "recommendation": (
            f"Increase levies by {levy_change_limit}% annually for the next 5 years to stabilize reserves."
            if baseline_available
            else "No levy or reserve data recorded for this building — no projection can be made."
        ),
        # False => every figure in `projections` derives from a 0.0 baseline. Callers must
        # not present these as a forecast. The frontend ignored this flag entirely until
        # 2026-08-28, which is how the $0/-$991k projection reached the screen.
        "baseline_available": baseline_available,
        # State the inputs so the UI can show what the projection is built on instead of
        # asking the reader to trust an unlabelled curve.
        "baseline_basis": {
            "levy_year": latest_year,
            "annual_levy_total": round(float(current_levy), 2),
            "annual_admin_levy": round(float(current_admin_levy), 2),
            "opening_reserve": round(float(current_reserve), 2),
            "source": "annual_levies.proposed (get_levy_proposed_amounts)",
        },
        "projections": results
    }
