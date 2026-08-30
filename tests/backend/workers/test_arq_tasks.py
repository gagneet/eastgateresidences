"""
Tests for workers/arq_tasks.py — ARQ task functions for the financial integration layer v2.

All DB calls mocked with AsyncMock. No Redis or external process required.
Tests verify: task logic, idempotency, toggle guard, multi-tenant isolation.
"""
from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db(toggle_enabled: bool = True, building_id: str = "13195"):
    """Build a mock db with feature_toggles pre-configured."""
    mock_db = MagicMock()
    toggle_doc = {"feature_key": "financial_integration_layer_v2", "building_id": building_id,
                  "is_enabled": toggle_enabled}
    mock_db.feature_toggles.find_one = AsyncMock(return_value=toggle_doc if toggle_enabled else None)
    mock_db._db = MagicMock()
    mock_db._db.feature_toggles.find_one = AsyncMock(return_value=toggle_doc)
    return mock_db


def _make_ctx():
    return {"redis": MagicMock()}


# ── Toggle guard ──────────────────────────────────────────────────────────────

class TestToggleGuard:
    @pytest.mark.asyncio
    async def test_toggle_off_skips_bank_feed_poll(self):
        ctx = _make_ctx()
        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=False)):
            from workers.arq_tasks import bank_feed_poll_task
            result = await bank_feed_poll_task(ctx, "13195")
        assert result["skipped"] is True

    @pytest.mark.asyncio
    async def test_toggle_off_skips_interest_accrual(self):
        ctx = _make_ctx()
        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=False)):
            from workers.arq_tasks import interest_accrual_task
            result = await interest_accrual_task(ctx, "13195")
        assert result["skipped"] is True

    @pytest.mark.asyncio
    async def test_toggle_off_skips_levy_reminders(self):
        ctx = _make_ctx()
        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=False)):
            from workers.arq_tasks import levy_reminder_dispatch_task
            result = await levy_reminder_dispatch_task(ctx, "13195")
        assert result["skipped"] is True


# ── bank_feed_poll_task ───────────────────────────────────────────────────────

class TestBankFeedPollTask:
    @pytest.mark.asyncio
    async def test_new_envelopes_inserted(self):
        ctx = _make_ctx()
        mock_envelope = {"provider_ref": "TXN-001", "amount_cents": 100000}
        mock_provider = MagicMock()
        mock_provider.pull_transactions = AsyncMock(return_value=[mock_envelope])
        mock_registry = MagicMock()
        mock_registry.get_bank_feed = AsyncMock(return_value=mock_provider)

        upsert_result = MagicMock()
        upsert_result.upserted_id = "new_id"

        mock_inbox = MagicMock()
        mock_inbox.update_one = AsyncMock(return_value=upsert_result)

        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.get_provider_registry", return_value=mock_registry), \
                patch("workers.arq_tasks.set_ctx_building_id"), \
                patch("workers.arq_tasks.db") as mock_db:
            mock_db._db.integration_inbox.update_one = AsyncMock(return_value=upsert_result)
            from workers.arq_tasks import bank_feed_poll_task
            result = await bank_feed_poll_task(ctx, "13195")

        assert result["envelopes_added"] == 1
        assert result["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_duplicate_envelope_not_re_inserted(self):
        """Re-running poll for same building produces 0 new envelopes (upsert returns no upserted_id)."""
        ctx = _make_ctx()
        mock_envelope = {"provider_ref": "TXN-001", "amount_cents": 100000}
        mock_provider = MagicMock()
        mock_provider.pull_transactions = AsyncMock(return_value=[mock_envelope])
        mock_registry = MagicMock()
        mock_registry.get_bank_feed = AsyncMock(return_value=mock_provider)

        # Simulate already-existing record: upserted_id is None
        existing_result = MagicMock()
        existing_result.upserted_id = None

        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.get_provider_registry", return_value=mock_registry), \
                patch("workers.arq_tasks.set_ctx_building_id"), \
                patch("workers.arq_tasks.db") as mock_db:
            mock_db._db.integration_inbox.update_one = AsyncMock(return_value=existing_result)
            from workers.arq_tasks import bank_feed_poll_task
            result = await bank_feed_poll_task(ctx, "13195")

        assert result["envelopes_added"] == 0

    @pytest.mark.asyncio
    async def test_no_provider_returns_zero_envelopes(self):
        ctx = _make_ctx()
        mock_registry = MagicMock()
        mock_registry.get_bank_feed = AsyncMock(side_effect=Exception("No provider"))

        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.get_provider_registry", return_value=mock_registry), \
                patch("workers.arq_tasks.set_ctx_building_id"):
            from workers.arq_tasks import bank_feed_poll_task
            result = await bank_feed_poll_task(ctx, "13195")

        assert result["envelopes_added"] == 0
        assert "error" in result


# ── interest_accrual_task ─────────────────────────────────────────────────────

class TestInterestAccrualTask:
    @pytest.mark.asyncio
    async def test_calls_run_interest_accrual(self):
        ctx = _make_ctx()
        mock_result = {"total_interest_cents": 5000, "buildings_processed": 1}

        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.run_interest_accrual", AsyncMock(return_value=mock_result)) as mock_accrual:
            from workers.arq_tasks import interest_accrual_task
            result = await interest_accrual_task(ctx, "13195")

        mock_accrual.assert_awaited_once_with(dry_run=False, target_building_id="13195")
        assert result["building_id"] == "13195"
        assert result["total_interest_cents"] == 5000

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self):
        """Task for building 16244 passes 16244 to run_interest_accrual — not 13195."""
        ctx = _make_ctx()
        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.run_interest_accrual", AsyncMock(return_value={})) as mock_accrual:
            from workers.arq_tasks import interest_accrual_task
            await interest_accrual_task(ctx, "16244")

        call_kwargs = mock_accrual.call_args[1]
        assert call_kwargs["target_building_id"] == "16244"


