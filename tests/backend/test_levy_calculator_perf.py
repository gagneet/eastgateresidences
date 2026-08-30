"""
Performance tests for levy calculator — parallel DB queries.

Uses routers.finance (NOT backend.routers.finance) because
conftest.py adds backend/ to sys.path directly.
"""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from routers.finance import calculate_levies


@pytest.mark.asyncio
async def test_calculate_levies_parallel_logic():
    # Mock data
    mock_levy = {
        "year": "2026",
        "status": "actual",
        "admin_levy_per_uoe_annual": 28.82,
        "sinking_levy_per_uoe_annual": 10.92,
        "total_uoe": 10000,
        "admin_fund": {"total_income": 288200},
        "sinking_fund": {"total_income": 109200},
        "payment_schedule": [
            {"quarter": "Q1", "due_date": "2026-03-31"},
            {"quarter": "Q2", "due_date": "2026-06-30"},
            {"quarter": "Q3", "due_date": "2026-09-30"},
            {"quarter": "Q4", "due_date": "2026-12-31"},
        ]
    }
    mock_units = [
        {"unit_number": "UA001", "unit_type": "apartment", "entitlement": 115},
        {"unit_number": "TH071", "unit_type": "townhouse", "entitlement": 150},
    ]
    mock_settings = {
        "levy_collection_frequency": "quarterly",
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "last",
    }

    # Setup mocks
    with patch("routers.finance.db") as mock_db, \
            patch("routers.finance.get_user_permissions") as mock_perms, \
            patch("routers.finance._get_general_settings", AsyncMock(return_value=mock_settings)):
        # Mock permissions
        mock_perms.return_value = MagicMock(can_view_finances=True)

        # Mock database calls
        mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
        mock_db.units.find.return_value.to_list = AsyncMock(return_value=mock_units)
        mock_db.settings.find_one = AsyncMock(return_value=mock_settings)
        mock_db.scheme_classes.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.buildings.find_one = AsyncMock(return_value=None)

        # Mock user
        mock_user = {"id": "admin", "role": "super_admin"}

        # Call the function
        response = await calculate_levies(year="2026", current_user=mock_user, building_id="13195")

        # Verify response structure and data
        assert response["year"] == "2026"
        assert len(response["levies"]) == 2

        # UA001 calculations (penny-allocation may differ by ≤$0.02 due to rounding)
        # admin_annual ≈ 28.82 × 115 = 3314.3
        # sinking_annual ≈ 10.92 × 115 = 1255.8
        ua001 = next(l for l in response["levies"] if l["unit_number"] == "UA001")
        assert abs(ua001["admin_annual"] - 3314.3) < 0.05, ua001["admin_annual"]
        assert abs(ua001["sinking_annual"] - 1255.8) < 0.05, ua001["sinking_annual"]
        assert abs(ua001["total_annual"] - 4570.1) < 0.10, ua001["total_annual"]

        # Verify all database tasks were called
        mock_db.annual_levies.find_one.assert_called_once()
        mock_db.units.find.assert_called_once()


@pytest.mark.asyncio
async def test_calculate_levies_no_levy_found():
    with patch("routers.finance.db") as mock_db, \
            patch("routers.finance.get_user_permissions") as mock_perms, \
            patch("routers.finance._get_general_settings", AsyncMock(return_value={})):
        mock_perms.return_value = MagicMock(can_view_finances=True)

        # Mock database calls - levy not found
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)
        mock_db.units.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.settings.find_one = AsyncMock(return_value={})
        mock_db.scheme_classes.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.buildings.find_one = AsyncMock(return_value=None)

        mock_user = {"id": "admin", "role": "super_admin"}

        response = await calculate_levies(year="9999", current_user=mock_user, building_id="13195")

        assert response == {"error": "No levy data found", "levies": []}
