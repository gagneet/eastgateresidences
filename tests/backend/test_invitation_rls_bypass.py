"""Phase E tests: Invitation RLS bypass sentinel (migration 0015).

Tests verify that:
1. Invitations can be looked up by token WITHOUT knowing the tenant_id
   (bypass sentinel UUID = 00000000-0000-0000-0000-000000000000)
2. A wrong tenant_id cannot see another tenant's invitations (isolation)
3. find_invitation_by_token() returns None for expired/claimed/cancelled tokens
4. The bypass sentinel does not expose invitations for an incorrect token hash

Migration 0015 adds the bypass sentinel to the tenant_isolation_core_user_invitations
policy, mirroring migration 0014 which did the same for core.users.

All tests are integration-style (require DATABASE_URL + Postgres with 0015 applied).
They are skipped automatically when DATABASE_URL is not set.
Data created during each test is fully removed in teardown (idempotent).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone, timedelta
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
        # asyncpg requires plain postgresql:// scheme (not postgresql+asyncpg://)
        return url.replace("postgresql+asyncpg://", "postgresql://")
    return None


_PG_URL = _pg_url()

# Bypass sentinel from migration 0015 (also 0014 for core.users)
_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"

# Platform tenant — seeded by seeds/super_admins.py (safe to use in tests)
_NS = uuid.NAMESPACE_DNS
PLATFORM_TENANT_ID = str(uuid.uuid5(_NS, "strataos-platform-tenant"))

pytestmark = pytest.mark.skipif(
    _PG_URL is None,
    reason="DATABASE_URL not set — skipping invitation RLS bypass tests",
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _cleanup_postgres_user(user_id: str) -> None:
    """Delete user from core.users (cascades to invitations)."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        await conn.execute(
            f"SET LOCAL app.tenant_id = '{PLATFORM_TENANT_ID}'"
        )
        await conn.execute("DELETE FROM core.users WHERE user_id = $1", user_id)
    finally:
        await conn.close()


async def _cleanup_invitation(invitation_id: str) -> None:
    """Delete invitation row (idempotent)."""
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_PG_URL)
    try:
        await conn.execute(
            f"SET LOCAL app.tenant_id = '{_BYPASS_UUID}'"
        )
        await conn.execute(
            "DELETE FROM core.user_invitations WHERE invitation_id = $1", invitation_id
        )
    finally:
        await conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_invitation_by_token_uses_bypass_sentinel() -> None:
    """Migration 0015: lookup by raw token works without knowing tenant_id."""
    from db_postgres.repos import identity_repo
    from utils.auth import hash_password

    inviter_email = f"inv-bypass-inviter-{uuid.uuid4()}@test.example.com"
    invitee_email = f"inv-bypass-invitee-{uuid.uuid4()}@test.example.com"
    inviter_id = None
    invitation_id = None
    try:
        inviter_id = await identity_repo.create_user_for_registration(
            email=inviter_email,
            password_hash=hash_password("Test1234!"),
            full_name="Test Inviter",
            role="super_admin",
            tenant_id=PLATFORM_TENANT_ID,
            is_approved=True,
            is_test_data=True,
        )

        invitation_id, raw_token = await identity_repo.create_invitation(
            tenant_id=PLATFORM_TENANT_ID,
            scheme_id=None,
            email=invitee_email,
            invited_role="owner",
            invited_by=inviter_id,
        )

        # find_invitation_by_token uses bypass sentinel — no tenant context needed
        result = await identity_repo.find_invitation_by_token(raw_token)

        assert result is not None, (
            "find_invitation_by_token returned None — RLS bypass sentinel may be missing "
            "(migration 0015 required)"
        )
        assert str(result["invitation_id"]) == str(invitation_id), (
            f"Expected invitation_id={invitation_id}, got {result.get('invitation_id')}"
        )
        assert str(result["tenant_id"]) == str(PLATFORM_TENANT_ID)

    finally:
        if invitation_id:
            await _cleanup_invitation(invitation_id)
        if inviter_id:
            await _cleanup_postgres_user(inviter_id)


@pytest.mark.asyncio
async def test_find_invitation_returns_none_for_nonexistent_token() -> None:
    """find_invitation_by_token returns None for an unknown token."""
    from db_postgres.repos import identity_repo

    bogus_token = secrets.token_urlsafe(32)
    result = await identity_repo.find_invitation_by_token(bogus_token)
    assert result is None, (
        "find_invitation_by_token should return None for a token that was never created"
    )


