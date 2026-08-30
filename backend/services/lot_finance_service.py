"""
Lot Finance Service — computes true cost of ownership per unit per year.

Aggregates:
  - Admin + sinking levy paid (unit_levy_ledger)
  - Council rates + land tax (council_rates collection)
  - Water bills (water_bills collection)
  - Interest / late payment penalties (levy_payments)

Stores in lot_financial_summary collection.
"""
from datetime import datetime, timezone

from typing import List

from database import db


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/lot_finance_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _year_filter(year: str) -> dict:
    """Match legacy `year` and documented `financial_year` fields."""
    return {"$or": [{"year": year}, {"financial_year": year}]}


def _amount(doc: dict, *keys: str) -> float:
    """Generated function header.

    Function: _amount
    Path: backend/services/lot_finance_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for key in keys:
        value = doc.get(key)
        if value is not None:
            return float(value or 0)
    return 0.0


async def compute_true_cost(lot_number: str, year: str, building_id: str) -> dict:
    """
    Compute full financial cost for a unit in a given year. Scoped to building.
    Returns LotFinancialSummary document.
    """
    # ── 1. Levy paid ──────────────────────────────────────────────────────
    ledger = await db.unit_levy_ledger.find_one(
        {
            "building_id": building_id,
            "$and": [
                {"$or": [{"lot_number": lot_number}, {"unit_number": lot_number}]},
                _year_filter(year),
            ],
        },
        {"_id": 0}
    )
    total_admin_paid = 0.0
    total_sinking_paid = 0.0
    net_balance = 0.0

    if ledger:
        net_balance = ledger.get("net_balance", 0) or 0
        # Use ASSESSED levy amounts (what the unit is charged) as the primary basis.
        # Most owners pay externally via DEFT/BPAY so total_paid=0 in the system —
        # using levied amounts gives the correct cost-distribution picture.
        admin_levied = ledger.get("admin_levied", 0) or 0
        sinking_levied = ledger.get("sinking_levied", 0) or 0
        if admin_levied + sinking_levied > 0:
            total_admin_paid = round(admin_levied, 2)
            total_sinking_paid = round(sinking_levied, 2)
        else:
            # Final fallback: split recorded payments proportionally (~77/23%)
            total_paid = ledger.get("total_paid", 0) or 0
            total_admin_paid = round(total_paid * 0.77, 2)
            total_sinking_paid = round(total_paid * 0.23, 2)

    # ── 2. Council rates + land tax ────────────────────────────────────────
    council = await db.council_rates.find_one(
        {
            "building_id": building_id,
            "$and": [
                {"$or": [{"lot_number": lot_number}, {"unit_number": lot_number}]},
                _year_filter(year),
            ],
        },
        {"_id": 0},
    )
    total_council = 0.0
    total_land_tax = 0.0
    if council:
        total_council = _amount(council, "total_rates", "council_rates", "rates_total")
        total_land_tax = _amount(council, "land_tax_total", "total_land_tax")

    # ── 3. Water bills ─────────────────────────────────────────────────────
    water_pipeline = [
        {
            "$match": {
                "building_id": building_id,
                "$and": [
                    {"$or": [{"lot_number": lot_number}, {"unit_number": lot_number}]},
                    _year_filter(year),
                ],
            }
        },
        {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$amount", "$amount_paid"]}}}}
    ]
    water_result = await db.water_bills.aggregate(water_pipeline).to_list(1)
    total_water = round(water_result[0]["total"], 2) if water_result else 0.0

    # ── 4. Interest / late fees ────────────────────────────────────────────
    interest_pipeline = [
        {"$match": {"building_id": building_id, "lot_number": lot_number, "payment_type": "interest",
                    "financial_year": year}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    int_res = await db.levy_payments.aggregate(interest_pipeline).to_list(1)
    total_interest = round(int_res[0]["total"], 2) if int_res else 0.0

    # ── 5. Total cost + per-UOE ────────────────────────────────────────────
    unit = await db.units.find_one(
        {"building_id": building_id, "unit_number": lot_number},
        {"_id": 0, "entitlement": 1, "is_owner_occupied": 1}
    )
    uoe = unit.get("entitlement", 1) if unit else 1
    # Land tax applies to investment/rental properties, not an owner's principal
    # place of residence — mirrors routers/council_rates.py:_unit_is_investment.
    if not unit or unit.get("is_owner_occupied", True):
        total_land_tax = 0.0
    total_cost = round(
        total_admin_paid + total_sinking_paid + total_council +
        total_water + total_land_tax + total_interest,
        2
    )
    cost_per_uoe = round(total_cost / uoe, 2) if uoe > 0 else 0.0
    # Grace-aware arrears (GAP-FIN-040) — same canonical per-unit formula as
    # /arrears/detail and the building aggregate, never a raw net_balance>0 flag.
    from utils.finance_helpers import get_unit_arrears_map
    arrears_map = await get_unit_arrears_map(building_id, year)
    arrears_flag = bool((arrears_map.get(lot_number) or {}).get("in_arrears"))

    # ── 6. Risk flag: cost > 1.5x avg for their UOE share ────────────────
    risk_flag = False  # set by compute_building_cost_distribution

    doc = {
        "lot_number": lot_number,
        "building_id": building_id,
        "financial_year": year,
        "total_admin_paid": total_admin_paid,
        "total_sinking_paid": total_sinking_paid,
        "special_levies": 0.0,
        "total_council": total_council,
        "total_water": total_water,
        "total_land_tax": total_land_tax,
        "total_interest": total_interest,
        "total_cost": total_cost,
        "cost_per_uoe": cost_per_uoe,
        "arrears_flag": arrears_flag,
        "risk_flag": risk_flag,
        "created_at": _now(),
    }

    await db.lot_financial_summary.update_one(
        {"building_id": building_id, "lot_number": lot_number, "financial_year": year},
        {"$set": doc},
        upsert=True
    )
    return doc


async def compute_building_cost_distribution(year: str, building_id: str) -> List[dict]:
    """
    Compute true cost for every unit and return sorted distribution. Scoped to building.
    Uses batch fetching and aggregation to eliminate N+1 patterns. - Bolt ⚡
    """
    import asyncio
    # Performance Optimization⚡: Perform all DB fetches in parallel
    tasks = [
        # 1. Fetch all units with entitlements
        db.units.find(
            {"building_id": building_id},
            {"_id": 0, "unit_number": 1, "entitlement": 1, "is_owner_occupied": 1}
        ).to_list(200),
        # 2. Fetch all ledger entries for the year
        db.unit_levy_ledger.find(
            {"building_id": building_id, **_year_filter(year)},
            {"_id": 0}
        ).to_list(200),
        # 3. Fetch all council rates for the year
        db.council_rates.find({"building_id": building_id, **_year_filter(year)}, {"_id": 0}).to_list(200),
        # 4. Aggregate water bills for the year
        db.water_bills.aggregate([
            {"$match": {"building_id": building_id, **_year_filter(year)}},
            {
                "$group": {
                    "_id": {"$ifNull": ["$unit_number", "$lot_number"]},
                    "total": {"$sum": {"$ifNull": ["$amount", "$amount_paid"]}},
                }
            }
        ]).to_list(200),
        # 5. Aggregate interest payments for the year
        db.levy_payments.aggregate([
            {"$match": {"building_id": building_id, "payment_type": "interest", "financial_year": year}},
            {"$group": {"_id": "$lot_number", "total": {"$sum": "$amount"}}}
        ]).to_list(200)
    ]

    try:
        (units, ledgers, rates,
         water_results, interest_results) = await asyncio.gather(*tasks)
    except Exception as e:
        raise RuntimeError(
            f"Failed to fetch building-wide financial data: {str(e)}"
        ) from e

    # Grace-aware per-unit arrears map fetched ONCE for the whole building
    # (GAP-FIN-040) — canonical, no N+1, consistent with /arrears/detail.
    from utils.finance_helpers import get_unit_arrears_map
    arrears_map = await get_unit_arrears_map(building_id, year)

    # Performance Optimization⚡: Create in-memory hash maps for O(1) lookup
    ledger_map = {}
    for ldg in ledgers:
        key = ldg.get("lot_number") or ldg.get("unit_number")
        if key:
            ledger_map[str(key)] = ldg
    rates_map = {}
    for r in rates:
        # Support both lot_number and unit_number keys as in compute_true_cost
        key = r.get("lot_number") or r.get("unit_number")
        if key:
            rates_map[str(key)] = r

    water_map = {w["_id"]: round(w.get("total", 0), 2)
                 for w in water_results if w.get("_id") and "total" in w}
    interest_map = {i["_id"]: round(i.get("total", 0), 2)
                    for i in interest_results if i.get("_id") and "total" in i}

    summaries = []
    now_ts = _now()

    # Performance Optimization⚡: Process all units in-memory without further DB calls
    for u in units:
        lot = u.get("unit_number", "")
        if not lot:
            continue

        uoe = u.get("entitlement", 1) or 1

        # 1. Levy amounts — use assessed (levied) figures, not payment-recorded ones.
        # Most owners pay via DEFT/BPAY externally, so total_paid=0 in the system.
        ledger = ledger_map.get(lot, {})
        total_admin_paid = 0.0
        total_sinking_paid = 0.0
        net_balance = 0.0

        if ledger:
            net_balance = ledger.get("net_balance", 0) or 0
            admin_levied = ledger.get("admin_levied", 0) or 0
            sinking_levied = ledger.get("sinking_levied", 0) or 0

            if admin_levied + sinking_levied > 0:
                total_admin_paid = round(admin_levied, 2)
                total_sinking_paid = round(sinking_levied, 2)
            else:
                total_paid = ledger.get("total_paid", 0) or 0
                total_admin_paid = round(total_paid * 0.77, 2)
                total_sinking_paid = round(total_paid * 0.23, 2)

        # 2. Council rates
        council = rates_map.get(lot, {})
        total_council = _amount(council, "total_rates", "council_rates", "rates_total")
        total_land_tax = _amount(council, "land_tax_total", "total_land_tax")
        # Land tax applies to investment/rental properties, not an owner's principal
        # place of residence — mirrors routers/council_rates.py:_unit_is_investment.
        if u.get("is_owner_occupied", True):
            total_land_tax = 0.0

        # 3. Water and Interest
        total_water = water_map.get(lot, 0.0)
        total_interest = interest_map.get(lot, 0.0)

        # 4. Totals
        total_cost = round(
            total_admin_paid + total_sinking_paid + total_council +
            total_water + total_land_tax + total_interest,
            2
        )
        cost_per_uoe = round(total_cost / uoe, 2) if uoe > 0 else 0.0
        arrears_flag = bool((arrears_map.get(lot) or {}).get("in_arrears"))

        summaries.append({
            "lot_number": lot,
            "building_id": building_id,
            "financial_year": year,
            "total_admin_paid": total_admin_paid,
            "total_sinking_paid": total_sinking_paid,
            "special_levies": 0.0,
            "total_council": total_council,
            "total_water": total_water,
            "total_land_tax": total_land_tax,
            "total_interest": total_interest,
            "total_cost": total_cost,
            "cost_per_uoe": cost_per_uoe,
            "arrears_flag": arrears_flag,
            "risk_flag": False,  # Updated below
            "created_at": now_ts,
        })

    # Compute average cost_per_uoe for risk flag calculation
    valid = [s for s in summaries if s["cost_per_uoe"] > 0]
    avg_cost_per_uoe = (sum(s["cost_per_uoe"] for s in valid) / len(valid)
                        if valid else 0)

    # Performance Optimization⚡: Prepare bulk operations to update summaries
    from pymongo import ReplaceOne
    bulk_ops = []

    for s in summaries:
        if avg_cost_per_uoe > 0:
            s["risk_flag"] = s["cost_per_uoe"] > avg_cost_per_uoe * 1.5
        else:
            s["risk_flag"] = False
        bulk_ops.append(
            ReplaceOne(
                {"building_id": building_id, "lot_number": s["lot_number"], "financial_year": year},
                s,
                upsert=True
            )
        )

    if bulk_ops:
        try:
            await db.lot_financial_summary.bulk_write(bulk_ops)
        except Exception as e:
            raise RuntimeError(
                f"Failed to persist financial summaries: {str(e)}"
            ) from e

    return sorted(summaries, key=lambda x: x["total_cost"], reverse=True)
