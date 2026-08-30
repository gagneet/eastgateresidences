"""
Tests for the Document Converter router (MarkItDown + pdfplumber enhancements).

Coverage:
  TestSupportedFormats      — GET /supported-formats: schema, PDF notes, size limit
  TestFileConversion        — happy paths: txt, csv, char/line counts, extension passthrough
  TestPdfFallback           — pdfplumber fallback triggers / scanned-PDF detection
  TestFileConversionErrors  — 400 bad ext, 413 oversized, 422 exception, temp cleanup
  TestUrlConversion         — valid URL, http/https, bad scheme, 422, optional filename
  TestHistory               — GET /history: manager sees all, owner sees own, empty state
  TestMultiTenantAuth       — building 13195, building 16244, unauthenticated rejection

All tests: unit-level (AsyncMock + patch), no live backend required, fully idempotent.
"""

import io
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteTestClient

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BUILDING_13195 = "13195"
BUILDING_16244 = "16244"

USER_OWNER = {
    "id": "user-owner-001",
    "role": "owner",
    "effective_role": None,
    "building_id": BUILDING_13195,
}
USER_EC = {
    "id": "user-ec-001",
    "role": "owner",
    "effective_role": "ec_member",  # elevated via effective_role
    "building_id": BUILDING_13195,
}
USER_SUPER = {
    "id": "user-sa-001",
    "role": "super_admin",
    "effective_role": None,
    "building_id": BUILDING_13195,
}


def _make_app(building_id=BUILDING_13195, user=None):
    """Minimal FastAPI app with only the document_converter router, auth stubbed out."""
    from routers.document_converter import router
    from utils.auth import get_current_building, get_current_user

    if user is None:
        user = USER_OWNER

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_building] = lambda: building_id
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.fixture(scope="module")
def client():
    """Authenticated owner client for building 13195."""
    return TestClient(_make_app())


@pytest.fixture(scope="module")
def client_ec():
    """EC member client (effective_role=ec_member) for building 13195."""
    return TestClient(_make_app(user=USER_EC))


@pytest.fixture(scope="module")
def client_16244():
    """Owner client for Sierra building 16244."""
    return TestClient(_make_app(building_id=BUILDING_16244))


@pytest.fixture(scope="module")
def client_unauth():
    """No auth overrides — dependency will reject with 401/422."""
    from routers.document_converter import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def _mock_md(text="# Hello"):
    """Return a patched _get_markitdown() that yields the given text."""
    md = MagicMock()
    md.convert.return_value = MagicMock(text_content=text)
    return md


def _post_txt(client, content=b"hello world", name="doc.txt"):
    return client.post(
        "/api/document-converter/convert",
        files={"file": (name, io.BytesIO(content), "text/plain")},
    )


# ---------------------------------------------------------------------------
# Mock the DB log call so no real DB is needed
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_db_log():
    """Prevent any real DB write during tests; reset rate limit counts around each test."""
    from utils.rate_limit import SLOWAPI_AVAILABLE, limiter
    if SLOWAPI_AVAILABLE:
        limiter.reset()
    with patch("routers.document_converter._log_conversion", new_callable=AsyncMock):
        yield
    if SLOWAPI_AVAILABLE:
        limiter.reset()


# ---------------------------------------------------------------------------
# 1. Supported formats
# ---------------------------------------------------------------------------

class TestSupportedFormats:
    def test_extension_list_present(self, client):
        r = client.get("/api/document-converter/supported-formats")
        assert r.status_code == 200
        data = r.json()
        assert ".pdf" in data["extensions"]
        assert ".docx" in data["extensions"]
        assert ".xlsx" in data["extensions"]

    def test_max_file_size_is_20(self, client):
        r = client.get("/api/document-converter/supported-formats")
        assert r.json()["max_file_size_mb"] == 20

    def test_pdf_notes_present(self, client):
        r = client.get("/api/document-converter/supported-formats")
        notes = r.json().get("pdf_notes", {})
        assert "fallback" in notes
        assert "scanned" in notes
        assert "multi_column" in notes

    def test_formats_list_not_empty(self, client):
        r = client.get("/api/document-converter/supported-formats")
        assert len(r.json()["formats"]) >= 6


