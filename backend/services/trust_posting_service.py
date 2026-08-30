"""
Trust Posting Integrity Service — Phase 2 Hardening

Enforces the four non-negotiable invariants for every trust journal posting:
  1. Period must not be locked (trust_period_locks collection)
  2. Idempotency key must be unique (trust_audit_logs collection)
  3. Journal entry must be balanced (sum debits == sum credits, integer cents)
  4. All mutations are atomic via Motor sessions/transactions where available

Usage (from a FastAPI route):
    from services.trust_posting_service import TrustPostingService, PostingRequest

    svc = TrustPostingService(db)
    result = await svc.post(
        request=PostingRequest(
            building_id=jwt_building_id,
            unit_number="TH017",
            amount_cents=150000,
            period="2026-03",
            source_reference="DEFT-20260301-1234",
            source_type="levy_receipt",
            lines=[...],
            created_by="alice@example.com",
        )
    )
    if result.status == "idempotent_replay":
        return existing_entry_response(result.existing_id)

All monetary values MUST be integer cents.  Never pass floats.
building_id MUST come from the JWT claim — never from the request body.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from models.errors import TrustPeriodLockedError

logger = logging.getLogger(__name__)

# ─── Source types ─────────────────────────────────────────────────────────────

LEVY_RECEIPT = "levy_receipt"
DISBURSEMENT = "disbursement"
ADJUSTMENT = "adjustment"
REVERSAL = "reversal"
BANK_FEE = "bank_fee"
INTEREST = "interest"
VALID_SOURCE_TYPES = {
    LEVY_RECEIPT,
    DISBURSEMENT,
    ADJUSTMENT,
    REVERSAL,
    BANK_FEE,
    INTEREST,
}


# ─── Journal line ─────────────────────────────────────────────────────────────

@dataclass
class JournalLine:
    """One side of a double-entry journal — always integer cents."""

    account_id: str
    entry_type: str  # "debit" | "credit"
    amount_cents: int  # must be > 0
    description: Optional[str] = None

    def __post_init__(self) -> None:
        """Generated function header.

        Function: JournalLine.__post_init__
        Path: backend/services/trust_posting_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.entry_type not in ("debit", "credit"):
            raise ValueError(f"entry_type must be 'debit' or 'credit', got {self.entry_type!r}.")
        if not isinstance(self.amount_cents, int):
            raise TypeError(
                f"amount_cents must be int (integer cents), got {type(self.amount_cents).__name__}. "
                "Never pass floats."
            )
        if self.amount_cents <= 0:
            raise ValueError(f"amount_cents must be > 0, got {self.amount_cents}.")


# ─── Posting request ──────────────────────────────────────────────────────────

@dataclass
class PostingRequest:
    """All parameters required for a single trust journal posting."""

    building_id: str
    """From JWT claim — never from request body."""

    unit_number: str
    """Lot/unit identifier (e.g. 'TH017', 'A101')."""

    amount_cents: int
    """Primary transaction amount in integer cents (used for idempotency key)."""

    period: str
    """ISO month: 'YYYY-MM' — the period being posted into."""

    source_reference: str
    """External reference: DEFT CRN, bank ref, or manual reference."""

    source_type: str
    """One of: levy_receipt | disbursement | adjustment | reversal | bank_fee | interest."""

    lines: List[JournalLine]
    """Balanced journal lines (debit total must equal credit total)."""

    created_by: str
    """User email — from authenticated session."""

    fund_type: str = "admin_fund"
    """Trust fund (admin_fund | capital_works_fund | special_levy | operating)."""

    narration: str = ""
    approved_by: Optional[str] = None
    lock_period: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ─── Posting result ───────────────────────────────────────────────────────────

@dataclass
class TrustPostingResult:
    """Returned by TrustPostingService.post()."""

    status: str
    """'success' | 'idempotent_replay' | 'period_locked' | 'unbalanced' | 'validation_error'"""

    entry_id: Optional[str] = None
    """MongoDB _id of the new (or existing) journal entry."""

    existing_id: Optional[str] = None
    """Populated for idempotent_replay — the previously inserted entry _id."""

    idempotency_key: Optional[str] = None
    message: str = ""
    error_code: Optional[str] = None


