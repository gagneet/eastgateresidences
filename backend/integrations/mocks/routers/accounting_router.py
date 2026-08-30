"""
backend/integrations/mocks/routers/accounting_router.py — Mock supplier invoice generator.

# @featuretrace:financial_integration_v2 — mock accounting source HTTP endpoint.
# Layer: router
# Data flow: admin POST → MockAccountingSource.create_mock_bill → AP queue (building-scoped).
# Related: mock_accounting_source.py, ap_approval.py (Phase 4).

Mounted at /api/integrations/mock/accounting/ when financial_integration_layer_v2
is enabled. Lets testers seed realistic invoices for end-to-end AP pipeline testing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from integrations.mocks.mock_accounting_source import MockAccountingSource
from utils.auth import get_current_building, get_current_user
from utils.permissions import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/mock/accounting", tags=["Mock Accounting"])

_source = MockAccountingSource()


class MockInvoiceRequest(BaseModel):
    vendor_name: str = Field(..., min_length=2, max_length=100)
    vendor_abn: Optional[str] = Field(None, pattern=r"^\d{11}$")
    invoice_number: str = Field(..., min_length=1, max_length=50)
    total_cents: int = Field(..., gt=0)
    gst_cents: int = Field(default=0, ge=0)
    days_until_due: int = Field(default=30, ge=1, le=365)


class MockInvoiceResponse(BaseModel):
    bill_id: str
    message: str


@router.post("/generate-invoice", response_model=MockInvoiceResponse)
async def generate_mock_invoice(
        payload: MockInvoiceRequest,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
) -> MockInvoiceResponse:
    """Generate a mock supplier invoice for AP pipeline testing.

    The created invoice appears in the accounting provider's bill list and
    flows into the AP approval queue when the pipeline is triggered.
    All records created here carry is_test_data=True.
    """
    from utils.auth import effective_role as _effective_role
    _role = _effective_role(current_user)
    if _role not in {"super_admin", "strata_admin", "ec_member", "strata_manager"}:
        raise HTTPException(status_code=403, detail="Strata manager or EC member role required")

    now = datetime.now(timezone.utc)
    due = now + timedelta(days=payload.days_until_due)

    from request_context import set_ctx_building_id
    set_ctx_building_id(building_id)

    bill_id = await _source.create_mock_bill(
        building_id=building_id,
        vendor_name=payload.vendor_name,
        vendor_abn=payload.vendor_abn,
        invoice_number=payload.invoice_number,
        total_cents=payload.total_cents,
        gst_cents=payload.gst_cents,
        issue_date=now,
        due_date=due,
        is_test_data=True,
    )

    return MockInvoiceResponse(
        bill_id=bill_id,
        message=f"Mock invoice {payload.invoice_number} created (is_test_data=True)",
    )


@router.get("/bills")
async def list_mock_bills(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(require_feature("financial_integration_layer_v2")),
) -> dict:
    """List all mock bills for this building (most recent first)."""
    from utils.auth import effective_role as _effective_role
    _role = _effective_role(current_user)
    if _role not in {"super_admin", "strata_admin", "ec_member", "strata_manager"}:
        raise HTTPException(status_code=403, detail="Strata manager or EC member role required")

    from request_context import set_ctx_building_id
    set_ctx_building_id(building_id)

    epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
    bills = await _source.list_bills(since=epoch)
    return {
        "bills": [
            {
                "provider_bill_id": b.provider_bill_id,
                "vendor_name": b.vendor_name,
                "vendor_abn": b.vendor_abn,
                "invoice_number": b.invoice_number,
                "total_cents": b.total_cents,
                "gst_cents": b.gst_cents,
                "issue_date": b.issue_date.isoformat() if b.issue_date else None,
            }
            for b in bills
        ],
        "count": len(bills),
    }