# ---------------------------------------------------------------------------
# 2. File conversion — happy paths
# ---------------------------------------------------------------------------

class TestFileConversion:
    def test_txt_round_trip(self, client):
        content = b"Strata levy notice\nQ1 2026"
        with patch("routers.document_converter._get_markitdown", return_value=_mock_md("Strata levy notice\nQ1 2026")):
            r = _post_txt(client, content)
        assert r.status_code == 200
        data = r.json()
        assert data["filename"] == "doc.txt"
        assert data["extension"] == ".txt"
        assert data["size_bytes"] == len(content)
        assert "Strata levy notice" in data["markdown"]

    def test_char_and_line_counts_correct(self, client):
        markdown = "Line one\nLine two\nLine three"
        with patch("routers.document_converter._get_markitdown", return_value=_mock_md(markdown)):
            r = _post_txt(client)
        data = r.json()
        assert data["char_count"] == len(markdown)
        assert data["line_count"] == markdown.count("\n")

    def test_extension_passthrough_to_temp_file(self, client):
        """MarkItDown must receive a temp file with the original .pdf extension."""
        import routers.document_converter as dc
        original = dc._get_markitdown
        seen_paths = []

        def capturing():
            md = MagicMock()

            def conv(path):
                seen_paths.append(path)
                return MagicMock(text_content="# heading")

            md.convert = conv
            return md

        dc._get_markitdown = capturing
        try:
            client.post("/api/document-converter/convert",
                        files={"file": ("report.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")})
        finally:
            dc._get_markitdown = original

        assert any(p.endswith(".pdf") for p in seen_paths), \
            "Temp file must preserve .pdf extension for MarkItDown type detection"

    def test_fallback_used_false_for_non_pdf(self, client):
        with patch("routers.document_converter._get_markitdown", return_value=_mock_md("content")):
            r = _post_txt(client)
        assert r.json()["fallback_used"] is False
        assert r.json()["is_scanned_pdf"] is False

    def test_csv_conversion(self, client):
        with patch("routers.document_converter._get_markitdown",
                   return_value=_mock_md("| unit | levy |\n|---|\n| TH017 | 7090 |")):
            r = client.post("/api/document-converter/convert",
                            files={"file": ("levies.csv", io.BytesIO(b"unit,levy\nTH017,7090"), "text/csv")})
        assert r.status_code == 200
        assert r.json()["extension"] == ".csv"


# ---------------------------------------------------------------------------
# 3. PDF fallback to pdfplumber
# ---------------------------------------------------------------------------

