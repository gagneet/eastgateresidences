#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — READ-ONLY diagnostic: is the GAP-FIN-046 repair safe to --apply?
# Layer: script
# Data flow: CLI -> finance.receipts / finance.receipt_allocations / finance.levy_items / finance.levy_runs
#            + Mongo unit_levy_ledger -> per-unit per-year state table -> stdout. SELECTs only, no writes.
"""READ-ONLY: answer the one question that decides whether the GAP-FIN-046 repair is safe.

The repair script (gap_fin_046_allocation_integrity_repair_20260805.py) assumes that only the
342 *2026* receipts are unallocated, and re-allocates just those. That is correct ONLY IF the
2021-2025 receipts are already allocated by their own ACTIVE receipts. If instead the phantom
(reversed) allocations are the *only* allocations a unit has, then:

  * Part 1 removes them -> 2021-2025 levy_items reopen,
  * Part 2 (2026-only) allocates 2026 receipts to the now-oldest OPEN item -> which is 2021,
    not 2026 (get_open_levy_items ORDER BY created_at ASC; allocate_payment fills oldest-first),
  * the per-year hard-assert fails -> the unit rolls back. Safe (no corruption) but a no-op.

This script classifies every unit's allocations by whether the underlying receipt is active or
reversed, per financial year, and compares against the active-receipts total (the correct
target = canonical + 2026) AND against Mongo unit_levy_ledger.total_paid (the repair's current
assert target — which we believe is STALE for 2026). Output tells you, per unit:

  SCENARIO A  historical already allocated by active receipts  -> parallel repair is sufficient
  SCENARIO B  phantom is the only allocation (historical unallocated) -> Part 2 must be broadened
              to allocate ALL active unallocated receipts (drop the received_on>=2026 filter),
              in received_on order, and the assert target must be active receipts, not Mongo.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/gap_fin_046_allocation_state_diagnostic_20260805.py
    venv/bin/python3 scripts/data_repair/gap_fin_046_allocation_state_diagnostic_20260805.py --unit UA050
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
import asyncpg  # noqa: E402

BUILDING_ID = "13195"
TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"


async def _mongo():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.getenv("MONGO_URL"))[os.getenv("DB_NAME", "strataos_production")]


async def run(unit_filter: str | None) -> int:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_ID)

    # Active receipts by unit/year (non-reversed) — the CORRECT paid target.
    active_recv = await conn.fetch("""
        SELECT l.unit_number, EXTRACT(YEAR FROM r.received_on)::int AS yr,
               SUM(r.amount_cents) AS cents, COUNT(*) AS n
        FROM finance.receipts r
        JOIN core.lots l ON l.lot_id = r.lot_id
        LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
        WHERE r.tenant_id = $1 AND rev.journal_entry_id IS NULL
        GROUP BY 1,2
    """, TENANT_ID)

    # Active receipts that currently have NO allocation, by year (what Part 2 must cover).
    unalloc = await conn.fetch("""
        SELECT l.unit_number, EXTRACT(YEAR FROM r.received_on)::int AS yr,
               SUM(r.amount_cents) AS cents, COUNT(*) AS n
        FROM finance.receipts r
        JOIN core.lots l ON l.lot_id = r.lot_id
        LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
        LEFT JOIN finance.receipt_allocations ra ON ra.receipt_id = r.receipt_id
        WHERE r.tenant_id = $1 AND rev.journal_entry_id IS NULL AND ra.allocation_id IS NULL
        GROUP BY 1,2
    """, TENANT_ID)

    # Allocations split by active vs reversed underlying receipt, by levy-run year.
    alloc_split = await conn.fetch("""
        SELECT l.unit_number, lr.financial_year AS yr,
               SUM(CASE WHEN rev.journal_entry_id IS NULL THEN ra.allocated_cents ELSE 0 END) AS active_cents,
               SUM(CASE WHEN rev.journal_entry_id IS NOT NULL THEN ra.allocated_cents ELSE 0 END) AS phantom_cents
        FROM finance.receipt_allocations ra
        JOIN finance.receipts r ON r.receipt_id = ra.receipt_id
        LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
        JOIN finance.levy_items li ON li.levy_item_id = ra.levy_item_id
        JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
        JOIN core.lots l ON l.lot_id = li.lot_id
        WHERE ra.tenant_id = $1
        GROUP BY 1,2
    """, TENANT_ID)

    # Current levy_items.paid_cents by unit/year.
    paid = await conn.fetch("""
        SELECT l.unit_number, lr.financial_year AS yr, SUM(li.paid_cents) AS cents
        FROM finance.levy_items li
        JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
        JOIN core.lots l ON l.lot_id = li.lot_id
        WHERE li.tenant_id = $1
        GROUP BY 1,2
    """, TENANT_ID)
    await conn.close()

    def index(rows, *keys):
        d = {}
        for r in rows:
            d[tuple(str(r[k]) for k in keys)] = r
        return d
    ar = index(active_recv, "unit_number", "yr")
    un = index(unalloc, "unit_number", "yr")
    al = index(alloc_split, "unit_number", "yr")
    pd = index(paid, "unit_number", "yr")

    # Mongo unit_levy_ledger.total_paid (the repair's current assert target).
    mdb = await _mongo()
    ledger = {}
    async for doc in mdb.unit_levy_ledger.find({"building_id": BUILDING_ID}, {"unit_number": 1, "year": 1, "total_paid": 1, "_id": 0}):
        ledger[(str(doc.get("unit_number")), str(doc.get("year")))] = round((doc.get("total_paid") or 0) * 100)

    units = sorted({r["unit_number"] for r in active_recv})
    if unit_filter:
        units = [u for u in units if u == unit_filter]
    years = ["2021", "2022", "2023", "2024", "2025", "2026"]

    scen_A = scen_B = mixed = 0
    print(f"\nGAP-FIN-046 allocation-state diagnostic — building {BUILDING_ID}  (all cents shown as $)\n")
    for u in units:
        hist_active_alloc = sum(int(al.get((u, y), {}).get("active_cents", 0) or 0) for y in ["2021", "2022", "2023", "2024", "2025"])
        hist_active_recv = sum(int(ar.get((u, y), {}).get("cents", 0) or 0) for y in ["2021", "2022", "2023", "2024", "2025"])
        hist_unalloc = sum(int(un.get((u, y), {}).get("cents", 0) or 0) for y in ["2021", "2022", "2023", "2024", "2025"])
        # Scenario call: is historical covered by ACTIVE allocations (A) or essentially not (B)?
        if hist_active_recv > 0 and hist_active_alloc >= hist_active_recv - 200:
            verdict = "A: historical allocated (repair sufficient)"; scen_A += 1
        elif hist_unalloc >= hist_active_recv - 200 and hist_active_recv > 0:
            verdict = "B: historical UNALLOCATED (repair must broaden Part 2)"; scen_B += 1
        else:
            verdict = "MIXED — inspect"; mixed += 1
        m26 = ledger.get((u, "2026"))
        r26 = int(ar.get((u, "2026"), {}).get("cents", 0) or 0)
        target_note = ("mongo2026 missing" if m26 is None
                       else ("mongo2026 STALE (<active)" if m26 < r26 - 200 else "mongo2026 ~ok"))
        if unit_filter or verdict.startswith("B") or verdict.startswith("MIXED"):
            print(f"  {u:6} {verdict}")
            print(f"         hist active_recv=${hist_active_recv/100:,.2f}  hist active_alloc=${hist_active_alloc/100:,.2f}  "
                  f"hist unalloc=${hist_unalloc/100:,.2f}  | assert-target: {target_note} "
                  f"(mongo2026=${(m26 or 0)/100:,.2f} vs active2026=${r26/100:,.2f})")

    print(f"\nSUMMARY: {len(units)} units  |  Scenario A={scen_A}  Scenario B={scen_B}  MIXED={mixed}")
    print("  A -> parallel repair (Part 2 = 2026-only) is sufficient; assert target still needs review.")
    print("  B/MIXED -> Part 2 must allocate ALL active unallocated receipts (not just 2026), in")
    print("            received_on order, and the per-year assert must target ACTIVE RECEIPTS, not")
    print("            Mongo unit_levy_ledger.total_paid (stale for 2026).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="READ-ONLY GAP-FIN-046 allocation-state diagnostic.")
    ap.add_argument("--unit", default=None, help="Restrict to one unit_number (e.g. UA050, TH076).")
    args = ap.parse_args()
    return asyncio.run(run(args.unit))


if __name__ == "__main__":
    raise SystemExit(main())
