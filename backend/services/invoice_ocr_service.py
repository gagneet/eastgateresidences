# @featuretrace:invoices — OCR extraction service: Claude Vision, Mindee, Tesseract, auto-waterfall.
# Layer: service
# Data flow: routers/invoices.py → parse_invoice(provider) → selected OCR engine.
# Related: backend/routers/invoices.py
#           backend/models/invoice_models.py
# Scope: (building-scoped)

"""
Invoice OCR Service — Claude Vision, Mindee, Tesseract, and auto-waterfall.

Provider selection:
  - "auto"          Try Claude Vision → Mindee → Tesseract (best accuracy)
  - "claude_vision" Anthropic Claude Vision API only
  - "mindee"        Mindee Invoice v4 API only
  - "tesseract"     Local Tesseract OCR only (no external API calls)
  - "unlimited_ocr" Advanced provider via isolated worker/sidecar when configured

Claude Vision sends the PDF/image directly as a document content block.
Tesseract requires system packages: tesseract-ocr + poppler-utils.
"""
import base64
import io
import json
import logging
import os
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MINDEE_API_KEY = os.environ.get("MINDEE_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"

VALID_OCR_PROVIDERS = {"auto", "claude_vision", "mindee", "tesseract", "unlimited_ocr"}

_EXTRACTION_PROMPT = """Extract all invoice or quote details from this document.
Return ONLY a single valid JSON object — no markdown fences, no explanation.

{
  "vendor_name": "company or person name",
  "abn": "XX XXX XXX XXX or null",
  "invoice_number": "reference number or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "line_items": [
    {"description": "item description", "qty": 1.0, "unit_price": 0.00, "total": 0.00}
  ],
  "subtotal": 0.00,
  "gst": 0.00,
  "total": 0.00,
  "suggested_fund": "admin",
  "confidence": 0.95
}

Rules:
- All monetary amounts as decimal numbers in AUD.
- If GST is not itemised, estimate it as total / 11 (standard Australian 10% GST).
- suggested_fund: use "admin" for maintenance/repairs/services/insurance;
  use "sinking" for capital works, major replacements, structural repairs.
- confidence: 0.0–1.0 reflecting how clearly the document is legible."""


async def extract_with_claude(file_bytes: bytes, file_type: str) -> Dict[str, Any]:
    """Call Claude Vision API and return parsed invoice dict."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed — run: pip install anthropic")

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    if file_type == "application/pdf":
        doc_block: Dict[str, Any] = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
            },
        }
    else:
        media_type = file_type if file_type in (
            "image/jpeg", "image/png", "image/webp"
        ) else "image/jpeg"
        doc_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
            },
        }

    message = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [doc_block, {"type": "text", "text": _EXTRACTION_PROMPT}],
            }
        ],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    parsed = json.loads(raw)
    parsed["ocr_source"] = "claude_vision"
    return parsed


async def extract_with_mindee(file_bytes: bytes) -> Dict[str, Any]:
    """Call Mindee Invoice v4 API and return parsed invoice dict."""
    try:
        import mindee
        from mindee import product as mindee_product
    except ImportError:
        raise RuntimeError("mindee package not installed — run: pip install mindee")

    if not MINDEE_API_KEY:
        raise RuntimeError("MINDEE_API_KEY environment variable is not set")

    import io as _io

    client = mindee.Client(api_key=MINDEE_API_KEY)
    input_doc = client.source_from_bytes(file_bytes, "invoice.pdf")
    result = client.parse(mindee_product.InvoiceV4, input_doc)
    pred = result.document.inference.prediction

    line_items = []
    for item in (pred.line_items or []):
        line_items.append({
            "description": str(getattr(item, "description", "") or ""),
            "qty": float(getattr(item, "quantity", 1) or 1),
            "unit_price": float(getattr(item, "unit_price", 0) or 0),
            "total": float(getattr(item, "total_amount", 0) or 0),
        })

    def _val(field):
        """Generated function header.

        Function: _val
        Path: backend/services/invoice_ocr_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return str(field.value) if field and getattr(field, "value", None) else None

    abn = None
    regs = getattr(pred, "supplier_company_registrations", [])
    if regs:
        abn = str(regs[0].value) if getattr(regs[0], "value", None) else None

    return {
        "vendor_name": _val(getattr(pred, "supplier_name", None)) or "",
        "abn": abn,
        "invoice_number": _val(getattr(pred, "invoice_number", None)),
        "invoice_date": _val(getattr(pred, "date", None)),
        "due_date": _val(getattr(pred, "due_date", None)),
        "line_items": line_items,
        "subtotal": float(getattr(getattr(pred, "total_net", None), "value", None) or 0),
        "gst": float(getattr(getattr(pred, "total_tax", None), "value", None) or 0),
        "total": float(getattr(getattr(pred, "total_amount", None), "value", None) or 0),
        "suggested_fund": "admin",
        "confidence": 0.8,
        "ocr_source": "mindee",
    }


async def extract_with_tesseract(file_bytes: bytes, file_type: str) -> Dict[str, Any]:
    """Run Tesseract OCR locally — no external API calls.

    Requires system packages:
      sudo apt install tesseract-ocr poppler-utils
    Python deps:
      pip install pytesseract pdf2image pillow
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise RuntimeError(
            "pytesseract + Pillow not installed — run: pip install pytesseract pillow"
        )

    pages_text = []

    if file_type == "application/pdf":
        try:
            from pdf2image import convert_from_bytes
        except ImportError:
            raise RuntimeError(
                "pdf2image not installed — run: pip install pdf2image "
                "(also needs: sudo apt install poppler-utils)"
            )
        try:
            images = convert_from_bytes(file_bytes, dpi=200)
        except Exception as exc:
            raise RuntimeError(f"PDF→image conversion failed: {exc}")
        for img in images:
            pages_text.append(pytesseract.image_to_string(img))
    else:
        try:
            img = Image.open(io.BytesIO(file_bytes))
            pages_text.append(pytesseract.image_to_string(img))
        except Exception as exc:
            raise RuntimeError(f"Tesseract image OCR failed: {exc}")

    full_text = "\n".join(pages_text)
    return _parse_tesseract_text(full_text)


def _parse_tesseract_text(text: str) -> Dict[str, Any]:
    """Heuristic regex extraction from raw Tesseract text output."""

    def _find(patterns, default=None):
        """Generated function header.

        Function: _find
        Path: backend/services/invoice_ocr_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return default

    def _money(patterns):
        """Generated function header.

        Function: _money
        Path: backend/services/invoice_ocr_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        raw = _find(patterns)
        if raw is None:
            return 0.0
        cleaned = re.sub(r"[,$\s]", "", raw)
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    vendor = _find([
        r"(?:from|vendor|supplier|company)[:\s]+([A-Za-z][\w\s&.,'-]{2,60})",
        r"^([A-Za-z][\w\s&.,'-]{5,50})\n",
    ], "")

    abn_raw = _find([r"ABN[:\s#]*(\d[\d\s]{9,12}\d)"])
    abn = re.sub(r"\s+", " ", abn_raw).strip() if abn_raw else None

    invoice_number = _find([
        r"invoice\s*(?:no|num|number|#)[.:\s#]*([A-Z0-9\-]{2,20})",
        r"\binv[#\s]*([A-Z0-9\-]{3,20})",
    ])

    invoice_date_raw = _find([
        r"(?:invoice\s+)?date[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
        r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})",
    ])
    invoice_date = _normalise_date(invoice_date_raw) if invoice_date_raw else None

    due_date_raw = _find([
        r"due\s+(?:date|by)[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
    ])
    due_date = _normalise_date(due_date_raw) if due_date_raw else None

    total = _money([
        # "Total: $495" / "Total Due: $495" / "Total Amount: $495" (most common AU formats)
        r"total\s*(?:due|amount|incl\.?\s*gst)?\s*:?\s*\$?([\d,]+\.?\d*)",
        r"amount\s+(?:due|payable)\s*:?\s*\$?([\d,]+\.?\d*)",
    ])
    gst = _money([
        r"gst[:\s]+\$?([\d,]+\.?\d*)",
        r"tax[:\s]+\$?([\d,]+\.?\d*)",
    ])
    subtotal = _money([
        r"subtotal[:\s]+\$?([\d,]+\.?\d*)",
        r"net\s+amount[:\s]+\$?([\d,]+\.?\d*)",
    ])

    if subtotal == 0.0 and total > 0:
        subtotal = round(total - gst, 2)
    if gst == 0.0 and total > 0:
        gst = round(total / 11, 2)

    text_lower = text.lower()
    capital_kw = ["capital", "replacement", "structural", "roof", "major", "renovation"]
    suggested_fund = "sinking" if any(k in text_lower for k in capital_kw) else "admin"

    fields_found = sum(bool(v) for v in [vendor, abn, invoice_number, invoice_date, total])
    confidence = min(0.3 + (fields_found * 0.08), 0.70)

    return {
        "vendor_name": vendor,
        "abn": abn,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "line_items": [],
        "subtotal": subtotal,
        "gst": gst,
        "total": total,
        "suggested_fund": suggested_fund,
        "confidence": round(confidence, 2),
        "ocr_source": "tesseract",
    }


def _normalise_date(raw: str) -> str:
    """Convert DD/MM/YYYY or DD-MM-YYYY to YYYY-MM-DD."""
    m = re.match(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})", raw.strip())
    if not m:
        return raw
    day, month, year = m.group(1), m.group(2), m.group(3)
    if len(year) == 2:
        year = "20" + year
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return raw


def _empty_result() -> Dict[str, Any]:
    """Generated function header.

    Function: _empty_result
    Path: backend/services/invoice_ocr_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "vendor_name": "",
        "abn": None,
        "invoice_number": None,
        "invoice_date": None,
        "due_date": None,
        "line_items": [],
        "subtotal": 0.0,
        "gst": 0.0,
        "total": 0.0,
        "suggested_fund": "admin",
        "confidence": 0.0,
        "ocr_source": "manual",
    }


