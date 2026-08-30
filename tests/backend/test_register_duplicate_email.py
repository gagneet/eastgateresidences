"""
Unit tests for duplicate-email behaviour in POST /api/auth/register.

Three distinct paths:
  A.  Email exists, password_hash set, unit_number present
      → 409 already_registered (message includes unit)
  B.  Email exists, no password_hash (MRI/import stub)
      → account is claimed: update_one existing record, follow normal approval flow
  B'. Email exists, password_hash set, but unit_number blank (partial/admin-created account)
      → also claimed: account could not meaningfully log in without a unit

All tests use AsyncMock — no live DB or backend required.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest
from starlette.background import BackgroundTasks
from bson import ObjectId

from routers.auth import register, UserCreate
from request_context import set_ctx_building_id
from utils.rate_limit import _DummyLimiter


def _make_scope():
    return {
        "type": "http", "method": "POST", "path": "/api/auth/register",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 12345),
    }


def _user_data(**kwargs):
    # Default to tenant so the owner+owner multi-unit-owner check doesn't fire.
    defaults = dict(
        email="owner@example.com",
        password="pytest-fixture-password-not-a-credential",
        full_name="Test Owner",
        role="tenant",
        unit_number="101",
        phone="0400000000",
        by_laws_acknowledged=True,
    )
    defaults.update(kwargs)
    return UserCreate(**defaults)


def _mock_db(existing_user, unit_doc=None):
    mock_db = MagicMock()
    mock_db.users.find_one = AsyncMock(return_value=existing_user)
    mock_db.units.find_one = AsyncMock(return_value=unit_doc or {"unit_number": "101", "building_id": "13195"})
    mock_db.memberships.find_one = AsyncMock(return_value=None)
    mock_db.users.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    mock_db.users.insert_one = AsyncMock(return_value=MagicMock(inserted_id="new-id"))
    mock_db.memberships.insert_one = AsyncMock(return_value=MagicMock(inserted_id="m1"))
    mock_db.user_units.insert_one = AsyncMock(return_value=MagicMock(inserted_id="uu1"))
    mock_db.by_laws_acknowledgments.insert_one = AsyncMock(return_value=MagicMock(inserted_id="bl1"))
    mock_db.user_units.find_one = AsyncMock(return_value=None)
    mock_db.user_notifications.find_one = AsyncMock(return_value=None)
    mock_db.memberships.distinct = AsyncMock(return_value=[])
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    cursor.sort = MagicMock(return_value=cursor)
    mock_db.users.find = MagicMock(return_value=cursor)
    mock_db.user_notifications.insert_many = AsyncMock(return_value=None)
    # memberships.find().to_list() — building-scoped FYI notification filtering
    _mem_cursor = MagicMock()
    _mem_cursor.to_list = AsyncMock(return_value=[])
    mock_db.memberships.find = MagicMock(return_value=_mem_cursor)
    # settings.find_one — building name in confirmation email
    mock_db.settings.find_one = AsyncMock(return_value={"building_name": "East Gate Residences"})
    return mock_db


@pytest.fixture(autouse=True)
def _bypass_rate_limiter():
    """Swap in a no-op limiter so tests don't exhaust the 5 req/min cap."""
    with patch("utils.rate_limit.limiter", _DummyLimiter()):
        yield


@pytest.fixture(autouse=True)
def _building_ctx():
    set_ctx_building_id("13195")


# ── Path A: active account (has password) ────────────────────────────────────

class TestAlreadyRegistered:

    @pytest.mark.asyncio
    async def test_returns_409_already_registered_code(self):
        """Registering with an email that already has a password returns 409/already_registered."""
        existing = {
            "id": "u1", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Alice",
            "unit_number": "101", "role": "tenant",
            "is_active": True, "is_approved": True, "status": "active",
            "building_id": "13195",
        }
        with patch("routers.auth.db", _mock_db(existing)):
            with pytest.raises(HTTPException) as exc:
                await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(), _building_id="13195")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "already_registered"
        assert "Unit 101" in exc.value.detail["message"]
        assert exc.value.detail["unit_number"] == "101"

    @pytest.mark.asyncio
    async def test_does_not_write_to_db(self):
        """No DB write must happen when rejecting an already-registered email."""
        existing = {
            "id": "u1", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Alice",
            "unit_number": "5", "role": "tenant",
            "is_active": True, "status": "active", "building_id": "13195",
        }
        mock_db = _mock_db(existing)
        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException):
                await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(), _building_id="13195")

        mock_db.users.insert_one.assert_not_called()
        mock_db.users.update_one.assert_not_called()


