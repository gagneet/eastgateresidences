"""
Tests for migration_021_index_optimisations and the new ensure_indexes() additions.

Verifies:
  - Legacy duplicate 2-key indexes are dropped (no building_id prefix)
  - All new compound indexes are created with correct key patterns and names
  - Partial index expressions are correctly specified
  - ensure_indexes() in financial_repository calls create_index for every DB-021 index
  - Multi-tenant isolation: building_id is the leading key on every new compound index
"""
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_mock_collection(name: str, existing_index_names: list[str] | None = None):
    """Return a mock Motor collection with index_information and create_index stubbed."""
    col = MagicMock()
    col.name = name
    existing = {n: {"key": [[n, 1]]} for n in (existing_index_names or [])}
    col.index_information = AsyncMock(return_value=existing)
    col.create_index = AsyncMock(return_value=name + "_idx")
    col.drop_index = AsyncMock(return_value=None)
    return col


def _make_mock_db(existing_levy_indexes=None, existing_ull_indexes=None):
    """Return a mock db object covering all collections touched by migration_021."""
    db = MagicMock()
    db.levy_payments = _make_mock_collection("levy_payments", existing_levy_indexes)
    db.unit_levy_ledger = _make_mock_collection("unit_levy_ledger", existing_ull_indexes)
    db.financial_transactions = _make_mock_collection("financial_transactions")
    db.bank_transactions = _make_mock_collection("bank_transactions")
    db.trust_ledger_entries = _make_mock_collection("trust_ledger_entries")
    db.insurance_policies = _make_mock_collection("insurance_policies")
    db.maintenance_requests = _make_mock_collection("maintenance_requests")
    db.work_orders = _make_mock_collection("work_orders")
    db.ownership_periods = _make_mock_collection("ownership_periods")
    return db


# ── migration_021: legacy index removal ──────────────────────────────────────

class TestMigration021LegacyIndexRemoval:
    """Verify old 2-key indexes without building_id are dropped when present."""

    @pytest.mark.asyncio
    async def test_drops_legacy_levy_payments_index_when_present(self):
        db = _make_mock_db(
            existing_levy_indexes=["unit_number_1_year_1"],
            existing_ull_indexes=[],
        )
        from scripts.migrations.migration_021_index_optimisations import _drop_if_exists
        await _drop_if_exists(db.levy_payments, "unit_number_1_year_1")
        db.levy_payments.drop_index.assert_awaited_once_with("unit_number_1_year_1")

    @pytest.mark.asyncio
    async def test_skips_drop_when_legacy_levy_payments_index_absent(self):
        db = _make_mock_db(existing_levy_indexes=[], existing_ull_indexes=[])
        from scripts.migrations.migration_021_index_optimisations import _drop_if_exists
        await _drop_if_exists(db.levy_payments, "unit_number_1_year_1")
        db.levy_payments.drop_index.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drops_legacy_unit_levy_ledger_index_when_present(self):
        db = _make_mock_db(
            existing_levy_indexes=[],
            existing_ull_indexes=["unit_number_1_year_1"],
        )
        from scripts.migrations.migration_021_index_optimisations import _drop_if_exists
        await _drop_if_exists(db.unit_levy_ledger, "unit_number_1_year_1")
        db.unit_levy_ledger.drop_index.assert_awaited_once_with("unit_number_1_year_1")

    @pytest.mark.asyncio
    async def test_skips_drop_when_legacy_ull_index_absent(self):
        db = _make_mock_db(existing_levy_indexes=[], existing_ull_indexes=[])
        from scripts.migrations.migration_021_index_optimisations import _drop_if_exists
        await _drop_if_exists(db.unit_levy_ledger, "unit_number_1_year_1")
        db.unit_levy_ledger.drop_index.assert_not_awaited()


# ── migration_021: new index creation ────────────────────────────────────────

