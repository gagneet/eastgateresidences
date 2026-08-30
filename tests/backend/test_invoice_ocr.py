"""
Tests for Invoice OCR — service, repository helpers, and router endpoints.

Building isolation: all mocked docs use building_id="13195".
"""
import base64
import json
import sys
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure backend is on sys.path before importing routers
_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Pre-import router module so patch() can resolve "routers.invoices.*"
import routers.invoices  # noqa: E402
from routers.invoices import (  # noqa: E402
    upload_and_parse, list_invoices, get_invoice,
    confirm_invoice, delete_invoice,
    get_ocr_provider_settings, update_ocr_provider_settings,
    get_global_ocr_settings, update_global_ocr_settings,
    _resolve_ocr_provider, OCRProviderUpdate, GlobalOCRUpdate,
)
from models.invoice_models import InvoiceConfirmRequest, InvoiceLineItem  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

BUILDING_A = "13195"
INVOICE_ID = "00000000-0000-0000-0000-000000000001"
BUILDING_B = "16244"
FAKE_PDF = b"%PDF-1.4 fake content"

PARSED_DATA = {
    "vendor_name": "ABC Plumbing Pty Ltd",
    "abn": "12 345 678 901",
    "invoice_number": "INV-001",
    "invoice_date": "2026-04-15",
    "due_date": "2026-05-15",
    "line_items": [{"description": "Emergency repair", "qty": 1, "unit_price": 450.0, "total": 450.0}],
    "subtotal": 450.0,
    "gst": 45.0,
    "total": 495.0,
    "suggested_fund": "admin",
    "confidence": 0.92,
    "ocr_source": "claude_vision",
}

INVOICE_DOC = {
    "id": INVOICE_ID,
    "building_id": BUILDING_A,
    "status": "pending_review",
    "document_type": "invoice",
    "file_name": "plumbing.pdf",
    "file_type": "application/pdf",
    "file_size_bytes": len(FAKE_PDF),
    "ocr_source": "claude_vision",
    "ocr_confidence": 0.92,
    "parsed_data": PARSED_DATA,
    "confirmed_data": None,
    "transaction_id": None,
    "receipt_generated": False,
    "receipt_data": None,
    "created_at": "2026-04-15T00:00:00+00:00",
    "created_by": "user-1",
    "confirmed_at": None,
    "confirmed_by": None,
}


def _user(role="strata_manager"):
    return {"id": "user-1", "role": role, "effective_role": role, "building_id": BUILDING_A}


def _confirm_body(**overrides):
    return InvoiceConfirmRequest(
        vendor_name="ABC Plumbing Pty Ltd",
        abn="12 345 678 901",
        invoice_number="INV-001",
        invoice_date="2026-04-15",
        subtotal=450.0, gst=45.0, total=495.0,
        fund="admin", category="Plumbing",
        line_items=[InvoiceLineItem(description="Repair", qty=1, unit_price=450.0, total=450.0)],
        **overrides,
    )


