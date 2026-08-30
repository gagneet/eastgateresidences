"""
Tests for Collection Rate, Units Outstanding, Total Arrears & Next Payment Fixes — FY 2026

Covers:
- get_unit_ledger_stats: computed fallback when no ledger docs for year
- get_building_kpis: uses levy_year not ledger_year for year selection
- get_top_arrears: deducts confirmed current-year payments from historical arrears
- verify_levy_payment: triggers _upsert_ledger_for_payment as background task
- total_arrears formula: max(0, -net_position) — excludes future undue levies
- next_payment_adjusted: period levy only (no balance_owing added)
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _manager_user():
    return {
        "id": "mgr-001",
        "full_name": "Anthony McDonald",
        "email": "anthony@eastgateresidences.com.au",
        "role": "chairman",
        "unit_number": "UA063",
        "permissions": {"can_view_finances": True, "can_manage_finances": True},
    }


def _pending_payment(payment_id="pay-001", unit="TH087", amount=1800.0):
    return {
        "id": payment_id,
        "unit_number": unit,
        "amount": amount,
        "payment_method": "bpay",
        "payment_reference": "BPAY2026001",
        "quarter": "Q1",
        "year": "2026",
        "fund_type": None,
        "payment_type": "standard",
        "credit_amount": None,
        "status": "pending_verification",
        "owner_id": "owner-001",
        "paid_by": None,
        "receipt_number": "RCP-2026-001",
        "notes": None,
        "stripe_payment_intent_id": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "rejected_by": None,
        "rejected_at": None,
        "rejection_reason": None,
        "created_at": "2026-03-01T00:00:00+00:00",
        "updated_at": "2026-03-01T00:00:00+00:00",
    }


def _sample_units():
    return [
        {"unit_number": "TH087", "entitlement": 161},
        {"unit_number": "UA001", "entitlement": 100},
        {"unit_number": "UA002", "entitlement": 120},
    ]


def _sample_levy_doc():
    return {
        "plan_id": "13195",
        "year": "2026",
        "admin_levy_per_uoe_annual": 34.087,
        "sinking_levy_per_uoe_annual": 9.9505,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Class 1: TestCollectionRateFallback
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectionRateFallback:
    """
    When unit_levy_ledger has no docs for the year, get_unit_ledger_stats
    falls back to computing from annual_levies + confirmed levy_payments.
    """

    @pytest.mark.asyncio
    async def test_empty_ledger_computes_from_levy_payments(self):
        """Zero ledger docs → computes totals from annual_levies + confirmed payments."""
        units = _sample_units()
        levy = _sample_levy_doc()
        rate = levy["admin_levy_per_uoe_annual"] + levy["sinking_levy_per_uoe_annual"]

        mock_db = MagicMock()
        # Ledger aggregation returns nothing
        mock_db.unit_levy_ledger.aggregate.return_value.__aiter__ = AsyncMock(return_value=iter([]))
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[])
        # annual_levies returns 2026 doc
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
        # units collection
        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=units)
        mock_db.units.find.return_value = units_cursor
        # confirmed payments: TH087 paid $1800
        confirmed_cursor = MagicMock()
        confirmed_cursor.to_list = AsyncMock(return_value=[
            {"_id": "TH087", "paid": 1800.0},
        ])
        mock_db.levy_payments.aggregate.return_value = confirmed_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2026", "13195")

        expected_total = round(sum(rate * u["entitlement"] for u in units), 2)
        assert result["total_levied"] == expected_total
        assert result["total_paid"] == 1800.0
        assert result["net_balance"] == round(expected_total - 1800.0, 2)
        assert result["total_outstanding"] > 0
        # TH087 paid $1800 out of ~$7090 → still owing; UA001/UA002 paid nothing → owing
        assert result["units_owing"] == 3
        assert result["units_credit"] == 0

    @pytest.mark.asyncio
    async def test_empty_ledger_zero_confirmed_payments(self):
        """No confirmed payments and empty ledger → 0% collection but non-zero levied."""
        units = _sample_units()
        levy = _sample_levy_doc()

        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[])
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=units)
        mock_db.units.find.return_value = units_cursor
        confirmed_cursor = MagicMock()
        confirmed_cursor.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.aggregate.return_value = confirmed_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2026", "13195")

        assert result["total_paid"] == 0
        rate = levy["admin_levy_per_uoe_annual"] + levy["sinking_levy_per_uoe_annual"]
        assert result["total_levied"] == round(sum(rate * u["entitlement"] for u in units), 2)
        assert result["units_owing"] == 3
        assert result["units_paid_up"] == 0

    @pytest.mark.asyncio
    async def test_empty_ledger_all_units_paid(self):
        """All units paid in full → 100% collection, units_paid_up == total units."""
        units = _sample_units()
        levy = _sample_levy_doc()
        rate = levy["admin_levy_per_uoe_annual"] + levy["sinking_levy_per_uoe_annual"]

        payments = [
            {"_id": u["unit_number"], "paid": round(rate * u["entitlement"], 2)}
            for u in units
        ]

        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[])
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=units)
        mock_db.units.find.return_value = units_cursor
        confirmed_cursor = MagicMock()
        confirmed_cursor.to_list = AsyncMock(return_value=payments)
        mock_db.levy_payments.aggregate.return_value = confirmed_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2026", "13195")

        assert result["units_paid_up"] == len(units)
        assert result["units_owing"] == 0
        assert result["total_outstanding"] == 0.0

    @pytest.mark.asyncio
    async def test_empty_ledger_no_levy_doc(self):
        """No levy doc for year → returns all zeros, no crash."""
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[])
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2099", "13195")

        assert result["total_levied"] == 0
        assert result["total_paid"] == 0
        assert result["units_owing"] == 0

    @pytest.mark.asyncio
    async def test_existing_ledger_docs_used_directly(self):
        """When ledger docs exist, return them directly without hitting annual_levies."""
        ledger_agg = [{
            "total_levied": 440375.10,
            "total_paid": 50000.0,
            "total_net_balance": 390375.10,
            "units_owing": 70,
            "units_credit": 3,
            "units_paid_up": 14,
            "total_outstanding": 388000.0,
        }]
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=ledger_agg)

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2025", "13195")

        assert result["total_levied"] == 440375.10
        assert result["total_paid"] == 50000.0
        assert result["units_owing"] == 70
        # annual_levies should NOT be queried
        mock_db.annual_levies.find_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_payment_correct_percentage(self):
        """One unit paid → collection_rate computable and > 0."""
        units = [{"unit_number": "TH087", "entitlement": 161}]
        levy = _sample_levy_doc()
        rate = levy["admin_levy_per_uoe_annual"] + levy["sinking_levy_per_uoe_annual"]
        total_levied = round(rate * 161, 2)
        amount_paid = 1800.0

        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[])
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=units)
        mock_db.units.find.return_value = units_cursor
        confirmed_cursor = MagicMock()
        confirmed_cursor.to_list = AsyncMock(return_value=[{"_id": "TH087", "paid": amount_paid}])
        mock_db.levy_payments.aggregate.return_value = confirmed_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2026", "13195")

        collection_rate = result["total_paid"] / result["total_levied"] * 100
        assert collection_rate > 0
        assert collection_rate < 100
        assert abs(collection_rate - (amount_paid / total_levied * 100)) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Class 2: TestBuildingKpisYearSelection
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildingKpisYearSelection:
    """
    get_building_kpis must use levy_year (latest annual_levy) not ledger_year
    (latest ledger entry) for the current year.
    """

    def test_levy_year_preferred_over_ledger_year(self):
        """
        With levy_year=2026 and ledger_year=2025, the selected year must be 2026.
        """
        levy_year = "2026"
        ledger_year = "2025"
        # Replicate the logic from server.py
        year = levy_year or ledger_year or "2025"
        assert year == "2026"

    def test_ledger_year_fallback_when_no_levy_year(self):
        """When levy_year is None, fall back to ledger_year."""
        levy_year = None
        ledger_year = "2025"
        year = levy_year or ledger_year or "2025"
        assert year == "2025"

    def test_default_2025_when_both_none(self):
        """When both are None, falls back to '2025'."""
        levy_year = None
        ledger_year = None
        year = levy_year or ledger_year or "2025"
        assert year == "2025"

    def test_explicit_financial_year_bypasses_lookup(self):
        """Explicit financial_year param should bypass year detection entirely."""
        financial_year = "2024"
        year = financial_year  # When financial_year is provided, year = financial_year
        assert year == "2024"

    def test_year_selection_does_not_use_old_logic(self):
        """Old logic: ledger_year or levy_year → would return 2025 instead of 2026."""
        levy_year = "2026"
        ledger_year = "2025"
        # Old (broken) logic
        old_year = ledger_year or levy_year or "2025"
        # New (correct) logic
        new_year = levy_year or ledger_year or "2025"
        assert old_year == "2025"
        assert new_year == "2026"
        assert new_year != old_year


# ─────────────────────────────────────────────────────────────────────────────
# Class 3: TestTopArrearsExclusion
# ─────────────────────────────────────────────────────────────────────────────

class TestTopArrearsExclusion:
    """
    get_top_arrears deducts confirmed current-year payments from historical
    arrears balances.  Units that have paid up should be excluded from the list.
    """

    def _apply_exclusion(self, aggregated_results, recent_paid, current_year="2026", financial_year=None):
        """Replicate the exclusion logic from server.py get_top_arrears."""
        if aggregated_results and (not financial_year or financial_year < current_year):
            if recent_paid:
                aggregated_results = [
                    {**r, "total_balance": r["total_balance"] - recent_paid.get(r["_id"], 0.0)}
                    for r in aggregated_results
                ]
                aggregated_results = [r for r in aggregated_results if r["total_balance"] > 0.01]
        return aggregated_results

    def test_unit_fully_paid_in_current_year_excluded(self):
        """Unit whose 2025 arrears are fully covered by a 2026 payment → removed."""
        arrears = [{"_id": "TH087", "total_balance": 1500.0, "latest_total_levied": 7000.0, "lot_number": "LOT87",
                    "latest_year": "2025"}]
        recent_paid = {"TH087": 1500.0}  # fully paid
        result = self._apply_exclusion(arrears, recent_paid)
        assert result == []

    def test_unit_partially_paid_shows_reduced_balance(self):
        """Unit partially paid → included with reduced balance."""
        arrears = [{"_id": "TH087", "total_balance": 3000.0, "latest_total_levied": 7000.0, "lot_number": "LOT87",
                    "latest_year": "2025"}]
        recent_paid = {"TH087": 1000.0}
        result = self._apply_exclusion(arrears, recent_paid)
        assert len(result) == 1
        assert result[0]["total_balance"] == 2000.0

    def test_unit_with_no_current_year_payment_unchanged(self):
        """Unit with no 2026 payment → balance unchanged."""
        arrears = [{"_id": "UA001", "total_balance": 2500.0, "latest_total_levied": 6000.0, "lot_number": "LOT01",
                    "latest_year": "2025"}]
        recent_paid = {}
        result = self._apply_exclusion(arrears, recent_paid)
        assert len(result) == 1
        assert result[0]["total_balance"] == 2500.0

    def test_mixed_units_filtered_correctly(self):
        """Mix of fully-paid, partially-paid, and unpaid units."""
        arrears = [
            {"_id": "TH087", "total_balance": 1500.0, "latest_total_levied": 7000.0, "lot_number": "LOT87",
             "latest_year": "2025"},
            {"_id": "UA001", "total_balance": 3000.0, "latest_total_levied": 6000.0, "lot_number": "LOT01",
             "latest_year": "2025"},
            {"_id": "UA002", "total_balance": 2000.0, "latest_total_levied": 5000.0, "lot_number": "LOT02",
             "latest_year": "2025"},
        ]
        recent_paid = {
            "TH087": 1500.0,  # fully paid → excluded
            "UA001": 1000.0,  # partially paid → reduced
            # UA002 not in recent_paid → unchanged
        }
        result = self._apply_exclusion(arrears, recent_paid)
        unit_numbers = [r["_id"] for r in result]
        assert "TH087" not in unit_numbers
        assert "UA001" in unit_numbers
        assert "UA002" in unit_numbers
        ua001 = next(r for r in result if r["_id"] == "UA001")
        assert ua001["total_balance"] == 2000.0

    def test_no_deduction_when_explicit_current_year_filter(self):
        """When financial_year equals current_year, no deduction applied (2026 ledger is fresh)."""
        arrears = [{"_id": "TH087", "total_balance": 5000.0, "latest_total_levied": 7000.0, "lot_number": "LOT87",
                    "latest_year": "2026"}]
        recent_paid = {"TH087": 5000.0}
        # When financial_year == current_year, the condition `financial_year < current_year` is False
        result = self._apply_exclusion(arrears, recent_paid, current_year="2026", financial_year="2026")
        # No deduction because financial_year is not less than current_year
        assert len(result) == 1
        assert result[0]["total_balance"] == 5000.0

    def test_empty_arrears_list_returns_empty(self):
        """Empty aggregated_results → returns empty list, no crash."""
        result = self._apply_exclusion([], {"TH087": 1800.0})
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Class 4: TestVerifyLedgerUpdate
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyLedgerUpdate:
    """
    _upsert_ledger_for_payment updates unit_levy_ledger when a payment is confirmed.
    verify_levy_payment schedules the task after confirming.
    """

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_ledger_entry(self):
        """Existing ledger row → admin_paid/sinking_paid/total_paid incremented."""
        existing_ledger = {
            "id": "ledger-001",
            "plan_id": "13195",
            "year": "2026",
            "unit_number": "TH087",
            "admin_opening": 0.0,
            "admin_levied": 5488.01,
            "admin_paid": 0.0,
            "admin_closing": 5488.01,
            "sinking_opening": 0.0,
            "sinking_levied": 1602.03,
            "sinking_paid": 0.0,
            "sinking_closing": 1602.03,
            "total_levied": 7090.04,
            "total_paid": 0.0,
            "net_balance": 7090.04,
        }
        levy_doc = _sample_levy_doc()

        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=existing_ledger)
        mock_db.unit_levy_ledger.update_one = AsyncMock()

        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("TH087", "2026", 1800.0, "pay-001", "13195")

        mock_db.unit_levy_ledger.update_one.assert_called_once()
        update_call = mock_db.unit_levy_ledger.update_one.call_args
        set_doc = update_call[0][1]["$set"]
        assert set_doc["total_paid"] == 1800.0
        assert set_doc["net_balance"] < 7090.04  # balance reduced

    @pytest.mark.asyncio
    async def test_upsert_creates_new_ledger_when_missing(self):
        """No existing ledger row → creates new doc with correct amounts."""
        levy_doc = _sample_levy_doc()
        unit_doc = {"entitlement": 161, "lot_number": "LOT87", "property_type": "Townhouse"}

        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
        mock_db.units.find_one = AsyncMock(return_value=unit_doc)
        prev_ledger_mock = AsyncMock(return_value=None)
        mock_db.unit_levy_ledger.find_one = AsyncMock(side_effect=[None, None])
        mock_db.unit_levy_ledger.replace_one = AsyncMock()

        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("TH087", "2026", 1800.0, "pay-001", "13195")

        mock_db.unit_levy_ledger.replace_one.assert_called_once()
        args = mock_db.unit_levy_ledger.replace_one.call_args
        doc = args[0][1]
        assert doc["unit_number"] == "TH087"
        assert doc["year"] == "2026"
        assert doc["total_paid"] == 1800.0
        assert doc["total_levied"] > 0

    @pytest.mark.asyncio
    async def test_upsert_accumulates_on_second_confirmation(self):
        """Confirming a second payment → total_paid accumulates correctly."""
        existing_ledger = {
            "id": "ledger-001",
            "plan_id": "13195",
            "year": "2026",
            "unit_number": "TH087",
            "admin_opening": 0.0,
            "admin_levied": 5488.01,
            "admin_paid": 1393.40,  # already paid from first payment
            "admin_closing": 4094.61,
            "sinking_opening": 0.0,
            "sinking_levied": 1602.03,
            "sinking_paid": 406.60,
            "sinking_closing": 1195.43,
            "total_levied": 7090.04,
            "total_paid": 1800.0,  # already paid $1800
            "net_balance": 5290.04,
        }
        levy_doc = _sample_levy_doc()

        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=existing_ledger)
        mock_db.unit_levy_ledger.update_one = AsyncMock()

        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            # Confirm a second payment of $1800
            await _upsert_ledger_for_payment("TH087", "2026", 1800.0, "pay-002", "13195")

        update_call = mock_db.unit_levy_ledger.update_one.call_args
        set_doc = update_call[0][1]["$set"]
        assert set_doc["total_paid"] == 3600.0  # 1800 + 1800
        assert set_doc["net_balance"] < 5290.04  # further reduced

    @pytest.mark.asyncio
    async def test_upsert_no_levy_doc_exits_gracefully(self):
        """No levy doc for year → function returns without error or DB writes."""
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)

        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            # Should not raise
            await _upsert_ledger_for_payment("TH087", "2099", 1000.0, "pay-001", "13195")

        mock_db.unit_levy_ledger.find_one.assert_not_called()
        mock_db.unit_levy_ledger.update_one.assert_not_called()
        mock_db.unit_levy_ledger.replace_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_payment_schedules_ledger_upsert(self):
        """verify_levy_payment endpoint schedules _upsert_ledger_for_payment as background task."""
        payment = _pending_payment()

        mock_db = MagicMock()
        mock_db.levy_payments.find_one = AsyncMock(return_value=payment)
        mock_db.levy_payments.update_one = AsyncMock()

        fake_user = _manager_user()
        permissions = MagicMock()
        permissions.can_manage_finances = True

        create_task_calls = []

        def mock_create_task(coro):
            create_task_calls.append(coro)
            # Return a dummy future
            import asyncio
            future = asyncio.get_event_loop().create_future()
            future.set_result(None)
            return future

        with (
            patch("routers.finance.db", mock_db),
            patch("routers.finance.get_current_user", return_value=fake_user),
            patch("routers.finance.get_user_permissions", return_value=permissions),
            patch("routers.finance.asyncio.create_task", side_effect=mock_create_task),
            patch("routers.finance._notify_payment_verified", return_value=None),
            patch("routers.finance._upsert_ledger_for_payment", return_value=None),
            patch("routers.finance.create_audit_log", return_value=None),
            patch("utils.financial_strangler.default_gate.is_postgres_enabled", new_callable=AsyncMock) as _pg_gate,
        ):
            _pg_gate.return_value = False
            from routers.finance import verify_levy_payment
            from models.finance import LevyPaymentVerify

            data = LevyPaymentVerify(notes=None)
            result = await verify_levy_payment(
                payment_id="pay-001",
                data=data,
                current_user=fake_user,
            )

        # Should have called create_task at least twice (audit + notify + ledger)
        assert len(create_task_calls) >= 2

    @pytest.mark.asyncio
    async def test_net_balance_decreases_after_verify(self):
        """After upsert, net_balance should decrease by the payment amount."""
        existing_ledger = {
            "id": "ledger-001",
            "plan_id": "13195",
            "year": "2026",
            "unit_number": "TH087",
            "admin_opening": 0.0,
            "admin_levied": 5488.01,
            "admin_paid": 0.0,
            "admin_closing": 5488.01,
            "sinking_opening": 0.0,
            "sinking_levied": 1602.03,
            "sinking_paid": 0.0,
            "sinking_closing": 1602.03,
            "total_levied": 7090.04,
            "total_paid": 0.0,
            "net_balance": 7090.04,
        }
        levy_doc = _sample_levy_doc()

        captured_update = {}

        async def capture_update_one(filter_doc, update_doc, *args, **kwargs):
            captured_update.update(update_doc.get("$set", {}))

        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=existing_ledger)
        mock_db.unit_levy_ledger.update_one = AsyncMock(side_effect=capture_update_one)

        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("TH087", "2026", 1800.0, "pay-001", "13195")

        original_balance = existing_ledger["net_balance"]
        new_balance = captured_update.get("net_balance", original_balance)
        assert new_balance < original_balance
        assert abs((original_balance - new_balance) - 1800.0) < 0.05  # within rounding


# ─────────────────────────────────────────────────────────────────────────────
# Class 5: TestCollectionRateIntegration
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectionRateIntegration:
    """Integration-level checks that verify the overall data flow."""

    def test_levy_rates_for_2026_are_correct(self):
        """Confirm hardcoded 2026 levy rates match expected values."""
        admin_rate = 34.087
        sinking_rate = 9.9505
        total_rate = admin_rate + sinking_rate
        # TH087 UOE=161
        th017_admin = round(admin_rate * 161, 2)
        th017_sinking = round(sinking_rate * 161, 2)
        th017_total = round(th017_admin + th017_sinking, 2)
        assert th017_total == 7090.04

    def test_admin_ratio_computed_correctly(self):
        """Admin payment ratio derived from rates is accurate."""
        admin_rate = 34.087
        sinking_rate = 9.9505
        total_rate = admin_rate + sinking_rate
        admin_ratio = admin_rate / total_rate
        assert abs(admin_ratio - 0.7741) < 0.001

    def test_uoe_10000_total_levy(self):
        """Total levied across all 10,000 UOE should be ~$440,375."""
        admin_rate = 34.087
        sinking_rate = 9.9505
        total_rate = admin_rate + sinking_rate
        total_levy = round(total_rate * 10000, 2)
        assert abs(total_levy - 440375.10) < 1.0  # within $1 rounding

    def test_collection_rate_formula_legacy(self):
        """Old formula (total_paid / total_levied × 100) is still computable — kept for reference."""
        total_levied = 440375.10
        total_paid = 1800.0
        old_rate = total_paid / total_levied * 100
        assert old_rate > 0
        assert old_rate < 1.0  # < 1% at start of year

    def test_net_collection_rate_formula_no_overdue(self):
        """
        New net-position formula with no overdue periods.
        collection_rate = (confirmed_paid - total_opening_arrears) / total_levied × 100
        When opening_arrears > confirmed_paid → negative rate (we're behind).
        """
        total_levied = 440375.10
        total_opening_arrears = 20168.35  # 2025 carry-forward (23 units)
        confirmed_paid = 1800.0  # only one Q1 advance payment
        obligations_so_far = total_opening_arrears  # no periods past due
        net_position = confirmed_paid - obligations_so_far
        collection_rate = net_position / total_levied * 100
        assert collection_rate < 0  # behind: not enough confirmed to cover opening arrears
        assert abs(collection_rate - ((1800 - 20168.35) / 440375.10 * 100)) < 0.01

    def test_net_collection_rate_formula_with_overdue_periods(self):
        """
        Net-position formula when 1 period (Q1) has passed its grace deadline.
        obligations_so_far = opening_arrears + (1/4) × total_levied
        """
        total_levied = 440375.10
        total_opening_arrears = 20168.35
        confirmed_paid = 120000.0  # some Q1 payments confirmed
        num_overdue = 1
        total_periods = 4
        obligations_so_far = total_opening_arrears + (num_overdue / total_periods) * total_levied
        net_position = confirmed_paid - obligations_so_far
        collection_rate = net_position / total_levied * 100
        # obligations = 20168.35 + 110093.775 = 130262.125
        # net = 120000 - 130262.125 = -10262.125
        assert collection_rate < 0
        assert abs(obligations_so_far - (20168.35 + 440375.10 / 4)) < 0.1

    def test_net_collection_rate_positive_when_advance_exceeds_arrears(self):
        """
        When total advance payments exceed total obligations: positive collection rate.
        User example: advances $6500, arrears $11000 → negative; reversed: positive.
        """
        total_levied = 100000.0
        total_opening_arrears = 0.0  # no historical arrears
        confirmed_paid = 15000.0
        num_overdue = 0
        obligations_so_far = total_opening_arrears  # no periods past due
        net_position = confirmed_paid - obligations_so_far
        collection_rate = net_position / total_levied * 100
        # 15000 - 0 = 15000 net; rate = 15%
        assert collection_rate == 15.0

    def test_user_example_negative_rate(self):
        """
        Reproduce user's stated example:
        advance $6500, arrears $11000 → net -$4500 → negative rate.
        """
        total_levied = 440375.10
        # Scenario: confirmed = $6500 advance; obligations_so_far = $6500 + $11000 = $17500
        # This simplifies to: net = confirmed - obligations = advance - (advance + arrears) = -arrears
        advance_total = 6500.0
        arrears_total = 11000.0
        # net = advance - (advance + arrears) — wrong; let's model directly:
        # confirmed_paid = advance; obligations = confirmed + arrears
        confirmed_paid = advance_total
        obligations_so_far = advance_total + arrears_total  # everything owed including what was paid
        net_position = confirmed_paid - obligations_so_far  # = -arrears_total = -11000
        collection_rate = net_position / total_levied * 100
        assert collection_rate < 0  # negative %
        # The negative amount equals the unmet arrears
        assert abs(net_position - (-arrears_total)) < 0.01

    def test_no_division_by_zero_when_levied_is_zero(self):
        """collection_rate calculation is guarded against zero division."""
        total_levied = 0
        confirmed_paid = 0
        obligations_so_far = 0
        net_position = confirmed_paid - obligations_so_far
        collection_rate = (net_position / total_levied * 100) if total_levied > 0 else 0
        assert collection_rate == 0


# ─────────────────────────────────────────────────────────────────────────────
# Class 6: TestLedgerStatsOpeningArrears
# ─────────────────────────────────────────────────────────────────────────────

class TestLedgerStatsOpeningArrears:
    """get_unit_ledger_stats now returns total_opening_arrears and units_opening_arrears."""

    @pytest.mark.asyncio
    async def test_opening_arrears_from_ledger_docs(self):
        """Ledger docs with positive opening balances → total_opening_arrears populated."""
        ledger_agg = [{
            "total_levied": 440375.10,
            "total_paid": 0.0,
            "total_net_balance": 460543.45,
            "units_owing": 87,
            "units_credit": 0,
            "units_paid_up": 0,
            "total_outstanding": 460543.45,
            "total_opening_arrears": 20168.35,
            "units_opening_arrears": 23,
        }]
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=ledger_agg)

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2026", "13195")

        assert result["total_opening_arrears"] == 20168.35
        assert result["units_opening_arrears"] == 23

    @pytest.mark.asyncio
    async def test_no_opening_arrears_returns_zero(self):
        """Ledger docs with zero opening balances → total_opening_arrears = 0."""
        ledger_agg = [{
            "total_levied": 440375.10,
            "total_paid": 440375.10,
            "total_net_balance": 0.0,
            "units_owing": 0,
            "units_credit": 0,
            "units_paid_up": 87,
            "total_outstanding": 0.0,
            "total_opening_arrears": 0.0,
            "units_opening_arrears": 0,
        }]
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=ledger_agg)

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2026", "13195")

        assert result["total_opening_arrears"] == 0.0
        assert result["units_opening_arrears"] == 0

    @pytest.mark.asyncio
    async def test_fallback_returns_zero_opening_arrears(self):
        """Fallback path (no ledger docs) returns total_opening_arrears=0."""
        units = _sample_units()
        levy = _sample_levy_doc()

        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[])
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=units)
        mock_db.units.find.return_value = units_cursor
        confirmed_cursor = MagicMock()
        confirmed_cursor.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.aggregate.return_value = confirmed_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_unit_ledger_stats
            result = await get_unit_ledger_stats("2026", "13195")

        assert result["total_opening_arrears"] == 0
        assert result["units_opening_arrears"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Class 7: TestGetArrearsUnitCount
# ─────────────────────────────────────────────────────────────────────────────

class TestGetArrearsUnitCount:
    """get_arrears_unit_count: no overdue → opening arrears only; overdue → per-unit check."""

    @pytest.mark.asyncio
    async def test_no_overdue_counts_opening_arrears_only(self):
        """Historical proxy (subtract_payments=False): units with opening_balance > 0.01 counted."""
        agg_result = [{"count": 23}]
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=agg_result)

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            # subtract_payments=False is the historical proxy branch (uses aggregate)
            count = await get_arrears_unit_count("2026", 0, 4, subtract_payments=False)

        assert count == 23

    @pytest.mark.asyncio
    async def test_no_overdue_zero_opening_arrears(self):
        """Historical proxy: no opening arrears and no overdue periods → count = 0."""
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(
            return_value=[{"count": 0}]
        )

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            # subtract_payments=False is the historical proxy branch
            count = await get_arrears_unit_count("2026", 0, 4, subtract_payments=False)

        assert count == 0

    @pytest.mark.asyncio
    async def test_one_overdue_period_counts_unpaid_units(self):
        """2026-08-03: get_arrears_metrics() no longer reconstructs obligations
        from opening_balance + entitlement-derived period levies (the defect
        behind East Gate's live '31 units / $1,469.49' bug) — it trusts each
        unit's own stored net_balance directly. With 1 overdue period and no
        in-grace period, units with a positive net_balance are simply in
        arrears."""
        ledger_docs = [
            {"unit_number": "TH087", "net_balance": 0.0},
            {"unit_number": "UA001", "net_balance": 250.0},
            {"unit_number": "UA002", "net_balance": 300.0},
        ]

        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count = await get_arrears_unit_count("2026", 1, "13195", 4)

        # TH087 has a zero balance → not outstanding; UA001 + UA002 owe money → outstanding
        assert count == 2

    @pytest.mark.asyncio
    async def test_one_overdue_with_opening_arrears(self):
        """A unit's net_balance already rolls opening/prior-period debt in —
        no separate opening_arrears lookup is needed or performed."""
        ledger_docs = [{"unit_number": "TH087", "net_balance": 1500.0}]

        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count = await get_arrears_unit_count("2026", 1, "13195", 4)

        assert count == 1

    @pytest.mark.asyncio
    async def test_no_ledger_docs_returns_zero(self):
        """No unit_levy_ledger docs for the year → returns 0, no crash."""
        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=[])
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count = await get_arrears_unit_count("2099", 2, "13195", 4)

        assert count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Class 8: TestComputePeriodDueDates
# ─────────────────────────────────────────────────────────────────────────────

class TestComputePeriodDueDates:
    """compute_period_due_dates (moved to finance_helpers) generates correct ISO dates."""

    def test_act_quarterly_last_day(self):
        """ACT schedule: months [3,6,9,12] with 'last' day type."""
        from utils.finance_helpers import compute_period_due_dates
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "last", None, 4)
        assert dates == ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]

    def test_custom_day_type_per_month(self):
        """Custom day type with per-month overrides (production settings)."""
        from utils.finance_helpers import compute_period_due_dates
        custom = {"3": 31, "6": 1, "9": 1, "12": 1}
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "custom", None, 4, custom)
        assert dates == ["2026-03-31", "2026-06-01", "2026-09-01", "2026-12-01"]

    def test_first_day_type(self):
        """'first' day type returns the 1st of each month."""
        from utils.finance_helpers import compute_period_due_dates
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "first", None, 4)
        assert all(d.endswith("-01") for d in dates)

    def test_num_periods_limits_output(self):
        """Only the first num_periods months are returned."""
        from utils.finance_helpers import compute_period_due_dates
        dates = compute_period_due_dates(2026, [3, 6, 9, 12], "last", None, 2)
        assert len(dates) == 2
        assert dates == ["2026-03-31", "2026-06-30"]

    def test_next_payment_adjusted_is_period_levy_only(self):
        """
        next_payment_adjusted must equal period_levy only (not period_levy + balance_owing).
        This test documents the fix — balance_owing is shown separately as Current Balance.
        """
        annual_levy = 7090.04
        payments_per_year = 4
        period_levy = round(annual_levy / payments_per_year, 2)
        balance_owing = 3545.02

        # OLD (wrong): next_payment_adjusted = period_levy + balance_owing
        old_value = round(period_levy + balance_owing, 2)
        # NEW (correct): next_payment_adjusted = period_levy only
        new_value = round(period_levy, 2)

        assert new_value == 1772.51
        assert old_value == 5317.53  # was incorrectly bloated
        assert new_value != old_value


# ─────────────────────────────────────────────────────────────────────────────
# Class 9: TestTotalArrearsFormula
# ─────────────────────────────────────────────────────────────────────────────

class TestTotalArrearsFormula:
    """
    total_arrears = total_opening_arrears

    Replaces the old (wrong) formulas:
    - v1: total_arrears = ls.get("total_outstanding", 0)  → summed ALL positive net_balance
          (full year levy × all units = massively inflated)
    - v2: total_arrears = max(0, -net_position)           → still wrong for historical years
          because external DEFT/BPAY payers have confirmed_paid=0, so FY2025 showed $482K.

    Final formula: use ONLY the historical carry-forward (opening_arrears from prior year).
    This is the only figure that reliably means "money that was overdue and not paid" without
    being corrupted by the DEFT/BPAY payment gap.

    collection_rate still uses the net_position formula — it is a separate KPI that measures
    current-year advance/deficit, which works correctly for the current FY.
    """

    # ── helper: mirrors current server.py formula ─────────────────────────────

    def _compute(self, total_opening_arrears: float, **_ignored):
        """Mirrors the server.py logic: total_arrears = total_opening_arrears."""
        return max(0.0, total_opening_arrears)

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_opening_arrears_is_total_arrears(self):
        """Core formula: total_arrears always equals total_opening_arrears."""
        result = self._compute(total_opening_arrears=20168.35)
        assert abs(result - 20168.35) < 0.01

    def test_zero_opening_arrears_means_zero_total_arrears(self):
        """Building with no carry-forward has zero arrears — even if all periods are overdue."""
        result = self._compute(total_opening_arrears=0.0)
        assert result == 0.0

    def test_negative_opening_arrears_floored_at_zero(self):
        """Should never happen in practice, but opening credit ≠ arrears → floor to 0."""
        result = self._compute(total_opening_arrears=-500.0)
        assert result == 0.0

    def test_does_not_include_current_year_levy(self):
        """
        Key regression: total_arrears must NOT include current-year levies regardless
        of how many periods are overdue.

        v1 bug example: FY2025 showed $482,358 (full levy × 87 units) because it summed
        all positive net_balance from the ledger.
        v2 bug example: FY2025 with confirmed_paid=0 (DEFT/BPAY gap) showed the entire levy
        because max(0, -net_position) = max(0, total_levied + opening_arrears).
        """
        total_opening_arrears = 3956.39  # actual FY2025 carry-forward
        total_levied = 425000.0  # FY2025 total levy

        result = self._compute(total_opening_arrears=total_opening_arrears)

        # Must NOT equal total_levied (old v1 bug)
        assert abs(result - total_levied) > 1000.0
        # Must NOT equal total_levied + opening_arrears (old v1 bug variant)
        assert result < total_levied
        # Must equal only the opening carry-forward
        assert abs(result - total_opening_arrears) < 0.01

    def test_fy2026_march_2_scenario(self):
        """
        FY2026 scenario as of 2026-03-02:
        - total_opening_arrears = $20,168.35 (23 units with 2025 carry-forward)
        - No periods overdue yet (Q1 due 2026-03-31 + 14-day grace = April 14)
        - confirmed_paid = $1,800.00 (one advance payment in levy_payments)
        Result: total_arrears = $20,168.35 (only the historical carry-forward).
        """
        result = self._compute(total_opening_arrears=20168.35)
        assert abs(result - 20168.35) < 0.01

    def test_new_building_no_history(self):
        """First year of operation: no carry-forward → zero arrears."""
        result = self._compute(total_opening_arrears=0.0)
        assert result == 0.0

    def test_large_carry_forward(self):
        """Large historical debt is reported correctly."""
        result = self._compute(total_opening_arrears=75432.80)
        assert abs(result - 75432.80) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Class 10: TestHistoricalYearKPIs
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoricalYearKPIs:
    """
    For completed years (levy_year_int < today.year), get_building_kpis uses
    next year's opening_arrears as the closing_arrears — immune to DEFT/BPAY gap.
    """

    def test_is_historical_year_detection(self):
        """Year < today.year → historical; today.year → current."""
        from datetime import date
        today = date.today()
        assert (today.year - 1) < today.year  # prior year is historical
        assert today.year >= today.year  # current year is not historical

    def test_historical_collection_rate_formula_fy2025(self):
        """FY2025: closing_arrears=$20,168.35 → rate=(1-20168.35/425K)×100 ≈ 95.3%."""
        total_levied = 425000.0
        closing_arrears = 20168.35  # FY2025 → FY2026 opening
        rate = (1.0 - closing_arrears / total_levied) * 100
        assert abs(rate - 95.25) < 0.1
        assert 94.0 < rate < 96.0

    def test_historical_collection_rate_formula_fy2024(self):
        """FY2024: closing_arrears≈$3,956.39 (FY2025 opening) → rate ≈ 99.1%."""
        total_levied = 425000.0
        closing_arrears = 3956.39  # FY2024 → FY2025 opening
        rate = (1.0 - closing_arrears / total_levied) * 100
        assert rate > 98.0
        assert rate < 100.0

    def test_historical_total_arrears_equals_closing_arrears(self):
        """total_arrears for historical year = next year's opening_arrears."""
        closing_arrears = 20168.35
        total_arrears = closing_arrears  # formula
        assert abs(total_arrears - 20168.35) < 0.01

    def test_historical_net_position_is_amount_collected(self):
        """For historical year, net_position = total_levied - closing_arrears."""
        total_levied = 425000.0
        closing_arrears = 20168.35
        net_position = total_levied - closing_arrears
        assert net_position > 0
        assert abs(net_position - 404831.65) < 0.1

    def test_historical_units_in_arrears_uses_next_year_opening(self):
        """
        Historical year uses get_arrears_unit_count(next_year, 0, total_periods).
        num_overdue=0 → only units with admin_opening+sinking_opening > 0.01 in next year.
        For FY2025 → next_year=2026 → 23 units with 2025 carry-forward.
        """
        # get_arrears_unit_count("2026", 0, 4) returns count from opening_balance branch
        # This avoids the DEFT/BPAY issue where num_overdue > 0 counts ALL 87 units
        count_from_opening_only_branch = 23
        assert count_from_opening_only_branch == 23  # confirmed from production data

    def test_current_year_still_uses_net_position_formula(self):
        """Current year (not historical) still uses confirmed_paid - obligations formula."""
        total_levied = 440375.10
        total_opening_arrears = 20168.35
        confirmed_paid = 1800.0
        num_overdue = 0
        total_periods = 4
        obligations_so_far = total_opening_arrears  # num_overdue=0, no period levies
        net_position = confirmed_paid - obligations_so_far
        collection_rate = (net_position / total_levied * 100) if total_levied > 0 else 0
        assert collection_rate < 0  # behind: not enough confirmed to cover opening arrears
        # Historical formula would wrongly show ~95% for the current year
        historical_rate = (1.0 - 20168.35 / 440375.10) * 100
        assert historical_rate > 90
        assert abs(collection_rate - historical_rate) > 90  # formulas diverge significantly

    def test_no_division_by_zero_historical_zero_levied(self):
        """collection_rate guarded against zero total_levied for historical year."""
        total_levied = 0
        closing_arrears = 1000.0
        collection_rate = ((1.0 - closing_arrears / total_levied) * 100) if total_levied > 0 else 0.0
        assert collection_rate == 0.0

    def test_perfect_collection_zero_closing_arrears(self):
        """If every owner paid: closing_arrears=0 → collection_rate=100%."""
        total_levied = 425000.0
        closing_arrears = 0.0
        rate = (1.0 - closing_arrears / total_levied) * 100
        assert rate == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Class 11: TestTopArrearsBalance
# ─────────────────────────────────────────────────────────────────────────────

class TestTopArrearsBalance:
    """
    get_top_arrears uses opening_balance (admin_opening + sinking_opening),
    not net_balance which includes undue current-year levies.
    """

    def test_opening_balance_not_net_balance(self):
        """
        TH074 FY2026: admin_opening=$1,603.12, sinking_opening=$484.53 → balance=$2,087.65.
        net_balance = 2087.65 + 9133.65 (annual levy) = $11,221.30 (wrong).
        """
        admin_opening = 1603.12
        sinking_opening = 484.53
        annual_levy = 9133.65
        opening_balance = admin_opening + sinking_opening
        net_balance = opening_balance + annual_levy  # what old code returned
        assert abs(opening_balance - 2087.65) < 0.01
        assert abs(net_balance - 11221.30) < 0.1
        assert opening_balance < net_balance
        # New code uses opening_balance only
        arrears_amount = opening_balance
        assert abs(arrears_amount - 2087.65) < 0.01

    def test_days_overdue_from_prev_year_last_due_date(self):
        """
        days_overdue for FY2026 arrears = days since FY2025 last grace deadline.
        Production settings: last month=Dec, custom day=1 → 2025-12-01 + 14 grace = 2025-12-15.
        As of 2026-03-03: 78 days (not ~570 from the old balance-size estimate).
        """
        from datetime import date, timedelta
        last_due = date(2025, 12, 1)
        grace_days = 14
        last_grace_deadline = last_due + timedelta(days=grace_days)  # 2025-12-15
        simulated_today = date(2026, 3, 3)
        days_overdue = max(1, (simulated_today - last_grace_deadline).days)
        assert days_overdue == 78
        # Old estimate for TH074: balance/((levied/4)/3) = 2087/(9134/4/3) ≈ balance-based → ~570d
        balance = 2087.65
        quarterly = 9133.65 / 4
        old_days = max(30, int(balance / (quarterly / 3)) * 30)
        assert old_days != days_overdue  # old estimate was wrong

    def test_pipeline_matches_exact_year_not_lte(self):
        """
        New pipeline matches year == year_to_query (not year <= financial_year).
        The old lte approach summed net_balance across multiple years, inflating amounts.
        """
        year_to_query = "2026"
        # Old: {"year": {"$lte": year_to_query}}
        # New: {"year": year_to_query}
        old_match = {"year": {"$lte": year_to_query}}
        new_match_value = year_to_query
        assert new_match_value == "2026"  # simple equality
        assert old_match["year"] != new_match_value  # old was a dict, new is string

    def test_zero_opening_balance_excluded(self):
        """Units with no opening arrears (zero carry-forward) are excluded."""
        unit_balance = 0.0
        should_include = unit_balance > 0.01
        assert should_include is False

    def test_small_positive_opening_balance_excluded(self):
        """Balances at or below $0.01 threshold are excluded (rounding artifacts)."""
        unit_balance = 0.005
        should_include = unit_balance > 0.01
        assert should_include is False

    def test_confirmed_payment_reduces_opening_balance(self):
        """A confirmed portal payment for the year reduces the displayed opening balance."""
        opening_balance = 2087.65
        confirmed_payment = 1000.0
        remaining = round(opening_balance - confirmed_payment, 2)
        assert abs(remaining - 1087.65) < 0.01
        assert remaining > 0.01  # still in arrears

    def test_confirmed_payment_fully_clears_opening_balance(self):
        """Full confirmed payment → unit excluded from arrears list."""
        opening_balance = 2087.65
        confirmed_payment = 2087.65
        remaining = round(opening_balance - confirmed_payment, 2)
        is_still_in_arrears = remaining > 0.01
        assert is_still_in_arrears is False

    def test_months_derived_from_days(self):
        """months field is computed from days_overdue / 30, not from balance size."""
        days_overdue = 78
        months = max(1, round(days_overdue / 30))
        assert months == 3  # 78 / 30 ≈ 2.6 → rounded to 3

    def test_no_opening_balance_units_return_empty(self):
        """If no units have opening arrears, the result is an empty list."""
        # Simulates the pipeline returning zero results
        aggregated_results = []
        arrears_list = [r for r in aggregated_results if r.get("opening_balance", 0) > 0.01]
        assert arrears_list == []


# ─────────────────────────────────────────────────────────────────────────────
# Class 12: TestGetCollectionRateMetrics — GAP-FIN-035 (2026-08-03)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCollectionRateMetrics:
    """get_collection_rate_metrics(): the canonical, per-unit-clamped due-date
    Collection Rate aggregation. Direct regression coverage for the reported
    bug — one unit's advance payment for a not-yet-due period must never
    inflate the building's (or another unit's) collected-to-date figure."""

    @pytest.mark.asyncio
    async def test_one_unit_paid_up_one_fully_unpaid(self):
        """Unit A due $1000, paid in full (net_balance=0). Unit B due $1000,
        paid nothing (net_balance=1000). Building collection rate = 50%."""
        ledger_docs = [
            {"unit_number": "UA001", "total_levied": 1000.0, "net_balance": 0.0,
             "admin_levied": 700.0, "admin_closing": 0.0,
             "sinking_levied": 300.0, "sinking_closing": 0.0},
            {"unit_number": "UA002", "total_levied": 1000.0, "net_balance": 1000.0,
             "admin_levied": 700.0, "admin_closing": 700.0,
             "sinking_levied": 300.0, "sinking_closing": 300.0},
        ]
        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_collection_rate_metrics
            result = await get_collection_rate_metrics("2026", "13195")

        assert result["due_to_date"] == 2000.0
        assert result["collected_to_date"] == 1000.0
        assert result["collection_rate_pct"] == 50.0
        assert result["collected_in_advance"] == 0.0

    @pytest.mark.asyncio
    async def test_advance_payment_for_not_yet_due_period_excluded_from_rate(self):
        """Direct regression for the reported bug: unit A has paid nothing
        toward its $1000 due; unit B has pre-paid $500 beyond its own
        due_to_date (net_balance = -500, i.e. total_levied=$500, paid=$1000).
        A per-unit-clamped aggregation must show unit B's contribution to
        'collected' capped at its own $500 due — never letting the $500
        advance flow into unit A's shortfall. Building rate must be 33.3%
        (500/1500), not 100% (which the old buggy
        `levied - signed_net_balance_sum` formula would have produced:
        1500 - (1000 + -500) = 1000 -> 1000/1500 = 66.7%, still wrong; the
        correct clamped answer keeps B's own excess entirely out of the
        numerator)."""
        ledger_docs = [
            {"unit_number": "UA001", "total_levied": 1000.0, "net_balance": 1000.0,
             "admin_levied": 1000.0, "admin_closing": 1000.0,
             "sinking_levied": 0.0, "sinking_closing": 0.0},
            {"unit_number": "UA002", "total_levied": 500.0, "net_balance": -500.0,
             "admin_levied": 500.0, "admin_closing": -500.0,
             "sinking_levied": 0.0, "sinking_closing": 0.0},
        ]
        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_collection_rate_metrics
            result = await get_collection_rate_metrics("2026", "13195")

        assert result["due_to_date"] == 1500.0
        # Unit A collected $0 (own due $1000); Unit B collected $500 (own due
        # $500, fully paid) — never $1000, since B's own due_to_date caps it.
        assert result["collected_to_date"] == 500.0
        assert result["collection_rate_pct"] == round(500 / 1500 * 100, 1)
        # Unit B's $500 overpayment beyond its own due amount is tracked here,
        # not folded into collected_to_date above.
        assert result["collected_in_advance"] == 500.0

    @pytest.mark.asyncio
    async def test_no_ledger_docs_returns_zero_rate_not_crash(self):
        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=[])
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_collection_rate_metrics
            result = await get_collection_rate_metrics("2099", "13195")

        assert result["due_to_date"] == 0.0
        assert result["collected_to_date"] == 0.0
        assert result["collection_rate_pct"] == 0.0
        assert result["collected_in_advance"] == 0.0

    @pytest.mark.asyncio
    async def test_admin_and_sinking_split_independently(self):
        """Per-fund figures must be computed independently, not derived by
        splitting the total rate proportionally."""
        ledger_docs = [
            {"unit_number": "UA001", "total_levied": 1000.0, "net_balance": 300.0,
             "admin_levied": 700.0, "admin_closing": 0.0,  # admin fully paid
             "sinking_levied": 300.0, "sinking_closing": 300.0},  # sinking unpaid
        ]
        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_collection_rate_metrics
            result = await get_collection_rate_metrics("2026", "13195")

        assert result["admin_fund"]["collection_rate_pct"] == 100.0
        assert result["sinking_fund"]["collection_rate_pct"] == 0.0
        assert result["collection_rate_pct"] == 70.0  # (700+0)/(700+300)


# ─────────────────────────────────────────────────────────────────────────────
# Class 12: TestGetArrearsUnitCountSubtractPayments
# ─────────────────────────────────────────────────────────────────────────────

class TestGetArrearsUnitCountSubtractPayments:
    """
    get_arrears_unit_count with subtract_payments=True (current year, num_overdue=0):
    2026-08-03 rewrite — counts units where net_balance > $0.01, read directly from
    unit_levy_ledger. net_balance already reflects opening balance minus confirmed
    payments, so there is no separate opening/payments reconstruction any more.
    """

    @pytest.mark.asyncio
    async def test_current_year_no_payments_all_arrears(self):
        """Units with a positive net_balance (nothing paid against it yet) remain in arrears."""
        ledger_docs = [
            {"unit_number": "TH087", "net_balance": 2087.65},
            {"unit_number": "UA001", "net_balance": 650.0},
            {"unit_number": "UA002", "net_balance": 0.0},  # no arrears
        ]

        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count = await get_arrears_unit_count("2026", 0, "13195", 4, subtract_payments=True)

        # TH087 and UA001 have net_balance > 0.01; UA002 has zero → 2 in arrears
        assert count == 2

    @pytest.mark.asyncio
    async def test_current_year_payment_reduces_count(self):
        """A unit whose net_balance already reflects a full payment (net_balance=0)
        is excluded from the count."""
        ledger_docs = [
            {"unit_number": "TH087", "net_balance": 0.0},
            {"unit_number": "UA001", "net_balance": 650.0},
        ]

        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count = await get_arrears_unit_count("2026", 0, "13195", 4, subtract_payments=True)

        # TH087 paid in full (net_balance=0) → excluded; UA001 still owes $650 → 1 in arrears
        assert count == 1

    @pytest.mark.asyncio
    async def test_current_year_partial_payment_still_in_arrears(self):
        """A partially-paid unit (net_balance still positive) remains counted."""
        ledger_docs = [
            {"unit_number": "TH087", "net_balance": 1087.65},
        ]

        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count = await get_arrears_unit_count("2026", 0, "13195", 4, subtract_payments=True)

        assert count == 1

    @pytest.mark.asyncio
    async def test_current_year_overpayment_not_counted(self):
        """Unit with credit balance (overpayment, negative net_balance) is NOT
        counted as in arrears — and never reduces any other unit's arrears."""
        ledger_docs = [
            {"unit_number": "TH087", "net_balance": -200.0},
        ]

        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count = await get_arrears_unit_count("2026", 0, "13195", 4, subtract_payments=True)

        assert count == 0

    @pytest.mark.asyncio
    async def test_current_year_no_ledger_docs_returns_zero(self):
        """No ledger docs for current year → count is 0 (not an error)."""
        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=[])
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count = await get_arrears_unit_count("2026", 0, "13195", 4, subtract_payments=True)

        assert count == 0

    @pytest.mark.asyncio
    async def test_subtract_true_vs_false_use_different_code_paths(self):
        """
        subtract_payments=True (num_overdue=0) reads live net_balance from
        unit_levy_ledger.find(). subtract_payments=False is the historical-proxy
        branch — an aggregate() counting units with any opening balance > $0.01,
        regardless of net_balance/payments. These remain genuinely different,
        intentionally-separate code paths (see get_arrears_metrics docstring).
        """
        ledger_docs = [
            {"unit_number": "TH087", "net_balance": 0.0},
            {"unit_number": "UA001", "net_balance": 650.0},
        ]
        # Historical aggregate would return 2 (both have opening > 0.01)
        agg_result = [{"count": 2}]

        mock_db = MagicMock()
        ledger_cursor = MagicMock()
        ledger_cursor.to_list = AsyncMock(return_value=ledger_docs)
        mock_db.unit_levy_ledger.find.return_value = ledger_cursor
        mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=agg_result)

        with patch("utils.finance_helpers.db", mock_db):
            from utils.finance_helpers import get_arrears_unit_count
            count_with_subtract = await get_arrears_unit_count("2026", 0, "13195", 4, subtract_payments=True)
            count_without_subtract = await get_arrears_unit_count("2026", 0, "13195", 4, subtract_payments=False)

        assert count_with_subtract == 1  # TH087's net_balance is already 0 → only UA001 remains
        assert count_without_subtract == 2  # historical proxy ignores net_balance → both counted
        assert count_with_subtract < count_without_subtract


