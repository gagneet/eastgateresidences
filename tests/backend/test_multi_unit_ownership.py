"""
Tests for multi-unit ownership feature.

Covers:
  - Registration with additional_unit_numbers (owners only)
  - Existing active owner gets owner_exists_add_unit error on re-register
  - GET /auth/my-units
  - POST /auth/switch-unit
  - POST /auth/add-unit
  - _get_user_owned_units helper
  - Multi-tenant isolation: units from building 16244 not visible in 13195 context

All records created here use is_test_data=True and are cleaned up in teardown.
"""

import asyncio
import uuid
from datetime import datetime, timezone, date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest
from starlette.background import BackgroundTasks

BUILDING_ID = "13195"
TEST_PREFIX = "muo-test-"


def _disable_rate_limit():
    from utils.rate_limit import update_rate_limit_state
    update_rate_limit_state(enabled=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(uid=None, email=None, role="owner", unit=None):
    uid = uid or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email or f"test-{uid[:8]}@example.com",
        "full_name": f"Test User {uid[:6]}",
        "role": role,
        "unit_number": unit,
        "password_hash": "hashed_pw_for_test",  # marks account as registered, not imported
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "building_id": BUILDING_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "custom_permissions": {},
        "is_test_data": True,
    }


def _user_unit(user_id, unit_number, is_primary=True, is_active=True, building_id=BUILDING_ID):
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "unit_number": unit_number,
        "role_at_unit": "owner",
        "is_primary": is_primary,
        "is_active": is_active,
        "start_date": date.today().isoformat(),
        "building_id": building_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "is_test_data": True,
    }


def _unit_doc(unit_number, building_id=BUILDING_ID):
    return {"unit_number": unit_number, "building_id": building_id, "unit_type": "apartment"}


def _make_req(path: str = "/api/auth/register") -> "StarletteRequest":
    """Return a dummy StarletteRequest. Rate limiter is disabled in each test via _disable_rate_limit()."""
    scope = {
        "type": "http", "method": "POST", "path": path,
        "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1234),
    }
    return StarletteRequest(scope)


def _mock_db():
    db = MagicMock()
    for col in ["users", "user_units", "units", "memberships", "user_notifications",
                "by_laws_acknowledgments", "feature_toggles", "buildings", "settings"]:
        c = MagicMock()
        c.find_one = AsyncMock(return_value=None)
        c.find = MagicMock(return_value=MagicMock(
            to_list=AsyncMock(return_value=[]),
            sort=MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[]))),
        ))
        c.distinct = AsyncMock(return_value=[])
        c.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
        c.insert_many = AsyncMock(return_value=None)
        c.update_one = AsyncMock(return_value=None)
        c.update_many = AsyncMock(return_value=None)
        c.delete_one = AsyncMock(return_value=None)
        setattr(db, col, c)
    return db


# ── _get_user_owned_units ─────────────────────────────────────────────────────

class TestGetUserOwnedUnits:
    @pytest.mark.asyncio
    async def test_returns_sorted_unit_numbers(self):
        from routers.auth import _get_user_owned_units
        mock_db = _mock_db()
        docs = [{"unit_number": "UA053"}, {"unit_number": "UA001"}, {"unit_number": "UA010"}]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=docs)
        mock_db.user_units.find.return_value = cursor

        with patch("routers.auth.db", mock_db):
            result = await _get_user_owned_units("user-1", BUILDING_ID)

        # Lexicographic sort (strings) — UA001 < UA010 < UA053
        assert result == ["UA001", "UA010", "UA053"]

    @pytest.mark.asyncio
    async def test_filters_docs_without_unit_number(self):
        from routers.auth import _get_user_owned_units
        mock_db = _mock_db()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[{"unit_number": "UA001"}, {}])
        mock_db.user_units.find.return_value = cursor

        with patch("routers.auth.db", mock_db):
            result = await _get_user_owned_units("user-1", BUILDING_ID)

        assert result == ["UA001"]

    @pytest.mark.asyncio
    async def test_empty_when_no_units(self):
        from routers.auth import _get_user_owned_units
        mock_db = _mock_db()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        mock_db.user_units.find.return_value = cursor

        with patch("routers.auth.db", mock_db):
            result = await _get_user_owned_units("user-1", BUILDING_ID)

        assert result == []


