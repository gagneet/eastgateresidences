from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
import pytest

# server is pre-imported by conftest.py with the correct production DB settings.
# No need to mutate os.environ here — that would poison subsequent test files.
import server


def _make_mock_db() -> MagicMock:
    mock_db = MagicMock()
    # _server_agg() does `cursor = await collection.aggregate(pipeline)` — aggregate must be AsyncMock
    _mem_cursor = MagicMock()
    _mem_cursor.to_list = AsyncMock(return_value=[{
        "total": 10,
        "pending": 2,
        "pending_owner_approval": 1
    }])
    mock_db.memberships.aggregate = AsyncMock(return_value=_mem_cursor)
    mock_db.units.count_documents = AsyncMock(return_value=100)
    mock_db.maintenance_requests.count_documents = AsyncMock(return_value=5)
    _inv_cursor = MagicMock()
    _inv_cursor.to_list = AsyncMock(return_value=[{
        "count": 3,
        "total_amount": 1500.50
    }])
    mock_db.invoices.aggregate = AsyncMock(return_value=_inv_cursor)
    mock_db.user_units.distinct = AsyncMock(return_value=["UA001", "UA002", "UA003"])
    mock_db.documents.count_documents = AsyncMock(return_value=20)
    mock_db.listings.count_documents = AsyncMock(return_value=2)
    mock_db.meetings.count_documents = AsyncMock(return_value=1)
    mock_db.work_order_invoices.count_documents = AsyncMock(return_value=1)
    return mock_db


async def test_get_admin_stats_v2():
    current_user = {"id": "admin_id", "role": "super_admin", "full_name": "Admin User"}
    building_id = "test_building"

    mock_db = _make_mock_db()

    # Scope server.db replacement to this test only — restored automatically
    with patch.object(server, "db", mock_db), \
            patch.object(server, "repair_orphan_staff_memberships", AsyncMock()), \
            patch("server.get_user_permissions") as mock_perms:
        mock_perms.return_value.can_manage_users = True

        result = await server.get_admin_stats(current_user=current_user, building_id=building_id)

        assert result["total_users"] == 10
        assert result["pending_users"] == 2
        assert result["pending_owner_approval_count"] == 1

        mock_db.memberships.aggregate.assert_called_once()
        pipeline = mock_db.memberships.aggregate.call_args[0][0]

        assert any("$match" in stage for stage in pipeline)
        assert any(
            "$group" in stage and "_id" in stage["$group"] and stage["$group"]["_id"] == "$user_id"
            for stage in pipeline
        )
        assert any("$lookup" in stage for stage in pipeline)
        assert any("$unwind" in stage for stage in pipeline)
        assert any(
            "$group" in stage and "_id" in stage["$group"] and stage["$group"]["_id"] is None
            for stage in pipeline
        )


if __name__ == "__main__":
    asyncio.run(test_get_admin_stats_v2())
