#!/usr/bin/env python3
"""Building-agnostic portal ⇄ constructed-ledger correction (Phase 2).

Replaces the East-Gate-hardcoded one-off ledger-sync scripts
(east_gate_2021_2025_ledger_sync.py, east_gate_2026_levy_payment_backfill.py's opening back-solve)
with a single generic, dual-control, idempotent correction that works for ANY building we scrape
from the Strata Web portal.

WHAT IT DOES — re-chain, nothing else:
  For each unit with a CHAIN BREAK (a year whose stored opening != the prior year's stored
  closing, per fund — e.g. a current year whose opening was back-solved from a portal scrape
  instead of carried forward), it re-chains openings FORWARD from the first broken year:

      opening(y)  = closing(y-1)                    # carried forward, per fund
      closing(y)  = opening(y) + levied + interest - paid   # stored levied/paid/interest TRUSTED
      net_balance = admin_closing + sinking_closing

  and recomputes every subsequent year the same way. It NEVER changes levied/paid/interest, and it
  NEVER reads or writes the portal balance — the portal is a VERIFICATION signal only (the residual
  `rechained_balance - portal` is reported, never forced to zero, never posted as a journal). This
  is the discipline in docs/architecture/strata_web_portal_reconciliation_logic.md §6 and the whole
  reason the old `total_paid = total_levied - net_balance` path (which caused a $1.77M duplicate
  credit) is banned.

WHAT IT DOES NOT DO:
  * It does not fix a residual paid-gap (a real missing/duplicate payment or wrong charge). Those
    need evidence, not re-chaining — the read-only engine (services/portal_ledger_reconciliation_
    service.py, GET /finance/reconciliation/analysis) reports them with a suspected-year ranking;
    resolve them via the matching/promotion pipeline or a source-document correction, then re-run.
  * It never creates or reverses payment records.

SAFETY:
  * Dry-run by DEFAULT. --apply required to write.
  * Dual control: --authorised-by and --executed-by must be two distinct identities.
  * Idempotent: keyed by a run-id; a doc already carrying this run-id is skipped.
  * Optimistic lock: a row is only updated if its opening/closing/net_balance still match what was
    read (a concurrent change aborts that row, never a blind overwrite).
  * Pre-write JSON snapshot of every affected row is written before any change.
  * Writes ONLY to unit_levy_ledger's balance fields + reconciliation metadata. reconciliation_
    source is "ledger_rechain" — NEVER "strata_web_portal_scrape".

Usage (from backend/):
    venv/bin/python3 scripts/reconcile_portal_ledger.py --building-id 13195                 # dry-run
    venv/bin/python3 scripts/reconcile_portal_ledger.py --building-id 13195 --export /tmp/r.json
    venv/bin/python3 scripts/reconcile_portal_ledger.py --building-id 13195 \
        --apply --authorised-by <uuid-A> --executed-by <uuid-B>

Note: --apply against a PRODUCTION building mutates real financial records and, per the Financial
Evidence Gateway rules, requires explicit production-mutation authorisation and reconciliation
evidence. The default dry-run is always safe.
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
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.portal_ledger_reconciliation_service import (  # noqa: E402
    DEFAULT_TOLERANCE_CENTS,
    UnitYearLedger,
    _latest_portal_by_unit,
    compute_rechain_plan,
    compute_unit_reconciliation,
)
from utils.unit_number import extract_lot_int  # noqa: E402


def _cents_to_dollars(cents: int) -> float:
    """unit_levy_ledger stores dollar floats (a known cents-violation, CLAUDE.md). We compute in
    cents and convert back at the single write boundary here."""
    return round(cents / 100.0, 2)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Building-agnostic portal⇄ledger re-chain correction.")
    p.add_argument("--building-id", required=True, help="Building to reconcile (no default).")
    p.add_argument("--year", default=None, help="Portal-snapshot financial year for residual "
                                                 "verification (default: latest available).")
    p.add_argument("--apply", action="store_true", default=False,
                   help="Write corrections. Without this the script is read-only.")
    p.add_argument("--authorised-by", default=None, help="Authoriser identity (required for --apply).")
    p.add_argument("--executed-by", default=None, help="Executor identity (required for --apply; "
                                                       "must differ from --authorised-by).")
    p.add_argument("--tolerance-cents", type=int, default=DEFAULT_TOLERANCE_CENTS)
    p.add_argument("--export", default=None, metavar="PATH", help="Write a JSON report here.")
    return p.parse_args()


async def _get_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    return client[os.getenv("DB_NAME", "strataos_production")]


async def _apply_unit(db, building_id: str, unit_number: str, changes: list[dict[str, Any]],
                      run_id: str, now_iso: str) -> int:
    """Apply one unit's per-year re-chain writes. Optimistic-locked + idempotent. Returns count."""
    applied = 0
    for c in changes:
        year_str_candidates = [str(c["year"])]
        # Idempotency: skip if this run already touched this row.
        already = await db.unit_levy_ledger.find_one(
            {"building_id": building_id, "unit_number": unit_number,
             "year": {"$in": year_str_candidates}, "rechain_run_id": run_id},
            {"_id": 1},
        )
        if already:
            continue
        before, after = c["before"], c["after"]
        result = await db.unit_levy_ledger.update_one(
            {
                "building_id": building_id, "unit_number": unit_number,
                "year": {"$in": year_str_candidates},
                # Optimistic lock on the dollar-stored values we read.
                "admin_opening": _cents_to_dollars(before["admin_opening_cents"]),
                "sinking_opening": _cents_to_dollars(before["sinking_opening_cents"]),
                "net_balance": _cents_to_dollars(before["net_balance_cents"]),
            },
            {"$set": {
                "admin_opening": _cents_to_dollars(after["admin_opening_cents"]),
                "sinking_opening": _cents_to_dollars(after["sinking_opening_cents"]),
                "admin_closing": _cents_to_dollars(after["admin_closing_cents"]),
                "sinking_closing": _cents_to_dollars(after["sinking_closing_cents"]),
                "net_balance": _cents_to_dollars(after["net_balance_cents"]),
                "rechain_run_id": run_id,
                "rechained_at": now_iso,
                # Deliberately NOT "strata_web_portal_scrape" — the correction is derived from the
                # ledger's own prior-year closing, never from the portal.
                "reconciliation_source": "ledger_rechain",
                "before_net_balance": _cents_to_dollars(before["net_balance_cents"]),
            }},
        )
        if result.modified_count:
            applied += 1
    return applied


