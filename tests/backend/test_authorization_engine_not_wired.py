"""``authorization_engine`` must not acquire a production caller by accident.

## Why this test exists

The GAP-SEC-006 over-grant — `committee_member` conferring `users.manage` and
`financial.manage` — was described in the plan and the task as **live**. It was
not. Tracing it showed:

- `traverse_graph` and `check_permission` have no caller outside the test suite.
- `routers/rbac.py` imports `check_permission` and never invokes it.
- `POST /auth/rbac/check` routes to `permission_service.user_can`, which does not
  consult `RELATION_PERMISSION_MAP` at all.

But the relationship tuples ARE written — `sync_user_relationships` runs on role
assignment — so the graph is populated. The over-grant was **armed and not
firing**. Giving the engine one production caller would have made it live
instantly, and nothing would have flagged that.

This test is that flag. It is not an argument that the engine is wrong; it is a
tripwire, so wiring it becomes a decision somebody makes on purpose. The
canonical evaluator is `services/capability_registry.py`.

If you are here because this test failed: either route through
`capability_registry` instead, or — if the relation graph really is the right
tool for what you are doing — re-audit `RELATION_PERMISSION_MAP` against the
access matrix in `docs/security/acl_information_access_implementation_plan.md`
§4, then add your module to `ALLOWED_CALLERS` in the same commit.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

#: Functions that evaluate the relation graph into a permission set. These are
#: the ones whose result would become an authorisation answer.
EVALUATING_FUNCTIONS = frozenset({"traverse_graph", "check_permission"})

#: Modules permitted to call them. Empty by design: nothing in production should.
#: Adding an entry here is the deliberate decision this test exists to force.
ALLOWED_CALLERS: frozenset[str] = frozenset()

#: Directories that are not production code.
EXCLUDED_DIRS = ("venv", "node_modules", "__pycache__", ".git")


def _production_python_files() -> list[Path]:
    """Every backend .py file that is not a vendored dependency or a test."""
    files = []
    for path in BACKEND.rglob("*.py"):
        parts = set(path.parts)
        if parts & set(EXCLUDED_DIRS):
            continue
        if path.name.startswith("test_") or "tests" in parts:
            continue
        files.append(path)
    return files


def _calls_in(path: Path) -> set[str]:
    """Return the names of the evaluating functions actually CALLED in this file.

    AST-based rather than a grep, so an import, a re-export, or the string
    appearing in a docstring does not count. Only a real call does — which is the
    thing that would make the over-grant live.
    """
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in EVALUATING_FUNCTIONS:
            found.add(name)
    return found


def test_the_relation_graph_has_no_production_caller():
    """The tripwire. See this module's docstring before changing it."""
    offenders: dict[str, set[str]] = {}
    for path in _production_python_files():
        module = str(path.relative_to(REPO_ROOT))
        if module in ALLOWED_CALLERS:
            continue
        # The engine calls its own traverse_graph from check_permission — that is
        # internal, not a production caller.
        if path == BACKEND / "services" / "authorization_engine.py":
            continue
        calls = _calls_in(path)
        if calls:
            offenders[module] = calls

    assert not offenders, (
        "services/authorization_engine.py has gained a production caller:\n  "
        + "\n  ".join(f"{mod} calls {sorted(fns)}" for mod, fns in sorted(offenders.items()))
        + "\n\nRELATION_PERMISSION_MAP has not been audited against the access matrix for "
        "route enforcement. Route through services/capability_registry.py instead, or "
        "re-audit the map and add the module to ALLOWED_CALLERS in the same commit. "
        "See this test's module docstring."
    )


def test_rbac_router_imports_check_permission_without_using_it():
    """Pins the specific shape found on 2026-08-24, so a change is visible.

    ``routers/rbac.py`` imports ``check_permission`` in its optional-service
    block and guards endpoints on ``_AUTH_ENGINE_AVAILABLE``, but never calls it.
    If that changes in either direction — the import goes away, or a call
    appears — this test says so, and the reviewer can decide which was intended.
    """
    rbac = BACKEND / "routers" / "rbac.py"
    source = rbac.read_text()

    assert "check_permission" in source, (
        "routers/rbac.py no longer references check_permission. If the import was "
        "removed as dead code, delete this test with it."
    )
    assert not _calls_in(rbac), (
        "routers/rbac.py now CALLS the relation-graph evaluator. That makes "
        "RELATION_PERMISSION_MAP live. See this module's docstring."
    )


def test_permission_service_does_not_consult_the_relation_map():
    """``POST /auth/rbac/check`` routes here, so this is the live permission path.

    It must stay independent of the relation map, otherwise the over-grant
    reaches production through the endpoint that looks like it is doing something
    else.
    """
    source = (BACKEND / "services" / "permission_service.py").read_text()

    assert "RELATION_PERMISSION_MAP" not in source
    assert "traverse_graph" not in source


def test_the_narrowed_map_is_documented_where_someone_will_look():
    """A narrowing nobody can find gets undone by the next person to read the map."""
    source = (BACKEND / "services" / "authorization_engine.py").read_text()

    assert "GAP-SEC-006" in source, "the narrowing must cite its task"
    assert re.search(r"users\.manage", source), (
        "the module docstring should name what was removed, so a reader who expects "
        "the old behaviour finds out why it changed"
    )
