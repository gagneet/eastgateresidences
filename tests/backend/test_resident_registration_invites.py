import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.background import BackgroundTasks
from starlette.requests import Request as StarletteRequest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

from models.user import UserRole
from routers.auth import (
    ResidentRegistrationInviteCreate,
    UserCreate,
    _get_staff_registration_reviewers,
    create_resident_registration_invite,
    get_resident_registration_invite,
    register,
)
from request_context import set_ctx_building_id
from utils.rate_limit import _DummyLimiter


def _request_scope():
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/register",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }


def _cursor(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


@pytest.fixture(autouse=True)
def _test_context(monkeypatch):
    set_ctx_building_id("B-100")
    monkeypatch.setenv("FRONTEND_URL", "https://portal.example.test")
    with patch("utils.rate_limit.limiter", _DummyLimiter()):
        yield


@pytest.mark.asyncio
async def test_create_resident_invite_uses_configured_frontend_url_and_building_scope():
    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"unit_number": "101"})
    mock_db.resident_registration_invites.insert_one = AsyncMock()
    mock_db.buildings.find_one = AsyncMock(return_value={
        "id": "B-100",
        "name": "Harbour View",
        "address": "1 Test Street",
    })

    current_user = {
        "id": "mgr-1",
        "role": UserRole.STRATA_MANAGER,
        "full_name": "Manager",
        "is_active": True,
    }
    body = ResidentRegistrationInviteCreate(
        role=UserRole.OWNER,
        unit_number="101",
        full_name="Avery Owner",
        email="avery@example.com",
    )

    with patch("routers.auth.db", mock_db), patch("routers.auth.send_email_async", AsyncMock()):
        result = await create_resident_registration_invite(
            body,
            BackgroundTasks(),
            current_user=current_user,
            building_id="B-100",
        )

    assert result["invite_url"].startswith("https://portal.example.test/register?invite=")
    inserted = mock_db.resident_registration_invites.insert_one.call_args.args[0]
    assert inserted["building_id"] == "B-100"
    assert inserted["unit_number"] == "101"
    assert inserted["role"] == UserRole.OWNER
    assert "token_hash" in inserted


@pytest.mark.asyncio
async def test_get_resident_invite_returns_prefill_without_token_hash():
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    mock_db = MagicMock()
    mock_db.resident_registration_invites.find_one = AsyncMock(return_value={
        "id": "invite-1",
        "building_id": "B-100",
        "role": UserRole.TENANT,
        "unit_number": "202",
        "full_name": "Taylor Tenant",
        "email": "taylor@example.com",
        "phone": "0400000000",
        "expires_at": expires_at,
    })
    mock_db.buildings.find_one = AsyncMock(return_value={
        "id": "B-100",
        "name": "Harbour View",
        "address": "1 Test Street",
    })

    with patch("routers.auth.db", mock_db):
        result = await get_resident_registration_invite(
            StarletteRequest(_request_scope()), "raw-token"
        )

    assert result["building"]["id"] == "B-100"
    assert result["role"] == UserRole.TENANT
    assert result["unit_number"] == "202"
    assert result["full_name"] == "Taylor Tenant"
    assert "token_hash" not in result


@pytest.mark.asyncio
async def test_register_rejects_invite_for_different_unit():
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    mock_db = MagicMock()
    mock_db.resident_registration_invites.find_one = AsyncMock(return_value={
        "id": "invite-1",
        "building_id": "B-100",
        "role": UserRole.OWNER,
        "unit_number": "101",
        "status": "pending",
        "expires_at": expires_at,
    })
    user_data = UserCreate(
        email="avery@example.com",
        password="pytest-fixture-password-not-a-credential",
        full_name="Avery Owner",
        role=UserRole.OWNER,
        unit_number="999",
        phone="0400000000",
        by_laws_acknowledged=True,
        invite_token="raw-token",
    )

    with patch("routers.auth.db", mock_db):
        with pytest.raises(HTTPException) as exc:
            await register(StarletteRequest(_request_scope()), user_data, BackgroundTasks(), _building_id="B-100")

    assert exc.value.status_code == 400
    assert "different unit" in exc.value.detail