# ── levy_reminder_dispatch_task ───────────────────────────────────────────────

class TestLevyReminderDispatchTask:
    @pytest.mark.asyncio
    async def test_passes_building_id_to_run_levy_reminders(self):
        ctx = _make_ctx()
        mock_result = {"sent": 3, "skipped": 2}

        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.run_levy_reminders", AsyncMock(return_value=mock_result)) as mock_reminders:
            from workers.arq_tasks import levy_reminder_dispatch_task
            result = await levy_reminder_dispatch_task(ctx, "13195")

        mock_reminders.assert_awaited_once_with(building_id="13195", tier=1)
        assert result["sent"] == 3

    @pytest.mark.asyncio
    async def test_different_buildings_isolated(self):
        ctx = _make_ctx()
        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.run_levy_reminders", AsyncMock(return_value={})) as mock_reminders:
            from workers.arq_tasks import levy_reminder_dispatch_task
            await levy_reminder_dispatch_task(ctx, "16244")

        assert mock_reminders.call_args[1]["building_id"] == "16244"


# ── daily_merkle_seal_task ────────────────────────────────────────────────────

class TestDailyMerkleSealTask:
    @pytest.mark.asyncio
    async def test_seals_existing_period(self):
        ctx = _make_ctx()
        mock_period = {"_id": "period_oid", "state": "open"}
        mock_seal = {"this_seal_hash": "abc123", "merkle_root": "xyz"}

        with patch("workers.arq_tasks.set_ctx_building_id"), \
                patch("workers.arq_tasks.seal_period", AsyncMock(return_value=mock_seal)), \
                patch("workers.arq_tasks.db") as mock_db:
            mock_db._db.financial_periods.find_one = AsyncMock(return_value=mock_period)
            from workers.arq_tasks import daily_merkle_seal_task
            result = await daily_merkle_seal_task(ctx, "13195")

        assert result["sealed"] is True
        assert result["seal"] == mock_seal

    @pytest.mark.asyncio
    async def test_no_period_returns_unsealed(self):
        ctx = _make_ctx()
        with patch("workers.arq_tasks.set_ctx_building_id"), \
                patch("workers.arq_tasks.db") as mock_db:
            mock_db._db.financial_periods.find_one = AsyncMock(return_value=None)
            from workers.arq_tasks import daily_merkle_seal_task
            result = await daily_merkle_seal_task(ctx, "13195")

        assert result["sealed"] is False
        assert result["reason"] == "no_period"

    @pytest.mark.asyncio
    async def test_idempotent_same_date(self):
        """Calling twice on same date: seal_period is called twice but returns existing seal both times."""
        ctx = _make_ctx()
        mock_period = {"_id": "p1", "state": "open"}
        existing_seal = {"this_seal_hash": "existing_hash"}

        with patch("workers.arq_tasks.set_ctx_building_id"), \
                patch("workers.arq_tasks.seal_period", AsyncMock(return_value=existing_seal)) as mock_seal_fn, \
                patch("workers.arq_tasks.db") as mock_db:
            mock_db._db.financial_periods.find_one = AsyncMock(return_value=mock_period)
            from workers.arq_tasks import daily_merkle_seal_task
            r1 = await daily_merkle_seal_task(ctx, "13195", target_date="2026-04-26")
            r2 = await daily_merkle_seal_task(ctx, "13195", target_date="2026-04-26")

        assert r1["seal"]["this_seal_hash"] == r2["seal"]["this_seal_hash"]
        assert mock_seal_fn.await_count == 2  # Called both times; seal_period itself is idempotent
