"""
migration_009_rollback.py

Drops the three collections created by migration_009_create_integration_collections.py:

  - integration_inbox
  - event_log
  - mock_biller_allocations

WARNING: This is a destructive operation. All documents and indexes in these
collections will be permanently deleted. Only run this rollback if you are
certain no production data has been written to these collections.

Idempotent: if a collection does not exist, the drop is a no-op (MongoDB
drop_collection() on a non-existent collection succeeds silently).

Run:
    cd backend && python3 scripts/migrations/migration_009_rollback.py
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))
load_dotenv(backend_dir / ".env")

MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME")

COLLECTIONS_TO_DROP = [
    "integration_inbox",
    "event_log",
    "mock_biller_allocations",
]


async def run() -> None:
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_009_rollback.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    print(f"Migration 009 ROLLBACK — dropping integration collections on db={DB_NAME!r}")
    print()
    print("WARNING: This will permanently delete the following collections and all their data:")
    for coll in COLLECTIONS_TO_DROP:
        count = await db[coll].count_documents({})
        print(f"  {coll}  ({count} documents)")

    print()
    confirm = input("Type 'yes' to confirm rollback, anything else to abort: ").strip().lower()
    if confirm != "yes":
        print("Rollback aborted.")
        client.close()
        return

    print()
    for coll in COLLECTIONS_TO_DROP:
        await db.drop_collection(coll)
        print(f"  Dropped: {coll}")

    print("\nMigration 009 rollback complete.")
    client.close()


if __name__ == "__main__":
    asyncio.run(run())
