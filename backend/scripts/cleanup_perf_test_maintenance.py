"""
One-time cleanup: remove maintenance requests created by the performance benchmark.

These entries have titles matching "Perf test request perf-*" and were created by
tests/performance/maintenance_benchmark.ts without a teardown step.

Usage:
    cd backend && python3 scripts/cleanup_perf_test_maintenance.py [--dry-run]
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

PERF_TITLE_PREFIX = "Perf test request perf-"


async def main(dry_run: bool = False) -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/cleanup_perf_test_maintenance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"title": {"$regex": r"^Perf test request perf-"}}
    docs = await db.maintenance_requests.find(query, {"id": 1, "title": 1, "building_id": 1, "created_at": 1}).to_list(
        None)

    if not docs:
        print("No perf test maintenance requests found.")
        return

    print(f"Found {len(docs)} perf test request(s):")
    for doc in docs:
        print(f"  [{doc.get('building_id')}] {doc.get('id')} — {doc.get('title')} (created {doc.get('created_at')})")

    if dry_run:
        print("\nDry run — nothing deleted.")
        return

    result = await db.maintenance_requests.delete_many(query)
    print(f"\nDeleted {result.deleted_count} record(s).")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry_run))
