from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_repair_orphan_staff_memberships_upserts_missing_membership():
    from utils.staff_membership_repair import repair_orphan_staff_memberships

    mock_db = MagicMock()
    mock_db.memberships.distinct = AsyncMock(return_value=[])
    orphan_cursor = MagicMock()
    orphan_cursor.to_list = AsyncMock(return_value=[
        {
            "id": "staff-001",
            "role": "service_provider",
            "created_at": "2026-04-14T10:00:00+00:00",
        }
    ])
    mock_db.users.find = MagicMock(return_value=orphan_cursor)
    mock_db.memberships.update_one = AsyncMock(return_value=MagicMock(upserted_id="mongo-1"))

    with patch("utils.staff_membership_repair.db", mock_db):
        repaired = await repair_orphan_staff_memberships("13195")

    assert repaired == 1
    membership_filter, membership_update = mock_db.memberships.update_one.call_args[0]
    assert membership_filter == {"user_id": "staff-001", "building_id": "13195"}
    assert membership_update["$setOnInsert"]["roles"] == ["service_provider"]
    assert membership_update["$setOnInsert"]["building_id"] == "13195"


@pytest.mark.asyncio
async def test_repair_orphan_staff_memberships_normalizes_legacy_pending_owner_status():
    from utils.staff_membership_repair import repair_orphan_staff_memberships

    mock_db = MagicMock()
    mock_db.memberships.distinct = AsyncMock(return_value=["staff-001"])
    staff_cursor = MagicMock()
    staff_cursor.to_list = AsyncMock(return_value=[
        {
            "id": "staff-001",
            "role": "service_provider",
            "status": "pending_owner_approval",
            "is_approved": False,
            "created_at": "2026-04-14T10:00:00+00:00",
        }
    ])
    mock_db.users.find = MagicMock(return_value=staff_cursor)
    mock_db.memberships.update_one = AsyncMock(return_value=MagicMock(upserted_id=None))
    mock_db.users.update_one = AsyncMock(return_value=MagicMock())

    with patch("utils.staff_membership_repair.db", mock_db):
        repaired = await repair_orphan_staff_memberships("13195")

    assert repaired == 0
    mock_db.users.update_one.assert_called_once()
    user_filter, user_update = mock_db.users.update_one.call_args[0]
    assert user_filter == {"id": "staff-001"}
    assert user_update["$set"]["status"] == "active"