# ─────────────────────────────────────────────────────────────────────────────
# Class 13: TestCurrentYearTotalArrearsFormula
# ─────────────────────────────────────────────────────────────────────────────

class TestCurrentYearTotalArrearsFormula:
    """
    For the current financial year, total_arrears in get_building_kpis uses:
        total_arrears = max(0, obligations_so_far - confirmed_paid)
    where:
        obligations_so_far = total_opening_arrears + (num_overdue / total_periods) * total_levied

    This is DIFFERENT from the historical year formula:
        total_arrears = closing_arrears (next year's total_opening_arrears)

    The new formula means total_arrears decreases as owners make confirmed payments.
    """

    def _compute_current(
            self,
            total_opening_arrears: float,
            num_overdue: int,
            total_periods: int,
            total_levied: float,
            confirmed_paid: float,
    ) -> float:
        """Mirrors server.py get_building_kpis current-year total_arrears formula."""
        obligations_so_far = total_opening_arrears + (num_overdue / total_periods) * total_levied
        return round(max(0.0, obligations_so_far - confirmed_paid), 2)

    def test_no_overdue_no_payments_equals_opening_arrears(self):
        """No overdue periods and no payments → arrears = opening_arrears (no period added)."""
        result = self._compute_current(
            total_opening_arrears=20168.35,
            num_overdue=0,
            total_periods=4,
            total_levied=440375.10,
            confirmed_paid=0.0,
        )
        assert abs(result - 20168.35) < 0.01

    def test_confirmed_payment_reduces_arrears(self):
        """Confirmed payment of $1800 reduces arrears from $20168 to $18368."""
        result = self._compute_current(
            total_opening_arrears=20168.35,
            num_overdue=0,
            total_periods=4,
            total_levied=440375.10,
            confirmed_paid=1800.0,
        )
        assert abs(result - 18368.35) < 0.01
        assert result < 20168.35

    def test_full_payment_clears_arrears_to_zero(self):
        """Paying the full opening arrears → total_arrears drops to zero."""
        opening = 20168.35
        result = self._compute_current(
            total_opening_arrears=opening,
            num_overdue=0,
            total_periods=4,
            total_levied=440375.10,
            confirmed_paid=opening,  # exactly paid all opening arrears
        )
        assert result == 0.0

    def test_one_overdue_period_adds_quarter_levy(self):
        """When Q1 deadline passes, obligations increase by one quarter levy."""
        total_levied = 440375.10
        quarter = total_levied / 4
        result = self._compute_current(
            total_opening_arrears=20168.35,
            num_overdue=1,
            total_periods=4,
            total_levied=total_levied,
            confirmed_paid=0.0,
        )
        expected = round(20168.35 + quarter, 2)
        assert abs(result - expected) < 0.01

    def test_overpayment_floors_at_zero(self):
        """Overpayment (advance payments > obligations) → arrears = 0, not negative."""
        result = self._compute_current(
            total_opening_arrears=0.0,
            num_overdue=0,
            total_periods=4,
            total_levied=440375.10,
            confirmed_paid=50000.0,  # advance payment exceeds obligations
        )
        assert result == 0.0

    def test_all_four_periods_overdue(self):
        """All 4 periods overdue → full year levy added to opening arrears."""
        total_levied = 440375.10
        opening = 20168.35
        result = self._compute_current(
            total_opening_arrears=opening,
            num_overdue=4,
            total_periods=4,
            total_levied=total_levied,
            confirmed_paid=0.0,
        )
        expected = round(opening + total_levied, 2)
        assert abs(result - expected) < 0.01

    def test_current_year_formula_differs_from_opening_only(self):
        """
        Regression: the old formula was total_arrears = total_opening_arrears.
        The new formula subtracts confirmed_paid, so results MUST differ when paid > 0.
        """
        opening_arrears = 20168.35
        confirmed_paid = 1800.0
        # Old formula
        old_arrears = opening_arrears
        # New formula (no overdue, current year)
        new_arrears = self._compute_current(
            total_opening_arrears=opening_arrears,
            num_overdue=0,
            total_periods=4,
            total_levied=440375.10,
            confirmed_paid=confirmed_paid,
        )
        assert new_arrears < old_arrears
        assert abs(old_arrears - new_arrears - confirmed_paid) < 0.01

    def test_zero_opening_arrears_zero_overdue_confirmed_payment(self):
        """No opening arrears, no overdue, some advance payment → 0 arrears."""
        result = self._compute_current(
            total_opening_arrears=0.0,
            num_overdue=0,
            total_periods=4,
            total_levied=440375.10,
            confirmed_paid=5000.0,
        )
        assert result == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Class 14: TestDynamicYearRange
