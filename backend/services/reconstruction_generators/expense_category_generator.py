# @featuretrace:financial-onboarding — generic (building-agnostic) manifest generator for
#   financial-only onboarding: reconstructs category-level supplier/expense debits from the
#   reported actual spend per category (source-agnostic: financial statement, budget export,
#   manager report, or any other declared-figures document — never assume a specific source).
#   Data flow: levy_categories + annual_levies (Mongo) -> ReconstructedTransactionRow (debit)
#   (building-scoped). Related: budget_levy_generator.py, financial_onboarding.py.
# @featuretrace:demo_bank — produces ReconstructedTransactionRow/ReconstructionManifest for
#   the existing reconstruction-batch pipeline (Mongo staging -> Demo Bank -> matching review),
#   the expense-side sibling of budget_levy_generator.py's owner-payment (income) generator.
# Layer: service
# Data flow: levy_categories + annual_levies (Mongo) -> ReconstructionManifest (debit rows) ->
#            reconstruction_batch_service.submit_for_review() -> [human approval] ->
#            import_historical_reconstruction(). (building-scoped)
# Related: budget_levy_generator.py (income-side sibling — this module never apportions
#          per-lot/UOE, expenses are category-level), financial_onboarding.py
#          (_build_manifest_for_batch merges both generators' output into one manifest),
#          migration_027_randomize_east_gate_demo_bank_levies.py (reused: _rng, _parse_year,
#          _load_levies, _due_dates).
# Toggle: historical_financial_reconstruction, historical_reconstruction_posting
# Collection: levy_categories, annual_levies
"""Generic "actual category spend" expense manifest builder.

Reconstructs one debit (expense) transaction per (year, fund_type, category) from
`levy_categories.actual_amount` — the reported actual spend per line item, from whatever document
or import supplied it (never assume it came from a specific source such as an AGM pack — a
building's proposed budget/actual-spend data can come from a financial statement, a strata
manager's report, a CSV export, or any other declared-figures document). This is the expense-side
counterpart to `budget_levy_generator.py`'s owner-payment (income) generator; the two are combined
into a single manifest by `financial_onboarding._build_manifest_for_batch()`.

Granularity
-----------
One row per category per year, not apportioned across units or quarters. Expenses are not
owner-apportioned by UOE the way levy income is — a supplier invoice or spend category belongs to
the fund as a whole, not to an individual lot. This also matches the shape the source data
actually has: category-level totals per year, not per-lot expense splits.

Gap tolerance
-------------
Missing `levy_categories` data for a year, or a category with a zero/missing `actual_amount`, is
recorded as a warning and skipped — never a hard failure. A building onboarded with partial
history (some years have reported actuals, some don't) is the expected case, not an error
condition, matching the income generator's same gap-tolerant behaviour.

Provenance
----------
This module does not need to set `provenance_class`/`confidence` itself — every row it produces
flows through the same `import_historical_reconstruction()` call as the income side once the
batch is synced, and that function unconditionally stamps `provenance_class="reconstruction"`,
`confidence="high"` on every row it materialises, regardless of `direction`. See
`integrations.demo_bank.ingestion.import_historical_reconstruction()`.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import (
    _due_dates,
    _load_gst_settings,
    _parse_year,
    _rng,
)
from integrations.demo_bank.reconstruction_batch_schemas import (
    ReconstructedTransactionRow,
    ReconstructionBatch,
)

_FUND_ACCOUNT_REF = {
    "admin": "ADMIN-{building_id}",
    "sinking": "SINKING-{building_id}",
}


def _normalise_fund_type(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    return "sinking" if text.startswith("sink") else "admin"


def _expense_effective_date(
    *, year: int, levy_doc: dict[str, Any] | None, max_date: date,
) -> date:
    """A category's `actual_amount` is a reported ANNUAL TOTAL, not one dated invoice — it
    has no real transaction date to reconstruct. Using a random day-of-year here would present a
    summary fact as if it were a single dated event, undermining audit clarity and distorting any
    monthly/trend reporting built on `posted_date` (see docs/features/onboarding-analysis-plan.md,
    "Expense reconstruction needs more careful treatment"). Deliberately NOT deterministic-random:
    every category for a given year gets the SAME explicit "summary effective date" — the
    building's own Q4 due date when known (a defensible "this is when the year's activity is
    being summarised", not a fabricated invoice date), else the plain financial-year-end."""
    anchor = _due_dates(levy_doc, year).get("Q4", date(year, 12, 31)) if levy_doc is not None else date(year, 12, 31)
    return min(anchor, max_date)


