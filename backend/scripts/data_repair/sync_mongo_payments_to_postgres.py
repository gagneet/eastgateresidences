#!/usr/bin/env python3
# @featuretrace:financial_core — runner for the Mongo->Postgres finance sync.
# Layer: script
# Data flow: levy_payments + finance.receipts -> mongo_pg_finance_sync -> demo_bank_transactions.
# Related: backend/services/mongo_pg_finance_sync.py (the logic; this file is I/O only)
# Collection: levy_payments, finance.receipts, demo_bank_transactions
# Tests: tests/backend/test_mongo_pg_finance_sync.py
"""Close the Mongo->Postgres finance gap for one building, through Demo Bank.

Dry-run by default. ``--apply`` writes Demo Bank **candidates** only — it never
posts a journal entry, never writes ``finance.*``, and never approves anything.
The candidates it creates still require human approval on the matching page.

    python3 scripts/data_repair/sync_mongo_payments_to_postgres.py --building-id 13195
    python3 scripts/data_repair/sync_mongo_payments_to_postgres.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.mongo_pg_finance_sync import (  # noqa: E402
    build_sync_plan,
    measure_drift,
    to_demo_bank_candidates,
)

BYPASS = "00000000-0000-0000-0000-000000000000"


def _fmt(cents: int) -> str:
    return f"${cents / 100:,.2f}"


async def run(building_id: str, apply: bool) -> None:
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ.get("DB_NAME", "strataos_production")]
    pg = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        # core.schemes admits a session under the bypass sentinel or the row's own
        # tenant — and the tenant is what we are looking up (footgun #8).
        await pg.execute(f"SET app.tenant_id = '{BYPASS}'")
        row = await pg.fetchrow(
            "SELECT scheme_id::text sid, tenant_id::text tid FROM core.schemes "
            "WHERE scheme_number = $1 AND is_test_data = FALSE",
            building_id,
        )
        if not row:
            raise SystemExit(f"No scheme for building_id={building_id!r}")
        tenant_id = row["tid"]
        # finance.* has NO bypass clause: without the real tenant these queries
        # return 0 rows and no error, which reads exactly like "Postgres is empty".
        await pg.execute(f"SET app.tenant_id = '{tenant_id}'")

        mongo_payments = await db.levy_payments.find(
            {"building_id": building_id}, {"_id": 0}
        ).to_list(20000)

        pg_receipts = [
            dict(r)
            for r in await pg.fetch(
                """
                SELECT l.unit_number, r.amount_cents, r.received_on
                  FROM finance.receipts r
                  JOIN core.lots l ON l.lot_id = r.lot_id
                 WHERE r.tenant_id = $1 AND r.retired_at IS NULL
                """,
                tenant_id,
            )
        ]

        plan = build_sync_plan(mongo_payments, pg_receipts)

        # Per-lot drift, so the gap is a number to watch rather than a surprise.
        mongo_by_unit: dict[str, int] = {}
        async for r in db.unit_levy_ledger.find(
            {"building_id": building_id}, {"_id": 0, "unit_number": 1, "net_balance": 1}
        ):
            u = r.get("unit_number")
            if u:
                mongo_by_unit[u] = mongo_by_unit.get(u, 0) + int(
                    round(float(r.get("net_balance") or 0) * 100)
                )
        pg_by_unit = {
            r["unit_number"]: int(r["net"])
            for r in await pg.fetch(
                """
                SELECT l.unit_number,
                       COALESCE(SUM(li.principal_cents + li.gst_cents
                                  + li.interest_cents + li.recovery_costs_cents), 0)
                     - COALESCE(SUM(li.paid_cents), 0)
                     - COALESCE((SELECT SUM(o.available_cents)
                                   FROM finance.owner_credit_balances o
                                  WHERE o.lot_id = l.lot_id AND o.tenant_id = $1), 0) AS net
                  FROM core.lots l
                  LEFT JOIN finance.levy_items li
                         ON li.lot_id = l.lot_id AND li.tenant_id = $1
                 WHERE l.tenant_id = $1
                 GROUP BY l.unit_number, l.lot_id
                """,
                tenant_id,
            )
        }
        drift = measure_drift(building_id, mongo_by_unit, pg_by_unit)

        print("=" * 76)
        print(f"Mongo -> Postgres finance sync — building {building_id}"
              f"  [{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 76)
        print(f"  Mongo levy_payments read     : {len(mongo_payments)}")
        print(f"  PG receipts (live)           : {len(pg_receipts)}")
        print(f"  already present in PG        : {plan.already_present}")
        print(f"  skipped (not confirmed)      : {plan.skipped_unconfirmed}")
        print(f"  skipped (no unit_number)     : {plan.skipped_no_unit}")
        print(f"  MISSING in PG                : {len(plan.missing_in_pg)}  {_fmt(plan.total_cents)}")
        print()
        print(f"  per-lot drift: {drift.lots_diverged}/{drift.lots_compared} lots differ, "
              f"net {_fmt(drift.net_gap_cents)}")

        for item in plan.missing_in_pg[:12]:
            print(f"      {item['unit_number']:<8} {_fmt(item['amount_cents']):>12}  "
                  f"{item['payment_date']}  {item['transaction_origin']}")
        if len(plan.missing_in_pg) > 12:
            print(f"      ... and {len(plan.missing_in_pg) - 12} more")

        if not apply:
            print("\n  DRY-RUN — re-run with --apply to create Demo Bank candidates.")
            return

        candidates = to_demo_bank_candidates(plan, building_id)
        created = 0
        for cand in candidates:
            # Idempotent on the derived key: a re-run cannot double-create a
            # candidate for the same fact, even while the first awaits review.
            res = await db.demo_bank_transactions.update_one(
                {"idempotency_key": cand["idempotency_key"]},
                {"$setOnInsert": cand},
                upsert=True,
            )
            if res.upserted_id is not None:
                created += 1
        print(f"\n  Demo Bank candidates created: {created} "
              f"(of {len(candidates)} missing; the rest already staged).")
        print("  They are requires_review=True — approve on /financials/matching to post.")
    finally:
        await pg.close()
        mongo.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.building_id, args.apply))


if __name__ == "__main__":
    main()
