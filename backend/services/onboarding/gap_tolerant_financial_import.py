# @featuretrace:financial-onboarding — gap-tolerant CSV/JSON importer for historical financial
#   onboarding: populates annual_levies (proposed budget + closing balance) and levy_categories
#   (actual category spend) from a partial-data upload, for any building.
# Layer: service
# Data flow: uploaded CSV/JSON (multipart, financial_onboarding.py's
#            /financials/onboarding/building/{id}/import-financial-data) -> this module ->
#            annual_levies / levy_categories (Mongo, upsert) -> read back unmodified by
#            budget_levy_generator.py / expense_category_generator.py. (building-scoped)
# Related: backend/services/reconstruction_generators/budget_levy_generator.py
#          backend/services/reconstruction_generators/expense_category_generator.py
#          backend/routers/financial_onboarding.py
# Toggle: historical_financial_reconstruction
# Collection: annual_levies, levy_categories
"""Gap-tolerant historical financial CSV/JSON importer.

Deliberately separate from the two existing general-purpose CSV upload paths
(`/finance/upload-*` in `backend/routers/finance.py`, `/financial-import/*` from
`backend/routers/financial_import.py`) — those assume relatively complete, current-year data.
This importer is purpose-built for the "partial historical data is normal" case: a building being
financial-only onboarded may have a proposed budget for some years, closing balances for some
funds, and reported actual category spend for some years — any subset, in any combination, from
whatever document or source supplied it (financial statement, budget export, manager report, or
any other declared-figures document — never assume a specific source such as an AGM pack).
Every field beyond `year`+`fund_type` is optional; a missing value is recorded in the returned
coverage summary, never raised as an error. Writes into the SAME `annual_levies`/`levy_categories`
collections the existing reconstruction generators already read — no new collection, no schema
change, so a corrected re-upload for one year is a normal upsert, not a special case.

Row shape (CSV header or JSON object keys, case-insensitive for CSV):
    year, fund_type, proposed_amount, closing_balance,
    opening_balance, actual_income, actual_spent,
    control_balance_as_of_date, expense_ledger_cutoff_date,
    category_name, category_proposed_amount, category_actual_amount,
    opening_balance_basis, closing_balance_basis, reconciliation_basis,
    income_control_type, verified_partial_income, income_documentation_gap,
    reason_code, reconciliation_status, derived_amount_posting_allowed,
    source_file, source_period_start, source_period_end

`year` and `fund_type` (admin|sinking, case-insensitive, "admin"/"sinking" prefix match) are the
only required fields. A row may carry a fund-level update (`proposed_amount`/`closing_balance`),
a category-level update (`category_name` plus `category_proposed_amount`/
`category_budgeted_amount`/`budgeted_amount` and/or `category_actual_amount`), or both.

`proposed_amount`/`closing_balance` are budget/declared-balance figures stored as float dollars on
`annual_levies.{fund}_fund` for backward compatibility with existing readers, AND (new) as integer
cents (`proposed_amount_cents`/`closing_balance_cents`) parsed via `Decimal` rather than `float`,
per this repo's mandatory cents-precision rule — binary float rounding is not acceptable for a
canonical cents value even at ingestion. `opening_balance`, `actual_income`, `actual_spent` are
actual-cash control figures — a distinct concept from the proposed budget in `proposed_amount` —
and are stored as integer cents only: `opening_balance_cents`, `actual_income_cents`, and
`actual_spent_cents`. `control_balance_as_of_date` (the date the declared opening/closing balance
is current to) and `expense_ledger_cutoff_date` (the date category-level expense detail actually
extends to, when a building's source data states one explicitly — never inferred from a
meeting/AGM date) are stored once per building/year, not nested per fund, since a single
balance-sheet date applies to both funds for a given building.

Reconciliation-evidence fields (new, all optional, fund-level): `opening_balance_basis`/
`closing_balance_basis`/`reconciliation_basis` record which accounting basis (e.g. "cash",
"fund_equity") a declared balance uses — needed when a building's opening and closing balances
for adjacent years are on different bases (a basis bridge, never itself a transaction).
`income_control_type` marks whether `actual_income_cents` was directly observed from source
documents or is a `derived_from_verified_roll_forward` plug computed from
opening+expense=closing when category-level income evidence is incomplete; when derived,
`verified_partial_income`/`income_documentation_gap` (dollars, converted to
`verified_partial_income_cents`/`income_documentation_gap_cents`) record how much of the derived
total is actually source-backed. `reason_code`/`reconciliation_status` are free-form audit labels
(e.g. `INCOME_CATEGORY_DETAIL_INCOMPLETE` / `reconciled_control_income_detail_pending`).
`derived_amount_posting_allowed` (bool, default `false` when `income_control_type` is set to a
derived value) is a hard gate: Phase C's reconciliation service must refuse to promote a record to
a fully-reconciled/postable status while this is `false`, regardless of variance being zero.
`source_file`/`source_period_start`/`source_period_end` are provenance — attached to whichever
record the row updates (the fund object if the row has no `category_name`, else the specific
category document).
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import uuid4

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import (
    LEVY_FREQUENCIES,
    _parse_year,
)
from utils.category_taxonomy import canonical_category

REQUIRED_COLUMNS = {"year", "fund_type"}
_TRUE_STRINGS = {"true", "1", "yes", "y"}


class FinancialImportError(ValueError):
    """Raised only for structurally invalid input (unparseable file, missing required columns)
    — never for a missing optional value within an otherwise-valid row, which is the expected
    gap case this importer exists to tolerate."""


def _normalise_levy_frequency(raw: Any) -> Optional[str]:
    text = str(raw or "").strip().lower()
    return text if text in LEVY_FREQUENCIES else None


def _normalise_fund_type(raw: Any) -> Optional[str]:
    text = str(raw or "").strip().lower()
    if text.startswith("admin"):
        return "admin"
    if text.startswith("sink"):
        return "sinking"
    return None


def _parse_amount(raw: Any) -> Optional[float]:
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _dollars_to_cents(dollars: float) -> int:
    return int(round(dollars * 100))


def _parse_amount_decimal(raw: Any) -> Optional[Decimal]:
    """Same tolerance as _parse_amount, but returns a Decimal for cents conversion —
    binary float rounding is not acceptable for a canonical cents value, even here."""
    if raw in (None, ""):
        return None
    text = str(raw).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _dollars_to_cents_decimal(dollars: Decimal) -> int:
    return int((dollars * 100).to_integral_value())


def _parse_bool(raw: Any) -> Optional[bool]:
    if raw in (None, ""):
        return None
    return str(raw).strip().lower() in _TRUE_STRINGS


def _parse_date(raw: Any) -> Optional[str]:
    """Accepts ISO (YYYY-MM-DD) only — the generic, unambiguous format this importer's own
    upstream converters are expected to emit. Malformed input is gap-tolerant (None, not raised),
    matching every other optional field in this importer."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def parse_rows(content: bytes, filename: str) -> list[dict[str, Any]]:
    """Parse CSV or JSON bytes into a flat list of raw row dicts (lower-cased keys)."""
    if filename.lower().endswith(".json"):
        try:
            data = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinancialImportError(f"Could not parse JSON file: {exc}") from exc
        if not isinstance(data, list):
            raise FinancialImportError("JSON file must be a top-level array of row objects.")
        rows = [{str(k).strip().lower(): v for k, v in row.items()} for row in data if isinstance(row, dict)]
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise FinancialImportError("CSV file has no header row.")
        header = {h.strip().lower() for h in reader.fieldnames}
        missing = REQUIRED_COLUMNS - header
        if missing:
            raise FinancialImportError(f"Missing required column(s): {', '.join(sorted(missing))}")
        rows = [{k.strip().lower(): v for k, v in row.items()} for row in reader]

    if not rows:
        raise FinancialImportError("File contains no data rows.")
    return rows


