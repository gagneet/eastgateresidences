"""
Migration 020 — Jurisdictional collections and ownership periods.

Idempotent. Safe to re-run.

Creates:
  - ownership_periods (tenant-scoped): bitemporal lot ownership records
  - jurisdictional_rule_overrides (global): per-jurisdiction rule overrides

Indexes:
  - ownership_periods: unique (building_id, lot_id, effective_from),
    descending lookup index, is_test_data filter index
  - jurisdictional_rule_overrides: unique jurisdiction

Patches:
  - buildings.jurisdiction defaults to "ACT" for records missing the field
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "strata_management")


async def run_migration():
    """Generated function header.

    Function: run_migration
    Path: backend/scripts/migrations/migration_020_jurisdictional_collections.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    existing = await db.list_collection_names()

    # ── ownership_periods (tenant-scoped) ─────────────────────────────────────
    if "ownership_periods" not in existing:
        await db.create_collection("ownership_periods")
        logger.info("Created collection: ownership_periods")

    op_indexes = await db.ownership_periods.index_information()

    if "building_lot_from_unique" not in op_indexes:
        await db.ownership_periods.create_index(
            [("building_id", 1), ("lot_id", 1), ("effective_from", 1)],
            unique=True,
            name="building_lot_from_unique",
        )
        logger.info("Created index: ownership_periods.building_lot_from_unique")

    if "building_lot_from_desc" not in op_indexes:
        await db.ownership_periods.create_index(
            [("building_id", 1), ("lot_id", 1), ("effective_from", -1)],
            name="building_lot_from_desc",
        )
        logger.info("Created index: ownership_periods.building_lot_from_desc")

    if "is_test_data_idx" not in op_indexes:
        if "is_test_data_ttl" in op_indexes:
            await db.ownership_periods.drop_index("is_test_data_ttl")
            logger.info("Dropped legacy index: ownership_periods.is_test_data_ttl")
        await db.ownership_periods.create_index(
            [("is_test_data", 1)],
            name="is_test_data_idx",
        )
        logger.info("Created index: ownership_periods.is_test_data_idx")

    # ── jurisdictional_rule_overrides (global) ────────────────────────────────
    if "jurisdictional_rule_overrides" not in existing:
        await db.create_collection("jurisdictional_rule_overrides")
        logger.info("Created collection: jurisdictional_rule_overrides")

    jro_indexes = await db.jurisdictional_rule_overrides.index_information()
    if "jurisdiction_unique" not in jro_indexes:
        await db.jurisdictional_rule_overrides.create_index(
            [("jurisdiction", 1)],
            unique=True,
            name="jurisdiction_unique",
        )
        logger.info("Created index: jurisdictional_rule_overrides.jurisdiction_unique")

    # ── buildings.jurisdiction field default ──────────────────────────────────
    result = await db.buildings.update_many(
        {"jurisdiction": {"$exists": False}},
        {"$set": {"jurisdiction": "ACT"}},
    )
    if result.modified_count:
        logger.info(
            "Patched %d buildings with default jurisdiction=ACT",
            result.modified_count,
        )

    logger.info("Migration 020 complete.")
    client.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