# ── Path B: imported account (no password) ───────────────────────────────────

class TestClaimImportedAccount:

    @pytest.mark.asyncio
    async def test_claim_succeeds_and_returns_auth_response(self):
        """Registering against an imported (no-password) record succeeds."""
        imported = {
            "id": "imported-id", "email": "owner@example.com",
            "full_name": "Strata Roll Owner", "role": "owner",
            "migration_source": "mri", "building_id": "13195",
        }
        with patch("routers.auth.db", _mock_db(imported)):
            result = await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(),
                                    _building_id="13195")

        assert result is not None
        assert hasattr(result, "token")
        assert hasattr(result, "user")

    @pytest.mark.asyncio
    async def test_claim_calls_update_not_insert_on_users(self):
        """Claiming must call update_one on the existing record, never insert_one."""
        imported = {
            "id": "imported-id", "email": "owner@example.com",
            "full_name": "Strata Roll Owner", "role": "owner", "building_id": "13195",
        }
        mock_db = _mock_db(imported)
        with patch("routers.auth.db", mock_db):
            await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(), _building_id="13195")

        mock_db.users.update_one.assert_called_once()
        mock_db.users.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_claim_preserves_existing_user_id(self):
        """The returned user id must equal the imported record's id."""
        imported_id = "stable-mri-id"
        imported = {
            "id": imported_id, "email": "owner@example.com",
            "full_name": "Strata Roll Owner", "role": "owner", "building_id": "13195",
        }
        with patch("routers.auth.db", _mock_db(imported)):
            result = await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(),
                                    _building_id="13195")

        assert result.user.id == imported_id

    @pytest.mark.asyncio
    async def test_claim_skips_membership_insert_when_one_exists(self):
        """An existing membership must not be duplicated during a claim."""
        imported = {
            "id": "imported-id", "email": "owner@example.com",
            "full_name": "Strata Roll Owner", "role": "owner", "building_id": "13195",
        }
        existing_mem = {"id": "mem-1", "user_id": "imported-id", "building_id": "13195", "is_active": True}
        mock_db = _mock_db(imported)
        mock_db.memberships.find_one = AsyncMock(return_value=existing_mem)

        with patch("routers.auth.db", mock_db):
            await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(), _building_id="13195")

        mock_db.memberships.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_mri_record_without_string_id_uses_object_id(self):
        """MRI-imported docs may only have _id (ObjectId); claiming must still work."""
        oid = ObjectId()
        imported = {
            "_id": oid,
            "email": "owner@example.com",
            "full_name": "Strata Roll Owner", "role": "owner", "building_id": "13195",
            # no "id" string field
        }
        mock_db = _mock_db(imported)
        with patch("routers.auth.db", mock_db):
            result = await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(),
                                    _building_id="13195")

        assert result is not None
        call_filter = mock_db.users.update_one.call_args[0][0]
        assert "_id" in call_filter, "Must filter by _id when imported record has no string id"


# ── Owner auto-approval ──────────────────────────────────────────────────────

def _unit_doc(owner_name="", owner_name_b=""):
    """Helper: build a unit doc with optional strata-roll owner names."""
    doc = {"unit_number": "101", "building_id": "13195"}
    if owner_name:
        doc["owner_name"] = owner_name
    if owner_name_b:
        doc["owner_name_b"] = owner_name_b
    return doc


