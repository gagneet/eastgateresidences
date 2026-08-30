from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from models.notification import NotificationCreate
from server import send_notification


def _make_user(role: str):
    return {
        "id": "admin-user-id",
        "full_name": "Admin User",
        "email": "admin@example.com",
        "role": role,
        "is_active": True,
    }


@pytest.mark.asyncio
async def test_send_notification_optimized_server():
    # Setup mocks
    mock_permissions = MagicMock()
    mock_permissions.can_send_notifications = True
    mock_permissions.can_post_announcements = True

    mock_users = [
        {"id": "user1", "email": "user1@example.com", "phone": "1234567890"},
        {"id": "user2", "email": "user2@example.com", "phone": "0987654321"},
    ]

    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=mock_users)
    mock_db.users.find.return_value = mock_cursor
    mock_memberships_cursor = MagicMock()
    mock_memberships_cursor.to_list = AsyncMock(return_value=[{"user_id": "user1"}, {"user_id": "user2"}])
    mock_db.memberships.find.return_value = mock_memberships_cursor
    mock_db.notifications.insert_one = AsyncMock()
    # send_notification now resolves the building name from settings for the
    # email copy instead of hardcoding one building's name.
    mock_db.settings.find_one = AsyncMock(return_value={"building_name": "Test Building"})

    # Patch dependencies in server.py
    with patch("server.get_user_permissions", return_value=mock_permissions), patch("server.db", mock_db), patch(
            "server.RESEND_AVAILABLE", True), patch("server.send_email_async",
                                                    new_callable=AsyncMock) as mock_send_email:
        mock_send_email.return_value = {"success": True}

        # Create request data
        data = NotificationCreate(
            recipients=["user1", "user2"],
            channels=["email"],
            title="Test Title",
            message="Test Message",
            notification_type="general"
        )

        # Execute function
        current_user = _make_user("super_admin")
        response = await send_notification(data=data, current_user=current_user, building_id="bldg-1")

        # Verify results
        assert response.status == "sent"
        assert response.sent_count == 2
        assert response.failed_count == 0
        assert mock_send_email.call_count == 2

        # Verify call arguments to send_email_async
        args, kwargs = mock_send_email.call_args_list[0]
        assert kwargs["to_email"] == "user1@example.com"
        assert "Test Title" in kwargs["subject"]
        assert "Test Message" in kwargs["html_content"]

        # Verify DB insertion
        mock_db.notifications.insert_one.assert_called_once()
        inserted_doc = mock_db.notifications.insert_one.call_args[0][0]
        assert inserted_doc["sent_count"] == 2
        assert inserted_doc["status"] == "sent"


@pytest.mark.asyncio
async def test_send_notification_partial_failure():
    # Setup mocks
    mock_permissions = MagicMock()
    mock_permissions.can_send_notifications = True
    mock_permissions.can_post_announcements = True

    mock_users = [
        {"id": "user1", "email": "user1@example.com", "phone": "1234567890"},
        {"id": "user2", "email": "user2@example.com", "phone": "0987654321"},
    ]

    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=mock_users)
    mock_db.users.find.return_value = mock_cursor
    mock_memberships_cursor = MagicMock()
    mock_memberships_cursor.to_list = AsyncMock(return_value=[{"user_id": "user1"}, {"user_id": "user2"}])
    mock_db.memberships.find.return_value = mock_memberships_cursor
    mock_db.notifications.insert_one = AsyncMock()
    # send_notification now resolves the building name from settings for the
    # email copy instead of hardcoding one building's name.
    mock_db.settings.find_one = AsyncMock(return_value={"building_name": "Test Building"})

    # Patch dependencies
    with patch("server.get_user_permissions", return_value=mock_permissions), patch("server.db", mock_db), patch(
            "server.RESEND_AVAILABLE", True), patch("server.send_email_async",
                                                    new_callable=AsyncMock) as mock_send_email:
        # Mock first success, second failure
        mock_send_email.side_effect = [{"success": True}, {"success": False, "error": "SMTP error"}]

        data = NotificationCreate(
            recipients=["user1", "user2"],
            channels=["email"],
            title="Test Title",
            message="Test Message",
            notification_type="general"
        )

        current_user = _make_user("super_admin")
        response = await send_notification(data=data, current_user=current_user, building_id="bldg-1")

        assert response.sent_count == 1
        assert response.failed_count == 1
        assert response.status == "partial_success"


