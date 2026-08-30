"""
Tests for four registration-related bug fixes (2026-03-30).

FIX 1 — routers/auth.py register(): _building_id = Depends(get_optional_building)
         added so db.units.find_one() works in the tenant-scoped collection.

FIX 2 — routers/auth.py register(): safe_b_name / safe_b_addr UnboundLocalError
         when going through the else branch (non-owner-approval path).  The else
         branch now fetches settings and defines those variables before the email
         template uses them.

FIX 3 — routers/finance.py update_dca_status(): audit log was missing.
         create_audit_log() is now called after db.units.update_one().

FIX 4 — routers/auth.py register(): membership record was never created, so
         newly registered users were invisible in get_users() (pipeline starts
         from db.memberships.aggregate).  db.memberships.insert_one() is now
         part of persistence_tasks.

All tests are pure-unit — no live MongoDB required.
Rate limiter is disabled via update_rate_limit_state(enabled=False) before each call.
"""

import inspect
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _disable_rate_limit():
    """Disable the rate limiter so unit tests can call endpoints freely."""
    from utils.rate_limit import update_rate_limit_state
    update_rate_limit_state(enabled=False)


def _make_user_create(
        email="newuser@example.com",
        role="owner",
        unit_number="UA001",
        full_name="Test Owner",
        password="pytest-fixture-password-not-a-credential",
        by_laws_acknowledged=False,
        end_date=None,
):
    """Return a minimal UserCreate-like namespace (no Pydantic overhead)."""
    from types import SimpleNamespace
    return SimpleNamespace(
        email=email,
        role=role,
        unit_number=unit_number,
        full_name=full_name,
        password=password,
        by_laws_acknowledged=by_laws_acknowledged,
        end_date=end_date,
        phone=None,
        # register() reads this to resolve a building-scoped resident invite;
        # None means "ordinary self-service registration, no invite".
        invite_token=None,
    )


def _make_unit(unit_number="UA001"):
    return {
        "id": "unit-001",
        "building_id": "13195",
        "unit_number": unit_number,
        "owner_name": "Test Owner",
        "owner_name_b": None,
    }


def _make_admin_user(uid=None, email="admin@eastgate.com"):
    return {
        "id": uid or str(uuid.uuid4()),
        "email": email,
        "full_name": "Admin User",
        "role": "super_admin",
        "is_active": True,
    }


def _make_starlette_request(host="127.0.0.1"):
    """Return a real Starlette Request so slowapi can inspect client.host."""
    from starlette.requests import Request as StarletteRequest
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/register",
        "query_string": b"",
        "headers": [],
        "client": (host, 12345),
    }
    return StarletteRequest(scope)


def _make_background_tasks():
    bt = MagicMock()
    bt.add_task = MagicMock()
    return bt


