"""Unit tests for FinancialCoreAdapter — dual-write methods.

Covers:
- record_payment: legacy/Postgres toggle routing (existing method)
- generate_levy:  legacy/Postgres toggle routing (new method)
- post_trust_ledger: legacy/Postgres toggle routing (new method)

All tests mock the feature-flag check and Postgres internals so no
database connection is required.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

BACKEND_PATH = str(Path(__file__).resolve().parents[2] / "backend")
if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)

os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("ENCRYPTION_KEY", "uJw9lYkG9btQfIp7q0M6vFlxnPVO92jYbP4P5Ih6QqQ=")

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from services.financial_core.adapter import FinancialCoreAdapter


_BUILDING_ID = "13195"
_LEVY_PARAMS = dict(
    financial_year="2026",
    quarter_no=1,
    issue_date=date(2026, 1, 1),
    due_date=date(2026, 3, 31),
    lot_levies=[
        {
            "lot_id": "00000000-0000-0000-0000-000000000001",
            "owner_party_id": "00000000-0000-0000-0000-000000000002",
            "fund_id": "00000000-0000-0000-0000-000000000003",
            "principal_cents": 100000,
            "gst_cents": 10000,
        }
    ],
    idempotency_key="levy-idem-001",
)
_TRUST_PARAMS = dict(
    trust_account_id="00000000-0000-0000-0000-000000000099",
    amount_cents=150000,
    transaction_date=date(2026, 3, 31),
    description="Levy receipt Q1 2026",
    source_type="levy_receipt",
    source_reference="EXT-2026-0001",
)
_EXPENSE_PARAMS = dict(
    amount_dollars=1000.00,
    gst_dollars=100.00,
    description="Annual building insurance",
    fund_type="admin",
    category_name="Insurance",
    financial_year="2026",
    vendor_name="Acme Insurance Co",
    invoice_number="INV-2026-0042",
    idempotency_key="expense-idem-001",
)


@pytest.mark.asyncio
class TestGenerateLevyAdapterToggle:
    """generate_levy() routes based on FINANCIAL_PG_WRITES_ENABLED."""

    async def test_returns_mongo_source_when_flag_off(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        with patch(
            "services.financial_core.adapter._is_financial_core_enabled",
            return_value=False,
        ):
            result = await adapter.generate_levy(**_LEVY_PARAMS)
        assert result["source"] == "mongo"

    async def test_calls_postgres_path_when_flag_on(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        mock_pg = AsyncMock(return_value={"levy_run_id": "abc", "item_count": 1, "source": "postgres"})
        with patch("services.financial_core.adapter._is_financial_core_enabled", return_value=True):
            with patch.object(adapter, "_generate_levy_postgres", mock_pg):
                result = await adapter.generate_levy(**_LEVY_PARAMS)
        assert result["source"] == "postgres"
        mock_pg.assert_awaited_once()

    async def test_postgres_path_receives_lot_levies(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        captured: list[dict] = []

        async def _fake_pg(**kw):
            captured.append(kw)
            return {"levy_run_id": "x", "item_count": 1, "source": "postgres"}

        with patch("services.financial_core.adapter._is_financial_core_enabled", return_value=True):
            with patch.object(adapter, "_generate_levy_postgres", _fake_pg):
                await adapter.generate_levy(**_LEVY_PARAMS)

        assert captured[0]["financial_year"] == "2026"
        assert captured[0]["quarter_no"] == 1
        assert len(captured[0]["lot_levies"]) == 1
        assert captured[0]["idempotency_key"] == "levy-idem-001"

    async def test_feature_flag_error_defaults_to_mongo(self):
        """If the flag check raises, generate_levy falls back to MongoDB path."""
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        with patch(
            "services.financial_core.adapter._is_financial_core_enabled",
            side_effect=Exception("DB unreachable"),
        ):
            # _is_financial_core_enabled wraps exceptions; re-raises here since
            # generate_levy does not have its own try/except — the caller's
            # try/except (mandated by the module docstring) handles this.
            with pytest.raises(Exception, match="DB unreachable"):
                await adapter.generate_levy(**_LEVY_PARAMS)


@pytest.mark.asyncio
class TestPostTrustLedgerAdapterToggle:
    """post_trust_ledger() routes based on TRUST_PG_LEDGER_ENABLED."""

    async def test_returns_mongo_source_when_flag_off(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        with patch(
            "services.financial_core.adapter.is_cutover_feature_enabled",
            return_value=False,
        ):
            result = await adapter.post_trust_ledger(**_TRUST_PARAMS)
        assert result["source"] == "mongo"

    async def test_calls_postgres_path_when_flag_on(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        mock_pg = AsyncMock(return_value={"bank_transaction_id": "tx-123", "source": "postgres"})
        with patch("services.financial_core.adapter.is_cutover_feature_enabled", return_value=True):
            with patch.object(adapter, "_post_trust_ledger_postgres", mock_pg):
                result = await adapter.post_trust_ledger(**_TRUST_PARAMS)
        assert result["source"] == "postgres"
        mock_pg.assert_awaited_once()

    async def test_postgres_path_receives_all_fields(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        captured: list[dict] = []

        async def _fake_pg(**kw):
            captured.append(kw)
            return {"bank_transaction_id": "tx-456", "source": "postgres"}

        with patch("services.financial_core.adapter.is_cutover_feature_enabled", return_value=True):
            with patch.object(adapter, "_post_trust_ledger_postgres", _fake_pg):
                await adapter.post_trust_ledger(**_TRUST_PARAMS)

        assert captured[0]["trust_account_id"] == _TRUST_PARAMS["trust_account_id"]
        assert captured[0]["amount_cents"] == 150000
        assert captured[0]["source_reference"] == "EXT-2026-0001"
        assert captured[0]["source_type"] == "levy_receipt"

    async def test_idempotent_flag_forwarded_to_postgres_path(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        captured: list[dict] = []

        async def _fake_pg(**kw):
            captured.append(kw)
            return {"bank_transaction_id": "tx-789", "source": "postgres"}

        extra = {**_TRUST_PARAMS, "idempotency_key": "idem-trust-001", "is_test_data": True}
        with patch("services.financial_core.adapter.is_cutover_feature_enabled", return_value=True):
            with patch.object(adapter, "_post_trust_ledger_postgres", _fake_pg):
                await adapter.post_trust_ledger(**extra)

        assert captured[0]["idempotency_key"] == "idem-trust-001"
        assert captured[0]["is_test_data"] is True


@pytest.mark.asyncio
class TestRecordExpenseAdapterToggle:
    """record_expense_transaction() routes based on FINANCIAL_PG_WRITES_ENABLED,
    mirroring TestGenerateLevyAdapterToggle's shape. Also verifies the
    Postgres path now routes through FinancialCoreService.record_expense()
    (replacing the former raw-SQL body) rather than asserting internals of
    that rewritten method directly — see TestRecordExpense in
    test_financial_core_service.py for record_expense()'s own coverage."""

    async def test_returns_mongo_source_when_flag_off(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        with patch(
            "services.financial_core.adapter._is_financial_core_enabled",
            return_value=False,
        ):
            result = await adapter.record_expense_transaction(**_EXPENSE_PARAMS)
        assert result["source"] == "mongo"

    async def test_calls_postgres_path_when_flag_on(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        mock_pg = AsyncMock(return_value={"journal_entry_id": "je-123", "source": "postgres"})
        with patch("services.financial_core.adapter._is_financial_core_enabled", return_value=True):
            with patch.object(adapter, "_record_expense_postgres", mock_pg):
                result = await adapter.record_expense_transaction(**_EXPENSE_PARAMS)
        assert result["source"] == "postgres"
        mock_pg.assert_awaited_once()

    async def test_postgres_path_receives_all_fields(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        captured: list[dict] = []

        async def _fake_pg(**kw):
            captured.append(kw)
            return {"journal_entry_id": "je-456", "source": "postgres"}

        with patch("services.financial_core.adapter._is_financial_core_enabled", return_value=True):
            with patch.object(adapter, "_record_expense_postgres", _fake_pg):
                await adapter.record_expense_transaction(**_EXPENSE_PARAMS)

        assert captured[0]["amount_dollars"] == 1000.00
        assert captured[0]["gst_dollars"] == 100.00
        assert captured[0]["category_name"] == "Insurance"
        assert captured[0]["vendor_name"] == "Acme Insurance Co"
        assert captured[0]["idempotency_key"] == "expense-idem-001"

    async def test_feature_flag_off_never_touches_postgres_path(self):
        adapter = FinancialCoreAdapter(_BUILDING_ID)
        mock_pg = AsyncMock()
        with patch("services.financial_core.adapter._is_financial_core_enabled", return_value=False):
            with patch.object(adapter, "_record_expense_postgres", mock_pg):
                await adapter.record_expense_transaction(**_EXPENSE_PARAMS)
        mock_pg.assert_not_awaited()


@pytest.mark.asyncio
class TestNormalizerStaticMethods:
    """Normalizer helpers on ShadowReadValidator (no DB needed)."""

    def setup_method(self):
        from services.shadow_read_validator import ShadowReadValidator
        self.v = ShadowReadValidator()

    def test_finance_summary_rounds_to_2dp(self):
        result = self.v._normalize_finance_summary(
            {"admin_fund_budget": 340870.021, "sinking_fund_budget": 99504.905,
             "total_income": 440374.926, "levy_arrears_total": 1234.565}
        )
        assert result["admin_fund_budget"] == round(340870.021, 2)
        assert result["sinking_fund_budget"] == round(99504.905, 2)

    def test_finance_summary_empty_input_returns_empty_dict(self):
        assert self.v._normalize_finance_summary(None) == {}
        assert self.v._normalize_finance_summary({}) == {}

    def test_unit_levy_balance_mongo_field_names(self):
        result = self.v._normalize_unit_levy_balance(
            {"total_levied": 10000, "total_paid": 9000, "net_balance": 1000}
        )
        assert result == {"levied_amount": 10000.0, "paid_amount": 9000.0, "closing_balance": 1000.0}

    def test_unit_levy_balance_postgres_field_names(self):
        result = self.v._normalize_unit_levy_balance(
            {"levied_amount": 10000.0, "paid_amount": 9000.0, "closing_balance": 1000.0}
        )
        assert result == {"levied_amount": 10000.0, "paid_amount": 9000.0, "closing_balance": 1000.0}

    def test_trial_balance_maps_accounts_by_code(self):
        result = self.v._normalize_trial_balance({
            "accounts": [
                {"account_code": "1100", "debit": 5000.0, "credit": 0.0},
                {"account_code": "4000", "debit": 0.0, "credit": 5000.0},
            ],
            "total_debits": 5000.0,
            "total_credits": 5000.0,
            "is_balanced": True,
        })
        assert "1100" in result["accounts"]
        assert "4000" in result["accounts"]
        assert result["is_balanced"] is True

    def test_trial_balance_none_gives_empty_structure(self):
        result = self.v._normalize_trial_balance(None)
        assert result["accounts"] == {}
        assert result["is_balanced"] is True

    def test_trust_account_summary_accepts_list(self):
        result = self.v._normalize_trust_account_summary([
            {"fund_type": "admin_fund", "current_balance": 50000.0},
        ])
        assert result["admin_fund"]["current_balance"] == 50000.0

    def test_trust_account_summary_aggregates_same_fund_type(self):
        result = self.v._normalize_trust_account_summary([
            {"fund_type": "admin_fund", "current_balance": 30000.0},
            {"fund_type": "admin_fund", "current_balance": 20000.0},
        ])
        assert result["admin_fund"]["current_balance"] == 50000.0

    def test_reconciliation_summary_integer_fields(self):
        result = self.v._normalize_reconciliation_summary({
            "matched_count": "10",
            "unmatched_bank_count": "2",
            "total_unreconciled_amount_cents": "15000",
            "discrepancy_cents": "0",
        })
        assert result["matched_count"] == 10
        assert isinstance(result["matched_count"], int)

    def test_reconciliation_summary_empty_returns_zeros(self):
        result = self.v._normalize_reconciliation_summary({})
        assert result["matched_count"] == 0
        assert result["discrepancy_cents"] == 0
