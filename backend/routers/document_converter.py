"""
Document converter router — PDF/Word/Excel/HTML→Markdown.

Libraries:
  Primary:  Microsoft MarkItDown (markitdown[all]>=0.1.5)
  Fallback: pdfplumber — used when MarkItDown returns < PDF_FALLBACK_THRESHOLD
            non-whitespace characters for a PDF file. pdfplumber extracts
            text and tables per-page, producing page-separated Markdown.

Features added over v1:
  - pdfplumber fallback for PDFs (empty / very short MarkItDown output)
  - Scanned-PDF detection (returns is_scanned_pdf=True in response)
  - Per-building + per-IP rate limit: 6 conversions/minute
  - Conversion history log written to document_conversion_logs (building-scoped)
  - GET /history — last 50 conversions for the building

Rate limit key: "doc_convert:{building_id}:{real_client_ip}"
History visibility: admin/EC/manager see all; owner/tenant see own only.
"""

import ipaddress
import logging
import os
import socket
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from database import db
from utils.auth import get_current_building, get_current_user
from utils.file_scan import scan_upload
from utils.client_ip import resolve_client_ips
from utils.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/document-converter", tags=["Document Converter"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx",
    ".html", ".htm", ".txt", ".csv", ".md",
}

MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# If MarkItDown extracts fewer than this many non-whitespace chars from a PDF,
# fall back to pdfplumber.
PDF_FALLBACK_THRESHOLD = 100

# Scanned-PDF heuristic: if pdfplumber also extracts fewer than this many chars
# across ALL pages, the PDF is almost certainly a scan (image-only, no OCR).
PDF_SCANNED_THRESHOLD = 50

# Roles that can see all building conversions (not just their own)
_MANAGER_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member", "admin_staff"}

