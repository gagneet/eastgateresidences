import copy
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi import HTTPException

from models.maintenance import MaintenanceRequestCreate
from models.user import UserRole
from routers.maintenance import create_maintenance_request, get_maintenance_requests, get_maintenance_request, _mask_maintenance_request


# Mock database
@pytest.fixture
def mock_db():
    with patch("routers.maintenance.db") as mock:
        yield mock


# Mock current user
def create_mock_user(user_id="user1", role=UserRole.TENANT, permissions=None):
    if permissions is None:
        from models.user import DEFAULT_PERMISSIONS
        permissions = DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS[UserRole.GUEST])

    return {
        "id": user_id,
        "full_name": "Test User",
        "email": "test@example.com",
        "role": role,
        "is_active": True,
        "is_approved": True,
        "custom_permissions": {}
    }


@pytest.mark.asyncio
async def test_maintenance_visibility_and_masking(mock_db):
    # Setup mock data
    other_private_request = {
        "id": "req-private",
        "title": "Private Leak",
        "description": "Leaking toilet",
        "category": "plumbing",
        "location": "Unit 101",
        "priority": "normal",
        "status": "approved",
        "images": ["img1.jpg"],
        "submitted_by": "other-user",
        "submitted_by_name": "Other Resident",
        "assigned_contractor": None,
        "estimated_cost": 500.0,
        "actual_cost": 450.0,
        "purchase_order_id": "po-1",
        "invoice_id": "inv-1",
        "approval_history": [{"status": "approved", "by": "Admin"}],
        "notes": [{"content": "Fixing soon"}],
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    }

    common_area_request = {
        "id": "req-common",
        "title": "Broken Lift",
        "description": "Lift 2 is out of order",
        "category": "common_area",
        "location": "Main Lobby",
        "priority": "high",
        "status": "in_progress",
        "images": [],
        "submitted_by": "other-user",
        "submitted_by_name": "Other Resident",
        "assigned_contractor": "contractor-1",
        "estimated_cost": 2000.0,
        "actual_cost": None,
        "purchase_order_id": "po-2",
        "invoice_id": None,
        "approval_history": [],
        "notes": [],
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    }

    my_request = {
        "id": "req-mine",
        "title": "My Squeaky Door",
        "description": "Bedroom door squeaks",
        "category": "carpentry",
        "location": "Unit 202",
        "priority": "low",
        "status": "submitted",
        "images": [],
        "submitted_by": "tenant-1",
        "submitted_by_name": "Test User",
        "assigned_contractor": None,
        "estimated_cost": 100.0,
        "actual_cost": None,
        "purchase_order_id": None,
        "invoice_id": None,
        "approval_history": [],
        "notes": [],
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    }

    all_requests = [other_private_request, common_area_request, my_request]

    # Mock current user (Tenant)
    tenant_user = create_mock_user(user_id="tenant-1", role=UserRole.TENANT)

    # --- Test Listing (get_maintenance_requests) for Tenant ---
    with patch("routers.maintenance.get_user_permissions") as mock_get_perms:
        from models.user import DEFAULT_PERMISSIONS
        tenant_perms = DEFAULT_PERMISSIONS[UserRole.TENANT]
        mock_get_perms.return_value = tenant_perms

        # Mock DB find to simulate the filter
        seen_queries = []

        def mock_find(query, projection=None):
            seen_queries.append(query)
            results = []
            for r in all_requests:
                # Basic simulation of the visibility logic
                matches = False
                if "submitted_by" in query and query["submitted_by"] == tenant_user["id"]:
                    if r["submitted_by"] == tenant_user["id"]: matches = True
                elif "assigned_contractor" in query and query["assigned_contractor"] == tenant_user["id"]:
                    if r["assigned_contractor"] == tenant_user["id"]: matches = True
                elif "$or" in query:
                    matches_or = False
                    for cond in query["$or"]:
                        # Simple field match
                        matched_cond = True
                        for k, v in cond.items():
                            if r.get(k) != v:
                                matched_cond = False
                                break
                        if matched_cond:
                            matches_or = True
                            break
                    if matches_or: matches = True

                if matches:
                    results.append(r)

            cursor = MagicMock()
            cursor.sort.return_value = cursor
            cursor.to_list = AsyncMock(return_value=results)
            return cursor

        mock_db.maintenance_requests.find = mock_find

        results = await get_maintenance_requests(current_user=tenant_user)

        # Should only see my_request and common_area_request
        assert len(results) == 2
        ids = [r.id for r in results]
        assert "req-mine" in ids
        assert "req-common" in ids
        assert "req-private" not in ids

        # Check masking for common area request
        common_res = next(r for r in results if r.id == "req-common")
        assert common_res.estimated_cost is None
        assert common_res.purchase_order_id is None
        assert common_res.submitted_by_name == "Other Resident"  # Common area name is NOT masked

        # Check NO masking for own request
        my_res = next(r for r in results if r.id == "req-mine")
        assert my_res.estimated_cost == 100.0
        assert my_res.submitted_by_name == "Test User"
        assert seen_queries[0].get("is_test_data") == {"$ne": True}

        await get_maintenance_requests(include_test_data=True, current_user=tenant_user)
        assert seen_queries[-1].get("is_test_data") == {"$ne": True}

        manager_user = create_mock_user(user_id="manager-1", role=UserRole.STRATA_MANAGER)
        manager_perms = copy.copy(tenant_perms)
        manager_perms.can_manage_requests = True
        mock_get_perms.return_value = manager_perms
        await get_maintenance_requests(include_test_data=True, current_user=manager_user)
        assert "is_test_data" not in seen_queries[-1]

    # --- Test Singular Access (get_maintenance_request) for Tenant ---
    with patch("routers.maintenance.get_user_permissions") as mock_get_perms:
        mock_get_perms.return_value = tenant_perms

        # 1. Accessing own request - OK
        mock_db.maintenance_requests.find_one = AsyncMock(return_value=my_request)
        res = await get_maintenance_request(req_id="req-mine", current_user=tenant_user)
        assert res.estimated_cost == 100.0

        # 2. Accessing common area request - OK (but masked)
        mock_db.maintenance_requests.find_one = AsyncMock(return_value=common_area_request)
        res = await get_maintenance_request(req_id="req-common", current_user=tenant_user)
        assert res.estimated_cost is None

        # 3. Accessing other's private request - 403 Forbidden
        mock_db.maintenance_requests.find_one = AsyncMock(return_value=other_private_request)
        with pytest.raises(HTTPException) as excinfo:
            await get_maintenance_request(req_id="req-private", current_user=tenant_user)
        assert excinfo.value.status_code == 403

    # --- Test Contractor Access ---
    contractor_user = create_mock_user(user_id="contractor-1", role=UserRole.SERVICE_PROVIDER)
    with patch("routers.maintenance.get_user_permissions") as mock_get_perms:
        contractor_perms = DEFAULT_PERMISSIONS[UserRole.SERVICE_PROVIDER]
        mock_get_perms.return_value = contractor_perms

        # Contractor can see request assigned to them with costs
        mock_db.maintenance_requests.find_one = AsyncMock(return_value=common_area_request)
        res = await get_maintenance_request(req_id="req-common", current_user=contractor_user)
        assert res.estimated_cost == 2000.0
        assert res.submitted_by_name == "Other Resident"

        # Test assigned_to_me=True parameter for contractor
        def mock_find_contractor(query, projection=None):
            results = []
            if "assigned_contractor" in query and query["assigned_contractor"] == contractor_user["id"]:
                results = [common_area_request]
            cursor = MagicMock()
            cursor.sort.return_value = cursor
            cursor.to_list = AsyncMock(return_value=results)
            return cursor

        mock_db.maintenance_requests.find = mock_find_contractor
        results = await get_maintenance_requests(assigned_to_me=True, current_user=contractor_user)
        assert len(results) == 1
        assert results[0].id == "req-common"

    # --- Test Admin Access (get_maintenance_request) ---
    admin_user = create_mock_user(user_id="admin-1", role=UserRole.SUPER_ADMIN)
    with patch("routers.maintenance.get_user_permissions") as mock_get_perms:
        admin_perms = DEFAULT_PERMISSIONS[UserRole.SUPER_ADMIN]
        mock_get_perms.return_value = admin_perms

        # Admin can see other's private request with costs
        mock_db.maintenance_requests.find_one = AsyncMock(return_value=other_private_request)
        res = await get_maintenance_request(req_id="req-private", current_user=admin_user)
        assert res.estimated_cost == 500.0
        assert res.submitted_by_name == "Other Resident"

    # --- Test Masking Integrity ---
    # Verified by Bolt ⚡: We use shallow copy for performance.
    # This ensures top-level fields (which are the ones we mask) are isolated.
    tenant_user = create_mock_user(user_id="tenant-1", role=UserRole.TENANT)
    with patch("routers.maintenance.get_user_permissions") as mock_get_perms:
        mock_get_perms.return_value = DEFAULT_PERMISSIONS[UserRole.TENANT]

        original = copy.deepcopy(other_private_request)
        masked = _mask_maintenance_request(original, tenant_user, is_admin=False)

        # Modify top-level masked fields in masked
        masked["estimated_cost"] = 9999.9
        masked["submitted_by_name"] = "Hacker"

        # Verify original is NOT affected (Top-level isolation)
        assert original["estimated_cost"] == 500.0
        assert original["submitted_by_name"] == "Other Resident"


