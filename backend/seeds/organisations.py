"""
Seed organisations and organisation_buildings collections — B2-04.

Idempotent: uses $setOnInsert so running multiple times is safe.
Creates the "Silverfox Technologies" organisation and maps it to
East Gate Residences (Strata Plan 13195).

Usage:
    cd backend && python -m seeds.organisations
"""
from __future__ import annotations

import os
import sys

import asyncio

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from datetime import datetime, timezone
from database import _db

DB_NAME = os.environ.get("DB_NAME", "strata_production")


async def seed() -> None:
    """Generated function header.

    Function: seed
    Path: backend/seeds/organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    db = _db[DB_NAME]

    now = datetime.now(timezone.utc).isoformat()

    org = {
        "id": "org-silverfox-001",
        "name": "Silverfox Technologies",
        "abn": "",
        "custom_domain": "eastgateresidences.com.au",
        "state": "ACT",
        "licence_type": "strata_manager",
        "created_at": now,
    }
    await db.organisations.update_one(
        {"id": org["id"]},
        {"$setOnInsert": org},
        upsert=True,
    )

    org_building = {
        "id": "ob-eastgate-13195",
        "organisation_id": "org-silverfox-001",
        "building_id": "13195",
        "building_name": "East Gate Residences",
        "assigned_at": now,
        "is_active": True,
    }
    await db.organisation_buildings.update_one(
        {
            "organisation_id": org_building["organisation_id"],
            "building_id": org_building["building_id"],
        },
        {"$setOnInsert": org_building},
        upsert=True,
    )

    print("✓ Organisations seeded (idempotent).")


if __name__ == "__main__":
    asyncio.run(seed())
