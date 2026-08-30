"""
Migration 017 — Populate plan_number and strata_address in db.settings

Three buildings had incomplete settings documents that caused generate_levy_notice_pdf
and generate_rental_certificate_pdf to fall back to hardcoded East Gate values:

  East Gate (13195): plan_number and strata_address were empty strings.
  Sierra (16244):    plan_number was absent; building_address was correct.
  Harbourview (18932): plan_number was absent; building_address was correct.

This migration sets plan_number and strata_address (East Gate only — Sierra and
Harbourview fall back to building_address via the pdf_generator fallback chain,
so no strata_address write is needed for them).

Values confirmed against authoritative sources:
  13195 — UP13195 strata plan document (user-verified 2026-04-25)
  16244 — seed_sierra.py + snapshot_settings.py
  18932 — seed_data.py + snapshot_settings.py + snapshot_buildings.py

Idempotent: $set is a no-op if fields already have the correct values.

Run:
  cd backend && python3 scripts/migrations/migration_017_populate_building_plan_number_strata_address.py

Dry-run (no writes):
  DRY_RUN=1 python3 scripts/migrations/migration_017_populate_building_plan_number_strata_address.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

import motor.motor_asyncio

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

MIGRATION_ID = "migration_017_populate_building_plan_number_strata_address"

UPDATES = [
    {
        "building_id": "13195",
        "label": "East Gate Residences",
        "fields": {
            "plan_number": "13195",
            "strata_address": "14 Hoolihan Street, Denman Prospect ACT 2611",
        },
        "source": "UP13195 strata plan document (user-verified 2026-04-25)",
    },
    {
        "building_id": "16244",
        "label": "Sierra Gungahlin",
        "fields": {
            "plan_number": "16244",
        },
        "source": "seed_sierra.py + snapshot_settings.py",
    },
    {
        "building_id": "18932",
        "label": "Harbourview Residences",
        "fields": {
            "plan_number": "18932",
        },
        "source": "seed_data.py + snapshot_settings.py + snapshot_buildings.py",
    },
]


async def run():
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_017_populate_building_plan_number_strata_address.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = motor.motor_asyncio.AsyncIOMotorClient(os.getenv("MONGO_URL"))
    db = client[os.getenv("DB_NAME", "strata")]

    now = datetime.now(timezone.utc).isoformat()
    logger.info(f"DRY_RUN={DRY_RUN}")

    for spec in UPDATES:
        bid = spec["building_id"]
        label = spec["label"]
        fields = spec["fields"]
        source = spec["source"]

        existing = await db.settings.find_one({"building_id": bid}, {"_id": 0, **{f: 1 for f in fields}})
        if existing is None:
            logger.warning(f"  [{bid}] No settings document found — will upsert")
        else:
            current = {f: existing.get(f) for f in fields}
            logger.info(f"  [{bid}] {label} — current: {current}")

        payload = {**fields, "migrated_at": now, "migrated_by": MIGRATION_ID}
        logger.info(
            f"  {'[DRY]' if DRY_RUN else '[WRITE]'} [{bid}] {label} → $set {fields}  (source: {source})"
        )

        if not DRY_RUN:
            result = await db.settings.update_one(
                {"building_id": bid},
                {"$set": payload},
                upsert=True,
            )
            matched = result.matched_count
            modified = result.modified_count
            upserted = result.upserted_id is not None
            logger.info(f"  [{bid}] matched={matched} modified={modified} upserted={upserted}")

    logger.info("Migration 017 complete" if not DRY_RUN else "Dry-run complete — no writes performed")
    client.close()


if __name__ == "__main__":
    asyncio.run(run())
