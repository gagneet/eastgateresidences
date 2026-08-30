"""
Seed building_summaries for all active buildings.

Calls _compute_summary() from community_dashboard directly so that each
building gets a real computed summary stored in the DB.  Safe to run
multiple times (upserts).

Run standalone:
    cd backend && python -m seeds.seed_building_summaries
"""

import asyncio
import logging

try:
    from database import db
except ImportError:
    db = None

logger = logging.getLogger(__name__)


async def seed_building_summaries():
    """Compute and upsert building_summaries for every active building."""
    if db is None:
        logger.error("No DB connection available")
        return

    # Import here to avoid circular imports at module load time
    from routers.community_dashboard import _compute_summary
    from request_context import set_ctx_building_id

    buildings = await db._db.buildings.find({"is_active": True}).to_list(100)
    if not buildings:
        logger.warning("No active buildings found — skipping building_summaries seed")
        return

    for bld in buildings:
        bid = bld.get("id", str(bld.get("_id", "")))
        name = bld.get("name", bid)
        try:
            # Set the tenant context so TenantCollection scoping works outside a request
            set_ctx_building_id(bid)
            summary = await _compute_summary(bid)
            health = summary.get("health_score", "?")
            grade = summary.get("health_grade", "?")
            logger.info(
                "Seeded building_summaries for %s (%s): score=%s grade=%s",
                name, bid, health, grade
            )
            print(f"  ✓ {name} ({bid}): health={health} grade={grade}")
        except Exception as exc:
            logger.error("Failed to compute summary for %s (%s): %s", name, bid, exc)
            print(f"  ✗ {name} ({bid}): ERROR — {exc}")
        finally:
            set_ctx_building_id(None)

    total = await db._db.building_summaries.count_documents({})
    print(f"\nTotal building_summaries documents: {total}")


if __name__ == "__main__":
    asyncio.run(seed_building_summaries())