@pytest.mark.asyncio
async def test_send_notification_xss_protection():
    # Setup mocks
    mock_permissions = MagicMock()
    mock_permissions.can_send_notifications = True

    mock_users = [{"id": "user1", "email": "user1@example.com"}]
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=mock_users)
    mock_db.users.find.return_value = mock_cursor
    mock_memberships_cursor = MagicMock()
    mock_memberships_cursor.to_list = AsyncMock(return_value=[{"user_id": "user1"}])
    mock_db.memberships.find.return_value = mock_memberships_cursor
    mock_db.notifications.insert_one = AsyncMock()
    # send_notification now resolves the building name from settings for the
    # email copy instead of hardcoding one building's name.
    mock_db.settings.find_one = AsyncMock(return_value={"building_name": "Test Building"})

    with patch("server.get_user_permissions", return_value=mock_permissions), patch("server.db", mock_db), patch(
            "server.RESEND_AVAILABLE", True), patch("server.send_email_async",
                                                    new_callable=AsyncMock) as mock_send_email:
        mock_send_email.return_value = {"success": True}

        # Malicious payload
        malicious_title = "<script>alert('XSS')</script>"
        malicious_message = "Hello <img src=x onerror=alert(1)>"

        data = NotificationCreate(
            recipients=["user1"],
            channels=["email"],
            title=malicious_title,
            message=malicious_message,
            notification_type="general"
        )

        current_user = _make_user("super_admin")
        await send_notification(data=data, current_user=current_user, building_id="bldg-1")

        # Verify that HTML content in send_email_async call is escaped
        args, kwargs = mock_send_email.call_args
        html_content = kwargs["html_content"]

        # Verify escaped title (html_lib.escape also escapes single quotes)
        assert "&lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;" in html_content
        assert "<script>" not in html_content

        # Verify escaped message
        assert "Hello &lt;img src=x onerror=alert(1)&gt;" in html_content
        assert "<img" not in html_content


@pytest.mark.asyncio
async def test_send_notification_non_email_channels():
    # Setup mocks
    mock_permissions = MagicMock()
    mock_permissions.can_send_notifications = True

    mock_users = [{"id": "user1", "email": "user1@example.com", "phone": "1234567890"}]
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=mock_users)
    mock_db.users.find.return_value = mock_cursor
    mock_memberships_cursor = MagicMock()
    mock_memberships_cursor.to_list = AsyncMock(return_value=[{"user_id": "user1"}])
    mock_db.memberships.find.return_value = mock_memberships_cursor
    mock_db.notifications.insert_one = AsyncMock()
    # send_notification now resolves the building name from settings for the
    # email copy instead of hardcoding one building's name.
    mock_db.settings.find_one = AsyncMock(return_value={"building_name": "Test Building"})

    with patch("server.get_user_permissions", return_value=mock_permissions), patch("server.db", mock_db):
        # SMS and WhatsApp only
        data = NotificationCreate(
            recipients=["user1"],
            channels=["sms", "whatsapp"],
            title="Test SMS",
            message="SMS Content",
            notification_type="general"
        )

        current_user = _make_user("super_admin")
        response = await send_notification(data=data, current_user=current_user, building_id="bldg-1")

        # Counters should be correctly updated for non-email channels
        assert response.sent_count == 1
        assert response.failed_count == 0
        assert response.status == "sent"