# Networks blocked from the URL converter to prevent SSRF.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _block_ssrf(url: str) -> None:
    """Raise HTTP 400 if the URL resolves to a private or reserved address."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL host.")
    try:
        resolved = socket.getaddrinfo(host, None)
    except OSError:
        raise HTTPException(status_code=400, detail="Cannot resolve host.")
    for _family, _type, _proto, _canonname, sockaddr in resolved:
        addr = ipaddress.ip_address(sockaddr[0])
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise HTTPException(
                    status_code=400,
                    detail="URL resolves to a private or reserved address.",
                )


# ---------------------------------------------------------------------------
# Rate-limit key: per building + per real IP (prevents one building's users
# from consuming another building's quota).
# ---------------------------------------------------------------------------

def _convert_rate_key(request: Request) -> str:
    """Composite slowapi key: building header + canonical client IP."""
    building = request.headers.get("X-Building-ID", "unknown")
    ip_address = resolve_client_ips(request).best
    return f"doc_convert:{building}:{ip_address}"


# ---------------------------------------------------------------------------
# pdfplumber helpers
# ---------------------------------------------------------------------------

def _table_to_markdown(table: list) -> str:
    """Convert a pdfplumber table (list of rows, each a list of cells) to Markdown."""
    if not table:
        return ""
    max_cols = max((len(row) for row in table), default=0)
    if max_cols == 0:
        return ""

    def _cell(v) -> str:
        """Generated function header.

        Function: _cell
        Path: backend/routers/document_converter.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return str(v or "").strip().replace("\n", " ").replace("|", "\\|")

    rows = [[_cell(c) for c in row] + [""] * (max_cols - len(row)) for row in table]
    header, body = rows[0], rows[1:]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * max_cols) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _extract_pdf_pdfplumber(path: str) -> tuple[str, bool]:
    """
    Extract text and tables from a PDF using pdfplumber.

    Returns:
        (markdown_text, is_scanned)
        is_scanned=True  → fewer than PDF_SCANNED_THRESHOLD chars extracted
                           across all pages (likely a scan / image-only PDF).
        markdown_text="" → pdfplumber is unavailable or extraction failed.

    Layout strategy: tables are detected first on each page (pdfplumber's
    table extractor handles complex column layouts better than raw text).
    The remaining text is appended below the tables for that page.
    Multi-column documents will still be linearised — this is a fundamental
    limitation of PDF text extraction without layout analysis.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed — PDF fallback unavailable")
        return "", False

    pages_md: list[str] = []
    total_chars = 0

    try:
        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                parts: list[str] = []

                # --- Tables ---
                tables = page.extract_tables() or []
                for table in tables:
                    md_table = _table_to_markdown(table)
                    if md_table:
                        parts.append(md_table)

                # --- Text ---
                text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                stripped = text.strip()
                if stripped:
                    total_chars += len(stripped)
                    parts.append(stripped)

                if parts:
                    header = f"## Page {page_num}"
                    pages_md.append(header + "\n\n" + "\n\n".join(parts))

    except Exception as exc:
        logger.warning(f"pdfplumber extraction error: {exc}")
        return "", False

    markdown = "\n\n---\n\n".join(pages_md)
    is_scanned = len(pages_md) > 0 and total_chars < PDF_SCANNED_THRESHOLD
    return markdown, is_scanned


# ---------------------------------------------------------------------------
# MarkItDown lazy loader
# ---------------------------------------------------------------------------

def _get_markitdown():
    """Lazy import — returns 503 with actionable message if library absent."""
    try:
        from markitdown import MarkItDown
        return MarkItDown()
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"MarkItDown not available: {exc}. Run: pip install 'markitdown[all]>=0.1.5'",
        )


# ---------------------------------------------------------------------------
# History logging (fire-and-forget — must never fail a conversion)
# ---------------------------------------------------------------------------

async def _log_conversion(*, building_id: str, user_id: str, record: dict) -> None:
    """Insert a conversion log entry. Silently swallows any DB error."""
    try:
        record.update({
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await db.document_conversion_logs.insert_one(record)
    except Exception as exc:
        logger.warning(f"Failed to write conversion log: {exc}")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ConvertUrlRequest(BaseModel):
    url: str
    filename: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/convert")
@limiter.limit("6/minute", key_func=_convert_rate_key)
async def convert_document(
        request: Request,
        file: UploadFile = File(...),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Convert an uploaded file to Markdown.

    PDF pipeline:
      1. MarkItDown (primary)
      2. pdfplumber (fallback if MarkItDown returns < PDF_FALLBACK_THRESHOLD chars)

    Response extra fields for PDFs:
      fallback_used  — True when pdfplumber was used instead of MarkItDown
      is_scanned_pdf — True when neither extractor found meaningful text
                       (almost certainly a scanned / image-only document)

    Rate limit: 6/minute per building per IP.
    Max file size: 20 MB.
    """
    # ── Validate extension ──────────────────────────────────────────────────
    _, ext = os.path.splitext(file.filename or "")
    ext = ext.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{ext}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            ),
        )

    # ── Read + size check ───────────────────────────────────────────────────
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({len(content) / 1024 / 1024:.1f} MB). "
                f"Maximum is {MAX_FILE_SIZE_MB} MB."
            ),
        )
    await scan_upload(content, context="converter", filename=file.filename or "", max_size_bytes=MAX_FILE_SIZE_BYTES)

    md = _get_markitdown()
    fallback_used = False
    is_scanned_pdf = False

    # ── Write temp file (extension preserved for MarkItDown type detection) ─
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Primary: MarkItDown
        try:
            result = md.convert(tmp_path)
            markdown_text = result.text_content or ""
        except Exception as exc:
            logger.warning(f"MarkItDown failed for '{file.filename}': {exc}")
            markdown_text = ""  # will trigger pdfplumber fallback for PDFs

        # Fallback: pdfplumber (PDFs only)
        if ext == ".pdf":
            meaningful = markdown_text.replace(" ", "").replace("\n", "")
            if len(meaningful) < PDF_FALLBACK_THRESHOLD:
                logger.info(
                    f"MarkItDown returned {len(meaningful)} chars for '{file.filename}' "
                    "— trying pdfplumber fallback"
                )
                fb_text, is_scanned_pdf = _extract_pdf_pdfplumber(tmp_path)
                if fb_text:
                    markdown_text = fb_text
                    fallback_used = True
                elif not markdown_text:
                    # Both extractors returned empty — tell the user why
                    is_scanned_pdf = True

    finally:
        os.unlink(tmp_path)

    # ── Error if no content produced for non-PDF ────────────────────────────
    if not markdown_text and ext != ".pdf":
        raise HTTPException(status_code=422, detail="Conversion produced no output.")

    await _log_conversion(
        building_id=building_id,
        user_id=current_user.get("id", "unknown"),
        record={
            "conversion_type": "file",
            "filename": file.filename,
            "extension": ext,
            "size_bytes": len(content),
            "char_count": len(markdown_text),
            "line_count": markdown_text.count("\n"),
            "fallback_used": fallback_used,
            "is_scanned_pdf": is_scanned_pdf,
            "status": "success",
        },
    )

    return {
        "filename": file.filename,
        "extension": ext,
        "size_bytes": len(content),
        "markdown": markdown_text,
        "char_count": len(markdown_text),
        "line_count": markdown_text.count("\n"),
        # PDF-specific signals
        "fallback_used": fallback_used,
        "is_scanned_pdf": is_scanned_pdf,
    }


