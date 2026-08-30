"""
Tests for utils/file_scan.py — Magika-based upload security barrier.

Covers:
  - BLOCKED_LABELS gate (dangerous types always rejected)
  - Per-context allowlist gate
  - Size limit enforcement
  - Empty file rejection
  - Fail-open behaviour when Magika unavailable
  - scan_upload integration for each context
"""
from unittest.mock import patch

import pytest

from utils.file_scan import (
    BLOCKED_LABELS,
    CONTEXT_ALLOWLISTS,
    ScanResult,
    scan_upload,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_scan_result(label: str, mime: str = "application/octet-stream", score: float = 0.99) -> ScanResult:
    return ScanResult(label=label, mime_type=mime, score=score, group="test")


def _mock_scan(label: str, mime: str = "application/octet-stream"):
    """Patch _scan_sync to return a fixed ScanResult."""
    return patch(
        "utils.file_scan._scan_sync",
        return_value=_make_scan_result(label, mime),
    )


# ── BLOCKED_LABELS set ────────────────────────────────────────────────────────

class TestBlockedLabels:
    def test_executable_labels_blocked(self):
        for label in ("pe", "elf", "macho", "dex", "apk", "jar", "class"):
            assert label in BLOCKED_LABELS, f"'{label}' should be in BLOCKED_LABELS"

    def test_script_labels_blocked(self):
        for label in ("python", "perl", "php", "shell", "powershell", "batch", "vba"):
            assert label in BLOCKED_LABELS, f"'{label}' should be in BLOCKED_LABELS"

    def test_web_script_labels_blocked(self):
        for label in ("javascript", "typescript"):
            assert label in BLOCKED_LABELS, f"'{label}' should be in BLOCKED_LABELS"

    def test_sqlite_blocked(self):
        assert "sqlite" in BLOCKED_LABELS

    def test_safe_labels_not_blocked(self):
        for label in ("pdf", "docx", "xlsx", "jpeg", "png", "csv", "txt"):
            assert label not in BLOCKED_LABELS, f"'{label}' must NOT be in BLOCKED_LABELS"


# ── Context allowlists ────────────────────────────────────────────────────────

class TestContextAllowlists:
    def test_all_contexts_defined(self):
        for ctx in ("document", "image", "csv", "pdf", "converter"):
            assert ctx in CONTEXT_ALLOWLISTS

    def test_document_allows_common_types(self):
        al = CONTEXT_ALLOWLISTS["document"]
        for label in ("pdf", "docx", "xlsx", "jpeg", "png", "txt", "csv"):
            assert label in al

    def test_image_rejects_non_images(self):
        al = CONTEXT_ALLOWLISTS["image"]
        for label in ("pdf", "csv", "txt", "docx"):
            assert label not in al

    def test_image_allows_image_types(self):
        al = CONTEXT_ALLOWLISTS["image"]
        for label in ("jpeg", "png", "gif", "webp", "bmp", "tiff", "heic"):
            assert label in al

    def test_pdf_context_only_allows_pdf(self):
        al = CONTEXT_ALLOWLISTS["pdf"]
        assert al == frozenset({"pdf"})

    def test_csv_context_allows_text_variants(self):
        al = CONTEXT_ALLOWLISTS["csv"]
        for label in ("csv", "tsv", "txt", "unknown"):
            assert label in al

    def test_converter_allows_broad_set(self):
        al = CONTEXT_ALLOWLISTS["converter"]
        for label in ("pdf", "docx", "xlsx", "html", "csv", "markdown", "jpeg"):
            assert label in al

    def test_no_context_allows_dangerous_labels(self):
        """No dangerous label must ever appear in any context allowlist."""
        for label in BLOCKED_LABELS:
            for ctx, al in CONTEXT_ALLOWLISTS.items():
                assert label not in al, (
                    f"Dangerous label '{label}' must not appear in '{ctx}' allowlist"
                )


# ── scan_upload: size gate ────────────────────────────────────────────────────

class TestSizeGate:
    @pytest.mark.asyncio
    async def test_empty_file_rejected(self):
        with pytest.raises(Exception) as exc_info:
            await scan_upload(b"", context="document")
        assert "Empty" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_oversized_file_rejected(self):
        big = b"x" * (26 * 1024 * 1024)  # 26 MB > 25 MB document limit
        with patch("utils.file_scan._scan_sync", return_value=None):
            with pytest.raises(Exception) as exc_info:
                await scan_upload(big, context="document")
        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_custom_size_limit_enforced(self):
        content = b"x" * (6 * 1024 * 1024)  # 6 MB
        with pytest.raises(Exception) as exc_info:
            await scan_upload(content, context="csv", max_size_bytes=5 * 1024 * 1024)
        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_file_at_limit_passes_size_check(self):
        content = b"a,b,c\n1,2,3\n"
        with _mock_scan("csv"):
            result = await scan_upload(content, context="csv", max_size_bytes=10 * 1024 * 1024)
        assert result.label == "csv"


# ── scan_upload: blocked-label gate ──────────────────────────────────────────

class TestBlockedLabelGate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("label", [
        "python", "shell", "pe", "elf", "powershell", "batch", "php", "javascript", "vba"
    ])
    async def test_blocked_label_raises_400(self, label):
        with _mock_scan(label):
            with pytest.raises(Exception) as exc_info:
                await scan_upload(b"some content", context="document")
        assert exc_info.value.status_code == 400
        assert "not permitted" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_blocked_label_rejected_in_all_contexts(self):
        """A dangerous label is rejected even in the most permissive context."""
        for ctx in CONTEXT_ALLOWLISTS:
            with _mock_scan("python"):
                with pytest.raises(Exception) as exc_info:
                    await scan_upload(b"content", context=ctx)
            assert exc_info.value.status_code == 400


# ── scan_upload: context allowlist gate ──────────────────────────────────────

class TestContextAllowlistGate:
    @pytest.mark.asyncio
    async def test_pdf_in_document_context_passes(self):
        with _mock_scan("pdf", "application/pdf"):
            result = await scan_upload(b"%PDF-1.4 content", context="document")
        assert result.label == "pdf"

    @pytest.mark.asyncio
    async def test_non_pdf_in_pdf_context_rejected(self):
        with _mock_scan("docx", "application/vnd.openxmlformats"):
            with pytest.raises(Exception) as exc_info:
                await scan_upload(b"PK\x03\x04", context="pdf")
        assert exc_info.value.status_code == 400
        assert "detected" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_svg_in_image_context_rejected(self):
        """SVG is in converter/document but NOT in image context."""
        assert "svg" not in CONTEXT_ALLOWLISTS["image"]
        with _mock_scan("svg", "image/svg+xml"):
            with pytest.raises(Exception) as exc_info:
                await scan_upload(b"<svg/>", context="image")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_in_csv_context_passes(self):
        """CSVs with unusual encoding may come back as 'unknown' — allowed in csv context."""
        with _mock_scan("unknown"):
            result = await scan_upload(b"col1,col2\nval1,val2", context="csv")
        assert result.label == "unknown"

    @pytest.mark.asyncio
    async def test_unknown_in_pdf_context_rejected(self):
        """Unknown type must NOT pass a strict pdf context."""
        with _mock_scan("unknown"):
            with pytest.raises(Exception) as exc_info:
                await scan_upload(b"\x00\x00\x00\x00", context="pdf")
        assert exc_info.value.status_code == 400

    def test_html_allowed_in_converter_not_document(self):
        assert "html" in CONTEXT_ALLOWLISTS["converter"]
        assert "html" not in CONTEXT_ALLOWLISTS["document"]


# ── scan_upload: fail-open when Magika unavailable ────────────────────────────

class TestFailOpen:
    @pytest.mark.asyncio
    async def test_magika_unavailable_passes_with_size_check(self):
        """When Magika can't scan, upload is still allowed (fail-open) but size checked."""
        with patch("utils.file_scan._scan_sync", return_value=None):
            result = await scan_upload(b"normal content", context="document")
        assert result.label == "unknown"
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_magika_unavailable_still_blocks_oversized(self):
        big = b"x" * (26 * 1024 * 1024)
        with patch("utils.file_scan._scan_sync", return_value=None):
            with pytest.raises(Exception) as exc_info:
                await scan_upload(big, context="document")
        assert exc_info.value.status_code == 413


# ── scan_upload: extension-spoofing scenario ─────────────────────────────────

class TestExtensionSpoofing:
    @pytest.mark.asyncio
    async def test_script_disguised_as_pdf_is_blocked(self):
        """A .pdf file containing shell script must be blocked by content detection."""
        with _mock_scan("shell", "text/x-shellscript"):
            with pytest.raises(Exception) as exc_info:
                await scan_upload(
                    b"#!/bin/bash\nrm -rf /", context="document", filename="report.pdf"
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_exe_disguised_as_docx_is_blocked(self):
        with _mock_scan("pe", "application/x-msdownload"):
            with pytest.raises(Exception) as exc_info:
                await scan_upload(b"MZ\x90\x00", context="document", filename="invoice.docx")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_real_pdf_passes_despite_pdf_extension(self):
        with _mock_scan("pdf", "application/pdf"):
            result = await scan_upload(b"%PDF-1.4", context="pdf", filename="statement.pdf")
        assert result.label == "pdf"


# ── ScanResult dataclass ──────────────────────────────────────────────────────

class TestScanResult:
    def test_scanresult_fields(self):
        r = ScanResult(label="pdf", mime_type="application/pdf", score=0.99, group="document")
        assert r.label == "pdf"
        assert r.mime_type == "application/pdf"
        assert r.score == 0.99
        assert r.group == "document"
