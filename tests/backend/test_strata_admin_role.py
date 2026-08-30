"""Tests for the strata_admin role.

Migration 0025 renamed the ``building_admin`` enum value to ``strata_admin``
to match the org model — Strata Admin is the admin of a Strata Management
Company (the ``core.tenants`` row), distinct from a Strata Manager who works
*for* the company managing buildings, and distinct from the volunteer
Chairman who heads an Executive Committee.

The legacy ``building_admin`` and ``chairman`` slugs were removed entirely
from the codebase — no normalisation aliases, no permission-map entries, no
role-list dead code. Chairman is now strictly an ``ec_position`` value
(``"CHAIRMAN"``) on a user whose ``role`` is ``ec_member``.

Covers:
  1. ``normalize_user_role`` no longer has legacy fallbacks (only ``reception``
     remains as a UI alias for ``admin_staff``).
  2. ``effective_role`` returns the canonical role.
  3. ``DEFAULT_PERMISSIONS`` has ``UserRole.STRATA_ADMIN`` with full permissions;
     no legacy alias keys remain.
  4. ``ROLE_PERMISSION_MAP`` has ``strata_admin`` slugs.
  5. ``is_approved_user`` treats ``strata_admin`` as auto-approved.
  6. Staff-management constants reference the new role; legacy chairman entries
     are gone.
  7. ``ECPosition`` still exposes the office-bearer constants
     (CHAIRMAN/TREASURER/SECRETARY/MEMBER) — Chairman is an ec_position, not
     a role.
  8. ``UserRole.STRATA_ADMIN`` constant exists; ``UserRole.CHAIRMAN`` was
     removed (chairman is no longer a top-level role).
  9. Router guard sets include ``strata_admin``.

Run::

    backend/venv/bin/pytest tests/backend/test_strata_admin_role.py -v
"""
import pytest
from datetime import datetime, timedelta, timezone


# ── 1. normalize_user_role ────────────────────────────────────────────────────

class TestNormalizeUserRole:
    def test_no_chairman_alias(self):
        """``chairman`` is no longer normalised — passes through as a string
        the role-set checks won't recognise. Loud failure beats silent mapping."""
        from models.user import normalize_user_role
        assert normalize_user_role("chairman") == "chairman"

    def test_no_building_admin_alias(self):
        """``building_admin`` is no longer normalised. Migration 0025 renamed
        the enum and dropped the alias."""
        from models.user import normalize_user_role
        assert normalize_user_role("building_admin") == "building_admin"

    def test_strata_admin_unchanged(self):
        from models.user import normalize_user_role, UserRole
        assert normalize_user_role("strata_admin") == UserRole.STRATA_ADMIN

    def test_reception_maps_to_admin_staff(self):
        from models.user import normalize_user_role, UserRole
        assert normalize_user_role("reception") == UserRole.ADMIN_STAFF

    def test_none_maps_to_guest(self):
        from models.user import normalize_user_role, UserRole
        assert normalize_user_role(None) == UserRole.GUEST

    def test_other_roles_pass_through(self):
        from models.user import normalize_user_role
        for role in ("super_admin", "ec_member", "strata_manager", "owner", "tenant"):
            assert normalize_user_role(role) == role


# ── 2. effective_role honours normalisation ──────────────────────────────────

class TestEffectiveRoleNormalization:
    def test_strata_admin_record_returns_strata_admin(self):
        from utils.auth import effective_role
        user = {"role": "strata_admin"}
        assert effective_role(user) == "strata_admin"

    def test_temp_elevation_to_ec_member(self):
        from utils.auth import effective_role
        exp = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        user = {
            "role": "owner",
            "effective_role": "ec_member",
            "temp_elevation": {"role": "ec_member", "expires_at": exp},
        }
        assert effective_role(user) == "ec_member"


# ── 3. DEFAULT_PERMISSIONS ───────────────────────────────────────────────────