@pytest.mark.asyncio
async def test_maintenance_test_data_flag_is_role_gated(mock_db):
    """Residents cannot hide records by sending is_test_data; super admins can for perf cleanup."""
    payload = MaintenanceRequestCreate(
        title="Perf test request perf-role-gate",
        description="Automated performance test — safe to delete",
        category="common_area",
        location="Level 1",
        priority="low",
        images=[],
        is_test_data=True,
    )
    inserted_docs = []

    async def _capture_insert(doc):
        inserted_docs.append(doc)

    def _close_task(coro):
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    mock_db.maintenance_requests.insert_one = AsyncMock(side_effect=_capture_insert)
    with (
        patch("routers.maintenance.asyncio.create_task", side_effect=_close_task) as mock_create_task,
        patch("services.request_catalogue_service.get_effective_feature_access", AsyncMock(return_value=True)),
    ):
        owner = create_mock_user(user_id="owner-1", role=UserRole.OWNER)
        owner["building_id"] = "TEST-REQUEST-A"
        await create_maintenance_request(payload, current_user=owner, building_id="TEST-REQUEST-A")
        owner_task_count = mock_create_task.call_count

        super_admin = create_mock_user(user_id="sa-1", role=UserRole.SUPER_ADMIN)
        super_admin["building_id"] = "TEST-REQUEST-A"
        await create_maintenance_request(payload, current_user=super_admin, building_id="TEST-REQUEST-A")

    assert inserted_docs[0]["is_test_data"] is False
    assert inserted_docs[1]["is_test_data"] is True
    assert owner_task_count > 0
    assert mock_create_task.call_count == owner_task_count