# ── Registration — additional_unit_numbers ────────────────────────────────────

class TestRegistrationMultiUnit:
    """Unit tests for the registration endpoint's multi-unit code paths."""

    @pytest.mark.asyncio
    async def test_invalid_additional_unit_raises_400(self):
        """Backend rejects additional_unit_numbers that don't exist in units collection."""
        from routers.auth import register
        from models.user import UserCreate
        from fastapi import HTTPException

        mock_db = _mock_db()
        # Primary unit exists; additional unit does NOT
        mock_db.users.find_one.return_value = None
        mock_db.units.find_one.side_effect = [
            {"unit_number": "UA001"},  # primary unit found
            None,  # additional unit not found
        ]

        _disable_rate_limit()
        req = _make_req()

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.get_optional_building", return_value=BUILDING_ID), \
                pytest.raises(HTTPException) as exc_info:
            await register(
                request=req,
                user_data=UserCreate(
                    email="owner@test.com",
                    password="pytest-fixture-password-not-a-credential",
                    full_name="Test Owner",
                    unit_number="UA001",
                    additional_unit_numbers=["INVALID99"],
                    role="owner",
                    by_laws_acknowledged=True,
                ),
                background_tasks=BackgroundTasks(),
                _building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 400
        assert "INVALID99" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_duplicate_additional_unit_deduplicated(self):
        """Duplicate unit numbers in additional_unit_numbers are silently deduplicated."""
        from routers.auth import register
        from models.user import UserCreate

        mock_db = _mock_db()
        mock_db.users.find_one.return_value = None
        mock_db.units.find_one.side_effect = [
            {"unit_number": "UA001"},  # primary
            {"unit_number": "UA002"},  # additional (first occurrence)
            # UA002 duplicate is filtered before hitting DB
        ]
        mock_db.user_units.find_one.return_value = None
        mock_db.memberships.find_one.return_value = None

        _disable_rate_limit()
        req = _make_req()

        inserted_units = []

        async def capture_insert(doc):
            inserted_units.append(doc.get("unit_number"))
            return MagicMock(inserted_id="x")

        mock_db.user_units.insert_one.side_effect = capture_insert

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.create_token", return_value="tok"), \
                patch("routers.auth.check_owner_name_against_roll", return_value=True), \
                patch("routers.auth._get_general_settings_or_default",
                      AsyncMock(return_value={"building_name": "Test Building", "building_address": ""})):
            await register(
                request=req,
                user_data=UserCreate(
                    email="owner@test.com",
                    password="pytest-fixture-password-not-a-credential",
                    full_name="Test Owner",
                    unit_number="UA001",
                    additional_unit_numbers=["UA002", "UA002"],  # duplicate
                    role="owner",
                    by_laws_acknowledged=True,
                ),
                background_tasks=BackgroundTasks(),
                _building_id=BUILDING_ID,
            )

        # Only one additional unit_units record for UA002 (deduped)
        assert inserted_units.count("UA002") == 1
        assert inserted_units.count("UA001") == 1

    @pytest.mark.asyncio
    async def test_primary_unit_excluded_from_additional(self):
        """Primary unit appearing in additional_unit_numbers is silently dropped."""
        from routers.auth import register
        from models.user import UserCreate

        mock_db = _mock_db()
        mock_db.users.find_one.return_value = None
        mock_db.units.find_one.side_effect = [
            {"unit_number": "UA001"},  # primary
            # UA001 in additional deduplicated — no second DB call
        ]
        mock_db.user_units.find_one.return_value = None

        inserted_units = []

        async def capture(doc):
            inserted_units.append(doc.get("unit_number"))
            return MagicMock(inserted_id="x")

        mock_db.user_units.insert_one.side_effect = capture

        _disable_rate_limit()
        req = _make_req()

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.create_token", return_value="tok"), \
                patch("routers.auth.check_owner_name_against_roll", return_value=True), \
                patch("routers.auth._get_general_settings_or_default",
                      AsyncMock(return_value={"building_name": "B", "building_address": ""})):
            await register(
                request=req,
                user_data=UserCreate(
                    email="owner2@test.com",
                    password="pytest-fixture-password-not-a-credential",
                    full_name="Test Owner Two",
                    unit_number="UA001",
                    additional_unit_numbers=["UA001"],  # same as primary
                    role="owner",
                    by_laws_acknowledged=True,
                ),
                background_tasks=BackgroundTasks(),
                _building_id=BUILDING_ID,
            )

        assert inserted_units.count("UA001") == 1  # only primary — not doubled


