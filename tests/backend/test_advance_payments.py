"""
Tests for advance/extra levy payment recording and pending-status quarter logic.

Covers:
- Advance payment mode (payment_type='advance') accepted by owners and managers
- credit_amount computed correctly when amount > period levy
- credit_balance and in_credit returned by /levy-status
- Partial and standard payment_type stored correctly
- Owner cannot record advance payment for another unit (403)
- Non-owner/manager still blocked (403)
- Backward compatibility: missing payment_type defaults to 'standard'
- Pending quarter status when payment_type is pending_verification
- Button disable logic: paid/pending/partial-past-deadline disabled, others enabled
- partial status fires when partial payment exists (before AND after deadline)
"""

import asyncio

from unittest.mock import AsyncMock, patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _neutral_interest_penalty():
    """get_levy_status()'s interest/penalty enrichment (routers/finance.py ~L6920) calls
    get_effective_interest_rate()/get_late_fee_policy() against the real `db` module attribute
    -- these tests patch `routers.finance.db` to a bare MagicMock with only specific
    collections configured as AsyncMock, so the unconfigured calls those two functions make
    raise 'object MagicMock can't be used in an await expression', caught and logged, leaving
    `computed_charges` at its pre-enrichment default. Most assertions here don't care (they
    test pending/partial/paid quarter *payment* status, not interest), so mocking these two
    functions to a neutral (0% rate, disabled late fee) result keeps the enrichment a no-op
    instead of a silently-swallowed exception, matching backend/CLAUDE.md's AsyncMock-for-every-
    DB-call rule."""
    with patch(
        "services.arrears_interest_service.get_effective_interest_rate",
        new_callable=AsyncMock, return_value={"rate_pct": 0.0, "max_rate_pct": 20.0},
    ), patch(
        "services.interest_penalty_service.get_late_fee_policy",
        new_callable=AsyncMock,
        return_value={
            "enabled": False, "amount": 0.0, "period_days": 14,
            "gst_inclusive": True, "start_year": None,
        },
    ):
        yield


# ─── Fixtures / helpers ──────────────────────────────────────────────────────

def _make_user(role="owner", unit_number="TH087", can_manage=False):
    perms = {
        "can_manage_finances": can_manage,
        "can_view_finances": True,
    }
    return {
        "id": "user-001",
        "full_name": "Test User",
        "email": "test@example.com",
        "role": role,
        "unit_number": unit_number,
        "permissions": perms,
    }


def _make_levy_doc(total_annual=7090.04, year="2026"):
    return {
        "plan_id": "13195",
        "year": year,
        "admin_levy_per_uoe_annual": 41.0,
        "sinking_levy_per_uoe_annual": 12.0,
    }


def _make_unit_doc(entitlement=140):
    return {"unit_number": "TH087", "entitlement": entitlement}


def _cursor(items):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    cursor.sort.return_value = cursor
    cursor.__aiter__.return_value = iter(items)
    return cursor


# ─── TestAdvancePaymentMode ───────────────────────────────────────────────────

