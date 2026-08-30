"""
Nightly adaptive navigation score recompute.
Schedule: 03:00 AEST (17:00 UTC)

Uses a single Motor client throughout — passes the raw db handle to
compute_scores_for_building() so the service does not create a second connection.
"""
import os
import sys

import asyncio
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27018")
DB_NAME = os.environ.get("DB_NAME", "strata_production")


async def main():
    """Generated function header.

    Function: main
    Path: backend/cron/cron_adaptive_nav.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from pymongo import AsyncMongoClient
    client = AsyncMongoClient(MONGO_URL)
    raw_db = client[DB_NAME]

    buildings = await raw_db.buildings.find(
        {"is_active": {"$ne": False}},
        {"_id": 0, "id": 1, "name": 1}
    ).to_list(length=None)

    logger.info(f"Recomputing adaptive nav scores for {len(buildings)} buildings")

    from services.adaptive_nav_service import compute_scores_for_building

    total_users = 0
    for building in buildings:
        building_id = building["id"]
        try:
            # Pass raw_db so the service uses the same connection
            count = await compute_scores_for_building(building_id, raw_db=raw_db)
            total_users += count
            logger.info(f"  {building.get('name', building_id)}: {count} users updated")
        except Exception as e:
            logger.error(f"  {building_id}: FAILED — {e}")

    logger.info(f"Complete. Total users updated: {total_users}")
    client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
