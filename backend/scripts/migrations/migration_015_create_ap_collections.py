"""
migration_015_create_ap_collections.py — Create AP pipeline collections.

Collections:
  invoice_documents    — supplier invoices through the AP lifecycle
  recurring_bill_templates — scheduled recurring invoice generation
  abn_validation_cache — 90-day TTL cache for ATO ABR lookups

payment_runs already created in migration_009 (Phase 1 ABA writer).

Idempotent — safe to re-run.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_migration(db) -> None:
    """Generated function header.

    Function: run_migration
    Path: backend/scripts/migrations/migration_015_create_ap_collections.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    existing = await db.list_collection_names()

    # ── invoice_documents ─────────────────────────────────────────────────────
    if "invoice_documents" not in existing:
        await db.create_collection("invoice_documents")
        logger.info("Created collection: invoice_documents")

    await db.invoice_documents.create_index(
        [("tenant_id", 1), ("vendor_abn", 1), ("invoice_number", 1)],
        unique=True,
        partialFilterExpression={
            "vendor_abn": {"$exists": True, "$type": "string"},
            "invoice_number": {"$exists": True, "$type": "string"},
        },
        name="uq_tenant_abn_invoice",
        background=True,
    )
    await db.invoice_documents.create_index(
        [("tenant_id", 1), ("state", 1)],
        name="idx_tenant_state",
        background=True,
    )
    await db.invoice_documents.create_index(
        [("content_sha256", 1)],
        name="idx_content_sha256",
        background=True,
    )
    logger.info("Indexes ensured: invoice_documents")

    # ── recurring_bill_templates ──────────────────────────────────────────────
    if "recurring_bill_templates" not in existing:
        await db.create_collection("recurring_bill_templates")
        logger.info("Created collection: recurring_bill_templates")

    await db.recurring_bill_templates.create_index(
        [("tenant_id", 1), ("is_active", 1)],
        name="idx_tenant_active",
        background=True,
    )
    logger.info("Indexes ensured: recurring_bill_templates")

    # ── abn_validation_cache ──────────────────────────────────────────────────
    if "abn_validation_cache" not in existing:
        await db.create_collection("abn_validation_cache")
        logger.info("Created collection: abn_validation_cache")

    await db.abn_validation_cache.create_index(
        [("abn", 1)],
        unique=True,
        name="uq_abn",
        background=True,
    )
    # TTL: 90 days (7,776,000 seconds)
    await db.abn_validation_cache.create_index(
        [("cached_at", 1)],
        expireAfterSeconds=7_776_000,
        name="ttl_cached_at",
        background=True,
    )
    logger.info("Indexes ensured: abn_validation_cache")

    logger.info("migration_015 complete")


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_015_create_ap_collections.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "strata_management")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        await run_migration(db)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
