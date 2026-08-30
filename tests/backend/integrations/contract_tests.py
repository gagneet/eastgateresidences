"""
tests/backend/integrations/contract_tests.py — Provider contract tests.

Every mock provider is tested against the same contract. When a real provider
(Basiq, DEFT, Monoova, Xero, Textract) is added, it must pass all the same
tests before it can be registered in the ProviderRegistry.

These tests do NOT skip — they run in CI with no DB or external API needed.
All five protocol contracts are verified here:
  - BankFeedProvider  → CsvUploadBankFeed
  - BillerProvider    → MockBiller
  - PaymentInitiation → MockAbaWriter
  - AccountingProvider → MockAccountingSource
  - OCRProvider       → MockOCR

Usage:
    pytest tests/backend/integrations/contract_tests.py -v
"""
from pathlib import Path
from unittest.mock import patch

import pytest

_TEST_BUILDING = "16244"  # Sierra demo — never "13195"

# Path to the standard invoice template used by OCR tests, resolved relative to mock_ocr.py's
# own location rather than hardcoded, so it survives future moves.
from integrations.mocks import mock_ocr as _mock_ocr_module

_INVOICE_TEMPLATE = Path(_mock_ocr_module.__file__).resolve().parent / "invoice_templates" / "standard.txt"


# ── BankFeedProvider contract ─────────────────────────────────────────────────

class TestBankFeedContract:
    """CsvUploadBankFeed must pass all BankFeedProvider contract assertions."""

    def test_has_name_attribute(self):
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        p = CsvUploadBankFeed()
        assert isinstance(p.name, str) and len(p.name) > 0

    def test_name_matches_registry_key(self):
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        from integrations.registry import MOCK_BANK_FEED
        assert CsvUploadBankFeed().name == MOCK_BANK_FEED

    def test_name_is_csv_upload_bank_feed(self):
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        assert CsvUploadBankFeed().name == "csv_upload_bank_feed"

    @pytest.mark.asyncio
    async def test_verify_webhook_returns_bool(self):
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        result = await CsvUploadBankFeed().verify_webhook({}, b"")
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_verify_webhook_returns_false_for_mock(self):
        """CSV upload mock has no webhook — must always return False, never True."""
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        result = await CsvUploadBankFeed().verify_webhook(
            {"X-Signature": "bad-sig"}, b"payload"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_parse_webhook_returns_list(self):
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        result = await CsvUploadBankFeed().parse_webhook(b"")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_parse_webhook_returns_empty_for_mock(self):
        """CSV upload mock returns empty list from parse_webhook."""
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        result = await CsvUploadBankFeed().parse_webhook(b'{"transactions":[]}')
        assert result == []

    @pytest.mark.asyncio
    async def test_list_accounts_returns_list(self):
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        result = await CsvUploadBankFeed().list_accounts("any-consent-id")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_list_accounts_returns_empty_for_mock(self):
        """CSV upload mock has no remote accounts to enumerate."""
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        result = await CsvUploadBankFeed().list_accounts("consent-xyz")
        assert result == []

    def test_supported_banks_is_non_empty_list(self):
        """Contract: BankFeedProvider implementations expose supported_banks()."""
        from integrations.mocks.csv_upload_bank_feed import CsvUploadBankFeed
        banks = CsvUploadBankFeed().supported_banks()
        assert isinstance(banks, list) and len(banks) > 0


# ── BillerProvider contract ───────────────────────────────────────────────────

class TestBillerContract:
    """MockBiller must pass all BillerProvider contract assertions."""

    def test_has_name_attribute(self):
        from integrations.mocks.mock_biller import MockBiller
        assert isinstance(MockBiller().name, str) and len(MockBiller().name) > 0

    def test_name_matches_registry_key(self):
        from integrations.mocks.mock_biller import MockBiller
        from integrations.registry import MOCK_BILLER
        assert MockBiller().name == MOCK_BILLER

    def test_name_is_mock_biller(self):
        from integrations.mocks.mock_biller import MockBiller
        assert MockBiller().name == "mock_biller"

    def test_name_is_string(self):
        from integrations.mocks.mock_biller import MockBiller
        name = MockBiller().name
        assert isinstance(name, str)

    @pytest.mark.asyncio
    async def test_allocate_reference_returns_biller_reference(self):
        """allocate_reference must return a BillerReference with a valid CRN."""
        from unittest.mock import AsyncMock, MagicMock
        from integrations.mocks.mock_biller import MockBiller
        from integrations.envelopes import BillerReference

        mock_db = MagicMock()
        mock_db.mock_biller_allocations.find_one = AsyncMock(return_value=None)
        mock_db.mock_biller_allocations.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="x")
        )

        with (
            patch("database.db", mock_db),
            patch(
                "request_context.get_ctx_building_id",
                return_value=_TEST_BUILDING,
            ),
        ):
            ref = await MockBiller().allocate_reference(_TEST_BUILDING, "005", 1)

        assert isinstance(ref, BillerReference)
        assert len(ref.crn) == 13
        assert ref.crn.isdigit()