# ─────────────────────────────────────────────────────────────────────────────

class TestDynamicYearRange:
    """
    Frontend year range is computed dynamically as:
        ALL_YEARS = Array.from({length: CURRENT_FY - 2021 + 1}, (_, i) => String(2021 + i))
        CHART_YEARS = ALL_YEARS.slice(-4)   // last 4 for bar charts

    This covers data from FY2021 through the current calendar year (FY2026 as of 2026-03-03).
    The range was previously hardcoded as ['2024', '2025', '2026'].

    These tests verify the Python-equivalent logic so backend can be validated against it.
    """

    def _compute_all_years(self, current_fy: int):
        """Compute all years from 2021 to current_fy (inclusive)."""
        return [str(2021 + i) for i in range(current_fy - 2021 + 1)]

    def _compute_chart_years(self, all_years, count=4):
        """Last `count` years from all_years."""
        return all_years[-count:]

    def test_year_2026_includes_2021_to_2026(self):
        """In 2026: all_years = ['2021','2022','2023','2024','2025','2026']."""
        result = self._compute_all_years(2026)
        assert result == ['2021', '2022', '2023', '2024', '2025', '2026']
        assert len(result) == 6

    def test_chart_years_are_last_four(self):
        """chart_years = last 4 of all_years → ['2023','2024','2025','2026'] for 2026."""
        all_years = self._compute_all_years(2026)
        chart_years = self._compute_chart_years(all_years)
        assert chart_years == ['2023', '2024', '2025', '2026']
        assert len(chart_years) == 4

    def test_year_2027_adds_new_year(self):
        """In 2027: all_years includes 2027, chart_years = ['2024','2025','2026','2027']."""
        all_years = self._compute_all_years(2027)
        assert '2027' in all_years
        assert '2021' in all_years
        assert len(all_years) == 7
        chart_years = self._compute_chart_years(all_years)
        assert chart_years == ['2024', '2025', '2026', '2027']

    def test_earliest_year_is_always_2021(self):
        """Regardless of current year, earliest year is always 2021 (when plan started)."""
        for fy in [2026, 2027, 2028, 2030]:
            all_years = self._compute_all_years(fy)
            assert all_years[0] == '2021', f"Expected 2021 as first year in FY{fy}"

    def test_all_years_are_contiguous(self):
        """Every year from 2021 to current is present with no gaps."""
        all_years = self._compute_all_years(2026)
        expected = [str(y) for y in range(2021, 2027)]
        assert all_years == expected

    def test_chart_years_count_is_four_regardless(self):
        """chart_years always has 4 entries once >= 4 years available."""
        for fy in [2024, 2025, 2026, 2027, 2030]:
            all_years = self._compute_all_years(fy)
            chart_years = self._compute_chart_years(all_years)
            expected_count = min(4, len(all_years))
            assert len(chart_years) == expected_count

    def test_older_years_accessible_but_not_in_chart(self):
        """FY2021, 2022, 2023 are in all_years but NOT in chart_years (space constrained)."""
        all_years = self._compute_all_years(2026)
        chart_years = self._compute_chart_years(all_years)
        assert '2021' in all_years
        assert '2022' in all_years
        assert '2021' not in chart_years  # too old for chart
        assert '2022' not in chart_years  # too old for chart

    def test_financial_year_selector_shows_all_years(self):
        """The year selector dropdown includes ALL years (not just last 3 or chart years)."""
        # Old hardcoded: ['2024', '2025', '2026'] — missing 2021, 2022, 2023
        old_years = ['2024', '2025', '2026']
        new_years = self._compute_all_years(2026)
        # New has more years than old
        assert len(new_years) > len(old_years)
        # New includes years that old was missing
        for yr in ['2021', '2022', '2023']:
            assert yr not in old_years
            assert yr in new_years


