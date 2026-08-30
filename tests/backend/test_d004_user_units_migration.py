"""
Tests for Migration 003: backfill user_id on legacy user_units documents (D-004).

Run from project root:
    backend/venv/bin/pytest tests/backend/test_d004_user_units_migration.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

from scripts.migrations.migration_003_backfill_user_units_user_id import backfill


def _oid():
    return ObjectId()


def _make_db(legacy_docs, user_docs):
    """Build a minimal mock db with user_units and users collections."""
    mock_db = MagicMock()

    # user_units.find() returns legacy_docs
    uu_cursor = MagicMock()
    uu_cursor.to_list = AsyncMock(return_value=legacy_docs)
    mock_db.user_units.find.return_value = uu_cursor

    # users.find() returns user_docs
    u_cursor = MagicMock()
    u_cursor.to_list = AsyncMock(return_value=user_docs)
    mock_db.users.find.return_value = u_cursor

    mock_db.user_units.update_one = AsyncMock(
        return_value=MagicMock(modified_count=1)
    )
    return mock_db


# ─── Happy path ───────────────────────────────────────────────────────────────

class TestBackfillHappyPath:

    @pytest.mark.asyncio
    async def test_matched_doc_gets_user_id_set(self):
        oid = _oid()
        mock_db = _make_db(
            legacy_docs=[{"_id": oid, "owner_email": "alice@example.com",
                          "building_id": "13195", "unit_number": "101"}],
            user_docs=[{"id": "user-alice", "email": "alice@example.com"}],
        )

        result = await backfill(mock_db)

        assert result["total_legacy"] == 1
        assert result["matched"] == 1
        assert result["unmatched"] == 0
        assert result["updated"] == 1

        mock_db.user_units.update_one.assert_called_once()
        call_filter, call_update = mock_db.user_units.update_one.call_args[0]
        assert call_filter == {"_id": oid}
        assert call_update["$set"]["user_id"] == "user-alice"

    @pytest.mark.asyncio
    async def test_multiple_docs_all_matched(self):
        docs = [
            {"_id": _oid(), "owner_email": f"user{i}@ex.com",
             "building_id": "13195", "unit_number": str(i)}
            for i in range(5)
        ]
        users = [{"id": f"uid-{i}", "email": f"user{i}@ex.com"} for i in range(5)]
        mock_db = _make_db(docs, users)

        result = await backfill(mock_db)

        assert result["total_legacy"] == 5
        assert result["matched"] == 5
        assert result["unmatched"] == 0
        assert result["updated"] == 5
        assert mock_db.user_units.update_one.call_count == 5

    @pytest.mark.asyncio
    async def test_email_matching_is_case_insensitive(self):
        oid = _oid()
        mock_db = _make_db(
            legacy_docs=[{"_id": oid, "owner_email": "Alice@Example.COM",
                          "building_id": "13195", "unit_number": "101"}],
            user_docs=[{"id": "user-alice", "email": "alice@example.com"}],
        )

        result = await backfill(mock_db)

        assert result["matched"] == 1
        call_filter, call_update = mock_db.user_units.update_one.call_args[0]
        assert call_update["$set"]["user_id"] == "user-alice"


# ─── No-match / partial paths ────────────────────────────────────────────────

class TestBackfillNoMatch:

    @pytest.mark.asyncio
    async def test_unmatched_doc_not_updated(self):
        """Doc with no corresponding user must not be written to."""
        mock_db = _make_db(
            legacy_docs=[{"_id": _oid(), "owner_email": "ghost@example.com",
                          "building_id": "13195", "unit_number": "101"}],
            user_docs=[],
        )

        result = await backfill(mock_db)

        assert result["unmatched"] == 1
        assert result["updated"] == 0
        mock_db.user_units.update_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_matched_and_unmatched(self):
        docs = [
            {"_id": _oid(), "owner_email": "known@ex.com",
             "building_id": "13195", "unit_number": "1"},
            {"_id": _oid(), "owner_email": "unknown@ex.com",
             "building_id": "13195", "unit_number": "2"},
        ]
        users = [{"id": "uid-known", "email": "known@ex.com"}]
        mock_db = _make_db(docs, users)

        result = await backfill(mock_db)

        assert result["matched"] == 1
        assert result["unmatched"] == 1
        assert result["updated"] == 1

    @pytest.mark.asyncio
    async def test_no_legacy_docs_is_noop(self):
        """When there are no legacy docs, no DB writes should occur."""
        mock_db = _make_db(legacy_docs=[], user_docs=[])

        result = await backfill(mock_db)

        assert result["total_legacy"] == 0
        assert result["updated"] == 0
        mock_db.user_units.update_one.assert_not_called()


# ─── Dry-run mode ─────────────────────────────────────────────────────────────

class TestBackfillDryRun:

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self):
        mock_db = _make_db(
            legacy_docs=[{"_id": _oid(), "owner_email": "alice@ex.com",
                          "building_id": "13195", "unit_number": "101"}],
            user_docs=[{"id": "uid-alice", "email": "alice@ex.com"}],
        )

        result = await backfill(mock_db, dry_run=True)

        assert result["matched"] == 1
        # No DB write in dry-run
        assert result["updated"] == 0
        mock_db.user_units.update_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_still_reports_correctly(self):
        docs = [
            {"_id": _oid(), "owner_email": f"u{i}@ex.com",
             "building_id": "13195", "unit_number": str(i)}
            for i in range(3)
        ]
        users = [{"id": f"uid-{i}", "email": f"u{i}@ex.com"} for i in range(3)]
        mock_db = _make_db(docs, users)

        result = await backfill(mock_db, dry_run=True)

        assert result["total_legacy"] == 3
        assert result["matched"] == 3
        assert result["updated"] == 0


# ─── Building filter ─────────────────────────────────────────────────────────

class TestBackfillBuildingFilter:

    @pytest.mark.asyncio
    async def test_building_filter_passed_to_query(self):
        mock_db = _make_db(legacy_docs=[], user_docs=[])

        await backfill(mock_db, building_filter="16244")

        query_used = mock_db.user_units.find.call_args[0][0]
        assert query_used["building_id"] == "16244"

    @pytest.mark.asyncio
    async def test_no_building_filter_omits_building_id_from_query(self):
        mock_db = _make_db(legacy_docs=[], user_docs=[])

        await backfill(mock_db, building_filter=None)

        query_used = mock_db.user_units.find.call_args[0][0]
        assert "building_id" not in query_used


# ─── Query correctness ────────────────────────────────────────────────────────

class TestBackfillQueryCorrectness:

    @pytest.mark.asyncio
    async def test_query_excludes_docs_with_user_id_already(self):
        """The find query must filter for user_id $exists False."""
        mock_db = _make_db(legacy_docs=[], user_docs=[])

        await backfill(mock_db)

        query_used = mock_db.user_units.find.call_args[0][0]
        assert query_used["user_id"] == {"$exists": False}

    @pytest.mark.asyncio
    async def test_query_requires_owner_email_present(self):
        mock_db = _make_db(legacy_docs=[], user_docs=[])

        await backfill(mock_db)

        query_used = mock_db.user_units.find.call_args[0][0]
        assert query_used["owner_email"]["$exists"] is True
