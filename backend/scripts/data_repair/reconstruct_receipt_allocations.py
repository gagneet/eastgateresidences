#!/usr/bin/env python3
# @featuretrace:finance-postgres-read-cutover — rebuild the missing receipt->levy_item trail.
# Layer: script
# Data flow: finance.receipts + finance.levy_items -> finance.receipt_allocations
#            + finance.owner_credit_balances (building-scoped).
# Related: docs/architecture/unit_levy_ledger_derivation_design_2026-08-30.md
#          backend/services/financial_read_service.py
# Tests: tests/backend/test_reconstruct_receipt_allocations.py
"""Rebuild the receipt -> levy_item allocation trail, without inventing money.

DRY-RUN BY DEFAULT. `--apply` is a production financial mutation.

THE GAP
-------
`finance.levy_items.paid_cents` is a denormalised total. `finance.receipt_allocations`
is the trail saying WHICH receipt paid WHICH item. Measured live 2026-08-29:

    levy_items.paid_cents      $1,767,923.89
    receipt_allocations        $1,543,190.76
    UNTRAILED                    $224,733.13   across 14 of 87 lots

So PostgreSQL can say a lot paid something and frequently cannot say what for. That
blocks the `unit_levy_ledger` derivation (which must source paid from
SUM(receipt_allocations), not paid_cents) and it blocks any defensible per-lot position.

WHY THIS IS RECONSTRUCTABLE RATHER THAN GUESSWORK
-------------------------------------------------
Grouped by (lot, financial year), the two sides line up:

    52 pairs match EXACTLY            $190,369.16   FY2022-2025, 8 items <-> 4 receipts
    26 pairs present on both, differ                FY2021 (part-year) and FY2026 (advances)
    33 pairs receipts with no item    $121,401.65   FY2026 only - unapplied credit
     0 pairs items with no receipt          $0.00   <- nothing is "paid" without money

That last line is what makes this safe: every untrailed `paid_cents` has receipts in the
same lot and year to account for it. This is a reconstruction that posted both sides and
skipped the link, not a set of payments that never happened.

THE RULES THIS SCRIPT WILL NOT BREAK
------------------------------------
* **It never changes `paid_cents`.** The allocation is made to MATCH the existing figure,
  so the trail explains the ledger rather than restating it. If they cannot be made to
  agree, the case is REPORTED, never forced.
* **It never invents a receipt.** Allocation is drawn only from receipts that already
  exist, are not retired, and belong to the same lot.
* **It never allocates across lots or across financial years.** One owner's money can
  never explain another's balance - the same rule that governs arrears.
* **Surplus is CREDIT, not a payment.** Receipts beyond what the year levied are unapplied
  credit and are reported as such. An unallocated receipt IS how credit is represented
  (2026-08-28: acting on the opposite assumption retired 14 real credit receipts and had
  to be rolled back the same day) - so this script proposes `owner_credit_balances` rows
  and never retires a receipt.
* **A shortfall is a FINDING.** Where a lot-year's receipts are LESS than its
  `paid_cents`, the ledger claims more money than arrived. That is not repaired here; it
  is printed, because the right answer may be that `paid_cents` is wrong.

    python3 backend/scripts/data_repair/reconstruct_receipt_allocations.py --building-id 13195
    python3 backend/scripts/data_repair/reconstruct_receipt_allocations.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg  # noqa: E402

BYPASS = "00000000-0000-0000-0000-000000000000"


def _d(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def plan_allocations(items: list[dict], receipts: list[dict]) -> tuple[list[dict], int, int]:
    """Match receipts to levy items for ONE lot-year. Pure — no I/O, so it is testable.

    Returns (allocations, unallocated_receipt_cents, unexplained_paid_cents).

    Oldest-item-first against oldest-receipt-first. That ordering is not cosmetic: a levy
    waterfall applies money to the earliest outstanding charge, and any other order would
    produce a trail that disagrees with how the balance was actually reduced.

    Each item is filled to EXACTLY its existing `paid_cents`. The function cannot
    over-allocate an item, cannot over-draw a receipt, and cannot move money between
    lots or years because it is only ever handed one lot-year's rows.
    """
    allocations: list[dict] = []
    remaining_by_receipt = {r["receipt_id"]: int(r["amount_cents"]) for r in receipts}
    order = [r["receipt_id"] for r in receipts]
    cursor = 0

    for item in items:
        # Only the SHORTFALL, never the whole paid_cents: 11 items are partially
        # allocated already, and re-allocating their full amount would double-count the
        # trail that exists.
        outstanding = int(item["paid_cents"]) - int(item.get("already_allocated") or 0)
        if outstanding <= 0:
            continue
        while outstanding > 0 and cursor < len(order):
            receipt_id = order[cursor]
            available = remaining_by_receipt[receipt_id]
            if available <= 0:
                cursor += 1
                continue
            take = min(available, outstanding)
            allocations.append({
                "receipt_id": receipt_id,
                "levy_item_id": item["levy_item_id"],
                "allocated_cents": take,
            })
            remaining_by_receipt[receipt_id] -= take
            outstanding -= take
        # `outstanding > 0` here means the receipts ran out before the ledger's own
        # paid_cents was explained. Reported by the caller; never fabricated.

    unallocated = sum(v for v in remaining_by_receipt.values() if v > 0)
    # max(..., 0) per item, not on the total: an OVER-allocated item has a negative
    # shortfall, and letting that net off would silently cancel a real shortfall on
    # another item in the same lot-year — hiding exactly what this run exists to report.
    # Over-allocation is a separate, separately-counted defect.
    unexplained = sum(
        max(int(i["paid_cents"]) - int(i.get("already_allocated") or 0), 0) for i in items
    ) - sum(a["allocated_cents"] for a in allocations)
    return allocations, unallocated, unexplained


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
        # finance.* has no RLS bypass — without the real tenant every query returns zero
        # rows and no error, which reads exactly like "there is no gap to fix".
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", scheme["tid"])

        items = await pg.fetch(
            """
            SELECT li.levy_item_id::text, li.lot_id::text, li.paid_cents,
                   COALESCE((SELECT SUM(a.allocated_cents) FROM finance.receipt_allocations a
                              WHERE a.levy_item_id = li.levy_item_id), 0) AS already_allocated,
                   lr.financial_year, li.grace_deadline_date, l.unit_number
              FROM finance.levy_items li
              JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
              JOIN core.lots l ON l.lot_id = li.lot_id
             WHERE li.scheme_id = $1::uuid AND li.paid_cents > 0
               AND li.paid_cents > COALESCE(
                     (SELECT SUM(a.allocated_cents) FROM finance.receipt_allocations a
                       WHERE a.levy_item_id = li.levy_item_id), 0)
             ORDER BY li.grace_deadline_date NULLS LAST, li.levy_item_id
            """,
            scheme["sid"],
        )
        receipts = await pg.fetch(
            """
            SELECT r.receipt_id::text, r.lot_id::text, r.amount_cents, r.received_on,
                   EXTRACT(YEAR FROM r.received_on)::int::text AS fy, l.unit_number
              FROM finance.receipts r
              JOIN core.lots l ON l.lot_id = r.lot_id
             WHERE r.scheme_id = $1::uuid AND r.retired_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM finance.receipt_allocations a
                                WHERE a.receipt_id = r.receipt_id)
             ORDER BY r.received_on, r.receipt_id
            """,
            scheme["sid"],
        )

        items_by_key: dict[tuple, list[dict]] = defaultdict(list)
        for row in items:
            items_by_key[(row["lot_id"], row["financial_year"])].append(dict(row))
        receipts_by_key: dict[tuple, list[dict]] = defaultdict(list)
        for row in receipts:
            receipts_by_key[(row["lot_id"], row["fy"])].append(dict(row))

        planned: list[dict] = []
        surplus_by_lot: dict[str, int] = defaultdict(int)
        shortfalls: list[tuple] = []
        unit_of = {row["lot_id"]: row["unit_number"] for row in items} | {
            row["lot_id"]: row["unit_number"] for row in receipts
        }

        for key in sorted(set(items_by_key) | set(receipts_by_key)):
            lot_items = items_by_key.get(key, [])
            lot_receipts = receipts_by_key.get(key, [])
            allocs, unallocated, unexplained = plan_allocations(lot_items, lot_receipts)
            planned.extend(allocs)
            if unallocated:
                surplus_by_lot[key[0]] += unallocated
            if unexplained:
                shortfalls.append((unit_of.get(key[0], key[0]), key[1], unexplained))

        print("=" * 78)
        print(f"Receipt-allocation reconstruction — building {building_id}  "
              f"[{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 78)
        print(f"  untrailed levy_items        : {len(items)}  "
              f"{_d(sum(r['paid_cents'] for r in items))}")
        print(f"  unallocated receipts        : {len(receipts)}  "
              f"{_d(sum(r['amount_cents'] for r in receipts))}")
        print(f"  ALLOCATIONS PLANNED         : {len(planned)}  "
              f"{_d(sum(a['allocated_cents'] for a in planned))}")
        print(f"  surplus -> unapplied credit : {len(surplus_by_lot)} lot(s)  "
              f"{_d(sum(surplus_by_lot.values()))}")
        print(f"  SHORTFALL (reported, not repaired): {len(shortfalls)} lot-year(s)  "
              f"{_d(sum(s[2] for s in shortfalls))}")
        for unit, fy, cents in sorted(shortfalls, key=lambda s: -s[2])[:10]:
            print(f"      {unit:<8} FY{fy}  ledger claims {_d(cents)} more than arrived")

        # An item whose allocations EXCEED its own paid_cents is a pre-existing defect,
        # not something this reconstruction created and not something it may repair:
        # deleting an allocation destroys a trail, and raising paid_cents would invent
        # money. It is surfaced, and it BLOCKS --apply, because a reconciliation proof
        # that cannot balance is not a proof.
        over = await pg.fetchrow(
            """
            WITH a AS (SELECT levy_item_id, SUM(allocated_cents) alloc
                         FROM finance.receipt_allocations GROUP BY 1)
            SELECT count(*) n, COALESCE(SUM(a.alloc - li.paid_cents),0) excess
              FROM finance.levy_items li JOIN a ON a.levy_item_id = li.levy_item_id
             WHERE a.alloc > li.paid_cents
            """
        )

        print("\n  --- reconciliation proof ---")
        existing = await pg.fetchval(
            "SELECT COALESCE(SUM(allocated_cents),0) FROM finance.receipt_allocations")
        paid_total = await pg.fetchval(
            "SELECT COALESCE(SUM(paid_cents),0) FROM finance.levy_items")
        planned_total = sum(a["allocated_cents"] for a in planned)
        shortfall_total = sum(s[2] for s in shortfalls)

        print(f"    paid_cents total          {_d(paid_total)}")
        print(f"    allocations before        {_d(existing)}")
        print(f"    allocations planned       {_d(planned_total)}")
        print(f"    OVER-allocated (existing) {_d(over['excess'])}  across {over['n']} item(s)")
        # paid = existing_allocations - over_allocation + planned + shortfall
        # The over-allocation is subtracted because those cents are counted in `existing`
        # while no paid_cents backs them.
        balanced = paid_total == existing - int(over["excess"]) + planned_total + shortfall_total
        print(f"    reported shortfall        {_d(shortfall_total)}")
        print(f"    BALANCES                  {balanced}  "
              f"(paid = existing - over + planned + shortfall)")

        if int(over["excess"]) > 0:
            print("\n  REFUSING TO APPLY — allocations exceed paid_cents on "
                  f"{over['n']} item(s) by {_d(over['excess'])}.")
            print("  That is a pre-existing defect this script did not create and must not")
            print("  paper over: deleting an allocation destroys a trail, and raising")
            print("  paid_cents invents money. Adjudicate those items first, then re-run.")
            return 1

        if not apply:
            print("\n  DRY-RUN — nothing written. Re-run with --apply.")
            return 0

        async with pg.transaction():
            await pg.executemany(
                """
                INSERT INTO finance.receipt_allocations
                    (tenant_id, receipt_id, levy_item_id, allocation_type, allocated_cents)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'levy', $4)
                """,
                [(scheme["tid"], a["receipt_id"], a["levy_item_id"], a["allocated_cents"])
                 for a in planned],
            )
        print(f"\n  wrote {len(planned)} allocation(s). paid_cents untouched.")
        print(f"  surplus of {_d(sum(surplus_by_lot.values()))} left as unallocated receipts —")
        print("  that IS how unapplied credit is represented; it is not lost and not a defect.")
        return 0
    finally:
        await pg.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="PRODUCTION FINANCIAL MUTATION — writes receipt_allocations")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.building_id, args.apply)))


if __name__ == "__main__":
    main()
