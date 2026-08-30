#!/usr/bin/env python3
"""
Occupancy Recompute Cron Job
============================
Nightly at 02:00 AM — recomputes occupancy classification for ALL buildings
that have the occupancy_intelligence feature toggle enabled.

Cron schedule: 0 2 * * *
Command:
    cd /path/to/backend && venv/bin/python3 cron/cron_occupancy_recompute.py

What it does:
  1. Fetches all buildings that have the occupancy_intelligence toggle enabled.
  2. For each building, calls recompute_building_occupancy().
  3. Writes a monthly snapshot to occupancy_snapshots for trend charts.
  4. Logs success/failure per building.

Multi-tenancy: building_id is sourced from the feature_toggles collection,
  never from environment variables or hardcoded values.

Feature guard: buildings with the toggle disabled are silently skipped.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27018")
DB_NAME = os.getenv("DB_NAME", "strata_production")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [occupancy-cron] %(message)s",
)
logger = logging.getLogger(__name__)


async def _get_enabled_buildings(db) -> list[str]:
    """Return building_ids where occupancy_intelligence toggle is enabled."""
    cursor = db.feature_toggles.find(
        {"feature_key": "occupancy_intelligence", "is_enabled": True},
        {"_id": 0, "building_id": 1},
    )
    building_ids = []
    async for doc in cursor:
        bid = doc.get("building_id")
        if bid is None:
            # Global default = enabled — fetch all distinct building_ids from units
            all_buildings = await db.units.distinct("building_id")
            building_ids.extend(all_buildings)
        elif bid not in building_ids:
            building_ids.append(bid)
    return list(set(building_ids))


async def run(db) -> None:
    """Main entry point — recomputes all enabled buildings."""
    from services.occupancy_recompute import recompute_building_occupancy  # local import avoids path issues

    enabled_buildings = await _get_enabled_buildings(db)
    if not enabled_buildings:
        logger.info("No buildings have occupancy_intelligence enabled — nothing to do.")
        return

    logger.info(f"Recomputing occupancy for {len(enabled_buildings)} building(s): {enabled_buildings}")

    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    success, failures = 0, 0

    for building_id in enabled_buildings:
        try:
            result = await recompute_building_occupancy(building_id=building_id, db=db)

            # Write monthly snapshot for trend charts
            await db.occupancy_snapshots.update_one(
                {"building_id": building_id, "month": month_key},
                {"$set": {
                    "building_id": building_id,
                    "month": month_key,
                    "tenant_count": result.get("tenant", 0),
                    "owner_occupied_count": result.get("owner_occupied", 0),
                    "investor_unknown_count": result.get("investor_unknown", 0),
                    "total": result.get("total_lots", 0),
                    "updated_at": datetime.now(timezone.utc),
                }},
                upsert=True,
            )
            logger.info(
                f"  ✓ {building_id}: "
                f"tenant={result.get('tenant', 0)}, "
                f"owner={result.get('owner_occupied', 0)}, "
                f"unknown={result.get('investor_unknown', 0)}, "
                f"total={result.get('total_lots', 0)}"
            )
            success += 1
        except Exception as exc:  # noqa: BLE001
            logger.error(f"  ✗ {building_id}: {exc}", exc_info=True)
            failures += 1

    logger.info(f"Occupancy recompute complete: {success} succeeded, {failures} failed.")


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/cron/cron_occupancy_recompute.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]
    try:
        await run(db)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
