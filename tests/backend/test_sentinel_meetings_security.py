import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException


# Mock users
def _super_admin_user():
    return {
        "id": "admin-id",
        "full_name": "Super Admin",
        "role": "super_admin",
        "is_approved": True,
    }


def _victim_user():
    return {
        "id": "victim-id",
        "full_name": "Victim Name",
        "email": "victim@other-building.com",
        "role": "owner",
    }


@pytest.mark.asyncio
async def test_cross_tenant_todo_leakage_blocked():
    """
    Vulnerability: create_todo leaks full_name of any user in the system by ID.
    Fix: Verify membership before fetching name.
    """
    from routers.meetings import create_todo
    from models.community import TodoCreate

    victim = _victim_user()
    todo_data = TodoCreate(
        title="Probe",
        assigned_to=victim["id"],
        due_date="2026-12-31",
        priority="normal"
    )

    with patch("routers.meetings.db") as mock_db, \
            patch("routers.meetings.get_user_permissions") as mock_perms:
        mock_perms.return_value = MagicMock(can_manage_meetings=True)

        # Mock membership lookup - return None (user doesn't belong to building-a)
        mock_db.memberships.find_one = AsyncMock(return_value=None)

        # ACT: Call the endpoint - should now raise 400
        with pytest.raises(HTTPException) as exc:
            await create_todo(
                todo=todo_data,
                current_user=_super_admin_user(),
                building_id="building-a"
            )

        assert exc.value.status_code == 400
        assert "does not belong to this building" in exc.value.detail


@pytest.mark.asyncio
async def test_cross_tenant_agm_attendance_blocked():
    """
    Vulnerability: update_attendance allows adding any user ID to a building's AGM.
    Fix: Verify membership of user and proxy.
    """
    from routers.meetings import update_attendance
    from models.community import AGMAttendanceUpdate

    victim = _victim_user()
    agm_id = "agm-123"
    building_a = "building-a"

    attendance_data = AGMAttendanceUpdate(
        user_id=victim["id"],
        status="attending"
    )

    with patch("routers.meetings.db") as mock_db:
        # Mock membership lookup - return None
        mock_db.memberships.find_one = AsyncMock(return_value=None)

        # ACT: should now raise 400
        with pytest.raises(HTTPException) as exc:
            await update_attendance(
                agm_id=agm_id,
                data=attendance_data,
                current_user=_super_admin_user(),
                building_id=building_a
            )

        assert exc.value.status_code == 400
        assert "does not belong to this building" in exc.value.detail
