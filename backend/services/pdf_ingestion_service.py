"""
PDF Ingestion Service — extracts financial data from uploaded PDFs.

Supports:
  - Budget PDFs: maps categories to levy_categories.budgeted_amount
  - Actual/Audit PDFs: maps categories to levy_categories.actual_amount

Uses pdfplumber for table extraction and difflib for fuzzy category matching.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timezone

from typing import List, Dict, Any

from database import db

logger = logging.getLogger(__name__)

# Optional pdfplumber import
try:
    import pdfplumber

    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/services/pdf_ingestion_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _hash_file(file_bytes: bytes) -> str:
    """Generated function header.

    Function: _hash_file
    Path: backend/services/pdf_ingestion_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return hashlib.sha256(file_bytes).hexdigest()


def _fuzzy_match(name: str, candidates: List[str], cutoff: float = 0.6) -> str | None:
    """Find the best matching category name using difflib."""
    from difflib import get_close_matches
    matches = get_close_matches(name.lower(), [c.lower() for c in candidates], n=1, cutoff=cutoff)
    if matches:
        # Return original case
        match_lower = matches[0]
        for c in candidates:
            if c.lower() == match_lower:
                return c
    return None


def _extract_tables_from_pdf(file_bytes: bytes) -> List[List[List[str | None]]]:
    """Extract all tables from PDF bytes using pdfplumber."""
    if not PDFPLUMBER_AVAILABLE:
        return []
    import io
    tables = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_tables = page.extract_tables()
            if page_tables:
                tables.extend(page_tables)
    return tables


def _parse_amount(value: Any) -> float | None:
    """Parse a string like '$1,234.56' or '1234.56' to float."""
    if value is None:
        return None
    s = str(value).replace("$", "").replace(",", "").replace(" ", "").strip()
    if not s or s in ("-", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _infer_categories_from_tables(
        tables: List[List[List[str | None]]],
        doc_type: str,
) -> List[Dict[str, Any]]:
    """
    Parse extracted tables into (category_name, amount, fund_type) triples.
    Looks for rows with a text name and a numeric amount.
    """
    results = []
    for table in tables:
        for row in table:
            if not row or len(row) < 2:
                continue
            # Try: first col = name, last numeric col = amount
            name_cell = row[0]
            if not name_cell or not isinstance(name_cell, str) or len(name_cell.strip()) < 3:
                continue
            # Find rightmost numeric value
            amount = None
            for cell in reversed(row[1:]):
                amount = _parse_amount(cell)
                if amount is not None:
                    break
            if amount is None or amount <= 0:
                continue
            results.append({
                "raw_name": name_cell.strip(),
                "amount": amount,
                "fund_type": None,  # inferred later
            })
    return results


async def ingest_financial_document(
        file_bytes: bytes,
        file_name: str,
        year: str,
        doc_type: str,
        uploaded_by: str,
        building_id: str,
) -> dict:
    """
    Ingest a financial PDF document. Scoped to building.
    
    Args:
        file_bytes: Raw PDF bytes
        file_name: Original file name
        year: Financial year (e.g. "2026")
        doc_type: "budget" | "actual" | "audit" | "statement"
        uploaded_by: User ID of uploader
        building_id: context
    
    Returns:
        FinancialDocumentResponse-compatible dict.
    """
    file_hash = _hash_file(file_bytes)
    doc_id = str(uuid.uuid4())

    # Deduplication check
    existing = await db.financial_documents.find_one({"building_id": building_id, "file_hash": file_hash},
                                                     {"_id": 0, "id": 1})
    if existing:
        return {
            "id": existing["id"],
            "building_id": building_id,
            "file_name": file_name,
            "financial_year": year,
            "document_type": doc_type,
            "processing_status": "duplicate",
            "categories_mapped": 0,
            "categories_new": 0,
            "errors": ["Duplicate file — already ingested."],
            "file_hash": file_hash,
            "uploaded_by": uploaded_by,
            "uploaded_at": _now(),
        }

    # Create initial record
    record = {
        "id": doc_id,
        "building_id": building_id,
        "file_name": file_name,
        "financial_year": year,
        "document_type": doc_type,
        "processing_status": "processing",
        "categories_mapped": 0,
        "categories_new": 0,
        "errors": [],
        "file_hash": file_hash,
        "uploaded_by": uploaded_by,
        "uploaded_at": _now(),
    }
    await db.financial_documents.insert_one({**record, "_id": doc_id})

    errors: List[str] = []
    categories_mapped = 0
    categories_new = 0

    if not PDFPLUMBER_AVAILABLE:
        errors.append("pdfplumber not installed — PDF parsing unavailable.")
        record["processing_status"] = "error"
        record["errors"] = errors
        await db.financial_documents.update_one({"id": doc_id}, {"$set": record})
        return record

    try:
        tables = _extract_tables_from_pdf(file_bytes)
        extracted = _infer_categories_from_tables(tables, doc_type)

        # Get existing categories for fuzzy matching
        existing_cats = await db.levy_categories.find(
            {"building_id": building_id, "year": year}, {"_id": 0, "name": 1, "fund_type": 1}
        ).to_list(100)
        existing_names = [c["name"] for c in existing_cats]

        for item in extracted:
            raw_name = item["raw_name"]
            amount = item["amount"]
            matched_name = _fuzzy_match(raw_name, existing_names)

            if matched_name:
                field = "budgeted_amount" if doc_type == "budget" else "actual_amount"
                await db.levy_categories.update_one(
                    {"building_id": building_id, "year": year, "name": matched_name},
                    {"$set": {field: amount}}
                )
                categories_mapped += 1
            else:
                # Create new category (administrative by default)
                new_cat = {
                    "id": str(uuid.uuid4()),
                    "building_id": building_id,
                    "year": year,
                    "fund_type": "administrative",
                    "name": raw_name,
                    "budgeted_amount": amount if doc_type == "budget" else 0,
                    "actual_amount": amount if doc_type != "budget" else 0,
                    "status": "proposed",
                    "created_at": _now(),
                    "updated_at": _now(),
                }
                await db.levy_categories.insert_one(new_cat)
                categories_new += 1
                existing_names.append(raw_name)

        processing_status = "complete"
    except Exception as e:
        errors.append(str(e))
        processing_status = "error"

    record.update({
        "processing_status": processing_status,
        "categories_mapped": categories_mapped,
        "categories_new": categories_new,
        "errors": errors,
    })
    await db.financial_documents.update_one({"id": doc_id}, {"$set": record})

    # Trigger anomaly scan after ingestion
    try:
        from services.anomaly_service import detect_financial_anomalies
        await detect_financial_anomalies(year, building_id)
    except Exception as exc:
        logger.warning("pdf_ingestion_service: post-ingest anomaly scan failed for %s/%s: %s", building_id, year, exc)

    return record