# ── PaymentInitiationProvider contract ───────────────────────────────────────

class TestPaymentInitiatorContract:
    """MockAbaWriter must pass all PaymentInitiationProvider contract assertions."""

    def test_has_name_attribute(self):
        from integrations.mocks.mock_aba_writer import MockAbaWriter
        assert isinstance(MockAbaWriter().name, str) and len(MockAbaWriter().name) > 0

    def test_name_matches_registry_key(self):
        from integrations.mocks.mock_aba_writer import MockAbaWriter
        from integrations.registry import MOCK_ABA_WRITER
        assert MockAbaWriter().name == MOCK_ABA_WRITER

    def test_name_is_mock_aba_writer(self):
        from integrations.mocks.mock_aba_writer import MockAbaWriter
        assert MockAbaWriter().name == "mock_aba_writer"

    @pytest.mark.asyncio
    async def test_cancel_payment_returns_bool(self):
        from integrations.mocks.mock_aba_writer import MockAbaWriter
        result = await MockAbaWriter().cancel_payment("any-ref")
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_cancel_payment_returns_false(self):
        """Mock ABA writer cannot cancel — must return False, never raise."""
        from integrations.mocks.mock_aba_writer import MockAbaWriter
        result = await MockAbaWriter().cancel_payment("run-uuid-001")
        assert result is False


# ── AccountingProvider contract ───────────────────────────────────────────────

