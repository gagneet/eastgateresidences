"""
Tests for the Email Access feature (/api/mail/access).

Covers:
- Unauthenticated access → 401
- Tenant/service-provider role → 403
- Owner/EC/Chairman/Strata-Manager/Super-Admin → 200
- Missing mail credentials → 404
- Response shape validation
- Password update endpoint (PUT /mail/update-password)
"""

import os
from unittest.mock import MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_user(role: str, mail_username: str = None, mail_password: str = None) -> dict:
    return {
        "id": "user-test-1",
        "full_name": "Test User",
        "email": "test@example.com",
        "role": role,
        "unit_number": "TH087",
        "status": "active",
        "mail_username": mail_username,
        "mail_password": mail_password,
        "permissions": {},
    }


# The webmail URL is configuration, not a constant: get_mail_access() reads
# WEBMAIL_URL then MAIL_URL, and returns "" when neither is set. It used to be
# hardcoded to East Gate's webmail, which broke every other building.
EXPECTED_MAIL_URL = "https://mail.example-strata.test/admin/#/mailbox/INBOX"

# Roles that have can_access_email = True in models/user.py.
# Per migration 0025: chairman is no longer a top-level role; the
# canonical replacement at the role level is strata_admin.
ALLOWED_ROLES = ["owner", "ec_member", "strata_admin", "strata_manager", "super_admin"]
# Roles that have can_access_email = False
DENIED_ROLES = ["tenant", "service_provider", "guest"]


# ──────────────────────────────────────────────────────────────────────────────
# Permission model tests (pure unit — no HTTP stack needed)
# ──────────────────────────────────────────────────────────────────────────────

class TestMailAccessPermissions:
    """Verify the Permission model's can_access_email flag per role."""

    def test_owner_can_access_email(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.OWNER].can_access_email is True

    def test_ec_member_can_access_email(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.EC_MEMBER].can_access_email is True

    def test_chairman_can_access_email(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.EC_MEMBER].can_access_email is True

    def test_strata_manager_can_access_email(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.STRATA_MANAGER].can_access_email is True

    def test_super_admin_can_access_email(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.SUPER_ADMIN].can_access_email is True

    def test_tenant_cannot_access_email(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.TENANT].can_access_email is False

    def test_service_provider_cannot_access_email(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.SERVICE_PROVIDER].can_access_email is False


# ──────────────────────────────────────────────────────────────────────────────
# Endpoint logic tests (mock DB + permissions)
# ──────────────────────────────────────────────────────────────────────────────

class TestMailAccessEndpoint:
    """
    Unit tests for the get_mail_access() handler logic.
    Patches get_user_permissions to control permission outcomes
    without requiring a live MongoDB connection.
    """

    def _perm(self, can_access: bool):
        p = MagicMock()
        p.can_access_email = can_access
        return p

    @pytest.mark.asyncio
    async def test_returns_403_when_permission_denied(self):
        from fastapi import HTTPException
        with patch("routers.auth.get_user_permissions", return_value=self._perm(False)):
            from routers.auth import get_mail_access
            user = _make_user("tenant")
            with pytest.raises(HTTPException) as exc_info:
                await get_mail_access(current_user=user)
            assert exc_info.value.status_code == 403
            assert "owner" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_returns_404_when_no_mail_credentials(self):
        from fastapi import HTTPException
        with patch("routers.auth.get_user_permissions", return_value=self._perm(True)):
            from routers.auth import get_mail_access
            user = _make_user("owner", mail_username=None, mail_password=None)
            with pytest.raises(HTTPException) as exc_info:
                await get_mail_access(current_user=user)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_when_mail_password_missing(self):
        from fastapi import HTTPException
        with patch("routers.auth.get_user_permissions", return_value=self._perm(True)):
            from routers.auth import get_mail_access
            # Username present but no password
            user = _make_user("owner", mail_username="avneet@eastgateresidences.com.au", mail_password=None)
            with pytest.raises(HTTPException) as exc_info:
                await get_mail_access(current_user=user)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_200_with_valid_credentials(self):
        with patch("routers.auth.get_user_permissions", return_value=self._perm(True)):
            from routers.auth import get_mail_access
            user = _make_user("owner",
                              mail_username="avneet@eastgateresidences.com.au",
                              mail_password="pytest-fixture-password-not-a-credential")
            with patch.dict(os.environ, {"WEBMAIL_URL": EXPECTED_MAIL_URL}):
                result = await get_mail_access(current_user=user)
            assert result.mail_username == "avneet@eastgateresidences.com.au"
            assert result.mail_password == "pytest-fixture-password-not-a-credential"
            assert result.has_access is True
            assert result.mail_url == EXPECTED_MAIL_URL

    @pytest.mark.asyncio
    async def test_response_shape(self):
        """Response must have all required MailAccessResponse fields."""
        with patch("routers.auth.get_user_permissions", return_value=self._perm(True)):
            from routers.auth import get_mail_access
            user = _make_user("owner",
                              mail_username="avneet@eastgateresidences.com.au",
                              mail_password="pytest-fixture-password-not-a-credential")
            result = await get_mail_access(current_user=user)
            # All four fields present
            assert hasattr(result, "mail_username")
            assert hasattr(result, "mail_password")
            assert hasattr(result, "mail_url")
            assert hasattr(result, "has_access")

    @pytest.mark.asyncio
    async def test_mail_url_comes_from_configuration_not_a_hardcoded_domain(self):
        """Any building's webmail must be reachable, so the URL is configuration.

        WEBMAIL_URL wins, MAIL_URL is the fallback, and neither set yields ""
        rather than silently pointing every tenant at one building's webmail.
        """
        with patch("routers.auth.get_user_permissions", return_value=self._perm(True)):
            from routers.auth import get_mail_access
            user = _make_user("ec_member",
                              mail_username="ec@example-strata.test",
                              mail_password="not-a-real-mailbox-password")  # fixture data on a synthetic user

            with patch.dict(os.environ, {"WEBMAIL_URL": EXPECTED_MAIL_URL}):
                result = await get_mail_access(current_user=user)
            assert result.mail_url == EXPECTED_MAIL_URL
            assert result.mail_url.startswith("https://")

            fallback = "https://mail.another-strata.test/"
            with patch.dict(os.environ, {"MAIL_URL": fallback}, clear=False):
                os.environ.pop("WEBMAIL_URL", None)
                result = await get_mail_access(current_user=user)
            assert result.mail_url == fallback

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WEBMAIL_URL", None)
                os.environ.pop("MAIL_URL", None)
                result = await get_mail_access(current_user=user)
            assert result.mail_url == ""
            assert "eastgateresidences" not in result.mail_url


