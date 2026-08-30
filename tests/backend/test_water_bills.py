#!/usr/bin/env python3
"""
Backend Tests: Water Bills (unit tests — no real DB writes)

Tests for the Icon Water (ACT) bills router at:
  backend/routers/water_bills.py

All tests mock the database to avoid any real MongoDB interaction.
Pattern: patch("routers.water_bills.db") per test or class.

Run:
    cd /home/gagneet/strata-management
    backend/venv/bin/pytest tests/backend/test_water_bills.py -v
"""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import HTTPException

# ── Helpers / shared fixtures ─────────────────────────────────────────────────

WATER_DAILY_RATE = 0.6616
SEWER_DAILY_RATE = 1.6773


def _make_manager(unit_number="TH087"):
    return {
        "id": "mgr-001",
        "full_name": "Test Manager",
        "email": "manager@example.com",
        "role": "strata_manager",
        "unit_number": unit_number,
    }


def _make_owner(unit_number="TH087"):
    return {
        "id": "owner-001",
        "full_name": "Test Owner",
        "email": "owner@example.com",
        "role": "owner",
        "unit_number": unit_number,
    }


def _make_bill_doc(
        bill_id="bill-uuid-001",
        unit_number="TH087",
        quarter="Q1 2026",
        period_start="2026-01-01",
        period_end="2026-03-31",
        amount=225.50,
        status="unpaid",
        amount_paid=0.0,
        notes=None,
):
    return {
        "id": bill_id,
        "unit_number": unit_number,
        "quarter": quarter,
        "billing_period_start": period_start,
        "billing_period_end": period_end,
        "days": 89,
        "amount": amount,
        "due_date": "2026-04-15",
        "water_usage_kl": None,
        "status": status,
        "amount_paid": amount_paid,
        "payment_date": None,
        "payment_reference": None,
        "notes": notes,
        "supply_breakdown": None,
        "created_at": "2026-03-01T00:00:00+00:00",
        "created_by": "mgr-001",
        "updated_at": "2026-03-01T00:00:00+00:00",
    }


def _make_perms(can_view=True, can_manage=True):
    return MagicMock(can_view_finances=can_view, can_manage_finances=can_manage)


# ── Supply charge calculations ─────────────────────────────────────────────────