# ── owner_exists_add_unit error path ─────────────────────────────────────────

class TestOwnerExistsAddUnit:
    @pytest.mark.asyncio
    async def test_active_owner_re_register_same_building_returns_409(self):
        """Active owner re-registering for a different unit gets owner_exists_add_unit."""
        from routers.auth import register
        from models.user import UserCreate
        from fastapi import HTTPException

        existing = _user(role="owner", unit="UA001")
        existing["status"] = "active"

        mock_db = _mock_db()
        mock_db.users.find_one.return_value = existing  # email already exists
        # No existing link between the existing user and UA053
        mock_db.user_units.find_one.return_value = None

        _disable_rate_limit()
        req = _make_req()

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await register(
                request=req,
                user_data=UserCreate(
                    email=existing["email"],
                    password="pytest-fixture-password-not-a-credential",
                    full_name=existing["full_name"],
                    unit_number="UA053",
                    role="owner",
                    by_laws_acknowledged=True,
                ),
                background_tasks=BackgroundTasks(),
                _building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "owner_exists_add_unit"

    @pytest.mark.asyncio
    async def test_owner_re_register_already_linked_unit_returns_already_registered(self):
        """Owner re-registering their already-linked unit gets 409 already_registered."""
        from routers.auth import register
        from models.user import UserCreate
        from fastapi import HTTPException

        existing = _user(role="owner", unit="UA001")
        existing["status"] = "active"

        mock_db = _mock_db()
        mock_db.users.find_one.return_value = existing
        # Already linked to UA001
        mock_db.user_units.find_one.return_value = _user_unit(existing["id"], "UA001")

        _disable_rate_limit()
        req = _make_req()

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await register(
                request=req,
                user_data=UserCreate(
                    email=existing["email"],
                    password="pytest-fixture-password-not-a-credential",
                    full_name=existing["full_name"],
                    unit_number="UA001",
                    role="owner",
                    by_laws_acknowledged=True,
                ),
                background_tasks=BackgroundTasks(),
                _building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "already_registered"
        assert exc_info.value.detail["unit_number"] == "UA001"

    @pytest.mark.asyncio
    async def test_non_owner_existing_user_still_gets_already_registered(self):
        """Existing tenant/guest re-registering gets 409 already_registered with unit info."""
        from routers.auth import register
        from models.user import UserCreate
        from fastapi import HTTPException

        existing = _user(role="tenant", unit="UA001")
        existing["status"] = "active"

        mock_db = _mock_db()
        mock_db.users.find_one.return_value = existing

        _disable_rate_limit()
        req = _make_req()

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await register(
                request=req,
                user_data=UserCreate(
                    email=existing["email"],
                    password="pytest-fixture-password-not-a-credential",
                    full_name=existing["full_name"],
                    unit_number="UA002",
                    role="tenant",
                    by_laws_acknowledged=True,
                ),
                background_tasks=BackgroundTasks(),
                _building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "already_registered"
        assert "already registered" in exc_info.value.detail["message"].lower()


# ── POST /auth/switch-unit ────────────────────────────────────────────────────

class TestSwitchUnit:
    @pytest.mark.asyncio
    async def test_switch_to_owned_unit_succeeds(self):
        from routers.auth import switch_unit, UnitSwitchRequest

        user = _user(unit="UA001")
        mock_db = _mock_db()
        mock_db.user_units.find_one.return_value = _user_unit(user["id"], "UA053")
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[
            {"unit_number": "UA001"}, {"unit_number": "UA053"}
        ])
        mock_db.user_units.find.return_value = cursor

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.create_token", return_value="new-tok"):
            result = await switch_unit(
                data=UnitSwitchRequest(unit_number="UA053"),
                current_user=user,
                building_id=BUILDING_ID,
            )

        assert result["token"] == "new-tok"
        assert result["unit_number"] == "UA053"
        assert "UA001" in result["user"].owned_units
        assert "UA053" in result["user"].owned_units

    @pytest.mark.asyncio
    async def test_switch_to_unlinked_unit_raises_403(self):
        from routers.auth import switch_unit, UnitSwitchRequest
        from fastapi import HTTPException

        user = _user(unit="UA001")
        mock_db = _mock_db()
        mock_db.user_units.find_one.return_value = None  # not linked

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await switch_unit(
                data=UnitSwitchRequest(unit_number="UA999"),
                current_user=user,
                building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_switch_unit_updates_jwt_claim(self):
        """The returned token carries the new unit_number."""
        from routers.auth import switch_unit, UnitSwitchRequest

        captured_kwargs = {}

        def mock_create_token(**kwargs):
            captured_kwargs.update(kwargs)
            return "tok-with-unit"

        user = _user(unit="UA001")
        mock_db = _mock_db()
        mock_db.user_units.find_one.return_value = _user_unit(user["id"], "UA053")
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[{"unit_number": "UA053"}])
        mock_db.user_units.find.return_value = cursor

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.create_token", side_effect=mock_create_token):
            await switch_unit(
                data=UnitSwitchRequest(unit_number="UA053"),
                current_user=user,
                building_id=BUILDING_ID,
            )

        assert captured_kwargs.get("unit_number") == "UA053"


# ── GET /auth/my-units ────────────────────────────────────────────────────────

class TestMyUnits:
    @pytest.mark.asyncio
    async def test_returns_all_active_units_with_enrichment(self):
        from routers.auth import get_my_units

        user = _user(unit="UA001")
        user_unit_docs = [
            {"unit_number": "UA001", "role_at_unit": "owner", "is_primary": True, "start_date": "2024-01-01"},
            {"unit_number": "UA053", "role_at_unit": "owner", "is_primary": False, "start_date": "2024-06-01"},
        ]
        unit_info_docs = [
            {"unit_number": "UA001", "unit_type": "apartment", "entitlement": 100, "floor_level": 1},
            {"unit_number": "UA053", "unit_type": "apartment", "entitlement": 105, "floor_level": 5},
        ]

        mock_db = _mock_db()
        uu_cursor = MagicMock()
        uu_cursor.to_list = AsyncMock(return_value=user_unit_docs)
        mock_db.user_units.find.return_value = uu_cursor

        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=unit_info_docs)
        mock_db.units.find.return_value = units_cursor

        with patch("routers.auth.db", mock_db):
            result = await get_my_units(current_user=user, building_id=BUILDING_ID)

        assert len(result) == 2
        unit_numbers = [r["unit_number"] for r in result]
        assert "UA001" in unit_numbers
        assert "UA053" in unit_numbers
        # Primary first
        assert result[0]["is_primary"] is True

    @pytest.mark.asyncio
    async def test_is_active_session_flag_set_correctly(self):
        from routers.auth import get_my_units

        user = _user(unit="UA053")  # active unit in JWT
        uu_cursor = MagicMock()
        uu_cursor.to_list = AsyncMock(return_value=[
            {"unit_number": "UA001", "role_at_unit": "owner", "is_primary": True, "start_date": "2024-01-01"},
            {"unit_number": "UA053", "role_at_unit": "owner", "is_primary": False, "start_date": "2024-06-01"},
        ])
        mock_db = _mock_db()
        mock_db.user_units.find.return_value = uu_cursor
        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=[])
        mock_db.units.find.return_value = units_cursor

        with patch("routers.auth.db", mock_db):
            result = await get_my_units(current_user=user, building_id=BUILDING_ID)

        active = next(r for r in result if r["unit_number"] == "UA053")
        inactive = next(r for r in result if r["unit_number"] == "UA001")
        assert active["is_active_session"] is True
        assert inactive["is_active_session"] is False

    @pytest.mark.asyncio
    async def test_empty_list_when_no_units(self):
        from routers.auth import get_my_units

        user = _user()
        mock_db = _mock_db()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        mock_db.user_units.find.return_value = cursor

        with patch("routers.auth.db", mock_db):
            result = await get_my_units(current_user=user, building_id=BUILDING_ID)

        assert result == []


