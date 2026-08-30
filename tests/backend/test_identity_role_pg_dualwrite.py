"""GAP-IDENTITY-UI-DB-001 — identity role write-cutover to Postgres.

Covers:
  1. identity_repo.set_scheme_role() issues a deactivate-others + upsert-target
     pair in one session (so a role *change* leaves exactly one active role).
  2. server.py::_write_postgres_role_assignment() gating — it is a no-op when
     identity_core is NOT postgres_write for the building, and propagates when it
     is (and the user has a core.users row).

These are pure-logic/mock tests (no live DB) — CI runs them in the venv.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest


class _FakeSession:
    def __init__(self):
        self.executed = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        return None


def _session_context(session):
    @asynccontextmanager
    async def _ctx():
        yield session
    return _ctx


# ── 1. set_scheme_role SQL shape ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_scheme_role_deactivates_others_then_upserts_target():
    from db_postgres.repos import identity_repo

    session = _FakeSession()
    with (
        patch("db_postgres.repos.identity_repo.async_session_context", _session_context(session)),
        patch("db_postgres.repos.identity_repo.set_tenant", AsyncMock()),
    ):
        await identity_repo.set_scheme_role(
            user_id="u-1",
            tenant_id="t-1",
            scheme_id="s-1",
            role="strata_manager",
            ec_position=None,
            granted_by="admin-1",
        )

    assert len(session.executed) == 2, "expected a deactivate then an upsert"
    deactivate_sql, deactivate_params = session.executed[0]
    upsert_sql, upsert_params = session.executed[1]

    # First statement deactivates the user's OTHER active roles for this scheme.
    assert "UPDATE core.user_role_assignments" in deactivate_sql
    assert "is_active = FALSE" in deactivate_sql
    assert "role <> CAST(:role AS core.user_role)" in deactivate_sql
    assert deactivate_params["uid"] == "u-1"
    assert deactivate_params["sid"] == "s-1"
    assert deactivate_params["role"] == "strata_manager"

    # Second statement upserts the target role active.
    assert "INSERT INTO core.user_role_assignments" in upsert_sql
    assert "ON CONFLICT (user_id, scheme_id, role)" in upsert_sql
    assert upsert_params["role"] == "strata_manager"
    assert upsert_params["tenant_id"] == "t-1"
    assert upsert_params["granted_by"] == "admin-1"


# ── 2. _write_postgres_role_assignment gating ───────────────────────────────

def _decision(postgres_allowed: bool):
    class _D:
        pass
    d = _D()
    d.postgres_allowed = postgres_allowed
    d.shadow_enabled = False
    return d


@pytest.mark.asyncio
async def test_role_propagation_noop_when_identity_core_not_promoted():
    import server

    set_role = AsyncMock()
    with (
        patch("services.domain_source_guard.require_domain_source",
              AsyncMock(return_value=_decision(False))),
        patch("db_postgres.repos.identity_repo.set_scheme_role", set_role),
    ):
        await server._write_postgres_role_assignment(
            building_id="99999",
            user_email="x@example.com",
            role="ec_member",
            ec_position="CHAIRMAN",
            granted_by="admin-1",
        )

    set_role.assert_not_called()  # not promoted → never writes Postgres


@pytest.mark.asyncio
async def test_role_propagation_writes_when_promoted_and_user_exists():
    import server

    set_role = AsyncMock()
    with (
        patch("services.domain_source_guard.require_domain_source",
              AsyncMock(return_value=_decision(True))),
        patch("db_postgres.repos.identity_repo.get_scheme_by_number",
              AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"})),
        patch("db_postgres.repos.identity_repo.find_user_by_email_for_admin",
              AsyncMock(return_value={"id": "u-1", "email": "x@example.com"})),
        patch("db_postgres.repos.identity_repo.set_scheme_role", set_role),
    ):
        await server._write_postgres_role_assignment(
            building_id="13195",
            user_email="x@example.com",
            role="ec_member",
            ec_position="CHAIRMAN",
            granted_by="admin-1",
        )

    set_role.assert_awaited_once()
    kwargs = set_role.await_args.kwargs
    assert kwargs["user_id"] == "u-1"
    assert kwargs["scheme_id"] == "s-1"
    assert kwargs["tenant_id"] == "t-1"
    assert kwargs["role"] == "ec_member"
    assert kwargs["ec_position"] == "CHAIRMAN"


@pytest.mark.asyncio
async def test_role_propagation_noop_when_user_not_in_postgres():
    import server

    set_role = AsyncMock()
    with (
        patch("services.domain_source_guard.require_domain_source",
              AsyncMock(return_value=_decision(True))),
        patch("db_postgres.repos.identity_repo.get_scheme_by_number",
              AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"})),
        patch("db_postgres.repos.identity_repo.find_user_by_email_for_admin",
              AsyncMock(return_value=None)),
        patch("db_postgres.repos.identity_repo.set_scheme_role", set_role),
    ):
        await server._write_postgres_role_assignment(
            building_id="13195",
            user_email="ghost@example.com",
            role="owner",
            ec_position=None,
            granted_by="admin-1",
        )

    set_role.assert_not_called()  # no core.users row → skip, never error
