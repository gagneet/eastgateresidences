from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import _db
from db_postgres.repos import config_repo
from services.settings_service import GENERAL_SETTINGS_KEY

logger = logging.getLogger("backfill_general_settings_to_postgres")


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/backfill_general_settings_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    query = {
        "$or": [
            {"type": {"$exists": False}},
            {"type": None},
            {"type": "general"},
        ]
    }

    docs = await _db.settings.find(query).sort("building_id", 1).to_list(None)
    synced = 0
    skipped = 0

    for doc in docs:
        building_id = doc.get("building_id")
        if not building_id:
            skipped += 1
            logger.warning("Skipping general settings doc without building_id: %s", doc.get("_id"))
            continue

        payload = {k: v for k, v in doc.items() if k != "_id"}
        scheme = await config_repo.resolve_scheme_context(building_id)
        if scheme is None:
            skipped += 1
            logger.warning("Skipping unresolved building_id=%s", building_id)
            continue

        await config_repo.upsert_building_setting(
            building_id,
            GENERAL_SETTINGS_KEY,
            payload,
        )
        synced += 1
        logger.info("Synced general settings for building_id=%s", building_id)

    logger.info("Backfill complete: synced=%s skipped=%s", synced, skipped)


if __name__ == "__main__":
    asyncio.run(main())
