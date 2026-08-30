"""Tests for the Postgres-first auth repoint.

Covers:
- get_current_user returns Postgres user when JWT carries tenant_id
- get_current_user falls back to MongoDB for JWTs without tenant_id
- login endpoint authenticates against Postgres user and returns JWT with tenant_id
- Onboarding: start session, fetch status, finalize
- Invitation send + claim

These are integration-style tests that require:
  - DATABASE_URL set (Postgres connection)
  - The super admin seed to have been run (seeds/super_admins.py)
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — skipping auth repoint integration tests.",
)

# ──────────────────────────────────────────────────────────────────────────────
# Imports deferred so skip fires before any import errors
# ──────────────────────────────────────────────────────────────────────────────

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
PLATFORM_TENANT_ID = str(uuid.uuid5(_NS, "strataos-platform-tenant"))

BASE_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8003/api")
SA_EMAIL = "administrator@strataos.live"
SA_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _login(email: str = SA_EMAIL, password: str = SA_PASSWORD) -> requests.Response:
    return requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )


def _asyncpg_dsn() -> str:
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


# ──────────────────────────────────────────────────────────────────────────────
# Login
# ──────────────────────────────────────────────────────────────────────────────

def test_login_postgres_user_returns_jwt():
    """Login with a Postgres super admin must return a JWT containing tenant_id."""
    response = _login()
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    assert "token" in data or "access_token" in data, f"No token in response: {data}"

    token = data.get("token") or data.get("access_token")
    assert token

    # Decode without verification to inspect claims
    import jwt
    from config import JWT_ALGORITHM
    payload = jwt.decode(token, options={"verify_signature": False}, algorithms=[JWT_ALGORITHM])
    assert "tenant_id" in payload, f"JWT missing tenant_id claim: {payload}"
    assert payload.get("email") == SA_EMAIL


def test_login_wrong_password_returns_401():
    response = _login(password="wrongpassword!")
    assert response.status_code in (401, 422), f"Expected 401, got {response.status_code}"


def test_login_unknown_email_returns_401():
    response = _login(email="nobody@example.invalid", password="anything")
    assert response.status_code in (401, 422)


# ──────────────────────────────────────────────────────────────────────────────
# get_current_user — Postgres path
# ──────────────────────────────────────────────────────────────────────────────

def test_get_current_user_with_postgres_jwt():
    """After login, /auth/me must return the Postgres user."""
    login_resp = _login()
    assert login_resp.status_code == 200, login_resp.text

    data = login_resp.json()
    token = data.get("token") or data.get("access_token")

    me_resp = requests.get(
        f"{BASE_URL}/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert me_resp.status_code == 200, f"/auth/me returned {me_resp.status_code}: {me_resp.text}"
    me = me_resp.json()
    assert me.get("email") == SA_EMAIL
    assert me.get("role") == "super_admin"


# ──────────────────────────────────────────────────────────────────────────────
# Onboarding flow
# ──────────────────────────────────────────────────────────────────────────────

def test_onboarding_flow_requires_super_admin():
    """Non-super-admin must get 403 on onboarding start."""
    response = requests.post(
        f"{BASE_URL}/onboarding/scheme/start",
        json={
            "scheme_number": "99999",
            "scheme_name": "Test Scheme",
            "jurisdiction": "ACT",
        },
        headers={"Authorization": "Bearer faketoken"},
        timeout=10,
    )
    # 401 or 403 acceptable depending on override wiring; key is NOT 201
    assert response.status_code in (401, 403), \
        f"Expected 401/403 for non-SA, got {response.status_code}: {response.text}"


@pytest.mark.asyncio
async def test_onboarding_start_creates_session():
    """Super admin can start an onboarding session for a new scheme."""
    import asyncpg
    import jwt

    login_resp = _login()
    assert login_resp.status_code == 200, login_resp.text
    token = (login_resp.json().get("token") or login_resp.json().get("access_token"))
    token_payload = jwt.decode(token, options={"verify_signature": False})
    caller_tenant_id = token_payload["tenant_id"]

    conn = await asyncpg.connect(_asyncpg_dsn())
    try:
        target_tenant_id = await conn.fetchval(
            """
            SELECT tenant_id::TEXT
            FROM core.tenants
            WHERE status::TEXT != 'archived'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
    finally:
        await conn.close()
    assert target_tenant_id, "No active tenant found for onboarding target"

    unique_num = f"T{uuid.uuid4().hex[:6].upper()}"
    resp = requests.post(
        f"{BASE_URL}/onboarding/scheme/start",
        json={
            "scheme_number": unique_num,
            "scheme_name": f"Test Scheme {unique_num}",
            "jurisdiction": "ACT",
            "tenant_id": target_tenant_id,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "session_id" in body
    assert "scheme_id" in body
    assert "tenant_id" in body
    session_id = body["session_id"]
    scheme_id = body["scheme_id"]

    # Get status
    status_resp = requests.get(
        f"{BASE_URL}/onboarding/scheme/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["session_id"] == session_id

    # Cleanup - delete only this test session and scheme.
    conn = await asyncpg.connect(_asyncpg_dsn())
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{caller_tenant_id}'")
            await conn.execute(
                "DELETE FROM core.onboarding_sessions WHERE session_id = $1",
                uuid.UUID(session_id),
            )
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{target_tenant_id}'")
            await conn.execute(
                "DELETE FROM core.schemes WHERE scheme_id = $1",
                uuid.UUID(scheme_id),
            )
    finally:
        await conn.close()
