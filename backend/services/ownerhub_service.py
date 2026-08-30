"""
OwnerHub Service — property health scoring, True Cost of Ownership (TCO),
weekly radar and snapshot persistence for investment property owners.
"""
import logging
from datetime import datetime, timezone, date

import asyncio
import re
from typing import Optional

from database import db
from request_context import get_ctx_building_id
from services.owner_service import get_owner_info
from services.tenancy_service import (
    compute_arrears,
    get_active_tenancy,
    get_inspections_for_property,
)
from utils.finance_helpers import get_levy_rates
from utils.helpers import get_current_utc_iso

logger = logging.getLogger(__name__)


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/ownerhub_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return get_current_utc_iso()


# ── Health Score ───────────────────────────────────────────────────────────

async def compute_property_health_score(
        property_id: str,
        prop_doc: Optional[dict] = None,
        active_tenancy: Optional[dict] = None,
        res_this_month: Optional[list] = None,
        res_avg: Optional[list] = None,
        open_requests: Optional[list] = None,
        inspections: Optional[list] = None
) -> dict:
    """
    Compute complex property health score.
    Performance Optimization⚡: Parallelized independent component fetches and added
    support for pre-fetched data to eliminate redundant DB round-trips.
    """
    prop = prop_doc or await db.owner_properties.find_one({"_id": __oid(property_id)})
    if not prop:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Property not found")

    risk_flags = []

    # Prepare time ranges for maintenance aggregation
    now_dt = datetime.now(timezone.utc)
    month_start = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    _m6 = now_dt.month - 6
    if _m6 > 0:
        six_months_ago = now_dt.replace(month=_m6, day=1)
    else:
        six_months_ago = now_dt.replace(year=now_dt.year - 1, month=_m6 + 12, day=1)

    # Performance Optimization⚡: Only fetch components that weren't provided by the caller
    tasks = []
    task_map = {}

    if active_tenancy is None:
        task_map["active_tenancy"] = len(tasks)
        tasks.append(get_active_tenancy(property_id))

    if res_this_month is None:
        task_map["res_this_month"] = len(tasks)
        tasks.append(db.tenant_maintenance_requests.aggregate([
            {"$match": {"property_id": property_id, "actual_cost": {"$gt": 0},
                        "updated_at": {"$gte": month_start.isoformat()}}},
            {"$group": {"_id": None, "total": {"$sum": "$actual_cost"}}}
        ]).to_list(1))

    if res_avg is None:
        task_map["res_avg"] = len(tasks)
        tasks.append(db.tenant_maintenance_requests.aggregate([
            {"$match": {"property_id": property_id, "actual_cost": {"$gt": 0},
                        "updated_at": {"$gte": six_months_ago.isoformat()}}},
            {"$group": {"_id": None, "total": {"$sum": "$actual_cost"}}}
        ]).to_list(1))

    if open_requests is None:
        task_map["open_requests"] = len(tasks)
        tasks.append(db.tenant_maintenance_requests.find(
            {"property_id": property_id, "status": {"$nin": ["completed", "closed", "rejected"]}}
        ).to_list(200))

    if inspections is None:
        task_map["inspections"] = len(tasks)
        tasks.append(get_inspections_for_property(property_id))

    if tasks:
        results = await asyncio.gather(*tasks)
        if "active_tenancy" in task_map: active_tenancy = results[task_map["active_tenancy"]]
        if "res_this_month" in task_map: res_this_month = results[task_map["res_this_month"]]
        if "res_avg" in task_map: res_avg = results[task_map["res_avg"]]
        if "open_requests" in task_map: open_requests = results[task_map["open_requests"]]
        if "inspections" in task_map: inspections = results[task_map["inspections"]]

    # ── 1. Income stability (25%) ──────────────────────────────────────────
    income_stability_score = 100.0
    if active_tenancy:
        arrears = await compute_arrears(active_tenancy["id"], tenancy_doc=active_tenancy)
        stage = arrears.get("arrears_stage", "ok")
        stage_deductions = {"ok": 0, "reminder": 20, "warning": 40, "breach": 60, "tribunal": 80}
        income_stability_score = max(0, 100 - stage_deductions.get(stage, 0))
        if stage != "ok":
            risk_flags.append(f"Rent arrears: {stage} ({arrears['arrears_days']} days)")
    else:
        income_stability_score = 50.0  # Vacant = moderate risk
        risk_flags.append("Property is vacant")

    # ── 2. Expense volatility (15%) ────────────────────────────────────────
    cost_this_month = res_this_month[0]["total"] if res_this_month else 0.0
    avg_monthly = (res_avg[0]["total"] / 6.0) if res_avg else 0.0

    if avg_monthly > 0 and cost_this_month > avg_monthly * 1.5:
        ratio = cost_this_month / avg_monthly
        expense_volatility_score = max(0.0, 100 - (ratio - 1.5) * 40)
        risk_flags.append(f"Maintenance costs {ratio:.1f}x above average this month")
    else:
        expense_volatility_score = 100.0

    # ── 3. Maintenance backlog (20%) ────────────────────────────────────────
    backlog_deduction = 0
    for req in open_requests:
        urgency = req.get("urgency", "routine")
        if urgency == "emergency":
            backlog_deduction += 40
            risk_flags.append(f"Open emergency maintenance: {req.get('issue_title', '')}")
        elif urgency == "urgent":
            backlog_deduction += 25
        else:
            backlog_deduction += 15
    maintenance_backlog_score = max(0.0, 100 - backlog_deduction)

    # ── 4. Tenant stability (15%) ────────────────────────────────────────────
    if active_tenancy:
        status = active_tenancy.get("status", "")
        if status == "periodic":
            tenant_stability_score = 50.0
            risk_flags.append("Tenancy is periodic (month-to-month)")
        else:
            try:
                lease_end = date.fromisoformat(active_tenancy.get("lease_end", "")[:10])
                days_remaining = (lease_end - date.today()).days
                months_remaining = days_remaining / 30
                if months_remaining >= 6:
                    tenant_stability_score = 100.0
                elif months_remaining >= 3:
                    months_below = 6 - months_remaining
                    tenant_stability_score = max(0, 100 - (months_below * 10))
                else:
                    tenant_stability_score = max(0, 100 - ((6 - months_remaining) * 10))
                if days_remaining < 90:
                    risk_flags.append(f"Lease expires in {days_remaining} days")
            except Exception:
                tenant_stability_score = 50.0
    else:
        tenant_stability_score = 20.0

    # ── 5. Capital risk (15%) ────────────────────────────────────────────────
    purchase_date_str = prop.get("purchase_date", "")
    capital_risk_score = 60.0  # default unknown
    if purchase_date_str:
        try:
            purchase_date = date.fromisoformat(purchase_date_str[:10])
            age_years = (date.today() - purchase_date).days / 365.25
            if age_years < 5:
                capital_risk_score = 100.0
            elif age_years < 10:
                capital_risk_score = 80.0
            elif age_years < 15:
                capital_risk_score = 60.0
            elif age_years < 20:
                capital_risk_score = 40.0
            else:
                capital_risk_score = 20.0
                risk_flags.append(f"Property is {age_years:.0f} years old — elevated capital risk")
        except Exception:
            capital_risk_score = 60.0

    # ── 6. Compliance (10%) ─────────────────────────────────────────────────
    # Performance Optimization⚡: Removed redundant fetch, using 'inspections' from parallel gather results.
    completed = [
        i for i in inspections
        if i.get("status") == "completed" and i.get("completed_date")
    ]
    if completed:
        completed.sort(key=lambda x: x.get("completed_date", ""), reverse=True)
        last_completed_str = completed[0].get("completed_date", "")
        try:
            last_completed = date.fromisoformat(last_completed_str[:10])
            days_since = (date.today() - last_completed).days
            if days_since < 90:
                compliance_score = 100.0
            elif days_since < 180:
                compliance_score = 70.0
                risk_flags.append("Inspection overdue (>3 months)")
            else:
                compliance_score = 40.0
                risk_flags.append("Inspection overdue (>6 months)")
        except Exception:
            compliance_score = 40.0
    else:
        compliance_score = 20.0
        risk_flags.append("No inspections on record")

    # ── Composite ────────────────────────────────────────────────────────────
    health_score = round(
        0.25 * income_stability_score +
        0.15 * expense_volatility_score +
        0.20 * maintenance_backlog_score +
        0.15 * tenant_stability_score +
        0.15 * capital_risk_score +
        0.10 * compliance_score,
        1
    )

    if health_score >= 75:
        risk_level = "healthy"
    elif health_score >= 50:
        risk_level = "moderate"
    elif health_score >= 30:
        risk_level = "high"
    else:
        risk_level = "critical"

    return {
        "property_id": property_id,
        "computed_at": _now(),
        "income_stability_score": income_stability_score,
        "expense_volatility_score": expense_volatility_score,
        "maintenance_backlog_score": maintenance_backlog_score,
        "tenant_stability_score": tenant_stability_score,
        "capital_risk_score": capital_risk_score,
        "compliance_score": compliance_score,
        "health_score": health_score,
        "risk_level": risk_level,
        "risk_flags": risk_flags,
    }


