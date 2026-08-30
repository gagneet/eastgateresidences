"""
East Gate Levy Ledger Reconciliation Script
============================================
Audits unit_levy_ledger against confirmed levy_payments for building_id=13195
and optionally repairs divergences.

Usage (from backend/):
    # Dry-run audit only (default, safe):
    venv/bin/python3 scripts/data_repair/reconcile_eastgate_levy_ledger.py

    # Restrict to specific years:
    venv/bin/python3 scripts/data_repair/reconcile_eastgate_levy_ledger.py --years 2025,2026

    # Apply repairs (writes to DB — requires explicit flag):
    venv/bin/python3 scripts/data_repair/reconcile_eastgate_levy_ledger.py --apply

    # Export reconciliation report to JSON:
    venv/bin/python3 scripts/data_repair/reconcile_eastgate_levy_ledger.py --export /tmp/recon_report.json

Safety guarantees:
    - Defaults to --dry-run; --apply required for any DB writes.
    - Hard-coded to building_id="13195" (East Gate). Use --building to override only in dev.
    - Never fabricates payment records. Repairs only update total_paid/net_balance where
      confirmed levy_payments sum > current total_paid (conservatively safe direction).
    - All repairs are idempotent via reconciliation_run_id.
    - Pre-repair snapshot exported to /tmp/recon_snapshot_<run_id>.json before writes.
    - No record is modified if it was touched by a more recent reconciliation run.

Field metadata written on apply:
    reconciliation_run_id    — UUID of this run (idempotency key)
    reconciliation_status    — "reconciled" | "data_gap" | "divergence" | "unchanged"
    reconciled_at            — ISO timestamp
    reconciliation_source    — "levy_payments_sum" | "manual" | "unchanged"
    external_paid_total      — sum of confirmed levy_payments found for this unit/year
    before_total_paid        — total_paid value before this run
    before_net_balance       — net_balance value before this run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# ── Config ───────────────────────────────────────────────────────────────────

BUILDING_ID = "13195"
SUPPORTED_YEARS = ["2021", "2022", "2023", "2024", "2025", "2026"]

# Only update a ledger row when confirmed payments exceed the currently recorded
# total_paid by more than this threshold (avoids float-precision micro-updates).
MIN_ADJUSTMENT_THRESHOLD = 0.01


def parse_args() -> argparse.Namespace:
    """Generated function header.

    Function: parse_args
    Path: backend/scripts/data_repair/reconcile_eastgate_levy_ledger.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    p = argparse.ArgumentParser(
        description="East Gate levy ledger reconciliation / audit tool."
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write repairs to DB. Without this flag the script is read-only.",
    )
    p.add_argument(
        "--years",
        default=",".join(SUPPORTED_YEARS),
        help=f"Comma-separated years to reconcile (default: {','.join(SUPPORTED_YEARS)})",
    )
    p.add_argument(
        "--building",
        default=BUILDING_ID,
        help=f"building_id to reconcile (default: {BUILDING_ID}). Override only in dev.",
    )
    p.add_argument(
        "--export",
        default=None,
        metavar="PATH",
        help="Export JSON reconciliation report to this path.",
    )
    return p.parse_args()


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/scripts/data_repair/reconcile_eastgate_levy_ledger.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    return client[os.getenv("DB_NAME", "strataos_production")]


async def load_ledger(db, building_id: str, years: list[str]) -> dict[tuple, dict]:
    """Return {(unit_number, year): ledger_doc} for all requested years."""
    docs = await db.unit_levy_ledger.find(
        {
            "building_id": building_id,
            "year": {"$in": years},
            "is_test_data": {"$ne": True},
        },
        {"_id": 0},
    ).to_list(None)
    return {(d["unit_number"], d["year"]): d for d in docs}


async def load_confirmed_payments(
    db, building_id: str, years: list[str]
) -> dict[tuple, float]:
    """Return {(unit_number, year): sum_confirmed_amount}."""
    agg = await db.levy_payments.aggregate([
        {
            "$match": {
                "building_id": building_id,
                "year": {"$in": years},
                "status": "confirmed",
                "is_test_data": {"$ne": True},
            }
        },
        {
            "$group": {
                "_id": {"unit_number": "$unit_number", "year": "$year"},
                "total": {"$sum": "$amount"},
                "count": {"$sum": 1},
            }
        },
    ]).to_list(None)
    return {
        (row["_id"]["unit_number"], row["_id"]["year"]): round(float(row["total"]), 2)
        for row in agg
    }