class TestSupplyChargeCalc:
    """Unit tests for the pure calculation helpers."""

    def test_days_in_q1_2026(self):
        from routers.water_bills import _days_in_range
        assert _days_in_range("2026-01-01", "2026-03-31") == 89

    def test_days_in_q2_2026(self):
        from routers.water_bills import _days_in_range
        assert _days_in_range("2026-04-01", "2026-06-30") == 90

    def test_days_same_date_is_zero(self):
        from routers.water_bills import _days_in_range
        assert _days_in_range("2026-06-01", "2026-06-01") == 0

    def test_calc_supply_q1(self):
        from routers.water_bills import _calc_supply
        result = _calc_supply("2026-01-01", "2026-03-31")
        expected_water = round(89 * WATER_DAILY_RATE, 2)
        expected_sewer = round(89 * SEWER_DAILY_RATE, 2)
        assert abs(result["water_supply_charge"] - expected_water) < 0.01
        assert abs(result["sewerage_supply_charge"] - expected_sewer) < 0.01
        assert abs(result["supply_total"] - (expected_water + expected_sewer)) < 0.01

    def test_calc_supply_with_usage(self):
        from routers.water_bills import _calc_supply
        result = _calc_supply("2026-01-01", "2026-03-31", usage_kl=42.5)
        assert result["usage_kl"] == 42.5

    def test_calc_supply_daily_rates_correct(self):
        from routers.water_bills import _calc_supply
        result = _calc_supply("2026-01-01", "2026-03-31")
        assert abs(result["water_daily_rate"] - WATER_DAILY_RATE) < 0.0001
        assert abs(result["sewer_daily_rate"] - SEWER_DAILY_RATE) < 0.0001

    def test_rate_schedule_is_the_single_source_of_the_rates(self):
        """The exported constants must be derived from the schedule, not typed twice.

        Three frontend files used to carry their own copies of these two figures.
        They now render whatever the API returns, so the schedule dict is the only
        place a price determination has to be applied — this test fails if someone
        re-introduces a second copy by editing a constant directly.
        """
        from routers.water_bills import (
            ICON_WATER_RATE_SCHEDULE,
            WATER_DAILY_RATE as served_water,
            SEWER_DAILY_RATE as served_sewer,
        )
        assert served_water == ICON_WATER_RATE_SCHEDULE["water_daily_rate"]
        assert served_sewer == ICON_WATER_RATE_SCHEDULE["sewer_daily_rate"]

    def test_rate_schedule_states_its_effective_period(self):
        """A rate without an effective period cannot be checked for staleness.

        The UI labels the figures with `schedule_label`, so these keys are part of
        the contract, not decoration — dropping one silently un-labels every rate
        on the water pages.
        """
        from routers.water_bills import ICON_WATER_RATE_SCHEDULE
        for key in ("provider", "jurisdiction", "schedule_label", "effective_from", "effective_to"):
            assert ICON_WATER_RATE_SCHEDULE.get(key), f"rate schedule is missing {key}"
        assert ICON_WATER_RATE_SCHEDULE["effective_from"] < ICON_WATER_RATE_SCHEDULE["effective_to"]

    def test_quarterly_ranges_returns_four(self):
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        assert len(ranges) == 4
        assert ranges[0]["quarter"] == "Q1"
        assert ranges[3]["quarter"] == "Q4"

    def test_quarterly_ranges_correct_dates(self):
        from routers.water_bills import _quarterly_ranges
        ranges = _quarterly_ranges(2026)
        assert ranges[0]["start"] == "2026-01-01"
        assert ranges[0]["end"] == "2026-03-31"
        assert ranges[3]["start"] == "2026-10-01"
        assert ranges[3]["end"] == "2026-12-31"


# ── build_response helper ──────────────────────────────────────────────────────

class TestBuildResponse:
    def test_balance_due_unpaid(self):
        from routers.water_bills import _build_response
        doc = _make_bill_doc(amount=225.50, status="unpaid", amount_paid=0.0)
        resp = _build_response(doc)
        assert abs(resp.balance_due - 225.50) < 0.01

    def test_balance_due_paid(self):
        from routers.water_bills import _build_response
        doc = _make_bill_doc(amount=225.50, status="paid", amount_paid=225.50)
        resp = _build_response(doc)
        assert resp.balance_due == 0.0

    def test_balance_due_partial(self):
        from routers.water_bills import _build_response
        doc = _make_bill_doc(amount=300.00, status="partial", amount_paid=100.00)
        resp = _build_response(doc)
        assert abs(resp.balance_due - 200.00) < 0.01

    def test_balance_due_never_negative(self):
        from routers.water_bills import _build_response
        # overpaid edge case
        doc = _make_bill_doc(amount=200.00, status="paid", amount_paid=250.00)
        resp = _build_response(doc)
        assert resp.balance_due == 0.0


# ── list_water_bills ───────────────────────────────────────────────────────────

