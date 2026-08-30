#!/usr/bin/env python3
# @featuretrace:strata-web-portal-finance-ingest — Post-scrape orchestration: stage snapshot -> infer deltas.
# Layer: script
# Data flow: (scraper already ran) -> strata_web_portal_ingest.run() -> staging_strata_web_snapshots
#            -> derive_strata_web_balance_delta_transactions() -> demo_bank_transactions
#            (source_type=strata_web_inferred_payment, requires_review=True) -> match/review -> GL.
# Related: backend/scripts/run_scraper.py                      (the scrape itself)
#          backend/scripts/ingest/strata_web_portal_ingest.py  (snapshot staging)
#          backend/services/strata_web_balance_inference_service.py (delta -> candidates)
#          backend/routers/demo_bank.py                        (POST /demo-bank/strata-web/infer-candidates)
# Toggle: demo_bank_feed_enabled
"""Run the two steps that must follow a portal scrape, and report whether they worked.

Why this exists
---------------
A scrape on its own changes nothing downstream. Three separate things have to happen,
and only the first is automatic:

  1. scrape            -> ``strata_owners`` / ``bank_accounts`` / ``building_summaries``
  2. stage a snapshot  -> ``staging_strata_web_snapshots``     <- NOT done by run_scraper.py
  3. infer deltas      -> ``demo_bank_transactions`` candidates <- manual, "not wired
                          into any scheduler" per its own endpoint docstring

``run_scraper.py`` performs step 1 only. Step 2 happens solely when the scrape is
triggered through ``POST /settings/strata-web-portal/sync``; a standalone scraper run
skips it. Step 3 is always manual. So it is entirely possible to run a scrape, see new
data in Mongo, and have **zero** payment candidates reach Demo Bank — with nothing
failing loudly to say so.

This script performs steps 2 and 3 together and reports the outcome explicitly,
including the two silent-no-op cases:

  * the snapshot staged under a financial-year label that does not pair with the
    previous one (now handled — pairing normalises the label, see
    ``utils.finance_helpers.normalise_financial_year``), and
  * only ONE snapshot existing for the year, which cannot produce a delta at all.

Catch-up is the default
-----------------------
The inference service compares the most recent snapshot against the one immediately
before it, and has no notion of "windows I have not processed yet". A missed run
therefore loses that window's movement **silently** — no error, no backlog, and the
next run reports success because its own window is fine.

East Gate lost 21 lots' movement worth $15,566.04 that way. So this walks EVERY
consecutive pair for the year, oldest-first, by default. ``_upsert_transaction`` is
idempotent, so re-processing an already-inferred window creates nothing new; the cost
of catching up is a few extra queries and the cost of not catching up is stranded
money. ``--latest-only`` restores single-window behaviour.

What it does NOT do
-------------------
It never writes to ``unit_levy_ledger``, ``levy_payments`` or any ``finance.*`` table.
Step 3 produces **candidates** (``requires_review=True``) that still have to pass
through matching, review and promotion like any other input — a portal balance is
reconciliation evidence, never a journal source.

Usage (from repo root):
    backend/venv/bin/python3 backend/scripts/ingest/strata_web_post_scrape_pipeline.py \
        --building-id 13195 --financial-year 2026                 # dry-run
    backend/venv/bin/python3 backend/scripts/ingest/strata_web_post_scrape_pipeline.py \
        --building-id 13195 --financial-year 2026 --apply
    ... --latest-only          # only the most recent pair (not recommended)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")


async def _snapshot_inventory(db, building_id: str, target_fy: str) -> dict:
    from utils.finance_helpers import normalise_financial_year

    rows = await db.staging_strata_web_snapshots.find(
        {"building_id": building_id}, {"_id": 0, "financial_year": 1, "snapshot_date": 1},
    ).to_list(None)
    for r in rows:
        r["normalised_year"] = normalise_financial_year(r.get("financial_year"))
    rows.sort(key=lambda r: str(r.get("snapshot_date") or ""))
    for_year = [r for r in rows if r["normalised_year"] == target_fy]
    return {"all": rows, "for_target_year": for_year, "count_for_year": len(for_year)}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--financial-year", required=True,
                    help="Any label form — '2026', '2025-2026', 'FY2026' all normalise.")
    ap.add_argument("--snapshot-date", default=None, help="Defaults to today.")
    ap.add_argument("--apply", action="store_true", help="Write. Default: dry-run.")
    ap.add_argument("--skip-staging", action="store_true",
                    help="Snapshot already staged (e.g. via the sync endpoint) — infer only.")
    # DEFAULT ON (2026-08-28, operator decision). Processing only the most recent pair
    # is what stranded 21 lots and $15,566.04 of payments in an unprocessed window, with
    # no error raised and the next run reporting success. Catching up is the safe
    # behaviour and re-processing is free — _upsert_transaction is idempotent — so the
    # burden of opting out belongs on the rare caller who wants a single window.
    ap.add_argument("--latest-only", action="store_true",
                    help="Infer ONLY the most recent snapshot pair. Default is to walk every "
                         "consecutive pair for the year oldest-first, so a window missed by an "
                         "earlier run is picked up rather than stranded.")
    args = ap.parse_args()

    from request_context import set_ctx_building_id
    from utils.finance_helpers import normalise_financial_year

    set_ctx_building_id(args.building_id)
    from database import db

    target_fy = normalise_financial_year(args.financial_year)
    report: dict = {
        "building_id": args.building_id,
        "financial_year_given": args.financial_year,
        "financial_year_normalised": target_fy,
        "mode": "APPLY" if args.apply else "DRY-RUN",
    }

    # Raw AsyncDatabase for our own reads (we filter building_id explicitly) — the same
    # convention strata_web_portal_ingest.py uses. The WRAPPER goes to the service.
    before = await _snapshot_inventory(db._db, args.building_id, target_fy)
    report["snapshots_before"] = before["for_target_year"]

    # ---- Step 2: stage the snapshot -------------------------------------------------
    if args.skip_staging:
        report["staging"] = {"skipped": True}
    else:
        from scripts.ingest import strata_web_portal_ingest

        try:
            staged = await strata_web_portal_ingest.run(
                building_id=args.building_id,
                financial_year=args.financial_year,
                snapshot_date=args.snapshot_date,
                dry_run=not args.apply,
            )
            report["staging"] = {
                k: v for k, v in staged.items() if k != "snapshot"
            }
            # `run()` returns DIFFERENT shapes per mode: the apply path returns
            # `per_unit_count` (an int) and NO `snapshot`; the dry-run path returns
            # `snapshot` and no count. Reading only `snapshot` reported 0 units on a
            # successful 87-unit apply (observed live 2026-08-28). Handle both.
            if "per_unit_count" in staged:
                report["staging"]["per_unit_balance_count"] = int(staged["per_unit_count"])
            else:
                snap = staged.get("snapshot") or {}
                report["staging"]["per_unit_balance_count"] = len(
                    snap.get("per_unit_balances") or []
                )
        except Exception as exc:
            report["staging"] = {"error": repr(exc)}

    after = await _snapshot_inventory(db._db, args.building_id, target_fy)
    report["snapshots_after"] = after["for_target_year"]

    # ---- Step 3: infer the deltas ---------------------------------------------------
    if after["count_for_year"] < 2:
        report["inference"] = {
            "skipped": True,
            "reason": (
                f"a balance delta needs TWO snapshots of FY{target_fy}; this building has "
                f"{after['count_for_year']}. Nothing to compare — this is the silent no-op "
                f"case, reported explicitly rather than returning zero candidates."
            ),
        }
    elif not args.apply:
        # List EVERY window the run would process, not just the last pair — with
        # catch-up as the default, showing only the newest pair understates the work
        # and hides exactly the missed-window case this mode exists to catch.
        pairs = [
            f"{older['snapshot_date']} -> {newer['snapshot_date']}"
            for older, newer in zip(after["for_target_year"], after["for_target_year"][1:])
        ]
        report["inference"] = {
            "skipped": True,
            "reason": "dry-run — candidate generation writes to demo_bank_transactions.",
            "mode": "latest-only" if args.latest_only else "catch-up (default)",
            "would_process": pairs[-1:] if args.latest_only else pairs,
        }
    elif not args.latest_only:
        # WHY THIS MODE EXISTS (2026-08-28).
        # derive_strata_web_balance_delta_transactions compares the most recent snapshot
        # against the one immediately before it. It has no concept of "windows I have not
        # processed yet", so if a run is missed, the movement in that window is never
        # inferred and is lost silently — there is no error and no backlog to notice.
        #
        # East Gate hit this: the 2026-08-06 -> 2026-08-19 window was never processed, so
        # 21 lots' movement totalling $15,566.04 of payments never became candidates. The
        # subsequent 08-19 -> 08-28 run reported success because ITS window was fine.
        #
        # This walks every consecutive pair for the year oldest-first, so a skipped window
        # is picked up on the next run rather than being stranded. _upsert_transaction is
        # idempotent, so re-processing an already-inferred window creates nothing new.
        from services.strata_web_balance_inference_service import (
            derive_strata_web_balance_delta_transactions,
        )

        windows = []
        for older, newer in zip(after["for_target_year"], after["for_target_year"][1:]):
            windows.append((older["snapshot_date"], newer["snapshot_date"]))

        results = []
        for older_date, newer_date in windows:
            doc = await db._db.staging_strata_web_snapshots.find_one(
                {"building_id": args.building_id, "snapshot_date": newer_date}
            )
            if not doc:
                results.append({"window": f"{older_date} -> {newer_date}", "error": "snapshot not found"})
                continue
            try:
                out = await derive_strata_web_balance_delta_transactions(
                    db=db, building_id=args.building_id, financial_year=target_fy,
                    current_snapshot_id=str(doc["_id"]),
                )
                out["window"] = f"{older_date} -> {newer_date}"
                results.append(out)
            except Exception as exc:
                results.append({"window": f"{older_date} -> {newer_date}", "error": repr(exc)})
        report["inference"] = {
            "mode": "catch-up",
            "windows_processed": len(results),
            "candidates_created": sum(int(r.get("candidates_created") or 0) for r in results),
            "candidates_skipped": sum(int(r.get("candidates_skipped") or 0) for r in results),
            "per_window": results,
        }
    else:
        from services.strata_web_balance_inference_service import (
            derive_strata_web_balance_delta_transactions,
        )

        try:
            # Pass the TenantScopedDatabase WRAPPER, never the raw AsyncDatabase.
            # `integrations/demo_bank/ingestion.py::_upsert_transaction` reaches through
            # `db._db.demo_bank_transactions`, so handing it `db._db` raises
            # AttributeError("AsyncDatabase has no attribute '_db'"). This matches how
            # routers/demo_bank.py calls the same function (`db=db`). Verified live
            # 2026-08-28 — the first --apply run failed here for exactly this reason.
            report["inference"] = await derive_strata_web_balance_delta_transactions(
                db=db, building_id=args.building_id, financial_year=target_fy,
            )
        except Exception as exc:
            report["inference"] = {"error": repr(exc)}

    print(json.dumps(report, indent=2, default=str))

    print(f"\n{'=' * 84}")
    print(f"Post-scrape pipeline — building {args.building_id}, FY{target_fy}  [{report['mode']}]")
    print("=" * 84)
    print("  snapshots for this year:")
    for s in after["for_target_year"]:
        print(f"    {s['snapshot_date']:12}  label={str(s.get('financial_year')):12}")
    if not after["for_target_year"]:
        print("    (none)")
    other = [s for s in after["all"] if s["normalised_year"] != target_fy]
    if other:
        print("  other years present:")
        for s in other:
            print(f"    {s['snapshot_date']:12}  label={str(s.get('financial_year')):12}"
                  f"  -> FY{s['normalised_year']}")
    inf = report.get("inference", {})
    if inf.get("skipped"):
        print(f"\n  inference SKIPPED: {inf['reason']}")
        if inf.get("would_process"):
            print(f"    mode: {inf.get('mode')}")
            for w in inf["would_process"]:
                print(f"    would process: {w}")
    elif "error" in inf:
        print(f"\n  inference FAILED: {inf['error']}")
    else:
        print(f"\n  candidates created: {inf.get('candidates_created')}   "
              f"skipped: {inf.get('candidates_skipped')}")
        for w in inf.get("per_window") or []:
            print(f"    {w.get('window')}: created={w.get('candidates_created')} "
                  f"skipped={w.get('candidates_skipped')}"
                  + (f"  ERROR={w['error']}" if w.get("error") else ""))
        for w in inf.get("warnings") or []:
            print(f"    warning: {w}")
        print("\n  Candidates are requires_review=True in demo_bank_transactions.")
        print("  They reach the ledger only via matching -> review -> promotion.")
    if not args.apply:
        print("\n  Dry-run. Re-run with --apply to stage and infer.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
