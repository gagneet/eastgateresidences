#!/usr/bin/env python3
# @featuretrace:demo_bank — finish an archival sweep that missed 176 debit rows.
# Layer: script
# Data flow: demo_bank_reconstruction_batches (status=superseded) -> the batch's own
#            demo_bank_transactions -> is_archived=True (building-scoped).
# Related: backend/integrations/demo_bank/ingestion.py
#          docs/architecture/mongo_postgres_finance_sync.md
# Collection: demo_bank_transactions, demo_bank_reconstruction_batches
# Tests: tests/backend/test_archive_superseded_reconstruction_debits.py
"""Archive Demo Bank rows belonging to a batch that has already been superseded.

The defect
----------
Reconstruction batch ``9990ae47-7b75-4e84-997f-0784c4939077`` was superseded on
2026-07-31 by the 2021-2026 historical financial rebuild. Its own record says
exactly what it is:

    reconstruction_method : generate_from_budget_v1
    status                : superseded
    warnings              : "these were synthetic budget-modelled transactions,
                             never real bank evidence."
    review_notes          : "Approving to proceed to Demo Bank staging only;
                             sync/GL posting withheld pending further data
                             refinement."

The sweep that archived it reached **2,122 of its 2,298 rows** — every credit
(2,088) and 34 of the 210 debits — and missed **176 debits totalling
$1,219,804.79**. Nothing failed: those rows carry ``sync_error=None`` and
``last_sync_attempt_at=None``, meaning no sync ever *attempted* them. They simply
sat in ``sync_status="pending"`` for a month.

Why that mattered more than it looks
------------------------------------
Anything counting "pending Demo Bank rows" as an intake backlog counted these,
and reported a $1.2M queue of unposted expenses that does not exist. The rows are
not owed to the ledger — they were explicitly withheld from it, and the evidence
they were modelled from has since been replaced by audited source documents. They
are also the *entire* explanation for a duplicate-key signal on live Demo Bank
data: 24 of them collide with rows that legitimately synced from the rebuild.

Archiving them is finishing a job that was already decided and half-done, not
making a new decision about real money.

Why archive and not delete
--------------------------
ACT/NSW retention is seven years and this codebase never hard-deletes financial
records — the sibling rows were soft-archived, and matching them exactly keeps
the batch internally consistent and auditable. Archiving already removes the rows
from every production query (each filters ``is_archived``), so a delete would buy
nothing and forfeit the audit trail.

Safety
------
* Dry-run by default; ``--apply`` required.
* Scoped to batches whose own ``status`` is ``superseded`` — it will not touch a
  live batch even if asked, so a mistyped batch id is inert rather than harmful.
* Idempotent: rows already archived are skipped, so a re-run is a no-op.
* Writes only ``is_archived`` / ``archived_at`` / ``archived_by`` /
  ``archived_reason``. Never deletes, never posts, never touches ``finance.*``.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

ARCHIVE_ACTOR = "system-data-repair@system.strataos.local"


def _fmt(cents: int) -> str:
    return f"${cents / 100:,.2f}"


async def run(building_id: str, batch_id: str, apply: bool) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "strataos_production")]
    try:
        batch = await db.demo_bank_reconstruction_batches.find_one(
            {"batch_id": batch_id, "building_id": building_id}, {"_id": 0}
        )
        if not batch:
            raise SystemExit(f"No reconstruction batch {batch_id!r} for building {building_id!r}")

        status = batch.get("status")
        if status != "superseded":
            # Refuse rather than warn. Archiving a live batch's rows would remove
            # real evidence from every production query.
            raise SystemExit(
                f"Batch {batch_id} has status={status!r}, not 'superseded'. "
                "This script only finishes an archival that was already decided."
            )

        # Reuse the batch's own stated reason verbatim, so the 176 rows carry the
        # identical provenance string as the 2,122 archived alongside them. A
        # freshly-worded reason would make them look like a separate event.
        reason = (
            batch.get("failure_reason")
            or (batch.get("warnings") or [None])[0]
            or "Superseded batch"
        )

        pending = await db.demo_bank_transactions.find(
            {"building_id": building_id, "source_batch_id": batch_id,
             "is_archived": {"$ne": True}},
            {"_id": 0, "amount_cents": 1, "direction": 1, "effective_date": 1},
        ).to_list(10000)

        total = sum(int(r.get("amount_cents") or 0) for r in pending)
        already = await db.demo_bank_transactions.count_documents(
            {"building_id": building_id, "source_batch_id": batch_id, "is_archived": True}
        )

        print("=" * 76)
        print(f"Archive superseded batch rows — building {building_id}"
              f"  [{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 76)
        print(f"  batch                    : {batch_id}")
        print(f"  batch status             : {status}")
        print(f"  reconstruction_method    : {batch.get('reconstruction_method')}")
        print(f"  already archived         : {already}")
        print(f"  still un-archived        : {len(pending)}  {_fmt(total)}")
        if pending:
            dirs: dict[str, int] = {}
            for r in pending:
                dirs[str(r.get("direction"))] = dirs.get(str(r.get("direction")), 0) + 1
            print(f"  by direction             : {dirs}")
            dates = [str(r.get("effective_date"))[:10] for r in pending]
            print(f"  effective_date range     : {min(dates)} -> {max(dates)}")
        print(f"\n  reason to be stamped     : {reason[:100]}")

        if not apply:
            print("\n  DRY-RUN — re-run with --apply to archive.")
            return 0

        result = await db.demo_bank_transactions.update_many(
            {"building_id": building_id, "source_batch_id": batch_id,
             "is_archived": {"$ne": True}},
            {
                "$set": {
                    "is_archived": True,
                    "archived_at": datetime.now(timezone.utc),
                    "archived_by": ARCHIVE_ACTOR,
                    "archived_reason": reason,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        print(f"\n  ARCHIVED {result.modified_count} rows, {_fmt(total)}.")
        return result.modified_count
    finally:
        client.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.building_id, args.batch_id, args.apply))


if __name__ == "__main__":
    main()