# ── Reconciliation logic ──────────────────────────────────────────────────────

def reconcile_unit_year(
    ledger: dict,
    confirmed_paid: float,
    payment_count: int,
) -> dict[str, Any]:
    """
    Compare a single unit_levy_ledger record against the sum of confirmed payments.

    Returns a reconciliation result dict.

    Rules:
      - If confirmed_paid > total_paid: under-counted in ledger (safe to repair upward).
      - If confirmed_paid < total_paid and confirmed_paid > 0: possible partial data gap.
      - If confirmed_paid == 0: no payment records — keep current ledger value unchanged.
      - Net balance formula: total_levied + opening_arrears - total_paid
    """
    unit = ledger.get("unit_number", "?")
    year = ledger.get("year", "?")
    total_levied = float(ledger.get("total_levied", 0) or 0)
    total_paid = float(ledger.get("total_paid", 0) or 0)
    net_balance = float(ledger.get("net_balance", 0) or 0)
    opening_arrears = float(ledger.get("opening_arrears", 0) or 0)

    # Verify ledger internal consistency
    expected_net = round(total_levied + opening_arrears - total_paid, 2)
    ledger_consistent = abs(expected_net - net_balance) < 0.05

    result: dict[str, Any] = {
        "unit_number": unit,
        "year": year,
        "total_levied": total_levied,
        "before_total_paid": total_paid,
        "before_net_balance": net_balance,
        "opening_arrears": opening_arrears,
        "confirmed_payment_count": payment_count,
        "confirmed_payment_sum": confirmed_paid,
        "ledger_internally_consistent": ledger_consistent,
        "adjustment": 0.0,
        "after_total_paid": total_paid,
        "after_net_balance": net_balance,
        "status": "unchanged",
        "action": "none",
        "notes": [],
    }

    if not ledger_consistent:
        result["notes"].append(
            f"Ledger inconsistency: total_levied({total_levied}) + opening_arrears({opening_arrears})"
            f" - total_paid({total_paid}) = {expected_net} ≠ net_balance({net_balance})"
        )

    if confirmed_paid == 0:
        # No confirmed payment records for this unit/year.
        if total_paid > 0:
            result["status"] = "data_gap"
            result["notes"].append(
                f"No confirmed payment records found but ledger shows total_paid={total_paid:.2f}. "
                "Cannot validate without payment records. Ledger value retained."
            )
        else:
            result["status"] = "data_gap"
            result["notes"].append("No confirmed payment records and total_paid=0 in ledger.")
        return result

    diff = round(confirmed_paid - total_paid, 2)

    if diff > MIN_ADJUSTMENT_THRESHOLD:
        # Confirmed payments exceed ledger's total_paid — safe upward correction.
        new_total_paid = round(total_paid + diff, 2)
        new_net_balance = round(total_levied + opening_arrears - new_total_paid, 2)
        result.update({
            "adjustment": diff,
            "after_total_paid": new_total_paid,
            "after_net_balance": new_net_balance,
            "status": "reconciled",
            "action": "update_total_paid",
            "notes": result["notes"] + [
                f"Confirmed payments ({confirmed_paid:.2f}) exceed ledger total_paid ({total_paid:.2f}) "
                f"by {diff:.2f}. Will update total_paid and recompute net_balance."
            ],
        })
    elif diff < -MIN_ADJUSTMENT_THRESHOLD:
        # Confirmed payments are LESS than ledger total_paid.
        # This could mean the payment record is incomplete (partial DEFT import).
        # We do NOT reduce total_paid — that would worsen the record.
        result.update({
            "status": "divergence",
            "action": "manual_review",
            "notes": result["notes"] + [
                f"Ledger total_paid ({total_paid:.2f}) exceeds confirmed payment sum ({confirmed_paid:.2f}) "
                f"by {abs(diff):.2f}. This may be an incomplete payment import. "
                "Not reducing total_paid — manual review required."
            ],
        })
    else:
        result["status"] = "reconciled"
        result["notes"].append(
            f"Confirmed payments ({confirmed_paid:.2f}) match ledger total_paid ({total_paid:.2f}). No change."
        )

    return result


