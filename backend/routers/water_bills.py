"""
Water Bills router module -- Icon Water (ACT) bills per unit.

Since Icon Water does not provide a public API, all bill data is manually
entered by strata managers or administrators.

GST NOTE: Icon Water (ACT) utility charges (water supply, sewerage supply,
and usage) are GST-free supplies under Australian tax law (ATO GST classification
for water and sewerage services). No GST is charged on or calculated for any
water bill record. All amounts stored and displayed are the GST-free total.

Collections used:
  - water_bills : bill records per unit, updated as payments come in

Auth rules:
  - Owners / Tenants : list bills and mark-paid for their own unit only
                       (requires can_view_finances)
  - Managers / EC    : create, list, and mark-paid for any unit
                       (requires can_manage_finances for create)
  - Super Admin / Chairman only : delete bills
"""

import uuid
from datetime import datetime, date, timezone

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer
from typing import List, Optional

from database import db
from models.property_taxes import (
    WaterBillCreate,
    WaterBillResponse,
    WaterBillMarkPaidRequest,
    WaterBillMarkPartialRequest,
    WaterChargeEstimate,
)
from models.user import UserRole
from services.settings_service import get_general_settings_or_default
from utils.auth import get_current_user, get_current_building, effective_role
from utils.helpers import create_audit_log
from utils.permissions import get_user_permissions

# -------------------------------------------------------------------------

router = APIRouter(prefix="")
security = HTTPBearer(auto_error=False)

# Icon Water (ACT) fixed daily supply rates — the ONE place these numbers live.
#
# They are the ACT regulated rates and they change on 1 July with each price
# determination, so the schedule they belong to is part of the data: a bare
# "$0.6616 / day" with no effective period cannot be checked for staleness by
# anyone reading it.
#
# This is stated here because three separate frontend files used to print these
# same four decimals with a "2025-26" caption typed in beside them by hand, while
# two of those files were ALREADY calling /water-bills/quarterly-estimates for the
# per-quarter totals. The rates are served alongside the estimates now, and the
# frontend renders what the API returns.
#
# NOT per-building yet. These are ACT/Icon Water rates, so a building in another
# jurisdiction would be shown the wrong figures. Making them building-scoped needs
# a jurisdiction-aware rate table and is a separate change — the seam is this one
# dict, so a per-building lookup replaces it and nothing else moves.
ICON_WATER_RATE_SCHEDULE: dict = {
    "provider": "Icon Water",
    "jurisdiction": "ACT",
    "schedule_label": "2025-26",
    "effective_from": "2025-07-01",
    "effective_to": "2026-06-30",
    "water_daily_rate": 0.6616,
    "sewer_daily_rate": 1.6773,
}

WATER_DAILY_RATE: float = ICON_WATER_RATE_SCHEDULE["water_daily_rate"]  # Water supply: $/day
SEWER_DAILY_RATE: float = ICON_WATER_RATE_SCHEDULE["sewer_daily_rate"]  # Sewerage supply: $/day


def _days_in_range(start: str, end: str) -> int:
    """Return the number of days between two ISO date strings (inclusive of start, exclusive of end)."""
    d1 = date.fromisoformat(start)
    d2 = date.fromisoformat(end)
    return max(0, (d2 - d1).days)


def _calc_supply(start: str, end: str, usage_kl: Optional[float] = None) -> dict:
    """Generated function header.

    Function: _calc_supply
    Path: backend/routers/water_bills.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    days = _days_in_range(start, end)
    water_sc = round(days * WATER_DAILY_RATE, 2)
    sewer_sc = round(days * SEWER_DAILY_RATE, 2)
    total = round(water_sc + sewer_sc, 2)
    return {
        "days": days,
        "water_supply_charge": water_sc,
        "sewerage_supply_charge": sewer_sc,
        "supply_total": total,
        "water_daily_rate": WATER_DAILY_RATE,
        "sewer_daily_rate": SEWER_DAILY_RATE,
        "usage_kl": usage_kl,
    }


# Approximate quarterly date ranges for a given calendar year
def _quarterly_ranges(year: int) -> list[dict]:
    """Generated function header.

    Function: _quarterly_ranges
    Path: backend/routers/water_bills.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return [
        {"quarter": "Q1", "start": f"{year}-01-01", "end": f"{year}-03-31", "label": "Jan–Mar"},
        {"quarter": "Q2", "start": f"{year}-04-01", "end": f"{year}-06-30", "label": "Apr–Jun"},
        {"quarter": "Q3", "start": f"{year}-07-01", "end": f"{year}-09-30", "label": "Jul–Sep"},
        {"quarter": "Q4", "start": f"{year}-10-01", "end": f"{year}-12-31", "label": "Oct–Dec"},
    ]


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/water_bills.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _is_privileged(user: dict) -> bool:
    """Return True for roles that can operate on any unit."""
    return effective_role(user) in (
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER,
        UserRole.STRATA_MANAGER,
    )


