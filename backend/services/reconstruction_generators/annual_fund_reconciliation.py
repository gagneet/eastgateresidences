# @featuretrace:financial-onboarding — direct annual-control reconciliation for historical
#   financial onboarding: reconciles a building's own imported opening/income/spent/closing
#   figures year by year, entirely independent of the synthetic Demo Bank transactions the
#   reconstruction generators produce. (building-scoped)
# Layer: service
# Data flow: annual_levies (Mongo, is_test_data filtered) -> per-year/fund reconciliation lines
#            -> financial_onboarding.py's manifest.calculation_configuration["annual_fund_reconciliation"]
#            (never consumed by budget_levy_generator.py/expense_category_generator.py — this
#            module reads the same collection those write from, in the opposite direction).
# Related: backend/services/onboarding/gap_tolerant_financial_import.py
#          backend/routers/financial_onboarding.py
#          backend/services/reconstruction_generators/expense_category_generator.py
# Toggle: historical_financial_reconstruction
# Collection: annual_levies, levy_categories
"""Direct annual-control reconciliation — decoupled from synthetic Demo Bank transactions.

Generated Demo Bank transactions model what levy receipts/expenses would look like under the
approved reconstruction assumptions. They are not evidence of the building's actual historical
cash movement, so this reconciliation intentionally uses imported annual control figures instead.

This module answers a narrower, more defensible question instead, using ONLY a building's own
imported annual control figures (`annual_levies.{fund}_fund.opening_balance_cents`/
`actual_income_cents`/`actual_spent_cents`/`closing_balance`) — never the generated
`ReconstructedTransactionRow`s:

    expected_closing[year] = opening_balance[year] + actual_income[year] - actual_spent[year]
    variance[year] = declared_closing_balance[year] - expected_closing[year]

A between-year opening/closing discontinuity (a building's imported opening balance for year N
not matching year N-1's declared closing balance) is reported SEPARATELY
(`opening_rollforward_gap_cents`) and is NEVER folded into `expected_closing[year]` for any year,
for any building — conflating a within-year cash movement with a between-period reporting
discontinuity would make it impossible to independently verify either year's own transactions
balance.

Every check here is gap-tolerant by construction: a building that never supplied a given control
figure gets `None` for anything derived from it, never a raised error and never a false variance.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Any, Literal, Optional

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import _parse_year

_RECONCILIATION_TOLERANCE_CENTS = 100  # $1 — rounding/date-basis slack, not a hard gate
_CUTOFF_MISMATCH_TOLERANCE_DAYS = 30  # generic threshold, not building-specific

OpeningProvenance = Literal["own_declared", "carried_forward_from_prior_closing", "no_prior_data"]
ReconciliationStatus = Literal["reconciled", "variance", "no_control_data", "partial_period_mismatch"]


@dataclass(frozen=True)
class AnnualFundReconciliationLine:
    year: int
    fund_type: str  # "admin" | "sinking"
    opening_balance_cents: int
    opening_provenance: OpeningProvenance
    declared_closing_balance_cents: Optional[int]
    actual_income_cents: Optional[int]
    actual_spent_cents: Optional[int]
    expected_closing_cents: Optional[int]
    variance_cents: Optional[int]
    within_tolerance: Optional[bool]
    opening_rollforward_gap_cents: Optional[int]
    category_detail_total_cents: int
    category_detail_gap_cents: Optional[int]
    annual_control_adjustment_cents: int
    control_balance_as_of_date: Optional[str]
    expense_ledger_cutoff_date: Optional[str]
    status: ReconciliationStatus

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dollars_to_cents(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _cutoff_mismatch(control_date: Optional[str], cutoff_date: Optional[str]) -> bool:
    if not control_date or not cutoff_date:
        return False
    try:
        control = date.fromisoformat(control_date)
        cutoff = date.fromisoformat(cutoff_date)
    except ValueError:
        return False
    return abs((control - cutoff).days) > _CUTOFF_MISMATCH_TOLERANCE_DAYS


async def compute_annual_fund_reconciliation(
    mongo_db, *, building_id: str, financial_year_start: int, financial_year_end: int,
    warnings: list[str], exceptions: Optional[list[dict[str, Any]]] = None,
) -> list[AnnualFundReconciliationLine]:
    """One line per (year, fund_type) across the requested range, using only imported annual
    control figures. Pure computation — no writes.

    `exceptions` (optional, from reconciliation_exceptions.list_exceptions()) — only entries with
    effect_scope="annual_control_adjustment" ever change expected_closing_cents; every other
    scope is informational and has no arithmetic effect here, by construction (see
    reconciliation_exceptions.net_annual_control_adjustments())."""
    from services.reconstruction_generators.reconciliation_exceptions import net_annual_control_adjustments
    exceptions = exceptions or []
    levy_docs: dict[int, dict[str, Any]] = {}
    for doc in await mongo_db.annual_levies.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}
    ).to_list(None):
        try:
            y = _parse_year(doc.get("year"))
        except (TypeError, ValueError):
            continue
        levy_docs[y] = doc

    category_totals: dict[tuple[int, str], int] = {}
    for doc in await mongo_db.levy_categories.find(
        {"building_id": building_id, "is_test_data": {"$ne": True}}
    ).to_list(None):
        try:
            y = _parse_year(doc.get("year"))
        except (TypeError, ValueError):
            continue
        fund_type = doc.get("fund_type")
        if fund_type not in ("admin", "sinking"):
            continue
        amount = _dollars_to_cents(doc.get("actual_amount")) or 0
        key = (y, fund_type)
        category_totals[key] = category_totals.get(key, 0) + amount

    lines: list[AnnualFundReconciliationLine] = []
    for year in range(financial_year_start, financial_year_end + 1):
        doc = levy_docs.get(year)
        control_date = doc.get("control_balance_as_of_date") if doc else None
        cutoff_date = doc.get("expense_ledger_cutoff_date") if doc else None

        for fund_type in ("admin", "sinking"):
            fund_doc = (doc or {}).get(f"{fund_type}_fund") or {}

            own_opening = fund_doc.get("opening_balance_cents")
            declared_closing = _dollars_to_cents(fund_doc.get("closing_balance"))
            actual_income = fund_doc.get("actual_income_cents")
            actual_spent = fund_doc.get("actual_spent_cents")

            prior_doc = levy_docs.get(year - 1)
            prior_fund_doc = (prior_doc or {}).get(f"{fund_type}_fund") or {}
            prior_declared_closing = _dollars_to_cents(prior_fund_doc.get("closing_balance"))

            if own_opening is not None:
                opening = own_opening
                provenance: OpeningProvenance = "own_declared"
            elif prior_doc is not None and prior_declared_closing is not None:
                opening = prior_declared_closing
                provenance = "carried_forward_from_prior_closing"
            elif prior_doc is not None:
                # A prior-year doc exists for this building but never declared its own closing
                # balance — genuinely unknown, not "no prior data at all." Treat the same as
                # no_prior_data (opening=0) but keep the distinction visible via the warning text.
                opening = 0
                provenance = "no_prior_data"
                warnings.append(
                    f"{year} {fund_type}: no opening balance supplied and the prior year "
                    f"({year - 1}) has no declared closing balance either — opening treated as "
                    "$0, not a confirmed figure."
                )
            else:
                opening = 0
                provenance = "no_prior_data"
                warnings.append(
                    f"{year} {fund_type}: no prior-year data exists for this building at all — "
                    "opening balance treated as $0, not a confirmed figure. Expected for a "
                    "building's first onboarded year."
                )

            control_adjustment_cents = net_annual_control_adjustments(exceptions, year=year, fund_type=fund_type)
            if actual_income is not None and actual_spent is not None:
                expected_closing = opening + actual_income - actual_spent + control_adjustment_cents
            else:
                expected_closing = None

            if expected_closing is not None and declared_closing is not None:
                variance = declared_closing - expected_closing
                within_tolerance = abs(variance) <= _RECONCILIATION_TOLERANCE_CENTS
            else:
                variance = None
                within_tolerance = None

            if own_opening is not None and prior_declared_closing is not None:
                rollforward_gap = own_opening - prior_declared_closing
            else:
                rollforward_gap = None

            category_total = category_totals.get((year, fund_type), 0)
            if actual_spent is not None:
                category_gap = actual_spent - category_total
                if abs(category_gap) > _RECONCILIATION_TOLERANCE_CENTS:
                    warnings.append(
                        f"{year} {fund_type}: category-level detail sums to "
                        f"${category_total / 100:.2f} but the declared actual_spent control is "
                        f"${actual_spent / 100:.2f} (gap ${category_gap / 100:.2f}) — category "
                        "breakdown is incomplete, not evidence of missing cash."
                    )
            else:
                category_gap = None

            mismatch = _cutoff_mismatch(control_date, cutoff_date)
            if mismatch:
                warnings.append(
                    f"{year} {fund_type}: declared balance is current to {control_date} but "
                    f"category-level expense detail only extends to {cutoff_date} — this year's "
                    "reconciliation compares mismatched periods and must not be treated as "
                    "confirming full-period completeness."
                )

            if mismatch:
                status: ReconciliationStatus = "partial_period_mismatch"
            elif expected_closing is None:
                status = "no_control_data"
            elif within_tolerance:
                status = "reconciled"
            else:
                status = "variance"

            lines.append(AnnualFundReconciliationLine(
                year=year, fund_type=fund_type,
                opening_balance_cents=opening, opening_provenance=provenance,
                declared_closing_balance_cents=declared_closing,
                actual_income_cents=actual_income, actual_spent_cents=actual_spent,
                expected_closing_cents=expected_closing, variance_cents=variance,
                within_tolerance=within_tolerance,
                opening_rollforward_gap_cents=rollforward_gap,
                category_detail_total_cents=category_total,
                category_detail_gap_cents=category_gap,
                annual_control_adjustment_cents=control_adjustment_cents,
                control_balance_as_of_date=control_date, expense_ledger_cutoff_date=cutoff_date,
                status=status,
            ))

    return lines
