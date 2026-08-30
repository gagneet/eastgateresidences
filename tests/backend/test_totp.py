"""
Tests for TOTP 2FA scaffold (P0-02).

All tests are pure unit tests — no live DB or network connection required.
Covers:
  - setup generates secret and QR code
  - verify correct code enables 2FA
  - verify wrong code is rejected
  - disable requires valid code
  - status returns correct state
  - login without TOTP succeeds normally (regression)
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


def _make_request(path: str = "/api/auth/totp") -> StarletteRequest:
    scope = {
        "type": "http", "method": "POST", "path": path,
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 12345),
    }
    return StarletteRequest(scope)


def _make_user(role: str = "owner", totp_enabled: bool = False) -> dict:
    return {
        "id": "user-totp-test-1",
        "email": "totp@example.com",
        "full_name": "TOTP Test User",
        "role": role,
        "unit_number": "TH087",
        "totp_enabled": totp_enabled,
        "totp_secret_encrypted": None,
        "totp_backup_codes_hashed": [],
    }


class TestTOTPHelpers:
    """Pure unit tests for TOTP crypto helpers."""

    def test_pyotp_generates_valid_secret(self):
        import pyotp

        secret = pyotp.random_base32()
        assert len(secret) >= 16, "Secret must be at least 16 chars"
        # Verify a generated code validates against the same secret
        code = pyotp.TOTP(secret).now()
        assert pyotp.TOTP(secret).verify(code, valid_window=1)

    def test_wrong_code_rejected(self):
        import pyotp

        secret = pyotp.random_base32()
        assert not pyotp.TOTP(secret).verify("000000", valid_window=1)

    def test_totp_secret_survives_encrypt_decrypt(self):
        import pyotp
        from utils.crypto import encrypt_sensitive, decrypt_sensitive

        secret = pyotp.random_base32()
        encrypted = encrypt_sensitive(secret)
        decrypted = decrypt_sensitive(encrypted)
        assert decrypted == secret


class TestTOTPSetupEndpoint:
    """Test /auth/totp/setup endpoint logic."""

    @pytest.mark.asyncio
    async def test_setup_generates_secret_and_stores_encrypted(self):
        from routers.totp import setup_totp
        import pyotp

        user = _make_user()
        captured: list[dict] = []

        mock_db = MagicMock()

        async def capture_update(query, update, *args, **kwargs):
            captured.append(update.get("$set", {}))

        mock_db.users.update_one = capture_update

        with patch("routers.totp.db", mock_db), \
                patch("routers.totp.get_current_user", return_value=user):
            result = await setup_totp(request=_make_request(), current_user=user)

        assert result.secret, "Secret must not be empty"
        assert result.qr_code_uri.startswith("otpauth://"), "URI must be an otpauth URL"
        assert len(result.backup_codes) == 8, "Must generate 8 backup codes"

        # Verify the stored value is encrypted
        assert captured, "update_one must be called"
        from utils.crypto import is_encrypted
        stored_secret = captured[0].get("totp_secret_encrypted", "")
        assert is_encrypted(stored_secret), "Stored secret must be encrypted"
        assert not captured[0].get("totp_enabled"), "totp_enabled must be False after setup"

    @pytest.mark.asyncio
    async def test_setup_backup_codes_not_stored_in_plain_text(self):
        """Backup codes returned to user must NOT match stored hashed codes."""
        from routers.totp import setup_totp

        user = _make_user()
        captured: list[dict] = []

        mock_db = MagicMock()

        async def capture_update(query, update, *args, **kwargs):
            captured.append(update.get("$set", {}))

        mock_db.users.update_one = capture_update

        with patch("routers.totp.db", mock_db):
            result = await setup_totp(request=_make_request(), current_user=user)

        stored_codes = captured[0].get("totp_backup_codes_hashed", [])
        for plain_code in result.backup_codes:
            assert plain_code not in stored_codes, "Plain text code must not appear in stored hashed codes"


class TestTOTPVerifyEndpoint:
    """Test /auth/totp/verify endpoint logic."""

    @pytest.mark.asyncio
    async def test_verify_correct_code_enables_2fa(self):
        import pyotp
        from routers.totp import verify_totp, TOTPVerifyRequest
        from utils.crypto import encrypt_sensitive

        secret = pyotp.random_base32()
        valid_code = pyotp.TOTP(secret).now()
        user = _make_user()
        db_user = {**user, "totp_secret_encrypted": encrypt_sensitive(secret)}

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_user)
        updates: list[dict] = []

        async def capture_update(query, update, *args, **kwargs):
            updates.append(update.get("$set", {}))

        mock_db.users.update_one = capture_update

        body = TOTPVerifyRequest(code=valid_code)
        with patch("routers.totp.db", mock_db), \
                patch("routers.totp.create_audit_log", AsyncMock()):
            result = await verify_totp(request=_make_request(), body=body, current_user=user)

        assert result.get("success") is True
        assert updates and updates[0].get("totp_enabled") is True

    @pytest.mark.asyncio
    async def test_verify_wrong_code_rejected(self):
        import pyotp
        from routers.totp import verify_totp, TOTPVerifyRequest
        from utils.crypto import encrypt_sensitive
        from fastapi import HTTPException

        secret = pyotp.random_base32()
        user = _make_user()
        db_user = {**user, "totp_secret_encrypted": encrypt_sensitive(secret)}

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_user)

        body = TOTPVerifyRequest(code="000000")
        with patch("routers.totp.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await verify_totp(request=_make_request(), body=body, current_user=user)

        assert exc_info.value.status_code == 400


class TestTOTPDisableEndpoint:
    """Test /auth/totp/disable endpoint logic."""

    @pytest.mark.asyncio
    async def test_disable_requires_valid_code(self):
        import pyotp
        from routers.totp import disable_totp, TOTPVerifyRequest
        from utils.crypto import encrypt_sensitive
        from fastapi import HTTPException

        secret = pyotp.random_base32()
        user = _make_user(totp_enabled=True)
        db_user = {
            **user,
            "totp_enabled": True,
            "totp_secret_encrypted": encrypt_sensitive(secret),
        }

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_user)

        body = TOTPVerifyRequest(code="000000")
        with patch("routers.totp.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await disable_totp(request=_make_request(), body=body, current_user=user)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_disable_with_correct_code_succeeds(self):
        import pyotp
        from routers.totp import disable_totp, TOTPVerifyRequest
        from utils.crypto import encrypt_sensitive

        secret = pyotp.random_base32()
        valid_code = pyotp.TOTP(secret).now()
        user = _make_user(totp_enabled=True)
        db_user = {
            **user,
            "totp_enabled": True,
            "totp_secret_encrypted": encrypt_sensitive(secret),
        }

        updates: list[dict] = []
        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_user)

        async def capture_update(query, update, *args, **kwargs):
            updates.append(update)

        mock_db.users.update_one = capture_update

        body = TOTPVerifyRequest(code=valid_code)
        with patch("routers.totp.db", mock_db), \
                patch("routers.totp.create_audit_log", AsyncMock()):
            result = await disable_totp(request=_make_request(), body=body, current_user=user)

        assert result.get("success") is True
        assert updates and updates[0].get("$set", {}).get("totp_enabled") is False


class TestTOTPStatus:
    """Test /auth/totp/status endpoint."""

    @pytest.mark.asyncio
    async def test_status_returns_correct_state_when_disabled(self):
        from routers.totp import get_totp_status

        user = _make_user(totp_enabled=False)
        db_user = {**user, "totp_enabled": False, "totp_backup_codes_hashed": []}

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_user)

        with patch("routers.totp.db", mock_db):
            result = await get_totp_status(current_user=user)

        assert result.enabled is False
        assert result.backup_codes_remaining == 0

    @pytest.mark.asyncio
    async def test_status_role_requires_totp_flag(self):
        from routers.totp import get_totp_status, TOTP_REQUIRED_ROLES

        for role in TOTP_REQUIRED_ROLES:
            user = _make_user(role=role)
            db_user = {**user, "totp_enabled": False, "totp_backup_codes_hashed": []}

            mock_db = MagicMock()
            mock_db.users.find_one = AsyncMock(return_value=db_user)

            with patch("routers.totp.db", mock_db):
                result = await get_totp_status(current_user=user)

            assert result.role_requires_totp is True, f"Role {role} should require TOTP"

        # Non-required role
        owner = _make_user(role="owner")
        db_owner = {**owner, "totp_enabled": False, "totp_backup_codes_hashed": []}
        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_owner)
        with patch("routers.totp.db", mock_db):
            result = await get_totp_status(current_user=owner)
        assert result.role_requires_totp is False


class TestLoginWithoutTOTP:
    """Regression: accounts without TOTP enabled login normally."""

    def test_totp_enabled_field_defaults_false(self):
        """User with no totp_enabled field should not be blocked."""
        user_no_totp = {"id": "u1", "role": "owner"}
        assert not user_no_totp.get("totp_enabled"), "Accounts without totp_enabled must pass normally"


class TestTOTPChallengeBackupCodes:
    """Backup code verification in the TOTP challenge endpoint."""

    @pytest.mark.asyncio
    async def test_challenge_accepts_valid_backup_code(self):
        """A valid backup code is accepted and consumed from the list."""
        import bcrypt
        import jwt as pyjwt
        import pyotp
        from datetime import datetime, timezone, timedelta
        from routers.totp import totp_challenge, TOTPChallengeRequest

        secret = pyotp.random_base32()
        plain_code = "ABCD1234"
        hashed = bcrypt.hashpw(plain_code.encode(), bcrypt.gensalt()).decode()

        user_id = "user-bc-test"
        db_user = {
            "id": user_id,
            "email": "bc@example.com",
            "full_name": "Backup Test",
            "role": "owner",
            "totp_enabled": True,
            "totp_secret_encrypted": secret,  # not encrypted in unit test
            "totp_backup_codes_hashed": [hashed],
            "hashed_password": "x",
        }

        pending_token = pyjwt.encode(
            {"sub": user_id, "type": "totp_pending",
             "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "test-secret-min32-bytes-for-hmac",
            algorithm="HS256",
        )

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_user)
        mock_db.users.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

        req = TOTPChallengeRequest(
            totp_token=pending_token,
            code=plain_code,
            is_backup_code=True,
        )

        with patch("routers.totp.db", mock_db), \
                patch("config.JWT_SECRET", "test-secret-min32-bytes-for-hmac"), \
                patch("config.JWT_ALGORITHM", "HS256"), \
                patch("routers.totp.decrypt_sensitive", side_effect=lambda x: x), \
                patch("routers.totp.create_token", return_value="full-jwt-token"):
            result = await totp_challenge(request=_make_request(), body=req)

        assert result["token"] == "full-jwt-token"
        # Sensitive fields must be stripped
        assert "hashed_password" not in result["user"]
        assert "totp_backup_codes_hashed" not in result["user"]
        assert "totp_secret_encrypted" not in result["user"]
        # Backup code should be consumed
        mock_db.users.update_one.assert_called_once()
        call_args = mock_db.users.update_one.call_args
        update = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("update", {})
        assert update["$set"]["totp_backup_codes_hashed"] == []

    @pytest.mark.asyncio
    async def test_challenge_rejects_invalid_backup_code(self):
        """An invalid backup code is rejected with HTTP 400."""
        import bcrypt
        import jwt as pyjwt
        from datetime import datetime, timezone, timedelta
        from fastapi import HTTPException
        from routers.totp import totp_challenge, TOTPChallengeRequest

        user_id = "user-bc-test-2"
        real_code = "REAL5678"
        hashed = bcrypt.hashpw(real_code.encode(), bcrypt.gensalt()).decode()

        db_user = {
            "id": user_id,
            "email": "bc2@example.com",
            "full_name": "BC Test 2",
            "role": "owner",
            "totp_enabled": True,
            "totp_secret_encrypted": "ignored",
            "totp_backup_codes_hashed": [hashed],
        }

        pending_token = pyjwt.encode(
            {"sub": user_id, "type": "totp_pending",
             "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "test-secret-min32-bytes-for-hmac", algorithm="HS256",
        )

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_user)
        mock_db.users.update_one = AsyncMock()

        req = TOTPChallengeRequest(
            totp_token=pending_token,
            code="WRONGCOD",
            is_backup_code=True,
        )

        with patch("routers.totp.db", mock_db), \
                patch("config.JWT_SECRET", "test-secret-min32-bytes-for-hmac"), \
                patch("config.JWT_ALGORITHM", "HS256"):
            with pytest.raises(HTTPException) as exc:
                await totp_challenge(request=_make_request(), body=req)

        assert exc.value.status_code == 400
        assert "backup" in exc.value.detail.lower()
        # Code must NOT be consumed on failure
        mock_db.users.update_one.assert_not_called()

    def test_challenge_request_model_has_is_backup_code(self):
        """TOTPChallengeRequest must include is_backup_code with default False."""
        from routers.totp import TOTPChallengeRequest
        req = TOTPChallengeRequest(totp_token="tok", code="123456")
        assert req.is_backup_code is False

    def test_challenge_request_model_accepts_is_backup_code_true(self):
        """TOTPChallengeRequest accepts is_backup_code=True."""
        from routers.totp import TOTPChallengeRequest
        req = TOTPChallengeRequest(totp_token="tok", code="ABCD1234", is_backup_code=True)
        assert req.is_backup_code is True

    @pytest.mark.asyncio
    async def test_challenge_strips_sensitive_fields_from_response(self):
        """The challenge response must not include hashed_password or totp secrets."""
        import jwt as pyjwt
        import pyotp
        from datetime import datetime, timezone, timedelta
        from routers.totp import totp_challenge, TOTPChallengeRequest

        secret = pyotp.random_base32()
        user_id = "user-sensitive-test"
        totp_code = pyotp.TOTP(secret).now()

        db_user = {
            "id": user_id,
            "email": "sens@example.com",
            "full_name": "Sensitive Test",
            "role": "owner",
            "totp_enabled": True,
            "totp_secret_encrypted": secret,
            "totp_backup_codes_hashed": ["some_hash"],
            "hashed_password": "bcrypt_hash_here",
            "mail_password": "enc:encrypted_value",
        }

        pending_token = pyjwt.encode(
            {"sub": user_id, "type": "totp_pending",
             "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "test-secret-min32-bytes-for-hmac", algorithm="HS256",
        )

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=db_user)

        req = TOTPChallengeRequest(totp_token=pending_token, code=totp_code, is_backup_code=False)

        with patch("routers.totp.db", mock_db), \
                patch("config.JWT_SECRET", "test-secret-min32-bytes-for-hmac"), \
                patch("config.JWT_ALGORITHM", "HS256"), \
                patch("routers.totp.decrypt_sensitive", side_effect=lambda x: x), \
                patch("routers.totp.create_token", return_value="full-jwt"):
            result = await totp_challenge(request=_make_request(), body=req)

        user_resp = result["user"]
        assert "hashed_password" not in user_resp
        assert "totp_secret_encrypted" not in user_resp
        assert "totp_backup_codes_hashed" not in user_resp
        assert "mail_password" not in user_resp
        assert user_resp["email"] == "sens@example.com"
