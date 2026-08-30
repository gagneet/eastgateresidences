"""
backend/integrations/abn_validator.py — ABN validation against ATO ABR JSON service.

# @featuretrace:ap_automation — ABN validation gate in the AP invoice pipeline.
# Layer: service
# Data flow: supplier upload → validate_abn() → abn_validation_cache (90-day TTL).
# Related: backend/routers/ap_supplier_upload.py
#           backend/domain/invoice_lifecycle.py
# Scope: (building-scoped)

Validation rules:
  - ABN must be 11 digits after stripping spaces.
  - Weighted checksum must equal 0 mod 89 (ATO algorithm).
  - ATO ABR JSON service confirms entity status and GST registration.
  - Results are cached for 90 days (TTL index on abn_validation_cache).
  - Supply > A$75 without a valid ABN triggers the 47% no-ABN withholding flag
    under ATO Taxation Ruling TR 2000/5.

Configure via environment:
  ABR_API_GUID  — GUID issued by abr.business.gov.au (required for live calls)
                  If absent, validate_abn() returns a cached/offline result only.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_ABN_RE = re.compile(r"^\d{11}$")

# ATO ABR JSON endpoint (no auth — GUID is a query param)
_ABR_BASE = "https://abr.business.gov.au/json/AbnDetails.aspx"

# ATO weighted check digit multipliers (11 positions)
_ABN_WEIGHTS = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]

# Supply threshold (cents) above which no-ABN withholding applies
_WITHHOLDING_THRESHOLD_CENTS = 7_500


@dataclass(frozen=True)
class ABNValidationResult:
    abn: str
    is_valid: bool
    entity_name: Optional[str]
    gst_registered: bool
    abn_status: str  # "Active", "Cancelled", "NotFound", "Offline"
    cached: bool
    withholding_required: bool  # True when supply > $75 ex-GST and ABN is invalid


def _normalise_abn(raw: str) -> str:
    """Strip spaces and non-digits from raw ABN string."""
    return re.sub(r"\D", "", raw or "")


def _check_abn_format(digits: str) -> bool:
    """Apply ATO weighted checksum: (d[0]-1)*w[0] + d[1]*w[1] + ... mod 89 == 0."""
    if not _ABN_RE.match(digits):
        return False
    total = 0
    for i, (d, w) in enumerate(zip(digits, _ABN_WEIGHTS)):
        digit = int(d) - (1 if i == 0 else 0)
        total += digit * w
    return total % 89 == 0


async def validate_abn(
        abn_raw: str,
        supply_amount_ex_gst_cents: int = 0,
        *,
        db=None,
) -> ABNValidationResult:
    """
    Validate an ABN and return entity details.

    Checks cache first. Falls back to ATO ABR JSON service if cache misses
    and ABR_API_GUID is configured. Always validates the checksum locally
    regardless of network availability.

    Args:
        abn_raw: Raw ABN string (may contain spaces).
        supply_amount_ex_gst_cents: Supply value in cents ex-GST. Used to
            determine whether the 47% withholding flag applies.
        db: Motor database handle (injected by callers; not imported globally
            to avoid circular imports).
    """
    abn = _normalise_abn(abn_raw)

    # Local checksum — no network needed
    fmt_ok = _check_abn_format(abn)
    if not fmt_ok:
        return ABNValidationResult(
            abn=abn,
            is_valid=False,
            entity_name=None,
            gst_registered=False,
            abn_status="InvalidFormat",
            cached=False,
            withholding_required=supply_amount_ex_gst_cents > _WITHHOLDING_THRESHOLD_CENTS,
        )

    # Cache lookup
    if db is not None:
        cached_doc = await db.abn_validation_cache.find_one({"abn": abn})
        if cached_doc:
            return ABNValidationResult(
                abn=abn,
                is_valid=cached_doc.get("is_valid", False),
                entity_name=cached_doc.get("entity_name"),
                gst_registered=cached_doc.get("gst_registered", False),
                abn_status=cached_doc.get("abn_status", "Unknown"),
                cached=True,
                withholding_required=(
                        not cached_doc.get("is_valid", False)
                        and supply_amount_ex_gst_cents > _WITHHOLDING_THRESHOLD_CENTS
                ),
            )

    # Live ABR lookup
    guid = os.getenv("ABR_API_GUID", "")
    result = await _fetch_abr(abn, guid)

    # Cache the result
    if db is not None:
        await _cache_result(db, abn, result)

    withholding = (
            not result["is_valid"]
            and supply_amount_ex_gst_cents > _WITHHOLDING_THRESHOLD_CENTS
    )
    return ABNValidationResult(
        abn=abn,
        is_valid=result["is_valid"],
        entity_name=result.get("entity_name"),
        gst_registered=result.get("gst_registered", False),
        abn_status=result.get("abn_status", "Unknown"),
        cached=False,
        withholding_required=withholding,
    )


async def _fetch_abr(abn: str, guid: str) -> dict:
    """Call ABR JSON service. Returns a normalised result dict."""
    if not guid:
        logger.warning("ABR_API_GUID not configured — returning Offline status for ABN %s", abn)
        return {"is_valid": True, "entity_name": None, "gst_registered": False, "abn_status": "Offline"}

    try:
        import httpx
        url = f"{_ABR_BASE}?abn={abn}&guid={guid}&callback=callback"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            # ABR wraps JSON in callback(...), strip wrapper
            text = resp.text.strip()
            if text.startswith("callback(") and text.endswith(")"):
                text = text[9:-1]
            import json
            data = json.loads(text)
        return _parse_abr_response(data)
    except Exception as exc:
        logger.warning("ABR lookup failed for ABN %s: %s", abn, exc)
        return {"is_valid": True, "entity_name": None, "gst_registered": False, "abn_status": "Offline"}


def _parse_abr_response(data: dict) -> dict:
    """Normalise the ABR JSON response to internal dict."""
    abn_status = data.get("AbnStatus", "")
    is_valid = abn_status == "Active"
    entity = data.get("EntityName") or data.get("MainName", {}).get("OrganisationName", "")
    gst = bool(data.get("Gst"))
    return {
        "is_valid": is_valid,
        "entity_name": entity or None,
        "gst_registered": gst,
        "abn_status": abn_status or "NotFound",
    }


async def _cache_result(db, abn: str, result: dict) -> None:
    """Upsert result into abn_validation_cache (TTL index handles expiry)."""
    try:
        await db.abn_validation_cache.update_one(
            {"abn": abn},
            {"$set": {
                "abn": abn,
                "is_valid": result["is_valid"],
                "entity_name": result.get("entity_name"),
                "gst_registered": result.get("gst_registered", False),
                "abn_status": result.get("abn_status", "Unknown"),
                "cached_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except Exception as exc:
        logger.warning("Failed to cache ABN result for %s: %s", abn, exc)
