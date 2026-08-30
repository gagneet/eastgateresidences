#!/usr/bin/env python3
# @featuretrace:finance-shadow-reads — GAP-FIN-057/046 collision fix: phantom-allocation
#   removal ONLY (no paid_cents decrement, no Bug-B backfill) for units whose paid_cents
#   is already anchored to the 2026-08-06 portal-evidenced adjustment.
# Layer: script (data-repair)
# Data flow: reads finance.receipt_allocations / receipts / journal_entries (reversal join)
#            + services.portal_ledger_reconciliation_service (portal evidence, verification
#            only) -> DELETE finance.receipt_allocations rows (--apply). Never touches
#            finance.levy_items.paid_cents.
# Related: scripts/data_repair/gap_fin_057_reverse_orphaned_allocations_20260807.py
#          scripts/data_repair/gap_fin_046_allocation_integrity_repair_20260805.py
#          scripts/data_repair/gap_fin_046_portal_evidenced_adjustment_20260806.py
#          tasks/CLOSE-ALL-BLOCKERS-RUNBOOK-2026-08-09.md
"""GAP-FIN-046/057 collision — phantom-allocation cleanup ONLY, portal-anchored (2026-08-09)
================================================================================================
Live 2026-08-09: the standard repair (gap_fin_046_allocation_integrity_repair_20260805.py,
Part 1 = delete stale/reversed-receipt allocations + decrement paid_cents, Part 2 = backfill
unallocated active receipts) rolled back on 11 units. Root-cause traced and CONFIRMED
uniform for 10 of the 11 (UA001, UA009, UA013, UA016, UA028, UA040, UA042, UA058, UA067,
UA070): each already carries a 2026-08-06 GAP-FIN-046 portal-evidenced adjustment
(gap_fin_046_portal_evidenced_adjustment_20260806.py, source_type='reconstruction_adjustment')
that decremented finance.levy_items.paid_cents DIRECTLY, outside receipt_allocations
entirely, because that script's own investigation found real (non-reversed) PG receipts for
these units sum to EXACTLY the levied amount every year 2021-2026 -- i.e. the historical
reconstruction generator fabricated "paid in full" receipts for periods the owner did not
actually pay. Backfilling those same active receipts (Part 2) re-introduces exactly the
overstatement the 08-06 correction removed; decrementing paid_cents again for the phantom
removal (Part 1) double-subtracts the portion 08-06 already accounted for, driving paid_cents
negative on some line items (CHECK(paid_cents >= 0) violations) or under target on others.
Confirmed: SUM(all receipt_allocations, incl. stale) - 08-06 adjustment == current paid_cents,
exact to the cent, for all 10.

DECISION (2026-08-09, explicit sign-off): for these 10 units, the 08-06 portal-verified
adjustment IS ground truth. This script:
  1. Deletes ONLY the stale (reversed-receipt) finance.receipt_allocations rows -- pure
     bookkeeping cleanup so the allocation table stops carrying orphaned linkages to
     reversed receipts. This is the same "Bug A" identification query as GAP-FIN-057 /
     GAP-FIN-046's Part 1.
  2. Does NOT decrement finance.levy_items.paid_cents. paid_cents stays exactly where the
     08-06 adjustment left it.
  3. Does NOT allocate any currently-unallocated active receipt for these units (no Bug-B
     backfill) -- those receipts are the disputed, reconstruction-generator-fabricated ones
     the 08-06 finding already proved unreliable. Explicitly excluded from scope, not
     silently skipped.
  4. Verifies the resulting state against the PORTAL balance (services.
     portal_ledger_reconciliation_service._latest_portal_by_unit -- the same evidence
     source the 08-06 script itself used), NOT Mongo (stale for 2026) and NOT
     SUM(receipt_allocations) (short by design for these units -- that gap is the
     documented, permanent reconciliation exception, never force-matched).

UA030 is explicitly OUT OF SCOPE for this script -- it has no 08-06 adjustment (confirmed
2026-08-09: its paid_cents == SUM(all receipt_allocations) exactly, no collision signature),
so its earlier rollback has a different, not-yet-diagnosed cause. Forcing it through this
script's portal-anchored logic would be wrong for a unit with no portal anchor to be
anchored to. UNITS is intentionally hardcoded to the confirmed-uniform subgroup, not derived
from a live query, so a future run can't silently pull in an undiagnosed unit.

REQUIRED TASK HEADER (Financial Evidence Gateway)
--------------------------------------------------
  Serving source before/after : Mongo / Mongo (does not promote PG reads)
  Authoritative source        : staging_strata_web_snapshots via
                                 portal_ledger_reconciliation_service._latest_portal_by_unit
                                 (the same source gap_fin_046_portal_evidenced_adjustment_
                                 20260806.py used to compute the adjustment being anchored to)
  Evidence type                : none posted -- pure deletion of orphaned linkage rows;
                                 verification only against portal_balance_snapshot
  Posting command               : raw DELETE on finance.receipt_allocations (allocation rows
                                 are derived bookkeeping linkages, not financial evidence --
                                 same DEVIATION-FROM-NEVER-HARD-DELETE rationale as
                                 gap_fin_046_allocation_integrity_repair_20260805.py and
                                 gap_fin_057_reverse_orphaned_allocations_20260807.py)
  Affected journals             : none (finance.levy_items.paid_cents is explicitly NOT
                                 touched by this script)
  Reconciliation invariants    : per unit, FY2026 outstanding (levied - paid) == portal net
                                 (+-2 cents), UNCHANGED before/after (paid_cents untouched);
                                 paid_cents != SUM(receipt_allocations) afterward is EXPECTED
                                 and must be documented, never treated as a defect
  Production mutation authorised: NO by default -- --apply required

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/gap_fin_046_phantom_removal_portal_anchored_20260809.py
    venv/bin/python3 scripts/data_repair/gap_fin_046_phantom_removal_portal_anchored_20260809.py --apply

Safety guarantees:
    - Defaults to --dry-run; --apply required for any DB write.
    - Hardcoded to the confirmed-uniform 10-unit subgroup (see UNITS below) -- never
      derived live, so scope can't silently drift.
    - Per-unit SAVEPOINT (session.begin_nested()); a unit whose post-deletion outstanding
      no longer matches its portal figure is rolled back and reported, never partially
      written.
    - Idempotent: a unit with no more stale allocations is a no-op.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import asyncpg  # noqa: E402

BUILDING_ID = "13195"
TENANT_ID = "9e9d75c2-bd92-4695-8487-1592018c3af9"
FINANCIAL_YEAR = "2026"
TOLERANCE_CENTS = 2

# Confirmed-uniform subgroup (2026-08-09) -- see module docstring. UA030 excluded (different
# root cause, resolved separately via the allocate_payment() fix + full backfill).
# TH074/TH077/UA050 added 2026-08-09: previously held out under FLAGGED_UNRESOLVED_UNITS in
# gap_fin_046_allocation_integrity_repair_20260805.py over active-receipt authenticity
# (suspected generator/duplicate receipts) -- irrelevant here, since this script never
# allocates or trusts active receipts, only deletes stale/reversed ones. Independently
# verified: each already reconciles FY2026 outstanding to its own portal figure exactly
# (TH074 $1,649.58, TH077 $292.00, UA050 $919.10, all within tolerance) under current
# paid_cents, same as the other 10 -- safe to include.
# @featuretrace:portal-anchored-paid-cents — The signed-off paid_cents/allocation exception.
# Layer: script
# Data flow: staging_strata_web_snapshots (portal balance) -> 2026-08-06 adjustment ->
#            finance.levy_items.paid_cents LEFT AS-IS while stale finance.receipt_allocations
#            rows are deleted (building-scoped). paid_cents then feeds
#            financial_read_service -> /arrears/detail.
# Related: tests/backend/test_portal_anchored_paid_cents_exception.py
#          tasks/GAP-FIN-073-post-restore-finance-audit.md  (Proof 2)
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#            (recompute_paid_cents -- the repair that must NOT be run over these units)
#
# LESSON (2026-08-27): this deliberate gap looks exactly like corruption. An integrity check
# reports 521 of 3,480 levy_items where paid_cents != SUM(receipt_allocations), 505 claiming
# $228,873.24 with nothing behind them, and the obvious repair is already built:
# recompute_paid_cents() sets paid_cents := SUM(surviving allocations).
#
# Running it over these units would INFLATE their arrears by ~$224,733. The 13 units
# `/arrears/detail` reports as in arrears are exactly the 13 in UNITS below; arrears derives
# from paid_cents, which holds the portal-verified truth, so $7,851.30 is CORRECT and
# force-matching would bill 13 real owners for debt they do not owe. GAP-FIN-073 originally
# recorded this as an integrity failure -- the wrong call is kept in that ticket on purpose,
# because the reasoning is what the next audit will reproduce.
UNITS = [
    "UA001", "UA009", "UA013", "UA016", "UA028", "UA040", "UA042", "UA058", "UA067", "UA070",
    "TH074", "TH077", "UA050",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", default=False, help="Write. Without this flag, dry-run only.")
    return p.parse_args()


def _pg_dsn() -> str:
    import os
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")


async def _fetch_scope() -> dict:
    """Read-only: the same stale-allocation identification query as GAP-FIN-057 /
    GAP-FIN-046 Part 1, restricted to UNITS."""
    conn = await asyncpg.connect(_pg_dsn())
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_ID)
    try:
        stale = await conn.fetch(
            """
            SELECT l.unit_number, ra.allocation_id, ra.receipt_id, ra.levy_item_id, ra.allocated_cents
            FROM finance.receipt_allocations ra
            JOIN finance.receipts        r    ON r.receipt_id       = ra.receipt_id
            JOIN finance.levy_items      li   ON li.levy_item_id    = ra.levy_item_id
            JOIN finance.journal_entries orig ON orig.journal_entry_id = r.journal_entry_id
            JOIN core.lots               l    ON l.lot_id           = li.lot_id
            WHERE l.unit_number = ANY($1)
              AND ra.levy_item_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM finance.journal_entries rev
                          WHERE rev.reversal_of_id = orig.journal_entry_id
                            AND rev.status = 'posted')
            ORDER BY l.unit_number, ra.receipt_id, ra.allocation_id
            """,
            UNITS,
        )
    finally:
        await conn.close()

    by_unit: dict[str, list[dict]] = {}
    for r in stale:
        by_unit.setdefault(r["unit_number"], []).append(dict(r))
    return by_unit


async def _portal_balances() -> dict[str, int]:
    """Portal net arrears per unit_number, FY2026 -- the verification target. Same
    evidence source as gap_fin_046_portal_evidenced_adjustment_20260806.py."""
    from database import db
    from request_context import set_ctx_building_id
    from services.portal_ledger_reconciliation_service import _latest_portal_by_unit
    from utils.unit_number import extract_lot_int

    set_ctx_building_id(BUILDING_ID)
    snapshot_date, portal_by_lot_int = await _latest_portal_by_unit(db, BUILDING_ID, FINANCIAL_YEAR)
    if snapshot_date is None:
        raise RuntimeError(f"No staged portal snapshot for building {BUILDING_ID} FY{FINANCIAL_YEAR}")

    out: dict[str, int] = {}
    for u in UNITS:
        lot_int = extract_lot_int(u)
        cents = portal_by_lot_int.get(lot_int)
        if cents is not None:
            out[u] = int(cents)
    return out


async def _pg_outstanding_fy2026(conn, unit_number: str) -> int:
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents
                             + li.recovery_costs_cents - li.paid_cents), 0) AS outstanding
        FROM finance.levy_items li
        JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
        JOIN core.lots l ON l.lot_id = li.lot_id
        WHERE l.unit_number = $1 AND l.tenant_id = $2 AND lr.financial_year = $3
        """,
        unit_number, TENANT_ID, FINANCIAL_YEAR,
    )
    return int(row["outstanding"])


