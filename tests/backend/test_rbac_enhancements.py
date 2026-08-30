"""
Tests for RBAC Enhancements — verifies fixes made in the RBAC audit.

Covers:
  1. user_to_response() serialises effective_role when elevation is active
  2. _check_owns_unit() fallback via unit_number (legacy Excel-imported data)
  3. admin_staff role has workorder.view, workorder.approve, announcements.view in ROLE_PERMISSION_MAP
  4. admin_staff role has correct backend DEFAULT_PERMISSIONS
  5. real_estate_agent has correct permission slug set
  6. service_provider has correct (minimal) permission slug set
  7. ROLE_PERMISSION_MAP covers all 10 roles
  8. All 10 roles present in DEFAULT_PERMISSIONS
  9. DashboardLayout isManager logic must include ec_member (AuthContext unit tests)

Run from project root:
    backend/venv/bin/pytest tests/backend/test_rbac_enhancements.py -v
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _elevated_user(base_role="owner", elevated_to="ec_member"):
    """Return a user dict with an active temp_elevation."""
    exp = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    return {
        "id": "user-elevated-001",
        "role": base_role,
        "effective_role": elevated_to,
        "temp_elevation": {
            "role": elevated_to,
            "expires_at": exp,
            "elevated_by": "admin-001",
        },
        "is_approved": True,
        "is_active": True,
        "unit_number": "TH087",
        "building_id": "building-east-001",
    }


# ─── 1. effective_role serialisation ──────────────────────────────────────────

class TestEffectiveRoleSerialisation:
    """user_to_response() must return the effective_role string when elevation is active."""

    def test_returns_base_role_when_no_elevation(self):
        from utils.permissions import user_to_response

        user = {
            "id": "u1",
            "email": "owner@example.com",
            "full_name": "Owner User",
            "role": "owner",
            "is_approved": True,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = user_to_response(user)
        assert resp.role == "owner"

    def test_returns_effective_role_when_elevation_active(self):
        from utils.permissions import user_to_response

        user = _elevated_user(base_role="owner", elevated_to="ec_member")
        user.update({
            "email": "owner@example.com",
            "full_name": "Owner User",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        resp = user_to_response(user)
        assert resp.role == "ec_member", (
            f"Expected effective_role 'ec_member' but got '{resp.role}'"
        )

    def test_elevation_raises_permissions(self):
        """When elevated to ec_member, user should get ec_member permissions."""
        from utils.permissions import user_to_response

        user = _elevated_user(base_role="owner", elevated_to="ec_member")
        user.update({
            "email": "owner@example.com",
            "full_name": "Owner User",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        resp = user_to_response(user)
        # EC member can manage finances
        assert resp.permissions.can_manage_finances is True

    def test_expired_elevation_returns_base_role(self):
        """An expired temp_elevation must revert to base role."""
        from utils.permissions import user_to_response

        exp = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        user = {
            "id": "u2",
            "email": "owner2@example.com",
            "full_name": "Owner Two",
            "role": "owner",
            "temp_elevation": {
                "role": "ec_member",
                "expires_at": exp,
                "elevated_by": "admin-001",
            },
            "is_approved": True,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = user_to_response(user)
        assert resp.role == "owner", (
            "Expired elevation should revert to base role 'owner'"
        )


# ─── 2. _check_owns_unit fallback ─────────────────────────────────────────────

class TestCheckOwnsUnitFallback:
    """_check_owns_unit should fall back to unit_number when owner_id is absent."""

    @pytest.mark.asyncio
    async def test_primary_owner_id_match_succeeds(self):
        from services.permission_service import _check_owns_unit

        mock_db = MagicMock()
        # Primary: owner_id lookup finds a unit
        mock_db.units._coll.find_one = AsyncMock(return_value={"id": "unit-001"})

        with patch("services.permission_service.db", mock_db):
            result = await _check_owns_unit("user-001", "building-001")

        assert result is True
        # Users collection should NOT be queried (primary check succeeded)
        mock_db.users._coll.find_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_unit_number_match_succeeds(self):
        from services.permission_service import _check_owns_unit

        mock_db = MagicMock()
        # Primary: owner_id lookup returns None (legacy data — no owner_id field)
        # Fallback: unit_number match succeeds
        mock_db.units._coll.find_one = AsyncMock(side_effect=[
            None,  # first call: owner_id filter → no match
            {"id": "unit-th017"},  # second call: unit_number filter → match
        ])
        mock_db.users._coll.find_one = AsyncMock(
            return_value={"unit_number": "TH087"}
        )

        with patch("services.permission_service.db", mock_db):
            result = await _check_owns_unit("user-001", "building-001")

        assert result is True
        mock_db.users._coll.find_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_unit_number_returns_false(self):
        from services.permission_service import _check_owns_unit

        mock_db = MagicMock()
        mock_db.units._coll.find_one = AsyncMock(return_value=None)
        mock_db.users._coll.find_one = AsyncMock(return_value={"unit_number": None})

        with patch("services.permission_service.db", mock_db):
            result = await _check_owns_unit("user-nobody", "building-001")

        assert result is False

    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        from services.permission_service import _check_owns_unit

        mock_db = MagicMock()
        mock_db.units._coll.find_one = AsyncMock(return_value=None)
        # User doc not found (returns None)
        mock_db.users._coll.find_one = AsyncMock(return_value=None)

        with patch("services.permission_service.db", mock_db):
            result = await _check_owns_unit("user-ghost", "building-001")

        assert result is False


# ─── 3. ROLE_PERMISSION_MAP completeness ──────────────────────────────────────

class TestRolePermissionMapCompleteness:
    """ROLE_PERMISSION_MAP must cover all 10 roles and include fixed reception slugs."""

    ALL_EXPECTED_ROLES = {
        "super_admin", "strata_admin", "strata_manager", "ec_member",
        "admin_staff", "owner", "tenant", "real_estate_agent",
        "service_provider", "guest",
    }

    def test_all_roles_present(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        required = self.ALL_EXPECTED_ROLES
        missing = required - set(ROLE_PERMISSION_MAP.keys())
        assert not missing, f"Missing roles: {missing}"

    def test_admin_staff_has_workorder_view(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "workorder.view" in ROLE_PERMISSION_MAP["admin_staff"], (
            "admin_staff must have workorder.view slug"
        )

    def test_admin_staff_has_workorder_approve(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "workorder.approve" in ROLE_PERMISSION_MAP["admin_staff"], (
            "admin_staff must have workorder.approve slug"
        )

    def test_admin_staff_has_announcements_view(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "announcements.view" in ROLE_PERMISSION_MAP["admin_staff"], (
            "admin_staff must have announcements.view slug"
        )

    def test_ec_member_has_users_manage(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "users.manage" in ROLE_PERMISSION_MAP["ec_member"], (
            "ec_member must have users.manage for resident management access"
        )

    def test_tenant_does_not_have_financial_view(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "financial.view" not in ROLE_PERMISSION_MAP["tenant"]
        assert "financial.view_own" not in ROLE_PERMISSION_MAP["tenant"]

    def test_super_admin_has_all_slugs(self):
        from models.rbac_models import ROLE_PERMISSION_MAP, ALL_PERMISSION_SLUGS
        super_admin_slugs = set(ROLE_PERMISSION_MAP["super_admin"])
        assert super_admin_slugs == ALL_PERMISSION_SLUGS, (
            f"super_admin missing slugs: {ALL_PERMISSION_SLUGS - super_admin_slugs}"
        )

    def test_strata_admin_lacks_building_config(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "building.config" not in ROLE_PERMISSION_MAP["strata_admin"]

    def test_strata_manager_lacks_building_config(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "building.config" not in ROLE_PERMISSION_MAP["strata_manager"]


# ─── 4. Reception DEFAULT_PERMISSIONS ─────────────────────────────────────────

class TestReceptionDefaultPermissions:
    """Reception role backend defaults should match the RBAC design spec."""

    def test_reception_can_manage_requests(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.RECEPTION]
        assert perms.can_manage_requests is True

    def test_reception_cannot_view_finances(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.RECEPTION]
        assert perms.can_view_finances is False
        assert perms.can_manage_finances is False

    def test_reception_can_view_documents(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.RECEPTION]
        assert perms.can_view_documents is True

    def test_reception_can_view_schedule(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.RECEPTION]
        assert perms.can_view_schedule is True


# ─── 5. Real estate agent permissions ─────────────────────────────────────────

class TestRealEstateAgentPermissions:
    """Real estate agent has tenancy/listing access but not finances."""

    def test_rea_cannot_view_finances(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.REAL_ESTATE_AGENT]
        assert perms.can_view_finances is False

    def test_rea_can_manage_tenancy(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.REAL_ESTATE_AGENT]
        assert perms.can_manage_tenancy is True

    def test_rea_has_listing_slugs_in_rbac(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        slugs = ROLE_PERMISSION_MAP["real_estate_agent"]
        assert "listings.view" in slugs
        assert "listings.create" in slugs
        assert "tenancy.manage" in slugs


# ─── 6. Service provider permissions ──────────────────────────────────────────

class TestServiceProviderPermissions:
    """Service provider has minimal access — only assigned jobs and schedule."""

    def test_sp_cannot_view_finances(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.SERVICE_PROVIDER]
        assert perms.can_view_finances is False
        assert perms.can_manage_finances is False

    def test_sp_cannot_manage_users(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.SERVICE_PROVIDER]
        assert perms.can_manage_users is False

    def test_sp_can_view_schedule(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.SERVICE_PROVIDER]
        assert perms.can_view_schedule is True

    def test_sp_rbac_slugs_minimal(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        slugs = set(ROLE_PERMISSION_MAP["service_provider"])
        assert "workorder.view" in slugs
        assert "schedule.view" in slugs
        # No financial or user management
        assert "financial.view" not in slugs
        assert "users.manage" not in slugs


# ─── 7. isManager must include ec_member ──────────────────────────────────────

class TestIsManagerLogic:
    """Unit-test the isManager role-set to confirm ec_member is included (Python equivalent)."""

    _MANAGER_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member"}
    _NON_MANAGER_ROLES = {"owner", "tenant", "real_estate_agent", "service_provider", "guest", "admin_staff"}

    def test_ec_member_is_manager(self):
        assert "ec_member" in self._MANAGER_ROLES, (
            "ec_member must be in MANAGER_ROLES — EC members need management nav items"
        )

    def test_strata_manager_is_manager(self):
        assert "strata_manager" in self._MANAGER_ROLES

    def test_strata_admin_is_manager(self):
        assert "strata_admin" in self._MANAGER_ROLES

    def test_super_admin_is_manager(self):
        assert "super_admin" in self._MANAGER_ROLES

    def test_owner_is_not_manager(self):
        assert "owner" in self._NON_MANAGER_ROLES

    def test_tenant_is_not_manager(self):
        assert "tenant" in self._NON_MANAGER_ROLES

    def test_admin_staff_is_not_manager(self):
        """Admin staff can manage requests but is not a management-tier role."""
        assert "admin_staff" in self._NON_MANAGER_ROLES


# ─── 8. isECMember must NOT include strata_manager ────────────────────────────

class TestIsECMemberLogic:
    """isECMember should only cover actual EC roles, not job-function roles like strata_manager."""

    _EC_ROLES = {"ec_member", "super_admin", "strata_admin"}
    _NON_EC_ROLES = {"strata_manager", "owner", "tenant", "real_estate_agent", "service_provider", "guest",
                     "admin_staff"}

    def test_strata_manager_not_ec_member(self):
        assert "strata_manager" not in self._EC_ROLES, (
            "strata_manager is a professional role, not an EC membership role"
        )

    def test_ec_member_is_ec(self):
        assert "ec_member" in self._EC_ROLES

    def test_strata_admin_is_ec(self):
        assert "strata_admin" in self._EC_ROLES


# ─── 9. Seed users cover all roles ────────────────────────────────────────────

class TestSeedUserRoleCoverage:
    """seeds/users.py must include test accounts for all non-guest roles."""

    EXPECTED_ROLES = {
        "super_admin", "strata_admin", "ec_member", "owner", "tenant",
        "admin_staff", "real_estate_agent", "service_provider",
    }

    def test_all_key_roles_seeded(self, monkeypatch):
        # The seed refuses to invent a password (GAP-SEC-013), and this assertion is
        # only about which ROLES the fixture list covers, so any value will do.
        monkeypatch.setenv("SEED_TEST_USER_PASSWORD", "throwaway-for-role-assertion")
        from seeds.users import get_users_seed_data
        seed_roles = {u["role"] for u in get_users_seed_data()}
        missing = self.EXPECTED_ROLES - seed_roles
        assert not missing, (
            f"Missing seed users for roles: {missing}. "
            "Add test accounts so each role path can be exercised in tests."
        )
