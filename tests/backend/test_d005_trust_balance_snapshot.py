"""
Tests for D-005: balance_snapshot_at migration (migration_004).

Covers: backfill logic, dry-run, already-populated docs, fallbacks.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(docs: list[dict]) -> MagicMock:
    """Build a mock db whose trust_accounts_v2 cursor returns *docs*."""
    mock_db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    mock_db.trust_accounts_v2.find.return_value = cursor
    mock_db.trust_accounts_v2.update_one = AsyncMock(
        return_value=MagicMock(modified_count=1)
    )
    return mock_db


# ---------------------------------------------------------------------------
# Import under test
# ---------------------------------------------------------------------------

import importlib, sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "backend"))
from scripts.migrations.migration_004_add_balance_snapshot_at import backfill


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBackfillNothingToDo:
    @pytest.mark.asyncio
    async def test_returns_zero_when_all_docs_already_have_field(self):
        db = _make_db([])
        result = await backfill(db)
        assert result == {"total": 0, "updated": 0}
        db.trust_accounts_v2.update_one.assert_not_called()


class TestBackfillHappyPath:
    @pytest.mark.asyncio
    async def test_uses_updated_at_when_present(self):
        ts = "2025-06-01T10:00:00+00:00"
        db = _make_db([{"_id": "a1", "updated_at": ts, "created_at": "2025-01-01T00:00:00+00:00"}])
        result = await backfill(db)
        assert result["total"] == 1
        assert result["updated"] == 1
        db.trust_accounts_v2.update_one.assert_called_once()
        call_args = db.trust_accounts_v2.update_one.call_args
        assert call_args[0][1]["$set"]["balance_snapshot_at"] == ts

    @pytest.mark.asyncio
    async def test_falls_back_to_created_at_when_no_updated_at(self):
        ts = "2024-03-15T08:30:00+00:00"
        db = _make_db([{"_id": "b2", "created_at": ts}])
        result = await backfill(db)
        call_args = db.trust_accounts_v2.update_one.call_args
        assert call_args[0][1]["$set"]["balance_snapshot_at"] == ts

    @pytest.mark.asyncio
    async def test_uses_current_time_when_no_timestamps(self):
        before = datetime.now(timezone.utc).isoformat()
        db = _make_db([{"_id": "c3"}])
        result = await backfill(db)
        after = datetime.now(timezone.utc).isoformat()
        call_args = db.trust_accounts_v2.update_one.call_args
        snapshot_at = call_args[0][1]["$set"]["balance_snapshot_at"]
        assert before <= snapshot_at <= after

    @pytest.mark.asyncio
    async def test_processes_multiple_documents(self):
        docs = [
            {"_id": "x1", "updated_at": "2025-01-01T00:00:00+00:00"},
            {"_id": "x2", "updated_at": "2025-02-01T00:00:00+00:00"},
            {"_id": "x3", "created_at": "2024-06-01T00:00:00+00:00"},
        ]
        db = _make_db(docs)
        result = await backfill(db)
        assert result["total"] == 3
        assert result["updated"] == 3
        assert db.trust_accounts_v2.update_one.call_count == 3


class TestBackfillDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_update(self):
        docs = [{"_id": "d1", "updated_at": "2025-06-01T00:00:00+00:00"}]
        db = _make_db(docs)
        result = await backfill(db, dry_run=True)
        db.trust_accounts_v2.update_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_reports_total(self):
        docs = [
            {"_id": "e1", "updated_at": "2025-01-01T00:00:00+00:00"},
            {"_id": "e2", "updated_at": "2025-02-01T00:00:00+00:00"},
        ]
        db = _make_db(docs)
        result = await backfill(db, dry_run=True)
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_dry_run_returns_zero_updated(self):
        docs = [{"_id": "f1", "updated_at": "2025-06-01T00:00:00+00:00"}]
        db = _make_db(docs)
        result = await backfill(db, dry_run=True)
        assert result["updated"] == 0


class TestBackfillQueryFilter:
    @pytest.mark.asyncio
    async def test_query_excludes_docs_with_existing_field(self):
        db = _make_db([])
        await backfill(db)
        find_call_filter = db.trust_accounts_v2.find.call_args[0][0]
        assert find_call_filter == {"balance_snapshot_at": {"$exists": False}}

    @pytest.mark.asyncio
    async def test_query_projects_required_fields(self):
        db = _make_db([])
        await backfill(db)
        projection = db.trust_accounts_v2.find.call_args[0][1]
        assert "_id" in projection
        assert "updated_at" in projection
        assert "created_at" in projection


class TestBackfillUpdatedAtPriority:
    @pytest.mark.asyncio
    async def test_updated_at_takes_priority_over_created_at(self):
        """updated_at should be used even when created_at is also present."""
        updated_ts = "2025-12-01T00:00:00+00:00"
        created_ts = "2024-01-01T00:00:00+00:00"
        db = _make_db([{"_id": "g1", "updated_at": updated_ts, "created_at": created_ts}])
        await backfill(db)
        call_args = db.trust_accounts_v2.update_one.call_args
        assert call_args[0][1]["$set"]["balance_snapshot_at"] == updated_ts
