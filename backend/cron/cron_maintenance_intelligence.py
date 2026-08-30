"""
Nightly Maintenance Intelligence Recomputation Task
"""

import os
import sys
from datetime import datetime, timezone, timedelta, date

import asyncio
import logging

# Insert the project root (parent of cron/) so `from database import db` resolves correctly.
# The old append() resolved to backend/backend — a non-existent directory — making all
# imports fail silently and causing this entire cron to be a production no-op.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from database import db
    from services.maintenance_intelligence_service import recompute_all_maintenance_intelligence
    from services.capital_shock_service import detect_capital_shocks
    from services.levy_fairness_service import simulate_levy_fairness
    from services.forecast_service import generate_levy_projection

    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def run_recompute():
    """Generated function header.

    Function: run_recompute
    Path: backend/cron/cron_maintenance_intelligence.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not DATABASE_AVAILABLE:
        logger.error("Database or services not available. Skipping.")
        return

    # Atomic execution lock using MongoDB
    lock_key = "cron_maintenance_intelligence_lock"
    now = datetime.now(timezone.utc)
    lock_expiry = now - timedelta(hours=1)

    # Atomic update to acquire lock:
    # Match if lock doesn't exist OR it's older than 1 hour.
    # Uses 'key' as the unique shard/index key.
    try:
        # Note: update_one with upsert=True and an $or filter can be tricky.
        # If no document matches, it will use the filter to build the new doc,
        # which might lead to unexpected results if the filter contains $or.
        # Better: use a simple query on 'key' and check 'acquired_at' in the filter.
        res = await db.locks.update_one(
            {
                "key": lock_key,
                "$or": [
                    {"acquired_at": {"$lt": lock_expiry.isoformat()}},
                    {"acquired_at": {"$exists": False}}
                ]
            },
            {"$set": {"acquired_at": now.isoformat(), "worker": "default"}},
            upsert=True
        )

        # If modified_count=0 and upserted_id=None, it means the document existed
        # but didn't match the "expired" filter (i.e., someone else is running it).
        if res.modified_count == 0 and res.upserted_id is None:
            logger.warning("Maintenance Intelligence recompute already running or locked. Skipping.")
            return
    except Exception as e:
        # Handle potential DuplicateKeyError if two processes upsert at the exact same time
        if "duplicate key" in str(e).lower():
            logger.warning("Race condition detected: lock recently acquired. Skipping.")
            return
        raise

    logger.info("Starting nightly maintenance intelligence recompute...")
    try:
        # Fetch all active buildings so every tenant is recomputed, not just East Gate.
        buildings_cursor = db._db.buildings.find(
            {"is_active": {"$ne": False}},
            {"building_id": 1, "plan_id": 1},
        )
        buildings = await buildings_cursor.to_list(length=200)
        building_ids = [
            b.get("building_id") or b.get("plan_id")
            for b in buildings
            if b.get("building_id") or b.get("plan_id")
        ]
        if not building_ids:
            logger.warning("No active buildings found — skipping recompute.")
            return

        year_str = str(date.today().year)
        for bid in building_ids:
            try:
                await recompute_all_maintenance_intelligence(bid)
                await simulate_levy_fairness(bid)
                await detect_capital_shocks(bid)
                await generate_levy_projection(year_str, bid)
            except Exception as exc:
                logger.error("Recompute failed for building %s: %s", bid, exc)

        logger.info("Nightly recompute completed for %d buildings.", len(building_ids))
    except Exception as e:
        logger.error(f"Nightly recompute failed: {e}")
    finally:
        # Release lock
        await db.locks.delete_one({"key": lock_key})


if __name__ == "__main__":
    asyncio.run(run_recompute())