def _make_valid_user_response():
    """Return a dict that passes UserResponse Pydantic validation."""
    return {
        "id": str(uuid.uuid4()),
        "email": "stub@test.com",
        "full_name": "Stub User",
        "role": "owner",
        "is_active": True,
        "is_approved": False,
        "status": "active",
        "permissions": {
            "can_view_documents": False,
            "can_upload_documents": False,
            "can_view_finances": False,
            "can_manage_finances": False,
            "can_create_listings": False,
            "can_manage_listings": False,
            "can_manage_users": False,
            "can_manage_ec": False,
            "can_send_announcements": False,
            "can_manage_meetings": False,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_staff_register_request(
        *,
        email="staff@example.com",
        role="service_provider",
        full_name="Service Provider",
        password="pytest-fixture-password-not-a-credential",
        building_id="13195",
):
    from types import SimpleNamespace
    return SimpleNamespace(
        email=email,
        password=password,
        full_name=full_name,
        role=role,
        phone="0400000000",
        organisation="360 Degree Building Services",
        professional_licence="LIC-001",
        building_id=building_id,
        terms_accepted=True,
    )


def _make_mock_db(
        *,
        existing_user=None,
        unit_doc=None,
        unit_owners=None,
        admin_users=None,
        settings_doc=None,
):
    """Build a fully-mocked db object for register() unit tests."""
    mock_db = MagicMock()

    # users.find_one — check for existing email
    mock_db.users.find_one = AsyncMock(return_value=existing_user)

    # users.find().to_list() — for admin and owner lookups
    _admin_cursor = MagicMock()
    _admin_cursor.to_list = AsyncMock(return_value=admin_users or [])
    mock_db.users.find = MagicMock(return_value=_admin_cursor)

    # units.find_one — unit validation
    mock_db.units.find_one = AsyncMock(return_value=unit_doc)

    # Persistence inserts
    mock_db.users.insert_one = AsyncMock(return_value=MagicMock())
    mock_db.memberships.insert_one = AsyncMock(return_value=MagicMock())
    mock_db.user_units.insert_one = AsyncMock(return_value=MagicMock())
    mock_db.by_laws_acknowledgments.insert_one = AsyncMock(return_value=MagicMock())
    mock_db.user_notifications.insert_many = AsyncMock(return_value=MagicMock())

    # Rollback paths
    mock_db.users.delete_one = AsyncMock(return_value=MagicMock())
    mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock())
    mock_db.user_units.delete_one = AsyncMock(return_value=MagicMock())
    mock_db.by_laws_acknowledgments.delete_one = AsyncMock(return_value=MagicMock())

    # memberships.find().to_list() — building-scoped FYI notification filtering
    _mem_cursor = MagicMock()
    _mem_cursor.to_list = AsyncMock(return_value=[])
    mock_db.memberships.find = MagicMock(return_value=_mem_cursor)
    # memberships.distinct() — _get_staff_registration_reviewers() resolves the
    # building's reviewers from active memberships before querying users.
    mock_db.memberships.distinct = AsyncMock(return_value=[u["id"] for u in (admin_users or []) if "id" in u])

    # Settings for branding in email templates
    mock_db.settings.find_one = AsyncMock(
        return_value=settings_doc or {
            "building_name": "East Gate Residences",
            "building_address": "13 Denman St, Denman Prospect ACT 2611",
        }
    )
    return mock_db


def _make_finance_mock_db():
    mock_db = MagicMock()
    mock_db.units.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    mock_db.audit_logs.insert_one = AsyncMock(return_value=MagicMock())
    return mock_db


def _make_finance_user(role="strata_manager"):
    return {
        "id": "admin-001",
        "full_name": "Finance Admin",
        "email": "finance@eastgate.com",
        "role": role,
        "permissions": {"can_manage_finances": True, "can_view_finances": True},
    }


@contextmanager
def _register_patches(mock_db, settings_doc=None):
    """Context manager that applies all common patches needed to call register()."""
    from models.user import UserResponse
    user_resp = UserResponse(**_make_valid_user_response())

    with patch("routers.auth.db", mock_db), \
            patch("routers.auth.hash_password", return_value="hashed_pw"), \
            patch("routers.auth.create_token", return_value="jwt_token"), \
            patch("routers.auth.user_to_response", return_value=user_resp), \
            patch("routers.auth.check_owner_name_against_roll", return_value=True), \
            patch("routers.auth.log_activity", AsyncMock()), \
            patch("asyncio.create_task"):
        yield


# ---------------------------------------------------------------------------
# TestRegistrationBuildingContext — FIX 1
# ---------------------------------------------------------------------------

class TestRegistrationBuildingContext:
    """FIX 1: register() has _building_id = Depends(get_optional_building).
    This ensures units (a TENANT_SCOPED_COLLECTION) can be looked up with
    automatic building_id injection via the db wrapper.
    """

    def test_register_signature_has_building_id_param(self):
        """register() must declare _building_id in its signature."""
        from routers.auth import register
        sig = inspect.signature(register)
        assert "_building_id" in sig.parameters, (
            "register() is missing _building_id parameter — Fix 1 not applied"
        )

    def test_building_id_param_uses_depends(self):
        """_building_id param must be a FastAPI Depends(...) — not a plain default."""
        from routers.auth import register
        from fastapi import params as fastapi_params

        sig = inspect.signature(register)
        param = sig.parameters["_building_id"]
        assert isinstance(param.default, fastapi_params.Depends), (
            "_building_id must use Depends(get_optional_building)"
        )

    def test_building_id_depends_on_get_optional_building(self):
        """The Depends dependency must be get_optional_building specifically."""
        from routers.auth import register
        from utils.auth import get_optional_building
        from fastapi import params as fastapi_params

        sig = inspect.signature(register)
        dep = sig.parameters["_building_id"].default
        assert isinstance(dep, fastapi_params.Depends)
        assert dep.dependency is get_optional_building, (
            "_building_id dependency must be get_optional_building"
        )

    @pytest.mark.asyncio
    async def test_register_without_unit_succeeds(self):
        """Owner registration without unit_number must succeed with no building context error."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(unit_number=None, role="owner")
        mock_db = _make_mock_db(unit_doc=None)

        with _register_patches(mock_db):
            result = await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )
        assert result is not None
        assert hasattr(result, "token")

    @pytest.mark.asyncio
    async def test_register_with_unit_uses_db_units_find_one(self):
        """When unit_number is provided, db.units.find_one must be called with it."""
        _disable_rate_limit()
        from routers.auth import register

        unit = _make_unit("UA001")
        user_data = _make_user_create(unit_number="UA001", role="owner")
        mock_db = _make_mock_db(unit_doc=unit)

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        mock_db.units.find_one.assert_called_once()
        call_filter = mock_db.units.find_one.call_args[0][0]
        assert call_filter.get("unit_number") == "UA001"

    @pytest.mark.asyncio
    async def test_register_invalid_unit_raises_400(self):
        """If unit_number is given but no matching unit found, raise HTTP 400."""
        _disable_rate_limit()
        from routers.auth import register
        from fastapi import HTTPException

        user_data = _make_user_create(unit_number="BADUNIT", role="owner")
        mock_db = _make_mock_db(unit_doc=None)  # unit not found

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.hash_password", return_value="hashed"), \
                patch("routers.auth.check_owner_name_against_roll", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                await register(
                    request=_make_starlette_request(),
                    user_data=user_data,
                    background_tasks=_make_background_tasks(),
                    _building_id="13195",
                )
        assert exc_info.value.status_code == 400
        assert "Invalid unit" in exc_info.value.detail


# ---------------------------------------------------------------------------
# TestRegistrationEmailNotification — FIX 2
# ---------------------------------------------------------------------------

class TestRegistrationEmailNotification:
    """FIX 2: safe_b_name / safe_b_addr must be defined in the else branch
    (non-owner-approval path) before the admin notification email template
    uses them.
    """

    def test_else_branch_fetches_settings_before_template(self):
        """The else branch in register() must have a settings lookup before safe_b_name is used."""
        import ast

        auth_path = os.path.join(
            os.path.dirname(__file__), "../../backend/routers/auth.py"
        )
        source = open(auth_path).read()
        tree = ast.parse(source)

        register_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "register":
                register_func = node
                break
        assert register_func is not None, "register() function not found in auth.py"

        found_else_with_settings = False
        for node in ast.walk(register_func):
            if not isinstance(node, ast.If):
                continue
            test_str = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
            if "requires_owner_approval" not in test_str:
                continue
            # Check else branch for a settings fetch (either settings.find_one or
            # _get_general_settings_or_default / _get_general_settings helper).
            for else_node in ast.walk(ast.Module(body=node.orelse, type_ignores=[])):
                if isinstance(else_node, ast.Await):
                    call_str = ast.unparse(else_node) if hasattr(ast, "unparse") else ""
                    if ("settings" in call_str and "find_one" in call_str) or \
                            "general_settings" in call_str:
                        found_else_with_settings = True
                        break

        assert found_else_with_settings, (
            "else branch in register() does not fetch settings before using safe_b_name"
        )

    @pytest.mark.asyncio
    async def test_owner_registration_no_crash_on_admin_email(self):
        """Owner registration (else branch, no owner-approval) must not raise NameError for safe_b_name."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number="UA001")
        unit = _make_unit("UA001")
        admin = _make_admin_user()
        mock_db = _make_mock_db(unit_doc=unit, admin_users=[admin])

        with _register_patches(mock_db):
            result = await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )
        assert result is not None

    @pytest.mark.asyncio
    async def test_tenant_no_owners_no_crash_on_admin_email(self):
        """Tenant with no existing unit owners (requires_owner_approval=False) → else branch → must not NameError."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(
            role="tenant",
            unit_number="UA001",
            by_laws_acknowledged=True,
        )
        unit = _make_unit("UA001")
        admin = _make_admin_user()
        # No owners → requires_owner_approval = False → else branch
        mock_db = _make_mock_db(unit_doc=unit, unit_owners=[], admin_users=[admin])

        with _register_patches(mock_db):
            result = await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )
        assert result is not None

    @pytest.mark.asyncio
    async def test_admin_email_contains_building_name_from_settings(self):
        """Admin notification email body must contain the building name from db.settings."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number="UA001")
        unit = _make_unit("UA001")
        admin = _make_admin_user(email="admin@eastgate.com")
        settings = {
            "building_name": "SENTINEL_TEST_BUILDING",
            "building_address": "99 Test Street",
        }
        mock_db = _make_mock_db(unit_doc=unit, admin_users=[admin], settings_doc=settings)
        bt = _make_background_tasks()

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=bt,
                _building_id="13195",
            )

        email_calls = bt.add_task.call_args_list
        found = any("SENTINEL_TEST_BUILDING" in str(c) for c in email_calls)
        assert found, (
            "Admin email body must contain the building name from db.settings"
        )

    @pytest.mark.asyncio
    async def test_safe_b_name_never_unbound_owner_no_unit(self):
        """Owner with no unit — else branch fires, must not NameError on safe_b_name."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number=None)
        admin = _make_admin_user()
        mock_db = _make_mock_db(unit_doc=None, admin_users=[admin])

        with _register_patches(mock_db):
            result = await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )
        assert result is not None


# ---------------------------------------------------------------------------
# TestRegistrationMembershipCreation — FIX 4 (most important)
# ---------------------------------------------------------------------------

class TestRegistrationMembershipCreation:
    """FIX 4: register() must insert a memberships record.
    get_users() starts from db.memberships.aggregate() — without a membership,
    the newly registered user is invisible in the admin user list.
    """

    @pytest.mark.asyncio
    async def test_register_creates_membership_record(self):
        """db.memberships.insert_one must be called once during registration."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number="UA001")
        mock_db = _make_mock_db(unit_doc=_make_unit("UA001"))

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        mock_db.memberships.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_membership_has_correct_building_id(self):
        """Membership building_id must equal the _building_id parameter passed to register()."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number="UA001")
        mock_db = _make_mock_db(unit_doc=_make_unit("UA001"))

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        doc = mock_db.memberships.insert_one.call_args[0][0]
        assert doc["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_register_membership_has_correct_role(self):
        """Membership roles list must contain the user's registered role."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number="UA001")
        mock_db = _make_mock_db(unit_doc=_make_unit("UA001"))

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        doc = mock_db.memberships.insert_one.call_args[0][0]
        assert doc["roles"] == ["owner"]

    @pytest.mark.asyncio
    async def test_register_membership_includes_unit(self):
        """If unit_number is given, membership.units must contain it."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number="UA042")
        mock_db = _make_mock_db(unit_doc=_make_unit("UA042"))

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        doc = mock_db.memberships.insert_one.call_args[0][0]
        assert "UA042" in doc["units"]

    @pytest.mark.asyncio
    async def test_register_membership_empty_units_when_no_unit(self):
        """If no unit_number provided, membership.units must be []."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number=None)
        mock_db = _make_mock_db(unit_doc=None)

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        doc = mock_db.memberships.insert_one.call_args[0][0]
        assert doc["units"] == []

    @pytest.mark.asyncio
    async def test_membership_user_id_matches_inserted_user(self):
        """Membership user_id must match the user id written to db.users."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number=None)
        mock_db = _make_mock_db(unit_doc=None)

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        user_doc = mock_db.users.insert_one.call_args[0][0]
        membership_doc = mock_db.memberships.insert_one.call_args[0][0]
        assert membership_doc["user_id"] == user_doc["id"]

    @pytest.mark.asyncio
    async def test_membership_is_active_true(self):
        """Newly created membership must be active."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number=None)
        mock_db = _make_mock_db(unit_doc=None)

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        doc = mock_db.memberships.insert_one.call_args[0][0]
        assert doc["is_active"] is True

    @pytest.mark.asyncio
    async def test_membership_and_user_inserts_both_called(self):
        """Both db.users.insert_one and db.memberships.insert_one must be called."""
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(role="owner", unit_number=None)
        mock_db = _make_mock_db(unit_doc=None)

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        mock_db.users.insert_one.assert_called_once()
        mock_db.memberships.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_buildings_create_distinct_memberships(self):
        """Two registrations with different building_ids create distinct membership records."""
        _disable_rate_limit()
        from routers.auth import register

        user_data_a = _make_user_create(email="alpha@example.com", role="owner", unit_number=None)
        user_data_b = _make_user_create(email="beta@example.com", role="owner", unit_number=None)
        mock_db_a = _make_mock_db(unit_doc=None)
        mock_db_b = _make_mock_db(unit_doc=None)

        with _register_patches(mock_db_a):
            await register(
                request=_make_starlette_request(),
                user_data=user_data_a,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        with _register_patches(mock_db_b):
            await register(
                request=_make_starlette_request(),
                user_data=user_data_b,
                background_tasks=_make_background_tasks(),
                _building_id="16244",
            )

        mem_a = mock_db_a.memberships.insert_one.call_args[0][0]
        mem_b = mock_db_b.memberships.insert_one.call_args[0][0]
        assert mem_a["building_id"] == "13195"
        assert mem_b["building_id"] == "16244"
        assert mem_a["user_id"] != mem_b["user_id"]

    def test_memberships_present_in_database_module(self):
        """memberships must appear in database.py (as GLOBAL_COLLECTION — not tenant-scoped)."""
        db_path = os.path.join(
            os.path.dirname(__file__), "../../backend/database.py"
        )
        if not os.path.exists(db_path):
            pytest.skip("database.py not found")
        source = open(db_path).read()
        assert "memberships" in source, "memberships must appear in database.py"

    @pytest.mark.asyncio
    async def test_register_rollback_on_persistence_failure(self):
        """When persistence fails, rollback must clean up db.users (and not NameError)."""
        _disable_rate_limit()
        from routers.auth import register
        from fastapi import HTTPException

        user_data = _make_user_create(role="owner", unit_number=None)
        mock_db = _make_mock_db(unit_doc=None)
        # Simulate DB write failure for memberships
        mock_db.memberships.insert_one = AsyncMock(side_effect=Exception("Mongo timeout"))

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.hash_password", return_value="hashed"), \
                patch("routers.auth.check_owner_name_against_roll", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                await register(
                    request=_make_starlette_request(),
                    user_data=user_data,
                    background_tasks=_make_background_tasks(),
                    _building_id="13195",
                )
        assert exc_info.value.status_code == 500
        assert "Registration failed" in exc_info.value.detail
        # Rollback must have deleted the user record
        mock_db.users.delete_one.assert_called_once()