class TestOwnerAutoApproval:
    """Owner auto-approval gates on strata-roll name match (primary or secondary).
    Tenants/guests always require explicit admin approval."""

    @pytest.mark.asyncio
    async def test_owner_matching_primary_name_is_auto_approved(self):
        """Owner whose name matches the primary strata-roll entry is auto-approved."""
        mock_db = _mock_db(None, unit_doc=_unit_doc(owner_name="Test Owner"))
        with patch("routers.auth.db", mock_db):
            await register(
                StarletteRequest(_make_scope()),
                _user_data(role="owner"),
                BackgroundTasks(),
                _building_id="13195",
            )

        insert_call = mock_db.users.insert_one.call_args[0][0]
        assert insert_call["is_approved"] is True, "Owner matching primary roll name must be auto-approved"

    @pytest.mark.asyncio
    async def test_owner_matching_secondary_name_is_auto_approved(self):
        """Owner whose name matches the secondary (co-owner) strata-roll entry is auto-approved."""
        mock_db = _mock_db(None, unit_doc=_unit_doc(owner_name="Other Person", owner_name_b="Test Owner"))
        with patch("routers.auth.db", mock_db):
            await register(
                StarletteRequest(_make_scope()),
                _user_data(role="owner"),
                BackgroundTasks(),
                _building_id="13195",
            )

        insert_call = mock_db.users.insert_one.call_args[0][0]
        assert insert_call["is_approved"] is True, "Owner matching secondary roll name must be auto-approved"

    @pytest.mark.asyncio
    async def test_owner_name_mismatch_is_not_approved(self):
        """Owner whose name does NOT match the strata roll is not auto-approved."""
        mock_db = _mock_db(None, unit_doc=_unit_doc(owner_name="Jane Smith", owner_name_b="Bob Smith"))
        with patch("routers.auth.db", mock_db):
            await register(
                StarletteRequest(_make_scope()),
                _user_data(role="owner"),
                BackgroundTasks(),
                _building_id="13195",
            )

        insert_call = mock_db.users.insert_one.call_args[0][0]
        assert insert_call["is_approved"] is False, "Owner not on roll must NOT be auto-approved"
        assert insert_call.get("is_name_flagged") is True, "Unmatched owner must be flagged"

    @pytest.mark.asyncio
    async def test_owner_with_no_roll_data_is_not_approved(self):
        """Owner where the unit has no owner_name at all stays pending (no roll data to verify against)."""
        mock_db = _mock_db(None, unit_doc=_unit_doc())  # no owner_name fields
        with patch("routers.auth.db", mock_db):
            await register(
                StarletteRequest(_make_scope()),
                _user_data(role="owner"),
                BackgroundTasks(),
                _building_id="13195",
            )

        insert_call = mock_db.users.insert_one.call_args[0][0]
        assert insert_call["is_approved"] is False, "Owner with no roll data must NOT be auto-approved"

    @pytest.mark.asyncio
    async def test_tenant_registration_sets_is_approved_false(self):
        """Tenants must NOT be auto-approved — they require admin approval."""
        mock_db = _mock_db(None)
        with patch("routers.auth.db", mock_db):
            await register(
                StarletteRequest(_make_scope()),
                _user_data(role="tenant"),
                BackgroundTasks(),
                _building_id="13195",
            )

        insert_call = mock_db.users.insert_one.call_args[0][0]
        assert insert_call["is_approved"] is False, "Tenant must NOT be auto-approved"

    @pytest.mark.asyncio
    async def test_claimed_owner_matching_roll_is_approved(self):
        """Claiming an imported owner stub where name matches the strata roll → auto-approved."""
        imported = {
            "id": "mri-owner-id", "email": "owner@example.com",
            "full_name": "Strata Roll Owner", "role": "owner", "building_id": "13195",
        }
        mock_db = _mock_db(imported, unit_doc=_unit_doc(owner_name="Test Owner"))
        with patch("routers.auth.db", mock_db):
            await register(
                StarletteRequest(_make_scope()),
                _user_data(role="owner"),  # full_name="Test Owner" by default
                BackgroundTasks(),
                _building_id="13195",
            )

        update_call = mock_db.users.update_one.call_args[0][1]
        assert update_call["$set"]["is_approved"] is True, "Claimed owner matching roll must be auto-approved"

    @pytest.mark.asyncio
    async def test_claimed_owner_not_matching_roll_is_pending(self):
        """Claiming an imported owner stub where name does NOT match the strata roll → pending."""
        imported = {
            "id": "mri-owner-id", "email": "owner@example.com",
            "full_name": "Strata Roll Owner", "role": "owner", "building_id": "13195",
        }
        mock_db = _mock_db(imported, unit_doc=_unit_doc(owner_name="Jane Smith"))
        with patch("routers.auth.db", mock_db):
            await register(
                StarletteRequest(_make_scope()),
                _user_data(role="owner"),  # full_name="Test Owner" — won't match "Jane Smith"
                BackgroundTasks(),
                _building_id="13195",
            )

        update_call = mock_db.users.update_one.call_args[0][1]
        assert update_call["$set"]["is_approved"] is False, "Claimed owner not matching roll must stay pending"


