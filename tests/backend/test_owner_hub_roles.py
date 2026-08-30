"""
test_owner_hub_roles.py — Tests verifying that chairman and ec_member roles
have proper access to OwnerHub backend endpoints, and that the OwnerHub
inherits owner privileges for EC roles.

These roles:
- chairman: EC role with ownership rights
- ec_member: EC role with ownership rights

Both should be able to access OwnerHub for their own properties. They must not
see another owner's investment property unless the owner explicitly shared that
property with their staff user id.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _user(role, unit_number="TH087", user_id="user-001"):
    return {
        "id": user_id,
        "email": f"{role}@example.com",
        "full_name": f"Test {role.title()}",
        "role": role,
        "unit_number": unit_number,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "building_id": "13195",
        "custom_permissions": {},
    }


class TestOwnerHubRoleAccess:
    """Test that all expected roles can access OwnerHub endpoints."""

    def test_allowed_roles_includes_ec_member(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "ec_member" in ALLOWED_ROLES

    def test_allowed_roles_includes_ec_member_for_chairman_position(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "ec_member" in ALLOWED_ROLES

    def test_allowed_roles_includes_owner(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "owner" in ALLOWED_ROLES

    def test_allowed_roles_excludes_tenant(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "tenant" not in ALLOWED_ROLES

    def test_allowed_roles_excludes_guest(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "guest" not in ALLOWED_ROLES


def _make_cursor(return_value):
    """Return a mock MongoDB cursor that supports .skip().limit().to_list() chaining."""
    cursor = MagicMock()
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=return_value)
    return cursor


class TestECMemberProperties:
    """EC property visibility is explicit-share only."""

    @pytest.mark.asyncio
    async def test_ec_member_list_properties_uses_staff_share_filter(self):
        from routers.ownerhub import list_properties

        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = _make_cursor([
            {"_id": "prop1", "id": "prop1", "address": "Unit 1", "owner_id": "other-owner", "shared_staff_ids": ["ec-001"]}
        ])

        with patch("database.db", mock_db):
            result = await list_properties(current_user=_user("ec_member", user_id="ec-001"))

        call_query = mock_db.owner_properties.find.call_args[0][0]
        assert call_query == {
            "$or": [
                {"shared_staff_ids": "ec-001"},
                {"shared_with_user_ids": "ec-001"},
                {"allowed_user_ids": "ec-001"},
                {"assigned_staff_ids": "ec-001"},
            ]
        }
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_ec_member_does_not_query_all_properties(self):
        from routers.ownerhub import list_properties

        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = _make_cursor([])

        with patch("database.db", mock_db):
            result = await list_properties(current_user=_user("ec_member", user_id="ec-001"))

        call_query = mock_db.owner_properties.find.call_args[0][0]
        assert call_query != {}, "EC members must not query every OwnerHub property"

    @pytest.mark.asyncio
    async def test_owner_list_properties_uses_owner_branch(self):
        """Regular owner should only see their own properties."""
        from routers.ownerhub import list_properties

        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = _make_cursor([])

        with patch("database.db", mock_db):
            result = await list_properties(current_user=_user("owner", user_id="owner-999"))

        assert mock_db.owner_properties.find.call_args[0][0] == {"owner_id": "owner-999"}


class TestECMemberUnits:
    """Test that ec_member sees all units in strata (admin branch)."""

    @pytest.mark.asyncio
    async def test_ec_member_list_units_no_unit_filter(self):
        from routers.ownerhub import list_strata_units

        mock_db = MagicMock()
        mock_db.units.find.return_value = _make_cursor([
            {"unit_number": "UA001"}, {"unit_number": "TH087"}
        ])

        with patch("database.db", mock_db):
            result = await list_strata_units(current_user=_user("ec_member"))

        query = mock_db.units.find.call_args[0][0]
        assert "unit_number" not in query, "ec_member should see all units, not filtered by unit_number"
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_owner_list_units_filtered_to_own_unit(self):
        """Owner should only get their own unit."""
        from routers.ownerhub import list_strata_units

        mock_db = MagicMock()
        mock_db.units.find.return_value.to_list = AsyncMock(return_value=[
            {"unit_number": "TH087"}
        ])

        with patch("database.db", mock_db):
            result = await list_strata_units(current_user=_user("owner", unit_number="TH087"))

        query = mock_db.units.find.call_args[0][0]
        assert query.get("unit_number") == "TH087", "Owner should be filtered to their unit"


class TestECMemberInspections:
    """EC inspection visibility follows explicit property sharing."""

    @pytest.mark.asyncio
    async def test_ec_member_inspections_use_staff_share_filter(self):
        from routers.ownerhub import list_all_inspections

        mock_db = MagicMock()
        # Simulate empty property list for the admin branch
        mock_db.owner_properties.find.return_value.__aiter__ = AsyncMock(return_value=iter([]))

        async def empty_aiter(self):
            return
            yield

        mock_db.owner_properties.find.return_value.__aiter__ = empty_aiter

        with patch("database.db", mock_db):
            result = await list_all_inspections(current_user=_user("ec_member", user_id="ec-001"))

        call_query = mock_db.owner_properties.find.call_args[0][0]
        assert call_query == {
            "$or": [
                {"shared_staff_ids": "ec-001"},
                {"shared_with_user_ids": "ec-001"},
                {"allowed_user_ids": "ec-001"},
                {"assigned_staff_ids": "ec-001"},
            ]
        }


class TestECMemberTCO:
    """Test that ec_member can view TCO for any unit."""

    @pytest.mark.asyncio
    async def test_ec_member_can_view_any_unit_tco(self):
        """ec_member should not be blocked by unit ownership check."""
        from routers.ownerhub import get_unit_tco

        with patch("services.ownerhub_service.compute_unit_tco",
                   new_callable=AsyncMock, return_value={"unit": "UA001"}) as mock_tco:
            # ec_member owns TH087 but is requesting TCO for UA001
            result = await get_unit_tco(
                unit_number="UA001",
                year="2026",
                vacancy_weeks=2,
                current_user=_user("ec_member", unit_number="TH087")
            )

        # Should complete without 403 error
        mock_tco.assert_called_once()

    @pytest.mark.asyncio
    async def test_owner_blocked_from_other_unit_tco(self):
        """Regular owner must not view another unit's TCO."""
        from routers.ownerhub import get_unit_tco
        from fastapi import HTTPException

        with patch("services.ownerhub_service.compute_unit_tco",
                   new_callable=AsyncMock, return_value={"unit": "UA001"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_unit_tco(
                    unit_number="UA001",
                    year="2026",
                    vacancy_weeks=2,
                    current_user=_user("owner", unit_number="TH087")  # owns TH087, requesting UA001
                )

        assert exc_info.value.status_code == 403