def _aud(c: int) -> str:
    return f"${c / 100:,.2f}"


async def main() -> int:
    args = parse_args()
    by_unit = await _fetch_scope()
    portal = await _portal_balances()

    print(f"GAP-FIN-046/057 collision fix — phantom removal ONLY, portal-anchored — "
          f"building {BUILDING_ID}, confirmed-uniform subgroup ({len(UNITS)} units)\n")

    conn = await asyncpg.connect(_pg_dsn())
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_ID)
    try:
        print(f"{'unit':8} {'stale#':>6} {'remove':>13} {'FY26 outstanding (pre)':>24} "
              f"{'portal net':>13} {'match?':>8}")
        print("-" * 90)
        rows_out = []
        for u in UNITS:
            stale = by_unit.get(u, [])
            remove = sum(s["allocated_cents"] for s in stale)
            outstanding = await _pg_outstanding_fy2026(conn, u)
            portal_cents = portal.get(u)
            portal_str = _aud(portal_cents) if portal_cents is not None else "NO SNAPSHOT"
            match = (portal_cents is not None and abs(outstanding - portal_cents) <= TOLERANCE_CENTS)
            rows_out.append((u, stale, remove, outstanding, portal_cents, match))
            print(f"{u:8} {len(stale):>6} {_aud(remove):>13} {_aud(outstanding):>24} "
                  f"{portal_str:>13} {'OK' if match else 'MISMATCH':>8}")

        print("\npaid_cents is intentionally NOT touched by this script. FY26 outstanding "
              "shown above is the PRE-existing (08-06-adjustment-anchored) figure and is not "
              "expected to change after the deletion, since paid_cents is untouched.")

        any_mismatch = any(not m for *_, m in rows_out)
        if any_mismatch:
            print("\nWARNING: one or more units do not currently match their portal figure "
                  "even BEFORE this script's deletion. That is a pre-existing condition (not "
                  "caused by this script) but should be investigated before --apply.")

        if not args.apply:
            print("\nDry-run complete. Re-run with --apply to delete the stale allocation rows "
                  "listed above (paid_cents will NOT be touched).")
            return 0

        print("\n--apply requested — deleting stale allocations per unit, in a SAVEPOINT, "
              "re-verifying FY2026 outstanding against portal after each unit's deletion:\n")

        kept, rolled_back = [], []
        for u, stale, remove, outstanding_before, portal_cents, _ in rows_out:
            if not stale:
                kept.append(u)
                continue
            tx = conn.transaction()
            await tx.start()
            try:
                for s in stale:
                    await conn.execute(
                        "DELETE FROM finance.receipt_allocations WHERE allocation_id = $1",
                        s["allocation_id"],
                    )
                outstanding_after = await _pg_outstanding_fy2026(conn, u)
                if portal_cents is None:
                    raise ValueError(f"{u}: no portal snapshot to verify against")
                if abs(outstanding_after - portal_cents) > TOLERANCE_CENTS:
                    raise ValueError(
                        f"{u}: post-deletion FY2026 outstanding={_aud(outstanding_after)} "
                        f"!= portal={_aud(portal_cents)}"
                    )
                if outstanding_after != outstanding_before:
                    raise ValueError(
                        f"{u}: FY2026 outstanding changed from {_aud(outstanding_before)} to "
                        f"{_aud(outstanding_after)} — expected no change (paid_cents untouched); "
                        "refusing to commit an unexplained shift."
                    )
            except Exception as exc:  # noqa: BLE001
                await tx.rollback()
                rolled_back.append({"unit_number": u, "error": str(exc)})
                continue
            await tx.commit()
            kept.append(u)
            print(f"  {u}: deleted {len(stale)} stale allocation(s) ({_aud(remove)}); "
                  f"FY2026 outstanding unchanged at {_aud(outstanding_before)}, "
                  f"matches portal {_aud(portal_cents)}.")

        if kept:
            print(f"\nCommitted for {len(kept)} unit(s): {kept}")
        if rolled_back:
            print(f"\nROLLED BACK for {len(rolled_back)} unit(s) (nothing written):")
            for r in rolled_back:
                print(f"  {r['unit_number']}: {r['error']}")
        return 1 if rolled_back and not kept else 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