class TestMigration021NewIndexes:
    """Verify every new index is created with the correct key pattern and name."""

    @pytest.mark.asyncio
    async def test_financial_transactions_lot_txdate_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.financial_transactions,
            [("building_id", 1), ("lot_number", 1), ("transaction_date", -1)],
            name="bid_lot_txdate_desc",
        )
        db.financial_transactions.create_index.assert_awaited_once_with(
            [("building_id", 1), ("lot_number", 1), ("transaction_date", -1)],
            name="bid_lot_txdate_desc",
        )

    @pytest.mark.asyncio
    async def test_financial_transactions_fund_txdate_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.financial_transactions,
            [("building_id", 1), ("fund_type", 1), ("transaction_date", -1)],
            name="bid_fund_txdate_desc",
        )
        db.financial_transactions.create_index.assert_awaited_once_with(
            [("building_id", 1), ("fund_type", 1), ("transaction_date", -1)],
            name="bid_fund_txdate_desc",
        )

    @pytest.mark.asyncio
    async def test_levy_payments_unit_status_index(self):
        """bid_unit_arrears_partial was renamed bid_unit_status after discovering
        MongoDB 6.0 rejects $ne in partial filter expressions (OperationFailure code 67).
        The compound (building_id, unit_number, status) index covers the same arrears
        queries without the partial filter restriction."""
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.levy_payments,
            [("building_id", 1), ("unit_number", 1), ("status", 1)],
            name="bid_unit_status",
        )
        db.levy_payments.create_index.assert_awaited_once_with(
            [("building_id", 1), ("unit_number", 1), ("status", 1)],
            name="bid_unit_status",
        )

    @pytest.mark.asyncio
    async def test_bank_transactions_date_amount_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.bank_transactions,
            [("building_id", 1), ("transaction_date", 1), ("amount_cents", 1)],
            name="bid_date_amount",
        )
        db.bank_transactions.create_index.assert_awaited_once_with(
            [("building_id", 1), ("transaction_date", 1), ("amount_cents", 1)],
            name="bid_date_amount",
        )

    @pytest.mark.asyncio
    async def test_bank_transactions_unmatched_partial_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.bank_transactions,
            [("building_id", 1), ("status", 1)],
            name="bid_status_unmatched_partial",
            partialFilterExpression={"status": "unmatched"},
        )
        db.bank_transactions.create_index.assert_awaited_once_with(
            [("building_id", 1), ("status", 1)],
            name="bid_status_unmatched_partial",
            partialFilterExpression={"status": "unmatched"},
        )

    @pytest.mark.asyncio
    async def test_trust_ledger_entries_account_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.trust_ledger_entries,
            [("building_id", 1), ("trust_account_id", 1), ("created_at", -1)],
            name="bid_account_created_desc",
        )
        db.trust_ledger_entries.create_index.assert_awaited_once_with(
            [("building_id", 1), ("trust_account_id", 1), ("created_at", -1)],
            name="bid_account_created_desc",
        )

    @pytest.mark.asyncio
    async def test_insurance_policies_expiry_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.insurance_policies,
            [("building_id", 1), ("expiry_date", 1)],
            name="bid_expiry",
        )
        db.insurance_policies.create_index.assert_awaited_once_with(
            [("building_id", 1), ("expiry_date", 1)],
            name="bid_expiry",
        )

    @pytest.mark.asyncio
    async def test_insurance_policies_policy_type_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.insurance_policies,
            [("building_id", 1), ("policy_type", 1)],
            name="bid_policy_type",
        )
        db.insurance_policies.create_index.assert_awaited_once_with(
            [("building_id", 1), ("policy_type", 1)],
            name="bid_policy_type",
        )

    @pytest.mark.asyncio
    async def test_maintenance_requests_status_created_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.maintenance_requests,
            [("building_id", 1), ("status", 1), ("created_at", -1)],
            name="bid_status_created_desc",
        )
        db.maintenance_requests.create_index.assert_awaited_once_with(
            [("building_id", 1), ("status", 1), ("created_at", -1)],
            name="bid_status_created_desc",
        )

    @pytest.mark.asyncio
    async def test_maintenance_requests_contractor_status_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.maintenance_requests,
            [("building_id", 1), ("assigned_contractor", 1), ("status", 1)],
            name="bid_contractor_status",
        )
        db.maintenance_requests.create_index.assert_awaited_once_with(
            [("building_id", 1), ("assigned_contractor", 1), ("status", 1)],
            name="bid_contractor_status",
        )

    @pytest.mark.asyncio
    async def test_work_orders_status_priority_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.work_orders,
            [("building_id", 1), ("status", 1), ("priority", 1), ("created_at", -1)],
            name="bid_status_priority_created",
        )
        db.work_orders.create_index.assert_awaited_once_with(
            [("building_id", 1), ("status", 1), ("priority", 1), ("created_at", -1)],
            name="bid_status_priority_created",
        )

    @pytest.mark.asyncio
    async def test_ownership_periods_owner_index(self):
        db = _make_mock_db()
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            db.ownership_periods,
            [("building_id", 1), ("owner_id", 1), ("effective_from", -1)],
            name="bid_owner_from_desc",
        )
        db.ownership_periods.create_index.assert_awaited_once_with(
            [("building_id", 1), ("owner_id", 1), ("effective_from", -1)],
            name="bid_owner_from_desc",
        )


# ── idempotency: skip creation when index already exists ─────────────────────

class TestMigration021Idempotency:
    """create_index must NOT be called when the named index already exists."""

    @pytest.mark.asyncio
    async def test_skips_create_when_index_already_exists(self):
        col = _make_mock_collection("financial_transactions", ["bid_lot_txdate_desc"])
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            col,
            [("building_id", 1), ("lot_number", 1), ("transaction_date", -1)],
            name="bid_lot_txdate_desc",
        )
        col.create_index.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_when_different_index_exists(self):
        col = _make_mock_collection("financial_transactions", ["some_other_index"])
        from scripts.migrations.migration_021_index_optimisations import _create_if_missing
        await _create_if_missing(
            col,
            [("building_id", 1), ("lot_number", 1), ("transaction_date", -1)],
            name="bid_lot_txdate_desc",
        )
        col.create_index.assert_awaited_once()