def _can_delete(user: dict) -> bool:
    """Only Super Admin and Chairman may delete water bill records."""
    return effective_role(user) in (UserRole.SUPER_ADMIN, UserRole.EC_MEMBER)


def _build_response(doc: dict) -> WaterBillResponse:
    """Compute derived field balance_due and return a WaterBillResponse."""
    amount = doc.get("amount", 0.0)
    amount_paid = doc.get("amount_paid", 0.0)
    return WaterBillResponse(
        **doc,
        balance_due=round(max(0.0, amount - amount_paid), 2),
    )


# -------------------------------------------------------------------------
# GET /api/water-bills/estimate
# -------------------------------------------------------------------------

@router.get("/water-bills/estimate", response_model=WaterChargeEstimate)
async def estimate_water_charges(
        start_date: str = Query(..., description="ISO date e.g. 2026-01-01"),
        end_date: str = Query(..., description="ISO date e.g. 2026-03-31"),
        usage_kl: Optional[float] = Query(None, description="Kilolitres consumed (optional)"),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Calculate Icon Water supply charges for a date range.

    The supply charges (water + sewerage) are FIXED PER PROPERTY and identical
    for all units in the complex. They are calculated purely from
    the number of days in the billing period at the ACT fixed daily rates.

    Usage charges (per kL) are NOT included — they appear on individual bills
    and must be entered manually.
    """
    try:
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must be ISO format YYYY-MM-DD")

    # Get building settings for branding
    settings_doc = await get_general_settings_or_default(building_id, {"_id": 0})
    b_name = settings_doc.get("building_name", "Our Residences")

    calc = _calc_supply(start_date, end_date, usage_kl)
    return WaterChargeEstimate(
        start_date=start_date,
        end_date=end_date,
        rate_schedule=ICON_WATER_RATE_SCHEDULE,
        note=(
            f"Supply charges are fixed for all {b_name} units at the ACT "
            f"regulated daily rates: water ${WATER_DAILY_RATE:.4f}/day, "
            f"sewerage ${SEWER_DAILY_RATE:.4f}/day."
        ),
        **calc,
    )


# -------------------------------------------------------------------------
# GET /api/water-bills/quarterly-estimates
# -------------------------------------------------------------------------

@router.get("/water-bills/quarterly-estimates")
async def quarterly_water_estimates(
        year: int = Query(..., description="Calendar year e.g. 2026"),
        current_user: dict = Depends(get_current_user),
):
    """
    Return supply-charge estimates for all four quarters of a given year.

    Values are identical for every unit — the only variable is the exact
    number of days in each quarter.
    """
    quarters = _quarterly_ranges(year)
    results = []
    for q in quarters:
        calc = _calc_supply(q["start"], q["end"])
        results.append({
            "quarter": q["quarter"],
            "label": q["label"],
            "start_date": q["start"],
            "end_date": q["end"],
            **calc,
        })
    return {
        "year": year,
        "estimates": results,
        "water_daily_rate": WATER_DAILY_RATE,
        "sewer_daily_rate": SEWER_DAILY_RATE,
        # The schedule these rates belong to, so a caller can label them honestly
        # instead of hardcoding a financial year next to the figure.
        "rate_schedule": ICON_WATER_RATE_SCHEDULE,
    }


# -------------------------------------------------------------------------
# GET /api/water-bills/{unit_number}
# -------------------------------------------------------------------------

@router.get("/water-bills/{unit_number}", response_model=List[WaterBillResponse])
async def list_water_bills(
        unit_number: str,
        limit: int = Query(20, ge=1, le=100, description="Maximum number of bills to return"),
        current_user: dict = Depends(get_current_user),
):
    """
    List water bills for a unit, sorted by creation date descending.

    Owners and tenants may only list bills for their own unit.
    Privileged roles (EC, Chairman, Strata Manager, Super Admin) may list
    bills for any unit.
    Requires can_view_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized to view finances")

    unit_number = unit_number.upper()

    if not _is_privileged(current_user):
        owner_unit = current_user.get("unit_number", "")
        if owner_unit.upper() != unit_number:
            raise HTTPException(
                status_code=403,
                detail="You may only view water bills for your own unit",
            )

    bills = (
        await db.water_bills.find({"unit_number": unit_number}, {"_id": 0})
        .sort("due_date", -1)
        .to_list(limit)
    )
    return [_build_response(b) for b in bills]


# -------------------------------------------------------------------------
# POST /api/water-bills/{unit_number}
# -------------------------------------------------------------------------

@router.post("/water-bills/{unit_number}", response_model=WaterBillResponse, status_code=201)
async def create_water_bill(
        unit_number: str,
        data: WaterBillCreate,
        current_user: dict = Depends(get_current_user),
):
    """
    Create a new water bill record for a unit.

    Requires can_manage_finances permission.
    Typically called by strata managers when an Icon Water quarterly bill arrives.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to manage finances")

    unit_number = unit_number.upper()

    unit_doc = await db.units.find_one({"unit_number": unit_number}, {"_id": 0, "unit_number": 1})
    if not unit_doc:
        raise HTTPException(status_code=404, detail=f"Unit {unit_number} not found")

    # Idempotency: reject duplicate (unit_number, billing_period_start, billing_period_end)
    if data.billing_period_start and data.billing_period_end:
        existing = await db.water_bills.find_one(
            {
                "unit_number": unit_number,
                "billing_period_start": data.billing_period_start,
                "billing_period_end": data.billing_period_end,
            },
            {"_id": 0, "id": 1, "quarter": 1, "amount": 1, "status": 1},
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A water bill for {unit_number} covering "
                    f"{data.billing_period_start} to {data.billing_period_end} "
                    f"already exists (id: {existing['id']}, status: {existing['status']}). "
                    "Use the existing bill or delete it first."
                ),
            )

    bill_id = str(uuid.uuid4())
    now_iso = _now()

    # Compute supply breakdown and days if billing period dates are provided
    supply_breakdown = None
    days = None
    if data.billing_period_start and data.billing_period_end:
        days = _days_in_range(data.billing_period_start, data.billing_period_end)
        supply_breakdown = _calc_supply(
            data.billing_period_start, data.billing_period_end, data.water_usage_kl
        )

    bill_doc = {
        "id": bill_id,
        "unit_number": unit_number,
        "quarter": data.quarter,
        "billing_period_start": data.billing_period_start,
        "billing_period_end": data.billing_period_end,
        "days": days,
        "amount": data.amount,
        "due_date": data.due_date,
        "water_usage_kl": data.water_usage_kl,
        "status": "unpaid",
        "amount_paid": 0.0,
        "payment_date": None,
        "payment_reference": None,
        "notes": data.notes,
        "supply_breakdown": supply_breakdown,
        # Icon Water (ACT) charges are GST-free — no GST on water/sewerage utility bills
        "gst_free": True,
        "gst_amount": 0.0,
        "created_at": now_iso,
        "created_by": current_user["id"],
        "updated_at": now_iso,
    }

    await db.water_bills.insert_one(bill_doc)

    asyncio.create_task(create_audit_log(
        action="water_bill_created",
        resource_type="water_bill",
        resource_id=bill_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "unit_number": unit_number,
            "quarter": data.quarter,
            "amount": data.amount,
            "due_date": data.due_date,
        },
    ))

    return _build_response(bill_doc)


# -------------------------------------------------------------------------
# PATCH /api/water-bills/{bill_id}/mark-paid
# -------------------------------------------------------------------------

@router.patch("/water-bills/{bill_id}/mark-paid", response_model=WaterBillResponse)
async def mark_water_bill_paid(
        bill_id: str,
        data: WaterBillMarkPaidRequest,
        current_user: dict = Depends(get_current_user),
):
    """
    Mark a water bill as fully paid.

    Sets status to paid, amount_paid to the full bill amount, and records
    the payment_date and optional payment_reference.

    Owners and tenants may mark only bills for their own unit.
    Privileged roles may mark any bill.
    Requires can_view_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    bill = await db.water_bills.find_one({"id": bill_id}, {"_id": 0})
    if not bill:
        raise HTTPException(status_code=404, detail="Water bill not found")

    if not _is_privileged(current_user):
        owner_unit = current_user.get("unit_number", "")
        if owner_unit.upper() != bill.get("unit_number", "").upper():
            raise HTTPException(
                status_code=403,
                detail="You may only update water bills for your own unit",
            )

    now_iso = _now()
    update = {
        "status": "paid",
        "amount_paid": bill.get("amount", 0.0),
        "payment_date": data.payment_date,
        "payment_reference": data.payment_reference,
        "updated_at": now_iso,
    }

    await db.water_bills.update_one({"id": bill_id}, {"$set": update})

    asyncio.create_task(create_audit_log(
        action="water_bill_marked_paid",
        resource_type="water_bill",
        resource_id=bill_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "unit_number": bill.get("unit_number"),
            "amount": bill.get("amount"),
            "payment_date": data.payment_date,
        },
    ))

    updated = {**bill, **update}
    return _build_response(updated)


