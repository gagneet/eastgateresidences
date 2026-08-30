"""
Tests for mail password encryption (P0-01).

All tests are pure unit tests — no live MongoDB connection required.
Covers:
  - encrypt/decrypt round-trip
  - is_encrypted detection
  - empty/None passthrough
  - idempotency (already-encrypted value not re-encrypted)
  - mail access endpoint decrypts before responding
  - password write endpoints encrypt before storing
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, 'backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


# ─── Unit tests for crypto.py ────────────────────────────────────────────────

class TestCryptoHelpers:
    def test_encrypt_decrypt_roundtrip(self):
        from utils.crypto import encrypt_sensitive, decrypt_sensitive

        plain = "TestPassword123!"
        encrypted = encrypt_sensitive(plain)
        assert encrypted != plain, "Encrypted value must differ from plain text"
        assert decrypt_sensitive(encrypted) == plain, "Decrypt must recover original"

    def test_is_encrypted_detects_ciphertext(self):
        from utils.crypto import encrypt_sensitive, is_encrypted

        plain = "TestPassword123!"
        assert not is_encrypted(plain), "Plain text should not be detected as encrypted"
        assert is_encrypted(encrypt_sensitive(plain)), "Ciphertext should be detected as encrypted"

    def test_empty_string_passthrough(self):
        from utils.crypto import encrypt_sensitive, decrypt_sensitive, is_encrypted

        assert encrypt_sensitive("") == ""
        assert decrypt_sensitive("") == ""
        assert not is_encrypted("")

    def test_none_passthrough(self):
        from utils.crypto import encrypt_sensitive, decrypt_sensitive, is_encrypted

        assert encrypt_sensitive(None) is None  # type: ignore[arg-type]
        assert decrypt_sensitive(None) is None  # type: ignore[arg-type]
        assert not is_encrypted(None)  # type: ignore[arg-type]

    def test_idempotent_encrypt(self):
        """Calling encrypt_sensitive twice does not double-encrypt."""
        from utils.crypto import encrypt_sensitive, decrypt_sensitive

        plain = "TestPassword123!"
        encrypted_once = encrypt_sensitive(plain)
        # enc: prefix means already encrypted — second call returns unchanged
        encrypted_twice = encrypt_sensitive(encrypted_once)
        assert encrypted_twice == encrypted_once, "Double-encryption must be idempotent"
        # Can still decrypt to original
        assert decrypt_sensitive(encrypted_twice) == plain

    def test_different_plaintexts_produce_different_ciphertexts(self):
        from utils.crypto import encrypt_sensitive

        enc_a = encrypt_sensitive("PasswordA")
        enc_b = encrypt_sensitive("PasswordB")
        assert enc_a != enc_b


# ─── Mail access endpoint ────────────────────────────────────────────────────

def _make_user(role: str = "owner", mail_password: str = "") -> dict:
    return {
        "id": "user-test-1",
        "full_name": "Test Owner",
        "email": "test@example.com",
        "role": role,
        "unit_number": "TH087",
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "mail_username": "test@eastgateresidences.com.au",
        "mail_password": mail_password,
    }


class TestMailAccessEndpointDecryption:
    """Mail access GET endpoint must return decrypted password."""

    @pytest.mark.asyncio
    async def test_returns_decrypted_password(self):
        from utils.crypto import encrypt_sensitive
        from routers.auth import get_mail_access

        plain = "pytest-fixture-password-not-a-credential"
        user = _make_user(mail_password=encrypt_sensitive(plain))

        with patch("routers.auth.get_current_user", return_value=user), \
                patch("routers.auth.db") as mock_db:
            result = await get_mail_access(current_user=user)

        assert result.mail_password == plain, "Endpoint must return plaintext, not ciphertext"
        # Fernet tokens start with "enc:" in our format
        assert not result.mail_password.startswith("enc:"), "Should not expose ciphertext"

    @pytest.mark.asyncio
    async def test_returns_plaintext_unchanged_during_migration(self):
        """Plaintext passwords (pre-migration) are passed through without error."""
        from routers.auth import get_mail_access

        plain = "pytest-fixture-password-not-a-credential"
        user = _make_user(mail_password=plain)

        with patch("routers.auth.get_current_user", return_value=user):
            result = await get_mail_access(current_user=user)

        assert result.mail_password == plain


class TestMailPasswordWriteEncryption:
    """Write endpoints must store encrypted passwords."""

    @pytest.mark.asyncio
    async def test_self_service_update_encrypts_password(self):
        from routers.auth import update_mail_password, MailPasswordUpdate
        from utils.crypto import is_encrypted
        from fastapi import Request

        user = _make_user(mail_password="OldPassword1!")
        data = MailPasswordUpdate(new_password="NewPassword1!")

        captured: list[dict] = []

        mock_db = MagicMock()

        async def capture_update(query, update, *args, **kwargs):
            captured.append(update.get("$set", {}))
            return MagicMock(modified_count=1)

        mock_db.users.update_one = capture_update
        mock_request = MagicMock(spec=Request)

        with patch("routers.auth.db", mock_db):
            await update_mail_password(request=mock_request, data=data, current_user=user)

        assert captured, "update_one must be called"
        stored_pwd = captured[0].get("mail_password", "")
        assert is_encrypted(stored_pwd), "Stored password must be encrypted"
        # And must not store the plain text
        assert stored_pwd != "NewPassword1!"

    @pytest.mark.asyncio
    async def test_admin_update_encrypts_password(self):
        from routers.auth import admin_update_mail_password, AdminMailPasswordUpdate
        from utils.crypto import is_encrypted

        admin = _make_user(role="super_admin", mail_password="AdminPwd1!")
        target = {
            "id": "user-target",
            "email": "target@example.com",
            "full_name": "Target User",
            "mail_username": "target@eastgateresidences.com.au",
        }
        data = AdminMailPasswordUpdate(
            mail_username="target@eastgateresidences.com.au",
            mail_password="NewAdminPwd1!",
        )

        captured: list[dict] = []

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=target)

        async def capture_update(query, update, *args, **kwargs):
            captured.append(update.get("$set", {}))
            return MagicMock(modified_count=1)

        mock_db.users.update_one = capture_update

        with patch("routers.auth.db", mock_db):
            await admin_update_mail_password(data=data, current_user=admin)

        assert captured, "update_one must be called"
        stored_pwd = captured[0].get("mail_password", "")
        assert is_encrypted(stored_pwd), "Admin-stored password must be encrypted"