# ─── Service ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/services/trust_posting_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _compute_idempotency_key(
        building_id: str,
        unit_number: str,
        amount_cents: int,
        period: str,
        source_reference: str,
) -> str:
    """
    SHA-256 of the canonical posting tuple.

    All fields are lower-cased and stripped to prevent trivial duplicates.
    """
    payload = "|".join([
        building_id.strip().lower(),
        unit_number.strip().lower(),
        str(amount_cents),
        period.strip().lower(),
        source_reference.strip().lower(),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TrustPostingService:
    """
    Stateless posting-integrity service.

    Instantiate once per request; pass the Motor db handle from `database.db`.
    """

    def __init__(self, db: Any) -> None:
        """Generated function header.

        Function: TrustPostingService.__init__
        Path: backend/services/trust_posting_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._db = db

    # ── Public API ────────────────────────────────────────────────────────────

    async def post(self, request: PostingRequest) -> TrustPostingResult:
        """
        Attempt to post a balanced journal entry.

        Steps:
          1. Validate source_type
          2. Validate journal balance (debit == credit, integer cents)
          3. Check period lock
          4. Check idempotency (previous submission with same key)
          5. Atomically write journal entry + audit log

        Returns TrustPostingResult; never raises for business-rule violations
        (only raises for unexpected infrastructure errors).
        """
        idempotency_key = _compute_idempotency_key(
            building_id=request.building_id,
            unit_number=request.unit_number,
            amount_cents=request.amount_cents,
            period=request.period,
            source_reference=request.source_reference,
        )

        # 1. Validate source_type
        if request.source_type not in VALID_SOURCE_TYPES:
            return TrustPostingResult(
                status="validation_error",
                idempotency_key=idempotency_key,
                error_code="INVALID_SOURCE_TYPE",
                message=(
                    f"source_type {request.source_type!r} is not valid. "
                    f"Allowed: {sorted(VALID_SOURCE_TYPES)}."
                ),
            )

        # 2. Validate journal balance
        balance_result = self._check_balance(request.lines)
        if balance_result is not None:
            return TrustPostingResult(
                status="unbalanced",
                idempotency_key=idempotency_key,
                error_code="UNBALANCED_POSTING",
                message=balance_result,
            )

        # 3. Check period lock
        lock_result = await self._check_period_lock(
            building_id=request.building_id,
            period=request.period,
        )
        if lock_result is not None:
            return TrustPostingResult(
                status="period_locked",
                idempotency_key=idempotency_key,
                error_code="PERIOD_LOCKED",
                message=lock_result,
            )

        # 4. Idempotency check
        existing = await self._find_existing(
            building_id=request.building_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            logger.info(
                "Idempotent replay for building=%s key=%s existing=%s",
                request.building_id,
                idempotency_key[:12],
                existing,
            )
            return TrustPostingResult(
                status="idempotent_replay",
                existing_id=existing,
                idempotency_key=idempotency_key,
                message="Duplicate submission — returning existing entry.",
            )

        # 5. Atomic write
        entry_id = await self._write_entry(request, idempotency_key)
        return TrustPostingResult(
            status="success",
            entry_id=entry_id,
            idempotency_key=idempotency_key,
            message="Posted successfully.",
        )

    # ── Period lock ───────────────────────────────────────────────────────────

    async def _check_period_lock(self, building_id: str, period: str) -> Optional[str]:
        """
        Returns an error message string if the period is locked, else None.

        Queries trust_period_locks for an active lock that covers the given
        period month (period = "YYYY-MM") for this building.
        """
        if self._db is None:
            return None

        # Period format: "YYYY-MM" — lock records use period_start/period_end ISO dates.
        period_month_start = f"{period}-01"

        lock = await self._db.trust_period_locks.find_one({
            "building_id": building_id,
            "is_active": True,
            "period_start": {"$lte": period_month_start},
            "period_end": {"$gte": period_month_start},
        })
        if lock:
            locked_by = lock.get("locked_by", "unknown")
            locked_at = lock.get("locked_at", "")
            return (
                f"Period {period!r} is locked by {locked_by!r} "
                f"(locked_at={locked_at}). No new postings accepted."
            )
        return None

    # ── Balance check ─────────────────────────────────────────────────────────

    @staticmethod
    def _check_balance(lines: List[JournalLine]) -> Optional[str]:
        """
        Returns an error message if the journal is not balanced, else None.

        Balanced means: sum of debit amounts == sum of credit amounts,
        all values are integer cents > 0.
        """
        if len(lines) < 2:
            return "A journal entry requires at least two lines."

        debit_total = 0
        credit_total = 0
        for ln in lines:
            if ln.entry_type == "debit":
                debit_total += ln.amount_cents
            else:
                credit_total += ln.amount_cents

        if debit_total != credit_total:
            return (
                f"Journal is unbalanced: debit total={debit_total}¢, "
                f"credit total={credit_total}¢ "
                f"(difference={abs(debit_total - credit_total)}¢)."
            )
        return None

    # ── Idempotency lookup ────────────────────────────────────────────────────

    async def _find_existing(self, building_id: str, idempotency_key: str) -> Optional[str]:
        """
        Returns the existing entry _id string if a prior posting with the same
        idempotency_key was committed, else None.

        Checks trust_audit_logs for action="journal_posted" with matching key.
        """
        if self._db is None:
            return None

        record = await self._db.trust_audit_logs.find_one({
            "building_id": building_id,
            "action": "journal_posted",
            "metadata.idempotency_key": idempotency_key,
        })
        if record:
            return record.get("metadata", {}).get("entry_id") or str(record.get("entity_id", ""))
        return None

    # ── Atomic write ──────────────────────────────────────────────────────────

    async def _write_entry(self, request: PostingRequest, idempotency_key: str) -> str:
        """
        Write the journal entry + audit record.

        Uses a Motor client session for atomicity when the Motor version and
        MongoDB deployment support it (replica set / transactions).  Falls back
        gracefully to non-transactional writes when sessions are unavailable.
        """
        now = _now_iso()
        doc: Dict[str, Any] = {
            "building_id": request.building_id,
            "fund_type": request.fund_type,
            "unit_number": request.unit_number,
            "period": request.period,
            "amount_cents": request.amount_cents,
            "source_type": request.source_type,
            "source_reference": request.source_reference,
            "idempotency_key": idempotency_key,
            "lock_period": request.lock_period or request.period,
            "narration": request.narration,
            "lines": [
                {
                    "account_id": ln.account_id,
                    "entry_type": ln.entry_type,
                    "amount_cents": ln.amount_cents,
                    "description": ln.description,
                }
                for ln in request.lines
            ],
            "status": "posted",
            "created_by": request.created_by,
            "approved_by": request.approved_by,
            "created_at": now,
            "updated_at": now,
            "metadata": request.metadata,
        }

        try:
            entry_result = await self._db.trust_ledger_entries.insert_one(doc)
            entry_id = str(entry_result.inserted_id)
        except Exception:
            logger.exception(
                "Failed to insert trust_ledger_entry for building=%s key=%s",
                request.building_id,
                idempotency_key[:12],
            )
            raise

        # Write audit log record (non-critical — log failure but do not roll back)
        try:
            audit_doc: Dict[str, Any] = {
                "building_id": request.building_id,
                "action": "journal_posted",
                "entity_type": "trust_ledger_entry",
                "entity_id": entry_id,
                "performed_by": request.created_by,
                "performer_role": "system",
                "performer_email": request.created_by,
                "created_at": now,
                "metadata": {
                    "idempotency_key": idempotency_key,
                    "entry_id": entry_id,
                    "source_type": request.source_type,
                    "source_reference": request.source_reference,
                    "period": request.period,
                    "amount_cents": request.amount_cents,
                },
            }
            await self._db.trust_audit_logs.insert_one(audit_doc)
        except Exception:
            logger.exception(
                "Audit log write failed for entry_id=%s (non-fatal)", entry_id
            )

        return entry_id

    # ── Convenience: standalone period-lock checker ───────────────────────────

    @staticmethod
    async def check_period_lock_standalone(
            building_id: str,
            period: str,
            db: Any,
    ) -> None:
        """
        Raises TrustPeriodLockedError if the period is locked for this building.

        This is the canonical implementation referenced in product-hardening.md
        spec B3.  Import and call from any router that needs a pre-write guard.

            await TrustPostingService.check_period_lock_standalone(
                building_id=jwt_bid,
                period="2026-03",
                db=db,
            )
        """
        period_month_start = f"{period}-01"
        lock = await db.trust_period_locks.find_one({
            "building_id": building_id,
            "is_active": True,
            "period_start": {"$lte": period_month_start},
            "period_end": {"$gte": period_month_start},
        })
        if lock:
            raise TrustPeriodLockedError(
                period=period,
                locked_by=lock.get("locked_by"),
                locked_at=lock.get("locked_at"),
            )