class TestListWaterBills:
    @pytest.mark.asyncio
    async def test_admin_can_list_any_unit(self):
        from routers.water_bills import list_water_bills
        manager = _make_manager(unit_number="UA001")

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms(can_view=True)
            mock_db.water_bills.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=[]
            )
            result = await list_water_bills("TH087", limit=20, current_user=manager)
        assert result == []

    @pytest.mark.asyncio
    async def test_owner_can_list_own_unit(self):
        from routers.water_bills import list_water_bills
        owner = _make_owner(unit_number="TH087")
        bill = _make_bill_doc()

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms(can_view=True, can_manage=False)
            mock_db.water_bills.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=[bill]
            )
            result = await list_water_bills("TH087", limit=20, current_user=owner)
        assert len(result) == 1
        assert result[0].unit_number == "TH087"

    @pytest.mark.asyncio
    async def test_owner_cannot_list_other_unit(self):
        from routers.water_bills import list_water_bills
        owner = _make_owner(unit_number="TH087")

        with patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms(can_view=True, can_manage=False)
            with pytest.raises(HTTPException) as exc_info:
                await list_water_bills("UA001", limit=20, current_user=owner)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_view_permission_raises_403(self):
        from routers.water_bills import list_water_bills
        owner = _make_owner()

        with patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms(can_view=False)
            with pytest.raises(HTTPException) as exc_info:
                await list_water_bills("TH087", limit=20, current_user=owner)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unit_number_normalised_to_upper(self):
        from routers.water_bills import list_water_bills
        manager = _make_manager()

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            find_mock = MagicMock()
            find_mock.sort.return_value.to_list = AsyncMock(return_value=[])
            mock_db.water_bills.find.return_value = find_mock

            await list_water_bills("th087", limit=20, current_user=manager)

            # The find call should use the upper-cased unit number
            call_args = mock_db.water_bills.find.call_args
            assert call_args[0][0]["unit_number"] == "TH087"


# ── create_water_bill ──────────────────────────────────────────────────────────