@pytest.mark.asyncio
async def test_find_invitation_returns_none_for_claimed_invitation() -> None:
    """find_invitation_by_token returns None when invitation already claimed."""
    import asyncpg  # type: ignore
    from db_postgres.repos import identity_repo
    from utils.auth import hash_password

    inviter_email = f"inv-claimed-inviter-{uuid.uuid4()}@test.example.com"
    invitee_email = f"inv-claimed-invitee-{uuid.uuid4()}@test.example.com"
    inviter_id = None
    invitation_id = None
    try:
        inviter_id = await identity_repo.create_user_for_registration(
            email=inviter_email,
            password_hash=hash_password("Test1234!"),
            full_name="Test Inviter Claimed",
            role="super_admin",
            tenant_id=PLATFORM_TENANT_ID,
            is_approved=True,
            is_test_data=True,
        )

        invitation_id, raw_token = await identity_repo.create_invitation(
            tenant_id=PLATFORM_TENANT_ID,
            scheme_id=None,
            email=invitee_email,
            invited_role="owner",
            invited_by=inviter_id,
        )

        # Mark the invitation as claimed directly via SQL (use explicit transaction for SET LOCAL)
        conn = await asyncpg.connect(_PG_URL)
        try:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL app.tenant_id = '{PLATFORM_TENANT_ID}'"
                )
                await conn.execute(
                    "UPDATE core.user_invitations SET claimed_at = NOW() "
                    "WHERE invitation_id = $1",
                    invitation_id,
                )
        finally:
            await conn.close()

        result = await identity_repo.find_invitation_by_token(raw_token)
        assert result is None, (
            "find_invitation_by_token should return None for a claimed invitation"
        )

    finally:
        if invitation_id:
            await _cleanup_invitation(invitation_id)
        if inviter_id:
            await _cleanup_postgres_user(inviter_id)


@pytest.mark.asyncio
async def test_find_invitation_returns_none_for_expired_invitation() -> None:
    """find_invitation_by_token returns None for expired invitations."""
    import asyncpg  # type: ignore
    from db_postgres.repos import identity_repo
    from utils.auth import hash_password

    inviter_email = f"inv-expired-inviter-{uuid.uuid4()}@test.example.com"
    invitee_email = f"inv-expired-invitee-{uuid.uuid4()}@test.example.com"
    inviter_id = None
    invitation_id = None
    try:
        inviter_id = await identity_repo.create_user_for_registration(
            email=inviter_email,
            password_hash=hash_password("Test1234!"),
            full_name="Test Inviter Expired",
            role="super_admin",
            tenant_id=PLATFORM_TENANT_ID,
            is_approved=True,
            is_test_data=True,
        )

        invitation_id, raw_token = await identity_repo.create_invitation(
            tenant_id=PLATFORM_TENANT_ID,
            scheme_id=None,
            email=invitee_email,
            invited_role="owner",
            invited_by=inviter_id,
        )

        # Backdate expires_at to the past (use explicit transaction for SET LOCAL)
        conn = await asyncpg.connect(_PG_URL)
        try:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL app.tenant_id = '{PLATFORM_TENANT_ID}'"
                )
                await conn.execute(
                    "UPDATE core.user_invitations SET expires_at = NOW() - INTERVAL '1 hour' "
                    "WHERE invitation_id = $1",
                    invitation_id,
                )
        finally:
            await conn.close()

        result = await identity_repo.find_invitation_by_token(raw_token)
        assert result is None, (
            "find_invitation_by_token should return None for an expired invitation"
        )

    finally:
        if invitation_id:
            await _cleanup_invitation(invitation_id)
        if inviter_id:
            await _cleanup_postgres_user(inviter_id)


@pytest.mark.asyncio
async def test_wrong_tenant_session_cannot_read_invitation() -> None:
    """RLS: a session with a different tenant_id cannot read another tenant's invitation."""
    import asyncpg  # type: ignore
    from db_postgres.repos import identity_repo
    from utils.auth import hash_password

    inviter_email = f"inv-iso-inviter-{uuid.uuid4()}@test.example.com"
    invitee_email = f"inv-iso-invitee-{uuid.uuid4()}@test.example.com"
    inviter_id = None
    invitation_id = None
    other_tenant = str(uuid.uuid4())
    try:
        inviter_id = await identity_repo.create_user_for_registration(
            email=inviter_email,
            password_hash=hash_password("Test1234!"),
            full_name="Test Inviter Isolation",
            role="super_admin",
            tenant_id=PLATFORM_TENANT_ID,
            is_approved=True,
            is_test_data=True,
        )

        invitation_id, _raw_token = await identity_repo.create_invitation(
            tenant_id=PLATFORM_TENANT_ID,
            scheme_id=None,
            email=invitee_email,
            invited_role="owner",
            invited_by=inviter_id,
        )

        # Connect with another tenant's context and attempt to read
        conn = await asyncpg.connect(_PG_URL)
        try:
            await conn.execute(f"SET LOCAL app.tenant_id = '{other_tenant}'")
            row = await conn.fetchrow(
                "SELECT invitation_id FROM core.user_invitations "
                "WHERE invitation_id = $1",
                invitation_id,
            )
            assert row is None, (
                f"RLS FAILURE: other_tenant can read PLATFORM_TENANT's invitation {invitation_id}"
            )
        finally:
            await conn.close()

    finally:
        if invitation_id:
            await _cleanup_invitation(invitation_id)
        if inviter_id:
            await _cleanup_postgres_user(inviter_id)
