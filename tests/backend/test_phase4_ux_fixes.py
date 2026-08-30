"""
Tests for Phase 4 UX Fixes:
  - Key/Fob register endpoints (GET/POST /building/keys-fobs, PUT/DELETE /building/keys-fobs/{id})
  - Expired accounts reactivation (JSON body, not query params)
  - Parcel owner self-registration (status=expected, unit enforcement)
  - Smart Requests manager queue (manager sees all, resident sees own only)

All tests mock MongoDB; no real DB writes occur. Fully idempotent.
Multi-tenant isolation checked for building 13195 vs 16244.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(__file__), "../../backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

BUILDING_ID = "13195"
BUILDING_ID_OTHER = "16244"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _user(role="owner", unit="UA042", building_id=BUILDING_ID):
    return {
        "id": f"user-{role}-001",
        "role": role,
        "email": f"{role}@test.com",
        "full_name": "Test User",
        "building_id": building_id,
        "unit_number": unit,
        "is_active": True,
    }


def _fob(fob_id="fob-001", unit="UA042", key_type="garage_fob", status="active", building_id=BUILDING_ID):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": fob_id,
        "building_id": building_id,
        "unit_number": unit,
        "key_type": key_type,
        "serial_number": "SN-001",
        "status": status,
        "issued_to_name": "Test Owner",  # matches KeyFobResponse field name
        "issued_to_user_id": None,
        "issued_date": now[:10],
        "returned_date": None,
        "notes": "",
        "created_at": now,
        "updated_at": now,
        "created_by_name": "Admin",
    }


def _parcel(parcel_id="parcel-001", unit="UA042", status="received", building_id=BUILDING_ID):
    return {
        "id": parcel_id,
        "building_id": building_id,
        "unit_number": unit,
        "carrier": "auspost",
        "status": status,
        "tracking_number": "TEST123",
        "description": "Test parcel",
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Key/Fob Register Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestKeyFobEndpoints:
    """Tests for /building/keys-fobs CRUD endpoints in server.py.
    Actual function names: list_key_fobs, issue_key_fob, update_key_fob, delete_key_fob.
    """

    @pytest.mark.asyncio
    async def test_list_key_fobs_manager_sees_all(self):
        """Manager role query must NOT include unit_number restriction."""
        from server import list_key_fobs

        manager = _user(role="strata_manager")
        mock_db = MagicMock()
        # list_key_fobs uses .find().sort().to_list(500) — all must be AsyncMock-compatible
        mock_to_list = AsyncMock(return_value=[
            _fob("fob-001", "UA042"),
            _fob("fob-002", "UA043", key_type="entry_key"),
        ])
        mock_sort = MagicMock()
        mock_sort.to_list = mock_to_list
        mock_find = MagicMock(return_value=mock_sort)
        mock_sort.sort = MagicMock(return_value=mock_sort)
        mock_db.key_fob_register.find = mock_find

        with patch("server.db", mock_db):
            result = await list_key_fobs(
                unit_number=None,
                key_type=None,
                status=None,
                current_user=manager,
                building_id=BUILDING_ID,
            )

        call_query = mock_db.key_fob_register.find.call_args[0][0]
        assert "unit_number" not in call_query, "Manager must not be filtered by unit"
        assert call_query["building_id"] == BUILDING_ID

    @pytest.mark.asyncio
    async def test_list_key_fobs_resident_scoped_to_own_unit(self):
        """Owner role query must be automatically scoped to their unit."""
        from server import list_key_fobs

        owner = _user(role="owner", unit="UA042")
        mock_db = MagicMock()
        mock_to_list = AsyncMock(return_value=[])
        mock_sort = MagicMock()
        mock_sort.to_list = mock_to_list
        mock_sort.sort = MagicMock(return_value=mock_sort)
        mock_db.key_fob_register.find = MagicMock(return_value=mock_sort)

        with patch("server.db", mock_db):
            await list_key_fobs(
                unit_number=None,
                key_type=None,
                status=None,
                current_user=owner,
                building_id=BUILDING_ID,
            )

        call_query = mock_db.key_fob_register.find.call_args[0][0]
        assert call_query.get("unit_number") == "UA042", (
            "Resident query must be scoped to their own unit"
        )
        assert call_query["building_id"] == BUILDING_ID

    @pytest.mark.asyncio
    async def test_list_key_fobs_building_isolation(self):
        """building_id from auth token must always be included in the query."""
        from server import list_key_fobs

        manager = _user(role="strata_manager", building_id=BUILDING_ID)
        mock_db = MagicMock()
        mock_to_list = AsyncMock(return_value=[])
        mock_sort = MagicMock()
        mock_sort.to_list = mock_to_list
        mock_sort.sort = MagicMock(return_value=mock_sort)
        mock_db.key_fob_register.find = MagicMock(return_value=mock_sort)

        with patch("server.db", mock_db):
            await list_key_fobs(
                unit_number=None, key_type=None, status=None,
                current_user=manager, building_id=BUILDING_ID,
            )

        call_query = mock_db.key_fob_register.find.call_args[0][0]
        assert call_query["building_id"] == BUILDING_ID
        assert call_query["building_id"] != BUILDING_ID_OTHER

    @pytest.mark.asyncio
    async def test_issue_key_fob_sets_building_id(self):
        """POST /building/keys-fobs must inject building_id into the stored document."""
        from server import issue_key_fob, KeyFobCreate, KeyFobResponse

        manager = _user(role="strata_manager")
        inserted_doc = {
            "id": "new-fob-id",
            "building_id": BUILDING_ID,
            "unit_number": "UA043",
            "key_type": "garage_fob",
            "serial_number": "SN-001",
            "issued_to_name": "New Owner",
            "issued_to_user_id": None,
            "issued_date": "2025-01-01",
            "returned_date": None,
            "status": "active",
            "notes": None,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "created_by_name": "Admin",
        }
        mock_db = MagicMock()
        mock_db.key_fob_register.insert_one = AsyncMock(return_value=MagicMock())

        payload = KeyFobCreate(
            unit_number="UA043",
            key_type="garage_fob",
            issued_to_name="New Owner",
        )

        with patch("server.db", mock_db):
            # Manually invoke so we can inspect insert_one args
            # (response_model strips _id so we check the inserted doc directly)
            from server import _effective_role
            with patch("server._effective_role", return_value="strata_manager"):
                # Call directly; response_model validation needs full doc
                import uuid as _uuid
                with patch("server.uuid.uuid4", return_value=_uuid.UUID("00000000-0000-0000-0000-000000000001")):
                    with patch("server.datetime") as mock_dt:
                        mock_dt.now.return_value.isoformat.return_value = "2025-01-01T00:00:00Z"
                        mock_dt.now.return_value.__getitem__ = lambda s, k: "2025-01-01"
                        try:
                            await issue_key_fob(
                                data=payload,
                                current_user=manager,
                                building_id=BUILDING_ID,
                            )
                        except Exception:
                            pass  # response_model validation may fail on mock; we check insert_one

        inserted = mock_db.key_fob_register.insert_one.call_args[0][0]
        assert inserted["building_id"] == BUILDING_ID
        assert inserted["unit_number"] == "UA043"

    @pytest.mark.asyncio
    async def test_delete_key_fob_cross_building_denied(self):
        """DELETE /building/keys-fobs/{id} must reject fobs from a different building."""
        from server import delete_key_fob
        from fastapi import HTTPException

        manager = _user(role="strata_manager", building_id=BUILDING_ID)
        other_building_fob = _fob("fob-other", building_id=BUILDING_ID_OTHER)

        mock_db = MagicMock()
        mock_db.key_fob_register.find_one = AsyncMock(return_value=other_building_fob)

        with patch("server.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await delete_key_fob(
                    fob_id="fob-other",
                    current_user=manager,
                    building_id=BUILDING_ID,
                )
        assert exc_info.value.status_code in (403, 404)


# ─────────────────────────────────────────────────────────────────────────────
# Expired Accounts Reactivation Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestExpiredAccountsReactivation:
    """POST /admin/reactivate-user accepts JSON body with user_unit_id + new_expiration_date."""

    def test_reactivate_request_model_fields(self):
        """ReactivateUserRequest requires user_unit_id and new_expiration_date."""
        from server import ReactivateUserRequest

        req = ReactivateUserRequest(
            user_unit_id="uu-123",
            new_expiration_date="2027-01-01",
            reason="Renewed lease",
        )
        assert req.user_unit_id == "uu-123"
        assert req.new_expiration_date == "2027-01-01"
        assert req.reason == "Renewed lease"

    def test_reactivate_request_model_requires_user_unit_id(self):
        """ReactivateUserRequest must fail if user_unit_id is missing."""
        from server import ReactivateUserRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ReactivateUserRequest(new_expiration_date="2027-01-01")

    def test_reactivate_request_model_requires_expiration_date(self):
        """ReactivateUserRequest must fail if new_expiration_date is missing."""
        from server import ReactivateUserRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ReactivateUserRequest(user_unit_id="uu-123")

    @pytest.mark.asyncio
    async def test_reactivate_user_updates_user_unit_record(self):
        """Reactivation sets is_active=True on the user_unit document."""
        from server import reactivate_expired_user, ReactivateUserRequest

        admin = _user(role="super_admin")
        user_unit_doc = {
            "id": "uu-001",
            "user_id": "user-target",
            "unit_number": "UA042",
            "building_id": BUILDING_ID,
            "is_active": False,
            "role": "owner",
            "role_at_unit": "owner",  # required by reactivate_expired_user logic
        }

        mock_db = MagicMock()
        mock_db.user_units.find_one = AsyncMock(return_value=user_unit_doc)
        mock_db.user_units.update_one = AsyncMock()
        mock_db.users.update_one = AsyncMock()

        with patch("server.db", mock_db):
            await reactivate_expired_user(
                data=ReactivateUserRequest(
                    user_unit_id="uu-001",
                    new_expiration_date="2027-01-01",
                    reason="Test reactivation",
                ),
                current_user=admin,
                building_id=BUILDING_ID,
            )

        mock_db.user_units.update_one.assert_called()
        call_filter, call_update = mock_db.user_units.update_one.call_args[0]
        assert call_filter.get("id") == "uu-001"
        set_fields = call_update.get("$set", {})
        assert set_fields.get("is_active") is True

    @pytest.mark.asyncio
    async def test_reactivate_user_non_admin_denied(self):
        """Only super_admin and chairman can reactivate expired users."""
        from server import reactivate_expired_user, ReactivateUserRequest
        from fastapi import HTTPException

        owner = _user(role="owner")
        mock_db = MagicMock()

        with patch("server.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await reactivate_expired_user(
                    data=ReactivateUserRequest(
                        user_unit_id="uu-001",
                        new_expiration_date="2027-01-01",
                    ),
                    current_user=owner,
                    building_id=BUILDING_ID,
                )
        assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Parcel Owner Self-Registration Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestParcelOwnerSelfRegistration:
    """Owner/resident must be able to register expected parcels with status='expected'."""

    @pytest.mark.asyncio
    async def test_owner_self_register_gets_expected_status(self):
        """When an owner calls log_parcel, status must be set to 'expected'."""
        from server import log_parcel, ParcelCreate

        owner = _user(role="owner", unit="UA042")
        mock_db = MagicMock()
        mock_db.parcels.insert_one = AsyncMock(return_value=MagicMock())

        payload = ParcelCreate(
            unit_number="UA042",
            carrier="auspost",
            tracking_number="AUS123",
            description="Expected delivery",
        )

        with patch("server.db", mock_db):
            try:
                await log_parcel(
                    data=payload,
                    current_user=owner,
                    building_id=BUILDING_ID,
                )
            except Exception:
                pass  # response_model may fail on mock; we only care about insert

        assert mock_db.parcels.insert_one.called, "insert_one must be called"
        inserted = mock_db.parcels.insert_one.call_args[0][0]
        assert inserted["status"] == "expected", (
            f"Owner self-registration must use status='expected', got '{inserted['status']}'"
        )
        assert inserted["building_id"] == BUILDING_ID

    @pytest.mark.asyncio
    async def test_owner_cannot_log_different_unit(self):
        """Owner submitting a different unit_number has it silently corrected to their own unit.
        Commit b29ff895 removed the 403 mismatch check to fix false rejections; the backend
        now ignores body unit_number for resident users and always uses their account unit."""
        from server import log_parcel, ParcelCreate

        owner = _user(role="owner", unit="UA042")  # owns UA042

        payload = ParcelCreate(
            unit_number="UA099",  # different unit — silently overridden
            carrier="auspost",
        )

        mock_db = MagicMock()
        mock_db.parcels.insert_one = AsyncMock(return_value=MagicMock())

        with patch("server.db", mock_db):
            await log_parcel(
                data=payload,
                current_user=owner,
                building_id=BUILDING_ID,
            )

        assert mock_db.parcels.insert_one.called
        inserted = mock_db.parcels.insert_one.call_args[0][0]
        assert inserted["unit_number"] == "UA042", "unit must be owner's own unit, not the submitted UA099"

    @pytest.mark.asyncio
    async def test_staff_log_gets_received_status(self):
        """When staff (super_admin) logs a parcel, status must be 'received'."""
        from server import log_parcel, ParcelCreate

        staff = _user(role="super_admin")
        staff["unit_number"] = None

        mock_db = MagicMock()
        mock_db.parcels.insert_one = AsyncMock(return_value=MagicMock())

        payload = ParcelCreate(
            unit_number="UA042",
            carrier="dhl",
            tracking_number="DHL123",
        )

        with patch("server.db", mock_db):
            try:
                await log_parcel(
                    data=payload,
                    current_user=staff,
                    building_id=BUILDING_ID,
                )
            except Exception:
                pass

        assert mock_db.parcels.insert_one.called
        inserted = mock_db.parcels.insert_one.call_args[0][0]
        assert inserted["status"] == "received", (
            f"Staff log must use status='received', got '{inserted['status']}'"
        )

    @pytest.mark.asyncio
    async def test_resident_without_unit_assignment_rejected(self):
        """An owner with no unit_number on their account must get 400, not a crash."""
        from server import log_parcel, ParcelCreate
        from fastapi import HTTPException

        owner_no_unit = _user(role="owner", unit=None)
        owner_no_unit["unit_number"] = None

        # unit_number is required in model; we supply one — backend must still reject
        # because the *user account* has no unit assigned (unit_number=None)
        payload = ParcelCreate(
            unit_number="UA042",
            carrier="auspost",
        )

        with patch("server.db", MagicMock()):
            with pytest.raises(HTTPException) as exc_info:
                await log_parcel(
                    data=payload,
                    current_user=owner_no_unit,
                    building_id=BUILDING_ID,
                )
        # 400 = account has no unit; 403 = unit mismatch
        assert exc_info.value.status_code in (400, 403)


# ─────────────────────────────────────────────────────────────────────────────
# Smart Requests Manager Queue Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSmartRequestsManagerQueue:
    """Manager roles must see all requests; residents see only their own.
    Actual signature: list_workflow_requests(status, request_type, current_user, building_id)
    """

    @staticmethod
    def _make_find_mock(docs):
        """Build a find() mock that supports both the legacy async-iter pattern and
        the new `.sort(...).to_list(...)` chain used by list_workflow_requests."""
        cursor = MagicMock()

        async def _aiter(_self=None):
            for doc in docs:
                yield doc

        cursor.__aiter__ = _aiter
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=list(docs))
        return MagicMock(return_value=cursor)

    @pytest.mark.asyncio
    async def test_manager_sees_all_requests_no_user_filter(self):
        """Manager query must NOT include submitted_by_user_id filter."""
        from routers.workflow_requests import list_workflow_requests

        manager = _user(role="strata_manager")
        mock_db = MagicMock()
        mock_db.workflow_requests.find = self._make_find_mock([
            {"id": "req-001", "building_id": BUILDING_ID, "submitted_by_user_id": "u1"},
            {"id": "req-002", "building_id": BUILDING_ID, "submitted_by_user_id": "u2"},
        ])

        with patch("routers.workflow_requests.db", mock_db):
            await list_workflow_requests(
                status=None,
                request_type=None,
                current_user=manager,
                building_id=BUILDING_ID,
            )

        query = mock_db.workflow_requests.find.call_args[0][0]
        assert "submitted_by_user_id" not in query, (
            "Manager query must not be filtered to a specific user"
        )

    @pytest.mark.asyncio
    async def test_owner_query_includes_own_user_id(self):
        """Owner query must include submitted_by_user_id = own user id."""
        from routers.workflow_requests import list_workflow_requests

        owner = _user(role="owner")
        mock_db = MagicMock()
        mock_db.workflow_requests.find = self._make_find_mock([
            {"id": "req-mine", "building_id": BUILDING_ID, "submitted_by_user_id": "user-owner-001"},
        ])

        with patch("routers.workflow_requests.db", mock_db):
            await list_workflow_requests(
                status=None,
                request_type=None,
                current_user=owner,
                building_id=BUILDING_ID,
            )

        query = mock_db.workflow_requests.find.call_args[0][0]
        assert query.get("submitted_by_user_id") == "user-owner-001", (
            "Owner query must filter to their own user id"
        )

    @pytest.mark.asyncio
    async def test_manager_query_excludes_test_data(self):
        """All queries must exclude is_test_data=True documents."""
        from routers.workflow_requests import list_workflow_requests

        manager = _user(role="strata_manager")
        mock_db = MagicMock()
        mock_db.workflow_requests.find = self._make_find_mock([])

        with patch("routers.workflow_requests.db", mock_db):
            await list_workflow_requests(
                status=None,
                request_type=None,
                current_user=manager,
                building_id=BUILDING_ID,
            )

        query = mock_db.workflow_requests.find.call_args[0][0]
        assert query.get("is_test_data") == {"$ne": True}, (
            "Query must exclude test data with {$ne: True}"
        )

    @pytest.mark.asyncio
    async def test_status_filter_applied_when_provided(self):
        """Status filter must be included in query when status param is given."""
        from routers.workflow_requests import list_workflow_requests

        manager = _user(role="strata_manager")
        mock_db = MagicMock()
        mock_db.workflow_requests.find = self._make_find_mock([])

        with patch("routers.workflow_requests.db", mock_db):
            await list_workflow_requests(
                status="open",
                request_type=None,
                current_user=manager,
                building_id=BUILDING_ID,
            )

        query = mock_db.workflow_requests.find.call_args[0][0]
        assert query.get("status") == "open"
