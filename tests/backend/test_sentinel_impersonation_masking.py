from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import Request

from models.user import UserRole
from server import get_resident_directory, get_owner_unit


@pytest.mark.asyncio
async def test_get_resident_directory_masking_impersonated():
    """Verify resident directory masking during impersonation."""
    mock_user = {
        "id": "admin-1",
        "role": UserRole.SUPER_ADMIN,
        "impersonator_id": "super-admin-id"
    }

    mock_results = [
        {
            "id": "user-1",
            "full_name": "John Doe",
            "email": "john@example.com",
            "phone": "+61411222333",
            "unit_number": "101"
        }
    ]

    with patch("server.db") as mock_db:
        # _server_agg() does `cursor = await collection.aggregate(pipeline)` — aggregate must be AsyncMock
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=mock_results)
        mock_db.memberships.aggregate = AsyncMock(return_value=mock_cursor)

        results = await get_resident_directory(current_user=mock_user, building_id="13195")

        assert len(results) == 1
        assert results[0]["full_name"] == "Resident"
        assert results[0]["email"] == "j***@example.com"
        assert results[0]["phone"] == "+6******33"


@pytest.mark.asyncio
async def test_get_owner_unit_masking_impersonated():
    """Verify single owner/unit view masking during impersonation."""
    # This endpoint is more complex because it uses many gathered tasks.
    # We mainly want to test the part after gathering.

    mock_user = {
        "id": "admin-1",
        "role": UserRole.SUPER_ADMIN,
        "impersonator_id": "super-admin-id",
        "unit_number": "101"
    }

    mock_unit = {
        "unit_number": "101",
        "owner_name": "Jane Doe",
        "owner_email": "jane@example.com",
        "tenant_name": "Bob Smith",
        "tenant_email": "bob@example.com",
        "lot_number": "LOT101",
        "entitlement": 100
    }

    # Mocking all the background dependencies used by get_owner_unit
    with patch("server.db") as mock_db, \
            patch("server._get_owner_info", AsyncMock(return_value={})), \
            patch("utils.finance_helpers.get_latest_levy_year", AsyncMock(return_value="2025")), \
            patch("utils.finance_helpers.get_latest_ledger_year", AsyncMock(return_value="2025")), \
            patch("utils.finance_helpers.get_levy_rates", AsyncMock(return_value={})), \
            patch("utils.finance_helpers.compute_period_due_dates", return_value=[]):
        mock_db.units.find_one = AsyncMock(return_value=mock_unit)
        mock_db.settings.find_one = AsyncMock(return_value={})
        mock_db.listings.count_documents = AsyncMock(return_value=0)
        mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2025"})
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={})
        mock_db.users.find_one = AsyncMock(return_value={})
        mock_db.levy_payments.find.return_value.to_list = AsyncMock(return_value=[])
        # get_owner_unit uses db.memberships.find({"building_id": ...}).to_list(None)
        membership_cursor = MagicMock()
        membership_cursor.to_list = AsyncMock(return_value=[])
        mock_db.memberships.find.return_value = membership_cursor

        response = await get_owner_unit(unit_number="101", current_user=mock_user, building_id="13195")

        assert response.owner_name == "Resident"
        assert response.owner_email == "j***@example.com"
        assert response.tenant_name == "Resident"
        assert response.tenant_email == "b***@example.com"
