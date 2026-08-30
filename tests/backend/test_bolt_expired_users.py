from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server import get_expired_users


def _make_mock_db():
    """Create a fresh MagicMock db with properly typed async methods."""
    mock_db = MagicMock()

    def setup_mock_collection(coll):
        coll.find.return_value.sort.return_value.to_list = AsyncMock()
        coll.find.return_value.to_list = AsyncMock()
        coll.count_documents = AsyncMock()
        return coll

    setup_mock_collection(mock_db.user_units)
    setup_mock_collection(mock_db.users)
    setup_mock_collection(mock_db.tenant_renewal_requests)
    return mock_db


@pytest.mark.asyncio
@patch("server.db")
async def test_get_expired_users_optimized(mock_db_patch):
    """Test that get_expired_users uses parallel fetching and batch enrichment correctly."""

    # Configure the mock db properly
    mock_db = _make_mock_db()
    mock_db_patch.user_units = mock_db.user_units
    mock_db_patch.users = mock_db.users
    mock_db_patch.tenant_renewal_requests = mock_db.tenant_renewal_requests

    mock_admin = {"role": "super_admin", "id": "admin1", "full_name": "Admin User"}

    today = datetime.now(timezone.utc).isoformat()
    expired_tenant = {
        "id": "rel1",
        "user_id": "user1",
        "unit_number": "101",
        "role_at_unit": "tenant",
        "expiration_date": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "is_active": True
    }
    expired_guest = {
        "id": "rel2",
        "user_id": "user2",
        "unit_number": "102",
        "role_at_unit": "guest",
        "end_date": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "is_active": True
    }

    user1 = {"id": "user1", "full_name": "Tenant One", "email": "t1@example.com", "is_active": True}
    user2 = {"id": "user2", "full_name": "Guest Two", "email": "g2@example.com", "is_active": True}
    renewal_req = {"user_id": "user1", "unit_number": "101", "status": "pending"}

    mock_db.user_units.find.return_value.sort.return_value.to_list.side_effect = [
        [expired_tenant],
        [expired_guest]
    ]
    mock_db.users.find.return_value.to_list.return_value = [user1, user2]
    mock_db.tenant_renewal_requests.find.return_value.to_list.return_value = [renewal_req]

    result = await get_expired_users(current_user=mock_admin)

    assert len(result) == 2

    t1 = next(u for u in result if u["user_id"] == "user1")
    assert t1["full_name"] == "Tenant One"
    assert t1["has_renewal_request"] is True

    g2 = next(u for u in result if u["user_id"] == "user2")
    assert g2["full_name"] == "Guest Two"
    assert g2["has_renewal_request"] is False

    assert mock_db.users.find.called
    user_find_calls = [call for call in mock_db.users.find.call_args_list if "$in" in str(call)]
    assert len(user_find_calls) > 0

    assert mock_db.tenant_renewal_requests.find.called
    renewal_find_calls = [call for call in mock_db.tenant_renewal_requests.find.call_args_list if "$or" in str(call)]
    assert len(renewal_find_calls) > 0
