"""
tests/backend/domain/test_recurring_bills.py

Tests for domain/recurring_bills.py — fixed_monthly, variable_cadence,
annual_with_instalments, one_off templates, idempotency, trailing median.
"""
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, call
from bson import ObjectId

from domain.recurring_bills import (
    _idempotency_key,
    _period_label,
    _trailing_median,
    instantiate_recurring_bills,
)

BUILDING_A = "13195"
TMPL_OID = ObjectId()
TMPL_ID = str(TMPL_OID)


def _make_db(find_one_result=None, history=None) -> MagicMock:
    mock_db = MagicMock()
    # recurring_bill_templates.find → returns templates
    tmpl_cursor = MagicMock()
    tmpl_cursor.to_list = AsyncMock(return_value=[])
    mock_db.recurring_bill_templates.find.return_value = tmpl_cursor
    mock_db.recurring_bill_templates.update_one = AsyncMock()

    # invoice_documents.find_one → idempotency check
    mock_db.invoice_documents.find_one = AsyncMock(return_value=find_one_result)

    # invoice_documents.find → history for variable_cadence
    hist_cursor = MagicMock()
    hist_cursor.to_list = AsyncMock(return_value=history or [])
    mock_db.invoice_documents.find.return_value = hist_cursor

    insert_result = MagicMock()
    insert_result.inserted_id = ObjectId()
    mock_db.invoice_documents.insert_one = AsyncMock(return_value=insert_result)

    return mock_db


def _tmpl(**kwargs) -> dict:
    base = {
        "_id": TMPL_OID,
        "is_active": True,
        "vendor_name": "Cleaning Co",
        "vendor_abn": "51824753556",
        "description": "Monthly cleaning",
        "category": "cleaning",
        "template_type": "fixed_monthly",
        "amount_cents": 50_000,
        "due_day": 1,
    }
    base.update(kwargs)
    return base


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestPeriodLabel:
    def test_deterministic(self):
        d = date(2026, 5, 1)
        assert _period_label("tmpl-abc", d) == "tmpl-abc:2026-05-01"

    def test_idempotency_key_is_hex(self):
        key = _idempotency_key("tmpl-abc", date(2026, 5, 1))
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_different_dates_different_keys(self):
        k1 = _idempotency_key(TMPL_ID, date(2026, 5, 1))
        k2 = _idempotency_key(TMPL_ID, date(2026, 6, 1))
        assert k1 != k2


class TestTrailingMedian:
    def test_three_values(self):
        assert _trailing_median([100, 200, 300]) == 200  # sorted median

    def test_uses_last_three(self):
        assert _trailing_median([50, 100, 200, 300, 400]) == 300

    def test_two_values_returns_higher(self):
        assert _trailing_median([100, 200]) == 200  # sorted[1] with len=2, mid=1

    def test_one_value(self):
        assert _trailing_median([150]) == 150

    def test_empty_returns_zero(self):
        assert _trailing_median([]) == 0


# ── fixed_monthly ─────────────────────────────────────────────────────────────

class TestFixedMonthly:
    @pytest.mark.asyncio
    async def test_instantiates_next_month_draft(self):
        mock_db = _make_db(find_one_result=None)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[_tmpl(template_type="fixed_monthly")])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        today = date(2026, 4, 23)  # next month = May 2026
        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=today, lookahead_days=14)

        assert len(ids) == 1
        mock_db.invoice_documents.insert_one.assert_called_once()
        inserted = mock_db.invoice_documents.insert_one.call_args[0][0]
        assert inserted["state"] == "draft"
        assert inserted["due_date"] == "2026-05-01"
        assert inserted["building_id"] == BUILDING_A
        assert inserted["tenant_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_idempotent_skips_existing(self):
        existing = {"_id": ObjectId(), "idempotency_key": "existing"}
        mock_db = _make_db(find_one_result=existing)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[_tmpl(template_type="fixed_monthly")])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=14)

        assert ids == []
        mock_db.invoice_documents.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_due_date_beyond_horizon_skipped(self):
        mock_db = _make_db(find_one_result=None)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[_tmpl(template_type="fixed_monthly")])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        # lookahead=1 day from Apr 23 → horizon Apr 24; next due date May 1 is beyond
        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=1)

        assert ids == []


# ── variable_cadence ──────────────────────────────────────────────────────────