def _provider_result_to_invoice_dict(result) -> Dict[str, Any]:
    """Convert OCRExtractionResult into the legacy invoice preview contract."""
    return {
        "vendor_name": result.vendor_name.value or "",
        "abn": result.vendor_abn.value,
        "invoice_number": result.invoice_number.value,
        "invoice_date": result.issue_date.value,
        "due_date": result.due_date.value,
        "line_items": [
            {
                "description": item.description,
                "qty": float(item.quantity or 1),
                "unit_price": (item.unit_price_cents or 0) / 100,
                "total": (item.total_cents or 0) / 100,
            }
            for item in result.line_items
        ],
        "subtotal": (result.subtotal_cents or 0) / 100,
        "gst": (result.gst_cents or 0) / 100,
        "total": (result.total_cents or 0) / 100,
        "suggested_fund": "admin",
        "confidence": result.overall_confidence,
        "ocr_source": result.provider,
    }


async def parse_invoice(
        file_bytes: bytes,
        file_type: str,
        provider: str = "auto",
) -> Dict[str, Any]:
    """Run OCR on invoice bytes using the specified provider.

    provider:
      "auto"         — Claude Vision → Mindee → Tesseract waterfall (default)
      "claude_vision" — Claude Vision only; falls back to empty on failure
      "mindee"        — Mindee only; falls back to empty on failure
      "tesseract"     — Tesseract only; falls back to empty on failure
      "unlimited_ocr" — Unlimited OCR via configured provider; falls back to empty on failure

    Never raises — always returns a usable dict.
    """
    if provider == "unlimited_ocr":
        try:
            from services.ocr.unlimited_ocr_client import UnlimitedOCRProvider

            unlimited = UnlimitedOCRProvider()
            result = await unlimited.extract_invoice(file_bytes, file_type)
            return _provider_result_to_invoice_dict(result)
        except Exception as exc:
            logger.warning("Unlimited OCR extraction failed: %s", exc)
            return _empty_result()

    if provider == "claude_vision":
        if not ANTHROPIC_API_KEY:
            logger.warning("claude_vision selected but ANTHROPIC_API_KEY not set; returning empty")
            return _empty_result()
        try:
            return await extract_with_claude(file_bytes, file_type)
        except Exception as exc:
            logger.warning("Claude Vision extraction failed: %s", exc)
            return _empty_result()

    if provider == "mindee":
        if not MINDEE_API_KEY:
            logger.warning("mindee selected but MINDEE_API_KEY not set; returning empty")
            return _empty_result()
        try:
            return await extract_with_mindee(file_bytes)
        except Exception as exc:
            logger.warning("Mindee extraction failed: %s", exc)
            return _empty_result()

    if provider == "tesseract":
        try:
            return await extract_with_tesseract(file_bytes, file_type)
        except Exception as exc:
            logger.warning("Tesseract extraction failed: %s", exc)
            return _empty_result()

    # "auto" waterfall: Claude Vision → Mindee → Tesseract
    if ANTHROPIC_API_KEY:
        try:
            result = await extract_with_claude(file_bytes, file_type)
            if float(result.get("confidence", 0)) >= 0.5:
                return result
            logger.warning("Claude Vision low confidence %.2f, trying Mindee", result.get("confidence"))
        except json.JSONDecodeError as exc:
            logger.warning("Claude Vision returned non-JSON: %s", exc)
        except Exception as exc:
            logger.warning("Claude Vision extraction failed: %s", exc)

    if MINDEE_API_KEY:
        try:
            return await extract_with_mindee(file_bytes)
        except Exception as exc:
            logger.warning("Mindee extraction failed: %s", exc)

    try:
        result = await extract_with_tesseract(file_bytes, file_type)
        if float(result.get("confidence", 0)) > 0.0:
            return result
    except Exception as exc:
        logger.warning("Tesseract extraction failed: %s", exc)

    logger.warning("All OCR engines failed or unconfigured — returning empty scaffold")
    return _empty_result()
