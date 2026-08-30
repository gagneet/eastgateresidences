"""
tests/backend/test_shadow_read_validator.py

Tests for the shadow-read validator (Component 6 Phase F).

Verifies that:
- Shadow read validator can compare Postgres and MongoDB results
- Divergences are correctly detected
- Divergences are logged to the database
- Query parameters are correctly captured
"""

import pytest
import uuid
import json
from datetime import datetime, timezone, date
from unittest.mock import AsyncMock, MagicMock, patch

from services.shadow_read_validator import (
    ShadowReadValidator,
    ShadowReadResult,
)


@pytest.fixture
def mock_scheme_ref():
    """Create a mock SchemeRef."""
    return MagicMock(
        tenant_id=uuid.uuid4(),
        scheme_id=uuid.uuid4(),
    )


@pytest.fixture
def mock_session():
    """Create a mock async database session."""
    return AsyncMock()


@pytest.fixture
def mock_outbox_repo():
    """Create a mock outbox repository."""
    return AsyncMock()


@pytest.mark.asyncio
class TestShadowReadResult:
    """Tests for ShadowReadResult data class."""

    async def test_shadow_read_result_construction(self):
        """ShadowReadResult can be constructed with all fields."""
        result = ShadowReadResult(
            query_type="get_trial_balance",
            query_params={"scheme_id": "123", "as_of_date": "2026-05-02"},
            postgres_value={"balance_cents": 100000},
            mongodb_value={"balance_cents": 100000},
            diverged=False,
            divergence_summary=None,
        )

        assert result.query_type == "get_trial_balance"
        assert result.diverged is False
        assert result.divergence_summary is None

    async def test_shadow_read_result_with_divergence(self):
        """ShadowReadResult can represent a divergence."""
        result = ShadowReadResult(
            query_type="get_fund_balance",
            query_params={"scheme_id": "123", "fund_id": "456"},
            postgres_value={"balance_cents": 100000},
            mongodb_value={"balance_cents": 105000},
            diverged=True,
            divergence_summary="Fund balance mismatch: Postgres 100000 vs MongoDB 105000",
        )

        assert result.diverged is True
        assert result.divergence_summary is not None

    async def test_shadow_read_result_to_dict(self):
        """ShadowReadResult can be converted to dict."""
        result = ShadowReadResult(
            query_type="get_trial_balance",
            query_params={"date": "2026-05-02"},
            postgres_value={"status": "ok"},
            mongodb_value={"status": "ok"},
            diverged=False,
        )

        result_dict = result.to_dict()
        assert isinstance(result_dict, dict)
        assert result_dict["query_type"] == "get_trial_balance"
        assert result_dict["diverged"] is False