async def main() -> None:
    args = parse_args()
    dry_run = not args.apply
    building_id = args.building_id
    tol = args.tolerance_cents
    run_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    if not dry_run:
        if not args.authorised_by or not args.executed_by:
            print("ERROR: --apply requires both --authorised-by and --executed-by.")
            sys.exit(2)
        if args.authorised_by == args.executed_by:
            print("ERROR: dual control — --authorised-by and --executed-by must be distinct.")
            sys.exit(2)

    print(f"\n{'='*72}")
    print(f"  Portal ⇄ Ledger re-chain correction")
    print(f"  building_id : {building_id}")
    print(f"  mode        : {'DRY-RUN' if dry_run else '*** APPLY — WILL WRITE unit_levy_ledger ***'}")
    print(f"  run_id      : {run_id}")
    print(f"{'='*72}\n")

    db = await _get_db()

    # Resolve the portal-snapshot year for residual verification.
    year = args.year
    if not year:
        latest = await db.unit_levy_ledger.find_one(
            {"building_id": building_id}, {"_id": 0, "year": 1}, sort=[("year", -1)],
        )
        year = (latest or {}).get("year") or str(datetime.now().year)
    snapshot_date, portal_by_unit = await _latest_portal_by_unit(db, building_id, str(year))

    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}, {"_id": 0},
    ).to_list(None)
    by_unit: dict[str, list[UnitYearLedger]] = {}
    for doc in ledger_docs:
        unit = str(doc.get("unit_number") or "")
        row = UnitYearLedger.from_doc(doc) if unit else None
        if row is not None:
            by_unit.setdefault(unit, []).append(row)

    plans: dict[str, list[dict[str, Any]]] = {}
    snapshot_rows: list[dict[str, Any]] = []
    residuals: list[dict[str, Any]] = []
    for unit, rows in sorted(by_unit.items()):
        changes = compute_rechain_plan(rows, tol)
        if not changes:
            continue
        plans[unit] = changes
        for c in changes:
            snapshot_rows.append({"unit_number": unit, "year": c["year"], **c["before"]})

        # Residual portal verification AFTER the proposed re-chain (report-only). Match by raw
        # lot int, not a display-prefixed token — see portal_ledger_reconciliation_service.
        # _latest_portal_by_unit for the full rationale (found 2026-08-06).
        portal_cents = portal_by_unit.get(extract_lot_int(unit))
        recon = compute_unit_reconciliation(
            unit_number=unit, years=rows, portal_balance_cents=portal_cents,
            as_of_date=snapshot_date, tolerance_cents=tol,
        )
        residuals.append({
            "unit_number": unit,
            "portal_balance_cents": portal_cents,
            "rechained_variance_cents": recon.rechained_variance_cents,
            "paid_gap_class": recon.paid_gap_class,
            "rechain_would_resolve": recon.rechain_would_resolve,
            "suspected_years": recon.suspected_years,
        })

    # ── Report ──
    print(f"  Units with chain breaks to re-chain : {len(plans)}")
    resolved = sum(1 for r in residuals if r["rechain_would_resolve"])
    print(f"  Re-chain alone resolves to portal   : {resolved}")
    print(f"  Still need evidence after re-chain   : {len(plans) - resolved}")
    print(f"  Portal snapshot date                 : {snapshot_date or 'none'}\n")
    for unit in sorted(plans):
        chg = plans[unit]
        res = next((r for r in residuals if r["unit_number"] == unit), {})
        yrs = ", ".join(str(c["year"]) for c in chg)
        rv = res.get("rechained_variance_cents")
        rv_txt = "n/a (no portal)" if rv is None else f"${rv/100:,.2f}"
        flag = "✓ resolves" if res.get("rechain_would_resolve") else f"⚠ residual {rv_txt} [{res.get('paid_gap_class')}]"
        print(f"    {unit:<10} re-chain years {yrs:<20} {flag}")

    applied_total = 0
    if not dry_run and plans:
        snapshot_path = f"/tmp/rechain_snapshot_{run_id}.json"
        with open(snapshot_path, "w") as f:
            json.dump(snapshot_rows, f, indent=2, default=str)
        print(f"\n  Pre-write snapshot: {snapshot_path}")
        confirm = input(f"\n  Type 'yes' to re-chain {len(plans)} units for {building_id}: ").strip().lower()
        if confirm != "yes":
            print("  Aborted."); sys.exit(0)
        for unit, changes in plans.items():
            applied_total += await _apply_unit(db, building_id, unit, changes, run_id, now_iso)
        print(f"  Applied {applied_total} row updates.")

    report = {
        "building_id": building_id, "run_id": run_id, "year": str(year),
        "mode": "dry_run" if dry_run else "applied",
        "run_at": now_iso, "portal_snapshot_date": snapshot_date,
        "authorised_by": args.authorised_by, "executed_by": args.executed_by,
        "units_with_chain_breaks": len(plans),
        "rechain_resolves_count": resolved,
        "rows_applied": applied_total,
        "plans": plans, "residuals": residuals,
        "notes": [
            "Re-chains stored openings forward only; levied/paid/interest untouched.",
            "Portal is verification-only — never a source; residual variance is reported, not forced.",
            "A residual after re-chain is a real paid-gap needing evidence (see suspected_years).",
        ],
    }
    export_path = args.export or f"/tmp/rechain_report_{run_id}.json"
    with open(export_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report: {export_path}")
    if dry_run:
        print("  DRY-RUN: no changes written. Re-run with --apply (+ dual-control) to correct.\n")


if __name__ == "__main__":
    asyncio.run(main())