@pytest.mark.asyncio
async def test_registration_reviewers_are_building_members_plus_platform_super_admins():
    """Reviewers = this building's management staff, plus platform super_admins.

    The two clauses are deliberately different shapes: building staff are matched
    by active membership of THIS building (so one building's staff never review
    another's residents), while super_admin is a platform-wide role that holds no
    per-building membership and would be invisible to a membership-only query.
    """
    mock_db = MagicMock()
    mock_db.memberships.distinct = AsyncMock(return_value=["sa-1", "staff-1", "owner-1", "other-building-manager"])
    mock_db.users.find = MagicMock(return_value=_cursor([]))

    with patch("routers.auth.db", mock_db):
        await _get_staff_registration_reviewers("B-100")

    user_query = mock_db.users.find.call_args.args[0]
    building_clause, super_admin_clause = user_query["$or"]

    assert building_clause["id"] == {"$in": ["sa-1", "staff-1", "owner-1", "other-building-manager"]}
    assert set(building_clause["role"]["$in"]) == {
        UserRole.STRATA_ADMIN,
        UserRole.ADMIN_STAFF,
        UserRole.STRATA_MANAGER,
    }
    # super_admins must NOT be constrained by building membership, or they would
    # never be notified for any building.
    assert super_admin_clause == {"role": UserRole.SUPER_ADMIN, "is_active": True}
    assert "id" not in super_admin_clause


# ── Security-critical paths ───────────────────────────────────────────────────
# The invite token is the only credential on the public sign-up path, so the
# building/unit/role/expiry checks below are the whole trust boundary.


