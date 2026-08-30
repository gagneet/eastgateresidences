from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers.community import get_resident_directory as get_dir_community
from server import get_resident_directory as get_dir_server


@pytest.mark.asyncio
async def test_get_resident_directory_optimized_aggregation():
    mock_user = {"id": "user1", "role": "owner"}
    building_id = "13195"

    mock_result = [
        {
            "id": "user1",
            "full_name": "John Doe",
            "unit_number": "UA101",
            "unit_type": "apartment",
            "move_in_date": "2023-01-01",
            "entitlement_units": 100,
            "annual_levy": 5000.0,
            "email": "john@example.com"
        }
    ]

    mock_cursor = AsyncMock()
    mock_cursor.to_list.return_value = mock_result

    # Test community router version
    with patch("routers.community.db") as mock_db_comm:
        mock_db_comm.memberships.aggregate.return_value = mock_cursor
        result = await get_dir_community(current_user=mock_user, building_id=building_id)
        assert len(result) == 1
        assert result[0]["full_name"] == "John Doe"
        assert mock_db_comm.memberships.aggregate.called
        pipeline = mock_db_comm.memberships.aggregate.call_args[0][0]
        assert pipeline[0]["$match"]["building_id"] == building_id

    # Test server.py version — _server_agg() does `cursor = await collection.aggregate(pipeline)`,
    # so aggregate must be AsyncMock, not a plain MagicMock.
    mock_cursor.to_list.reset_mock()
    with patch("server.db") as mock_db_server:
        mock_db_server.memberships.aggregate = AsyncMock(return_value=mock_cursor)
        result = await get_dir_server(current_user=mock_user, building_id=building_id)
        assert len(result) == 1
        assert result[0]["full_name"] == "John Doe"
        assert mock_db_server.memberships.aggregate.called
        pipeline = mock_db_server.memberships.aggregate.call_args[0][0]
        assert pipeline[0]["$match"]["building_id"] == building_id


@pytest.mark.asyncio
async def test_get_resident_directory_impersonation_masking():
    mock_user = {"id": "admin1", "role": "super_admin", "impersonator_id": "boss"}
    building_id = "13195"

    mock_result = [
        {
            "id": "user1",
            "full_name": "John Doe",
            "unit_number": "UA101",
            "email": "john@example.com",
            "phone": "0412345678"
        }
    ]

    mock_cursor = AsyncMock()
    mock_cursor.to_list.return_value = mock_result

    with patch("server.db") as mock_db:
        mock_db.memberships.aggregate = AsyncMock(return_value=mock_cursor)
        # We need to mock mask_email and mask_phone if they are imported in server.py
        with patch("server.mask_email", return_value="j***@example.com"), \
                patch("server.mask_phone", return_value="041*******"):
            result = await get_dir_server(current_user=mock_user, building_id=building_id)
            assert result[0]["full_name"] == "Resident"
            assert result[0]["email"] == "j***@example.com"
            assert result[0]["phone"] == "041*******"
