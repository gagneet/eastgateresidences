#!/usr/bin/env python3
"""GAP-FIN-035 Item 2 follow-up (round 2) — allocate orphaned reconciliation-delta receipts.

Found while re-verifying the $190/28-unit reconciliation: 14 of East Gate's 2026
"portal_scrape_reconciliation_delta" receipts (posted 2026-08-02, real journal entries,
status='posted') have ZERO rows in finance.receipt_allocations -- the receipt exists and the
cash is recorded, but it was never applied against the levy_item(s) it was meant to settle.
This is why finance.levy_items.paid_cents never reflected these payments, and why a fresh
Mongo-vs-Postgres delta calculation kept showing them as still outstanding two days later.

This script does NOT create new receipts -- it finds already-posted, already-idempotency-keyed
receipts with this external_reference prefix that have zero allocations, and calls
FinancialCoreService.allocate_payment() on each (the same sanctioned allocation path every
other payment in this codebase goes through).

Usage:
    cd backend
    venv/bin/python3 scripts/gap_fin_035_allocate_orphaned_reconciliation_receipts.py \\
        --building-id 13195                 # dry-run (default)
    venv/bin/python3 scripts/gap_fin_035_allocate_orphaned_reconciliation_receipts.py \\
        --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402
from services.financial_core import get_financial_core_service  # noqa: E402
from services.financial_core.domain.entities import AllocatePaymentCommand, SchemeRef  # noqa: E402


async def main(building_id: str, apply: bool) -> None:
    set_ctx_building_id(building_id)
    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        print(f"No Postgres scheme found for building_id={building_id!r} -- aborting.")
        return
    scheme_ref = SchemeRef(tenant_id=scheme["tenant_id"], scheme_id=scheme["scheme_id"])

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))

        result = await session.execute(
            text("""
                SELECT r.receipt_id, r.amount_cents, r.external_reference, je.effective_on,
                       l.unit_number
                FROM finance.receipts r
                JOIN finance.journal_entries je ON je.journal_entry_id = r.journal_entry_id
                JOIN core.lots l ON l.lot_id = r.lot_id
                LEFT JOIN finance.receipt_allocations ra ON ra.receipt_id = r.receipt_id
                WHERE r.scheme_id = :sid
                  AND r.external_reference LIKE 'portal_scrape_reconciliation_delta:%'
                  AND je.status = 'posted'
                GROUP BY r.receipt_id, r.amount_cents, r.external_reference, je.effective_on, l.unit_number
                HAVING COUNT(ra.allocation_id) = 0
                ORDER BY l.unit_number
            """),
            {"sid": str(scheme["scheme_id"])},
        )
        orphaned = result.fetchall()

        print(f"Found {len(orphaned)} orphaned (posted but unallocated) reconciliation receipt(s):")
        total_cents = sum(row.amount_cents for row in orphaned)
        for row in orphaned:
            print(f"  {row.unit_number}: receipt={row.receipt_id} amount=${row.amount_cents/100:,.2f} "
                  f"posted_on={row.effective_on}")
        print(f"Total: ${total_cents/100:,.2f}")

        if not apply:
            print("\nDry run -- not allocating anything. Re-run with --apply to allocate.")
            return

        svc = get_financial_core_service(session)
        allocated, failed = 0, []
        for row in orphaned:
            try:
                async with session.begin_nested():
                    cmd = AllocatePaymentCommand(scheme_ref=scheme_ref, receipt_id=row.receipt_id)
                    await svc.allocate_payment(cmd)
                allocated += 1
                print(f"  [{row.unit_number}] allocated receipt {row.receipt_id}")
            except Exception as exc:
                failed.append((row.unit_number, str(exc)))
                print(f"  [{row.unit_number}] FAILED: {exc}")

        await session.commit()
        print(f"\nDone. Allocated: {allocated}/{len(orphaned)}. Failed: {len(failed)}")
        for unit_number, err in failed:
            print(f"  {unit_number}: {err}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", type=str, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.building_id, args.apply))
