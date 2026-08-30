"""
tests/backend/routers/test_ap_supplier_upload.py

Tests for routers/ap_supplier_upload.py — permission gate, file type
validation, size limit, OCR extraction, ABN validation, duplicate detection,
and draft invoice creation.
"""
import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from routers.ap_supplier_upload import supplier_upload, _ALLOWED_MIME, _MAX_FILE_BYTES
from integrations.envelopes import OCRExtractionResult, OCRFieldResult
from integrations.abn_validator import ABNValidationResult
from domain.duplicate_detection import DuplicateCheckResult

BUILDING_A = "13195"
BUILDING_B = "16244"

_SUPPLIER_USER = {
    "role": "service_provider",
    "effective_role": "service_provider",
    "email": "supplier@acme.com.au",
    "_id": "user-sp-001",
}
_OWNER_USER = {
    "role": "owner",
    "effective_role": "owner",
    "email": "owner@unit.com.au",
    "_id": "user-own-001",
}


def _make_upload_file(content: bytes = b"%PDF-1.4 fake content", content_type: str = "application/pdf"):
    f = MagicMock()
    f.content_type = content_type
    f.read = AsyncMock(return_value=content)
    f.filename = "invoice.pdf"
    return f


def _make_db(insert_id="abc123") -> MagicMock:
    mock_db = MagicMock()
    result = MagicMock()
    result.inserted_id = insert_id
    mock_db.invoice_documents.insert_one = AsyncMock(return_value=result)
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    mock_db.invoice_documents.find_one = AsyncMock(return_value=None)
    mock_db.invoice_documents.find.return_value = cursor
    mock_db.abn_validation_cache.find_one = AsyncMock(return_value=None)
    mock_db.abn_validation_cache.update_one = AsyncMock()
    return mock_db


def _mock_extraction(valid_abn: bool = True) -> OCRExtractionResult:
    abn = "51824753556" if valid_abn else "00000000000"
    return OCRExtractionResult(
        vendor_name=OCRFieldResult(value="Acme Pty Ltd", confidence=0.95),
        vendor_abn=OCRFieldResult(value=abn, confidence=0.92),
        invoice_number=OCRFieldResult(value="INV-2026-001", confidence=0.88),
        issue_date=OCRFieldResult(value="2026-04-01", confidence=0.90),
        due_date=OCRFieldResult(value="2026-04-30", confidence=0.85),
        total_cents=1_100_00,
        gst_cents=100_00,
        is_tax_invoice=True,
        raw_text="Invoice from Acme Pty Ltd",
        overall_confidence=0.90,
    )


def _mock_abn_result(is_valid=True, withholding=False) -> ABNValidationResult:
    return ABNValidationResult(
        abn="51824753556" if is_valid else "00000000000",
        is_valid=is_valid,
        entity_name="Acme Pty Ltd" if is_valid else None,
        gst_registered=is_valid,
        abn_status="Active" if is_valid else "InvalidFormat",
        cached=False,
        withholding_required=withholding,
    )


def _mock_dup_result(is_dup=False) -> DuplicateCheckResult:
    return DuplicateCheckResult(
        is_duplicate=is_dup, layer=None, existing_invoice_id=None, reason=None
    )


# ── Permission gate ───────────────────────────────────────────────────────────

class TestPermissionGate:
    @pytest.mark.asyncio
    async def test_owner_without_permission_returns_403(self):
        from fastapi import HTTPException
        from utils.permissions import Permission

        file = _make_upload_file()
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms:
            mock_perms.return_value = Permission(can_submit_supplier_invoice=False)
            with pytest.raises(HTTPException) as exc_info:
                await supplier_upload(
                    file=file,
                    current_user=_OWNER_USER,
                    building_id=BUILDING_A,
                )
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_service_provider_with_permission_allowed(self):
        from utils.permissions import Permission

        file = _make_upload_file()
        mock_db = _make_db()
        extraction = _mock_extraction()
        abn_result = _mock_abn_result()
        dup_result = _mock_dup_result()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice", new=AsyncMock(return_value=extraction)), \
                patch("integrations.abn_validator.validate_abn", new=AsyncMock(return_value=abn_result)), \
                patch("domain.duplicate_detection.check_duplicate", new=AsyncMock(return_value=dup_result)):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            result = await supplier_upload(
                file=file,
                current_user=_SUPPLIER_USER,
                building_id=BUILDING_A,
            )
        assert result.state == "draft"
        assert result.vendor_name == "Acme Pty Ltd"


