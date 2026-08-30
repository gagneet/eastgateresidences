"""Tests for services/reconstruction_generators/annual_fund_reconciliation.py.

Uses an in-memory fake Mongo (no real DB), matching the pattern established in
test_gap_tolerant_financial_import.py. Deliberately never constructs a ReconstructedTransactionRow
or calls a generator — this module is decoupled from synthetic transactions by design.
"""
from __future__ import annotations

import pytest

from services.reconstruction_generators.annual_fund_reconciliation import (
    compute_annual_fund_reconciliation,
)

BUILDING_A = "16244"
BUILDING_B = "77001"


class _Cursor:
    def __init__(self, items):
        self._items = items

    def to_list(self, _n):
        async def _inner():
            return list(self._items)
        return _inner()


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    def find(self, query: dict):
        matches = [
            doc for doc in self._docs
            if all(
                (doc.get(k) != v.get("$ne") if isinstance(v, dict) and "$ne" in v else doc.get(k) == v)
                for k, v in query.items()
            )
        ]
        return _Cursor(matches)


class _FakeMongoDb:
    def __init__(self):
        self.annual_levies = _FakeCollection()
        self.levy_categories = _FakeCollection()


def _levy_doc(building_id, year, *, admin=None, sinking=None, control_date=None, cutoff_date=None, is_test_data=False):
    doc = {
        "building_id": building_id, "year": str(year), "is_test_data": is_test_data,
        "admin_fund": admin or {}, "sinking_fund": sinking or {},
    }
    if control_date:
        doc["control_balance_as_of_date"] = control_date
    if cutoff_date:
        doc["expense_ledger_cutoff_date"] = cutoff_date
    return doc


class TestYearByYearCardinality:
    @pytest.mark.asyncio
    async def test_produces_one_line_per_fund_per_year_not_two_total(self):
        """The exact regression this module exists to fix: the old whole-range check produced
        one line per FUND (2 total for a multi-year batch); this must produce one per
        (year, fund) pair."""
        db = _FakeMongoDb()
        for year in (2021, 2022, 2023):
            db.annual_levies._docs.append(_levy_doc(
                BUILDING_A, year,
                admin={"opening_balance_cents": 0, "actual_income_cents": 100000, "actual_spent_cents": 90000, "closing_balance": 100.0},
            ))
        warnings: list[str] = []
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2023, warnings=warnings,
        )
        assert len(lines) == 3 * 2  # 3 years x 2 funds
        years_seen = {l.year for l in lines}
        assert years_seen == {2021, 2022, 2023}

    @pytest.mark.asyncio
    async def test_variance_isolated_to_the_specific_bad_year(self):
        """A whole-range check could net a bad year against a good one and hide the problem —
        this must catch the bad year even when the range's totals would look fine averaged."""
        db = _FakeMongoDb()
        # 2021: genuinely reconciles.
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2021,
            admin={"opening_balance_cents": 0, "actual_income_cents": 100000, "actual_spent_cents": 90000, "closing_balance": 100.0},
        ))
        # 2022: badly wrong (declared closing is way off).
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2022,
            admin={"opening_balance_cents": 10000, "actual_income_cents": 100000, "actual_spent_cents": 90000, "closing_balance": 5000.0},
        ))
        warnings: list[str] = []
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2022, warnings=warnings,
        )
        admin_2021 = next(l for l in lines if l.year == 2021 and l.fund_type == "admin")
        admin_2022 = next(l for l in lines if l.year == 2022 and l.fund_type == "admin")
        assert admin_2021.status == "reconciled"
        assert admin_2022.status == "variance"


