import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock
import sys
import os

# Add backend to path
sys.path.append(os.path.abspath("backend"))


def test_unapproved_user_access_blocked():
    """
    Sentinel Security Test:
    Verifies that unapproved users (is_approved=False) are blocked from accessing
    sensitive endpoints in server.py that now use get_approved_user.
    """
    from server import app
    from utils.auth import get_current_user, get_current_building

    unapproved_user = {
        "id": "unapproved-owner-id",
        "email": "unapproved@example.com",
        "role": "owner",
        "is_approved": False,
        "is_active": True,
        "building_id": "13195",
        "full_name": "Unapproved Owner",
        "unit_number": "101"
    }

    # Dependency overrides
    app.dependency_overrides[get_current_user] = lambda: unapproved_user
    app.dependency_overrides[get_current_building] = lambda: "13195"

    client = TestClient(app)

    # 1. Test /api/owners-units
    response = client.get("/api/owners-units")
    assert response.status_code == 403, "Unapproved owner should be blocked from /api/owners-units"
    body = response.json()
    msg = (body.get("detail") or body.get("error", {}).get("message", "")).lower()
    assert "pending approval" in msg

    # 2. Test /api/owners-units/{unit_number}
    response = client.get("/api/owners-units/101")
    assert response.status_code == 403, "Unapproved owner should be blocked from /api/owners-units/101"

    # 3. Test /api/documents/important
    response = client.get("/api/documents/important")
    assert response.status_code == 403, "Unapproved owner should be blocked from /api/documents/important"

    # Cleanup overrides
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_approved_user_access_allowed():
    """
    Verifies that the get_approved_user dependency does NOT raise for an approved user.

    Uses a direct async call to the dependency rather than TestClient to avoid the
    "Future attached to a different loop" RuntimeError that occurs when AsyncMock
    objects bound to the pytest-asyncio session loop are used inside TestClient's
    own event loop.
    """
    from fastapi import HTTPException
    from utils.auth import get_approved_user, is_approved_user

    approved_user = {
        "id": "approved-owner-id",
        "email": "approved@example.com",
        "role": "owner",
        "is_approved": True,
        "is_active": True,
        "building_id": "13195",
        "full_name": "Approved Owner",
        "unit_number": "101"
    }

    # Call get_approved_user directly — it wraps get_current_user and checks is_approved.
    # Pass the user as the resolved dependency value (FastAPI would inject it at runtime).
    try:
        result = await get_approved_user(current_user=approved_user)
        assert result == approved_user, "get_approved_user should return the user unchanged"
    except HTTPException as exc:
        assert False, f"Approved user incorrectly received {exc.status_code}: {exc.detail}"

    # Also verify the helper predicate directly
    assert is_approved_user(approved_user), "is_approved_user() must return True for approved user"


def test_unapproved_user_get_approved_user_raises():
    """
    Verifies synchronously that get_approved_user raises 403 for unapproved users.
    Mirrors the intent of test_unapproved_user_access_blocked without a TestClient.
    """
    import asyncio
    from fastapi import HTTPException
    from utils.auth import get_approved_user

    unapproved_user = {
        "id": "x",
        "role": "owner",
        "is_approved": False,
        "is_active": True,
        "full_name": "Test",
    }

    async def _run():
        try:
            await get_approved_user(current_user=unapproved_user)
            return None  # should not reach here
        except HTTPException as exc:
            return exc.status_code

    status = asyncio.get_event_loop().run_until_complete(_run())
    assert status == 403, f"Expected 403 for unapproved user, got {status}"
