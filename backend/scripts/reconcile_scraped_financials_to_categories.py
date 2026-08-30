#!/usr/bin/env python3
# @featuretrace:financial-onboarding — bridges a Strata Sync portal scrape (staged in
#   strata_financials) into levy_categories, the collection the expense-reconstruction
#   generator (and the dashboards) actually read, via the SAME reviewed gap-tolerant CSV
#   import path used for the canonical AGM-PDF rebuild — never a raw overwrite.
# Layer: script
# Data flow: strata_financials (scraped, staged) -> diff against levy_categories (live) ->
#            [human review] -> gap_tolerant_financial_import.import_gap_tolerant_financial_data
#            -> levy_categories (Mongo, upsert) -> read by
#            expense_category_generator.generate_expense_manifest_from_categories via the
#            existing /financials/onboarding/building/{id}/reconstruction-batches/* API and
#            its frontend page, HistoricalReconstructionPage.tsx
#            (/financials/historical-reconstruction). (building-scoped)
# Related: backend/services/onboarding/gap_tolerant_financial_import.py
#          backend/services/reconstruction_generators/expense_category_generator.py
#          backend/routers/strata_sync.py, backend/scripts/run_scraper.py
# Toggle: disable_strata_sync_direct_write (the reason this script exists at all — the
#         scraper deliberately does NOT write levy_categories directly for this building)
# Collection: strata_financials, levy_categories
"""Reconcile a Strata Sync portal scrape against the live category actuals.

Building's Building Financials tab is periodically scraped into `strata_financials`
(staging only — see run_scraper.py/routers/strata_sync.py). The figures dashboards and the
GL-expense-reconstruction pipeline actually read come from `levy_categories`, which is
deliberately NOT written directly by the scraper for any building with
`disable_strata_sync_direct_write` enabled (East Gate, 13195) — per this repo's Financial
Evidence Gateway policy, raw scraped evidence must pass through a human review step before
becoming category "actual" fact.

This script is that review step's tooling, not a replacement for the review itself:

  1. --preview (default): read-only diff, per category, of strata_financials' scraped
     `actual` against levy_categories' currently-stored `actual_amount`. Prints every
     category whose figure would change, unabridged — nothing is summarised away.
  2. --apply: re-runs the same diff (never trusts a stale/cached preview) and, for exactly
     the categories that still differ, feeds a category_name/category_actual_amount row
     through the SAME `import_gap_tolerant_financial_data()` used for the canonical
     AGM-PDF rebuild — an upsert, not a raw $set, tagged with a durable `source_file`
     provenance string identifying this as a portal-scrape-derived correction.

What this script does NOT do: it never touches finance.expense_transactions,
finance.journal_entries, or any GL/ledger table — getting the corrected category actuals
into the GL is the reconstruction-batch pipeline's job (create a batch, generate-preview,
submit-review, then a real human approves/applies via
/financials/historical-reconstruction), not this script's.

Usage (from backend/):
    venv/bin/python3 scripts/reconcile_scraped_financials_to_categories.py --building-id 13195
    venv/bin/python3 scripts/reconcile_scraped_financials_to_categories.py --building-id 13195 \
        --year 2026 --apply --authorised-by <uuid-A> --executed-by <uuid-B>

Dry-run by default. --apply requires --authorised-by and --executed-by as two DISTINCT real
identities (dual control, matching this repo's established pattern for anything that changes
a figure a dashboard displays for real building data) — enforced by this script even though
import_gap_tolerant_financial_data() itself has no dual-control concept of its own. Both
identities must also resolve to a real, active `users` doc with an eligible role
(super_admin/strata_admin/strata_manager, matching _RECONSTRUCTION_UPLOAD_ROLES in
routers/financial_onboarding.py) — added 2026-08-19 so a typo'd or made-up UUID can no
longer satisfy dual control just by being non-empty and different from the other one.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import motor.motor_asyncio  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

from services.onboarding.gap_tolerant_financial_import import (  # noqa: E402
    import_gap_tolerant_financial_data,
)

_FUND_MAP = {"admin": "admin", "capital_works": "sinking", "sinking": "sinking"}
_TOLERANCE = 0.01  # matches this repo's established dollar-float rounding tolerance

# Same role set as _RECONSTRUCTION_UPLOAD_ROLES in routers/financial_onboarding.py — the
# gate already used for "Importing historical financial data" via the same underlying
# import_gap_tolerant_financial_data() this script calls, rather than inventing a new
# role set for this one script.
_ELIGIBLE_IDENTITY_ROLES = {"super_admin", "strata_admin", "strata_manager"}


async def _validate_identity(mdb, user_id: str, label: str) -> str | None:
    """Look up a real `users` doc for a dual-control identity (2026-08-19 hardening —
    --authorised-by/--executed-by previously only had to be non-empty and different from
    each other, with no check that either string was a real user at all).

    Returns None when valid, otherwise a human-readable rejection reason — never raises,
    so run() can report problems with BOTH identities in one pass instead of stopping at
    the first bad one.
    """
    user = await mdb["users"].find_one(
        {"id": user_id}, {"_id": 0, "id": 1, "role": 1, "is_active": 1, "full_name": 1}
    )
    if user is None:
        return f"{label} {user_id!r} does not match any users.id — not a real user."
    if not user.get("is_active", False):
        return f"{label} {user_id!r} ({user.get('full_name', 'unknown')}) is not an active user."
    if user.get("role") not in _ELIGIBLE_IDENTITY_ROLES:
        return (
            f"{label} {user_id!r} ({user.get('full_name', 'unknown')}) has role "
            f"{user.get('role')!r} — must be one of {sorted(_ELIGIBLE_IDENTITY_ROLES)}."
        )
    return None


async def _resolve_scraped_financial_year(mdb, building_id: str) -> str:
    """strata_financials.financial_year uses the scraper's generic July-June FY string
    (e.g. "2026-2027") while levy_categories.year uses a bare calendar-year string (e.g.
    "2026") — the two are NOT the same value for a building whose real accounting year
    doesn't follow July-June (East Gate's own due dates span the calendar year evenly,
    suggesting it doesn't). Rather than guess which bare year a given FY string maps to,
    auto-detect: pick the single most-recently-scraped financial_year value present.
    Raises if more than one exists (ambiguous — pass --scraped-financial-year explicitly)."""
    values = await mdb["strata_financials"].distinct("financial_year", {"building_id": building_id})
    if not values:
        raise SystemExit(f"No strata_financials rows for building {building_id!r} — run a portal sync first.")
    if len(values) > 1:
        raise SystemExit(
            f"Multiple strata_financials.financial_year values found ({sorted(values)}) — "
            "pass --scraped-financial-year explicitly to disambiguate."
        )
    return values[0]


async def _resolve_canonical(mdb, building_id: str, doc: dict, *, max_hops: int = 5) -> dict | None:
    """Follow an archived category's `merged_into` chain to its live canonical doc.

    `consolidate_levy_categories_canonical.py` archives duplicate-spelling category
    docs (`is_archived=True`) and points `merged_into` at the surviving canonical doc's
    `id`. Live East Gate data has never shown more than one hop, but this is bounded
    and cycle-guarded rather than assumed single-hop, since a broken/circular chain
    should return None (unresolvable) rather than loop or raise.

    `building_id` is always included in the lookup filter (never `id` alone) per this
    repo's mandatory multi-tenant rule, and lets the `bid_id_unique` compound index
    (server.py, 2026-08-19) serve the query instead of a collection scan.
    """
    seen_ids: set = set()
    current = doc
    for _ in range(max_hops):
        if not current.get("is_archived"):
            return current
        mi = current.get("merged_into")
        if not mi or mi in seen_ids:
            return None
        seen_ids.add(mi)
        current = await mdb["levy_categories"].find_one(
            {"id": mi, "building_id": building_id}, {"_id": 0}
        )
        if current is None:
            return None
    return None  # exceeded max_hops without resolving — treat as unresolvable


async def _diff(mdb, building_id: str, year: str, scraped_financial_year: str) -> list[dict]:
    """Pure read. Returns one row per scraped category, classified as:

      NEW / UPDATE / UNCHANGED — as before, compared against the live (non-archived)
        levy_categories doc for that name.
      ARCHIVED_CONFLICT — the scraped name has NO live doc, but matches an ARCHIVED
        one that was consolidated into a differently-named canonical category (audit
        finding, 2026-08-19: comparing against a stale archived doc's actual_amount,
        or worse, blindly NEW-inserting under the archived name via --apply, would
        silently undo consolidate_levy_categories_canonical.py's cleanup — confirmed
        live on East Gate for "Fire Protection - Repairs/Replacements" (archived,
        merged into "Fire Protection - Contracted") and "Tax Agent Fees - BAS/GST"
        (archived, merged into "GST Administration"), both of which ALSO independently
        appear as their own scraped category in the same sync). Never eligible for
        --apply — surfaced for a human to decide whether the scraped figure belongs
        folded into the canonical category or is a genuinely distinct new line item.
      DUPLICATE_LIVE_CATEGORY — more than one non-archived levy_categories doc shares
        this name AND fund_type for this year (a genuine DB data-integrity issue) —
        there is no single record --apply could safely target, so it's flagged instead
        of silently picking one (the previous, unaudited version of this function did
        exactly that: last-doc-wins by iteration order, with no warning).

    The grouping key is always (name, fund_type), never name alone — self-audit finding
    (2026-08-19, same day as the ARCHIVED_CONFLICT fix): a first version of this key was
    name-only and flagged East Gate's "Building Repairs & Maintenance" as a
    DUPLICATE_LIVE_CATEGORY, but the two docs sharing that name are legitimately one
    admin-fund doc ($5,149.17) and one sinking-fund doc ($0.00 placeholder) — not a
    duplicate at all. A strata category name can legitimately exist once per fund.

    Only NEW/UPDATE rows are ever eligible for --apply (see `run()`).
    """
    scraped = await mdb["strata_financials"].find(
        {"building_id": building_id, "financial_year": scraped_financial_year}, {"_id": 0}
    ).to_list(500)
    if not scraped:
        raise SystemExit(
            f"No strata_financials rows for building {building_id!r} "
            f"financial_year {scraped_financial_year!r}."
        )

    live_by_key: dict[tuple, dict] = {}
    archived_by_key: dict[tuple, dict] = {}
    duplicate_keys: set = set()
    async for doc in mdb["levy_categories"].find(
        {"building_id": building_id, "year": year}, {"_id": 0}
    ):
        key = (doc["name"].strip().lower(), doc.get("fund_type"))
        if doc.get("is_archived"):
            archived_by_key.setdefault(key, doc)
        elif key in live_by_key:
            duplicate_keys.add(key)
        else:
            live_by_key[key] = doc

    rows = []
    for cat in scraped:
        name = str(cat.get("category") or "").strip()
        if not name:
            continue
        fund_raw = str(cat.get("fund") or "").strip().lower()
        fund_type = _FUND_MAP.get(fund_raw)
        if fund_type is None:
            rows.append({
                "category": name, "fund": fund_raw or "(missing)", "action": "SKIPPED",
                "reason": f"unrecognised fund {fund_raw!r} — expected admin/capital_works",
                "stored_actual": None, "scraped_actual": None, "delta": None,
            })
            continue
        scraped_actual = round(float(cat.get("actual") or 0.0), 2)
        key = (name.lower(), fund_type)

        if key in duplicate_keys:
            rows.append({
                "category": name, "fund": fund_type, "action": "DUPLICATE_LIVE_CATEGORY",
                "reason": (f"{name!r} ({fund_type}) has more than one non-archived "
                           f"levy_categories doc for {year} — cannot safely target a single "
                           "record. Resolve the duplicate in the DB first (see "
                           "consolidate_levy_categories_canonical.py)."),
                "stored_actual": None, "scraped_actual": scraped_actual, "delta": None,
            })
            continue

        live_doc = live_by_key.get(key)
        if live_doc is None and key in archived_by_key:
            canonical = await _resolve_canonical(mdb, building_id, archived_by_key[key])
            if canonical is not None and canonical.get("id") != archived_by_key[key].get("id"):
                canonical_actual = round(float(canonical.get("actual_amount") or 0.0), 2)
                rows.append({
                    "category": name, "fund": fund_type, "action": "ARCHIVED_CONFLICT",
                    "reason": (f"{name!r} was archived and consolidated into canonical category "
                               f"{canonical['name']!r} (currently ${canonical_actual:,.2f}) — a human "
                               "must decide whether this scraped figure belongs folded into the "
                               "canonical category or is a genuinely distinct new line item."),
                    "stored_actual": None, "scraped_actual": scraped_actual, "delta": None,
                })
                continue
            # Unresolvable chain (broken/circular merged_into) — falls through to NEW
            # below as a safe default; still worth a human's attention, but there is no
            # canonical doc to point at, so treat like any other genuinely-new category.

        stored_actual = round(float(live_doc.get("actual_amount") or 0.0), 2) if live_doc else None
        delta = None if stored_actual is None else round(scraped_actual - stored_actual, 2)
        if live_doc is None:
            action = "NEW"
        elif delta is not None and abs(delta) > _TOLERANCE:
            action = "UPDATE"
        else:
            action = "UNCHANGED"
        rows.append({
            "category": name, "fund": fund_type, "action": action,
            "reason": None, "stored_actual": stored_actual,
            "scraped_actual": scraped_actual, "delta": delta,
        })
    return rows


def _print_diff(rows: list[dict], year: str) -> None:
    changed = [r for r in rows if r["action"] in ("NEW", "UPDATE")]
    unchanged = [r for r in rows if r["action"] == "UNCHANGED"]
    skipped = [r for r in rows if r["action"] == "SKIPPED"]
    flagged = [r for r in rows if r["action"] in ("ARCHIVED_CONFLICT", "DUPLICATE_LIVE_CATEGORY")]

    print(f"Year {year}: {len(rows)} scraped categories — "
          f"{len(changed)} would change, {len(unchanged)} unchanged, {len(skipped)} skipped, "
          f"{len(flagged)} flagged for human review (never auto-applied).\n")

    if changed:
        print(f"{'Category':<42} {'Fund':<9} {'Stored':>12} {'Scraped':>12} {'Delta':>10}  Action")
        print("-" * 100)
        for r in sorted(changed, key=lambda r: -abs(r["delta"] or r["scraped_actual"] or 0)):
            stored = "—" if r["stored_actual"] is None else f"${r['stored_actual']:,.2f}"
            scraped = f"${r['scraped_actual']:,.2f}"
            delta = "(new)" if r["delta"] is None else f"${r['delta']:,.2f}"
            print(f"{r['category']:<42} {r['fund']:<9} {stored:>12} {scraped:>12} {delta:>10}  {r['action']}")
        total_delta = sum(r["delta"] or r["scraped_actual"] for r in changed)
        print("-" * 100)
        print(f"{'TOTAL DELTA':<42} {'':<9} {'':>12} {'':>12} ${total_delta:>9,.2f}")

    if flagged:
        print(f"\nFlagged for human review — NOT eligible for --apply "
              f"({len(flagged)} categor{'y' if len(flagged) == 1 else 'ies'}):")
        for r in flagged:
            print(f"  [{r['action']}] {r['category']} (scraped actual ${r['scraped_actual']:,.2f}): {r['reason']}")

    if skipped:
        print("\nSkipped (not applied either way):")
        for r in skipped:
            print(f"  {r['category']}: {r['reason']}")


async def run(
    building_id: str, year: str, apply: bool, authorised_by: str | None, executed_by: str | None,
    scraped_financial_year: str | None = None,
) -> int:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ.get("DB_NAME", "strata_production")
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    mdb = client[db_name]

    resolved_fy = scraped_financial_year or await _resolve_scraped_financial_year(mdb, building_id)
    print(f"Comparing strata_financials[financial_year={resolved_fy!r}] against "
          f"levy_categories[year={year!r}].\n")

    rows = await _diff(mdb, building_id, year, resolved_fy)
    _print_diff(rows, year)

    changed = [r for r in rows if r["action"] in ("NEW", "UPDATE")]
    if not changed:
        print("\nNothing to apply.")
        return 0

    if not apply:
        print("\nPreview only — nothing written. Re-run with --apply --authorised-by <uuid> "
              "--executed-by <uuid> to write these changes.")
        return 0

    if not authorised_by or not executed_by:
        print("\n--apply requires --authorised-by and --executed-by.", file=sys.stderr)
        return 2
    if authorised_by == executed_by:
        print("\n--authorised-by and --executed-by must be different identities (dual control).",
              file=sys.stderr)
        return 2

    identity_errors = [
        err for err in (
            await _validate_identity(mdb, authorised_by, "--authorised-by"),
            await _validate_identity(mdb, executed_by, "--executed-by"),
        ) if err
    ]
    if identity_errors:
        for err in identity_errors:
            print(f"\n{err}", file=sys.stderr)
        return 2

    today = datetime.now(timezone.utc).date().isoformat()
    source_file = f"strata_web_portal_scrape_{today}_authorised_by_{authorised_by}"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["year", "fund_type", "category_name", "category_actual_amount", "source_file"])
    for r in changed:
        writer.writerow([year, r["fund"], r["category"], r["scraped_actual"], source_file])
    content = buf.getvalue().encode("utf-8")

    result = await import_gap_tolerant_financial_data(
        mdb, building_id=building_id, content=content,
        filename=f"scrape_reconciliation_{today}.csv",
        uploaded_by=f"reconcile_scraped_financials_to_categories:{executed_by}",
        is_test_data=False,
    )
    print(f"\nApplied. {len(changed)} categor{'y' if len(changed) == 1 else 'ies'} updated in levy_categories.")
    print(f"Import result: {result}")
    print(
        "\nNOT posted to the GL. Next step: create a reconstruction batch (year "
        f"{year}) and take it through preview -> submit-review -> approve -> apply via "
        "/financials/historical-reconstruction."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--year", default=None, help="Financial year, e.g. 2026. Defaults to the current calendar year.")
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is preview-only.")
    parser.add_argument("--authorised-by", default=None, help="UUID of the person who reviewed and authorised this update.")
    parser.add_argument("--executed-by", default=None, help="UUID of the person running --apply. Must differ from --authorised-by.")
    parser.add_argument(
        "--scraped-financial-year", default=None,
        help="strata_financials.financial_year value to compare (e.g. '2026-2027'). "
             "Auto-detected when only one value exists for the building.",
    )
    args = parser.parse_args()

    year = args.year or str(datetime.now(timezone.utc).year)
    return asyncio.run(run(
        args.building_id, year, args.apply, args.authorised_by, args.executed_by,
        scraped_financial_year=args.scraped_financial_year,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
