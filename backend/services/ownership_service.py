"""
Ownership Service — bitemporal ownership resolution for lots.

Provides resolve_owner(lot_id, building_id, as_at) which queries the
ownership_periods collection for the record effective at a given point in time,
returning the most recent matching period by sorting on effective_from descending.

Used by:
  - matching engine (L5 JW name match) for payment attribution after sale
  - settlement adjustment endpoint for pro-rata levy split at conveyancing
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


async def resolve_owner(
        lot_id: str,
        building_id: str,
        as_at: datetime,
        *,
        db,
) -> Optional[dict]:
    """Return the owner record effective for lot_id in building_id at as_at.

    Searches ownership_periods for the period where effective_from <= as_at
    and (effective_to is null OR effective_to > as_at).

    Returns the owner dict or None if no ownership record exists for that date.
    """
    if as_at.tzinfo is None:
        as_at = as_at.replace(tzinfo=timezone.utc)

    period = await db.ownership_periods.find_one(
        {
            "building_id": building_id,
            "lot_id": lot_id,
            "effective_from": {"$lte": as_at},
            "$or": [
                {"effective_to": None},
                {"effective_to": {"$gt": as_at}},
            ],
        },
        sort=[("effective_from", -1)],
    )
    return period


async def get_ownership_history(
        lot_id: str,
        building_id: str,
        *,
        db,
) -> list[dict]:
    """Return all ownership periods for a lot, ordered by effective_from ascending."""
    cursor = db.ownership_periods.find(
        {"building_id": building_id, "lot_id": lot_id},
    ).sort("effective_from", 1)
    return await cursor.to_list(500)


async def record_ownership_transfer(
        lot_id: str,
        building_id: str,
        new_owner: dict,
        settlement_date: datetime,
        recorded_by: str,
        *,
        db,
        is_test_data: bool = False,
) -> dict:
    """Record a change of ownership by closing the current period and opening a new one.

    settlement_date: the legal settlement date (conveyancing date).
    new_owner: dict with at minimum owner_name, owner_email, owner_id.

    Idempotent: if a period already starts at settlement_date for this lot, returns it.
    """
    if settlement_date.tzinfo is None:
        settlement_date = settlement_date.replace(tzinfo=timezone.utc)

    existing = await db.ownership_periods.find_one(
        {
            "building_id": building_id,
            "lot_id": lot_id,
            "effective_from": settlement_date,
        }
    )
    if existing:
        return existing

    # Close the current period
    await db.ownership_periods.update_many(
        {
            "building_id": building_id,
            "lot_id": lot_id,
            "effective_to": None,
        },
        {"$set": {"effective_to": settlement_date}},
    )

    now = datetime.now(timezone.utc)
    period = {
        "building_id": building_id,
        "lot_id": lot_id,
        "effective_from": settlement_date,
        "effective_to": None,
        "owner_name": new_owner.get("owner_name", ""),
        "owner_email": new_owner.get("owner_email", ""),
        "owner_id": new_owner.get("owner_id"),
        "recorded_by": recorded_by,
        "recorded_at": now,
        "is_test_data": is_test_data,
    }
    result = await db.ownership_periods.insert_one(period)
    period["_id"] = result.inserted_id
    logger.info(
        "Ownership transfer recorded: building=%s lot=%s settlement=%s",
        building_id, lot_id, settlement_date.isoformat(),
    )
    return period