# -------------------------------------------------------------------------
# PATCH /api/water-bills/{bill_id}/mark-partial
# -------------------------------------------------------------------------

@router.patch("/water-bills/{bill_id}/mark-partial", response_model=WaterBillResponse)
async def mark_water_bill_partial(
        bill_id: str,
        data: WaterBillMarkPartialRequest,
        current_user: dict = Depends(get_current_user),
):
    """
    Record a partial payment against a water bill.

    The amount_paid field is accumulated: new partial is added to any existing
    amount_paid. If the running total meets or exceeds the bill total, status
    is automatically promoted to paid.

    Each partial payment note is appended to the bill notes field with a
    date-stamped prefix to maintain a full audit trail.

    Owners and tenants may update only bills for their own unit.
    Privileged roles may update any bill.
    Requires can_view_finances permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    bill = await db.water_bills.find_one({"id": bill_id}, {"_id": 0})
    if not bill:
        raise HTTPException(status_code=404, detail="Water bill not found")

    if not _is_privileged(current_user):
        owner_unit = current_user.get("unit_number", "")
        if owner_unit.upper() != bill.get("unit_number", "").upper():
            raise HTTPException(
                status_code=403,
                detail="You may only update water bills for your own unit",
            )

    if bill.get("status") == "paid":
        raise HTTPException(status_code=400, detail="Bill is already fully paid")

    total_amount = bill.get("amount", 0.0)
    existing_paid = bill.get("amount_paid", 0.0)
    new_total_paid = round(existing_paid + data.amount_paid, 2)

    if new_total_paid > total_amount:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Payment of ${data.amount_paid:.2f} would exceed the bill"
                f" total of ${total_amount:.2f}. Existing payments:"
                f" ${existing_paid:.2f}. Use mark-paid to settle the full balance."
            ),
        )

    new_status = "paid" if new_total_paid >= total_amount else "partial"
    now_iso = _now()

    # Append dated note to preserve full partial-payment audit trail
    existing_notes = bill.get("notes") or ""
    if data.notes:
        appended = f"[{data.payment_date}] Partial ${data.amount_paid:.2f}: {data.notes}"
    else:
        appended = f"[{data.payment_date}] Partial ${data.amount_paid:.2f}"
    combined_notes = (existing_notes + "\n" + appended).strip() if existing_notes else appended

    update = {
        "status": new_status,
        "amount_paid": new_total_paid,
        "payment_date": data.payment_date,
        "notes": combined_notes,
        "updated_at": now_iso,
    }

    await db.water_bills.update_one({"id": bill_id}, {"$set": update})

    asyncio.create_task(create_audit_log(
        action="water_bill_partial_payment",
        resource_type="water_bill",
        resource_id=bill_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "unit_number": bill.get("unit_number"),
            "amount_paid_this_time": data.amount_paid,
            "total_paid_now": new_total_paid,
            "bill_total": total_amount,
            "new_status": new_status,
        },
    ))

    updated = {**bill, **update}
    return _build_response(updated)


# -------------------------------------------------------------------------
# DELETE /api/water-bills/{bill_id}
# -------------------------------------------------------------------------

@router.delete("/water-bills/{bill_id}", status_code=204)
async def delete_water_bill(
        bill_id: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Permanently delete a water bill record.

    Requires can_manage_finances permission AND Super Admin or Chairman role.
    This is a destructive operation with no soft-delete. The audit log entry
    persists after deletion for compliance purposes.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized to manage finances")

    if not _can_delete(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only Super Admin or Chairman may delete water bill records",
        )

    bill = await db.water_bills.find_one(
        {"id": bill_id},
        {"_id": 0, "unit_number": 1, "quarter": 1, "amount": 1, "status": 1},
    )
    if not bill:
        raise HTTPException(status_code=404, detail="Water bill not found")

    await db.water_bills.delete_one({"id": bill_id})

    asyncio.create_task(create_audit_log(
        action="water_bill_deleted",
        resource_type="water_bill",
        resource_id=bill_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details=bill,
    ))
