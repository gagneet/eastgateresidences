"""
Comprehensive pytest tests for the Work Order system.

Covers:
- POST   /work-orders           — create a new work order
- GET    /work-orders           — list work orders with optional filters
- POST   /work-orders/{id}/approvals — EC approval submission
- PUT    /invoices/{id}/approve — approve a submitted invoice
- PUT    /invoices/{id}/reject  — reject a submitted invoice with reason

All tests use routers.work_orders (NOT backend.routers.work_orders) because
conftest.py adds backend/ to sys.path directly.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers / fixture factories
# ---------------------------------------------------------------------------

def _admin_user():
    return {
        "id": "user-admin-001",
        "full_name": "Super Admin",
        "role": "super_admin",
        "unit_number": None,
        "ec_position": None,
    }


def _chairman_user():
    # Per architecture (commit 67fbc4a5, migration 0025): chairman is an
    # ec_position on an ec_member user, not a top-level role.
    return {
        "id": "user-chair-001",
        "full_name": "Anthony McDonald",
        "role": "ec_member",
        "unit_number": "UA063",
        "ec_position": "CHAIRMAN",
    }


def _ec_user(user_id="ec-1", ec_position="MEMBER"):
    return {
        "id": user_id,
        "full_name": f"EC Member {user_id}",
        "role": "ec_member",
        "unit_number": "UA001",
        "ec_position": ec_position,
    }


def _strata_manager_user():
    return {
        "id": "user-sm-001",
        "full_name": "Strata Manager",
        "role": "strata_manager",
        "unit_number": None,
        "ec_position": None,
    }


def _owner_user():
    return {
        "id": "user-owner-001",
        "full_name": "Avneet Rooprai",
        "role": "owner",
        "unit_number": "TH087",
        "ec_position": None,
    }


def _make_perms(can_meetings=True, can_finances=True):
    p = MagicMock()
    p.can_manage_meetings = can_meetings
    p.can_manage_finances = can_finances
    return p


def _work_order_create(**kwargs):
    from models.work_order import WorkOrderCreate
    defaults = dict(
        maintenance_request_id=str(uuid.uuid4()),
        building_id="east_gate",
        lot_number="TH087",
        title="Repair Roof",
        description="Leaking roof near gutters",
        priority="high",
        supplier_type="roofing",
        estimated_cost=1500.0,
        requires_approval=True,
        is_emergency=False,
    )
    defaults.update(kwargs)
    return WorkOrderCreate(**defaults)


def _work_order_doc(wo_id=None, **kwargs):
    """Return a minimal work order document as stored in MongoDB."""
    wo_id = wo_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": wo_id,
        "maintenance_request_id": str(uuid.uuid4()),
        "building_id": "east_gate",
        "lot_number": "TH087",
        "title": "Repair Roof",
        "description": "Leaking roof near gutters",
        "priority": "high",
        "supplier_type": "roofing",
        "estimated_cost": 1500.0,
        "requires_approval": True,
        "is_emergency": False,
        "emergency_override": False,
        "status": "new",
        "created_by": "user-admin-001",
        "assigned_vendor": None,
        "vendor_name": None,
        "approval_status": "none",
        "completion_date": None,
        "created_at": now,
        "updated_at": now,
    }
    doc.update(kwargs)
    return doc


def _invoice_doc(inv_id=None, wo_id=None, amount=1200.0, **kwargs):
    inv_id = inv_id or str(uuid.uuid4())
    wo_id = wo_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": inv_id,
        "work_order_id": wo_id,
        "vendor_id": "vendor-001",
        "invoice_number": "INV-001",
        "amount": amount,
        "gst": amount * 0.1,
        "attachments": [],
        "submitted_date": now,
        "completion_photos": [],
        "compliance_certificates": [],
        "approval_status": "submitted",
        "payment_status": "pending",
        "approved_by": None,
        "approved_at": None,
        "paid_at": None,
        "warnings": [],
        "created_at": now,
        "updated_at": now,
    }
    doc.update(kwargs)
    return doc


def _quote_doc(wo_id=None, amount=1000.0, selected=True):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "work_order_id": wo_id or str(uuid.uuid4()),
        "vendor_id": "vendor-001",
        "vendor_name": "Ace Plumbing Pty Ltd",
        "vendor_rating": 4.5,
        "amount": amount,
        "description": "Full roof repair",
        "attachments": [],
        "submitted_date": now,
        "selected": selected,
        "status": "accepted",
        "created_at": now,
    }


def _approval_doc(wo_id, user_id, decision="approve", ec_position="MEMBER"):
    return {
        "id": str(uuid.uuid4()),
        "work_order_id": wo_id,
        "approver_id": user_id,
        "approver_name": f"EC Member {user_id}",
        "role": "ec_member",
        "ec_position": ec_position,
        "decision": decision,
        "comment": "Looks good",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _mock_cursor(items):
    """Return a MagicMock that mimics a Motor cursor with .to_list()."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    return cursor


