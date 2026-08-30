#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — READ-ONLY finder: exact-timestamp duplicate receipts (GAP-FIN-046 class 1).
# Layer: script
# Data flow: CLI -> finance.receipts + Mongo unit_levy_ledger + portal snapshot -> per-unit reversal PLAN. SELECTs only.
"""READ-ONLY duplicate-receipt finder + portal-validated reversal PLAN — GAP-FIN-046, unit class 1.

Of the 14 units the GAP-FIN-046 allocation repair correctly holds back, one sub-class is
**exact-timestamp duplicate receipts** (the UA050 pattern: e.g. $203.98 posted twice 0.1s apart,
same date — the same insert firing twice, not two real charges). This tool finds those pairs and,
for each unit, PROVES that reversing the extras lands the unit's active-receipt total on the
portal-validated figure (Mongo `unit_levy_ledger.total_paid`, which was synced to the portal).

It writes NOTHING. It emits the exact `receipt_id`s to reverse and a per-unit reconciliation
verdict, so the plan can be checked against the parallel session's independently-pulled timestamps
before any write.

⚠️ LIMITATION — CONFIRMED 2026-08-05, DO NOT USE FOR --apply REVERSAL. Validated against the
receipt-level data: the same-amount+date+timestamp heuristic here OVER-FLAGS. UA008/UA014 were
flagged but are false positives — their same-amount receipts have DISTINCT external_references
(legitimate separate quarter/fund payments backfilled in one batch with identical timestamps), and
both already committed correctly in the repair. Meanwhile the REAL portal-scrape duplicates (UA050)
each carry a DISTINCT UUID external_reference (a fresh id per insert), so they are invisible to
every attribute signature — amount, timestamp, AND external_reference alike. Conclusion: the 14
held-back units CANNOT be de-duplicated by any receipt-attribute heuristic. They require per-unit
reconciliation against the PORTAL's actual per-payment history (not Mongo — Mongo's total_paid is
itself ~$15 off for UA050). Keep this script as a READ-ONLY inspector only; there is no safe
automated reversal for this class.

Deliberately ONE class at a time. It does NOT touch:
  - under-allocation units (UA001 $0.76 / UA009 $71.00 / UA013 $0.31 — receipts that never found an
    open item; a different fix), or
  - estimated-over-receipt arrears units (TH074 $1,649.16 / TH077 $292.00 — the "assume paid on
    time" generator posted phantom payments; reverse-the-excess, but a different source than a
    double-insert).
A unit that does NOT cleanly reconcile after removing its exact-timestamp duplicates is reported as
"not this class" and left for its own targeted pass.

FOLLOW-UP (the actual reversal --apply, spec — run only after this plan is reconciled against the
parallel session's timestamps, and after the 7-day-soak decision):
  1. For each `reverse` receipt_id: `FinancialCoreService.reverse_entry(ReverseEntryCommand(
     journal_entry_id=<receipt.journal_entry_id>, scheme_ref=..., reason="GAP-FIN-046 dup"))` —
     canonical, immutable, creates a mirrored reversal (service.py:809). Per-unit savepoint.
  2. Hard-assert the unit's remaining active receipts == Mongo total_paid within 2c, else roll back.
  3. Re-run gap_fin_046_allocation_integrity_repair --apply — it now sees the duplicates as reversed
     (excluded from Part 2) and allocates only the real receipts, so the unit finally commits.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/gap_fin_046_duplicate_receipt_finder_20260805.py
    venv/bin/python3 scripts/data_repair/gap_fin_046_duplicate_receipt_finder_20260805.py --cluster-seconds 10
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
import asyncpg  # noqa: E402

BUILDING_ID = "13195"
TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"
TOLERANCE_CENTS = 2

# The 14 units the repair holds back (for focus/labelling only — detection runs building-wide).
HELD_BACK = {"TH074", "TH077", "UA001", "UA009", "UA013", "UA016", "UA028", "UA030",
             "UA040", "UA042", "UA050", "UA058", "UA067", "UA070"}

# Portal outstanding snapshot (2026-08, external truth) for context. + = arrears, - = credit.
PORTAL = {"UA001": 0.76, "UA009": 71.00, "UA013": 0.31, "UA016": 1222.04, "UA028": 929.72,
          "UA030": 918.84, "UA040": 918.84, "UA042": 926.55, "UA050": 918.84, "UA058": 918.84,
          "UA067": None, "UA070": 919.66, "TH074": 1649.16, "TH077": 292.00}


async def _mongo():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.getenv("MONGO_URL"))[os.getenv("DB_NAME", "strataos_production")]


async def run(cluster_seconds: float) -> int:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_ID)
    rows = await conn.fetch("""
        SELECT l.unit_number, r.receipt_id, r.received_on, r.amount_cents, r.created_at,
               r.journal_entry_id, EXTRACT(YEAR FROM r.received_on)::int AS yr
        FROM finance.receipts r
        JOIN core.lots l ON l.lot_id = r.lot_id
        LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
        WHERE r.tenant_id = $1 AND rev.journal_entry_id IS NULL
        ORDER BY l.unit_number, r.received_on, r.amount_cents, r.created_at
    """, TENANT_ID)
    await conn.close()

    # Group by (unit, received_on, amount); within a group, flag as duplicates any receipt whose
    # created_at is within cluster_seconds of an earlier kept receipt in the same group.
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        groups[(r["unit_number"], r["received_on"], r["amount_cents"])].append(r)

    dupes_by_unit: dict[str, list] = defaultdict(list)
    excess_2026: dict[str, int] = defaultdict(int)
    for (unit, _d, amt), recs in groups.items():
        if len(recs) < 2:
            continue
        recs.sort(key=lambda x: x["created_at"])
        kept = recs[0]
        for r in recs[1:]:
            delta = abs((r["created_at"] - kept["created_at"]).total_seconds())
            if delta <= cluster_seconds:
                dupes_by_unit[unit].append(r)
                if r["yr"] == 2026:
                    excess_2026[unit] += amt
            else:
                kept = r  # far apart in time → treat as a legitimate separate charge, keep it

    # Current active 2026 total per unit.
    active_2026: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["yr"] == 2026:
            active_2026[r["unit_number"]] += r["amount_cents"]

    mdb = await _mongo()
    target_2026: dict[str, int] = {}
    async for d in mdb.unit_levy_ledger.find({"building_id": BUILDING_ID, "year": "2026"},
                                             {"unit_number": 1, "total_paid": 1, "_id": 0}):
        target_2026[str(d.get("unit_number"))] = round((d.get("total_paid") or 0) * 100)

    print(f"\nGAP-FIN-046 duplicate-receipt finder — building {BUILDING_ID}  cluster<= {cluster_seconds}s\n")
    reconciles, not_this_class = [], []
    for unit in sorted(dupes_by_unit):
        post = active_2026[unit] - excess_2026[unit]
        tgt = target_2026.get(unit)
        portal = PORTAL.get(unit)
        held = " (held-back)" if unit in HELD_BACK else ""
        if tgt is not None and abs(post - tgt) <= TOLERANCE_CENTS:
            reconciles.append(unit)
            verdict = "RECONCILES → safe to reverse"
        else:
            not_this_class.append(unit)
            verdict = "does NOT reconcile → not this class (leave for its own pass)"
        print(f"  {unit:6}{held}  {len(dupes_by_unit[unit])} dup receipt(s), "
              f"2026 excess=${excess_2026[unit]/100:,.2f}")
        print(f"         active2026=${active_2026[unit]/100:,.2f} − excess = ${post/100:,.2f}  "
              f"vs mongo_target=${(tgt or 0)/100:,.2f}  portal_outstanding="
              f"{('$'+format(portal,',.2f')) if portal is not None else 'n/a'}  → {verdict}")
        for r in dupes_by_unit[unit]:
            print(f"           reverse receipt_id={r['receipt_id']} journal_entry_id={r['journal_entry_id']} "
                  f"${r['amount_cents']/100:,.2f} {r['received_on']} created_at={r['created_at']}")

    print(f"\nSUMMARY: {len(dupes_by_unit)} units with same-timestamp duplicates.")
    print(f"  RECONCILE after reversal (this class): {reconciles or '—'}")
    print(f"  NOT this class (need other pass):       {not_this_class or '—'}")
    print("\nREAD-ONLY: nothing written. See module docstring for the reversal --apply follow-up spec.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="READ-ONLY GAP-FIN-046 duplicate-receipt finder + reversal plan.")
    ap.add_argument("--cluster-seconds", type=float, default=5.0,
                    help="Max created_at gap (s) for two same date/amount receipts to count as a double-insert.")
    args = ap.parse_args()
    return asyncio.run(run(args.cluster_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
