"""
Regression tests for the raw-`role` guard bug class.

A temporarily elevated user keeps their underlying `role` (typically "owner")
and receives `effective_role="ec_member"`. A guard that reads
`current_user["role"]` while listing `ec_member` among the allowed roles
therefore rejects exactly the users elevation was meant to admit — a silent
403 that looks like a permissions misconfiguration.

CLAUDE.md mandates `_effective_role(user)` / `utils.auth.effective_role(user)`
for every role guard. Seven sites still read the raw role while allowing
`ec_member`; they were fixed on 2026-08-23:

    backend/routers/community.py   delete_blog_post
    backend/routers/meetings.py    update_attendance, confirm_attendance
    backend/server.py              reactivate_expired_user, trigger_agm_alert,
                                   create_compliance_item, delete_compliance_item

The first test below is the durable one: it re-runs the audit and fails if the
pattern reappears anywhere. The rest pin the specific call sites by asserting
they resolve the elevated role rather than the raw one.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from models.user import UserRole
from utils.auth import effective_role


def _elevated_owner() -> dict:
    """An owner elevated to EC member — raw role "owner", effective "ec_member"."""
    return {
        "id": "user-elevated-001",
        "role": UserRole.OWNER,
        "effective_role": UserRole.EC_MEMBER,
        "building_id": "13195",
    }


def test_effective_role_resolves_elevation():
    """Baseline: the helper prefers effective_role over the raw role."""
    assert effective_role(_elevated_owner()) == UserRole.EC_MEMBER
    assert effective_role({"role": UserRole.OWNER}) == UserRole.OWNER


def _raw_role_guards_allowing_ec_member(path: Path) -> list[str]:
    """Comparisons on the raw role whose allowed set includes ec_member.

    AST, not line matching. The original audit compared each LINE for both
    `current_user["role"]` and `ec_member`, so it only ever saw the single-line
    shape. It missed the two commonest real ones:

        manager_roles = [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, ...]
        if current_user["role"] not in manager_roles:      # <- allowed set is a name

        if current_user["role"] in [                       # <- list spans lines
            UserRole.SUPER_ADMIN,
            UserRole.EC_MEMBER,
        ]:

    Three live guards were hiding in those two shapes on 2026-08-26
    (server.py::get_unit_occupants, ::update_agm_attendance,
    ::confirm_agm_attendance) while this test reported clean. Locally-assigned
    role lists are substituted before the check so the first shape is visible.
    """
    tree = ast.parse(path.read_text())
    offenders: list[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local: dict[str, str] = {}
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                local[node.targets[0].id] = ast.unparse(node.value)
        for node in ast.walk(func):
            if not isinstance(node, ast.Compare):
                continue
            source = ast.unparse(node)
            if not re.search(r"""current_user\[["']role["']\]""", source):
                continue
            expanded = source
            for name, value in local.items():
                expanded = expanded.replace(name, value)
            if re.search(r"\bec_member\b|\bEC_MEMBER\b", expanded):
                offenders.append(f"{path}:{node.lineno}: in {func.name}(): {source}")
    return offenders


def test_no_raw_role_guard_allows_ec_member_anywhere():
    """The audit from CLAUDE.md, as an executable test.

    A guard that reads current_user["role"] is only a bug when ec_member is in
    its allowed set, because that is the role elevation injects. Guards that
    check only super_admin or only strata_manager are unaffected and are
    deliberately not flagged.
    """
    targets = [Path("backend/server.py"), *sorted(Path("backend/routers").glob("*.py"))]
    assert targets, "no backend sources found — has the layout changed?"

    offenders: list[str] = []
    for path in targets:
        offenders += _raw_role_guards_allowing_ec_member(path)

    assert not offenders, (
        "raw current_user[\"role\"] guard(s) that allow ec_member — these 403 "
        "every temporarily elevated user:\n  " + "\n  ".join(offenders)
        + "\n\nUse effective_role(current_user) (routers) or "
          "_effective_role(current_user) (server.py)."
    )


def test_the_audit_itself_catches_the_shapes_that_used_to_hide(tmp_path):
    """The audit is only as good as the shapes it can see — pin all three.

    Without this, a regression to line matching would make the audit above silently
    stop finding anything, which is indistinguishable from the code being clean.
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from models.user import UserRole\n"
        "\n"
        "def one_line(current_user):\n"
        "    if current_user['role'] in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:\n"
        "        return True\n"
        "\n"
        "def via_local_variable(current_user):\n"
        "    manager_roles = [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]\n"
        "    if current_user['role'] not in manager_roles:\n"
        "        return False\n"
        "\n"
        "def multi_line(current_user):\n"
        "    if current_user['role'] in [\n"
        "        UserRole.SUPER_ADMIN,\n"
        "        UserRole.EC_MEMBER,\n"
        "    ]:\n"
        "        return True\n"
        "\n"
        "def unaffected_super_admin_only(current_user):\n"
        "    if current_user['role'] != UserRole.SUPER_ADMIN:\n"
        "        return False\n"
        "\n"
        "def already_fixed(current_user):\n"
        "    manager_roles = [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]\n"
        "    if effective_role(current_user) not in manager_roles:\n"
        "        return False\n"
    )
    found = _raw_role_guards_allowing_ec_member(sample)
    functions = {line.split("in ")[1].split("(")[0] for line in found}
    assert functions == {"one_line", "via_local_variable", "multi_line"}, found


@pytest.mark.parametrize(
    "module_path,func_name",
    [
        ("backend/routers/community.py", "delete_blog_post"),
        ("backend/routers/meetings.py", "update_attendance"),
        ("backend/routers/meetings.py", "confirm_attendance"),
    ],
)
def test_router_guards_use_effective_role(module_path, func_name):
    """The three router sites resolve the elevated role."""
    source = Path(module_path).read_text()
    match = re.search(
        rf"async def {func_name}\(.*?(?=\nasync def |\n@router|\Z)", source, re.S
    )
    assert match, f"{func_name} not found in {module_path}"
    body = match.group(0)

    assert "effective_role(current_user)" in body, (
        f"{module_path}::{func_name} must guard on effective_role(current_user)"
    )
    assert not re.search(r'current_user\[\s*["\']role["\']\s*\]', body), (
        f"{module_path}::{func_name} still reads the raw role"
    )


@pytest.mark.parametrize(
    "func_name",
    [
        "reactivate_expired_user",
        "trigger_agm_alert",
        "create_compliance_item",
        "delete_compliance_item",
    ],
)
def test_server_guards_use_effective_role(func_name):
    """The four server.py sites resolve the elevated role."""
    source = Path("backend/server.py").read_text()
    match = re.search(
        rf"async def {func_name}\(.*?(?=\nasync def |\n@api_router|\Z)", source, re.S
    )
    assert match, f"{func_name} not found in backend/server.py"
    body = match.group(0)

    assert "_effective_role(current_user)" in body, (
        f"server.py::{func_name} must guard on _effective_role(current_user)"
    )
    assert not re.search(r'current_user\[\s*["\']role["\']\s*\]', body), (
        f"server.py::{func_name} still reads the raw role"
    )


def test_elevated_owner_passes_the_fixed_guard_shape():
    """The elevated owner is admitted by the corrected guard and rejected by the old one."""
    user = _elevated_owner()
    allowed = [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]

    # The old, buggy shape.
    assert user["role"] not in allowed, "precondition: raw role must not be allowed"
    # The corrected shape.
    assert effective_role(user) in allowed
