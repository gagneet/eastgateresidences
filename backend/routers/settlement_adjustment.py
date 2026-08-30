"""
Settlement Adjustment Router — pro-rata levy split for conveyancers.

GET /api/settlement-adjustment/{lot_id}?date=YYYY-MM-DD
Returns the levy split between outgoing and incoming owner based on settlement date.

Building context is resolved server-side from the authenticated session (JWT
``building_id`` claim, ``X-Building-ID`` header for super_admin, or membership
fallback) — see ``utils.auth.get_current_building``. Any caller-supplied
``building_id`` query parameter is ignored to prevent BOLA cross-building access.

Used by:
  - Conveyancers requesting pro-rata levy adjustment at property settlement
  - Admin staff processing ownership transfers
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from database import db
from services.ownership_service import resolve_owner, get_ownership_history
from utils.auth import get_current_user, get_current_building

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settlement-adjustment", tags=["Settlement Adjustment"])


@router.get("/{lot_id}")
async def get_settlement_adjustment(
        lot_id: str,
        building_id: str = Depends(get_current_building),
        date: str = Query(..., description="Settlement date in YYYY-MM-DD format"),
        current_user: dict = Depends(get_current_user),
):
    """Return the pro-rata levy split for a lot at settlement date.

    Calculates how much of the current levy period the outgoing owner is
    responsible for vs the incoming owner, based on the settlement date.

    ``building_id`` is resolved from the authenticated session via
    ``get_current_building`` — any ``?building_id=`` query value is ignored.

    Returns 404 if no ownership record exists for the lot.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    allowed = {"super_admin", "strata_manager", "ec_member"}
    if _role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised.")

    try:
        settlement_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date must be YYYY-MM-DD format.",
        )

    if settlement_dt > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settlement date is in the future — no ownership period exists yet.",
        )

    outgoing_owner = await resolve_owner(lot_id, building_id, settlement_dt, db=db)
    if not outgoing_owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ownership record found for lot {lot_id} in building {building_id} at {date}.",
        )

    # Find the current levy period for this lot
    levy_period = await db.financial_periods.find_one(
        {
            "building_id": building_id,
            "state": {"$in": ["open", "closed"]},
            "period_start": {"$lte": settlement_dt},
            "period_end": {"$gte": settlement_dt},
        }
    )

    if not levy_period:
        return {
            "lot_id": lot_id,
            "building_id": building_id,
            "settlement_date": date,
            "outgoing_owner": outgoing_owner.get("owner_name"),
            "adjustment": None,
            "message": "No active levy period found covering the settlement date.",
        }

    period_start: datetime = levy_period["period_start"]
    period_end: datetime = levy_period["period_end"]
    if period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=timezone.utc)
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=timezone.utc)

    period_days = (period_end - period_start).days or 1
    outgoing_days = (settlement_dt - period_start).days
    incoming_days = period_days - outgoing_days

    total_levy_cents = levy_period.get("levy_cents", 0)
    outgoing_cents = round(total_levy_cents * outgoing_days / period_days)
    incoming_cents = total_levy_cents - outgoing_cents

    return {
        "lot_id": lot_id,
        "building_id": building_id,
        "settlement_date": date,
        "outgoing_owner": outgoing_owner.get("owner_name"),
        "period_start": period_start.date().isoformat(),
        "period_end": period_end.date().isoformat(),
        "period_days": period_days,
        "outgoing_days": outgoing_days,
        "incoming_days": incoming_days,
        "total_levy_cents": total_levy_cents,
        "outgoing_levy_cents": outgoing_cents,
        "incoming_levy_cents": incoming_cents,
        "outgoing_levy_aud": f"{outgoing_cents / 100:.2f}",
        "incoming_levy_aud": f"{incoming_cents / 100:.2f}",
    }


@router.get("/{lot_id}/history")
async def get_lot_ownership_history(
        lot_id: str,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return all ownership periods for a lot (admin/manager only).

    ``building_id`` is resolved from the authenticated session via
    ``get_current_building`` — any ``?building_id=`` query value is ignored.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised.")

    history = await get_ownership_history(lot_id, building_id, db=db)
    for period in history:
        period.pop("_id", None)
    return {"lot_id": lot_id, "building_id": building_id, "ownership_history": history}
