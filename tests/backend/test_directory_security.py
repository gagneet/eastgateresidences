from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from models.user import UserRole
from server import get_resident_directory
from utils.auth import get_approved_user


@pytest.mark.asyncio
async def test_directory_access_unapproved_user():
    """Test that an unapproved user cannot access the resident directory."""
    unapproved_user = {
        "id": "user_pending",
        "role": "owner",
        "full_name": "Pending Owner",
        "is_approved": False,
        "is_active": True
    }

    with pytest.raises(HTTPException) as excinfo:
        await get_approved_user(current_user=unapproved_user)

    assert excinfo.value.status_code == 403
    assert "pending approval" in excinfo.value.detail.lower()


@pytest.mark.asyncio
@patch("server.db")
async def test_directory_access_approved_user(mock_db):
    """Test that an approved user CAN access the directory."""
    approved_user = {
        "id": "user_approved",
        "role": "owner",
        "full_name": "Approved Owner",
        "is_approved": True,
        "is_active": True
    }

    mock_directory = [
        {"id": "res1", "full_name": "John Resident", "unit_number": "UA101"}
    ]
    # _server_agg() does `cursor = await collection.aggregate(pipeline)` — aggregate must be AsyncMock
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=mock_directory)
    mock_db.memberships.aggregate = AsyncMock(return_value=mock_cursor)

    response = await get_resident_directory(current_user=approved_user, building_id="13195")
    assert len(response) == 1
    assert response[0]["full_name"] == "John Resident"


@pytest.mark.asyncio
@patch("server.db")
async def test_directory_access_admin_unapproved(mock_db):
    """Test that an admin CAN access the directory even if is_approved is False."""
    admin_user = {
        "id": "admin1",
        "role": UserRole.SUPER_ADMIN,
        "full_name": "Admin",
        "is_approved": False,
        "is_active": True
    }

    mock_directory = [
        {"id": "res1", "full_name": "John Resident", "unit_number": "UA101"}
    ]
    # _server_agg() does `cursor = await collection.aggregate(pipeline)` — aggregate must be AsyncMock
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=mock_directory)
    mock_db.memberships.aggregate = AsyncMock(return_value=mock_cursor)

    response = await get_resident_directory(current_user=admin_user, building_id="13195")
    assert len(response) == 1
