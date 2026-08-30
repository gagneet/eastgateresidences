# @featuretrace:levy — Owner-facing levy breakdown/balance and building-health service layer.
# Layer: service
# Data flow: routers/owner_finance.py -> get_levy_breakdown()/get_savings_per_lot() ->
#            unit_levy_ledger + units + levy_categories + savings_events (building-scoped).
# Related: backend/routers/owner_finance.py
#           frontend/src/pages/dashboard/MyFinancesPage.jsx
# Collection: unit_levy_ledger, units, levy_categories, savings_events
# Tests: tests/backend/test_owner_finance_service.py
"""
Owner financial dashboard data service.

All DB queries pass `building_id` explicitly in filters — the canonical pattern
used throughout this codebase (see levy_fairness_service.py).  Never rely solely
on request-context injection for tenant-scoped collections; the explicit filter
is the single source of truth for isolation correctness.
"""
from datetime import datetime, timezone

from database import db
from utils.finance_helpers import get_levy_rates, compute_period_installment_amounts

# Mirrors the frequency map already used in routers/finance.py (get_levy_status,
# get_unit_levy_ledger) — kept local rather than a new shared import to avoid
# widening this fix's blast radius; if a third call site needs it, extract then.
_FREQUENCY_TO_PERIODS = {"monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1}


async def get_levy_breakdown(unit_number: str, building_id: str) -> dict:
    """
    Returns per-category levy breakdown for the logged-in owner.

    Algorithm:
      1. Look up the unit's UOE in `units` (scoped to building_id).
      2. Sum all UOE across the building to compute the owner's proportional share.
      3. Find the most recent year's budgets from `budgets`.
         - Try current financial year first (e.g. "2026-2027"), then the two
           preceding years, then fall back to any year available in the DB.
         - Budget years in this DB are stored as "YYYY-YYYY" (e.g. "2025-2026").
      4. Compute the current year's canonical per-period levy amount the SAME way
         routers/finance.py::get_levy_status() does (Levy Payments page): rate-per-UOE
         x UOE x building's actual levy_collection_frequency, split into exact
         instalments via compute_period_installment_amounts(). This is the
         building's real quarterly (or monthly/half-yearly) amount for the CURRENT
         period, independent of how much of the year has elapsed.
      5. Return quarterly_levy = rate-based canonical figure (step 4)
                                 OR  units.period_levy
                                 OR  sum(budget-derived per-category amounts).

    GAP-FIN-033 Part B1 fix (2026-08-02): previously step 4 preferred
    `unit_levy_ledger.period_levy` (never written by any live path — only a
    one-time seed script) falling back to `unit_levy_ledger.total_levied / 4`.
    `total_levied` is a YTD-to-date figure, not the annual total (see CLAUDE.md's
    "levy_income is YTD collected, not an annual figure" rule, and
    get_levy_proposed_amounts()'s docstring) — dividing a YTD total by the full
    period count under-reports the true per-period amount by roughly the
    fraction of the year not yet elapsed. Live-confirmed for East Gate/TH087:
    total_levied=$3,545.02 (Q1+Q2 only) / 4 = $886.26, exactly half the correct
    $1,772.51 quarterly figure. The ledger-based tier is removed entirely rather
    than patched, since there is no reliable way to recover "how many periods
    this total_levied covers" from the ledger document alone.
    """

    # ── 1. Unit lookup ────────────────────────────────────────────────────────
    unit = await db.units.find_one(
        # Explicit building_id — never trust context alone for tenant isolation.
        {"unit_number": unit_number, "building_id": building_id},
        {"_id": 0, "entitlement": 1, "lot_entitlement": 1, "period_levy": 1},
    )
    if not unit:
        return {"error": "Unit not found"}

    your_uoe: float = float(unit.get("entitlement") or unit.get("lot_entitlement") or 1)

    # ── 2. Total UOE for the building ─────────────────────────────────────────
    # Aggregate pipeline: wrapper prepends {"$match": {"building_id": bid}}
    # automatically, so the result is already building-scoped.
    agg = await db.units.aggregate([
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": None, "total_uoe": {"$sum": "$entitlement"}}},
    ]).to_list(1)
    total_uoe: float = float(agg[0]["total_uoe"]) if agg else 1.0
    uoe_share: float = your_uoe / total_uoe if total_uoe > 0 else 0.0

    # ── 3. Budget categories (most recent available year) ─────────────────────
    # levy_categories uses single-year format "YYYY" (e.g. "2026"), not "YYYY-YYYY".
    current_year = datetime.now(timezone.utc).year
    candidate_years = [str(current_year), str(current_year - 1), str(current_year - 2)]
    levy_cats: list = []
    used_year: str | None = None

    for yr in candidate_years:
        levy_cats = await db.levy_categories.find(
            {"year": yr, "building_id": building_id},
            {"_id": 0, "name": 1, "budgeted_amount": 1, "fund_type": 1},
        ).to_list(200)
        if levy_cats:
            used_year = yr
            break

    # Last-resort: use whatever year exists in levy_categories for this building.
    if not levy_cats:
        any_doc = await db.levy_categories.find_one(
            {"building_id": building_id},
            {"_id": 0, "year": 1},
        )
        if any_doc:
            fallback_yr = any_doc["year"]
            levy_cats = await db.levy_categories.find(
                {"year": fallback_yr, "building_id": building_id},
                {"_id": 0, "name": 1, "budgeted_amount": 1, "fund_type": 1},
            ).to_list(200)
            used_year = fallback_yr

    # Build per-category breakdown.
    categories: list = []
    total_quarterly: float = 0.0
    for b in levy_cats:
        annual = float(b.get("budgeted_amount") or 0)
        your_quarterly = round((annual * uoe_share) / 4, 2)
        total_quarterly += your_quarterly
        categories.append({
            "name": b.get("name", ""),
            "fund_type": b.get("fund_type", "admin"),
            "annual_budget": annual,
            "your_quarterly": your_quarterly,
        })

    categories.sort(key=lambda x: x["your_quarterly"], reverse=True)
    for cat in categories:
        cat["pct"] = round(
            cat["your_quarterly"] / total_quarterly * 100, 1
        ) if total_quarterly > 0 else 0

    # ── 4. Live ledger row (needed below only for step 6's net_balance) ───────
    # Prefer the CURRENT calendar year's ledger row. A plain "sort by year desc,
    # take first" picks whatever row sorts highest as a string — including a
    # still-forming next-FY row created ahead of the FY start (this happened in
    # production: a "2027" row existed while "2026" was still the current year).
    # Never select a future year; fall back to the most recent PAST/current year
    # when no current-year row exists yet.
    ledger_docs = await db.unit_levy_ledger.find(
        {
            "unit_number": unit_number,
            "building_id": building_id,
            "year": {"$lte": str(current_year)},
        },
    ).sort("year", -1).to_list(1)

    ledger: dict = ledger_docs[0] if ledger_docs else {}

    # ── 4b. Canonical rate-based per-period levy — the SAME computation
    # get_levy_status() (routers/finance.py, Levy Payments page) uses, so the two
    # owner-facing pages never disagree. See this function's docstring for why the
    # unit_levy_ledger-derived tier (total_levied is YTD, not annual) was removed.
    rate_period_levy: float = 0.0
    try:
        settings_doc = await db.settings.find_one(
            {
                "building_id": building_id,
                "$or": [{"type": {"$exists": False}}, {"type": None}, {"type": "general"}],
            },
            {"_id": 0, "levy_collection_frequency": 1},
            sort=[("updated_at", -1), ("created_at", -1), ("_id", -1)],
        )
        levy_collection_frequency = (settings_doc or {}).get("levy_collection_frequency", "quarterly")
        num_periods = _FREQUENCY_TO_PERIODS.get(levy_collection_frequency, 4)
        rates = await get_levy_rates(str(current_year), building_id)
        admin_annual = float(rates.get("admin_annual", 0) or 0) * your_uoe
        sinking_annual = float(rates.get("sinking_annual", 0) or 0) * your_uoe
        total_annual = round(admin_annual + sinking_annual, 2)
        if num_periods > 0:
            period_amounts = compute_period_installment_amounts(total_annual, num_periods)
            rate_period_levy = period_amounts[0] if period_amounts else round(total_annual / num_periods, 2)
    except (KeyError, TypeError, ValueError):
        # A building mid-onboarding (no annual_levies doc yet for this year) is a
        # real, expected case, not a fabricated one -- fall through to the next tier.
        rate_period_levy = 0.0

    # ── 5. Resolve final quarterly_levy (priority order) ─────────────────────
    # rate_schedule (canonical, matches Levy Payments page) > units.period_levy
    # > budget-derived sum. quarterly_levy_source records which tier fired
    # (GAP-FIN-014) so the frontend can label non-canonical figures rather than
    # presenting every tier as equally authoritative.
    if rate_period_levy:
        quarterly_levy = rate_period_levy
        quarterly_levy_source = "rate_schedule"
    elif unit.get("period_levy"):
        quarterly_levy = float(unit["period_levy"])
        quarterly_levy_source = "unit_master"
    else:
        quarterly_levy = total_quarterly
        quarterly_levy_source = "budget_derived"

    # ── 6. Live balance from unit_levy_ledger (most recent year) ─────────────
    # units.balance_owing / balance_credit are seeded once from 2025 closing
    # data and are never updated — do NOT use them for current balance display.
    # Use the ledger's net_balance (positive = owes, negative = credit).
    _live_net = float(ledger.get("net_balance", 0))
    live_balance_owing = round(max(0.0, _live_net), 2)
    live_balance_credit = round(abs(min(0.0, _live_net)), 2)

    return {
        "unit_number": unit_number,
        "your_uoe": your_uoe,
        "total_uoe": total_uoe,
        "uoe_share_pct": round(uoe_share * 100, 3),
        "quarterly_levy": quarterly_levy,
        "quarterly_levy_source": quarterly_levy_source,
        "categories": categories,
        "balance_owing": live_balance_owing,
        "balance_credit": live_balance_credit,
        "ledger_year": ledger.get("year"),
        "data_year": used_year,
    }


async def get_savings_per_lot(unit_number: str, building_id: str) -> dict:
    """
    Returns the owner's personalised share of building savings YTD.
    your_share = total_saved_ytd * (your_uoe / total_uoe)
    """

    unit = await db.units.find_one(
        {"unit_number": unit_number, "building_id": building_id},
        {"_id": 0, "entitlement": 1, "lot_entitlement": 1},
    )
    if not unit:
        return {"error": "Unit not found", "your_share_cents": 0, "total_saved_ytd_cents": 0}

    your_uoe: float = float(unit.get("entitlement") or unit.get("lot_entitlement") or 1)

    # Building-scoped total UOE.
    agg = await db.units.aggregate([
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": None, "total_uoe": {"$sum": "$entitlement"}}},
    ]).to_list(1)
    total_uoe: float = float(agg[0]["total_uoe"]) if agg else 1.0

    current_year = datetime.now(timezone.utc).year
    # Savings events use the same "YYYY-YYYY" year format as budgets.
    fy = f"{current_year}-{current_year + 1}"

    savings_agg = await db.savings_events.aggregate([
        {
            "$match": {
                "building_id": building_id,
                "financial_year": fy,
                "verified": True,
                "is_test_data": {"$ne": True},
            }
        },
        {"$group": {"_id": None, "total": {"$sum": "$amount_saved_cents"}}},
    ]).to_list(1)
    total_saved_ytd_cents: int = savings_agg[0]["total"] if savings_agg else 0

    # Lot count scoped to this building only.
    lot_count: int = await db.units.count_documents(
        {"building_id": building_id, "is_test_data": {"$ne": True}}
    )

    your_share_cents = (
        round(total_saved_ytd_cents * your_uoe / total_uoe) if total_uoe > 0 else 0
    )
    levy_reduction_cents = round(your_share_cents / 4)
    average_per_lot_cents = (
        round(total_saved_ytd_cents / lot_count)
        if total_saved_ytd_cents > 0 and lot_count > 0
        else 0
    )

    return {
        "total_saved_ytd_cents": total_saved_ytd_cents,
        "average_per_lot_cents": average_per_lot_cents,
        "your_share_cents": your_share_cents,
        "levy_reduction_equivalent_cents": levy_reduction_cents,
        "your_uoe": your_uoe,
        "total_uoe": total_uoe,
    }