class TestCreateWaterBill:
    @pytest.mark.asyncio
    async def test_creates_bill_returns_201_data(self):
        from routers.water_bills import create_water_bill
        from models.property_taxes import WaterBillCreate

        manager = _make_manager()
        data = WaterBillCreate(
            quarter="Q1 2026",
            billing_period_start="2026-01-01",
            billing_period_end="2026-03-31",
            amount=225.50,
            due_date="2026-04-15",
        )
        inserted = {}

        async def fake_insert(doc):
            inserted.update(doc)

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.units.find_one = AsyncMock(return_value={"unit_number": "TH087"})
            # No existing bill for this period
            mock_db.water_bills.find_one = AsyncMock(return_value=None)
            mock_db.water_bills.insert_one = AsyncMock(side_effect=fake_insert)
            mock_asyncio.create_task = MagicMock()

            result = await create_water_bill("TH087", data, current_user=manager)

        assert inserted["unit_number"] == "TH087"
        assert inserted["quarter"] == "Q1 2026"
        assert abs(inserted["amount"] - 225.50) < 0.01
        assert inserted["status"] == "unpaid"
        assert inserted["amount_paid"] == 0.0
        assert result.balance_due == pytest.approx(225.50, abs=0.01)

    @pytest.mark.asyncio
    async def test_duplicate_period_returns_409(self):
        """Creating a bill for an already-existing (unit, period) must return 409."""
        from routers.water_bills import create_water_bill
        from models.property_taxes import WaterBillCreate

        manager = _make_manager()
        data = WaterBillCreate(
            quarter="Q1 2026",
            billing_period_start="2026-01-01",
            billing_period_end="2026-03-31",
            amount=225.50,
            due_date="2026-04-15",
        )
        existing = _make_bill_doc(status="paid", amount_paid=225.50)

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            mock_db.units.find_one = AsyncMock(return_value={"unit_number": "TH087"})
            mock_db.water_bills.find_one = AsyncMock(return_value=existing)

            with pytest.raises(HTTPException) as exc_info:
                await create_water_bill("TH087", data, current_user=manager)

        assert exc_info.value.status_code == 409
        assert "already exists" in exc_info.value.detail
        assert existing["id"] in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_idempotency_same_request_twice_one_record(self):
        """Calling create twice for the same period results in only one insert."""
        from routers.water_bills import create_water_bill
        from models.property_taxes import WaterBillCreate

        manager = _make_manager()
        data = WaterBillCreate(
            quarter="Q1 2026",
            billing_period_start="2026-01-01",
            billing_period_end="2026-03-31",
            amount=225.50,
            due_date="2026-04-15",
        )

        inserted_docs = []

        async def fake_insert(doc):
            inserted_docs.append(dict(doc))

        # First call: no existing bill → inserts
        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.units.find_one = AsyncMock(return_value={"unit_number": "TH087"})
            mock_db.water_bills.find_one = AsyncMock(return_value=None)
            mock_db.water_bills.insert_one = AsyncMock(side_effect=fake_insert)
            mock_asyncio.create_task = MagicMock()

            first_result = await create_water_bill("TH087", data, current_user=manager)

        assert len(inserted_docs) == 1

        # Second call: existing bill found → 409, no second insert
        existing_bill = inserted_docs[0]
        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            mock_db.units.find_one = AsyncMock(return_value={"unit_number": "TH087"})
            mock_db.water_bills.find_one = AsyncMock(return_value={
                "id": existing_bill["id"],
                "status": existing_bill["status"],
            })
            mock_db.water_bills.insert_one = AsyncMock(side_effect=fake_insert)

            with pytest.raises(HTTPException) as exc_info:
                await create_water_bill("TH087", data, current_user=manager)

        assert exc_info.value.status_code == 409
        # insert_one was never called a second time
        assert len(inserted_docs) == 1

    @pytest.mark.asyncio
    async def test_no_manage_permission_raises_403(self):
        from routers.water_bills import create_water_bill
        from models.property_taxes import WaterBillCreate

        owner = _make_owner()
        data = WaterBillCreate(
            quarter="Q1 2026",
            billing_period_start="2026-01-01",
            billing_period_end="2026-03-31",
            amount=225.50,
            due_date="2026-04-15",
        )

        with patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms(can_manage=False)
            with pytest.raises(HTTPException) as exc_info:
                await create_water_bill("TH087", data, current_user=owner)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_unit_raises_404(self):
        from routers.water_bills import create_water_bill
        from models.property_taxes import WaterBillCreate

        manager = _make_manager()
        data = WaterBillCreate(
            quarter="Q1 2026",
            billing_period_start="2026-01-01",
            billing_period_end="2026-03-31",
            amount=225.50,
            due_date="2026-04-15",
        )

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            mock_db.units.find_one = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await create_water_bill("XX999", data, current_user=manager)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_supply_breakdown_computed_from_period(self):
        from routers.water_bills import create_water_bill
        from models.property_taxes import WaterBillCreate

        manager = _make_manager()
        data = WaterBillCreate(
            quarter="Q1 2026",
            billing_period_start="2026-01-01",
            billing_period_end="2026-03-31",
            amount=225.50,
            due_date="2026-04-15",
            water_usage_kl=42.5,
        )
        inserted = {}

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.units.find_one = AsyncMock(return_value={"unit_number": "TH087"})
            mock_db.water_bills.find_one = AsyncMock(return_value=None)
            mock_db.water_bills.insert_one = AsyncMock(side_effect=lambda d: inserted.update(d))
            mock_asyncio.create_task = MagicMock()

            await create_water_bill("TH087", data, current_user=manager)

        assert inserted.get("supply_breakdown") is not None
        assert inserted["supply_breakdown"]["days"] == 89
        assert inserted["days"] == 89

    @pytest.mark.asyncio
    async def test_duplicate_check_uses_billing_period_fields(self):
        """The idempotency query uses billing_period_start and billing_period_end."""
        from routers.water_bills import create_water_bill
        from models.property_taxes import WaterBillCreate

        manager = _make_manager()
        data = WaterBillCreate(
            quarter="Q1 2026",
            billing_period_start="2026-01-01",
            billing_period_end="2026-03-31",
            amount=225.50,
            due_date="2026-04-15",
        )
        find_queries = []

        async def capture_find_one(query, *args, **kwargs):
            find_queries.append(query)
            return None  # no existing bill

        inserted = {}

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.units.find_one = AsyncMock(return_value={"unit_number": "TH087"})
            mock_db.water_bills.find_one = AsyncMock(side_effect=capture_find_one)
            mock_db.water_bills.insert_one = AsyncMock(side_effect=lambda d: inserted.update(d))
            mock_asyncio.create_task = MagicMock()

            await create_water_bill("TH087", data, current_user=manager)

        # The duplicate-check query should contain unit_number and both period fields
        assert len(find_queries) == 1
        dup_query = find_queries[0]
        assert dup_query["unit_number"] == "TH087"
        assert dup_query["billing_period_start"] == "2026-01-01"
        assert dup_query["billing_period_end"] == "2026-03-31"


