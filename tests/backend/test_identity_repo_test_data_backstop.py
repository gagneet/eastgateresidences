# @featuretrace:test-hygiene — pins the is_test_data backstop on the Postgres user writes.
# Layer: test
# Data flow: identity_repo.create_user / create_user_for_registration -> core.users.is_test_data (global).
# Related: backend/db_postgres/repos/identity_repo.py (_under_pytest)
#           tests/backend/test_no_unflagged_test_users.py (the static scan this backstops)
#           backend/scripts/data_repair/neutralise_leaked_test_users.py
# Toggle: none
"""A user written from inside a pytest run must be flagged ``is_test_data``.

WHY THIS GUARD EXISTS
---------------------
``pytest_sessionfinish`` sweeps by ``is_test_data`` and the ``APP_ENV=production``
login gate refuses accounts carrying it. Both defences key off the flag, so an
UNFLAGGED test row is not clutter — it is a live credential invisible to every
cleanup and every gate.

``tests/backend/test_no_unflagged_test_users.py`` scans ``tests/`` for direct
``core.users`` writes, which catches a test that inserts a user itself. It cannot
catch the case that actually leaked: ``test_multi_unit_ownership.py`` mocks
``routers.auth.db`` thoroughly and asserts on the MongoDB inserts, but
``routers/auth.py`` also calls ``identity_repo.create_user_for_registration``
(auth.py:1521), that path has no test double, and it reached the real
``DATABASE_URL``. The *code under test* did the writing, so the static scan saw
nothing — and the production registration path has no reason to set the flag.

Three rows (``owner@test.com``, ``owner2@test.com``, ``owner@example.com``) sat
active and password-bearing in East Gate's production tenant on 2026-08-27 as a
result.

The backstop is ``_under_pytest()``, keyed on ``PYTEST_CURRENT_TEST`` — set by
pytest per test and absent in any production process. These tests assert the flag
is forced on, and that the mechanism is inert outside a test run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from db_postgres.repos import identity_repo  # noqa: E402


class _Session:
    """Records bound parameters; returns a new user_id and no pre-existing row."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        result = MagicMock()
        # create_user_for_registration probes for an existing row first; None
        # makes it fall through to the INSERT this test is about.
        result.scalar = MagicMock(
            return_value=None if "SELECT user_id" in str(statement)
            else "77777777-7777-7777-7777-777777777777"
        )
        return result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _insert_params(session: _Session) -> dict:
    inserts = [p for sql, p in session.calls if "INSERT INTO core.users" in sql]
    assert inserts, "no INSERT INTO core.users was issued"
    return inserts[0]


def test_under_pytest_is_true_inside_a_test_run():
    assert "PYTEST_CURRENT_TEST" in os.environ, "pytest should set this per test"
    assert identity_repo._under_pytest() is True


def test_under_pytest_is_false_without_the_marker():
    """Inert in production: the branch depends solely on pytest's own env var."""
    saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        assert identity_repo._under_pytest() is False
    finally:
        if saved is not None:
            os.environ["PYTEST_CURRENT_TEST"] = saved


@pytest.mark.asyncio
async def test_create_user_for_registration_forces_the_flag_under_pytest():
    """The registration path is the one that leaked; it must flag regardless of caller."""
    session = _Session()
    with patch("db_postgres.repos.identity_repo.async_session_context", lambda: session), \
            patch("db_postgres.repos.identity_repo.set_tenant", AsyncMock()):
        await identity_repo.create_user_for_registration(
            email="backstop-probe@test.example.com",
            password_hash="x",
            full_name="Backstop Probe",
            role="owner",
            tenant_id="22222222-2222-2222-2222-222222222222",
            is_test_data=False,          # caller says "not test data"
        )
    assert _insert_params(session)["is_test_data"] is True, (
        "a user written during a test run must be flagged even when the caller "
        "passes is_test_data=False — the production registration path always does"
    )


@pytest.mark.asyncio
async def test_create_user_forces_the_flag_under_pytest():
    session = _Session()
    with patch("db_postgres.repos.identity_repo.async_session_context", lambda: session), \
            patch("db_postgres.repos.identity_repo.set_tenant", AsyncMock()):
        await identity_repo.create_user(
            {"email": "backstop-probe2@test.example.com", "full_name": "Backstop Probe 2",
             "password_hash": "x", "role": "owner"},
            tenant_id="22222222-2222-2222-2222-222222222222",
        )
    assert _insert_params(session)["is_test_data"] is True


def test_status_is_cast_to_the_real_enum_type():
    """core.users.status is core.record_status; core.user_status does not exist.

    Casting to the wrong type raised inside update_user_profile's catch-all, which
    logged "non-fatal Postgres sync error" and returned False — silently making
    every status change Mongo-only, so archiving a user never removed them from a
    promoted building's list.
    """
    source = (BACKEND / "db_postgres" / "repos" / "identity_repo.py").read_text(encoding="utf-8")
    assert "CAST(:status AS core.record_status)" in source
    # Matched on the CAST expression, not the bare type name: the surrounding
    # comment names core.user_status deliberately, to explain why it is wrong.
    assert "CAST(:status AS core.user_status)" not in source, (
        "core.user_status is not a type in this database; casting to it fails at runtime"
    )
