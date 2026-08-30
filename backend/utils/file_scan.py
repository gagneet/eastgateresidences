"""
File scanning utility — Google Magika content-based type detection.

Provides a security barrier for all file uploads:
 - Detects true file type from byte content, ignoring spoofed extensions/headers.
 - Blocks dangerous types (executables, scripts, bytecode) unconditionally.
 - Enforces per-context allowlists (document / image / csv / pdf / converter).
 - Enforces file size limits.

Usage:
    from utils.file_scan import scan_upload

    content = await file.read()
    await scan_upload(content, context="document", filename=file.filename)
    # raises HTTPException(400/413) on rejection; returns ScanResult on success

Fail-open: if the magika package is not installed, size limits are still enforced
but type detection is skipped with a warning (allows dev installs to work without
the ML dependency). Install with: pip install magika
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Dedicated thread pool — Magika inference is CPU-bound (~5 ms per file)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="magika-scan")

# ── Magika lazy singleton ─────────────────────────────────────────────────────
_magika: Optional[object] = None
_magika_checked = False


def _get_magika() -> Optional[object]:
    """Generated function header.

    Function: _get_magika
    Path: backend/utils/file_scan.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    global _magika, _magika_checked
    if _magika_checked:
        return _magika
    _magika_checked = True
    try:
        from magika import Magika  # noqa: PLC0415
        _magika = Magika()
        logger.info("Magika file scanner initialised")
    except Exception as exc:
        logger.warning("Magika unavailable — file type scanning disabled: %s", exc)
        _magika = None
    return _magika


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    label: str  # Magika label, e.g. "pdf", "python", "pe"
    mime_type: str  # Standard MIME type
    score: float  # Confidence 0.0–1.0
    group: str  # Category: "document", "code", "binary", "image", etc.


# ── Security definitions ──────────────────────────────────────────────────────

# Labels that are ALWAYS rejected regardless of upload context.
BLOCKED_LABELS: frozenset[str] = frozenset({
    # Native executables / compiled bytecode
    "pe", "elf", "macho", "dex", "apk", "jar", "class", "msi", "cab",
    # Scripting languages (Magika label strings)
    "python", "perl", "ruby", "php",
    "shell",  # bash/sh — Magika's label for all POSIX shell scripts
    "powershell",
    "batch",  # Windows .bat/.cmd — Magika's label
    "vba",
    # Web scripts
    "javascript", "typescript",
    # Embedded database (no legitimate upload use)
    "sqlite",
})

# Per-context allowlists — only labels in the allowlist pass.
# "csv" context is intentionally loose (text/unknown) to handle encoding variants.
_DOCUMENT_LABELS: frozenset[str] = frozenset({
    "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
    "odt", "ods", "odp", "rtf",
    "txt", "csv", "tsv", "json",
    "jpeg", "png", "gif", "webp", "bmp", "tiff", "svg", "heic",
    "xml", "unknown",  # unknown allowed — legitimate files may be unrecognised
})
_IMAGE_LABELS: frozenset[str] = frozenset({
    "jpeg", "png", "gif", "webp", "bmp", "tiff", "heic",
})
_CSV_LABELS: frozenset[str] = frozenset({
    "csv", "tsv", "txt", "unknown",  # many CSVs detected as plain text
})
# "tabular" = the CSV labels PLUS spreadsheet workbooks, for the onboarding /
# financial-import upload paths that accept either a .csv or an .xlsx
# (see backend/utils/tabular_upload.py). Magika labels an .xlsx as "xlsx", but
# older/odd workbooks can come back as the generic "zip" container label.
_TABULAR_LABELS: frozenset[str] = frozenset({
    "csv", "tsv", "txt", "unknown",
    "xlsx", "xls", "zip", "ods",
})
_PDF_LABELS: frozenset[str] = frozenset({"pdf"})
_CONVERTER_LABELS: frozenset[str] = frozenset({
    "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
    "odt", "ods", "odp", "rtf",
    "txt", "csv", "tsv", "markdown",
    "jpeg", "png", "gif", "webp", "bmp", "tiff", "svg", "heic",
    "html", "xml", "unknown",
})