# ── POST /auth/add-unit ───────────────────────────────────────────────────────

class TestAddUnit:
    @pytest.mark.asyncio
    async def test_owner_can_add_new_unit(self):
        from routers.auth import add_unit, AddUnitRequest

        user = _user(role="owner", unit="UA001")
        mock_db = _mock_db()
        mock_db.units.find_one.return_value = _unit_doc("UA053")
        mock_db.user_units.find_one.return_value = None  # no existing link (active or pending)
        # _get_user_owned_units returns only UA001 — UA053 is is_active=False (pending)
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[{"unit_number": "UA001"}])
        mock_db.user_units.find.return_value = cursor

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.send_email_async", new_callable=AsyncMock):
            result = await add_unit(
                data=AddUnitRequest(unit_number="UA053"),
                background_tasks=BackgroundTasks(),
                current_user=user,
                building_id=BUILDING_ID,
            )

        # UA053 is pending (is_active=False) so must NOT appear in owned_units yet
        assert "UA053" not in result["owned_units"]
        assert "UA001" in result["owned_units"]
        # The response still identifies what was added
        assert result["unit_number"] == "UA053"
        mock_db.user_units.insert_one.assert_called_once()
        # Verify the inserted record is pending (is_active=False)
        inserted = mock_db.user_units.insert_one.call_args[0][0]
        assert inserted["is_active"] is False

    @pytest.mark.asyncio
    async def test_add_pending_unit_raises_409(self):
        """A second claim for the same unit while a pending one exists should be rejected."""
        from routers.auth import add_unit, AddUnitRequest
        from fastapi import HTTPException

        user = _user(role="owner", unit="UA001")
        mock_db = _mock_db()
        mock_db.units.find_one.return_value = _unit_doc("UA053")
        # Simulate a pending (is_active=False) link already in the DB
        mock_db.user_units.find_one.return_value = _user_unit(user["id"], "UA053", is_active=False)

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await add_unit(
                data=AddUnitRequest(unit_number="UA053"),
                background_tasks=BackgroundTasks(),
                current_user=user,
                building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 409
        assert "pending" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_add_nonexistent_unit_raises_400(self):
        from routers.auth import add_unit, AddUnitRequest
        from fastapi import HTTPException

        user = _user(role="owner", unit="UA001")
        mock_db = _mock_db()
        mock_db.units.find_one.return_value = None  # unit not found

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await add_unit(
                data=AddUnitRequest(unit_number="BADUNIT"),
                background_tasks=BackgroundTasks(),
                current_user=user,
                building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_add_already_linked_unit_raises_409(self):
        from routers.auth import add_unit, AddUnitRequest
        from fastapi import HTTPException

        user = _user(role="owner", unit="UA001")
        mock_db = _mock_db()
        mock_db.units.find_one.return_value = _unit_doc("UA001")
        mock_db.user_units.find_one.return_value = _user_unit(user["id"], "UA001")  # already linked

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await add_unit(
                data=AddUnitRequest(unit_number="UA001"),
                background_tasks=BackgroundTasks(),
                current_user=user,
                building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_tenant_cannot_add_unit(self):
        from routers.auth import add_unit, AddUnitRequest
        from fastapi import HTTPException

        user = _user(role="tenant", unit="UA001")
        mock_db = _mock_db()

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await add_unit(
                data=AddUnitRequest(unit_number="UA002"),
                background_tasks=BackgroundTasks(),
                current_user=user,
                building_id=BUILDING_ID,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_add_unit_notifies_admins(self):
        from routers.auth import add_unit, AddUnitRequest

        user = _user(role="owner", unit="UA001")
        admin = {**_user(role="super_admin"), "email": "admin@test.com"}

        mock_db = _mock_db()
        mock_db.units.find_one.return_value = _unit_doc("UA053")
        mock_db.user_units.find_one.return_value = None
        # Admin list
        admin_cursor = MagicMock()
        admin_cursor.to_list = AsyncMock(return_value=[admin])
        mock_db.users.find.return_value = admin_cursor
        owned_cursor = MagicMock()
        # UA053 is is_active=False (pending); _get_user_owned_units returns active-only
        owned_cursor.to_list = AsyncMock(return_value=[{"unit_number": "UA001"}])
        mock_db.user_units.find.return_value = owned_cursor

        # send_email_async is queued via BackgroundTasks.add_task (not executed in unit tests)
        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.send_email_async", new_callable=AsyncMock):
            await add_unit(
                data=AddUnitRequest(unit_number="UA053"),
                background_tasks=BackgroundTasks(),
                current_user=user,
                building_id=BUILDING_ID,
            )

        mock_db.user_notifications.insert_many.assert_called_once()
        notifs = mock_db.user_notifications.insert_many.call_args[0][0]
        assert len(notifs) == 1
        assert notifs[0]["type"] == "unit_claim"
        assert "UA053" in notifs[0]["title"]


# ── Multi-tenant isolation ────────────────────────────────────────────────────

class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_switch_unit_requires_active_link_in_current_building(self):
        """A user_units record from a different building cannot be used to switch."""
        from routers.auth import switch_unit, UnitSwitchRequest
        from fastapi import HTTPException

        user = _user(unit="UA001")
        mock_db = _mock_db()
        # user_units.find_one returns None (the query already scopes by user_id + unit_number + is_active;
        # the tenant-scoped DB wrapper adds building_id automatically in production — here we simulate None)
        mock_db.user_units.find_one.return_value = None

        with patch("routers.auth.db", mock_db), \
                pytest.raises(HTTPException) as exc_info:
            await switch_unit(
                data=UnitSwitchRequest(unit_number="UA053"),
                current_user=user,
                building_id="16244",  # different building
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_my_units_scoped_to_building(self):
        """get_my_units only returns units belonging to the authenticated building."""
        from routers.auth import get_my_units

        user = _user(unit="UA001")
        # Only building 13195 units returned (DB wrapper applies building filter)
        uu_cursor = MagicMock()
        uu_cursor.to_list = AsyncMock(return_value=[
            {"unit_number": "UA001", "role_at_unit": "owner", "is_primary": True, "start_date": "2024-01-01"},
        ])
        mock_db = _mock_db()
        mock_db.user_units.find.return_value = uu_cursor
        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(return_value=[{"unit_number": "UA001", "unit_type": "apartment"}])
        mock_db.units.find.return_value = units_cursor

        with patch("routers.auth.db", mock_db):
            result = await get_my_units(current_user=user, building_id=BUILDING_ID)

        # No units from building 16244 in result
        assert all(r.get("unit_number", "").startswith("UA") for r in result)
        assert len(result) == 1


# ── JWT unit_number claim ─────────────────────────────────────────────────────

class TestJwtUnitClaim:
    def test_create_token_includes_unit_number_when_provided(self):
        import jwt
        from utils.auth import create_token
        from config import JWT_SECRET, JWT_ALGORITHM

        token = create_token(
            user_id="uid-1",
            email="owner@test.com",
            role="owner",
            building_id=BUILDING_ID,
            unit_number="UA053",
        )
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["unit_number"] == "UA053"
        assert payload["building_id"] == BUILDING_ID

    def test_create_token_without_unit_number_has_no_claim(self):
        import jwt
        from utils.auth import create_token
        from config import JWT_SECRET, JWT_ALGORITHM

        token = create_token(
            user_id="uid-1",
            email="owner@test.com",
            role="owner",
            building_id=BUILDING_ID,
        )
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert "unit_number" not in payload


# ── UserResponse owned_units ──────────────────────────────────────────────────

class TestUserResponseOwnedUnits:
    def test_owned_units_defaults_to_empty_list(self):
        from utils.permissions import user_to_response

        user = _user(unit="UA001")
        user["owned_units"] = []
        response = user_to_response(user)
        assert response.owned_units == []

    def test_owned_units_passed_through(self):
        from utils.permissions import user_to_response

        user = _user(unit="UA001")
        user["owned_units"] = ["UA001", "UA053"]
        response = user_to_response(user)
        assert set(response.owned_units) == {"UA001", "UA053"}

    def test_owned_units_not_in_user_dict_returns_empty(self):
        from utils.permissions import user_to_response

        user = _user(unit="UA001")
        # owned_units key absent
        response = user_to_response(user)
        assert response.owned_units == []