# ── True Cost of Ownership ─────────────────────────────────────────────────

async def compute_tco(property_id: str, inputs: dict) -> dict:
    """Generated function header.

    Function: compute_tco
    Path: backend/services/ownerhub_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    prop = await db.owner_properties.find_one({"_id": __oid(property_id)})
    if not prop:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Property not found")

    year = inputs.get("year", str(datetime.now(timezone.utc).year))
    weekly_rent = prop.get("weekly_rent") or inputs.get("weekly_rent", 0.0)
    current_value = prop.get("current_value") or inputs.get("current_value", 0.0)
    vacancy_weeks = inputs.get("vacancy_weeks", 2)

    # ── Income ────────────────────────────────────────────────────────────
    if inputs.get("annual_rental_income"):
        gross_rental_income = float(inputs["annual_rental_income"])
    else:
        # Attempt to sum from rent_transactions for the year
        active = await get_active_tenancy(property_id)
        if active:
            year_prefix = year[:4] if "-" in year else year
            pipeline = [
                {"$match": {
                    "tenancy_id": active["id"],
                    "type": "rent",
                    "status": "paid",
                    "transaction_date": {"$regex": f"^{year_prefix}"}
                }},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]
            res = await db.rent_transactions.aggregate(pipeline).to_list(1)
            gross_rental_income = res[0]["total"] if res else (weekly_rent * 52)
        else:
            gross_rental_income = weekly_rent * 52

    vacancy_loss = round(weekly_rent * vacancy_weeks, 2)
    net_rental_income = round(gross_rental_income - vacancy_loss, 2)

    # ── Strata levies ────────────────────────────────────────────────────
    # Same fix as compute_unit_tco(): prefer the committed ANNUAL rate from
    # annual_levies (matches the main Dashboard) over the unit_levy_ledger row,
    # which is only updated incrementally as quarters are actually invoiced and
    # therefore under-reports the annual figure for any partially-billed year.
    strata_levies = 0.0
    eastgate_unit = prop.get("eastgate_unit_number")
    unit_doc = None
    if eastgate_unit:
        unit_doc = await db.units.find_one(
            {"unit_number": eastgate_unit}, {"_id": 0, "entitlement": 1, "building_id": 1}
        )
        if unit_doc:
            building_id = get_ctx_building_id() or unit_doc.get("building_id", "")
            year_str_prop = year[:4] if "-" in year else year
            levy_rates = await get_levy_rates(year_str_prop, building_id)
            entitlement_prop = float(unit_doc.get("entitlement", 1) or 1)
            rate_based_levies = round(
                (levy_rates.get("admin_annual", 0.0) + levy_rates.get("sinking_annual", 0.0)) * entitlement_prop,
                2,
            )
            if rate_based_levies:
                strata_levies = rate_based_levies
        if not strata_levies:
            ledger = await db.unit_levy_ledger.find_one(
                {"lot_number": eastgate_unit, "year": year}, {"_id": 0}
            )
            if ledger:
                strata_levies = round(
                    (ledger.get("admin_levied", 0) or 0) +
                    (ledger.get("sinking_levied", 0) or 0),
                    2
                )
    if strata_levies == 0.0:
        strata_levies = float(inputs.get("strata_levies", 0.0))

    # ── Other fixed costs ────────────────────────────────────────────────
    insurance = float(inputs.get("annual_insurance", 0.0) or 0.0)
    council_rates = float(inputs.get("council_rates", 0.0) or 0.0)
    water_rates = float(inputs.get("water_rates", 0.0) or 0.0)

    # ── Property management fee ─────────────────────────────────────────
    mgmt_pct = inputs.get("property_mgmt_fee_pct")
    property_mgmt_fee = round((net_rental_income * mgmt_pct / 100), 2) if mgmt_pct else 0.0

    # ── Maintenance costs (actual from requests) ─────────────────────────
    year_prefix = year[:4] if "-" in year else year
    maint_pipeline = [
        {"$match": {
            "property_id": property_id,
            "actual_cost": {"$gt": 0},
            "completed_date": {"$regex": f"^{year_prefix}"}
        }},
        {"$group": {"_id": None, "total": {"$sum": "$actual_cost"}}}
    ]
    maint_res = await db.tenant_maintenance_requests.aggregate(maint_pipeline).to_list(1)
    maintenance_costs = round(maint_res[0]["total"] if maint_res else 0.0, 2)

    # ── Capital replacement (unit share from sinking fund forecast) ──
    # cap_works is initialised to [] so the 10-year loop that references it
    # doesn't hit a NameError when eastgate_unit is absent.
    # NOTE: capital_replacement_cost is informational only — excluded from total_costs
    # below because the sinking levy (already inside strata_levies) funds the same
    # forecast capital works; adding it again would double-count that spend.
    # Reuses unit_doc fetched above (strata-levies section) to avoid a duplicate query.
    cap_works = []
    capital_replacement_cost = 0.0
    if eastgate_unit:
        if unit_doc:
            total_entitlement = await db.units.aggregate([
                {"$group": {"_id": None, "total": {"$sum": "$entitlement"}}}
            ]).to_list(1)
            tot_ent = total_entitlement[0]["total"] if total_entitlement else 1
            share = (unit_doc.get("entitlement", 1) / tot_ent) if tot_ent > 0 else 0

            # Sinking fund forecast for this year (collection was previously called capital_works)
            cap_works = await db.capital_works.find(
                {"forecast_year": int(year_prefix), "status": {"$ne": "completed"}},
                {"_id": 0, "estimated_cost": 1}
            ).to_list(100)
            building_cap_total = sum(c.get("estimated_cost", 0) for c in cap_works)
            capital_replacement_cost = round(building_cap_total * share, 2)

    # ── Financing ────────────────────────────────────────────────────────
    mortgage_balance = float(inputs.get("mortgage_balance", 0.0) or 0.0)
    interest_rate = float(inputs.get("interest_rate", 0.0) or 0.0)
    mortgage_interest = round(mortgage_balance * (interest_rate / 100), 2) if mortgage_balance else 0.0

    # capital_replacement_cost is intentionally excluded — see NOTE above (double-counts the sinking levy).
    total_costs = round(
        strata_levies + insurance + council_rates + water_rates +
        property_mgmt_fee + maintenance_costs +
        mortgage_interest,
        2
    )
    net_cashflow = round(net_rental_income - total_costs, 2)
    net_yield_pct = round(net_cashflow / current_value * 100, 2) if current_value else None

    # ── Ownership score ──────────────────────────────────────────────────
    if net_yield_pct is None:
        yield_score = 50.0
    elif net_yield_pct >= 5:
        yield_score = 100.0
    elif net_yield_pct >= 4:
        yield_score = 80.0
    elif net_yield_pct >= 3:
        yield_score = 60.0
    elif net_yield_pct >= 2:
        yield_score = 40.0
    else:
        yield_score = 20.0

    # Blend with health score
    try:
        health_data = await compute_property_health_score(property_id)
        health_score = health_data.get("health_score", 50.0)
    except Exception:
        health_score = 50.0
    ownership_score = round((yield_score * 0.6) + (health_score * 0.4), 1)

    # ── 10-year projection ───────────────────────────────────────────────
    ten_year = []
    proj_net_rent = net_rental_income
    proj_costs = total_costs - mortgage_interest  # exclude financing from growth
    proj_mortgage = mortgage_interest
    for yr in range(1, 11):
        proj_net_rent = round(proj_net_rent * 1.03, 2)
        proj_costs = round(proj_costs * 1.03, 2)

        # Subtract any capital works for this year
        cap_yr = int(year_prefix) + yr
        cap_works_yr = await db.capital_works.find(
            {"forecast_year": cap_yr, "status": {"$ne": "completed"}},
            {"_id": 0, "estimated_cost": 1}
        ).to_list(100)
        cap_total_yr = sum(c.get("estimated_cost", 0) for c in cap_works_yr)
        cap_yr_unit = 0.0
        base_cap_total = sum(c.get("estimated_cost", 0) for c in cap_works)
        if eastgate_unit and capital_replacement_cost > 0 and base_cap_total > 0:
            share_ratio = capital_replacement_cost / base_cap_total
            cap_yr_unit = round(cap_total_yr * share_ratio, 2)

        yr_net = round(proj_net_rent - proj_costs - proj_mortgage - cap_yr_unit, 2)
        ten_year.append({"year": cap_yr, "net_cashflow": yr_net})

    return {
        "property_id": property_id,
        "year": year,
        "gross_rental_income": round(gross_rental_income, 2),
        "vacancy_loss": vacancy_loss,
        "net_rental_income": net_rental_income,
        "strata_levies": strata_levies,
        "insurance": insurance,
        "council_rates": council_rates,
        "water_rates": water_rates,
        "property_mgmt_fee": property_mgmt_fee,
        "maintenance_costs": maintenance_costs,
        "capital_replacement_cost": capital_replacement_cost,
        "mortgage_interest": mortgage_interest,
        "total_costs": total_costs,
        "net_cashflow": net_cashflow,
        "net_yield_pct": net_yield_pct,
        "ownership_score": ownership_score,
        "ten_year_projection": ten_year,
        "assumptions": [
            "Recurring costs in the 10-year projection grow at 3% per year.",
            "Capital replacement cost allocation uses unit entitlement / total building entitlement.",
            "Capital replacement is informational only — it is not included in Total Annual Cost "
            "because it is already funded through your strata sinking levy above. The 10-year "
            "projection layers actual one-off capital works events on top of recurring costs.",
        ],
        "computed_at": _now(),
    }


# ── Owner mortgage (personal financing input for True Cost of Ownership) ──────
# Stored per building_id + unit_number + user_id in the `owner_mortgages` collection.
# Balance is integer CENTS (CLAUDE.md ledger-money rule); interest_rate is an annual
# percentage float (e.g. 6.25 = 6.25% p.a.). This is private financial data — only the
# authenticated owner of the unit reads/writes their own record (see the router guard and
# the user_id-scoped lookup in compute_unit_tco).

async def get_unit_mortgage(building_id: str, unit_number: str, user_id: str) -> Optional[dict]:
    """Return the stored mortgage for this owner+unit, or None."""
    if not (building_id and unit_number and user_id):
        return None
    return await db.owner_mortgages.find_one(
        {"building_id": building_id, "unit_number": str(unit_number).upper(), "user_id": user_id},
        {"_id": 0},
    )


async def save_unit_mortgage(
    building_id: str,
    unit_number: str,
    user_id: str,
    mortgage_balance_cents: int,
    interest_rate: float,
    repayment_type: str = "principal_and_interest",
) -> dict:
    """Upsert the owner's mortgage for a unit. Validates BEFORE the upsert (a $setOnInsert
    on a bad value would otherwise stick). Balance in integer cents, rate as annual %."""
    unit_number = str(unit_number or "").upper()
    if mortgage_balance_cents < 0:
        raise ValueError("mortgage_balance_cents must be >= 0")
    if not (0 <= float(interest_rate) <= 100):
        raise ValueError("interest_rate must be between 0 and 100")
    if repayment_type not in ("principal_and_interest", "interest_only"):
        repayment_type = "principal_and_interest"
    doc = {
        "building_id": building_id,
        "unit_number": unit_number,
        "user_id": user_id,
        "mortgage_balance_cents": int(mortgage_balance_cents),
        "interest_rate": round(float(interest_rate), 4),
        "repayment_type": repayment_type,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.owner_mortgages.update_one(
        {"building_id": building_id, "unit_number": unit_number, "user_id": user_id},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def compute_unit_tco(unit_number: str, year: str, vacancy_weeks: int = 2,
                           user_id: Optional[str] = None) -> dict:
    """
    Compute True Cost of Ownership for a strata unit directly by unit_number.
    Used by admin/strata-manager view that doesn't require owner_properties registration.

    When ``user_id`` is the authenticated owner of the unit and they have saved a mortgage,
    the estimated annual mortgage interest is returned as its own ``mortgage_interest`` field
    (a personal financing cost — deliberately NOT folded into ``total_costs``/``cost_per_uoe``,
    which stay scoped to strata holding costs; the frontend renders it as its own chart category).
    """
    unit_number = str(unit_number or "").upper()
    unit = await db.units.find_one({"unit_number": unit_number}, {"_id": 0})
    if not unit:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unit {unit_number} not found")

    year_str = str(year)[:4] if year else str(datetime.now(timezone.utc).year)
    year_int = int(year_str)
    building_id = get_ctx_building_id() or unit.get("building_id", "")
    entitlement = float(unit.get("entitlement", 1) or 1)

    # ACT/Australian financial year format: calendar year 2026 → "2025-26"
    fin_year_fmt = f"{year_int - 1}-{str(year_int)[-2:].zfill(2)}"

    def _year_match_query(y: str, yi: int, fy: str) -> dict:
        """Match documents stored under any year key format for this calendar year."""
        return {
            "$or": [
                {"year": y},
                {"financial_year": y},
                {"financial_year": fy},           # e.g. "2025-26" for year 2026
                {"year_numeric": yi},
                {"year_numeric": y},
                {"year": {"$regex": f"^{y}-Q[1-4]$", "$options": "i"}},
            ]
        }

    year_query = _year_match_query(year_str, year_int, fin_year_fmt)
    lot_or_unit_query = {"$or": [{"unit_number": unit_number}, {"lot_number": unit_number}]}

    # Strata levies — prefer the COMMITTED ANNUAL rate (admin + sinking per-UOE,
    # GST-inclusive) from annual_levies, the same source the main Dashboard uses
    # (see routers get_owner_unit → utils.finance_helpers.get_levy_rates). The
    # unit_levy_ledger "annual" row is only updated incrementally as quarters are
    # actually invoiced (e.g. quarters_charged=1 mid-year), so treating it as the
    # full-year total under-reports the levy for any year that isn't fully billed
    # yet. Only fall back to the ledger when no annual_levies rate doc exists for
    # this building/year (e.g. a new building before onboarding).
    levy_rates = await get_levy_rates(year_str, building_id)
    strata_admin_levies = round(levy_rates.get("admin_annual", 0.0) * entitlement, 2)
    strata_sinking_levies = round(levy_rates.get("sinking_annual", 0.0) * entitlement, 2)

    if strata_admin_levies or strata_sinking_levies:
        levy_source = "annual_levies:rate_based(committed_annual)"
        strata_levies = round(strata_admin_levies + strata_sinking_levies, 2)
    else:
        # Fallback: no annual_levies rate doc for this year — derive from unit_levy_ledger.
        ledger_docs = await db.unit_levy_ledger.find(
            {"$and": [{"building_id": building_id}, lot_or_unit_query, year_query]},
            {"_id": 0, "year": 1, "quarter": 1, "admin_levied": 1, "sinking_levied": 1, "updated_at": 1},
        ).to_list(40)

        def _is_quarter_row(doc: dict) -> bool:
            """Generated function header.

            Function: _is_quarter_row
            Path: backend/services/ownerhub_service.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            if doc.get("quarter"):
                return True
            yv = str(doc.get("year", ""))
            return bool(re.match(r"^\d{4}-Q[1-4]$", yv, re.IGNORECASE))

        def _normalise_quarter_key(doc: dict) -> str:
            """Return a normalised key (Q1/Q2/Q3/Q4) for deduplication.

            Handles mixed storage formats:
              - quarter='Q1'      → 'Q1'
              - quarter='Q1 2026' → 'Q1'
              - year='2026-Q1'    → 'Q1'
            This prevents double-counting when the same quarter exists in both formats.
            """
            raw = str(doc.get("quarter") or doc.get("year") or "")
            # Extract the Qn part using a regex regardless of surrounding year text.
            m = re.search(r"(Q[1-4])", raw, re.IGNORECASE)
            return m.group(1).upper() if m else raw.upper()

        quarter_rows = [d for d in ledger_docs if _is_quarter_row(d)]
        annual_rows = [d for d in ledger_docs if not _is_quarter_row(d)]

        strata_admin_levies = 0.0
        strata_sinking_levies = 0.0
        levy_source = "unit_levy_ledger:none"
        if quarter_rows:
            # Quarter rows are authoritative when present for the selected year.
            # Use normalised Q1/Q2/Q3/Q4 key to deduplicate across storage formats
            # (e.g. quarter="Q1" and year="2026-Q1" for the same quarter).
            rows_by_quarter = {}
            for row in quarter_rows:
                quarter_key = _normalise_quarter_key(row)
                existing = rows_by_quarter.get(quarter_key)
                if existing is None or str(row.get("updated_at", "")) > str(existing.get("updated_at", "")):
                    rows_by_quarter[quarter_key] = row
            selected_rows = list(rows_by_quarter.values())
            strata_admin_levies = round(sum(float(r.get("admin_levied", 0) or 0) for r in selected_rows), 2)
            strata_sinking_levies = round(sum(float(r.get("sinking_levied", 0) or 0) for r in selected_rows), 2)
            levy_source = f"unit_levy_ledger:quarterly({len(selected_rows)} rows)"
        elif annual_rows:
            # Choose the most complete annual row.
            best = max(
                annual_rows,
                key=lambda r: float(r.get("admin_levied", 0) or 0) + float(r.get("sinking_levied", 0) or 0),
            )
            strata_admin_levies = round(float(best.get("admin_levied", 0) or 0), 2)
            strata_sinking_levies = round(float(best.get("sinking_levied", 0) or 0), 2)
            levy_source = "unit_levy_ledger:annual"
        strata_levies = round(strata_admin_levies + strata_sinking_levies, 2)

    # Council rates + land tax
    # Rates are stored with financial_year format ("2025-26"), so query includes that.
    council_docs = await db.council_rates.find(
        {"$and": [{"building_id": building_id}, lot_or_unit_query, year_query]},
        {"_id": 0},
    ).to_list(20)

    def _council_totals(doc: dict) -> tuple[float, float]:
        """Generated function header.

        Function: _council_totals
        Path: backend/services/ownerhub_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        rates_total = (
            doc.get("total_rates")
            or doc.get("council_rates")
            or doc.get("rates_total")
            or 0
        )
        if not rates_total:
            rates_total = sum(float(doc.get(f"rates_q{i}", 0) or 0) for i in range(1, 5))
        land_tax_total = doc.get("land_tax_total") or doc.get("total_land_tax") or 0
        if not land_tax_total:
            land_tax_total = sum(float(doc.get(f"land_tax_q{i}", 0) or 0) for i in range(1, 5))
        return round(float(rates_total or 0), 2), round(float(land_tax_total or 0), 2)

    council_rates_amt = 0.0
    land_tax_amt = 0.0
    rates_source = "council_rates:none"
    if council_docs:
        # Prefer document with the largest known annual total.
        best_doc = max(council_docs, key=lambda d: sum(_council_totals(d)))
        council_rates_amt, land_tax_amt = _council_totals(best_doc)
        rates_source = "council_rates:cached_or_live"
    # Land tax applies to investment/rental properties, not an owner's principal
    # place of residence — mirrors the is_owner_occupied gate already applied in
    # routers/council_rates.py:_unit_is_investment/_build_land_tax.
    if unit.get("is_owner_occupied", True):
        land_tax_amt = 0.0
    rates_and_charges_amt = round(council_rates_amt + land_tax_amt, 2)

    # Water bills — stored without year/financial_year; match via billing_period_start or quarter string.
    # Use CALENDAR YEAR only (billing_period_start within YYYY-01-01 .. YYYY-12-31, or quarter=" YYYY").
    # A previous version also added a FY half-year range (Jul–Dec of prior year) via $or, which caused
    # Q3/Q4 of the prior year to appear in the current year's total — inflating water charges.
    # The `*year_query["$or"]` spread is kept as a fallback for any future records that explicitly
    # store a year/financial_year field.
    water_match = {
        "$and": [
            {"building_id": building_id},
            lot_or_unit_query,
            {
                "$or": [
                    # Calendar-year primary: billing_period_start in YYYY-01-01 .. YYYY-12-31
                    {"billing_period_start": {"$gte": f"{year_int}-01-01", "$lte": f"{year_int}-12-31"}},
                    # Quarter string ends with " YYYY", e.g. "Q1 2026"
                    {"quarter": {"$regex": f" {year_int}$", "$options": "i"}},
                    # Fallback for explicit year/financial_year fields (future-proof)
                    *year_query["$or"],
                ]
            },
        ]
    }
    water_res = await db.water_bills.aggregate([
        {"$match": water_match},
        {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$amount", {"$ifNull": ["$amount_paid", 0]}]}}}},
    ]).to_list(1)
    water_amt = round(water_res[0]["total"], 2) if water_res else 0.0

    # GAP-DASH-001 (TCO water gap): with no manually-entered water_bills for this unit/year,
    # water_amt is $0, and the Owner dashboard's `annual > 0` category filter then hides the Water
    # tile entirely — so a real, unavoidable holding cost silently vanishes from "True Cost of
    # Ownership". Fall back to an estimate (mirrors true_cost_service._get_water_estimate): prefer
    # the unit's stored water_estimate_cents, else a flat annual default (~ Icon Water fixed supply
    # charge). Marked "estimated" so the figure is never mistaken for a billed actual.
    water_source = "water_bills:actual"
    if water_amt <= 0.0:
        _water_est_cents = (unit or {}).get("water_estimate_cents")
        if _water_est_cents:
            water_amt = round(float(_water_est_cents) / 100, 2)
        else:
            water_amt = 1800.0  # flat annual default (dollars), matches true_cost_service default
        water_source = "estimated"

    # Capital replacement (unit share)
    # ─────────────────────────────────────────────────────────────────────────
    # Methodology: annual provisioning cost for building capital works.
    #
    # A single-year expenditure from sinking_fund_plan is misleading as a TCO
    # number because capital works are lumpy (e.g. $413 k in 2029, $6 k in 2028).
    # Instead we compute a 5-year forward amortised annual capital exposure:
    #   1. Fetch sinking_fund_plan rows for years year_int … year_int+4.
    #   2. Sum expenditures; divide by 5 → annual building capital average.
    #   3. Multiply by unit_share = entitlement / total_entitlement.
    #
    # This gives a stable, meaningful annual ownership provisioning cost.
    # For the 10-year projection we still use per-year plan expenditure × share
    # so owners see the actual lumpy capital events on the timeline.
    #
    # NOTE: capital_cost is informational only — it is NOT added into total_costs
    # below. The sinking levy (already inside strata_levies) is what actually funds
    # these forecast capital works; adding capital_cost on top would double-count
    # the same spend once as a levy the owner pays and again as a derived forecast.
    # ─────────────────────────────────────────────────────────────────────────
    total_ent_agg = await db.units.aggregate([
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": None, "total": {"$sum": "$entitlement"}}},
    ]).to_list(1)
    total_ent = float(total_ent_agg[0]["total"] if total_ent_agg else 10000)
    unit_share = entitlement / total_ent if total_ent > 0 else 0

    _AMORT_YEARS = 5
    plan_fwd_rows = await db.sinking_fund_plan.find(
        {
            "building_id": building_id,
            "$or": [
                {"year": {"$gte": year_int, "$lte": year_int + _AMORT_YEARS - 1}},
                {"year": {"$gte": str(year_int), "$lte": str(year_int + _AMORT_YEARS - 1)}},
            ],
        },
        {"_id": 0, "year": 1, "expenditure": 1, "capital_expenditure": 1, "total_expenditure": 1},
    ).to_list(_AMORT_YEARS)

    def _plan_expenditure(row: dict) -> float:
        # Use explicit None check so that expenditure=0.0 is respected and does NOT
        # fall through to capital_expenditure (the falsy `or` chain would return
        # capital_expenditure for a legitimately zero-expenditure year).
        """Generated function header.

        Function: _plan_expenditure
        Path: backend/services/ownerhub_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        for key in ("expenditure", "capital_expenditure", "total_expenditure"):
            val = row.get(key)
            if val is not None:
                return float(val)
        return 0.0

    fwd_total_expenditure = sum(_plan_expenditure(r) for r in plan_fwd_rows)
    fwd_years_count = len(plan_fwd_rows) if plan_fwd_rows else _AMORT_YEARS
    annual_building_capital = fwd_total_expenditure / fwd_years_count if fwd_years_count > 0 else 0.0
    capital_cost = round(annual_building_capital * unit_share, 2)
    capital_source = (
        f"sinking_fund_plan:{_AMORT_YEARS}yr_amortised({fwd_years_count} rows)"
        if plan_fwd_rows else "none"
    )

    # GAP-DASH-001 (TCO mortgage): estimated annual mortgage interest for the AUTHENTICATED owner
    # of this unit. Personal financing data — only surfaced when user_id matches an owner_mortgages
    # record for this unit (a manager/agent passes their own user_id → no record → stays hidden).
    # Kept OUT of total_costs / cost_per_uoe (those remain strata holding-cost metrics); returned as
    # its own field so the frontend can render it as a distinct chart category.
    mortgage_interest = 0.0
    mortgage_source = "none"
    if user_id:
        _mortgage = await get_unit_mortgage(building_id, unit_number, user_id)
        if _mortgage and _mortgage.get("mortgage_balance_cents") and _mortgage.get("interest_rate"):
            _bal_dollars = float(_mortgage["mortgage_balance_cents"]) / 100
            mortgage_interest = round(_bal_dollars * float(_mortgage["interest_rate"]) / 100, 2)
            mortgage_source = "owner_input"

    # capital_cost is intentionally excluded — see NOTE above (double-counts the sinking levy).
    total_costs = round(strata_levies + rates_and_charges_amt + water_amt, 2)

    # 10-year projection: recurring costs grow at 3% p.a.; capital per-year from sinking_fund_plan.
    # Fetch all sinking_fund_plan rows for the projection window up-front (avoid N+1).
    proj_end = year_int + 10
    all_plan_rows = await db.sinking_fund_plan.find(
        {
            "building_id": building_id,
            "$or": [
                {"year": {"$gte": year_int + 1, "$lte": proj_end}},
                {"year": {"$gte": str(year_int + 1), "$lte": str(proj_end)}},
            ],
        },
        {"_id": 0, "year": 1, "expenditure": 1, "capital_expenditure": 1, "total_expenditure": 1},
    ).to_list(12)
    plan_by_year: dict[int, float] = {}
    for row in all_plan_rows:
        try:
            yr_key = int(row["year"])
            plan_by_year[yr_key] = _plan_expenditure(row)
        except (KeyError, TypeError, ValueError):
            pass

    projection = []
    recurring_costs = strata_levies + rates_and_charges_amt + water_amt
    running_costs = recurring_costs
    for i in range(1, 11):
        yr = year_int + i
        running_costs = round(running_costs * 1.03, 2)
        cap_yr_unit = round(plan_by_year.get(yr, 0.0) * unit_share, 2)
        projection.append({
            "year": yr,
            "total_costs": round(running_costs + cap_yr_unit, 2),
            "capital_cost": cap_yr_unit,
            "has_capital_works": cap_yr_unit > 0,
        })

    _owner = await get_owner_info(unit_number, building_id) if building_id else {}
    return {
        "unit_number": unit_number,
        "unit_type": unit.get("property_type", ""),
        "owner_name": _owner.get("owner_name") or unit.get("owner_name", ""),
        "entitlement": entitlement,
        "year": year_str,
        "strata_levies": strata_levies,
        "council_rates": council_rates_amt,
        "land_tax": land_tax_amt,
        "rates_and_charges": rates_and_charges_amt,
        "water_charges": water_amt,
        "capital_replacement": capital_cost,
        "mortgage_interest": mortgage_interest,
        "total_costs": total_costs,
        "cost_per_uoe": round(total_costs / entitlement, 2) if entitlement > 0 else 0.0,
        "projection": projection,
        "breakdown": {
            "strata_levies": strata_levies,
            "rates_and_charges": rates_and_charges_amt,
            "water_charges": water_amt,
            "capital_replacement": capital_cost,
            "mortgage_interest": mortgage_interest,
        },
        "breakdown_details": {
            "strata_levies": {
                "source": levy_source,
                "calculation": (
                    "Committed annual levy = (admin + sinking per-UOE rate, GST-inclusive) "
                    "× unit entitlement. Matches the amount shown on your main Dashboard."
                    if levy_source.startswith("annual_levies:")
                    else "Admin levied + Sinking levied for selected year (summing quarterly rows when present) — "
                         "no committed annual rate found for this building/year yet."
                ),
            },
            "rates_and_charges": {
                "source": rates_source,
                "calculation": "Council rates annual total + land tax annual total (or summed quarterly instalments when annual total absent).",
            },
            "water_charges": {
                "source": water_source,
                "calculation": (
                    "Sum of water bill amounts for selected year."
                    if water_source == "water_bills:actual"
                    else "Estimated: unit water_estimate_cents, or a flat annual default when no "
                         "water bills have been entered for this unit/year."
                ),
            },
            "capital_replacement": {
                "source": capital_source,
                "calculation": (
                    "This is NOT building insurance or reinstatement value — insurance premiums are "
                    "a separate admin-fund expense and are not broken out on this page. This figure is "
                    "your entitlement share of the building's FORECAST capital works (e.g. roof, "
                    "painting, lifts) drawn from the sinking fund plan: "
                    f"5-year forward capital plan amortised annually "
                    f"({fwd_years_count} years of sinking fund plan data, "
                    f"total ${annual_building_capital * _AMORT_YEARS:,.0f} building capex) "
                    f"× unit entitlement share ({entitlement:.0f}/{total_ent:.0f} = {unit_share*100:.2f}%). "
                    "10-year projection shows the actual year-by-year capital events. "
                    "This is informational only — not included in Total Annual Cost because it is "
                    "already funded through your sinking fund levy above."
                ),
            },
        },
        "assumptions": [
            "Recurring costs in 10-year projection grow at 3% per year.",
            "Capital cost allocation uses unit entitlement / total building entitlement.",
            "Capital Replacement (above) is informational only and is not added to Total "
            "Annual Cost — it is already funded through your sinking fund levy. Capital works "
            "shown on the chart are one-off forecast events layered on top of recurring costs, "
            "not an additional charge.",
        ],
        "net_cashflow": None,
        "net_yield": None,
        "ownership_score": None,
        "computed_at": _now(),
    }