class TestAdvancePaymentMode:
    """payment_type='advance' records correctly and computes credit_amount."""

    @pytest.mark.asyncio
    async def test_advance_payment_computes_credit_amount(self):
        """When amount > period_levy, credit_amount = amount - period_levy."""
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate

        owner = _make_user(role="owner", unit_number="TH087")
        levy_doc = _make_levy_doc()
        unit_doc = _make_unit_doc(entitlement=140)

        # period_levy = (41 + 12) * 140 / 4 = 7420 / 4 = 1855.00
        period_levy = round((41 + 12) * 140 / 4, 2)
        advance_amount = period_levy + 500.00  # $500 extra

        data = LevyPaymentCreate(
            unit_number="TH087",
            amount=advance_amount,
            payment_method="deft",
            quarter="Q2",
            year="2026",
            payment_type="advance",
        )

        inserted = {}

        async def fake_insert(doc):
            inserted.update(doc)

        with patch("routers.finance.db") as mock_db, \
                patch("utils.finance_helpers.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 41.0, "sinking_annual": 12.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ), \
                patch("routers.finance.asyncio") as mock_async:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.levy_payments.count_documents = AsyncMock(return_value=10)
            mock_db.levy_payments.insert_one = AsyncMock(side_effect=fake_insert)
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.units.find_one = AsyncMock(return_value=unit_doc)
            mock_async.create_task = MagicMock()
            mock_async.gather = asyncio.gather

            result = await record_levy_payment(data, owner, building_id="13195", idempotency_key=None)

        assert inserted.get("payment_type") == "advance"
        assert inserted.get("credit_amount") is not None
        assert abs(inserted["credit_amount"] - 500.00) < 0.02

    @pytest.mark.asyncio
    async def test_advance_payment_no_credit_when_amount_lte_period(self):
        """credit_amount is None when advance amount <= period levy."""
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate

        owner = _make_user(role="owner", unit_number="TH087")
        levy_doc = _make_levy_doc()
        unit_doc = _make_unit_doc(entitlement=140)

        period_levy = round((41 + 12) * 140 / 4, 2)
        data = LevyPaymentCreate(
            unit_number="TH087",
            amount=period_levy,  # exactly the quarter levy, no credit
            payment_method="bpay",
            quarter="Q1",
            year="2026",
            payment_type="advance",
        )

        inserted = {}

        async def fake_insert(doc):
            inserted.update(doc)

        with patch("routers.finance.db") as mock_db, \
                patch("utils.finance_helpers.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 41.0, "sinking_annual": 12.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ), \
                patch("routers.finance.asyncio") as mock_async:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.levy_payments.count_documents = AsyncMock(return_value=5)
            mock_db.levy_payments.insert_one = AsyncMock(side_effect=fake_insert)
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.units.find_one = AsyncMock(return_value=unit_doc)
            mock_async.create_task = MagicMock()
            mock_async.gather = asyncio.gather

            await record_levy_payment(data, owner, building_id="13195", idempotency_key=None)

        assert inserted.get("credit_amount") is None

    @pytest.mark.asyncio
    async def test_advance_payment_status_is_pending_for_owner(self):
        """Owner self-reported advance payment gets status=pending_verification."""
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate

        owner = _make_user(role="owner", unit_number="TH087")
        levy_doc = _make_levy_doc()
        unit_doc = _make_unit_doc(entitlement=140)

        period_levy = round((41 + 12) * 140 / 4, 2)
        data = LevyPaymentCreate(
            unit_number="TH087",
            amount=period_levy + 300,
            payment_method="deft",
            quarter="Q3",
            year="2026",
            payment_type="advance",
        )

        inserted = {}

        async def fake_insert(doc):
            inserted.update(doc)

        with patch("routers.finance.db") as mock_db, \
                patch("utils.finance_helpers.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 41.0, "sinking_annual": 12.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ), \
                patch("routers.finance.asyncio") as mock_async:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.levy_payments.count_documents = AsyncMock(return_value=0)
            mock_db.levy_payments.insert_one = AsyncMock(side_effect=fake_insert)
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.units.find_one = AsyncMock(return_value=unit_doc)
            mock_async.create_task = MagicMock()
            mock_async.gather = asyncio.gather

            await record_levy_payment(data, owner, building_id="13195", idempotency_key=None)

        assert inserted["status"] == "pending_verification"

    @pytest.mark.asyncio
    async def test_advance_payment_status_is_confirmed_for_manager(self):
        """Manager-recorded advance payment gets status=confirmed."""
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate

        manager = _make_user(role="super_admin", can_manage=True)
        levy_doc = _make_levy_doc()
        unit_doc = _make_unit_doc(entitlement=140)

        period_levy = round((41 + 12) * 140 / 4, 2)
        data = LevyPaymentCreate(
            unit_number="TH087",
            amount=period_levy + 1000,
            payment_method="bank_transfer",
            quarter="Q1",
            year="2026",
            payment_type="advance",
        )

        inserted = {}

        async def fake_insert(doc):
            inserted.update(doc)

        with patch("routers.finance.db") as mock_db, \
                patch("utils.finance_helpers.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 41.0, "sinking_annual": 12.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ), \
                patch("routers.finance.asyncio") as mock_async:
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            mock_db.levy_payments.count_documents = AsyncMock(return_value=20)
            mock_db.levy_payments.insert_one = AsyncMock(side_effect=fake_insert)
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.units.find_one = AsyncMock(return_value=unit_doc)
            mock_async.create_task = MagicMock()
            mock_async.gather = asyncio.gather

            await record_levy_payment(data, manager, building_id="13195", idempotency_key=None)

        assert inserted["status"] == "confirmed"
        assert inserted["confirmed_by"] == manager["id"]


# ─── TestPaymentTypeStorage ───────────────────────────────────────────────────

class TestPaymentTypeStorage:
    """payment_type is stored correctly for all modes."""

    @pytest.mark.asyncio
    async def test_standard_payment_type_stored(self):
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate

        owner = _make_user(role="owner", unit_number="UA001")
        data = LevyPaymentCreate(
            unit_number="UA001",
            amount=1500.00,
            payment_method="bpay",
            quarter="Q1",
            year="2026",
            payment_type="standard",
        )

        inserted = {}

        async def fake_insert(doc):
            inserted.update(doc)

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ), \
                patch("routers.finance.asyncio") as mock_async:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.levy_payments.count_documents = AsyncMock(return_value=1)
            mock_db.levy_payments.insert_one = AsyncMock(side_effect=fake_insert)
            mock_db.annual_levies.find_one = AsyncMock(return_value=None)
            mock_db.units.find_one = AsyncMock(return_value=None)
            mock_async.create_task = MagicMock()
            mock_async.gather = asyncio.gather

            await record_levy_payment(data, owner, building_id="13195", idempotency_key=None)

        assert inserted.get("payment_type") == "standard"
        assert inserted.get("credit_amount") is None

    @pytest.mark.asyncio
    async def test_partial_payment_type_stored(self):
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate

        owner = _make_user(role="owner", unit_number="UA001")
        data = LevyPaymentCreate(
            unit_number="UA001",
            amount=750.00,
            payment_method="deft",
            quarter="Q2",
            year="2026",
            payment_type="partial",
        )

        inserted = {}

        async def fake_insert(doc):
            inserted.update(doc)

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ), \
                patch("routers.finance.asyncio") as mock_async:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.levy_payments.count_documents = AsyncMock(return_value=2)
            mock_db.levy_payments.insert_one = AsyncMock(side_effect=fake_insert)
            mock_db.annual_levies.find_one = AsyncMock(return_value=None)
            mock_db.units.find_one = AsyncMock(return_value=None)
            mock_async.create_task = MagicMock()
            mock_async.gather = asyncio.gather

            await record_levy_payment(data, owner, building_id="13195", idempotency_key=None)

        assert inserted.get("payment_type") == "partial"

    @pytest.mark.asyncio
    async def test_missing_payment_type_defaults_to_standard(self):
        """Backward compat: omitting payment_type defaults to 'standard'."""
        from models.finance import LevyPaymentCreate

        data = LevyPaymentCreate(
            unit_number="TH087",
            amount=1000.00,
            payment_method="bpay",
            quarter="Q1",
            year="2026",
        )
        assert data.payment_type == "standard"


# ─── TestCreditBalanceInLevyStatus ───────────────────────────────────────────

class TestCreditBalanceInLevyStatus:
    """credit_balance and in_credit are returned correctly by /levy-status."""

    @pytest.mark.asyncio
    async def test_credit_balance_returned_when_overpaid(self):
        """When total_paid > total_annual + prev_year_balance, credit_balance > 0."""
        from routers.finance import get_levy_status

        unit = {"entitlement": 100}
        settings = {
            "id": "main",
            "grace_period_days": 14,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "last",
        }
        levy = {
            "plan_id": "13195",
            "year": "2026",
            "admin_levy_per_uoe_annual": 40.0,
            "sinking_levy_per_uoe_annual": 10.0,
            "payment_schedule": [],
        }
        # total_annual = (40 + 10) * 100 = 5000
        # paid = 6000 → credit_balance = 1000
        payments = [
            {"quarter": "Q1", "amount": 6000.0, "status": "confirmed", "receipt_number": "EG-2026-00001"},
        ]
        prev_ledger = None
        ledger = None

        user = _make_user(role="owner", unit_number="UA010")

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)), \
                patch("routers.finance.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch("routers.finance.get_current_user", return_value=user):
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.units.find_one = AsyncMock(return_value=unit)
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(side_effect=[ledger, prev_ledger])
            mock_db.levy_payments.find = MagicMock()
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=payments)
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA010", year="2026", current_user=user, building_id="13195")

        assert result["credit_balance"] == 1000.0
        assert result["in_credit"] is True
        assert result["balance_due"] == 0.0

    @pytest.mark.asyncio
    async def test_no_credit_balance_when_underpaid(self):
        """When total_paid < total_annual, credit_balance is 0 and in_credit is False."""
        from routers.finance import get_levy_status

        unit = {"entitlement": 100}
        settings = {
            "id": "main",
            "grace_period_days": 14,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "last",
        }
        levy = {
            "plan_id": "13195",
            "year": "2026",
            "admin_levy_per_uoe_annual": 40.0,
            "sinking_levy_per_uoe_annual": 10.0,
            "payment_schedule": [],
        }
        payments = [
            {"quarter": "Q1", "amount": 1250.0, "status": "confirmed", "receipt_number": "EG-2026-00002"},
        ]

        user = _make_user(role="owner", unit_number="UA010")

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)), \
                patch("routers.finance.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.units.find_one = AsyncMock(return_value=unit)
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.levy_payments.find = MagicMock()
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=payments)
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA010", year="2026", current_user=user, building_id="13195")

        assert result["credit_balance"] == 0.0
        assert result["in_credit"] is False
        assert result["balance_due"] == 3750.0

    @pytest.mark.asyncio
    async def test_credit_balance_zero_when_exactly_paid(self):
        """Exact payment results in credit_balance=0 and balance_due=0."""
        from routers.finance import get_levy_status

        unit = {"entitlement": 100}
        settings = {
            "id": "main",
            "grace_period_days": 14,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "last",
        }
        levy = {
            "plan_id": "13195",
            "year": "2026",
            "admin_levy_per_uoe_annual": 40.0,
            "sinking_levy_per_uoe_annual": 10.0,
            "payment_schedule": [],
        }
        # total_annual = 5000; pay exactly 5000
        payments = [
            {"quarter": "Q1", "amount": 5000.0, "status": "confirmed", "receipt_number": "EG-2026-00003"},
        ]

        user = _make_user(role="owner", unit_number="UA010")

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)), \
                patch("routers.finance.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.units.find_one = AsyncMock(return_value=unit)
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.levy_payments.find = MagicMock()
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=payments)
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA010", year="2026", current_user=user, building_id="13195")

        assert result["credit_balance"] == 0.0
        assert result["in_credit"] is False
        assert result["balance_due"] == 0.0


