"""
Pydantic models for the Invoice OCR feature.

Collections used: invoice_documents (building-scoped)
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class InvoiceLineItem(BaseModel):
    description: str = ""
    qty: float = 1.0
    unit_price: float = 0.0
    total: float = 0.0


class ParsedInvoiceData(BaseModel):
    vendor_name: str = ""
    abn: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    line_items: List[InvoiceLineItem] = []
    subtotal: float = 0.0
    gst: float = 0.0
    total: float = 0.0
    suggested_fund: Optional[str] = "admin"
    suggested_category: Optional[str] = None
    confidence: float = 0.0


class InvoiceConfirmRequest(BaseModel):
    vendor_name: str
    abn: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: str
    due_date: Optional[str] = None
    line_items: List[InvoiceLineItem] = []
    subtotal: float
    gst: float = 0.0
    total: float
    fund: Literal["admin", "sinking"] = "admin"
    category: Optional[str] = None
    document_type: str = "invoice"


class InvoiceDocumentResponse(BaseModel):
    id: str
    building_id: str
    status: str
    document_type: str
    file_name: str
    file_type: str
    file_size_bytes: int
    ocr_source: Optional[str] = None
    ocr_confidence: float = 0.0
    parsed_data: Optional[Dict[str, Any]] = None
    confirmed_data: Optional[Dict[str, Any]] = None
    transaction_id: Optional[str] = None
    receipt_generated: bool = False
    receipt_data: Optional[str] = None
    created_at: str
    created_by: str
    confirmed_at: Optional[str] = None
    confirmed_by: Optional[str] = None


class InvoiceListResponse(BaseModel):
    invoices: List[InvoiceDocumentResponse]
    total: int
