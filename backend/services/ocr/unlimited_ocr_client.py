"""Unlimited OCR provider adapter.

# @featuretrace:document_ocr — Building-scoped OCR provider boundary and job processing.
# Layer: service
# Data flow: /ocr/jobs → ProviderRegistry.get_ocr(building_id) → OCRProvider.extract_invoice
#            → ocr_jobs / ocr_results (building-scoped).
# Related: backend/routers/ocr.py
#           backend/integrations/registry.py
#           frontend/src/pages/dashboard/OCRPage.jsx
# Scope: (building-scoped)
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

from integrations.envelopes import BankTxObserved, OCRExtractionResult, OCRFieldResult, OCRLineItem

UNLIMITED_OCR = "unlimited_ocr"


@dataclass(frozen=True)
class UnlimitedOCRConfig:
    enabled: bool
    backend: str
    endpoint: str
    api_key_present: bool
    max_pages: int
    max_bytes: int
    timeout_seconds: int
    pdf_dpi: int
    default_mode: str
    model_version: str

    @property
    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if self.backend == "private_endpoint":
            return bool(self.endpoint)
        if self.backend == "baidu_cloud":
            return self.api_key_present
        return self.backend == "local"

    @property
    def unavailable_reason(self) -> str | None:
        if self.is_available:
            return None
        if not self.enabled:
            return "UNLIMITED_OCR_ENABLED is false"
        if self.backend == "private_endpoint" and not self.endpoint:
            return "UNLIMITED_OCR_ENDPOINT is required for private_endpoint mode"
        if self.backend == "baidu_cloud" and not self.api_key_present:
            return "UNLIMITED_OCR_API_KEY is required for baidu_cloud mode"
        if self.backend not in {"disabled", "local", "baidu_cloud", "private_endpoint"}:
            return f"Unsupported UNLIMITED_OCR_BACKEND {self.backend!r}"
        return "Unlimited OCR runtime is not configured"


def get_unlimited_ocr_config() -> UnlimitedOCRConfig:
    return UnlimitedOCRConfig(
        enabled=os.environ.get("UNLIMITED_OCR_ENABLED", "false").lower() == "true",
        backend=os.environ.get("UNLIMITED_OCR_BACKEND", "disabled"),
        endpoint=os.environ.get("UNLIMITED_OCR_ENDPOINT", ""),
        api_key_present=bool(os.environ.get("UNLIMITED_OCR_API_KEY")),
        max_pages=int(os.environ.get("UNLIMITED_OCR_MAX_PAGES", "25")),
        max_bytes=int(os.environ.get("UNLIMITED_OCR_MAX_BYTES", str(20 * 1024 * 1024))),
        timeout_seconds=int(os.environ.get("UNLIMITED_OCR_TIMEOUT_SECONDS", "1200")),
        pdf_dpi=int(os.environ.get("UNLIMITED_OCR_PDF_DPI", "300")),
        default_mode=os.environ.get("UNLIMITED_OCR_DEFAULT_MODE", "auto"),
        model_version=os.environ.get("UNLIMITED_OCR_MODEL_VERSION", "baidu/Unlimited-OCR@unconfigured"),
    )


def _empty_result(provider: str, raw_text: str = "") -> OCRExtractionResult:
    empty = OCRFieldResult(value=None, confidence=0.0)
    return OCRExtractionResult(
        vendor_name=empty,
        vendor_abn=empty,
        invoice_number=empty,
        issue_date=empty,
        due_date=empty,
        total_cents=None,
        gst_cents=None,
        subtotal_cents=None,
        line_items=(),
        raw_text=raw_text,
        overall_confidence=0.0,
        is_tax_invoice=False,
        provider=provider,
    )


def normalize_unlimited_ocr_payload(payload: dict[str, Any], provider: str = UNLIMITED_OCR) -> OCRExtractionResult:
    """Normalize a sidecar/cloud OCR payload into the platform OCR contract."""

    def field(name: str) -> OCRFieldResult:
        raw = payload.get(name)
        if isinstance(raw, dict):
            value = raw.get("value")
            confidence = float(raw.get("confidence", 0.0) or 0.0)
            raw_text = raw.get("raw_text")
        else:
            value = raw
            confidence = float(payload.get(f"{name}_confidence", 0.0) or 0.0)
            raw_text = None
        confidence = max(0.0, min(1.0, confidence))
        return OCRFieldResult(value=str(value) if value not in (None, "") else None, confidence=confidence, raw_text=raw_text)

    def cents(name: str) -> int | None:
        value = payload.get(name)
        if value in (None, ""):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(round(value * 100))
        cleaned = str(value).replace("$", "").replace(",", "").strip()
        try:
            return int(round(float(cleaned) * 100))
        except ValueError:
            return None

    line_items = []
    for item in payload.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        line_items.append(OCRLineItem(
            description=str(item.get("description") or ""),
            quantity=str(item.get("quantity")) if item.get("quantity") is not None else None,
            unit_price_cents=cents_from_value(item.get("unit_price_cents", item.get("unit_price"))),
            total_cents=cents_from_value(item.get("total_cents", item.get("total"))),
            gst_cents=cents_from_value(item.get("gst_cents", item.get("gst"))),
        ))

    return OCRExtractionResult(
        vendor_name=field("vendor_name"),
        vendor_abn=field("vendor_abn"),
        invoice_number=field("invoice_number"),
        issue_date=field("issue_date"),
        due_date=field("due_date"),
        total_cents=cents("total_cents") if "total_cents" in payload else cents("total"),
        gst_cents=cents("gst_cents") if "gst_cents" in payload else cents("gst"),
        subtotal_cents=cents("subtotal_cents") if "subtotal_cents" in payload else cents("subtotal"),
        line_items=tuple(line_items),
        raw_text=str(payload.get("raw_text") or ""),
        overall_confidence=max(0.0, min(1.0, float(payload.get("overall_confidence", payload.get("confidence", 0.0)) or 0.0))),
        is_tax_invoice=bool(payload.get("is_tax_invoice", False)),
        provider=provider,
    )


def cents_from_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value * 100))
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return int(round(float(cleaned) * 100))
    except ValueError:
        return None


def raw_text_sha256(result: OCRExtractionResult) -> str:
    return hashlib.sha256((result.raw_text or "").encode("utf-8")).hexdigest()


class UnlimitedOCRProvider:
    """Adapter for a configured Unlimited OCR sidecar/cloud runtime."""

    name = UNLIMITED_OCR

    def __init__(self, config: UnlimitedOCRConfig | None = None) -> None:
        self.config = config or get_unlimited_ocr_config()

    @property
    def is_available(self) -> bool:
        return self.config.is_available

    @property
    def unavailable_reason(self) -> str | None:
        return self.config.unavailable_reason

    async def extract_invoice(self, document_bytes: bytes, mime_type: str) -> OCRExtractionResult:
        if not document_bytes:
            return _empty_result(self.name)
        if len(document_bytes) > self.config.max_bytes:
            raise RuntimeError(f"Document exceeds Unlimited OCR byte limit of {self.config.max_bytes}")
        if not self.config.is_available:
            raise RuntimeError(self.config.unavailable_reason or "Unlimited OCR unavailable")
        if self.config.backend == "private_endpoint":
            return await self._extract_with_private_endpoint(document_bytes, mime_type)
        raise RuntimeError(
            f"Unlimited OCR backend {self.config.backend!r} requires an isolated worker/client implementation before use"
        )

    async def extract_bank_statement(self, document_bytes: bytes) -> list[BankTxObserved]:
        return []

    async def _extract_with_private_endpoint(self, document_bytes: bytes, mime_type: str) -> OCRExtractionResult:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for Unlimited OCR private_endpoint mode") from exc

        headers = {}
        if self.config.api_key_present:
            headers["Authorization"] = f"Bearer {os.environ.get('UNLIMITED_OCR_API_KEY', '')}"
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.post(
                f"{self.config.endpoint.rstrip('/')}/extract",
                files={"file": ("document", document_bytes, mime_type)},
                data={
                    "mode": self.config.default_mode,
                    "max_pages": str(self.config.max_pages),
                    "pdf_dpi": str(self.config.pdf_dpi),
                },
                headers=headers,
            )
            response.raise_for_status()
            return normalize_unlimited_ocr_payload(response.json(), provider=self.name)