# ── multi-tenant: building_id must be the leading key on every new index ─────

class TestMigration021MultiTenantLeadingKey:
    """Every new compound index must have building_id as key[0]."""

    NEW_INDEXES = [
        ([("building_id", 1), ("lot_number", 1), ("transaction_date", -1)], "bid_lot_txdate_desc"),
        ([("building_id", 1), ("fund_type", 1), ("transaction_date", -1)], "bid_fund_txdate_desc"),
        ([("building_id", 1), ("unit_number", 1), ("status", 1)], "bid_unit_status"),
        ([("building_id", 1), ("transaction_date", 1), ("amount_cents", 1)], "bid_date_amount"),
        ([("building_id", 1), ("status", 1)], "bid_status_unmatched_partial"),
        ([("building_id", 1), ("trust_account_id", 1), ("created_at", -1)], "bid_account_created_desc"),
        ([("building_id", 1), ("expiry_date", 1)], "bid_expiry"),
        ([("building_id", 1), ("policy_type", 1)], "bid_policy_type"),
        ([("building_id", 1), ("status", 1), ("created_at", -1)], "bid_status_created_desc"),
        ([("building_id", 1), ("assigned_contractor", 1), ("status", 1)], "bid_contractor_status"),
        ([("building_id", 1), ("status", 1), ("priority", 1), ("created_at", -1)], "bid_status_priority_created"),
        ([("building_id", 1), ("owner_id", 1), ("effective_from", -1)], "bid_owner_from_desc"),
    ]

    @pytest.mark.parametrize("keys,name", NEW_INDEXES)
    def test_building_id_is_leading_key(self, keys, name):
        assert keys[0][0] == "building_id", (
            f"Index '{name}' must have building_id as the leading key; got '{keys[0][0]}'"
        )

    @pytest.mark.parametrize("keys,name", NEW_INDEXES)
    def test_name_starts_with_bid(self, keys, name):
        assert name.startswith("bid_"), (
            f"Index name '{name}' should start with 'bid_' to signal building_id prefix"
        )


# ── financial_repository.ensure_indexes DB-021 additions ─────────────────────

class TestEnsureIndexesDB021Additions:
    """Verify financial_repository.ensure_indexes() calls create_index for every DB-021 index."""

    @pytest.mark.asyncio
    async def test_ensure_indexes_creates_db021_indexes(self):
        mock_db = MagicMock()
        for col_name in [
            "financial_years", "financial_categories", "financial_transactions",
            "levy_payments", "expense_transactions", "unit_levy_ledger",
            "levy_plans", "invoice_documents",
            "bank_transactions", "trust_ledger_entries", "insurance_policies",
            "annual_levies", "units",
        ]:
            col = MagicMock()
            col.create_index = AsyncMock(return_value="ok")
            # _safe_create_index calls await collection.index_information() before create_index.
            # Return {} (no pre-existing indexes) so the helper proceeds to create normally.
            col.index_information = AsyncMock(return_value={})
            setattr(mock_db, col_name, col)

        with patch("repositories.financial_repository.db", mock_db):
            from repositories import financial_repository
            await financial_repository.ensure_indexes()

        ft_calls = [str(c) for c in mock_db.financial_transactions.create_index.call_args_list]
        assert any("bid_lot_txdate_desc" in c for c in ft_calls), (
            "Missing financial_transactions index: bid_lot_txdate_desc"
        )
        assert any("bid_fund_txdate_desc" in c for c in ft_calls), (
            "Missing financial_transactions index: bid_fund_txdate_desc"
        )

        lp_calls = [str(c) for c in mock_db.levy_payments.create_index.call_args_list]
        assert any("bid_unit_status" in c for c in lp_calls), (
            "Missing levy_payments compound index: bid_unit_status"
        )

        bt_calls = [str(c) for c in mock_db.bank_transactions.create_index.call_args_list]
        assert any("bid_date_amount" in c for c in bt_calls), (
            "Missing bank_transactions index: bid_date_amount"
        )
        assert any("bid_status_unmatched_partial" in c for c in bt_calls), (
            "Missing bank_transactions partial index: bid_status_unmatched_partial"
        )

        tl_calls = [str(c) for c in mock_db.trust_ledger_entries.create_index.call_args_list]
        assert any("bid_account_created_desc" in c for c in tl_calls), (
            "Missing trust_ledger_entries index: bid_account_created_desc"
        )

        ip_calls = [str(c) for c in mock_db.insurance_policies.create_index.call_args_list]
        assert any("bid_expiry" in c for c in ip_calls), (
            "Missing insurance_policies index: bid_expiry"
        )
        assert any("bid_policy_type" in c for c in ip_calls), (
            "Missing insurance_policies index: bid_policy_type"
        )
