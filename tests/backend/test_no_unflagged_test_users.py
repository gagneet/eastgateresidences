# @featuretrace:test-hygiene — guards the is_test_data contract for user creation.
# Layer: test
# Data flow: static scan of tests/ → fails the suite if a user can be created without is_test_data.
# Related: tests/backend/conftest.py (pytest_sessionfinish sweep)
#           backend/scripts/data_repair/neutralise_leaked_test_users.py
# Toggle: none
"""Every test that creates a ``core.users`` row must flag it ``is_test_data``.

WHY THIS GUARD EXISTS
---------------------
``pytest_sessionfinish`` cleans up by ``is_test_data``, so an unflagged test user
is invisible to it *forever*. Per-test ``finally`` blocks are not a substitute:
they are skipped whenever a test errors early or the run is killed, which is
exactly how production accumulated **2,155 leaked ``core.users`` rows out of
2,160** by 2026-08-25 — 1,772 of them active ``super_admin`` accounts sharing the
constant password committed in ``test_invitation_rls_bypass.py``.

Until that incident nothing filtered ``is_test_data`` at login, so those rows were
working credentials rather than clutter. The login path now refuses them when
``APP_ENV=production``, but that defence only fires when the flag is actually set
— which is what this test enforces.

This is a STATIC scan, deliberately: it needs no database, runs in milliseconds,
and fails at the point the offending line is written rather than after a leak.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent.parent

# A call to either repo helper, or a raw INSERT into core.users.
_CALL = re.compile(r"\b(?:create_user_for_registration|create_user)\s*\(")
_INSERT = re.compile(r"INSERT\s+INTO\s+core\.users", re.IGNORECASE)
# Prose in docstrings/comments mentions these helpers by name; ignore those.
_PROSE_PREFIX = ("#", "*", "-", '"', "'", "1.", "2.", "3.")
# How far past the opening line to look for the flag. Generous: these are
# multi-line dict literals and multi-line SQL.
_WINDOW = 35


def _offenders() -> list[str]:
    hits: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            is_call = bool(_CALL.search(line)) and "def " not in line \
                and "import" not in line and not stripped.startswith(_PROSE_PREFIX)
            if not (is_call or _INSERT.search(line)):
                continue
            if "is_test_data" not in "\n".join(lines[i:i + _WINDOW]):
                hits.append(f"{path.relative_to(TESTS_ROOT)}:{i + 1}: {stripped[:70]}")
    return hits


def test_every_test_created_user_is_flagged_is_test_data() -> None:
    offenders = _offenders()
    assert not offenders, (
        "These test user creations do not set is_test_data, so the "
        "pytest_sessionfinish sweep can never clean them up:\n  "
        + "\n  ".join(offenders)
        + "\n\nPass is_test_data=True (repo helpers) or add the is_test_data "
          "column set to TRUE (raw INSERT). An unflagged super_admin row is a "
          "leaked production credential, not just untidy test data."
    )


def test_the_cleanup_sweep_actually_covers_core_users() -> None:
    """The sweep must name core.users explicitly.

    Regression guard for the original defect: the sweep listed eight core.*
    tables and simply never included the one that holds credentials.
    """
    conftest = (TESTS_ROOT / "backend" / "conftest.py").read_text(encoding="utf-8")
    sweep = conftest.split("def pytest_sessionfinish", 1)
    assert len(sweep) == 2, "pytest_sessionfinish hook not found in conftest.py"
    body = sweep[1]
    assert "DELETE FROM core.users WHERE is_test_data" in body, (
        "pytest_sessionfinish no longer deletes is_test_data rows from core.users. "
        "That omission is what leaked 2,155 rows into production."
    )
