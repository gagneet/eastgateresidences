"""GET /onboarding/scheme/{session_id} role-guard fix (GAP-ONBOARD-002 Item 2 follow-up,
2026-08-19): this endpoint used the stricter _require_super_admin() while every sibling
onboarding endpoint (start, create, PATCH, the four import endpoints, finalize) already used
the broader _require_can_onboard() (super_admin/strata_admin/strata_manager) — a strata_admin
or strata_manager could create and drive an onboarding session end-to-end but never fetch its
own status, breaking any UI (e.g. the Onboarding Wizard's resume flow) that polls this endpoint.

All DB interactions are mocked so no live database is required.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport

from server import app, get_current_user

SESSION_ID = str(uuid.uuid4())
SCHEME_ID = str(uuid.uuid4())
TENANT_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())


def _user(role: str) -> dict:
    return {
        "id": USER_ID,
        "full_name": f"Test {role}",
        "role": role,
        "tenant_id": TENANT_ID,
        "is_approved": True,
    }


def _make_pg_session():
    row = (SESSION_ID, TENANT_ID, SCHEME_ID, "in_progress", 3, {}, "2026-08-19T00:00:00Z", "2026-08-19T00:00:00Z")
    fetch_result = MagicMock()
    fetch_result.fetchone = MagicMock(return_value=row)
    pg = AsyncMock()
    pg.execute = AsyncMock(return_value=fetch_result)
    pg.__aenter__ = AsyncMock(return_value=pg)
    pg.__aexit__ = AsyncMock(return_value=False)
    return pg


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["super_admin", "strata_admin", "strata_manager"])
async def test_get_onboarding_status_allows_every_onboarding_role(role):
    """A strata_admin/strata_manager who created (or is driving) an onboarding session must
    be able to GET its status, same as super_admin — not a pre-existing-but-narrower guard."""
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    mock_pg = _make_pg_session()
    try:
        with (
            patch("routers.onboarding.async_session_context", return_value=mock_pg),
            patch("routers.onboarding.set_tenant", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/api/onboarding/scheme/{SESSION_ID}")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["session_id"] == SESSION_ID


@pytest.mark.asyncio
async def test_get_onboarding_status_rejects_owner_role():
    """A role outside {super_admin, strata_admin, strata_manager} (e.g. owner) must still be
    rejected — this fix widens the guard to the onboarding-role set, not to everyone."""
    app.dependency_overrides[get_current_user] = lambda: _user("owner")
    mock_pg = _make_pg_session()
    try:
        with (
            patch("routers.onboarding.async_session_context", return_value=mock_pg),
            patch("routers.onboarding.set_tenant", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/api/onboarding/scheme/{SESSION_ID}")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
