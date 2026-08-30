"""
migration_010_rollback.py

Rolls back migration_010_backfill_building_id_from_plan_id.py.

WHAT THIS DOES
  Removes the building_id field from any document where plan_id is still
  present.  The logic is conservative:

    - Only removes building_id if the document ALSO has plan_id (non-null).
    - Does NOT touch documents that have building_id but no plan_id — those
      had building_id before migration_010 ran and must not be disturbed.
    - Safe to run even if migration_010 was only partially applied.

IDEMPOTENT
  Re-running this script is safe.  Documents that already had building_id
  removed (or that never had it backfilled) are not touched.

WARNING
  After this rollback the DB is in the pre-migration state: documents seeded
  by legacy scripts (enhanced_dashboard.py, levy_ledger_east_gate.py, etc.)
  will again be missing building_id.  The TenantScopedDatabase $or shim in
  database.py will still match them via plan_id, but any code that queries
  building_id directly (without going through the shim) may miss them.

CONFIRMATION PROMPT
  The script will show a preview count and require explicit confirmation before
  writing, unless --force is passed.

Run:
    cd backend && python3 scripts/migrations/migration_010_rollback.py
    cd backend && python3 scripts/migrations/migration_010_rollback.py --dry-run
    cd backend && python3 scripts/migrations/migration_010_rollback.py --force
"""

import argparse
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

# Must match migration_010 — keep in sync.
GLOBAL_COLLECTIONS = {
    "buildings",
    "users",
    "roles",
    "site_settings",
    "feature_toggles",
    "system_logs",
    "system_events",
    "integration_inbox",
    "event_log",
    "mock_biller_allocations",
}


async def run(dry_run: bool = False, force: bool = False) -> None:
    """Generated function header.

    Function: run
    Path: backend/scripts/migrations/migration_010_rollback.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"Migration 010 ROLLBACK [{mode}] — remove backfilled building_id on db={DB_NAME!r}")
    print()

    # Documents that were backfilled have BOTH plan_id AND building_id.
    # We only unset building_id where plan_id is also present (non-null),
    # which is exactly the set of documents this rollback should touch.
    backfilled_filter = {
        "plan_id": {"$exists": True, "$ne": None},
        "building_id": {"$exists": True, "$ne": None},
    }

    all_collection_infos = await db.list_collections()
    all_collections = sorted([c["name"] for c in all_collection_infos])
    tenant_collections = [c for c in all_collections if c not in GLOBAL_COLLECTIONS]

    print(f"Scanning {len(tenant_collections)} tenant-scoped collections ...")
    print()

    total_found = 0
    results = []

    for coll_name in tenant_collections:
        coll = db[coll_name]
        count = await coll.count_documents(backfilled_filter)
        if count == 0:
            continue
        total_found += count
        results.append((coll_name, count, coll))

    if not results:
        print("No documents found with both plan_id and building_id present. Nothing to roll back.")
        client.close()
        return

    print("Collections that will have building_id REMOVED (where plan_id is also present):")
    for coll_name, count, _ in results:
        print(f"  {coll_name}: {count} document(s)")
    print()
    print(f"Total: {total_found} document(s) across {len(results)} collection(s).")
    print()

    if dry_run:
        print("DRY-RUN: no changes written.")
        client.close()
        return

    if not force:
        confirm = input(
            "Type 'yes' to confirm rollback (removes building_id where plan_id exists), "
            "anything else to abort: "
        ).strip().lower()
        if confirm != "yes":
            print("Rollback aborted.")
            client.close()
            return

    print()
    total_updated = 0
    for coll_name, count, coll in results:
        result = await coll.update_many(
            backfilled_filter,
            {"$unset": {"building_id": ""}},
        )
        updated = result.modified_count
        total_updated += updated
        print(f"  {coll_name}: {count} matched → {updated} building_id field(s) removed")

    print()
    print(f"Migration 010 rollback complete: building_id removed from {total_updated} document(s).")
    client.close()


def parse_args() -> argparse.Namespace:
    """Generated function header.

    Function: parse_args
    Path: backend/scripts/migrations/migration_010_rollback.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Migration 010 rollback: remove building_id backfilled by migration_010 "
            "(i.e. documents that still have plan_id)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be rolled back without writing any changes.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(dry_run=args.dry_run, force=args.force))
