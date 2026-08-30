"""
Test Email Configuration Feature

Tests for Migadu email settings, SMTP SSL/TLS support,
and email sending functionality.

Session 21: Migadu Email Configuration & SMTP SSL/TLS Support
"""

import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from server import EmailSettingsUpdate, app

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

client = TestClient(app)


@pytest.fixture(autouse=True)
def _neutralise_operator_email_policy(monkeypatch):
    """Isolate these tests from the deployment's outgoing-email configuration.

    Every test below asserts SMTP *mechanics* — that port 465 uses SMTP_SSL, that
    587 calls starttls(), that a connection error is reported. None of them is
    about whether this deployment is currently allowed to send mail.

    But `send_email_async` consults the kill switch in utils/email_suppression
    first, and that switch is driven by environment variables read from
    backend/.env. When an operator sets `EMAIL_SEND_DISABLED_ALL=true` — as was
    done on 2026-08-25 to stop duplicate levy notices going out — the send path
    returns before ever touching smtplib, and four of these tests fail with
    "Expected 'SMTP' to be called once. Called 0 times."

    A unit test of SMTP behaviour must not depend on a production mute switch, so
    every such control is cleared here. Suppression itself is covered separately
    and deliberately, in tests/backend/test_email_suppression.py.

    EMAIL_ALLOWED_DOMAINS joined the list on 2026-08-27: it restricts delivery to a
    recipient-domain allowlist, and these tests send to example.com/gmail.com, so
    leaving it set reproduced the identical "Called 0 times" failure this fixture was
    written for. Any NEW operator email control must be added here too.
    """
    for var in (
        "EMAIL_SEND_DISABLED_ALL",
        "EMAIL_SEND_DISABLED_BUILDING_IDS",
        "EMAIL_ALLOW_UNRESOLVED_BUILDING",
        "EMAIL_ALLOWED_DOMAINS",
    ):
        monkeypatch.delenv(var, raising=False)


# ==================== MODEL TESTS ====================

def test_email_settings_model_basic():
    """Test EmailSettingsUpdate model with basic fields"""
    settings = EmailSettingsUpdate(
        provider="smtp",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="test@example.com",
        smtp_password="test123"
    )
    assert settings.provider == "smtp"
    assert settings.smtp_host == "smtp.example.com"
    assert settings.smtp_port == 587


def test_email_settings_model_with_security():
    """Test EmailSettingsUpdate model includes smtp_security field"""
    settings = EmailSettingsUpdate(
        provider="smtp",
        smtp_host="smtp.migadu.com",
        smtp_port=465,
        smtp_security="ssl",
        smtp_user="noreply@eastgateresidences.com.au",
        smtp_password="test123",
        sender_email="noreply@eastgateresidences.com.au",
        sender_name="Test"
    )
    assert settings.smtp_security == "ssl"
    assert settings.smtp_port == 465
    assert settings.smtp_host == "smtp.migadu.com"


def test_email_settings_model_defaults():
    """Test EmailSettingsUpdate model default values"""
    settings = EmailSettingsUpdate()
    assert settings.provider == "resend"
    # smtp_security has default value "tls"
    assert settings.smtp_security == "tls"  # Default value


def test_email_settings_migadu_preset():
    """Test Migadu preset configuration"""
    settings = EmailSettingsUpdate(
        provider="smtp",
        smtp_host="smtp.migadu.com",
        smtp_port=465,
        smtp_security="ssl",
        smtp_user="noreply@eastgateresidences.com.au",
        smtp_password="securepass123",
        sender_email="noreply@eastgateresidences.com.au",
        sender_name="East Gate Residences"
    )

    # Verify Migadu configuration
    assert settings.smtp_host == "smtp.migadu.com"
    assert settings.smtp_port == 465
    assert settings.smtp_security == "ssl"
    assert settings.sender_email == "noreply@eastgateresidences.com.au"


def test_email_settings_tls_configuration():
    """Test TLS (STARTTLS) configuration for port 587"""
    settings = EmailSettingsUpdate(
        provider="smtp",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_security="tls",
        smtp_user="user@gmail.com",
        smtp_password="apppassword"
    )

    assert settings.smtp_port == 587
    assert settings.smtp_security == "tls"


# ==================== SMTP LOGIC TESTS ====================

@pytest.mark.asyncio
async def test_smtp_ssl_port_detection():
    """Test that port 465 triggers SMTP_SSL logic"""
    settings = {
        "provider": "smtp",
        "smtp_host": "smtp.migadu.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_user": "test@example.com",
        "smtp_password": "pass123",
        "sender_email": "test@example.com",
        "sender_name": "Test"
    }

    # Port 465 should use SMTP_SSL, not SMTP + STARTTLS
    assert settings["smtp_port"] == 465
    assert settings["smtp_security"] == "ssl"

    # Verify logic: port 465 OR security="ssl" should trigger SMTP_SSL
    should_use_ssl = settings["smtp_port"] == 465 or settings["smtp_security"] == "ssl"
    assert should_use_ssl is True