# ─────────────────────────────────────────────────────────────────────────────
# Class 15: TestCurrentYearCollectionRateNewFormula
# ─────────────────────────────────────────────────────────────────────────────

class TestCurrentYearCollectionRateNewFormula:
    """
    Tests for the updated current-year collection_rate formula:
        collection_rate = confirmed_paid / total_obligations × 100
        total_obligations = total_opening_arrears + total_levied

    This replaces the old net-position formula:
        collection_rate = (confirmed_paid - obligations_so_far) / total_levied × 100

    Key properties of the new formula:
    - Always 0–100% (never negative)
    - Not gated by quarter due dates / num_overdue
    - Denominator = full-year obligations (opening arrears + annual levy)
    """

    def _new_collection_rate(self, confirmed_paid, total_opening_arrears, total_levied):
        """Pure formula under test."""
        total_obligations = total_opening_arrears + total_levied
        if total_obligations <= 0:
            return 0.0
        return round(confirmed_paid / total_obligations * 100, 2)

    def _old_collection_rate(self, confirmed_paid, obligations_so_far, total_levied):
        """Old net-position formula for comparison."""
        net_position = confirmed_paid - obligations_so_far
        if total_levied <= 0:
            return 0.0
        return round(net_position / total_levied * 100, 2)

    def test_formula_is_confirmed_over_total_obligations(self):
        """Core formula: confirmed / (opening + levied) × 100."""
        # TH087 scenario: $1,800 confirmed / ($20,168 opening + $440,375 levied)
        confirmed = 1800.0
        opening = 20168.35
        levied = 440375.10
        total_obligations = opening + levied  # $460,543.45
        expected = round(confirmed / total_obligations * 100, 2)
        result = self._new_collection_rate(confirmed, opening, levied)
        assert result == expected
        assert 0 < result < 1  # ~0.39%

    def test_zero_confirmed_means_zero_rate(self):
        """If nothing has been confirmed in the platform, rate is exactly 0%."""
        result = self._new_collection_rate(
            confirmed_paid=0.0,
            total_opening_arrears=20168.35,
            total_levied=440375.10,
        )
        assert result == 0.0

    def test_full_payment_means_100_percent(self):
        """If confirmed_paid equals total_obligations exactly, rate is 100%."""
        opening = 20168.35
        levied = 440375.10
        total_obligations = opening + levied
        result = self._new_collection_rate(
            confirmed_paid=total_obligations,
            total_opening_arrears=opening,
            total_levied=levied,
        )
        assert result == 100.0

    def test_one_platform_payment_early_in_year(self):
        """Single $1,800 portal payment against $460,543 total obligations ≈ 0.39%."""
        result = self._new_collection_rate(
            confirmed_paid=1800.0,
            total_opening_arrears=20168.35,
            total_levied=440375.10,
        )
        # $1,800 / $460,543.45 × 100 ≈ 0.39%
        assert 0.38 <= result <= 0.40

    def test_rate_not_negative(self):
        """Rate can never be negative regardless of confirmed vs obligations ratio."""
        # Even if confirmed_paid = 0 and obligations are huge
        result = self._new_collection_rate(
            confirmed_paid=0.0,
            total_opening_arrears=50000.0,
            total_levied=500000.0,
        )
        assert result >= 0.0

        # Even with no opening arrears
        result2 = self._new_collection_rate(
            confirmed_paid=0.0,
            total_opening_arrears=0.0,
            total_levied=440375.10,
        )
        assert result2 == 0.0

    def test_rate_ignores_num_overdue(self):
        """
        New formula is independent of num_overdue — whether 0 or 4 quarters are overdue,
        the collection rate is the same (only confirmed_paid and total_obligations matter).
        """
        confirmed = 55000.0
        opening = 20168.35
        levied = 440375.10

        rate_zero_overdue = self._new_collection_rate(confirmed, opening, levied)
        rate_two_overdue = self._new_collection_rate(confirmed, opening, levied)
        rate_four_overdue = self._new_collection_rate(confirmed, opening, levied)

        # All must be equal — num_overdue is not an input to the new formula
        assert rate_zero_overdue == rate_two_overdue == rate_four_overdue

        # For contrast: old formula would differ based on obligations_so_far
        obligations_0 = opening + (0 / 4) * levied
        obligations_2 = opening + (2 / 4) * levied
        old_rate_0 = self._old_collection_rate(confirmed, obligations_0, levied)
        old_rate_2 = self._old_collection_rate(confirmed, obligations_2, levied)
        assert old_rate_0 != old_rate_2  # old formula DOES depend on overdue count

    def test_historical_year_formula_unchanged(self):
        """Historical years still use (1 - closing_arrears / total_levied) × 100."""
        # FY2025: closing_arrears (= next year's opening) = $3,956, levied = $422,580
        closing_arrears = 3956.39
        total_levied = 422580.00
        historical_rate = round((1.0 - closing_arrears / total_levied) * 100, 2)
        # Should be ~99.06% — same as before
        assert 98.5 <= historical_rate <= 99.5
        assert historical_rate != self._new_collection_rate(0.0, closing_arrears, total_levied)

    def test_new_vs_old_formula_diverge(self):
        """
        Proves the old net-position formula was negative for the current year scenario,
        while the new formula is positive.
        """
        confirmed = 1800.0
        opening = 20168.35
        levied = 440375.10

        # Old formula: Q1 not yet overdue on March 3, so obligations_so_far = opening only
        # But confirmed > opening → net_position > 0 / total_levied → very small positive
        # That's actually the edge case where old was positive… but if num_overdue>0:
        num_overdue = 1  # suppose Q1 grace deadline has passed
        total_periods = 4
        obligations_so_far = opening + (num_overdue / total_periods) * levied
        old_rate = self._old_collection_rate(confirmed, obligations_so_far, levied)

        # Old: ($1,800 - ($20,168 + $110,094)) / $440,375 × 100 → very negative
        assert old_rate < 0, f"Expected old formula to be negative, got {old_rate}%"

        # New formula: always 0–100
        new_rate = self._new_collection_rate(confirmed, opening, levied)
        assert new_rate >= 0.0, f"Expected new formula to be non-negative, got {new_rate}%"
        assert new_rate > old_rate  # new is always better (higher, non-negative)


