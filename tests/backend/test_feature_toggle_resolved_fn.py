"""Tests: core.feature_toggle_resolved() SQL function.

Tests the 2-tier toggle resolution:
  Tier 2: scheme-level override (feature_toggle_overrides row)
  Tier 3: global default (feature_toggles row)
  First match wins.  Missing key → FALSE.

Uses real DB insert/cleanup via ROLLBACK (no persistent test data).
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
    reason="DATABASE_URL not set — skipping Postgres function tests",
)


# ── Fixture: bare connection ───────────────────────────────────────────────

@pytest.fixture(scope="module")
async def pg():
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    yield conn
    await conn.close()


# ── Helpers ────────────────────────────────────────────────────────────────

async def _resolve(conn, scheme_id: str, feature_key: str) -> bool:
    return await conn.fetchval(
        "SELECT core.feature_toggle_resolved($1::uuid, $2)",
        scheme_id, feature_key,
    )


# ── Tests ─────────────────────────────────────────────────────────────────

async def test_unknown_feature_key_returns_false(pg) -> None:
    """Non-existent feature key always resolves to FALSE (COALESCE)."""
    result = await _resolve(pg, str(uuid.uuid4()), "nonexistent.key.xyz.abc.123")
    assert result is False


async def test_global_default_true_no_override(pg) -> None:
    """Global default=TRUE with no scheme override → TRUE."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            key = f"test.toggle.global.true.{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO core.feature_toggles
                (toggle_id, feature_key, feature_name, category, is_enabled,
                 last_modified_at, created_at)
                VALUES (gen_random_uuid(), $1, $2, 'test', true, now(), now())
                """,
                key, f"Test toggle {key}",
            )
            result = await _resolve(conn, str(uuid.uuid4()), key)
            assert result is True, f"Global default=TRUE, expected True, got {result!r}"

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_global_default_false_no_override(pg) -> None:
    """Global default=FALSE with no scheme override → FALSE."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            key = f"test.toggle.global.false.{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO core.feature_toggles
                (toggle_id, feature_key, feature_name, category, is_enabled,
                 last_modified_at, created_at)
                VALUES (gen_random_uuid(), $1, $2, 'test', false, now(), now())
                """,
                key, f"Test toggle {key}",
            )
            result = await _resolve(conn, str(uuid.uuid4()), key)
            assert result is False, f"Global default=FALSE, expected False, got {result!r}"

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_scheme_override_true_beats_global_false(pg) -> None:
    """Scheme override=TRUE beats global default=FALSE."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            # Need a real tenant + scheme for FK
            tenant_id = str(uuid.uuid4())
            scheme_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_toggle_override",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "TG001", "Test Scheme TG001",
            )
            key = f"test.toggle.override.true.{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO core.feature_toggles
                (toggle_id, feature_key, feature_name, category, is_enabled,
                 last_modified_at, created_at)
                VALUES (gen_random_uuid(), $1, $2, 'test', false, now(), now())
                """,
                key, f"Test toggle {key}",
            )
            # Need a real user for set_by FK
            user_id = str(uuid.uuid4())
            email = f"toggle-test-{user_id[:8]}@example.invalid"
            await conn.execute(
                """
                INSERT INTO core.users
                (user_id, tenant_id, email, full_name, password_hash,
                 role, is_active, is_approved, mfa_required, totp_enabled,
                 is_name_flagged, is_test_data, permission_overrides,
                 created_at, updated_at)
                VALUES ($1, $2, $3, $4, '', 'super_admin', true, true, false, false,
                        false, true, '{}'::jsonb, now(), now())
                """,
                user_id, tenant_id, email, "Toggle Test User",
            )
            await conn.execute(
                """
                INSERT INTO core.feature_toggle_overrides
                (override_id, tenant_id, scheme_id, feature_key, is_enabled, set_by, set_at)
                VALUES (gen_random_uuid(), $1, $2, $3, true, $4, now())
                """,
                tenant_id, scheme_id, key, user_id,
            )

            result = await _resolve(conn, scheme_id, key)
            assert result is True, (
                f"Scheme override=TRUE should beat global default=FALSE, got {result!r}"
            )

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_scheme_override_false_beats_global_true(pg) -> None:
    """Scheme override=FALSE beats global default=TRUE."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            tenant_id = str(uuid.uuid4())
            scheme_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_toggle_disable",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "TG002", "Test Scheme TG002",
            )
            key = f"test.toggle.override.false.{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO core.feature_toggles
                (toggle_id, feature_key, feature_name, category, is_enabled,
                 last_modified_at, created_at)
                VALUES (gen_random_uuid(), $1, $2, 'test', true, now(), now())
                """,
                key, f"Test toggle {key}",
            )
            user_id = str(uuid.uuid4())
            email = f"toggle-dis-{user_id[:8]}@example.invalid"
            await conn.execute(
                """
                INSERT INTO core.users
                (user_id, tenant_id, email, full_name, password_hash,
                 role, is_active, is_approved, mfa_required, totp_enabled,
                 is_name_flagged, is_test_data, permission_overrides,
                 created_at, updated_at)
                VALUES ($1, $2, $3, $4, '', 'super_admin', true, true, false, false,
                        false, true, '{}'::jsonb, now(), now())
                """,
                user_id, tenant_id, email, "Toggle Disable User",
            )
            await conn.execute(
                """
                INSERT INTO core.feature_toggle_overrides
                (override_id, tenant_id, scheme_id, feature_key, is_enabled, set_by, set_at)
                VALUES (gen_random_uuid(), $1, $2, $3, false, $4, now())
                """,
                tenant_id, scheme_id, key, user_id,
            )

            result = await _resolve(conn, scheme_id, key)
            assert result is False, (
                f"Scheme override=FALSE should beat global default=TRUE, got {result!r}"
            )

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_other_scheme_override_does_not_affect_this_scheme(pg) -> None:
    """Override for scheme B must NOT affect scheme A (no cross-tenant bleed)."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            tenant_id = str(uuid.uuid4())
            scheme_a = str(uuid.uuid4())
            scheme_b = str(uuid.uuid4())
            # Tenant first (no RLS), then SET LOCAL, then schemes (RLS requires context)
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_toggle_isolation",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            for s, n in [(scheme_a, "TG003"), (scheme_b, "TG004")]:
                await conn.execute(
                    "INSERT INTO core.schemes "
                    "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                    "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                    s, tenant_id, n, f"Test Scheme {n}",
                )
            key = f"test.toggle.isolation.{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO core.feature_toggles
                (toggle_id, feature_key, feature_name, category, is_enabled,
                 last_modified_at, created_at)
                VALUES (gen_random_uuid(), $1, $2, 'test', false, now(), now())
                """,
                key, f"Test toggle {key}",
            )
            user_id = str(uuid.uuid4())
            email = f"toggle-iso-{user_id[:8]}@example.invalid"
            await conn.execute(
                """
                INSERT INTO core.users
                (user_id, tenant_id, email, full_name, password_hash,
                 role, is_active, is_approved, mfa_required, totp_enabled,
                 is_name_flagged, is_test_data, permission_overrides,
                 created_at, updated_at)
                VALUES ($1, $2, $3, $4, '', 'super_admin', true, true, false, false,
                        false, true, '{}'::jsonb, now(), now())
                """,
                user_id, tenant_id, email, "Isolation User",
            )
            # Only scheme B gets an override=TRUE
            await conn.execute(
                """
                INSERT INTO core.feature_toggle_overrides
                (override_id, tenant_id, scheme_id, feature_key, is_enabled, set_by, set_at)
                VALUES (gen_random_uuid(), $1, $2, $3, true, $4, now())
                """,
                tenant_id, scheme_b, key, user_id,
            )

            # Scheme A should still see global default=FALSE
            result_a = await _resolve(conn, scheme_a, key)
            result_b = await _resolve(conn, scheme_b, key)

            assert result_a is False, f"Scheme A should see global default=FALSE, got {result_a!r}"
            assert result_b is True, f"Scheme B should see its override=TRUE, got {result_b!r}"

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()
