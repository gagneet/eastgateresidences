"""
Tests for Migration 005: delete stale feature toggle keys (F-012).

Covers: stale key deletion, dry-run, already-clean state, both keys.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "backend"))

from scripts.migrations.migration_005_rename_stale_toggle_keys import cleanup, STALE_KEYS


def _make_db(docs_by_key: dict) -> MagicMock:
    """Build a mock db whose feature_toggles collection returns docs per key."""
    mock_db = MagicMock()

    def _find_side_effect(query, *args, **kwargs):
        key = query.get("feature_key")
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=docs_by_key.get(key, []))
        return cursor

    mock_db.feature_toggles.find.side_effect = _find_side_effect
    mock_db.feature_toggles.delete_many = AsyncMock(
        return_value=MagicMock(deleted_count=1)
    )
    return mock_db


class TestCleanupStaleKeys:
    @pytest.mark.asyncio
    async def test_deletes_savings_ledger_key(self):
        db = _make_db({"savings_ledger": [{"_id": "a1", "building_id": None}]})
        result = await cleanup(db)
        assert result["savings_ledger"] == 1
        db.feature_toggles.delete_many.assert_any_call({"feature_key": "savings_ledger"})

    @pytest.mark.asyncio
    async def test_deletes_building_health_score_key(self):
        db = _make_db({"building_health_score": [{"_id": "b1", "building_id": "13195"}]})
        result = await cleanup(db)
        assert result["building_health_score"] == 1
        db.feature_toggles.delete_many.assert_any_call({"feature_key": "building_health_score"})

    @pytest.mark.asyncio
    async def test_both_keys_deleted(self):
        db = _make_db({
            "savings_ledger": [{"_id": "x1", "building_id": None}],
            "building_health_score": [{"_id": "x2", "building_id": None}],
        })
        result = await cleanup(db)
        assert result["savings_ledger"] == 1
        assert result["building_health_score"] == 1
        assert db.feature_toggles.delete_many.call_count == 2

    @pytest.mark.asyncio
    async def test_already_clean_no_delete(self):
        db = _make_db({})  # no stale docs
        result = await cleanup(db)
        db.feature_toggles.delete_many.assert_not_called()
        assert all(v == 0 for v in result.values())


class TestCleanupDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_delete(self):
        db = _make_db({
            "savings_ledger": [{"_id": "d1", "building_id": None}],
            "building_health_score": [{"_id": "d2", "building_id": None}],
        })
        result = await cleanup(db, dry_run=True)
        db.feature_toggles.delete_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_reports_counts(self):
        db = _make_db({
            "savings_ledger": [{"_id": "e1", "building_id": None}],
        })
        result = await cleanup(db, dry_run=True)
        assert result["savings_ledger"] == 1

    @pytest.mark.asyncio
    async def test_dry_run_clean_state_reports_zero(self):
        db = _make_db({})
        result = await cleanup(db, dry_run=True)
        db.feature_toggles.delete_many.assert_not_called()
        assert all(v == 0 for v in result.values())


class TestStaleKeyList:
    def test_stale_keys_are_the_expected_two(self):
        assert set(STALE_KEYS) == {"savings_ledger", "building_health_score"}
