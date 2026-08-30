"""
Tests for OwnerHub HTTP endpoints (routers/ownerhub.py).

Covers:
- GET  /owner-hub/properties        — role-based list filtering
- POST /owner-hub/properties        — owner/super_admin create; forbidden for others
- GET  /owner-hub/properties/{id}   — 404 on missing property
- PUT  /owner-hub/properties/{id}   — delegates to tenancy_service.update_property
- DELETE /owner-hub/properties/{id} — 204 on success; forbidden for non-owner
- GET  /owner-hub/weekly-radar      — returns list; empty when no properties
- GET  /owner-hub/units             — admin gets all; owner gets own unit
- GET  /owner-hub/unit-tco          — owner restricted to own unit; admin unrestricted
- Role gating: tenant/guest → 403

All tests are pure unit tests — no live database, no network.
Mocks: services.tenancy_service, services.ownerhub_service, database.db
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ─── Shared user fixtures ──────────────────────────────────────────────────

def _owner_user(uid="user-owner-1", unit="TH087"):
    return {
        "id": uid,
        "email": "owner@test.com",
        "role": "owner",
        "unit_number": unit,
        "is_approved": True,
        "is_active": True,
        "full_name": "Test Owner",
    }


def _super_admin_user():
    return {
        "id": "user-admin-1",
        "email": "admin@test.com",
        "role": "super_admin",
        "unit_number": "",
        "is_approved": True,
        "is_active": True,
        "full_name": "Super Admin",
    }


def _ec_user():
    return {
        "id": "user-ec-1",
        "email": "ec@test.com",
        "role": "ec_member",
        "unit_number": "UA001",
        "is_approved": True,
        "is_active": True,
        "full_name": "EC Member",
    }


def _strata_manager_user():
    return {
        "id": "user-sm-1",
        "email": "sm@test.com",
        "role": "strata_manager",
        "unit_number": "",
        "is_approved": True,
        "is_active": True,
        "full_name": "Strata Manager",
    }


def _rea_user():
    return {
        "id": "user-rea-1",
        "email": "rea@test.com",
        "role": "real_estate_agent",
        "unit_number": "",
        "is_approved": True,
        "is_active": True,
        "full_name": "Real Estate Agent",
    }


def _tenant_user():
    return {
        "id": "user-tenant-1",
        "email": "tenant@test.com",
        "role": "tenant",
        "unit_number": "TH071",
        "is_approved": True,
        "is_active": True,
        "full_name": "Tenant User",
    }


def _guest_user():
    return {
        "id": "user-guest-1",
        "email": "guest@test.com",
        "role": "guest",
        "unit_number": "",
        "is_approved": True,
        "is_active": True,
        "full_name": "Guest User",
    }


def _sample_property(prop_id="prop-001", owner_id="user-owner-1"):
    return {
        "id": prop_id,
        "address": "Unit TH087, East Gate Residences",
        "suburb": "Braddon",
        "state": "ACT",
        "postcode": "2612",
        "owner_id": owner_id,
        "status": "vacant",
        "assigned_agent_ids": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


# ─── Role gating ──────────────────────────────────────────────────────────

class TestOwnerHubRoleGating:
    """Tenant and guest roles must receive 403 from _require_ownerhub."""

    def test_require_ownerhub_blocks_tenant(self):
        """_require_ownerhub raises HTTPException 403 for tenant role."""
        from routers.ownerhub import _require_ownerhub
        with pytest.raises(HTTPException) as exc_info:
            _require_ownerhub(_tenant_user())
        assert exc_info.value.status_code == 403

    def test_require_ownerhub_blocks_guest(self):
        """_require_ownerhub raises HTTPException 403 for guest role."""
        from routers.ownerhub import _require_ownerhub
        with pytest.raises(HTTPException) as exc_info:
            _require_ownerhub(_guest_user())
        assert exc_info.value.status_code == 403

    def test_require_ownerhub_allows_owner(self):
        """_require_ownerhub returns user dict for owner role."""
        from routers.ownerhub import _require_ownerhub
        result = _require_ownerhub(_owner_user())
        assert result["role"] == "owner"

    def test_require_ownerhub_allows_super_admin(self):
        """_require_ownerhub returns user dict for super_admin."""
        from routers.ownerhub import _require_ownerhub
        result = _require_ownerhub(_super_admin_user())
        assert result["role"] == "super_admin"

    def test_require_ownerhub_allows_strata_manager(self):
        """_require_ownerhub returns user dict for strata_manager."""
        from routers.ownerhub import _require_ownerhub
        result = _require_ownerhub(_strata_manager_user())
        assert result["role"] == "strata_manager"

    def test_require_ownerhub_allows_real_estate_agent(self):
        """_require_ownerhub returns user dict for real_estate_agent."""
        from routers.ownerhub import _require_ownerhub
        result = _require_ownerhub(_rea_user())
        assert result["role"] == "real_estate_agent"


# ─── ALLOWED_ROLES constant ───────────────────────────────────────────────

class TestAllowedRolesConstant:
    """ALLOWED_ROLES set must contain expected roles."""

    def test_owner_in_allowed_roles(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "owner" in ALLOWED_ROLES

    def test_super_admin_in_allowed_roles(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "super_admin" in ALLOWED_ROLES

    def test_strata_manager_in_allowed_roles(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "strata_manager" in ALLOWED_ROLES

    def test_real_estate_agent_in_allowed_roles(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "real_estate_agent" in ALLOWED_ROLES

    def test_tenant_not_in_allowed_roles(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "tenant" not in ALLOWED_ROLES

    def test_guest_not_in_allowed_roles(self):
        from routers.ownerhub import ALLOWED_ROLES
        assert "guest" not in ALLOWED_ROLES


# ─── GET /owner-hub/properties ────────────────────────────────────────────

class TestListProperties:
    """list_properties routes to correct DB queries by role."""

    def test_ec_member_cannot_access_unshared_property_doc(self):
        """EC role alone is not enough to view a resident's OwnerHub property."""
        from services.tenancy_service import can_access_ownerhub_property

        prop = _sample_property(owner_id="resident-owner")
        assert can_access_ownerhub_property(prop, _ec_user()) is False

    def test_ec_member_can_access_explicitly_shared_property_doc(self):
        """Owner-shared staff ids grant property visibility."""
        from services.tenancy_service import can_access_ownerhub_property

        user = _ec_user()
        prop = {**_sample_property(owner_id="resident-owner"), "shared_staff_ids": [user["id"]]}
        assert can_access_ownerhub_property(prop, user) is True

    @pytest.mark.asyncio
    async def test_owner_queries_own_properties(self):
        """Owner role fetches only properties they created."""
        from routers import ownerhub
        user = _owner_user()
        raw_docs = [{"_id": "p1", "address": "Own", "owner_id": user["id"]}]
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=raw_docs)
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.list_properties(current_user=user)
        assert mock_db.owner_properties.find.call_args[0][0] == {"owner_id": user["id"]}
        assert result[0]["address"] == "Own"

    @pytest.mark.asyncio
    async def test_super_admin_queries_all_properties(self):
        """super_admin fetches all properties from db.owner_properties."""
        from routers import ownerhub
        user = _super_admin_user()
        raw_docs = [{"_id": "fake-id", "address": "Test"}]
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=raw_docs)
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.list_properties(current_user=user)
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_rea_queries_assigned_properties(self):
        """real_estate_agent fetches only properties where they are assigned."""
        from routers import ownerhub
        user = _rea_user()
        raw_docs = [{"_id": "p1", "address": "Assigned", "assigned_agent_ids": [user["id"]]}]
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=raw_docs)
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.list_properties(current_user=user)
        # Verify the filter used assigned_agent_ids
        call_args = mock_db.owner_properties.find.call_args
        assert "assigned_agent_ids" in call_args[0][0]
        assert result[0]["address"] == "Assigned"

    @pytest.mark.asyncio
    async def test_strata_manager_queries_explicitly_shared_properties(self):
        """strata_manager only fetches properties shared to their user id."""
        from routers import ownerhub
        user = _strata_manager_user()
        raw_docs = [{"_id": "p1", "address": "Shared", "shared_staff_ids": [user["id"]]}]
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=raw_docs)
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.list_properties(current_user=user)
        assert mock_db.owner_properties.find.call_args[0][0] == {
            "$or": [
                {"shared_staff_ids": user["id"]},
                {"shared_with_user_ids": user["id"]},
                {"allowed_user_ids": user["id"]},
                {"assigned_staff_ids": user["id"]},
            ]
        }
        assert len(result) == 1


