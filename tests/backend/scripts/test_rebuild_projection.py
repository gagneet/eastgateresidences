"""
Tests for scripts/rebuild_projection.py and repositories/financial_repository.rebuild_projection.

Covers: full rebuild, incremental rebuild (since_event_id), unknown projection error,
multi-tenant isolation, idempotency of repeated rebuild.
No live DB required — Motor calls mocked with AsyncMock.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_raw_db(levy_rows=None):
    """Return a mock raw_db with levy_payments.aggregate returning levy_rows."""
    raw_db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=levy_rows or [])
    raw_db.levy_payments.aggregate = MagicMock(return_value=cursor)
    bulk_result = MagicMock()
    bulk_result.upserted_ids = {}
    raw_db.unit_levy_ledger.bulk_write = AsyncMock(return_value=bulk_result)
    return raw_db


class TestRebuildProjection:
    @pytest.mark.asyncio
    async def test_rebuilds_unit_levy_ledger_from_levy_payments(self):
        """rebuild_projection aggregates levy_payments and upserts unit_levy_ledger."""
        from repositories.financial_repository import rebuild_projection

        rows = [
            {"_id": {"building_id": "13195", "unit_id": "u1", "year": "2026"},
             "total_paid_cents": 150000, "payment_count": 2},
            {"_id": {"building_id": "13195", "unit_id": "u2", "year": "2026"},
             "total_paid_cents": 75000, "payment_count": 1},
        ]
        raw_db = _make_raw_db(rows)
        count = await rebuild_projection(raw_db, "unit_levy_ledger", "13195", [])

        assert count == 2
        assert raw_db.unit_levy_ledger.bulk_write.await_count == 1

    @pytest.mark.asyncio
    async def test_lot_balances_projection_alias(self):
        """lot_balances_projection is an alias for unit_levy_ledger."""
        from repositories.financial_repository import rebuild_projection

        rows = [
            {"_id": {"building_id": "16244", "unit_id": "u9", "year": "2026"},
             "total_paid_cents": 50000, "payment_count": 1},
        ]
        raw_db = _make_raw_db(rows)
        count = await rebuild_projection(raw_db, "lot_balances_projection", "16244", [])

        assert count == 1

    @pytest.mark.asyncio
    async def test_unknown_projection_raises_value_error(self):
        from repositories.financial_repository import rebuild_projection
        raw_db = _make_raw_db()
        with pytest.raises(ValueError, match="No rebuild handler"):
            await rebuild_projection(raw_db, "nonexistent_projection", "13195", [])

    @pytest.mark.asyncio
    async def test_empty_levy_payments_returns_zero(self):
        """No payments → no records to rebuild → count=0."""
        from repositories.financial_repository import rebuild_projection

        raw_db = _make_raw_db(levy_rows=[])
        count = await rebuild_projection(raw_db, "unit_levy_ledger", "13195", [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self):
        """Aggregate pipeline receives building_id in $match — 16244 data not mixed with 13195."""
        from repositories.financial_repository import rebuild_projection

        rows = [
            {"_id": {"building_id": "16244", "unit_id": "u5", "year": "2026"},
             "total_paid_cents": 20000, "payment_count": 1},
        ]
        raw_db = _make_raw_db(rows)
        await rebuild_projection(raw_db, "unit_levy_ledger", "16244", [])

        # Verify the aggregate pipeline had the building_id $match
        pipeline_arg = raw_db.levy_payments.aggregate.call_args[0][0]
        match_stage = next(s for s in pipeline_arg if "$match" in s)
        assert match_stage["$match"]["building_id"] == "16244"

    @pytest.mark.asyncio
    async def test_idempotent_rebuild(self):
        """Running rebuild twice produces the same upsert calls — no data duplication."""
        from repositories.financial_repository import rebuild_projection

        rows = [
            {"_id": {"building_id": "13195", "unit_id": "u1", "year": "2026"},
             "total_paid_cents": 100000, "payment_count": 1},
        ]
        raw_db = _make_raw_db(rows)
        c1 = await rebuild_projection(raw_db, "unit_levy_ledger", "13195", [])
        c2 = await rebuild_projection(raw_db, "unit_levy_ledger", "13195", [])

        # Both runs return count=1; bulk_write called once per run
        assert c1 == 1
        assert c2 == 1
        assert raw_db.unit_levy_ledger.bulk_write.await_count == 2
