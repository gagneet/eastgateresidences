# @featuretrace:invoices — FastAPI router handling all invoice/quote OCR endpoints.
# Layer: router
# Data flow: InvoicesPage.jsx + InvoiceUploadModal.jsx → POST /invoices/upload-and-parse
#            → invoice_ocr_service → invoice_documents (building-scoped).
# Related: frontend/src/pages/dashboard/InvoicesPage.jsx
#           frontend/src/components/invoices/InvoiceUploadModal.jsx
#           backend/services/invoice_ocr_service.py
#           backend/repositories/financial_repository.py (insert_invoice_record, etc.)

"""
Invoice OCR Router — upload, parse, review, confirm.

Workflow:
  POST /invoices/upload-and-parse  → OCR → pending_review record
  GET  /invoices                   → list for building
  GET  /invoices/{id}              → single record (no file_data)
  POST /invoices/{id}/confirm      → write financial_transaction + receipt
  DELETE /invoices/{id}            → delete pending only (confirmed are retained)
  GET  /invoices/{id}/receipt      → download receipt PDF

OCR provider settings:
  GET  /settings/ocr-provider      → per-building selection + monthly scan count
  PATCH /settings/ocr-provider     → update per-building OCR provider
  GET  /admin/settings/global-ocr  → app-level default (super_admin only)
  PATCH /admin/settings/global-ocr → update app-level default (super_admin only)

Permission gate : can_manage_finances
Feature gate    : invoice_ocr
"""
import asyncio
import base64
import logging
import uuid as _uuid_mod
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from database import db
from models.invoice_models import (
    InvoiceConfirmRequest,
    InvoiceDocumentResponse,
    InvoiceListResponse,
)
from repositories import financial_repository as repo
from repositories.financial_repository import get_invoice_receipt_data as _get_receipt_data
from services.invoice_ocr_service import parse_invoice, VALID_OCR_PROVIDERS
from services.ocr.unlimited_ocr_client import UNLIMITED_OCR, get_unlimited_ocr_config
from services.cutover_config_service import (
    is_cutover_feature_enabled,
    FINANCIAL_PG_WRITES_ENABLED,
)
from utils.auth import get_current_building
from utils.permissions import require_feature
from utils.file_scan import scan_upload
from utils.pdf_generator import generate_invoice_receipt_pdf

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
_VALID_DOC_TYPES = {"invoice", "quote"}

_APP_DEFAULTS_ID = "app_defaults"


# ── Pydantic models for OCR settings ─────────────────────────────────────────

class OCRProviderUpdate(BaseModel):
    ocr_provider: str


class GlobalOCRUpdate(BaseModel):
    ocr_default_provider: str


class OCRProviderSettingResponse(BaseModel):
    building_id: str
    ocr_provider: str


