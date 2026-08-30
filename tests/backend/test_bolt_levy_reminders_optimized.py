from datetime import datetime, date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from routers.notifications import send_levy_reminder
from server import send_levy_reminder as send_levy_reminder_server


def _make_user(role: str, id="admin-id"):
    return {
        "id": id,
        "full_name": "Admin User",
        "email": "admin@example.com",
        "role": role,
        "is_active": True,
    }


@pytest.mark.asyncio
async def test_send_levy_reminder_optimized():
    # Setup mocks
    mock_permissions = MagicMock()
    mock_permissions.can_send_notifications = True

    # 1. Mock Levy Event
    today = date(2026, 3, 10)
    mock_event = {
        "start_date": "2026-03-31",
        "title": "Q1 Levy Due",
        "event_type": "levy_due"
    }
    mock_events_cursor = MagicMock()
    mock_events_cursor.to_list = AsyncMock(return_value=[mock_event])

    # 2. Mock Annual Levies / Rates
    mock_levy_year = "2026"
    mock_levy_rates = {
        "admin_annual": 40.0,
        "admin_quarterly": 10.0,
        "sinking_annual": 12.0,
        "sinking_quarterly": 3.0,
    }

    # 3. Mock Units
    mock_units = [
        {"unit_number": "101", "owner_email": "owner1@example.com", "entitlement": 100},
        {"unit_number": "102", "owner_email": "owner2@example.com", "entitlement": 120},
    ]
    mock_units_cursor = MagicMock()
    mock_units_cursor.to_list = AsyncMock(return_value=mock_units)

    # 4. Mock Owners (Users)
    mock_owners = [
        {"id": "user1", "email": "owner1@example.com", "full_name": "Owner One",
         "mail_username": "alias1@eastgate.local"},
        {"id": "user2", "email": "owner2@example.com", "full_name": "Owner Two"},
    ]
    mock_owners_cursor = MagicMock()
    mock_owners_cursor.to_list = AsyncMock(return_value=mock_owners)

    mock_db = MagicMock()
    # Chain find calls
    mock_db.events.find.return_value = mock_events_cursor
    mock_db.units.find.return_value = mock_units_cursor
    mock_db.users.find.return_value = mock_owners_cursor
    mock_db.settings.find_one = AsyncMock(return_value={})
    mock_memberships_cursor = MagicMock()
    mock_memberships_cursor.to_list = AsyncMock(return_value=[{"user_id": "user1"}, {"user_id": "user2"}])
    mock_db.memberships.find.return_value = mock_memberships_cursor

    # Patch dependencies
    with patch("routers.notifications.get_user_permissions", return_value=mock_permissions), \
            patch("routers.notifications.db", mock_db), \
            patch("routers.notifications.send_email_async", new_callable=AsyncMock) as mock_send_email, \
            patch("routers.notifications.get_latest_levy_year", AsyncMock(return_value=mock_levy_year)), \
            patch("routers.notifications.get_levy_rates", AsyncMock(return_value=mock_levy_rates)), \
            patch("routers.notifications.datetime") as mock_datetime:
        mock_datetime.now.return_value.date.return_value = today
        mock_datetime.fromisoformat = datetime.fromisoformat

        mock_send_email.return_value = {"success": True}

        # Execute
        current_user = _make_user("super_admin")
        response = await send_levy_reminder(current_user=current_user, building_id="bldg-1")

        # Verify logic
        assert "Sent 3 levy reminders" in response["message"]
        assert response["next_due_date"] == "2026-03-31"

        # Verify parallelized email dispatch (Owner 1 has 2 emails, Owner 2 has 1)
        assert mock_send_email.call_count == 3

        # Verify batch fetching of users - should only be called once
        mock_db.users.find.assert_called_once()

    # Repeat for server.py implementation
    mock_db.users.find.reset_mock()
    mock_send_email.reset_mock()
    mock_db.settings.find_one = AsyncMock(return_value={})
    mock_db.memberships.find.return_value = mock_memberships_cursor

    with patch("server.get_user_permissions", return_value=mock_permissions), \
            patch("server.db", mock_db), \
            patch("server.send_email_async", new_callable=AsyncMock) as mock_send_email, \
            patch("utils.finance_helpers.get_latest_levy_year", AsyncMock(return_value=mock_levy_year)), \
            patch("utils.finance_helpers.get_levy_rates", AsyncMock(return_value=mock_levy_rates)), \
            patch("server.datetime") as mock_datetime:
        mock_datetime.now.return_value.date.return_value = today
        mock_datetime.fromisoformat = datetime.fromisoformat

        mock_send_email.return_value = {"success": True}

        response = await send_levy_reminder_server(current_user=current_user, building_id="bldg-1")

        # Verify logic
        assert "Sent 3 levy reminders" in response["message"]
        assert response["next_due_date"] == "2026-03-31"

        # Verify parallelized email dispatch (Owner 1 has 2 emails, Owner 2 has 1)
        assert mock_send_email.call_count == 3

        # Verify batch fetching of users - should only be called once
        mock_db.users.find.assert_called_once()
        args, kwargs = mock_db.users.find.call_args
        assert "owner" in args[0]["role"]["$in"]

        # Verify personalization and escaping in one call
        call_args = mock_send_email.call_args_list[0][1]
        assert call_args["to_email"] in ["owner1@example.com", "alias1@eastgate.local"]
        assert "Owner One" in call_args["html_content"]
        assert "$1,300.00" in call_args["html_content"]  # (4000+1200)/4 = 1300 per 100 UOE


@pytest.mark.asyncio
async def test_send_levy_reminder_no_events():
    mock_permissions = MagicMock()
    mock_permissions.can_send_notifications = True

    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_db.events.find.return_value = mock_cursor

    with patch("routers.notifications.get_user_permissions", return_value=mock_permissions), \
            patch("routers.notifications.db", mock_db):
        current_user = _make_user("super_admin")
        response = await send_levy_reminder(current_user=current_user)

        assert response["message"] == "No upcoming levy due dates found"