# ─── TestPermissions ─────────────────────────────────────────────────────────

class TestPermissions:
    """Permissions for advance payments follow the same rules as regular payments."""

    @pytest.mark.asyncio
    async def test_owner_cannot_record_advance_for_other_unit(self):
        """Owner recording advance payment for a different unit → 403."""
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate
        from fastapi import HTTPException

        owner = _make_user(role="owner", unit_number="TH087")
        data = LevyPaymentCreate(
            unit_number="UA001",  # different unit
            amount=5000.00,
            payment_method="deft",
            quarter="Q1",
            year="2026",
            payment_type="advance",
        )

        with patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ):
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            with pytest.raises(HTTPException) as exc_info:
                await record_levy_payment(data, owner, building_id="13195")

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_tenant_cannot_record_advance_payment(self):
        """Tenant role cannot record any levy payments → 403."""
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate
        from fastapi import HTTPException

        tenant = _make_user(role="tenant", unit_number="TH080")
        data = LevyPaymentCreate(
            unit_number="TH080",
            amount=1500.00,
            payment_method="bpay",
            quarter="Q1",
            year="2026",
            payment_type="advance",
        )

        with patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ):
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            with pytest.raises(HTTPException) as exc_info:
                await record_levy_payment(data, tenant, building_id="13195")

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_manager_can_record_advance_for_any_unit(self):
        """Manager with can_manage_finances can record advance for any unit."""
        from routers.finance import record_levy_payment
        from models.finance import LevyPaymentCreate

        manager = _make_user(role="super_admin", can_manage=True)
        levy_doc = _make_levy_doc()
        unit_doc = _make_unit_doc()

        data = LevyPaymentCreate(
            unit_number="UA050",
            amount=9999.99,
            payment_method="bank_transfer",
            quarter="Q1",
            year="2026",
            payment_type="advance",
        )

        inserted = {}

        async def fake_insert(doc):
            inserted.update(doc)

        with patch("routers.finance.db") as mock_db, \
                patch("utils.finance_helpers.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 41.0, "sinking_annual": 12.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms, \
                patch(
                    "routers.finance.get_finance_write_route_runtime_state",
                    new=AsyncMock(return_value={"source": "mongo", "domain_mode": "mongo_primary"}),
                ), \
                patch("routers.finance.asyncio") as mock_async:
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            mock_db.levy_payments.count_documents = AsyncMock(return_value=50)
            mock_db.levy_payments.insert_one = AsyncMock(side_effect=fake_insert)
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
            mock_db.units.find_one = AsyncMock(return_value=unit_doc)
            mock_async.create_task = MagicMock()
            mock_async.gather = asyncio.gather

            await record_levy_payment(data, manager, building_id="13195", idempotency_key=None)

        assert inserted["unit_number"] == "UA050"
        assert inserted["payment_type"] == "advance"


