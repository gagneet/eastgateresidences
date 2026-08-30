"""
Regression tests for the canonical `effective_role()` helper extracted to
`utils/auth.py` (2026-04-20), and for the downstream helpers that now use it.

Guards against these known bugs (platform_gaps_audit.md):

  F-001 — utils/permissions.py:require_role() previously read raw
          `user.get("role")`, silently 403-ing every elevated user through the
          9 feature-toggle admin endpoints.
  F-002 — `_effective_role()` was trapped inside `server.py` and could not be
          imported by routers; this test locks the helper's home in utils/auth.
  F-015 — `require_permission_v2` and `get_effective_feature_access` used raw
          role for the super-admin bypass; now they use `effective_role()`.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.auth import effective_role
from utils.permissions import (
    get_effective_feature_access,
    require_role,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _iso_future(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _iso_past(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def owner_plain():
    return {"id": "u-owner", "role": "owner"}


@pytest.fixture
def owner_elevated_to_super():
    return {
        "id": "u-owner-2",
        "role": "owner",
        "effective_role": "super_admin",
        "temp_elevation": {"role": "super_admin", "expires_at": _iso_future()},
    }


@pytest.fixture
def owner_elevation_expired():
    return {
        "id": "u-owner-3",
        "role": "owner",
        "temp_elevation": {"role": "super_admin", "expires_at": _iso_past()},
    }


@pytest.fixture
def empty_user():
    return {}


# ─── effective_role() canonical behaviour ────────────────────────────────────

class TestEffectiveRoleHelper:

    def test_returns_raw_role_when_no_elevation(self, owner_plain):
        assert effective_role(owner_plain) == "owner"

    def test_returns_elevated_role_when_effective_role_set(self, owner_elevated_to_super):
        assert effective_role(owner_elevated_to_super) == "super_admin"

    def test_falls_back_to_guest_when_role_missing(self, empty_user):
        assert effective_role(empty_user) == "guest"

    def test_ignores_expired_elevation_without_effective_role_marker(self, owner_elevation_expired):
        # `effective_role()` is a read-only helper — it trusts the `effective_role`
        # field set by `get_current_user`. If the field isn't set (which is
        # correct for expired elevations per utils/auth.py:146-148), the helper
        # returns the raw role. This test pins that contract.
        assert effective_role(owner_elevation_expired) == "owner"


# ─── F-001: require_role() must accept elevated users ───────────────────────

class TestRequireRoleHonoursElevation:

    @pytest.mark.asyncio
    async def test_plain_owner_denied_for_super_admin_gate(self, owner_plain):
        checker = require_role("super_admin")
        with pytest.raises(Exception) as exc:  # HTTPException
            await checker(current_user=owner_plain)
        assert "403" in str(exc.value) or "Access denied" in str(exc.value)

    @pytest.mark.asyncio
    async def test_elevated_owner_passes_super_admin_gate(self, owner_elevated_to_super):
        checker = require_role("super_admin")
        result = await checker(current_user=owner_elevated_to_super)
        assert result is owner_elevated_to_super

    @pytest.mark.asyncio
    async def test_elevated_user_passes_list_gate(self, owner_elevated_to_super):
        checker = require_role(["chairman", "super_admin"])
        result = await checker(current_user=owner_elevated_to_super)
        assert result is owner_elevated_to_super


# ─── F-015: get_effective_feature_access super-admin bypass via effective_role ──

class TestFeatureAccessSuperAdminBypass:

    @pytest.mark.asyncio
    async def test_elevated_super_admin_bypasses_disabled_feature(self, owner_elevated_to_super):
        # Even with a disabled feature toggle, an elevated super_admin gets True.
        with patch("db_postgres.repos.config_repo.resolve_feature_toggle", new=AsyncMock(return_value=False)):
            result = await get_effective_feature_access(owner_elevated_to_super, "x")
        assert result is True

    @pytest.mark.asyncio
    async def test_plain_owner_blocked_by_disabled_feature(self, owner_plain):
        with patch("db_postgres.repos.config_repo.resolve_feature_toggle", new=AsyncMock(return_value=False)):
            result = await get_effective_feature_access(owner_plain, "x")
        assert result is False


# ─── F-002: import surface lock-in ──────────────────────────────────────────

def test_effective_role_importable_from_utils_auth():
    """Pins the canonical home. If someone moves or renames it, this test
    fails loudly and the 40+ future router migrations (per §F-002) are
    protected from silently falling back to raw role checks."""
    from utils.auth import effective_role as imported
    assert callable(imported)


def test_server_forwarder_still_works():
    """server.py's _effective_role must keep working — 13 in-server call
    sites still use it (see §F-002)."""
    from server import _effective_role as legacy
    assert legacy({"role": "tenant"}) == "tenant"
    # Per migration 0025: chairman is no longer normalised to anything;
    # it passes through as the literal string the caller sent. Downstream
    # role-set checks will not recognise it, which is the deliberate
    # loud-failure behaviour. Test the post-migration canonical case
    # (effective_role=ec_member from a temp_elevation flow).
    assert legacy({"role": "owner", "effective_role": "ec_member"}) == "ec_member"