class TestDefaultPermissions:
    def test_strata_admin_has_full_permissions(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        perms = DEFAULT_PERMISSIONS[UserRole.STRATA_ADMIN]
        assert perms.can_view_documents is True
        assert perms.can_manage_finances is True
        assert perms.can_manage_document_permissions is True
        assert perms.can_manage_tenancy is True
        assert perms.can_manage_users is True
        assert perms.can_send_notifications is True

    def test_strata_admin_same_as_strata_manager(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.STRATA_ADMIN] == DEFAULT_PERMISSIONS[UserRole.STRATA_MANAGER]

    def test_no_legacy_building_admin_alias(self):
        """Tech-debt cleanup invariant: the legacy slug must not appear in the map."""
        from models.user import DEFAULT_PERMISSIONS
        assert "building_admin" not in DEFAULT_PERMISSIONS

    def test_no_legacy_chairman_alias(self):
        """Tech-debt cleanup invariant: chairman is an ec_position, not a role."""
        from models.user import DEFAULT_PERMISSIONS
        assert "chairman" not in DEFAULT_PERMISSIONS

    def test_ec_member_cannot_manage_tenancy(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert DEFAULT_PERMISSIONS[UserRole.EC_MEMBER].can_manage_tenancy is False

    def test_strata_admin_in_default_permissions(self):
        from models.user import DEFAULT_PERMISSIONS, UserRole
        assert UserRole.STRATA_ADMIN in DEFAULT_PERMISSIONS


# ── 4. ROLE_PERMISSION_MAP ───────────────────────────────────────────────────

class TestRolePermissionMap:
    def test_strata_admin_in_map(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "strata_admin" in ROLE_PERMISSION_MAP

    def test_strata_admin_excludes_building_config(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "building.config" not in ROLE_PERMISSION_MAP["strata_admin"]

    def test_strata_admin_has_financial_manage(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "financial.manage" in ROLE_PERMISSION_MAP["strata_admin"]

    def test_strata_admin_has_users_manage(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "users.manage" in ROLE_PERMISSION_MAP["strata_admin"]

    def test_strata_admin_same_slugs_as_strata_manager(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert set(ROLE_PERMISSION_MAP["strata_admin"]) == set(ROLE_PERMISSION_MAP["strata_manager"])

    def test_no_legacy_chairman_in_map(self):
        from models.rbac_models import ROLE_PERMISSION_MAP
        assert "chairman" not in ROLE_PERMISSION_MAP


# ── 5. is_approved_user ──────────────────────────────────────────────────────

class TestIsApprovedUser:
    def test_strata_admin_is_auto_approved(self):
        from utils.auth import is_approved_user
        user = {"role": "strata_admin", "is_approved": False, "is_active": True}
        assert is_approved_user(user) is True

    def test_ec_member_is_auto_approved(self):
        from utils.auth import is_approved_user
        user = {"role": "ec_member", "is_approved": False, "is_active": True}
        assert is_approved_user(user) is True

    def test_owner_requires_is_approved(self):
        from utils.auth import is_approved_user
        unapproved = {"role": "owner", "is_approved": False, "is_active": True}
        approved = {"role": "owner", "is_approved": True, "is_active": True}
        assert is_approved_user(unapproved) is False
        assert is_approved_user(approved) is True


# ── 6. Staff-management constants ────────────────────────────────────────────

class TestStaffManagementConstants:
    def test_strata_admin_in_elevatable_roles(self):
        from routers.staff_management import ELEVATABLE_ROLES
        assert "strata_admin" in ELEVATABLE_ROLES

    def test_chairman_not_in_elevatable_roles(self):
        from routers.staff_management import ELEVATABLE_ROLES
        assert "chairman" not in ELEVATABLE_ROLES

    def test_ec_position_map_excludes_strata_admin(self):
        from routers.staff_management import EC_POSITION_MAP
        assert "strata_admin" not in EC_POSITION_MAP

    def test_ec_position_map_treasurer(self):
        from routers.staff_management import EC_POSITION_MAP
        assert EC_POSITION_MAP["treasurer"] == "TREASURER"

    def test_ec_position_map_secretary(self):
        from routers.staff_management import EC_POSITION_MAP
        assert EC_POSITION_MAP["secretary"] == "SECRETARY"

    def test_ec_position_map_ec_member_is_member(self):
        from routers.staff_management import EC_POSITION_MAP
        assert EC_POSITION_MAP["ec_member"] == "MEMBER"

    def test_ec_position_map_no_legacy_chairman_slug(self):
        """The chairman entry was a legacy alias; removing it forces callers
        to use the canonical ``strata_admin`` or ``ec_member`` slug."""
        from routers.staff_management import EC_POSITION_MAP
        assert "chairman" not in EC_POSITION_MAP


# ── 7. ECPosition constants ──────────────────────────────────────────────────

class TestECPositionConstants:
    def test_chairman_constant(self):
        from models.user import ECPosition
        assert ECPosition.CHAIRMAN == "CHAIRMAN"

    def test_treasurer_constant(self):
        from models.user import ECPosition
        assert ECPosition.TREASURER == "TREASURER"

    def test_secretary_constant(self):
        from models.user import ECPosition
        assert ECPosition.SECRETARY == "SECRETARY"

    def test_member_constant(self):
        from models.user import ECPosition
        assert ECPosition.MEMBER == "MEMBER"


# ── 8. UserRole class ────────────────────────────────────────────────────────

class TestUserRoleClass:
    def test_strata_admin_constant(self):
        from models.user import UserRole
        assert UserRole.STRATA_ADMIN == "strata_admin"

    def test_chairman_top_level_role_removed(self):
        """Chairman is no longer a top-level role — it is an ``ec_position``.
        Any code still referencing ``UserRole.CHAIRMAN`` would AttributeError;
        catching that early prevents silent role-routing bugs."""
        from models.user import UserRole
        assert not hasattr(UserRole, "CHAIRMAN"), (
            "UserRole.CHAIRMAN should have been removed when chairman was "
            "remapped to ec_member; ECPosition.CHAIRMAN still exists for the "
            "office-bearer position."
        )

    def test_ec_member_constant(self):
        from models.user import UserRole
        assert UserRole.EC_MEMBER == "ec_member"

    def test_strata_admin_distinct_from_ec_member(self):
        from models.user import UserRole
        assert UserRole.STRATA_ADMIN != UserRole.EC_MEMBER

    def test_strata_admin_distinct_from_strata_manager(self):
        from models.user import UserRole
        assert UserRole.STRATA_ADMIN != UserRole.STRATA_MANAGER


# ── 9. Router guard sets include strata_admin ───────────────────────────────

class TestRouterRoleGuards:
    """Spot-check a sample of routers to ensure strata_admin appears in
    their role-allow sets after the rename."""

    def test_ap_approval_includes_strata_admin(self):
        from routers.ap_approval import _MANAGER_ROLES
        assert "strata_admin" in _MANAGER_ROLES

    def test_risk_index_includes_strata_admin(self):
        from routers.risk_index import _ADMIN_ROLES
        assert "strata_admin" in _ADMIN_ROLES

    def test_financial_import_includes_strata_admin(self):
        from routers.financial_import import _ALLOWED_ROLES
        assert "strata_admin" in _ALLOWED_ROLES

    def test_intelligence_plan_edit_includes_strata_admin(self):
        from routers.intelligence import _PLAN_EDIT_ROLES
        assert "strata_admin" in _PLAN_EDIT_ROLES