def _site_settings_single_approval():
    return {
        "id": "main",
        "work_order_thresholds": [
            {
                "max_amount": 5000,
                "approval_mode": "SINGLE_APPROVAL",
                "approval_roles": ["CHAIRMAN", "TREASURER"],
                "approval_required": True,
            }
        ],
    }


def _site_settings_majority(max_amount=20000):
    return {
        "id": "main",
        "work_order_thresholds": [
            {
                "max_amount": max_amount,
                "approval_mode": "MAJORITY",
                "approval_roles": ["MEMBER"],
                "approval_required": True,
            }
        ],
    }


def _site_settings_dual_approval():
    return {
        "id": "main",
        "work_order_thresholds": [
            {
                "max_amount": 10000,
                "approval_mode": "DUAL_APPROVAL",
                "approval_roles": ["CHAIRMAN", "TREASURER"],
                "approval_required": True,
            }
        ],
    }


# ===========================================================================
# CLASS 1: TestCreateWorkOrder
# ===========================================================================

class TestCreateWorkOrder:
    """POST /work-orders — create_work_order()"""

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_creates_work_order_successfully(self, mock_db, mock_perms, mock_audit, mock_log):
        """Admin can create a work order; status defaults to NEW, DB insert called."""
        from routers.work_orders import create_work_order

        data = _work_order_create()
        user = _admin_user()

        mock_perms.return_value = _make_perms()
        mock_db.maintenance_requests.find_one = AsyncMock(
            return_value={"id": data.maintenance_request_id}
        )
        mock_db.work_orders.insert_one = AsyncMock()
        mock_db.maintenance_requests.update_one = AsyncMock()

        result = await create_work_order(data, current_user=user, building_id="13195")

        assert result.title == "Repair Roof"
        assert result.status == "new"
        mock_db.work_orders.insert_one.assert_called_once()
        mock_db.maintenance_requests.update_one.assert_called_once()

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_emergency_work_order_status_approved(self, mock_db, mock_perms, mock_audit, mock_log):
        """Emergency work orders bypass approval and get status APPROVED immediately."""
        from routers.work_orders import create_work_order

        data = _work_order_create(is_emergency=True)
        user = _admin_user()

        mock_perms.return_value = _make_perms()
        mock_db.maintenance_requests.find_one = AsyncMock(
            return_value={"id": data.maintenance_request_id}
        )
        mock_db.work_orders.insert_one = AsyncMock()
        mock_db.maintenance_requests.update_one = AsyncMock()

        result = await create_work_order(data, current_user=user, building_id="13195")

        assert result.status == "approved"

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_missing_maintenance_request_raises_404(self, mock_db, mock_perms, mock_audit, mock_log):
        """404 raised when the linked maintenance request does not exist."""
        from routers.work_orders import create_work_order

        data = _work_order_create()
        user = _admin_user()

        mock_perms.return_value = _make_perms()
        mock_db.maintenance_requests.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await create_work_order(data, current_user=user, building_id="13195")

        assert exc.value.status_code == 404
        assert "Maintenance request not found" in exc.value.detail

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_unauthorized_role_raises_403(self, mock_db, mock_perms, mock_audit, mock_log):
        """Owner role with no manage permissions receives 403."""
        from routers.work_orders import create_work_order

        data = _work_order_create()
        user = _owner_user()

        mock_perms.return_value = _make_perms(can_meetings=False, can_finances=False)

        with pytest.raises(HTTPException) as exc:
            await create_work_order(data, current_user=user, building_id="13195")

        assert exc.value.status_code == 403

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_db_called_with_correct_fields(self, mock_db, mock_perms, mock_audit, mock_log):
        """insert_one receives a document containing id, status, and created_at."""
        from routers.work_orders import create_work_order

        data = _work_order_create()
        user = _admin_user()

        mock_perms.return_value = _make_perms()
        mock_db.maintenance_requests.find_one = AsyncMock(
            return_value={"id": data.maintenance_request_id}
        )
        mock_db.work_orders.insert_one = AsyncMock()
        mock_db.maintenance_requests.update_one = AsyncMock()

        await create_work_order(data, current_user=user, building_id="13195")

        insert_call = mock_db.work_orders.insert_one.call_args[0][0]
        assert "id" in insert_call
        assert "status" in insert_call
        assert "created_at" in insert_call
        assert insert_call["created_by"] == user["id"]

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_chairman_can_create_work_order(self, mock_db, mock_perms, mock_audit, mock_log):
        """Chairman role with can_manage_meetings=True can create work orders."""
        from routers.work_orders import create_work_order

        data = _work_order_create()
        user = _chairman_user()

        mock_perms.return_value = _make_perms(can_meetings=True, can_finances=False)
        mock_db.maintenance_requests.find_one = AsyncMock(
            return_value={"id": data.maintenance_request_id}
        )
        mock_db.work_orders.insert_one = AsyncMock()
        mock_db.maintenance_requests.update_one = AsyncMock()

        result = await create_work_order(data, current_user=user, building_id="13195")

        assert result.status == "new"
        mock_db.work_orders.insert_one.assert_called_once()


