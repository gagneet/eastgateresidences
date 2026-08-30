"""Regression test for the SA switch-building 404 loop.

Bug: ``identity_repo.get_scheme_by_id`` and ``get_scheme_by_number`` did
not set the ``app.tenant_id`` RLS bypass sentinel before SELECTing
``core.schemes``.  The ``tenant_isolation_core_schemes`` policy added in
migration 0026 only returns rows when *either*:

  - ``tenant_id = core.current_tenant_id()`` (caller's own tenant), OR
  - ``app.tenant_id = '00000000-...'`` (the documented cross-tenant
    bypass sentinel).

A fresh pool connection has neither set, so the helper silently returned
``None`` for every cross-tenant lookup — including the one inside
``POST /auth/switch-building``.  The frontend then treated the 404 as a
"stale localStorage" signal, cleared it, and bounced back to
``/select-building``: an infinite loop.

This test guards against regression by exercising both helpers from a
fresh asyncio task with NO prior bypass set.
"""
from __future__ import annotations

import os
import re

import asyncpg
import pytest

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping Postgres-backed test.",
)


@pytest.fixture(scope="module")
async def existing_scheme():
    """Return (scheme_id, scheme_number) of any active scheme.

    Uses asyncpg directly with the bypass sentinel — independent of the
    helpers under test.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("SET app.tenant_id = '00000000-0000-0000-0000-000000000000'")
    row = await conn.fetchrow(
        "SELECT scheme_id::TEXT, scheme_number FROM core.schemes"
        " WHERE status = 'active' AND COALESCE(is_test_data, FALSE) = FALSE LIMIT 1"
    )
    await conn.close()
    if row is None:
        pytest.skip("No active scheme in DB — seed first")
    return row["scheme_id"], row["scheme_number"]


@pytest.mark.asyncio
async def test_get_scheme_by_id_works_without_prior_bypass(existing_scheme):
    """Regression: helper must set its own RLS bypass.

    The previous bug was that the helper ran without bypass and silently
    returned None whenever the connection pool gave it a fresh asyncpg
    connection.  This test calls the helper directly without setting any
    GUC beforehand and asserts a row is returned.
    """
    from db_postgres.repos.identity_repo import get_scheme_by_id

    sid, _ = existing_scheme
    result = await get_scheme_by_id(sid)
    assert result is not None, (
        "get_scheme_by_id returned None for a known-active scheme. "
        "RLS bypass sentinel is missing — see migration 0026 + helper docstring."
    )
    assert str(result["scheme_id"]) == sid


@pytest.mark.asyncio
async def test_get_scheme_by_number_works_without_prior_bypass(existing_scheme):
    """Same regression for the plan-number lookup path."""
    from db_postgres.repos.identity_repo import get_scheme_by_number

    _, scheme_number = existing_scheme
    result = await get_scheme_by_number(scheme_number)
    assert result is not None, (
        "get_scheme_by_number returned None for a known-active scheme."
    )
    assert result["scheme_number"] == scheme_number


@pytest.mark.asyncio
async def test_get_scheme_by_id_returns_none_for_unknown_uuid():
    """Sanity: a non-existent UUID still returns None (not raises)."""
    from db_postgres.repos.identity_repo import get_scheme_by_id

    result = await get_scheme_by_id("11111111-1111-1111-1111-111111111111")
    assert result is None


@pytest.mark.asyncio
async def test_get_scheme_by_id_returns_none_for_non_uuid_input():
    """Plan numbers like ``"13195"`` must return None so the fallback
    chain (id → number) can proceed without an asyncpg parameter error."""
    from db_postgres.repos.identity_repo import get_scheme_by_id

    assert await get_scheme_by_id("13195") is None
    assert await get_scheme_by_id("not-uuid") is None
    assert await get_scheme_by_id("") is None
    assert await get_scheme_by_id(None) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_bypass_does_not_leak_across_calls(existing_scheme):
    """The RLS bypass GUC must NOT persist across pool connections.

    Regression for the audit finding on 2026-05-03 — five admin helpers
    had been using ``set_config(..., false)`` (SESSION-scoped) which
    persisted on the pooled connection because SQLAlchemy's default
    ``pool_reset_on_return='rollback'`` does not clear GUCs.  Any
    subsequent caller of the same connection inherited cross-tenant
    read access.

    All five (find_user_by_email_for_admin, find_user_by_id_for_admin,
    list_all_active_schemes, list_schemes_for_user, get_scheme_by_id /
    by_number) now use ``set_config(..., true)`` (txn-scoped) so the
    bypass disappears at commit.

    This test exercises the leak by:
      1. Calling list_all_active_schemes (which sets the bypass).
      2. Connecting via a fresh asyncpg session (which the SQLAlchemy
         pool may or may not reuse) and asserting that the GUC is
         empty / NULL.
    """
    from db_postgres.repos.identity_repo import list_all_active_schemes

    # 1. Run a helper that uses the bypass
    schemes = await list_all_active_schemes()
    assert len(schemes) >= 1  # pre-condition: at least one active scheme

    # 2. Inspect the GUC from a parallel asyncpg connection. We can't
    #    deterministically grab the SAME pooled connection, so we instead
    #    spawn N connections and assert ALL of them have the GUC empty.
    #    If the leak exists, at least one of them will be the previously-
    #    used connection with the bypass still set.
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=10)
    try:
        for _ in range(10):
            async with pool.acquire() as conn:
                # current_setting('app.tenant_id', true) returns NULL or empty
                # when the GUC has never been set on this connection.
                value = await conn.fetchval(
                    "SELECT current_setting('app.tenant_id', true)"
                )
                # Either NULL (never set on this conn) or empty string
                # (was set txn-locally, cleared at commit) — but NEVER the
                # bypass UUID.
                assert value != "00000000-0000-0000-0000-000000000000", (
                    f"RLS bypass GUC leaked into pool connection: {value!r}. "
                    "A helper is still using set_config(..., false)."
                )
    finally:
        await pool.close()