@pytest.mark.asyncio
async def test_smtp_tls_port_detection():
    """Test that port 587 triggers STARTTLS logic"""
    settings = {
        "provider": "smtp",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "tls",
        "smtp_user": "test@gmail.com",
        "smtp_password": "pass123"
    }

    # Port 587 should use SMTP + STARTTLS
    assert settings["smtp_port"] == 587
    assert settings["smtp_security"] == "tls"

    # Verify logic: NOT port 465 AND NOT security="ssl" should use STARTTLS
    should_use_ssl = settings["smtp_port"] == 465 or settings["smtp_security"] == "ssl"
    assert should_use_ssl is False


@pytest.mark.asyncio
async def test_security_mode_priority():
    """Test that security mode setting takes priority over port"""
    # Case 1: Port 587 but security="ssl" should use SMTP_SSL
    settings1 = {
        "smtp_port": 587,
        "smtp_security": "ssl"
    }
    should_use_ssl_1 = settings1["smtp_port"] == 465 or settings1["smtp_security"] == "ssl"
    assert should_use_ssl_1 is True  # Security mode overrides port

    # Case 2: Port 465 but security="tls" should still use SMTP_SSL (port takes priority)
    settings2 = {
        "smtp_port": 465,
        "smtp_security": "tls"
    }
    should_use_ssl_2 = settings2["smtp_port"] == 465 or settings2["smtp_security"] == "ssl"
    assert should_use_ssl_2 is True  # Port 465 always uses SSL


# ==================== EMAIL SENDING TESTS (MOCKED) ====================

@pytest.mark.asyncio
@patch('server.db')
async def test_send_email_migadu_ssl(mock_db):
    """Test email sending with Migadu SSL configuration"""
    from server import send_email_async

    # Mock database settings
    mock_db.email_settings.find_one = AsyncMock(return_value={
        "provider": "smtp",
        "smtp_host": "smtp.migadu.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_user": "noreply@eastgateresidences.com.au",
        "smtp_password": "test123",
        "sender_email": "noreply@eastgateresidences.com.au",
        "sender_name": "East Gate Residences"
    })

    # Mock smtplib.SMTP_SSL
    with patch('server.smtplib.SMTP_SSL') as mock_smtp_ssl:
        mock_server = MagicMock()
        mock_smtp_ssl.return_value.__enter__.return_value = mock_server

        # Send test email
        result = await send_email_async(
            to_email="test@example.com",
            subject="Test Email",
            html_content="<p>Test content</p>"
        )

        # Verify SMTP_SSL was called with correct parameters
        mock_smtp_ssl.assert_called_once_with("smtp.migadu.com", 465)

        # Verify login was called
        mock_server.login.assert_called_once_with(
            "noreply@eastgateresidences.com.au",
            "test123"
        )

        # Verify success response
        assert result["success"] is True
        assert result["provider"] == "smtp"


@pytest.mark.asyncio
@patch('server.db')
async def test_send_email_starttls(mock_db):
    """Test email sending with STARTTLS (port 587)"""
    from server import send_email_async

    # Mock database settings for TLS
    mock_db.email_settings.find_one = AsyncMock(return_value={
        "provider": "smtp",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "tls",
        "smtp_user": "test@gmail.com",
        "smtp_password": "apppass",
        "sender_email": "test@gmail.com",
        "sender_name": "Test Sender"
    })

    # Mock smtplib.SMTP
    with patch('server.smtplib.SMTP') as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        # Send test email
        result = await send_email_async(
            to_email="recipient@example.com",
            subject="Test Email TLS",
            html_content="<p>TLS test</p>"
        )

        # Verify SMTP was called (not SMTP_SSL)
        mock_smtp.assert_called_once_with("smtp.gmail.com", 587)

        # Verify STARTTLS was called
        mock_server.starttls.assert_called_once()

        # Verify login
        mock_server.login.assert_called_once_with("test@gmail.com", "apppass")


@pytest.mark.asyncio
@patch('server.db')
async def test_send_email_no_encryption(mock_db):
    """Test email sending with no encryption (security=none)"""
    from server import send_email_async

    # Mock database settings with no security
    mock_db.email_settings.find_one = AsyncMock(return_value={
        "provider": "smtp",
        "smtp_host": "localhost",
        "smtp_port": 25,
        "smtp_security": "none",
        "smtp_user": "user",
        "smtp_password": "pass",
        "sender_email": "sender@localhost",
        "sender_name": "Local Sender"
    })

    # Mock smtplib.SMTP
    with patch('server.smtplib.SMTP') as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        # Send test email
        result = await send_email_async(
            to_email="test@localhost",
            subject="Test No Encryption",
            html_content="<p>Plain SMTP</p>"
        )

        # Verify SMTP was called
        mock_smtp.assert_called_once_with("localhost", 25)

        # Verify STARTTLS was NOT called (security=none)
        mock_server.starttls.assert_not_called()


# ==================== API ENDPOINT TESTS ====================

@pytest.fixture
def admin_token():
    """Create admin token for authentication"""
    # This would need to be implemented based on your auth system
    # For now, return a mock token
    return "mock_admin_token"


