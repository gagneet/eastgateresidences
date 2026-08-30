from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from fastapi import HTTPException, Request, BackgroundTasks

import server
from models.user import UserRole, UserUpdate, UserCreate


@pytest.mark.asyncio
@patch("server.db")
async def test_get_unit_occupants_masking(mock_db):
    """Test that email is masked for non-privileged users."""
    unit_number = "101"
    mock_db.units.find_one = AsyncMock(return_value={"unit_number": unit_number, "lot_number": "1"})

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[
        {"user_id": "user1", "role_at_unit": "owner", "start_date": "2024-01-01", "is_active": True}
    ])
    mock_db.user_units.find.return_value = mock_cursor

    mock_user_cursor = MagicMock()
    mock_user_cursor.to_list = AsyncMock(return_value=[
        {"id": "user1", "full_name": "John Doe", "email": "john.doe@example.com", "role": "owner"}
    ])
    mock_db.users.find.return_value = mock_user_cursor

    # Unauthenticated user — email should be masked
    response = await server.get_unit_occupants(unit_number, current_user=None)
    assert response["occupants"][0]["email"] == "j***@example.com"

    # Different user — email masked
    other_user = {"id": "user2", "role": "owner", "full_name": "Jane Doe"}
    response = await server.get_unit_occupants(unit_number, current_user=other_user)
    assert response["occupants"][0]["email"] == "j***@example.com"

    # The user themselves — email visible
    self_user = {"id": "user1", "role": "owner", "full_name": "John Doe"}
    response = await server.get_unit_occupants(unit_number, current_user=self_user)
    assert response["occupants"][0]["email"] == "john.doe@example.com"

    # Admin — email visible
    admin_user = {"id": "admin1", "role": UserRole.SUPER_ADMIN, "full_name": "Admin"}
    response = await server.get_unit_occupants(unit_number, current_user=admin_user)
    assert response["occupants"][0]["email"] == "john.doe@example.com"


@pytest.mark.asyncio
@patch("server.get_user_permissions")
@patch("server.db")
async def test_update_user_privilege_escalation(mock_db, mock_perms):
    """Test that strata manager cannot promote users to super_admin."""
    user_id = "user1"
    update_data = UserUpdate(role=UserRole.SUPER_ADMIN)

    mock_db.users.find_one = AsyncMock(return_value={
        "id": user_id, "role": "owner", "full_name": "User"
    })
    mock_db.memberships.find_one = AsyncMock(return_value={
        "building_id": "13195",
        "user_id": user_id,
        "is_active": True,
    })

    mock_p = MagicMock()
    mock_p.can_manage_users = True
    mock_perms.return_value = mock_p

    manager_user = {"id": "manager1", "role": UserRole.STRATA_MANAGER, "full_name": "Manager"}

    with pytest.raises(HTTPException) as exc:
        await server.update_user(
            user_id,
            update_data,
            current_user=manager_user,
            building_id="13195",
        )

    assert exc.value.status_code == 403
    assert "Only super admins can assign the super admin role" in exc.value.detail


@pytest.mark.asyncio
@patch("server.get_user_permissions")
@patch("server.db")
async def test_update_user_approval_activates_staff_account(mock_db, mock_perms):
    """Approving a pending staff account must also set is_active=True."""
    user_id = "staff1"
    update_data = UserUpdate(is_approved=True)

    target_user = {
        "id": user_id,
        "role": "service_provider",
        "full_name": "Stephanie Edwards",
        "email": "sedwards@360degree.net.au",
        "created_at": "2026-04-14T00:00:00Z",
        "is_active": False,
        "is_approved": False,
        "status": "active",
    }

    mock_db.memberships.find_one = AsyncMock(return_value={
        "building_id": "13195",
        "user_id": user_id,
        "is_active": True,
    })
    find_one_calls = {"count": 0}

    async def _find_one_side_effect(*args, **kwargs):
        find_one_calls["count"] += 1
        if find_one_calls["count"] >= 3:
            return {**target_user, "is_active": True, "is_approved": True}
        return target_user

    mock_db.users.find_one = AsyncMock(side_effect=_find_one_side_effect)
    manager_cursor = MagicMock()
    manager_cursor.to_list = AsyncMock(return_value=[])
    mock_db.users.find = MagicMock(return_value=manager_cursor)
    mock_db.user_units.update_many = AsyncMock()
    mock_db.users.update_one = AsyncMock(return_value=MagicMock())

    # The approval path now resolves the building's display name for the
    # approval email, and scopes reviewer notifications to this building's
    # active memberships. Both need awaitable doubles.
    mock_db.settings.find_one = AsyncMock(return_value={"building_name": "Test Building"})
    membership_cursor = MagicMock()
    membership_cursor.to_list = AsyncMock(return_value=[])
    mock_db.memberships.find = MagicMock(return_value=membership_cursor)

    mock_p = MagicMock()
    mock_p.can_manage_users = True
    mock_perms.return_value = mock_p

    admin_user = {"id": "admin1", "role": UserRole.SUPER_ADMIN, "full_name": "Admin", "email": "admin@example.com"}

    def _consume_task(coro):
        coro.close()
        return MagicMock()

    with patch("server.send_email_async", AsyncMock()), \
            patch("server.create_user_notification", AsyncMock()), \
            patch("server.create_audit_log", AsyncMock()), \
            patch("server.asyncio.create_task", side_effect=_consume_task):
        await server.update_user(
            user_id,
            update_data,
            current_user=admin_user,
            building_id="13195",
        )

    update_doc = mock_db.users.update_one.call_args[0][1]["$set"]
    assert update_doc["is_approved"] is True
    assert update_doc["is_active"] is True
    assert update_doc["status"] == "active"


@pytest.mark.asyncio
@patch("server.get_user_permissions")
@patch("server.db")
async def test_delete_user_protection(mock_db, mock_perms):
    """Test that a strata manager cannot delete a super_admin account."""
    target_user_id = "admin1"

    async def _find_one_side_effect(query, *args, **kwargs):
        if "email" in query:
            return None
        if query.get("id") == target_user_id:
            return {"id": target_user_id, "role": UserRole.SUPER_ADMIN, "full_name": "Super Admin"}
        return None

    mock_db.users.find_one = AsyncMock(side_effect=_find_one_side_effect)
    mock_db.memberships.find_one = AsyncMock(return_value={
        "building_id": "13195",
        "user_id": target_user_id,
        "is_active": True,
    })

    mock_p = MagicMock()
    mock_p.can_manage_users = True
    mock_perms.return_value = mock_p

    manager_user = {"id": "manager1", "role": UserRole.STRATA_MANAGER, "full_name": "Manager"}

    with pytest.raises(HTTPException) as exc:
        await server.delete_user(target_user_id, current_user=manager_user, building_id="13195")

    assert exc.value.status_code == 403
    assert "Only super admins can remove other super admin accounts" in exc.value.detail


@pytest.mark.asyncio
@patch("routers.auth.db")
async def test_register_role_restriction(mock_db):
    """Test that registering with super_admin role is blocked."""
    from routers.auth import register
    test_user = UserCreate(
        email="hacker@test.com",
        password="password123",
        full_name="Hacker",
        role=UserRole.SUPER_ADMIN,
        terms_accepted=True
    )

    mock_db.users.find_one = AsyncMock(return_value=None)

    mock_request = MagicMock(spec=Request)
    mock_request.scope = {"type": "http"}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"

    background_tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as exc:
        await register(mock_request, test_user, background_tasks)

    assert exc.value.status_code == 400
    assert "Invalid role" in exc.value.detail