# ─── TestModelValidation ─────────────────────────────────────────────────────

class TestModelValidation:
    """LevyPaymentCreate model validation for payment_type field."""

    def test_valid_payment_types(self):
        from models.finance import LevyPaymentCreate

        for pt in ["standard", "partial", "advance"]:
            data = LevyPaymentCreate(
                unit_number="TH087",
                amount=1000.0,
                payment_method="deft",
                quarter="Q1",
                year="2026",
                payment_type=pt,
            )
            assert data.payment_type == pt

    def test_none_payment_type_defaults_to_standard(self):
        from models.finance import LevyPaymentCreate

        data = LevyPaymentCreate(
            unit_number="TH087",
            amount=1000.0,
            payment_method="deft",
            quarter="Q1",
            year="2026",
            payment_type=None,
        )
        # payment_type=None is accepted; the router normalises it to 'standard'
        assert data.payment_type is None  # model allows None, router handles it

    def test_response_model_accepts_credit_amount(self):
        from models.finance import LevyPaymentResponse

        resp = LevyPaymentResponse(
            id="abc",
            unit_number="TH087",
            amount=2000.0,
            payment_method="deft",
            payment_reference=None,
            quarter="Q1",
            year="2026",
            fund_type=None,
            payment_type="advance",
            credit_amount=500.0,
            status="pending_verification",
            notes=None,
            receipt_number="EG-2026-00001",
            paid_by="user-001",
            confirmed_by=None,
            confirmed_at=None,
            created_at="2026-03-02T12:00:00Z",
        )
        assert resp.payment_type == "advance"
        assert resp.credit_amount == 500.0

    def test_response_model_accepts_null_credit_amount(self):
        from models.finance import LevyPaymentResponse

        resp = LevyPaymentResponse(
            id="xyz",
            unit_number="UA001",
            amount=1000.0,
            payment_method="bpay",
            payment_reference=None,
            quarter="Q2",
            year="2026",
            fund_type=None,
            payment_type="standard",
            credit_amount=None,
            status="confirmed",
            notes=None,
            receipt_number="EG-2026-00002",
            paid_by="user-001",
            confirmed_by="user-001",
            confirmed_at="2026-03-02T12:00:00Z",
            created_at="2026-03-02T12:00:00Z",
        )
        assert resp.credit_amount is None