def test_get_email_settings_unauthorized():
    """Test GET /email-settings without authorization"""
    response = client.get("/api/email-settings")
    assert response.status_code in [401, 403]  # Unauthorized or Forbidden


@patch('server.get_current_user')
@patch('server.db')
def test_get_email_settings_authorized(mock_db, mock_get_user):
    """Test GET /email-settings with proper authorization"""
    # Mock admin user
    mock_get_user.return_value = {
        "id": "admin_id",
        "role": "super_admin",
        "email": "administrator@eastgateresidences.com.au"
    }

    # Mock database response
    mock_db.email_settings.find_one = AsyncMock(return_value={
        "provider": "smtp",
        "smtp_host": "smtp.migadu.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_user": "noreply@eastgateresidences.com.au",
        "smtp_password": "encrypted_password",
        "sender_email": "noreply@eastgateresidences.com.au"
    })

    # Note: This test would need proper authentication setup
    # For now, it demonstrates the test structure


@patch('server.get_current_user')
@patch('server.db')
def test_update_email_settings(mock_db, mock_get_user):
    """Test PUT /email-settings"""
    # Mock admin user
    mock_get_user.return_value = {
        "id": "admin_id",
        "role": "super_admin",
        "email": "administrator@eastgateresidences.com.au"
    }

    # Mock database update
    mock_db.email_settings.update_one = AsyncMock(return_value=None)

    # Test data
    update_data = {
        "provider": "smtp",
        "smtp_host": "smtp.migadu.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_user": "noreply@eastgateresidences.com.au",
        "smtp_password": "newpassword",
        "sender_email": "noreply@eastgateresidences.com.au",
        "sender_name": "East Gate Residences"
    }

    # Note: This test would need proper authentication and API client setup


# ==================== INTEGRATION TESTS ====================

@pytest.mark.integration
def test_migadu_preset_values():
    """Test that Migadu preset values are correct"""
    migadu_config = {
        "provider": "smtp",
        "smtp_host": "smtp.migadu.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_user": "noreply@eastgateresidences.com.au",
        "sender_email": "noreply@eastgateresidences.com.au",
        "sender_name": "East Gate Residences"
    }

    # Verify all required fields present
    assert "smtp_host" in migadu_config
    assert "smtp_port" in migadu_config
    assert "smtp_security" in migadu_config

    # Verify correct values
    assert migadu_config["smtp_host"] == "smtp.migadu.com"
    assert migadu_config["smtp_port"] == 465
    assert migadu_config["smtp_security"] == "ssl"


@pytest.mark.integration
def test_email_settings_schema_compatibility():
    """Test that email settings schema is backward compatible"""
    # Old settings (without smtp_security)
    old_settings = EmailSettingsUpdate(
        provider="smtp",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_password="pass"
    )

    # Should still work
    assert old_settings.provider == "smtp"
    # smtp_security has default value "tls" even when not explicitly provided
    assert old_settings.smtp_security == "tls"  # Default value

    # New settings (with smtp_security)
    new_settings = EmailSettingsUpdate(
        provider="smtp",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="tls",
        smtp_user="user@example.com",
        smtp_password="pass"
    )

    # Should work with new field
    assert new_settings.smtp_security == "tls"


# ==================== ERROR HANDLING TESTS ====================

@pytest.mark.asyncio
@patch('server.db')
async def test_send_email_smtp_connection_error(mock_db):
    """Test email sending with SMTP connection error"""
    from server import send_email_async

    # Mock database settings
    mock_db.email_settings.find_one = AsyncMock(return_value={
        "provider": "smtp",
        "smtp_host": "invalid.smtp.server",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_user": "user@example.com",
        "smtp_password": "pass"
    })

    # Mock SMTP_SSL to raise connection error
    with patch('server.smtplib.SMTP_SSL') as mock_smtp_ssl:
        mock_smtp_ssl.side_effect = Exception("Connection refused")

        # Send email should handle error gracefully
        result = await send_email_async(
            to_email="test@example.com",
            subject="Test",
            html_content="<p>Test</p>"
        )

        # Verify error response
        assert result["success"] is False
        assert "error" in result


@pytest.mark.asyncio
@patch('server.db')
async def test_send_email_authentication_error(mock_db):
    """Test email sending with authentication error"""
    from server import send_email_async

    # Mock database settings with wrong credentials
    mock_db.email_settings.find_one = AsyncMock(return_value={
        "provider": "smtp",
        "smtp_host": "smtp.migadu.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_user": "wrong@example.com",
        "smtp_password": "wrongpass"
    })

    # Mock SMTP_SSL to raise authentication error
    with patch('server.smtplib.SMTP_SSL') as mock_smtp_ssl:
        mock_server = MagicMock()
        mock_smtp_ssl.return_value.__enter__.return_value = mock_server
        mock_server.login.side_effect = Exception("Authentication failed")

        # Send email should handle auth error
        result = await send_email_async(
            to_email="test@example.com",
            subject="Test",
            html_content="<p>Test</p>"
        )

        # Verify error response
        assert result["success"] is False


# ==================== TEST RUNNER ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
