"""
Tests for Notifications endpoint performance and correctness.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest


def _mock_request(path: str = "/api/notifications/send") -> StarletteRequest:
    scope = {
        "type": "http", "method": "POST", "path": path,
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 12345),
    }
    return StarletteRequest(scope)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_user(role: str) -> dict:
    return {
        "id": "admin-user-id",
        "full_name": "Admin User",
        "email": "admin@example.com",
        "role": role,
        "is_active": True,
        "permissions": {},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestSendNotification:
    """
    Unit tests for the send_notification() handler logic.
    """

    @pytest.mark.asyncio
    async def test_send_notification_to_multiple_users(self):
        # 1. Setup mocks
        from routers.notifications import send_notification
        from models.notification import NotificationCreate

        mock_permissions = MagicMock()
        mock_permissions.can_send_notifications = True
        mock_permissions.can_post_announcements = True

        mock_users = [
            {"id": "user1", "email": "user1@example.com", "phone": "1234567890"},
            {"id": "user2", "email": "user2@example.com", "phone": "0987654321"},
        ]

        mock_db = MagicMock()
        # Mocking find().to_list()
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=mock_users)
        mock_db.users.find.return_value = mock_cursor
        mock_db.notifications.insert_one = AsyncMock()
        mock_db.settings.find_one = AsyncMock(return_value={})
        mock_memberships = MagicMock()
        mock_memberships.to_list = AsyncMock(return_value=[
            {"user_id": "user1"},
            {"user_id": "user2"},
        ])
        mock_db.memberships.find.return_value = mock_memberships

        # 2. Patch dependencies
        with patch("routers.notifications.get_user_permissions", return_value=mock_permissions), \
                patch("routers.notifications.db", mock_db), \
                patch("routers.notifications.RESEND_AVAILABLE", True), \
                patch("routers.notifications.send_email_async", new_callable=AsyncMock) as mock_send_email:
            mock_send_email.return_value = {"success": True}

            # 3. Create request data
            data = NotificationCreate(
                recipients=["user1", "user2"],
                channels=["email"],
                title="Test Title",
                message="Test Message",
                notification_type="general"
            )

            # 4. Execute function
            current_user = _make_user("super_admin")
            response = await send_notification(
                request=_mock_request(),
                data=data,
                current_user=current_user,
                building_id="13195",
            )

            # 5. Verify results
            assert response.status == "sent"
            assert response.sent_count == 2
            assert response.failed_count == 0
            assert mock_send_email.call_count == 2

            # Verify DB insertion
            mock_db.notifications.insert_one.assert_called_once()
            inserted_doc = mock_db.notifications.insert_one.call_args[0][0]
            assert inserted_doc["sent_count"] == 2
            assert inserted_doc["title"] == "Test Title"

    @pytest.mark.asyncio
    async def test_send_notification_unauthorized(self):
        from routers.notifications import send_notification
        from models.notification import NotificationCreate

        mock_permissions = MagicMock()
        mock_permissions.can_send_notifications = False
        mock_permissions.can_post_announcements = False

        with patch("routers.notifications.get_user_permissions", return_value=mock_permissions):
            data = NotificationCreate(
                recipients=["user1"],
                channels=["email"],
                title="Test Title",
                message="Test Message",
                notification_type="general"
            )

            current_user = _make_user("tenant")
            with pytest.raises(HTTPException) as exc_info:
                await send_notification(request=_mock_request(), data=data, current_user=current_user)

            assert exc_info.value.status_code == 403