# ── Weekly Radar ───────────────────────────────────────────────────────────

async def generate_weekly_radar(property_id: str) -> dict:
    """
    Generate weekly radar alerts and health metrics for a property.
    Performance Optimization⚡: Consolidated all data fetches into a single parallel stage
    and eliminated redundant downstream DB round-trips for arrears and health score.
    """
    # Performance Optimization⚡: Consolidated fetches including components needed by health score
    now_dt = datetime.now(timezone.utc)
    month_start = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    _m6 = now_dt.month - 6
    six_months_ago = now_dt.replace(month=_m6, day=1) if _m6 > 0 else now_dt.replace(year=now_dt.year - 1,
                                                                                     month=_m6 + 12, day=1)

    tasks = [
        db.owner_properties.find_one({"_id": __oid(property_id)}),
        get_active_tenancy(property_id),
        get_inspections_for_property(property_id),
        db.property_health_snapshots.find_one({"property_id": property_id}, sort=[("computed_at", -1)]),
        # Maintenance - All Open (for health score and filtering alerts in memory)
        db.tenant_maintenance_requests.find({
            "property_id": property_id,
            "status": {"$nin": ["completed", "closed", "rejected"]}
        }).to_list(200),
        # Maintenance - Costs this month (for health score)
        db.tenant_maintenance_requests.aggregate([
            {"$match": {"property_id": property_id, "actual_cost": {"$gt": 0},
                        "updated_at": {"$gte": month_start.isoformat()}}},
            {"$group": {"_id": None, "total": {"$sum": "$actual_cost"}}}
        ]).to_list(1),
        # Maintenance - Costs last 6 months (for health score)
        db.tenant_maintenance_requests.aggregate([
            {"$match": {"property_id": property_id, "actual_cost": {"$gt": 0},
                        "updated_at": {"$gte": six_months_ago.isoformat()}}},
            {"$group": {"_id": None, "total": {"$sum": "$actual_cost"}}}
        ]).to_list(1),
    ]

    results = await asyncio.gather(*tasks)
    prop_doc = results[0]
    active = results[1]
    inspections = results[2]
    last_snap = results[3]
    open_requests = results[4]
    res_this_month = results[5]
    res_avg = results[6]

    # Filter urgent requests in memory to reduce DB calls - Nitpick from review ⚡
    open_urgent = [
        r for r in open_requests
        if r.get("urgency") in ["urgent", "emergency"]
    ][:50]

    # Compute health score using pre-fetched components to avoid O(N) round-trips
    health_data = await compute_property_health_score(
        property_id,
        prop_doc=prop_doc,
        active_tenancy=active,
        res_this_month=res_this_month,
        res_avg=res_avg,
        open_requests=open_requests,
        inspections=inspections
    )

    alerts = []

    # Check lease expiry and arrears
    if active:
        try:
            lease_end = date.fromisoformat(active.get("lease_end", "")[:10])
            days_remaining = (lease_end - date.today()).days
            if days_remaining < 90:
                alerts.append(f"Lease expires in {days_remaining} days")
        except Exception as exc:
            logger.warning("ownerhub_service: lease_end date parse failed: %s", exc)

        # Check arrears (Performance Optimization⚡: Passing pre-fetched tenancy doc)
        arrears = await compute_arrears(active["id"], tenancy_doc=active)
        if arrears["arrears_amount"] > 0:
            alerts.append(
                f"Rent arrears: ${arrears['arrears_amount']:.2f} ({arrears['arrears_stage']})"
            )
    else:
        alerts.append("Property is currently vacant")

    # Check open urgent/emergency maintenance
    for req in open_urgent:
        alerts.append(
            f"Open {req.get('urgency')} maintenance: {req.get('issue_title', '')}"
        )

    # Inspection overdue
    completed = [i for i in inspections if i.get("status") == "completed" and i.get("completed_date")]
    if completed:
        completed.sort(key=lambda x: x.get("completed_date", ""), reverse=True)
        try:
            last_dt = date.fromisoformat(completed[0]["completed_date"][:10])
            if (date.today() - last_dt).days > 180:
                alerts.append("Property inspection overdue (>6 months)")
        except Exception as exc:
            logger.warning("ownerhub_service: inspection completed_date parse failed: %s", exc)
    else:
        alerts.append("No completed inspections on record")

    # Health score results
    health_score = health_data.get("health_score", 0.0)

    # Compare to stored snapshot for change
    health_change = 0.0
    if last_snap:
        health_change = round(health_score - last_snap.get("health_score", health_score), 1)

    return {
        "property_id": property_id,
        "generated_at": _now(),
        "alerts": alerts,
        "health_score": health_score,
        "health_change": health_change,
        "risk_level": health_data.get("risk_level"),
    }


# ── Snapshot Persistence ───────────────────────────────────────────────────

async def store_health_snapshot(property_id: str, snapshot: dict) -> str:
    """Generated function header.

    Function: store_health_snapshot
    Path: backend/services/ownerhub_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = _now()
    snapshot["computed_at"] = now
    result = await db.property_health_snapshots.update_one(
        {"property_id": property_id},
        {"$set": snapshot},
        upsert=True
    )
    upserted_id = result.upserted_id
    if upserted_id:
        return str(upserted_id)
    # Find existing
    doc = await db.property_health_snapshots.find_one({"property_id": property_id}, {"_id": 1})
    return str(doc["_id"]) if doc else ""


# ── Internal helper ────────────────────────────────────────────────────────

def __oid(id_str: str):
    """Generated function header.

    Function: __oid
    Path: backend/services/ownerhub_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from bson import ObjectId
    try:
        return ObjectId(id_str)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid ID: {id_str}")