class TestPdfFallback:
    def _post_pdf(self, client, md_text="", plumber_text="", plumber_scanned=False):
        with patch("routers.document_converter._get_markitdown") as mock_md_fn, \
                patch("routers.document_converter._extract_pdf_pdfplumber") as mock_plumber:
            mock_md_fn.return_value.convert.return_value = MagicMock(text_content=md_text)
            mock_plumber.return_value = (plumber_text, plumber_scanned)
            return client.post(
                "/api/document-converter/convert",
                files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            ), mock_plumber

    def test_fallback_triggered_when_markitdown_empty(self, client):
        r, plumber = self._post_pdf(client, md_text="", plumber_text="## Page 1\n\nSome text")
        assert r.status_code == 200
        plumber.assert_called_once()
        assert r.json()["fallback_used"] is True
        assert "Page 1" in r.json()["markdown"]

    def test_fallback_triggered_when_markitdown_below_threshold(self, client):
        # < 100 non-whitespace chars triggers fallback
        short = "x" * 50
        r, plumber = self._post_pdf(client, md_text=short, plumber_text="## Page 1\n\nFull text here")
        plumber.assert_called_once()
        assert r.json()["fallback_used"] is True

    def test_fallback_not_triggered_when_markitdown_sufficient(self, client):
        # >= 100 non-whitespace chars — no fallback
        rich = "x" * 200
        r, plumber = self._post_pdf(client, md_text=rich)
        plumber.assert_not_called()
        assert r.json()["fallback_used"] is False

    def test_scanned_pdf_detected(self, client):
        # Both MarkItDown and pdfplumber return empty
        r, _ = self._post_pdf(client, md_text="", plumber_text="", plumber_scanned=True)
        assert r.status_code == 200
        assert r.json()["is_scanned_pdf"] is True
        assert r.json()["fallback_used"] is False  # no content from fallback either

    def test_scanned_pdf_returns_empty_markdown(self, client):
        r, _ = self._post_pdf(client, md_text="", plumber_text="", plumber_scanned=True)
        assert r.json()["markdown"] == ""
        assert r.json()["char_count"] == 0

    def test_pdfplumber_table_to_markdown_helper(self):
        from routers.document_converter import _table_to_markdown
        table = [["Unit", "Levy"], ["TH017", "7090.04"], ["UA063", "3060.60"]]
        md = _table_to_markdown(table)
        assert "| Unit | Levy |" in md
        assert "| --- |" in md or "| ---" in md
        assert "TH017" in md

    def test_pdfplumber_table_empty_input(self):
        from routers.document_converter import _table_to_markdown
        assert _table_to_markdown([]) == ""
        # all-None row still produces a table (2 cols with empty cells) — just don't crash
        result = _table_to_markdown([[None, None]])
        assert isinstance(result, str)
        assert "---" in result  # separator row present = valid markdown table

    def test_pdfplumber_extract_called_with_pdf_path(self, client):
        seen = []
        with patch("routers.document_converter._get_markitdown") as mock_md, \
                patch("routers.document_converter._extract_pdf_pdfplumber") as mock_plumber:
            mock_md.return_value.convert.return_value = MagicMock(text_content="")
            mock_plumber.side_effect = lambda p: (seen.append(p), ("text", False))[1]
            client.post("/api/document-converter/convert",
                        files={"file": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
        assert len(seen) == 1 and seen[0].endswith(".pdf")


# ---------------------------------------------------------------------------
# 4. File conversion — error paths
# ---------------------------------------------------------------------------

class TestFileConversionErrors:
    def test_unsupported_extension_400(self, client):
        r = client.post("/api/document-converter/convert",
                        files={"file": ("script.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
        assert r.status_code == 400
        assert "Unsupported" in r.json()["detail"]

    def test_zip_rejected(self, client):
        r = client.post("/api/document-converter/convert",
                        files={"file": ("archive.zip", io.BytesIO(b"PK"), "application/zip")})
        assert r.status_code == 400

    def test_oversized_file_413(self, client):
        big = b"x" * (21 * 1024 * 1024)
        r = client.post("/api/document-converter/convert",
                        files={"file": ("big.txt", io.BytesIO(big), "text/plain")})
        assert r.status_code == 413
        assert "large" in r.json()["detail"].lower()

    def test_markitdown_exception_returns_422_for_non_pdf(self, client):
        with patch("routers.document_converter._get_markitdown") as mock_md:
            mock_md.return_value.convert.side_effect = Exception("Corrupt file")
            r = _post_txt(client)
        assert r.status_code == 422
        assert "Conversion" in r.json()["detail"]

    def test_temp_file_removed_on_success(self, client):
        unlinked = []
        real_unlink = os.unlink

        def capture(p):
            unlinked.append(p)
            real_unlink(p)

        with patch("routers.document_converter._get_markitdown", return_value=_mock_md("ok")), \
                patch("os.unlink", side_effect=capture):
            _post_txt(client)

        assert len(unlinked) >= 1

    def test_temp_file_removed_on_failure(self, client):
        unlinked = []
        real_unlink = os.unlink

        def capture(p):
            unlinked.append(p)
            real_unlink(p)

        with patch("routers.document_converter._get_markitdown") as mock_md, \
                patch("os.unlink", side_effect=capture):
            mock_md.return_value.convert.side_effect = Exception("fail")
            _post_txt(client)  # will hit fallback / 422, not crash

        assert len(unlinked) >= 1, "Temp file must be cleaned up even on exception"


# ---------------------------------------------------------------------------
# 5. URL conversion
# ---------------------------------------------------------------------------

class TestUrlConversion:
    def test_valid_https_url(self, client):
        # _block_ssrf does a live DNS lookup; mock it so the test focuses on
        # scheme acceptance + happy-path response shape.
        with patch("routers.document_converter._block_ssrf"), \
                patch("routers.document_converter._get_markitdown", return_value=_mock_md("# Page")):
            r = client.post("/api/document-converter/convert-url",
                            json={"url": "https://example.com/doc.pdf"})
        assert r.status_code == 200
        assert r.json()["url"] == "https://example.com/doc.pdf"

    def test_valid_http_url(self, client):
        # _block_ssrf does a live DNS lookup; mock it so the test focuses on scheme acceptance.
        with patch("routers.document_converter._block_ssrf"), \
                patch("routers.document_converter._get_markitdown", return_value=_mock_md("content")):
            r = client.post("/api/document-converter/convert-url",
                            json={"url": "http://internal.example.com/report"})
        assert r.status_code == 200

    def test_ftp_scheme_rejected(self, client):
        r = client.post("/api/document-converter/convert-url",
                        json={"url": "ftp://files.example.com/doc.pdf"})
        assert r.status_code == 400

    def test_file_scheme_rejected(self, client):
        r = client.post("/api/document-converter/convert-url",
                        json={"url": "file:///etc/passwd"})
        assert r.status_code == 400

    def test_exception_returns_422(self, client):
        with patch("routers.document_converter._block_ssrf"), \
                patch("routers.document_converter._get_markitdown") as mock_md:
            mock_md.return_value.convert.side_effect = Exception("Network error")
            r = client.post("/api/document-converter/convert-url",
                            json={"url": "https://example.com/fail"})
        assert r.status_code == 422

    def test_optional_filename_returned(self, client):
        with patch("routers.document_converter._block_ssrf"), \
                patch("routers.document_converter._get_markitdown", return_value=_mock_md("# Doc")):
            r = client.post("/api/document-converter/convert-url",
                            json={"url": "https://example.com/report.pdf", "filename": "My Report"})
        assert r.json()["filename"] == "My Report"

    def test_fallback_and_scanned_false_for_url(self, client):
        with patch("routers.document_converter._block_ssrf"), \
                patch("routers.document_converter._get_markitdown", return_value=_mock_md("content")):
            r = client.post("/api/document-converter/convert-url",
                            json={"url": "https://example.com/doc"})
        assert r.json()["fallback_used"] is False
        assert r.json()["is_scanned_pdf"] is False


# ---------------------------------------------------------------------------
# 6. Conversion history
# ---------------------------------------------------------------------------

class TestHistory:
    def _history_entries(self, n, user_id="user-owner-001"):
        return [
            {
                "id": f"log-{i}",
                "building_id": BUILDING_13195,
                "user_id": user_id,
                "conversion_type": "file",
                "filename": f"doc{i}.pdf",
                "extension": ".pdf",
                "char_count": 100 * i,
                "status": "success",
                "created_at": f"2026-04-09T12:0{i}:00Z",
            }
            for i in range(1, n + 1)
        ]

    def test_manager_sees_all_building_history(self, client_ec):
        entries = self._history_entries(5, user_id="other-user")
        with patch("routers.document_converter.db") as mock_db:
            mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=entries
            )
            r = client_ec.get("/api/document-converter/history")
        assert r.status_code == 200
        # EC member query should NOT filter by user_id
        call_args = mock_db.document_conversion_logs.find.call_args[0][0]
        assert "user_id" not in call_args

    def test_owner_sees_only_own_history(self, client):
        entries = self._history_entries(3, user_id="user-owner-001")
        with patch("routers.document_converter.db") as mock_db:
            mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=entries
            )
            r = client.get("/api/document-converter/history")
        assert r.status_code == 200
        # Owner query must filter by user_id
        call_args = mock_db.document_conversion_logs.find.call_args[0][0]
        assert call_args.get("user_id") == USER_OWNER["id"]

    def test_history_scoped_to_building(self, client):
        with patch("routers.document_converter.db") as mock_db:
            mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=[]
            )
            r = client.get("/api/document-converter/history")
        assert r.status_code == 200
        call_args = mock_db.document_conversion_logs.find.call_args[0][0]
        assert call_args["building_id"] == BUILDING_13195

    def test_history_returns_count(self, client):
        entries = self._history_entries(4)
        with patch("routers.document_converter.db") as mock_db:
            mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=entries
            )
            r = client.get("/api/document-converter/history")
        assert r.json()["count"] == 4
        assert len(r.json()["entries"]) == 4

    def test_history_empty_state(self, client):
        with patch("routers.document_converter.db") as mock_db:
            mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=[]
            )
            r = client.get("/api/document-converter/history")
        assert r.status_code == 200
        assert r.json()["entries"] == []
        assert r.json()["count"] == 0

    def test_history_limit_clamped(self, client):
        with patch("routers.document_converter.db") as mock_db:
            mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=[]
            )
            # limit=0 should be clamped to 1
            r = client.get("/api/document-converter/history?limit=0")
            assert r.status_code == 200
            to_list_call = mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list
            assert to_list_call.call_args[0][0] >= 1

            # limit=9999 should be clamped to 200
            mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=[]
            )
            r2 = client.get("/api/document-converter/history?limit=9999")
            assert r2.status_code == 200
            to_list_call2 = mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list
            assert to_list_call2.call_args[0][0] <= 200


