import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import asyncio

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

# cron_script and database are set by setup_module() at execution time (not collection time).
# Deferring sys.modules['database'] manipulation prevents poisoning test_tenant_isolation.py
# and other files that import TenantCollection at collection time.
cron_script = None
_original_database_module = None

# Placeholder for the database module — setup_module() replaces this with the mock.
# Declared at module level so the class can reference `database.db` without NameError.
import database  # noqa: E402 (import after path setup)


def setup_module(module):
    """
    Runs once before any test in this file executes (AFTER all collection).
    Deferred from module level so sys.modules['database'] is not replaced during
    pytest collection — which would cause test_tenant_isolation.py to get a
    MagicMock instead of the real TenantCollection class.
    """
    global _original_database_module

    _original_database_module = sys.modules.get('database')

    # Replace database module with a MagicMock so the cron script uses mocks
    mock_db = MagicMock()
    mock_database_module = MagicMock()
    mock_database_module.db = mock_db
    sys.modules['database'] = mock_database_module

    # Re-bind the module-level `database` name to the mock so class methods
    # that reference `database.db` see the mock (Python resolves globals at call time).
    module.database = mock_database_module

    # Force fresh import of the cron script with the mocked database
    sys.modules.pop('cron.cron_payment_reminders', None)
    import cron.cron_payment_reminders as cs
    module.cron_script = cs  # exposes to module globals used by test class


def teardown_module(module):
    """Restore database module and create a fresh event loop.

    IsolatedAsyncioTestCase destroys the event loop after its tests run.
    Re-creating it here prevents subsequent test files from raising
    'RuntimeError: There is no current event loop in thread MainThread'.
    """
    if _original_database_module is not None:
        sys.modules['database'] = _original_database_module
    else:
        sys.modules.pop('database', None)

    # IsolatedAsyncioTestCase closed the loop — create a fresh one
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except Exception:
        pass


