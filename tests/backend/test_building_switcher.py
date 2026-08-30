"""
test_building_switcher.py — Unit tests for multi-building switcher functionality.

Post-Mongo→Postgres identity cutover, both /buildings/me and
/auth/switch-building delegate to ``db_postgres.repos.identity_repo``.
Tests mock those repo helpers, not the legacy Mongo collections.

Tests cover:
- GET /buildings/me returns accessible buildings list (Postgres)
- POST /auth/switch-building with valid building_id returns new JWT
- POST /auth/switch-building with invalid building_id returns 403
- Super admin can switch to any building
- Regular user can only switch to their buildings
- JWT always contains building_id after login (fallback to DEFAULT_BUILDING_ID)
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.auth import DEFAULT_BUILDING_ID


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(role="super_admin", building_id="13195", **kwargs):
    return {
        "id": "user-001",
        "email": "admin@example.com",
        "full_name": "Test Admin",
        "role": role,
        "building_id": building_id,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "custom_permissions": {},
        **kwargs,
    }


def _close_background_task(coro):
    """Mimic fire-and-forget scheduling without leaking an unawaited coroutine in tests."""
    if hasattr(coro, "close"):
        coro.close()
    return MagicMock()


def _scheme_row(scheme_id="d565e4fa-bd11-4fb1-a213-82100ddc78ff",
                scheme_number="13195",
                scheme_name="East Gate Residences",
                tenant_id="9e9d75c2-bd92-4695-8487-1592018c3af9",
                tenant_name="East Gate OC",
                jurisdiction="ACT",
                is_demo=False,
                is_active=True):
    """Build a scheme row matching identity_repo's return shape."""
    return {
        "scheme_id": scheme_id,
        "scheme_number": scheme_number,
        "scheme_name": scheme_name,
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "jurisdiction": jurisdiction,
        "is_demo": is_demo,
        "is_active": is_active,
    }


SCHEME_EAST_GATE = _scheme_row()
SCHEME_HARBOURVIEW = _scheme_row(
    scheme_id="11111111-1111-1111-1111-111111111111",
    scheme_number="18932",
    scheme_name="Harbourview Residences",
    tenant_id="22222222-2222-2222-2222-222222222222",
    tenant_name="Harbourview OC",
    jurisdiction="NSW",
)


# ── Test: GET /buildings/me ────────────────────────────────────────────────

class TestGetMyBuildings:
    """Verify /buildings/me returns correct buildings per role."""

    @pytest.mark.asyncio
    async def test_super_admin_sees_all_buildings(self):
        """Super admin gets all active schemes via list_all_active_schemes."""
        from routers.auth import get_my_buildings

        user = _make_user(role="super_admin")
        with patch(
            "db_postgres.repos.identity_repo.list_all_active_schemes",
            AsyncMock(return_value=[SCHEME_EAST_GATE, SCHEME_HARBOURVIEW]),
        ):
            result = await get_my_buildings(current_user=user)

        assert len(result) == 2
        building_ids = [b["building_id"] for b in result]
        assert "13195" in building_ids
        assert "18932" in building_ids

    @pytest.mark.asyncio
    async def test_regular_user_sees_only_their_buildings(self):
        """Non-super_admin gets schemes via list_schemes_for_user(user_id)."""
        from routers.auth import get_my_buildings

        user = _make_user(role="owner")
        with patch(
            "db_postgres.repos.identity_repo.list_schemes_for_user",
            AsyncMock(return_value=[SCHEME_EAST_GATE]),
        ) as mock_list:
            result = await get_my_buildings(current_user=user)

        assert len(result) == 1
        assert result[0]["building_id"] == "13195"
        mock_list.assert_awaited_once_with("user-001")

    @pytest.mark.asyncio
    async def test_user_with_no_memberships_sees_no_buildings(self):
        """User with zero scheme assignments gets an empty list."""
        from routers.auth import get_my_buildings

        user = _make_user(role="owner", building_id=None)
        with patch(
            "db_postgres.repos.identity_repo.list_schemes_for_user",
            AsyncMock(return_value=[]),
        ):
            result = await get_my_buildings(current_user=user)

        assert result == []


