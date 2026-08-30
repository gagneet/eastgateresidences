"""Route guards — the canonical owner, and the two bug classes it closes.

There were 116 private `_require_*` guards across 70 router files. `_require_manager`
alone had 19 copies with 9 different role sets. Two failure modes came out of that,
and the audits below are the durable part of this file: they re-derive the finding
from source, so a regression fails the build rather than waiting for the next
incident.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from models.user import UserRole
from utils.route_guards import (
    GOVERNANCE,
    OPERATIONAL_MANAGEMENT,
    PLATFORM_ADMIN,
    VALID_ROLES,
    assert_roles,
    require_roles,
)

ROUTERS = Path("backend/routers")


def _elevated_owner() -> dict:
    """An owner elevated to EC member: raw role "owner", effective "ec_member"."""
    return {"id": "u1", "role": UserRole.OWNER, "effective_role": UserRole.EC_MEMBER}


# ─── The owner's own contract ────────────────────────────────────────────────

class TestAssertRoles:
    def test_admits_on_effective_role_not_raw_role(self):
        """The whole point: elevation must be honoured."""
        user = _elevated_owner()
        assert assert_roles(user, OPERATIONAL_MANAGEMENT) is user

    def test_rejects_an_unelevated_owner(self):
        with pytest.raises(HTTPException) as exc:
            assert_roles({"role": UserRole.OWNER}, OPERATIONAL_MANAGEMENT)
        assert exc.value.status_code == 403

    def test_missing_role_is_rejected_not_defaulted_to_access(self):
        with pytest.raises(HTTPException):
            assert_roles({}, OPERATIONAL_MANAGEMENT)

    def test_unknown_role_name_raises_immediately(self):
        """"admin" is not a role — the back-office role is "admin_staff". Twelve
        guards tested for strings like this, so the condition could never match and
        the guard was silently NARROWER than its author believed. Failing loudly is
        the only way that stops being invisible."""
        for bad in ("admin", "treasurer", "maintenance", "chairman"):
            with pytest.raises(ValueError, match="unknown role"):
                assert_roles({"role": UserRole.SUPER_ADMIN}, {bad})

    def test_empty_role_set_is_a_programming_error(self):
        with pytest.raises(ValueError):
            assert_roles({"role": UserRole.SUPER_ADMIN}, set())

    def test_custom_detail_is_surfaced(self):
        with pytest.raises(HTTPException) as exc:
            assert_roles({"role": UserRole.OWNER}, PLATFORM_ADMIN, detail="Nope.")
        assert exc.value.detail == "Nope."


class TestNamedRoleSets:
    def test_all_named_sets_contain_only_real_roles(self):
        for name, roles in [("PLATFORM_ADMIN", PLATFORM_ADMIN),
                            ("OPERATIONAL_MANAGEMENT", OPERATIONAL_MANAGEMENT),
                            ("GOVERNANCE", GOVERNANCE)]:
            assert roles <= VALID_ROLES, f"{name} names a role that does not exist"

    def test_governance_excludes_strata_manager(self):
        """Submitting a committee decision is a different trust boundary from
        operating the building (CLAUDE.md, governance vs operational)."""
        assert UserRole.STRATA_MANAGER not in GOVERNANCE
        assert UserRole.EC_MEMBER in GOVERNANCE

    def test_require_roles_validates_at_construction_not_per_request(self):
        with pytest.raises(ValueError):
            require_roles("admin")
        assert callable(require_roles(UserRole.SUPER_ADMIN))


# ─── The durable audits ──────────────────────────────────────────────────────

def _guards(path: Path):
    """Yield (guard_name, node, source) for each module-level _require_* function."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:  # pragma: no cover
        return
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_require"):
            yield node.name, node, src


def _role_literals(node: ast.AST) -> set[str]:
    """String constants sitting in a literal collection ALONGSIDE a known role.

    AST, never text matching: `routers/defects_register.py` carries a COMMENT
    reading 'admin is not a valid role name', and a regex audit reports that
    comment as a violation. Structure cannot make that mistake.
    """
    found: set[str] = set()
    for coll in ast.walk(node):
        if not isinstance(coll, (ast.Set, ast.List, ast.Tuple)):
            continue
        strings, enums = set(), set()
        for el in coll.elts:
            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                strings.add(el.value)
            elif isinstance(el, ast.Attribute) and isinstance(el.value, ast.Name) and el.value.id == "UserRole":
                value = getattr(UserRole, el.attr, None)
                if isinstance(value, str):
                    enums.add(value)
        if (strings & VALID_ROLES) or enums:
            found |= strings
    return found


def test_no_router_guard_names_a_role_that_does_not_exist():
    """Twelve guards tested for "admin", "treasurer" or "maintenance". None is a
    UserRole, so each condition was dead and the guard admitted fewer people than
    its author intended — with nothing raising. Fixed 2026-08-28 by REMOVING the
    dead strings: substituting "admin_staff" would have WIDENED access, which is an
    operator's decision, not a cleanup script's."""
    offenders = []
    for path in sorted(ROUTERS.glob("*.py")):
        for name, node, _ in _guards(path):
            for bad in sorted(_role_literals(node) - VALID_ROLES):
                offenders.append(f"{path.name}:{node.lineno} {name}() -> {bad!r}")
    assert not offenders, (
        "role guard(s) testing a string that is not a UserRole — the condition can "
        "never match, so the guard is silently narrower than it looks:\n  "
        + "\n  ".join(offenders)
        + f"\n\nValid roles: {sorted(VALID_ROLES)}"
    )


def test_no_router_guard_reads_the_raw_role_without_honouring_elevation():
    """A guard must resolve the role through effective_role() (or the inline
    `.get("effective_role") or .get("role")` idiom it expands to). Reading the raw
    role 403s every temporarily elevated user, because elevation leaves `role` as
    "owner" and sets `effective_role`.

    `routers/matching.py::_require_finance` was the last one; fixed 2026-08-28.
    """
    offenders = []
    for path in sorted(ROUTERS.glob("*.py")):
        for name, node, src in _guards(path):
            segment = ast.get_source_segment(src, node) or ""
            honours = (
                "effective_role(" in segment.replace(".effective_role(", "")
                or '"effective_role"' in segment
                or "'effective_role'" in segment
                or "assert_roles(" in segment
                or "require_roles(" in segment
            )
            reads_raw = '"role"' in segment or "'role'" in segment
            if reads_raw and not honours:
                offenders.append(f"{path.name}:{node.lineno} {name}()")
    assert not offenders, (
        "role guard(s) reading the raw role with no elevation check — these 403 "
        "every temporarily elevated user:\n  " + "\n  ".join(offenders)
        + "\n\nUse utils.route_guards.assert_roles(user, {...})."
    )
