"""Tests: core.user_effective_role() SQL function.

Tests the 4-level resolution logic:
  1. Active temp-elevation (expires_at in future)
  2. Permanent active assignment for the specific scheme
  3. Permanent global assignment (scheme_id IS NULL)
  4. Fallback to user's base role column

Uses real DB insert/cleanup via ROLLBACK (no persistent test data).
Requires DATABASE_URL env var (or backend/.env).  Skipped if absent.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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


# ── Fixture: isolated DB connection + seed helper ─────────────────────────

@pytest.fixture(scope="module")
async def pg():
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    yield conn
    await conn.close()


async def _seed_user(conn, *, tenant_id: str, scheme_id: str) -> str:
    """Insert a minimal user row. Returns user_id."""
    user_id = str(uuid.uuid4())
    email = f"test-fn-{user_id[:8]}@example.invalid"
    await conn.execute(
        """
        INSERT INTO core.users
        (user_id, tenant_id, email, full_name, password_hash,
         role, is_active, is_approved, mfa_required, totp_enabled,
         is_name_flagged, is_test_data, permission_overrides,
         created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5,
                'owner', true, true, false, false,
                false, true, '{}'::jsonb,
                now(), now())
        """,
        user_id, tenant_id, email, "Test Function User", "",
    )
    return user_id


async def _seed_assignment(
        conn,
        *,
        tenant_id: str,
        user_id: str,
        scheme_id: str | None,
        role: str,
        expires_at: datetime | None = None,
        is_active: bool = True,
) -> str:
    """Insert a role assignment row. Returns assignment_id."""
    assignment_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO core.user_role_assignments
        (assignment_id, tenant_id, user_id, scheme_id, role,
         granted_at, expires_at, is_active)
        VALUES ($1, $2, $3, $4, $5, now(), $6, $7)
        """,
        assignment_id, tenant_id, user_id, scheme_id, role, expires_at, is_active,
    )
    return assignment_id


# ── Helper: resolve via function ───────────────────────────────────────────

async def _resolve(conn, user_id: str, scheme_id: str) -> str | None:
    return await conn.fetchval(
        "SELECT core.user_effective_role($1::uuid, $2::uuid)::text",
        user_id, scheme_id,
    )


# ── Tests ─────────────────────────────────────────────────────────────────

async def test_fallback_to_base_role_with_no_assignments(pg) -> None:
    """User with no assignments returns their core.users.role column value."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            tenant_id = str(uuid.uuid4())
            scheme_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_fn_fallback",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "FN001", "Test Scheme FN001",
            )
            user_id = await _seed_user(conn, tenant_id=tenant_id, scheme_id=scheme_id)

            role = await _resolve(conn, user_id, scheme_id)
            assert role == "owner", f"Expected 'owner' (base role), got {role!r}"

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_permanent_scheme_assignment(pg) -> None:
    """User with a scheme-level 'ec_member' assignment returns 'ec_member'."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            tenant_id = str(uuid.uuid4())
            scheme_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_fn_scheme",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "FN002", "Test Scheme FN002",
            )
            user_id = await _seed_user(conn, tenant_id=tenant_id, scheme_id=scheme_id)
            await _seed_assignment(conn, tenant_id=tenant_id, user_id=user_id, scheme_id=scheme_id, role="ec_member")

            role = await _resolve(conn, user_id, scheme_id)
            assert role == "ec_member", f"Expected ec_member, got {role!r}"

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_global_assignment(pg) -> None:
    """Super admin has a global (scheme_id=NULL) assignment → returned for any scheme."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            tenant_id = str(uuid.uuid4())
            scheme_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_fn_global",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "FN003", "Test Scheme FN003",
            )
            user_id = await _seed_user(conn, tenant_id=tenant_id, scheme_id=scheme_id)
            await _seed_assignment(conn, tenant_id=tenant_id, user_id=user_id, scheme_id=None, role="super_admin")

            role = await _resolve(conn, user_id, scheme_id)
            assert role == "super_admin", f"Expected super_admin, got {role!r}"

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_temp_elevation_overrides_permanent(pg) -> None:
    """Active temp elevation (expires_at in future) wins over a permanent assignment."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            tenant_id = str(uuid.uuid4())
            scheme_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_fn_elevation",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "FN004", "Test Scheme FN004",
            )
            user_id = await _seed_user(conn, tenant_id=tenant_id, scheme_id=scheme_id)

            # Permanent scheme assignment: owner
            await _seed_assignment(conn, tenant_id=tenant_id, user_id=user_id, scheme_id=scheme_id, role="owner")

            # Temp elevation: ec_member, expires 2 hours from now
            future = datetime.now(timezone.utc) + timedelta(hours=2)
            # Temp elevations get a different role (not matching unique index) — use building_admin
            await _seed_assignment(
                conn,
                tenant_id=tenant_id, user_id=user_id, scheme_id=scheme_id,
                role="strata_admin", expires_at=future,
            )

            role = await _resolve(conn, user_id, scheme_id)
            assert role == "strata_admin", (
                f"Temp elevation (building_admin) should win over permanent (owner), got {role!r}"
            )

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_expired_temp_elevation_falls_through(pg) -> None:
    """Expired temp elevation is ignored; falls back to permanent assignment."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            tenant_id = str(uuid.uuid4())
            scheme_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_fn_expired",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "FN005", "Test Scheme FN005",
            )
            user_id = await _seed_user(conn, tenant_id=tenant_id, scheme_id=scheme_id)

            # Permanent scheme assignment
            await _seed_assignment(conn, tenant_id=tenant_id, user_id=user_id, scheme_id=scheme_id,
                                   role="strata_manager")

            # Expired temp elevation (expires in the past)
            past = datetime.now(timezone.utc) - timedelta(hours=1)
            await _seed_assignment(
                conn,
                tenant_id=tenant_id, user_id=user_id, scheme_id=scheme_id,
                role="super_admin", expires_at=past,
            )

            role = await _resolve(conn, user_id, scheme_id)
            assert role == "strata_manager", (
                f"Expired elevation should fall through to strata_manager, got {role!r}"
            )

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_inactive_assignment_ignored(pg) -> None:
    """Inactive (is_active=false) assignment is not returned."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        async with conn.transaction():
            tenant_id = str(uuid.uuid4())
            scheme_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_fn_inactive",
            )
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "FN006", "Test Scheme FN006",
            )
            user_id = await _seed_user(conn, tenant_id=tenant_id, scheme_id=scheme_id)
            await _seed_assignment(
                conn, tenant_id=tenant_id, user_id=user_id, scheme_id=scheme_id,
                role="ec_member", is_active=False,
            )

            # User base role is 'owner'; no active assignment → should return 'owner'
            role = await _resolve(conn, user_id, scheme_id)
            assert role == "owner", f"Inactive assignment must be ignored, got {role!r}"

            raise Exception("ROLLBACK")
    except Exception as e:
        if "ROLLBACK" not in str(e):
            raise
    finally:
        await conn.close()


async def test_unknown_user_returns_guest(pg) -> None:
    """Non-existent user_id returns 'guest' (COALESCE fallback)."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        role = await conn.fetchval(
            "SELECT core.user_effective_role($1::uuid, $2::uuid)::text",
            str(uuid.uuid4()),
            str(uuid.uuid4()),
        )
        assert role == "guest", f"Expected 'guest' for unknown user, got {role!r}"
    finally:
        await conn.close()