class GlobalOCRSettingResponse(BaseModel):
    ocr_default_provider: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/routers/invoices.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _finance_roles(user: dict) -> bool:
    """Generated function header.

    Function: _finance_roles
    Path: backend/routers/invoices.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role", "guest")
    return role in {"super_admin", "strata_admin", "strata_manager", "ec_member"}


def _require_valid_id(invoice_id: str) -> None:
    """Generated function header.

    Function: _require_valid_id
    Path: backend/routers/invoices.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        _uuid_mod.UUID(invoice_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid invoice ID format")


async def _resolve_ocr_provider(building_id: str) -> str:
    """Resolve OCR provider: per-building pref → global default → 'auto'."""
    # Performance Optimization⚡: Parallelize building and global settings lookups.
    # This reduces sequential database round-trips from O(2) to O(1) concurrent requests.
    try:
        building_task = db.settings.find_one(
            {"building_id": building_id},
            {"integration_provider_preference": 1},
        )
        global_task = db.site_settings.find_one(
            {"id": _APP_DEFAULTS_ID},
            {"ocr_default_provider": 1},
        )
        building_settings, app_defaults = await asyncio.gather(building_task, global_task)

        if building_settings:
            pref = building_settings.get("integration_provider_preference", {})
            ocr_pref = pref.get("ocr", "")
            if ocr_pref and ocr_pref in VALID_OCR_PROVIDERS:
                return ocr_pref

        if app_defaults:
            global_pref = app_defaults.get("ocr_default_provider", "")
            if global_pref and global_pref in VALID_OCR_PROVIDERS:
                return global_pref
    except Exception as exc:
        logger.warning("Failed to resolve OCR provider preference: %s", exc)

    return "auto"


async def _get_monthly_scan_avg(building_id: str) -> int:
    """Return average monthly invoice uploads for this building over the last 3 months.

    Falls back to 5 (research-based default for a strata building) if no history.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        count = await db.invoice_documents.count_documents({
            "building_id": building_id,
            "created_at": {"$gte": cutoff},
            "is_test_data": {"$ne": True},
        })
        return max(1, round(count / 3))
    except Exception:
        return 5


# ── Upload & Parse ────────────────────────────────────────────────────────────

@router.post("/invoices/upload-and-parse", response_model=InvoiceDocumentResponse)
async def upload_and_parse(
        file: UploadFile = File(...),
        document_type: str = Form("invoice"),
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Upload an invoice or quote PDF/image, run OCR, return parsed preview."""
    if not _finance_roles(current_user):
        raise HTTPException(status_code=403, detail="Finance management access required")

    if document_type not in _VALID_DOC_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"Invalid document_type '{document_type}'. Must be 'invoice' or 'quote'")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in _ALLOWED_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported file type: {content_type}. Accepted: PDF, JPEG, PNG, WebP")

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 20 MB limit")

    await scan_upload(file_bytes, context="document", filename=file.filename or "invoice")

    provider = await _resolve_ocr_provider(building_id)
    parsed = await parse_invoice(file_bytes, content_type, provider=provider)

    doc = await repo.insert_invoice_record({
        "building_id": building_id,
        "status": "pending_review",
        "document_type": document_type,
        "file_name": file.filename or "invoice",
        "file_data": base64.b64encode(file_bytes).decode("utf-8"),
        "file_type": content_type,
        "file_size_bytes": len(file_bytes),
        "ocr_source": parsed.get("ocr_source"),
        "ocr_confidence": float(parsed.get("confidence", 0)),
        "parsed_data": parsed,
        "confirmed_data": None,
        "transaction_id": None,
        "receipt_generated": False,
        "created_by": current_user["id"],
        "is_test_data": False,
    })

    doc.pop("file_data", None)
    return InvoiceDocumentResponse(**doc)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/invoices", response_model=InvoiceListResponse)
