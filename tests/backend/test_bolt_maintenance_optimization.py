from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import HTTPException, BackgroundTasks

from routers.maintenance import approve_invoice, reject_invoice, _notify_invoice_update


@pytest.fixture
def mock_db():
    # Patch database.db directly because of local imports in maintenance.py
    with patch("database.db") as mock:
        # Also patch routers.maintenance.db just in case
        with patch("routers.maintenance.db", mock):
            yield mock


@pytest.fixture
def current_user():
    return {
        "id": "user123",
        "full_name": "Admin User",
        "role": "super_admin",
        "email": "admin@example.com"
    }


@pytest.fixture
def background_tasks():
    return MagicMock(spec=BackgroundTasks)


@pytest.mark.asyncio
async def test_approve_invoice_optimization(mock_db, current_user, background_tasks):
    inv_id = "inv123"

    # Mock aggregation to return invoice + related context
    mock_invoice_data = {
        "id": inv_id,
        "purchase_order_id": "po123",
        "invoice_number": "INV-001",
        "total_amount": 100.0,
        "contractor_name": "Contractor A",
        "po": {"id": "po123", "maintenance_request_id": "req123"},
        "maintenance_req": {"id": "req123", "submitted_by": "user456"},
        "creator": {"id": "user456", "email": "res@example.com"}
    }

    # Mock aggregation result
    mock_agg = MagicMock()
    mock_agg.to_list = AsyncMock(return_value=[mock_invoice_data])
    mock_db.invoices.aggregate.return_value = mock_agg

    # Mock update_one
    mock_db.invoices.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    # Mock permissions
    with patch("routers.maintenance.get_user_permissions") as mock_perms:
        mock_perms.return_value = MagicMock(can_manage_finances=True)

        response = await approve_invoice(inv_id, background_tasks, current_user)

        assert response == {"message": "Invoice approved successfully"}

        # Verify aggregation trip
        mock_db.invoices.aggregate.assert_called_once()

        # Verify atomic update
        mock_db.invoices.update_one.assert_called_once()

        # Verify background task was added with FULL context (snapshot)
        background_tasks.add_task.assert_called_once()
        args, kwargs = background_tasks.add_task.call_args
        assert args[0] == _notify_invoice_update
        assert args[1]["id"] == inv_id
        assert args[2] == "approved"
        # Check pre-fetched context
        assert args[4]["id"] == "po123"
        assert args[5]["id"] == "req123"
        assert args[6]["id"] == "user456"


@pytest.mark.asyncio
async def test_reject_invoice_optimization(mock_db, current_user, background_tasks):
    inv_id = "inv123"
    reason = "Incorrect amount"

    # Mock aggregation
    mock_invoice_data = {
        "id": inv_id,
        "purchase_order_id": "po123",
        "invoice_number": "INV-001",
        "total_amount": 100.0,
        "contractor_name": "Contractor A",
        "po": {"id": "po123", "maintenance_request_id": "req123"},
        "maintenance_req": {"id": "req123", "submitted_by": "user456"},
        "creator": {"id": "user456", "email": "res@example.com"}
    }
    mock_agg = MagicMock()
    mock_agg.to_list = AsyncMock(return_value=[mock_invoice_data])
    mock_db.invoices.aggregate.return_value = mock_agg

    # Mock update_one
    mock_db.invoices.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    # Mock permissions
    with patch("routers.maintenance.get_user_permissions") as mock_perms:
        mock_perms.return_value = MagicMock(can_manage_finances=True)

        response = await reject_invoice(inv_id, reason, background_tasks, current_user)

        assert response == {"message": "Invoice rejected successfully"}

        # Verify atomic update
        mock_db.invoices.update_one.assert_called_once()

        # Verify background task was added with pre-fetched context
        background_tasks.add_task.assert_called_once()
        args, kwargs = background_tasks.add_task.call_args
        assert args[1]["id"] == inv_id
        assert args[2] == "rejected"
        assert args[6]["id"] == "user456"
        assert args[7] == reason


@pytest.mark.asyncio
async def test_notify_invoice_update_no_db_lookups():
    # Test that _notify_invoice_update uses pre-fetched context and performs NO lookups
    invoice = {"invoice_number": "INV-001", "contractor_name": "C1", "total_amount": 100.0}
    po = {"id": "po123"}
    maintenance_req = {"id": "req123", "submitted_by": "user456", "title": "Job 1"}
    creator = {"full_name": "Resident", "email": "res@example.com"}

    # Patch database to ensure it's NOT used
    with patch("database.db") as mock_db, \
            patch("routers.maintenance.create_user_notification") as mock_notif, \
            patch("routers.maintenance.send_email_async") as mock_email:
        await _notify_invoice_update(invoice, "approved", "Admin", po, maintenance_req, creator)

        # Verify NO lookups were performed (stale read window closed)
        mock_db.purchase_orders.find_one.assert_not_called()
        mock_db.maintenance_requests.find_one.assert_not_called()
        mock_db.users.find_one.assert_not_called()

        # Verify services triggered with correct pre-fetched data
        mock_notif.assert_called_once()
        mock_email.assert_called_once()
        args, kwargs = mock_email.call_args
        assert kwargs["to_email"] == "res@example.com"
        assert "Approved" in kwargs["subject"]
