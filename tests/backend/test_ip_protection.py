import pytest
from httpx import AsyncClient, ASGITransport

from models.user import UserRole
from request_context import set_ctx_building_id
from server import app, db, _get_auth_admin
from utils.auth import get_current_user, get_current_building, get_optional_building


@pytest.mark.asyncio
async def test_auth_admin_initialization():
    # Credentials are obfuscated in code
    email, _ = _get_auth_admin()
    assert email == "gagneet@silverfoxtechnologies.com.au"

    # Check if user exists in DB (initialized by startup_event normally,
    # but we can check if it exists after a request or just check DB directly if mocked)
    user = await db.users.find_one({"email": email})
    if not user:
        # Manually trigger the startup logic if needed for test environment
        from server import startup_event
        await startup_event()
        user = await db.users.find_one({"email": email})

    assert user is not None
    assert user["role"] == UserRole.SUPER_ADMIN
    assert user["is_active"] is True
    assert user["is_approved"] is True


@pytest.mark.asyncio
async def test_auth_admin_protection():
    auth_email, _ = _get_auth_admin()
    auth_user = await db.users.find_one({"email": auth_email})
    assert auth_user is not None, "Auth admin must exist in DB"
    auth_user_id = auth_user["id"]

    # Use the other real super_admin that exists in the DB (not the auth admin itself)
    other_admin = await db.users.find_one({
        "role": UserRole.SUPER_ADMIN,
        "is_active": True,
        "email": {"$ne": auth_email}
    })
    assert other_admin is not None, "A second super_admin must exist in the DB for this test"

    async def _override_building_admin_test():
        set_ctx_building_id("13195")
        return "13195"

    headers = {}
    app.dependency_overrides[get_current_user] = lambda: other_admin
    app.dependency_overrides[get_current_building] = _override_building_admin_test

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Try to delete auth admin as a different super_admin — must be blocked
            response = await ac.delete(f"/api/users/{auth_user_id}", headers=headers)
            assert response.status_code in (403, 404)
            if response.status_code == 403:
                assert "System administrator account cannot be deleted" in response.json()["detail"]
            else:
                assert response.json().get("detail") in ("User not found", "User not found in this building")

            # Try to change auth admin role as a different super_admin — must be blocked
            response = await ac.put(f"/api/users/{auth_user_id}", json={"role": "owner"}, headers=headers)
            assert response.status_code in (403, 404)
    finally:
        app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_ip_string_protection():
    email, _ = _get_auth_admin()
    auth_user = await db.users.find_one({"email": email})

    # Use a real super_admin (not the auth admin) so auth passes
    other_admin = await db.users.find_one({
        "role": UserRole.SUPER_ADMIN,
        "is_active": True,
        "email": {"$ne": email},
    })
    assert other_admin is not None, "A second super_admin must exist in the DB for this test"

    async def _override_building():
        set_ctx_building_id("13195")
        return "13195"

    # Override both get_current_building (used by PUT) and get_optional_building (used by GET)
    app.dependency_overrides[get_current_building] = _override_building
    app.dependency_overrides[get_optional_building] = _override_building
    headers = {}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Other admin tries to update ip_string
            app.dependency_overrides[get_current_user] = lambda: other_admin
            settings_res = await ac.get("/api/settings", headers=headers)
            settings = settings_res.json()
            original_ip = settings.get("ip_string")
            try:
                new_ip = "Unauthorized change"
                await ac.put("/api/settings", json={"ip_string": new_ip}, headers=headers)

                # Verify it didn't change
                settings_res = await ac.get("/api/settings", headers=headers)
                settings = settings_res.json()
                assert settings["ip_string"] != new_ip

                # Authorized admin tries to update ip_string
                app.dependency_overrides[get_current_user] = lambda: auth_user
                new_ip = "Authorized change by Silverfox"
                response = await ac.put("/api/settings", json={"ip_string": new_ip}, headers=headers)
                assert response.status_code == 200

                settings_res = await ac.get("/api/settings", headers=headers)
                settings = settings_res.json()
                assert settings["ip_string"] == new_ip
            finally:
                if original_ip is not None:
                    app.dependency_overrides[get_current_user] = lambda: auth_user
                    await ac.put("/api/settings", json={"ip_string": original_ip}, headers=headers)
    finally:
        app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_ip_header_presence():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/settings")
        assert response.headers[
                   "X-IP-Protection"] == "A vision by: Silverfox Technologies, Australia - Contact: gagneet@silverfoxtechnologies.com.au"
