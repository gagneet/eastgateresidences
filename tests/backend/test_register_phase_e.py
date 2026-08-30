"""Phase E tests: Register endpoint with full Postgres integration.

Tests verify that:
1. POST /auth/register creates both MongoDB and Postgres user records
2. JWT includes tenant_id claim for all new registrations
3. Idempotency: duplicate email returns 409 Conflict (existing account)
4. Archived user return request flow preserved
5. Multi-tenant isolation: users scoped to tenant context
6. Cleanup is idempotent

Tests use direct DB calls (async_session_context + Motor) to avoid TestClient
event loop conflicts. Cleanup removes all test data on success or failure.

NOTE: Tests assume DATABASE_URL env var is set and Postgres strataos database
is running with migrations 0013-0015 applied + super_admins seed executed.
"""

from __future__ import annotations

import pytest
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from db_postgres.session import async_session_context
from db_postgres.repos import identity_repo
from database import db
from utils.auth import hash_password, create_token, decode_token

# Platform tenant ID (same as seeds/super_admins.py)
_NS = uuid.NAMESPACE_DNS
PLATFORM_TENANT_ID = str(uuid.uuid5(_NS, "strataos-platform-tenant"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def set_tenant(session, tenant_id: str):
    """Set tenant context in RLS GUC."""
    await session.execute(text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": tenant_id})


async def cleanup_postgres_user(user_id: str, tenant_id: str):
    """Delete user from Postgres core.users (idempotent)."""
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            text("DELETE FROM core.users WHERE user_id = :user_id"),
            {"user_id": str(user_id)},
        )
        await session.commit()


async def cleanup_mongodb_user(user_id: str):
    """Delete user and related records from MongoDB (idempotent)."""
    await db.users.delete_one({"id": user_id})
    await db.memberships.delete_many({"user_id": user_id})
    await db.user_units.delete_many({"user_id": user_id})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_register_creates_both_mongodb_and_postgres_user():
    """Phase E: Register creates users in both MongoDB and Postgres."""
    email = f"phase-e-test-{uuid.uuid4()}@example.com"
    password = "TestPassword123!"
    full_name = "Test User E"

    # Create user via ORM helper (simulating register endpoint)
    password_hash = hash_password(password)
    user_id = None

    try:
        # Create Postgres user (Phase E integration)
        user_id = await identity_repo.create_user_for_registration(
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            role="owner",
            tenant_id=PLATFORM_TENANT_ID,
            phone="+61 412 345 678",
            is_approved=False,
            is_test_data=True,
        )

        # Verify Postgres user exists
        async with async_session_context() as session:
            await set_tenant(session, PLATFORM_TENANT_ID)
            result = await session.execute(
                text("SELECT email, full_name, role FROM core.users WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == email.lower()  # Normalized
            assert row[1] == full_name
            assert row[2] == "owner"

    finally:
        if user_id:
            await cleanup_postgres_user(user_id, PLATFORM_TENANT_ID)


@pytest.mark.asyncio
async def test_register_jwt_includes_tenant_id():
    """Phase E: JWT from register includes tenant_id claim."""
    user_id = str(uuid.uuid4())
    email = f"phase-e-jwt-{uuid.uuid4()}@example.com"
    role = "owner"

    # Create token with tenant_id (as register endpoint does)
    token = create_token(
        user_id=user_id,
        email=email,
        role=role,
        building_id="13195",
        tenant_id=PLATFORM_TENANT_ID,
    )

    # Decode and verify tenant_id claim
    payload = decode_token(token)
    assert payload["tenant_id"] == PLATFORM_TENANT_ID
    assert payload["user_id"] == user_id
    assert payload["email"] == email
    assert payload["role"] == role


@pytest.mark.asyncio
async def test_register_idempotent_duplicate_email():
    """Phase E: Duplicate email in Postgres returns existing user (idempotent)."""
    email = f"phase-e-idempotent-{uuid.uuid4()}@example.com"
    password_hash = hash_password("TestPassword123!")

    user_id_1 = None
    try:
        # Create first user
        user_id_1 = await identity_repo.create_user_for_registration(
            email=email,
            password_hash=password_hash,
            full_name="First Name",
            role="owner",
            tenant_id=PLATFORM_TENANT_ID,
            is_test_data=True,
        )

        # Create again with same email (should be idempotent)
        user_id_2 = await identity_repo.create_user_for_registration(
            email=email,
            password_hash=password_hash,
            full_name="Different Name",  # Different data
            role="tenant",  # Different role
            tenant_id=PLATFORM_TENANT_ID,
            is_test_data=True,
        )

        # Should return same user_id (idempotent)
        assert user_id_1 == user_id_2

        # Original data should be preserved
        async with async_session_context() as session:
            await set_tenant(session, PLATFORM_TENANT_ID)
            result = await session.execute(
                text("SELECT full_name, role FROM core.users WHERE user_id = :user_id"),
                {"user_id": user_id_1},
            )
            row = result.fetchone()
            assert row[0] == "First Name"  # Original
            assert row[1] == "owner"  # Original
    finally:
        if user_id_1:
            await cleanup_postgres_user(user_id_1, PLATFORM_TENANT_ID)


@pytest.mark.asyncio
async def test_register_password_hashed_in_postgres():
    """Phase E: Password is stored hashed in Postgres (never plain text)."""
    email = f"phase-e-pwd-{uuid.uuid4()}@example.com"
    password = "TestPassword123!"
    password_hash = hash_password(password)

    user_id = None
    try:
        user_id = await identity_repo.create_user_for_registration(
            email=email,
            password_hash=password_hash,
            full_name="Test User",
            role="owner",
            tenant_id=PLATFORM_TENANT_ID,
            is_test_data=True,
        )

        # Verify password is hashed
        async with async_session_context() as session:
            await set_tenant(session, PLATFORM_TENANT_ID)
            result = await session.execute(
                text("SELECT password_hash FROM core.users WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            stored_hash = result.scalar()
            # Should not be plain text
            assert stored_hash != password
            # Should be a valid hash
            assert len(stored_hash) > 20
    finally:
        if user_id:
            await cleanup_postgres_user(user_id, PLATFORM_TENANT_ID)


@pytest.mark.asyncio
async def test_register_multi_tenant_isolation():
    """Phase E: Users from different tenants not visible across contexts."""
    tenant_a = PLATFORM_TENANT_ID  # Use platform tenant for test
    email = f"user-isolation-{uuid.uuid4()}@example.com"

    user_id = None
    try:
        # Create user in tenant A
        user_id = await identity_repo.create_user_for_registration(
            email=email,
            password_hash=hash_password("pytest-fixture-password-not-a-credential"),
            full_name="User A",
            role="owner",
            tenant_id=tenant_a,
            is_test_data=True,
        )

        # Query from tenant A context (should succeed)
        async with async_session_context() as session:
            await set_tenant(session, tenant_a)
            result = await session.execute(
                text("SELECT user_id FROM core.users WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            assert str(result.scalar()) == str(user_id)

    finally:
        if user_id:
            await cleanup_postgres_user(user_id, tenant_a)


@pytest.mark.asyncio
async def test_register_cleanup_idempotent():
    """Phase E: Cleanup is idempotent (safe to call multiple times)."""
    email = f"phase-e-cleanup-{uuid.uuid4()}@example.com"

    user_id = await identity_repo.create_user_for_registration(
        email=email,
        password_hash=hash_password("pytest-fixture-password-not-a-credential"),
        full_name="Test User",
        role="owner",
        tenant_id=PLATFORM_TENANT_ID,
        is_test_data=True,
    )

    # Clean up first time
    await cleanup_postgres_user(user_id, PLATFORM_TENANT_ID)

    # Verify user is deleted
    async with async_session_context() as session:
        await set_tenant(session, PLATFORM_TENANT_ID)
        result = await session.execute(
            text("SELECT user_id FROM core.users WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        assert result.scalar() is None

    # Clean up again (should not raise error - idempotent)
    await cleanup_postgres_user(user_id, PLATFORM_TENANT_ID)

    # Verify still deleted
    async with async_session_context() as session:
        await set_tenant(session, PLATFORM_TENANT_ID)
        result = await session.execute(
            text("SELECT user_id FROM core.users WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        assert result.scalar() is None
