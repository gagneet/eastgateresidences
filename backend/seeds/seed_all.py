"""
Master Seed Script Runner for Maintenance Intelligence
"""

import asyncio
import logging

from seeds.buildings import seed_buildings
from seeds.seed_asset_templates import seed_asset_templates
from seeds.seed_building_summaries import seed_building_summaries
from seeds.seed_demo_building import seed_demo_building
from seeds.seed_demo_enrichment import seed_demo_enrichment
from seeds.seed_demo_finance import seed_demo_finance
from seeds.seed_demo_intelligence_dataset import seed_demo_intelligence_dataset
from seeds.seed_demo_workorders import seed_demo_workorders
from seeds.seed_mega_complex import seed_mega_complex
from seeds.seed_sierra import seed_sierra

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_all_seeds():
    """Generated function header.

    Function: run_all_seeds
    Path: backend/seeds/seed_all.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logger.info("Starting master seeding process...")

    await seed_buildings()  # Buildings must be seeded first
    await seed_sierra()  # Sierra (16244) users, EC, emergency, content
    # Harbourview (18932) removed 2026-08-20 — synthetic seed building with no users,
    # payments or documents. Re-adding this call would recreate it.
    await seed_demo_enrichment()  # Sierra: levies, ledger, announcements, meetings, events
    await seed_asset_templates()
    await seed_demo_building()
    await seed_demo_workorders()
    await seed_demo_finance()
    await seed_demo_intelligence_dataset()
    await seed_mega_complex()
    await seed_building_summaries()  # Compute building_summaries for all active buildings

    logger.info("Master seeding process completed.")


if __name__ == "__main__":
    asyncio.run(run_all_seeds())
