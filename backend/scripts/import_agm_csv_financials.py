"""Import a building's historical financials from a verified AGM-source CSV
into Mongo `annual_levies`/`levy_categories`/`sinking_fund_plan`/
`expense_transactions`, treating the CSV as sole ground truth.

BACKGROUND
══════════
Per explicit user direction (2026-07-25): MongoDB predates the Civium portal
connection for East Gate and may hold synthetic/incomplete/wrong historical
data. `docs/finances/up13195_agm_proposed_spent_financials.csv` (hand-
verified against real AGM minutes/papers, Dec 2020 – Mar 2026) is the sole
ground truth. This script corrects Mongo to match it, and never treats
Mongo's current value as evidence of anything.

WHY CATEGORY-LEVEL ROWS ARE PARSED FROM THE CSV AT RUNTIME, NOT HARDCODED
──────────────────────────────────────────────────────────────────────────
Verified live: every year's `Actual Category` rows sum EXACTLY to that
year's confirmed `Actual Fund Total` (LY2022 Admin $216,901.65, Sinking
$24,219.61; LY2023 $169,266.08 / $31,340.78; LY2024 $311,666.80 / $1,618.18
— all exact). This is strong evidence the CSV's category-level data is
internally consistent, so it's parsed directly rather than hand-transcribed
(hand-transcribing hundreds of rows risks silent transcription errors a
generic parser doesn't).

WHY FUND-LEVEL TOTALS ARE AN EXPLICIT, HARDCODED, CSV-CITED TABLE
──────────────────────────────────────────────────────────────────
The CSV frequently gives *multiple*, differently-labeled candidate figures
for the same (year, fund) — e.g. LY2021 Admin actual has 4 variants (cash
basis at two different dates, accrual, "unreconciled"); some years' figures
are explicitly dual-labeled ex-GST/incl-GST; some proposed-fund-total rows
represent a full-year adopted budget while the "actual" is a partial-year
figure. Picking the right one requires human judgment against the CSV's own
notes column — the same judgment already applied and *proven correct* for
FY2021/2022 in `scripts/finances/update_2021_2022_levy_data.py`. This script
extends that same judgment to FY2023–2026, explicitly, with a citation per
entry — it does not attempt to auto-derive "the" figure from the CSV's
multiple labeled variants (that would be a plausible-looking heuristic
papering over a decision that should stay visible and reviewable).

This is designed to be REUSABLE for onboarding a new building's historical
financials from the same CSV format: the category-parsing/cascade/write
logic is fully building-agnostic (`--building-id`); only the `FUND_TOTALS`
correction table below is East-Gate-specific, and its shape is the template
a future building's onboarding would fill in after doing the same review.

SCOPE
═════
- `annual_levies` / `levy_categories`: FY2022–2026 (FY2021 already correct
  per `update_2021_2022_levy_data.py`, left untouched).
- `sinking_fund_plan`: FY2021–2026 actual years only (`is_actual: true`).
  FY2027+ forecast/projection years are NEVER touched.
- Mongo `expense_transactions` (the granular, per-category collection —
  different from Postgres's table of the same name): backfills FY2022–2024
  only, from CSV `Actual Category` rows. FY2025/2026 already have real
  per-invoice Civium-scraped data in this collection and are NEVER
  overwritten by this script.

USAGE
═════
    cd backend
    venv/bin/python3 scripts/import_agm_csv_financials.py                # dry-run (default)
    venv/bin/python3 scripts/import_agm_csv_financials.py --apply        # writes

Requires MONGO_URL, DB_NAME in backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.category_taxonomy import canonical_category  # noqa: E402

BUILDING_ID = "13195"
CSV_PATH = Path(__file__).resolve().parents[2] / "docs" / "finances" / "up13195_agm_proposed_spent_financials.csv"
NOW = datetime.now(timezone.utc).isoformat()

FUND_KEY = {"Administrative": "admin_fund", "Sinking": "sinking_fund"}
CATEGORY_FUND_TYPE = {"Administrative": "administrative", "Sinking": "sinking"}

# ─────────────────────────────────────────────────────────────────────────
# Explicit, CSV-cited fund-level corrections. Only fields listed here are
# changed — every other field on the existing annual_levies doc (e.g.
# other_income, which the CSV doesn't give a clean per-year breakdown for)
# is preserved as-is. Years/funds not listed here are left completely
# untouched.
# ─────────────────────────────────────────────────────────────────────────
FUND_TOTALS: dict[str, dict[str, dict[str, float]]] = {
    "2023": {
        # Admin levy_income ($165,986.79) is NOT touched: the CSV's only
        # LY2023 "Proposed Fund Total" ($221,316, "adopted") is a full-year
        # *proposed* budget, not a contradicting *actual* figure — no CSV
        # row states an actual 2023 admin levy income to compare against.
        "sinking": {
            # CSV: "TOTAL Sinking Fund Income/Expenditure (adopted) = 60958"
            "levy_income": 60958.00,
            # CSV: "TOTAL Sinking Fund Expenses (Civium period only) = 31340.78"
            # (was 8447.00 — matches the category-level sum exactly)
            "total_expenses": 31340.78,
        },
    },
    "2024": {
        "sinking": {
            # CSV: "TOTAL Sinking Fund (ex-GST) / incl. GST = 70795"
            "levy_income": 70795.00,
            # CSV: "TOTAL Sinking Fund Expenses = 1618.18" (was 9815.00 —
            # matches the category-level sum exactly)
            "total_expenses": 1618.18,
        },
    },
    "2025": {
        "admin": {
            # CSV: "TOTAL Administrative Fund (ex-GST) / incl. GST = 262076"
            # (was 317948.97)
            "levy_income": 262076.00,
        },
        "sinking": {
            # CSV: "TOTAL Sinking Fund income (ex-GST) / expense budget = 99262"
            # (was 80622.00) — the CSV's own note distinguishes income raised
            # ($99,262) from the much smaller expense budget ($9,815) for
            # this year specifically ("fund deliberately building reserves
            # from LY2025 onward") — both stored, on different fields.
            "levy_income": 99262.00,
            "proposed_expenses": 9815.00,
            # CSV: "TOTAL Sinking Fund Expenses = 48039.69" (was 14950.00)
            "total_expenses": 48039.69,
        },
    },
    "2026": {
        "admin": {
            # CSV: "YTD Administrative Fund Expenses (to 8 Apr 2026) = 75592.65"
            # (was 145628.22 — that figure doesn't match this building's own
            # granular Mongo expense_transactions collection, which sums to
            # $75,548.55 for 2026 admin, corroborating the CSV within $44.10)
            "total_expenses": 75592.65,
        },
        "sinking": {
            "proposed_expenses": 73644.00,
            # CSV: "YTD Sinking Fund Expenses (to 8 Apr 2026) = 70060" —
            # exactly matches this building's own expense_transactions sum
            # ($70,060.00 exactly) — high confidence correction.
            "total_expenses": 70060.00,
        },
    },
}

# Years to check for missing actual-category data (levy_categories +
# Mongo expense_transactions). Only years/funds whose existing actual_amount
# sum is genuinely zero get new docs added — see the add-only logic below.
CATEGORY_YEARS = ("2022", "2023", "2024")


def _load_csv_rows() -> list[dict]:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _actual_rows(rows: list[dict], year: str, fund: str) -> list[tuple[str, float]]:
    return [
        (r["category"], float(r["amount_aud"]))
        for r in rows
        if r["levy_year_covered"] == f"LY{year}" and r["fund"] == fund
           and r["record_type"] == "Actual Category" and _is_float(r["amount_aud"])
    ]


def _is_float(v) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _recompute_cascade(docs_by_year: dict[str, dict], existing_by_year: dict[str, dict]) -> None:
    """Recompute total_income/closing_balance/surplus_deficit for both funds.

    opening_balance is cascaded forward from the prior year's *new*
    closing_balance ONLY across boundaries that were already anchored in the
    pre-correction data (existing opening[y] == existing closing[y-1]).
    Verified live: 2023->2024->2025->2026 are anchored for both funds
    (period_notes confirm these as real audited/handover anchors), but
    2022->2023 is NOT (existing opening_balance $49,718.78 != existing
    closing_balance $20,631.29 for admin; same gap for sinking) — the
    period_note explains why: "Jan-Apr 2023 (under previous manager) is not
    separately recorded." Blindly cascading through that gap would silently
    erase a real, documented, source-cited discontinuity. Where a boundary
    isn't anchored, this year's existing opening_balance is preserved
    unchanged rather than forced to match the prior year's closing.
    """
    for fund_key in ("admin_fund", "sinking_fund"):
        prior_new_close = None
        for year in sorted(docs_by_year.keys()):
            doc = docs_by_year[year]
            fund = doc[fund_key]
            existing_fund = existing_by_year[year][fund_key]
            existing_opening = round(float(existing_fund.get("opening_balance", 0) or 0), 2)

            if prior_new_close is not None:
                prior_year = str(int(year) - 1)
                prior_existing_close = round(float(existing_by_year[prior_year][fund_key].get("closing_balance", 0) or 0), 2)
                anchored = abs(prior_existing_close - existing_opening) < 0.01
                fund["opening_balance"] = prior_new_close if anchored else existing_opening
            else:
                fund["opening_balance"] = existing_opening

            fund["total_income"] = round(fund.get("levy_income", 0.0) + fund.get("other_income", 0.0), 2)
            fund["closing_balance"] = round(
                fund["opening_balance"] + fund["total_income"] - fund.get("total_expenses", 0.0), 2
            )
            fund["surplus_deficit"] = round(fund["total_income"] - fund.get("total_expenses", 0.0), 2)
            prior_new_close = fund["closing_balance"]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the corrections (default: dry-run, prints diff only).")
    args = parser.parse_args()

    import motor.motor_asyncio
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    csv_rows = _load_csv_rows()

    client = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "strata_production")]

    try:
        existing = {
            d["year"]: d
            async for d in db.annual_levies.find({"plan_id": BUILDING_ID})
        }
        for y in ("2021", "2022", "2023", "2024", "2025", "2026"):
            if y not in existing:
                print(f"ABORT — no existing annual_levies doc for year {y}; this script only corrects, never creates from scratch.")
                return

        # ── 1. Build corrected annual_levies docs ────────────────────────
        working = {y: dict(existing[y]) for y in existing}
        for y, doc in working.items():
            doc["admin_fund"] = dict(doc["admin_fund"])
            doc["sinking_fund"] = dict(doc["sinking_fund"])

        for year, funds in FUND_TOTALS.items():
            for fund_key, overrides in funds.items():
                target = working[year]["admin_fund" if fund_key == "admin" else "sinking_fund"]
                for field, value in overrides.items():
                    target[field] = value

        _recompute_cascade(working, existing)

        print("=" * 100)
        print("annual_levies plan — only fields that actually change are shown")
        print("=" * 100)
        any_change = False
        for year in sorted(working.keys()):
            for fund_label, fund_key in (("Admin", "admin_fund"), ("Sinking", "sinking_fund")):
                old = existing[year][fund_key]
                new = working[year][fund_key]
                diffs = {
                    k: (old.get(k), new[k])
                    for k in ("levy_income", "other_income", "total_income", "total_expenses",
                              "opening_balance", "closing_balance", "surplus_deficit", "proposed_expenses")
                    if k in new and round(float(old.get(k, 0) or 0), 2) != round(float(new.get(k, 0) or 0), 2)
                }
                if diffs:
                    any_change = True
                    print(f"\n  FY{year} {fund_label}:")
                    for k, (o, n) in diffs.items():
                        print(f"    {k}: {o} -> {n}")

        # ── 2. Category-level plan — ADD-ONLY, never replaces existing data ──
        # Checked live: levy_categories' *existing* actual_amount sums already
        # exactly match the CSV for FY2023 (both funds) and FY2024 Admin — only
        # FY2022 (both funds) and FY2024 Sinking have actual_amount=0 across
        # every category (genuinely never populated). Only those get new
        # actual-only category docs (budgeted_amount=0, clearly described) —
        # existing proposed-budget category docs are NEVER touched or deleted,
        # avoiding a detail-loss regression (e.g. FY2022 Admin already has 43
        # proposed categories; the CSV's own "pre-AGM draft" proposed rows for
        # that year are a much coarser 10-row summary, not a superset).
        category_plan: dict[tuple[str, str], list[tuple[str, float]]] = {}
        for year in CATEGORY_YEARS:
            for fund_label, fund_type in (("Administrative", "administrative"), ("Sinking", "sinking")):
                existing_cats = await db.levy_categories.find(
                    {"plan_id": BUILDING_ID, "year": year, "fund_type": fund_type}
                ).to_list(None)
                existing_actual_sum = round(sum(float(c.get("actual_amount") or 0) for c in existing_cats), 2)
                actual = _actual_rows(csv_rows, year, fund_label)
                csv_actual_sum = round(sum(a for _, a in actual), 2)
                if not actual or abs(existing_actual_sum - csv_actual_sum) < 0.01:
                    continue  # already correct or CSV has nothing for this year/fund
                if existing_actual_sum != 0:
                    print(
                        f"  WARNING FY{year} {fund_label}: existing actual_amount sum "
                        f"(${existing_actual_sum:,.2f}) is nonzero but doesn't match CSV "
                        f"(${csv_actual_sum:,.2f}) — skipping rather than guessing which "
                        f"existing docs to correct (needs a name-matched review, not an add)."
                    )
                    continue
                category_plan[(year, fund_label)] = actual

        print(f"\n{'=' * 100}\nlevy_categories ADD-ONLY plan (actual-only docs; existing proposed docs untouched)\n{'=' * 100}")
        for (year, fund_label), actual in sorted(category_plan.items()):
            actual_total = sum(a for _, a in actual)
            print(f"  FY{year} {fund_label}: +{len(actual)} actual-only categories (${actual_total:,.2f})")

        if not any_change and not category_plan:
            print("\nNothing to do — already matches the CSV.")
            return

        if not args.apply:
            print("\nDry-run only — no writes made. Re-run with --apply to write these changes.")
            return

        # ── 3. Apply annual_levies ────────────────────────────────────────
        for year, doc in working.items():
            doc["updated_at"] = NOW
            await db.annual_levies.replace_one({"plan_id": BUILDING_ID, "year": year}, doc)

        # ── 4. Apply sinking_fund_plan (actual years only) ───────────────
        for year, doc in working.items():
            sf = doc["sinking_fund"]
            await db.sinking_fund_plan.update_one(
                {"plan_id": BUILDING_ID, "year": int(year), "is_actual": True},
                {"$set": {
                    "contribution": sf["levy_income"],
                    "expenditure": sf["total_expenses"],
                    "actual_expenditure": sf["total_expenses"],
                    "opening_balance": sf["opening_balance"],
                    "closing_balance": sf["closing_balance"],
                    "net_position": sf["surplus_deficit"],
                    "updated_at": NOW,
                }},
            )

        # ── 5. Apply levy_categories — ADD-ONLY (existing docs never touched) ─
        for (year, fund_label), actual in category_plan.items():
            fund_type = CATEGORY_FUND_TYPE[fund_label]
            # Idempotent re-run guard: remove only this script's own prior
            # additions for this year/fund before re-adding, never anything
            # pre-existing (matches the data_source tag set below).
            await db.levy_categories.delete_many({
                "plan_id": BUILDING_ID, "year": year, "fund_type": fund_type,
                "data_source": "agm_csv_import",
            })
            docs = [{
                "id": str(uuid.uuid4()), "plan_id": BUILDING_ID, "year": year, "status": "actual",
                "fund_type": fund_type, "name": name, "budgeted_amount": 0.0,
                "actual_amount": amount,
                # Canonical identity so this add-only actual can later be consolidated with the
                # proposed-budget doc for the same economic category (utils/category_taxonomy.py).
                "canonical_key": canonical_category(name, fund_type).key,
                "canonical_name": canonical_category(name, fund_type).display_name,
                "description": "Actual spend from verified AGM source document (not a corresponding "
                                "proposed-budget line item in this collection).",
                "data_source": "agm_csv_import", "created_at": NOW, "updated_at": NOW,
            } for name, amount in actual]
            if docs:
                await db.levy_categories.insert_many(docs)

        # ── 6. Apply Mongo expense_transactions (FY2022/FY2024 sinking only —
        # wherever category_plan added new actual data) ──────────────────
        for (year, fund_label), actual in category_plan.items():
            fund_type_short = "admin" if fund_label == "Administrative" else "sinking"
            await db.expense_transactions.delete_many({
                "building_id": BUILDING_ID, "financial_year": year,
                "fund_type_short": fund_type_short, "data_source": "agm_csv_import",
            })
            docs = [{
                "id": str(uuid.uuid4()), "building_id": BUILDING_ID, "plan_id": BUILDING_ID,
                "financial_year": year, "date": f"{year}-12-31", "category_id": name,
                "category_name": name, "fund_type": f"{fund_label} Fund", "fund_type_short": fund_type_short,
                "amount": amount, "gst_amount": 0.0, "supplier_name": None, "invoice_number": None,
                "notes": "Aggregate category total from verified AGM source document — no per-invoice "
                         "detail available for this year (pre-Civium-portal-scrape).",
                "is_gst_inclusive": False, "data_source": "agm_csv_import", "is_synthetic": False,
                "derivation_level": "aggregate_balancing",
                "created_by": "import_agm_csv_financials.py", "created_at": NOW, "updated_at": NOW,
            } for name, amount in actual]
            if docs:
                await db.expense_transactions.insert_many(docs)

        print(f"\nApplied: {sum(1 for v in FUND_TOTALS.values() for _ in v)} fund-total year/fund corrections, "
              f"{len(category_plan)} year/fund category sets written.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