# ─── POST /owner-hub/properties ───────────────────────────────────────────

class TestCreateProperty:
    """create_property is restricted to owner and super_admin roles."""

    @pytest.mark.asyncio
    async def test_owner_can_create_property(self):
        """Owner role successfully creates a property."""
        from routers import ownerhub
        from models.tenancy import PropertyCreate
        user = _owner_user()
        data = PropertyCreate(
            address="Unit TH087, East Gate", suburb="Braddon", state="ACT", postcode="2612"
        )
        expected = _sample_property(owner_id=user["id"])
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.create_property = AsyncMock(return_value=expected)
            result = await ownerhub.create_property(data=data, current_user=user)
        mock_svc.create_property.assert_awaited_once()
        assert result["owner_id"] == user["id"]

    @pytest.mark.asyncio
    async def test_super_admin_can_create_property(self):
        """super_admin can create a property."""
        from routers import ownerhub
        from models.tenancy import PropertyCreate
        user = _super_admin_user()
        data = PropertyCreate(
            address="Unit UA063, East Gate", suburb="Braddon", state="ACT", postcode="2612"
        )
        expected = _sample_property(owner_id=user["id"])
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.create_property = AsyncMock(return_value=expected)
            result = await ownerhub.create_property(data=data, current_user=user)
        assert result is not None

    @pytest.mark.asyncio
    async def test_ec_member_cannot_create_property(self):
        """EC member receives 403 when attempting to create a property."""
        from routers import ownerhub
        from models.tenancy import PropertyCreate
        user = _ec_user()
        data = PropertyCreate(
            address="Unit UA001", suburb="Braddon", state="ACT", postcode="2612"
        )
        with pytest.raises(HTTPException) as exc_info:
            await ownerhub.create_property(data=data, current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_rea_cannot_create_property(self):
        """Real estate agent receives 403 when attempting to create a property."""
        from routers import ownerhub
        from models.tenancy import PropertyCreate
        user = _rea_user()
        data = PropertyCreate(
            address="Unit UA010", suburb="Braddon", state="ACT", postcode="2612"
        )
        with pytest.raises(HTTPException) as exc_info:
            await ownerhub.create_property(data=data, current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_create_passes_owner_id_to_service(self):
        """create_property passes the current user's id as owner_id."""
        from routers import ownerhub
        from models.tenancy import PropertyCreate
        user = _owner_user(uid="specific-owner-id")
        data = PropertyCreate(
            address="12 Test Street", suburb="Braddon", state="ACT", postcode="2612"
        )
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.create_property = AsyncMock(return_value={"id": "new-prop"})
            await ownerhub.create_property(data=data, current_user=user)
        call_args = mock_svc.create_property.call_args
        assert call_args[0][0] == "specific-owner-id"


# ─── GET /owner-hub/properties/{id} ──────────────────────────────────────

class TestGetProperty:
    """get_property delegates to tenancy_service.get_property_by_id."""

    @pytest.mark.asyncio
    async def test_returns_property_for_valid_id(self):
        """Returns property dict when service succeeds."""
        from routers import ownerhub
        user = _owner_user()
        expected = _sample_property()
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(return_value=expected)
            result = await ownerhub.get_property(
                property_id="prop-001", current_user=user
            )
        assert result["id"] == "prop-001"

    @pytest.mark.asyncio
    async def test_propagates_404_from_service(self):
        """404 from service propagates as HTTPException."""
        from routers import ownerhub
        user = _owner_user()
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(
                side_effect=HTTPException(status_code=404, detail="Property not found")
            )
            with pytest.raises(HTTPException) as exc_info:
                await ownerhub.get_property(
                    property_id="nonexistent", current_user=user
                )
        assert exc_info.value.status_code == 404


# ─── PUT /owner-hub/properties/{id} ──────────────────────────────────────

class TestUpdateProperty:
    """update_property delegates to tenancy_service.update_property."""

    @pytest.mark.asyncio
    async def test_owner_can_update_own_property(self):
        """Owner can update their own property."""
        from routers import ownerhub
        from models.tenancy import PropertyUpdate
        user = _owner_user()
        data = PropertyUpdate(suburb="Civic")
        updated = {**_sample_property(), "suburb": "Civic"}
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.update_property = AsyncMock(return_value=updated)
            result = await ownerhub.update_property(
                property_id="prop-001", data=data, current_user=user
            )
        assert result["suburb"] == "Civic"

    @pytest.mark.asyncio
    async def test_update_passes_exclude_none_payload(self):
        """Only non-None fields are passed to the service."""
        from routers import ownerhub
        from models.tenancy import PropertyUpdate
        user = _owner_user()
        data = PropertyUpdate(suburb="Braddon")  # only suburb set
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.update_property = AsyncMock(return_value={"id": "prop-001"})
            await ownerhub.update_property(
                property_id="prop-001", data=data, current_user=user
            )
        call_args = mock_svc.update_property.call_args
        payload = call_args[0][1]  # second positional arg is updates dict
        assert "suburb" in payload
        # Fields not set should be absent (exclude_none=True)
        assert "address" not in payload


# ─── DELETE /owner-hub/properties/{id} ───────────────────────────────────

class TestDeleteProperty:
    """delete_property is restricted to owner and super_admin."""

    @pytest.mark.asyncio
    async def test_owner_can_delete_own_property(self):
        """Owner can delete their own property (returns None — 204)."""
        from routers import ownerhub
        user = _owner_user()
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.delete_property = AsyncMock(return_value=True)
            result = await ownerhub.delete_property(
                property_id="prop-001", current_user=user
            )
        mock_svc.delete_property.assert_awaited_once_with("prop-001", user["id"])
        # 204 endpoint returns None implicitly
        assert result is None

    @pytest.mark.asyncio
    async def test_super_admin_can_delete_property(self):
        """super_admin can delete any property."""
        from routers import ownerhub
        user = _super_admin_user()
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.delete_property = AsyncMock(return_value=True)
            result = await ownerhub.delete_property(
                property_id="prop-001", current_user=user
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_ec_member_cannot_delete_property(self):
        """EC member receives 403 when trying to delete a property."""
        from routers import ownerhub
        user = _ec_user()
        with pytest.raises(HTTPException) as exc_info:
            await ownerhub.delete_property(property_id="prop-001", current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_strata_manager_cannot_delete_property(self):
        """Strata manager receives 403 (not owner or super_admin)."""
        from routers import ownerhub
        user = _strata_manager_user()
        with pytest.raises(HTTPException) as exc_info:
            await ownerhub.delete_property(property_id="prop-001", current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_rea_cannot_delete_property(self):
        """Real estate agent receives 403 when trying to delete a property."""
        from routers import ownerhub
        user = _rea_user()
        with pytest.raises(HTTPException) as exc_info:
            await ownerhub.delete_property(property_id="prop-001", current_user=user)
        assert exc_info.value.status_code == 403


# ─── GET /owner-hub/weekly-radar ─────────────────────────────────────────

class TestWeeklyRadar:
    """weekly_radar returns list of radar objects (one per property)."""

    @pytest.mark.asyncio
    async def test_owner_with_no_properties_returns_empty_list(self):
        """When owner has no registered properties, returns []."""
        from routers import ownerhub
        user = _owner_user()
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[])  # no properties
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.weekly_radar(current_user=user)
        assert result == []

    @pytest.mark.asyncio
    async def test_owner_radar_filters_by_owner_id(self):
        """Owner query uses owner_id field."""
        from routers import ownerhub
        user = _owner_user(uid="user-owner-99")
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            await ownerhub.weekly_radar(current_user=user)
        call_args = mock_db.owner_properties.find.call_args
        assert call_args[0][0].get("owner_id") == "user-owner-99"

    @pytest.mark.asyncio
    async def test_super_admin_radar_queries_all_properties(self):
        """super_admin passes empty filter to find all properties."""
        from routers import ownerhub
        user = _super_admin_user()
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.weekly_radar(current_user=user)
        call_args = mock_db.owner_properties.find.call_args
        assert call_args[0][0] == {}  # no filter for admin
        assert result == []

    @pytest.mark.asyncio
    async def test_rea_radar_filters_by_assigned_agent(self):
        """REA query uses assigned_agent_ids field."""
        from routers import ownerhub
        user = _rea_user()
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            await ownerhub.weekly_radar(current_user=user)
        call_args = mock_db.owner_properties.find.call_args
        assert "assigned_agent_ids" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_radar_calls_generate_weekly_radar_per_property(self):
        """weekly_radar calls ohsvc.generate_weekly_radar for each property."""
        from routers import ownerhub
        user = _owner_user()
        prop_id = "507f1f77bcf86cd799439011"  # valid ObjectId hex
        from bson import ObjectId
        raw_docs = [{"_id": ObjectId(prop_id)}]
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=raw_docs)
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        expected_radar = {"property_id": prop_id, "issues": []}
        with patch("database.db", mock_db):
            with patch("routers.ownerhub.ohsvc") as mock_ohsvc:
                mock_ohsvc.generate_weekly_radar = AsyncMock(return_value=expected_radar)
                result = await ownerhub.weekly_radar(current_user=user)
        mock_ohsvc.generate_weekly_radar.assert_awaited_once()
        assert result == [expected_radar]

    @pytest.mark.asyncio
    async def test_radar_skips_property_on_error(self):
        """If ohsvc.generate_weekly_radar raises, that property is skipped."""
        from routers import ownerhub
        from bson import ObjectId
        user = _owner_user()
        prop_id = "507f1f77bcf86cd799439011"
        raw_docs = [{"_id": ObjectId(prop_id)}]
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=raw_docs)
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            with patch("routers.ownerhub.ohsvc") as mock_ohsvc:
                mock_ohsvc.generate_weekly_radar = AsyncMock(
                    side_effect=Exception("service failure")
                )
                result = await ownerhub.weekly_radar(current_user=user)
        # Error swallowed — empty list
        assert result == []


# ─── GET /owner-hub/units ─────────────────────────────────────────────────

class TestListStrataUnits:
    """list_strata_units — admin sees all, owner sees own unit only."""

    @pytest.mark.asyncio
    async def test_super_admin_gets_all_units(self):
        """super_admin fetches all units (no unit_number filter)."""
        from routers import ownerhub
        user = _super_admin_user()
        all_units = [
            {"unit_number": "TH087", "owner_name": "Test Owner"},
            {"unit_number": "UA001", "owner_name": "Other Owner"},
        ]
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=all_units)
        mock_db = MagicMock()
        mock_db.units.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.list_strata_units(current_user=user)
        assert len(result) == 2
        # Admin uses no filter
        call_args = mock_db.units.find.call_args
        assert call_args[0][0] == {}

    @pytest.mark.asyncio
    async def test_owner_gets_only_own_unit(self):
        """Owner fetches only their own unit from db.units."""
        from routers import ownerhub
        user = _owner_user(unit="TH087")
        own_unit = [{"unit_number": "TH087", "owner_name": "Test Owner"}]
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=own_unit)
        mock_db = MagicMock()
        mock_db.units.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.list_strata_units(current_user=user)
        assert len(result) == 1
        assert result[0]["unit_number"] == "TH087"
        # Filter should include unit_number
        call_args = mock_db.units.find.call_args
        assert call_args[0][0].get("unit_number") == "TH087"

    @pytest.mark.asyncio
    async def test_strata_manager_gets_all_units(self):
        """strata_manager has same admin-level access to units list."""
        from routers import ownerhub
        user = _strata_manager_user()
        all_units = [{"unit_number": "UA001"}]
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=all_units)
        mock_db = MagicMock()
        mock_db.units.find.return_value = mock_cursor
        with patch("database.db", mock_db):
            result = await ownerhub.list_strata_units(current_user=user)
        call_args = mock_db.units.find.call_args
        assert call_args[0][0] == {}