class TestAccountingContract:
    """MockAccountingSource must pass all AccountingProvider contract assertions."""

    def test_has_name_attribute(self):
        from integrations.mocks.mock_accounting_source import MockAccountingSource
        assert isinstance(MockAccountingSource().name, str)

    def test_name_matches_registry_key(self):
        from integrations.mocks.mock_accounting_source import MockAccountingSource
        from integrations.registry import MOCK_ACCOUNTING
        assert MockAccountingSource().name == MOCK_ACCOUNTING

    def test_name_is_mock_accounting_source(self):
        from integrations.mocks.mock_accounting_source import MockAccountingSource
        assert MockAccountingSource().name == "mock_accounting_source"

    @pytest.mark.asyncio
    async def test_verify_webhook_returns_bool(self):
        from integrations.mocks.mock_accounting_source import MockAccountingSource
        result = await MockAccountingSource().verify_webhook({}, b"")
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_verify_webhook_returns_false_for_mock(self):
        """Mock accounting source has no real webhook verification."""
        from integrations.mocks.mock_accounting_source import MockAccountingSource
        result = await MockAccountingSource().verify_webhook(
            {"X-Xero-Signature": "abc"}, b"body"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_parse_webhook_returns_list(self):
        from integrations.mocks.mock_accounting_source import MockAccountingSource
        result = await MockAccountingSource().parse_webhook(b"")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_parse_webhook_returns_empty_for_mock(self):
        """Mock accounting source parse_webhook returns []."""
        from integrations.mocks.mock_accounting_source import MockAccountingSource
        result = await MockAccountingSource().parse_webhook(b'{"events":[]}')
        assert result == []


# ── OCRProvider contract ──────────────────────────────────────────────────────

class TestOCRContract:
    """MockOCR must pass all OCRProvider contract assertions."""

    def test_has_name_attribute(self):
        from integrations.mocks.mock_ocr import MockOCR
        assert isinstance(MockOCR().name, str) and len(MockOCR().name) > 0

    def test_name_matches_registry_key(self):
        from integrations.mocks.mock_ocr import MockOCR
        from integrations.registry import MOCK_OCR
        assert MockOCR().name == MOCK_OCR

    def test_name_is_mock_ocr(self):
        from integrations.mocks.mock_ocr import MockOCR
        assert MockOCR().name == "mock_ocr"

    @pytest.mark.asyncio
    async def test_extract_bank_statement_empty_bytes_returns_list(self):
        """extract_bank_statement with empty bytes returns [] — never raises."""
        from integrations.mocks.mock_ocr import MockOCR
        with patch("request_context.get_ctx_building_id", return_value=_TEST_BUILDING):
            result = await MockOCR().extract_bank_statement(b"")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_extract_bank_statement_returns_list_type(self):
        """Contract: extract_bank_statement must return list[BankTxObserved] or []."""
        from integrations.mocks.mock_ocr import MockOCR
        with patch("request_context.get_ctx_building_id", return_value=_TEST_BUILDING):
            result = await MockOCR().extract_bank_statement(b"not-valid-csv")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_extract_invoice_returns_ocr_result(self):
        """extract_invoice must return an OCRExtractionResult instance."""
        from integrations.mocks.mock_ocr import MockOCR
        from integrations.envelopes import OCRExtractionResult

        assert _INVOICE_TEMPLATE.exists(), (
            f"Standard invoice template not found: {_INVOICE_TEMPLATE}"
        )
        invoice_bytes = _INVOICE_TEMPLATE.read_bytes()
        result = await MockOCR().extract_invoice(invoice_bytes, "text/plain")

        assert isinstance(result, OCRExtractionResult)

    @pytest.mark.asyncio
    async def test_extract_invoice_provider_field(self):
        """OCRExtractionResult.provider must be 'mock_ocr'."""
        from integrations.mocks.mock_ocr import MockOCR

        invoice_bytes = _INVOICE_TEMPLATE.read_bytes()
        result = await MockOCR().extract_invoice(invoice_bytes, "text/plain")
        assert result.provider == "mock_ocr"

    @pytest.mark.asyncio
    async def test_extract_invoice_confidence_in_range(self):
        """overall_confidence must be in [0.0, 1.0]."""
        from integrations.mocks.mock_ocr import MockOCR

        invoice_bytes = _INVOICE_TEMPLATE.read_bytes()
        result = await MockOCR().extract_invoice(invoice_bytes, "text/plain")
        assert 0.0 <= result.overall_confidence <= 1.0

    @pytest.mark.asyncio
    async def test_extract_invoice_is_deterministic(self):
        """Calling extract_invoice twice with the same bytes yields the same result."""
        from integrations.mocks.mock_ocr import MockOCR

        invoice_bytes = (
            b"TAX INVOICE\nABC Pty Ltd\nABN: 47 123 456 789\n"
            b"Invoice No: INV-001\nTotal: $1,100.00\nGST: $100.00"
        )
        r1 = await MockOCR().extract_invoice(invoice_bytes, "text/plain")
        r2 = await MockOCR().extract_invoice(invoice_bytes, "text/plain")

        assert r1.vendor_abn.value == r2.vendor_abn.value
        assert r1.total_cents == r2.total_cents
        assert r1.overall_confidence == r2.overall_confidence
        assert r1.provider == r2.provider

    @pytest.mark.asyncio
    async def test_extract_invoice_tax_invoice_detected(self):
        """'TAX INVOICE' marker in the document sets is_tax_invoice=True."""
        from integrations.mocks.mock_ocr import MockOCR

        invoice_bytes = _INVOICE_TEMPLATE.read_bytes()
        result = await MockOCR().extract_invoice(invoice_bytes, "text/plain")
        assert result.is_tax_invoice is True

    @pytest.mark.asyncio
    async def test_extract_invoice_standard_template_abn(self):
        """ABN '47 123 456 789' is extracted from the standard invoice template."""
        from integrations.mocks.mock_ocr import MockOCR

        invoice_bytes = _INVOICE_TEMPLATE.read_bytes()
        result = await MockOCR().extract_invoice(invoice_bytes, "text/plain")
        assert result.vendor_abn.value == "47123456789"

    @pytest.mark.asyncio
    async def test_extract_invoice_standard_template_total(self):
        """Total $2,937.00 → total_cents=293700 from the standard invoice template."""
        from integrations.mocks.mock_ocr import MockOCR

        invoice_bytes = _INVOICE_TEMPLATE.read_bytes()
        result = await MockOCR().extract_invoice(invoice_bytes, "text/plain")
        assert result.total_cents == 293700

    @pytest.mark.asyncio
    async def test_extract_invoice_all_money_fields_are_int(self):
        """Contract: all monetary fields in OCRExtractionResult must be int cents, not float."""
        from integrations.mocks.mock_ocr import MockOCR

        invoice_bytes = _INVOICE_TEMPLATE.read_bytes()
        result = await MockOCR().extract_invoice(invoice_bytes, "text/plain")

        for field_name in ("total_cents", "gst_cents", "subtotal_cents"):
            value = getattr(result, field_name)
            if value is not None:
                assert isinstance(value, int), (
                    f"{field_name}={value!r} must be int, not {type(value).__name__}"
                )

    @pytest.mark.asyncio
    async def test_extract_invoice_empty_bytes_does_not_raise(self):
        """extract_invoice with empty bytes returns a result — never raises."""
        from integrations.mocks.mock_ocr import MockOCR

        result = await MockOCR().extract_invoice(b"", "text/plain")
        # Result must be an OCRExtractionResult regardless of empty input
        from integrations.envelopes import OCRExtractionResult
        assert isinstance(result, OCRExtractionResult)
        assert result.provider == "mock_ocr"