async def generate_expense_manifest_from_categories(
    *, mongo_db, building_id: str, batch: ReconstructionBatch,
) -> tuple[list[ReconstructedTransactionRow], list[str]]:
    """Build the expense-side transaction rows + warnings for the requested batch's year range.

    Returns a plain (rows, warnings) tuple rather than a full ReconstructionManifest — this
    generator is never used standalone; `_build_manifest_for_batch()` merges its output with
    the income generator's into one combined manifest and owns manifest-level concerns (hashing,
    reconciliation, "nothing to generate at all" validation). Pure computation, no writes.
    """
    warnings: list[str] = []
    today = datetime.now(timezone.utc).date()

    category_docs = await mongo_db.levy_categories.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}
    ).to_list(None)
    by_year: dict[int, list[dict[str, Any]]] = {}
    for doc in category_docs:
        try:
            year = _parse_year(doc.get("year"))
        except (TypeError, ValueError):
            continue
        if batch.financial_year_start <= year <= batch.financial_year_end:
            by_year.setdefault(year, []).append(doc)

    missing_years = [
        year for year in range(batch.financial_year_start, batch.financial_year_end + 1)
        if year not in by_year
    ]
    if missing_years:
        warnings.append(
            f"No levy_categories (actual spend) found for year(s) {missing_years} — skipped, "
            "no expense rows generated for those years."
        )

    # Date-basis anchor only — reuses the same annual_levies doc the income generator loads,
    # gap-tolerant (a missing doc just falls back to a plain calendar-year date range).
    levy_docs_by_year: dict[int, dict[str, Any]] = {}
    if by_year:
        annual_levy_docs = await mongo_db.annual_levies.find(
            {"building_id": building_id, "is_test_data": {"$ne": True}}
        ).to_list(None)
        for doc in annual_levy_docs:
            try:
                levy_docs_by_year[_parse_year(doc.get("year"))] = doc
            except (TypeError, ValueError):
                continue

    gst = await _load_gst_settings(mongo_db, building_id)
    gst_multiplier = float(gst["gst_multiplier"]) or 1.0

    transactions: list[ReconstructedTransactionRow] = []
    skipped_zero_count = 0

    for year in sorted(by_year):
        for cat in by_year[year]:
            fund_type = _normalise_fund_type(cat.get("fund_type"))
            name = str(cat.get("name") or "Unspecified category").strip()
            try:
                actual_amount = float(cat.get("actual_amount") or 0)
            except (TypeError, ValueError):
                actual_amount = 0.0
            if actual_amount <= 0:
                skipped_zero_count += 1
                continue

            amount_inc_gst_cents = int(round(actual_amount * 100))
            amount_ex_gst_cents = int(round(amount_inc_gst_cents / gst_multiplier))
            gst_cents = amount_inc_gst_cents - amount_ex_gst_cents

            account_ref = _FUND_ACCOUNT_REF[fund_type].format(building_id=building_id)
            posted_date = _expense_effective_date(
                year=year, levy_doc=levy_docs_by_year.get(year), max_date=today,
            )

            transactions.append(ReconstructedTransactionRow(
                account_ref=account_ref,
                unit_number="",
                financial_year=str(year),
                quarter=None,
                fund_type=fund_type,
                levy_component="ordinary",
                posted_date=posted_date,
                amount_cents=amount_inc_gst_cents,
                amount_ex_gst_cents=amount_ex_gst_cents,
                gst_cents=gst_cents,
                direction="debit",
                assumption_code="actual_category_spend",
                description=(
                    f"Estimated {year} spend summary: {name} ({fund_type.title()} Fund) — "
                    "reported ANNUAL TOTAL, not a single dated transaction; posted_date is a "
                    "summary effective date, not a real invoice/payment date."
                ),
                transaction_sequence=1,
                payment_group_id=None,
            ))

    if skipped_zero_count:
        warnings.append(
            f"Skipped {skipped_zero_count} category/year row(s) with zero or missing "
            "actual_amount — nothing to reconstruct for those."
        )

    return transactions, warnings


_SCRAPED_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _parse_scraped_date(raw: str) -> date | None:
    """Parse the Strata Web portal's D/M/YYYY invoice-line date. Returns None (never guesses)
    if the string doesn't match — callers must skip and warn, matching this module's existing
    gap-tolerant, never-hard-fail convention."""
    match = _SCRAPED_DATE_RE.match(str(raw or "").strip())
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