# ─── TestPendingQuarterStatus ─────────────────────────────────────────────────

class TestPendingQuarterStatus:
    """
    pending_verification payments are reflected in levy-status:
    - Quarter status = 'pending' when self-reported payment covers full amount
    - Quarter status = 'partial' when self-reported payment is less than full
    - pending_total returned at response level
    - balance_due and total_paid include pending amounts
    """

    def _base_kwargs(self):
        unit = {"entitlement": 100}
        settings = {
            "id": "main",
            "grace_period_days": 14,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "last",
        }
        levy = {
            "plan_id": "13195",
            "year": "2026",
            "admin_levy_per_uoe_annual": 40.0,
            "sinking_levy_per_uoe_annual": 10.0,
            "payment_schedule": [],
        }
        return unit, settings, levy

    @pytest.mark.asyncio
    async def test_quarter_status_is_pending_when_self_reported_full_payment(self):
        """
        A pending_verification payment covering Q1 fully → Q1 status = 'pending',
        NOT 'upcoming'. balance_due and total_paid reflect the payment.
        """
        from routers.finance import get_levy_status

        unit, settings, levy = self._base_kwargs()
        # period_levy = (40+10)*100/4 = 1250
        payments = [
            {"quarter": "Q1", "amount": 1250.0, "status": "pending_verification", "receipt_number": "DEFT-001"},
        ]
        user = _make_user(role="owner", unit_number="UA020")

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)), \
                patch("routers.finance.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.units.find_one = AsyncMock(return_value=unit)
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.levy_payments.find = MagicMock()
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=payments)
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA020", year="2026", current_user=user, building_id="13195")

        q1 = next(q for q in result["quarters"] if q["quarter"] == "Q1")
        assert q1["status"] == "pending", f"Expected 'pending', got '{q1['status']}'"
        assert q1["amount_paid"] == 1250.0
        assert q1["pending_amount"] == 1250.0
        assert q1["confirmed_amount"] == 0.0
        assert result["total_paid"] == 1250.0
        assert result["pending_total"] == 1250.0
        assert result["balance_due"] == 3750.0  # 5000 - 1250

    @pytest.mark.asyncio
    async def test_quarter_status_is_partial_when_self_reported_partial_payment(self):
        """
        A pending_verification payment covering less than Q1 → Q1 status = 'partial'.
        """
        from routers.finance import get_levy_status

        unit, settings, levy = self._base_kwargs()
        payments = [
            {"quarter": "Q1", "amount": 600.0, "status": "pending_verification", "receipt_number": "DEFT-002"},
        ]
        user = _make_user(role="owner", unit_number="UA020")

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)), \
                patch("routers.finance.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.units.find_one = AsyncMock(return_value=unit)
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.levy_payments.find = MagicMock()
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=payments)
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA020", year="2026", current_user=user, building_id="13195")

        q1 = next(q for q in result["quarters"] if q["quarter"] == "Q1")
        assert q1["status"] == "partial"
        assert q1["amount_paid"] == 600.0
        assert result["pending_total"] == 600.0

    @pytest.mark.asyncio
    async def test_confirmed_payment_keeps_paid_status(self):
        """A confirmed payment → status stays 'paid', not 'pending'."""
        from routers.finance import get_levy_status

        unit, settings, levy = self._base_kwargs()
        payments = [
            {"quarter": "Q1", "amount": 1250.0, "status": "confirmed", "receipt_number": "EG-2026-00005"},
        ]
        user = _make_user(role="owner", unit_number="UA020")

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)), \
                patch("routers.finance.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.units.find_one = AsyncMock(return_value=unit)
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.levy_payments.find = MagicMock()
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=payments)
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA020", year="2026", current_user=user, building_id="13195")

        q1 = next(q for q in result["quarters"] if q["quarter"] == "Q1")
        assert q1["status"] == "paid"
        assert result["pending_total"] == 0.0

    @pytest.mark.asyncio
    async def test_no_payment_quarter_stays_upcoming(self):
        """Quarter with no payments stays 'upcoming' (not past deadline)."""
        from routers.finance import get_levy_status

        unit, settings, levy = self._base_kwargs()
        user = _make_user(role="owner", unit_number="UA020")

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)), \
                patch("routers.finance.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.units.find_one = AsyncMock(return_value=unit)
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.levy_payments.find = MagicMock()
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=[])
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA020", year="2026", current_user=user, building_id="13195")

        for q in result["quarters"]:
            assert q["status"] in ("upcoming", "overdue"), f"Unexpected status '{q['status']}' for {q['quarter']}"
        assert result["total_paid"] == 0.0
        assert result["pending_total"] == 0.0

    @pytest.mark.asyncio
    async def test_mixed_confirmed_and_pending_payments(self):
        """
        Q1 confirmed, Q2 pending_verification, Q3 no payment.
        Status: Q1=paid, Q2=pending, Q3=upcoming.
        total_paid includes both.
        """
        from routers.finance import get_levy_status

        unit, settings, levy = self._base_kwargs()
        payments = [
            {"quarter": "Q1", "amount": 1250.0, "status": "confirmed", "receipt_number": "EG-001"},
            {"quarter": "Q2", "amount": 1250.0, "status": "pending_verification", "receipt_number": "DEFT-003"},
        ]
        user = _make_user(role="owner", unit_number="UA020")

        with patch("routers.finance.db") as mock_db, \
                patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)), \
                patch("routers.finance.get_levy_rates",
                      new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})), \
                patch("routers.finance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=False)
            mock_db.units.find_one = AsyncMock(return_value=unit)
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.levy_payments.find = MagicMock()
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=payments)
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA020", year="2026", current_user=user, building_id="13195")

        quarters = {q["quarter"]: q for q in result["quarters"]}
        assert quarters["Q1"]["status"] == "paid"
        assert quarters["Q2"]["status"] == "pending"
        assert quarters["Q3"]["status"] in ("upcoming", "overdue")
        assert result["total_paid"] == 2500.0
        assert result["pending_total"] == 1250.0