# ─────────────────────────────────────────────────────────────────────────────
# OCR Service unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestInvoiceOCRService:

    @pytest.mark.asyncio
    async def test_extract_with_claude_returns_parsed_dict(self):
        from services.invoice_ocr_service import extract_with_claude
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(PARSED_DATA))]
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", "test-key"), \
                patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_with_claude(FAKE_PDF, "application/pdf")

        assert result["vendor_name"] == "ABC Plumbing Pty Ltd"
        assert result["total"] == 495.0
        assert result["ocr_source"] == "claude_vision"

    @pytest.mark.asyncio
    async def test_extract_with_claude_strips_markdown_fences(self):
        from services.invoice_ocr_service import extract_with_claude
        fenced = "```json\n" + json.dumps(PARSED_DATA) + "\n```"
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=fenced)]
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", "test-key"), \
                patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_with_claude(FAKE_PDF, "application/pdf")

        assert result["vendor_name"] == "ABC Plumbing Pty Ltd"

    @pytest.mark.asyncio
    async def test_parse_invoice_falls_back_to_mindee_on_claude_error(self):
        from services.invoice_ocr_service import parse_invoice
        mindee_result = {**PARSED_DATA, "ocr_source": "mindee"}

        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", "test-key"), \
                patch("services.invoice_ocr_service.MINDEE_API_KEY", "mindee-key"), \
                patch("services.invoice_ocr_service.extract_with_claude",
                      AsyncMock(side_effect=Exception("API error"))), \
                patch("services.invoice_ocr_service.extract_with_mindee", AsyncMock(return_value=mindee_result)):
            result = await parse_invoice(FAKE_PDF, "application/pdf")

        assert result["ocr_source"] == "mindee"

    @pytest.mark.asyncio
    async def test_parse_invoice_falls_back_on_low_confidence(self):
        from services.invoice_ocr_service import parse_invoice
        low_conf = {**PARSED_DATA, "confidence": 0.3}
        mindee_result = {**PARSED_DATA, "ocr_source": "mindee"}

        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", "test-key"), \
                patch("services.invoice_ocr_service.MINDEE_API_KEY", "mindee-key"), \
                patch("services.invoice_ocr_service.extract_with_claude", AsyncMock(return_value=low_conf)), \
                patch("services.invoice_ocr_service.extract_with_mindee", AsyncMock(return_value=mindee_result)):
            result = await parse_invoice(FAKE_PDF, "application/pdf")

        assert result["ocr_source"] == "mindee"

    @pytest.mark.asyncio
    async def test_parse_invoice_returns_empty_scaffold_when_no_keys(self):
        from services.invoice_ocr_service import parse_invoice
        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", ""), \
                patch("services.invoice_ocr_service.MINDEE_API_KEY", ""):
            result = await parse_invoice(FAKE_PDF, "application/pdf")

        assert result["ocr_source"] == "manual"
        assert result["total"] == 0.0

    @pytest.mark.asyncio
    async def test_no_api_key_raises_runtime_error(self):
        from services.invoice_ocr_service import extract_with_claude
        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", ""):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                await extract_with_claude(FAKE_PDF, "application/pdf")