class TestOpeningProvenance:
    @pytest.mark.asyncio
    async def test_own_declared_opening_used_when_present(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2022, admin={"opening_balance_cents": 12345, "closing_balance": 500.0},
        ))
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2022, financial_year_end=2022, warnings=[],
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        assert admin.opening_provenance == "own_declared"
        assert admin.opening_balance_cents == 12345

    @pytest.mark.asyncio
    async def test_carried_forward_from_prior_declared_closing(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(BUILDING_A, 2021, admin={"closing_balance": 55.0}))
        db.annual_levies._docs.append(_levy_doc(BUILDING_A, 2022, admin={}))  # no own opening
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2022, warnings=[],
        )
        admin_2022 = next(l for l in lines if l.year == 2022 and l.fund_type == "admin")
        assert admin_2022.opening_provenance == "carried_forward_from_prior_closing"
        assert admin_2022.opening_balance_cents == 5500

    @pytest.mark.asyncio
    async def test_no_prior_data_labeled_generically_across_two_different_buildings(self):
        """Building-agnostic guard: this label must fire purely from data absence for ANY
        building, never from a hardcoded 'first year' assumption."""
        for building_id, year in ((BUILDING_A, 2021), (BUILDING_B, 2019)):
            db = _FakeMongoDb()
            db.annual_levies._docs.append(_levy_doc(building_id, year, admin={"closing_balance": 10.0}))
            warnings: list[str] = []
            lines = await compute_annual_fund_reconciliation(
                db, building_id=building_id, financial_year_start=year, financial_year_end=year, warnings=warnings,
            )
            admin = next(l for l in lines if l.fund_type == "admin")
            assert admin.opening_provenance == "no_prior_data"
            assert admin.opening_balance_cents == 0
            assert any(str(year) in w for w in warnings)

    @pytest.mark.asyncio
    async def test_missing_declared_closing_for_prior_year_warns_not_raises(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(BUILDING_A, 2021, admin={}))  # exists but no closing_balance
        db.annual_levies._docs.append(_levy_doc(BUILDING_A, 2022, admin={}))
        warnings: list[str] = []
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2022, warnings=warnings,
        )
        admin_2022 = next(l for l in lines if l.year == 2022 and l.fund_type == "admin")
        assert admin_2022.opening_provenance == "no_prior_data"
        assert len(warnings) >= 1


class TestOpeningRollforwardGapNeverAffectsExpectedClosing:
    @pytest.mark.asyncio
    async def test_rollforward_gap_computed_but_not_folded_into_expected_closing(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(BUILDING_A, 2021, admin={"closing_balance": -173.43}))
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2022,
            admin={"opening_balance_cents": 2337475, "actual_income_cents": 24897054, "actual_spent_cents": 21690165, "closing_balance": 554.44},
        ))
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2022, warnings=[],
        )
        admin_2022 = next(l for l in lines if l.year == 2022 and l.fund_type == "admin")
        # own opening (23374.75) minus prior declared closing (-17.343) = a large gap.
        assert admin_2022.opening_rollforward_gap_cents == 2337475 - (-17343)
        # expected_closing uses ONLY opening + income - spent — the rollforward gap must not
        # appear anywhere in this arithmetic.
        assert admin_2022.expected_closing_cents == 2337475 + 24897054 - 21690165


class TestCategoryDetailGapIsInformationalOnly:
    @pytest.mark.asyncio
    async def test_category_gap_computed_independently_of_balance_variance(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2021,
            admin={"opening_balance_cents": 0, "actual_income_cents": 13848827, "actual_spent_cents": 15583133, "closing_balance": -17343.06},
        ))
        db.levy_categories._docs.append({
            "building_id": BUILDING_A, "year": "2021", "fund_type": "admin",
            "name": "Banking costs", "actual_amount": 138570.01, "is_test_data": False,
        })
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2021, warnings=[],
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        assert admin.category_detail_total_cents == 13857001
        assert admin.category_detail_gap_cents == 15583133 - 13857001
        # Balance-level status is unaffected by the category gap.
        assert admin.status == "reconciled"

    @pytest.mark.asyncio
    async def test_no_control_total_means_no_category_gap_computed(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(BUILDING_A, 2021, admin={}))  # no actual_spent_cents
        db.levy_categories._docs.append({
            "building_id": BUILDING_A, "year": "2021", "fund_type": "admin",
            "name": "Insurance", "actual_amount": 100.0, "is_test_data": False,
        })
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2021, warnings=[],
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        assert admin.category_detail_gap_cents is None
        assert admin.category_detail_total_cents == 10000  # still computed, just nothing to compare