async def import_gap_tolerant_financial_data(
    mongo_db, *, building_id: str, content: bytes, filename: str, uploaded_by: str,
    is_test_data: bool = False,
) -> dict[str, Any]:
    """Parse + upsert. Returns a structured result including per-row warnings (rows skipped for
    being unparseable — a data-quality issue in the upload itself) and a coverage summary (which
    years/funds/categories now have data, for the wizard's preview step to render as a grid).

    `is_test_data`, per this repo's mandatory convention, is stamped on every written
    annual_levies/levy_categories doc — the reconstruction generators filter it out (see
    `_load_levies`/`generate_expense_manifest_from_categories`), and the standard
    `is_test_data=True` sweep in `tests/backend/conftest.py` cleans it up. Never True for a real
    production upload — only test/k6/perf scripts set it."""
    rows = parse_rows(content, filename)

    fund_updates_by_year: dict[int, dict[str, dict[str, Any]]] = {}
    year_level_dates: dict[int, dict[str, str]] = {}
    category_rows: list[
        tuple[int, str, str, Optional[float], Optional[float], Optional[str], Optional[str], Optional[str]]
    ] = []
    row_warnings: list[str] = []

    for i, row in enumerate(rows, start=2):  # header is conceptually row 1
        try:
            year = _parse_year(row.get("year"))
        except (TypeError, ValueError):
            row_warnings.append(f"Row {i}: could not parse 'year' ({row.get('year')!r}) — skipped.")
            continue
        fund_type = _normalise_fund_type(row.get("fund_type"))
        if fund_type is None:
            row_warnings.append(
                f"Row {i}: missing/unrecognised fund_type ({row.get('fund_type')!r}, expected "
                "'admin' or 'sinking') — skipped."
            )
            continue

        proposed_amount = _parse_amount(row.get("proposed_amount"))
        closing_balance = _parse_amount(row.get("closing_balance"))
        opening_balance = _parse_amount(row.get("opening_balance"))
        actual_income = _parse_amount(row.get("actual_income"))
        actual_spent = _parse_amount(row.get("actual_spent"))
        proposed_amount_dec = _parse_amount_decimal(row.get("proposed_amount"))
        closing_balance_dec = _parse_amount_decimal(row.get("closing_balance"))
        verified_partial_income_dec = _parse_amount_decimal(row.get("verified_partial_income"))
        income_documentation_gap_dec = _parse_amount_decimal(row.get("income_documentation_gap"))

        opening_balance_basis = str(row.get("opening_balance_basis") or "").strip() or None
        closing_balance_basis = str(row.get("closing_balance_basis") or "").strip() or None
        reconciliation_basis = str(row.get("reconciliation_basis") or "").strip() or None
        income_control_type = str(row.get("income_control_type") or "").strip() or None
        reason_code = str(row.get("reason_code") or "").strip() or None
        reconciliation_status = str(row.get("reconciliation_status") or "").strip() or None
        derived_amount_posting_allowed = _parse_bool(row.get("derived_amount_posting_allowed"))
        fund_source_file = str(row.get("source_file") or "").strip() or None
        fund_source_period_start = _parse_date(row.get("source_period_start"))
        fund_source_period_end = _parse_date(row.get("source_period_end"))

        if any(v is not None for v in (
            proposed_amount, closing_balance, opening_balance, actual_income, actual_spent,
            opening_balance_basis, closing_balance_basis, reconciliation_basis,
            income_control_type, verified_partial_income_dec, income_documentation_gap_dec,
            reason_code, reconciliation_status, derived_amount_posting_allowed,
        )):
            fund_updates_by_year.setdefault(year, {}).setdefault(fund_type, {})
            if proposed_amount is not None:
                fund_updates_by_year[year][fund_type]["levy_income"] = proposed_amount
            if closing_balance is not None:
                fund_updates_by_year[year][fund_type]["closing_balance"] = closing_balance
            # New control fields are actual-cash figures, so store integer cents at ingestion.
            # levy_income/closing_balance stay in their legacy float-dollar shape because
            # existing annual_levies readers already consume those fields that way — but also
            # gain a *_cents twin (Decimal-parsed) so a cents-exact reconciliation validator has
            # a canonical value to read, per this repo's mandatory cents-precision rule.
            if opening_balance is not None:
                fund_updates_by_year[year][fund_type]["opening_balance_cents"] = _dollars_to_cents(opening_balance)
            if actual_income is not None:
                fund_updates_by_year[year][fund_type]["actual_income_cents"] = _dollars_to_cents(actual_income)
            if actual_spent is not None:
                fund_updates_by_year[year][fund_type]["actual_spent_cents"] = _dollars_to_cents(actual_spent)
            if proposed_amount_dec is not None:
                fund_updates_by_year[year][fund_type]["proposed_amount_cents"] = _dollars_to_cents_decimal(proposed_amount_dec)
            if closing_balance_dec is not None:
                fund_updates_by_year[year][fund_type]["closing_balance_cents"] = _dollars_to_cents_decimal(closing_balance_dec)
            if opening_balance_basis is not None:
                fund_updates_by_year[year][fund_type]["opening_balance_basis"] = opening_balance_basis
            if closing_balance_basis is not None:
                fund_updates_by_year[year][fund_type]["closing_balance_basis"] = closing_balance_basis
            if reconciliation_basis is not None:
                fund_updates_by_year[year][fund_type]["reconciliation_basis"] = reconciliation_basis
            if income_control_type is not None:
                fund_updates_by_year[year][fund_type]["income_control_type"] = income_control_type
            if verified_partial_income_dec is not None:
                fund_updates_by_year[year][fund_type]["verified_partial_income_cents"] = _dollars_to_cents_decimal(verified_partial_income_dec)
            if income_documentation_gap_dec is not None:
                fund_updates_by_year[year][fund_type]["income_documentation_gap_cents"] = _dollars_to_cents_decimal(income_documentation_gap_dec)
            if reason_code is not None:
                fund_updates_by_year[year][fund_type]["reason_code"] = reason_code
            if reconciliation_status is not None:
                fund_updates_by_year[year][fund_type]["reconciliation_status"] = reconciliation_status
            if derived_amount_posting_allowed is not None:
                fund_updates_by_year[year][fund_type]["derived_amount_posting_allowed"] = derived_amount_posting_allowed
            # A derived income control must default closed (never postable) unless the row
            # explicitly says otherwise — silence must not be read as permission to post.
            elif income_control_type == "derived_from_verified_roll_forward":
                fund_updates_by_year[year][fund_type]["derived_amount_posting_allowed"] = False
            if fund_source_file is not None and not str(row.get("category_name") or "").strip():
                fund_updates_by_year[year][fund_type]["source_file"] = fund_source_file
            if fund_source_period_start is not None and not str(row.get("category_name") or "").strip():
                fund_updates_by_year[year][fund_type]["source_period_start"] = fund_source_period_start
            if fund_source_period_end is not None and not str(row.get("category_name") or "").strip():
                fund_updates_by_year[year][fund_type]["source_period_end"] = fund_source_period_end

        control_date_raw = row.get("control_balance_as_of_date")
        if control_date_raw:
            parsed = _parse_date(control_date_raw)
            if parsed is None:
                row_warnings.append(f"Row {i}: could not parse control_balance_as_of_date ({control_date_raw!r}) — ignored.")
            else:
                year_level_dates.setdefault(year, {})["control_balance_as_of_date"] = parsed
        cutoff_date_raw = row.get("expense_ledger_cutoff_date")
        if cutoff_date_raw:
            parsed = _parse_date(cutoff_date_raw)
            if parsed is None:
                row_warnings.append(f"Row {i}: could not parse expense_ledger_cutoff_date ({cutoff_date_raw!r}) — ignored.")
            else:
                year_level_dates.setdefault(year, {})["expense_ledger_cutoff_date"] = parsed

        levy_frequency_raw = row.get("levy_frequency")
        if levy_frequency_raw:
            normalised_frequency = _normalise_levy_frequency(levy_frequency_raw)
            if normalised_frequency is None:
                row_warnings.append(
                    f"Row {i}: unrecognised levy_frequency ({levy_frequency_raw!r}, expected one of "
                    f"{sorted(LEVY_FREQUENCIES)}) — defaulted to 'quarterly'."
                )
                normalised_frequency = "quarterly"
            year_level_dates.setdefault(year, {})["levy_frequency"] = normalised_frequency

        category_name = str(row.get("category_name") or "").strip()
        if category_name:
            category_proposed_amount = _parse_amount(
                row.get("category_proposed_amount")
                or row.get("category_budgeted_amount")
                or row.get("budgeted_amount")
            )
            category_rows.append((
                year, fund_type, category_name, category_proposed_amount,
                _parse_amount(row.get("category_actual_amount")),
                fund_source_file, fund_source_period_start, fund_source_period_end,
            ))

    now = datetime.now(timezone.utc)
    years_touched: set[int] = set(fund_updates_by_year) | set(year_level_dates)

    fund_write_count = 0
    for year in years_touched:
        funds = fund_updates_by_year.get(year, {})
        dates = year_level_dates.get(year, {})
        if not funds and not dates:
            continue
        set_fields: dict[str, Any] = {
            "building_id": building_id, "year": str(year), "updated_at": now,
            "is_test_data": is_test_data,
            **dates,
        }
        for fund_type, values in funds.items():
            for field, value in values.items():
                set_fields[f"{fund_type}_fund.{field}"] = value
        await mongo_db.annual_levies.update_one(
            {"building_id": building_id, "year": str(year)},
            {"$set": set_fields, "$setOnInsert": {"created_at": now, "imported_by": uploaded_by}},
            upsert=True,
        )
        fund_write_count += 1

    category_write_count = 0
    for (
        year, fund_type, name, proposed_amount, actual_amount,
        cat_source_file, cat_source_period_start, cat_source_period_end,
    ) in category_rows:
        years_touched.add(year)
        filter_doc = {"building_id": building_id, "year": str(year), "name": name, "fund_type": fund_type}
        # Stamp the canonical category identity so multi-source imports are deterministically
        # groupable and duplicate spellings of one economic category can be consolidated
        # (see utils/category_taxonomy.py + scripts/data_repair/consolidate_levy_categories_canonical.py).
        _canon = canonical_category(name, fund_type)
        set_doc: dict[str, Any] = {
            "updated_at": now, "is_test_data": is_test_data,
            "canonical_key": _canon.key, "canonical_name": _canon.display_name,
        }
        set_on_insert_extra: dict[str, Any] = {}
        if proposed_amount is not None:
            set_doc["budgeted_amount"] = proposed_amount
        else:
            set_on_insert_extra["budgeted_amount"] = 0.0
        if cat_source_file is not None:
            set_doc["source_file"] = cat_source_file
        if cat_source_period_start is not None:
            set_doc["source_period_start"] = cat_source_period_start
        if cat_source_period_end is not None:
            set_doc["source_period_end"] = cat_source_period_end
        if actual_amount is not None:
            # A row explicitly supplies an amount — always the authoritative new value,
            # whether this is a new category or a corrected re-upload of an existing one.
            set_doc["actual_amount"] = actual_amount
            set_doc["status"] = "actual"
        else:
            # No amount supplied for this row. The 0.0/"proposed" placeholder is valid only
            # when inserting a brand-new category; a corrected re-upload that omits the amount
            # must not overwrite an existing real actual_amount/status.
            set_on_insert_extra["actual_amount"] = 0.0
            set_on_insert_extra["status"] = "proposed"
        # Atomic upsert (matches the annual_levies pattern above) rather than a separate
        # find_one + insert_one/update_one — the previous check-then-act shape allowed two
        # concurrent uploads for the same year/category/fund_type to race and create two
        # levy_categories docs with different generated ids instead of one.
        await mongo_db.levy_categories.update_one(
            filter_doc,
            {
                "$set": set_doc,
                "$setOnInsert": {**filter_doc, "id": str(uuid4()), "created_at": now, **set_on_insert_extra},
            },
            upsert=True,
        )
        category_write_count += 1

    coverage = await _coverage_summary(mongo_db, building_id=building_id, years=sorted(years_touched))

    return {
        "years_touched": sorted(years_touched),
        "fund_rows_written": fund_write_count,
        "category_rows_written": category_write_count,
        "row_warnings": row_warnings,
        "coverage": coverage,
    }


