# @featuretrace:occupancy_intelligence — Batch recompute engine for occupancy status
# Layer: service
# Data flow: units + rental_certificates + lot_ownerships → occupancy_status (building-scoped)
# Related: backend/routers/occupancy.py
#           backend/services/occupancy_service.py
#           backend/cron/cron_occupancy_recompute.py

"""
Occupancy Recompute Engine
===========================
Processes ALL lots for a given building and upserts the occupancy_status
collection.  Called by:
  - cron_occupancy_recompute.py  (nightly at 02:00)
  - POST /occupancy/recompute    (manual trigger, admin only)

building_id is ALWAYS sourced from the JWT — never from caller input.
"""

from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.settings_service import get_general_settings_or_default
from services.occupancy_service import classify_occupancy

logger = logging.getLogger(__name__)

# ── Building address lookup table (cached after first fetch) ──────────────────
_BUILDING_ADDRESS_CACHE: Dict[str, Optional[str]] = {}


async def _get_building_address(building_id: str, db) -> Optional[str]:
    """Return the street address for a building from the settings collection."""
    if building_id in _BUILDING_ADDRESS_CACHE:
        return _BUILDING_ADDRESS_CACHE[building_id]
    doc = await get_general_settings_or_default(building_id, {"_id": 0})
    addr = None
    if doc:
        addr = doc.get("building_address") or doc.get("street_address") or doc.get("address")
    _BUILDING_ADDRESS_CACHE[building_id] = addr
    return addr


async def _fetch_active_rental_certs(building_id: str, db) -> Dict[str, List[Dict[str, Any]]]:
    """Return a mapping of {unit_number: [cert_docs]} for active certs."""
    by_unit: Dict[str, List[Dict[str, Any]]] = {}
    cursor = db.rental_certificates.find(
        {"building_id": building_id, "status": "issued"},
        {"_id": 0, "unit_number": 1, "lot_number": 1, "status": 1, "cert_number": 1},
    )
    async for cert in cursor:
        key = cert.get("unit_number") or cert.get("lot_number")
        if key:
            by_unit.setdefault(key, []).append(cert)
    return by_unit


async def _fetch_registered_users(building_id: str, db) -> Dict[str, Dict[str, Any]]:
    """Return a mapping of {unit_number: user_doc} for approved residents.

    Fetches users that are approved AND have a unit_number, regardless of whether
    the unit_number field was indexed. Includes role so the classifier can detect
    registered tenants that have no rental certificate yet.
    """
    by_unit: Dict[str, Dict[str, Any]] = {}
    # Note: some users may not have building_id set if registered before the
    # multi-tenant migration — fall back to matching on unit_number alone for
    # the primary building, then filter by building_id where present.
    cursor = db.users.find(
        {
            "$or": [
                {"building_id": building_id, "is_approved": True},
                {"building_id": {"$exists": False}, "is_approved": True},
            ],
            "unit_number": {"$exists": True, "$ne": None, "$ne": ""},
        },
        {"_id": 0, "unit_number": 1, "address": 1, "mailing_address": 1, "role": 1, "building_id": 1},
    )
    async for user in cursor:
        # Exclude users explicitly scoped to a different building
        user_bid = user.get("building_id")
        if user_bid and user_bid != building_id:
            continue
        unit = user.get("unit_number")
        if unit and unit not in by_unit:
            by_unit[unit] = user
    return by_unit


async def recompute_building_occupancy(building_id: str, db) -> Dict[str, Any]:
    """Classify all lots for *building_id* and upsert occupancy_status.

    Returns a summary dict with counts and timing.
    """
    now = datetime.now(timezone.utc)
    logger.info(f"[occupancy] Recomputing for building_id={building_id}")

    # Load all supporting data in parallel-ish (sequential is fine for Motor)
    building_address = await _get_building_address(building_id, db)
    active_certs = await _fetch_active_rental_certs(building_id, db)
    registered_users = await _fetch_registered_users(building_id, db)

    # Fetch all units for this building
    units: List[Dict[str, Any]] = await db.units.find(
        {"building_id": building_id},
        {"_id": 0},
    ).to_list(length=None)

    counts = {"tenant": 0, "owner_occupied": 0, "investor_unknown": 0, "errors": 0}

    for unit in units:
        lot_number = unit.get("unit_number") or unit.get("lot_number")
        if not lot_number:
            continue

        # Assemble owner_data — inject registered user for address matching
        owner_data: Dict[str, Any] = {
            k: unit.get(k)
            for k in (
                "owner_name", "owner_name_b",
                "contact_address", "mailing_address", "postal_address",
            )
        }
        reg_user = registered_users.get(lot_number)
        if reg_user:
            owner_data["_registered_user"] = reg_user

        certs = active_certs.get(lot_number, [])

        try:
            status, confidence, sources = classify_occupancy(
                lot=unit,
                rental_certificates=certs,
                owner_data=owner_data,
                building_address=building_address,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[occupancy] classify error for {lot_number}: {exc}")
            counts["errors"] += 1
            continue

        counts[status] = counts.get(status, 0) + 1

        doc = {
            "building_id": building_id,
            "lot_number": lot_number,
            "unit_type": unit.get("unit_type") or unit.get("property_type"),
            "status": status,
            "confidence": round(confidence, 4),
            "sources": sources,
            "last_verified": now,
            "updated_at": now,
        }

        # Upsert: update if exists, insert with new id if not
        existing = await db.occupancy_status.find_one(
            {"building_id": building_id, "lot_number": lot_number},
            {"_id": 0, "id": 1},
        )
        if existing:
            await db.occupancy_status.update_one(
                {"building_id": building_id, "lot_number": lot_number},
                {"$set": doc},
            )
        else:
            await db.occupancy_status.insert_one({
                "id": str(uuid.uuid4()),
                "created_at": now,
                **doc,
            })

    total = len(units)
    logger.info(
        f"[occupancy] Done for {building_id}: "
        f"tenant={counts['tenant']}, owner={counts['owner_occupied']}, "
        f"unknown={counts['investor_unknown']}, errors={counts['errors']}, "
        f"total={total}"
    )
    return {
        "building_id": building_id,
        "total_lots": total,
        **counts,
        "computed_at": now.isoformat(),
    }