def _pending_invite(**overrides):
    invite = {
        "id": "invite-1",
        "building_id": "B-100",
        "role": UserRole.OWNER,
        "unit_number": "101",
        "status": "pending",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    invite.update(overrides)
    return invite


def _user_create(**overrides):
    payload = {
        "email": "avery@example.com",
        "password": "pytest-fixture-password-not-a-credential",
        "full_name": "Avery Owner",
        "role": UserRole.OWNER,
        "unit_number": "101",
        "phone": "0400000000",
        "by_laws_acknowledged": True,
        "invite_token": "raw-token",
    }
    payload.update(overrides)
    return UserCreate(**payload)


async def _register_expecting_400(mock_db, user_data, building_id):
    with patch("routers.auth.db", mock_db):
        with pytest.raises(HTTPException) as exc:
            await register(
                StarletteRequest(_request_scope()),
                user_data,
                BackgroundTasks(),
                _building_id=building_id,
            )
    assert exc.value.status_code == 400
    return exc.value.detail


@pytest.mark.asyncio
async def test_register_rejects_invite_issued_for_a_different_building():
    """An invite for building A must not create an account in building B.

    This is the core multi-tenant boundary of the whole feature: the token is
    public (it is emailed, and lands in a URL), so the server — not the caller —
    has to decide which building it belongs to.
    """
    mock_db = MagicMock()
    mock_db.resident_registration_invites.find_one = AsyncMock(return_value=_pending_invite(building_id="B-100"))

    detail = await _register_expecting_400(mock_db, _user_create(), building_id="B-999")
    assert "different building" in detail


@pytest.mark.asyncio
async def test_register_rejects_invite_when_no_building_context_supplied():
    """Fails closed: no X-Building-ID means get_optional_building() returns None.

    Registration must not silently fall through to a default building — an
    earlier version of get_optional_building() returned DEFAULT_BUILDING_ID,
    which would have routed every context-less invite to East Gate.
    """
    mock_db = MagicMock()
    mock_db.resident_registration_invites.find_one = AsyncMock(return_value=_pending_invite())

    detail = await _register_expecting_400(mock_db, _user_create(), building_id=None)
    assert "different building" in detail


@pytest.mark.asyncio
async def test_register_rejects_invite_for_a_different_role():
    """The invited role is fixed by the sender; a tenant invite cannot self-upgrade to owner."""
    mock_db = MagicMock()
    mock_db.resident_registration_invites.find_one = AsyncMock(
        return_value=_pending_invite(role=UserRole.TENANT)
    )

    detail = await _register_expecting_400(
        mock_db, _user_create(role=UserRole.OWNER), building_id="B-100"
    )
    assert "different user type" in detail


@pytest.mark.asyncio
async def test_register_rejects_expired_invite_and_marks_it_expired():
    mock_db = MagicMock()
    mock_db.resident_registration_invites.find_one = AsyncMock(
        return_value=_pending_invite(
            expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        )
    )
    mock_db.resident_registration_invites.update_one = AsyncMock()

    detail = await _register_expecting_400(mock_db, _user_create(), building_id="B-100")
    assert "expired" in detail

    # The invite is burned rather than left pending for a later retry.
    update_filter, update_doc = mock_db.resident_registration_invites.update_one.call_args.args
    assert update_filter["id"] == "invite-1"
    assert update_doc["$set"]["status"] == "expired"


@pytest.mark.asyncio
async def test_register_rejects_unknown_or_already_used_invite():
    """Consumed invites are status='used', so the pending-only lookup misses them."""
    mock_db = MagicMock()
    mock_db.resident_registration_invites.find_one = AsyncMock(return_value=None)

    detail = await _register_expecting_400(mock_db, _user_create(), building_id="B-100")
    assert "not found or already used" in detail


@pytest.mark.asyncio
async def test_public_invite_lookup_rejects_expired_invite_with_410():
    mock_db = MagicMock()
    mock_db.resident_registration_invites.find_one = AsyncMock(
        return_value=_pending_invite(
            expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        )
    )
    mock_db.resident_registration_invites.update_one = AsyncMock()

    with patch("routers.auth.db", mock_db):
        with pytest.raises(HTTPException) as exc:
            await get_resident_registration_invite(
                StarletteRequest(_request_scope()), "raw-token"
            )

    assert exc.value.status_code == 410


# ── Authorisation on invite creation ─────────────────────────────────────────


@pytest.mark.parametrize(
    "role",
    [UserRole.OWNER, UserRole.TENANT, UserRole.EC_MEMBER, UserRole.ADMIN_STAFF, UserRole.GUEST],
)
@pytest.mark.asyncio
async def test_only_admins_and_managers_may_create_invites(role):
    """Sending an invite provisions building access, so it is not an owner/EC action.

    Note ec_member and admin_staff are included here deliberately: both are
    'management-ish' roles that a reader might assume can invite, and the
    frontend gates the button on canManageUsers (which ec_member HAS), so the
    backend guard is the only thing actually enforcing this.
    """
    mock_db = MagicMock()
    body = ResidentRegistrationInviteCreate(
        role=UserRole.OWNER, unit_number="101", full_name="Avery Owner"
    )

    with patch("routers.auth.db", mock_db):
        with pytest.raises(HTTPException) as exc:
            await create_resident_registration_invite(
                body,
                BackgroundTasks(),
                current_user={"id": "u-1", "role": role, "is_active": True, "is_approved": True},
                building_id="B-100",
            )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_invite_creation_honours_temporary_elevation():
    """_can_send_resident_invite reads effective_role, not the raw role field.

    An owner elevated to strata_manager must be allowed; checking user["role"]
    here would deny them (the documented elevated-role footgun).
    """
    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"unit_number": "101"})
    mock_db.resident_registration_invites.insert_one = AsyncMock()
    mock_db.buildings.find_one = AsyncMock(return_value={"id": "B-100", "name": "Harbour View"})

    body = ResidentRegistrationInviteCreate(
        role=UserRole.OWNER, unit_number="101", full_name="Avery Owner"
    )
    elevated = {
        "id": "owner-1",
        "role": UserRole.OWNER,
        "effective_role": UserRole.STRATA_MANAGER,
        "is_active": True,
        "is_approved": True,
    }

    with patch("routers.auth.db", mock_db):
        result = await create_resident_registration_invite(
            body, BackgroundTasks(), current_user=elevated, building_id="B-100"
        )

    assert result["invite_url"].startswith("https://portal.example.test/register?invite=")


