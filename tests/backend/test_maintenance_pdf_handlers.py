#!/usr/bin/env python3
"""
Unit Tests: Maintenance router PDF handler functions
  GET /purchase-orders/{po_id}/pdf
  GET /invoices/{inv_id}/pdf

Tests that:
  - handlers fetch building_settings and pass them to the PDF generators
  - 403 returned when user lacks can_view_finances permission
  - 404 returned when PO or invoice not found
  - 500 returned when PDF generation unavailable
  - Multi-tenant: settings fetched for the correct building_id (not cross-contaminated)
  - handlers are building-scoped (building_id in every DB query)

No live backend required — all DB and service calls are mocked.

Run:
    cd /home/gagneet/strata-management
    backend/venv/bin/python3 -m pytest tests/backend/test_maintenance_pdf_handlers.py -v
"""

import io
import sys
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from fastapi import HTTPException

# ── Fixtures ──────────────────────────────────────────────────────────────────

EAST_GATE_BID = "13195"
SIERRA_BID = "16244"

EAST_GATE_SETTINGS = {
    "building_id": EAST_GATE_BID,
    "strata_address": "14 Hoolihan Street, Denman Prospect ACT 2914",
    "plan_number": "13195",
}
SIERRA_SETTINGS = {
    "building_id": SIERRA_BID,
    "strata_address": "2 Burrumarra Avenue, Gungahlin ACT 2912",
    "plan_number": "16244",
}

_PO = {
    "id": "po-001",
    "po_number": "PO-2026-001",
    "building_id": EAST_GATE_BID,
    "contractor_id": "c-001",
    "description": "Lobby lighting replacement",
    "amount": 1500.0,
    "total_amount": 1650.0,
    "created_at": "2026-04-01T10:00:00",
    "due_date": "2026-04-30",
    "status": "approved",
}

_CONTRACTOR = {
    "id": "c-001",
    "name": "Acme Electrical",
    "abn": "11 222 333 444",
    "phone": "02 6123 4567",
    "email": "works@acme.com",
}

_INVOICE = {
    "id": "inv-001",
    "invoice_number": "INV-2026-001",
    "purchase_order_id": "po-001",
    "contractor_id": "c-001",
    "building_id": EAST_GATE_BID,
    "amount": 1500.0,
    "gst_amount": 150.0,
    "total_amount": 1650.0,
    "status": "pending",
    "notes": "",
    "created_at": "2026-04-15T10:00:00",
}

_GST_CONFIG = {"levy_gst_rate": 0.10, "gst_registered": True}


def _make_user(can_view_finances=True, can_manage_finances=True):
    from utils.permissions import Permission
    perms = Permission(can_view_finances=can_view_finances, can_manage_finances=can_manage_finances)
    return {
        "id": "user-admin-001",
        "role": "strata_manager",
        "effective_role": "strata_manager",
        "building_id": EAST_GATE_BID,
    }, perms


# ── helpers for patching the router ──────────────────────────────────────────

def _patch_po_handler_deps(building_id, settings, po=None, contractor=None, gst=None, pdf_bytes=b"fake-pdf"):
    """Return a context that patches all external calls for the PO PDF handler."""
    from unittest.mock import patch as _p
    import contextlib

    @contextlib.contextmanager
    def ctx():
        po_doc = dict(po or _PO)
        po_doc["building_id"] = building_id

        mock_db = MagicMock()
        mock_db.purchase_orders.find_one = AsyncMock(return_value=po_doc)
        mock_db.contractors.find_one = AsyncMock(return_value=dict(contractor or _CONTRACTOR))

        with _p("routers.maintenance.db", mock_db), \
                _p("routers.maintenance.get_building_levy_gst_settings",
                   AsyncMock(return_value=dict(gst or _GST_CONFIG))), \
                _p("routers.maintenance.get_general_settings_or_default",
                   AsyncMock(return_value=dict(settings))), \
                _p("routers.maintenance.generate_purchase_order_pdf",
                   MagicMock(return_value=pdf_bytes)) as mock_gen_po:
            yield mock_db, mock_gen_po

    return ctx()


def _patch_invoice_handler_deps(building_id, settings, invoice=None, po=None,
                                contractor=None, pdf_bytes=b"fake-pdf"):
    from unittest.mock import patch as _p
    import contextlib

    @contextlib.contextmanager
    def ctx():
        inv_doc = dict(invoice or _INVOICE)
        inv_doc["building_id"] = building_id

        mock_db = MagicMock()
        mock_db.invoices.find_one = AsyncMock(return_value=inv_doc)
        mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(po or _PO))
        mock_db.contractors.find_one = AsyncMock(return_value=dict(contractor or _CONTRACTOR))

        with _p("routers.maintenance.db", mock_db), \
                _p("routers.maintenance.get_general_settings_or_default",
                   AsyncMock(return_value=dict(settings))), \
                _p("routers.maintenance.generate_invoice_pdf",
                   MagicMock(return_value=pdf_bytes)) as mock_gen_inv:
            yield mock_db, mock_gen_inv

    return ctx()


