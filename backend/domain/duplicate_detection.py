"""
backend/domain/duplicate_detection.py — Three-layer AP invoice duplicate detection.

# @featuretrace:ap_automation — duplicate gate in the submitted → validated transition.
# Layer: domain
# Data flow: invoice submission → duplicate_detection → block or pass.
# Related: backend/domain/invoice_lifecycle.py
#           backend/routers/ap_supplier_upload.py
# Scope: (building-scoped)

Three layers (checked in order, each progressively more expensive):
  Layer 1 — DB unique index on (tenant_id, vendor_abn, invoice_number).
             Caught at insert time; returns existing document ID.
  Layer 2 — Near-duplicate: same vendor_abn + total_cents + issue_date ± 3 days
             + Levenshtein distance ≤ 2 on invoice_number.
  Layer 3 — Content hash: SHA-256 of normalised OCR text matches an existing doc.

Cross-building isolation: all queries include tenant_id from the caller.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DATE_WINDOW_DAYS = 3
_LEVENSHTEIN_THRESHOLD = 2


@dataclass(frozen=True)
class DuplicateCheckResult:
    is_duplicate: bool
    layer: Optional[int]  # 1, 2, or 3 — which layer fired
    existing_invoice_id: Optional[str]
    reason: Optional[str]


def normalise_ocr_text(raw_text: str) -> str:
    """Strip whitespace and lowercase for deterministic content hashing."""
    return re.sub(r"\s+", " ", (raw_text or "").lower()).strip()


def content_sha256(ocr_text: str) -> str:
    """Generated function header.

    Function: content_sha256
    Path: backend/domain/duplicate_detection.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    normalised = normalise_ocr_text(ocr_text)
    return hashlib.sha256(normalised.encode()).hexdigest()


def _levenshtein(a: str, b: str) -> int:
    """Standard dynamic-programming Levenshtein distance."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1,
                          prev[j - 1] + (0 if ca == cb else 1))
        prev = curr
    return prev[lb]


async def check_duplicate(
        tenant_id: str,
        vendor_abn: Optional[str],
        invoice_number: Optional[str],
        total_cents: int,
        issue_date: Optional[str],  # ISO date string "YYYY-MM-DD"
        content_hash: Optional[str],
        *,
        db,
        exclude_id: Optional[str] = None,
) -> DuplicateCheckResult:
    """
    Run all three duplicate-detection layers for a given building (tenant_id).

    Args:
        exclude_id: invoice _id (str) to exclude from checks — used when
                    re-validating a draft that was already inserted.
    """
    # ── Layer 1: exact ABN + invoice_number ───────────────────────────────────
    if vendor_abn and invoice_number:
        query: dict = {
            "tenant_id": tenant_id,
            "vendor_abn": vendor_abn,
            "invoice_number": invoice_number,
        }
        existing = await db.invoice_documents.find_one(query)
        if existing and str(existing.get("_id")) != (exclude_id or ""):
            return DuplicateCheckResult(
                is_duplicate=True,
                layer=1,
                existing_invoice_id=str(existing["_id"]),
                reason=f"Exact duplicate: vendor ABN {vendor_abn} + invoice# {invoice_number}",
            )

    # ── Layer 2: near-duplicate (same vendor, similar amount, close date) ─────
    if vendor_abn and issue_date:
        try:
            dt = datetime.fromisoformat(issue_date)
            low = (dt - timedelta(days=_DATE_WINDOW_DAYS)).isoformat()[:10]
            high = (dt + timedelta(days=_DATE_WINDOW_DAYS)).isoformat()[:10]
        except ValueError:
            low = high = issue_date

        candidates = await db.invoice_documents.find(
            {
                "tenant_id": tenant_id,
                "vendor_abn": vendor_abn,
                "total_cents": total_cents,
                "issue_date": {"$gte": low, "$lte": high},
            }
        ).to_list(20)

        for cand in candidates:
            if str(cand.get("_id")) == (exclude_id or ""):
                continue
            existing_inv_num = cand.get("invoice_number", "")
            candidate_inv_num = invoice_number or ""
            if _levenshtein(existing_inv_num, candidate_inv_num) <= _LEVENSHTEIN_THRESHOLD:
                return DuplicateCheckResult(
                    is_duplicate=True,
                    layer=2,
                    existing_invoice_id=str(cand["_id"]),
                    reason=(
                        f"Near-duplicate: same vendor ABN, amount, date window, "
                        f"and invoice# edit-distance ≤ {_LEVENSHTEIN_THRESHOLD}"
                    ),
                )

    # ── Layer 3: content SHA-256 ──────────────────────────────────────────────
    if content_hash:
        existing = await db.invoice_documents.find_one(
            {"tenant_id": tenant_id, "content_sha256": content_hash}
        )
        if existing and str(existing.get("_id")) != (exclude_id or ""):
            return DuplicateCheckResult(
                is_duplicate=True,
                layer=3,
                existing_invoice_id=str(existing["_id"]),
                reason="Content duplicate: normalised OCR text SHA-256 matches existing invoice",
            )

    return DuplicateCheckResult(
        is_duplicate=False, layer=None, existing_invoice_id=None, reason=None
    )
