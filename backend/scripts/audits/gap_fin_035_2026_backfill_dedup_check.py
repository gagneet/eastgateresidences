#!/usr/bin/env python3
"""GAP-FIN-035 Item 1 — read-only dedup check before any 2026 levy-payment backfill.

Does NOT write to MongoDB or PostgreSQL, and does NOT post anything. Its only
purpose is to classify each of a building's 2026 Mongo `demo_bank_transactions`
credit rows as "already posted to Postgres" or "needs posting", so a future
posting script cannot silently create a 4th duplicate-posting incident in this
exact pathway (three real ones already happened for East Gate FY2026: a
$1,769,655.36 duplicate GL credit, 22 duplicate receipt postings, and 20
pre-existing bad future-dated receipts — see
tasks/GAP-FIN-033-financial-overview-live-verification-and-fy2026-posting.md).

Correlation + dedup logic, established by live investigation before writing
this script (not guessed):
  - Mongo `demo_bank_transactions.finance_bank_transaction_ref` is a
    COMMA-SEPARATED list of Postgres `finance.bank_transactions.bank_transaction_id`
    UUIDs (one Mongo doc can fund-split into 2+ Postgres rows, e.g. admin+sinking).
  - `finance.receipts.bank_transaction_id` is populated in ZERO rows for East
    Gate 2026 as of 2026-08-03 — it is not a usable join key today, despite
    being the "correct" column. Included here for future-proofing only.
  - The only currently-populated join key is `finance.receipts.external_reference`,
    written in THREE incompatible formats by different historical scripts:
      1. Legacy `financial_matching.py` path: provider_txn_id / description / inbox_event_id.
      2. `gap_fin_031_post_fy2026_derived_receipts.py` phase 1: "gap_fin_031_derived:<bank_transaction_id>".
      3. Same script, phase 2 (reconciliation delta): "portal_scrape_reconciliation_delta:<year>"
         — NOT tied to a specific bank_transaction_id at all (one per lot/year).
  - `finance.journal_entries.idempotency_key` is the real DB-enforced unique
    constraint (UNIQUE(tenant_id, idempotency_key)) behind format #2 above
    ("gap-fin-031-derived:<uuid>" per that script's IntegrityError-catch path)
    — the most reliable signal that a specific bank_transaction_id has already
    been posted, when present.

Usage:
    cd backend
    venv/bin/python3 scripts/audits/gap_fin_035_2026_backfill_dedup_check.py --building-id 13195
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from database import db  # noqa: E402
from db_postgres.repos import config_repo  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402


async def main(building_id: str, year: str) -> None:
    set_ctx_building_id(building_id)

    scheme = await config_repo.resolve_scheme_context(building_id)
    if not scheme:
        print(f"No Postgres scheme found for building_id={building_id} — cannot cross-check.")
        return

    mongo_docs = await db._db.demo_bank_transactions.find(
        {
            "building_id": building_id,
            "strata_year": year,
            "direction": "credit",
            "is_archived": {"$ne": True},
            "is_test_data": {"$ne": True},
        },
        {
            "_id": 1, "unit_number": 1, "amount_cents": 1,
            "finance_bank_transaction_ref": 1, "description": 1,
        },
    ).to_list(2000)

    total_amount_cents = sum(int(d.get("amount_cents") or 0) for d in mongo_docs)
    print(f"Mongo demo_bank_transactions ({year}, credit, not archived): "
          f"{len(mongo_docs)} docs, ${total_amount_cents / 100:,.2f}")

    # Explode the comma-separated finance_bank_transaction_ref field into a
    # flat set of Postgres bank_transaction_id UUIDs, tracking which Mongo doc(s)
    # each one belongs to (usually 1, but a UUID could theoretically appear on
    # more than one doc if source data has its own duplication — flag, don't hide).
    pg_uuid_to_mongo_docs: dict[str, list[str]] = {}
    unparseable_docs = []
    for d in mongo_docs:
        raw_ref = d.get("finance_bank_transaction_ref") or ""
        uuids = [u.strip() for u in str(raw_ref).split(",") if u.strip()]
        if not uuids:
            unparseable_docs.append(str(d["_id"]))
            continue
        for u in uuids:
            pg_uuid_to_mongo_docs.setdefault(u, []).append(str(d["_id"]))

    multi_doc_uuids = {u: docs for u, docs in pg_uuid_to_mongo_docs.items() if len(docs) > 1}
    print(f"Distinct Postgres bank_transaction_id UUIDs referenced: {len(pg_uuid_to_mongo_docs)}")
    print(f"Mongo docs with no parseable finance_bank_transaction_ref: {len(unparseable_docs)}")
    if multi_doc_uuids:
        print(f"WARNING: {len(multi_doc_uuids)} Postgres UUID(s) referenced by >1 Mongo doc — "
              f"investigate before trusting any 1:1 assumption. Sample: "
              f"{dict(list(multi_doc_uuids.items())[:3])}")

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))

        all_uuids = list(pg_uuid_to_mongo_docs.keys())
        if not all_uuids:
            print("No Postgres UUIDs to check — nothing to cross-reference.")
            return

        # Check every classification signal established above, in one pass.
        result = await session.execute(
            text("""
                SELECT
                    bt.bank_transaction_id::text AS bank_transaction_id,
                    r.receipt_id IS NOT NULL AS has_receipt_by_bank_tx_id,
                    r2.receipt_id IS NOT NULL AS has_receipt_by_external_ref_uuid,
                    r3.receipt_id IS NOT NULL AS has_receipt_by_gap_fin_031_prefix,
                    je.journal_entry_id IS NOT NULL AS has_journal_by_idempotency_key
                FROM finance.bank_transactions bt
                LEFT JOIN finance.receipts r
                    ON r.bank_transaction_id = bt.bank_transaction_id
                LEFT JOIN finance.receipts r2
                    ON r2.external_reference = bt.bank_transaction_id::text
                LEFT JOIN finance.receipts r3
                    ON r3.external_reference = 'gap_fin_031_derived:' || bt.bank_transaction_id::text
                LEFT JOIN finance.journal_entries je
                    ON je.idempotency_key = 'gap-fin-031-derived:' || bt.bank_transaction_id::text
                WHERE bt.bank_transaction_id = ANY(:uuids)
            """),
            {"uuids": all_uuids},
        )
        rows = result.mappings().all()

    checked_ids = {row["bank_transaction_id"] for row in rows}
    missing_from_pg = [u for u in all_uuids if u not in checked_ids]

    already_posted = []
    needs_posting = []
    for row in rows:
        posted = (
            row["has_receipt_by_bank_tx_id"]
            or row["has_receipt_by_external_ref_uuid"]
            or row["has_receipt_by_gap_fin_031_prefix"]
            or row["has_journal_by_idempotency_key"]
        )
        (already_posted if posted else needs_posting).append(row["bank_transaction_id"])

    print(f"\nPostgres UUIDs referenced by Mongo but NOT FOUND in finance.bank_transactions "
          f"(data-integrity gap, not a dedup signal): {len(missing_from_pg)}")
    if missing_from_pg:
        print(f"  sample: {missing_from_pg[:5]}")

    print(f"\nAlready posted (matches at least one dedup signal): {len(already_posted)}")
    print(f"Needs posting (no dedup signal matched): {len(needs_posting)}")

    # Map back to Mongo doc level: a Mongo doc counts as "fully posted" only if
    # ALL of its linked Postgres UUIDs are already posted, "partially posted"
    # if some are and some aren't (a real, flaggable data-quality state), and
    # "needs posting" only if NONE of its linked UUIDs are posted.
    already_posted_set = set(already_posted)
    needs_posting_set = set(needs_posting)
    fully_posted_docs, partially_posted_docs, needs_posting_docs = [], [], []
    for d in mongo_docs:
        raw_ref = d.get("finance_bank_transaction_ref") or ""
        uuids = [u.strip() for u in str(raw_ref).split(",") if u.strip()]
        if not uuids:
            continue
        posted_count = sum(1 for u in uuids if u in already_posted_set)
        if posted_count == len(uuids):
            fully_posted_docs.append(str(d["_id"]))
        elif posted_count == 0:
            needs_posting_docs.append(str(d["_id"]))
        else:
            partially_posted_docs.append(str(d["_id"]))

    print(f"\nMongo docs fully posted (all linked PG rows already posted): {len(fully_posted_docs)}")
    print(f"Mongo docs PARTIALLY posted (mixed — real data-quality flag, do not auto-post the rest): "
          f"{len(partially_posted_docs)}")
    if partially_posted_docs:
        print(f"  sample: {partially_posted_docs[:5]}")
    print(f"Mongo docs needing posting (zero linked PG rows posted): {len(needs_posting_docs)}")

    needs_posting_amount_cents = sum(
        int(d.get("amount_cents") or 0) for d in mongo_docs if str(d["_id"]) in needs_posting_docs
    )
    print(f"\nTotal amount for docs needing posting: ${needs_posting_amount_cents / 100:,.2f}")
    print("\nThis script posted nothing. Use these counts to design the actual posting script's "
          "dedup filter before writing it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", type=str, required=True)
    parser.add_argument("--year", type=str, default="2026")
    args = parser.parse_args()
    asyncio.run(main(args.building_id, args.year))
