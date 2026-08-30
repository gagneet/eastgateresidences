"""
backend/integrations/envelopes.py — Immutable event envelope types.

# @featuretrace:financial_integration_v2 — adapter boundary types.
# Layer: service
# Data flow: provider SDK → adapter → EventEnvelope → integration_inbox → domain (building-scoped).
# Related: protocols.py (Protocol definitions), registry.py (provider DI).

Every adapter translates a provider-specific payload into one of these types before
any domain code sees it. No provider SDK type ever appears in backend/domain/ or
backend/services/. This is the single most important invariant in the integration layer.

All models are frozen (immutable after construction). Money fields use Cents (integer).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from domain import Cents

# ── ULID generator (no external dependency) ──────────────────────────────────

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(n: int, num_chars: int) -> str:
    """Generated function header.

    Function: _encode_crockford
    Path: backend/integrations/envelopes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result: list[str] = []
    for _ in range(num_chars):
        result.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return "".join(reversed(result))


def generate_ulid() -> str:
    """Generate a sortable, time-encoded, globally unique ID (ULID spec)."""
    ts_ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode_crockford(ts_ms, 10) + _encode_crockford(rand, 16)


# ── Idempotency key ───────────────────────────────────────────────────────────

def compute_idempotency_key(tenant_id: str, raw: dict[str, Any]) -> str:
    """SHA-256 of canonical (sorted) JSON of the raw provider payload + tenant_id.

    Re-delivering the same payload always produces the same key, so the
    integration_inbox unique index absorbs the duplicate without error.
    """
    canonical = json.dumps(
        {"tenant_id": tenant_id, **raw},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Core envelope ─────────────────────────────────────────────────────────────

class EventEnvelope(BaseModel, frozen=True):
    """Immutable wrapper produced by every adapter at the provider boundary.

    Fields:
        event_id          Platform-generated ULID. Sortable, globally unique.
        provider_event_id Provider's own identifier (for debugging and audit).
        idempotency_key   SHA-256 of canonical payload — duplicate detector.
        tenant_id         building_id the event belongs to. NEVER absent.
        occurred_at       When the event happened at source (e.g. transaction date).
        received_at       When the platform created the envelope (pipeline latency).
        event_type        String tag identifying the event variant.
        raw               Verbatim provider payload preserved for audit and replay.
    """

    event_id: str = Field(default_factory=generate_ulid)
    provider_event_id: str
    idempotency_key: str
    tenant_id: str
    occurred_at: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    raw: dict[str, Any]

    @model_validator(mode="after")
    def _tenant_required(self) -> "EventEnvelope":
        """Generated function header.

        Function: EventEnvelope._tenant_required
        Path: backend/integrations/envelopes.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not self.tenant_id:
            raise ValueError("tenant_id is required on every EventEnvelope")
        return self


# ── Bank feed types ───────────────────────────────────────────────────────────

class BankTxObserved(BaseModel, frozen=True):
    """A single bank transaction as seen by a BankFeedProvider.

    amount_cents is signed: positive = credit (money in), negative = debit
    (money out). Follows the accounting convention throughout the platform.
    """

    provider_txn_id: str  # Provider's unique ID for this transaction
    tenant_id: str  # building_id
    account_ref: str  # Internal reference to the trust bank account
    occurred_at: datetime  # Transaction date (value date at source)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    amount_cents: Cents  # Signed integer cents
    description: str  # Raw transaction description from bank
    balance_after_cents: Optional[Cents] = None  # Running balance if available
    bpay_crn: Optional[str] = None  # Extracted BPAY CRN if found in description
    osko_e2e_id: Optional[str] = None  # NPP End-to-End ID if found
    lot_ref_raw: Optional[str] = None  # Raw lot/unit reference if parseable
    is_test_data: bool = False
    # Optional provenance payload for providers that reconstruct rather than
    # observe a transaction (e.g. Demo Bank's historical-reconstruction path).
    # Real providers (a real bank feed, CSV upload of an actual statement) omit
    # this entirely — it is not part of the provider-neutral contract's required
    # shape, only an optional extension real providers never need to populate.
    # Recognised keys when present: transaction_origin, reconstruction_batch_id,
    # reconstruction_version, assumption_code, levy_component, source_document_ids.
    metadata: Optional[dict[str, Any]] = None


class BankAccountMetadata(BaseModel, frozen=True):
    """Metadata for a bank account the provider has consent to observe."""

    account_ref: str  # Platform's internal reference
    bsb: str
    account_number: str
    account_name: str
    institution_name: str
    currency: str = "AUD"
    tenant_id: str


# ── Biller types ──────────────────────────────────────────────────────────────

class BillerReference(BaseModel, frozen=True):
    """A CRN or DRN allocated by a BillerProvider for a given lot+instalment."""

    crn: str  # 13-digit CRN (or DRN for DEFT)
    biller_code: str  # BPAY biller code (e.g. "96503" for DEFT)
    check_digit_algorithm: str  # "MOD10V05" or "MOD10V01" (Luhn)
    display_format: str  # Human-readable, e.g. "0013195 001 001 4"
    scheme_id: str  # building_id-derived scheme identifier
    lot_id: str
    instalment_seq: int
    tenant_id: str


class BillerPaymentObserved(BaseModel, frozen=True):
    """A single per-lot payment decomposition from the biller's feedback file."""

    provider_feedback_id: str
    crn: str
    lot_id: str
    tenant_id: str
    occurred_at: datetime
    amount_cents: Cents
    channel: str  # "BPAY", "EFT", "DEFT", etc.
    is_reversal: bool = False
    is_test_data: bool = False


class BillerBatchRecord(BaseModel, frozen=True):
    """Consolidated settlement record the biller credits to the trust account."""

    batch_date: datetime
    total_amount_cents: Cents
    tenant_id: str
    provider_batch_id: str
    transaction_count: int


# ── Payment initiation types ──────────────────────────────────────────────────

class PaymentLine(BaseModel, frozen=True):
    """A single disbursement line within a payment run."""

    line_id: str = Field(default_factory=generate_ulid)
    payee_name: str  # Account title (max 32 chars for ABA)
    bsb: str  # 6-digit BSB, no hyphen
    account_number: str
    amount_cents: Cents  # Signed: positive = credit (payment out), negative = debit (direct debit / reversal)
    lodgement_reference: str  # Invoice number or reference (max 18 chars ABA)
    remitter_name: str  # Payer name shown to payee (max 16 chars ABA)
    transaction_code: str = "53"  # 53 = externally initiated credit


class PaymentRun(BaseModel, frozen=True):
    """A batch of outbound disbursements submitted to a PaymentInitiationProvider."""

    run_id: str = Field(default_factory=generate_ulid)
    tenant_id: str
    description: str  # 12-char ABA Descriptive Record description
    processing_date: datetime  # Date bank should process the file
    originating_bsb: str
    originating_account: str
    originating_account_name: str
    bank_abbreviation: str  # 3-char bank code (e.g. "CBA")
    apca_user_id: str  # 7-digit APCA user ID
    lines: tuple[PaymentLine, ...]
    idempotency_key: str = Field(default_factory=generate_ulid)


class PaymentRunAccepted(BaseModel, frozen=True):
    """Provider acknowledgment of a submitted payment run."""

    provider_reference: str
    run_id: str
    status: str  # "accepted", "pending", "queued"
    aba_file_path: Optional[str] = None
    aba_sha256: Optional[str] = None
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Accounting provider types ─────────────────────────────────────────────────

class AccountingBill(BaseModel, frozen=True):
    """A bill record from an accounting provider (Xero, MYOB, etc.)."""

    provider_bill_id: str
    vendor_name: str
    vendor_abn: Optional[str] = None
    invoice_number: Optional[str] = None
    issue_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    total_cents: Cents
    gst_cents: Cents = 0
    currency: str = "AUD"
    line_items: tuple[dict[str, Any], ...] = ()
    raw: dict[str, Any] = Field(default_factory=dict)


# ── OCR types ─────────────────────────────────────────────────────────────────

class OCRFieldResult(BaseModel, frozen=True):
    """A single extracted field with confidence score."""

    value: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    raw_text: Optional[str] = None


class OCRLineItem(BaseModel, frozen=True):
    description: str
    quantity: Optional[str] = None
    unit_price_cents: Optional[Cents] = None
    total_cents: Optional[Cents] = None
    gst_cents: Optional[Cents] = None


class OCRExtractionResult(BaseModel, frozen=True):
    """Invoice extraction result from an OCRProvider.

    All monetary fields are integer cents. The adapter coerces floats at the
    boundary before constructing this model.
    """

    vendor_name: OCRFieldResult
    vendor_abn: OCRFieldResult
    invoice_number: OCRFieldResult
    issue_date: OCRFieldResult
    due_date: OCRFieldResult
    total_cents: Optional[Cents] = None
    gst_cents: Optional[Cents] = None
    subtotal_cents: Optional[Cents] = None
    line_items: tuple[OCRLineItem, ...] = ()
    raw_text: str = ""
    overall_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    is_tax_invoice: bool = False
    provider: str = "unknown"