class TestLevyStatusAccessAndInstallments:
    @pytest.mark.asyncio
    async def test_ec_member_can_view_other_units_levy_status(self):
        from routers.finance import get_levy_status

        user = _make_user(role="ec_member", unit_number="TH001", can_manage=True)
        settings = {
            "grace_period_days": 14,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "first",
        }
        levy = {"building_id": "13195", "year": "2026", "payment_schedule": []}
        ledger = {"total_paid": 0.0, "opening_arrears": 0.0}

        with (
            patch("routers.finance.db") as mock_db,
            patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)),
            patch("routers.finance.get_levy_rates",
                  new=AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 10.0})),
            patch("routers.finance.get_user_permissions", return_value=MagicMock(can_manage_finances=True)),
        ):
            mock_db.units.find_one = AsyncMock(return_value={"entitlement": 100})
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(side_effect=[ledger, None])
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=[])
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA010", year="2026", current_user=user, building_id="13195")

        assert result["unit_number"] == "UA010"
        assert result["annual_levy"] == pytest.approx(5000.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_owner_cannot_view_other_units_levy_status(self):
        from fastapi import HTTPException
        from routers.finance import get_levy_status

        user = _make_user(role="owner", unit_number="UA001", can_manage=False)

        with pytest.raises(HTTPException) as exc:
            await get_levy_status("UA010", year="2026", current_user=user, building_id="13195")

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_levy_status_uses_residual_final_installment_amount(self):
        from datetime import date as real_date
        from routers.finance import get_levy_status

        class FrozenDate(real_date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 1)

        user = _make_user(role="owner", unit_number="UA063", can_manage=False)
        settings = {
            "grace_period_days": 14,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "first",
        }
        levy = {
            "building_id": "13195",
            "year": "2026",
            "payment_schedule": [
                {"quarter": "Q1", "due_date": "2026-03-01"},
                {"quarter": "Q2", "due_date": "2026-06-01"},
                {"quarter": "Q3", "due_date": "2026-09-01"},
                {"quarter": "Q4", "due_date": "2026-12-01"},
            ],
        }

        with (
            patch("routers.finance.db") as mock_db,
            patch("routers.finance.date", FrozenDate),
            patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)),
            patch("routers.finance.get_levy_rates",
                  new=AsyncMock(return_value={"admin_annual": 34.08705, "sinking_annual": 9.9505})),
            patch("routers.finance.get_user_permissions", return_value=MagicMock(can_manage_finances=False)),
        ):
            mock_db.units.find_one = AsyncMock(return_value={"entitlement": 139})
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])  # lifetime-paid gather task
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(
                side_effect=[{"total_paid": 0.0, "opening_arrears": 0.0}, None])
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=[])
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA063", year="2026", current_user=user, building_id="13195")

        assert result["annual_levy"] == pytest.approx(6121.22, abs=0.01)
        assert result["period_levy"] == pytest.approx(1530.30, abs=0.01)

        # `standard_amount` is THIS period's own levy in isolation, which is the
        # installment split this test is named for. The assertion used to be made
        # against `amount_due`, which is the CUMULATIVE gross owing (own levy plus
        # arrears carried forward from earlier overdue periods) — a different figure
        # that this fixture actively contradicts: the frozen date of 2026-04-01 puts
        # Q1 past its 2026-03-01 due date plus 14 days grace, with no payments, so
        # Q1's 1530.30 legitimately rolls into Q2 and Q2's amount_due is 3060.60.
        # Asserting a flat per-quarter amount against amount_due could therefore never
        # hold, and this test has failed continuously since 8ab3e1a5 (2026-08-12),
        # where it was recorded as one of five known-unfixed baseline failures.
        assert [q["standard_amount"] for q in result["quarters"]] == [
            pytest.approx(1530.30, abs=0.01),
            pytest.approx(1530.30, abs=0.01),
            pytest.approx(1530.30, abs=0.01),
            pytest.approx(1530.32, abs=0.01),
        ]

        # The point of a residual final installment: the four instalments must sum to
        # the annual levy EXACTLY, with the rounding remainder absorbed by the last one
        # rather than silently lost. 6121.22 / 4 = 1530.305, so three quarters round to
        # 1530.30 (4590.90) and Q4 carries the remaining 1530.32.
        assert sum(q["standard_amount"] for q in result["quarters"]) == pytest.approx(
            result["annual_levy"], abs=0.01
        )
        assert result["quarters"][-1]["standard_amount"] > result["quarters"][0]["standard_amount"]

        # The overdue Q1 rollforward that makes the flat-amount_due assertion impossible,
        # pinned explicitly so the two concepts cannot be conflated again.
        quarters = {q["quarter"]: q for q in result["quarters"]}
        assert quarters["Q1"]["status"] == "overdue"
        assert quarters["Q1"]["amount_due"] == pytest.approx(1530.30, abs=0.01)
        assert quarters["Q2"]["amount_due"] == pytest.approx(3060.60, abs=0.01)


    @pytest.mark.asyncio
    async def test_ledger_row_without_net_balance_is_not_read_as_paid_up(self):
        """A ledger row carrying no net_balance must not be read as "owes nothing".

        paid_this_year is derived as `levied_due_to_date - ledger_net_balance`. If a
        missing net_balance defaults to 0.0, that arithmetic reports a unit which has
        paid nothing as fully paid up to date, for every period already due — a silent
        financial misstatement rather than a visible error. All 546 live
        unit_levy_ledger rows carry net_balance (verified 2026-08-20), so this guards a
        shape production does not currently produce but an importer could.
        """
        from datetime import date as real_date
        from routers.finance import get_levy_status

        class FrozenDate(real_date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 1)

        user = _make_user(role="owner", unit_number="UA063", can_manage=False)
        settings = {
            "grace_period_days": 14,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "first",
        }
        levy = {
            "building_id": "13195",
            "year": "2026",
            "payment_schedule": [
                {"quarter": "Q1", "due_date": "2026-03-01"},
                {"quarter": "Q2", "due_date": "2026-06-01"},
                {"quarter": "Q3", "due_date": "2026-09-01"},
                {"quarter": "Q4", "due_date": "2026-12-01"},
            ],
        }

        with (
            patch("routers.finance.db") as mock_db,
            patch("routers.finance.date", FrozenDate),
            patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)),
            patch("routers.finance.get_levy_rates",
                  new=AsyncMock(return_value={"admin_annual": 34.08705, "sinking_annual": 9.9505})),
            patch("routers.finance.get_user_permissions", return_value=MagicMock(can_manage_finances=False)),
        ):
            mock_db.units.find_one = AsyncMock(return_value={"entitlement": 139})
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 0.0}])
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            # Ledger row present but with NO net_balance key, and no payments anywhere.
            mock_db.unit_levy_ledger.find_one = AsyncMock(
                side_effect=[{"total_paid": 0.0, "opening_arrears": 0.0}, None])
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=[])
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA063", year="2026", current_user=user, building_id="13195")

        quarters = {q["quarter"]: q for q in result["quarters"]}
        # Nothing was paid, so the already-due Q1 must read as overdue and still owing.
        assert quarters["Q1"]["status"] == "overdue"
        assert quarters["Q1"]["amount_due"] == pytest.approx(1530.30, abs=0.01)
        assert quarters["Q1"]["amount_paid"] == pytest.approx(0.0, abs=0.01)
        assert result["total_paid"] == pytest.approx(0.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_ledger_net_balance_of_zero_is_authoritative_paid_up(self):
        """net_balance == 0 is a real "paid up", not a missing value.

        The guard above must test the VALUE for None, not truthiness — a falsy-but-
        present 0 is the single most common state for a unit that has paid on time.
        """
        from datetime import date as real_date
        from routers.finance import get_levy_status

        class FrozenDate(real_date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 1)

        user = _make_user(role="owner", unit_number="UA063", can_manage=False)
        settings = {
            "grace_period_days": 14,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "first",
        }
        levy = {
            "building_id": "13195",
            "year": "2026",
            "payment_schedule": [
                {"quarter": "Q1", "due_date": "2026-03-01"},
                {"quarter": "Q2", "due_date": "2026-06-01"},
                {"quarter": "Q3", "due_date": "2026-09-01"},
                {"quarter": "Q4", "due_date": "2026-12-01"},
            ],
        }

        with (
            patch("routers.finance.db") as mock_db,
            patch("routers.finance.date", FrozenDate),
            patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)),
            patch("routers.finance.get_levy_rates",
                  new=AsyncMock(return_value={"admin_annual": 34.08705, "sinking_annual": 9.9505})),
            patch("routers.finance.get_user_permissions", return_value=MagicMock(can_manage_finances=False)),
        ):
            mock_db.units.find_one = AsyncMock(return_value={"entitlement": 139})
            mock_db.unit_levy_ledger.aggregate.return_value.to_list = AsyncMock(return_value=[{"total": 1530.30}])
            mock_db.annual_levies.find_one = AsyncMock(return_value=levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(
                side_effect=[{"total_paid": 1530.30, "opening_arrears": 0.0, "net_balance": 0.0}, None])
            mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=[])
            mock_db._db.journal_entries.find.return_value = _cursor([])
            mock_db.strata_owners.find_one = AsyncMock(return_value=None)

            result = await get_levy_status("UA063", year="2026", current_user=user, building_id="13195")

        quarters = {q["quarter"]: q for q in result["quarters"]}
        # Levied-to-date 1530.30 minus a net balance of 0 = 1530.30 paid, clearing Q1.
        assert quarters["Q1"]["status"] == "paid"
        assert quarters["Q1"]["amount_due"] == pytest.approx(0.0, abs=0.01)
        # And with Q1 settled, Q2 shows only its own instalment, no rollforward.
        assert quarters["Q2"]["amount_due"] == pytest.approx(1530.30, abs=0.01)


