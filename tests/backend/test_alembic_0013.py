"""Tests for alembic migrations 0013 and 0014.

Verifies that the migrations added the expected columns, tables, and
SQL functions to the Postgres schema.

Requires a live Postgres connection (DATABASE_URL env var).
"""
from __future__ import annotations

import os
import re
import pytest
import asyncpg

_RAW_URL = os.environ.get("DATABASE_URL", "")
# asyncpg requires postgresql:// not postgresql+asyncpg://
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping Postgres migration tests.",
)


@pytest.fixture(scope="module")
async def pg():
    conn = await asyncpg.connect(DATABASE_URL)
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_last_login_ip_column_exists(pg):
    """core.users must have a last_login_ip TEXT column."""
    row = await pg.fetchrow(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'core'
          AND table_name = 'users'
          AND column_name = 'last_login_ip'
        """
    )
    assert row is not None, "Column core.users.last_login_ip not found"
    assert row["data_type"] == "text"
    assert row["is_nullable"] == "YES"


@pytest.mark.asyncio
async def test_find_user_for_auth_function_exists(pg):
    """core.find_user_for_auth must exist as a SECURITY DEFINER function."""
    row = await pg.fetchrow(
        """
        SELECT routine_name, security_type
        FROM information_schema.routines
        WHERE routine_schema = 'core'
          AND routine_name = 'find_user_for_auth'
          AND routine_type = 'FUNCTION'
        """
    )
    assert row is not None, "Function core.find_user_for_auth not found"
    assert row["security_type"] == "DEFINER"


@pytest.mark.asyncio
async def test_find_user_for_auth_returns_no_rows_for_unknown_email(pg):
    """Calling the function with an unknown email must return no rows (not error)."""
    rows = await pg.fetch(
        "SELECT * FROM core.find_user_for_auth('nobody@example.invalid')"
    )
    assert rows == [], f"Expected empty result, got: {rows}"


@pytest.mark.asyncio
async def test_onboarding_sessions_table_exists(pg):
    """core.onboarding_sessions must exist with expected columns."""
    rows = await pg.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'core'
          AND table_name = 'onboarding_sessions'
        ORDER BY ordinal_position
        """
    )
    col_names = {r["column_name"] for r in rows}
    required = {
        "session_id", "tenant_id", "scheme_id", "initiated_by",
        "status", "current_step", "step_data", "is_test_data",
        "created_at", "updated_at",
    }
    missing = required - col_names
    assert not missing, f"Missing columns in core.onboarding_sessions: {missing}"


@pytest.mark.asyncio
async def test_onboarding_sessions_indexes_exist(pg):
    """core.onboarding_sessions must have the two expected indexes."""
    rows = await pg.fetch(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'core'
          AND tablename = 'onboarding_sessions'
        """
    )
    index_names = {r["indexname"] for r in rows}
    assert any("tenant" in n for n in index_names), \
        f"No tenant_id index found on onboarding_sessions: {index_names}"
    assert any("initiator" in n or "initiated_by" in n for n in index_names), \
        f"No initiated_by index found on onboarding_sessions: {index_names}"
