#!/usr/bin/env python3
"""Allocate the East Gate 2021-2025 historical levy-payment receipts (posted
by east_gate_2021_2025_levy_payment_backfill.py) against their open levy_items.

record_historical_payment() only creates the Receipt (matching record_payment()'s
own "does NOT allocate -- call allocate_payment next" contract) -- this script
is the "next" step, mirroring how _post_payment_to_ledger() in
financial_matching.py always calls record_payment() + allocate_payment() together
for the live bank-feed-matching flow.

Usage:
    cd backend
    venv/bin/python3 scripts/east_gate_2021_2025_levy_payment_allocate.py            # dry-run
    venv/bin/python3 scripts/east_gate_2021_2025_levy_payment_allocate.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.financial_core import get_financial_core_service  # noqa: E402
from services.financial_core.domain.entities import AllocatePaymentCommand, SchemeRef  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env")

BUILDING_ID = "13195"


async def main(apply: bool):
    scheme = await resolve_scheme_context(BUILDING_ID)
    scheme_ref = SchemeRef(tenant_id=scheme["tenant_id"], scheme_id=scheme["scheme_id"])

    async with async_session_context() as session:
        await set_tenant(session, str(scheme["tenant_id"]))
        result = await session.execute(
            text(
                """
                SELECT r.receipt_id::text, r.lot_id::text
                FROM finance.receipts r
                LEFT JOIN finance.receipt_allocations pa ON pa.receipt_id = r.receipt_id
                WHERE r.scheme_id = CAST(:sid AS uuid)
                  AND EXTRACT(YEAR FROM r.received_on)::int BETWEEN 2021 AND 2025
                  AND pa.allocation_id IS NULL
                ORDER BY r.received_on
                """
            ),
            {"sid": str(scheme["scheme_id"])},
        )
        rows = result.fetchall()

    print(f"Found {len(rows)} unallocated 2021-2025 receipts.")
    if not apply:
        print("Dry run -- not allocating anything. Re-run with --apply to allocate.")
        return

    allocated, failed = 0, []
    for i, row in enumerate(rows, 1):
        receipt_id = row[0]
        try:
            async with async_session_context() as session:
                await set_tenant(session, str(scheme["tenant_id"]))
                svc = get_financial_core_service(session)
                cmd = AllocatePaymentCommand(scheme_ref=scheme_ref, receipt_id=receipt_id)
                allocations = await svc.allocate_payment(cmd)
                allocated += 1
                if i % 100 == 0 or i == len(rows):
                    print(f"  [{i}/{len(rows)}] allocated receipt={receipt_id} -> {len(allocations)} lines")
        except Exception as e:
            failed.append((receipt_id, str(e)))
            print(f"  [{i}/{len(rows)}] FAILED receipt={receipt_id}: {e}")

    print(f"\nDone. Allocated: {allocated}/{len(rows)}. Failed: {len(failed)}")
    if failed:
        print("First 10 failures:")
        for receipt_id, err in failed[:10]:
            print(f"  {receipt_id}: {err}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
