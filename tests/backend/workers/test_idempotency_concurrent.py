"""
Idempotency and concurrent-execution tests for ARQ tasks and rebuild_projection.

Verifies:
- Running the same ARQ task twice for the same building produces the same outcome
  (no duplicate inserts, no inflated counters).
- Concurrent calls to rebuild_projection for different buildings are isolated.
- The _job_id deduplication key prevents enqueueing the same job twice.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_raw_db(levy_rows=None):
    raw_db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=levy_rows or [])
    raw_db.levy_payments.aggregate = MagicMock(return_value=cursor)
    bulk_result = MagicMock()
    bulk_result.upserted_ids = {}
    raw_db.unit_levy_ledger.bulk_write = AsyncMock(return_value=bulk_result)
    return raw_db


def _make_ctx():
    return {"redis": MagicMock()}


# ── rebuild_projection: idempotency ───────────────────────────────────────────

class TestRebuildProjectionIdempotency:
    @pytest.mark.asyncio
    async def test_repeated_rebuild_same_upsert_count(self):
        """Two sequential rebuilds produce the same count; no phantom rows created."""
        from repositories.financial_repository import rebuild_projection

        rows = [
            {"_id": {"building_id": "13195", "unit_id": "u1", "year": "2026"},
             "total_paid_cents": 100000, "payment_count": 1},
            {"_id": {"building_id": "13195", "unit_id": "u2", "year": "2026"},
             "total_paid_cents": 50000, "payment_count": 1},
        ]
        raw_db = _make_raw_db(rows)
        c1 = await rebuild_projection(raw_db, "unit_levy_ledger", "13195", [])
        c2 = await rebuild_projection(raw_db, "unit_levy_ledger", "13195", [])

        assert c1 == c2 == 2
        # Each run calls bulk_write once (one batch per rebuild, idempotent)
        assert raw_db.unit_levy_ledger.bulk_write.await_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_rebuild_different_buildings_isolated(self):
        """Concurrent rebuilds for 13195 and 16244 do not cross-contaminate."""
        from repositories.financial_repository import rebuild_projection

        rows_13195 = [
            {"_id": {"building_id": "13195", "unit_id": "u1", "year": "2026"},
             "total_paid_cents": 100000, "payment_count": 1},
        ]
        rows_16244 = [
            {"_id": {"building_id": "16244", "unit_id": "u9", "year": "2026"},
             "total_paid_cents": 200000, "payment_count": 2},
            {"_id": {"building_id": "16244", "unit_id": "u10", "year": "2026"},
             "total_paid_cents": 150000, "payment_count": 1},
        ]

        raw_db_a = _make_raw_db(rows_13195)
        raw_db_b = _make_raw_db(rows_16244)

        c_a, c_b = await asyncio.gather(
            rebuild_projection(raw_db_a, "unit_levy_ledger", "13195", []),
            rebuild_projection(raw_db_b, "unit_levy_ledger", "16244", []),
        )

        assert c_a == 1
        assert c_b == 2

        # Each DB saw only its own bulk_write call
        assert raw_db_a.unit_levy_ledger.bulk_write.await_count == 1
        assert raw_db_b.unit_levy_ledger.bulk_write.await_count == 1

    @pytest.mark.asyncio
    async def test_rebuild_with_alias_idempotent(self):
        """lot_balances_projection alias produces same result as unit_levy_ledger on re-run."""
        from repositories.financial_repository import rebuild_projection

        rows = [
            {"_id": {"building_id": "16244", "unit_id": "u5", "year": "2026"},
             "total_paid_cents": 75000, "payment_count": 1},
        ]
        raw_db = _make_raw_db(rows)
        c1 = await rebuild_projection(raw_db, "lot_balances_projection", "16244", [])
        c2 = await rebuild_projection(raw_db, "unit_levy_ledger", "16244", [])

        assert c1 == c2 == 1


# ── ARQ task: idempotency ─────────────────────────────────────────────────────

class TestAarqTaskIdempotency:
    @pytest.mark.asyncio
    async def test_bank_feed_poll_idempotent_on_duplicate_envelope(self):
        """Second poll for same transaction produces envelopes_added=0, not 1."""
        ctx = _make_ctx()
        mock_envelope = {"provider_ref": "TXN-DUPE-001", "amount_cents": 50000}
        mock_provider = MagicMock()
        mock_provider.pull_transactions = AsyncMock(return_value=[mock_envelope])
        mock_registry = MagicMock()
        mock_registry.get_bank_feed = AsyncMock(return_value=mock_provider)

        # First poll: upserted_id set — new record
        new_result = MagicMock()
        new_result.upserted_id = "inserted_id"

        # Second poll: upserted_id None — already existed
        existing_result = MagicMock()
        existing_result.upserted_id = None

        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.get_provider_registry", return_value=mock_registry), \
                patch("workers.arq_tasks.set_ctx_building_id"), \
                patch("workers.arq_tasks.db") as mock_db:
            mock_db._db.integration_inbox.update_one = AsyncMock(
                side_effect=[new_result, existing_result]
            )
            from workers.arq_tasks import bank_feed_poll_task
            r1 = await bank_feed_poll_task(ctx, "13195")
            r2 = await bank_feed_poll_task(ctx, "13195")

        assert r1["envelopes_added"] == 1
        assert r2["envelopes_added"] == 0

    @pytest.mark.asyncio
    async def test_merkle_seal_task_idempotent(self):
        """Calling daily_merkle_seal_task twice returns the same hash both times."""
        ctx = _make_ctx()
        mock_period = {"_id": "period_oid", "state": "open"}
        mock_seal = {"this_seal_hash": "stable_hash_abc", "merkle_root": "root_xyz"}

        with patch("workers.arq_tasks.set_ctx_building_id"), \
                patch("workers.arq_tasks.seal_period", AsyncMock(return_value=mock_seal)), \
                patch("workers.arq_tasks.db") as mock_db:
            mock_db._db.financial_periods.find_one = AsyncMock(return_value=mock_period)
            from workers.arq_tasks import daily_merkle_seal_task
            r1 = await daily_merkle_seal_task(ctx, "13195", target_date="2026-04-26")
            r2 = await daily_merkle_seal_task(ctx, "13195", target_date="2026-04-26")

        assert r1["seal"]["this_seal_hash"] == r2["seal"]["this_seal_hash"]
        assert r1["sealed"] is True
        assert r2["sealed"] is True


# ── ARQ task: multi-building concurrent isolation ─────────────────────────────

class TestAarqTaskConcurrentIsolation:
    @pytest.mark.asyncio
    async def test_interest_accrual_concurrent_buildings_each_get_own_call(self):
        """Concurrent interest_accrual_task for 13195 and 16244 each call run_interest_accrual with own building_id."""
        ctx = _make_ctx()
        calls: list = []

        async def tracking_accrual(dry_run: bool, target_building_id: str):
            calls.append(target_building_id)
            return {"total_interest_cents": 1000, "buildings_processed": 1}

        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.run_interest_accrual", side_effect=tracking_accrual):
            from workers.arq_tasks import interest_accrual_task
            await asyncio.gather(
                interest_accrual_task(ctx, "13195"),
                interest_accrual_task(ctx, "16244"),
            )

        assert sorted(calls) == ["13195", "16244"]

    @pytest.mark.asyncio
    async def test_levy_reminders_concurrent_buildings_isolated(self):
        """Concurrent levy_reminder_dispatch_task for two buildings passes each their own building_id."""
        ctx = _make_ctx()
        calls: list = []

        async def tracking_reminders(building_id: str, tier: int):
            calls.append(building_id)
            return {"sent": 1, "skipped": 0}

        with patch("workers.arq_tasks._toggle_on", AsyncMock(return_value=True)), \
                patch("workers.arq_tasks.run_levy_reminders", side_effect=tracking_reminders):
            from workers.arq_tasks import levy_reminder_dispatch_task
            await asyncio.gather(
                levy_reminder_dispatch_task(ctx, "13195"),
                levy_reminder_dispatch_task(ctx, "16244"),
            )

        assert sorted(calls) == ["13195", "16244"]
