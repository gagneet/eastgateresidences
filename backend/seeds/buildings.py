"""
Seed script for buildings (multi-tenant).
Run standalone: python -m seeds.buildings
Or via seed_all.py
"""

from datetime import datetime, timezone

import asyncio

try:
    from database import db
except ImportError:
    db = None

NOW = datetime.now(timezone.utc).isoformat()

BUILDINGS = [
    {
        "id": "13195",
        "building_id": "13195",  # alias for tenant-scoped lookups
        "name": "East Gate Residences",
        "address": "14 Hoolihan Street, Denman Prospect ACT 2611",
        "jurisdiction": "ACT",   # ACT Unit Titles (Management) Act 2011
        "lots": 87,
        "lot_count": 87,  # normalised alias (portfolio endpoints use this field)
        "year_built": 2018,
        "is_active": True,
        "is_demo": False,
        "slug": "eastgate",
        "description": "East Gate Residences strata complex, Denman Prospect ACT.",
        "created_at": NOW,
    },
    {
        "id": "16244",
        "building_id": "16244",
        "name": "Sierra",
        "address": "70 Efkarpidis Street, Gungahlin ACT 2912",
        "jurisdiction": "ACT",   # ACT Unit Titles (Management) Act 2011
        "lots": 120,
        "lot_count": 120,
        "year_built": 2022,
        "is_active": True,
        "is_demo": False,
        "slug": "sierra",
        "description": "Sierra strata complex, Gungahlin ACT.",
        "created_at": NOW,
    },
]


async def seed_buildings():
    """Generated function header.

    Function: seed_buildings
    Path: backend/seeds/buildings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if db is None:
        print("No DB connection available")
        return

    for building in BUILDINGS:
        existing = await db.buildings.find_one({"id": building["id"]})
        if existing:
            # Update any missing or newly-added fields
            update = {}
            for field in ("is_active", "slug", "description", "is_demo",
                          "lot_count", "building_id", "jurisdiction"):
                if field not in existing:
                    update[field] = building[field]
            if update:
                await db.buildings.update_one({"id": building["id"]}, {"$set": update})
                print(f"Updated {building['name']}: {update}")
            else:
                print(f"Skipped (exists): {building['name']}")
        else:
            await db.buildings.insert_one(building)
            print(f"Added: {building['name']} @ {building['address']}")

    total = await db.buildings.count_documents({})
    print(f"\nTotal buildings: {total}")


if __name__ == "__main__":
    asyncio.run(seed_buildings())
