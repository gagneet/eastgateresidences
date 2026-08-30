"""
Regression tests for BOLA hardening on unit and marketplace listing endpoints.

Verifies that update/delete operations on units and listings are scoped to the
authenticated building — a request from building A cannot touch records in building B.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import server
from server import app, get_current_user, get_current_building

client = TestClient(app)

BUILDING_A = "13195"
BUILDING_B = "16244"

AUTH_HEADER = {"Authorization": "Bearer test-token"}


def _admin_user(uid="admin-1"):
    return {
        "id": uid,
        "full_name": "Admin User",
        "role": "super_admin",
        "is_approved": True,
        "permissions": {"can_manage_users": True},
    }


def _owner_user(uid="owner-1"):
    return {
        "id": uid,
        "full_name": "Owner User",
        "role": "owner",
        "is_approved": True,
        "permissions": {},
    }


# ---------------------------------------------------------------------------
# update_unit — PUT /units/{unit_id}
# ---------------------------------------------------------------------------

_UNIT_PAYLOAD = {
    "unit_number": "42",
    "unit_type": "apartment",
    "bedrooms": 2,
    "bathrooms": 1,
    "parking_spaces": 1,
    "entitlement_units": 100,
    "is_owner_occupied": True,
}

_UNIT_DOC_EXTRA = {
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


class TestUpdateUnitBOLA:
    def _mock_db(self, update_matched: int, found_unit=None):
        update_result = MagicMock(matched_count=update_matched)
        mock_units = SimpleNamespace(
            update_one=AsyncMock(return_value=update_result),
            find_one=AsyncMock(return_value=found_unit),
        )
        return SimpleNamespace(units=mock_units)

    def test_update_unit_scopes_filter_to_building(self, monkeypatch):
        """update_one filter MUST include building_id."""
        unit_id = "unit-abc"
        unit_doc = {"id": unit_id, "building_id": BUILDING_A, **_UNIT_PAYLOAD, **_UNIT_DOC_EXTRA}
        mock_db = self._mock_db(update_matched=1, found_unit=unit_doc)

        app.dependency_overrides[get_current_user] = lambda: _admin_user()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A

        try:
            with monkeypatch.context() as m:
                m.setattr(server, "db", mock_db)
                response = client.put(
                    f"/api/units/{unit_id}", json=_UNIT_PAYLOAD, headers=AUTH_HEADER
                )

            assert response.status_code == 200
            filter_used = mock_db.units.update_one.call_args[0][0]
            assert filter_used["id"] == unit_id
            assert filter_used["building_id"] == BUILDING_A
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_update_unit_cross_tenant_returns_404(self, monkeypatch):
        """Updating a unit that belongs to building B while authenticated as building A returns 404."""
        unit_id = "unit-building-b"
        # matched_count=0 simulates the unit not existing in building A
        mock_db = self._mock_db(update_matched=0)

        app.dependency_overrides[get_current_user] = lambda: _admin_user()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A

        try:
            with monkeypatch.context() as m:
                m.setattr(server, "db", mock_db)
                response = client.put(
                    f"/api/units/{unit_id}", json=_UNIT_PAYLOAD, headers=AUTH_HEADER
                )

            assert response.status_code == 404
            body = response.json()
            msg = (body.get("detail") or body.get("error", {}).get("message", "")).lower()
            assert "building" in msg
            # find_one must NOT have been called (early return after matched_count check)
            mock_db.units.find_one.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_update_unit_nonexistent_returns_404(self, monkeypatch):
        """Updating a unit that does not exist at all returns 404."""
        mock_db = self._mock_db(update_matched=0)

        app.dependency_overrides[get_current_user] = lambda: _admin_user()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A

        try:
            with monkeypatch.context() as m:
                m.setattr(server, "db", mock_db)
                response = client.put(
                    "/api/units/no-such-unit", json=_UNIT_PAYLOAD, headers=AUTH_HEADER
                )

            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


# ---------------------------------------------------------------------------
# update_listing — PUT /listings/{listing_id}
# ---------------------------------------------------------------------------

_LISTING_PAYLOAD = {
    "title": "Test Listing",
    "description": "A test listing",
    "listing_type": "sell",
    "is_public": True,
}


class TestUpdateListingBOLA:
    def _mock_db(self, find_result=None):
        mock_listings = SimpleNamespace(
            find_one=AsyncMock(return_value=find_result),
            update_one=AsyncMock(return_value=MagicMock()),
        )
        return SimpleNamespace(listings=mock_listings)

    def test_update_listing_scopes_lookup_to_building(self, monkeypatch):
        """Initial find_one and update_one MUST include building_id."""
        listing_id = "listing-abc"
        owner = _owner_user()
        listing_doc = {
            "id": listing_id,
            "building_id": BUILDING_A,
            "created_by": owner["id"],
            **_LISTING_PAYLOAD,
        }
        mock_db = self._mock_db(find_result=listing_doc)
        # Second find_one (post-update fetch) also returns the doc
        mock_db.listings.find_one.side_effect = [listing_doc, listing_doc]

        app.dependency_overrides[get_current_user] = lambda: owner
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A

        try:
            with monkeypatch.context() as m:
                m.setattr(server, "db", mock_db)
                response = client.put(
                    f"/api/listings/{listing_id}",
                    json=_LISTING_PAYLOAD,
                    headers=AUTH_HEADER,
                )

            assert response.status_code == 200
            first_filter = mock_db.listings.find_one.call_args_list[0][0][0]
            assert first_filter["building_id"] == BUILDING_A
            update_filter = mock_db.listings.update_one.call_args[0][0]
            assert update_filter["building_id"] == BUILDING_A
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_update_listing_cross_tenant_returns_404(self, monkeypatch):
        """Listing from building B is not found when queried under building A context."""
        listing_id = "listing-building-b"
        mock_db = self._mock_db(find_result=None)  # scoped find returns nothing

        app.dependency_overrides[get_current_user] = lambda: _owner_user()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A

        try:
            with monkeypatch.context() as m:
                m.setattr(server, "db", mock_db)
                response = client.put(
                    f"/api/listings/{listing_id}",
                    json=_LISTING_PAYLOAD,
                    headers=AUTH_HEADER,
                )

            assert response.status_code == 404
            mock_db.listings.update_one.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


# ---------------------------------------------------------------------------
# delete_listing — DELETE /listings/{listing_id}
# ---------------------------------------------------------------------------


class TestDeleteListingBOLA:
    def _mock_db(self, find_result=None, deleted_count: int = 1):
        mock_listings = SimpleNamespace(
            find_one=AsyncMock(return_value=find_result),
            delete_one=AsyncMock(return_value=MagicMock(deleted_count=deleted_count)),
        )
        return SimpleNamespace(listings=mock_listings)

    def test_delete_listing_scopes_to_building(self, monkeypatch):
        """delete_one filter MUST include building_id."""
        listing_id = "listing-abc"
        owner = _owner_user()
        listing_doc = {
            "id": listing_id,
            "building_id": BUILDING_A,
            "created_by": owner["id"],
            **_LISTING_PAYLOAD,
        }
        mock_db = self._mock_db(find_result=listing_doc, deleted_count=1)

        app.dependency_overrides[get_current_user] = lambda: owner
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A

        try:
            with monkeypatch.context() as m:
                m.setattr(server, "db", mock_db)
                response = client.delete(
                    f"/api/listings/{listing_id}", headers=AUTH_HEADER
                )

            assert response.status_code == 200
            delete_filter = mock_db.listings.delete_one.call_args[0][0]
            assert delete_filter["id"] == listing_id
            assert delete_filter["building_id"] == BUILDING_A
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_delete_listing_cross_tenant_returns_404(self, monkeypatch):
        """Listing from building B is not found when deleting under building A context."""
        listing_id = "listing-building-b"
        mock_db = self._mock_db(find_result=None)  # scoped find returns nothing

        app.dependency_overrides[get_current_user] = lambda: _owner_user()
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A

        try:
            with monkeypatch.context() as m:
                m.setattr(server, "db", mock_db)
                response = client.delete(
                    f"/api/listings/{listing_id}", headers=AUTH_HEADER
                )

            assert response.status_code == 404
            mock_db.listings.delete_one.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_delete_listing_deleted_count_zero_returns_404(self, monkeypatch):
        """If delete_one matches nothing (race condition / wrong tenant), return 404."""
        listing_id = "listing-gone"
        owner = _owner_user()
        listing_doc = {
            "id": listing_id,
            "building_id": BUILDING_A,
            "created_by": owner["id"],
            **_LISTING_PAYLOAD,
        }
        mock_db = self._mock_db(find_result=listing_doc, deleted_count=0)

        app.dependency_overrides[get_current_user] = lambda: owner
        app.dependency_overrides[get_current_building] = lambda: BUILDING_A

        try:
            with monkeypatch.context() as m:
                m.setattr(server, "db", mock_db)
                response = client.delete(
                    f"/api/listings/{listing_id}", headers=AUTH_HEADER
                )

            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)
