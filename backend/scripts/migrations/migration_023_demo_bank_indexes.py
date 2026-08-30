"""Create Demo Bank MongoDB indexes.

# @featuretrace:demo_bank — idempotent MongoDB indexes for Demo Bank staging.
# Layer: migration
# Data flow: this script → demo_bank_transactions / demo_bank_accounts / demo_bank_import_batches.

Run on the server before importing historical East Gate data:

    cd /opt/strata-management
    MONGODB_URI=mongodb://127.0.0.1:27018/strata_management \
      python3 backend/scripts/migrations/migration_023_demo_bank_indexes.py

The unique indexes are the deduplication safety net for CSV/manual/Strata Web replay.
This script is idempotent and safe to run repeatedly.
"""
from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _database_name(uri: str, fallback: str) -> str:
    """Generated function header.

    Function: _database_name
    Path: backend/scripts/migrations/migration_023_demo_bank_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parsed = urlparse(uri)
    if parsed.path and parsed.path != "/":
        return parsed.path.lstrip("/").split("?")[0]
    return fallback


async def run(db) -> None:
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_023_demo_bank_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await db.demo_bank_transactions.create_index(
        [("building_id", ASCENDING), ("idempotency_key", ASCENDING)],
        unique=True,
        background=True,
        name="uq_demo_bank_tx_idem",
    )
    await db.demo_bank_transactions.create_index(
        [("building_id", ASCENDING), ("account_ref", ASCENDING), ("posted_date", DESCENDING)],
        background=True,
        name="idx_demo_bank_tx_feed_pull",
    )
    await db.demo_bank_transactions.create_index(
        [("building_id", ASCENDING), ("sync_status", ASCENDING)],
        background=True,
        name="idx_demo_bank_tx_sync_pending",
    )
    await db.demo_bank_transactions.create_index(
        [("building_id", ASCENDING), ("provider", ASCENDING), ("external_transaction_id", ASCENDING)],
        background=True,
        name="idx_demo_bank_tx_provider_external_id",
    )
    await db.demo_bank_transactions.create_index(
        [("building_id", ASCENDING), ("source_type", ASCENDING), ("posted_date", ASCENDING)],
        background=True,
        name="idx_demo_bank_tx_source_year",
    )

    await db.demo_bank_import_batches.create_index(
        [("building_id", ASCENDING), ("file_hash", ASCENDING)],
        unique=True,
        sparse=True,
        background=True,
        name="uq_demo_bank_batch_hash",
    )
    await db.demo_bank_import_batches.create_index(
        [("building_id", ASCENDING), ("source_type", ASCENDING), ("created_at", DESCENDING)],
        background=True,
        name="idx_demo_bank_batch_source",
    )

    await db.demo_bank_accounts.create_index(
        [("building_id", ASCENDING), ("account_ref", ASCENDING)],
        unique=True,
        background=True,
        name="uq_demo_bank_account_ref",
    )


async def _close_client(client: AsyncIOMotorClient) -> None:
    """Generated function header.

    Function: _close_client
    Path: backend/scripts/migrations/migration_023_demo_bank_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = client.close()
    if inspect.isawaitable(result):
        await result


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/migrations/migration_023_demo_bank_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    uri = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27018/strata_management"
    db_name = os.environ.get("MONGODB_DB") or os.environ.get("DB_NAME") or _database_name(uri, "strata_management")
    client = AsyncIOMotorClient(uri)
    try:
        print(f"Creating Demo Bank indexes on MongoDB database: {db_name}")
        await run(client[db_name])
        print("Demo Bank indexes created/verified successfully.")
    finally:
        await _close_client(client)


if __name__ == "__main__":
    asyncio.run(main())