# ── File type validation ──────────────────────────────────────────────────────

class TestFileTypeValidation:
    @pytest.mark.asyncio
    async def test_pdf_accepted(self):
        from utils.permissions import Permission

        file = _make_upload_file(content_type="application/pdf")
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice",
                      new=AsyncMock(return_value=_mock_extraction())), \
                patch("integrations.abn_validator.validate_abn",
                      new=AsyncMock(return_value=_mock_abn_result())), \
                patch("domain.duplicate_detection.check_duplicate",
                      new=AsyncMock(return_value=_mock_dup_result())):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            result = await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)
        assert result.invoice_id is not None

    @pytest.mark.asyncio
    async def test_unsupported_mime_rejected(self):
        from fastapi import HTTPException
        from utils.permissions import Permission

        file = _make_upload_file(content=b"<html>", content_type="text/html")
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms:
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            with pytest.raises(HTTPException) as exc_info:
                await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)
            assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_file_too_large_rejected(self):
        from fastapi import HTTPException
        from utils.permissions import Permission

        oversized = b"x" * (_MAX_FILE_BYTES + 1)
        file = _make_upload_file(content=oversized, content_type="application/pdf")
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms:
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            with pytest.raises(HTTPException) as exc_info:
                await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)
            assert exc_info.value.status_code == 413


# ── Duplicate detection ───────────────────────────────────────────────────────

class TestDuplicateDetection:
    @pytest.mark.asyncio
    async def test_duplicate_returns_409(self):
        from fastapi import HTTPException
        from utils.permissions import Permission
        from domain.duplicate_detection import DuplicateCheckResult

        file = _make_upload_file()
        mock_db = _make_db()

        dup = DuplicateCheckResult(
            is_duplicate=True, layer=1,
            existing_invoice_id="existing-001",
            reason="Exact duplicate",
        )

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice",
                      new=AsyncMock(return_value=_mock_extraction())), \
                patch("integrations.abn_validator.validate_abn",
                      new=AsyncMock(return_value=_mock_abn_result())), \
                patch("domain.duplicate_detection.check_duplicate", new=AsyncMock(return_value=dup)):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            with pytest.raises(HTTPException) as exc_info:
                await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)
            assert exc_info.value.status_code == 409
            assert "Duplicate" in exc_info.value.detail["message"]


# ── ABN withholding flag ──────────────────────────────────────────────────────

class TestWithholdingFlag:
    @pytest.mark.asyncio
    async def test_invalid_abn_sets_withholding_true(self):
        from utils.permissions import Permission

        file = _make_upload_file()
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice",
                      new=AsyncMock(return_value=_mock_extraction(valid_abn=False))), \
                patch("integrations.abn_validator.validate_abn",
                      new=AsyncMock(return_value=_mock_abn_result(is_valid=False, withholding=True))), \
                patch("domain.duplicate_detection.check_duplicate",
                      new=AsyncMock(return_value=_mock_dup_result())):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            result = await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)

        assert result.withholding_required is True
        assert result.abn_valid is False


# ── Draft document creation ───────────────────────────────────────────────────