@pytest.mark.asyncio
async def test_invite_creation_rejects_staff_roles():
    """Resident invites cannot be used to mint manager/admin accounts."""
    mock_db = MagicMock()
    body = ResidentRegistrationInviteCreate(
        role=UserRole.STRATA_MANAGER, unit_number="101", full_name="Someone"
    )

    with patch("routers.auth.db", mock_db):
        with pytest.raises(HTTPException) as exc:
            await create_resident_registration_invite(
                body,
                BackgroundTasks(),
                current_user={
                    "id": "sa-1", "role": UserRole.SUPER_ADMIN,
                    "is_active": True, "is_approved": True,
                },
                building_id="B-100",
            )

    assert exc.value.status_code == 400
    assert "owners, tenants, and guests" in exc.value.detail


@pytest.mark.asyncio
async def test_invite_creation_rejects_unit_not_in_this_building():
    """db.units is tenant-scoped, so a miss means the unit is not in the caller's building."""
    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value=None)
    body = ResidentRegistrationInviteCreate(
        role=UserRole.OWNER, unit_number="999", full_name="Avery Owner"
    )

    with patch("routers.auth.db", mock_db):
        with pytest.raises(HTTPException) as exc:
            await create_resident_registration_invite(
                body,
                BackgroundTasks(),
                current_user={
                    "id": "mgr-1", "role": UserRole.STRATA_MANAGER,
                    "is_active": True, "is_approved": True,
                },
                building_id="B-100",
            )

    assert exc.value.status_code == 400
    assert "Unit not found" in exc.value.detail


@pytest.mark.asyncio
async def test_no_hardcoded_domain_in_invite_url():
    """The invite URL must come from configuration, never a baked-in domain."""
    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"unit_number": "101"})
    mock_db.resident_registration_invites.insert_one = AsyncMock()
    mock_db.buildings.find_one = AsyncMock(return_value={"id": "B-100", "name": "Harbour View"})

    body = ResidentRegistrationInviteCreate(
        role=UserRole.OWNER, unit_number="101", full_name="Avery Owner"
    )
    manager = {
        "id": "mgr-1", "role": UserRole.STRATA_MANAGER,
        "is_active": True, "is_approved": True,
    }

    with patch("routers.auth.db", mock_db), \
            patch.dict(os.environ, {"FRONTEND_URL": "https://another-strata.example.org"}):
        result = await create_resident_registration_invite(
            body, BackgroundTasks(), current_user=manager, building_id="B-100"
        )

    assert result["invite_url"].startswith("https://another-strata.example.org/register?invite=")
    assert "eastgateresidences" not in result["invite_url"]


# ── Approval reachability ────────────────────────────────────────────────────


def test_every_notified_reviewer_role_can_actually_approve():
    """Whoever we email "please approve" must be able to approve.

    Approval runs through PUT /users/{id}, which requires can_manage_users, and
    listing pending users through GET /users requires the same. A role in
    _STAFF_REVIEWER_ROLES without that permission gets an approval email whose
    link 403s — notified but powerless.

    See tasks/GAP-ONBOARD-005 for the admin_staff decision this pins.
    """
    from routers.auth import _STAFF_REVIEWER_ROLES
    from utils.permissions import get_user_permissions

    # super_admin is notified via its own clause in _get_staff_registration_reviewers
    # rather than via _STAFF_REVIEWER_ROLES, so assert it explicitly here too.
    notified_roles = set(_STAFF_REVIEWER_ROLES) | {UserRole.SUPER_ADMIN}

    powerless = sorted(
        role for role in notified_roles
        if not get_user_permissions(
            {"id": "u", "role": role, "is_active": True, "is_approved": True}
        ).can_manage_users
    )

    assert not powerless, (
        f"{powerless} are emailed to approve registrations but lack can_manage_users, "
        "so both GET /users and PUT /users/{id} return 403 for them. Either grant the "
        "permission or stop listing them as reviewers."
    )