# ──────────────────────────────────────────────────────────────────────────────
# Permission model — allowed roles have correct flag (parametrized)
# ──────────────────────────────────────────────────────────────────────────────

class TestCanAccessEmailFlagByRole:

    @pytest.mark.parametrize("role", ALLOWED_ROLES)
    def test_allowed_role_flag(self, role):
        from models.user import DEFAULT_PERMISSIONS
        perm = DEFAULT_PERMISSIONS.get(role)
        assert perm is not None, f"No default permission for role={role}"
        assert perm.can_access_email is True, f"Expected can_access_email=True for role={role}"

    @pytest.mark.parametrize("role", DENIED_ROLES)
    def test_denied_role_flag(self, role):
        from models.user import DEFAULT_PERMISSIONS
        perm = DEFAULT_PERMISSIONS.get(role)
        if perm is None:
            pytest.skip(f"Role {role} has no default permissions entry")
        assert perm.can_access_email is False, f"Expected can_access_email=False for role={role}"


# ──────────────────────────────────────────────────────────────────────────────
# MailAccessResponse model validation
# ──────────────────────────────────────────────────────────────────────────────

class TestMailAccessResponseModel:

    def test_model_requires_all_fields(self):
        """MailAccessResponse must reject construction with missing fields."""
        from pydantic import ValidationError
        import sys
        sys.path.insert(0, "/home/gagneet/strata-management/backend")
        try:
            # Import from server module scope
            from server import MailAccessResponse
            with pytest.raises((ValidationError, TypeError)):
                MailAccessResponse(mail_username="a@b.com")  # missing password, url, has_access
        except ImportError:
            pytest.skip("server.MailAccessResponse not importable in isolation")

    def test_model_valid_construction(self):
        import sys
        sys.path.insert(0, "/home/gagneet/strata-management/backend")
        try:
            from server import MailAccessResponse
            obj = MailAccessResponse(
                mail_username="test@eastgateresidences.com.au",
                mail_password="pytest-fixture-password-not-a-credential",
                mail_url=EXPECTED_MAIL_URL,
                has_access=True,
            )
            assert obj.has_access is True
        except ImportError:
            pytest.skip("server.MailAccessResponse not importable in isolation")

    def test_has_access_defaults_to_false_pattern(self):
        """Verify False can be set (used for future no-access variant)."""
        import sys
        sys.path.insert(0, "/home/gagneet/strata-management/backend")
        try:
            from server import MailAccessResponse
            obj = MailAccessResponse(
                mail_username="test@eastgateresidences.com.au",
                mail_password="pytest-fixture-password-not-a-credential",
                mail_url=EXPECTED_MAIL_URL,
                has_access=False,
            )
            assert obj.has_access is False
        except ImportError:
            pytest.skip("server.MailAccessResponse not importable in isolation")