# ── Path B′: partial account (has password, no unit) ─────────────────────────

class TestClaimPartialAccount:
    """Accounts with a password_hash but no unit_number are partially created.
    Registration should claim and complete them, not reject with 409."""

    @pytest.mark.asyncio
    async def test_partial_account_is_claimed_not_rejected(self):
        """password_hash set + no unit_number → claim path, not 409."""
        partial = {
            "id": "partial-id", "email": "owner@example.com",
            "password_hash": "old_hash", "full_name": "Partial User",
            "role": "tenant", "building_id": "13195",
            # unit_number intentionally absent
        }
        with patch("routers.auth.db", _mock_db(partial)):
            result = await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(),
                                    _building_id="13195")

        assert result is not None
        assert hasattr(result, "token")

    @pytest.mark.asyncio
    async def test_partial_account_calls_update_not_insert(self):
        """Claiming a partial account must update the existing row, never insert."""
        partial = {
            "id": "partial-id", "email": "owner@example.com",
            "password_hash": "old_hash", "full_name": "Partial User",
            "role": "tenant", "building_id": "13195",
        }
        mock_db = _mock_db(partial)
        with patch("routers.auth.db", mock_db):
            await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(), _building_id="13195")

        mock_db.users.update_one.assert_called_once()
        mock_db.users.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_account_preserves_user_id(self):
        """The returned user id must equal the partial record's id."""
        partial = {
            "id": "partial-id", "email": "owner@example.com",
            "password_hash": "old_hash", "full_name": "Partial User",
            "role": "tenant", "building_id": "13195",
        }
        with patch("routers.auth.db", _mock_db(partial)):
            result = await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(),
                                    _building_id="13195")

        assert result.user.id == "partial-id"

    @pytest.mark.asyncio
    async def test_full_account_with_unit_is_still_rejected(self):
        """password_hash set AND unit_number present AND is_approved=True → 409 already_registered."""
        full = {
            "id": "u1", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Alice",
            "unit_number": "101", "role": "tenant",
            "is_active": True, "is_approved": True, "status": "active",
            "building_id": "13195",
        }
        with patch("routers.auth.db", _mock_db(full)):
            with pytest.raises(HTTPException) as exc:
                await register(StarletteRequest(_make_scope()), _user_data(), BackgroundTasks(), _building_id="13195")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "already_registered"


# ── Pending account unblocking ────────────────────────────────────────────────