async def generate_itemized_expense_manifest_from_scraper(
    *, mongo_db, building_id: str, batch: ReconstructionBatch,
) -> tuple[list[ReconstructedTransactionRow], list[str]]:
    """Build one expense (debit) row PER individual invoice line captured by the Strata Web
    portal scraper, instead of one lump row per category/year.

    Source: `strata_financials.transactions` (populated by
    `scripts.run_scraper.extract_financials()`, which already
    expands every paginated category on the portal's Building Financials page and captures each
    underlying invoice line — date, invoice/ref, supplier, details, amount). That itemized array
    has always been scraped and stored; nothing downstream previously read it (
    `_sync_scraper_to_levy_collections` only takes the category-level planned/actual totals, and
    `GET /finance/portal-actuals` is read-only display). This is the missing link that lets real,
    dated, supplier-attributed invoice evidence flow into the same reconstruction-manifest ->
    human-approval -> `record_expense()`/`record_historical_expense()` pipeline
    `generate_expense_manifest_from_categories()` already uses for the coarse fallback — per the
    MANDATORY "Category Expenses Must Post Through the Same Demo Bank -> GL Pipeline as Levy
    Income" rule (never a bespoke one-off posting path).

    ADDITIVE, not a replacement: `financial_onboarding._build_manifest_for_batch()` still has the
    coarse category-level generator above as a fallback for years/buildings where the scraper has
    no itemized data (or the building was onboarded via CSV/PDF import instead of the portal).

    Caveat, surfaced explicitly rather than silently accepted: every row this produces still flows
    through the same `import_historical_reconstruction()` materialisation as the coarse generator,
    which unconditionally stamps `provenance_class="reconstruction"` on everything regardless of
    direction (see this module's own docstring above). A real, dated, supplier-attributed scraped
    invoice line is stronger evidence than a back-solved annual total, but the current pipeline
    has no finer-grained provenance tag to give it — flagged as a follow-up, not fixed here.
    """
    warnings: list[str] = []

    fin_docs = await mongo_db.strata_financials.find(
        {"building_id": building_id}
    ).to_list(None)

    gst = await _load_gst_settings(mongo_db, building_id)
    gst_multiplier = float(gst["gst_multiplier"]) or 1.0

    transactions: list[ReconstructedTransactionRow] = []
    skipped_unparseable_date = 0
    skipped_out_of_range = 0
    skipped_zero_or_negative = 0
    categories_with_lines = 0

    for doc in fin_docs:
        lines = doc.get("transactions") or []
        if not lines:
            continue
        categories_with_lines += 1
        fund_type = _normalise_fund_type(doc.get("fund"))
        category_name = str(doc.get("category") or "Unspecified category").strip()
        account_ref = _FUND_ACCOUNT_REF[fund_type].format(building_id=building_id)

        for seq, line in enumerate(lines, start=1):
            posted_date = _parse_scraped_date(line.get("date"))
            if posted_date is None:
                skipped_unparseable_date += 1
                continue
            if not (batch.financial_year_start <= posted_date.year <= batch.financial_year_end):
                skipped_out_of_range += 1
                continue

            try:
                amount = float(line.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if amount <= 0:
                skipped_zero_or_negative += 1
                continue

            amount_inc_gst_cents = int(round(amount * 100))
            amount_ex_gst_cents = int(round(amount_inc_gst_cents / gst_multiplier))
            gst_cents = amount_inc_gst_cents - amount_ex_gst_cents

            supplier = str(line.get("supplier") or "").strip()
            invoice_ref = str(line.get("invoice_ref") or "").strip()
            details = str(line.get("details") or "").strip()
            description_parts = [f"{category_name} ({fund_type.title()} Fund)"]
            if supplier:
                description_parts.append(f"Supplier: {supplier}")
            if invoice_ref:
                description_parts.append(f"Invoice/Ref: {invoice_ref}")
            if details:
                description_parts.append(details)

            transactions.append(ReconstructedTransactionRow(
                account_ref=account_ref,
                unit_number="",
                financial_year=str(posted_date.year),
                quarter=None,
                fund_type=fund_type,
                levy_component="ordinary",
                posted_date=posted_date,
                amount_cents=amount_inc_gst_cents,
                amount_ex_gst_cents=amount_ex_gst_cents,
                gst_cents=gst_cents,
                direction="debit",
                assumption_code="scraped_portal_invoice_line",
                description=" — ".join(description_parts),
                transaction_sequence=seq,
                payment_group_id=None,
            ))

    if skipped_unparseable_date:
        warnings.append(
            f"Skipped {skipped_unparseable_date} scraped invoice line(s) with an unparseable "
            "date — never guessed, needs source review."
        )
    if skipped_out_of_range:
        warnings.append(
            f"Skipped {skipped_out_of_range} scraped invoice line(s) dated outside the batch's "
            f"{batch.financial_year_start}-{batch.financial_year_end} range."
        )
    if skipped_zero_or_negative:
        warnings.append(
            f"Skipped {skipped_zero_or_negative} scraped invoice line(s) with zero/negative "
            "amount."
        )
    if not categories_with_lines:
        warnings.append(
            "No strata_financials.transactions (itemized scraped invoice lines) found for this "
            "building — the coarse category-level generator is the only source available."
        )

    return transactions, warnings