# ---------------------------------------------------------------------------
# TestOwnerRegistrationPersistence — public owner workflow
# ---------------------------------------------------------------------------

class TestOwnerRegistrationPersistence:
    """Owner registration should persist the records the workflow depends on."""

    @pytest.mark.asyncio
    async def test_owner_registration_creates_user_unit_record(self):
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(
            role="owner",
            unit_number="UA001",
            by_laws_acknowledged=True,
        )
        mock_db = _make_mock_db(unit_doc=_make_unit("UA001"))

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        mock_db.user_units.insert_one.assert_called_once()
        user_unit_doc = mock_db.user_units.insert_one.call_args[0][0]
        assert user_unit_doc["unit_number"] == "UA001"
        assert user_unit_doc["role_at_unit"] == "owner"
        assert user_unit_doc["is_primary"] is True
        # _register_patches mocks check_owner_name_against_roll to return True
        # (name matches strata roll) → is_approved=True → is_active=True
        assert user_unit_doc["is_active"] is True

    @pytest.mark.asyncio
    async def test_owner_registration_with_bylaws_acknowledgment_persists_ack_record(self):
        _disable_rate_limit()
        from routers.auth import register

        user_data = _make_user_create(
            role="owner",
            unit_number="UA001",
            by_laws_acknowledged=True,
        )
        mock_db = _make_mock_db(unit_doc=_make_unit("UA001"))

        with _register_patches(mock_db):
            await register(
                request=_make_starlette_request(),
                user_data=user_data,
                background_tasks=_make_background_tasks(),
                _building_id="13195",
            )

        mock_db.by_laws_acknowledgments.insert_one.assert_called_once()
        user_doc = mock_db.users.insert_one.call_args[0][0]
        ack_doc = mock_db.by_laws_acknowledgments.insert_one.call_args[0][0]
        assert ack_doc["user_id"] == user_doc["id"]
        assert ack_doc["is_current"] is True
        assert ack_doc["by_laws_version"] == "2026-v1"