@pytest.mark.asyncio
class TestShadowReadValidator:
    """Tests for ShadowReadValidator."""

    async def test_validator_initialization(self, mock_session, mock_outbox_repo):
        """Validator can be initialized with session and repository."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        assert validator.session == mock_session
        assert validator.outbox_repo == mock_outbox_repo

    async def test_compare_values_identical_strings(self):
        """Compare identical string values."""
        value_a = "test value"
        value_b = "test value"
        diverged, summary = ShadowReadValidator._compare_values(value_a, value_b)

        assert diverged is False
        assert summary is None

    async def test_compare_values_identical_dicts(self):
        """Compare identical dict values."""
        value_a = {"balance_cents": 100000, "currency": "AUD"}
        value_b = {"balance_cents": 100000, "currency": "AUD"}
        diverged, summary = ShadowReadValidator._compare_values(value_a, value_b)

        assert diverged is False
        assert summary is None

    async def test_compare_values_different_numbers(self):
        """Compare different numeric values."""
        value_a = {"balance": 100000}
        value_b = {"balance": 105000}
        diverged, summary = ShadowReadValidator._compare_values(value_a, value_b)

        assert diverged is True
        assert summary is not None
        assert "100000" in str(summary)
        assert "105000" in str(summary)

    async def test_compare_values_different_structures(self):
        """Compare structurally different values."""
        value_a = {"field_a": "value"}
        value_b = {"field_b": "value"}
        diverged, summary = ShadowReadValidator._compare_values(value_a, value_b)

        assert diverged is True
        assert summary is not None

    async def test_compare_values_with_null(self):
        """Compare values where one is null."""
        value_a = {"balance": 100000}
        value_b = {"balance": None}
        diverged, summary = ShadowReadValidator._compare_values(value_a, value_b)

        assert diverged is True

    async def test_validate_finance_summary_no_divergence(self, mock_session, mock_outbox_repo):
        """Finance summary validation reports no divergence when budgets match."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        payload = {
            "admin_fund_budget": 340870.02,
            "sinking_fund_budget": 99504.90,
            "total_income": 440374.92,
            "levy_arrears_total": 0.0,
        }
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_finance_summary(
                building_id="13195",
                query_params={"financial_year": "2026"},
                mongodb_value=payload,
                postgres_value=payload,
            )
        assert result.diverged is False
        assert result.query_type == "get_finance_summary"
        assert result.diverging_fields == []

    async def test_validate_finance_summary_detects_budget_divergence(self, mock_session, mock_outbox_repo):
        """Finance summary validation detects mismatched admin_fund_budget."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        mongo_val = {"admin_fund_budget": 340870.02, "sinking_fund_budget": 99504.90, "total_income": 440374.92, "levy_arrears_total": 0.0}
        pg_val    = {"admin_fund_budget": 0.0,        "sinking_fund_budget": 0.0,        "total_income": 0.0,        "levy_arrears_total": 0.0}
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_finance_summary(
                building_id="13195",
                query_params={"financial_year": "2026"},
                mongodb_value=mongo_val,
                postgres_value=pg_val,
            )
        assert result.diverged is True
        assert "admin_fund_budget" in result.diverging_fields

    async def test_validate_unit_levy_balance_normalises_mongo_keys(self, mock_session, mock_outbox_repo):
        """Unit levy balance normaliser maps MongoDB field names to canonical keys."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        mongo_val = {"total_levied": 10000.0, "total_paid": 9000.0, "net_balance": 1000.0}
        pg_val    = {"levied_amount": 10000.0, "paid_amount": 9000.0, "closing_balance": 1000.0}
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_unit_levy_balance(
                building_id="13195",
                query_params={"unit_number": "42", "financial_year": "2026"},
                mongodb_value=mongo_val,
                postgres_value=pg_val,
            )
        assert result.diverged is False

    async def test_validate_unit_levy_balance_detects_divergence(self, mock_session, mock_outbox_repo):
        """Unit levy balance validation detects mismatched closing balance."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        mongo_val = {"total_levied": 10000.0, "total_paid": 9000.0, "net_balance": 1000.0}
        pg_val    = {"levied_amount": 10000.0, "paid_amount": 9000.0, "closing_balance": 0.0}
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_unit_levy_balance(
                building_id="13195",
                query_params={"unit_number": "42", "financial_year": "2026"},
                mongodb_value=mongo_val,
                postgres_value=pg_val,
            )
        assert result.diverged is True
        assert "closing_balance" in result.diverging_fields

    async def test_validate_trial_balance_no_divergence(self, mock_session, mock_outbox_repo):
        """Trial balance validation reports no divergence for identical account lists."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        payload = {
            "accounts": [{"account_code": "1100", "debit": 1000.0, "credit": 0.0}],
            "total_debits": 1000.0,
            "total_credits": 0.0,
            "is_balanced": False,
        }
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_trial_balance(
                building_id="13195",
                query_params={},
                mongodb_value=payload,
                postgres_value=payload,
            )
        assert result.diverged is False
        assert result.query_type == "get_trial_balance"

    async def test_validate_trial_balance_postgres_empty_diverges(self, mock_session, mock_outbox_repo):
        """Trial balance reports divergence when Postgres tables are empty (expected baseline)."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        mongo_val = {
            "accounts": [{"account_code": "1100", "debit": 50000.0, "credit": 0.0}],
            "total_debits": 50000.0,
            "total_credits": 0.0,
            "is_balanced": False,
        }
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_trial_balance(
                building_id="13195",
                query_params={},
                mongodb_value=mongo_val,
                postgres_value=None,
            )
        assert result.diverged is True

    async def test_validate_trust_account_summary_aggregates_by_fund_type(self, mock_session, mock_outbox_repo):
        """Trust account summary normaliser maps fund_type → current_balance."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        payload = [
            {"fund_type": "admin_fund", "current_balance": 50000.0},
            {"fund_type": "capital_works_fund", "current_balance": 20000.0},
        ]
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_trust_account_summary(
                building_id="13195",
                query_params={},
                mongodb_value=payload,
                postgres_value=payload,
            )
        assert result.diverged is False
        assert result.query_type == "get_trust_account_summary"

    async def test_validate_reconciliation_summary_no_divergence(self, mock_session, mock_outbox_repo):
        """Reconciliation summary validation with matching counts and discrepancy."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        payload = {
            "matched_count": 10,
            "unmatched_bank_count": 2,
            "total_unreconciled_amount_cents": 15000,
            "discrepancy_cents": 0,
        }
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_reconciliation_summary(
                building_id="13195",
                query_params={"run_id": "abc"},
                mongodb_value=payload,
                postgres_value=payload,
            )
        assert result.diverged is False

    async def test_validate_reconciliation_summary_detects_count_divergence(self, mock_session, mock_outbox_repo):
        """Reconciliation summary validation detects mismatched matched_count."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        mongo_val = {"matched_count": 10, "unmatched_bank_count": 2, "total_unreconciled_amount_cents": 15000, "discrepancy_cents": 0}
        pg_val    = {"matched_count": 0,  "unmatched_bank_count": 0, "total_unreconciled_amount_cents": 0,     "discrepancy_cents": 0}
        with patch("services.shadow_read_validator.config_repo.resolve_scheme_context", return_value=None):
            result = await validator.validate_reconciliation_summary(
                building_id="13195",
                query_params={"run_id": "abc"},
                mongodb_value=mongo_val,
                postgres_value=pg_val,
            )
        assert result.diverged is True
        assert "matched_count" in result.diverging_fields


