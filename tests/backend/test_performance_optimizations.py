"""
Performance optimization tests for work orders — batch queries and timeline building.

Uses routers.work_orders (NOT backend.routers.work_orders) because
conftest.py adds backend/ to sys.path directly.
"""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from models.user import UserRole
from routers.work_orders import get_pending_work_order_invoices, get_work_order_timeline


@pytest.mark.asyncio
async def test_get_pending_work_order_invoices_optimized():
    mock_user = {"id": "admin-1", "full_name": "Admin User", "role": UserRole.SUPER_ADMIN}

    mock_invoices = [
        {"id": "inv-1", "work_order_id": "wo-1", "vendor_id": "v-1", "invoice_number": "INV-001", "amount": 1000.0,
         "approval_status": "submitted"},
        {"id": "inv-2", "work_order_id": "wo-2", "vendor_id": "v-2", "invoice_number": "INV-002", "amount": 2500.0,
         "approval_status": "submitted"}
    ]

    mock_quotes = [
        {"work_order_id": "wo-1", "amount": 1000.0, "selected": True},
        {"work_order_id": "wo-2", "amount": 2000.0, "selected": True}
    ]

    with patch("routers.work_orders.db") as mock_db:
        # Mocking db.work_order_invoices.find().sort().to_list()
        mock_find_invoices = MagicMock()
        mock_sort_invoices = MagicMock()
        mock_find_invoices.sort.return_value = mock_sort_invoices
        mock_sort_invoices.to_list = AsyncMock(return_value=mock_invoices)
        mock_db.work_order_invoices.find.return_value = mock_find_invoices

        # Mocking db.work_order_quotes.find().to_list()
        mock_find_quotes = MagicMock()
        mock_find_quotes.to_list = AsyncMock(return_value=mock_quotes)
        mock_db.work_order_quotes.find.return_value = mock_find_quotes

        response = await get_pending_work_order_invoices(current_user=mock_user)

        assert len(response) == 2
        # inv-1: 1000.0 == 1000.0 -> no warnings
        assert response[0].id == "inv-1"
        assert response[0].warnings == []

        # inv-2: 2500.0 > 2000.0 -> warning
        assert response[1].id == "inv-2"
        assert any("exceeds approved quote" in w for w in response[1].warnings)

        # Verify batch query was used
        mock_db.work_order_quotes.find.assert_called_once()
        args, kwargs = mock_db.work_order_quotes.find.call_args
        assert "work_order_id" in args[0]
        assert "$in" in args[0]["work_order_id"]
        assert sorted(args[0]["work_order_id"]["$in"]) == sorted(["wo-1", "wo-2"])


@pytest.mark.asyncio
async def test_get_work_order_timeline_optimized():
    wo_id = "wo-123"
    mock_user = {"id": "user-1", "full_name": "Test User", "role": UserRole.EC_MEMBER}

    mock_wo = {
        "id": wo_id,
        "title": "Repair Gate",
        "created_at": "2026-01-01T10:00:00Z",
        "created_by_name": "System Admin"
    }

    mock_quotes = [
        {"submitted_date": "2026-01-02T10:00:00Z", "vendor_name": "Vendor A", "amount": 500.0, "selected": True}
    ]

    mock_approvals = [
        {"timestamp": "2026-01-03T10:00:00Z", "decision": "approve", "approver_name": "Chairman", "role": "chairman"}
    ]

    mock_invoices = [
        {"created_at": "2026-01-04T10:00:00Z", "invoice_number": "INV-123", "amount": 500.0, "status": "paid",
         "paid_at": "2026-01-05T10:00:00Z"}
    ]

    with patch("routers.work_orders.db") as mock_db, \
            patch("routers.work_orders.get_user_permissions") as mock_perms:
        mock_perms.return_value = MagicMock(can_manage_meetings=True)

        mock_db.work_orders.find_one = AsyncMock(return_value=mock_wo)
        mock_db.work_order_quotes.find.return_value.to_list = AsyncMock(return_value=mock_quotes)
        mock_db.work_order_approvals.find.return_value.to_list = AsyncMock(return_value=mock_approvals)
        mock_db.work_order_invoices.find.return_value.to_list = AsyncMock(return_value=mock_invoices)

        response = await get_work_order_timeline(wo_id=wo_id, current_user=mock_user)

        # 1 WO created + 1 Quote submitted + 1 Quote selected + 1 Approval + 1 Invoice submitted + 1 Payment processed = 6 events
        assert len(response) == 6
        assert response[0]["type"] == "WORK_ORDER_CREATED"
        assert response[-1]["type"] == "PAYMENT_PROCESSED"

        # Verify asyncio.gather was likely used by checking if all mocks were called
        mock_db.work_orders.find_one.assert_called_once()
        mock_db.work_order_quotes.find.assert_called_once()
        mock_db.work_order_approvals.find.assert_called_once()
        mock_db.work_order_invoices.find.assert_called_once()
