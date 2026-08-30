#!/usr/bin/env python3
# @featuretrace:finance-postgres-read-cutover — resolve allocations exceeding paid_cents.
# Layer: script
# Data flow: finance.receipt_allocations vs finance.levy_items -> corrected paid_cents
#            and corrected allocation amounts (building-scoped).
# Related: backend/scripts/data_repair/reconstruct_receipt_allocations.py
#          docs/architecture/allocation_trail_reconstruction_2026-08-30.md
# Tests: tests/backend/test_adjudicate_over_allocations.py
"""Resolve the 16 levy_items whose allocations exceed their own paid_cents.

DRY-RUN BY DEFAULT. `--apply` is a production financial mutation.

These 16 items block `reconstruct_receipt_allocations.py`, which refuses to run while a
reconciliation cannot balance. Adjudicating them means asking, per item, WHICH of the two
numbers is wrong — and the answer is not the same for all of them.

TWO GROUPS, OPPOSITE TREATMENTS
-------------------------------
Comparing each item's allocation against what it CHARGED separates them cleanly:

**Group 1 — allocation <= charged (8 items, $2,302.85).**
    e.g. TH074 admin FY2026: charged $1,363.48, paid_cents $377.69, allocated $1,061.81.
    The allocations point at real, unretired receipts and stay within what the item
    charged. So the money did go to this item and `paid_cents` was simply never updated.
    `paid_cents` is the wrong number. Raise it to the allocated total.
    This does not invent money: every cent is already evidenced by a receipt allocation.

**Group 2 — allocation > charged (8 items, $1,837.26).**
    e.g. UA050 admin FY2026: charged $698.78, paid_cents $698.78, allocated $1,106.74.
    The item is fully paid, and $407.96 MORE than the charge was assigned to it. Here the
    ALLOCATION is the wrong number: money cannot be applied to a charge beyond the charge.
    Raising `paid_cents` instead would claim the owner owed more than they were billed.

    The excess is REDUCED, not deleted. Deleting the row would destroy the link to a real
    receipt; reducing `allocated_cents` keeps the trail and returns the difference to the
    receipt's unallocated balance — which is exactly how unapplied credit is represented.

WHAT THIS WILL NOT DO
---------------------
* It never touches a retired receipt (none of the 34 receipts involved are retired).
* It never deletes an allocation row.
* It never raises `paid_cents` above what the item CHARGED.
* It never moves money between lots or years.

    python3 backend/scripts/data_repair/adjudicate_over_allocated_levy_items.py --building-id 13195
    python3 backend/scripts/data_repair/adjudicate_over_allocated_levy_items.py --building-id 13195 --apply
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

BYPASS = "00000000-0000-0000-0000-000000000000"


def _d(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def classify(charged: int, paid: int, allocated: int) -> tuple[str, int]:
    """Which number is wrong, and by how much. Pure — the whole judgement lives here.

    Returns ("raise_paid", new_paid_cents) or ("reduce_allocation", target_total_cents).
    """
    if allocated <= charged:
        # The allocation is defensible: real receipts, within what was billed. The
        # ledger's own paid_cents simply lags it.
        return "raise_paid", allocated
    # More was applied to this charge than the charge is worth. The allocation total must
    # come down to the charge; the surplus returns to the receipt as unapplied credit.
    return "reduce_allocation", charged


async def run(building_id: str, apply: bool) -> int:
    pg = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        # `SELECT set_config(...)` with a BOUND parameter, not an f-string `SET`.
        # asyncpg cannot bind a parameter to `SET`, which is why the f-string form is
        # widespread in this repo — but set_config() is a normal function call and takes
        # one. The value here is a UUID read from core.schemes, so the f-string was not
        # exploitable; the point is that it is a pattern which stops being safe the
        # moment its source changes to anything a user can influence, and there is no
        # reason to keep it when a parameterised form exists. tests/backend/conftest.py
        # already uses exactly this.
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        scheme = await pg.fetchrow(
            "SELECT scheme_id::text sid, tenant_id::text tid FROM core.schemes "
            "WHERE scheme_number = $1 AND is_test_data = FALSE",
            building_id,
        )
        if not scheme:
            raise SystemExit(f"No scheme for building_id={building_id!r}")
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", scheme["tid"])

        rows = await pg.fetch(
            """
            WITH a AS (SELECT levy_item_id, SUM(allocated_cents) alloc
                         FROM finance.receipt_allocations GROUP BY 1)
            SELECT li.levy_item_id::text lid, l.unit_number, lr.financial_year fy,
                   f.fund_type,
                   li.principal_cents + li.gst_cents + li.interest_cents
                     + li.recovery_costs_cents AS charged,
                   li.paid_cents, a.alloc
              FROM finance.levy_items li
              JOIN a ON a.levy_item_id = li.levy_item_id
              JOIN core.lots l ON l.lot_id = li.lot_id
              JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
              JOIN finance.funds f ON f.fund_id = li.fund_id
             WHERE li.scheme_id = $1::uuid AND a.alloc > li.paid_cents
             ORDER BY a.alloc - li.paid_cents DESC
            """,
            scheme["sid"],
        )

        raise_paid, reduce_alloc = [], []
        for r in rows:
            action, target = classify(int(r["charged"]), int(r["paid_cents"]), int(r["alloc"]))
            (raise_paid if action == "raise_paid" else reduce_alloc).append((dict(r), target))

        print("=" * 82)
        print(f"Over-allocation adjudication — building {building_id}  "
              f"[{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 82)
        print(f"\n  GROUP 1 — allocation <= charged, so paid_cents is understated ({len(raise_paid)})")
        for r, target in raise_paid:
            print(f"    {r['unit_number']:<8} FY{r['fy']} {r['fund_type']:<8} "
                  f"charged {_d(r['charged']):>11}  paid {_d(r['paid_cents']):>11} "
                  f"-> {_d(target):>11}")
        g1 = sum(t - int(r["paid_cents"]) for r, t in raise_paid)
        print(f"    paid_cents raised by {_d(g1)} — every cent already evidenced by an allocation")

        print(f"\n  GROUP 2 — allocation > charged, so the allocation is overstated ({len(reduce_alloc)})")
        for r, target in reduce_alloc:
            print(f"    {r['unit_number']:<8} FY{r['fy']} {r['fund_type']:<8} "
                  f"charged {_d(r['charged']):>11}  alloc {_d(r['alloc']):>11} "
                  f"-> {_d(target):>11}")
        g2 = sum(int(r["alloc"]) - t for r, t in reduce_alloc)
        print(f"    allocations reduced by {_d(g2)} — returns to the receipt as unapplied credit")

        print(f"\n  total over-allocation resolved: {_d(g1 + g2)}")

        retired = await pg.fetchval(
            """
            WITH a AS (SELECT levy_item_id, SUM(allocated_cents) alloc
                         FROM finance.receipt_allocations GROUP BY 1)
            SELECT count(*) FROM finance.receipt_allocations ra
              JOIN finance.levy_items li ON li.levy_item_id = ra.levy_item_id
              JOIN a ON a.levy_item_id = li.levy_item_id
              JOIN finance.receipts rc ON rc.receipt_id = ra.receipt_id
             WHERE a.alloc > li.paid_cents AND rc.retired_at IS NOT NULL
            """
        )
        print(f"  retired receipts among those touched: {retired}  (must be 0)")
        if retired:
            print("\n  REFUSING — a retired receipt is involved. Adjudicate that first.")
            return 1

        if not apply:
            print("\n  DRY-RUN — nothing written. Re-run with --apply.")
            return 0

        async with pg.transaction():
            for r, target in raise_paid:
                # Never above what the item charged — asserted in SQL, not just in Python,
                # so a bad target cannot be written even if classify() were wrong.
                await pg.execute(
                    """
                    UPDATE finance.levy_items
                       SET paid_cents = $2
                     WHERE levy_item_id = $1::uuid
                       AND $2 <= principal_cents + gst_cents + interest_cents
                                 + recovery_costs_cents
                    """,
                    r["lid"], target,
                )
            for r, target in reduce_alloc:
                # Absorb the surplus across the item's allocations, largest first.
                #
                # A single "reduce the largest row" does NOT work: where the surplus
                # equals that row exactly (UA028 sinking — three allocations of $203.98,
                # $112.82 and $91.16 against a $203.98 charge) the row would have to go
                # to zero, `allocated_cents > 0` is a CHECK constraint, and the guarded
                # UPDATE silently did nothing. That left $203.98 unresolved and the
                # post-condition check caught it.
                #
                # A row that must go to zero is DELETED. That is not destroying a trail:
                # a zero allocation cannot exist under the constraint, and the money it
                # claimed returns to the receipt's unallocated balance, which is exactly
                # how unapplied credit is represented. The receipt itself is untouched.
                surplus = int(r["alloc"]) - target
                allocations = await pg.fetch(
                    """
                    SELECT allocation_id::text aid, allocated_cents
                      FROM finance.receipt_allocations
                     WHERE levy_item_id = $1::uuid
                     ORDER BY allocated_cents DESC
                    """,
                    r["lid"],
                )
                for allocation in allocations:
                    if surplus <= 0:
                        break
                    amount = int(allocation["allocated_cents"])
                    take = min(amount, surplus)
                    if take == amount:
                        await pg.execute(
                            "DELETE FROM finance.receipt_allocations WHERE allocation_id = $1::uuid",
                            allocation["aid"],
                        )
                    else:
                        await pg.execute(
                            """
                            UPDATE finance.receipt_allocations
                               SET allocated_cents = allocated_cents - $2
                             WHERE allocation_id = $1::uuid
                            """,
                            allocation["aid"], take,
                        )
                    surplus -= take
                if surplus > 0:
                    raise RuntimeError(
                        f"could not absorb {_d(surplus)} of surplus on levy_item "
                        f"{r['lid']} — refusing to leave a half-corrected item"
                    )

        remaining = await pg.fetchval(
            """
            WITH a AS (SELECT levy_item_id, SUM(allocated_cents) alloc
                         FROM finance.receipt_allocations GROUP BY 1)
            SELECT COALESCE(SUM(a.alloc - li.paid_cents), 0)
              FROM finance.levy_items li JOIN a ON a.levy_item_id = li.levy_item_id
             WHERE a.alloc > li.paid_cents
            """
        )
        print(f"\n  applied. remaining over-allocation: {_d(remaining)}  (must be $0.00)")
        return 0 if int(remaining) == 0 else 1
    finally:
        await pg.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="PRODUCTION FINANCIAL MUTATION")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.building_id, args.apply)))


if __name__ == "__main__":
    main()