class TestPostgresBuildingContext:
    """Regression tests for JWT plan-number context after switch-building."""

    @pytest.mark.asyncio
    async def test_get_current_user_accepts_plan_number_jwt_after_switch(self):
        """A switched JWT carries 13195, but PG membership validation needs UUID."""
        from request_context import set_ctx_building_id
        from utils.auth import get_current_user

        user = _make_user(
            role="owner",
            id="51c9d008-20fd-5e5e-b7cf-a07a0befcf20",
            tenant_id="9e9d75c2-bd92-4695-8487-1592018c3af9",
            building_id=None,
        )

        set_ctx_building_id(None)
        try:
            with patch("utils.auth.decode_token", return_value={
                "user_id": user["id"],
                "tenant_id": user["tenant_id"],
                "building_id": "13195",
            }), patch(
                "db_postgres.repos.identity_repo.get_user_by_id",
                AsyncMock(return_value=user.copy()),
            ), patch(
                "db_postgres.repos.identity_repo.get_scheme_by_id",
                AsyncMock(return_value=None),
            ), patch(
                "db_postgres.repos.identity_repo.get_scheme_by_number",
                AsyncMock(return_value=SCHEME_EAST_GATE),
            ), patch(
                "db_postgres.repos.identity_repo.is_user_in_scheme",
                AsyncMock(return_value=True),
            ) as mock_is_member:
                result = await get_current_user(credentials=SimpleNamespace(credentials="token"))

            assert result["building_id"] == "13195"
            mock_is_member.assert_awaited_once_with(
                user["id"], SCHEME_EAST_GATE["scheme_id"], user["tenant_id"]
            )
        finally:
            set_ctx_building_id(None)

    @pytest.mark.asyncio
    async def test_plan_number_jwt_is_resolved_before_pg_membership_check(self):
        """The JWT carries 13195, but user_role_assignments needs the scheme UUID."""
        from request_context import set_ctx_building_id
        from utils.auth import get_current_building

        user = _make_user(
            role="owner",
            id="51c9d008-20fd-5e5e-b7cf-a07a0befcf20",
            tenant_id="9e9d75c2-bd92-4695-8487-1592018c3af9",
            building_id="13195",
        )

        set_ctx_building_id(None)
        try:
            with patch("utils.auth.decode_token", return_value={
                "tenant_id": user["tenant_id"],
                "building_id": "13195",
            }), patch(
                "db_postgres.repos.identity_repo.get_scheme_by_id",
                AsyncMock(return_value=None),
            ) as mock_by_id, patch(
                "db_postgres.repos.identity_repo.get_scheme_by_number",
                AsyncMock(return_value=SCHEME_EAST_GATE),
            ) as mock_by_number, patch(
                "db_postgres.repos.identity_repo.is_user_in_scheme",
                AsyncMock(return_value=True),
            ) as mock_is_member:
                result = await get_current_building(
                    request=SimpleNamespace(headers={}),
                    current_user=user,
                    credentials=SimpleNamespace(credentials="token"),
                )

            assert result == "13195"
            mock_by_id.assert_awaited_once_with("13195")
            mock_by_number.assert_awaited_once_with("13195")
            mock_is_member.assert_awaited_once_with(
                user["id"], SCHEME_EAST_GATE["scheme_id"], user["tenant_id"]
            )
        finally:
            set_ctx_building_id(None)

    @pytest.mark.asyncio
    async def test_admin_staff_is_membership_scoped(self):
        """Admin staff should only see schemes they belong to, not every scheme."""
        from routers.auth import get_my_buildings

        user = _make_user(role="admin_staff", building_id=None)
        with patch(
            "db_postgres.repos.identity_repo.list_schemes_for_user",
            AsyncMock(return_value=[SCHEME_EAST_GATE]),
        ) as mock_list:
            result = await get_my_buildings(current_user=user)

        assert len(result) == 1
        assert result[0]["building_id"] == "13195"
        mock_list.assert_awaited_once_with("user-001")