@router.post("/convert-url")
@limiter.limit("6/minute", key_func=_convert_rate_key)
async def convert_url(
        request: Request,
        body: ConvertUrlRequest,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """
    Convert a publicly accessible URL to Markdown.
    Accepts http:// and https:// only.
    Rate limit: 6/minute per building per IP.
    """
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="URL must start with http:// or https://",
        )

    _block_ssrf(body.url)

    md = _get_markitdown()

    try:
        result = md.convert(body.url)
        markdown_text = result.text_content or ""
    except Exception as exc:
        logger.error(f"MarkItDown URL conversion failed for '{body.url}': {exc}")
        await _log_conversion(
            building_id=building_id,
            user_id=current_user.get("id", "unknown"),
            record={
                "conversion_type": "url",
                "filename": body.filename or body.url.split("/")[-1] or "converted",
                "source_url": body.url,
                "char_count": 0,
                "line_count": 0,
                "fallback_used": False,
                "is_scanned_pdf": False,
                "status": "error",
                "error_message": str(exc),
            },
        )
        raise HTTPException(status_code=422, detail=f"Conversion failed: {exc}")

    filename = body.filename or body.url.split("/")[-1] or "converted"

    await _log_conversion(
        building_id=building_id,
        user_id=current_user.get("id", "unknown"),
        record={
            "conversion_type": "url",
            "filename": filename,
            "source_url": body.url,
            "char_count": len(markdown_text),
            "line_count": markdown_text.count("\n"),
            "fallback_used": False,
            "is_scanned_pdf": False,
            "status": "success",
        },
    )

    return {
        "url": body.url,
        "filename": filename,
        "markdown": markdown_text,
        "char_count": len(markdown_text),
        "line_count": markdown_text.count("\n"),
        "fallback_used": False,
        "is_scanned_pdf": False,
    }


@router.get("/history")
async def get_conversion_history(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
        limit: int = 50,
):
    """
    Return recent conversion history for this building.

    Visibility:
      - admin / EC / manager → all conversions in the building (last {limit})
      - owner / tenant        → own conversions only

    Fields returned exclude the raw markdown (size concern); use the
    convert endpoint again to re-produce markdown from the original file.
    """
    limit = max(1, min(limit, 200))  # clamp 1–200

    # Resolve effective role the same way _effective_role() in server.py does —
    # imported here to avoid circular import (server.py imports routers).
    effective_role = current_user.get("effective_role") or current_user.get("role", "")
    query: dict = {"building_id": building_id}
    if effective_role not in _MANAGER_ROLES:
        query["user_id"] = current_user.get("id", "")

    projection = {"_id": 0, "markdown": 0}  # never return raw markdown in history
    entries = await (
        db.document_conversion_logs
        .find(query, projection)
        .sort("created_at", -1)
        .to_list(limit)
    )
    return {"building_id": building_id, "entries": entries, "count": len(entries)}


@router.get("/supported-formats")
async def get_supported_formats():
    """Return accepted file extensions and size limit. No auth required."""
    return {
        "extensions": sorted(SUPPORTED_EXTENSIONS),
        "max_file_size_mb": MAX_FILE_SIZE_MB,
        "pdf_notes": {
            "fallback": "pdfplumber is used as a fallback when MarkItDown extracts little content",
            "scanned": "Scanned (image-only) PDFs cannot be converted — no OCR is performed",
            "multi_column": "Multi-column layouts are linearised (text flows top-to-bottom)",
        },
        "formats": [
            {"ext": ".pdf", "label": "PDF Document"},
            {"ext": ".docx", "label": "Word Document (.docx)"},
            {"ext": ".doc", "label": "Word Document (.doc)"},
            {"ext": ".xlsx", "label": "Excel Spreadsheet (.xlsx)"},
            {"ext": ".xls", "label": "Excel Spreadsheet (.xls)"},
            {"ext": ".pptx", "label": "PowerPoint Presentation"},
            {"ext": ".html", "label": "HTML Page"},
            {"ext": ".csv", "label": "CSV File"},
            {"ext": ".txt", "label": "Plain Text"},
        ],
    }