class TestPendingAccountUnblocking:
    """Accounts that are fully formed (password + unit) but is_approved=False
    must not receive a confusing 'try to login' message — instead the system
    should auto-approve via strata-roll match or surface a pending message."""

    @pytest.mark.asyncio
    async def test_pending_account_matching_roll_is_auto_approved(self):
        """is_approved=False + name matches strata roll → auto-approved, 409 pending_now_approved."""
        pending = {
            "id": "pending-id", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Test Owner",
            "unit_number": "101", "role": "owner",
            "is_active": True, "is_approved": False, "status": "active",
            "building_id": "13195",
        }
        mock_db = _mock_db(pending, unit_doc=_unit_doc(owner_name="Test Owner"))
        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await register(StarletteRequest(_make_scope()), _user_data(role="owner"), BackgroundTasks(),
                               _building_id="13195")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "pending_now_approved"
        update_call = mock_db.users.update_one.call_args[0][1]
        assert update_call["$set"]["is_approved"] is True

    @pytest.mark.asyncio
    async def test_pending_account_not_matching_roll_returns_pending_approval(self):
        """is_approved=False + name does NOT match roll → 409 pending_approval, admin notified."""
        pending = {
            "id": "pending-id", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Test Owner",
            "unit_number": "101", "role": "owner",
            "is_active": True, "is_approved": False, "status": "active",
            "building_id": "13195",
        }
        mock_db = _mock_db(pending, unit_doc=_unit_doc(owner_name="Jane Smith"))
        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await register(StarletteRequest(_make_scope()), _user_data(role="owner"), BackgroundTasks(),
                               _building_id="13195")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "pending_approval"
        mock_db.users.update_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_account_no_roll_data_returns_pending_approval(self):
        """is_approved=False + no roll data on unit → 409 pending_approval."""
        pending = {
            "id": "pending-id", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Test Owner",
            "unit_number": "101", "role": "owner",
            "is_active": True, "is_approved": False, "status": "active",
            "building_id": "13195",
        }
        mock_db = _mock_db(pending, unit_doc=_unit_doc())  # no owner_name fields
        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await register(StarletteRequest(_make_scope()), _user_data(role="owner"), BackgroundTasks(),
                               _building_id="13195")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_approved_account_is_not_intercepted(self):
        """is_approved=True → normal 409 (pending gate must not fire)."""
        approved = {
            "id": "u1", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Test Owner",
            "unit_number": "101", "role": "tenant",
            "is_active": True, "is_approved": True, "status": "active",
            "building_id": "13195",
        }
        with patch("routers.auth.db", _mock_db(approved)):
            with pytest.raises(HTTPException) as exc:
                await register(StarletteRequest(_make_scope()), _user_data(role="tenant"), BackgroundTasks(),
                               _building_id="13195")

        assert exc.value.detail["code"] not in ("pending_now_approved", "pending_approval")

    @pytest.mark.asyncio
    async def test_admin_notification_sent_when_no_recent_notif(self):
        """Admin bell notification must be created when no recent notification exists."""
        pending = {
            "id": "pending-id", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Test Owner",
            "unit_number": "101", "role": "owner",
            "is_active": True, "is_approved": False, "status": "active",
            "building_id": "13195",
        }
        mock_db = _mock_db(pending, unit_doc=_unit_doc(owner_name="Jane Smith"))
        # Simulate no recent notification exists
        mock_db.user_notifications.find_one = AsyncMock(return_value=None)
        # Simulate an admin reviewer exists
        admin_cursor = MagicMock()
        admin_cursor.to_list = AsyncMock(return_value=[
            {"id": "admin-1", "email": "admin@example.com", "full_name": "Admin", "role": "strata_manager"}])
        mock_db.users.find = MagicMock(return_value=admin_cursor)

        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException):
                await register(StarletteRequest(_make_scope()), _user_data(role="owner"), BackgroundTasks(),
                               _building_id="13195")

        mock_db.user_notifications.insert_many.assert_called_once()

    @pytest.mark.asyncio
    async def test_admin_notification_not_duplicated_within_24h(self):
        """If a recent notification was already sent, insert_many must NOT be called again."""
        pending = {
            "id": "pending-id", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Test Owner",
            "unit_number": "101", "role": "owner",
            "is_active": True, "is_approved": False, "status": "active",
            "building_id": "13195",
        }
        mock_db = _mock_db(pending, unit_doc=_unit_doc(owner_name="Jane Smith"))
        # Simulate a recent notification already exists
        mock_db.user_notifications.find_one = AsyncMock(return_value={"id": "existing-notif"})

        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException):
                await register(StarletteRequest(_make_scope()), _user_data(role="owner"), BackgroundTasks(),
                               _building_id="13195")

        mock_db.user_notifications.insert_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_tenant_returns_pending_approval_no_roll_check(self):
        """Pending tenant must get pending_approval — roll check must NOT fire (tenants aren't on strata roll)."""
        pending = {
            "id": "tenant-pending", "email": "owner@example.com",
            "password_hash": "hashed", "full_name": "Test Tenant",
            "unit_number": "101", "role": "tenant",
            "is_active": True, "is_approved": False, "status": "active",
            "building_id": "13195",
        }
        mock_db = _mock_db(pending, unit_doc=_unit_doc(owner_name="Some Owner"))
        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await register(StarletteRequest(_make_scope()), _user_data(role="tenant"), BackgroundTasks(),
                               _building_id="13195")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "pending_approval"
        # units.find_one must NOT be called — roll check is owner-only
        mock_db.units.find_one.assert_not_called()
        # auto-approve update must not happen
        mock_db.users.update_one.assert_not_called()