# ── mark_water_bill_paid ───────────────────────────────────────────────────────

class TestMarkPaid:
    @pytest.mark.asyncio
    async def test_manager_can_mark_paid(self):
        from routers.water_bills import mark_water_bill_paid
        from models.property_taxes import WaterBillMarkPaidRequest

        manager = _make_manager()
        bill = _make_bill_doc(status="unpaid", amount=225.50, amount_paid=0.0)
        data = WaterBillMarkPaidRequest(
            payment_date="2026-03-28",
            payment_reference="REF-001",
        )

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=bill)
            mock_db.water_bills.update_one = AsyncMock()
            mock_asyncio.create_task = MagicMock()

            result = await mark_water_bill_paid("bill-uuid-001", data, current_user=manager)

        assert result.status == "paid"
        assert abs(result.amount_paid - 225.50) < 0.01
        assert result.balance_due == 0.0

    @pytest.mark.asyncio
    async def test_mark_paid_missing_bill_raises_404(self):
        from routers.water_bills import mark_water_bill_paid
        from models.property_taxes import WaterBillMarkPaidRequest

        manager = _make_manager()
        data = WaterBillMarkPaidRequest(payment_date="2026-03-28")

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await mark_water_bill_paid("nonexistent-id", data, current_user=manager)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_owner_cannot_mark_paid_other_unit(self):
        from routers.water_bills import mark_water_bill_paid
        from models.property_taxes import WaterBillMarkPaidRequest

        owner = _make_owner(unit_number="TH087")
        # Bill belongs to UA001
        bill = _make_bill_doc(unit_number="UA001", status="unpaid")
        data = WaterBillMarkPaidRequest(payment_date="2026-03-28")

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms(can_manage=False)
            mock_db.water_bills.find_one = AsyncMock(return_value=bill)

            with pytest.raises(HTTPException) as exc_info:
                await mark_water_bill_paid("bill-uuid-001", data, current_user=owner)
        assert exc_info.value.status_code == 403


# ── mark_water_bill_partial ────────────────────────────────────────────────────