# ─── TestButtonDisableLogic ───────────────────────────────────────────────────

class TestButtonDisableLogic:
    """
    Validates that the quarter 'status' field returned by /levy-status
    supports correct frontend button-disable logic:
      Disabled:  status='paid' | status='pending' | (status='partial' AND past_deadline)
      Enabled:   status='upcoming' | status='overdue' | (status='partial' AND NOT past_deadline)
    """

    def _make_quarter(self, status, past_deadline=False):
        return {"status": status, "past_deadline": past_deadline}

    def _should_disable(self, q):
        return (
                q["status"] == "paid"
                or q["status"] == "pending"
                or (q["status"] == "partial" and q["past_deadline"])
        )

    def test_paid_is_disabled(self):
        assert self._should_disable(self._make_quarter("paid")) is True

    def test_pending_is_disabled(self):
        assert self._should_disable(self._make_quarter("pending")) is True

    def test_partial_past_deadline_is_disabled(self):
        assert self._should_disable(self._make_quarter("partial", past_deadline=True)) is True

    def test_partial_before_deadline_is_enabled(self):
        assert self._should_disable(self._make_quarter("partial", past_deadline=False)) is False

    def test_upcoming_is_enabled(self):
        assert self._should_disable(self._make_quarter("upcoming")) is False

    def test_overdue_is_enabled(self):
        """No payment + past deadline — owner can still self-report a late payment."""
        assert self._should_disable(self._make_quarter("overdue")) is False
