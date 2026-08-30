"""Tests: alembic migration 0012 — RLS isolation.

Verifies that Row-Level Security is active on all tenant-scoped tables and
that a row inserted for tenant A is invisible to a session using tenant B's
context — the core multi-tenant isolation guarantee.

Requires DATABASE_URL env var (or backend/.env).  Skipped if absent.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


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
    reason="DATABASE_URL not set — skipping RLS isolation tests",
)

# ── Tables expected to have RLS enabled ────────────────────────────────────

_TENANT_TABLES = [
    "users",
    "user_units",
    "user_role_assignments",
    "user_sessions",
    "user_invitations",
    "feature_toggle_overrides",
    "building_settings",
]


@pytest.fixture(scope="module")
async def pg():
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    yield conn
    await conn.close()


# ── RLS enabled checks ─────────────────────────────────────────────────────

@pytest.mark.parametrize("table", _TENANT_TABLES)
async def test_rls_enabled_on_tenant_table(pg, table: str) -> None:
    row = await pg.fetchrow("""
                            SELECT relrowsecurity, relforcerowsecurity
                            FROM pg_class c
                                     JOIN pg_namespace n ON c.relnamespace = n.oid
                            WHERE n.nspname = 'core'
                              AND c.relname = $1
                            """, table)
    assert row is not None, f"Table core.{table} not found"
    assert row["relrowsecurity"], f"RLS not enabled on core.{table}"
    assert row["relforcerowsecurity"], f"FORCE RLS not set on core.{table}"


@pytest.mark.parametrize("table", _TENANT_TABLES)
async def test_rls_policy_exists(pg, table: str) -> None:
    count = await pg.fetchval("""
                              SELECT count(*)
                              FROM pg_policies
                              WHERE schemaname = 'core'
                                AND tablename = $1
                              """, table)
    assert count >= 1, f"No RLS policy found on core.{table}"


# ── Global tables must NOT have RLS ────────────────────────────────────────

@pytest.mark.parametrize("table", ["feature_toggles", "role_permissions"])
async def test_rls_disabled_on_global_table(pg, table: str) -> None:
    row = await pg.fetchrow("""
                            SELECT relrowsecurity
                            FROM pg_class c
                                     JOIN pg_namespace n ON c.relnamespace = n.oid
                            WHERE n.nspname = 'core'
                              AND c.relname = $1
                            """, table)
    assert row is not None, f"Table core.{table} not found"
    assert not row["relrowsecurity"], (
        f"RLS should NOT be enabled on global table core.{table}"
    )


# ── Tenant isolation: building_settings ───────────────────────────────────

async def test_building_settings_rls_isolation(pg) -> None:
    """Row inserted for tenant A must be invisible to tenant B's session."""
    import asyncpg  # type: ignore

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    # We need real tenant + scheme rows to satisfy FK constraints.
    # Seed minimal parent rows inside a single connection / transaction so
    # teardown via ROLLBACK is automatic regardless of test outcome.
    conn_a = await asyncpg.connect(_PG_URL)
    conn_b = await asyncpg.connect(_PG_URL)
    try:
        async with conn_a.transaction():
            # Seed tenants + schemes for both sides so FK is satisfied.
            # Use actual column names from the DB schema (tenant_name, scheme_number).
            await conn_a.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) "
                "VALUES ($1, $2, now(), now())",
                tenant_a, "TestTenantA_rls",
            )
            await conn_a.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) "
                "VALUES ($1, $2, now(), now())",
                tenant_b, "TestTenantB_rls",
            )
            scheme_a = str(uuid.uuid4())
            scheme_b = str(uuid.uuid4())
            # core.schemes has RLS — must set tenant context before INSERT
            await conn_a.execute(f"SET LOCAL app.tenant_id = '{tenant_a}'")
            await conn_a.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_a, tenant_a, "TA001", "Test Scheme TA001",
            )
            await conn_a.execute(f"SET LOCAL app.tenant_id = '{tenant_b}'")
            await conn_a.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_b, tenant_b, "TB001", "Test Scheme TB001",
            )

            # Insert a building_setting for tenant A
            setting_id = str(uuid.uuid4())
            await conn_a.execute(
                f"SET LOCAL app.tenant_id = '{tenant_a}'"
            )
            await conn_a.execute(
                "INSERT INTO core.building_settings "
                "(setting_id, tenant_id, scheme_id, setting_key, setting_value, set_at) "
                "VALUES ($1, $2, $3, $4, $5::jsonb, now())",
                setting_id, tenant_a, scheme_a, "test.isolation.key", '"hello"',
            )

            # Tenant B's session must NOT see the row
            async with conn_b.transaction():
                await conn_b.execute(f"SET LOCAL app.tenant_id = '{tenant_b}'")
                row = await conn_b.fetchrow(
                    "SELECT * FROM core.building_settings WHERE setting_id = $1",
                    setting_id,
                )
                assert row is None, (
                    "RLS FAILURE: tenant B can read tenant A's building_settings row"
                )

            # Tenant A's own session CAN see the row
            count = await conn_a.fetchval(
                "SELECT count(*) FROM core.building_settings WHERE setting_id = $1",
                setting_id,
            )
            assert count == 1, "Tenant A cannot see its own building_settings row"

            raise Exception("ROLLBACK: test data cleanup")  # noqa: TRY301
    except Exception as exc:
        if "ROLLBACK: test data cleanup" not in str(exc):
            raise
    finally:
        await conn_a.close()
        await conn_b.close()


# ── Alembic revision ────────────────────────────────────────────────────────

async def test_alembic_version_is_current(pg) -> None:
    """Alembic revision must be at least 0015 (rls_invitation_bypass)."""
    rev = await pg.fetchval(
        "SELECT version_num FROM core.alembic_version LIMIT 1"
    )
    # Migrations applied through Phase E: 0015 = rls_invitation_bypass
    _MINIMUM_REVISION = "0015"
    assert rev is not None, "No alembic version row found in core.alembic_version"
    assert rev >= _MINIMUM_REVISION, (
        f"Expected migration >= {_MINIMUM_REVISION} (Phase E), got {rev!r}. "
        "Run: cd backend && alembic upgrade head"
    )