async def list_invoices(
        status: str = None,
        document_type: str = None,
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_invoices
    Path: backend/routers/invoices.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _finance_roles(current_user):
        raise HTTPException(status_code=403, detail="Finance management access required")

    invoices = await repo.list_invoices(
        building_id=building_id,
        status=status,
        document_type=document_type,
    )
    return InvoiceListResponse(
        invoices=[InvoiceDocumentResponse(**inv) for inv in invoices],
        total=len(invoices),
    )


# ── Get single ────────────────────────────────────────────────────────────────

@router.get("/invoices/{invoice_id}", response_model=InvoiceDocumentResponse)
async def get_invoice(
        invoice_id: str,
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_invoice
    Path: backend/routers/invoices.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _finance_roles(current_user):
        raise HTTPException(status_code=403, detail="Finance management access required")
    _require_valid_id(invoice_id)

    doc = await repo.get_invoice(invoice_id, building_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")
    doc.pop("file_data", None)
    return InvoiceDocumentResponse(**doc)


# ── Confirm ───────────────────────────────────────────────────────────────────

@router.post("/invoices/{invoice_id}/confirm", response_model=InvoiceDocumentResponse)
async def confirm_invoice(
        invoice_id: str,
        body: InvoiceConfirmRequest,
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Confirm (possibly edited) parsed data, create transaction, generate receipt.

    Atomicity fix (ISSUE-01): the transaction insert and invoice status update are
    logically coupled.  If the invoice update fails after the transaction is
    inserted we roll back the transaction document so no orphan exists.

    Postgres write path: when ``financial_pg_writes_enabled`` is ON for this
    building the expense is written to ``finance.journal_entries`` via
    ``FinancialCoreAdapter.record_expense_transaction()``.  The MongoDB
    ``financial_transactions`` write is suppressed on the Postgres path because
    the outbox relay will mirror it asynchronously.
    """
    if not _finance_roles(current_user):
        raise HTTPException(status_code=403, detail="Finance management access required")
    _require_valid_id(invoice_id)

    doc = await repo.get_invoice(invoice_id, building_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if doc.get("status") == "confirmed":
        raise HTTPException(status_code=409, detail="Invoice already confirmed")

    if body.total <= 0:
        raise HTTPException(status_code=400, detail="Total amount must be greater than zero")
    if not body.vendor_name.strip():
        raise HTTPException(status_code=400, detail="Vendor name is required")

    try:
        fy = str(datetime.fromisoformat(body.invoice_date).year)
    except (ValueError, TypeError):
        fy = str(datetime.now(timezone.utc).year)

    # Check whether the Postgres write path is active for this building.
    _pg_writes = False
    try:
        _pg_writes = await is_cutover_feature_enabled(building_id, FINANCIAL_PG_WRITES_ENABLED)
    except Exception as _flag_err:
        logger.warning("Invoice confirm: flag resolution failed, defaulting to Mongo path: %s", _flag_err)

    tx_doc: dict | None = None
    pg_result: dict | None = None

    if _pg_writes:
        # --- Postgres path via FinancialCoreAdapter ---
        # MongoDB financial_transactions write is skipped only when the adapter
        # confirms it actually wrote to Postgres (source == "postgres").  If the
        # adapter returns source="mongo" (flag off, GL accounts missing, etc.)
        # we fall through so the Mongo insert below runs as normal.
        try:
            from services.financial_core.adapter import FinancialCoreAdapter
            _adapter = FinancialCoreAdapter(building_id)
            pg_result = await _adapter.record_expense_transaction(
                amount_dollars=body.total,
                gst_dollars=body.gst or 0.0,
                description=f"Invoice {body.invoice_number or ''} — {body.vendor_name}".strip(" —"),
                fund_type=body.fund,
                category_name=body.category or "General Maintenance",
                financial_year=fy,
                vendor_name=body.vendor_name,
                invoice_number=body.invoice_number,
                transaction_date=body.invoice_date,
                created_by=current_user["id"],
                idempotency_key=f"inv-confirm-{invoice_id}",
                is_test_data=False,
            )
            # Only treat the result as a successful PG write when the adapter
            # confirms it by returning source="postgres" with a journal_entry_id.
            # source="mongo" means the flag was off internally or a fallback
            # occurred — in that case we must still do the MongoDB insert below.
            if pg_result.get("source") == "postgres" and pg_result.get("journal_entry_id"):
                tx_doc = {
                    "id": pg_result["journal_entry_id"],
                    "source": "postgres",
                }
            else:
                logger.info(
                    "Invoice confirm: adapter returned source=%r for %s, using MongoDB path",
                    pg_result.get("source"), invoice_id,
                )
                _pg_writes = False  # fall through to Mongo insert
        except Exception as _pg_err:
            logger.warning(
                "Invoice confirm: Postgres write failed for %s, falling back to MongoDB: %s",
                invoice_id, _pg_err,
            )
            _pg_writes = False  # fall through to Mongo path

    if not _pg_writes:
        # --- MongoDB path (legacy + rollback safety) ---
        tx_doc = await repo.insert_transaction({
            "building_id": building_id,
            "financial_year": fy,
            "fund_type": body.fund,
            "category_name": body.category or "General Maintenance",
            "transaction_type": "expense",
            "amount": body.total,
            "gst_amount": body.gst,
            "description": f"Invoice {body.invoice_number or ''} — {body.vendor_name}".strip(" —"),
            "reference": body.invoice_number,
            "transaction_date": body.invoice_date,
            "supplier_name": body.vendor_name,
            "created_by": current_user["id"],
            "source": "invoice_ocr",
        })

    building_doc = await db.buildings.find_one({"id": building_id}, {"_id": 0, "name": 1})
    building_name = (building_doc or {}).get("name", "Your Building")

    try:
        receipt_bytes = generate_invoice_receipt_pdf(
            invoice_data=body.model_dump(),
            building_name=building_name,
        )
        receipt_b64 = base64.b64encode(receipt_bytes).decode("utf-8") if receipt_bytes else None
    except Exception as exc:
        logger.error("Receipt PDF generation failed for invoice %s: %s", invoice_id, exc)
        receipt_b64 = None

    # Atomicity fix: update the invoice document; roll back the transaction on failure.
    try:
        confirmed_doc = await repo.update_invoice(invoice_id, building_id, {
            "status": "confirmed",
            "confirmed_data": body.model_dump(),
            "transaction_id": tx_doc["id"] if tx_doc else None,
            "receipt_generated": receipt_b64 is not None,
            "receipt_data": receipt_b64,
            "confirmed_at": _now(),
            "confirmed_by": current_user["id"],
        })
    except Exception as update_err:
        # Roll back the MongoDB transaction document to avoid an orphan.
        # NOTE: if _pg_writes is True the journal entry in finance.journal_entries
        # was already committed by the time update_invoice() runs — async_session_context
        # commits on exit.  There is no automatic Postgres rollback here.  The
        # idempotency key (inv-confirm-{invoice_id}) prevents double-posting on
        # a client retry, but the orphaned journal entry will remain until the
        # outbox relay detects the missing invoice_document pairing.
        if not _pg_writes and tx_doc and tx_doc.get("id"):
            try:
                await db.financial_transactions.delete_one({"id": tx_doc["id"]})
                logger.warning(
                    "Invoice confirm: rolled back transaction %s after invoice update failure",
                    tx_doc["id"],
                )
            except Exception as rb_err:
                logger.error(
                    "Invoice confirm: transaction rollback also failed for %s: %s",
                    tx_doc["id"], rb_err,
                )
        raise HTTPException(
            status_code=500,
            detail=f"Invoice confirmation failed after transaction was created: {update_err}",
        )

    confirmed_doc.pop("file_data", None)
    return InvoiceDocumentResponse(**confirmed_doc)


# ── Delete (pending only) ─────────────────────────────────────────────────────

@router.delete("/invoices/{invoice_id}", status_code=204)
async def delete_invoice(
        invoice_id: str,
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: delete_invoice
    Path: backend/routers/invoices.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _finance_roles(current_user):
        raise HTTPException(status_code=403, detail="Finance management access required")
    _require_valid_id(invoice_id)

    doc = await repo.get_invoice(invoice_id, building_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if doc["status"] == "confirmed":
        raise HTTPException(status_code=409, detail="Confirmed invoices cannot be deleted (7-year retention policy)")

    await db.invoice_documents.delete_one({"id": invoice_id, "building_id": building_id})


# ── Receipt download ──────────────────────────────────────────────────────────

@router.get("/invoices/{invoice_id}/receipt")
async def get_receipt(
        invoice_id: str,
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_receipt
    Path: backend/routers/invoices.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _finance_roles(current_user):
        raise HTTPException(status_code=403, detail="Finance management access required")
    _require_valid_id(invoice_id)

    receipt_doc = await _get_receipt_data(invoice_id, building_id)
    if receipt_doc is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not receipt_doc.get("receipt_data"):
        raise HTTPException(status_code=404, detail="Receipt not yet generated")

    pdf_bytes = base64.b64decode(receipt_doc["receipt_data"])
    vendor = (receipt_doc.get("confirmed_data") or {}).get("vendor_name", "invoice")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in vendor)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="receipt_{safe_name}.pdf"'},
    )


# ── OCR Provider Settings (per-building) ─────────────────────────────────────

@router.get("/settings/ocr-provider")
async def get_ocr_provider_settings(
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Return current OCR provider for this building, monthly scan count, and cost info."""
    if not _finance_roles(current_user):
        raise HTTPException(status_code=403, detail="Finance management access required")

    # Performance Optimization⚡: Parallelize settings lookups and scan averages.
    # This reduces sequential database round-trips from O(3) to O(1) concurrent sets.
    role = current_user.get("effective_role") or current_user.get("role", "guest")

    tasks = [
        db.settings.find_one({"building_id": building_id}, {"integration_provider_preference": 1}),
        _get_monthly_scan_avg(building_id)
    ]
    if role == "super_admin":
        tasks.append(db.site_settings.find_one({"id": _APP_DEFAULTS_ID}, {"ocr_default_provider": 1}))

    results = await asyncio.gather(*tasks)

    building_settings = results[0] or {}
    monthly_scans = results[1]
    app_defaults = results[2] if len(results) > 2 else {}

    pref = building_settings.get("integration_provider_preference", {})
    current_provider = pref.get("ocr") or "auto"
    if current_provider not in VALID_OCR_PROVIDERS:
        current_provider = "auto"

    global_default = app_defaults.get("ocr_default_provider") if role == "super_admin" else None

    return {
        "building_id": building_id,
        "ocr_provider": current_provider,
        "monthly_scans": monthly_scans,
        "global_default": global_default,
        "available_providers": _provider_metadata(monthly_scans),
    }


@router.patch("/settings/ocr-provider", response_model=OCRProviderSettingResponse)
async def update_ocr_provider_settings(
        body: OCRProviderUpdate,
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Set per-building OCR provider preference."""
    if not _finance_roles(current_user):
        raise HTTPException(status_code=403, detail="Finance management access required")

    if body.ocr_provider not in VALID_OCR_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider. Valid options: {sorted(VALID_OCR_PROVIDERS)}",
        )
    if body.ocr_provider == UNLIMITED_OCR and not get_unlimited_ocr_config().is_available:
        raise HTTPException(
            status_code=400,
            detail="Unlimited OCR is not configured for this environment.",
        )

    await db.settings.update_one(
        {"building_id": building_id},
        {"$set": {
            "integration_provider_preference.ocr": body.ocr_provider,
            "updated_at": _now(),
        }},
        upsert=True,
    )
    return {"building_id": building_id, "ocr_provider": body.ocr_provider}


# ── OCR Provider Settings (global app default, super_admin only) ──────────────

@router.get("/admin/settings/global-ocr")
async def get_global_ocr_settings(
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Return the app-level OCR default (super_admin only)."""
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")

    app_defaults = await db.site_settings.find_one(
        {"id": _APP_DEFAULTS_ID},
        {"ocr_default_provider": 1},
    ) or {}
    return {
        "ocr_default_provider": app_defaults.get("ocr_default_provider") or "auto",
        "available_providers": _provider_metadata(5),
    }


@router.patch("/admin/settings/global-ocr", response_model=GlobalOCRSettingResponse)
async def update_global_ocr_settings(
        body: GlobalOCRUpdate,
        current_user: dict = Depends(require_feature("invoice_ocr")),
        building_id: str = Depends(get_current_building),
):
    """Set the app-level OCR default (super_admin only)."""
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")

    if body.ocr_default_provider not in VALID_OCR_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider. Valid options: {sorted(VALID_OCR_PROVIDERS)}",
        )
    if body.ocr_default_provider == UNLIMITED_OCR and not get_unlimited_ocr_config().is_available:
        raise HTTPException(
            status_code=400,
            detail="Unlimited OCR is not configured for this environment.",
        )

    await db.site_settings.update_one(
        {"id": _APP_DEFAULTS_ID},
        {"$set": {
            "id": _APP_DEFAULTS_ID,
            "ocr_default_provider": body.ocr_default_provider,
            "updated_at": _now(),
        }},
        upsert=True,
    )
    return {"ocr_default_provider": body.ocr_default_provider}


# ── Provider metadata helper ──────────────────────────────────────────────────

def _provider_metadata(monthly_scans: int) -> list:
    """Return display info for all providers, including cost estimates at current scan volume."""
    unlimited = get_unlimited_ocr_config()
    return [
        {
            "id": "auto",
            "label": "Auto (Recommended)",
            "description": (
                "Automatically selects the best available engine. "
                "Tries Claude Vision first for highest accuracy, falls back to Mindee, "
                "then Tesseract if others are unavailable."
            ),
            "cost_per_scan_aud": "Variable",
            "est_monthly_cost_aud": f"AUD ${_auto_monthly_cost(monthly_scans):.2f}",
            "est_scans_per_month": monthly_scans,
            "strengths": ["Highest accuracy", "Automatic fallback", "Handles all document types"],
            "badge": "Recommended",
            "requires_api_key": False,
        },
        {
            "id": "claude_vision",
            "label": "Claude Vision (Anthropic)",
            "description": (
                "AI-powered computer vision using claude-sonnet-4-6. "
                "Best for complex layouts, poor-quality scans, handwritten notes, "
                "and multi-language invoices. Sends document to Anthropic API."
            ),
            "cost_per_scan_aud": "~AUD $0.02–$0.05 per scan",
            "est_monthly_cost_aud": f"AUD ${monthly_scans * 0.035:.2f}",
            "est_scans_per_month": monthly_scans,
            "strengths": ["Best accuracy", "Handles handwriting", "Complex layouts", "Multi-language"],
            "badge": "Premium AI",
            "requires_api_key": True,
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        {
            "id": "mindee",
            "label": "Mindee Invoice AI",
            "description": (
                "Specialist invoice OCR API trained on millions of real invoices. "
                "Excellent line-item extraction and ABN/GST detection for standard formats. "
                "Free tier covers 250 pages/month."
            ),
            "cost_per_scan_aud": "Free ≤250/mo, ~AUD $0.015 per scan beyond",
            "est_monthly_cost_aud": "Free" if monthly_scans <= 250 else f"AUD ${max(0, monthly_scans - 250) * 0.015:.2f}",
            "est_scans_per_month": monthly_scans,
            "strengths": ["Structured invoices", "Line-item extraction", "ABN/GST detection"],
            "badge": "Specialist",
            "requires_api_key": True,
            "api_key_env": "MINDEE_API_KEY",
        },
        {
            "id": "tesseract",
            "label": "Tesseract (Local / Free)",
            "description": (
                "Open-source OCR engine that runs on your own server. "
                "No data is sent to external services and there is no per-scan cost. "
                "Best for clear, text-layer PDFs. Less accurate on scanned images or "
                "complex layouts. Requires tesseract-ocr system package."
            ),
            "cost_per_scan_aud": "Free",
            "est_monthly_cost_aud": "Free",
            "est_scans_per_month": monthly_scans,
            "strengths": ["No per-scan cost", "Data stays local", "No API key required"],
            "badge": "Free / Local",
            "requires_api_key": False,
        },
        {
            "id": UNLIMITED_OCR,
            "label": "Unlimited OCR (Advanced Document Parsing)",
            "description": (
                "Advanced OCR for scanned, dense, or multi-page documents. "
                "Runs only through the configured isolated runtime: local worker, Baidu Cloud, "
                "or private endpoint. Finance-impacting results still require human review."
            ),
            "cost_per_scan_aud": "Depends on runtime",
            "est_monthly_cost_aud": "Configure runtime first",
            "est_scans_per_month": monthly_scans,
            "strengths": ["Dense scans", "Multi-page PDFs", "Tables and long documents"],
            "badge": "Advanced",
            "requires_api_key": unlimited.backend == "baidu_cloud",
            "api_key_env": "UNLIMITED_OCR_API_KEY" if unlimited.backend == "baidu_cloud" else None,
            "selectable": unlimited.is_available,
            "available": unlimited.is_available,
            "unavailable_reason": unlimited.unavailable_reason,
            "runtime_mode": unlimited.backend,
            "max_pages": unlimited.max_pages,
            "max_bytes": unlimited.max_bytes,
        },
    ]


def _auto_monthly_cost(monthly_scans: int) -> float:
    """Estimate auto-waterfall cost: assume ~70% Claude Vision, 20% Mindee, 10% Tesseract."""
    claude_scans = round(monthly_scans * 0.7)
    mindee_scans = round(monthly_scans * 0.2)
    return (claude_scans * 0.035) + (max(0, mindee_scans - 250) * 0.015)
