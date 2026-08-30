#!/usr/bin/env python3
"""
Unit Tests: generate_purchase_order_pdf and generate_invoice_pdf

Tests that:
  - building_settings.strata_address is used as the PDF footer (not hardcoded)
  - building_address is accepted as a fallback when strata_address is absent
  - No address in footer when building_settings is None or empty
  - Multi-tenant: Sierra (16244) and East Gate (13195) each get their own address
  - XSS-safe: addresses containing HTML special chars are properly escaped
  - generate_invoice_pdf behaves identically for address resolution
  - Both functions return bytes when PDF_AVAILABLE=True
  - Neither function raises BuildingSettingsIncompleteError (reserved for legal docs)

No live backend or DB required — pure unit tests.

Run:
    cd /home/gagneet/strata-management
    backend/venv/bin/python3 -m pytest tests/backend/test_pdf_generator_po_invoice.py -v
"""

import io
import sys
import os
import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from config import PDF_AVAILABLE
from utils.pdf_generator import generate_purchase_order_pdf, generate_invoice_pdf, BuildingSettingsIncompleteError

pytestmark = pytest.mark.skipif(not PDF_AVAILABLE, reason="reportlab not installed")


def _pdf_text(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF using pdfminer."""
    from pdfminer.high_level import extract_text
    return extract_text(io.BytesIO(pdf_bytes))


# ── Shared test data ──────────────────────────────────────────────────────────

EAST_GATE_SETTINGS = {
    "building_id": "13195",
    "building_name": "East Gate Residences",
    "strata_address": "14 Hoolihan Street, Denman Prospect ACT 2914",
    "plan_number": "13195",
    "building_abn": "12 345 678 901",
    "gst_registered": True,
    "levy_gst_rate": 0.10,
}

SIERRA_SETTINGS = {
    "building_id": "16244",
    "building_name": "Sierra Gungahlin",
    "strata_address": "2 Burrumarra Avenue, Gungahlin ACT 2912",
    "plan_number": "16244",
    "building_abn": "98 765 432 109",
    "gst_registered": True,
    "levy_gst_rate": 0.10,
}

HARBOURVIEW_SETTINGS = {
    "building_id": "18932",
    "building_name": "Harbourview Residences",
    "strata_address": "1 Marina Crescent, Tuggeranong ACT 2900",
    "plan_number": "18932",
    "gst_registered": False,
    "levy_gst_rate": 0.0,
}


def _make_po(po_number="PO-2026-001", amount=1000.0):
    return {
        "id": "po-test-001",
        "po_number": po_number,
        "description": "Replace common area lighting",
        "amount": amount,
        "total_amount": amount * 1.1,
        "created_at": "2026-04-01T10:00:00",
        "due_date": "2026-04-30",
    }


def _make_contractor():
    return {
        "id": "c-001",
        "name": "Acme Electrical Pty Ltd",
        "abn": "11 222 333 444",
        "phone": "02 6123 4567",
        "email": "works@acmeelectrical.com.au",
    }


def _make_invoice(amount=1000.0):
    return {
        "id": "inv-test-001",
        "invoice_number": "INV-2026-001",
        "purchase_order_id": "po-test-001",
        "contractor_id": "c-001",
        "amount": amount,
        "gst_amount": amount * 0.1,
        "total_amount": amount * 1.1,
        "status": "pending",
        "notes": "First invoice for lighting replacement",
        "created_at": "2026-04-15T10:00:00",
    }


# ── generate_purchase_order_pdf ───────────────────────────────────────────────

class TestPurchaseOrderPdfStructure:
    """PDF is generated and has correct format."""

    def test_returns_bytes(self):
        pdf = generate_purchase_order_pdf(_make_po(), _make_contractor())
        assert isinstance(pdf, bytes)
        assert len(pdf) > 1000

    def test_pdf_magic_bytes(self):
        pdf = generate_purchase_order_pdf(_make_po(), _make_contractor())
        assert pdf[:4] == b"%PDF"

    def test_no_crash_when_building_settings_none(self):
        pdf = generate_purchase_order_pdf(_make_po(), _make_contractor(), building_settings=None)
        assert isinstance(pdf, bytes)

    def test_no_crash_when_building_settings_empty(self):
        pdf = generate_purchase_order_pdf(_make_po(), _make_contractor(), building_settings={})
        assert isinstance(pdf, bytes)

    def test_does_not_raise_building_settings_incomplete_error(self):
        """Operational PDFs must never raise — blank footer is acceptable."""
        try:
            pdf = generate_purchase_order_pdf(
                _make_po(), _make_contractor(),
                building_settings={"building_id": "13195"},  # no strata_address
            )
            assert isinstance(pdf, bytes)
        except BuildingSettingsIncompleteError:
            pytest.fail("generate_purchase_order_pdf must not raise BuildingSettingsIncompleteError")


class TestPurchaseOrderPdfAddressResolution:
    """strata_address flows from building_settings into the PDF footer."""

    def test_east_gate_address_in_footer(self):
        pdf = generate_purchase_order_pdf(
            _make_po(), _make_contractor(),
            building_settings=EAST_GATE_SETTINGS,
        )
        text = _pdf_text(pdf)
        assert "14 Hoolihan Street" in text or "Denman Prospect" in text

    def test_sierra_address_in_footer(self):
        pdf = generate_purchase_order_pdf(
            _make_po(), _make_contractor(),
            building_settings=SIERRA_SETTINGS,
        )
        text = _pdf_text(pdf)
        assert "Gungahlin" in text or "Burrumarra" in text

    def test_harbourview_address_in_footer(self):
        pdf = generate_purchase_order_pdf(
            _make_po(), _make_contractor(),
            building_settings=HARBOURVIEW_SETTINGS,
        )
        text = _pdf_text(pdf)
        assert "Tuggeranong" in text or "Marina Crescent" in text

    def test_building_address_fallback_when_strata_address_absent(self):
        """building_address is used when strata_address key is missing."""
        settings = {
            "building_id": "16244",
            "building_address": "99 Fallback Road, Gungahlin ACT 2912",
        }
        pdf = generate_purchase_order_pdf(_make_po(), _make_contractor(), building_settings=settings)
        text = _pdf_text(pdf)
        assert "99 Fallback Road" in text

    def test_empty_footer_when_no_address_provided(self):
        """With building_settings={} the footer must be blank — not crash."""
        pdf = generate_purchase_order_pdf(_make_po(), _make_contractor(), building_settings={})
        assert isinstance(pdf, bytes)
        # No address should appear from a previous test's building settings
        text = _pdf_text(pdf)
        assert "Denman Prospect" not in text
        assert "Gungahlin" not in text


class TestPurchaseOrderPdfMultiTenant:
    """Two buildings produce PDFs with their own addresses — not cross-contaminated."""

    def test_east_gate_and_sierra_produce_distinct_pdfs(self):
        pdf_eg = generate_purchase_order_pdf(
            _make_po("PO-EG-001"), _make_contractor(),
            building_settings=EAST_GATE_SETTINGS,
        )
        pdf_si = generate_purchase_order_pdf(
            _make_po("PO-SI-001"), _make_contractor(),
            building_settings=SIERRA_SETTINGS,
        )
        assert pdf_eg != pdf_si

    def test_sierra_pdf_does_not_contain_east_gate_address(self):
        text = _pdf_text(generate_purchase_order_pdf(
            _make_po(), _make_contractor(), building_settings=SIERRA_SETTINGS,
        ))
        assert "Denman Prospect" not in text
        assert "Hoolihan" not in text

    def test_east_gate_pdf_does_not_contain_sierra_address(self):
        text = _pdf_text(generate_purchase_order_pdf(
            _make_po(), _make_contractor(), building_settings=EAST_GATE_SETTINGS,
        ))
        assert "Gungahlin" not in text
        assert "Burrumarra" not in text

    def test_all_three_buildings_return_valid_pdf(self):
        for settings in [EAST_GATE_SETTINGS, SIERRA_SETTINGS, HARBOURVIEW_SETTINGS]:
            pdf = generate_purchase_order_pdf(_make_po(), _make_contractor(), building_settings=settings)
            assert pdf[:4] == b"%PDF", f"Invalid PDF for building {settings['building_id']}"


class TestPurchaseOrderPdfContent:
    """Core PO content fields appear in the rendered text."""

    def test_po_number_in_pdf(self):
        text = _pdf_text(generate_purchase_order_pdf(
            _make_po("PO-TEST-999"), _make_contractor(),
            building_settings=EAST_GATE_SETTINGS,
        ))
        assert "PO-TEST-999" in text

    def test_contractor_name_in_pdf(self):
        text = _pdf_text(generate_purchase_order_pdf(
            _make_po(), _make_contractor(), building_settings=EAST_GATE_SETTINGS,
        ))
        assert "Acme Electrical" in text

    def test_gst_not_registered_produces_zero_gst(self):
        text = _pdf_text(generate_purchase_order_pdf(
            _make_po(amount=1000.0),
            _make_contractor(),
            gst_rate=0.10,
            gst_registered=False,
            building_settings=HARBOURVIEW_SETTINGS,  # gst_registered=False building
        ))
        assert "$0.00" in text

    def test_xss_safe_building_name(self):
        """Building name with HTML special characters must not crash."""
        po = _make_po()
        contractor = _make_contractor()
        settings = {
            "building_id": "99999",
            "strata_address": "Test Address & Co <Suite 1>, ACT",
        }
        pdf = generate_purchase_order_pdf(po, contractor, building_settings=settings)
        assert isinstance(pdf, bytes)
        # Extracted text will have & not &amp; — that's fine, the escaping protects the XML layer
        text = _pdf_text(pdf)
        assert "Test Address" in text


# ── generate_invoice_pdf ──────────────────────────────────────────────────────

class TestInvoicePdfStructure:
    def test_returns_bytes(self):
        pdf = generate_invoice_pdf(_make_invoice(), _make_po(), _make_contractor())
        assert isinstance(pdf, bytes)

    def test_pdf_magic_bytes(self):
        assert generate_invoice_pdf(_make_invoice(), _make_po(), _make_contractor())[:4] == b"%PDF"

    def test_no_crash_when_building_settings_none(self):
        pdf = generate_invoice_pdf(_make_invoice(), _make_po(), _make_contractor(), building_settings=None)
        assert isinstance(pdf, bytes)

    def test_no_crash_when_building_settings_empty(self):
        pdf = generate_invoice_pdf(_make_invoice(), _make_po(), _make_contractor(), building_settings={})
        assert isinstance(pdf, bytes)

    def test_does_not_raise_building_settings_incomplete_error(self):
        try:
            pdf = generate_invoice_pdf(
                _make_invoice(), _make_po(), _make_contractor(),
                building_settings={"building_id": "13195"},
            )
            assert isinstance(pdf, bytes)
        except BuildingSettingsIncompleteError:
            pytest.fail("generate_invoice_pdf must not raise BuildingSettingsIncompleteError")

    def test_missing_po_does_not_crash(self):
        """PO may be {} if deleted — must not crash."""
        pdf = generate_invoice_pdf(_make_invoice(), {}, _make_contractor(), building_settings=EAST_GATE_SETTINGS)
        assert isinstance(pdf, bytes)

    def test_missing_contractor_does_not_crash(self):
        pdf = generate_invoice_pdf(_make_invoice(), _make_po(), {}, building_settings=EAST_GATE_SETTINGS)
        assert isinstance(pdf, bytes)


class TestInvoicePdfAddressResolution:
    def test_east_gate_address_in_footer(self):
        text = _pdf_text(generate_invoice_pdf(
            _make_invoice(), _make_po(), _make_contractor(),
            building_settings=EAST_GATE_SETTINGS,
        ))
        assert "14 Hoolihan Street" in text or "Denman Prospect" in text

    def test_sierra_address_in_footer(self):
        text = _pdf_text(generate_invoice_pdf(
            _make_invoice(), _make_po(), _make_contractor(),
            building_settings=SIERRA_SETTINGS,
        ))
        assert "Gungahlin" in text or "Burrumarra" in text

    def test_building_address_fallback(self):
        settings = {"building_id": "16244", "building_address": "99 Fallback Road, Gungahlin"}
        text = _pdf_text(generate_invoice_pdf(
            _make_invoice(), _make_po(), _make_contractor(), building_settings=settings,
        ))
        assert "99 Fallback Road" in text

    def test_empty_footer_when_no_address(self):
        pdf = generate_invoice_pdf(_make_invoice(), _make_po(), _make_contractor(), building_settings={})
        text = _pdf_text(pdf)
        assert "Denman Prospect" not in text
        assert "Gungahlin" not in text


class TestInvoicePdfMultiTenant:
    def test_sierra_pdf_does_not_contain_east_gate_address(self):
        text = _pdf_text(generate_invoice_pdf(
            _make_invoice(), _make_po(), _make_contractor(), building_settings=SIERRA_SETTINGS,
        ))
        assert "Denman Prospect" not in text
        assert "Hoolihan" not in text

    def test_east_gate_pdf_does_not_contain_sierra_address(self):
        text = _pdf_text(generate_invoice_pdf(
            _make_invoice(), _make_po(), _make_contractor(), building_settings=EAST_GATE_SETTINGS,
        ))
        assert "Gungahlin" not in text

    def test_all_three_buildings_produce_distinct_pdfs(self):
        pdfs = [
            generate_invoice_pdf(
                _make_invoice(), _make_po(), _make_contractor(), building_settings=s,
            )
            for s in [EAST_GATE_SETTINGS, SIERRA_SETTINGS, HARBOURVIEW_SETTINGS]
        ]
        assert all(p[:4] == b"%PDF" for p in pdfs)
        assert pdfs[0] != pdfs[1]
        assert pdfs[1] != pdfs[2]


class TestInvoicePdfContent:
    def test_invoice_number_in_pdf(self):
        text = _pdf_text(generate_invoice_pdf(
            _make_invoice(), _make_po(), _make_contractor(), building_settings=EAST_GATE_SETTINGS,
        ))
        assert "INV-2026-001" in text

    def test_notes_appear_when_present(self):
        invoice = _make_invoice()
        invoice["notes"] = "Lighting replacement completed phase one"
        text = _pdf_text(generate_invoice_pdf(
            invoice, _make_po(), _make_contractor(), building_settings=EAST_GATE_SETTINGS,
        ))
        assert "Lighting replacement completed" in text

    def test_empty_notes_does_not_crash(self):
        invoice = _make_invoice()
        invoice["notes"] = ""
        pdf = generate_invoice_pdf(invoice, _make_po(), _make_contractor(), building_settings=EAST_GATE_SETTINGS)
        assert isinstance(pdf, bytes)
