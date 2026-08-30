"""Tests: SQLAlchemy ORM round-trip for identity and config models.

Verifies that:
  1. ORM models can INSERT rows into the DB
  2. The same rows can be SELECTed back
  3. Values survive the round-trip unchanged
  4. RLS tenant isolation works through the ORM session (set_tenant())

Uses real DB connections with ROLLBACK for cleanup.
Requires DATABASE_URL env var (or backend/.env).  Skipped if absent.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Import both model modules so SQLAlchemy Base.metadata has both core.users
# (from models_identity) and core.feature_toggles (from models_config) registered
# before any FK resolution occurs during ORM operations.
import db_postgres.models_identity  # noqa: F401,E402
import db_postgres.models_config  # noqa: F401,E402


def _pg_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        env_file = _BACKEND / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return url  # keep the +asyncpg:// form for SQLAlchemy


_PG_URL = _pg_url()
pytestmark = pytest.mark.skipif(
    _PG_URL is None,
    reason="DATABASE_URL not set — skipping ORM round-trip tests",
)


# ── Engine / session helpers ───────────────────────────────────────────────

def _make_engine():
    from sqlalchemy.ext.asyncio import create_async_engine
    return create_async_engine(_PG_URL, echo=False)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    e = _make_engine()
    yield e


@pytest.fixture(scope="module")
async def raw_pg():
    """asyncpg connection for seeding parent rows (bypasses SQLAlchemy)."""
    import asyncpg  # type: ignore
    pg_url = _PG_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(pg_url)
    yield conn
    await conn.close()


# ── FeatureToggle ORM round-trip (global table, no RLS) ────────────────────

async def test_feature_toggle_orm_insert_fetch(engine) -> None:
    """FeatureToggle: insert via ORM, fetch back, verify fields."""
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from db_postgres.models_config import FeatureToggle

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    key = f"orm.test.toggle.{uuid.uuid4().hex[:10]}"
    toggle_id = str(uuid.uuid4())

    async with factory() as session:
        async with session.begin():
            ft = FeatureToggle(
                toggle_id=toggle_id,
                feature_key=key,
                feature_name="ORM Test Toggle",
                category="test",
                is_enabled=True,
                routes=["/test"],
                allowed_roles=["super_admin"],
                depends_on=[],
                seeded_version=99,
                last_modified_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
            session.add(ft)

        # Fetch back in a new transaction
        async with session.begin():
            result = await session.execute(
                select(FeatureToggle).where(FeatureToggle.toggle_id == toggle_id)
            )
            fetched = result.scalar_one()

            assert fetched.feature_key == key
            assert fetched.is_enabled is True
            assert fetched.category == "test"
            assert "super_admin" in fetched.allowed_roles

            # Cleanup
            await session.delete(fetched)


# ── RolePermission ORM round-trip (global table, no RLS) ──────────────────

async def test_role_permission_orm_insert_fetch(engine) -> None:
    """RolePermission: insert via ORM, fetch back, verify fields."""
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from db_postgres.models_config import RolePermission

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    role = "super_admin"
    perm_key = f"orm.test.perm.{uuid.uuid4().hex[:8]}"

    async with factory() as session:
        async with session.begin():
            rp = RolePermission(role=role, permission_key=perm_key, is_granted=True)
            session.add(rp)

        async with session.begin():
            result = await session.execute(
                select(RolePermission).where(
                    RolePermission.role == role,
                    RolePermission.permission_key == perm_key,
                )
            )
            fetched = result.scalar_one()

            assert fetched.role == role
            assert fetched.permission_key == perm_key
            assert fetched.is_granted is True

            await session.delete(fetched)


# ── User ORM round-trip (tenant-scoped, RLS via set_tenant) ───────────────

async def test_user_orm_insert_fetch_with_rls(engine, raw_pg) -> None:
    """User: insert via ORM with tenant context, fetch back, RLS isolation verified."""
    import asyncpg  # type: ignore
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from db_postgres.models_identity import User
    from db_postgres.session import set_tenant

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    tenant_id = str(uuid.uuid4())
    scheme_id = str(uuid.uuid4())

    # Seed parent rows via raw asyncpg (outside ORM).
    # core.tenants has no RLS; core.schemes has RLS — set tenant context first.
    pg_url = _PG_URL.replace("postgresql+asyncpg://", "postgresql://")
    seed_conn = await asyncpg.connect(pg_url)
    try:
        async with seed_conn.transaction():
            await seed_conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_orm_user",
            )
            await seed_conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await seed_conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "OR001", "Test Scheme OR001",
            )
    finally:
        await seed_conn.close()

    user_id = str(uuid.uuid4())
    email = f"orm-test-{user_id[:8]}@example.invalid"

    cleanup_conn = await asyncpg.connect(pg_url)
    try:
        async with factory() as session:
            async with session.begin():
                await set_tenant(session, tenant_id)
                u = User(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    email=email,
                    full_name="ORM Test User",
                    password_hash="",
                    role="owner",
                    is_active=True,
                    is_approved=True,
                    mfa_required=False,
                    totp_enabled=False,
                    is_name_flagged=False,
                    is_test_data=True,
                    permission_overrides={},
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(u)

            # Fetch back — must set tenant context again in the new transaction
            async with session.begin():
                await set_tenant(session, tenant_id)
                result = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                fetched = result.scalar_one()
                assert fetched.email == email
                assert fetched.full_name == "ORM Test User"
                assert fetched.role == "owner"
                assert fetched.is_test_data is True

            # RLS isolation: a different tenant should NOT see the row
            other_tenant = str(uuid.uuid4())
            async with session.begin():
                await set_tenant(session, other_tenant)
                result2 = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                assert result2.scalar_one_or_none() is None, (
                    "RLS FAILURE: other tenant can see the user row"
                )

    finally:
        # Cleanup: tenant-scoped deletes need SET LOCAL inside a transaction
        async with cleanup_conn.transaction():
            await cleanup_conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await cleanup_conn.execute(
                "DELETE FROM core.users WHERE user_id = $1", user_id
            )
            await cleanup_conn.execute(
                "DELETE FROM core.schemes WHERE scheme_id = $1", scheme_id
            )
        # core.tenants is not RLS-scoped
        await cleanup_conn.execute(
            "DELETE FROM core.tenants WHERE tenant_id = $1", tenant_id
        )
        await cleanup_conn.close()


# ── BuildingSetting ORM round-trip (tenant-scoped) ────────────────────────

async def test_building_setting_orm_insert_fetch(engine) -> None:
    """BuildingSetting: insert JSONB value via ORM, fetch back, verify type."""
    import asyncpg  # type: ignore
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from db_postgres.models_config import BuildingSetting
    from db_postgres.session import set_tenant

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    pg_url = _PG_URL.replace("postgresql+asyncpg://", "postgresql://")
    tenant_id = str(uuid.uuid4())
    scheme_id = str(uuid.uuid4())
    seed_conn = await asyncpg.connect(pg_url)
    try:
        async with seed_conn.transaction():
            await seed_conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name, created_at, updated_at) VALUES ($1, $2, now(), now())",
                tenant_id, "TestTenant_orm_bs",
            )
            await seed_conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await seed_conn.execute(
                "INSERT INTO core.schemes "
                "(scheme_id, tenant_id, scheme_number, scheme_name, jurisdiction, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'ACT'::compliance.jurisdiction_code, now(), now())",
                scheme_id, tenant_id, "OR002", "Test Scheme OR002",
            )
    finally:
        await seed_conn.close()

    setting_id = str(uuid.uuid4())
    cleanup_conn = await asyncpg.connect(pg_url)
    try:
        async with factory() as session:
            async with session.begin():
                await set_tenant(session, tenant_id)
                bs = BuildingSetting(
                    setting_id=setting_id,
                    tenant_id=tenant_id,
                    scheme_id=scheme_id,
                    setting_key="orm.test.jsonb.setting",
                    setting_value={"fy_start_month": 7, "currency": "AUD"},
                    set_at=datetime.now(timezone.utc),
                )
                session.add(bs)

            async with session.begin():
                await set_tenant(session, tenant_id)
                result = await session.execute(
                    select(BuildingSetting).where(BuildingSetting.setting_id == setting_id)
                )
                fetched = result.scalar_one()
                assert fetched.setting_value["fy_start_month"] == 7
                assert fetched.setting_value["currency"] == "AUD"

    finally:
        async with cleanup_conn.transaction():
            await cleanup_conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await cleanup_conn.execute(
                "DELETE FROM core.building_settings WHERE setting_id = $1", setting_id
            )
            await cleanup_conn.execute(
                "DELETE FROM core.schemes WHERE scheme_id = $1", scheme_id
            )
        await cleanup_conn.execute(
            "DELETE FROM core.tenants WHERE tenant_id = $1", tenant_id
        )
        await cleanup_conn.close()
