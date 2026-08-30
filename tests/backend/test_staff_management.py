"""
Tests for Staff User Management endpoints.

Tests cover:
- List staff users (super admin only)
- Get staff user detail
- Assign elevated role (owner, ec_member, secretary, treasurer, strata_admin) to staff user
- Revoke role assignment
- Cross-building data isolation (no data leaks)
- Invalid role assignment rejection
- Non-super-admin access rejected (403)
- Unknown staff user returns 404
- Duplicate role assignment returns 409
- Building validation (inactive building rejected)

Run from project root:
    backend/venv/bin/pytest tests/backend/test_staff_management.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def super_admin():
    return {
        "id": "user-sadmin-001",
        "role": "super_admin",
        "email": "admin@eastgate.com",
        "full_name": "Super Admin",
        "building_id": "building-east-001",
        "is_approved": True,
        "is_active": True,
    }


@pytest.fixture
def strata_manager_user():
    return {
        "id": "user-sm-001",
        "role": "strata_manager",
        "email": "manager@example.com",
        "full_name": "Jane Manager",
        "organisation": "East Gate Strata Pty Ltd",
        "professional_licence": "SM-12345",
        "phone": "0411000001",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def real_estate_agent_user():
    return {
        "id": "user-rea-001",
        "role": "real_estate_agent",
        "email": "agent@realty.com",
        "full_name": "Bob Agent",
        "organisation": "Premier Realty",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def reception_user():
    return {
        "id": "user-rec-001",
        "role": "admin_staff",
        "email": "reception@eastgate.com",
        "full_name": "Sam Reception",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def building_a():
    return {"id": "building-east-001", "name": "East Gate Residences", "is_active": True}


@pytest.fixture
def building_b():
    return {"id": "building-west-002", "name": "West Gate Residences", "is_active": True}


@pytest.fixture
def ec_member_role_def():
    return {"id": "role-ec-member", "name": "EC_MEMBER", "display_name": "EC Member"}


@pytest.fixture
def strata_admin_role_def():
    return {"id": "role-strata-admin", "name": "STRATA_ADMIN", "display_name": "Strata Admin"}


@pytest.fixture
def owner_role_def():
    return {"id": "role-owner", "name": "OWNER", "display_name": "Owner"}


@pytest.fixture
def active_assignment(strata_manager_user, building_a, ec_member_role_def):
    return {
        "id": "asgn-001",
        "user_id": strata_manager_user["id"],
        "role_id": ec_member_role_def["id"],
        "building_id": building_a["id"],
        "is_active": True,
        "start_date": "2026-01-01T00:00:00Z",
        "end_date": None,
        "notes": "Elected at AGM",
        "created_at": "2026-01-01T00:00:00Z",
    }


# ─── Import helpers ──────────────────────────────────────────────────────────

def _make_async_mock(return_value):
    """Return an AsyncMock that yields return_value."""
    m = AsyncMock()
    m.return_value = return_value
    return m


def _close_background_task(coro):
    """Close fire-and-forget coroutines when tests stub asyncio.create_task."""
    if hasattr(coro, "close"):
        coro.close()
    return MagicMock()


# ─── Unit tests for helper functions ─────────────────────────────────────────

class TestRequireSuperAdmin:
    """_require_super_admin raises 403 for non-super-admin users."""

    def test_super_admin_passes(self, super_admin):
        from routers.staff_management import _require_super_admin
        # Should not raise
        _require_super_admin(super_admin)

    def test_chairman_rejected(self):
        from routers.staff_management import _require_super_admin
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_super_admin({"role": "chairman"})
        assert exc.value.status_code == 403

    def test_strata_manager_rejected(self):
        from routers.staff_management import _require_super_admin
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_super_admin({"role": "strata_manager"})
        assert exc.value.status_code == 403

    def test_owner_rejected(self):
        from routers.staff_management import _require_super_admin
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_super_admin({"role": "owner"})
        assert exc.value.status_code == 403

    def test_ec_member_rejected(self):
        from routers.staff_management import _require_super_admin
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_super_admin({"role": "ec_member"})
        assert exc.value.status_code == 403


class TestElevatableRoles:
    """ELEVATABLE_ROLES constant has expected values."""

    def test_all_expected_roles_present(self):
        # Chairman is no longer a top-level role. strata_admin is a separate
        # organisation-administration role and must not set ec_position.
        from routers.staff_management import ELEVATABLE_ROLES
        assert "owner" in ELEVATABLE_ROLES
        assert "ec_member" in ELEVATABLE_ROLES
        assert "secretary" in ELEVATABLE_ROLES
        assert "treasurer" in ELEVATABLE_ROLES
        assert "strata_admin" in ELEVATABLE_ROLES

    def test_staff_role_not_elevatable(self):
        from routers.staff_management import ELEVATABLE_ROLES
        assert "strata_manager" not in ELEVATABLE_ROLES
        assert "real_estate_agent" not in ELEVATABLE_ROLES
        assert "admin_staff" not in ELEVATABLE_ROLES
        assert "super_admin" not in ELEVATABLE_ROLES

    def test_staff_roles_set(self):
        from routers.staff_management import STAFF_ROLES
        assert "strata_manager" in STAFF_ROLES
        assert "real_estate_agent" in STAFF_ROLES
        assert "admin_staff" in STAFF_ROLES


class TestECPositionMap:
    """EC_POSITION_MAP maps governance roles to legacy ec_position values."""

    def test_strata_admin_is_not_an_ec_position_alias(self):
        from routers.staff_management import EC_POSITION_MAP
        assert "strata_admin" not in EC_POSITION_MAP

    def test_treasurer_maps_to_treasurer(self):
        from routers.staff_management import EC_POSITION_MAP
        assert EC_POSITION_MAP["treasurer"] == "TREASURER"

    def test_secretary_maps_to_secretary(self):
        from routers.staff_management import EC_POSITION_MAP
        assert EC_POSITION_MAP["secretary"] == "SECRETARY"

    def test_ec_member_maps_to_member(self):
        from routers.staff_management import EC_POSITION_MAP
        assert EC_POSITION_MAP["ec_member"] == "MEMBER"

    def test_owner_not_in_ec_map(self):
        from routers.staff_management import EC_POSITION_MAP
        assert "owner" not in EC_POSITION_MAP


# ─── Endpoint tests ───────────────────────────────────────────────────────────

class TestListStaffUsers:
    """GET /admin/staff-users"""

    @pytest.mark.asyncio
    async def test_super_admin_can_list_all_staff(
            self, super_admin, strata_manager_user, real_estate_agent_user, building_a
    ):
        """Super admin sees all staff roles across all buildings."""
        from routers.staff_management import list_staff_users

        with patch("routers.staff_management.db") as mock_db:
            # Mock users query
            mock_cursor = MagicMock()
            mock_cursor.to_list = AsyncMock(return_value=[strata_manager_user, real_estate_agent_user])
            mock_db.users.find.return_value = mock_cursor

            # Mock memberships and buildings
            mock_mem_cursor = MagicMock()
            mock_mem_cursor.to_list = AsyncMock(return_value=[])
            mock_db.memberships.find.return_value = mock_mem_cursor

            mock_bldg_cursor = MagicMock()
            mock_bldg_cursor.to_list = AsyncMock(return_value=[building_a])
            mock_db.buildings.find.return_value = mock_bldg_cursor

            mock_roles_cursor = MagicMock()
            mock_roles_cursor.to_list = AsyncMock(return_value=[])
            mock_db.user_roles.find.return_value = mock_roles_cursor

            result = await list_staff_users(current_user=super_admin)
            assert len(result) == 2
            emails = {u["email"] for u in result}
            assert "manager@example.com" in emails
            assert "agent@realty.com" in emails

    @pytest.mark.asyncio
    async def test_non_super_admin_rejected(self):
        """Non-super-admin gets 403."""
        from routers.staff_management import list_staff_users
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await list_staff_users(current_user={"role": "chairman"})
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_role_filter_applied(self, super_admin, strata_manager_user, building_a):
        """Filtering by role returns only matching role type."""
        from routers.staff_management import list_staff_users

        with patch("routers.staff_management.db") as mock_db:
            mock_cursor = MagicMock()
            mock_cursor.to_list = AsyncMock(return_value=[strata_manager_user])
            mock_db.users.find.return_value = mock_cursor

            mock_mem = MagicMock()
            mock_mem.to_list = AsyncMock(return_value=[])
            mock_db.memberships.find.return_value = mock_mem
            mock_db.buildings.find.return_value = MagicMock(**{"to_list": AsyncMock(return_value=[])})
            mock_db.user_roles.find.return_value = MagicMock(**{"to_list": AsyncMock(return_value=[])})

            result = await list_staff_users(role="strata_manager", current_user=super_admin)
            # Verify the query included role filter
            call_args = mock_db.users.find.call_args
            query = call_args[0][0]
            assert query.get("role") == "strata_manager"


class TestGetStaffUserDetail:
    """GET /admin/staff-users/{user_id}"""

    @pytest.mark.asyncio
    async def test_returns_user_with_enriched_data(
            self, super_admin, strata_manager_user, building_a, active_assignment, ec_member_role_def
    ):
        from routers.staff_management import get_staff_user

        with patch("routers.staff_management.db") as mock_db:
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.memberships.find.return_value = MagicMock(
                **{"to_list": AsyncMock(
                    return_value=[{"user_id": strata_manager_user["id"], "building_id": building_a["id"]}])}
            )
            mock_db.buildings.find.return_value = MagicMock(
                **{"to_list": AsyncMock(return_value=[building_a])}
            )
            mock_db.user_roles.find.return_value = MagicMock(
                **{"to_list": AsyncMock(return_value=[active_assignment])}
            )
            mock_db.roles.find.return_value = MagicMock(
                **{"to_list": AsyncMock(return_value=[ec_member_role_def])}
            )

            result = await get_staff_user(user_id=strata_manager_user["id"], current_user=super_admin)
            assert result["id"] == strata_manager_user["id"]
            assert result["email"] == "manager@example.com"
            assert len(result["buildings"]) == 1
            assert result["buildings"][0]["id"] == building_a["id"]
            assert len(result["role_assignments"]) == 1
            assert result["role_assignments"][0]["id"] == "asgn-001"

    @pytest.mark.asyncio
    async def test_non_staff_user_returns_404(self, super_admin):
        """An owner user ID raises 404 (not a staff role)."""
        from routers.staff_management import get_staff_user
        from fastapi import HTTPException

        with patch("routers.staff_management.db") as mock_db:
            mock_db.users.find_one = AsyncMock(return_value=None)  # not found as staff role

            with pytest.raises(HTTPException) as exc:
                await get_staff_user(user_id="user-owner-999", current_user=super_admin)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self):
        from routers.staff_management import get_staff_user
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await get_staff_user(user_id="any-id", current_user={"role": "strata_manager"})
        assert exc.value.status_code == 403


class TestAssignBuildingRole:
    """POST /admin/staff-users/{user_id}/building-roles"""

    @pytest.mark.asyncio
    async def test_assign_ec_member_role_succeeds(
            self, super_admin, strata_manager_user, building_a, ec_member_role_def
    ):
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest

        body = BuildingRoleAssignRequest(
            role_name="ec_member",
            building_id=building_a["id"],
            notes="Elected at AGM 2026",
        )

        with patch("routers.staff_management.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value=building_a)
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.roles.find_one = AsyncMock(return_value=ec_member_role_def)
            mock_db.user_roles.find_one = AsyncMock(return_value=None)  # no duplicate
            mock_db.user_roles.insert_one = AsyncMock(return_value=MagicMock())
            _ur_cursor = MagicMock()
            _ur_cursor.to_list = AsyncMock(return_value=[])
            mock_db.user_roles.find = MagicMock(return_value=_ur_cursor)
            mock_db.memberships.find_one = AsyncMock(return_value={"id": "mem-001"})  # already member
            mock_db.users.update_one = AsyncMock(return_value=MagicMock())

            with patch("routers.staff_management.create_audit_log", new_callable=AsyncMock):
                with patch("routers.staff_management.asyncio.create_task", side_effect=_close_background_task):
                    result = await assign_building_role(
                        user_id=strata_manager_user["id"],
                        body=body,
                        current_user=super_admin,
                    )

            assert result["id"] is not None
            assert "ec_member" in result["message"]

    @pytest.mark.asyncio
    async def test_invalid_role_rejected(self, super_admin, strata_manager_user):
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest
        from fastapi import HTTPException

        body = BuildingRoleAssignRequest(
            role_name="super_admin",  # not allowed
            building_id="building-east-001",
        )

        with pytest.raises(HTTPException) as exc:
            await assign_building_role(
                user_id=strata_manager_user["id"],
                body=body,
                current_user=super_admin,
            )
        assert exc.value.status_code == 400
        assert "super_admin" in exc.value.detail

    @pytest.mark.asyncio
    async def test_inactive_building_rejected(self, super_admin, strata_manager_user):
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest
        from fastapi import HTTPException

        body = BuildingRoleAssignRequest(
            role_name="ec_member",
            building_id="building-inactive-999",
        )

        with patch("routers.staff_management.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value=None)  # inactive/not found

            with pytest.raises(HTTPException) as exc:
                await assign_building_role(
                    user_id=strata_manager_user["id"],
                    body=body,
                    current_user=super_admin,
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_duplicate_assignment_rejected(
            self, super_admin, strata_manager_user, building_a, ec_member_role_def, active_assignment
    ):
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest
        from fastapi import HTTPException

        body = BuildingRoleAssignRequest(
            role_name="ec_member",
            building_id=building_a["id"],
        )

        with patch("routers.staff_management.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value=building_a)
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.roles.find_one = AsyncMock(return_value=ec_member_role_def)
            mock_db.user_roles.find_one = AsyncMock(return_value=active_assignment)  # duplicate!

            with pytest.raises(HTTPException) as exc:
                await assign_building_role(
                    user_id=strata_manager_user["id"],
                    body=body,
                    current_user=super_admin,
                )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_non_staff_user_rejected(self, super_admin, building_a):
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest
        from fastapi import HTTPException

        body = BuildingRoleAssignRequest(role_name="ec_member", building_id=building_a["id"])

        with patch("routers.staff_management.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value=building_a)
            mock_db.users.find_one = AsyncMock(return_value=None)  # not a staff user

            with pytest.raises(HTTPException) as exc:
                await assign_building_role(
                    user_id="user-owner-001",
                    body=body,
                    current_user=super_admin,
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_strata_admin_does_not_set_legacy_ec_position(
            self, super_admin, strata_manager_user, building_a, strata_admin_role_def
    ):
        """Assigning strata_admin grants organisation-admin access, not EC chairperson state."""
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest

        body = BuildingRoleAssignRequest(
            role_name="strata_admin",
            building_id=building_a["id"],
        )

        with patch("routers.staff_management.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value=building_a)
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.roles.find_one = AsyncMock(return_value=strata_admin_role_def)
            mock_db.user_roles.find_one = AsyncMock(return_value=None)
            mock_db.user_roles.insert_one = AsyncMock()
            _ur_cursor = MagicMock()
            _ur_cursor.to_list = AsyncMock(return_value=[])
            mock_db.user_roles.find = MagicMock(return_value=_ur_cursor)
            mock_db.memberships.find_one = AsyncMock(return_value={"id": "mem-001"})
            mock_db.users.update_one = AsyncMock()

            with patch("routers.staff_management.asyncio.create_task", side_effect=_close_background_task):
                await assign_building_role(
                    user_id=strata_manager_user["id"],
                    body=body,
                    current_user=super_admin,
                )

            # Verify no legacy ec_position write occurred. The router may still
            # update users.role to the highest active role, so inspect all calls
            # instead of asserting update_one was never used.
            update_calls = mock_db.users.update_one.call_args_list
            ec_pos_call = next(
                (c for c in update_calls
                 if c[0][1].get("$set", {}).get("ec_position") == "CHAIRMAN"),
                None,
            )
            assert ec_pos_call is None

    @pytest.mark.asyncio
    async def test_owner_role_does_not_set_ec_position(
            self, super_admin, strata_manager_user, building_a, owner_role_def
    ):
        """Assigning owner role does NOT update ec_position (it's not an EC role)."""
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest

        body = BuildingRoleAssignRequest(
            role_name="owner",
            building_id=building_a["id"],
        )

        with patch("routers.staff_management.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value=building_a)
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.roles.find_one = AsyncMock(return_value=owner_role_def)
            mock_db.user_roles.find_one = AsyncMock(return_value=None)
            mock_db.user_roles.insert_one = AsyncMock()
            _ur_cursor = MagicMock()
            _ur_cursor.to_list = AsyncMock(return_value=[])
            mock_db.user_roles.find = MagicMock(return_value=_ur_cursor)
            mock_db.memberships.find_one = AsyncMock(return_value={"id": "mem-001"})
            update_mock = AsyncMock()
            mock_db.users.update_one = update_mock

            with patch("routers.staff_management.asyncio.create_task", side_effect=_close_background_task):
                await assign_building_role(
                    user_id=strata_manager_user["id"],
                    body=body,
                    current_user=super_admin,
                )

            # update_one should NOT have been called for ec_position
            if update_mock.called:
                call_args = update_mock.call_args
                assert "$set" not in call_args[0][1] or "ec_position" not in call_args[0][1].get("$set", {})

    @pytest.mark.asyncio
    async def test_membership_created_when_not_a_member(
            self, super_admin, strata_manager_user, building_a, ec_member_role_def
    ):
        """If not already a member of the building, membership is created."""
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest

        body = BuildingRoleAssignRequest(role_name="ec_member", building_id=building_a["id"])

        with patch("routers.staff_management.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value=building_a)
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.roles.find_one = AsyncMock(return_value=ec_member_role_def)
            mock_db.user_roles.find_one = AsyncMock(return_value=None)
            mock_db.user_roles.insert_one = AsyncMock()
            _ur_cursor = MagicMock()
            _ur_cursor.to_list = AsyncMock(return_value=[])
            mock_db.user_roles.find = MagicMock(return_value=_ur_cursor)
            mock_db.memberships.find_one = AsyncMock(return_value=None)  # not a member yet
            membership_insert = AsyncMock()
            mock_db.memberships.insert_one = membership_insert
            mock_db.users.update_one = AsyncMock()

            with patch("routers.staff_management.asyncio.create_task", side_effect=_close_background_task):
                await assign_building_role(
                    user_id=strata_manager_user["id"],
                    body=body,
                    current_user=super_admin,
                )

            assert membership_insert.called
            inserted_doc = membership_insert.call_args[0][0]
            assert inserted_doc["user_id"] == strata_manager_user["id"]
            assert inserted_doc["building_id"] == building_a["id"]

    @pytest.mark.asyncio
    async def test_non_admin_cannot_assign_roles(self, strata_manager_user, building_a):
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest
        from fastapi import HTTPException

        body = BuildingRoleAssignRequest(role_name="ec_member", building_id=building_a["id"])

        with pytest.raises(HTTPException) as exc:
            await assign_building_role(
                user_id=strata_manager_user["id"],
                body=body,
                current_user={"role": "chairman"},
            )
        assert exc.value.status_code == 403


class TestRevokeBuildingRole:
    """DELETE /admin/staff-users/{user_id}/building-roles/{assignment_id}"""

    @pytest.mark.asyncio
    async def test_revoke_active_assignment_succeeds(
            self, super_admin, strata_manager_user, active_assignment
    ):
        from routers.staff_management import revoke_building_role

        with patch("routers.staff_management.db") as mock_db:
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.user_roles.find_one = AsyncMock(return_value=active_assignment)
            update_mock = AsyncMock()
            mock_db.user_roles.update_one = update_mock
            # No remaining EC roles
            mock_db.user_roles.find_one.side_effect = [strata_manager_user, active_assignment, None]
            mock_db.users.update_one = AsyncMock()

            with patch("routers.staff_management.asyncio.create_task", side_effect=_close_background_task):
                result = await revoke_building_role(
                    user_id=strata_manager_user["id"],
                    assignment_id=active_assignment["id"],
                    current_user=super_admin,
                )
            assert result["assignment_id"] == active_assignment["id"]

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_returns_409(
            self, super_admin, strata_manager_user
    ):
        from routers.staff_management import revoke_building_role
        from fastapi import HTTPException

        inactive_assignment = {
            "id": "asgn-002",
            "user_id": strata_manager_user["id"],
            "role_id": "role-ec-member",
            "building_id": "building-east-001",
            "is_active": False,  # already revoked
        }

        with patch("routers.staff_management.db") as mock_db:
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.user_roles.find_one = AsyncMock(return_value=inactive_assignment)

            with pytest.raises(HTTPException) as exc:
                await revoke_building_role(
                    user_id=strata_manager_user["id"],
                    assignment_id="asgn-002",
                    current_user=super_admin,
                )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_revoke_unknown_assignment_returns_404(
            self, super_admin, strata_manager_user
    ):
        from routers.staff_management import revoke_building_role
        from fastapi import HTTPException

        with patch("routers.staff_management.db") as mock_db:
            mock_db.users.find_one = AsyncMock(return_value=strata_manager_user)
            mock_db.user_roles.find_one = AsyncMock(return_value=None)  # not found

            with pytest.raises(HTTPException) as exc:
                await revoke_building_role(
                    user_id=strata_manager_user["id"],
                    assignment_id="asgn-nonexistent",
                    current_user=super_admin,
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_non_admin_cannot_revoke(self, strata_manager_user, active_assignment):
        from routers.staff_management import revoke_building_role
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await revoke_building_role(
                user_id=strata_manager_user["id"],
                assignment_id=active_assignment["id"],
                current_user={"role": "ec_member"},
            )
        assert exc.value.status_code == 403


class TestCrossBuildingIsolation:
    """Data isolation — staff user cannot access another building's data."""

    def test_staff_roles_query_scoped_to_staff_roles_only(self, super_admin):
        """List query only includes STAFF_ROLES, never mixing owner/tenant/ec data."""
        from routers.staff_management import STAFF_ROLES
        # Verify no residency roles are in STAFF_ROLES
        residency_roles = {"owner", "tenant", "guest", "ec_member"}
        assert residency_roles.isdisjoint(STAFF_ROLES), \
            "STAFF_ROLES must not include residency or governance roles"

    @pytest.mark.asyncio
    async def test_assign_validates_building_exists_before_write(
            self, super_admin, strata_manager_user
    ):
        """Building must exist and be active — prevents assignment to arbitrary building IDs."""
        from routers.staff_management import assign_building_role, BuildingRoleAssignRequest
        from fastapi import HTTPException

        body = BuildingRoleAssignRequest(
            role_name="ec_member",
            building_id="attacker-injected-building-id",
        )

        with patch("routers.staff_management.db") as mock_db:
            mock_db.buildings.find_one = AsyncMock(return_value=None)  # not found

            with pytest.raises(HTTPException) as exc:
                await assign_building_role(
                    user_id=strata_manager_user["id"],
                    body=body,
                    current_user=super_admin,
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_staff_excludes_archived_users(self, super_admin):
        """Archived staff users are not returned in list."""
        from routers.staff_management import list_staff_users

        archived_sm = {
            "id": "user-sm-archived",
            "role": "strata_manager",
            "email": "old@example.com",
            "status": "archived",
        }

        with patch("routers.staff_management.db") as mock_db:
            # The query MUST include status not-in-archived filter
            mock_cursor = MagicMock()
            mock_cursor.to_list = AsyncMock(return_value=[])
            mock_db.users.find.return_value = mock_cursor

            await list_staff_users(current_user=super_admin)

            call_query = mock_db.users.find.call_args[0][0]
            # Verify status filter excludes archived
            assert "status" in call_query
            status_filter = call_query["status"]
            assert "$nin" in status_filter
            assert "archived" in status_filter["$nin"]
