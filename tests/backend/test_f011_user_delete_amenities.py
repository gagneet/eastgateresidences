"""
Tests for F-011 fixes:
  - DELETE /users/{user_id}: chairman-block removed; multi-building cleanup
  - GET/POST /amenities, DELETE /amenities/{key}: new amenity management endpoints
  - GET /parcels/{parcel_id}/track: new parcel tracking endpoint

Run from project root:
    backend/venv/bin/pytest tests/backend/test_f011_user_delete_amenities.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

import server


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_perms(can_manage_users=True):
    p = MagicMock()
    p.can_manage_users = can_manage_users
    return p


def _make_user(role="strata_manager", uid="mgr-001"):
    return {"id": uid, "role": role, "full_name": "Test User", "building_id": "13195"}


def _membership_doc(building_id="13195", user_id="target-001"):
    return {"building_id": building_id, "user_id": user_id, "is_active": True}


# ─── DELETE /users/{user_id} ─────────────────────────────────────────────────

class TestDeleteUserChairmanAllowed:
    """Chairman must be able to delete users (chairman-block was incorrectly present)."""

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_chairman_can_delete_user(self, mock_db, mock_perms):
        mock_perms.return_value = _make_perms(can_manage_users=True)

        mock_db.users.find_one = AsyncMock(side_effect=lambda q, *a, **kw: (
            None if "email" in q
            else {"id": "target-001", "role": "owner", "full_name": "Owner"}
        ))
        mock_db.memberships.find_one = AsyncMock(return_value=_membership_doc())
        mock_db.user_units.update_many = AsyncMock(return_value=MagicMock(modified_count=1))
        mock_db.notifications.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
        mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        mock_db.memberships.count_documents = AsyncMock(return_value=0)
        mock_db.users.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

        result = await server.delete_user(
            "target-001",
            current_user=_make_user(role="chairman", uid="chair-001"),
            building_id="13195",
        )
        assert result["message"] == "User membership archived successfully"

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_no_permission_still_403(self, mock_db, mock_perms):
        mock_perms.return_value = _make_perms(can_manage_users=False)

        with pytest.raises(HTTPException) as exc:
            await server.delete_user(
                "target-001",
                current_user=_make_user(role="tenant"),
                building_id="13195",
            )
        assert exc.value.status_code == 403


class TestDeleteUserMultiBuildingCleanup:
    """Global user record must only be archived when no memberships remain."""

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_single_building_user_archived_globally(self, mock_db, mock_perms):
        mock_perms.return_value = _make_perms(can_manage_users=True)

        mock_db.users.find_one = AsyncMock(side_effect=lambda q, *a, **kw: (
            None if "email" in q
            else {"id": "target-001", "role": "owner", "full_name": "Owner"}
        ))
        mock_db.memberships.find_one = AsyncMock(return_value=_membership_doc())
        mock_db.user_units.update_many = AsyncMock(return_value=MagicMock())
        mock_db.notifications.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
        mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock())
        # No remaining memberships after deletion
        mock_db.memberships.count_documents = AsyncMock(return_value=0)
        mock_db.users.update_one = AsyncMock(return_value=MagicMock())

        await server.delete_user(
            "target-001",
            current_user=_make_user(),
            building_id="13195",
        )

        # Global user update MUST have been called with archived status
        call_args = mock_db.users.update_one.call_args
        assert call_args is not None
        set_doc = call_args[0][1]["$set"]
        assert set_doc["status"] == "archived"
        assert set_doc["is_active"] is False

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_multi_building_user_not_archived_globally(self, mock_db, mock_perms):
        mock_perms.return_value = _make_perms(can_manage_users=True)

        mock_db.users.find_one = AsyncMock(side_effect=lambda q, *a, **kw: (
            None if "email" in q
            else {"id": "target-001", "role": "owner", "full_name": "Owner"}
        ))
        mock_db.memberships.find_one = AsyncMock(return_value=_membership_doc())
        mock_db.user_units.update_many = AsyncMock(return_value=MagicMock())
        mock_db.notifications.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
        mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock())
        # User still has membership in another building
        mock_db.memberships.count_documents = AsyncMock(return_value=1)
        mock_db.users.update_one = AsyncMock(return_value=MagicMock())

        await server.delete_user(
            "target-001",
            current_user=_make_user(),
            building_id="13195",
        )

        # Global user record must NOT be archived
        mock_db.users.update_one.assert_not_called()

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_membership_deleted_before_count(self, mock_db, mock_perms):
        """delete_one must be called before count_documents so count is accurate."""
        mock_perms.return_value = _make_perms(can_manage_users=True)

        call_order = []

        mock_db.users.find_one = AsyncMock(side_effect=lambda q, *a, **kw: (
            None if "email" in q
            else {"id": "target-001", "role": "owner", "full_name": "Owner"}
        ))
        mock_db.memberships.find_one = AsyncMock(return_value=_membership_doc())
        mock_db.user_units.update_many = AsyncMock(return_value=MagicMock())
        mock_db.notifications.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))

        async def _delete_one(*a, **kw):
            call_order.append("delete_one")
            return MagicMock()

        async def _count_documents(*a, **kw):
            call_order.append("count_documents")
            return 0

        mock_db.memberships.delete_one = _delete_one
        mock_db.memberships.count_documents = _count_documents
        mock_db.users.update_one = AsyncMock(return_value=MagicMock())

        await server.delete_user(
            "target-001",
            current_user=_make_user(),
            building_id="13195",
        )

        assert call_order == ["delete_one", "count_documents"]

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_multi_tenant_isolation_separate_buildings(self, mock_db, mock_perms):
        """Deleting user from building 13195 must not affect building 16244 data."""
        mock_perms.return_value = _make_perms(can_manage_users=True)

        mock_db.users.find_one = AsyncMock(side_effect=lambda q, *a, **kw: (
            None if "email" in q
            else {"id": "target-001", "role": "owner", "full_name": "Owner"}
        ))
        mock_db.memberships.find_one = AsyncMock(return_value=_membership_doc())
        mock_db.user_units.update_many = AsyncMock(return_value=MagicMock())
        mock_db.notifications.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
        mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock())
        mock_db.memberships.count_documents = AsyncMock(return_value=0)
        mock_db.users.update_one = AsyncMock(return_value=MagicMock())

        await server.delete_user(
            "target-001",
            current_user=_make_user(),
            building_id="13195",
        )

        # user_units update must be scoped to 13195 only
        update_filter = mock_db.user_units.update_many.call_args[0][0]
        assert update_filter["building_id"] == "13195"

        # notifications delete must be scoped to 13195 only
        notify_filter = mock_db.notifications.delete_many.call_args[0][0]
        assert notify_filter["building_id"] == "13195"

        # membership delete must be scoped to 13195 only
        delete_filter = mock_db.memberships.delete_one.call_args[0][0]
        assert delete_filter["building_id"] == "13195"


# ─── GET /amenities ──────────────────────────────────────────────────────────

class TestListAmenities:

    @pytest.mark.asyncio
    @patch("server.require_feature", return_value=lambda: {})
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_returns_defaults_when_none_configured(self, mock_db, mock_perms, _feat):
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        mock_db.building_amenities.find.return_value = cursor

        result = await server.list_amenities(
            current_user=_make_user(),
            building_id="13195",
            _feature={},
        )

        assert result["using_defaults"] is True
        assert len(result["amenities"]) > 0

    @pytest.mark.asyncio
    @patch("server.require_feature", return_value=lambda: {})
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_returns_building_amenities(self, mock_db, mock_perms, _feat):
        custom = [{"key": "rooftop", "label": "Rooftop Deck", "building_id": "13195"}]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=custom)
        mock_db.building_amenities.find.return_value = cursor

        result = await server.list_amenities(
            current_user=_make_user(),
            building_id="13195",
            _feature={},
        )

        assert result["using_defaults"] is False
        assert result["amenities"][0]["key"] == "rooftop"

    @pytest.mark.asyncio
    @patch("server.require_feature", return_value=lambda: {})
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_building_16244_isolated(self, mock_db, mock_perms, _feat):
        """list_amenities for building 16244 must use its own DB query."""
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        mock_db.building_amenities.find.return_value = cursor

        await server.list_amenities(
            current_user=_make_user(),
            building_id="16244",
            _feature={},
        )

        query = mock_db.building_amenities.find.call_args[0][0]
        assert query["building_id"] == "16244"


# ─── POST /amenities ─────────────────────────────────────────────────────────

class TestAddAmenity:

    @pytest.mark.asyncio
    @patch("server.require_feature", return_value=lambda: {})
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_creates_amenity(self, mock_db, mock_perms, _feat):
        mock_perms.return_value = _make_perms(can_manage_users=True)
        mock_db.building_amenities.find_one = AsyncMock(return_value=None)
        mock_db.building_amenities.insert_one = AsyncMock(return_value=MagicMock())

        data = server.AmenityCreate(key="sauna", label="Sauna", icon="Flame")
        result = await server.add_amenity(
            data=data,
            current_user=_make_user(),
            building_id="13195",
            _feature={},
        )

        assert result["key"] == "sauna"
        assert result["building_id"] == "13195"
        assert "_id" not in result

    @pytest.mark.asyncio
    @patch("server.require_feature", return_value=lambda: {})
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_duplicate_key_returns_409(self, mock_db, mock_perms, _feat):
        mock_perms.return_value = _make_perms(can_manage_users=True)
        mock_db.building_amenities.find_one = AsyncMock(return_value={"key": "pool"})

        with pytest.raises(HTTPException) as exc:
            await server.add_amenity(
                data=server.AmenityCreate(key="pool", label="Pool", icon="Waves"),
                current_user=_make_user(),
                building_id="13195",
                _feature={},
            )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    @patch("server.require_feature", return_value=lambda: {})
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_no_permission_403(self, mock_db, mock_perms, _feat):
        mock_perms.return_value = _make_perms(can_manage_users=False)
        mock_db.building_amenities.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await server.add_amenity(
                data=server.AmenityCreate(key="gym", label="Gym", icon="Dumbbell"),
                current_user=_make_user(role="owner"),
                building_id="13195",
                _feature={},
            )
        assert exc.value.status_code == 403


# ─── DELETE /amenities/{amenity_key} ─────────────────────────────────────────

class TestRemoveAmenity:

    @pytest.mark.asyncio
    @patch("server.require_feature", return_value=lambda: {})
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_removes_amenity(self, mock_db, mock_perms, _feat):
        mock_perms.return_value = _make_perms(can_manage_users=True)
        mock_db.building_amenities.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=1)
        )

        result = await server.remove_amenity(
            amenity_key="pool",
            current_user=_make_user(),
            building_id="13195",
            _feature={},
        )
        assert result["message"] == "Amenity removed"
        delete_filter = mock_db.building_amenities.delete_one.call_args[0][0]
        assert delete_filter["building_id"] == "13195"
        assert delete_filter["key"] == "pool"

    @pytest.mark.asyncio
    @patch("server.require_feature", return_value=lambda: {})
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_missing_amenity_returns_404(self, mock_db, mock_perms, _feat):
        mock_perms.return_value = _make_perms(can_manage_users=True)
        mock_db.building_amenities.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=0)
        )

        with pytest.raises(HTTPException) as exc:
            await server.remove_amenity(
                amenity_key="nonexistent",
                current_user=_make_user(),
                building_id="13195",
                _feature={},
            )
        assert exc.value.status_code == 404


# ─── GET /parcels/{parcel_id}/track ──────────────────────────────────────────

class TestTrackParcelStatus:

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_no_tracking_number_returns_message(self, mock_db, mock_perms):
        mock_perms.return_value = _make_perms(can_manage_users=True)
        mock_db.parcels.find_one = AsyncMock(return_value={
            "id": "parcel-001",
            "building_id": "13195",
            "unit_number": "101",
            "carrier": "auspost",
            "tracking_number": None,
        })

        result = await server.track_parcel_status(
            parcel_id="parcel-001",
            current_user=_make_user(),
            building_id="13195",
        )

        assert result["tracking_number"] is None
        assert "No tracking number" in result["message"]

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_resident_cannot_track_other_unit_parcel(self, mock_db, mock_perms):
        mock_perms.return_value = _make_perms(can_manage_users=False)
        mock_db.parcels.find_one = AsyncMock(return_value={
            "id": "parcel-002",
            "building_id": "13195",
            "unit_number": "202",  # different unit
            "carrier": "dhl",
            "tracking_number": "TRACK123",
        })

        with pytest.raises(HTTPException) as exc:
            await server.track_parcel_status(
                parcel_id="parcel-002",
                current_user={**_make_user(role="owner"), "unit_number": "101"},
                building_id="13195",
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_resident_can_track_own_unit_parcel(self, mock_db, mock_perms):
        mock_perms.return_value = _make_perms(can_manage_users=False)
        mock_db.parcels.find_one = AsyncMock(return_value={
            "id": "parcel-003",
            "building_id": "13195",
            "unit_number": "101",
            "carrier": "other",
            "tracking_number": None,
        })

        result = await server.track_parcel_status(
            parcel_id="parcel-003",
            current_user={**_make_user(role="owner"), "unit_number": "101"},
            building_id="13195",
        )
        # No tracking number → message response, no 403
        assert "message" in result

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_parcel_not_found_404(self, mock_db, mock_perms):
        mock_perms.return_value = _make_perms(can_manage_users=True)
        mock_db.parcels.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await server.track_parcel_status(
                parcel_id="missing",
                current_user=_make_user(),
                building_id="13195",
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    @patch("server.get_user_permissions")
    @patch("server.db")
    async def test_building_16244_scoped(self, mock_db, mock_perms):
        """Parcel lookup must be scoped to the correct building."""
        mock_perms.return_value = _make_perms(can_manage_users=True)
        mock_db.parcels.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await server.track_parcel_status(
                parcel_id="parcel-x",
                current_user=_make_user(),
                building_id="16244",
            )
        assert exc.value.status_code == 404
        # Confirm query was scoped to 16244
        query = mock_db.parcels.find_one.call_args[0][0]
        assert query["building_id"] == "16244"
