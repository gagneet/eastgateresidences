#!/usr/bin/env python3
# @featuretrace:finance-postgres-read-cutover — money cannot pay a charge that does not exist yet.
# Layer: script
# Data flow: finance.receipt_allocations (cross-year) -> re-pointed within the receipt's own
#            levy year, with dependent paid_cents corrected (building-scoped).
# Related: backend/scripts/data_repair/reconstruct_receipt_allocations.py
#          backend/scripts/data_repair/adjudicate_over_allocated_levy_items.py
# Tests: tests/backend/test_cross_year_allocations.py
"""Re-point allocations where the receipt PREDATES the levy item it is applied to.

DRY-RUN BY DEFAULT. `--apply` is a production financial mutation.

THE DEFECT
----------
14 allocations totalling $1,469.05 apply **2021 receipts to FY2026 levy items** — money
received before the charge existed. Created 2026-08-02, so they predate this repair
campaign and were not produced by it.

They are also the reason 13 lots showed an FY2021 shortfall. Each affected lot has four
quarterly 2021 receipts; the first (dated 2021-03-31) was partly drained to FY2026, so the
lot's own FY2021 admin item — due 2022-01-14 — had nothing left to trail against. The
earlier diagnosis "FY2021 paid_cents is overstated" was wrong: the money is there, it was
pointed at the wrong year.

Verified: every lot's FY2021 paid_cents equals its 2021-dated receipts exactly, building
wide $138,460.00 = $138,460.00. Nothing is missing. Only the pointer is wrong.

FORWARD-SHIFTED ALLOCATIONS ARE LEFT ALONE
------------------------------------------
Five allocations run the other way — a 2022 receipt paying an FY2021 item, and so on,
$101.94 each. That is ordinary timing: a Q4 levy due 14 January is paid in January, in the
next calendar year. Only the BACKWARD direction is impossible, and only that is touched.

WHAT IT DOES
------------
1. Re-points each backward allocation to an untrailed levy item belonging to the SAME LOT
   in the receipt's own levy year, oldest charge first.
2. Corrects `paid_cents` on the FY2026 items that lose an allocation, so they do not keep
   claiming money that moved away. This reverses, for those items only, the raise applied
   by `adjudicate_over_allocated_levy_items.py` — that adjudication was correct given the
   allocations as they stood, and is wrong once the allocation is known to be misdirected.
3. Never creates or destroys a receipt, and never changes any total.

    python3 backend/scripts/data_repair/repair_cross_year_allocations.py --building-id 13195
    python3 backend/scripts/data_repair/repair_cross_year_allocations.py --building-id 13195 --apply
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


def plan_repoint(bad: list[dict], targets: list[dict]) -> tuple[list[dict], int]:
    """Match each misdirected allocation to an untrailed item in the receipt's own year.

    Pure. `bad` are allocations to re-point; `targets` are that lot's untrailed items for
    the receipt's year, oldest charge first. Returns (moves, unplaced_cents).

    An allocation that cannot be placed is REPORTED, not deleted — losing it would
    destroy the link between a real receipt and the money it represents.
    """
    remaining = {t["levy_item_id"]: int(t["shortfall"]) for t in targets}
    order = [t["levy_item_id"] for t in targets]
    moves: list[dict] = []
    unplaced = 0

    for allocation in bad:
        amount = int(allocation["allocated_cents"])
        for item_id in order:
            if amount <= 0:
                break
            room = remaining.get(item_id, 0)
            if room <= 0:
                continue
            take = min(room, amount)
            moves.append({
                "allocation_id": allocation["allocation_id"],
                "from_item": allocation["levy_item_id"],
                "to_item": item_id,
                "cents": take,
            })
            remaining[item_id] -= take
            amount -= take
        unplaced += max(amount, 0)
    return moves, unplaced


async def run(building_id: str, apply: bool) -> int:
    pg = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        scheme = await pg.fetchrow(
            "SELECT scheme_id::text sid, tenant_id::text tid FROM core.schemes "
            "WHERE scheme_number = $1 AND is_test_data = FALSE",
            building_id,
        )
        if not scheme:
            raise SystemExit(f"No scheme for building_id={building_id!r}")
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", scheme["tid"])

        bad = await pg.fetch(
            """
            SELECT ra.allocation_id::text, ra.allocated_cents, ra.levy_item_id::text,
                   li.lot_id::text, l.unit_number,
                   EXTRACT(YEAR FROM rc.received_on)::int::text AS receipt_year,
                   lr.financial_year AS item_year
              FROM finance.receipt_allocations ra
              JOIN finance.receipts rc ON rc.receipt_id = ra.receipt_id
              JOIN finance.levy_items li ON li.levy_item_id = ra.levy_item_id
              JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
              JOIN core.lots l ON l.lot_id = li.lot_id
             WHERE li.scheme_id = $1::uuid
               AND EXTRACT(YEAR FROM rc.received_on)::int < lr.financial_year::int
             ORDER BY l.unit_number
            """,
            scheme["sid"],
        )

        by_lot_year: dict[tuple, list[dict]] = defaultdict(list)
        for row in bad:
            by_lot_year[(row["lot_id"], row["receipt_year"])].append(dict(row))

        all_moves: list[dict] = []
        unplaced_total = 0
        print("=" * 80)
        print(f"Cross-year allocation repair — building {building_id}  "
              f"[{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 80)
        print(f"  misdirected allocations (receipt predates the levy): {len(bad)}  "
              f"{_d(sum(r['allocated_cents'] for r in bad))}")

        for (lot_id, receipt_year), allocations in sorted(by_lot_year.items()):
            targets = [
                dict(t) for t in await pg.fetch(
                    """
                    SELECT li.levy_item_id::text,
                           li.paid_cents - COALESCE((
                               SELECT SUM(a.allocated_cents) FROM finance.receipt_allocations a
                                WHERE a.levy_item_id = li.levy_item_id), 0) AS shortfall
                      FROM finance.levy_items li
                      JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                     WHERE li.lot_id = $1::uuid AND lr.financial_year = $2
                       AND li.paid_cents > COALESCE((
                               SELECT SUM(a.allocated_cents) FROM finance.receipt_allocations a
                                WHERE a.levy_item_id = li.levy_item_id), 0)
                     ORDER BY li.grace_deadline_date NULLS LAST
                    """,
                    lot_id, receipt_year,
                )
            ]
            moves, unplaced = plan_repoint(allocations, targets)
            all_moves.extend(moves)
            unplaced_total += unplaced
            unit = allocations[0]["unit_number"]
            print(f"    {unit:<8} {receipt_year} receipt -> FY{allocations[0]['item_year']}  "
                  f"{_d(sum(a['allocated_cents'] for a in allocations)):>10}  "
                  f"re-pointed to {len(moves)} FY{receipt_year} item(s)"
                  + (f"  UNPLACED {_d(unplaced)}" if unplaced else ""))

        print(f"\n  moves planned : {len(all_moves)}  {_d(sum(m['cents'] for m in all_moves))}")
        print(f"  unplaced      : {_d(unplaced_total)}  (reported, never deleted)")

        # The FY2026 items losing an allocation must have paid_cents brought back down,
        # or they keep claiming money that moved to another year.
        losing: dict[str, int] = defaultdict(int)
        for m in all_moves:
            losing[m["from_item"]] += m["cents"]
        print(f"\n  FY2026 items losing an allocation: {len(losing)}")
        for item_id, cents in list(losing.items())[:6]:
            row = await pg.fetchrow(
                """SELECT l.unit_number, f.fund_type, li.paid_cents
                     FROM finance.levy_items li JOIN core.lots l ON l.lot_id=li.lot_id
                     JOIN finance.funds f ON f.fund_id=li.fund_id
                    WHERE li.levy_item_id=$1::uuid""", item_id)
            print(f"      {row['unit_number']:<8} {row['fund_type']:<8} "
                  f"paid {_d(row['paid_cents'])} -> {_d(max(int(row['paid_cents']) - cents, 0))}")

        if not apply:
            print("\n  DRY-RUN — nothing written. Re-run with --apply.")
            return 0

        async with pg.transaction():
            for m in all_moves:
                current = await pg.fetchval(
                    "SELECT allocated_cents FROM finance.receipt_allocations "
                    "WHERE allocation_id = $1::uuid", m["allocation_id"],
                )
                if current is None:
                    continue
                if int(current) == int(m["cents"]):
                    # The whole allocation moves. RE-POINT the row rather than
                    # reducing-then-inserting: reducing it to zero violates
                    # CHECK (allocated_cents > 0) the moment the UPDATE lands, before any
                    # cleanup can run — which is exactly how the first attempt failed and
                    # (correctly) rolled the whole transaction back. Re-pointing also
                    # keeps the original row, so the receipt link is never broken.
                    await pg.execute(
                        """
                        UPDATE finance.receipt_allocations
                           SET levy_item_id = $2::uuid
                         WHERE allocation_id = $1::uuid
                        """, m["allocation_id"], m["to_item"],
                    )
                else:
                    # Partial move: reduce the original (still > 0 by construction) and
                    # add a sibling row carrying the same receipt.
                    await pg.execute(
                        """
                        UPDATE finance.receipt_allocations
                           SET allocated_cents = allocated_cents - $2
                         WHERE allocation_id = $1::uuid
                        """, m["allocation_id"], m["cents"],
                    )
                    await pg.execute(
                        """
                        INSERT INTO finance.receipt_allocations
                            (tenant_id, receipt_id, levy_item_id, allocation_type, allocated_cents)
                        SELECT tenant_id, receipt_id, $2::uuid, 'levy', $3
                          FROM finance.receipt_allocations WHERE allocation_id = $1::uuid
                        """, m["allocation_id"], m["to_item"], m["cents"],
                    )
            for item_id, cents in losing.items():
                await pg.execute(
                    """
                    UPDATE finance.levy_items
                       SET paid_cents = GREATEST(paid_cents - $2, 0)
                     WHERE levy_item_id = $1::uuid
                    """, item_id, cents,
                )

        remaining = await pg.fetchval(
            """
            SELECT COALESCE(SUM(ra.allocated_cents),0) FROM finance.receipt_allocations ra
              JOIN finance.receipts rc ON rc.receipt_id=ra.receipt_id
              JOIN finance.levy_items li ON li.levy_item_id=ra.levy_item_id
              JOIN finance.levy_runs lr ON lr.levy_run_id=li.levy_run_id
             WHERE EXTRACT(YEAR FROM rc.received_on)::int < lr.financial_year::int
            """
        )
        print(f"\n  applied. remaining backward allocations: {_d(remaining)}  (must be $0.00)")
        return 0 if int(remaining) == 0 else 1
    finally:
        await pg.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--apply", action="store_true", help="PRODUCTION FINANCIAL MUTATION")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.building_id, args.apply)))


if __name__ == "__main__":
    main()