# ---------------------------------------------------------------------------
# TestStaffRegistrationWorkflow — pending admin approval surfaces
# ---------------------------------------------------------------------------

class TestStaffRegistrationWorkflow:
    """Staff registration must feed the same membership-driven admin workflow as resident approvals."""

    @pytest.mark.asyncio
    async def test_staff_self_registration_creates_membership_and_manager_notifications(self):
        _disable_rate_limit()
        from routers.auth import register_staff

        reviewer_cursor = MagicMock()
        reviewer_cursor.to_list = AsyncMock(return_value=[
            {"id": "super-1", "email": "super@example.com", "role": "super_admin", "full_name": "Super Admin"},
            {"id": "ec-1", "email": "ec@example.com", "role": "ec_member", "full_name": "EC Member"},
            {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
        ])
        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=None)
        mock_db.users.find = MagicMock(return_value=reviewer_cursor)
        mock_db.users.insert_one = AsyncMock(return_value=MagicMock())
        mock_db.users.delete_one = AsyncMock(return_value=MagicMock())
        mock_db.buildings.find_one = AsyncMock(return_value={"id": "13195"})
        mock_db.memberships.insert_one = AsyncMock(return_value=MagicMock())
        mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock())
        mock_db.memberships.distinct = AsyncMock(return_value=["ec-1", "manager-1"])
        mock_db.user_notifications.insert_many = AsyncMock(return_value=MagicMock())

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.hash_password", return_value="hashed_pw"), \
                patch("routers.auth.send_email_async", AsyncMock()), \
                patch("routers.auth.create_audit_log", AsyncMock()), \
                patch("routers.auth.asyncio.create_task"):
            result = await register_staff(
                request=_make_starlette_request(),
                data=_make_staff_register_request(),
            )

        user_doc = mock_db.users.insert_one.call_args[0][0]
        membership_doc = mock_db.memberships.insert_one.call_args[0][0]
        notifications = mock_db.user_notifications.insert_many.call_args[0][0]

        assert user_doc["status"] == "active"
        assert user_doc["is_active"] is False
        assert user_doc["is_approved"] is False
        assert membership_doc["building_id"] == "13195"
        assert membership_doc["roles"] == ["service_provider"]
        assert membership_doc["user_id"] == user_doc["id"]
        assert {n["user_id"] for n in notifications} == {"super-1", "ec-1", "manager-1"}
        assert all(n["type"] == "user_approval" for n in notifications)
        assert all(n["link"].startswith("/admin/users?tab=service") for n in notifications)
        assert result["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_staff_self_registration_requires_explicit_building(self):
        _disable_rate_limit()
        from routers.auth import register_staff

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value=None)

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.hash_password", return_value="hashed_pw"):
            with pytest.raises(HTTPException) as exc:
                await register_staff(
                    request=_make_starlette_request(),
                    data=_make_staff_register_request(building_id=""),
                )

        assert exc.value.status_code == 400
        assert "Building is required" in exc.value.detail

    @pytest.mark.asyncio
    async def test_admin_create_staff_user_creates_membership(self):
        _disable_rate_limit()
        from routers.auth import admin_create_staff_user, AdminCreateStaffRequest

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": "13195"})
        mock_db.users.insert_one = AsyncMock(return_value=MagicMock())
        mock_db.users.delete_one = AsyncMock(return_value=MagicMock())
        mock_db.memberships.insert_one = AsyncMock(return_value=MagicMock())
        mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock())

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.hash_password", return_value="hashed_pw"), \
                patch("routers.auth.create_audit_log", AsyncMock()), \
                patch("routers.auth.asyncio.create_task"):
            result = await admin_create_staff_user(
                data=AdminCreateStaffRequest(
                    email="manager@example.com",
                    password="pytest-fixture-password-not-a-credential",
                    full_name="Building Manager",
                    role="strata_manager",
                    phone="0400000000",
                    organisation="East Gate",
                    professional_licence="LIC-200",
                    building_id="13195",
                    send_welcome_email=False,
                ),
                current_user={"id": "super-1", "role": "super_admin", "full_name": "Super Admin"},
                building_id="13195",
            )

        membership_doc = mock_db.memberships.insert_one.call_args[0][0]
        assert membership_doc["roles"] == ["strata_manager"]
        assert membership_doc["building_id"] == "13195"
        assert result["is_active"] is True

    @pytest.mark.asyncio
    async def test_manager_can_create_service_provider_for_their_building(self):
        _disable_rate_limit()
        from routers.auth import admin_create_staff_user, AdminCreateStaffRequest

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": "13195"})
        mock_db.users.insert_one = AsyncMock(return_value=MagicMock())
        mock_db.users.delete_one = AsyncMock(return_value=MagicMock())
        mock_db.memberships.insert_one = AsyncMock(return_value=MagicMock())
        mock_db.memberships.delete_one = AsyncMock(return_value=MagicMock())
        mock_db.memberships.find_one = AsyncMock(return_value={
            "user_id": "manager-1",
            "building_id": "13195",
            "is_active": True,
            "roles": ["strata_manager"],
        })

        manager_user = {
            "id": "manager-1",
            "role": "strata_manager",
            "full_name": "Building Manager",
            "is_approved": True,
            "permissions": {"can_manage_users": True},
        }

        with patch("routers.auth.db", mock_db), \
                patch("routers.auth.hash_password", return_value="hashed_pw"), \
                patch("routers.auth.create_audit_log", AsyncMock()), \
                patch("routers.auth.asyncio.create_task"):
            result = await admin_create_staff_user(
                data=AdminCreateStaffRequest(
                    email="service@example.com",
                    password="pytest-fixture-password-not-a-credential",
                    full_name="Service User",
                    role="service_provider",
                    phone="0400000001",
                    organisation="Fix It Pty Ltd",
                    building_id="13195",
                    send_welcome_email=False,
                ),
                current_user=manager_user,
                building_id="13195",
            )

        membership_doc = mock_db.memberships.insert_one.call_args[0][0]
        assert membership_doc["roles"] == ["service_provider"]
        assert membership_doc["building_id"] == "13195"
        assert result["role"] == "service_provider"

    @pytest.mark.asyncio
    async def test_manager_needs_manager_role_in_target_building_to_create_service_provider(self):
        _disable_rate_limit()
        from routers.auth import admin_create_staff_user, AdminCreateStaffRequest

        mock_db = MagicMock()
        mock_db.users.find_one = AsyncMock(return_value=None)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": "13195"})
        mock_db.memberships.find_one = AsyncMock(return_value={
            "user_id": "manager-1",
            "building_id": "13195",
            "is_active": True,
            "roles": ["tenant"],
        })

        manager_user = {
            "id": "manager-1",
            "role": "strata_manager",
            "full_name": "Building Manager",
            "is_approved": True,
            "permissions": {"can_manage_users": True},
        }

        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await admin_create_staff_user(
                    data=AdminCreateStaffRequest(
                        email="service@example.com",
                        password="pytest-fixture-password-not-a-credential",
                        full_name="Service User",
                        role="service_provider",
                        phone="0400000001",
                        organisation="Fix It Pty Ltd",
                        building_id="13195",
                        send_welcome_email=False,
                    ),
                    current_user=manager_user,
                    building_id="13195",
                )

        assert exc.value.status_code == 403
        assert "building manager" in exc.value.detail

    @pytest.mark.asyncio
    async def test_manager_cannot_create_management_staff_roles(self):
        _disable_rate_limit()
        from routers.auth import admin_create_staff_user, AdminCreateStaffRequest

        mock_db = MagicMock()
        manager_user = {
            "id": "manager-1",
            "role": "strata_manager",
            "full_name": "Building Manager",
            "is_approved": True,
            "permissions": {"can_manage_users": True},
        }

        with patch("routers.auth.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await admin_create_staff_user(
                    data=AdminCreateStaffRequest(
                        email="staff@example.com",
                        password="pytest-fixture-password-not-a-credential",
                        full_name="Admin Staff",
                        role="admin_staff",
                        building_id="13195",
                        send_welcome_email=False,
                    ),
                    current_user=manager_user,
                    building_id="13195",
                )

        assert exc.value.status_code == 400
        assert "service_provider" in exc.value.detail


