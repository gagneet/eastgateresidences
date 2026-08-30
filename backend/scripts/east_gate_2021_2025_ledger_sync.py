#!/usr/bin/env python3
"""Sync Mongo unit_levy_ledger + levy_payments for East Gate (13195) 2021-2025
to reflect the levy payments posted to Postgres by
east_gate_2021_2025_levy_payment_backfill.py.

For each unit, each year 2021-2025 IN CHRONOLOGICAL ORDER (closing balances
chain forward year to year):
  - admin_paid / sinking_paid / total_paid are recomputed from the actual
    posted Postgres receipts (grouped by the receipt's own metadata.source_doc_id
    back to the originating demo_bank_transactions doc's admin/sinking
    allocation split), NOT assumed equal to admin_levied/sinking_levied --
    though because every unit's Demo Bank credit total for 2021-2025 was
    already confirmed live to exactly equal that year's total_levied (0
    mismatches across all 87 units x 5 years), that IS what falls out.
  - admin_closing/sinking_closing/net_balance are recomputed
    (opening + levied - paid), with each year's closing becoming the NEXT
    year's opening -- correcting the ever-growing net_balance that resulted
    from admin_paid/sinking_paid having been stuck at 0 for these years.
  - applied_payment_ids is populated with the newly-created levy_payments
    Mongo document ids (one per individual payment, not just the aggregate),
    so OwnerDashboard's payment-history view (which reads levy_payments, not
    finance.receipts directly) shows each individual historical payment.

2026 is explicitly NOT touched -- its own admin_opening/sinking_opening/
total_paid/net_balance come from a separate, independent scraper-derived
source (unit_levy_ledger.reconciliation_note: "back-solved from the portal's
live outstanding balance") and must not be overwritten here. This DOES mean
2026's opening_balance will not reflect 2025's newly-corrected closing --
flagged as a known, deliberate gap, not a silent inconsistency.

Usage:
    cd backend
    venv/bin/python3 scripts/east_gate_2021_2025_ledger_sync.py            # dry-run
    venv/bin/python3 scripts/east_gate_2021_2025_ledger_sync.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from bson import ObjectId  # noqa: E402
from database import db as mongo_db  # noqa: E402
from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from sqlalchemy import text  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env")

BUILDING_ID = "13195"
YEARS = ["2021", "2022", "2023", "2024", "2025"]

set_ctx_building_id(BUILDING_ID)


async def _fetch_receipts_by_unit_year() -> dict[tuple[str, str], list[dict]]:
    """Returns {(unit_number, year): [{"source_doc_id", "amount_cents",
    "received_on", "receipt_id"}, ...]}."""
    scheme = await resolve_scheme_context(BUILDING_ID)
    grouped: dict[tuple[str, str], list[dict]] = {}
    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        result = await session.execute(
            text(
                """
                SELECT receipt_id::text, received_on, amount_cents, metadata
                FROM finance.receipts
                WHERE scheme_id = CAST(:sid AS uuid)
                  AND metadata->>'source' = 'demo_bank_transactions'
                  AND EXTRACT(YEAR FROM received_on)::int BETWEEN 2021 AND 2025
                """
            ),
            {"sid": str(scheme["scheme_id"])},
        )
        for row in result.fetchall():
            meta = row.metadata or {}
            source_doc_id = meta.get("source_doc_id")
            year = meta.get("strata_year")
            if not source_doc_id or not year:
                continue
            try:
                tx = await mongo_db.demo_bank_transactions.find_one({"_id": ObjectId(source_doc_id)})
            except Exception:
                tx = None
            if not tx:
                continue
            unit_number = tx["unit_number"]
            grouped.setdefault((unit_number, year), []).append({
                "source_doc_id": source_doc_id,
                "amount_cents": row.amount_cents,
                "received_on": row.received_on,
                "receipt_id": row.receipt_id,
                "allocations": tx.get("allocations") or [],
                "quarter": meta.get("quarter"),
                "reconstruction_batch_id": tx.get("reconstruction_batch_id"),
            })
    return grouped


async def main(apply: bool):
    grouped = await _fetch_receipts_by_unit_year()
    units = sorted({unit for (unit, _year) in grouped})
    print(f"Found posted receipts for {len(units)} units across {YEARS}")

    plan = []  # [(unit, year, new_admin_paid, new_sinking_paid, new_total_paid,
               #   new_admin_closing, new_sinking_closing, new_net_balance, payments)]

    for unit in units:
        # Chain opening forward: 2021 uses the ledger's OWN currently-stored
        # admin_opening/sinking_opening (untouched, reflects the genesis
        # handoff); each subsequent year's opening = prior year's newly
        # computed closing.
        carried_admin_opening = None
        carried_sinking_opening = None

        for year in YEARS:
            ledger_doc = await mongo_db.unit_levy_ledger.find_one(
                {"building_id": BUILDING_ID, "year": year, "unit_number": unit}
            )
            if not ledger_doc:
                print(f"  WARNING: no unit_levy_ledger doc for unit={unit} year={year} -- skipping")
                continue

            admin_opening = carried_admin_opening if carried_admin_opening is not None else float(ledger_doc.get("admin_opening") or 0)
            sinking_opening = carried_sinking_opening if carried_sinking_opening is not None else float(ledger_doc.get("sinking_opening") or 0)
            admin_levied = float(ledger_doc.get("admin_levied") or 0)
            sinking_levied = float(ledger_doc.get("sinking_levied") or 0)

            payments = grouped.get((unit, year), [])
            admin_paid_cents = 0
            sinking_paid_cents = 0
            for p in payments:
                for alloc in p["allocations"]:
                    if alloc.get("fund_type") == "admin":
                        admin_paid_cents += alloc.get("amount_cents", 0)
                    elif alloc.get("fund_type") == "sinking":
                        sinking_paid_cents += alloc.get("amount_cents", 0)

            new_admin_paid = round(admin_paid_cents / 100, 2)
            new_sinking_paid = round(sinking_paid_cents / 100, 2)
            new_total_paid = round(new_admin_paid + new_sinking_paid, 2)

            new_admin_closing = round(admin_opening + admin_levied - new_admin_paid, 2)
            new_sinking_closing = round(sinking_opening + sinking_levied - new_sinking_paid, 2)
            new_net_balance = round(new_admin_closing + new_sinking_closing, 2)

            plan.append({
                "unit": unit, "year": year,
                "admin_opening": admin_opening, "sinking_opening": sinking_opening,
                "admin_paid": new_admin_paid, "sinking_paid": new_sinking_paid, "total_paid": new_total_paid,
                "admin_closing": new_admin_closing, "sinking_closing": new_sinking_closing,
                "net_balance": new_net_balance,
                "payments": payments,
            })

            carried_admin_opening = new_admin_closing
            carried_sinking_opening = new_sinking_closing

    total_payments = sum(len(p["payments"]) for p in plan)
    print(f"Planned {len(plan)} unit-year ledger updates, {total_payments} individual payment records")
    if plan:
        sample = plan[0]
        print(f"Sample: unit={sample['unit']} year={sample['year']} "
              f"admin_paid={sample['admin_paid']} sinking_paid={sample['sinking_paid']} "
              f"net_balance={sample['net_balance']} (was {(await mongo_db.unit_levy_ledger.find_one({'building_id': BUILDING_ID, 'year': sample['year'], 'unit_number': sample['unit']}))['net_balance']})")

    if not apply:
        print("\nDry run -- not writing anything. Re-run with --apply to write.")
        return

    now = datetime.now(timezone.utc).isoformat()
    updated, payment_docs_created = 0, 0
    for entry in plan:
        applied_payment_ids = []
        for p in entry["payments"]:
            mongo_payment_id = str(uuid.uuid4())
            await mongo_db.levy_payments.insert_one({
                "id": mongo_payment_id,
                "building_id": BUILDING_ID,
                "unit_number": entry["unit"],
                "amount": round(p["amount_cents"] / 100, 2),
                "payment_method": "bank_transfer",
                "payment_reference": p["source_doc_id"],
                "payment_date": p["received_on"].isoformat(),
                "quarter": p["quarter"] or "AUTO",
                "year": entry["year"],
                "transaction_origin": "reconstructed_historical",
                "reconstruction_batch_id": str(p["reconstruction_batch_id"]) if p["reconstruction_batch_id"] else None,
                "status": "confirmed",
                "receipt_number": f"PG-{p['receipt_id']}",
                "notes": "East Gate 2021-2025 historical levy-payment backfill (Demo Bank source, date corrected to real quarterly due date)",
                "confirmed_by": "system:historical_backfill",
                "confirmed_at": now,
                "created_at": now,
            })
            applied_payment_ids.append(mongo_payment_id)
            payment_docs_created += 1

        await mongo_db.unit_levy_ledger.update_one(
            {"building_id": BUILDING_ID, "year": entry["year"], "unit_number": entry["unit"]},
            {"$set": {
                "admin_paid": entry["admin_paid"],
                "sinking_paid": entry["sinking_paid"],
                "total_paid": entry["total_paid"],
                "admin_closing": entry["admin_closing"],
                "sinking_closing": entry["sinking_closing"],
                "net_balance": entry["net_balance"],
                "applied_payment_ids": applied_payment_ids,
                "reconciliation_note": (
                    "Recomputed 2026-08-02: admin_paid/sinking_paid/total_paid now sourced from "
                    "real posted Postgres receipts (finance.receipts, via "
                    "east_gate_2021_2025_levy_payment_backfill.py), not left at 0. Demo Bank "
                    "transactions treated as the real per-lot per-period transactional record per "
                    "explicit user direction; posting dates re-derived from each transaction's own "
                    "stated quarter label, not its (confirmed unreliable) raw effective_date."
                ),
                "reconciled_at": now,
            }},
        )
        updated += 1
        if updated % 50 == 0 or updated == len(plan):
            print(f"  [{updated}/{len(plan)}] unit_levy_ledger updated, {payment_docs_created} payment records so far")

    print(f"\nDone. Updated {updated} unit_levy_ledger docs, created {payment_docs_created} levy_payments docs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