@pytest.mark.asyncio
class TestShadowReadIntegration:
    """Integration tests for shadow-read validation workflow."""

    async def test_divergence_logging_creates_database_entry(
            self,
            mock_session,
            mock_outbox_repo,
            mock_scheme_ref,
    ):
        """Divergence logging writes to database."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)

        result = ShadowReadResult(
            query_type="get_trial_balance",
            query_params={"scheme_id": str(mock_scheme_ref.scheme_id)},
            postgres_value={"balance": 100000},
            mongodb_value={"balance": 105000},
            diverged=True,
            divergence_summary="Balance mismatch",
        )

        mock_session.execute = AsyncMock()

        await validator._log_divergence(mock_scheme_ref, result)

        # Verify session.execute was called
        assert mock_session.execute.await_count == 1

        # Verify the SQL had the right components
        call_args = mock_session.execute.call_args
        sql_text = str(call_args[0][0])
        assert "shadow_read_divergences" in sql_text
        assert "INSERT" in sql_text

    async def test_multiple_validations_in_sequence(
            self,
            mock_session,
            mock_outbox_repo,
    ):
        """Multiple different comparators can run in sequence and produce distinct query_types."""
        validator = ShadowReadValidator(mock_session, mock_outbox_repo)
        patch_scheme = patch(
            "services.shadow_read_validator.config_repo.resolve_scheme_context",
            return_value=None,
        )
        empty = {}
        with patch_scheme:
            r1 = await validator.validate_finance_summary(
                building_id="13195",
                query_params={},
                mongodb_value=empty,
                postgres_value=empty,
            )
            r2 = await validator.validate_trial_balance(
                building_id="13195",
                query_params={},
                mongodb_value=empty,
                postgres_value=empty,
            )
            r3 = await validator.validate_reconciliation_summary(
                building_id="13195",
                query_params={},
                mongodb_value=empty,
                postgres_value=empty,
            )

        assert r1.query_type == "get_finance_summary"
        assert r2.query_type == "get_trial_balance"
        assert r3.query_type == "get_reconciliation_summary"
        # Empty vs empty — no divergence
        assert all(not r.diverged for r in [r1, r2, r3])