# ─────────────────────────────────────────────────────────────────────────────
# Router: upload-and-parse
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadAndParse:

    @pytest.mark.asyncio
    async def test_upload_returns_parsed_preview(self):
        set_ctx_building_id(BUILDING_A)
        stored = {**INVOICE_DOC}
        mock_file = MagicMock()
        mock_file.content_type = "application/pdf"
        mock_file.filename = "plumbing.pdf"
        mock_file.read = AsyncMock(return_value=FAKE_PDF)

        with patch("routers.invoices.parse_invoice", AsyncMock(return_value=PARSED_DATA)), \
                patch("routers.invoices.scan_upload", AsyncMock()), \
                patch("routers.invoices.repo.insert_invoice_record", AsyncMock(return_value=stored)):
            result = await upload_and_parse(
                file=mock_file, document_type="invoice",
                current_user=_user(), building_id=BUILDING_A,
            )

        assert result.id == INVOICE_ID
        assert result.status == "pending_review"
        assert result.ocr_source == "claude_vision"

    @pytest.mark.asyncio
    async def test_upload_rejects_unsupported_file_type(self):
        from fastapi import HTTPException
        mock_file = MagicMock()
        mock_file.content_type = "text/csv"
        mock_file.filename = "data.csv"
        mock_file.read = AsyncMock(return_value=b"col1,col2")

        with pytest.raises(HTTPException) as exc:
            await upload_and_parse(
                file=mock_file, document_type="invoice",
                current_user=_user(), building_id=BUILDING_A,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_rejects_oversized_file(self):
        from fastapi import HTTPException
        mock_file = MagicMock()
        mock_file.content_type = "application/pdf"
        mock_file.filename = "big.pdf"
        mock_file.read = AsyncMock(return_value=b"x" * (21 * 1024 * 1024))

        with patch("routers.invoices.scan_upload", AsyncMock()):
            with pytest.raises(HTTPException) as exc:
                await upload_and_parse(
                    file=mock_file, document_type="invoice",
                    current_user=_user(), building_id=BUILDING_A,
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_non_finance_role_is_forbidden(self):
        from fastapi import HTTPException
        mock_file = MagicMock()
        mock_file.content_type = "application/pdf"
        mock_file.filename = "inv.pdf"
        mock_file.read = AsyncMock(return_value=FAKE_PDF)

        with patch("routers.invoices.scan_upload", AsyncMock()):
            with pytest.raises(HTTPException) as exc:
                await upload_and_parse(
                    file=mock_file, document_type="invoice",
                    current_user=_user(role="tenant"), building_id=BUILDING_A,
                )
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Router: confirm
# ─────────────────────────────────────────────────────────────────────────────

class TestConfirmInvoice:

    @pytest.mark.asyncio
    async def test_confirm_creates_transaction_and_marks_confirmed(self):
        set_ctx_building_id(BUILDING_A)
        tx = {"id": "tx-001", "building_id": BUILDING_A}
        receipt_b64 = base64.b64encode(b"%PDF receipt").decode()
        confirmed_doc = {
            **INVOICE_DOC, "status": "confirmed",
            "transaction_id": "tx-001",
            "receipt_generated": True,
            "receipt_data": receipt_b64,
            "confirmed_at": "2026-04-15T01:00:00+00:00",
            "confirmed_by": "user-1",
        }

        with patch("routers.invoices.repo.get_invoice", AsyncMock(return_value={**INVOICE_DOC})), \
                patch("routers.invoices.repo.insert_transaction", AsyncMock(return_value=tx)), \
                patch("routers.invoices.generate_invoice_receipt_pdf", return_value=b"%PDF receipt"), \
                patch("routers.invoices.repo.update_invoice", AsyncMock(return_value=confirmed_doc)):
            result = await confirm_invoice(
                invoice_id=INVOICE_ID, body=_confirm_body(),
                current_user=_user(), building_id=BUILDING_A,
            )

        assert result.status == "confirmed"
        assert result.transaction_id == "tx-001"
        assert result.receipt_generated is True

    @pytest.mark.asyncio
    async def test_confirm_rejects_already_confirmed(self):
        from fastapi import HTTPException
        confirmed = {**INVOICE_DOC, "status": "confirmed"}

        with patch("routers.invoices.repo.get_invoice", AsyncMock(return_value=confirmed)):
            with pytest.raises(HTTPException) as exc:
                await confirm_invoice(INVOICE_ID, _confirm_body(), _user(), BUILDING_A)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_confirm_rejects_zero_total(self):
        from fastapi import HTTPException

        with patch("routers.invoices.repo.get_invoice", AsyncMock(return_value={**INVOICE_DOC})):
            with pytest.raises(HTTPException) as exc:
                await confirm_invoice(
                    INVOICE_ID,
                    InvoiceConfirmRequest(
                        vendor_name="ABC", invoice_date="2026-04-15",
                        subtotal=0, gst=0, total=0, fund="admin",
                    ),
                    _user(), BUILDING_A,
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_confirm_rejects_missing_vendor(self):
        from fastapi import HTTPException

        with patch("routers.invoices.repo.get_invoice", AsyncMock(return_value={**INVOICE_DOC})):
            with pytest.raises(HTTPException) as exc:
                await confirm_invoice(
                    INVOICE_ID,
                    InvoiceConfirmRequest(
                        vendor_name="  ", invoice_date="2026-04-15",
                        subtotal=450, gst=45, total=495, fund="admin",
                    ),
                    _user(), BUILDING_A,
                )
        assert exc.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Router: delete
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteInvoice:

    @pytest.mark.asyncio
    async def test_delete_pending_succeeds(self):
        mock_db = MagicMock()
        mock_db.invoice_documents.delete_one = AsyncMock()

        with patch("routers.invoices.repo.get_invoice", AsyncMock(return_value={**INVOICE_DOC})), \
                patch("routers.invoices.db", mock_db):
            await delete_invoice(INVOICE_ID, _user(), BUILDING_A)

        mock_db.invoice_documents.delete_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_confirmed_is_blocked(self):
        from fastapi import HTTPException
        confirmed = {**INVOICE_DOC, "status": "confirmed"}

        with patch("routers.invoices.repo.get_invoice", AsyncMock(return_value=confirmed)):
            with pytest.raises(HTTPException) as exc:
                await delete_invoice(INVOICE_ID, _user(), BUILDING_A)
        assert exc.value.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# Multi-tenant isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiTenantIsolation:

    @pytest.mark.asyncio
    async def test_invoice_from_building_a_not_visible_in_building_b(self):
        from fastapi import HTTPException
        # repo returns None when building_id doesn't match
        with patch("routers.invoices.repo.get_invoice", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await get_invoice(INVOICE_ID, _user(), BUILDING_B)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_passes_building_id_to_repository(self):
        with patch("routers.invoices.repo.list_invoices", AsyncMock(return_value=[])) as mock_list:
            await list_invoices(
                status=None, document_type=None,
                current_user=_user(), building_id=BUILDING_B,
            )
        mock_list.assert_awaited_once_with(
            building_id=BUILDING_B, status=None, document_type=None
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tesseract OCR engine
# ─────────────────────────────────────────────────────────────────────────────

class TestTesseractOCR:

    def test_parse_tesseract_text_extracts_total(self):
        from services.invoice_ocr_service import _parse_tesseract_text
        text = "ABC Plumbing\nABN: 12 345 678 901\nInvoice No: INV-001\nDate: 15/04/2026\nTotal Due: $495.00\nGST: $45.00"
        result = _parse_tesseract_text(text)
        assert result["total"] == 495.0
        assert result["gst"] == 45.0
        assert result["ocr_source"] == "tesseract"

    def test_parse_tesseract_text_extracts_abn(self):
        from services.invoice_ocr_service import _parse_tesseract_text
        text = "Vendor Corp\nABN: 12 345 678 901\nTotal: $100.00"
        result = _parse_tesseract_text(text)
        assert result["abn"] == "12 345 678 901"

    def test_parse_tesseract_text_normalises_date(self):
        from services.invoice_ocr_service import _parse_tesseract_text
        text = "Invoice date: 15/04/2026\nTotal: $100.00"
        result = _parse_tesseract_text(text)
        assert result["invoice_date"] == "2026-04-15"

    def test_parse_tesseract_text_suggests_sinking_fund_for_capital_works(self):
        from services.invoice_ocr_service import _parse_tesseract_text
        text = "Roof replacement project\nTotal: $50000.00"
        result = _parse_tesseract_text(text)
        assert result["suggested_fund"] == "sinking"

    def test_parse_tesseract_text_suggests_admin_fund_by_default(self):
        from services.invoice_ocr_service import _parse_tesseract_text
        text = "Plumbing repair\nTotal: $495.00"
        result = _parse_tesseract_text(text)
        assert result["suggested_fund"] == "admin"

    def test_parse_tesseract_text_extracts_total_without_space_before_colon(self):
        """Regression: 'Total: $X' (no space before colon) must extract correctly."""
        from services.invoice_ocr_service import _parse_tesseract_text
        result = _parse_tesseract_text("Service invoice\nTotal: $1,250.00\nGST: $113.64")
        assert result["total"] == 1250.0
        assert result["gst"] == 113.64

    def test_parse_tesseract_text_total_formats(self):
        """Multiple common AU invoice total formats all extract correctly."""
        from services.invoice_ocr_service import _parse_tesseract_text
        cases = [
            ("Total: $100.00", 100.0),
            ("Total Due: $200.00", 200.0),
            ("Total Amount: $300.00", 300.0),
            ("Amount Due: $400.00", 400.0),
        ]
        for text, expected in cases:
            result = _parse_tesseract_text(text)
            assert result["total"] == expected, f"Failed for: {text!r}"

    def test_parse_tesseract_text_confidence_scales_with_fields(self):
        from services.invoice_ocr_service import _parse_tesseract_text
        # No fields found → low confidence
        result_empty = _parse_tesseract_text("")
        assert result_empty["confidence"] <= 0.35
        # All fields found → higher confidence
        text = "Vendor Corp\nABN: 12 345 678 901\nInvoice No: INV-001\nDate: 15/04/2026\nTotal Due: $495.00"
        result_full = _parse_tesseract_text(text)
        assert result_full["confidence"] > result_empty["confidence"]

    def test_normalise_date_handles_two_digit_year(self):
        from services.invoice_ocr_service import _normalise_date
        assert _normalise_date("15/04/26") == "2026-04-15"

    def test_normalise_date_passthrough_on_unknown_format(self):
        from services.invoice_ocr_service import _normalise_date
        assert _normalise_date("April 15 2026") == "April 15 2026"

    @pytest.mark.asyncio
    async def test_extract_with_tesseract_missing_package_raises(self):
        from services.invoice_ocr_service import extract_with_tesseract
        import sys
        with patch.dict(sys.modules, {"pytesseract": None, "PIL": None}):
            with pytest.raises((RuntimeError, ImportError)):
                await extract_with_tesseract(FAKE_PDF, "application/pdf")

    @pytest.mark.asyncio
    async def test_parse_invoice_with_tesseract_provider(self):
        from services.invoice_ocr_service import parse_invoice
        tess_result = {**PARSED_DATA, "ocr_source": "tesseract"}

        with patch("services.invoice_ocr_service.extract_with_tesseract", AsyncMock(return_value=tess_result)):
            result = await parse_invoice(FAKE_PDF, "application/pdf", provider="tesseract")

        assert result["ocr_source"] == "tesseract"

    @pytest.mark.asyncio
    async def test_parse_invoice_tesseract_returns_empty_on_failure(self):
        from services.invoice_ocr_service import parse_invoice

        with patch("services.invoice_ocr_service.extract_with_tesseract",
                   AsyncMock(side_effect=RuntimeError("no tesseract"))):
            result = await parse_invoice(FAKE_PDF, "application/pdf", provider="tesseract")

        assert result["ocr_source"] == "manual"

    @pytest.mark.asyncio
    async def test_parse_invoice_auto_falls_back_to_tesseract(self):
        from services.invoice_ocr_service import parse_invoice
        tess_result = {**PARSED_DATA, "ocr_source": "tesseract", "confidence": 0.6}

        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", ""), \
                patch("services.invoice_ocr_service.MINDEE_API_KEY", ""), \
                patch("services.invoice_ocr_service.extract_with_tesseract", AsyncMock(return_value=tess_result)):
            result = await parse_invoice(FAKE_PDF, "application/pdf", provider="auto")

        assert result["ocr_source"] == "tesseract"


# ─────────────────────────────────────────────────────────────────────────────
# parse_invoice() with explicit provider param
# ─────────────────────────────────────────────────────────────────────────────

class TestParseInvoiceProviderParam:

    @pytest.mark.asyncio
    async def test_claude_vision_provider_skips_mindee(self):
        from services.invoice_ocr_service import parse_invoice
        claude_result = {**PARSED_DATA, "ocr_source": "claude_vision"}

        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", "key"), \
                patch("services.invoice_ocr_service.extract_with_claude",
                      AsyncMock(return_value=claude_result)) as mock_claude, \
                patch("services.invoice_ocr_service.extract_with_mindee", AsyncMock()) as mock_mindee:
            result = await parse_invoice(FAKE_PDF, "application/pdf", provider="claude_vision")

        assert result["ocr_source"] == "claude_vision"
        mock_claude.assert_awaited_once()
        mock_mindee.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mindee_provider_skips_claude(self):
        from services.invoice_ocr_service import parse_invoice
        mindee_result = {**PARSED_DATA, "ocr_source": "mindee"}

        with patch("services.invoice_ocr_service.MINDEE_API_KEY", "key"), \
                patch("services.invoice_ocr_service.extract_with_mindee",
                      AsyncMock(return_value=mindee_result)) as mock_mindee, \
                patch("services.invoice_ocr_service.extract_with_claude", AsyncMock()) as mock_claude:
            result = await parse_invoice(FAKE_PDF, "application/pdf", provider="mindee")

        assert result["ocr_source"] == "mindee"
        mock_mindee.assert_awaited_once()
        mock_claude.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_claude_vision_no_api_key_returns_empty(self):
        from services.invoice_ocr_service import parse_invoice
        with patch("services.invoice_ocr_service.ANTHROPIC_API_KEY", ""):
            result = await parse_invoice(FAKE_PDF, "application/pdf", provider="claude_vision")
        assert result["ocr_source"] == "manual"

    @pytest.mark.asyncio
    async def test_mindee_no_api_key_returns_empty(self):
        from services.invoice_ocr_service import parse_invoice
        with patch("services.invoice_ocr_service.MINDEE_API_KEY", ""):
            result = await parse_invoice(FAKE_PDF, "application/pdf", provider="mindee")
        assert result["ocr_source"] == "manual"


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_ocr_provider()
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveOCRProvider:

    @pytest.mark.asyncio
    async def test_returns_per_building_preference(self):
        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value={
            "building_id": BUILDING_A,
            "integration_provider_preference": {"ocr": "mindee"},
        })
        mock_db.site_settings.find_one = AsyncMock(return_value=None)
        with patch("routers.invoices.db", mock_db):
            result = await _resolve_ocr_provider(BUILDING_A)
        assert result == "mindee"

    @pytest.mark.asyncio
    async def test_falls_back_to_global_default_when_no_building_pref(self):
        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value=None)
        mock_db.site_settings.find_one = AsyncMock(return_value={
            "id": "app_defaults",
            "ocr_default_provider": "tesseract",
        })
        with patch("routers.invoices.db", mock_db):
            result = await _resolve_ocr_provider(BUILDING_A)
        assert result == "tesseract"

    @pytest.mark.asyncio
    async def test_falls_back_to_auto_when_no_config(self):
        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value=None)
        mock_db.site_settings.find_one = AsyncMock(return_value=None)
        with patch("routers.invoices.db", mock_db):
            result = await _resolve_ocr_provider(BUILDING_A)
        assert result == "auto"

    @pytest.mark.asyncio
    async def test_ignores_invalid_building_preference(self):
        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value={
            "integration_provider_preference": {"ocr": "unknown_provider"},
        })
        mock_db.site_settings.find_one = AsyncMock(return_value=None)
        with patch("routers.invoices.db", mock_db):
            result = await _resolve_ocr_provider(BUILDING_A)
        assert result == "auto"

    @pytest.mark.asyncio
    async def test_db_error_falls_back_to_auto(self):
        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(side_effect=Exception("DB unavailable"))
        mock_db.site_settings.find_one = AsyncMock(side_effect=Exception("DB unavailable"))
        with patch("routers.invoices.db", mock_db):
            result = await _resolve_ocr_provider(BUILDING_A)
        assert result == "auto"

    @pytest.mark.asyncio
    async def test_building_pref_takes_priority_over_global(self):
        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value={
            "integration_provider_preference": {"ocr": "claude_vision"},
        })
        mock_db.site_settings.find_one = AsyncMock(return_value={
            "ocr_default_provider": "tesseract",
        })
        with patch("routers.invoices.db", mock_db):
            result = await _resolve_ocr_provider(BUILDING_A)
        assert result == "claude_vision"


