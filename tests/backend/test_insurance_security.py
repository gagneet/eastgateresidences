import os
import sys
from unittest.mock import AsyncMock, patch, MagicMock, ANY
from bson import ObjectId

import pytest
from fastapi import HTTPException

# Ensure backend is in path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, 'backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from routers.insurance import (
    list_insurance_policies,
    get_insurance_policy,
    update_insurance_policy,
    cancel_insurance_policy
)


@pytest.mark.asyncio
async def test_insurance_endpoints_use_dependency_building_id():
    """Verify that insurance endpoints use the building_id from the dependency, not an untrusted source."""
    mock_user = {"id": "user-123", "full_name": "Finance User", "role": "strata_manager"}
    verified_building_id = "verified-building-789"
    policy_id = str(ObjectId())

    mock_db = MagicMock()
    mock_db.insurance_policies.count_documents = AsyncMock(return_value=0)

    # Mock for list_insurance_policies
    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.skip.return_value = mock_cursor
    mock_cursor.limit.return_value = mock_cursor
    mock_cursor.__aiter__.return_value = []
    mock_db.insurance_policies.find.return_value = mock_cursor

    # Mock for get_insurance_policy
    mock_db.insurance_policies.find_one = AsyncMock(return_value=None)

    # Mock for update/cancel
    mock_db.insurance_policies.update_one = AsyncMock(return_value=MagicMock(matched_count=0))

    with patch("routers.insurance._get_db", return_value=mock_db), \
            patch("routers.insurance._require_finance", return_value=mock_user), \
            patch("routers.insurance.create_audit_log", new_callable=AsyncMock) as mock_audit:
        # 1. Test list_insurance_policies
        await list_insurance_policies(
            building_id=verified_building_id,
            page=1,
            page_size=20,
            current_user=mock_user
        )
        # Verify query used the verified building_id
        mock_db.insurance_policies.count_documents.assert_called_with({"building_id": verified_building_id})

        # 2. Test get_insurance_policy
        with pytest.raises(HTTPException):
            await get_insurance_policy(
                policy_id=policy_id,
                building_id=verified_building_id,
                current_user=mock_user
            )
        mock_db.insurance_policies.find_one.assert_called_with({
            "_id": ObjectId(policy_id),
            "building_id": verified_building_id,
        })

        # 3. Test update_insurance_policy
        with pytest.raises(HTTPException):
            await update_insurance_policy(
                policy_id=policy_id,
                building_id=verified_building_id,
                payload={"insurer_name": "New Insurer"},
                current_user=mock_user
            )
        mock_db.insurance_policies.update_one.assert_any_call(
            {"_id": ObjectId(policy_id), "building_id": verified_building_id},
            {"$set": {"insurer_name": "New Insurer", "updated_at": ANY}}
        )

        # 4. Test cancel_insurance_policy
        with pytest.raises(HTTPException):
            await cancel_insurance_policy(
                policy_id=policy_id,
                building_id=verified_building_id,
                reason="Expired",
                current_user=mock_user
            )
        mock_db.insurance_policies.update_one.assert_any_call(
            {"_id": ObjectId(policy_id), "building_id": verified_building_id},
            {"$set": {
                "status": "cancelled",
                "cancelled_at": ANY,
                "cancelled_by": "user-123",
                "cancellation_reason": "Expired",
                "updated_at": ANY,
            }}
        )


@pytest.mark.asyncio
async def test_audit_log_uses_correct_signature():
    """Verify that create_audit_log is called with the new signature (positional arguments)."""
    mock_user = {"id": "user-123", "full_name": "Finance User", "role": "strata_manager"}
    verified_building_id = "verified-building-789"
    policy_id = str(ObjectId())

    mock_db = MagicMock()
    # Mock update_one to succeed
    mock_db.insurance_policies.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    with patch("routers.insurance._get_db", return_value=mock_db), \
            patch("routers.insurance._require_finance", return_value=mock_user), \
            patch("routers.insurance.create_audit_log", new_callable=AsyncMock) as mock_audit:
        await update_insurance_policy(
            policy_id=policy_id,
            building_id=verified_building_id,
            payload={"insurer_name": "New Insurer"},
            current_user=mock_user
        )

        # Small delay to allow asyncio task to run
        import asyncio
        await asyncio.sleep(0.1)

        # Check signature: create_audit_log(action, resource_type, resource_id, user_id, user_name, details, building_id)
        args, kwargs = mock_audit.call_args
        assert args[0] == "insurance_policy_updated"
        assert args[1] == "insurance_policy"
        assert args[2] == policy_id
        assert args[3] == "user-123"
        assert args[4] == "Finance User"
        assert args[5] == {"fields": ["insurer_name", "updated_at"]}
        assert args[6] == verified_building_id