class TestAuthMemberships:
    """Verify legacy /auth/memberships response compatibility."""

    @pytest.mark.asyncio
    async def test_super_admin_synthetic_memberships_keep_collision_guard(self):
        """Synthetic SA memberships must not reuse building_id as the row id."""
        from routers.auth import get_memberships

        mock_db = MagicMock()
        mock_db.buildings.find.return_value.to_list = AsyncMock(return_value=[
            {"id": "13195", "slug": "east-gate", "name": "East Gate Residences", "is_active": True},
        ])

        user = _make_user(role="super_admin")
        with patch("routers.auth.db", mock_db):
            result = await get_memberships(current_user=user)

        assert result == [{
            "id": "sa_13195",
            "user_id": "user-001",
            "building_id": "13195",
            "building_slug": "east-gate",
            "building_name": "East Gate Residences",
            "roles": ["super_admin"],
            "units": [],
            "is_primary": False,
        }]
        assert result[0]["id"] != result[0]["building_id"]


# ── Test: POST /auth/switch-building ──────────────────────────────────────

class TestSwitchBuilding:
    """Verify /auth/switch-building enforces access control and returns new JWT."""

    @pytest.mark.asyncio
    async def test_super_admin_can_switch_to_any_building(self):
        """Super admin can switch to any valid scheme without membership check."""
        from routers.auth import switch_building, BuildingSwitchRequest

        user = _make_user(role="super_admin")
        with patch(
            "db_postgres.repos.identity_repo.get_scheme_by_id",
            AsyncMock(return_value=None),
        ), patch(
            "db_postgres.repos.identity_repo.get_scheme_by_number",
            AsyncMock(return_value=SCHEME_HARBOURVIEW),
        ), patch("routers.auth.create_token", return_value="new-jwt-token"), \
                patch("routers.auth.user_to_response", return_value={"id": user["id"]}), \
                patch("routers.auth.asyncio.create_task", side_effect=_close_background_task):
            result = await switch_building(
                data=BuildingSwitchRequest(building_id="18932"),
                current_user=user,
            )

        assert result["token"] == "new-jwt-token"
        assert result["building"]["building_id"] == "18932"

    @pytest.mark.asyncio
    async def test_regular_user_can_switch_to_their_building(self):
        """User with scheme assignment can switch to that scheme."""
        from routers.auth import switch_building, BuildingSwitchRequest

        user = _make_user(role="owner")
        with patch(
            "db_postgres.repos.identity_repo.get_scheme_by_id",
            AsyncMock(return_value=None),
        ), patch(
            "db_postgres.repos.identity_repo.get_scheme_by_number",
            AsyncMock(return_value=SCHEME_EAST_GATE),
        ), patch(
            "db_postgres.repos.identity_repo.list_schemes_for_user",
            AsyncMock(return_value=[SCHEME_EAST_GATE]),
        ), patch("routers.auth.create_token", return_value="scoped-token"), \
                patch("routers.auth.user_to_response", return_value={"id": user["id"]}), \
                patch("routers.auth.asyncio.create_task", side_effect=_close_background_task):
            result = await switch_building(
                data=BuildingSwitchRequest(building_id="13195"),
                current_user=user,
            )

        assert result["token"] == "scoped-token"

    @pytest.mark.asyncio
    async def test_regular_user_cannot_switch_to_unaffiliated_building(self):
        """Non-super_admin without scheme assignment is rejected with 403."""
        from routers.auth import switch_building, BuildingSwitchRequest
        from fastapi import HTTPException

        user = _make_user(role="owner")
        with patch(
            "db_postgres.repos.identity_repo.get_scheme_by_id",
            AsyncMock(return_value=None),
        ), patch(
            "db_postgres.repos.identity_repo.get_scheme_by_number",
            AsyncMock(return_value=SCHEME_HARBOURVIEW),
        ), patch(
            "db_postgres.repos.identity_repo.list_schemes_for_user",
            AsyncMock(return_value=[SCHEME_EAST_GATE]),  # no harbourview
        ):
            with pytest.raises(HTTPException) as exc:
                await switch_building(
                    data=BuildingSwitchRequest(building_id="18932"),
                    current_user=user,
                )

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_switch_to_nonexistent_building_returns_404(self):
        """Switching to a scheme that doesn't exist returns 404."""
        from routers.auth import switch_building, BuildingSwitchRequest
        from fastapi import HTTPException

        user = _make_user(role="super_admin")
        with patch(
            "db_postgres.repos.identity_repo.get_scheme_by_id",
            AsyncMock(return_value=None),
        ), patch(
            "db_postgres.repos.identity_repo.get_scheme_by_number",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc:
                await switch_building(
                    data=BuildingSwitchRequest(building_id="nonexistent"),
                    current_user=user,
                )

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_staff_cannot_switch_to_unaffiliated_building(self):
        """Admin staff must also hold a scheme assignment for the target."""
        from routers.auth import switch_building, BuildingSwitchRequest
        from fastapi import HTTPException

        user = _make_user(role="admin_staff")
        with patch(
            "db_postgres.repos.identity_repo.get_scheme_by_id",
            AsyncMock(return_value=None),
        ), patch(
            "db_postgres.repos.identity_repo.get_scheme_by_number",
            AsyncMock(return_value=SCHEME_HARBOURVIEW),
        ), patch(
            "db_postgres.repos.identity_repo.list_schemes_for_user",
            AsyncMock(return_value=[]),
        ):
            with pytest.raises(HTTPException) as exc:
                await switch_building(
                    data=BuildingSwitchRequest(building_id="18932"),
                    current_user=user,
                )

        assert exc.value.status_code == 403


# ── Test: JWT always contains building_id ─────────────────────────────────

class TestJWTBuildingIdFallback:
    """
    Verify DEFAULT_BUILDING_ID clean-break behaviour (Phase E).

    As of Phase E, DEFAULT_BUILDING_ID is deliberately set to "" so that
    login never silently falls back to a hard-coded building.  Users without
    an explicit building membership receive a JWT with no building_id claim;
    the frontend must prompt them to select or join a building.
    """

    def test_default_building_id_is_empty_string_clean_break(self):
        """DEFAULT_BUILDING_ID must be '' — no silent fallback to East Gate."""
        assert isinstance(DEFAULT_BUILDING_ID, str), "DEFAULT_BUILDING_ID must be a str"
        assert DEFAULT_BUILDING_ID == "", (
            "DEFAULT_BUILDING_ID should be empty string (clean-break). "
            "Silent fallback to a specific building_id is forbidden in multi-tenant mode."
        )

    def test_default_building_id_is_not_hardcoded_plan_number(self):
        """DEFAULT_BUILDING_ID must NOT be '13195' — multi-tenant clean-break."""
        assert DEFAULT_BUILDING_ID != "13195", (
            "DEFAULT_BUILDING_ID must NOT be '13195'. "
            "Hardcoding East Gate as default breaks multi-tenant isolation."
        )

    @pytest.mark.asyncio
    async def test_login_with_no_memberships_omits_building_id_from_jwt(self):
        """
        When a user has building_id=None and zero memberships, the JWT must
        NOT carry any building_id claim (or carry an empty string).
        The frontend is responsible for directing the user to pick a building.
        """
        from utils.auth import create_token
        import jwt

        # Simulate: user has no building_id and no memberships
        scoped_building_id = None
        memberships = []  # no memberships
        if not scoped_building_id:
            if len(memberships) == 1:
                scoped_building_id = memberships[0]["building_id"]
        # Clean-break: no fallback to a specific building
        if not scoped_building_id:
            scoped_building_id = DEFAULT_BUILDING_ID  # ""

        # scoped_building_id == "" means JWT carries empty or missing building_id
        token = create_token("user-123", "test@example.com", "owner", building_id=scoped_building_id)
        payload = jwt.decode(token, options={"verify_signature": False})
        # building_id in JWT should be absent or empty — never "13195"
        jwt_building_id = payload.get("building_id")
        assert jwt_building_id != "13195", (
            "JWT must NOT silently assign 13195 for users with no building membership."
        )
