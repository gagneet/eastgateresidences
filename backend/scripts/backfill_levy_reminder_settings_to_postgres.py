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
from services.levy_reminder_settings_service import (
    LEVY_REMINDER_SETTINGS_KEY,
    normalise_levy_reminder_settings,
)

logger = logging.getLogger("backfill_levy_reminder_settings_to_postgres")


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/backfill_levy_reminder_settings_to_postgres.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    synced = 0
    skipped = 0
    seen_buildings: set[str] = set()

    docs = await _db.levy_reminder_settings.find({}, {"_id": 0}).sort("building_id", 1).to_list(None)
    legacy_docs = await _db.settings.find(
        {"type": "levy_reminder_settings"},
        {"_id": 0},
    ).sort("building_id", 1).to_list(None)

    for source, source_docs in (
        ("levy_reminder_settings", docs),
        ("settings.type=levy_reminder_settings", legacy_docs),
    ):
        for doc in source_docs:
            building_id = doc.get("building_id")
            if not building_id:
                skipped += 1
                logger.warning("Skipping %s doc without building_id", source)
                continue
            if building_id in seen_buildings:
                continue

            scheme = await config_repo.resolve_scheme_context(building_id)
            if scheme is None:
                skipped += 1
                logger.warning("Skipping unresolved building_id=%s from %s", building_id, source)
                continue

            payload = normalise_levy_reminder_settings(building_id, doc)
            if payload is None:
                skipped += 1
                logger.warning("Skipping unparseable levy reminder settings for building_id=%s", building_id)
                continue

            payload.pop("reminder_days", None)
            await config_repo.upsert_building_setting(
                building_id,
                LEVY_REMINDER_SETTINGS_KEY,
                payload,
            )
            seen_buildings.add(building_id)
            synced += 1
            logger.info("Synced levy reminder settings for building_id=%s from %s", building_id, source)

    logger.info("Backfill complete: synced=%s skipped=%s", synced, skipped)


if __name__ == "__main__":
    asyncio.run(main())