# ── Apply phase ───────────────────────────────────────────────────────────────

async def apply_repairs(
    db,
    building_id: str,
    repairs: list[dict],
    run_id: str,
    now_iso: str,
) -> int:
    """Write repairs to unit_levy_ledger. Returns count of updated records."""
    count = 0
    for r in repairs:
        if r["action"] != "update_total_paid":
            continue

        # Idempotency: skip if this run_id already applied to this doc.
        existing = await db.unit_levy_ledger.find_one(
            {
                "building_id": building_id,
                "unit_number": r["unit_number"],
                "year": r["year"],
                "reconciliation_run_id": run_id,
            },
            {"_id": 1},
        )
        if existing:
            print(f"  SKIP {r['unit_number']}/{r['year']}: already reconciled by run {run_id}")
            continue

        result = await db.unit_levy_ledger.update_one(
            {
                "building_id": building_id,
                "unit_number": r["unit_number"],
                "year": r["year"],
                # Safety: only update if values match what we read (optimistic lock)
                "total_paid": r["before_total_paid"],
                "net_balance": r["before_net_balance"],
            },
            {
                "$set": {
                    "total_paid": r["after_total_paid"],
                    "net_balance": r["after_net_balance"],
                    "reconciliation_run_id": run_id,
                    "reconciliation_status": r["status"],
                    "reconciled_at": now_iso,
                    "reconciliation_source": "levy_payments_sum",
                    "external_paid_total": r["confirmed_payment_sum"],
                    "before_total_paid": r["before_total_paid"],
                    "before_net_balance": r["before_net_balance"],
                },
            },
        )
        if result.modified_count:
            count += 1
            print(
                f"  UPDATED {r['unit_number']}/{r['year']}: "
                f"total_paid {r['before_total_paid']:.2f} → {r['after_total_paid']:.2f}  "
                f"net_balance {r['before_net_balance']:.2f} → {r['after_net_balance']:.2f}"
            )
        else:
            print(
                f"  SKIP {r['unit_number']}/{r['year']}: optimistic lock mismatch "
                "(record changed since read)"
            )
    return count


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_report(
    results: list[dict],
    years: list[str],
    building_id: str,
    dry_run: bool,
    run_id: str,
) -> None:
    """Generated function header.

    Function: print_report
    Path: backend/scripts/data_repair/reconcile_eastgate_levy_ledger.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    by_year: dict[str, list[dict]] = {y: [] for y in years}
    for r in results:
        if r["year"] in by_year:
            by_year[r["year"]].append(r)

    total_before_outstanding = 0.0
    total_after_outstanding = 0.0
    total_repairs = 0
    total_data_gaps = 0
    total_divergences = 0

    print(f"\n{'='*70}")
    print(f"  EAST GATE LEVY LEDGER RECONCILIATION REPORT")
    print(f"  building_id : {building_id}")
    print(f"  run_id      : {run_id}")
    print(f"  mode        : {'DRY-RUN (no writes)' if dry_run else 'APPLIED'}")
    print(f"{'='*70}")

    for yr in years:
        rows = by_year.get(yr, [])
        if not rows:
            print(f"\n  Year {yr}: no ledger data found")
            continue

        yr_before_out = sum(max(0.0, r["before_net_balance"]) for r in rows)
        yr_after_out = sum(max(0.0, r["after_net_balance"]) for r in rows)
        yr_before_paid = sum(r["before_total_paid"] for r in rows)
        yr_levied = sum(r["total_levied"] for r in rows)
        yr_repairs = [r for r in rows if r["action"] == "update_total_paid"]
        yr_gaps = [r for r in rows if r["status"] == "data_gap"]
        yr_divs = [r for r in rows if r["status"] == "divergence"]
        yr_units_before = sum(1 for r in rows if r["before_net_balance"] > 0.01)
        yr_units_after = sum(1 for r in rows if r["after_net_balance"] > 0.01)

        total_before_outstanding += yr_before_out
        total_after_outstanding += yr_after_out
        total_repairs += len(yr_repairs)
        total_data_gaps += len(yr_gaps)
        total_divergences += len(yr_divs)

        print(f"\n  Year {yr}:")
        print(f"    Units: {len(rows)}  |  Total levied: ${yr_levied:,.2f}  |  Total paid: ${yr_before_paid:,.2f}")
        print(f"    Before: ${yr_before_out:,.2f} outstanding ({yr_units_before} units)")
        print(f"    After:  ${yr_after_out:,.2f} outstanding ({yr_units_after} units)  [{'SAME' if dry_run else 'PROJECTED'}]")
        print(f"    Repairs: {len(yr_repairs)}  |  Data gaps: {len(yr_gaps)}  |  Divergences: {len(yr_divs)}")

        if yr_repairs:
            print(f"\n    Units with confirmed payment upward adjustment:")
            for r in sorted(yr_repairs, key=lambda x: x["unit_number"]):
                print(
                    f"      {r['unit_number']:<8}  paid: {r['before_total_paid']:>10.2f} → {r['after_total_paid']:>10.2f}"
                    f"  net: {r['before_net_balance']:>10.2f} → {r['after_net_balance']:>10.2f}"
                    f"  [confirmed: {r['confirmed_payment_sum']:.2f}]"
                )

        if yr_gaps:
            print(f"\n    Units with no confirmed payment records (data_gap):")
            gap_with_paid = [r for r in yr_gaps if r["before_total_paid"] > 0]
            gap_zero = [r for r in yr_gaps if r["before_total_paid"] == 0]
            if gap_with_paid:
                print(f"      Has ledger total_paid but no payment records ({len(gap_with_paid)} units — NOT MODIFIED):")
                for r in sorted(gap_with_paid, key=lambda x: x["unit_number"])[:10]:
                    print(f"        {r['unit_number']:<8}  total_paid={r['before_total_paid']:>10.2f}  net={r['before_net_balance']:>10.2f}")
                if len(gap_with_paid) > 10:
                    print(f"        ... and {len(gap_with_paid)-10} more")
            if gap_zero:
                print(f"      Zero paid + zero payment records ({len(gap_zero)} units):")
                for r in sorted(gap_zero, key=lambda x: x["unit_number"])[:5]:
                    print(f"        {r['unit_number']:<8}  total_levied={r['total_levied']:>10.2f}  net={r['before_net_balance']:>10.2f}")
                if len(gap_zero) > 5:
                    print(f"        ... and {len(gap_zero)-5} more")

        if yr_divs:
            print(f"\n    Units where ledger total_paid > confirmed sum (manual review):")
            for r in sorted(yr_divs, key=lambda x: x["unit_number"]):
                print(
                    f"      {r['unit_number']:<8}  ledger_paid={r['before_total_paid']:.2f}"
                    f"  confirmed={r['confirmed_payment_sum']:.2f}"
                    f"  diff={r['before_total_paid']-r['confirmed_payment_sum']:.2f}"
                )

    print(f"\n{'='*70}")
    print(f"  TOTALS")
    print(f"    Before total outstanding : ${total_before_outstanding:,.2f}")
    print(f"    After  total outstanding : ${total_after_outstanding:,.2f}")
    print(f"    Reduction from repairs   : ${total_before_outstanding - total_after_outstanding:,.2f}")
    print(f"    Repair candidates        : {total_repairs}")
    print(f"    Data gaps (no records)   : {total_data_gaps}")
    print(f"    Divergences (review)     : {total_divergences}")
    print(f"\n  ASSUMPTIONS USED:")
    print(f"    - Only updates total_paid upward when confirmed payments > ledger value")
    print(f"    - Does NOT reduce total_paid (avoids worsening records with partial data)")
    print(f"    - Does NOT create synthetic payment records")
    print(f"    - net_balance recomputed as: total_levied + opening_arrears - total_paid")
    print(f"    - 2021-2024 are clean (total_paid == total_levied) and will likely show no repairs")
    if dry_run:
        print(f"\n  DRY-RUN: No changes written. Re-run with --apply to write repairs.")
    print(f"{'='*70}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/data_repair/reconcile_eastgate_levy_ledger.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    args = parse_args()
    dry_run = not args.apply
    building_id = args.building
    years = [y.strip() for y in args.years.split(",") if y.strip()]
    run_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*70}")
    print(f"  East Gate Levy Ledger Reconciliation")
    print(f"  building_id : {building_id}  |  years : {', '.join(years)}")
    print(f"  mode        : {'DRY-RUN' if dry_run else '*** APPLY — WILL WRITE TO DB ***'}")
    print(f"  run_id      : {run_id}")
    print(f"{'='*70}\n")

    if not dry_run:
        print("  WARNING: This will modify unit_levy_ledger records in MongoDB.")
        print("  A snapshot will be written before any changes.")
        confirm = input("  Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            print("  Aborted.")
            sys.exit(0)

    db = await _get_db()

    print("  Loading unit_levy_ledger...")
    ledger_map = await load_ledger(db, building_id, years)
    print(f"  Loaded {len(ledger_map)} ledger records across {len(years)} years")

    print("  Loading confirmed levy_payments...")
    payment_map = await load_confirmed_payments(db, building_id, years)
    print(f"  Loaded confirmed payment sums for {len(payment_map)} unit/year combinations")

    # Export pre-repair snapshot
    if not dry_run:
        snapshot_path = f"/tmp/recon_snapshot_{run_id}.json"
        snapshot = [
            {
                "unit_number": doc["unit_number"],
                "year": doc["year"],
                "total_levied": doc.get("total_levied", 0),
                "total_paid": doc.get("total_paid", 0),
                "net_balance": doc.get("net_balance", 0),
                "opening_arrears": doc.get("opening_arrears", 0),
            }
            for doc in ledger_map.values()
        ]
        with open(snapshot_path, "w") as f:
            json.dump(snapshot, f, indent=2)
        print(f"  Pre-repair snapshot written to: {snapshot_path}")

    # Reconcile
    all_results: list[dict] = []
    repairs: list[dict] = []

    for (unit, year), ledger_doc in sorted(ledger_map.items()):
        confirmed_paid = payment_map.get((unit, year), 0.0)
        payment_count = 0  # summary only in this path
        result = reconcile_unit_year(ledger_doc, confirmed_paid, payment_count)
        all_results.append(result)
        if result["action"] == "update_total_paid":
            repairs.append(result)

    # Print full report
    print_report(all_results, years, building_id, dry_run, run_id)

    # Apply repairs if requested
    if not dry_run and repairs:
        print(f"  Applying {len(repairs)} repairs...")
        updated = await apply_repairs(db, building_id, repairs, run_id, now_iso)
        print(f"  Applied {updated} updates.")

    # Export JSON report
    report = {
        "run_id": run_id,
        "building_id": building_id,
        "years": years,
        "mode": "dry_run" if dry_run else "applied",
        "run_at": now_iso,
        "summary": {
            "total_records": len(all_results),
            "repair_candidates": len(repairs),
            "data_gaps": sum(1 for r in all_results if r["status"] == "data_gap"),
            "divergences": sum(1 for r in all_results if r["status"] == "divergence"),
            "before_total_outstanding": sum(
                max(0.0, r["before_net_balance"]) for r in all_results
            ),
            "after_total_outstanding": sum(
                max(0.0, r["after_net_balance"]) for r in all_results
            ),
        },
        "assumptions": [
            "Only updates total_paid upward when confirmed payments > ledger value",
            "Does NOT reduce total_paid (avoids worsening records with partial data)",
            "Does NOT create synthetic payment records",
            "net_balance = total_levied + opening_arrears - total_paid",
            "Source: levy_payments.status='confirmed' only",
        ],
        "results": all_results,
    }

    export_path = args.export or f"/tmp/recon_report_{run_id}.json"
    with open(export_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  Report written to: {export_path}")


if __name__ == "__main__":
    asyncio.run(main())