# ===========================================================================
# CLASS 2: TestGetWorkOrders
# ===========================================================================

class TestGetWorkOrders:
    """GET /work-orders — get_work_orders()"""

    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_returns_list_of_work_orders(self, mock_db, mock_perms):
        """Admin receives all work orders in the collection."""
        from routers.work_orders import get_work_orders

        wo1 = _work_order_doc()
        wo2 = _work_order_doc()

        mock_perms.return_value = _make_perms()
        mock_db.work_orders.find.return_value = MagicMock(
            sort=MagicMock(return_value=_mock_cursor([wo1, wo2]))
        )

        result = await get_work_orders(status=None, maintenance_request_id=None, current_user=_admin_user(),
                                       building_id="13195")

        assert len(result) == 2
        assert result[0].id == wo1["id"]
        assert result[1].id == wo2["id"]

    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_filter_by_status(self, mock_db, mock_perms):
        """Status filter is passed through to the MongoDB query."""
        from routers.work_orders import get_work_orders

        wo = _work_order_doc(status="pending_approval")

        mock_perms.return_value = _make_perms()
        mock_db.work_orders.find.return_value = MagicMock(
            sort=MagicMock(return_value=_mock_cursor([wo]))
        )

        result = await get_work_orders(status="pending_approval",
                                       maintenance_request_id=None,
                                       current_user=_admin_user(), building_id="13195")

        # Verify the filter was passed to find()
        call_args = mock_db.work_orders.find.call_args[0]
        query = call_args[0]
        assert query.get("status") == "pending_approval"
        assert len(result) == 1

    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_filter_by_maintenance_request_id(self, mock_db, mock_perms):
        """maintenance_request_id filter is included in the MongoDB query."""
        from routers.work_orders import get_work_orders

        req_id = str(uuid.uuid4())
        wo = _work_order_doc(maintenance_request_id=req_id)

        mock_perms.return_value = _make_perms()
        mock_db.work_orders.find.return_value = MagicMock(
            sort=MagicMock(return_value=_mock_cursor([wo]))
        )

        await get_work_orders(status=None,
                              maintenance_request_id=req_id,
                              current_user=_admin_user(), building_id="13195")

        call_args = mock_db.work_orders.find.call_args[0]
        assert call_args[0].get("maintenance_request_id") == req_id

    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_owner_without_unit_returns_empty(self, mock_db, mock_perms):
        """Non-admin user with no unit_number receives an empty list."""
        from routers.work_orders import get_work_orders

        user = _owner_user()
        user["unit_number"] = None  # No unit assigned

        mock_perms.return_value = _make_perms(can_meetings=False, can_finances=False)

        result = await get_work_orders(status=None, maintenance_request_id=None, current_user=user, building_id="13195")

        assert result == []

    @patch("routers.work_orders.get_user_permissions")
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_owner_with_unit_filtered_by_lot(self, mock_db, mock_perms):
        """Owner with a unit_number has lot_number filter applied to query."""
        from routers.work_orders import get_work_orders

        user = _owner_user()
        wo = _work_order_doc(lot_number="TH087")

        mock_perms.return_value = _make_perms(can_meetings=False, can_finances=False)
        mock_db.work_orders.find.return_value = MagicMock(
            sort=MagicMock(return_value=_mock_cursor([wo]))
        )

        await get_work_orders(status=None, maintenance_request_id=None, current_user=user, building_id="13195")

        call_args = mock_db.work_orders.find.call_args[0]
        assert call_args[0].get("lot_number") == "TH087"


# ===========================================================================
# CLASS 3: TestSubmitApproval
# ===========================================================================