class TestVariableCadence:
    @pytest.mark.asyncio
    async def test_uses_trailing_median(self):
        history = [
            {"total_cents": 100_00, "state": "reconciled"},
            {"total_cents": 200_00, "state": "reconciled"},
            {"total_cents": 300_00, "state": "reconciled"},
        ]
        mock_db = _make_db(find_one_result=None, history=history)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[_tmpl(template_type="variable_cadence")])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=14)

        assert len(ids) == 1
        inserted = mock_db.invoice_documents.insert_one.call_args[0][0]
        assert inserted["total_cents"] == 200_00  # median of 100, 200, 300
        assert inserted["is_forecast_amount"] is True

    @pytest.mark.asyncio
    async def test_fallback_to_template_amount_when_no_history(self):
        mock_db = _make_db(find_one_result=None, history=[])
        # invoice_documents.find returns empty history
        hist_cursor = MagicMock()
        hist_cursor.to_list = AsyncMock(return_value=[])
        mock_db.invoice_documents.find.return_value = hist_cursor

        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[_tmpl(template_type="variable_cadence", amount_cents=75_000)])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=14)

        assert len(ids) == 1
        inserted = mock_db.invoice_documents.insert_one.call_args[0][0]
        assert inserted["total_cents"] == 75_000


# ── one_off ───────────────────────────────────────────────────────────────────

class TestOneOff:
    @pytest.mark.asyncio
    async def test_past_due_date_instantiates(self):
        mock_db = _make_db(find_one_result=None)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[
            _tmpl(template_type="one_off", due_date="2026-04-20", amount_cents=500_000)
        ])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=7)

        assert len(ids) == 1

    @pytest.mark.asyncio
    async def test_deactivates_template_after_instantiation(self):
        mock_db = _make_db(find_one_result=None)
        tmpl = _tmpl(template_type="one_off", due_date="2026-04-20")
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[tmpl])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=7)

        mock_db.recurring_bill_templates.update_one.assert_called_once()
        update_args = mock_db.recurring_bill_templates.update_one.call_args[0]
        assert update_args[1]["$set"]["is_active"] is False

    @pytest.mark.asyncio
    async def test_future_due_date_not_instantiated(self):
        mock_db = _make_db(find_one_result=None)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[
            _tmpl(template_type="one_off", due_date="2026-12-01")
        ])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=7)

        assert ids == []

    @pytest.mark.asyncio
    async def test_missing_due_date_skipped(self):
        mock_db = _make_db(find_one_result=None)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[_tmpl(template_type="one_off")])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=7)

        assert ids == []


# ── Multi-tenant isolation ────────────────────────────────────────────────────

class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_inserted_doc_has_correct_building_id(self):
        mock_db = _make_db(find_one_result=None)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[_tmpl(template_type="fixed_monthly")])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=14)

        inserted = mock_db.invoice_documents.insert_one.call_args[0][0]
        assert inserted["building_id"] == BUILDING_A
        assert inserted["tenant_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_inactive_template_skipped(self):
        mock_db = _make_db(find_one_result=None)
        tmpl_cursor = MagicMock()
        tmpl_cursor.to_list = AsyncMock(return_value=[
            _tmpl(template_type="fixed_monthly", is_active=False)
        ])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        # The find() call filters is_active=True, so only active templates are returned
        # Our mock returns empty because the find() filter is on the DB side
        # The cursor returns the inactive template but the engine skips nothing
        # Test: verify find() was called with is_active=True filter
        await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=14)

        find_call = mock_db.recurring_bill_templates.find.call_args[0][0]
        assert find_call.get("is_active") is True

    @pytest.mark.asyncio
    async def test_error_in_one_template_does_not_abort_others(self):
        """A broken template should not stop other templates from running."""
        mock_db = _make_db(find_one_result=None)
        tmpl_cursor = MagicMock()
        broken = _tmpl(template_type="unknown_type")
        good = _tmpl(template_type="fixed_monthly")
        tmpl_cursor.to_list = AsyncMock(return_value=[broken, good])
        mock_db.recurring_bill_templates.find.return_value = tmpl_cursor

        ids = await instantiate_recurring_bills(BUILDING_A, db=mock_db, as_of=date(2026, 4, 23), lookahead_days=14)

        # Good template should still instantiate
        assert len(ids) == 1
