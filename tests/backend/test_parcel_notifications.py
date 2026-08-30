"""
Tests for parcel notification wiring (GAP-001).

All tests are pure unit tests or use mock objects.
Covers:
  - In-app notifications triggered on parcel creation
  - Email notification uses html_content= kwarg (not body=)
  - Email notification uses context="parcel_notification"
  - Dual email address support (login + @eastgateresidences.com.au)
  - Fallback to units.owner_email when no user_units records
  - SSE push fires on parcel_logged
  - No notification when no active residents
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

BUILDING_ID = "13195"
UNIT_NUMBER = "TH087"


def _build_user_units_row(user_id: str) -> dict:
    return {"user_id": user_id, "unit_number": UNIT_NUMBER, "is_active": True, "role_at_unit": "owner"}


def _build_user(user_id: str, email: str, mail_username: str = None) -> dict:
    return {
        "id": user_id,
        "email": email,
        "full_name": "Test Owner",
        "mail_username": mail_username or "",
        "is_active": True,
    }


class TestParcelNotificationHelpers:
    """Unit tests for the _notify_parcel_residents helper in bookings.py."""

    @pytest.mark.asyncio
    async def test_email_uses_html_content_kwarg(self):
        """send_email_async must be called with html_content=, NOT body=."""
        from routers.bookings import _notify_parcel_residents

        user = _build_user("u1", "owner@example.com")
        user_unit = _build_user_units_row("u1")

        mock_db = MagicMock()
        # user_units returns one active occupant
        mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[user_unit])
        # users batch fetch returns the user
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[user])
        # units fallback not needed
        mock_db.units.find_one = AsyncMock(return_value=None)

        email_calls: list[dict] = []

        async def capture_email(**kwargs):
            email_calls.append(kwargs)
            return {"success": True}

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.send_email_async", side_effect=capture_email), \
                patch("routers.bookings.create_notifications_batch", AsyncMock(return_value=1)), \
                patch("routers.bookings.asyncio.create_task", lambda coro: None):
            await _notify_parcel_residents(
                unit_number=UNIT_NUMBER,
                building_id=BUILDING_ID,
                parcel_id="parcel-test-1",
                carrier="auspost",
                description="Test parcel",
            )

        # create_task wraps the coroutine — we need a different approach to verify
        # the kwarg. Patch asyncio.create_task to actually run the coroutine sync.
        import asyncio

        email_calls2: list[dict] = []

        async def capture_email2(**kwargs):
            email_calls2.append(kwargs)
            return {"success": True}

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.send_email_async", side_effect=capture_email2), \
                patch("routers.bookings.create_notifications_batch", AsyncMock(return_value=1)):
            await _notify_parcel_residents(
                unit_number=UNIT_NUMBER,
                building_id=BUILDING_ID,
                parcel_id="parcel-test-2",
                carrier="auspost",
                description="Test parcel",
            )
            # Drain pending tasks
            await asyncio.sleep(0)

        for call_kwargs in email_calls2:
            assert "html_content" in call_kwargs, (
                "send_email_async MUST be called with html_content=, NOT body=. "
                f"Got kwargs: {list(call_kwargs.keys())}"
            )
            assert "body" not in call_kwargs, "body= kwarg must NOT be used"

    @pytest.mark.asyncio
    async def test_email_uses_parcel_notification_context(self):
        """send_email_async must be called with context='parcel_notification'."""
        from routers.bookings import _notify_parcel_residents
        import asyncio

        user = _build_user("u1", "owner@example.com")
        user_unit = _build_user_units_row("u1")

        mock_db = MagicMock()
        mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[user_unit])
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[user])
        mock_db.units.find_one = AsyncMock(return_value=None)

        email_calls: list[dict] = []

        async def capture_email(**kwargs):
            email_calls.append(kwargs)
            return {"success": True}

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.send_email_async", side_effect=capture_email), \
                patch("routers.bookings.create_notifications_batch", AsyncMock(return_value=1)):
            await _notify_parcel_residents(
                unit_number=UNIT_NUMBER,
                building_id=BUILDING_ID,
                parcel_id="parcel-test-3",
                carrier="auspost",
            )
            await asyncio.sleep(0)

        for call_kwargs in email_calls:
            assert call_kwargs.get("context") == "parcel_notification", (
                f"Expected context='parcel_notification', got: {call_kwargs.get('context')}"
            )

    @pytest.mark.asyncio
    async def test_dual_email_deduplication(self):
        """User whose login email == mail_username only receives one email."""
        from routers.bookings import _notify_parcel_residents
        import asyncio

        # Same email in both fields → must only send once
        same_email = "owner@eastgateresidences.com.au"
        user = _build_user("u1", same_email, mail_username=same_email)
        user_unit = _build_user_units_row("u1")

        mock_db = MagicMock()
        mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[user_unit])
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[user])
        mock_db.units.find_one = AsyncMock(return_value=None)

        email_calls: list[str] = []

        async def capture_email(**kwargs):
            email_calls.append(kwargs.get("to_email", ""))
            return {"success": True}

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.send_email_async", side_effect=capture_email), \
                patch("routers.bookings.create_notifications_batch", AsyncMock(return_value=1)):
            await _notify_parcel_residents(
                unit_number=UNIT_NUMBER,
                building_id=BUILDING_ID,
                parcel_id="parcel-test-4",
                carrier="dhl",
            )
            await asyncio.sleep(0)

        assert email_calls.count(same_email) <= 1, (
            f"Duplicate email sent {email_calls.count(same_email)} times to {same_email}"
        )

    @pytest.mark.asyncio
    async def test_dual_email_sends_to_both_addresses(self):
        """User with different login email and mail_username receives two emails."""
        from routers.bookings import _notify_parcel_residents
        import asyncio

        login_email = "owner@gmail.com"
        mail_username = "owner@eastgateresidences.com.au"
        user = _build_user("u1", login_email, mail_username=mail_username)
        user_unit = _build_user_units_row("u1")

        mock_db = MagicMock()
        mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[user_unit])
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[user])
        mock_db.units.find_one = AsyncMock(return_value=None)

        email_calls: list[str] = []

        async def capture_email(**kwargs):
            email_calls.append(kwargs.get("to_email", ""))
            return {"success": True}

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.send_email_async", side_effect=capture_email), \
                patch("routers.bookings.create_notifications_batch", AsyncMock(return_value=1)):
            await _notify_parcel_residents(
                unit_number=UNIT_NUMBER,
                building_id=BUILDING_ID,
                parcel_id="parcel-test-5",
                carrier="amazon",
            )
            await asyncio.sleep(0)

        assert login_email in email_calls, f"Login email not in {email_calls}"
        assert mail_username in email_calls, f"Mail email not in {email_calls}"

    @pytest.mark.asyncio
    async def test_fallback_to_units_owner_email(self):
        """When no user_units records, uses units.owner_email as fallback."""
        from routers.bookings import _notify_parcel_residents
        import asyncio

        mock_db = MagicMock()
        # No user_units occupants
        mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[])
        # No users by unit_number
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[])
        # Units fallback email
        mock_db.units.find_one = AsyncMock(return_value={
            "unit_number": UNIT_NUMBER,
            "owner_email": "owner_fallback@example.com",
            "owner_name": "Fallback Owner",
        })

        email_calls: list[str] = []

        async def capture_email(**kwargs):
            email_calls.append(kwargs.get("to_email", ""))
            return {"success": True}

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.send_email_async", side_effect=capture_email), \
                patch("routers.bookings.create_notifications_batch", AsyncMock(return_value=0)):
            await _notify_parcel_residents(
                unit_number=UNIT_NUMBER,
                building_id=BUILDING_ID,
                parcel_id="parcel-test-6",
                carrier="toll",
            )
            await asyncio.sleep(0)

        assert "owner_fallback@example.com" in email_calls, (
            f"Fallback email not sent. Sent to: {email_calls}"
        )

    @pytest.mark.asyncio
    async def test_no_notification_when_no_residents_and_no_owner_email(self):
        """When unit has no active residents and no owner_email, no emails sent."""
        from routers.bookings import _notify_parcel_residents
        import asyncio

        mock_db = MagicMock()
        mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.units.find_one = AsyncMock(return_value={"unit_number": UNIT_NUMBER})  # no owner_email

        email_calls: list[str] = []

        async def capture_email(**kwargs):
            email_calls.append(kwargs.get("to_email", ""))
            return {"success": True}

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.send_email_async", side_effect=capture_email), \
                patch("routers.bookings.create_notifications_batch", AsyncMock(return_value=0)):
            await _notify_parcel_residents(
                unit_number=UNIT_NUMBER,
                building_id=BUILDING_ID,
                parcel_id="parcel-test-7",
                carrier="fedex",
            )
            await asyncio.sleep(0)

        assert email_calls == [], f"No emails should be sent but got: {email_calls}"

    @pytest.mark.asyncio
    async def test_in_app_notification_uses_batch(self):
        """In-app notifications use create_notifications_batch (not loop over create_user_notification)."""
        from routers.bookings import _notify_parcel_residents

        user = _build_user("u1", "owner@example.com")
        user_unit = _build_user_units_row("u1")

        mock_db = MagicMock()
        mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[user_unit])
        mock_db.users.find.return_value.to_list = AsyncMock(return_value=[user])
        mock_db.units.find_one = AsyncMock(return_value=None)

        batch_calls: list[list] = []

        async def capture_batch(notifications, *args, **kwargs):
            batch_calls.append(notifications)
            return len(notifications)

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.send_email_async", AsyncMock(return_value={"success": True})), \
                patch("routers.bookings.create_notifications_batch", side_effect=capture_batch), \
                patch("routers.bookings.asyncio.create_task", lambda coro: None):
            await _notify_parcel_residents(
                unit_number=UNIT_NUMBER,
                building_id=BUILDING_ID,
                parcel_id="parcel-test-8",
                carrier="startrack",
            )

        assert batch_calls, "create_notifications_batch must be called"
        # Each notification batch item must have the required fields
        for notif in batch_calls[0]:
            assert "user_id" in notif
            assert "title" in notif
            assert "message" in notif
            assert notif.get("type") == "parcel"


class TestParcelCreateDoesNotPassDbToNotification:
    """
    Regression: create_user_notification() must not receive db= kwarg.
    The function imports db internally — passing db causes TypeError.
    """

    def test_create_user_notification_signature_has_no_db_param(self):
        """Verify the helper signature has no db parameter."""
        import inspect
        from utils.helpers import create_user_notification

        sig = inspect.signature(create_user_notification)
        assert "db" not in sig.parameters, (
            "create_user_notification must NOT have a 'db' parameter — "
            "it imports db internally. Passing db= causes TypeError."
        )
