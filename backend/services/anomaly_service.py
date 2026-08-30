# @featuretrace:financial-anomaly-detection — scans budget/expense/arrears data for irregularities.
# Layer: service
# Data flow: get_levy_fund_data() + financial_categories/financial_transactions → detect_financial_anomalies() → db.financial_anomalies (building-scoped).
# Related: backend/services/report_service.py (surfaces anomalies in the executive PDF)
#          backend/utils/finance_helpers.py (get_levy_fund_data)
"""
Anomaly Detection Service — scans financial data for irregularities.

Detects:
  1. Budget overrun > 20% (actual > budgeted * 1.2)
  2. Unbudgeted categories (actual > 0, budgeted == 0)
  3. Expense spike (actual > 3-yr rolling avg * 1.3)
  4. Owner arrears (2+ unpaid quarters)
  5. Interest spike (interest category increases > 50% YoY)
  6. Forecasted deficit (forecast closing_balance < 0)
  7. Cashflow negative risk

Stores results in financial_anomalies collection.
Reads from financial_categories + financial_transactions (new schema).
Falls back to levy_categories budgeted_amount (not actual_amount) if needed.
"""
import uuid
from datetime import datetime, timezone, date, timedelta

from typing import List, Optional

from database import db
from services.settings_service import get_general_settings_or_default
from repositories.financial_repository import aggregate_actual_by_category
from utils.finance_helpers import compute_period_due_dates, get_levy_fund_data, get_levy_rates
from utils.finance_logger import timed

