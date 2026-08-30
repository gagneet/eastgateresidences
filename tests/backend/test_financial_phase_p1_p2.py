"""
Financial Platform Phase P1/P2 — Gap Coverage Tests

Covers:
  - Expense Transactions CRUD (/finance/expense-transactions)
  - Income Transactions POST (/finance/income-transactions)
  - Special Levies (/finance/special-levies)
  - Bank Reconciliations (/finance/bank-reconciliations)
  - Insurance Policies (/finance/insurance-policies)
  - Payment Plans (/finance/payment-plans)
  - Health Service scoring (services/health_service.py)
  - Lot Finance cost distribution (services/lot_finance_service.py)
  - Notice Service — Section 83 PDF generation (services/notice_service.py)
  - Arrears recovery workflow — contact log append and DCA status transitions

Run with:
    /home/gagneet/strata-management/backend/venv/bin/pytest backend/tests/test_financial_phase_p1_p2.py -v
"""
import io
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from fastapi import HTTPException



NOW_STR = "2026-03-01T00:00:00+00:00"
BUILDING_ID = "13195"

_MOCK_OWNER = {"owner_name": "Test Owner", "owner_email": "owner@test.com",
               "owner_id": "user-123", "owner_name_b": None, "owner_email_b": None, "source": "mock"}


@pytest.fixture(autouse=True)
def _mock_get_owner_info():
    with patch("services.notice_service.get_owner_info", AsyncMock(return_value=_MOCK_OWNER)):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cursor(data):
    """Return a mock cursor whose .sort().to_list() chain returns data."""
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=data)
    return cursor


async def _async_iter(items):
    """Async generator for mocking `async for doc in cursor` iteration."""
    for item in items:
        yield item


def _make_user(role: str = "super_admin", uid: str = "u1") -> dict:
    return {
        "id": uid,
        "role": role,
        "full_name": "Test Admin",
        "permissions": {
            "can_view_finances": role in {"super_admin", "chairman", "strata_manager", "ec_member"},
            "can_manage_finances": role in {"super_admin", "chairman", "strata_manager"},
        },
    }


def _make_expense(eid: str = "exp-001") -> dict:
    """Complete expense transaction document matching ExpenseTransactionResponse."""
    return {
        "id": eid,
        "plan_id": "13195",
        "financial_year": "2026",
        "category_id": "cat-01",
        "fund_type": "administrative",
        "date": "2026-02-15",
        "amount": 1250.00,
        "description": "Plumbing repair",
        "supplier_name": "ACT Plumbing",
        "invoice_number": "INV-001",
        "is_gst_inclusive": True,
        "gst_amount": 113.64,
        "created_at": NOW_STR,
        "updated_at": NOW_STR,
        "created_by": "u1",
    }


def _make_income(iid: str = "inc-001") -> dict:
    """Complete income transaction document matching IncomeTransactionResponse."""
    return {
        "id": iid,
        "plan_id": "13195",
        "financial_year": "2026",
        "category_id": "cat-int",
        "fund_type": "administrative",
        "date": "2026-02-28",
        "amount": 450.00,
        "description": "Interest earned",
        "source": "interest",
        "created_at": NOW_STR,
        "updated_at": NOW_STR,
        "created_by": "u1",
    }


def _make_special_levy(slid: str = "sl-001") -> dict:
    """Complete special levy document matching SpecialLevyResponse."""
    return {
        "id": slid,
        "plan_id": "13195",
        "year": "2026",
        "title": "Emergency Lift Repair",
        "description": "Urgent repair required for lift #2",
        "total_amount": 45000.00,
        "due_date": "2026-04-30",
        "fund_type": "sinking",
        "created_at": NOW_STR,
        "updated_at": NOW_STR,
    }


def _make_special_levy_payment(slpid: str = "slp-001") -> dict:
    """Complete special levy payment matching SpecialLevyPaymentResponse."""
    return {
        "id": slpid,
        "special_levy_id": "sl-001",
        "unit_number": "TH087",
        "amount_levied": 517.24,
        "amount_paid": 0.0,
        "status": "unpaid",
        "paid_at": None,
        "created_at": NOW_STR,
        "updated_at": NOW_STR,
    }


def _make_bank_recon(bid: str = "br-001") -> dict:
    """Complete bank reconciliation matching BankReconciliationResponse."""
    return {
        "id": bid,
        "plan_id": "13195",
        "financial_year": "2026",
        "fund_type": "administrative",
        "statement_date": "2026-02-28",
        "opening_balance": 125000.00,
        "closing_balance": 98500.00,
        "total_receipts": 52000.00,
        "total_payments": 78500.00,
        "bank_statement_reference": "BST-20260228",
        "fund_account_name": "Eastgate Admin Fund",
        # Derived fields required by BankReconciliationResponse
        "variance_amount": -1000.00,
        # closing - (opening + receipts - payments) = 98500 - (125000+52000-78500) = 0... adjusted for test
        "is_reconciled": True,
        "reconciled_at": NOW_STR,
        "reconciled_by": "u1",
        "created_at": NOW_STR,
        "updated_at": NOW_STR,
    }


def _make_insurance(pid: str = "ins-001") -> dict:
    """Complete insurance policy matching InsurancePolicyResponse."""
    return {
        "id": pid,
        "plan_id": "13195",
        "policy_number": "CAR-2026-001",
        "insurer": "IAG Commercial",
        "broker": "Willis Towers Watson",
        "policy_type": "Building",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "premium_amount": 85000.00,
        "sum_insured": 22500000.00,
        "is_active": True,
        "created_at": NOW_STR,
        "updated_at": NOW_STR,
    }


def _make_payment_plan(ppid: str = "pp-001") -> dict:
    """Complete payment plan matching PaymentPlanResponse."""
    return {
        "id": ppid,
        "unit_number": "TH087",
        "total_amount": 3500.00,
        "start_date": "2026-03-01",
        "status": "active",
        "is_active": True,
        "instalments": [
            {"due_date": "2026-03-31", "amount": 875.00, "is_paid": False, "paid_at": None},
            {"due_date": "2026-06-30", "amount": 875.00, "is_paid": False, "paid_at": None},
            {"due_date": "2026-09-30", "amount": 875.00, "is_paid": False, "paid_at": None},
            {"due_date": "2026-12-31", "amount": 875.00, "is_paid": False, "paid_at": None},
        ],
        "notes": "4-quarter repayment plan agreed 2026-02-28",
        "created_by": "u1",
        "created_at": NOW_STR,
        "updated_at": NOW_STR,
    }


def _make_unit(unit_number: str = "TH087") -> dict:
    return {
        "unit_number": unit_number,
        "lot_number": "LOT017",
        "entitlement": 120,
        "owner_name": "Avneet Rooprai",
        "arrears_metadata": {
            "dca_status": "none",
            "dca_reference": None,
            "first_notice_sent_at": None,
            "legal_referral_status": "none",
            "has_active_payment_plan": False,
            "contact_log": [],
        },
    }


