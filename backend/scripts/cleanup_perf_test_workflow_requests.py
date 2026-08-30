"""Remove workflow requests created by tests/performance/workflow_requests_benchmark.ts.

Why a script and not a k6 teardown()
------------------------------------
The benchmark creates its records with ``is_test_data=True`` (mandatory for anything a
test or perf script writes — see CLAUDE.md). Two facts make API-side teardown impossible:

* ``workflow_requests`` has no DELETE endpoint. Records are retained for 7 years under
  ACT/NSW rules, so one will not be added for a benchmark.
* ``PUT /workflow-requests/{id}/status`` resolves the record with
  ``{"is_test_data": {"$ne": True}}`` and therefore 404s for every row the benchmark
  creates. Closing them through the API cannot work by construction.

So the k6 teardown() verifies the rows are invisible to production queries and defers the
actual removal to this script, which talks to Mongo directly.

Safety
------
Only documents matching ALL of ``is_test_data=True`` AND the benchmark's deterministic
subject prefix are touched, so a real resident's request can never be caught by it. The
prefix must stay in sync with ``PERF_SUBJECT_PREFIX`` in the benchmark.

Usage:
    cd backend && python3 scripts/cleanup_perf_test_workflow_requests.py --dry-run
    cd backend && python3 scripts/cleanup_perf_test_workflow_requests.py
    cd backend && python3 scripts/cleanup_perf_test_workflow_requests.py --building-id 13195
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

# Keep in sync with PERF_SUBJECT_PREFIX in
# tests/performance/workflow_requests_benchmark.ts.
PERF_SUBJECT_PREFIX = "Perf test workflow-request perf-"


def _build_filter(building_id: str | None) -> dict:
    """Both conditions are required.

    ``is_test_data`` alone would sweep records written by any other test; the subject
    prefix alone would be a substring match against user-supplied text. Together they
    identify exactly what this benchmark wrote.
    """
    query: dict = {
        "is_test_data": True,
        "subject": {"$regex": f"^{PERF_SUBJECT_PREFIX}"},
    }
    if building_id:
        query["building_id"] = building_id
    return query


async def main(dry_run: bool, building_id: str | None) -> int:
    mongo_url = os.environ["MONGO_URL"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ["DB_NAME"]]
    try:
        query = _build_filter(building_id)
        matched = await db.workflow_requests.count_documents(query)
        scope = f"building {building_id}" if building_id else "all buildings"
        print(f"Matched {matched} perf workflow request(s) in {scope}.")

        if matched == 0:
            return 0

        # Surface a sample so an operator can eyeball what is about to go, rather than
        # trusting a count. A cleanup that deletes the wrong rows is unrecoverable here.
        async for doc in db.workflow_requests.find(query, {"_id": 0, "id": 1, "subject": 1,
                                                          "building_id": 1}).limit(5):
            print(f"  {doc.get('building_id')}  {doc.get('id')}  {doc.get('subject')}")
        if matched > 5:
            print(f"  … and {matched - 5} more")

        if dry_run:
            print("\n--dry-run: nothing deleted. Re-run without the flag to remove them.")
            return 0

        result = await db.workflow_requests.delete_many(query)
        print(f"\nDeleted {result.deleted_count} perf workflow request(s).")
        # A mismatch means documents changed underneath the run — report it rather than
        # letting the difference pass as success.
        if result.deleted_count != matched:
            print(
                f"WARNING: matched {matched} but deleted {result.deleted_count}. "
                "Re-run with --dry-run to inspect the remainder."
            )
            return 1
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be removed without deleting anything.")
    parser.add_argument("--building-id", default=None,
                        help="Restrict cleanup to a single building_id.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dry_run, args.building_id)))