# ---------------------------------------------------------------------------
# TestDCAStatusAuditLog — FIX 3
# ---------------------------------------------------------------------------

class TestDCAStatusAuditLog:
    """FIX 3: update_dca_status() must call create_audit_log() after updating
    the unit document, creating a traceable audit trail.
    """

    @pytest.mark.asyncio
    async def test_update_dca_status_calls_create_audit_log(self):
        """create_audit_log must be called once for each successful DCA status update."""
        from routers.finance import update_dca_status

        mock_db = _make_finance_mock_db()
        mock_audit = AsyncMock()

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", mock_audit):
            await update_dca_status("UA042", "referred", _make_finance_user(), "13195")

        mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_log_has_correct_action(self):
        """Audit log action keyword must be 'dca_status_manual_update'."""
        from routers.finance import update_dca_status

        mock_db = _make_finance_mock_db()
        mock_audit = AsyncMock()

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", mock_audit):
            await update_dca_status("UA042", "recovering", _make_finance_user(), "13195")

        assert mock_audit.call_args[1]["action"] == "dca_status_manual_update"

    @pytest.mark.asyncio
    async def test_audit_log_has_correct_resource_type(self):
        """Audit log resource_type must be 'unit'."""
        from routers.finance import update_dca_status

        mock_db = _make_finance_mock_db()
        mock_audit = AsyncMock()

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", mock_audit):
            await update_dca_status("TH087", "none", _make_finance_user(), "13195")

        kwargs = mock_audit.call_args[1]
        assert kwargs["resource_type"] == "unit"
        assert kwargs["resource_id"] == "TH087"

    @pytest.mark.asyncio
    async def test_audit_log_has_new_dca_status_in_details(self):
        """Audit log details must include new_dca_status with the value passed."""
        from routers.finance import update_dca_status

        mock_db = _make_finance_mock_db()
        mock_audit = AsyncMock()

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", mock_audit):
            await update_dca_status("UA042", "resolved", _make_finance_user(), "13195")

        details = mock_audit.call_args[1]["details"]
        assert "new_dca_status" in details
        assert details["new_dca_status"] == "resolved"

    @pytest.mark.asyncio
    async def test_audit_log_records_user_id(self):
        """Audit log must capture the user_id of the actor who made the change."""
        from routers.finance import update_dca_status

        mock_db = _make_finance_mock_db()
        mock_audit = AsyncMock()
        current_user = _make_finance_user()

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", mock_audit):
            await update_dca_status("UA042", "eligible", current_user, "13195")

        assert mock_audit.call_args[1]["user_id"] == current_user["id"]

    @pytest.mark.asyncio
    async def test_audit_log_records_building_id_in_details(self):
        """Audit log details must include building_id for multi-tenant traceability."""
        from routers.finance import update_dca_status

        mock_db = _make_finance_mock_db()
        mock_audit = AsyncMock()

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", mock_audit):
            await update_dca_status("UA042", "referred", _make_finance_user(), "13195")

        assert mock_audit.call_args[1]["details"]["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_update_dca_status_rejects_invalid_status(self):
        """Invalid status must raise HTTP 400 and must NOT call audit log."""
        from routers.finance import update_dca_status
        from fastapi import HTTPException

        mock_db = _make_finance_mock_db()
        mock_audit = AsyncMock()

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", mock_audit):
            with pytest.raises(HTTPException) as exc_info:
                await update_dca_status("UA042", "INVALID_STATUS", _make_finance_user(), "13195")

        assert exc_info.value.status_code == 400
        mock_audit.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_dca_status_returns_success_true(self):
        """Successful update must return {'success': True}."""
        from routers.finance import update_dca_status

        mock_db = _make_finance_mock_db()

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.create_audit_log", AsyncMock()):
            result = await update_dca_status("UA042", "recalled", _make_finance_user(), "13195")

        assert result["success"] is True

    def test_create_audit_log_imported_in_finance_router(self):
        """create_audit_log must be imported/available in routers/finance.py."""
        finance_path = os.path.join(
            os.path.dirname(__file__), "../../backend/routers/finance.py"
        )
        source = open(finance_path).read()
        assert "create_audit_log" in source, (
            "create_audit_log not found in routers/finance.py — Fix 3 not applied"
        )

    @pytest.mark.asyncio
    async def test_all_six_valid_statuses_accepted(self):
        """All six allowed DCA status values must pass validation without HTTP 400."""
        from routers.finance import update_dca_status

        valid_statuses = ["none", "eligible", "referred", "recovering", "resolved", "recalled"]

        for status_val in valid_statuses:
            mock_db = _make_finance_mock_db()
            with patch("routers.finance.db", mock_db), \
                    patch("routers.finance.create_audit_log", AsyncMock()):
                result = await update_dca_status("UA042", status_val, _make_finance_user(), "13195")
            assert result["success"] is True, f"Status '{status_val}' should be accepted"


# ---------------------------------------------------------------------------
# TestRegistrationSignatureIntegrity — structural safety checks
# ---------------------------------------------------------------------------

class TestRegistrationSignatureIntegrity:
    """Structural checks to verify the register() and update_dca_status()
    endpoints have the correct signature and route configuration post-fix.
    """

    def test_register_has_request_param(self):
        from routers.auth import register
        assert "request" in inspect.signature(register).parameters

    def test_register_has_user_data_param(self):
        from routers.auth import register
        assert "user_data" in inspect.signature(register).parameters

    def test_register_has_background_tasks_param(self):
        from routers.auth import register
        assert "background_tasks" in inspect.signature(register).parameters

    def test_register_is_async(self):
        from routers.auth import register
        assert asyncio.iscoroutinefunction(register), "register() must be async"

    def test_register_is_post_endpoint(self):
        """register() must be registered as a POST route on /auth/register."""
        from routers.auth import router
        routes = {r.path: r for r in router.routes}
        assert "/auth/register" in routes, "/auth/register route not found in auth router"
        assert "POST" in routes["/auth/register"].methods

    def test_update_dca_status_is_patch_endpoint(self):
        """update_dca_status must be a PATCH endpoint."""
        from routers.finance import router as finance_router
        routes = {r.path: r for r in finance_router.routes}
        assert "/arrears/{unit_number}/dca-status" in routes
        assert "PATCH" in routes["/arrears/{unit_number}/dca-status"].methods

    def test_update_dca_status_is_async(self):
        from routers.finance import update_dca_status
        assert asyncio.iscoroutinefunction(update_dca_status)

    def test_register_decorated_with_rate_limit(self):
        """register() must be wrapped with the rate_limit decorator."""
        auth_path = os.path.join(
            os.path.dirname(__file__), "../../backend/routers/auth.py"
        )
        source = open(auth_path).read()
        # @rate_limit("rate_limit_register", ...) must precede async def register
        import re
        pattern = r'@rate_limit\(["\']rate_limit_register["\']'
        assert re.search(pattern, source), (
            "register() must be decorated with @rate_limit('rate_limit_register', ...)"
        )