_OVERRUN_THRESHOLD = 0.20  # 20% over budget triggers anomaly
_SPIKE_MULTIPLIER = 1.30  # 30% above rolling avg = spike
_ARREARS_QUARTERS = 2  # 2+ missed quarters = arrears
_INTEREST_SPIKE_PCT = 0.50  # 50% YoY interest increase


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/anomaly_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _severity_from_deviation(deviation_pct: float) -> str:
    """Generated function header.

    Function: _severity_from_deviation
    Path: backend/services/anomaly_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if deviation_pct >= 100:
        return "critical"
    if deviation_pct >= 50:
        return "high"
    if deviation_pct >= 20:
        return "medium"
    return "low"


async def _get_categories_with_actuals(year: str, building_id: str) -> list:
    """
    Return categories enriched with actual_amount from financial_transactions.
    Falls back to levy_categories (budgeted_amount only) when new collections empty.
    """
    from services.financial_service import get_legacy_levy_categories_shape
    cats = await get_legacy_levy_categories_shape(year, building_id=building_id)
    if cats:
        return cats

    # Fallback: levy_categories without actual_amount (Hard Rule compliant)
    legacy = await db.levy_categories.find(
        {"year": year, "building_id": building_id},
        {"_id": 0, "name": 1, "fund_type": 1, "budgeted_amount": 1, "year": 1}
    ).to_list(100)
    for c in legacy:
        c["actual_amount"] = 0.0
    return legacy


async def detect_financial_anomalies(year: str, building_id: str) -> List[dict]:
    """
    Run all anomaly detectors for the given year.
    Clears unresolved anomalies first (re-scan on demand).
    Returns list of detected anomaly documents.
    """
    with timed("anomaly_scan", year=year, building_id=building_id):
        return await _detect_financial_anomalies_impl(year, building_id)


async def _detect_financial_anomalies_impl(year: str, building_id: str) -> List[dict]:
    # Clear existing unresolved anomalies for this year
    """Generated function header.

    Function: _detect_financial_anomalies_impl
    Path: backend/services/anomaly_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await db.financial_anomalies.delete_many({"building_id": building_id, "financial_year": year, "resolved": False})

    anomalies = []

    # ── 1. Budget overrun + unbudgeted categories ──────────────────────────
    cats = await _get_categories_with_actuals(year, building_id)

    for cat in cats:
        actual = cat.get("actual_amount", 0) or 0
        budgeted = cat.get("budgeted_amount", 0) or 0
        name = cat.get("name", "Unknown")
        fund_type = cat.get("fund_type", "")

        if actual > 0 and budgeted == 0:
            a = _make_anomaly(
                year=year, fund_type=fund_type, category=name,
                building_id=building_id,
                anomaly_type="unbudgeted",
                severity="high",
                description=f"{name}: actual ${actual:,.2f} with no budget allocation.",
                actual_value=actual, expected_value=0, deviation_pct=100
            )
            anomalies.append(a)

        elif budgeted > 0 and actual > budgeted * (1 + _OVERRUN_THRESHOLD):
            deviation = ((actual - budgeted) / budgeted) * 100
            severity = _severity_from_deviation(deviation)
            a = _make_anomaly(
                year=year, fund_type=fund_type, category=name,
                building_id=building_id,
                anomaly_type="budget_overrun",
                severity=severity,
                description=f"{name}: over budget by {deviation:.1f}% (${actual:,.2f} vs ${budgeted:,.2f}).",
                actual_value=actual, expected_value=budgeted, deviation_pct=round(deviation, 2)
            )
            anomalies.append(a)

    # ── 2. Expense spike (3-year rolling average) ──────────────────────────
    category_names = list({c["name"] for c in cats})
    for cat_name in category_names:
        fund_type = next((c["fund_type"] for c in cats if c["name"] == cat_name), "")
        # Get prior year actuals from financial_transactions
        hist_values = []
        for offset in range(1, 4):
            prev_yr = str(int(year) - offset)
            prev_actuals = await aggregate_actual_by_category(prev_yr, fund_type, building_id)
            val = prev_actuals.get(cat_name)
            if val is not None:
                hist_values.append(val)
            else:
                # Fallback to levy_categories budgeted_amount (not actual_amount)
                prev_cat = await db.levy_categories.find_one(
                    {"name": cat_name, "fund_type": fund_type, "building_id": building_id, "year": prev_yr},
                    {"_id": 0, "budgeted_amount": 1}
                )
                if prev_cat:
                    hist_values.append(prev_cat.get("budgeted_amount") or 0)

        if len(hist_values) >= 2:
            avg = sum(hist_values) / len(hist_values)
            current_actual = next(
                (c.get("actual_amount", 0) or 0 for c in cats if c["name"] == cat_name), 0
            )
            if avg > 0 and current_actual > avg * _SPIKE_MULTIPLIER:
                deviation = ((current_actual - avg) / avg) * 100
                a = _make_anomaly(
                    year=year, fund_type=fund_type, category=cat_name,
                    building_id=building_id,
                    anomaly_type="expense_spike",
                    severity=_severity_from_deviation(deviation),
                    description=f"{cat_name}: ${current_actual:,.2f} vs 3yr avg ${avg:,.2f} (+{deviation:.1f}%).",
                    actual_value=current_actual, expected_value=round(avg, 2),
                    deviation_pct=round(deviation, 2)
                )
                anomalies.append(a)

    # ── 3. Owner arrears detection ─────────────────────────────────────────
    today = date.today()
    try:
        levy_year_int = int(year)
    except (ValueError, TypeError):
        levy_year_int = today.year

    is_historical_year = levy_year_int < today.year

    # Fetch levy rates for this year
    levy_rates = await get_levy_rates(year, building_id)
    rate_per_uoe = levy_rates.get("admin_annual", 0) + levy_rates.get("sinking_annual", 0)

    # Fetch settings for grace period and due dates
    settings_doc = await get_general_settings_or_default(building_id, {"_id": 0})
    grace_days = int(settings_doc.get("grace_period_days", 14)) if settings_doc else 14
    due_months = settings_doc.get("levy_due_months", [3, 6, 9, 12]) if settings_doc else [3, 6, 9, 12]
    due_day_type = settings_doc.get("levy_due_day_type", "last") if settings_doc else "last"
    due_day = settings_doc.get("levy_due_day") if settings_doc else None
    custom_dates = settings_doc.get("levy_due_custom_dates", {}) if settings_doc else {}
    total_periods = len(due_months)

    # Compute periods past grace deadline
    computed_dates = compute_period_due_dates(
        levy_year_int, due_months, due_day_type, due_day, total_periods, custom_dates
    )
    periods_past_grace = sum(
        1 for d_str in computed_dates
        if today > date.fromisoformat(d_str) + timedelta(days=grace_days)
    )

    # Confirmed payments per unit for this year
    payments_agg = await db.levy_payments.aggregate([
        {"$match": {"building_id": building_id, "year": year, "status": "confirmed"}},
        {"$group": {"_id": "$unit_number", "paid": {"$sum": "$amount"}}}
    ]).to_list(200)
    paid_by_unit = {r["_id"]: round(r["paid"], 2) for r in payments_agg}

    # Canonical grace-aware per-unit arrears (GAP-FIN-040) for BOTH historical and
    # current years — one source shared with /arrears/detail and the building
    # aggregate. This deliberately replaces the previous current-year branch that
    # reconstructed obligation as `opening + periods_past_grace * period_levy`, the
    # exact pattern CLAUDE.md flags as producing the UA042 $963.31→$2,768 inflation
    # (get_unit_arrears_map trusts net_balance and only excludes the in-grace slice).
    from utils.finance_helpers import get_unit_arrears_map
    arrears_map = await get_unit_arrears_map(building_id, year)
    lot_docs = await db.unit_levy_ledger.find(
        {"year": year, "building_id": building_id},
        {"_id": 0, "unit_number": 1, "lot_number": 1},
    ).to_list(200)
    lot_by_unit = {d.get("unit_number"): d.get("lot_number") for d in lot_docs}

    for unit_num, row in arrears_map.items():
        if not row["in_arrears"]:
            continue
        balance = row["arrears"]
        lot = lot_by_unit.get(unit_num) or unit_num or "Unknown"
        severity = (
            "critical" if balance > 5000
            else ("high" if periods_past_grace >= _ARREARS_QUARTERS else "medium")
        )
        a = _make_anomaly(
            year=year, fund_type=None, category=None, lot_number=lot,
            building_id=building_id,
            anomaly_type="owner_arrears",
            severity=severity,
            description=f"Unit {lot}: ${balance:,.2f} arrears outstanding ({periods_past_grace} periods past grace).",
            actual_value=round(balance, 2), expected_value=0, deviation_pct=None
        )
        anomalies.append(a)

    # ── 4. Interest spike YoY ──────────────────────────────────────────────
    interest_cats = [c for c in cats if "interest" in c.get("name", "").lower()]
    for cat in interest_cats:
        current_val = cat.get("actual_amount", 0) or 0
        prev_yr = str(int(year) - 1)
        prev_actuals = await aggregate_actual_by_category(prev_yr, cat.get("fund_type", ""), building_id)
        prev_val = prev_actuals.get(cat["name"])
        if prev_val is None:
            # Fallback: budgeted_amount from levy_categories
            prev_doc = await db.levy_categories.find_one(
                {"name": cat["name"], "fund_type": cat.get("fund_type", ""),
                 "building_id": building_id, "year": prev_yr},
                {"_id": 0, "budgeted_amount": 1}
            )
            prev_val = (prev_doc.get("budgeted_amount") or 0) if prev_doc else 0

        if prev_val > 0 and current_val > prev_val * (1 + _INTEREST_SPIKE_PCT):
            deviation = ((current_val - prev_val) / prev_val) * 100
            a = _make_anomaly(
                year=year, fund_type=cat.get("fund_type", ""), category=cat["name"],
                building_id=building_id,
                anomaly_type="interest_spike",
                severity="high",
                description=f"Interest category {cat['name']} spiked +{deviation:.1f}% YoY.",
                actual_value=current_val, expected_value=prev_val,
                deviation_pct=round(deviation, 2)
            )
            anomalies.append(a)

    # ── 5. Forecasted deficit ──────────────────────────────────────────────
    levy = await get_levy_fund_data(year, building_id)
    if levy:
        admin = levy.get("admin_fund", {})
        sinking = levy.get("sinking_fund", {})
        for fund_name, fund in [("administrative", admin), ("sinking", sinking)]:
            closing = fund.get("closing_balance", 0) or 0
            if closing < 0:
                a = _make_anomaly(
                    year=year, fund_type=fund_name, category=None,
                    building_id=building_id,
                    anomaly_type="forecasted_deficit",
                    severity="critical",
                    description=f"{fund_name.title()} fund projected closing balance: ${closing:,.2f}.",
                    actual_value=closing, expected_value=0, deviation_pct=None
                )
                anomalies.append(a)

    # ── 6. Maintenance Intelligence Anomalies ───────────────────────────
    # Repeat repairs on same asset (3+ in 12 months)
    year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    repeat_agg = await db.work_orders.aggregate([
        {"$match": {"building_id": building_id, "status": "completed", "created_at": {"$gte": year_ago},
                    "asset_id": {"$ne": None}}},
        {"$group": {"_id": "$asset_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gte": 3}}}
    ]).to_list(100)

    for r in repeat_agg:
        asset_id = r["_id"]
        count = r["count"]
        asset = await db.building_assets.find_one({"building_id": building_id, "id": asset_id}, {"name": 1})
        name = asset.get("name", "Unknown Asset") if asset else "Unknown Asset"

        a = _make_anomaly(
            year=year, anomaly_type="repeat_repairs", severity="medium",
            building_id=building_id,
            description=f"Frequent repairs: {name} has been repaired {count} times in the last 12 months.",
            category=name, actual_value=float(count), expected_value=1.0
        )
        anomalies.append(a)

    # Repair cost vs Replacement cost
    assets = await db.building_assets.find({"building_id": building_id}).to_list(1000)
    for asset in assets:
        replacement_cost = asset.get("replacement_cost_estimate", 0)
        if replacement_cost > 0:
            # Aggregate costs from invoices linked to this asset
            cost_agg = await db.invoices.aggregate([
                {"$match": {"building_id": building_id, "asset_id": asset["id"], "payment_status": "paid"}},
                {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
            ]).to_list(1)

            total_repair_cost = cost_agg[0]["total"] if cost_agg else 0
            if total_repair_cost > replacement_cost * 0.7:
                deviation = (total_repair_cost / replacement_cost) * 100
                a = _make_anomaly(
                    year=year, anomaly_type="repair_cost_high",
                    building_id=building_id,
                    severity="high" if total_repair_cost < replacement_cost else "critical",
                    description=f"Repair costs for {asset['name']} (${total_repair_cost:,.2f}) exceed 70% of replacement cost (${replacement_cost:,.2f}).",
                    category=asset["name"], actual_value=total_repair_cost,
                    expected_value=replacement_cost, deviation_pct=round(deviation, 1)
                )
                anomalies.append(a)

    # Insert all detected anomalies
    if anomalies:
        await db.financial_anomalies.insert_many(anomalies)

    return anomalies


def _make_anomaly(
        year: str,
        anomaly_type: str,
        severity: str,
        description: str,
        building_id: str,
        fund_type=None,
        category=None,
        lot_number=None,
        actual_value=None,
        expected_value=None,
        deviation_pct=None,
) -> dict:
    """Generated function header.

    Function: _make_anomaly
    Path: backend/services/anomaly_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "financial_year": year,
        "fund_type": fund_type,
        "category": category,
        "lot_number": lot_number,
        "anomaly_type": anomaly_type,
        "severity": severity,
        "deviation_pct": deviation_pct,
        "expected_value": expected_value,
        "actual_value": actual_value,
        "description": description,
        "detected_at": _now(),
        "resolved": False,
    }
