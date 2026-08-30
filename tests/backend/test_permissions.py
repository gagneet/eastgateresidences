"""
Tests for the RBAC Permission Service.

Tests cover:
- Owner can view own financials
- Owner cannot edit building config
- EC Member can vote on motion
- Chairman can approve invoice
- Tenant cannot view levies
- Strata Manager can manage multiple buildings
- Legacy permission backward compatibility
- ABAC contextual rules

Run from project root:
    backend/venv/bin/pytest tests/backend/test_permissions.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_owner():
    return {
        "id": "user-owner-001",
        "role": "owner",
        "unit_number": "TH087",
        "building_id": "building-east-001",
        "is_approved": True,
        "is_active": True,
    }


@pytest.fixture
def mock_chairman():
    # Per migration 0025: chairman is an ec_position, not a role.
    return {
        "id": "user-chair-001",
        "role": "ec_member",
        "ec_position": "CHAIRMAN",
        "building_id": "building-east-001",
        "is_approved": True,
        "is_active": True,
    }


@pytest.fixture
def mock_ec_member():
    return {
        "id": "user-ec-001",
        "role": "ec_member",
        "building_id": "building-east-001",
        "is_approved": True,
        "is_active": True,
    }


@pytest.fixture
def mock_tenant():
    return {
        "id": "user-tenant-001",
        "role": "tenant",
        "unit_number": "TH087",
        "building_id": "building-east-001",
        "is_approved": True,
        "is_active": True,
    }


@pytest.fixture
def mock_strata_manager():
    return {
        "id": "user-sm-001",
        "role": "strata_manager",
        "building_id": None,  # can manage multiple buildings
        "is_approved": True,
        "is_active": True,
    }


@pytest.fixture
def mock_super_admin():
    return {
        "id": "user-admin-001",
        "role": "super_admin",
        "is_approved": True,
        "is_active": True,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_user_can_context(user_dict, slugs: set, abac_result=None):
    """
    Return a context-manager stack that patches the three key callables inside
    user_can so that no real database is required.

    - db.users._coll.find_one  → returns user_dict
    - get_user_permission_slugs → returns slugs (the RBAC result)
    - get_user_active_roles     → returns [] (audit-trail path; not under test here)
    - apply_abac_rules          → returns abac_result (None = defer to RBAC)
    """
    mock_db = MagicMock()
    mock_db.users._coll.find_one = AsyncMock(return_value=user_dict)
    # Cache miss so the slug-fetch path is exercised
    mock_db.permissions_cache._coll.find_one = AsyncMock(return_value=None)
    mock_db.permissions_cache._coll.update_one = AsyncMock()

    return (
        patch("services.permission_service.db", mock_db),
        patch(
            "services.permission_service.get_user_permission_slugs",
            new=AsyncMock(return_value=slugs),
        ),
        patch(
            "services.permission_service.get_user_active_roles",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "services.permission_service.apply_abac_rules",
            new=AsyncMock(return_value=abac_result),
        ),
    )


# ─── 1–3  Owner Permission Tests ──────────────────────────────────────────────

class TestOwnerPermissions:
    """Owner can view their own financials but cannot manage finances or building config."""

    # The RBAC seed assigns these slugs to the OWNER role
    _OWNER_SLUGS = {
        "financial.view_own",
        "workorder.view", "workorder.create",
        "documents.view",
        "committee.vote",
        "announcements.view",
        "meetings.view",
        "listings.view", "listings.create",
        "maintenance.request",
        "tenancy.view", "tenancy.manage",
        "chat.access", "email.access",
    }

    async def test_owner_can_view_own_financials(self, mock_owner):
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_owner, self._OWNER_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_owner["id"],
                "financial.view_own",
                building_id=mock_owner["building_id"],
            )

        assert result.allowed is True
        assert "financial.view_own" in result.reason or result.allowed

    async def test_owner_cannot_manage_finances(self, mock_owner):
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_owner, self._OWNER_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_owner["id"],
                "financial.manage",
                building_id=mock_owner["building_id"],
            )

        assert result.allowed is False

    async def test_owner_cannot_edit_building_config(self, mock_owner):
        from services.permission_service import user_can

        # ABAC rule forces deny for building.config unless SUPER_ADMIN
        patches = _make_user_can_context(mock_owner, self._OWNER_SLUGS, abac_result=False)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_owner["id"],
                "building.config",
                building_id=mock_owner["building_id"],
            )

        assert result.allowed is False
        assert "deny" in result.reason.lower() or not result.allowed


# ─── 4  Chairman Permission Tests ─────────────────────────────────────────────

class TestChairmanPermissions:
    """Chairman has all EC_MEMBER permissions plus invoice approval."""

    _CHAIRMAN_SLUGS = {
        # inherited from EC_MEMBER
        "financial.view", "financial.manage",
        "workorder.view", "workorder.create", "workorder.update", "workorder.approve",
        "documents.view", "documents.upload",
        "committee.vote", "committee.finalize_minutes", "committee.manage",
        "users.view", "users.manage",
        "announcements.view", "announcements.create", "announcements.manage",
        "meetings.view", "meetings.manage",
        "listings.view", "listings.create",
        "maintenance.request", "maintenance.manage",
        "tenancy.view",
        "chat.access", "notifications.send",
        "schedule.view", "email.access",
        # chairman-specific
        "financial.approve_invoice", "financial.export",
        "users.invite",
    }

    async def test_chairman_can_approve_invoice(self, mock_chairman):
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_chairman, self._CHAIRMAN_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_chairman["id"],
                "financial.approve_invoice",
                building_id=mock_chairman["building_id"],
            )

        assert result.allowed is True

    async def test_chairman_inherits_ec_member_vote_permission(self, mock_chairman):
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_chairman, self._CHAIRMAN_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_chairman["id"],
                "committee.vote",
                building_id=mock_chairman["building_id"],
            )

        assert result.allowed is True


# ─── 5  EC Member Permission Tests ────────────────────────────────────────────

class TestECMemberPermissions:
    """EC Member can vote on motions and view full financials."""

    _EC_SLUGS = {
        "financial.view", "financial.manage",
        "workorder.view", "workorder.create", "workorder.update", "workorder.approve",
        "documents.view", "documents.upload",
        "committee.vote", "committee.finalize_minutes", "committee.manage",
        "users.view", "users.manage",
        "announcements.view", "announcements.create", "announcements.manage",
        "meetings.view", "meetings.manage",
        "listings.view", "listings.create",
        "maintenance.request", "maintenance.manage",
        "tenancy.view",
        "chat.access", "notifications.send",
        "schedule.view", "email.access",
    }

    async def test_ec_member_can_vote(self, mock_ec_member):
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_ec_member, self._EC_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_ec_member["id"],
                "committee.vote",
                building_id=mock_ec_member["building_id"],
            )

        assert result.allowed is True

    async def test_ec_member_can_view_full_financials(self, mock_ec_member):
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_ec_member, self._EC_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_ec_member["id"],
                "financial.view",
                building_id=mock_ec_member["building_id"],
            )

        assert result.allowed is True

    async def test_ec_member_cannot_approve_invoice(self, mock_ec_member):
        """invoice approval is chairman-only, not part of base EC_MEMBER slug set."""
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_ec_member, self._EC_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_ec_member["id"],
                "financial.approve_invoice",
                building_id=mock_ec_member["building_id"],
            )

        assert result.allowed is False


# ─── 6  Tenant Permission Tests ───────────────────────────────────────────────

class TestTenantPermissions:
    """Tenants can access documents and chat but cannot view financial data."""

    _TENANT_SLUGS = {
        "workorder.view",
        "documents.view",
        "announcements.view",
        "meetings.view",
        "listings.view", "listings.create",
        "maintenance.request",
        "chat.access",
    }

    async def test_tenant_cannot_view_levies(self, mock_tenant):
        """Tenants have no financial.view OR financial.view_own slug."""
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_tenant, self._TENANT_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            for slug in ("financial.view", "financial.view_own"):
                result = await user_can(
                    mock_tenant["id"],
                    slug,
                    building_id=mock_tenant["building_id"],
                )
                assert result.allowed is False, (
                    f"Tenant should not have '{slug}' but got allowed=True"
                )

    async def test_tenant_can_view_documents(self, mock_tenant):
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_tenant, self._TENANT_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_tenant["id"],
                "documents.view",
                building_id=mock_tenant["building_id"],
            )

        assert result.allowed is True

    async def test_tenant_cannot_manage_users(self, mock_tenant):
        from services.permission_service import user_can

        patches = _make_user_can_context(mock_tenant, self._TENANT_SLUGS)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_tenant["id"],
                "users.manage",
                building_id=mock_tenant["building_id"],
            )

        assert result.allowed is False


# ─── 7  Strata Manager Permission Tests ───────────────────────────────────────

class TestStrataManagerPermissions:
    """Strata manager holds all slugs except building.config."""

    # All slugs from RBAC seed minus building.config
    _SM_SLUGS = {
        "building.view", "building.update",
        "financial.view", "financial.view_own", "financial.manage",
        "financial.approve_invoice", "financial.export",
        "workorder.view", "workorder.create", "workorder.update", "workorder.approve",
        "documents.view", "documents.upload", "documents.delete", "documents.manage",
        "committee.vote", "committee.finalize_minutes", "committee.manage",
        "users.view", "users.manage", "users.invite",
        "announcements.view", "announcements.create", "announcements.manage",
        "meetings.view", "meetings.manage",
        "listings.view", "listings.create", "listings.manage",
        "maintenance.request", "maintenance.manage",
        "tenancy.view", "tenancy.manage",
        "chat.access",
        "notifications.send",
        "schedule.view", "schedule.manage",
        "email.access",
    }

    async def test_strata_manager_has_all_building_permissions(self, mock_strata_manager):
        """Strata manager can approve invoices, manage finances, and manage users."""
        from services.permission_service import user_can

        for slug in ("financial.approve_invoice", "financial.manage", "users.manage"):
            patches = _make_user_can_context(mock_strata_manager, self._SM_SLUGS)
            with patches[0], patches[1], patches[2], patches[3]:
                result = await user_can(
                    mock_strata_manager["id"],
                    slug,
                )
            assert result.allowed is True, (
                f"Strata manager should have '{slug}' but got allowed=False"
            )

    async def test_strata_manager_lacks_building_config(self, mock_strata_manager):
        """building.config is SUPER_ADMIN only; strata_manager is denied by ABAC."""
        from services.permission_service import user_can

        patches = _make_user_can_context(
            mock_strata_manager, self._SM_SLUGS, abac_result=False
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await user_can(
                mock_strata_manager["id"],
                "building.config",
            )

        assert result.allowed is False


# ─── 8  Super Admin Bypass ────────────────────────────────────────────────────

class TestSuperAdminBypass:
    """Super admin is never denied — bypass happens before any RBAC/ABAC check."""

    async def test_super_admin_bypass(self, mock_super_admin):
        from services.permission_service import user_can

        mock_db = MagicMock()
        mock_db.users._coll.find_one = AsyncMock(return_value=mock_super_admin)

        with patch("services.permission_service.db", mock_db):
            for slug in ("building.config", "financial.approve_invoice", "users.manage"):
                result = await user_can(mock_super_admin["id"], slug)
                assert result.allowed is True, (
                    f"SUPER_ADMIN should bypass deny for '{slug}'"
                )
                assert "SUPER_ADMIN" in result.reason

    async def test_super_admin_bypass_does_not_call_rbac(self, mock_super_admin):
        """Confirm that RBAC slug lookup is never called for super_admin."""
        from services.permission_service import user_can

        mock_db = MagicMock()
        mock_db.users._coll.find_one = AsyncMock(return_value=mock_super_admin)

        mock_slugs = AsyncMock(return_value=set())
        with patch("services.permission_service.db", mock_db), \
                patch("services.permission_service.get_user_permission_slugs", mock_slugs):
            result = await user_can(mock_super_admin["id"], "financial.approve_invoice")

        assert result.allowed is True
        mock_slugs.assert_not_called()


# ─── 9  Legacy Permission Mapping ────────────────────────────────────────────

class TestLegacyPermissionMapping:
    """Pure-function tests — no DB required."""

    def test_legacy_permission_mapping(self):
        """can_view_finances=True must map to both financial.view and financial.view_own."""
        from services.permission_service import legacy_to_slug_permissions

        slugs = legacy_to_slug_permissions({"can_view_finances": True})

        assert "financial.view" in slugs
        assert "financial.view_own" in slugs

    def test_legacy_permission_false_flags_excluded(self):
        """False flags must not produce any slugs."""
        from services.permission_service import legacy_to_slug_permissions

        slugs = legacy_to_slug_permissions({"can_view_finances": False, "can_chat": False})

        assert len(slugs) == 0

    def test_legacy_can_manage_finances_maps_correctly(self):
        """can_manage_finances=True maps to manage + approve_invoice + export."""
        from services.permission_service import legacy_to_slug_permissions

        slugs = legacy_to_slug_permissions({"can_manage_finances": True})

        assert "financial.manage" in slugs
        assert "financial.approve_invoice" in slugs
        assert "financial.export" in slugs

    def test_legacy_can_chat_maps_to_chat_access(self):
        from services.permission_service import legacy_to_slug_permissions

        slugs = legacy_to_slug_permissions({"can_chat": True})

        assert "chat.access" in slugs

    def test_legacy_can_manage_users_maps_to_users_slugs(self):
        from services.permission_service import legacy_to_slug_permissions

        slugs = legacy_to_slug_permissions({"can_manage_users": True})

        assert "users.view" in slugs
        assert "users.manage" in slugs
        assert "users.invite" in slugs

    def test_legacy_multiple_true_flags_union(self):
        """Multiple True flags yield the union of all their mapped slugs."""
        from services.permission_service import legacy_to_slug_permissions

        slugs = legacy_to_slug_permissions({
            "can_view_finances": True,
            "can_chat": True,
            "can_manage_users": True,
        })

        assert "financial.view" in slugs
        assert "chat.access" in slugs
        assert "users.manage" in slugs

    def test_legacy_empty_dict_returns_empty_set(self):
        from services.permission_service import legacy_to_slug_permissions

        assert legacy_to_slug_permissions({}) == set()

    def test_legacy_unknown_flag_ignored(self):
        """Unrecognised permission flags are silently ignored."""
        from services.permission_service import legacy_to_slug_permissions

        slugs = legacy_to_slug_permissions({"can_fly_rockets": True})

        assert len(slugs) == 0


# ─── 10  Backward Compatibility — get_user_permissions ───────────────────────

class TestLegacyPermissionBackwardCompat:
    """get_user_permissions() must continue returning the old Permission object."""

    def test_legacy_permission_backward_compat_owner(self, mock_owner):
        from utils.permissions import get_user_permissions

        perms = get_user_permissions(mock_owner)

        assert perms.can_view_finances is True
        assert perms.can_manage_finances is False

    def test_legacy_permission_backward_compat_ec_member(self, mock_ec_member):
        from utils.permissions import get_user_permissions

        perms = get_user_permissions(mock_ec_member)

        assert perms.can_view_finances is True
        assert perms.can_manage_finances is True

    def test_legacy_permission_backward_compat_tenant(self, mock_tenant):
        from utils.permissions import get_user_permissions

        perms = get_user_permissions(mock_tenant)

        assert perms.can_view_finances is False
        assert perms.can_manage_finances is False

    def test_legacy_permission_backward_compat_chairman(self, mock_chairman):
        from utils.permissions import get_user_permissions

        perms = get_user_permissions(mock_chairman)

        assert perms.can_view_finances is True
        assert perms.can_manage_finances is True
        assert perms.can_post_announcements is True

    def test_legacy_permission_returns_permission_model(self, mock_owner):
        """Return type must be the Permission Pydantic model."""
        from utils.permissions import get_user_permissions
        from models.user import Permission

        perms = get_user_permissions(mock_owner)

        assert isinstance(perms, Permission)

    def test_legacy_unapproved_user_gets_guest_permissions(self):
        """Unapproved regular users must receive the guest permission profile."""
        from utils.permissions import get_user_permissions
        from models.user import DEFAULT_PERMISSIONS, UserRole

        unapproved_owner = {
            "id": "user-unapproved-001",
            "role": "owner",
            "is_approved": False,
            "is_active": True,
        }
        perms = get_user_permissions(unapproved_owner)
        guest_perms = DEFAULT_PERMISSIONS[UserRole.GUEST]

        assert perms == guest_perms

    def test_legacy_custom_permissions_override_defaults(self, mock_owner):
        """custom_permissions dict must be merged on top of role defaults."""
        from utils.permissions import get_user_permissions

        user_with_custom = {**mock_owner, "custom_permissions": {"can_manage_finances": True}}
        perms = get_user_permissions(user_with_custom)

        assert perms.can_manage_finances is True


# ─── 11–13  DEFAULT_PERMISSIONS mapping ──────────────────────────────────────

class TestDefaultPermissions:
    """Direct assertions on the DEFAULT_PERMISSIONS dict from models/user.py."""

    def test_default_permissions_owner(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole

        owner_perms = DEFAULT_PERMISSIONS[UserRole.OWNER]

        assert owner_perms.can_view_finances is True
        assert owner_perms.can_manage_finances is False
        assert owner_perms.can_manage_users is False
        assert owner_perms.can_view_documents is True

    def test_default_permissions_tenant(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole

        tenant_perms = DEFAULT_PERMISSIONS[UserRole.TENANT]

        assert tenant_perms.can_view_finances is False
        assert tenant_perms.can_manage_finances is False
        assert tenant_perms.can_view_documents is True
        assert tenant_perms.can_post_announcements is False

    def test_default_permissions_ec_member(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole

        ec_perms = DEFAULT_PERMISSIONS[UserRole.EC_MEMBER]

        assert ec_perms.can_manage_finances is True
        assert ec_perms.can_view_finances is True
        assert ec_perms.can_manage_users is True
        assert ec_perms.can_post_announcements is True

    def test_default_permissions_chairman(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole

        chair_perms = DEFAULT_PERMISSIONS[UserRole.EC_MEMBER]

        assert chair_perms.can_manage_finances is True
        assert chair_perms.can_manage_users is True
        assert chair_perms.can_post_announcements is True
        assert chair_perms.can_send_notifications is True

    def test_default_permissions_super_admin_all_true(self):
        """Super admin must have every permission flag set to True."""
        from models.user import DEFAULT_PERMISSIONS, UserRole

        admin_perms = DEFAULT_PERMISSIONS[UserRole.SUPER_ADMIN]
        all_flags = admin_perms.model_dump()

        assert all(all_flags.values()), (
            f"Super admin missing True for: "
            f"{[k for k, v in all_flags.items() if not v]}"
        )

    def test_default_permissions_guest_all_false(self):
        """Guest must have every permission flag set to False."""
        from models.user import DEFAULT_PERMISSIONS, UserRole

        guest_perms = DEFAULT_PERMISSIONS[UserRole.GUEST]
        all_flags = guest_perms.model_dump()

        assert not any(all_flags.values()), (
            f"Guest should have no permissions but got True for: "
            f"{[k for k, v in all_flags.items() if v]}"
        )

    def test_default_permissions_strata_manager_matches_strata_admin(self):
        """Strata manager and strata admin share the same permission profile.

        (Per migration 0025: chairman is no longer a top-level role; the
        permission-equivalent is strata_admin. EC member has narrower
        permissions — e.g. can_manage_tenancy=False.)
        """
        from models.user import DEFAULT_PERMISSIONS, UserRole

        sm = DEFAULT_PERMISSIONS[UserRole.STRATA_MANAGER].model_dump()
        sa = DEFAULT_PERMISSIONS[UserRole.STRATA_ADMIN].model_dump()

        assert sm == sa, (
            f"Permission mismatch between STRATA_MANAGER and STRATA_ADMIN: "
            f"{[k for k in sm if sm[k] != sa[k]]}"
        )

    def test_default_permissions_service_provider_no_finances(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole

        sp_perms = DEFAULT_PERMISSIONS[UserRole.SERVICE_PROVIDER]

        assert sp_perms.can_view_finances is False
        assert sp_perms.can_manage_finances is False

    def test_all_roles_present_in_default_permissions(self):
        """Every UserRole constant must have a default Permission entry."""
        from models.user import DEFAULT_PERMISSIONS, UserRole

        expected_roles = {
            UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.EC_MEMBER,
            UserRole.STRATA_MANAGER, UserRole.RECEPTION, UserRole.OWNER,
            UserRole.TENANT, UserRole.REAL_ESTATE_AGENT,
            UserRole.SERVICE_PROVIDER, UserRole.GUEST,
        }
        missing = expected_roles - set(DEFAULT_PERMISSIONS.keys())
        assert not missing, f"Missing DEFAULT_PERMISSIONS entries for: {missing}"


# ─── ABAC contextual rules ────────────────────────────────────────────────────

class TestABACContextualRules:
    """Test ABAC rule evaluation directly (no full user_can pipeline)."""

    async def test_abac_building_config_denied_for_non_super_admin(self, mock_owner):
        from services.permission_service import apply_abac_rules

        mock_db = MagicMock()
        mock_db.users._coll.find_one = AsyncMock(
            return_value={"id": mock_owner["id"], "role": "owner"}
        )

        with patch("services.permission_service.db", mock_db):
            result = await apply_abac_rules(
                mock_owner["id"], "building.config", "building-east-001", None
            )

        assert result is False

    async def test_abac_building_config_allowed_for_super_admin(self, mock_super_admin):
        from services.permission_service import apply_abac_rules

        mock_db = MagicMock()
        mock_db.users._coll.find_one = AsyncMock(
            return_value={"id": mock_super_admin["id"], "role": "super_admin"}
        )

        with patch("services.permission_service.db", mock_db):
            result = await apply_abac_rules(
                mock_super_admin["id"], "building.config", None, None
            )

        assert result is True

    async def test_abac_building_scope_mismatch_denies(self, mock_owner):
        """Resource in a different building than the user's scope is denied."""
        from services.permission_service import apply_abac_rules

        mock_db = MagicMock()
        mock_db.users._coll.find_one = AsyncMock(return_value={"role": "owner"})

        resource = {"building_id": "building-west-999"}
        with patch("services.permission_service.db", mock_db):
            result = await apply_abac_rules(
                mock_owner["id"],
                "financial.view",
                "building-east-001",
                resource,
            )

        assert result is False

    async def test_abac_returns_none_for_unrecognised_slug(self, mock_owner):
        """Unknown slugs must pass through (return None) without forcing a decision."""
        from services.permission_service import apply_abac_rules

        mock_db = MagicMock()
        mock_db.users._coll.find_one = AsyncMock(return_value={"role": "owner"})

        with patch("services.permission_service.db", mock_db):
            result = await apply_abac_rules(
                mock_owner["id"], "chat.access", None, None
            )

        assert result is None
