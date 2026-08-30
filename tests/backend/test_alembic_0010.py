"""Tests: alembic migration 0010 — identity tables schema.

Verifies that every table, column, index, and constraint specified in
migration 0010 actually exists in the strataos PostgreSQL database.

Requires DATABASE_URL env var (or backend/.env).  Does NOT require a running
backend server.  Skipped if DATABASE_URL is absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Allow imports from backend/
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ── Helpers ────────────────────────────────────────────────────────────────

def _pg_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        env_file = _BACKEND / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if url:
        return url.replace("postgresql+asyncpg://", "postgresql://")
    return None


_PG_URL = _pg_url()
pytestmark = pytest.mark.skipif(
    _PG_URL is None,
    reason="DATABASE_URL not set — skipping Postgres schema tests",
)


@pytest.fixture(scope="module")
async def pg():
    """Async asyncpg connection for the whole module."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    yield conn
    await conn.close()


# ── core.user_role enum ────────────────────────────────────────────────────

async def test_user_role_enum_values(pg) -> None:
    rows = await pg.fetch("""
                          SELECT e.enumlabel
                          FROM pg_enum e
                                   JOIN pg_type t ON e.enumtypid = t.oid
                                   JOIN pg_namespace n ON t.typnamespace = n.oid
                          WHERE n.nspname = 'core'
                            AND t.typname = 'user_role'
                          ORDER BY e.enumsortorder
                          """)
    values = {r["enumlabel"] for r in rows}
    expected = {
        "super_admin", "strata_admin", "strata_manager", "ec_member",
        "admin_staff", "real_estate_agent", "service_provider",
        "owner", "tenant", "guest",
    }
    assert expected == values, f"Unexpected enum values: {values ^ expected}"
    # Confirm legacy 'chairman' is NOT present (it's an ec_position now)
    assert "chairman" not in values


# ── core.users columns ─────────────────────────────────────────────────────

async def test_users_columns_exist(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'users'
                          """)
    cols = {r["column_name"] for r in rows}
    required = {
        "user_id", "tenant_id", "party_id",
        "email",  # renamed from login_email in 0010
        "full_name",  # renamed from display_name in 0010
        "first_name", "last_name",
        "phone",
        "password_hash",
        "auth_subject", "mfa_required",
        "totp_secret_encrypted", "totp_enabled",
        "status", "is_active",
        "is_approved", "approved_at", "approved_by", "owner_approved_at",
        "is_name_flagged", "flag_reason",
        "role", "effective_role", "default_scheme_id",
        "permission_overrides",
        "is_test_data", "last_login_at",
        "created_at", "updated_at",
    }
    missing = required - cols
    assert not missing, f"Missing columns on core.users: {missing}"

    # login_email must NOT exist (was renamed to email)
    assert "login_email" not in cols
    assert "display_name" not in cols


# ── core.user_units ────────────────────────────────────────────────────────

async def test_user_units_table_exists(pg) -> None:
    exists = await pg.fetchval("""
                               SELECT count(*)
                               FROM information_schema.tables
                               WHERE table_schema = 'core'
                                 AND table_name = 'user_units'
                               """)
    assert exists == 1


async def test_user_units_columns(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'user_units'
                          """)
    cols = {r["column_name"] for r in rows}
    required = {
        "user_unit_id", "tenant_id", "user_id", "scheme_id",
        "lot_id", "party_id", "relationship",
        "valid_from", "valid_to", "is_test_data", "created_at",
    }
    assert required <= cols


# ── core.user_role_assignments ─────────────────────────────────────────────

async def test_user_role_assignments_columns(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'user_role_assignments'
                          """)
    cols = {r["column_name"] for r in rows}
    required = {
        "assignment_id", "tenant_id", "user_id", "scheme_id",
        "role", "ec_position",
        "granted_by", "granted_at",
        "expires_at",  # non-NULL = temp elevation
        "is_active",
    }
    assert required <= cols


async def test_user_role_assignments_partial_indexes(pg) -> None:
    """Two partial unique indexes must exist instead of a single table constraint."""
    rows = await pg.fetch("""
                          SELECT indexname
                          FROM pg_indexes
                          WHERE schemaname = 'core'
                            AND tablename = 'user_role_assignments'
                          """)
    idx_names = {r["indexname"] for r in rows}
    assert "ura_user_scheme_role_unique" in idx_names, f"Missing scheme-scoped partial index: {idx_names}"
    assert "ura_user_global_role_unique" in idx_names, f"Missing global partial index: {idx_names}"


# ── core.user_sessions ─────────────────────────────────────────────────────

async def test_user_sessions_columns(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'user_sessions'
                          """)
    cols = {r["column_name"] for r in rows}
    required = {
        "session_id", "tenant_id", "user_id",
        "jwt_jti",  # unique JWT ID claim
        "issued_at", "expires_at", "revoked_at", "last_activity_at",
        "ip_address", "user_agent",
    }
    assert required <= cols


# ── core.user_invitations ──────────────────────────────────────────────────

async def test_user_invitations_columns(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'user_invitations'
                          """)
    cols = {r["column_name"] for r in rows}
    required = {
        "invitation_id", "tenant_id", "scheme_id",
        "email", "invited_role", "invited_by", "invited_at",
        "claim_token_sha256", "expires_at",
        "claimed_at", "claimed_user_id",
        "cancelled_at", "cancelled_by",
    }
    assert required <= cols


# ── core.user_effective_role() SQL function ────────────────────────────────

async def test_user_effective_role_function_exists(pg) -> None:
    exists = await pg.fetchval("""
                               SELECT count(*)
                               FROM information_schema.routines
                               WHERE routine_schema = 'core'
                                 AND routine_name = 'user_effective_role'
                               """)
    assert exists >= 1, "core.user_effective_role function not found"