class TestDraftCreation:
    @pytest.mark.asyncio
    async def test_draft_inserted_with_correct_fields(self):
        from utils.permissions import Permission

        file = _make_upload_file()
        mock_db = _make_db(insert_id="draft-oid-001")

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice",
                      new=AsyncMock(return_value=_mock_extraction())), \
                patch("integrations.abn_validator.validate_abn",
                      new=AsyncMock(return_value=_mock_abn_result())), \
                patch("domain.duplicate_detection.check_duplicate",
                      new=AsyncMock(return_value=_mock_dup_result())):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            result = await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)

        inserted = mock_db.invoice_documents.insert_one.call_args[0][0]
        assert inserted["state"] == "draft"
        assert inserted["tenant_id"] == BUILDING_A
        assert inserted["building_id"] == BUILDING_A
        assert inserted["submitted_by"] == _SUPPLIER_USER["email"]
        assert inserted["is_test_data"] is False
        assert "content_sha256" in inserted
        assert "transitions" in inserted

    @pytest.mark.asyncio
    async def test_result_includes_ocr_confidence(self):
        from utils.permissions import Permission

        file = _make_upload_file()
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice",
                      new=AsyncMock(return_value=_mock_extraction())), \
                patch("integrations.abn_validator.validate_abn",
                      new=AsyncMock(return_value=_mock_abn_result())), \
                patch("domain.duplicate_detection.check_duplicate",
                      new=AsyncMock(return_value=_mock_dup_result())):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            result = await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)

        assert result.overall_ocr_confidence == 0.90
        assert isinstance(result.low_confidence_fields, list)

    @pytest.mark.asyncio
    async def test_low_confidence_fields_flagged(self):
        """Fields with confidence < 0.70 appear in low_confidence_fields."""
        from utils.permissions import Permission

        low_conf_extraction = OCRExtractionResult(
            vendor_name=OCRFieldResult(value="Acme", confidence=0.95),
            vendor_abn=OCRFieldResult(value="51824753556", confidence=0.60),  # low
            invoice_number=OCRFieldResult(value="INV-001", confidence=0.55),  # low
            issue_date=OCRFieldResult(value="2026-04-01", confidence=0.90),
            due_date=OCRFieldResult(value="2026-04-30", confidence=0.85),
            total_cents=110_00, gst_cents=10_00,
            is_tax_invoice=True,
            raw_text="test", overall_confidence=0.70,
        )

        file = _make_upload_file()
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice",
                      new=AsyncMock(return_value=low_conf_extraction)), \
                patch("integrations.abn_validator.validate_abn",
                      new=AsyncMock(return_value=_mock_abn_result())), \
                patch("domain.duplicate_detection.check_duplicate",
                      new=AsyncMock(return_value=_mock_dup_result())):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            result = await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)

        assert "vendor_abn" in result.low_confidence_fields
        assert "invoice_number" in result.low_confidence_fields
        assert "vendor_name" not in result.low_confidence_fields


# ── Feature toggle gate ───────────────────────────────────────────────────────

class TestFeatureToggleGate:
    @pytest.mark.asyncio
    async def test_feature_disabled_returns_403(self):
        from fastapi import HTTPException
        from utils.permissions import require_feature

        checker = require_feature("ap_automation")
        with patch("utils.permissions.get_effective_feature_access",
                   new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc_info:
                await checker(current_user=_SUPPLIER_USER)
        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        # detail is now a structured dict: {"code": ..., "feature_key": "ap_automation", ...}
        assert "ap_automation" in (detail if isinstance(detail, str) else detail.get("feature_key", ""))

    @pytest.mark.asyncio
    async def test_feature_enabled_passes_through_user(self):
        from utils.permissions import require_feature

        checker = require_feature("ap_automation")
        with patch("utils.permissions.get_effective_feature_access",
                   new=AsyncMock(return_value=True)):
            result = await checker(current_user=_SUPPLIER_USER)
        assert result == _SUPPLIER_USER


# ── Multi-tenant isolation ────────────────────────────────────────────────────

class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_invoice_scoped_to_requesting_building(self):
        """Draft invoice must be scoped to the uploading user's building, not a hardcoded value."""
        from utils.permissions import Permission

        file = _make_upload_file()
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice",
                      new=AsyncMock(return_value=_mock_extraction())), \
                patch("integrations.abn_validator.validate_abn",
                      new=AsyncMock(return_value=_mock_abn_result())), \
                patch("domain.duplicate_detection.check_duplicate",
                      new=AsyncMock(return_value=_mock_dup_result())):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_A)

        inserted = mock_db.invoice_documents.insert_one.call_args[0][0]
        assert inserted["building_id"] == BUILDING_A
        assert inserted["tenant_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_building_b_scopes_to_building_b(self):
        """Same endpoint called for BUILDING_B must scope the invoice to BUILDING_B, not BUILDING_A."""
        from utils.permissions import Permission

        file = _make_upload_file()
        mock_db = _make_db()

        with patch("routers.ap_supplier_upload.db", mock_db), \
                patch("routers.ap_supplier_upload.get_user_permissions") as mock_perms, \
                patch("integrations.mocks.mock_ocr.MockOCR.extract_invoice",
                      new=AsyncMock(return_value=_mock_extraction())), \
                patch("integrations.abn_validator.validate_abn",
                      new=AsyncMock(return_value=_mock_abn_result())), \
                patch("domain.duplicate_detection.check_duplicate",
                      new=AsyncMock(return_value=_mock_dup_result())):
            mock_perms.return_value = Permission(can_submit_supplier_invoice=True)
            await supplier_upload(file=file, current_user=_SUPPLIER_USER, building_id=BUILDING_B)

        inserted = mock_db.invoice_documents.insert_one.call_args[0][0]
        assert inserted["building_id"] == BUILDING_B
        assert inserted["tenant_id"] == BUILDING_B
        assert inserted["building_id"] != BUILDING_A