# ─────────────────────────────────────────────────────────────────────────────
# OCR provider settings endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestOCRProviderSettingsEndpoints:

    def _mock_db(self, building_pref="auto", global_pref="auto"):
        mock_db = MagicMock()
        mock_db.settings.find_one = AsyncMock(return_value={
            "integration_provider_preference": {"ocr": building_pref},
        })
        mock_db.site_settings.find_one = AsyncMock(return_value={
            "id": "app_defaults",
            "ocr_default_provider": global_pref,
        })
        mock_db.settings.update_one = AsyncMock()
        mock_db.site_settings.update_one = AsyncMock()
        mock_db.invoice_documents.count_documents = AsyncMock(return_value=15)
        return mock_db

    @pytest.mark.asyncio
    async def test_get_ocr_settings_returns_provider_and_scan_count(self):
        mock_db = self._mock_db(building_pref="mindee")
        with patch("routers.invoices.db", mock_db):
            result = await get_ocr_provider_settings(
                current_user=_user(), building_id=BUILDING_A
            )
        assert result["ocr_provider"] == "mindee"
        assert result["monthly_scans"] >= 1
        # 5, not 4: auto, claude_vision, mindee, tesseract, unlimited_ocr (GAP-OCR-001).
        assert len(result["available_providers"]) == 5

    @pytest.mark.asyncio
    async def test_get_ocr_settings_includes_global_default_for_super_admin(self):
        mock_db = self._mock_db(global_pref="tesseract")
        admin = _user(role="super_admin")
        admin["effective_role"] = "super_admin"
        with patch("routers.invoices.db", mock_db):
            result = await get_ocr_provider_settings(
                current_user=admin, building_id=BUILDING_A
            )
        assert result["global_default"] == "tesseract"

    @pytest.mark.asyncio
    async def test_get_ocr_settings_global_default_hidden_from_non_admin(self):
        mock_db = self._mock_db()
        with patch("routers.invoices.db", mock_db):
            result = await get_ocr_provider_settings(
                current_user=_user(role="strata_manager"), building_id=BUILDING_A
            )
        assert result["global_default"] is None

    @pytest.mark.asyncio
    async def test_update_ocr_provider_writes_to_settings(self):
        mock_db = self._mock_db()
        with patch("routers.invoices.db", mock_db):
            result = await update_ocr_provider_settings(
                body=OCRProviderUpdate(ocr_provider="tesseract"),
                current_user=_user(), building_id=BUILDING_A,
            )
        assert result["ocr_provider"] == "tesseract"
        mock_db.settings.update_one.assert_awaited_once()
        call_args = mock_db.settings.update_one.call_args
        assert call_args[0][1]["$set"]["integration_provider_preference.ocr"] == "tesseract"

    @pytest.mark.asyncio
    async def test_update_ocr_provider_rejects_invalid_value(self):
        from fastapi import HTTPException
        mock_db = self._mock_db()
        with patch("routers.invoices.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await update_ocr_provider_settings(
                    body=OCRProviderUpdate(ocr_provider="aws_textract"),
                    current_user=_user(), building_id=BUILDING_A,
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_ocr_provider_forbidden_for_tenant(self):
        from fastapi import HTTPException
        mock_db = self._mock_db()
        with patch("routers.invoices.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await update_ocr_provider_settings(
                    body=OCRProviderUpdate(ocr_provider="auto"),
                    current_user=_user(role="tenant"), building_id=BUILDING_A,
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_global_ocr_requires_super_admin(self):
        from fastapi import HTTPException
        mock_db = self._mock_db()
        with patch("routers.invoices.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await get_global_ocr_settings(
                    current_user=_user(role="strata_manager"), building_id=BUILDING_A
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_global_ocr_returns_default(self):
        mock_db = self._mock_db(global_pref="mindee")
        admin = _user(role="super_admin")
        admin["effective_role"] = "super_admin"
        with patch("routers.invoices.db", mock_db):
            result = await get_global_ocr_settings(
                current_user=admin, building_id=BUILDING_A
            )
        assert result["ocr_default_provider"] == "mindee"
        # 5, not 4: auto, claude_vision, mindee, tesseract, unlimited_ocr (GAP-OCR-001).
        assert len(result["available_providers"]) == 5

    @pytest.mark.asyncio
    async def test_update_global_ocr_writes_to_site_settings(self):
        mock_db = self._mock_db()
        admin = _user(role="super_admin")
        admin["effective_role"] = "super_admin"
        with patch("routers.invoices.db", mock_db):
            result = await update_global_ocr_settings(
                body=GlobalOCRUpdate(ocr_default_provider="claude_vision"),
                current_user=admin, building_id=BUILDING_A,
            )
        assert result["ocr_default_provider"] == "claude_vision"
        mock_db.site_settings.update_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_global_ocr_requires_super_admin(self):
        from fastapi import HTTPException
        mock_db = self._mock_db()
        with patch("routers.invoices.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await update_global_ocr_settings(
                    body=GlobalOCRUpdate(ocr_default_provider="auto"),
                    current_user=_user(role="chairman"), building_id=BUILDING_A,
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_provider_metadata_includes_cost_estimates(self):
        from routers.invoices import _provider_metadata
        providers = _provider_metadata(monthly_scans=10)
        ids = {p["id"] for p in providers}
        # unlimited_ocr (GAP-OCR-001) is a real 5th provider option — disabled/unavailable by
        # default (UNLIMITED_OCR_ENABLED=false) but still listed with its availability metadata.
        assert ids == {"auto", "claude_vision", "mindee", "tesseract", "unlimited_ocr"}
        tesseract = next(p for p in providers if p["id"] == "tesseract")
        assert tesseract["est_monthly_cost_aud"] == "Free"
        mindee = next(p for p in providers if p["id"] == "mindee")
        assert "AUD" in mindee["est_monthly_cost_aud"] or mindee["est_monthly_cost_aud"] == "Free"

    @pytest.mark.asyncio
    async def test_upload_uses_resolved_provider(self):
        """upload_and_parse must pass the resolved provider to parse_invoice."""
        set_ctx_building_id(BUILDING_A)
        stored = {**INVOICE_DOC, "ocr_source": "tesseract"}
        mock_file = MagicMock()
        mock_file.content_type = "application/pdf"
        mock_file.filename = "inv.pdf"
        mock_file.read = AsyncMock(return_value=FAKE_PDF)

        captured_provider = []

        async def _fake_parse(fb, ft, provider="auto"):
            captured_provider.append(provider)
            return {**PARSED_DATA, "ocr_source": provider}

        with patch("routers.invoices._resolve_ocr_provider", AsyncMock(return_value="tesseract")), \
                patch("routers.invoices.parse_invoice", side_effect=_fake_parse), \
                patch("routers.invoices.scan_upload", AsyncMock()), \
                patch("routers.invoices.repo.insert_invoice_record", AsyncMock(return_value=stored)):
            await upload_and_parse(
                file=mock_file, document_type="invoice",
                current_user=_user(), building_id=BUILDING_A,
            )

        assert captured_provider == ["tesseract"]
