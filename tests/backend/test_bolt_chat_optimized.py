from unittest.mock import AsyncMock, patch

import pytest

from models.chat import ChatGroupUpdate
from routers.chat import check_group_membership, check_group_admin, get_chat_group, update_chat_group


@pytest.mark.asyncio
async def test_check_group_membership_optimized():
    with patch("routers.chat.db") as mock_db:
        mock_db.chat_groups.count_documents = AsyncMock(return_value=1)

        result = await check_group_membership("group-1", "user-1")

        assert result is True
        mock_db.chat_groups.count_documents.assert_called_once_with({"id": "group-1", "members.user_id": "user-1"})


@pytest.mark.asyncio
async def test_check_group_admin_optimized():
    with patch("routers.chat.db") as mock_db:
        mock_db.chat_groups.count_documents = AsyncMock(return_value=1)

        result = await check_group_admin("group-1", "user-1")

        assert result is True
        mock_db.chat_groups.count_documents.assert_called_once_with({
            "id": "group-1",
            "members": {"$elemMatch": {"user_id": "user-1", "role": "admin"}}
        })


@pytest.mark.asyncio
async def test_get_chat_group_optimized():
    mock_user = {"id": "user-1", "full_name": "Test User"}
    mock_group = {
        "id": "group-1",
        "name": "Test Group",
        "members": [
            {"user_id": "user-1", "full_name": "Test User", "role": "member", "joined_at": "2026-01-01T10:00:00Z"}],
        "settings": {"allow_member_invite": True, "member_approval_required": False, "who_can_delete": ["admin"],
                     "auto_join_criteria": None},
        "is_system": False,
        "type": "custom",
        "created_by": "user-1",
        "created_at": "2026-01-01T10:00:00Z"
    }

    with patch("routers.chat.db") as mock_db:
        mock_db.chat_groups.find_one = AsyncMock(return_value=mock_group)
        mock_db.group_messages.find_one = AsyncMock(return_value=None)

        response = await get_chat_group("group-1", current_user=mock_user)

        assert response.id == "group-1"
        assert response.member_count == 1
        mock_db.chat_groups.find_one.assert_called_once()


@pytest.mark.asyncio
async def test_update_chat_group_optimized():
    mock_user = {"id": "user-1", "full_name": "Test User"}
    mock_group = {
        "id": "group-1",
        "name": "Test Group",
        "members": [
            {"user_id": "user-1", "full_name": "Test User", "role": "admin", "joined_at": "2026-01-01T10:00:00Z"}],
        "settings": {"allow_member_invite": True, "member_approval_required": False, "who_can_delete": ["admin"],
                     "auto_join_criteria": None},
        "is_system": False,
        "type": "custom",
        "created_by": "user-1",
        "created_at": "2026-01-01T10:00:00Z"
    }

    with patch("routers.chat.db") as mock_db:
        mock_db.chat_groups.find_one = AsyncMock(return_value=mock_group)
        mock_db.chat_groups.update_one = AsyncMock()

        update_data = ChatGroupUpdate(name="New Name")
        response = await update_chat_group("group-1", update_data, current_user=mock_user)

        assert response.name == "New Name"
        mock_db.chat_groups.update_one.assert_called_once()