# ── PO PDF handler ────────────────────────────────────────────────────────────

class TestPurchaseOrderPdfHandler:

    @pytest.mark.asyncio
    async def test_handler_calls_get_general_settings(self):
        """Handler must call get_general_settings_or_default with the correct building_id."""
        from routers.maintenance import get_purchase_order_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_building_levy_gst_settings",
                      AsyncMock(return_value=_GST_CONFIG)) as _, \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)) as mock_settings, \
                patch("routers.maintenance.generate_purchase_order_pdf",
                      MagicMock(return_value=b"fake-pdf")):
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_purchase_order_pdf("po-001", user, EAST_GATE_BID)

            mock_settings.assert_awaited_once_with(EAST_GATE_BID)

    @pytest.mark.asyncio
    async def test_handler_passes_building_settings_to_pdf_generator(self):
        """The settings dict must be forwarded as building_settings kwarg."""
        from routers.maintenance import get_purchase_order_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_building_levy_gst_settings",
                      AsyncMock(return_value=_GST_CONFIG)), \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)), \
                patch("routers.maintenance.generate_purchase_order_pdf",
                      MagicMock(return_value=b"fake-pdf")) as mock_gen:
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_purchase_order_pdf("po-001", user, EAST_GATE_BID)

            call_kwargs = mock_gen.call_args.kwargs
            assert call_kwargs.get("building_settings") == EAST_GATE_SETTINGS

    @pytest.mark.asyncio
    async def test_handler_403_when_no_finance_permission(self):
        """Users without can_view_finances get 403."""
        from routers.maintenance import get_purchase_order_pdf
        user, perms = _make_user(can_view_finances=False)

        with patch("routers.maintenance.get_user_permissions", return_value=perms):
            with pytest.raises(HTTPException) as exc_info:
                await get_purchase_order_pdf("po-001", user, EAST_GATE_BID)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_handler_404_when_po_not_found(self):
        """Missing PO returns 404."""
        from routers.maintenance import get_purchase_order_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db:
            mock_db.purchase_orders.find_one = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await get_purchase_order_pdf("nonexistent", user, EAST_GATE_BID)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_handler_500_when_pdf_unavailable(self):
        """When generate_purchase_order_pdf returns None, handler returns 500."""
        from routers.maintenance import get_purchase_order_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_building_levy_gst_settings",
                      AsyncMock(return_value=_GST_CONFIG)), \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)), \
                patch("routers.maintenance.generate_purchase_order_pdf",
                      MagicMock(return_value=None)):
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            with pytest.raises(HTTPException) as exc_info:
                await get_purchase_order_pdf("po-001", user, EAST_GATE_BID)
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_handler_returns_streaming_response(self):
        """Successful call returns a StreamingResponse."""
        from routers.maintenance import get_purchase_order_pdf
        from starlette.responses import StreamingResponse
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_building_levy_gst_settings",
                      AsyncMock(return_value=_GST_CONFIG)), \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)), \
                patch("routers.maintenance.generate_purchase_order_pdf",
                      MagicMock(return_value=b"%PDF-fake")):
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            result = await get_purchase_order_pdf("po-001", user, EAST_GATE_BID)
            assert isinstance(result, StreamingResponse)
            assert result.media_type == "application/pdf"


class TestPurchaseOrderPdfHandlerMultiTenant:
    """Settings are fetched for the correct building — not cross-contaminated."""

    @pytest.mark.asyncio
    async def test_sierra_handler_fetches_sierra_settings(self):
        from routers.maintenance import get_purchase_order_pdf
        user, perms = _make_user()
        sierra_po = dict(_PO)
        sierra_po["building_id"] = SIERRA_BID

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_building_levy_gst_settings",
                      AsyncMock(return_value=_GST_CONFIG)), \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=SIERRA_SETTINGS)) as mock_settings, \
                patch("routers.maintenance.generate_purchase_order_pdf",
                      MagicMock(return_value=b"fake")) as mock_gen:
            mock_db.purchase_orders.find_one = AsyncMock(return_value=sierra_po)
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_purchase_order_pdf("po-001", user, SIERRA_BID)

            mock_settings.assert_awaited_once_with(SIERRA_BID)
            assert mock_gen.call_args.kwargs["building_settings"] == SIERRA_SETTINGS

    @pytest.mark.asyncio
    async def test_db_queries_include_building_id(self):
        """Every DB find_one call must scope by building_id."""
        from routers.maintenance import get_purchase_order_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_building_levy_gst_settings",
                      AsyncMock(return_value=_GST_CONFIG)), \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)), \
                patch("routers.maintenance.generate_purchase_order_pdf",
                      MagicMock(return_value=b"fake")):
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_purchase_order_pdf("po-001", user, EAST_GATE_BID)

            po_query = mock_db.purchase_orders.find_one.call_args[0][0]
            assert po_query.get("building_id") == EAST_GATE_BID


# ── Invoice PDF handler ───────────────────────────────────────────────────────

