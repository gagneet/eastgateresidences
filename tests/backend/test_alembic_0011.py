"""Tests: alembic migration 0011 — config tables schema.

Verifies that every table, column, index, and SQL function specified in
migration 0011 actually exists in the strataos PostgreSQL database.

Requires DATABASE_URL env var (or backend/.env).  Skipped if absent.
"""
from __future__ import annotations

import os
import sys
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
    reason="DATABASE_URL not set — skipping Postgres schema tests",
)


@pytest.fixture(scope="module")
async def pg():
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    yield conn
    await conn.close()


# ── core.feature_toggles ───────────────────────────────────────────────────

async def test_feature_toggles_columns(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'feature_toggles'
                          """)
    cols = {r["column_name"] for r in rows}
    required = {
        "toggle_id", "feature_key", "feature_name",
        "description", "category", "is_enabled",
        "routes", "allowed_roles", "depends_on",
        "seeded_version", "last_modified_by", "last_modified_at", "created_at",
    }
    assert required <= cols


async def test_feature_toggles_unique_feature_key(pg) -> None:
    """feature_key must be unique (the UNIQUE constraint added in 0011)."""
    idx_rows = await pg.fetch("""
                              SELECT indexname, indexdef
                              FROM pg_indexes
                              WHERE schemaname = 'core'
                                AND tablename = 'feature_toggles'
                                AND indexdef LIKE '%UNIQUE%'
                              """)
    idx_defs = " ".join(r["indexdef"] for r in idx_rows)
    assert "feature_key" in idx_defs, "No unique index on core.feature_toggles.feature_key"


# ── core.feature_toggle_overrides ─────────────────────────────────────────

async def test_feature_toggle_overrides_columns(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'feature_toggle_overrides'
                          """)
    cols = {r["column_name"] for r in rows}
    required = {
        "override_id", "tenant_id", "scheme_id",
        "feature_key", "is_enabled", "set_by", "set_at", "reason",
    }
    assert required <= cols


async def test_feature_toggle_overrides_unique_scheme_feature(pg) -> None:
    """UNIQUE (scheme_id, feature_key) — one override per feature per scheme."""
    rows = await pg.fetch("""
                          SELECT indexname, indexdef
                          FROM pg_indexes
                          WHERE schemaname = 'core'
                            AND tablename = 'feature_toggle_overrides'
                            AND indexdef LIKE '%UNIQUE%'
                          """)
    combined = " ".join(r["indexdef"] for r in rows)
    assert "scheme_id" in combined and "feature_key" in combined, (
        f"Missing UNIQUE(scheme_id, feature_key) on feature_toggle_overrides: {combined}"
    )


# ── core.building_settings ─────────────────────────────────────────────────

async def test_building_settings_columns(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'building_settings'
                          """)
    cols = {r["column_name"] for r in rows}
    required = {
        "setting_id", "tenant_id", "scheme_id",
        "setting_key", "setting_value", "set_by", "set_at",
    }
    assert required <= cols


async def test_building_settings_value_is_jsonb(pg) -> None:
    dtype = await pg.fetchval("""
                              SELECT data_type
                              FROM information_schema.columns
                              WHERE table_schema = 'core'
                                AND table_name = 'building_settings'
                                AND column_name = 'setting_value'
                              """)
    assert dtype == "jsonb", f"Expected jsonb, got {dtype}"


# ── core.role_permissions ──────────────────────────────────────────────────

async def test_role_permissions_columns(pg) -> None:
    rows = await pg.fetch("""
                          SELECT column_name
                          FROM information_schema.columns
                          WHERE table_schema = 'core'
                            AND table_name = 'role_permissions'
                          """)
    cols = {r["column_name"] for r in rows}
    assert {"role", "permission_key", "is_granted"} <= cols


async def test_role_permissions_composite_pk(pg) -> None:
    """Primary key must be (role, permission_key)."""
    pk_cols = await pg.fetch("""
                             SELECT kcu.column_name
                             FROM information_schema.table_constraints tc
                                      JOIN information_schema.key_column_usage kcu
                                           ON tc.constraint_name = kcu.constraint_name
                                               AND tc.table_schema = kcu.table_schema
                             WHERE tc.constraint_type = 'PRIMARY KEY'
                               AND tc.table_schema = 'core'
                               AND tc.table_name = 'role_permissions'
                             """)
    pk = {r["column_name"] for r in pk_cols}
    assert pk == {"role", "permission_key"}, f"Unexpected PK: {pk}"


# ── core.feature_toggle_resolved() ────────────────────────────────────────

async def test_feature_toggle_resolved_function_exists(pg) -> None:
    count = await pg.fetchval("""
                              SELECT count(*)
                              FROM information_schema.routines
                              WHERE routine_schema = 'core'
                                AND routine_name = 'feature_toggle_resolved'
                              """)
    assert count >= 1, "core.feature_toggle_resolved function not found"


async def test_feature_toggle_resolved_returns_false_for_unknown(pg) -> None:
    """Unknown feature key → FALSE (COALESCE(NULL, false))."""
    import uuid
    result = await pg.fetchval(
        "SELECT core.feature_toggle_resolved($1::uuid, $2)",
        str(uuid.uuid4()),
        "nonexistent.feature.key.xyz",
    )
    assert result is False


# ── Alembic revision ────────────────────────────────────────────────────────

async def test_alembic_version_is_at_least_0011(pg) -> None:
    rev = await pg.fetchval(
        "SELECT version_num FROM core.alembic_version LIMIT 1"
    )
    # Revision is either '0011' or higher (e.g. '0012' after Phase B)
    assert rev >= "0011", f"Expected revision >= 0011, got {rev!r}"