class TestSubmitApproval:
    """POST /work-orders/{wo_id}/approvals — submit_approval()"""

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_single_approval_mode_approves_immediately(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """SINGLE_APPROVAL mode: one chairman approval immediately approves the WO."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _chairman_user()

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
            comment="Approved by chairman",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=2000.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_single_approval()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            [_approval_doc(wo_id, user["id"], "approve", "CHAIRMAN")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        result = await submit_approval(wo_id, approval_data, current_user=user)

        # Check the WO was updated to approved
        update_calls = mock_db.work_orders.update_one.call_args_list
        approved_call = next(
            (c for c in update_calls if c[0][1].get("$set", {}).get("status") == "approved"),
            None,
        )
        assert approved_call is not None
        assert result.decision == "approve"

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_majority_approval_mode_3_of_5(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """MAJORITY mode: 3 approvals from 5 EC members meets the threshold."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _ec_user("ec-3")

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
            comment="Looks good",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=6000.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_majority()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor([
            _approval_doc(wo_id, "ec-1", "approve"),
            _approval_doc(wo_id, "ec-2", "approve"),
            _approval_doc(wo_id, "ec-3", "approve"),
        ])
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        update_calls = mock_db.work_orders.update_one.call_args_list
        approved_call = next(
            (c for c in update_calls if c[0][1].get("$set", {}).get("status") == "approved"),
            None,
        )
        assert approved_call is not None

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_reject_decision_cancels_work_order(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """A single reject decision sets work order status to CANCELLED."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _chairman_user()

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="reject",
            comment="Budget exceeded",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=3000.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_single_approval()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            [_approval_doc(wo_id, user["id"], "reject", "CHAIRMAN")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        update_calls = mock_db.work_orders.update_one.call_args_list
        cancelled_call = next(
            (c for c in update_calls if c[0][1].get("$set", {}).get("status") == "cancelled"),
            None,
        )
        assert cancelled_call is not None

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_unauthorized_role_raises_403(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """Owner role cannot submit approvals — 403 raised."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _owner_user()

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
        )

        with pytest.raises(HTTPException) as exc:
            await submit_approval(wo_id, approval_data, current_user=user)

        assert exc.value.status_code == 403

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approval_record_stored_in_db(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """insert_one is called with the correct approval document fields."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _ec_user("ec-99", "TREASURER")

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
            comment="Looks fine",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=1000.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_single_approval()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            [_approval_doc(wo_id, "ec-99", "approve", "TREASURER")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        insert_doc = mock_db.work_order_approvals.insert_one.call_args[0][0]
        assert insert_doc["approver_id"] == "ec-99"
        assert insert_doc["decision"] == "approve"
        assert insert_doc["work_order_id"] == wo_id
        assert "timestamp" in insert_doc


# ===========================================================================
# CLASS 4: TestInvoiceApprove
# ===========================================================================

class TestInvoiceApprove:
    """PUT /work-orders/invoices/{inv_id}/approve — approve_work_order_invoice()"""

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approve_invoice_success(self, mock_db, mock_audit, mock_log):
        """Valid invoice with a matching quote is approved; financial_transactions entry created."""
        from routers.work_orders import approve_work_order_invoice

        wo_id = str(uuid.uuid4())
        inv = _invoice_doc(wo_id=wo_id, amount=900.0)
        wo = _work_order_doc(wo_id=wo_id)
        quote = _quote_doc(wo_id=wo_id, amount=1000.0)

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.work_order_quotes.find_one = AsyncMock(return_value=quote)
        mock_db.work_order_invoices.update_one = AsyncMock()
        mock_db.financial_transactions.insert_one = AsyncMock()

        result = await approve_work_order_invoice(inv["id"], current_user=_chairman_user(), building_id="13195")

        mock_db.work_order_invoices.update_one.assert_called_once()
        mock_db.financial_transactions.insert_one.assert_called_once()

        update_args = mock_db.work_order_invoices.update_one.call_args[0]
        assert update_args[1]["$set"]["approval_status"] == "approved"
        assert result == {"message": "Invoice approved successfully"}

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approve_invoice_no_quote_raises_400(self, mock_db, mock_audit, mock_log):
        """Invoice without a selected quote raises 400 (non-emergency)."""
        from routers.work_orders import approve_work_order_invoice

        wo_id = str(uuid.uuid4())
        inv = _invoice_doc(wo_id=wo_id)
        wo = _work_order_doc(wo_id=wo_id, is_emergency=False)

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.work_order_quotes.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await approve_work_order_invoice(inv["id"], current_user=_chairman_user(), building_id="13195")

        assert exc.value.status_code == 400
        assert "No approved quote" in exc.value.detail

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approve_invoice_amount_double_quote_raises_400(self, mock_db, mock_audit, mock_log):
        """Invoice amount exceeding 2× the quote triggers a hard block (400)."""
        from routers.work_orders import approve_work_order_invoice

        wo_id = str(uuid.uuid4())
        quote = _quote_doc(wo_id=wo_id, amount=1000.0)
        inv = _invoice_doc(wo_id=wo_id, amount=2100.0)  # > 1000 * 2
        wo = _work_order_doc(wo_id=wo_id, is_emergency=False)

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.work_order_quotes.find_one = AsyncMock(return_value=quote)

        with pytest.raises(HTTPException) as exc:
            await approve_work_order_invoice(inv["id"], current_user=_chairman_user(), building_id="13195")

        assert exc.value.status_code == 400
        assert "double" in exc.value.detail.lower() or "Hard block" in exc.value.detail

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approve_invoice_emergency_bypass_no_quote(self, mock_db, mock_audit, mock_log):
        """Emergency work orders bypass the no-quote check and approve successfully."""
        from routers.work_orders import approve_work_order_invoice

        wo_id = str(uuid.uuid4())
        inv = _invoice_doc(wo_id=wo_id, amount=1500.0)
        wo = _work_order_doc(wo_id=wo_id, is_emergency=True)

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.work_order_quotes.find_one = AsyncMock(return_value=None)
        mock_db.work_order_invoices.update_one = AsyncMock()
        mock_db.financial_transactions.insert_one = AsyncMock()

        result = await approve_work_order_invoice(inv["id"], current_user=_chairman_user(), building_id="13195")

        assert result == {"message": "Invoice approved successfully"}
        mock_db.work_order_invoices.update_one.assert_called_once()

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approve_invoice_unauthorized_raises_403(self, mock_db, mock_audit, mock_log):
        """Owner role cannot approve invoices — 403 raised."""
        from routers.work_orders import approve_work_order_invoice

        with pytest.raises(HTTPException) as exc:
            await approve_work_order_invoice("inv-001", current_user=_owner_user(), building_id="13195")

        assert exc.value.status_code == 403

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approve_invoice_not_found_raises_404(self, mock_db, mock_audit, mock_log):
        """404 when the invoice ID does not exist."""
        from routers.work_orders import approve_work_order_invoice

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await approve_work_order_invoice("nonexistent-inv", current_user=_chairman_user(), building_id="13195")

        assert exc.value.status_code == 404
        assert "Invoice not found" in exc.value.detail

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_strata_manager_can_approve(self, mock_db, mock_audit, mock_log):
        """strata_manager role is allowed to approve invoices."""
        from routers.work_orders import approve_work_order_invoice

        wo_id = str(uuid.uuid4())
        inv = _invoice_doc(wo_id=wo_id, amount=800.0)
        wo = _work_order_doc(wo_id=wo_id)
        quote = _quote_doc(wo_id=wo_id, amount=1000.0)

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.work_order_quotes.find_one = AsyncMock(return_value=quote)
        mock_db.work_order_invoices.update_one = AsyncMock()
        mock_db.financial_transactions.insert_one = AsyncMock()

        result = await approve_work_order_invoice(inv["id"], current_user=_strata_manager_user(), building_id="13195")

        assert result == {"message": "Invoice approved successfully"}

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approve_sets_approved_by_and_at(self, mock_db, mock_audit, mock_log):
        """Approval update sets approved_by and approved_at fields."""
        from routers.work_orders import approve_work_order_invoice

        wo_id = str(uuid.uuid4())
        inv = _invoice_doc(wo_id=wo_id, amount=500.0)
        wo = _work_order_doc(wo_id=wo_id)
        quote = _quote_doc(wo_id=wo_id, amount=1000.0)

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.work_order_quotes.find_one = AsyncMock(return_value=quote)
        mock_db.work_order_invoices.update_one = AsyncMock()
        mock_db.financial_transactions.insert_one = AsyncMock()

        user = _chairman_user()
        await approve_work_order_invoice(inv["id"], current_user=user, building_id="13195")

        update_args = mock_db.work_order_invoices.update_one.call_args[0]
        set_fields = update_args[1]["$set"]
        assert set_fields["approved_by"] == user["id"]
        assert "approved_at" in set_fields

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_approve_emergency_override_bypass_double_check(self, mock_db, mock_audit, mock_log):
        """emergency_override flag also bypasses the 2× quote hard block."""
        from routers.work_orders import approve_work_order_invoice

        wo_id = str(uuid.uuid4())
        quote = _quote_doc(wo_id=wo_id, amount=500.0)
        inv = _invoice_doc(wo_id=wo_id, amount=3000.0)  # Far exceeds 2× quote
        wo = _work_order_doc(wo_id=wo_id, is_emergency=False, emergency_override=True)

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.work_order_quotes.find_one = AsyncMock(return_value=quote)
        mock_db.work_order_invoices.update_one = AsyncMock()
        mock_db.financial_transactions.insert_one = AsyncMock()

        result = await approve_work_order_invoice(inv["id"], current_user=_chairman_user(), building_id="13195")

        assert result == {"message": "Invoice approved successfully"}


# ===========================================================================
# CLASS 5: TestInvoiceReject
# ===========================================================================

class TestInvoiceReject:
    """PUT /work-orders/invoices/{inv_id}/reject — reject_work_order_invoice()"""

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_reject_invoice_success(self, mock_db, mock_audit, mock_log):
        """Valid reason with ≥5 chars → invoice rejected, update_one called."""
        from routers.work_orders import reject_work_order_invoice

        inv = _invoice_doc()

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_order_invoices.update_one = AsyncMock()

        result = await reject_work_order_invoice(inv["id"],
                                                 reason="Amount does not match bank records",
                                                 current_user=_chairman_user(), building_id="13195")

        mock_db.work_order_invoices.update_one.assert_called_once()
        assert result == {"message": "Invoice rejected successfully"}

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_reject_invoice_short_reason_raises_422(self, mock_db, mock_audit, mock_log):
        """Rejection reason shorter than 5 characters raises 422."""
        from routers.work_orders import reject_work_order_invoice

        inv = _invoice_doc()
        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)

        with pytest.raises(HTTPException) as exc:
            await reject_work_order_invoice(inv["id"],
                                            reason="Bad",
                                            current_user=_chairman_user(), building_id="13195")

        assert exc.value.status_code == 422

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_reject_invoice_empty_reason_raises_422(self, mock_db, mock_audit, mock_log):
        """Empty/whitespace-only rejection reason raises 422."""
        from routers.work_orders import reject_work_order_invoice

        inv = _invoice_doc()
        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)

        with pytest.raises(HTTPException) as exc:
            await reject_work_order_invoice(inv["id"],
                                            reason="   ",
                                            current_user=_chairman_user(), building_id="13195")

        assert exc.value.status_code == 422

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_reject_invoice_not_found_raises_404(self, mock_db, mock_audit, mock_log):
        """404 raised when the invoice ID does not exist."""
        from routers.work_orders import reject_work_order_invoice

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await reject_work_order_invoice("nonexistent",
                                            reason="Invoice appears fraudulent",
                                            current_user=_chairman_user(), building_id="13195")

        assert exc.value.status_code == 404

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_reject_invoice_unauthorized_raises_403(self, mock_db, mock_audit, mock_log):
        """Owner role cannot reject invoices — 403 raised."""
        from routers.work_orders import reject_work_order_invoice

        with pytest.raises(HTTPException) as exc:
            await reject_work_order_invoice("inv-001",
                                            reason="Some valid reason here",
                                            current_user=_owner_user(), building_id="13195")

        assert exc.value.status_code == 403

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_reject_sets_rejection_fields(self, mock_db, mock_audit, mock_log):
        """rejected_by, rejected_at, and rejection_reason are set on the invoice."""
        from routers.work_orders import reject_work_order_invoice

        inv = _invoice_doc()
        reason_text = "Amount does not match the approved quote"

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_order_invoices.update_one = AsyncMock()

        user = _chairman_user()
        await reject_work_order_invoice(inv["id"], reason=reason_text, current_user=user, building_id="13195")

        update_args = mock_db.work_order_invoices.update_one.call_args[0]
        set_fields = update_args[1]["$set"]
        assert set_fields["approval_status"] == "rejected"
        assert set_fields["rejected_by"] == user["id"]
        assert "rejected_at" in set_fields
        assert set_fields["rejection_reason"] == reason_text.strip()

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_strata_manager_can_reject(self, mock_db, mock_audit, mock_log):
        """strata_manager role is allowed to reject invoices."""
        from routers.work_orders import reject_work_order_invoice

        inv = _invoice_doc()

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_order_invoices.update_one = AsyncMock()

        result = await reject_work_order_invoice(inv["id"],
                                                 reason="Work not completed to specification",
                                                 current_user=_strata_manager_user(), building_id="13195")

        assert result == {"message": "Invoice rejected successfully"}

    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_reject_creates_audit_log(self, mock_db, mock_audit, mock_log):
        """create_audit_log is called when an invoice is rejected."""
        from routers.work_orders import reject_work_order_invoice

        inv = _invoice_doc()

        mock_db.work_order_invoices.find_one = AsyncMock(return_value=inv)
        mock_db.work_order_invoices.update_one = AsyncMock()

        await reject_work_order_invoice(inv["id"],
                                        reason="Invoice numbers do not match",
                                        current_user=_chairman_user(), building_id="13195")

        mock_audit.assert_called_once()
        args = mock_audit.call_args[0]
        assert args[0] == "invoice_rejected"
        assert args[1] == "work_order"
        assert args[6] == "13195"


# ===========================================================================
# CLASS 6: TestApprovalWorkflowModes
# ===========================================================================

class TestApprovalWorkflowModes:
    """Approval completion rules for SINGLE, DUAL, and MAJORITY modes."""

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_dual_approval_requires_two(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """DUAL_APPROVAL mode: first approval alone does NOT approve the WO."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _chairman_user()

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=4000.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_dual_approval()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        # Only one approver so far
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            [_approval_doc(wo_id, "user-chair-001", "approve", "CHAIRMAN")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        # update_one should NOT have been called with status=approved
        update_calls = mock_db.work_orders.update_one.call_args_list
        approved_call = next(
            (c for c in update_calls if c[0][1].get("$set", {}).get("status") == "approved"),
            None,
        )
        assert approved_call is None

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_majority_requires_ceil_of_half(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """MAJORITY mode with 3 EC members: 2 approvals (ceil(3/2)=2) should approve."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _ec_user("ec-2")

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=8000.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_majority()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        # 2 approvals, 3 EC members → (3//2)+1 = 2 → meets threshold
        mock_db.work_order_approvals.find.return_value = _mock_cursor([
            _approval_doc(wo_id, "ec-1", "approve"),
            _approval_doc(wo_id, "ec-2", "approve"),
        ])
        mock_db.users.count_documents = AsyncMock(return_value=3)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        update_calls = mock_db.work_orders.update_one.call_args_list
        approved_call = next(
            (c for c in update_calls if c[0][1].get("$set", {}).get("status") == "approved"),
            None,
        )
        assert approved_call is not None

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_majority_not_met_stays_pending(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """MAJORITY mode with 5 EC members: 2 approvals is not enough (need 3)."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _ec_user("ec-2")

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=8000.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_majority()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        # Only 2 approvals, 5 EC members → need (5//2)+1=3 → not met
        mock_db.work_order_approvals.find.return_value = _mock_cursor([
            _approval_doc(wo_id, "ec-1", "approve"),
            _approval_doc(wo_id, "ec-2", "approve"),
        ])
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        update_calls = mock_db.work_orders.update_one.call_args_list
        approved_call = next(
            (c for c in update_calls if c[0][1].get("$set", {}).get("status") == "approved"),
            None,
        )
        assert approved_call is None

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_dual_approval_two_unique_approvers_approves(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """DUAL_APPROVAL: two distinct approvers with qualifying roles → approved."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _ec_user("ec-treasurer", "TREASURER")

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=4000.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_dual_approval()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        # Two distinct approvers — both with qualifying roles
        mock_db.work_order_approvals.find.return_value = _mock_cursor([
            _approval_doc(wo_id, "user-chair-001", "approve", "CHAIRMAN"),
            _approval_doc(wo_id, "ec-treasurer", "approve", "TREASURER"),
        ])
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        update_calls = mock_db.work_orders.update_one.call_args_list
        approved_call = next(
            (c for c in update_calls if c[0][1].get("$set", {}).get("status") == "approved"),
            None,
        )
        assert approved_call is not None

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_no_rules_fallback_chairman_approves(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """Fallback logic (no thresholds): chairman approval is sufficient."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _chairman_user()

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
        )

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=500.0)

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(return_value=None)  # No settings
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            [_approval_doc(wo_id, "user-chair-001", "approve", "CHAIRMAN")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        update_calls = mock_db.work_orders.update_one.call_args_list
        approved_call = next(
            (c for c in update_calls if c[0][1].get("$set", {}).get("status") == "approved"),
            None,
        )
        assert approved_call is not None

    @patch("routers.work_orders._notify_ec_pending_approval", new_callable=AsyncMock)
    @patch("routers.work_orders.log_activity", new_callable=AsyncMock)
    @patch("routers.work_orders.create_audit_log", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_large_work_order_creates_committee_resolution(
            self, mock_db, mock_audit, mock_log, mock_notify
    ):
        """Work orders > $5000 generate a CommitteeResolution record on approval."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        user = _chairman_user()

        approval_data = WorkOrderApprovalCreate(
            work_order_id=wo_id,
            decision="approve",
        )

        # Amount > 5000 triggers resolution creation
        wo = _work_order_doc(wo_id=wo_id, estimated_cost=7500.0, vendor_name="Ace Plumbing")

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(
            return_value=_site_settings_single_approval()
        )
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            [_approval_doc(wo_id, "user-chair-001", "approve", "CHAIRMAN")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=user)

        mock_db.committee_resolutions.insert_one.assert_called_once()
        resolution_doc = mock_db.committee_resolutions.insert_one.call_args[0][0]
        assert resolution_doc["work_order_id"] == wo_id
        assert resolution_doc["approved_amount"] == 7500.0


# ===========================================================================
# CLASS N: TestECPositionSubRoles
# ===========================================================================

class TestECPositionSubRoles:
    """
    Tests for Treasurer, Secretary, and Member EC sub-roles.

    Approval permission:  CHAIRMAN + TREASURER only (DUAL_APPROVAL mode)
    Notification scope:   CHAIRMAN + TREASURER + SECRETARY
    """

    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_treasurer_can_approve_invoice_dual_mode(self, mock_db):
        """Treasurer counts as a valid approver in DUAL_APPROVAL mode."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        treasurer = _ec_user(user_id="treasurer-1", ec_position="TREASURER")
        approval_data = WorkOrderApprovalCreate(work_order_id=wo_id, decision="approve")

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=8000.0, status="pending_approval")

        # First approval was already given by the chairman
        existing_approvals = [_approval_doc(wo_id, "user-chair-001", "approve", "CHAIRMAN")]

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(return_value=_site_settings_dual_approval())
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            existing_approvals + [_approval_doc(wo_id, "treasurer-1", "approve", "TREASURER")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        # Treasurer submitting the second approval — should not get 403
        result = await submit_approval(wo_id, approval_data, current_user=treasurer)

        assert result.ec_position == "TREASURER"
        # The work order should be approved (2 distinct approvers in required_roles)
        update_call = mock_db.work_orders.update_one.call_args
        assert update_call[0][1]["$set"]["approval_status"] == "approved"

    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_secretary_cannot_approve_invoice(self, mock_db):
        """Secretary role is notification-only — cannot approve in SINGLE_APPROVAL mode."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate
        from fastapi import HTTPException

        wo_id = str(uuid.uuid4())
        secretary = {
            "id": "sec-1",
            "full_name": "Secretary User",
            "role": "ec_member",
            "ec_position": "SECRETARY",
            "unit_number": "UA002",
        }
        approval_data = WorkOrderApprovalCreate(work_order_id=wo_id, decision="approve")

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=3000.0, status="pending_approval")

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(return_value=_site_settings_single_approval())
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            [_approval_doc(wo_id, "sec-1", "approve", "SECRETARY")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        # Secretary CAN submit an approval record (role=ec_member passes the role check),
        # but their ec_position is NOT in ["CHAIRMAN", "TREASURER"] so the completion
        # check should NOT mark the WO as approved.
        await submit_approval(wo_id, approval_data, current_user=secretary)

        # update_one may not be called at all (is_approved=False), or if called,
        # the approval_status must NOT be "approved"
        if mock_db.work_orders.update_one.called:
            update_arg = mock_db.work_orders.update_one.call_args[0][1]
            status = update_arg.get("$set", {}).get("approval_status", "")
            assert status != "approved", "Secretary should not be able to approve as sole approver"

    @patch("routers.work_orders.create_notifications_batch", new_callable=AsyncMock)
    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_secretary_included_in_notification_list(self, mock_db, mock_notify_batch):
        """Secretary EC members are notified (CC'd) when a WO requires approval.

        Verifies both the Mongo query shape (ec_position filter is in the
        $or branch) and that the resulting recipients become notifications.
        """
        from routers.work_orders import _notify_ec_pending_approval

        wo_id = str(uuid.uuid4())
        wo_doc = _work_order_doc(wo_id=wo_id, title="Roof Repair")

        # The production query now filters ec_position server-side, so the
        # mock returns only the officeholders that the real query would
        # match (chairman/treasurer/secretary — NOT the plain MEMBER).
        notifiable_recipients = [
            {**_chairman_user(), "id": "chair-1"},
            {**_ec_user("treasurer-1", "TREASURER")},
            {**_ec_user("secretary-1", "SECRETARY")},
        ]

        mock_db.work_orders.find_one = AsyncMock(return_value=wo_doc)
        mock_db.users.find.return_value = _mock_cursor(notifiable_recipients)

        mock_db.memberships.find.return_value.to_list = AsyncMock(return_value=[{"user_id": "ec-sec-001"}])
        await _notify_ec_pending_approval(wo_id, 5000.0, building_id="13195")

        # Verify the Mongo query pushed the ec_position filter server-side
        # so plain MEMBER users are never read in the first place.
        find_call_args = mock_db.users.find.call_args[0][0]
        ec_branch = next(
            (b for b in find_call_args["$or"] if b.get("role") == "ec_member"),
            None,
        )
        assert ec_branch is not None, "Expected an ec_member branch in $or query"
        assert ec_branch.get("ec_position") == {
            "$in": ["CHAIRMAN", "TREASURER", "SECRETARY"]
        }, "ec_position filter must be pushed into the Mongo query"

        # Notifications should go to: chairman, treasurer, secretary
        notif_list = mock_notify_batch.call_args[0][0]
        notified_ids = {n["user_id"] for n in notif_list}
        assert "chair-1" in notified_ids, "Chairman should be notified"
        assert "treasurer-1" in notified_ids, "Treasurer should be notified"
        assert "secretary-1" in notified_ids, "Secretary should be notified"

    @patch("routers.work_orders.db")
    @pytest.mark.asyncio
    async def test_plain_ec_member_cannot_single_approve(self, mock_db):
        """EC member with position=MEMBER cannot complete a SINGLE_APPROVAL decision alone."""
        from routers.work_orders import submit_approval
        from models.work_order import WorkOrderApprovalCreate

        wo_id = str(uuid.uuid4())
        member = _ec_user("member-1", "MEMBER")
        approval_data = WorkOrderApprovalCreate(work_order_id=wo_id, decision="approve")

        wo = _work_order_doc(wo_id=wo_id, estimated_cost=2000.0, status="pending_approval")

        mock_db.work_orders.find_one = AsyncMock(return_value=wo)
        mock_db.site_settings.find_one = AsyncMock(return_value=_site_settings_single_approval())
        mock_db.work_order_approvals.insert_one = AsyncMock()
        mock_db.work_order_approvals.find.return_value = _mock_cursor(
            [_approval_doc(wo_id, "member-1", "approve", "MEMBER")]
        )
        mock_db.users.count_documents = AsyncMock(return_value=5)
        mock_db.work_orders.update_one = AsyncMock()
        mock_db.committee_resolutions.insert_one = AsyncMock()

        await submit_approval(wo_id, approval_data, current_user=member)

        # WO should NOT be approved — MEMBER is not in ["CHAIRMAN", "TREASURER"]
        if mock_db.work_orders.update_one.called:
            update_arg = mock_db.work_orders.update_one.call_args[0][1]
            status = update_arg.get("$set", {}).get("approval_status", "")
            assert status != "approved", "Plain EC member should not complete single approval"