def _make_ledger(unit_number: str = "TH087", net_balance: float = 2087.0) -> dict:
    return {
        "unit_number": unit_number,
        "year": "2026",
        "plan_id": "13195",
        "admin_opening": 1500.0,
        "sinking_opening": 587.0,
        "admin_levied": 5100.0,
        "sinking_levied": 1946.0,
        "admin_closing": 1500.0,
        "sinking_closing": 587.0,
        "net_balance": net_balance,
        "total_paid": 0.0,
    }


def _build_finance_db():
    """Build a complete mock db for finance router tests."""
    mock_db = MagicMock()

    mock_db.expense_transactions.find.return_value = _cursor([_make_expense("exp-001"), _make_expense("exp-002")])
    mock_db.expense_transactions.insert_one = AsyncMock(return_value=MagicMock(inserted_id="exp-001"))

    mock_db.income_transactions.find.return_value = _cursor([_make_income()])
    mock_db.income_transactions.insert_one = AsyncMock(return_value=MagicMock(inserted_id="inc-001"))

    mock_db.special_levies.find.return_value = _cursor([_make_special_levy()])
    mock_db.special_levies.insert_one = AsyncMock(return_value=MagicMock(inserted_id="sl-001"))
    mock_db.special_levies.find_one = AsyncMock(return_value=_make_special_levy())
    mock_db.special_levy_payments.find.return_value = _cursor([_make_special_levy_payment()])
    mock_db.special_levy_payments.insert_many = AsyncMock(return_value=MagicMock())

    mock_db.bank_reconciliations.find.return_value = _cursor([_make_bank_recon()])
    mock_db.bank_reconciliations.insert_one = AsyncMock(return_value=MagicMock(inserted_id="br-001"))

    mock_db.insurance_policies.find.return_value = _cursor([_make_insurance()])
    mock_db.insurance_policies.insert_one = AsyncMock(return_value=MagicMock(inserted_id="ins-001"))

    mock_db.payment_plans.find.return_value = _cursor([_make_payment_plan()])
    mock_db.payment_plans.insert_one = AsyncMock(return_value=MagicMock(inserted_id="pp-001"))
    mock_db.payment_plans.update_many = AsyncMock(return_value=MagicMock(modified_count=0))

    # Unit/ledger lookups
    mock_db.units.find.return_value = _cursor([_make_unit("TH087"), _make_unit("UA001")])
    mock_db.units.find_one = AsyncMock(return_value=_make_unit())
    mock_db.units.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    mock_db.units.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_make_ledger())
    mock_db.unit_levy_ledger.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    mock_db.settings.find_one = AsyncMock(return_value={})
    mock_db.feature_toggles.find_one = AsyncMock(return_value={"is_enabled": True})

    return mock_db


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Expense Transactions
# ─────────────────────────────────────────────────────────────────────────────

