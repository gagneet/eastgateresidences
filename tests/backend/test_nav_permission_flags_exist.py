"""
Guards the navigation permission_flag contract.

`routers/navigation.py::get_nav_config` resolves menu permission flags with
`can_flags.get(pf, False)`, so a `permission_flag` naming a key that does not
exist on `models.user.Permission` is not a no-op — it hides the item from
EVERY user, super_admin included, silently and forever.

That is exactly what happened to `can_manage_settings` and `can_view_audit`.
Both were referenced in seeds/navigation_configs.py, neither existed on the
Permission model or on any user document, and five admin menu items
(/admin, /settings, /admin/feature-toggles, /admin/audit-logs and the strata
admin "Organisation settings") were invisible to everyone.

These tests make that class of bug impossible to reintroduce.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from models.user import DEFAULT_PERMISSIONS, Permission, UserRole
from utils.permissions import get_user_permissions

_SEED_FILES = (
    Path("backend/seeds/navigation_configs.py"),
    Path("backend/seeds/snapshot_navigation_configs.py"),
)


def _declared_permission_flags() -> set[str]:
    """Every non-empty permission_flag value used by any navigation seed."""
    flags: set[str] = set()
    for path in _SEED_FILES:
        if not path.exists():
            continue
        for match in re.finditer(
            r"""["']permission_flag["']\s*:\s*["']([a-z_]+)["']""", path.read_text()
        ):
            flags.add(match.group(1))
    return flags


def test_seed_files_are_present():
    """Fail loudly rather than vacuously passing if the seeds move."""
    assert any(p.exists() for p in _SEED_FILES), (
        f"no navigation seed file found at {[str(p) for p in _SEED_FILES]}; "
        "update _SEED_FILES"
    )


def test_every_nav_permission_flag_exists_on_the_permission_model():
    """A permission_flag that is not a Permission field hides its item from all users."""
    declared = _declared_permission_flags()
    assert declared, "no permission_flag values parsed — has the seed format changed?"

    unknown = sorted(declared - set(Permission.model_fields))
    assert not unknown, (
        f"navigation seeds reference permission_flag(s) that do not exist on "
        f"models.user.Permission: {unknown}.\n"
        "navigation.py resolves flags with can_flags.get(pf, False), so these "
        "items are hidden from EVERY user including super_admin. Either add the "
        "field to Permission (and grant it in DEFAULT_PERMISSIONS) or point the "
        "seed at an existing flag."
    )


def test_every_nav_permission_flag_is_granted_to_at_least_one_role():
    """A flag no role holds is indistinguishable from a hidden item."""
    declared = _declared_permission_flags() & set(Permission.model_fields)

    ungranted = sorted(
        flag for flag in declared
        if not any(getattr(perm, flag, False) for perm in DEFAULT_PERMISSIONS.values())
    )
    assert not ungranted, (
        f"permission_flag(s) exist on the model but no role is granted them: "
        f"{ungranted}. Every menu item gated on these is hidden from everyone."
    )


@pytest.mark.parametrize(
    "role,flag,expected",
    [
        # super_admin's nav carries /admin, /settings, /admin/feature-toggles
        # and /admin/audit-logs.
        (UserRole.SUPER_ADMIN, "can_manage_settings", True),
        (UserRole.SUPER_ADMIN, "can_view_audit", True),
        # strata_admin's nav carries "Organisation settings"; the platform audit
        # log is not in its menu.
        (UserRole.STRATA_ADMIN, "can_manage_settings", True),
        (UserRole.STRATA_ADMIN, "can_view_audit", False),
        # strata_manager's nav carries building "Settings".
        (UserRole.STRATA_MANAGER, "can_manage_settings", True),
        (UserRole.STRATA_MANAGER, "can_view_audit", False),
        # An EC member governs; they do not administer settings or read the
        # platform audit log. See docs/security/acl_information_access_implementation_plan.md.
        (UserRole.EC_MEMBER, "can_manage_settings", False),
        (UserRole.EC_MEMBER, "can_view_audit", False),
        (UserRole.ADMIN_STAFF, "can_manage_settings", False),
        (UserRole.OWNER, "can_manage_settings", False),
        (UserRole.OWNER, "can_view_audit", False),
        (UserRole.TENANT, "can_manage_settings", False),
        (UserRole.GUEST, "can_manage_settings", False),
        (UserRole.GUEST, "can_view_audit", False),
    ],
)
def test_settings_and_audit_flags_are_granted_narrowly(role, flag, expected):
    """Pin who holds the two flags, so a future widening is a deliberate edit."""
    permissions = get_user_permissions({"role": role, "is_approved": True})
    assert getattr(permissions, flag) is expected, (
        f"{role} should have {flag}={expected}"
    )
