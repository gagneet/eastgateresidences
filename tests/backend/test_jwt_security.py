"""
JWT Security Tests — HS256-specific attack hardening.

Coverage:
  1. alg:none attack — token with alg=none must be rejected
  2. Algorithm substitution — only HS256 is accepted
  3. Tampered payload — role/building_id escalation via payload edit is rejected
  4. Expired token — exp in the past raises ExpiredSignatureError
  5. Empty/garbage signature — incomplete tokens rejected
  6. Token lifetime — expiry does not exceed configured hours
  7. Guest token cap — guest tokens capped at GUEST_JWT_MAX_DAYS
  8. Sensitive data not in payload — no password/secret/connection string in claims
  9. create_token contract — password is never a parameter

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_jwt_security.py -v
"""
from __future__ import annotations

import base64
import inspect
import json
import sys
import time
from pathlib import Path

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS
from utils.auth import create_token


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _b64url(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _craft_token(header: dict, payload: dict, signature: str = "") -> str:
    return f"{_b64url(header)}.{_b64url(payload)}.{signature}"


def _demo_payload(role: str = "owner", building_id: str = "UP-DEMO-001") -> dict:
    now = int(time.time())
    return {
        "user_id": "test-security-user",
        "email": "manager@acmestrata.demo",
        "role": role,
        "building_id": building_id,
        "exp": now + 3600,
        "iat": now,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. alg:none attack
# ─────────────────────────────────────────────────────────────────────────────

class TestAlgNoneAttack:
    """A token claiming alg=none must never be accepted by PyJWT's HS256 decoder."""

    def test_alg_none_lowercase_rejected(self):
        token = _craft_token({"alg": "none", "typ": "JWT"}, _demo_payload())
        with pytest.raises((jwt.InvalidAlgorithmError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_alg_none_uppercase_rejected(self):
        token = _craft_token({"alg": "NONE", "typ": "JWT"}, _demo_payload())
        with pytest.raises((jwt.InvalidAlgorithmError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_alg_empty_string_rejected(self):
        token = _craft_token({"alg": "", "typ": "JWT"}, _demo_payload())
        with pytest.raises((jwt.InvalidAlgorithmError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_rs256_header_rejected_when_only_hs256_allowed(self):
        """RS256→HS256 algorithm confusion: RS256 header must not be accepted."""
        token = _craft_token({"alg": "RS256", "typ": "JWT"}, _demo_payload(), "fakesig")
        with pytest.raises((jwt.InvalidAlgorithmError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_hs512_algorithm_rejected(self):
        """Algorithms other than the configured one must be rejected."""
        token = _craft_token({"alg": "HS512", "typ": "JWT"}, _demo_payload(), "fakesig")
        with pytest.raises((jwt.InvalidAlgorithmError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tampered payload — signature integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestTamperedPayload:
    """Modifying a legitimate token's payload without the secret must be rejected."""

    def _valid_token(self, role: str = "owner") -> str:
        return create_token(
            user_id="test-security-user",
            email="manager@acmestrata.demo",
            role=role,
            building_id="UP-DEMO-001",
        )

    def _swap_payload(self, token: str, new_payload: dict) -> str:
        parts = token.split(".")
        return f"{parts[0]}.{_b64url(new_payload)}.{parts[2]}"

    def test_role_escalation_to_super_admin_rejected(self):
        token = self._valid_token("strata_manager")
        original_payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        original_payload["role"] = "super_admin"
        tampered = self._swap_payload(token, original_payload)

        with pytest.raises((jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(tampered, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_building_id_changed_to_production_rejected(self):
        """Changing building_id to the production building without re-signing is rejected."""
        token = self._valid_token("owner")
        original_payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        original_payload["building_id"] = "13195"  # production
        tampered = self._swap_payload(token, original_payload)

        with pytest.raises((jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(tampered, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_super_admin_flag_injected_rejected(self):
        token = self._valid_token("owner")
        original_payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        original_payload["is_super_admin"] = True
        original_payload["bypass_rls"] = True
        tampered = self._swap_payload(token, original_payload)

        with pytest.raises((jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(tampered, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_garbage_signature_rejected(self):
        token = self._valid_token("owner")
        parts = token.split(".")
        garbage_token = f"{parts[0]}.{parts[1]}.YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo"
        with pytest.raises((jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(garbage_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_empty_signature_rejected(self):
        token = self._valid_token("owner")
        parts = token.split(".")
        empty_sig_token = f"{parts[0]}.{parts[1]}."
        with pytest.raises((jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(empty_sig_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_completely_different_secret_rejected(self):
        """Token signed with a different secret must be rejected."""
        payload = _demo_payload()
        token_with_wrong_secret = jwt.encode(payload, "wrong-secret-key", algorithm=JWT_ALGORITHM)
        with pytest.raises((jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError)):
            jwt.decode(token_with_wrong_secret, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ─────────────────────────────────────────────────────────────────────────────
# 3. Expired tokens
# ─────────────────────────────────────────────────────────────────────────────

class TestExpiredToken:
    """Tokens with exp in the past must raise ExpiredSignatureError."""

    def _expired_token(self, seconds_ago: int = 3600, role: str = "owner") -> str:
        now = int(time.time())
        payload = {
            "user_id": "test-security-user",
            "email": "manager@acmestrata.demo",
            "role": role,
            "exp": now - seconds_ago,
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    def test_token_expired_1_hour_ago(self):
        token = self._expired_token(seconds_ago=3600)
        with pytest.raises(jwt.ExpiredSignatureError):
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_token_expired_30_days_ago_super_admin(self):
        """Even a super_admin token must be rejected if expired."""
        token = self._expired_token(seconds_ago=86400 * 30, role="super_admin")
        with pytest.raises(jwt.ExpiredSignatureError):
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    def test_token_expired_by_1_second(self):
        """Token expired by even 1 second must be rejected."""
        token = self._expired_token(seconds_ago=1)
        with pytest.raises(jwt.ExpiredSignatureError):
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ─────────────────────────────────────────────────────────────────────────────
# 4. Token lifetime
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenLifetime:
    """Tokens issued by create_token must not exceed JWT_EXPIRATION_HOURS."""

    def test_regular_token_lifetime_within_configured_hours(self):
        token = create_token(
            user_id="test-user",
            email="manager@acmestrata.demo",
            role="strata_manager",
        )
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        now = int(time.time())
        lifetime_hours = (payload["exp"] - now) / 3600

        assert 0 < lifetime_hours <= JWT_EXPIRATION_HOURS, (
            f"Token lifetime {lifetime_hours:.2f}h exceeds max {JWT_EXPIRATION_HOURS}h"
        )

    def test_guest_token_does_not_exceed_364_days(self):
        """Guest tokens are hard-capped at 364 days regardless of JWT_EXPIRATION_HOURS."""
        from utils.auth import GUEST_JWT_MAX_DAYS
        token = create_token(
            user_id="guest-test",
            email="guest@acmedemo.au",
            role="guest",
        )
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        now = int(time.time())
        lifetime_days = (payload["exp"] - now) / 86400
        max_allowed = min(JWT_EXPIRATION_HOURS / 24, GUEST_JWT_MAX_DAYS)

        assert lifetime_days <= max_allowed + 0.1, (
            f"Guest token expires in {lifetime_days:.1f}d, max is {max_allowed:.1f}d"
        )

    def test_guest_token_with_past_end_date_expires_immediately(self):
        """Guest token with past end_date must be immediately invalid."""
        from datetime import datetime, timezone, timedelta
        past_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        token = create_token(
            user_id="guest-test",
            email="guest@acmedemo.au",
            role="guest",
            end_date=past_date,
        )
        # Decode without exp verification — we want to inspect the exp value,
        # not re-verify it (the token will already be expired).
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        now = int(time.time())
        assert payload["exp"] <= now + 5, (
            "Guest token with past end_date must expire at or before now"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Payload sensitive-data audit
# ─────────────────────────────────────────────────────────────────────────────

class TestPayloadSensitiveData:
    """JWT payload must never contain secrets, passwords, or connection strings."""

    BANNED_SUBSTRINGS = [
        "password", "secret", "jwt_secret", "mongo_url", "database_url",
        "mongodb://", "postgresql://", "postgres://", "private_key", "api_key",
        "smtp_pass", "fernet", "encryption_key",
    ]

    def test_standard_token_has_no_sensitive_fields(self):
        token = create_token(
            user_id="test-user",
            email="manager@acmestrata.demo",
            role="strata_manager",
            building_id="UP-DEMO-001",
        )
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        payload_str = json.dumps(payload).lower()

        for banned in self.BANNED_SUBSTRINGS:
            assert banned not in payload_str, (
                f"JWT payload contains forbidden string '{banned}'"
            )

    def test_token_contains_required_claims(self):
        token = create_token(
            user_id="test-user",
            email="manager@acmestrata.demo",
            role="owner",
        )
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert "user_id" in payload, "Token must contain user_id"
        assert "email" in payload, "Token must contain email"
        assert "role" in payload, "Token must contain role"
        assert "exp" in payload, "Token must contain exp"

    def test_create_token_does_not_accept_password_parameter(self):
        """create_token must never have a password parameter — it would risk embedding it."""
        sig = inspect.signature(create_token)
        assert "password" not in sig.parameters, (
            "create_token must not accept a 'password' parameter"
        )

    def test_impersonator_id_in_token_does_not_leak_admin_details(self):
        """When impersonator_id is set, it is just an ID — not the admin's email/role."""
        token = create_token(
            user_id="owner-1",
            email="owner@acmedemo.au",
            role="owner",
            impersonator_id="admin-secret-id",
        )
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        payload_str = json.dumps(payload)

        # impersonator_id should be present but must not contain sensitive admin details
        assert "impersonator_id" in payload
        # No admin email embedded in the token
        assert "administrator@" not in payload_str
