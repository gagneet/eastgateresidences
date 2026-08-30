"""Tests for the Postgres identity repository.

Tests cover:
- Super admin lookup via find_user_for_auth (SECURITY DEFINER bypasses RLS)
- get_user_by_id with correct tenant context
- User creation + role assignment
- update_last_login
- Invitation lifecycle: create → find_by_token → claim

All tests are idempotent and clean up after themselves.
Requires a live Postgres connection (DATABASE_URL env var).
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
import asyncpg

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)

from db_postgres.repos.identity_repo import (
    find_user_for_auth,
    get_user_by_id,
    create_user,
    add_role_assignment,
    update_last_login,
    create_invitation,
    find_invitation_by_token,
    claim_invitation,
)
from db_postgres.session import async_session_context

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping Postgres identity repo tests.",
)

# Platform tenant created by super_admins seed
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
PLATFORM_TENANT_ID = str(uuid.uuid5(_NS, "strataos-platform-tenant"))

SA_EMAIL = "gagneet@silverfoxtechnologies.com.au"


# ──────────────────────────────────────────────────────────────────────────────
# find_user_for_auth
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_user_for_auth_returns_super_admin():
    """Must find the seeded super admin by email without tenant context."""
    user = await find_user_for_auth(SA_EMAIL)
    assert user is not None, f"Super admin {SA_EMAIL!r} not found"
    assert user["email"] == SA_EMAIL
    assert user["role"] == "super_admin"
    assert user["is_active"] is True
    assert user["is_approved"] is True
    assert "tenant_id" in user
    assert "id" in user


@pytest.mark.asyncio
async def test_find_user_for_auth_unknown_email_returns_none():
    user = await find_user_for_auth("nobody@example.invalid")
    assert user is None


# ──────────────────────────────────────────────────────────────────────────────
# get_user_by_id
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_user_by_id_returns_super_admin():
    """Must fetch the seeded super admin by ID when tenant context is set."""
    sa = await find_user_for_auth(SA_EMAIL)
    assert sa is not None

    user = await get_user_by_id(sa["id"], sa["tenant_id"])
    assert user is not None
    assert user["email"] == SA_EMAIL
    assert user["role"] == "super_admin"


@pytest.mark.asyncio
async def test_get_user_by_id_wrong_tenant_returns_none():
    """Must not return a user when called with a different tenant context."""
    sa = await find_user_for_auth(SA_EMAIL)
    assert sa is not None

    fake_tenant = str(uuid.uuid4())
    user = await get_user_by_id(sa["id"], fake_tenant)
    assert user is None


# ──────────────────────────────────────────────────────────────────────────────
# create_user + add_role_assignment
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user_and_role_assignment():
    """Must create a user and assign a role, then clean up."""
    test_tenant_id = PLATFORM_TENANT_ID
    test_email = f"test_repo_{uuid.uuid4().hex[:8]}@example.invalid"

    created_id = await create_user(
        data={
            "email": test_email,
            "full_name": "Repo Test User",
            "first_name": "Repo",
            "last_name": "Test",
            "password_hash": "$2b$12$placeholder_hash_for_test",
            "role": "owner",
            "is_active": True,
            "is_approved": True,
            # Backstop for the explicit cleanup below: if this test errors before
            # its finally block runs, the session-end sweep still finds the row.
            "is_test_data": True,
        },
        tenant_id=test_tenant_id,
    )
    assert created_id is not None

    await add_role_assignment(
        user_id=str(created_id),
        role="owner",
        tenant_id=test_tenant_id,
        scheme_id=None,
    )

    # Verify
    user = await get_user_by_id(str(created_id), test_tenant_id)
    assert user is not None
    assert user["email"] == test_email

    # Cleanup
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{test_tenant_id}'")
            await conn.execute(
                "DELETE FROM core.user_role_assignments WHERE user_id = $1",
                uuid.UUID(str(created_id)),
            )
            await conn.execute(
                "DELETE FROM core.users WHERE user_id = $1",
                uuid.UUID(str(created_id)),
            )
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# update_last_login
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_last_login_sets_ip():
    """Must update last_login_at and last_login_ip for an existing user."""
    sa = await find_user_for_auth(SA_EMAIL)
    assert sa is not None

    await update_last_login(sa["id"], sa["tenant_id"], ip="127.0.0.1")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{sa['tenant_id']}'")
            row = await conn.fetchrow(
                "SELECT last_login_ip FROM core.users WHERE user_id = $1",
                uuid.UUID(sa["id"]),
            )
        assert row is not None
        assert row["last_login_ip"] == "127.0.0.1"
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Invitation lifecycle
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invitation_lifecycle():
    """Full invitation flow: create → find → claim → verify claimed."""
    tenant_id = PLATFORM_TENANT_ID
    sa = await find_user_for_auth(SA_EMAIL)
    assert sa is not None

    test_email = f"invite_test_{uuid.uuid4().hex[:8]}@example.invalid"

    from datetime import timedelta
    invitation_id, raw_token = await create_invitation(
        tenant_id=tenant_id,
        email=test_email,
        invited_role="owner",
        scheme_id=None,
        invited_by=sa["id"],
        ttl_hours=168,  # 7 days
        prefill={"first_name": "Invite", "last_name": "Test"},
    )
    assert invitation_id is not None

    # Find by raw token
    found = await find_invitation_by_token(raw_token)
    assert found is not None
    assert str(found["email"]) == test_email
    assert str(found["invited_role"]) == "owner"

    # Claim the invitation — create a stub user first
    test_user_id = await create_user(
        data={
            "email": test_email,
            "full_name": "Invite Test",
            "first_name": "Invite",
            "last_name": "Test",
            "password_hash": "$2b$12$placeholder_hash_for_test",
            "role": "owner",
            "is_active": True,
            "is_approved": True,
            # Backstop for the explicit cleanup below — see above.
            "is_test_data": True,
        },
        tenant_id=tenant_id,
    )

    await claim_invitation(invitation_id, str(test_user_id), tenant_id)

    # Verify token is consumed (cannot be found again)
    found_after = await find_invitation_by_token(raw_token)
    assert found_after is None, "Token should be un-findable after claim"

    # Cleanup (delete invitations first due to FK dependency on users)
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            await conn.execute(
                "DELETE FROM core.user_invitations WHERE invitation_id = $1",
                uuid.UUID(invitation_id),
            )
            await conn.execute(
                "DELETE FROM core.user_role_assignments WHERE user_id = $1",
                uuid.UUID(str(test_user_id)),
            )
            await conn.execute(
                "DELETE FROM core.users WHERE user_id = $1",
                uuid.UUID(str(test_user_id)),
            )
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# update_user_profile — dual-write path (BUG-1 fix)
# ──────────────────────────────────────────────────────────────────────────────

from db_postgres.repos.identity_repo import update_user_profile


@pytest.mark.asyncio
async def test_update_user_profile_updates_existing_user():
    """Dual-write: update_user_profile must write full_name + phone to core.users."""
    from db_postgres.repos.identity_repo import create_user
    tenant_id = PLATFORM_TENANT_ID
    unique_email = f"test-profile-update-{uuid.uuid4().hex[:8]}@test.strataos.local"

    # Create a user to update
    user_id = await create_user(
        {
            "email": unique_email,
            "full_name": "Old Name",
            "password_hash": "x",
            "role": "owner",
            "is_approved": False,
            "default_scheme_id": None,
            "is_test_data": True,
        },
        tenant_id,
    )
    assert user_id

    try:
        # Apply profile update
        updated = await update_user_profile(
            user_id,
            {"full_name": "New Full Name", "phone": "0400 000 001"},
        )
        assert updated is True, "update_user_profile must return True for an existing user"

        # Verify the change landed in Postgres
        found = await get_user_by_id(user_id, tenant_id)
        assert found is not None
        assert found["full_name"] == "New Full Name"

    finally:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(
                "DELETE FROM core.users WHERE user_id = $1",
                uuid.UUID(str(user_id)),
            )
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_update_user_profile_non_uuid_id_returns_false():
    """Legacy Mongo string IDs are not UUIDs — must return False, not raise."""
    result = await update_user_profile("mongo-legacy-string-id", {"full_name": "X"})
    assert result is False


@pytest.mark.asyncio
async def test_update_user_profile_unknown_uuid_returns_false():
    """A valid UUID that doesn't exist in core.users must return False, not raise."""
    result = await update_user_profile(str(uuid.uuid4()), {"full_name": "Ghost"})
    assert result is False


@pytest.mark.asyncio
async def test_update_user_profile_empty_fields_returns_false():
    """No whitelisted fields → nothing to update → return False immediately."""
    result = await update_user_profile(str(uuid.uuid4()), {"updated_at": "2026-01-01"})
    assert result is False
