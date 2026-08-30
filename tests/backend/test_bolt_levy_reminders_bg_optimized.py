import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
import pytest

# Add backend to path
sys.path.insert(0, os.path.abspath("backend"))

# Mock 'db' before importing server to avoid connection issues during import
mock_db = MagicMock()


@pytest.mark.asyncio
async def test_check_and_send_levy_reminders_optimized_overdue():
    # We need to mock 'db' before importing server to avoid connection issues during import
    with patch("motor.motor_asyncio.AsyncIOMotorClient"), \
            patch("server.db", mock_db), \
            patch("database.db", mock_db):

        from server import check_and_send_levy_reminders

        # 1. Mock Buildings
        mock_buildings = [{"id": "bldg-1", "name": "Building One"}]
        mock_buildings_cursor = MagicMock()
        mock_buildings_cursor.to_list = AsyncMock(return_value=mock_buildings)
        mock_db.buildings.find.return_value = mock_buildings_cursor

        # 2. Mock Settings — GAP-FIN-012: settings now stored in levy_reminder_settings collection
        mock_settings = {
            "building_id": "bldg-1",
            "enabled": True,
            "pre_due_days": [14, 7],
            "overdue_days": [1, 7, 14, 30],
            "overdue_threshold_cents": 100,
        }
        mock_db.levy_reminder_settings.find_one = AsyncMock(return_value=mock_settings)

        # 3. Mock Events (Overdue)
        today = date(2026, 3, 10)
        # 1 day overdue
        mock_event = {
            "start_date": "2026-03-09",
            "title": "Q1 Levy Due",
            "event_type": "levy_due"
        }
        mock_events_cursor = MagicMock()
        mock_events_cursor.to_list = AsyncMock(return_value=[mock_event])
        mock_db.events.find.return_value = mock_events_cursor

        # 4. Mock Reminder Log (not sent yet)
        mock_db.auto_reminders_log.find_one = AsyncMock(return_value=None)

        # 5. Mock Units with Arrears
        mock_units_with_arrears = [
            {"unit_number": "101", "net_balance": 500.0},
            {"unit_number": "102", "net_balance": 750.0},
        ]
        mock_ledger_cursor = MagicMock()
        mock_ledger_cursor.to_list = AsyncMock(return_value=mock_units_with_arrears)
        mock_db.unit_levy_ledger.find.return_value = mock_ledger_cursor

        # 6. Mock Owners (user_units)
        mock_owners = [
            {"user_id": "user-1", "unit_number": "101"},
            {"user_id": "user-2", "unit_number": "102"},
        ]
        mock_owners_cursor = MagicMock()
        mock_owners_cursor.to_list = AsyncMock(return_value=mock_owners)
        mock_db.user_units.find.return_value = mock_owners_cursor

        # Patch utilities
        with patch("server.db", mock_db), \
                patch("server.datetime") as mock_datetime, \
                patch("server.create_notifications_batch", AsyncMock(return_value=2)) as mock_batch_notif, \
                patch("utils.finance_helpers.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("asyncio.sleep", side_effect=asyncio.CancelledError):

            mock_datetime.now.return_value = datetime(2026, 3, 10, tzinfo=timezone.utc)
            mock_datetime.fromisoformat = datetime.fromisoformat

            try:
                await check_and_send_levy_reminders()
            except asyncio.CancelledError:
                pass

            # Verify optimization
            # 1. Batch fetch owners
            mock_db.user_units.find.assert_called_once()
            args, kwargs = mock_db.user_units.find.call_args
            assert args[0]["unit_number"]["$in"] == ["101", "102"]

            # 2. Batch notifications
            mock_batch_notif.assert_called_once()
            notifs = mock_batch_notif.call_args[0][0]
            assert len(notifs) == 2
            assert notifs[0]["user_id"] == "user-1"
            assert "Unit 101" in notifs[0]["message"]
            assert notifs[1]["user_id"] == "user-2"
            assert "Unit 102" in notifs[1]["message"]

            # 3. Log entry
            mock_db.auto_reminders_log.insert_one.assert_called_once()
            log_doc = mock_db.auto_reminders_log.insert_one.call_args[0][0]
            assert log_doc["sent_count"] == 2
            assert log_doc["type"] == "overdue"


@pytest.mark.asyncio
async def test_check_and_send_levy_reminders_error_handling():
    # Test that the loop continues even if one building fails
    mock_db.buildings.find.return_value.to_list = AsyncMock(return_value=[
        {"id": "fail-bldg"},
        {"id": "success-bldg"}
    ])

    # success-bldg will have settings, fail-bldg will raise error on settings fetch
    def mock_find_one(query, projection=None):
        if query.get("building_id") == "fail-bldg":
            raise Exception("Database Error")
        return AsyncMock()()  # Return an awaitable that returns None

    # GAP-FIN-012: settings now in levy_reminder_settings collection
    mock_db.levy_reminder_settings.find_one.side_effect = mock_find_one

    with patch("motor.motor_asyncio.AsyncIOMotorClient"), \
            patch("server.db", mock_db), \
            patch("database.db", mock_db), \
            patch("server.datetime") as mock_datetime, \
            patch("asyncio.sleep", side_effect=asyncio.CancelledError), \
            patch("server.logger") as mock_logger:

        from server import check_and_send_levy_reminders
        mock_datetime.now.return_value = datetime(2026, 3, 10, tzinfo=timezone.utc)

        try:
            await check_and_send_levy_reminders()
        except asyncio.CancelledError:
            pass

        # Verify logger was called for fail-bldg
        # The code catches Exception inside the loop over buildings
        mock_logger.error.assert_any_call("Error processing levy event for building fail-bldg: Database Error")