class TestCronOptimizations(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.db.users.find.reset_mock()
        database.db.email_notification_preferences.find.reset_mock()
        database.db.water_bills.find.reset_mock()
        database.db.agm.find.reset_mock()
        database.db.events.find.reset_mock()
        database.db.units.find.reset_mock()
        database.db.memberships.find.reset_mock()
        database.db.buildings.find.reset_mock()
        database.db.documents.insert_one = AsyncMock()
        database.db.settings.find_one = AsyncMock(
            return_value={"building_name": "Test Building", "reminder_lead_days": 14})
        database.db.memberships.find.return_value.to_list = AsyncMock(return_value=[
            {"user_id": "user1"},
            {"user_id": "user2"},
        ])
        database.db.buildings.find.return_value.to_list = AsyncMock(return_value=[
            {"id": "13195"},
        ])
        # JurisdictionService.get_building_state() (jurisdiction_service.py:50), added to
        # send_levy_reminders() by commit e658cae4 (automated-notice legal paragraph) —
        # returning a state directly here short-circuits before its fallback to the
        # (also-unmocked) buildings.find_one().
        database.db.jurisdiction_config.find_one = AsyncMock(
            return_value={"building_id": "13195", "state": "ACT"})

    async def test_send_levy_reminders_batching(self):
        # Setup mocks
        database.db.events.find.return_value.to_list = AsyncMock(return_value=[
            {"title": "Q1 Levy Due - FY 2026", "start_date": "2026-03-31", "event_type": "levy_due"}
        ])
        database.db.units.find.return_value.to_list = AsyncMock(return_value=[
            {"unit_number": "UA101", "owner_email": "owner1@example.com", "entitlement": 100},
            {"unit_number": "UA102", "owner_email": "owner2@example.com", "entitlement": 120}
        ])
        database.db.users.find.return_value.to_list = AsyncMock(return_value=[
            {"id": "user1", "email": "owner1@example.com", "full_name": "Owner One"},
            {"id": "user2", "email": "owner2@example.com", "full_name": "Owner Two"}
        ])
        database.db.email_notification_preferences.find.return_value.to_list = AsyncMock(return_value=[
            {"user_id": "user1", "levy_reminders_enabled": True},
            {"user_id": "user2", "levy_reminders_enabled": False}
        ])

        with patch('cron.cron_payment_reminders.get_latest_levy_year', AsyncMock(return_value="2026")), \
                patch('utils.finance_helpers.get_levy_rates', AsyncMock(return_value={
                    "admin_annual": 10.0, "sinking_annual": 5.0
                })), \
                patch('cron.cron_payment_reminders.generate_levy_notice_pdf', MagicMock(return_value=b"pdf")), \
                patch('cron.cron_payment_reminders.send_email_async', AsyncMock(return_value={"success": True})):
            await cron_script.send_levy_reminders(14, "13195")

            # Verify batching: users.find should be called once for all owners
            self.assertEqual(database.db.users.find.call_count, 1)
            # Verify batching: preferences.find should be called once with $in
            self.assertEqual(database.db.email_notification_preferences.find.call_count, 1)

    async def test_send_water_reminders_batching(self):
        # Setup mocks
        database.db.water_bills.find.return_value.to_list = AsyncMock(return_value=[
            {"unit_number": "UA101", "due_date": "2026-03-31", "amount": 100.0, "status": "unpaid"},
            {"unit_number": "UA102", "due_date": "2026-03-31", "amount": 150.0, "status": "unpaid"}
        ])
        database.db.users.find.return_value.to_list = AsyncMock(return_value=[
            {"id": "user1", "unit_number": "UA101", "email": "owner1@example.com", "full_name": "Owner One"},
            {"id": "user2", "unit_number": "UA102", "email": "owner2@example.com", "full_name": "Owner Two"}
        ])
        database.db.email_notification_preferences.find.return_value.to_list = AsyncMock(return_value=[
            {"user_id": "user1", "water_reminders_enabled": True},
            {"user_id": "user2", "water_reminders_enabled": True}
        ])

        with patch('cron.cron_payment_reminders.send_email_async', AsyncMock(return_value={"success": True})):
            await cron_script.send_water_reminders(14, "13195")

            # Verify batching
            self.assertEqual(database.db.users.find.call_count, 1)
            self.assertEqual(database.db.email_notification_preferences.find.call_count, 1)

    async def test_send_agm_reminders_batching(self):
        # Setup mocks
        database.db.agm.find.return_value.to_list = AsyncMock(return_value=[
            {"title": "AGM 2026", "date": "2026-03-31"}
        ])
        database.db.users.find.return_value.to_list = AsyncMock(return_value=[
            {"id": "user1", "email": "owner1@example.com", "full_name": "Owner One", "role": "owner",
             "is_approved": True},
            {"id": "user2", "email": "owner2@example.com", "full_name": "Owner Two", "role": "owner",
             "is_approved": True}
        ])
        database.db.email_notification_preferences.find.return_value.to_list = AsyncMock(return_value=[
            {"user_id": "user1", "agm_reminders_enabled": True},
            {"user_id": "user2", "agm_reminders_enabled": True}
        ])

        with patch('cron.cron_payment_reminders.send_email_async', AsyncMock(return_value={"success": True})):
            await cron_script.send_agm_reminders(14, "13195")

            # Verify batching
            self.assertEqual(database.db.users.find.call_count, 1)
            self.assertEqual(database.db.email_notification_preferences.find.call_count, 1)

    async def test_main_processes_each_active_building(self):
        database.db.buildings.find.return_value.to_list = AsyncMock(return_value=[
            {"id": "13195"},
            {"id": "16244"},
        ])

        with patch('cron.cron_payment_reminders.get_reminder_settings', AsyncMock(side_effect=[
            {"reminder_lead_days": 14},
            {"reminder_lead_days": 7},
        ])), \
                patch('cron.cron_payment_reminders.send_levy_reminders', AsyncMock()) as levy_mock, \
                patch('cron.cron_payment_reminders.send_council_tax_reminders', AsyncMock()) as council_mock, \
                patch('cron.cron_payment_reminders.send_water_reminders', AsyncMock()) as water_mock, \
                patch('cron.cron_payment_reminders.send_agm_reminders', AsyncMock()) as agm_mock:
            await cron_script.main()

        levy_mock.assert_any_call(14, "13195")
        levy_mock.assert_any_call(7, "16244")
        self.assertEqual(levy_mock.await_count, 2)
        self.assertEqual(council_mock.await_count, 2)
        self.assertEqual(water_mock.await_count, 2)
        self.assertEqual(agm_mock.await_count, 2)


if __name__ == "__main__":
    unittest.main()