class TestPartialPeriodMismatch:
    @pytest.mark.asyncio
    async def test_both_dates_present_and_far_apart_flags_mismatch(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2026,
            admin={"opening_balance_cents": 0, "actual_income_cents": 0, "actual_spent_cents": 7559265, "closing_balance": 227.92},
            control_date="2026-07-26", cutoff_date="2026-04-08",
        ))
        warnings: list[str] = []
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2026, financial_year_end=2026, warnings=warnings,
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        assert admin.status == "partial_period_mismatch"
        assert any("mismatched periods" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_dates_close_together_does_not_flag_mismatch(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2022,
            admin={"opening_balance_cents": 0, "actual_income_cents": 100000, "actual_spent_cents": 90000, "closing_balance": 100.0},
            control_date="2022-12-31", cutoff_date="2022-12-20",
        ))
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2022, financial_year_end=2022, warnings=[],
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        assert admin.status == "reconciled"

    @pytest.mark.asyncio
    async def test_either_date_absent_does_not_flag_mismatch(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2022,
            admin={"opening_balance_cents": 0, "actual_income_cents": 100000, "actual_spent_cents": 90000, "closing_balance": 100.0},
            control_date="2022-12-31",  # no cutoff_date at all
        ))
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2022, financial_year_end=2022, warnings=[],
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        assert admin.status == "reconciled"


class TestGapTolerance:
    @pytest.mark.asyncio
    async def test_no_annual_levies_doc_at_all_for_a_year_is_no_control_data_not_an_error(self):
        db = _FakeMongoDb()
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2021, warnings=[],
        )
        assert len(lines) == 2
        assert all(l.status == "no_control_data" for l in lines)

    @pytest.mark.asyncio
    async def test_is_test_data_docs_excluded(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2021, admin={"actual_income_cents": 999, "actual_spent_cents": 1}, is_test_data=True,
        ))
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2021, warnings=[],
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        assert admin.status == "no_control_data"  # the is_test_data doc was correctly ignored


class TestExceptionsIntegration:
    @pytest.mark.asyncio
    async def test_annual_control_adjustment_exception_changes_expected_closing(self):
        """End-to-end proof that reconciliation_exceptions.py's effect_scope gate actually
        reaches this module's arithmetic — not just unit-tested in isolation."""
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2021,
            admin={"opening_balance_cents": 0, "actual_income_cents": 100000, "actual_spent_cents": 90000, "closing_balance": 200.0},
        ))
        exceptions = [{
            "year": 2021, "fund_type": "admin", "amount_cents": 10000, "direction": "credit",
            "effect_scope": "annual_control_adjustment",
        }]
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2021,
            warnings=[], exceptions=exceptions,
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        # 0 + 100000 - 90000 + 10000 (the exception) = 20000 = declared $200.00 exactly.
        assert admin.expected_closing_cents == 20000
        assert admin.status == "reconciled"
        assert admin.annual_control_adjustment_cents == 10000

    @pytest.mark.asyncio
    async def test_informational_scope_exception_does_not_change_expected_closing(self):
        db = _FakeMongoDb()
        db.annual_levies._docs.append(_levy_doc(
            BUILDING_A, 2021,
            admin={"opening_balance_cents": 0, "actual_income_cents": 100000, "actual_spent_cents": 90000, "closing_balance": 100.0},
        ))
        exceptions = [{
            "year": 2021, "fund_type": "admin", "amount_cents": 10000, "direction": "credit",
            "effect_scope": "informational",
        }]
        lines = await compute_annual_fund_reconciliation(
            db, building_id=BUILDING_A, financial_year_start=2021, financial_year_end=2021,
            warnings=[], exceptions=exceptions,
        )
        admin = next(l for l in lines if l.fund_type == "admin")
        assert admin.expected_closing_cents == 10000  # unaffected by the informational entry
        assert admin.annual_control_adjustment_cents == 0