async def _coverage_summary(mongo_db, *, building_id: str, years: list[int]) -> list[dict[str, Any]]:
    """One entry per touched year: which of the 4 data points (proposed admin, proposed
    sinking, closing balance per fund, category count) are actually present — the wizard's
    preview step renders this directly as a ✓/— grid so gaps are visible before generation."""
    if not years:
        return []

    levy_docs: dict[int, dict[str, Any]] = {}
    for doc in await mongo_db.annual_levies.find({"building_id": building_id}).to_list(None):
        try:
            y = _parse_year(doc.get("year"))
        except (TypeError, ValueError):
            continue
        if y in years:
            levy_docs[y] = doc

    category_counts: dict[int, int] = {}
    for doc in await mongo_db.levy_categories.find({"building_id": building_id}).to_list(None):
        try:
            y = _parse_year(doc.get("year"))
        except (TypeError, ValueError):
            continue
        if y in years:
            category_counts[y] = category_counts.get(y, 0) + 1

    summary = []
    for year in years:
        doc = levy_docs.get(year, {})
        admin = doc.get("admin_fund") or {}
        sinking = doc.get("sinking_fund") or {}
        summary.append({
            "year": year,
            "proposed_admin_present": admin.get("levy_income") not in (None, ""),
            "proposed_sinking_present": sinking.get("levy_income") not in (None, ""),
            "closing_balance_admin_present": admin.get("closing_balance") not in (None, ""),
            "closing_balance_sinking_present": sinking.get("closing_balance") not in (None, ""),
            "actual_income_admin_present": admin.get("actual_income_cents") is not None,
            "actual_income_sinking_present": sinking.get("actual_income_cents") is not None,
            "actual_spent_admin_present": admin.get("actual_spent_cents") is not None,
            "actual_spent_sinking_present": sinking.get("actual_spent_cents") is not None,
            "opening_balance_admin_present": admin.get("opening_balance_cents") is not None,
            "opening_balance_sinking_present": sinking.get("opening_balance_cents") is not None,
            "control_balance_as_of_date": doc.get("control_balance_as_of_date"),
            "expense_ledger_cutoff_date": doc.get("expense_ledger_cutoff_date"),
            "category_count": category_counts.get(year, 0),
        })
    return summary
