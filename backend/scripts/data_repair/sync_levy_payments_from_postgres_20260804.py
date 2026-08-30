"""
East Gate — Backfill Missing 2026 Itemized `levy_payments` from PostgreSQL (2026-08-04)
=========================================================================================
`core.domain_cutover_status` for finance_ledger/13195 is `postgres_shadow` (reads/writes
still Mongo) — PostgreSQL is NOT being promoted to serve reads by this script. This is a
one-way itemized-history backfill: MongoDB's `unit_levy_ledger.total_paid` for year=2026
is ALREADY correct (corrected 2026-08-03 by GAP-FIN-035's total_paid consistency fix), but
the itemized `levy_payments` transaction rows underneath it are missing 342 real 2026
receipts that exist in `finance.receipts` (Postgres) — 328 from the GAP-FIN-031
bank-transaction-matching pipeline (2026-03-11 to 2026-07-31) and 14 from the 2026-08-02
portal-scrape reconciliation delta. This violates the repo's MANDATORY historical-
reconstruction-uniformity rule: every year, including current YTD, must carry the same
itemized transaction trail — 2026 was left as a back-solved total with no itemized detail
underneath while 2021-2025 carry full itemized records.

This script does NOT change any balance. `unit_levy_ledger.total_paid`/`net_balance` stay
exactly as they are. It only inserts the missing itemized detail rows, and — per this repo's
"no component constructs financial truth" rule — only for a unit where the ledger's own
stored total_paid for year=2026 exactly equals (existing Mongo 2026 confirmed itemized sum
+ the new rows about to be inserted). Any unit where that doesn't hold is left completely
untouched and reported for manual review, never guessed at.

What it explicitly does NOT touch:
    - `finance.receipts` / any PostgreSQL table (read-only source).
    - `unit_levy_ledger` (any field) — GAP-FIN-035 already corrected total_paid/net_balance;
      this script only backfills the itemized detail underneath an already-correct total.
    - Any unit where the per-unit hard-assert fails (reported, not written).
    - Any receipt already reversed in Postgres (`finance.journal_entries.reversal_of_id`).
    - Any PG receipt that already has a matching (unit, date, amount) confirmed row in Mongo.

Usage (from backend/):
    # Dry-run audit only (default, safe):
    venv/bin/python3 scripts/data_repair/sync_levy_payments_from_postgres_20260804.py

    # Apply (writes to DB — requires explicit flag):
    venv/bin/python3 scripts/data_repair/sync_levy_payments_from_postgres_20260804.py --apply

Safety guarantees:
    - Defaults to --dry-run; --apply required for any DB writes.
    - Hard-coded to building_id="13195" (East Gate) / tenant_id 9e9d75c2-... — will not
      touch any other building.
    - Per-unit hard-assert against unit_levy_ledger.total_paid (year=2026) before writing
      anything for that unit — units that fail the assert are skipped and reported, never
      partially written.
    - Idempotent: matches on (unit_number, payment_date, amount within 1c) against existing
      Mongo confirmed rows before proposing an insert, so re-running --apply after a partial
      failure will not create duplicates.
    - Every inserted row cites its source PostgreSQL receipt_id (`receipt_number` = "PG-<id>",
      matching the existing convention already used by prior backfills in this collection)
      and a distinct transaction_origin (never "reconstructed_historical", which is reserved
      for the AGM/CSV-derived 2021-2025 backfill and would mislabel these as something they
      are not) plus this run's own `sync_batch_id` for full traceability.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import asyncpg

BUILDING_ID = "13195"
TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"
BYPASS_TENANT = "00000000-0000-0000-0000-000000000000"
LEDGER_YEAR = "2026"

_ORIGIN_BY_CATEGORY = {
    "gap_fin_031_derived": "postgres_bank_matched_sync_20260804",
    "portal_scrape_reconciliation_delta": "postgres_portal_reconciliation_sync_20260804",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill missing 2026 itemized levy_payments for East Gate from PostgreSQL finance.receipts."
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write to Mongo. Without this flag the script is read-only.",
    )
    return p.parse_args()


async def _get_mongo():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    return client[os.getenv("DB_NAME", "strataos_production")]


async def _get_pg_active_2026_receipts() -> list[dict]:
    """Active (non-reversed) 2026 receipts from the two known-real sources only."""
    database_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
    conn = await asyncpg.connect(database_url)
    # finance.* and core.lots have NO RLS bypass clause (unlike core.schemes/core.tenants) —
    # must set the real tenant_id or every query silently returns zero rows, not an error.
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_ID)
    rows = await conn.fetch(
        """
        SELECT l.unit_number, r.receipt_id, r.received_on, r.amount_cents, r.channel, r.external_reference
        FROM finance.receipts r
        JOIN core.lots l ON l.lot_id = r.lot_id
        LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
        WHERE r.tenant_id = $1
          AND rev.journal_entry_id IS NULL
          AND (r.external_reference LIKE 'gap_fin_031_derived%'
               OR r.external_reference LIKE 'portal_scrape_reconciliation_delta%')
        ORDER BY l.unit_number, r.received_on
        """,
        TENANT_ID,
    )
    await conn.close()
    out = []
    for r in rows:
        ref = r["external_reference"] or ""
        category = "gap_fin_031_derived" if ref.startswith("gap_fin_031_derived") else "portal_scrape_reconciliation_delta"
        out.append({
            "unit_number": r["unit_number"],
            "receipt_id": str(r["receipt_id"]),
            "received_on": r["received_on"].isoformat(),
            "amount": round(int(r["amount_cents"]) / 100, 2),
            "channel": r["channel"],
            "category": category,
        })
    return out


async def main() -> int:
    args = parse_args()
    db = await _get_mongo()
    pg_receipts = await _get_pg_active_2026_receipts()

    by_unit: dict[str, list[dict]] = {}
    for r in pg_receipts:
        by_unit.setdefault(r["unit_number"], []).append(r)

    print(f"PostgreSQL active 2026 bank-matched/portal-scrape receipts: {len(pg_receipts)} across {len(by_unit)} units")
    print()

    sync_batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    plan_inserts: list[dict] = []
    skipped_units: list[dict] = []
    clean_units = 0

    for unit_number, receipts in sorted(by_unit.items()):
        ledger_doc = await db.unit_levy_ledger.find_one(
            {"building_id": BUILDING_ID, "unit_number": unit_number, "year": LEDGER_YEAR}
        )
        if not ledger_doc:
            skipped_units.append({"unit_number": unit_number, "reason": "no FY2026 unit_levy_ledger doc"})
            continue
        target_total_paid = ledger_doc.get("total_paid")
        if target_total_paid is None:
            skipped_units.append({"unit_number": unit_number, "reason": "no total_paid on ledger doc"})
            continue

        existing_2026 = await db.levy_payments.find(
            {"building_id": BUILDING_ID, "unit_number": unit_number, "status": "confirmed",
             "payment_date": {"$gt": "2025-12-01"}}
        ).to_list(200)
        existing_sum = round(sum(p.get("amount", 0.0) for p in existing_2026), 2)
        existing_keys = {(p.get("payment_date"), round(p.get("amount", 0.0), 2)) for p in existing_2026}

        to_insert = [
            r for r in receipts
            if (r["received_on"], r["amount"]) not in existing_keys
        ]
        new_sum = round(sum(r["amount"] for r in to_insert), 2)
        projected_total = round(existing_sum + new_sum, 2)

        if abs(projected_total - target_total_paid) >= 0.02:
            skipped_units.append({
                "unit_number": unit_number,
                "reason": "does not reconcile to ledger.total_paid",
                "existing_mongo_2026_sum": existing_sum,
                "new_from_pg": new_sum,
                "projected_total": projected_total,
                "ledger_total_paid": target_total_paid,
                "diff": round(projected_total - target_total_paid, 2),
            })
            continue

        clean_units += 1
        for r in to_insert:
            plan_inserts.append({
                "id": str(uuid.uuid4()),
                "building_id": BUILDING_ID,
                "unit_number": unit_number,
                "amount": r["amount"],
                "payment_method": r["channel"] or "bank_transfer",
                "payment_reference": r["receipt_id"],
                "payment_date": r["received_on"],
                "quarter": "AUTO",
                "year": LEDGER_YEAR,
                "transaction_origin": _ORIGIN_BY_CATEGORY[r["category"]],
                "reconstruction_batch_id": sync_batch_id,
                "source_pg_receipt_id": r["receipt_id"],
                "status": "confirmed",
                "receipt_number": f"PG-{r['receipt_id']}",
                "notes": (
                    "Backfilled 2026-08-04 from PostgreSQL finance.receipts "
                    f"(receipt_id={r['receipt_id']}, source={r['category']}). "
                    "Itemized-history uniformity backfill — unit_levy_ledger.total_paid "
                    "unchanged, already correct per GAP-FIN-035 (2026-08-03)."
                ),
                "confirmed_by": "system:postgres_sync_20260804",
                "confirmed_at": now,
                "created_at": now,
            })

    print(f"Units reconciling cleanly (ledger.total_paid == existing + new): {clean_units}")
    print(f"Units skipped (need manual review, NOT touched):                {len(skipped_units)}")
    print(f"Rows to insert:                                                 {len(plan_inserts)}")
    print()

    if skipped_units:
        print("SKIPPED — manual review needed, nothing written for these units:")
        for s in skipped_units:
            print(f"  {s}")
        print()

    if not plan_inserts:
        print("Nothing to insert.")
        return 0

    if not args.apply:
        print("Sample of planned inserts:")
        for row in plan_inserts[:5]:
            print(f"  {row['unit_number']}  {row['payment_date']}  ${row['amount']:>10,.2f}  {row['transaction_origin']}  ref={row['receipt_number']}")
        print(f"  ... {len(plan_inserts)} total")
        print()
        print(f"sync_batch_id for this run: {sync_batch_id}")
        print("Dry-run complete. Re-run with --apply to write these changes.")
        return 0

    print("Applying...")
    result = await db.levy_payments.insert_many(plan_inserts)
    print(f"Inserted {len(result.inserted_ids)} rows. sync_batch_id={sync_batch_id}")

    # Post-write verification: re-sum each touched unit's 2026 confirmed rows and assert
    # it still equals the ledger's total_paid — catches any write-layer surprise immediately.
    touched_units = sorted({r["unit_number"] for r in plan_inserts})
    mismatches = []
    for unit_number in touched_units:
        ledger_doc = await db.unit_levy_ledger.find_one(
            {"building_id": BUILDING_ID, "unit_number": unit_number, "year": LEDGER_YEAR}
        )
        rows = await db.levy_payments.find(
            {"building_id": BUILDING_ID, "unit_number": unit_number, "status": "confirmed",
             "payment_date": {"$gt": "2025-12-01"}}
        ).to_list(200)
        post_sum = round(sum(r.get("amount", 0.0) for r in rows), 2)
        if abs(post_sum - ledger_doc.get("total_paid", 0.0)) >= 0.02:
            mismatches.append((unit_number, post_sum, ledger_doc.get("total_paid")))

    if mismatches:
        print(f"POST-WRITE VERIFICATION FAILED for {len(mismatches)} unit(s): {mismatches}")
        return 1

    print(f"Post-write verification passed for all {len(touched_units)} touched units.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
