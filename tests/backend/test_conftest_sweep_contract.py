# @featuretrace:test-hygiene — pins the properties of the pytest_sessionfinish is_test_data sweep.
# Layer: test
# Data flow: static scan of tests/backend/conftest.py -> core.users cleanup contract (global).
# Related: tests/backend/conftest.py (pytest_sessionfinish)
#           backend/db_postgres/repos/identity_repo.py (_under_pytest — creates the rows it sweeps)
#           backend/scripts/data_repair/neutralise_leaked_test_users.py
# Toggle: none
"""The is_test_data sweep must survive a row it cannot delete.

WHY THIS GUARD EXISTS
---------------------
The sweep cleans child tables scoped to test TENANTS, then deletes
``core.users WHERE is_test_data = TRUE`` globally. Those two scopes disagree for
a flagged user living in a REAL tenant — which is exactly what
``identity_repo._under_pytest()`` produces when a test exercises a production
handler (``routers.auth.register`` writes to Postgres unmocked), and what
``neutralise_leaked_test_users.py --flag-unflagged`` produces when it retro-flags
an existing leak.

Reproduced on 2026-08-27 by planting one flagged user with a role assignment in
East Gate's tenant and running a two-test session::

    [conftest] is_test_data sweep failed: update or delete on table "users"
    violates foreign key constraint "user_role_assignments_user_id_fkey"

Because the delete was a single statement inside a single ``try``, that one row
aborted the WHOLE sweep and every other test user survived with it — including
users nothing referenced. That is the failure mode this guards.

The fix has two halves, and both are asserted here because either alone is
insufficient: clear the identity rows a test user OWNS (regardless of tenant),
and delete the remainder one row at a time in a savepoint so a row referenced by
a real audit record is isolated, left deactivated, and REPORTED rather than
taking the sweep down with it.

Deliberately a static scan. The behaviour under test runs in
``pytest_sessionfinish``, after the last test has reported, so it cannot be
exercised from inside a test; the live proof is recorded in
``docs/fixes/owner_transfers_and_user_list_cleanup_2026_08_27.md``.
"""
from __future__ import annotations

import re
from pathlib import Path

CONFTEST = Path(__file__).resolve().parent / "conftest.py"


def _sweep_source() -> str:
    source = CONFTEST.read_text(encoding="utf-8")
    start = source.index("def pytest_sessionfinish")
    return source[start:]


def _sweep_code() -> str:
    """The sweep with whole-line comments stripped.

    The comments deliberately quote the very SQL the fix removed, in order to
    explain why it was wrong. Asserting against the raw text would match that
    prose and report a regression that is not there.
    """
    return "\n".join(
        line for line in _sweep_source().split("\n")
        if not line.lstrip().startswith("#")
    )


def test_user_owned_identity_rows_are_cleared_regardless_of_tenant():
    """Children keyed on the test USER set, not only on test tenants."""
    sweep = _sweep_source()
    for table in (
        "core.user_sessions",
        "core.user_email_aliases",
        "core.user_units",
        "core.user_role_assignments",
        "core.user_invitations",
    ):
        pattern = re.compile(
            rf"DELETE FROM {re.escape(table)} WHERE[^\"']*?"
            r"SELECT user_id FROM core\.users WHERE is_test_data = TRUE",
            re.IGNORECASE | re.DOTALL,
        )
        assert pattern.search(sweep), (
            f"{table} is not cleared for test users outside a test tenant — a flagged "
            f"user in a real tenant will block the core.users delete"
        )


def test_user_role_assignments_covers_both_references():
    """The table references core.users twice; either reference blocks the delete."""
    sweep = _sweep_source()
    block = sweep[sweep.index("DELETE FROM core.user_role_assignments WHERE"):]
    block = block[:block.index('",')]
    assert "user_id IN" in block and "granted_by IN" in block, (
        "user_role_assignments has FKs on both user_id and granted_by"
    )


def test_users_are_deleted_one_row_at_a_time_in_a_savepoint():
    """One undeletable row must not abort the deletion of every other test user."""
    sweep = _sweep_code()
    assert "DELETE FROM core.users WHERE is_test_data = TRUE" not in sweep, (
        "a single bulk delete is all-or-nothing: one row referenced by an audit "
        "column aborts the sweep and every other test user survives with it"
    )
    assert "async with conn.transaction():" in sweep, (
        "each row delete needs its own savepoint so a failure is isolated"
    )
    assert "DELETE FROM core.users WHERE user_id = $1" in sweep


def test_survivors_are_reported_by_identity_not_just_counted():
    """A count alone does not tell an engineer which row to go and look at."""
    sweep = _sweep_source()
    tail = sweep[sweep.index("blocked: list[str] = []"):]
    assert "sys.stderr.write" in tail, "survivors must be reported"
    assert "email" in tail and "tenant_id" in tail, (
        "report the surviving rows' email and tenant — the 2026-08-27 failure named "
        "only a constraint, which does not identify the offending row"
    )


def test_sweep_still_deactivates_before_deleting():
    """A survivor must at least be unable to authenticate."""
    sweep = _sweep_source()
    deactivate = sweep.index(
        "UPDATE core.users SET is_active = FALSE WHERE is_test_data = TRUE AND is_active"
    )
    delete = sweep.index("DELETE FROM core.users WHERE user_id = $1")
    assert deactivate < delete, "deactivate first, so a row that cannot be deleted is inert"