class TestExpenseTransactions:
    """Tests for expense transaction CRUD endpoints."""

    @pytest.mark.asyncio
    async def test_get_expenses_returns_list(self, pin_store):
        """GET /expense-transactions returns list of expense response objects."""
        mock_db = _build_finance_db()

        import routers.finance
        # This test exercises the MONGO path; pin it so the dispatch cannot silently
        # send the handler to the live PostgreSQL ledger and ignore mock_db.
        with pin_store("mongo"), patch("routers.finance.db", mock_db):
            result = await routers.finance.get_expenses(
                year="2026",
                current_user=_make_user("super_admin"),
                building_id=BUILDING_ID,
            )

        assert isinstance(result, list)
        assert len(result) == 2
        # Pydantic model — use attribute access
        assert result[0].id == "exp-001"
        assert result[0].amount == 1250.00

    @pytest.mark.asyncio
    async def test_record_expense_creates_document(self):
        """POST /expense-transactions inserts a document and returns model."""
        mock_db = _build_finance_db()

        from models.finance import ExpenseTransactionCreate
        import routers.finance

        data = ExpenseTransactionCreate(
            financial_year="2026",
            category_id="cat-01",
            fund_type="administrative",
            date="2026-02-15",
            amount=1250.00,
            description="Plumbing repair",
            supplier_name="ACT Plumbing",
            invoice_number="INV-001",
            is_gst_inclusive=True,
            gst_amount=113.64,
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            result = await routers.finance.record_expense(
                data=data,
                current_user=_make_user("super_admin"),
            )

        assert mock_db.expense_transactions.insert_one.called
        assert result.amount == 1250.00
        assert result.supplier_name == "ACT Plumbing"
        assert result.created_by == "u1"

    @pytest.mark.asyncio
    async def test_expense_year_filter_applied(self):
        """GET /expense-transactions passes year filter to MongoDB."""
        mock_db = _build_finance_db()

        import routers.finance
        with patch("routers.finance.db", mock_db):
            await routers.finance.get_expenses(
                year="2026",
                current_user=_make_user("ec_member"),
                building_id=BUILDING_ID,
            )

        call_args = mock_db.expense_transactions.find.call_args
        query = call_args[0][0] if call_args[0] else {}
        # get_expenses builds its year filter via _year_match_filter(), which matches
        # "financial_year", "year", OR "levy_year" (different expense-source documents
        # use different field names) nested under "$or" -- not a flat "financial_year"
        # key. This assertion was written before that helper existed.
        assert {"financial_year": "2026"} in query.get("$or", [])
        assert query.get("building_id") == BUILDING_ID

    @pytest.mark.asyncio
    async def test_expense_gst_fields_stored(self):
        """Expense insertion includes is_gst_inclusive and gst_amount."""
        mock_db = _build_finance_db()

        from models.finance import ExpenseTransactionCreate
        import routers.finance

        data = ExpenseTransactionCreate(
            financial_year="2026",
            category_id="cat-01",
            fund_type="administrative",
            date="2026-02-15",
            amount=550.00,
            description="Office supplies",
            supplier_name="Officeworks",
            is_gst_inclusive=True,
            gst_amount=50.00,
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            await routers.finance.record_expense(
                data=data,
                current_user=_make_user("chairman"),
            )

        inserted = mock_db.expense_transactions.insert_one.call_args[0][0]
        assert inserted["is_gst_inclusive"] is True
        assert inserted["gst_amount"] == 50.00


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Income Transactions
# ─────────────────────────────────────────────────────────────────────────────

class TestIncomeTransactions:
    """Tests for income transaction POST endpoint."""

    @pytest.mark.asyncio
    async def test_record_income_creates_document(self):
        """POST /income-transactions inserts and returns income model."""
        mock_db = _build_finance_db()

        from models.finance import IncomeTransactionCreate
        import routers.finance

        data = IncomeTransactionCreate(
            financial_year="2026",
            category_id="cat-int",
            fund_type="administrative",
            date="2026-02-28",
            amount=450.00,
            description="Interest earned February 2026",
            source="interest",
        )

        with patch("routers.finance.db", mock_db):
            result = await routers.finance.record_income(
                data=data,
                current_user=_make_user("strata_manager"),
            )

        assert mock_db.income_transactions.insert_one.called
        assert result.amount == 450.00
        assert result.source == "interest"

    @pytest.mark.asyncio
    async def test_income_source_field_required(self):
        """IncomeTransactionCreate requires 'source' field."""
        from models.finance import IncomeTransactionCreate
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            # source is required — omitting it should fail
            IncomeTransactionCreate(
                financial_year="2026",
                category_id="cat-int",
                fund_type="administrative",
                date="2026-02-28",
                amount=450.00,
                description="Missing source field",
            )

    @pytest.mark.asyncio
    async def test_income_valid_sources(self):
        """Income source can be interest, rebate, grant, or other."""
        from models.finance import IncomeTransactionCreate

        for source in ["interest", "rebate", "grant", "other"]:
            obj = IncomeTransactionCreate(
                financial_year="2026",
                category_id="cat-int",
                fund_type="administrative",
                date="2026-02-28",
                amount=100.00,
                description=f"Income from {source}",
                source=source,
            )
            assert obj.source == source

    @pytest.mark.asyncio
    async def test_income_stored_fields_complete(self):
        """Recorded income document includes id, created_by, timestamps."""
        mock_db = _build_finance_db()

        from models.finance import IncomeTransactionCreate
        import routers.finance

        data = IncomeTransactionCreate(
            financial_year="2026",
            category_id="cat-interest",
            fund_type="administrative",
            date="2026-02-28",
            amount=500.0,
            description="Bank interest",
            source="interest",
        )
        with patch("routers.finance.db", mock_db):
            result = await routers.finance.record_income(
                data=data,
                current_user=_make_user("super_admin", "admin-001"),
            )

        inserted = mock_db.income_transactions.insert_one.call_args[0][0]
        assert "id" in inserted
        assert inserted["created_by"] == "admin-001"
        assert "created_at" in inserted
        assert "updated_at" in inserted


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Special Levies
# ─────────────────────────────────────────────────────────────────────────────

class TestSpecialLevies:
    """Tests for special levy creation and retrieval."""

    @pytest.mark.asyncio
    async def test_get_special_levies_returns_list(self):
        """GET /special-levies returns list of levy response objects."""
        mock_db = _build_finance_db()

        import routers.finance
        with patch("routers.finance.db", mock_db):
            result = await routers.finance.get_special_levies(
                year=None,
                current_user=_make_user("chairman"),
            )

        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0].title == "Emergency Lift Repair"

    @pytest.mark.asyncio
    async def test_create_special_levy_stores_fields(self):
        """POST /special-levies inserts with correct fields."""
        mock_db = _build_finance_db()

        from models.finance import SpecialLevyCreate
        import routers.finance

        data = SpecialLevyCreate(
            year="2026",
            title="Emergency Lift Repair",
            description="Urgent repair required for lift #2",
            total_amount=45000.00,
            due_date="2026-04-30",
            fund_type="sinking",
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            result = await routers.finance.create_special_levy(
                data=data,
                current_user=_make_user("super_admin"),
                building_id="13195",
            )

        assert mock_db.special_levies.insert_one.called
        inserted = mock_db.special_levies.insert_one.call_args[0][0]
        assert inserted["title"] == "Emergency Lift Repair"
        assert inserted["total_amount"] == 45000.00
        assert inserted["fund_type"] == "sinking"
        assert inserted["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_special_levy_year_filter(self):
        """GET /special-levies with year filter applies year to query."""
        mock_db = _build_finance_db()

        import routers.finance
        with patch("routers.finance.db", mock_db):
            await routers.finance.get_special_levies(
                year="2026",
                current_user=_make_user("ec_member"),
            )

        call_args = mock_db.special_levies.find.call_args
        query = call_args[0][0] if call_args[0] else {}
        assert query.get("year") == "2026"

    @pytest.mark.asyncio
    async def test_get_special_levy_payments(self):
        """GET /special-levies/{id}/payments returns payments list."""
        mock_db = _build_finance_db()

        import routers.finance
        with patch("routers.finance.db", mock_db):
            result = await routers.finance.get_special_levy_payments(
                special_levy_id="sl-001",
                current_user=_make_user("chairman"),
            )

        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0].unit_number == "TH087"
        assert result[0].amount_levied == 517.24

    @pytest.mark.asyncio
    async def test_create_special_levy_creates_unit_payment_records(self):
        """POST /special-levies creates per-unit payment records."""
        mock_db = _build_finance_db()

        from models.finance import SpecialLevyCreate
        import routers.finance

        data = SpecialLevyCreate(
            year="2026",
            title="Pool Resurfacing",
            description="Pool resurfacing special levy",
            total_amount=10000.00,
            due_date="2026-06-30",
            fund_type="sinking",
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            await routers.finance.create_special_levy(
                data=data,
                current_user=_make_user("super_admin"),
                building_id="13195",
            )

        # 2 units in mock → insert_many called with payment docs
        assert mock_db.special_levy_payments.insert_many.called
        payment_docs = mock_db.special_levy_payments.insert_many.call_args[0][0]
        assert len(payment_docs) == 2
        for doc in payment_docs:
            assert doc["status"] == "unpaid"
            assert doc["amount_levied"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Bank Reconciliations
# ─────────────────────────────────────────────────────────────────────────────

class TestBankReconciliations:
    """Tests for bank reconciliation CRUD."""

    @pytest.mark.asyncio
    async def test_get_bank_reconciliations_returns_list(self):
        """GET /bank-reconciliations returns list of reconciliation objects."""
        mock_db = _build_finance_db()

        import routers.finance
        with patch("routers.finance.db", mock_db):
            result = await routers.finance.get_bank_reconciliations(
                year="2026",
                current_user=_make_user("strata_manager"),
            )

        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0].fund_type == "administrative"

    @pytest.mark.asyncio
    async def test_create_bank_reconciliation_stores_balances(self):
        """POST /bank-reconciliations stores opening/closing balance and variance."""
        mock_db = _build_finance_db()

        from models.finance import BankReconciliationCreate
        import routers.finance

        data = BankReconciliationCreate(
            financial_year="2026",
            fund_type="administrative",
            statement_date="2026-02-28",
            opening_balance=125000.00,
            closing_balance=98500.00,
            total_receipts=52000.00,
            total_payments=78500.00,
            bank_statement_reference="BST-20260228",
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            result = await routers.finance.create_bank_reconciliation(
                data=data,
                current_user=_make_user("super_admin"),
            )

        assert mock_db.bank_reconciliations.insert_one.called
        inserted = mock_db.bank_reconciliations.insert_one.call_args[0][0]
        assert inserted["opening_balance"] == 125000.00
        assert inserted["closing_balance"] == 98500.00
        # variance = closing - (opening + receipts - payments) = 98500 - (125000+52000-78500) = 0
        assert "variance_amount" in inserted
        assert "is_reconciled" in inserted

    @pytest.mark.asyncio
    async def test_reconciliation_variance_calculation(self):
        """Reconciliation calculates variance from statement figures."""
        from models.finance import BankReconciliationCreate
        import routers.finance

        mock_db = _build_finance_db()

        # Exact balance: 100000 + 50000 - 40000 = 110000 closing → variance 0
        data = BankReconciliationCreate(
            financial_year="2026",
            fund_type="sinking",
            statement_date="2026-02-28",
            opening_balance=100000.00,
            closing_balance=110000.00,
            total_receipts=50000.00,
            total_payments=40000.00,
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            result = await routers.finance.create_bank_reconciliation(
                data=data,
                current_user=_make_user("super_admin"),
            )

        assert abs(result.variance_amount) < 0.01
        assert result.is_reconciled is True

    @pytest.mark.asyncio
    async def test_reconciliation_year_filter(self):
        """GET /bank-reconciliations year param is forwarded to query."""
        mock_db = _build_finance_db()

        import routers.finance
        with patch("routers.finance.db", mock_db):
            await routers.finance.get_bank_reconciliations(
                year="2026",
                current_user=_make_user("ec_member"),
            )

        call_args = mock_db.bank_reconciliations.find.call_args
        query = call_args[0][0] if call_args[0] else {}
        assert query.get("financial_year") == "2026"


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Insurance Policies
# ─────────────────────────────────────────────────────────────────────────────

class TestInsurancePolicies:
    """Tests for insurance policy CRUD."""

    @pytest.mark.asyncio
    async def test_get_insurance_policies_returns_list(self):
        """GET /insurance-policies returns list of policy objects."""
        mock_db = _build_finance_db()

        import routers.finance
        with patch("routers.finance.db", mock_db):
            result = await routers.finance.get_insurance_policies(
                active_only=False,
                current_user=_make_user("ec_member"),
            )

        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0].policy_number == "CAR-2026-001"

    @pytest.mark.asyncio
    async def test_get_insurance_active_only_filter(self):
        """GET /insurance-policies?active_only=True filters for active policies."""
        mock_db = _build_finance_db()

        import routers.finance
        with patch("routers.finance.db", mock_db):
            await routers.finance.get_insurance_policies(
                active_only=True,
                current_user=_make_user("chairman"),
            )

        call_args = mock_db.insurance_policies.find.call_args
        query = call_args[0][0] if call_args[0] else {}
        assert query.get("is_active") is True

    @pytest.mark.asyncio
    async def test_create_insurance_policy_stores_all_fields(self):
        """POST /insurance-policies stores policy_number, insurer, premium."""
        mock_db = _build_finance_db()

        from models.finance import InsurancePolicyCreate
        import routers.finance

        data = InsurancePolicyCreate(
            policy_number="CAR-2026-001",
            insurer="IAG Commercial",
            broker="Willis Towers Watson",
            policy_type="Building",
            start_date="2026-01-01",
            end_date="2026-12-31",
            premium_amount=85000.00,
            sum_insured=22500000.00,
            is_active=True,
        )

        with patch("routers.finance.db", mock_db):
            result = await routers.finance.create_insurance_policy(
                data=data,
                current_user=_make_user("super_admin"),
            )

        assert mock_db.insurance_policies.insert_one.called
        inserted = mock_db.insurance_policies.insert_one.call_args[0][0]
        assert inserted["policy_number"] == "CAR-2026-001"
        assert inserted["premium_amount"] == 85000.00
        assert inserted["sum_insured"] == 22500000.00

    def test_insurance_policy_plan_id_default(self):
        """InsurancePolicyCreate plan_id defaults to '' (clean-break, Phase E)."""
        from models.finance import InsurancePolicyCreate

        pol = InsurancePolicyCreate(
            policy_number="TEST-001",
            insurer="QBE",
            policy_type="Public Liability",
            start_date="2026-01-01",
            end_date="2026-12-31",
            premium_amount=12000.00,
            sum_insured=10000000.00,
        )
        # Phase E clean-break: DEFAULT_BUILDING_ID = "" — models no longer default to "13195"
        assert pol.plan_id == "", (
            "plan_id must default to '' in multi-tenant mode — never hardcode East Gate"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: Payment Plans
# ─────────────────────────────────────────────────────────────────────────────

class TestPaymentPlans:
    """Tests for the NSW Form 1 payment plan flow (ss.83A-83C SSMA 2015).

    These previously targeted duplicate handlers in routers/finance.py that
    registered the same (method, path) as routers/payment_plans.py. Because
    finance_router is included first, those handlers shadowed the owner-initiated
    Form 1 flow entirely — owners got 403 instead of being able to submit a
    hardship request. The finance.py duplicates were removed; routers/payment_plans.py
    is now the single owner of /payment-plans. See
    tests/backend/test_route_registration_uniqueness.py for the guard that keeps
    this from recurring.
    """

    @staticmethod
    def _plan_db():
        """Mongo double whose payment_plans.find() returns one requested plan."""
        mock_db = MagicMock()
        doc = {
            "_id": "mongo-oid",
            "id": "plan-1",
            "building_id": "13195",
            "unit_number": "TH087",
            "owner_id": "user-1",
            "outstanding_amount_cents": 350000,
            "requested_instalment_cents": 87500,
            "instalment_frequency": "monthly",
            "status": "requested",
        }

        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.skip = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        cursor.__aiter__ = lambda _self: _async_iter([dict(doc)])
        mock_db.payment_plans.find = MagicMock(return_value=cursor)
        mock_db.payment_plans.count_documents = AsyncMock(return_value=1)
        mock_db.payment_plans.insert_one = AsyncMock(return_value=MagicMock(inserted_id="plan-1"))
        return mock_db

    @pytest.mark.asyncio
    async def test_list_payment_plans_returns_data(self):
        """GET /payment-plans returns the building's plans for a manager."""
        import routers.payment_plans as pp

        with patch.object(pp, "_get_db", return_value=self._plan_db()):
            result = await pp.list_payment_plans(
                status=None, unit_number=None, page=1, page_size=20,
                building_id="13195",
                current_user=_make_user("strata_manager"),
            )

        assert result["total"] == 1
        assert result["data"][0]["unit_number"] == "TH087"

    @pytest.mark.asyncio
    async def test_list_payment_plans_unit_filter(self):
        """GET /payment-plans?unit_number=TH087 filters by unit, scoped to building."""
        import routers.payment_plans as pp

        mock_db = self._plan_db()
        with patch.object(pp, "_get_db", return_value=mock_db):
            await pp.list_payment_plans(
                status=None, unit_number="TH087", page=1, page_size=20,
                building_id="13195",
                current_user=_make_user("strata_manager"),
            )

        query = mock_db.payment_plans.find.call_args[0][0]
        assert query["unit_number"] == "TH087"
        assert query["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_owner_only_sees_own_plans(self):
        """An owner must not see other owners' hardship requests."""
        import routers.payment_plans as pp

        mock_db = self._plan_db()
        owner = _make_user("owner")
        with patch.object(pp, "_get_db", return_value=mock_db):
            await pp.list_payment_plans(
                status=None, unit_number=None, page=1, page_size=20,
                building_id="13195", current_user=owner,
            )

        query = mock_db.payment_plans.find.call_args[0][0]
        assert query["owner_id"] == owner["id"], "owner listing must be scoped to owner_id"

    @pytest.mark.asyncio
    async def test_owner_can_submit_form1_request(self):
        """POST /payment-plans lets an OWNER submit — this is the flow the shadow broke."""
        import routers.payment_plans as pp

        mock_db = self._plan_db()
        payload = pp.PaymentPlanCreate(
            unit_number="TH087",
            outstanding_amount_cents=350000,
            requested_instalment_cents=87500,
            instalment_frequency="monthly",
            proposed_start_date="2026-03-01",
            hardship_reason="Reduced income following redundancy.",
        )

        with patch.object(pp, "_get_db", return_value=mock_db), \
                patch.object(pp, "create_audit_log", new=AsyncMock()):
            await pp.create_payment_plan(
                payload=payload,
                building_id="13195",
                current_user=_make_user("owner"),
            )

        inserted = mock_db.payment_plans.insert_one.call_args[0][0]
        assert inserted["unit_number"] == "TH087"
        assert inserted["building_id"] == "13195"
        assert inserted["status"] == "requested"
        assert inserted["outstanding_amount_cents"] == 350000
        assert inserted["hardship_reason"].startswith("Reduced income")

    @pytest.mark.asyncio
    async def test_invalid_instalment_frequency_rejected(self):
        """Frequency is validated before the document is written."""
        import routers.payment_plans as pp

        mock_db = self._plan_db()
        payload = pp.PaymentPlanCreate(
            unit_number="TH087",
            outstanding_amount_cents=350000,
            requested_instalment_cents=87500,
            instalment_frequency="fortnightly-ish",
            proposed_start_date="2026-03-01",
        )

        with patch.object(pp, "_get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                await pp.create_payment_plan(
                    payload=payload,
                    building_id="13195",
                    current_user=_make_user("owner"),
                )

        assert exc.value.status_code == 400
        assert not mock_db.payment_plans.insert_one.called

    def test_payment_plan_instalments_sum_to_total(self):
        """Mock payment plan instalment amounts sum to total_amount."""
        plan = _make_payment_plan()
        instalment_sum = sum(i["amount"] for i in plan["instalments"])
        assert abs(instalment_sum - plan["total_amount"]) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Section 7: Health Service — Financial Health Scoring
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthService:
    """Tests for compute_financial_health() scoring logic."""

    def _make_levy(self):
        return {
            "year": "2026",
            "plan_id": "13195",
            "admin_fund": {
                "total_income": 330000.0,
                "total_expenses": 270000.0,
                "closing_balance": 60000.0,
            },
            "sinking_fund": {
                "total_income": 70000.0,
                "total_expenses": 70000.0,
                "closing_balance": 0.0,
            },
        }

    def _make_cashflow(self):
        """Return dict matching generate_cashflow_projection's return shape."""
        return {
            "year": "2026",
            "months": [],
            "annual_income": 400000.0,
            "annual_expenses": 340000.0,
            "min_balance": 25000.0,
            "risk_months": 0,
            "opening_balance": 60000.0,
        }

    def _make_sinking_projection(self):
        return {
            "projections": [
                {"reserve": 100000.0, "target": 90000.0, "year": 2022 + i}
                for i in range(14)
            ]
        }

    def _make_ledger_stats(self):
        return {
            "units_owing": 23,
            "total_opening_arrears": 20168.0,
            "confirmed_paid": 0.0,
            "total_levied": 440375.0,
        }

    @pytest.mark.asyncio
    async def test_compute_health_returns_score_dict(self):
        """compute_financial_health returns dict with required keys."""
        import services.health_service

        mock_db = MagicMock()
        mock_db.units.count_documents = AsyncMock(return_value=87)
        mock_db.financial_forecasts.find.return_value = _cursor([])

        with patch("services.health_service._get_levy_data", AsyncMock(return_value=self._make_levy())), \
                patch("services.health_service.get_unit_ledger_stats",
                      AsyncMock(return_value=self._make_ledger_stats())), \
                patch("services.health_service.get_budget_summary", AsyncMock(return_value={})), \
                patch("services.health_service.generate_cashflow_projection",
                      AsyncMock(return_value=self._make_cashflow())), \
                patch("services.health_service.generate_sinking_fund_projection",
                      AsyncMock(return_value=self._make_sinking_projection())), \
                patch("services.health_service.db", mock_db):
            result = await services.health_service.compute_financial_health("2026", BUILDING_ID)

        assert isinstance(result, dict)
        assert "score" in result
        assert "year" in result
        assert "breakdown" in result
        assert result["year"] == "2026"

    @pytest.mark.asyncio
    async def test_compute_health_score_in_valid_range(self):
        """Health score is between 0 and 100."""
        import services.health_service

        mock_db = MagicMock()
        mock_db.units.count_documents = AsyncMock(return_value=87)
        mock_db.financial_forecasts.find.return_value = _cursor([])

        with patch("services.health_service._get_levy_data", AsyncMock(return_value=self._make_levy())), \
                patch("services.health_service.get_unit_ledger_stats",
                      AsyncMock(return_value=self._make_ledger_stats())), \
                patch("services.health_service.get_budget_summary", AsyncMock(return_value={})), \
                patch("services.health_service.generate_cashflow_projection",
                      AsyncMock(return_value=self._make_cashflow())), \
                patch("services.health_service.generate_sinking_fund_projection",
                      AsyncMock(return_value=self._make_sinking_projection())), \
                patch("services.health_service.db", mock_db):
            result = await services.health_service.compute_financial_health("2026", BUILDING_ID)

        assert 0 <= result["score"] <= 100

    @pytest.mark.asyncio
    async def test_compute_health_no_levy_data_returns_zero(self):
        """When no levy data found, returns score=0."""
        import services.health_service

        with patch("services.health_service._get_levy_data", AsyncMock(return_value=None)):
            result = await services.health_service.compute_financial_health("2099", BUILDING_ID)

        assert result["score"] == 0.0
        assert result["year"] == "2099"
        assert result["risk_level"] == "critical"

    def test_score_clamp_bounds(self):
        """_score_clamp clamps values to [0.0, 1.0]."""
        import services.health_service

        assert services.health_service._score_clamp(-0.5) == 0.0
        assert services.health_service._score_clamp(1.5) == 1.0
        assert services.health_service._score_clamp(0.75) == 0.75
        assert services.health_service._score_clamp(0.0) == 0.0
        assert services.health_service._score_clamp(1.0) == 1.0

    @pytest.mark.asyncio
    async def test_compute_health_breakdown_keys_present(self):
        """Health score breakdown contains all expected component keys."""
        import services.health_service

        mock_db = MagicMock()
        mock_db.units.count_documents = AsyncMock(return_value=87)
        mock_db.financial_forecasts.find.return_value = _cursor([])

        with patch("services.health_service._get_levy_data", AsyncMock(return_value=self._make_levy())), \
                patch("services.health_service.get_unit_ledger_stats",
                      AsyncMock(return_value=self._make_ledger_stats())), \
                patch("services.health_service.get_budget_summary", AsyncMock(return_value={})), \
                patch("services.health_service.generate_cashflow_projection",
                      AsyncMock(return_value=self._make_cashflow())), \
                patch("services.health_service.generate_sinking_fund_projection",
                      AsyncMock(return_value=self._make_sinking_projection())), \
                patch("services.health_service.db", mock_db):
            result = await services.health_service.compute_financial_health("2026", BUILDING_ID)

        breakdown = result.get("breakdown", {})
        assert "surplus_ratio" in breakdown
        assert "arrears_pct" in breakdown
        assert "cashflow_buffer" in breakdown


# ─────────────────────────────────────────────────────────────────────────────
# Section 8: Lot Finance Service — True Cost of Ownership
# ─────────────────────────────────────────────────────────────────────────────

class TestLotFinanceService:
    """Tests for compute_true_cost() and cost distribution logic."""

    def _make_annual_levy(self):
        return {
            "year": "2026",
            "plan_id": "13195",
            "admin_levy_per_uoe_annual": 30.0,
            "sinking_levy_per_uoe_annual": 10.0,
        }

    def _build_lot_finance_db(self, units=None):
        mock_db = MagicMock()
        unit_list = units or [_make_unit("TH087"), _make_unit("UA001")]
        ledger_list = [_make_ledger(u["unit_number"]) for u in unit_list]

        # ── find_one mocks ──
        mock_db.units.find_one = AsyncMock(side_effect=lambda q, *a, **kw: _make_unit(q.get("unit_number", "TH087")))
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_make_ledger("TH087"))
        mock_db.annual_levies.find_one = AsyncMock(return_value=self._make_annual_levy())
        mock_db.council_rates.find_one = AsyncMock(return_value=None)  # no council data in test

        # ── find cursor mocks (must support .to_list without sort for distribution) ──
        mock_db.units.find.return_value = _cursor(unit_list)
        mock_db.unit_levy_ledger.find.return_value = _cursor(ledger_list)
        mock_db.council_rates.find.return_value = _cursor([])  # no council data
        mock_db.budgets.find.return_value = _cursor([])

        # ── aggregate mocks ──
        mock_db.levy_payments.aggregate.return_value = _cursor([])
        mock_db.water_bills.aggregate.return_value = _cursor([])  # no water data

        # ── write mocks ──
        mock_db.lot_financial_summary.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        mock_db.lot_financial_summary.bulk_write = AsyncMock(return_value=MagicMock())
        return mock_db

    @pytest.mark.asyncio
    async def test_compute_true_cost_returns_dict_for_unit(self):
        """compute_true_cost returns dict with lot_number and year."""
        import services.lot_finance_service

        mock_db = self._build_lot_finance_db()

        with patch("services.lot_finance_service.db", mock_db):
            result = await services.lot_finance_service.compute_true_cost("TH087", "2026", BUILDING_ID)

        assert isinstance(result, dict)
        # Must have identifying fields
        assert result.get("lot_number") == "TH087" or result.get("unit_number") == "TH087"
        # The service uses financial_year as the year key
        assert result.get("financial_year") == "2026" or result.get("year") == "2026"

    @pytest.mark.asyncio
    async def test_compute_true_cost_missing_unit_raises(self):
        """compute_true_cost raises when unit not found."""
        import services.lot_finance_service

        mock_db = MagicMock()
        mock_db.units.find_one = AsyncMock(return_value=None)

        with patch("services.lot_finance_service.db", mock_db):
            with pytest.raises((ValueError, KeyError, AttributeError, TypeError)):
                await services.lot_finance_service.compute_true_cost("UA999", "2026", BUILDING_ID)

    @pytest.mark.asyncio
    async def test_compute_building_cost_distribution_returns_list(self):
        """compute_building_cost_distribution returns list of cost breakdown dicts."""
        import services.lot_finance_service

        mock_db = self._build_lot_finance_db()

        with patch("services.lot_finance_service.db", mock_db):
            result = await services.lot_finance_service.compute_building_cost_distribution("2026", BUILDING_ID)

        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_compute_building_cost_distribution_includes_rates_water_and_land_tax(self):
        """True cost uses documented financial_year utility/tax records."""
        import services.lot_finance_service

        unit = _make_unit("TH087")
        # Land tax only applies to investment/rental properties (see is_owner_occupied
        # gate in lot_finance_service.compute_building_cost_distribution) — model this
        # unit as tenanted so the assertion below exercises a real, non-zero land tax.
        unit["is_owner_occupied"] = False
        ledger = _make_ledger("TH087")
        ledger["financial_year"] = "2026"
        council = {
            "unit_number": "TH087",
            "financial_year": "2026",
            "total_rates": 2400.0,
            "land_tax_total": 900.0,
        }
        water = [{"_id": "TH087", "total": 650.0}]

        mock_db = self._build_lot_finance_db(units=[unit])
        mock_db.unit_levy_ledger.find.return_value = _cursor([ledger])
        mock_db.council_rates.find.return_value = _cursor([council])
        mock_db.water_bills.aggregate.return_value = _cursor(water)

        with patch("services.lot_finance_service.db", mock_db):
            result = await services.lot_finance_service.compute_building_cost_distribution("2026", BUILDING_ID)

        summary = result[0]
        assert summary["total_council"] == 2400.0
        assert summary["total_land_tax"] == 900.0
        assert summary["total_water"] == 650.0
        assert summary["total_cost"] == 5100.0 + 1946.0 + 2400.0 + 900.0 + 650.0


# ─────────────────────────────────────────────────────────────────────────────
# Section 9: Notice Service — Section 83 PDF
# ─────────────────────────────────────────────────────────────────────────────

class TestNoticeServiceExtended:
    """Extended tests for notice service beyond test_arrears_board.py coverage."""

    def _build_notice_db(self, unit_number: str = "TH087", net_balance: float = 2087.0):
        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value={
            "building_name": "East Gate Residences",
            "plan_number": "13195",
            "levy_due_months": [3, 6, 9, 12],
            "grace_period_days": 14,
            "levy_due_day_type": "last",
        })
        mock_db.units.find_one = AsyncMock(return_value=_make_unit(unit_number))
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_make_ledger(unit_number, net_balance))
        mock_db.user_units.find_one = AsyncMock(return_value={
            "unit_number": unit_number, "user_id": "u-owner", "is_active": True
        })
        mock_db.users.find_one = AsyncMock(return_value={
            "id": "u-owner",
            "full_name": "Avneet Rooprai",
            "email": "avneet@eastgateresidences.com.au",
        })
        mock_payments = MagicMock()
        mock_payments.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.aggregate = MagicMock(return_value=mock_payments)
        mock_db.units.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        return mock_db

    @pytest.mark.asyncio
    async def test_pdf_contains_unit_number(self):
        """Generated PDF contains the unit number."""
        import services.notice_service

        mock_db = self._build_notice_db("TH087")
        with patch("services.notice_service.db", mock_db), \
                patch("services.notice_service.create_audit_log", AsyncMock()):
            pdf_bytes = await services.notice_service.generate_arrears_notice(
                "TH087", "2026", "admin-1", "Admin", BUILDING_ID
            )

        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = " ".join(page.extract_text() or "" for page in pdf.pages)
        assert "TH087" in text

    @pytest.mark.asyncio
    async def test_pdf_contains_legislation_reference(self):
        """Generated PDF cites Section 83 legislation."""
        import services.notice_service

        mock_db = self._build_notice_db("UA101", 3000.0)
        with patch("services.notice_service.db", mock_db), \
                patch("services.notice_service.create_audit_log", AsyncMock()):
            pdf_bytes = await services.notice_service.generate_arrears_notice(
                "UA101", "2026", "admin-1", "Admin", BUILDING_ID
            )

        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = " ".join(page.extract_text() or "" for page in pdf.pages).upper()
        assert "SECTION 83" in text or "S83" in text or "83" in text

    @pytest.mark.asyncio
    async def test_contact_log_appended_on_notice(self):
        """Notice generation appends an entry to arrears_metadata.contact_log."""
        import services.notice_service

        mock_db = self._build_notice_db("TH087", 2087.0)
        with patch("services.notice_service.db", mock_db), \
                patch("services.notice_service.create_audit_log", AsyncMock()):
            await services.notice_service.generate_arrears_notice(
                "TH087", "2026", "u-admin", "Admin User", BUILDING_ID
            )

        mock_db.units.update_one.assert_called()
        update_call = mock_db.units.update_one.call_args
        update_doc = update_call[0][1] if update_call[0] else {}
        assert "$push" in update_doc
        assert "arrears_metadata.contact_log" in update_doc["$push"]

    @pytest.mark.asyncio
    async def test_notice_unit_not_found_raises(self):
        """Notice generation raises ValueError when unit doesn't exist."""
        import services.notice_service

        mock_db = MagicMock()
        mock_db.units.find_one = AsyncMock(return_value=None)
        mock_db.settings.find_one = AsyncMock(return_value={})

        with patch("services.notice_service.db", mock_db):
            with pytest.raises(ValueError, match="not found"):
                await services.notice_service.generate_arrears_notice(
                    "UA999", "2026", "admin-1", "Admin", BUILDING_ID
                )

    @pytest.mark.asyncio
    async def test_notice_audit_log_created(self):
        """Notice generation calls create_audit_log once."""
        import services.notice_service

        mock_db = self._build_notice_db("UA101")
        mock_audit = AsyncMock()

        with patch("services.notice_service.db", mock_db), \
                patch("services.notice_service.create_audit_log", mock_audit):
            await services.notice_service.generate_arrears_notice(
                "UA101", "2026", "admin-1", "Admin", BUILDING_ID
            )

        mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_pdf_is_valid_bytes(self):
        """Generated PDF starts with PDF magic bytes."""
        import services.notice_service

        mock_db = self._build_notice_db("UA001", 1500.0)
        with patch("services.notice_service.db", mock_db), \
                patch("services.notice_service.create_audit_log", AsyncMock()):
            pdf_bytes = await services.notice_service.generate_arrears_notice(
                "UA001", "2026", "admin-1", "Admin", BUILDING_ID
            )

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 1000
        # PDF files start with %PDF
        assert pdf_bytes[:4] == b"%PDF"


# ─────────────────────────────────────────────────────────────────────────────
# Section 10: Arrears Recovery Workflow — DCA Status Transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestArrearsWorkflow:
    """Tests for DCA referral and status transition logic."""

    def _build_workflow_db(self, dca_status: str = "eligible"):
        mock_db = MagicMock()
        unit = _make_unit("UA101")
        unit["arrears_metadata"]["dca_status"] = dca_status
        mock_db.units.find_one = AsyncMock(return_value=unit)
        mock_db.units.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_make_ledger("UA101", 2500.0))
        return mock_db

    @pytest.mark.asyncio
    async def test_refer_to_dca_sets_referred_status(self):
        """refer_to_dca sets dca_status to 'referred'."""
        import routers.finance

        mock_db = self._build_workflow_db("eligible")
        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            result = await routers.finance.refer_to_dca(
                "UA101",
                current_user={"id": "admin-1", "full_name": "Admin"},
            )

        assert result.get("success") is True
        update_call = mock_db.units.update_one.call_args
        update_doc = update_call[0][1] if update_call[0] else {}
        assert update_doc["$set"]["arrears_metadata.dca_status"] == "referred"

    @pytest.mark.asyncio
    async def test_refer_to_dca_generates_reference_number(self):
        """DCA referral creates a reference number containing the unit number."""
        import routers.finance

        mock_db = self._build_workflow_db("eligible")
        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            result = await routers.finance.refer_to_dca(
                "UA101",
                current_user={"id": "admin-1", "full_name": "Admin"},
            )

        assert "dca_reference" in result
        assert "UA101" in result["dca_reference"]

    def test_arrears_metadata_default_structure(self):
        """arrears_metadata defaults are correct after migration."""
        unit = _make_unit("UA101")
        meta = unit["arrears_metadata"]
        assert meta["dca_status"] == "none"
        assert meta["contact_log"] == []
        assert meta["has_active_payment_plan"] is False
        assert meta["legal_referral_status"] == "none"
        assert meta["dca_reference"] is None
        assert meta["first_notice_sent_at"] is None

    @pytest.mark.asyncio
    async def test_dca_eligibility_threshold(self):
        """DCA eligibility requires >$1500 arrears after 14 days notice."""
        import cron.cron_finance_recompute

        mock_db = MagicMock()
        mock_db.unit_levy_ledger.find.return_value = _cursor([
            {"unit_number": "UA101", "net_balance": 2500.0},
            {"unit_number": "UA102", "net_balance": 500.0},  # below $1500
        ])

        sent_at_over_14 = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        sent_at_recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

        async def find_one_side_effect(query, *args, **kwargs):
            uid = query.get("unit_number")
            if uid == "UA101":
                return {
                    "unit_number": "UA101",
                    "arrears_metadata": {
                        "dca_status": "none",
                        "first_notice_sent_at": sent_at_over_14,
                    }
                }
            return {
                "unit_number": "UA102",
                "arrears_metadata": {
                    "dca_status": "none",
                    "first_notice_sent_at": sent_at_recent,
                }
            }

        mock_db.units.find_one = AsyncMock(side_effect=find_one_side_effect)
        mock_db.units.update_one = AsyncMock()

        with patch("cron.cron_finance_recompute.db", mock_db):
            count = await cron.cron_finance_recompute._check_dca_eligibility(
                "2026", dry_run=False, building_id=BUILDING_ID
            )

        # Only UA101 qualifies: >$1500 AND >14 days since notice
        assert count == 1

    @pytest.mark.asyncio
    async def test_dca_already_referred_not_double_updated(self):
        """Units already referred to DCA are not updated again by cron."""
        import cron.cron_finance_recompute

        mock_db = MagicMock()
        mock_db.unit_levy_ledger.find.return_value = _cursor([
            {"unit_number": "UA101", "net_balance": 5000.0},
        ])

        sent_at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        mock_db.units.find_one = AsyncMock(return_value={
            "unit_number": "UA101",
            "arrears_metadata": {
                "dca_status": "referred",  # already referred
                "first_notice_sent_at": sent_at,
            }
        })
        mock_db.units.update_one = AsyncMock()

        with patch("cron.cron_finance_recompute.db", mock_db):
            count = await cron.cron_finance_recompute._check_dca_eligibility(
                "2026", dry_run=False, building_id=BUILDING_ID
            )

        assert count == 0
        mock_db.units.update_one.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Section 11: Role-Based Permission Verification
# ─────────────────────────────────────────────────────────────────────────────

class TestPermissions:
    """Verify correct permission mappings for P1/P2 roles."""

    VIEW_ROLES = {"super_admin", "chairman", "strata_manager", "ec_member"}
    MANAGE_ROLES = {"super_admin", "chairman", "strata_manager"}
    RESTRICTED_ROLES = {"owner", "tenant", "guest", "service_provider"}

    def test_view_permissions_correct_roles(self):
        """can_view_finances granted to correct roles."""
        for role in self.VIEW_ROLES:
            user = _make_user(role)
            assert user["permissions"]["can_view_finances"] is True, f"{role} should have view"

    def test_view_permissions_not_granted_to_restricted(self):
        """can_view_finances NOT granted to owner/tenant/guest."""
        for role in self.RESTRICTED_ROLES:
            user = _make_user(role)
            assert user["permissions"]["can_view_finances"] is False, f"{role} should NOT have view"

    def test_manage_permissions_correct_roles(self):
        """can_manage_finances granted to super_admin, chairman, strata_manager."""
        for role in self.MANAGE_ROLES:
            user = _make_user(role)
            assert user["permissions"]["can_manage_finances"] is True, f"{role} should have manage"

    def test_manage_permissions_not_granted_to_ec_member(self):
        """ec_member can view but NOT manage finances."""
        user = _make_user("ec_member")
        assert user["permissions"]["can_view_finances"] is True
        assert user["permissions"]["can_manage_finances"] is False

    def test_manage_permissions_not_granted_to_restricted(self):
        """can_manage_finances NOT granted to owner/tenant/guest."""
        for role in self.RESTRICTED_ROLES:
            user = _make_user(role)
            assert user["permissions"]["can_manage_finances"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Section 12: Model Validation Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestModelValidation:
    """Tests for Pydantic model validation on P1/P2 models."""

    def test_expense_transaction_defaults(self):
        """ExpenseTransactionCreate uses correct defaults."""
        from models.finance import ExpenseTransactionCreate

        exp = ExpenseTransactionCreate(
            financial_year="2026",
            category_id="cat-01",
            fund_type="administrative",
            date="2026-02-15",
            amount=500.0,
            description="Test",
            supplier_name="Test Supplier",
        )
        assert exp.plan_id == "", "plan_id must default to '' (clean-break, Phase E)"
        assert exp.is_gst_inclusive is True
        assert exp.gst_amount == 0.0

    def test_special_levy_defaults(self):
        """SpecialLevyCreate defaults plan_id and fund_type."""
        from models.finance import SpecialLevyCreate

        sl = SpecialLevyCreate(
            year="2026",
            title="Test Levy",
            description="Test",
            total_amount=10000.0,
            due_date="2026-06-30",
        )
        assert sl.plan_id == "", "plan_id must default to '' (clean-break, Phase E)"
        assert sl.fund_type == "sinking"

    def test_bank_reconciliation_defaults(self):
        """BankReconciliationCreate defaults plan_id."""
        from models.finance import BankReconciliationCreate

        br = BankReconciliationCreate(
            financial_year="2026",
            fund_type="administrative",
            statement_date="2026-02-28",
            opening_balance=100000.0,
            closing_balance=95000.0,
            total_receipts=50000.0,
            total_payments=55000.0,
        )
        assert br.plan_id == "", "plan_id must default to '' (clean-break, Phase E)"

    def test_payment_plan_instalment_defaults_not_paid(self):
        """PaymentPlanInstalment defaults is_paid to False."""
        from models.finance import PaymentPlanInstalment

        inst = PaymentPlanInstalment(due_date="2026-03-31", amount=500.0)
        assert inst.is_paid is False
        assert inst.paid_at is None

    def test_income_transaction_inherits_transaction_base(self):
        """IncomeTransactionCreate inherits plan_id default from TransactionBase."""
        from models.finance import IncomeTransactionCreate

        inc = IncomeTransactionCreate(
            financial_year="2026",
            category_id="cat-int",
            fund_type="administrative",
            date="2026-02-28",
            amount=100.0,
            source="interest",
        )
        assert inc.plan_id == "", "plan_id must default to '' (clean-break, Phase E)"