CONTEXT_ALLOWLISTS: dict[str, frozenset[str]] = {
    "document": _DOCUMENT_LABELS,
    "image": _IMAGE_LABELS,
    "csv": _CSV_LABELS,
    "tabular": _TABULAR_LABELS,
    "pdf": _PDF_LABELS,
    "converter": _CONVERTER_LABELS,
}

# Default size limits per context (bytes)
_DEFAULT_SIZE_LIMITS: dict[str, int] = {
    "document": 25 * 1024 * 1024,  # 25 MB
    "image": 10 * 1024 * 1024,  # 10 MB
    "csv": 10 * 1024 * 1024,  # 10 MB
    "tabular": 10 * 1024 * 1024,  # 10 MB — same cap as csv
    "pdf": 50 * 1024 * 1024,  # 50 MB — financial reports can be large
    "converter": 20 * 1024 * 1024,  # 20 MB — matches existing converter limit
}


# ── Internal scan ─────────────────────────────────────────────────────────────

def _scan_sync(content: bytes) -> Optional[ScanResult]:
    """Run synchronous Magika scan. Called via thread pool."""
    m = _get_magika()
    if m is None:
        return None
    try:
        result = m.identify_bytes(content)
        return ScanResult(
            label=result.output.label,
            mime_type=result.output.mime_type,
            score=result.score,
            group=result.output.group,
        )
    except Exception as exc:
        logger.error("Magika scan error: %s", exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

async def scan_upload(
        content: bytes,
        *,
        context: str,
        filename: str = "",
        max_size_bytes: Optional[int] = None,
) -> ScanResult:
    """
    Scan uploaded bytes for security and type validity.

    Raises HTTPException(413) if content exceeds size limit.
    Raises HTTPException(400) if content matches a blocked label or falls
    outside the allowlist for the given context.

    Returns ScanResult on success. If Magika is unavailable, size limits are
    still enforced and a ScanResult with label="unknown" is returned.
    """
    # ── 1. Size gate ──────────────────────────────────────────────────────────
    limit = max_size_bytes if max_size_bytes is not None else _DEFAULT_SIZE_LIMITS.get(context, 25 * 1024 * 1024)
    if not content:
        raise HTTPException(status_code=400, detail="Empty file is not allowed.")
    if len(content) > limit:
        mb = limit // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File too large. Maximum allowed size is {mb} MB.")

    # ── 2. Magika content detection ───────────────────────────────────────────
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _scan_sync, content)

    if result is None:
        # Fail-open: Magika unavailable — skip type check, still logged
        logger.warning("File type scan skipped (Magika unavailable): %s", filename or "upload")
        return ScanResult(label="unknown", mime_type="application/octet-stream", score=0.0, group="unknown")

    label = result.label
    logger.info(
        "Scan: file=%r context=%s label=%s mime=%s score=%.2f",
        filename or "—", context, label, result.mime_type, result.score,
    )

    # ── 3. Blocked-label gate (always, regardless of context) ─────────────────
    if label in BLOCKED_LABELS:
        logger.warning(
            "BLOCKED upload — dangerous type: file=%r label=%s score=%.2f",
            filename or "—", label, result.score,
        )
        raise HTTPException(
            status_code=400,
            detail="This file type is not permitted for upload.",
        )

    # ── 4. Context allowlist gate ─────────────────────────────────────────────
    allowlist = CONTEXT_ALLOWLISTS.get(context)
    if allowlist and label not in allowlist:
        logger.warning(
            "REJECTED upload — outside allowlist: file=%r context=%s label=%s",
            filename or "—", context, label,
        )
        raise HTTPException(
            status_code=400,
            detail=f"File content does not match the expected type for this upload (detected: {result.mime_type}).",
        )

    return result