# ---------------------------------------------------------------------------
# 7. Multi-tenant auth
# ---------------------------------------------------------------------------

class TestMultiTenantAuth:
    def test_building_13195_can_convert(self, client):
        with patch("routers.document_converter._get_markitdown", return_value=_mock_md("ok")):
            r = _post_txt(client)
        assert r.status_code == 200

    def test_building_16244_can_convert(self, client_16244):
        """Sierra building must have identical feature parity."""
        with patch("routers.document_converter._get_markitdown", return_value=_mock_md("sierra ok")):
            r = _post_txt(client_16244)
        assert r.status_code == 200

    def test_unauthenticated_convert_rejected(self, client_unauth):
        r = _post_txt(client_unauth)
        assert r.status_code in (401, 422), f"Must not be 200, got {r.status_code}"

    def test_unauthenticated_url_convert_rejected(self, client_unauth):
        r = client_unauth.post("/api/document-converter/convert-url",
                               json={"url": "https://example.com"})
        assert r.status_code in (401, 422)

    def test_unauthenticated_history_rejected(self, client_unauth):
        r = client_unauth.get("/api/document-converter/history")
        assert r.status_code in (401, 422)

    def test_history_building_16244_scoped(self, client_16244):
        with patch("routers.document_converter.db") as mock_db:
            mock_db.document_conversion_logs.find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=[]
            )
            r = client_16244.get("/api/document-converter/history")
        assert r.status_code == 200
        call_args = mock_db.document_conversion_logs.find.call_args[0][0]
        assert call_args["building_id"] == BUILDING_16244
        assert call_args["building_id"] != BUILDING_13195