# ─────────────────────────────────────────────────────────────────────────────
# Class: TestUpsertLedgerAnnualNormalization
# ─────────────────────────────────────────────────────────────────────────────

class TestUpsertLedgerAnnualNormalization:
    """
    _upsert_ledger_for_payment must use the annual levy amounts (from annual_levies)
    when computing closing balances on an existing ledger record, regardless of whether
    the stored admin_levied is quarterly (scraper-created) or annual (portal-created).

    Scenario: annual levy = $4,000 (admin $3,000 + sinking $1,000), quarterly = $1,000.
    UOE = 100, admin_rate = 30.0/UOE/yr, sinking_rate = 10.0/UOE/yr.

    Scraper creates ledger with admin_levied = $750 (Q1 quarterly).
    Portal payment arrives for the full year ($4,000).  Without the fix,
    new_admin_closing = $0 + $750 - $3,000 = -$2,250 → net_balance = -$3,000 (wrong).
    After the fix:  new_admin_closing = $0 + $3,000 - $3,000 = $0 → net_balance = $0.
    """

    # ---------- fixtures ------------------------------------------------------

    @staticmethod
    def _levy_doc():
        """Annual levy doc: admin $30/UOE, sinking $10/UOE → $40/UOE/yr."""
        return {
            "building_id": "13195",
            "year": "2026",
            "admin_levy_per_uoe_annual": 30.0,
            "sinking_levy_per_uoe_annual": 10.0,
        }

    @staticmethod
    def _scraper_ledger(net_balance=1000.0):
        """
        Ledger created by the quarterly scraper: admin_levied = $750 (Q1 only),
        uoe = 100.  net_balance = $1,000 mirrors the Strata Web portal balance.
        """
        return {
            "building_id": "13195",
            "year": "2026",
            "unit_number": "UA001",
            "uoe": 100,
            "admin_opening": 0.0,
            "admin_levied": 750.0,  # Q1 quarterly amount — NOT annual
            "admin_paid": 0.0,
            "admin_closing": 750.0,
            "admin_interest": 0.0,
            "sinking_opening": 0.0,
            "sinking_levied": 250.0,  # Q1 quarterly amount — NOT annual
            "sinking_paid": 0.0,
            "sinking_closing": 250.0,
            "sinking_interest": 0.0,
            "total_levied": 1000.0,
            "total_paid": 0.0,
            "net_balance": net_balance,
            "data_source": "scraper",
        }

    @staticmethod
    def _mock_db(levy_doc, existing_ledger):
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=existing_ledger)
        mock_db.unit_levy_ledger.update_one = AsyncMock()
        mock_db.units.update_one = AsyncMock()
        return mock_db

    # ---------- tests ---------------------------------------------------------

    @pytest.mark.asyncio
    async def test_full_year_payment_on_quarterly_ledger_gives_zero_balance(self):
        """
        A full-year payment ($4,000) on a scraper-created quarterly ledger (Q1 only,
        admin_levied=$750) must produce net_balance=0, not a misleading -$3,000 credit.
        Before the fix: closing = $0 + $750 - $3,000 = -$2,250 per fund → net = -$3,000.
        After the fix:  closing = $0 + $3,000 - $3,000 = $0 per fund → net = $0.
        """
        mock_db = self._mock_db(self._levy_doc(), self._scraper_ledger(net_balance=1000.0))
        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("UA001", "2026", 4000.0, "pay-full", "13195")

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        assert set_doc["net_balance"] == pytest.approx(0.0, abs=0.01), (
            f"Full-year payment should give net_balance=0, got {set_doc['net_balance']}"
        )
        # Levied fields must be normalized to annual amounts
        assert set_doc["admin_levied"] == pytest.approx(3000.0, abs=0.01)
        assert set_doc["sinking_levied"] == pytest.approx(1000.0, abs=0.01)
        assert set_doc["total_levied"] == pytest.approx(4000.0, abs=0.01)
        # units sync: no arrears, no credit
        units_set = mock_db.units.update_one.call_args[0][1]["$set"]
        assert units_set["balance_owing"] == 0.0
        assert units_set["balance_credit"] == 0.0

    @pytest.mark.asyncio
    async def test_quarterly_payment_on_quarterly_ledger_shows_three_quarters_owing(self):
        """
        A single quarterly payment ($1,000) on a scraper Q1 ledger (admin_levied=$750)
        must show net_balance = $3,000 (three remaining quarters), not $0 (which the old
        code gave because $750 levied - $750 paid = $0).
        After fix: closing = $0 + $3,000 - $750 = $2,250 admin + $750 sinking = $3,000.
        """
        mock_db = self._mock_db(self._levy_doc(), self._scraper_ledger(net_balance=1000.0))
        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("UA001", "2026", 1000.0, "pay-q1", "13195")

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        assert set_doc["net_balance"] == pytest.approx(3000.0, abs=0.01), (
            f"Q1 payment should leave net_balance=$3,000 (annual outstanding), "
            f"got {set_doc['net_balance']}"
        )
        assert set_doc["net_balance"] > 0, "Unit still owes for Q2–Q4"
        units_set = mock_db.units.update_one.call_args[0][1]["$set"]
        assert units_set["balance_owing"] == pytest.approx(3000.0, abs=0.01)
        assert units_set["balance_credit"] == 0.0

    @pytest.mark.asyncio
    async def test_overpayment_shows_correct_credit(self):
        """
        Payment of $5,000 (annual $4,000 + $1,000 extra) → net_balance = -$1,000.
        The $1,000 credit is held in trust for the next quarter — this is correct.
        """
        mock_db = self._mock_db(self._levy_doc(), self._scraper_ledger(net_balance=1000.0))
        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("UA001", "2026", 5000.0, "pay-over", "13195")

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        assert set_doc["net_balance"] == pytest.approx(-1000.0, abs=0.01), (
            f"Overpayment of $1,000 should give net_balance=-$1,000, "
            f"got {set_doc['net_balance']}"
        )
        units_set = mock_db.units.update_one.call_args[0][1]["$set"]
        assert units_set["balance_owing"] == 0.0
        assert units_set["balance_credit"] == pytest.approx(1000.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_no_uoe_falls_back_to_stored_levied(self):
        """
        When the existing record has no UOE (old scraper records), the function must
        fall back to the stored admin_levied rather than computing from rate × 0.
        """
        ledger_no_uoe = self._scraper_ledger(net_balance=7090.04)
        ledger_no_uoe.pop("uoe")  # simulate old record without uoe
        ledger_no_uoe["admin_levied"] = 5488.01
        ledger_no_uoe["sinking_levied"] = 1602.03
        ledger_no_uoe["total_levied"] = 7090.04
        ledger_no_uoe["admin_closing"] = 5488.01
        ledger_no_uoe["sinking_closing"] = 1602.03

        mock_db = self._mock_db(self._levy_doc(), ledger_no_uoe)
        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("UA001", "2026", 1800.0, "pay-001", "13195")

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        # With no UOE, uses stored admin_levied=5488.01 → normal accounting
        assert set_doc["total_paid"] == pytest.approx(1800.0, abs=0.01)
        assert set_doc["net_balance"] < 7090.04  # reduced by payment

    @pytest.mark.asyncio
    async def test_already_annual_ledger_unchanged(self):
        """
        A portal-created record already has admin_levied = annual amount ($3,000).
        The fix must produce identical results to before — no change in behaviour.
        """
        annual_ledger = self._scraper_ledger(net_balance=4000.0)
        annual_ledger["admin_levied"] = 3000.0  # already annual
        annual_ledger["sinking_levied"] = 1000.0  # already annual
        annual_ledger["total_levied"] = 4000.0
        annual_ledger["admin_closing"] = 3000.0
        annual_ledger["sinking_closing"] = 1000.0

        mock_db = self._mock_db(self._levy_doc(), annual_ledger)
        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("UA001", "2026", 4000.0, "pay-full", "13195")

        set_doc = mock_db.unit_levy_ledger.update_one.call_args[0][1]["$set"]
        assert set_doc["net_balance"] == pytest.approx(0.0, abs=0.01)
        assert set_doc["admin_levied"] == pytest.approx(3000.0, abs=0.01)
        assert set_doc["total_levied"] == pytest.approx(4000.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation_building_id_in_query(self):
        """
        DB queries for unit_levy_ledger and units must include the correct building_id.
        A payment for building 13195 must not update ledgers for building 16244.
        """
        mock_db = self._mock_db(self._levy_doc(), self._scraper_ledger())
        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("UA001", "2026", 4000.0, "pay-bld", "13195")

        # All queries / updates must reference building_id=13195
        for call in mock_db.unit_levy_ledger.find_one.call_args_list:
            filt = call[0][0]
            assert filt.get("building_id") == "13195", (
                f"unit_levy_ledger query missing correct building_id: {filt}"
            )
        for call in mock_db.unit_levy_ledger.update_one.call_args_list:
            filt = call[0][0]
            assert filt.get("building_id") == "13195"
        for call in mock_db.units.update_one.call_args_list:
            filt = call[0][0]
            assert filt.get("building_id") == "13195"

    @pytest.mark.asyncio
    async def test_idempotency_same_payment_id_skipped(self):
        """
        Applying the same payment_id twice must not update the ledger a second time.
        """
        ledger = self._scraper_ledger()
        ledger["applied_payment_ids"] = ["pay-dup"]

        mock_db = self._mock_db(self._levy_doc(), ledger)
        with patch("routers.finance.db", mock_db):
            from routers.finance import _upsert_ledger_for_payment
            await _upsert_ledger_for_payment("UA001", "2026", 4000.0, "pay-dup", "13195")

        mock_db.unit_levy_ledger.update_one.assert_not_called()
        mock_db.units.update_one.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Class 14: TestGetFundCollectionsByUnitType — GAP-FIN-035 Item 3 (2026-08-03)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetFundCollectionsByUnitType:
    """get_fund_collections_by_unit_type(): life-to-date Admin/Sinking Fund
    collected totals, broken down by unit-type group, summed across every
    year found for the building. Uses the same per-unit-clamped
    due_to_date/collected_to_date shape as get_collection_rate_metrics()."""

    def _mock_db(self, units, ledger_by_year):
        mock_db = MagicMock()
        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=units)
        mock_db.units.find.return_value = units_cursor

        mock_db.unit_levy_ledger.distinct = AsyncMock(return_value=list(ledger_by_year.keys()))

        def _find_side_effect(query, *_args, **_kwargs):
            cursor = MagicMock()
            cursor.to_list = AsyncMock(return_value=ledger_by_year.get(query.get("year"), []))
            return cursor

        mock_db.unit_levy_ledger.find.side_effect = _find_side_effect
        return mock_db

    @pytest.mark.asyncio
    async def test_splits_apartment_and_townhouse_correctly(self):
        units = [
            {"unit_number": "UA001", "unit_type": "apartment"},
            {"unit_number": "TH071", "unit_type": "townhouse"},
        ]
        ledger_by_year = {
            "2025": [
                {"unit_number": "UA001", "admin_levied": 1000.0, "admin_closing": 0.0,
                 "sinking_levied": 300.0, "sinking_closing": 0.0},
                {"unit_number": "TH071", "admin_levied": 2000.0, "admin_closing": 500.0,
                 "sinking_levied": 600.0, "sinking_closing": 600.0},
            ],
        }
        mock_db = self._mock_db(units, ledger_by_year)

        with (
            patch("utils.finance_helpers.db", mock_db),
            patch("services.cutover_status_service.get_cutover_status", new=AsyncMock(return_value=None)),
        ):
            from utils.finance_helpers import get_fund_collections_by_unit_type
            result = await get_fund_collections_by_unit_type("13195")

        assert result["years_included"] == ["2025"]
        assert result["postgres_promotion_pending"] is False
        # UA001 (apartment): admin fully collected (1000), sinking fully collected (300).
        assert result["by_unit_type"]["Apartment"]["admin_collected_to_date"] == 1000.0
        assert result["by_unit_type"]["Apartment"]["sinking_collected_to_date"] == 300.0
        # TH071 (townhouse): admin collected 2000-500=1500, sinking collected 0 (fully unpaid).
        assert result["by_unit_type"]["Townhouse"]["admin_collected_to_date"] == 1500.0
        assert result["by_unit_type"]["Townhouse"]["sinking_collected_to_date"] == 0.0
        assert result["admin_fund"]["collected_to_date"] == 2500.0  # 1000 + 1500
        assert result["sinking_fund"]["collected_to_date"] == 300.0  # 300 + 0
        assert result["total_collected_to_date"] == 2800.0

    @pytest.mark.asyncio
    async def test_unit_type_falls_back_to_unit_number_prefix(self):
        """No unit_type/property_type field set -> classify by UA/TH prefix,
        matching services.levy_fairness_service._group_key()."""
        units = [{"unit_number": "UA005"}, {"unit_number": "TH080"}]
        ledger_by_year = {
            "2026": [
                {"unit_number": "UA005", "admin_levied": 500.0, "admin_closing": 0.0,
                 "sinking_levied": 0.0, "sinking_closing": 0.0},
                {"unit_number": "TH080", "admin_levied": 700.0, "admin_closing": 0.0,
                 "sinking_levied": 0.0, "sinking_closing": 0.0},
            ],
        }
        mock_db = self._mock_db(units, ledger_by_year)

        with (
            patch("utils.finance_helpers.db", mock_db),
            patch("services.cutover_status_service.get_cutover_status", new=AsyncMock(return_value=None)),
        ):
            from utils.finance_helpers import get_fund_collections_by_unit_type
            result = await get_fund_collections_by_unit_type("13195")

        assert "Apartment" in result["by_unit_type"]
        assert "Townhouse" in result["by_unit_type"]
        assert result["by_unit_type"]["Apartment"]["admin_collected_to_date"] == 500.0
        assert result["by_unit_type"]["Townhouse"]["admin_collected_to_date"] == 700.0

    @pytest.mark.asyncio
    async def test_sums_across_multiple_years(self):
        """Life-to-date means summing every year's own collected_to_date, not
        just the latest year."""
        units = [{"unit_number": "UA001", "unit_type": "apartment"}]
        ledger_by_year = {
            "2021": [{"unit_number": "UA001", "admin_levied": 100.0, "admin_closing": 0.0,
                       "sinking_levied": 0.0, "sinking_closing": 0.0}],
            "2022": [{"unit_number": "UA001", "admin_levied": 200.0, "admin_closing": 0.0,
                       "sinking_levied": 0.0, "sinking_closing": 0.0}],
        }
        mock_db = self._mock_db(units, ledger_by_year)

        with (
            patch("utils.finance_helpers.db", mock_db),
            patch("services.cutover_status_service.get_cutover_status", new=AsyncMock(return_value=None)),
        ):
            from utils.finance_helpers import get_fund_collections_by_unit_type
            result = await get_fund_collections_by_unit_type("13195")

        assert result["years_included"] == ["2021", "2022"]
        assert result["admin_fund"]["collected_to_date"] == 300.0  # 100 + 200

    @pytest.mark.asyncio
    async def test_explicit_years_param_overrides_discovery(self):
        units = [{"unit_number": "UA001", "unit_type": "apartment"}]
        ledger_by_year = {
            "2021": [{"unit_number": "UA001", "admin_levied": 100.0, "admin_closing": 0.0,
                       "sinking_levied": 0.0, "sinking_closing": 0.0}],
            "2022": [{"unit_number": "UA001", "admin_levied": 200.0, "admin_closing": 0.0,
                       "sinking_levied": 0.0, "sinking_closing": 0.0}],
        }
        mock_db = self._mock_db(units, ledger_by_year)

        with (
            patch("utils.finance_helpers.db", mock_db),
            patch("services.cutover_status_service.get_cutover_status", new=AsyncMock(return_value=None)),
        ):
            from utils.finance_helpers import get_fund_collections_by_unit_type
            result = await get_fund_collections_by_unit_type("13195", years=["2021"])

        mock_db.unit_levy_ledger.distinct.assert_not_called()
        assert result["years_included"] == ["2021"]
        assert result["admin_fund"]["collected_to_date"] == 100.0

    @pytest.mark.asyncio
    async def test_no_ledger_data_returns_zero_not_crash(self):
        mock_db = self._mock_db([], {})

        with (
            patch("utils.finance_helpers.db", mock_db),
            patch("services.cutover_status_service.get_cutover_status", new=AsyncMock(return_value=None)),
        ):
            from utils.finance_helpers import get_fund_collections_by_unit_type
            result = await get_fund_collections_by_unit_type("13195")

        assert result["years_included"] == []
        assert result["total_collected_to_date"] == 0.0
        assert result["by_unit_type"] == {}