class TestInvoicePdfHandler:

    @pytest.mark.asyncio
    async def test_handler_calls_get_general_settings(self):
        from routers.maintenance import get_invoice_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)) as mock_settings, \
                patch("routers.maintenance.generate_invoice_pdf",
                      MagicMock(return_value=b"fake")):
            mock_db.invoices.find_one = AsyncMock(return_value=dict(_INVOICE))
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_invoice_pdf("inv-001", user, EAST_GATE_BID)

            mock_settings.assert_awaited_once_with(EAST_GATE_BID)

    @pytest.mark.asyncio
    async def test_handler_passes_building_settings_to_pdf_generator(self):
        from routers.maintenance import get_invoice_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)), \
                patch("routers.maintenance.generate_invoice_pdf",
                      MagicMock(return_value=b"fake")) as mock_gen:
            mock_db.invoices.find_one = AsyncMock(return_value=dict(_INVOICE))
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_invoice_pdf("inv-001", user, EAST_GATE_BID)

            assert mock_gen.call_args.kwargs.get("building_settings") == EAST_GATE_SETTINGS

    @pytest.mark.asyncio
    async def test_handler_403_when_no_finance_permission(self):
        from routers.maintenance import get_invoice_pdf
        user, perms = _make_user(can_view_finances=False)

        with patch("routers.maintenance.get_user_permissions", return_value=perms):
            with pytest.raises(HTTPException) as exc_info:
                await get_invoice_pdf("inv-001", user, EAST_GATE_BID)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_handler_404_when_invoice_not_found(self):
        from routers.maintenance import get_invoice_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db:
            mock_db.invoices.find_one = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await get_invoice_pdf("nonexistent", user, EAST_GATE_BID)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_handler_500_when_pdf_unavailable(self):
        from routers.maintenance import get_invoice_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)), \
                patch("routers.maintenance.generate_invoice_pdf",
                      MagicMock(return_value=None)):
            mock_db.invoices.find_one = AsyncMock(return_value=dict(_INVOICE))
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            with pytest.raises(HTTPException) as exc_info:
                await get_invoice_pdf("inv-001", user, EAST_GATE_BID)
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_handler_returns_streaming_response(self):
        from routers.maintenance import get_invoice_pdf
        from starlette.responses import StreamingResponse
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)), \
                patch("routers.maintenance.generate_invoice_pdf",
                      MagicMock(return_value=b"%PDF-fake")):
            mock_db.invoices.find_one = AsyncMock(return_value=dict(_INVOICE))
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            result = await get_invoice_pdf("inv-001", user, EAST_GATE_BID)
            assert isinstance(result, StreamingResponse)
            assert result.media_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_invoice_db_query_scoped_by_building_id(self):
        from routers.maintenance import get_invoice_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=EAST_GATE_SETTINGS)), \
                patch("routers.maintenance.generate_invoice_pdf",
                      MagicMock(return_value=b"fake")):
            mock_db.invoices.find_one = AsyncMock(return_value=dict(_INVOICE))
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_invoice_pdf("inv-001", user, EAST_GATE_BID)

            inv_query = mock_db.invoices.find_one.call_args[0][0]
            assert inv_query.get("building_id") == EAST_GATE_BID


class TestInvoicePdfHandlerMultiTenant:

    @pytest.mark.asyncio
    async def test_sierra_handler_fetches_sierra_settings(self):
        from routers.maintenance import get_invoice_pdf
        user, perms = _make_user()
        sierra_inv = dict(_INVOICE)
        sierra_inv["building_id"] = SIERRA_BID

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=SIERRA_SETTINGS)) as mock_settings, \
                patch("routers.maintenance.generate_invoice_pdf",
                      MagicMock(return_value=b"fake")) as mock_gen:
            mock_db.invoices.find_one = AsyncMock(return_value=sierra_inv)
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_invoice_pdf("inv-001", user, SIERRA_BID)

            mock_settings.assert_awaited_once_with(SIERRA_BID)
            assert mock_gen.call_args.kwargs["building_settings"] == SIERRA_SETTINGS

    @pytest.mark.asyncio
    async def test_east_gate_settings_not_used_for_sierra(self):
        """Sierra call must never receive East Gate settings."""
        from routers.maintenance import get_invoice_pdf
        user, perms = _make_user()

        with patch("routers.maintenance.get_user_permissions", return_value=perms), \
                patch("routers.maintenance.db") as mock_db, \
                patch("routers.maintenance.get_general_settings_or_default",
                      AsyncMock(return_value=SIERRA_SETTINGS)), \
                patch("routers.maintenance.generate_invoice_pdf",
                      MagicMock(return_value=b"fake")) as mock_gen:
            mock_db.invoices.find_one = AsyncMock(return_value=dict(_INVOICE))
            mock_db.purchase_orders.find_one = AsyncMock(return_value=dict(_PO))
            mock_db.contractors.find_one = AsyncMock(return_value=dict(_CONTRACTOR))

            await get_invoice_pdf("inv-001", user, SIERRA_BID)

            passed_settings = mock_gen.call_args.kwargs["building_settings"]
            assert passed_settings.get("strata_address") != EAST_GATE_SETTINGS["strata_address"]
