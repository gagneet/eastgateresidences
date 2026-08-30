"""Unit tests for services/reconstruction_generators/expense_category_generator.py.

generate_expense_manifest_from_categories() reads levy_categories via a plain motor-style
mongo_db.levy_categories.find(...).to_list(None) call, plus _load_gst_settings and (optionally)
annual_levies for date-basis anchoring. A minimal fake Mongo db is used rather than AsyncMock
throughout, since the function makes several distinct .find() calls across two collections and
a hand-rolled fake keeps the per-collection fixture data readable.

Uses building_id "16244" (Sierra demo), never "13195" (East Gate).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from services.reconstruction_generators.expense_category_generator import (
    generate_expense_manifest_from_categories,
)
from integrations.demo_bank.reconstruction_batch_schemas import ReconstructionBatch

BUILDING_ID = "16244"
_GST_SETTINGS_ON = {"gst_multiplier": 1.1, "effective_gst_rate": 0.1, "gst_registered": True}
_GST_SETTINGS_OFF = {"gst_multiplier": 1.0, "effective_gst_rate": 0.0, "gst_registered": False}


class _FakeCursor:
    def __init__(self, items):
        self._items = items

    def to_list(self, _n):
        async def _inner():
            return list(self._items)
        return _inner()


def _matches_not_test_data(doc: dict) -> bool:
    # Mirrors real Mongo's {"is_test_data": {"$ne": True}} — a MISSING field also matches
    # (not-equal-to-True), same as every doc that predates this flag existing.
    return doc.get("is_test_data") is not True


class _FakeMongoDb:
    def __init__(self, *, levy_categories=None, annual_levies=None):
        self._levy_categories = levy_categories or []
        self._annual_levies = annual_levies or []

    @property
    def levy_categories(self):
        db = self

        class _Coll:
            def find(self, query):
                return _FakeCursor([
                    d for d in db._levy_categories
                    if d.get("building_id") == query.get("building_id") and _matches_not_test_data(d)
                ])

        return _Coll()

    @property
    def annual_levies(self):
        db = self

        class _Coll:
            def find(self, query):
                return _FakeCursor([
                    d for d in db._annual_levies
                    if d.get("building_id") == query.get("building_id") and _matches_not_test_data(d)
                ])

        return _Coll()


def _batch(**overrides) -> ReconstructionBatch:
    defaults = dict(
        batch_id="batch-1", building_id=BUILDING_ID, financial_year_start=2024, financial_year_end=2024,
        reconstruction_method="generate_from_budget_v1", created_by="user-1", is_test_data=True,
    )
    defaults.update(overrides)
    return ReconstructionBatch(**defaults)


async def _run(*, categories, annual_levies=None, batch=None, gst_settings=None):
    db = _FakeMongoDb(levy_categories=categories, annual_levies=annual_levies or [])
    with patch(
        "services.reconstruction_generators.expense_category_generator._load_gst_settings",
        new=AsyncMock(return_value=gst_settings or _GST_SETTINGS_ON),
    ):
        return await generate_expense_manifest_from_categories(
            mongo_db=db, building_id=BUILDING_ID, batch=batch or _batch(),
        )


class TestBasicGeneration:
    @pytest.mark.asyncio
    async def test_one_row_per_category(self):
        categories = [
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "administrative", "name": "Insurance", "actual_amount": 8450.0},
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "sinking", "name": "Lift Replacement", "actual_amount": 44800.0},
        ]
        transactions, warnings = await _run(categories=categories)

        assert len(transactions) == 2
        by_name = {t.description.split(":")[1].split("(")[0].strip(): t for t in transactions}
        assert "Insurance" in by_name
        assert "Lift Replacement" in by_name
        assert not warnings

    @pytest.mark.asyncio
    async def test_direction_is_debit(self):
        categories = [{"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "Cleaning", "actual_amount": 4200.0}]
        transactions, _ = await _run(categories=categories)
        assert transactions[0].direction == "debit"

    @pytest.mark.asyncio
    async def test_fund_type_normalisation(self):
        categories = [
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "Administrative", "name": "A", "actual_amount": 100.0},
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "Sinking Fund", "name": "B", "actual_amount": 200.0},
        ]
        transactions, _ = await _run(categories=categories)
        fund_types = {t.fund_type for t in transactions}
        assert fund_types == {"admin", "sinking"}

    @pytest.mark.asyncio
    async def test_account_ref_matches_fund(self):
        categories = [
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "A", "actual_amount": 100.0},
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "sinking", "name": "B", "actual_amount": 200.0},
        ]
        transactions, _ = await _run(categories=categories)
        refs = {t.fund_type: t.account_ref for t in transactions}
        assert refs["admin"] == f"ADMIN-{BUILDING_ID}"
        assert refs["sinking"] == f"SINKING-{BUILDING_ID}"

    @pytest.mark.asyncio
    async def test_gst_inclusive_amount_split(self):
        categories = [{"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "A", "actual_amount": 1100.0}]
        transactions, _ = await _run(categories=categories, gst_settings=_GST_SETTINGS_ON)
        t = transactions[0]
        assert t.amount_cents == 110000
        assert t.amount_cents == t.amount_ex_gst_cents + t.gst_cents
        assert t.amount_ex_gst_cents == 100000  # 1100 / 1.1 = 1000.00

    @pytest.mark.asyncio
    async def test_gst_unregistered_no_split(self):
        categories = [{"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "A", "actual_amount": 1000.0}]
        transactions, _ = await _run(categories=categories, gst_settings=_GST_SETTINGS_OFF)
        t = transactions[0]
        assert t.gst_cents == 0
        assert t.amount_cents == t.amount_ex_gst_cents == 100000

    @pytest.mark.asyncio
    async def test_no_unit_number_no_per_lot_split(self):
        """Expenses are category-level, never apportioned across units."""
        categories = [{"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "A", "actual_amount": 100.0}]
        transactions, _ = await _run(categories=categories)
        assert transactions[0].unit_number == ""
        assert transactions[0].quarter is None

    @pytest.mark.asyncio
    async def test_posted_date_anchored_to_annual_levies_q4_due_date_when_present(self):
        categories = [{"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "A", "actual_amount": 100.0}]
        annual_levies = [{
            "building_id": BUILDING_ID, "year": "2024",
            "payment_schedule": [{"quarter": "Q4", "due_date": "2024-12-01"}],
        }]
        transactions, _ = await _run(categories=categories, annual_levies=annual_levies)
        # Deterministic offset is -75..+10 days from the Q4 due date, always within the FY.
        assert date(2024, 1, 1) <= transactions[0].posted_date <= date(2024, 12, 31)

    @pytest.mark.asyncio
    async def test_all_categories_in_a_year_share_the_same_summary_effective_date(self):
        """A category's actual_amount is an annual total, not a dated invoice — posted_date is a
        deliberate "summary effective date" (financial-year end / Q4 due date), not a per-category
        pseudo-random spread. All categories for the same year must land on the same date."""
        categories = [
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "Insurance", "actual_amount": 8450.0},
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "sinking", "name": "Lift Replacement", "actual_amount": 44800.0},
        ]
        transactions, _ = await _run(categories=categories)
        dates = {t.posted_date for t in transactions}
        assert len(dates) == 1

    @pytest.mark.asyncio
    async def test_posted_date_never_in_future(self):
        future_year = date.today().year + 1
        categories = [{"building_id": BUILDING_ID, "year": str(future_year), "fund_type": "admin", "name": "A", "actual_amount": 100.0}]
        batch = _batch(financial_year_start=future_year, financial_year_end=future_year)
        transactions, _ = await _run(categories=categories, batch=batch)
        assert transactions[0].posted_date <= date.today()


class TestTestDataIsolation:
    @pytest.mark.asyncio
    async def test_is_test_data_categories_excluded_from_real_reconstruction(self):
        """A category doc created by a test/perf script (is_test_data=True) must never influence
        a real reconstruction run — mirrors _load_units' existing convention in migration_027."""
        categories = [
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "Real", "actual_amount": 100.0},
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "TestOnly", "actual_amount": 999999.0, "is_test_data": True},
        ]
        transactions, _ = await _run(categories=categories)
        names = [t.description for t in transactions]
        assert len(transactions) == 1
        assert not any("TestOnly" in d for d in names)

    @pytest.mark.asyncio
    async def test_is_test_data_annual_levies_excluded_from_date_basis(self):
        """A test-flagged annual_levies doc must not anchor a real reconstruction's expense
        summary-effective-date either (the date-basis lookup is a separate query)."""
        categories = [{"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "A", "actual_amount": 100.0}]
        annual_levies = [{
            "building_id": BUILDING_ID, "year": "2024", "is_test_data": True,
            "payment_schedule": [{"quarter": "Q4", "due_date": "2024-06-01"}],  # deliberately implausible
        }]
        transactions, _ = await _run(categories=categories, annual_levies=annual_levies)
        # Falls back to plain Dec-31 financial-year-end, NOT the test doc's June date.
        assert transactions[0].posted_date == date(2024, 12, 31)


class TestGapTolerance:
    @pytest.mark.asyncio
    async def test_missing_year_warns_not_errors(self):
        batch = _batch(financial_year_start=2023, financial_year_end=2024)
        categories = [{"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "A", "actual_amount": 100.0}]
        transactions, warnings = await _run(categories=categories, batch=batch)

        assert len(transactions) == 1
        assert any("2023" in w and "levy_categories" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_zero_actual_amount_skipped_with_warning(self):
        categories = [
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "A", "actual_amount": 0.0},
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "B", "actual_amount": None},
            {"building_id": BUILDING_ID, "year": "2024", "fund_type": "admin", "name": "C", "actual_amount": 500.0},
        ]
        transactions, warnings = await _run(categories=categories)

        assert len(transactions) == 1
        assert transactions[0].description.startswith("Estimated 2024 spend summary: C")
        assert any("Skipped 2" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_no_categories_at_all_returns_empty_not_error(self):
        """Unlike the income generator, this function never raises on "nothing to generate" —
        that decision belongs to the merge point in _build_manifest_for_batch, since a building
        can legitimately have income data but no expense data (or vice versa)."""
        transactions, warnings = await _run(categories=[])
        assert transactions == []
        assert any("2024" in w for w in warnings)