# ─── GET /owner-hub/unit-tco ──────────────────────────────────────────────

class TestGetUnitTco:
    """get_unit_tco — owner can only see own unit; admin can see any unit."""

    @pytest.mark.asyncio
    async def test_owner_can_view_own_unit_tco(self):
        """Owner requesting their own unit_number gets 200."""
        from routers import ownerhub
        user = _owner_user(unit="TH087")
        tco_result = {"unit_number": "TH087", "net_cashflow": 12500.0}
        with patch("routers.ownerhub.ohsvc") as mock_ohsvc:
            mock_ohsvc.compute_unit_tco = AsyncMock(return_value=tco_result)
            result = await ownerhub.get_unit_tco(
                unit_number="TH087", year=None,
                vacancy_weeks=2, current_user=user
            )
        assert result["unit_number"] == "TH087"

    @pytest.mark.asyncio
    async def test_owner_cannot_view_other_unit_tco(self):
        """Owner requesting a different unit receives 403."""
        from routers import ownerhub
        user = _owner_user(unit="TH087")
        with pytest.raises(HTTPException) as exc_info:
            await ownerhub.get_unit_tco(
                unit_number="UA001", year=None,
                vacancy_weeks=2, current_user=user
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_super_admin_can_view_any_unit_tco(self):
        """super_admin is not restricted to a specific unit."""
        from routers import ownerhub
        user = _super_admin_user()
        tco_result = {"unit_number": "TH087", "net_cashflow": 9000.0}
        with patch("routers.ownerhub.ohsvc") as mock_ohsvc:
            mock_ohsvc.compute_unit_tco = AsyncMock(return_value=tco_result)
            result = await ownerhub.get_unit_tco(
                unit_number="TH087", year="2026",
                vacancy_weeks=2, current_user=user
            )
        mock_ohsvc.compute_unit_tco.assert_awaited_once()
        assert result == tco_result

    @pytest.mark.asyncio
    async def test_strata_manager_can_view_any_unit_tco(self):
        """strata_manager is not restricted (in admin role set)."""
        from routers import ownerhub
        user = _strata_manager_user()
        tco_result = {"unit_number": "UA001", "net_cashflow": 7500.0}
        with patch("routers.ownerhub.ohsvc") as mock_ohsvc:
            mock_ohsvc.compute_unit_tco = AsyncMock(return_value=tco_result)
            result = await ownerhub.get_unit_tco(
                unit_number="UA001", year=None,
                vacancy_weeks=2, current_user=user
            )
        assert result == tco_result

    @pytest.mark.asyncio
    async def test_unit_tco_defaults_year_to_current(self):
        """When year is None, compute_unit_tco is called with current year string."""
        import datetime
        from routers import ownerhub
        user = _super_admin_user()
        expected_year = str(datetime.date.today().year)
        with patch("routers.ownerhub.ohsvc") as mock_ohsvc:
            mock_ohsvc.compute_unit_tco = AsyncMock(return_value={})
            await ownerhub.get_unit_tco(
                unit_number="TH087", year=None,
                vacancy_weeks=2, current_user=user
            )
        call_args = mock_ohsvc.compute_unit_tco.call_args
        assert call_args[0][1] == expected_year


# ─── Router registration ──────────────────────────────────────────────────

class TestRouterRegistration:
    """Verify the ownerhub router is registered in the main FastAPI app."""

    def test_ownerhub_router_prefix(self):
        """The router has /owner-hub prefix."""
        from routers.ownerhub import router
        assert router.prefix == "/owner-hub"

    def test_ownerhub_router_has_properties_routes(self):
        """Router exposes GET and POST /owner-hub/properties routes."""
        from routers.ownerhub import router
        paths = [r.path for r in router.routes]
        assert "/owner-hub/properties" in paths

    def test_ownerhub_router_has_weekly_radar_route(self):
        """Router exposes /owner-hub/weekly-radar route."""
        from routers.ownerhub import router
        paths = [r.path for r in router.routes]
        assert "/owner-hub/weekly-radar" in paths

    def test_ownerhub_router_has_units_route(self):
        """Router exposes /owner-hub/units route."""
        from routers.ownerhub import router
        paths = [r.path for r in router.routes]
        assert "/owner-hub/units" in paths

    def test_ownerhub_router_has_unit_tco_route(self):
        """Router exposes /owner-hub/unit-tco route."""
        from routers.ownerhub import router
        paths = [r.path for r in router.routes]
        assert "/owner-hub/unit-tco" in paths


# ─── Tenancy endpoints ────────────────────────────────────────────────────

class TestTenancyEndpoints:
    """Tenancy CRUD delegates to tenancy_service correctly."""

    @pytest.mark.asyncio
    async def test_create_tenancy_delegates_to_service(self):
        """create_tenancy calls svc.create_tenancy with correct args."""
        from routers import ownerhub
        from models.tenancy import TenancyCreate
        user = _owner_user()
        data = TenancyCreate(
            property_id="prop-001",
            tenant_name="Emma Wilson",
            tenant_email="emma@example.com",
            lease_start="2026-03-01",
            lease_end="2027-02-28",
            rent_amount=650.0,
            bond_amount=2600.0,
        )
        expected = {"id": "tenancy-001", "property_id": "prop-001"}
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(return_value=_sample_property())
            mock_svc.create_tenancy = AsyncMock(return_value=expected)
            result = await ownerhub.create_tenancy(data=data, current_user=user)
        mock_svc.create_tenancy.assert_awaited_once()
        assert result["property_id"] == "prop-001"

    @pytest.mark.asyncio
    async def test_list_tenancies_access_checks_property(self):
        """list_tenancies calls get_property_by_id for access control."""
        from routers import ownerhub
        user = _owner_user()
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(return_value=_sample_property())
            mock_svc.get_tenancies_for_property = AsyncMock(return_value=[])
            result = await ownerhub.list_tenancies(
                property_id="prop-001", current_user=user
            )
        mock_svc.get_property_by_id.assert_awaited_once_with("prop-001", user)
        assert result == []

    @pytest.mark.asyncio
    async def test_record_payment_delegates_to_service(self):
        """record_payment verifies tenancy exists then records payment."""
        from routers import ownerhub
        from models.tenancy import RentTransactionCreate
        user = _owner_user()
        data = RentTransactionCreate(
            tenancy_id="tenancy-001",
            amount=650.0,
            transaction_date="2026-03-10",
        )
        existing_tenancy = {
            "id": "tenancy-001",
            "property_id": "prop-001",
            "status": "active",
            "rent_amount": 650.0,
            "payment_frequency": "weekly",
        }
        expected_tx = {"id": "tx-001", "tenancy_id": "tenancy-001", "amount": 650.0}
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_tenancy_by_id = AsyncMock(return_value=existing_tenancy)
            mock_svc.get_property_by_id = AsyncMock(return_value=_sample_property())
            mock_svc.record_rent_payment = AsyncMock(return_value=expected_tx)
            result = await ownerhub.record_payment(
                tenancy_id="tenancy-001", data=data, current_user=user
            )
        mock_svc.record_rent_payment.assert_awaited_once()
        assert result["amount"] == 650.0

    @pytest.mark.asyncio
    async def test_get_ledger_checks_tenancy_existence(self):
        """get_ledger verifies tenancy exists before fetching ledger."""
        from routers import ownerhub
        user = _owner_user()
        existing_tenancy = {"id": "tenancy-001", "property_id": "prop-001"}
        ledger_entries = [{"id": "entry-001", "amount": 650.0}]
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_tenancy_by_id = AsyncMock(return_value=existing_tenancy)
            mock_svc.get_property_by_id = AsyncMock(return_value=_sample_property())
            mock_svc.get_rent_ledger = AsyncMock(return_value=ledger_entries)
            result = await ownerhub.get_ledger(tenancy_id="tenancy-001", current_user=user)
        mock_svc.get_tenancy_by_id.assert_awaited_once_with("tenancy-001")
        assert result == ledger_entries


# ─── _doc helper ─────────────────────────────────────────────────────────

class TestDocHelper:
    """_doc normalises MongoDB _id to id string."""

    def test_doc_converts_object_id(self):
        from routers.ownerhub import _doc
        from bson import ObjectId
        oid = ObjectId()
        result = _doc({"_id": oid, "address": "Test"})
        assert "id" in result
        assert "_id" not in result
        assert result["id"] == str(oid)

    def test_doc_handles_none(self):
        from routers.ownerhub import _doc
        assert _doc(None) is None

    def test_doc_preserves_other_fields(self):
        from routers.ownerhub import _doc
        from bson import ObjectId
        result = _doc({"_id": ObjectId(), "suburb": "Braddon", "postcode": "2612"})
        assert result["suburb"] == "Braddon"
        assert result["postcode"] == "2612"


# ─── New endpoint tests (fixes for 405/404/422 issues) ───────────────────

class TestPostPropertyTenancy:
    """POST /properties/{property_id}/tenancy — create tenancy via property URL."""

    @pytest.mark.asyncio
    async def test_create_tenancy_for_property_success(self):
        """Owner can create a tenancy via /properties/{id}/tenancy."""
        from routers import ownerhub
        from models.tenancy import TenancyCreate
        user = _owner_user()
        prop = {"id": "prop-001", "owner_id": "user-owner-1", "address": "1 Test St"}
        new_tenancy = {"id": "tenancy-001", "property_id": "prop-001", "tenant_name": "Jane Doe"}
        data = TenancyCreate(
            tenant_name="Jane Doe", tenant_email="jane@example.com",
            lease_start="2024-01-01", lease_end="2025-01-01",
            rent_amount=600.0, bond_amount=1200.0
        )
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(return_value=prop)
            mock_svc.create_tenancy = AsyncMock(return_value=new_tenancy)
            result = await ownerhub.create_tenancy_for_property(
                property_id="prop-001", data=data, current_user=user
            )
        mock_svc.get_property_by_id.assert_awaited_once_with("prop-001", user)
        mock_svc.create_tenancy.assert_awaited_once()
        assert result["id"] == "tenancy-001"

    @pytest.mark.asyncio
    async def test_create_tenancy_for_property_forbidden_for_tenant(self):
        """Tenants cannot access the endpoint."""
        from routers import ownerhub
        user = _tenant_user()
        with pytest.raises(HTTPException) as exc_info:
            ownerhub._require_ownerhub(user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_create_tenancy_injects_property_id(self):
        """The property_id from the URL is injected into the payload."""
        from routers import ownerhub
        from models.tenancy import TenancyCreate
        user = _owner_user()
        prop = {"id": "prop-abc", "owner_id": "user-owner-1"}
        data = TenancyCreate(
            tenant_name="Bob", tenant_email="bob@example.com",
            lease_start="2024-06-01", lease_end="2025-06-01",
            rent_amount=550.0, bond_amount=1100.0
        )
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(return_value=prop)
            mock_svc.create_tenancy = AsyncMock(return_value={"id": "t-new"})
            await ownerhub.create_tenancy_for_property(
                property_id="prop-abc", data=data, current_user=user
            )
        call_args = mock_svc.create_tenancy.call_args
        assert call_args[0][0] == "prop-abc"
        payload = call_args[0][1]
        assert payload["property_id"] == "prop-abc"


class TestGetPropertyLedger:
    """GET /properties/{property_id}/ledger — convenience ledger endpoint."""

    @pytest.mark.asyncio
    async def test_returns_ledger_for_active_tenancy(self):
        """Returns ledger entries when active tenancy exists."""
        from routers import ownerhub
        user = _owner_user()
        prop = {"id": "prop-001"}
        tenancy = {"id": "tenancy-001", "property_id": "prop-001"}
        entries = [{"id": "e1", "amount": 600.0, "type": "rent"}]
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(return_value=prop)
            mock_svc.get_active_tenancy = AsyncMock(return_value=tenancy)
            mock_svc.get_rent_ledger = AsyncMock(return_value=entries)
            result = await ownerhub.get_property_ledger(
                property_id="prop-001", current_user=user
            )
        mock_svc.get_active_tenancy.assert_awaited_once_with("prop-001")
        mock_svc.get_rent_ledger.assert_awaited_once_with("tenancy-001", limit=50)
        assert result == entries

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_tenancy(self):
        """Returns [] gracefully when no active tenancy exists."""
        from routers import ownerhub
        user = _owner_user()
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(return_value={"id": "prop-001"})
            mock_svc.get_active_tenancy = AsyncMock(return_value=None)
            result = await ownerhub.get_property_ledger(
                property_id="prop-001", current_user=user
            )
        assert result == []
        mock_svc.get_rent_ledger.assert_not_called() if hasattr(mock_svc.get_rent_ledger, "assert_not_called") else None

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(self):
        """Custom limit is passed to get_rent_ledger."""
        from routers import ownerhub
        user = _super_admin_user()
        tenancy = {"id": "t-001"}
        with patch("routers.ownerhub.svc") as mock_svc:
            mock_svc.get_property_by_id = AsyncMock(return_value={"id": "p-001"})
            mock_svc.get_active_tenancy = AsyncMock(return_value=tenancy)
            mock_svc.get_rent_ledger = AsyncMock(return_value=[])
            await ownerhub.get_property_ledger(
                property_id="p-001", limit=25, current_user=user
            )
        mock_svc.get_rent_ledger.assert_awaited_once_with("t-001", limit=25)


class TestGetInspectionsAggregate:
    """GET /inspections — aggregate inspections across all user's properties."""

    @pytest.mark.asyncio
    async def test_owner_gets_own_property_inspections(self):
        """Owner receives inspections from their own properties (empty when no props)."""
        from routers import ownerhub
        user = _owner_user()

        # Patch the database.db that is imported locally inside list_all_inspections
        mock_cursor = MagicMock()

        async def _empty_aiter(self):
            return
            yield  # pragma: no cover

        mock_cursor.__aiter__ = _empty_aiter
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor

        with patch("database.db", mock_db):
            result = await ownerhub.list_all_inspections(current_user=user)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_forbidden_for_tenant(self):
        """Tenants are blocked by _require_ownerhub."""
        from routers import ownerhub
        user = _tenant_user()
        with pytest.raises(HTTPException) as exc_info:
            ownerhub._require_ownerhub(user)
        assert exc_info.value.status_code == 403


class TestTCOModelOptional:
    """TrueOwnershipCostInput — property_id and year are now Optional."""

    def test_model_accepts_body_without_property_id(self):
        """Model can be instantiated without property_id (URL-provided)."""
        from models.tenancy import TrueOwnershipCostInput
        obj = TrueOwnershipCostInput(vacancy_weeks=3)
        assert obj.property_id is None
        assert obj.year is None

    def test_model_accepts_all_optional_fields(self):
        """Instantiation with only optional overrides works."""
        from models.tenancy import TrueOwnershipCostInput
        obj = TrueOwnershipCostInput(
            annual_rental_income=28000.0,
            mortgage_balance=350000.0,
            interest_rate=6.2,
            vacancy_weeks=2,
        )
        assert obj.annual_rental_income == 28000.0
        assert obj.property_id is None
        assert obj.year is None

    def test_model_still_accepts_explicit_property_id(self):
        """property_id can still be set explicitly (backward compat)."""
        from models.tenancy import TrueOwnershipCostInput
        obj = TrueOwnershipCostInput(property_id="prop-123", year="2025-2026")
        assert obj.property_id == "prop-123"
        assert obj.year == "2025-2026"

    @pytest.mark.asyncio
    async def test_post_tenancies_requires_property_id_in_body(self):
        """POST /tenancies raises 422 when property_id missing from body."""
        from routers import ownerhub
        from models.tenancy import TenancyCreate
        user = _owner_user()
        data = TenancyCreate(
            tenant_name="Test", tenant_email="t@x.com",
            lease_start="2024-01-01", lease_end="2025-01-01",
            rent_amount=500.0, bond_amount=1000.0,
        )
        assert data.property_id is None

        with pytest.raises(HTTPException) as exc:
            await ownerhub.create_tenancy(data=data, current_user=user)
        assert exc.value.status_code == 422