class TestMarkPartial:
    @pytest.mark.asyncio
    async def test_partial_payment_sets_partial_status(self):
        from routers.water_bills import mark_water_bill_partial
        from models.property_taxes import WaterBillMarkPartialRequest

        manager = _make_manager()
        bill = _make_bill_doc(amount=300.00, status="unpaid", amount_paid=0.0)
        data = WaterBillMarkPartialRequest(
            amount_paid=100.00,
            payment_date="2026-05-01",
            notes="First instalment",
        )

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=bill)
            mock_db.water_bills.update_one = AsyncMock()
            mock_asyncio.create_task = MagicMock()

            result = await mark_water_bill_partial("bill-uuid-001", data, current_user=manager)

        assert result.status == "partial"
        assert abs(result.amount_paid - 100.00) < 0.01
        assert abs(result.balance_due - 200.00) < 0.01

    @pytest.mark.asyncio
    async def test_partial_auto_promotes_to_paid_when_full(self):
        from routers.water_bills import mark_water_bill_partial
        from models.property_taxes import WaterBillMarkPartialRequest

        manager = _make_manager()
        bill = _make_bill_doc(
            amount=300.00, status="partial", amount_paid=150.00,
            notes="[2026-05-01] Partial $150.00: First payment"
        )
        data = WaterBillMarkPartialRequest(
            amount_paid=150.00,
            payment_date="2026-06-01",
            notes="Final payment",
        )

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=bill)
            mock_db.water_bills.update_one = AsyncMock()
            mock_asyncio.create_task = MagicMock()

            result = await mark_water_bill_partial("bill-uuid-001", data, current_user=manager)

        assert result.status == "paid"
        assert result.balance_due == 0.0
        assert abs(result.amount_paid - 300.00) < 0.01

    @pytest.mark.asyncio
    async def test_partial_note_appended(self):
        from routers.water_bills import mark_water_bill_partial
        from models.property_taxes import WaterBillMarkPartialRequest

        manager = _make_manager()
        bill = _make_bill_doc(
            amount=300.00, status="partial", amount_paid=100.00,
            notes="[2026-05-01] Partial $100.00: First instalment"
        )
        data = WaterBillMarkPartialRequest(
            amount_paid=50.00,
            payment_date="2026-05-15",
            notes="Second instalment",
        )
        captured_update = {}

        async def fake_update_one(query, update_doc):
            captured_update.update(update_doc.get("$set", {}))

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=bill)
            mock_db.water_bills.update_one = AsyncMock(side_effect=fake_update_one)
            mock_asyncio.create_task = MagicMock()

            await mark_water_bill_partial("bill-uuid-001", data, current_user=manager)

        combined_notes = captured_update.get("notes", "")
        assert "First instalment" in combined_notes
        assert "Second instalment" in combined_notes

    @pytest.mark.asyncio
    async def test_overpayment_rejected(self):
        from routers.water_bills import mark_water_bill_partial
        from models.property_taxes import WaterBillMarkPartialRequest

        manager = _make_manager()
        bill = _make_bill_doc(amount=100.00, status="unpaid", amount_paid=0.0)
        data = WaterBillMarkPartialRequest(
            amount_paid=150.00,  # exceeds total
            payment_date="2026-03-01",
        )

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=bill)

            with pytest.raises(HTTPException) as exc_info:
                await mark_water_bill_partial("bill-uuid-001", data, current_user=manager)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_already_paid_bill_rejected(self):
        from routers.water_bills import mark_water_bill_partial
        from models.property_taxes import WaterBillMarkPartialRequest

        manager = _make_manager()
        bill = _make_bill_doc(amount=225.50, status="paid", amount_paid=225.50)
        data = WaterBillMarkPartialRequest(
            amount_paid=10.00,
            payment_date="2026-03-28",
        )

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=bill)

            with pytest.raises(HTTPException) as exc_info:
                await mark_water_bill_partial("bill-uuid-001", data, current_user=manager)
        assert exc_info.value.status_code == 400


# ── delete_water_bill ──────────────────────────────────────────────────────────

class TestDeleteWaterBill:
    @pytest.mark.asyncio
    async def test_super_admin_can_delete(self):
        from routers.water_bills import delete_water_bill

        super_admin = {
            "id": "sa-001",
            "full_name": "Super Admin",
            "role": "super_admin",
            "unit_number": None,
        }
        bill = _make_bill_doc()

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms, \
                patch("routers.water_bills.asyncio") as mock_asyncio:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=bill)
            mock_db.water_bills.delete_one = AsyncMock()
            mock_asyncio.create_task = MagicMock()

            # Should return None (204 No Content)
            result = await delete_water_bill("bill-uuid-001", current_user=super_admin)
        assert result is None

    @pytest.mark.asyncio
    async def test_owner_cannot_delete(self):
        from routers.water_bills import delete_water_bill

        owner = _make_owner()

        with patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            with pytest.raises(HTTPException) as exc_info:
                await delete_water_bill("bill-uuid-001", current_user=owner)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_missing_bill_raises_404(self):
        from routers.water_bills import delete_water_bill

        super_admin = {
            "id": "sa-001",
            "full_name": "Super Admin",
            "role": "super_admin",
            "unit_number": None,
        }

        with patch("routers.water_bills.db") as mock_db, \
                patch("routers.water_bills.get_user_permissions") as mock_perms:
            mock_perms.return_value = _make_perms()
            mock_db.water_bills.find_one = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await delete_water_bill("nonexistent-id", current_user=super_admin)
        assert exc_info.value.status_code == 404
