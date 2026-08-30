# @featuretrace:finance-postgres-write-lifecycle — PostgreSQL receipt lifecycle service: verify, reject (reversal), safe delete.
# Layer: service
# Data flow: PATCH /levy-payments/{id}/verify → verify_receipt (building-scoped)
#            PATCH /levy-payments/{id}/reject → reject_receipt + reverse_entry (building-scoped)
#            DELETE /levy-payments/{id}       → delete_draft_receipt or 409 POSTED_RECORD_IMMUTABLE (building-scoped)
# Related: backend/routers/finance.py
#          backend/services/finance_write_cutover_service.py
#          backend/services/financial_core/service.py
#          tests/backend/test_finance_payment_lifecycle.py
# Toggle: financial_pg_writes_enabled
# Table: finance.receipts, finance.journal_entries, finance.journal_lines, core.outbox
# Tests: tests/backend/test_finance_payment_lifecycle.py
"""PostgreSQL finance write orchestration for Prompt 7 + Prompt 8.

Prompt 7: first promoted write slice — manager-approved manual levy receipt posting.
Prompt 8: lifecycle routes — verify (idempotent confirm), reject (reversal for posted),
          safe delete (409 for posted; status-only cancel for unposted drafts).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant
from services.financial_core.adapters.db_postgres.ledger_repo import PostgresLedgerRepository
from services.financial_core.adapters.db_postgres.outbox_repo import PostgresOutboxRepository
from services.financial_core.domain.entities import (
    AllocatePaymentCommand,
    EntryDirection,
    EntryStatus,
    JournalEntry,
    JournalLine,
    ReverseEntryCommand,
    SchemeRef,
    PaymentChannel,
    RecordPaymentCommand,
)
from services.financial_core.service import FinancialCoreService
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)

_PAYMENT_CHANNEL_MAP = {
    "deft": PaymentChannel.DEFT,
    "bpay": PaymentChannel.BPAY,
    "credit_card": PaymentChannel.CARD,
    "card": PaymentChannel.CARD,
    "bank_transfer": PaymentChannel.BANK_TRANSFER,
    "cash": PaymentChannel.CASH,
    "cheque": PaymentChannel.CHEQUE,
    "manual_adjustment": PaymentChannel.MANUAL_ADJUSTMENT,
}


@dataclass(frozen=True)
class PostgresReceiptPostResult:
    response: dict[str, Any]
    replayed: bool
    journal_entry_id: str
    receipt_id: str


def dollars_to_cents(value: Any) -> int:
    """Convert an API dollar value to integer cents without float arithmetic."""
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=422, detail="amount must be a valid decimal dollar value") from exc

    cents = decimal_value * Decimal("100")
    if cents != cents.to_integral_value():
        raise HTTPException(status_code=422, detail="amount must not contain fractions of a cent")
    cents_int = int(cents)
    if cents_int <= 0:
        raise HTTPException(status_code=422, detail="amount must be greater than zero")
    return cents_int


def request_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 hash for idempotency payload comparison."""
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_uuid(value: Any, field: str) -> UUID:
    """Generated function header.

    Function: _parse_uuid
    Path: backend/services/finance_postgres_write_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail=f"PostgreSQL {field} mapping is missing") from exc


class FinancePostgresWriteService:
    """High-level writer for route-promoted PostgreSQL finance operations."""

    async def post_manual_levy_receipt(
        self,
        *,
        building_id: str,
        unit_number: str,
        amount: Any,
        payment_method: str,
        payment_reference: str | None,
        quarter: str,
        year: str,
        fund_type: str | None,
        payment_type: str | None,
        notes: str | None,
        actor: dict[str, Any],
        idempotency_key: str,
        is_test_data: bool = False,
    ) -> PostgresReceiptPostResult:
        """Create receipt, journal and allocation rows in one PostgreSQL transaction."""
        amount_cents = dollars_to_cents(amount)
        actor_user_id = await config_repo.resolve_actor_user_id(
            actor_user_id=str(actor.get("id") or actor.get("_id") or ""),
            actor_email=actor.get("email"),
            require_existing=True,
        )
        actor_uuid = _parse_uuid(actor_user_id, "actor user")

        scheme = await config_repo.resolve_scheme_context(building_id)
        if not scheme:
            raise HTTPException(status_code=409, detail=f"No PostgreSQL scheme found for building {building_id}")
        scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"]))) 

        payload = {
            "building_id": building_id,
            "unit_number": unit_number,
            "amount_cents": amount_cents,
            "payment_method": payment_method,
            "payment_reference": payment_reference,
            "quarter": quarter,
            "year": year,
            "fund_type": fund_type,
            "payment_type": payment_type or "standard",
        }
        payload_hash = request_hash(payload)

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            existing = await session.execute(
                text(
                    """
                    SELECT
                        je.journal_entry_id::text,
                        je.metadata,
                        r.receipt_id::text,
                        r.created_at,
                        r.amount_cents
                    FROM finance.journal_entries je
                    LEFT JOIN finance.receipts r
                      ON r.journal_entry_id = je.journal_entry_id
                    WHERE je.scheme_id = :scheme_id
                      AND je.idempotency_key = :idempotency_key
                    LIMIT 1
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"]), "idempotency_key": idempotency_key},
            )
            existing_row = existing.fetchone()
            if existing_row:
                metadata = dict(existing_row.metadata or {})
                if metadata.get("request_hash") != payload_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key was already used with a different payment payload",
                    )
                response = self._response_shape(
                    receipt_id=str(existing_row.receipt_id),
                    unit_number=unit_number,
                    amount_cents=int(existing_row.amount_cents or amount_cents),
                    payment_method=payment_method,
                    payment_reference=payment_reference,
                    quarter=quarter,
                    year=year,
                    fund_type=fund_type,
                    payment_type=payment_type,
                    notes=notes,
                    actor_id=str(actor.get("id") or actor.get("_id") or ""),
                    created_at=existing_row.created_at,
                    replayed=True,
                )
                return PostgresReceiptPostResult(
                    response=response,
                    replayed=True,
                    journal_entry_id=str(existing_row.journal_entry_id),
                    receipt_id=str(existing_row.receipt_id),
                )

            lot_row = await session.execute(
                text(
                    """
                    SELECT
                        l.lot_id::text,
                        op.owner_party_id::text
                    FROM core.lots l
                    LEFT JOIN core.ownership_periods op
                      ON op.lot_id = l.lot_id
                     AND op.scheme_id = l.scheme_id
                     AND op.recorded_to IS NULL
                     AND op.valid_from <= CURRENT_DATE
                     AND (op.valid_to IS NULL OR op.valid_to > CURRENT_DATE)
                    WHERE l.scheme_id = :scheme_id
                      AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
                    ORDER BY op.valid_from DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"]), "unit_number": unit_number},
            )
            lot = lot_row.fetchone()
            if not lot or not lot.lot_id:
                raise HTTPException(status_code=404, detail="Unit/lot not found in PostgreSQL for this building")
            if not lot.owner_party_id:
                raise HTTPException(status_code=409, detail="No current PostgreSQL owner mapping for this unit/lot")

            ledger = PostgresLedgerRepository(session)
            outbox = PostgresOutboxRepository(session)
            core = FinancialCoreService(ledger, outbox)

            receipt = await core.record_payment(
                RecordPaymentCommand(
                    scheme_ref=scheme_ref,
                    payer_party_id=UUID(str(lot.owner_party_id)),
                    lot_id=UUID(str(lot.lot_id)),
                    channel=_PAYMENT_CHANNEL_MAP.get(str(payment_method).lower(), PaymentChannel.MANUAL_ADJUSTMENT),
                    received_on=date.today(),
                    amount_cents=amount_cents,
                    external_reference=payment_reference,
                    idempotency_key=idempotency_key,
                    is_test_data=is_test_data,
                    posted_by=actor_uuid,
                    approved_by=actor_uuid,
                    metadata={
                        "request_hash": payload_hash,
                        "building_id": building_id,
                        "unit_number": unit_number,
                        "quarter": quarter,
                        "year": year,
                        "fund_type": fund_type,
                        "payment_type": payment_type or "standard",
                        "source_mode": "postgres_write",
                    },
                )
            )

            allocations = await core.allocate_payment(
                AllocatePaymentCommand(
                    scheme_ref=scheme_ref,
                    receipt_id=receipt.receipt_id,
                )
            )

            await create_audit_log(
                action="finance_postgres_receipt_posted",
                resource_type="finance_receipt",
                resource_id=str(receipt.receipt_id),
                user_id=str(actor.get("id") or actor.get("_id") or ""),
                user_name=str(actor.get("full_name") or actor.get("email") or "Unknown user"),
                details={
                    "route": "POST /levy-payments",
                    "source_mode": "postgres_write",
                    "amount_cents": amount_cents,
                    "unit_number": unit_number,
                    "lot_id": str(lot.lot_id),
                    "owner_party_id": str(lot.owner_party_id),
                    "journal_entry_id": str(receipt.journal_entry_id),
                    "allocation_count": len(allocations),
                    "allocated_cents": sum(a.allocated_cents for a in allocations),
                    "idempotency_key": idempotency_key,
                    "request_hash": payload_hash,
                },
                building_id=building_id,
            )

            response = self._response_shape(
                receipt_id=str(receipt.receipt_id),
                unit_number=unit_number,
                amount_cents=amount_cents,
                payment_method=payment_method,
                payment_reference=payment_reference,
                quarter=quarter,
                year=year,
                fund_type=fund_type,
                payment_type=payment_type,
                notes=notes,
                actor_id=str(actor.get("id") or actor.get("_id") or ""),
                created_at=datetime.now(timezone.utc),
                replayed=False,
            )
            return PostgresReceiptPostResult(
                response=response,
                replayed=False,
                journal_entry_id=str(receipt.journal_entry_id),
                receipt_id=str(receipt.receipt_id),
            )

    async def verify_receipt(
        self,
        *,
        building_id: str,
        payment_id: str,
        notes: str | None,
        actor: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify (confirm) a PG receipt.

        If the receipt is already in confirmed/posted status this is an idempotent
        no-op — we write an audit event but return the existing shape without error.
        """
        actor_user_id = await config_repo.resolve_actor_user_id(
            actor_user_id=str(actor.get("id") or actor.get("_id") or ""),
            actor_email=actor.get("email"),
            require_existing=True,
        )

        scheme = await config_repo.resolve_scheme_context(building_id)
        if not scheme:
            raise HTTPException(status_code=409, detail=f"No PostgreSQL scheme found for building {building_id}")

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            row = await session.execute(
                text(
                    """
                    SELECT
                        r.receipt_id::text,
                        r.amount_cents,
                        r.received_on,
                        r.channel,
                        r.external_reference,
                        r.lot_id::text,
                        je.journal_entry_id::text,
                        je.status  AS entry_status,
                        je.metadata
                    FROM finance.receipts r
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = r.journal_entry_id
                    WHERE r.receipt_id = :receipt_id
                      AND r.scheme_id = :scheme_id
                    """
                ),
                {"receipt_id": payment_id, "scheme_id": str(scheme["scheme_id"])},
            )
            receipt_row = row.fetchone()
            if not receipt_row:
                raise HTTPException(status_code=404, detail="Payment receipt not found in PostgreSQL for this building")

            metadata = dict(receipt_row.metadata or {})
            stored_building_id = metadata.get("building_id", "")
            if stored_building_id and stored_building_id != building_id:
                raise HTTPException(status_code=403, detail="Cross-building receipt access denied")

            entry_status = str(receipt_row.entry_status or "").lower()
            already_confirmed = entry_status in {"posted", "confirmed"}

            if not already_confirmed:
                # Transition receipt — update journal entry status to confirmed/posted
                await session.execute(
                    text(
                        """
                        UPDATE finance.journal_entries
                           SET status = 'posted',
                               posted_at = now(),
                               posted_by = :actor_id,
                               approved_by = :actor_id,
                         WHERE journal_entry_id = :journal_entry_id
                           AND scheme_id = :scheme_id
                        """
                    ),
                    {
                        "actor_id": actor_user_id,
                        "journal_entry_id": str(receipt_row.journal_entry_id),
                        "scheme_id": str(scheme["scheme_id"]),
                    },
                )

            await create_audit_log(
                action="finance_pg_receipt_verified" if not already_confirmed else "finance_pg_receipt_verify_noop",
                resource_type="finance_receipt",
                resource_id=str(receipt_row.receipt_id),
                user_id=str(actor.get("id") or actor.get("_id") or ""),
                user_name=str(actor.get("full_name") or actor.get("email") or "Unknown"),
                details={
                    "route": "PATCH /levy-payments/{id}/verify",
                    "source_mode": "postgres_write",
                    "building_id": building_id,
                    "already_confirmed": already_confirmed,
                    "journal_entry_id": str(receipt_row.journal_entry_id),
                    "amount_cents": int(receipt_row.amount_cents),
                    "notes": notes,
                },
                building_id=building_id,
            )

        return self._receipt_verify_response(
            receipt_id=str(receipt_row.receipt_id),
            metadata=metadata,
            amount_cents=int(receipt_row.amount_cents),
            channel=str(receipt_row.channel),
            external_reference=receipt_row.external_reference,
            actor_id=str(actor.get("id") or actor.get("_id") or ""),
            notes=notes,
        )

    async def reject_receipt(
        self,
        *,
        building_id: str,
        payment_id: str,
        rejection_reason: str,
        notes: str | None,
        actor: dict[str, Any],
    ) -> dict[str, Any]:
        """Reject a PG receipt.

        - If no posted journal exists → status-only transition to rejected.
        - If posted journal exists → create reversal entry (original remains IMMUTABLE).
        - If already rejected → idempotent 409.
        """
        if not rejection_reason or not rejection_reason.strip():
            raise HTTPException(status_code=400, detail="rejection_reason is required")

        actor_user_id = await config_repo.resolve_actor_user_id(
            actor_user_id=str(actor.get("id") or actor.get("_id") or ""),
            actor_email=actor.get("email"),
            require_existing=True,
        )
        actor_uuid = _parse_uuid(actor_user_id, "actor user")

        scheme = await config_repo.resolve_scheme_context(building_id)
        if not scheme:
            raise HTTPException(status_code=409, detail=f"No PostgreSQL scheme found for building {building_id}")
        scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            row = await session.execute(
                text(
                    """
                    SELECT
                        r.receipt_id::text,
                        r.amount_cents,
                        r.received_on,
                        r.channel,
                        r.external_reference,
                        r.lot_id::text,
                        je.journal_entry_id::text,
                        je.status  AS entry_status,
                        je.metadata,
                        je.reversal_of_id::text  AS reversal_of_id
                    FROM finance.receipts r
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = r.journal_entry_id
                    WHERE r.receipt_id = :receipt_id
                      AND r.scheme_id = :scheme_id
                    """
                ),
                {"receipt_id": payment_id, "scheme_id": str(scheme["scheme_id"])},
            )
            receipt_row = row.fetchone()
            if not receipt_row:
                raise HTTPException(status_code=404, detail="Payment receipt not found in PostgreSQL for this building")

            metadata = dict(receipt_row.metadata or {})
            stored_building_id = metadata.get("building_id", "")
            if stored_building_id and stored_building_id != building_id:
                raise HTTPException(status_code=403, detail="Cross-building receipt access denied")

            entry_status = str(receipt_row.entry_status or "").lower()

            # Duplicate rejection guard
            if entry_status in {"reversed", "voided"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ALREADY_REJECTED",
                        "message": "This receipt has already been rejected or reversed.",
                        "status": 409,
                        "retryable": False,
                        "suggested_action": "No further action required; receipt is already rejected.",
                    },
                )

            has_posted_journal = entry_status == "posted"
            reversal_entry_id: str | None = None

            if has_posted_journal:
                ledger = PostgresLedgerRepository(session)
                outbox = PostgresOutboxRepository(session)
                core = FinancialCoreService(ledger, outbox)

                idempotency_key = f"reversal-{payment_id}"
                reverse_result = await core.reverse_entry(
                    ReverseEntryCommand(
                        scheme_ref=scheme_ref,
                        journal_entry_id=UUID(str(receipt_row.journal_entry_id)),
                        reason=rejection_reason.strip(),
                        idempotency_key=idempotency_key,
                    )
                )
                reversal_entry_id = str(reverse_result.reversal_entry_id)

            # Mark original journal entry as reversed (status-only; lines not mutated)
            await session.execute(
                text(
                    """
                    UPDATE finance.journal_entries
                       SET status = 'reversed',
                     WHERE journal_entry_id = :journal_entry_id
                       AND scheme_id = :scheme_id
                    """
                ),
                {
                    "journal_entry_id": str(receipt_row.journal_entry_id),
                    "scheme_id": str(scheme["scheme_id"]),
                },
            )

            await create_audit_log(
                action="finance_pg_receipt_rejected",
                resource_type="finance_receipt",
                resource_id=str(receipt_row.receipt_id),
                user_id=str(actor.get("id") or actor.get("_id") or ""),
                user_name=str(actor.get("full_name") or actor.get("email") or "Unknown"),
                details={
                    "route": "PATCH /levy-payments/{id}/reject",
                    "source_mode": "postgres_write",
                    "building_id": building_id,
                    "rejection_reason": rejection_reason.strip(),
                    "had_posted_journal": has_posted_journal,
                    "original_journal_entry_id": str(receipt_row.journal_entry_id),
                    "reversal_journal_entry_id": reversal_entry_id,
                    "amount_cents": int(receipt_row.amount_cents),
                    "notes": notes,
                },
                building_id=building_id,
            )

        return self._receipt_reject_response(
            receipt_id=str(receipt_row.receipt_id),
            metadata=metadata,
            amount_cents=int(receipt_row.amount_cents),
            channel=str(receipt_row.channel),
            external_reference=receipt_row.external_reference,
            actor_id=str(actor.get("id") or actor.get("_id") or ""),
            rejection_reason=rejection_reason.strip(),
            notes=notes,
            reversal_entry_id=reversal_entry_id,
        )

    async def delete_receipt(
        self,
        *,
        building_id: str,
        payment_id: str,
        actor: dict[str, Any],
    ) -> dict[str, Any]:
        """Safe delete for a PG receipt.

        - If receipt has posted journal entries → HTTP 409 POSTED_RECORD_IMMUTABLE.
          Use PATCH .../reject instead.
        - If receipt is draft/unposted → allow status-only cancellation (no physical delete).
        - NEVER physically deletes posted PG records.
        """
        scheme = await config_repo.resolve_scheme_context(building_id)
        if not scheme:
            raise HTTPException(status_code=409, detail=f"No PostgreSQL scheme found for building {building_id}")

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            row = await session.execute(
                text(
                    """
                    SELECT
                        r.receipt_id::text,
                        r.amount_cents,
                        r.channel,
                        r.external_reference,
                        je.journal_entry_id::text,
                        je.status  AS entry_status,
                        je.metadata
                    FROM finance.receipts r
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = r.journal_entry_id
                    WHERE r.receipt_id = :receipt_id
                      AND r.scheme_id = :scheme_id
                    """
                ),
                {"receipt_id": payment_id, "scheme_id": str(scheme["scheme_id"])},
            )
            receipt_row = row.fetchone()
            if not receipt_row:
                raise HTTPException(status_code=404, detail="Payment receipt not found in PostgreSQL for this building")

            metadata = dict(receipt_row.metadata or {})
            stored_building_id = metadata.get("building_id", "")
            if stored_building_id and stored_building_id != building_id:
                raise HTTPException(status_code=403, detail="Cross-building receipt access denied")

            entry_status = str(receipt_row.entry_status or "").lower()
            is_posted = entry_status == "posted"

            if is_posted:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "POSTED_RECORD_IMMUTABLE",
                        "message": "Posted ledger records cannot be deleted. Use reject/reversal instead.",
                        "status": 409,
                        "retryable": False,
                        "suggested_action": f"PATCH /levy-payments/{payment_id}/reject",
                    },
                )

            # Draft/unposted: mark as voided (status-only — no physical delete)
            await session.execute(
                text(
                    """
                    UPDATE finance.journal_entries
                       SET status = 'voided',
                     WHERE journal_entry_id = :journal_entry_id
                       AND scheme_id = :scheme_id
                    """
                ),
                {
                    "journal_entry_id": str(receipt_row.journal_entry_id),
                    "scheme_id": str(scheme["scheme_id"]),
                },
            )

            await create_audit_log(
                action="finance_pg_receipt_cancelled",
                resource_type="finance_receipt",
                resource_id=str(receipt_row.receipt_id),
                user_id=str(actor.get("id") or actor.get("_id") or ""),
                user_name=str(actor.get("full_name") or actor.get("email") or "Unknown"),
                details={
                    "route": "DELETE /levy-payments/{id}",
                    "source_mode": "postgres_write",
                    "building_id": building_id,
                    "prior_status": entry_status,
                    "journal_entry_id": str(receipt_row.journal_entry_id),
                    "amount_cents": int(receipt_row.amount_cents),
                },
                building_id=building_id,
            )

        return {"message": "Payment record cancelled", "id": payment_id}

    async def create_adjustment_journal(
        self,
        *,
        building_id: str,
        narration: str,
        reason: str,
        lines: list[dict[str, Any]],
        idempotency_key: str,
        actor: dict[str, Any],
        is_test_data: bool = False,
    ) -> dict[str, Any]:
        """Create a manual adjustment journal entry in PostgreSQL.

        Invariants enforced (fail hard — no silent fallback):
          - Total debits must equal total credits (balanced double-entry).
          - All amount_cents must be positive integers (no floats, no zero).
          - narration and reason are required.
          - Idempotency-Key is required.
          - Caller must have can_manage_finances.
          - Building scope enforced via scheme resolution.
        """
        if not narration or not narration.strip():
            raise HTTPException(status_code=400, detail="narration is required")
        if not reason or not reason.strip():
            raise HTTPException(status_code=400, detail="reason is required for adjustment journals")
        if not lines:
            raise HTTPException(status_code=400, detail="at least one journal line is required")
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required for adjustment journals")

        actor_user_id = await config_repo.resolve_actor_user_id(
            actor_user_id=str(actor.get("id") or actor.get("_id") or ""),
            actor_email=actor.get("email"),
            require_existing=True,
        )
        actor_uuid = _parse_uuid(actor_user_id, "actor user")

        scheme = await config_repo.resolve_scheme_context(building_id)
        if not scheme:
            raise HTTPException(status_code=409, detail=f"No PostgreSQL scheme found for building {building_id}")
        scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

        # Validate and parse lines — enforce integer cents, no floats
        parsed_lines: list[JournalLine] = []
        for idx, line in enumerate(lines):
            raw_amount = line.get("amount_cents")
            if raw_amount is None:
                raise HTTPException(status_code=422, detail=f"line[{idx}]: amount_cents is required")
            if not isinstance(raw_amount, int):
                raise HTTPException(
                    status_code=422,
                    detail=f"line[{idx}]: amount_cents must be an integer (got {type(raw_amount).__name__})",
                )
            if raw_amount <= 0:
                raise HTTPException(status_code=422, detail=f"line[{idx}]: amount_cents must be positive")

            raw_direction = str(line.get("direction", "")).lower()
            if raw_direction not in {"debit", "credit"}:
                raise HTTPException(
                    status_code=422,
                    detail=f"line[{idx}]: direction must be 'debit' or 'credit'",
                )

            raw_gl = line.get("gl_account_id")
            if not raw_gl:
                raise HTTPException(status_code=422, detail=f"line[{idx}]: gl_account_id is required")
            try:
                gl_uuid = UUID(str(raw_gl))
            except (ValueError, TypeError):
                raise HTTPException(status_code=422, detail=f"line[{idx}]: gl_account_id is not a valid UUID")

            raw_gst = line.get("gst_cents", 0)
            if not isinstance(raw_gst, int):
                raise HTTPException(
                    status_code=422,
                    detail=f"line[{idx}]: gst_cents must be an integer (got {type(raw_gst).__name__})",
                )
            if raw_gst < 0:
                raise HTTPException(status_code=422, detail=f"line[{idx}]: gst_cents must be non-negative")

            parsed_lines.append(
                JournalLine(
                    gl_account_id=gl_uuid,
                    direction=EntryDirection.DEBIT if raw_direction == "debit" else EntryDirection.CREDIT,
                    amount_cents=raw_amount,
                    gst_cents=raw_gst,
                    narration=line.get("narration") or narration,
                )
            )

        # Balance check
        total_debits = sum(l.amount_cents for l in parsed_lines if l.direction == EntryDirection.DEBIT)
        total_credits = sum(l.amount_cents for l in parsed_lines if l.direction == EntryDirection.CREDIT)
        if total_debits != total_credits:
            raise HTTPException(
                status_code=422,
                detail=f"Adjustment journal is not balanced: debit={total_debits} credit={total_credits}",
            )

        payload_hash = request_hash({
            "building_id": building_id,
            "narration": narration,
            "reason": reason,
            "lines": [
                {"gl_account_id": str(l.gl_account_id), "direction": l.direction.value, "amount_cents": l.amount_cents}
                for l in parsed_lines
            ],
        })

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])

            # Idempotency check
            existing = await session.execute(
                text(
                    """
                    SELECT journal_entry_id::text, metadata
                    FROM finance.journal_entries
                    WHERE scheme_id = :scheme_id
                      AND idempotency_key = :idempotency_key
                    LIMIT 1
                    """
                ),
                {"scheme_id": str(scheme["scheme_id"]), "idempotency_key": idempotency_key},
            )
            existing_row = existing.fetchone()
            if existing_row:
                stored_hash = (dict(existing_row.metadata or {})).get("request_hash")
                if stored_hash != payload_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key already used with a different adjustment payload",
                    )
                return {
                    "journal_entry_id": str(existing_row.journal_entry_id),
                    "status": "posted",
                    "building_id": building_id,
                    "narration": narration,
                    "reason": reason,
                    "total_debit_cents": total_debits,
                    "total_credit_cents": total_credits,
                    "replayed": True,
                }

            ledger = PostgresLedgerRepository(session)
            outbox = PostgresOutboxRepository(session)
            core = FinancialCoreService(ledger, outbox)

            period_id = await ledger.get_current_accounting_period(scheme_ref)

            entry = JournalEntry(
                scheme_ref=scheme_ref,
                period_id=period_id,
                source_type="manual_adjustment",
                source_reference=reason.strip()[:200],
                narration=narration.strip(),
                effective_on=date.today(),
                lines=parsed_lines,
                idempotency_key=idempotency_key,
                is_test_data=is_test_data,
                posted_by=actor_uuid,
                approved_by=actor_uuid,
                metadata={
                    "request_hash": payload_hash,
                    "building_id": building_id,
                    "reason": reason.strip(),
                    "source_mode": "postgres_write",
                },
            )

            saved_entry = await ledger.save_journal_entry(entry)
            posted_entry = await ledger.post_journal_entry(saved_entry.journal_entry_id, scheme_ref)

            await outbox.publish(
                scheme_ref=scheme_ref,
                event_type="journal.adjustment.created",
                payload={
                    "journal_entry_id": str(posted_entry.journal_entry_id),
                    "narration": narration,
                    "reason": reason,
                    "total_debit_cents": total_debits,
                    "building_id": building_id,
                },
            )

            await create_audit_log(
                action="finance_pg_adjustment_journal_posted",
                resource_type="finance_journal_entry",
                resource_id=str(posted_entry.journal_entry_id),
                user_id=str(actor.get("id") or actor.get("_id") or ""),
                user_name=str(actor.get("full_name") or actor.get("email") or "Unknown"),
                details={
                    "route": "POST /finance/adjustment-journals",
                    "source_mode": "postgres_write",
                    "building_id": building_id,
                    "narration": narration,
                    "reason": reason,
                    "total_debit_cents": total_debits,
                    "line_count": len(parsed_lines),
                    "idempotency_key": idempotency_key,
                    "request_hash": payload_hash,
                },
                building_id=building_id,
            )

        return {
            "journal_entry_id": str(posted_entry.journal_entry_id),
            "status": "posted",
            "building_id": building_id,
            "narration": narration,
            "reason": reason,
            "total_debit_cents": total_debits,
            "total_credit_cents": total_credits,
            "replayed": False,
        }

    def _receipt_verify_response(
        self,
        *,
        receipt_id: str,
        metadata: dict[str, Any],
        amount_cents: int,
        channel: str,
        external_reference: str | None,
        actor_id: str,
        notes: str | None,
    ) -> dict[str, Any]:
        """Generated function header.

        Function: FinancePostgresWriteService._receipt_verify_response
        Path: backend/services/finance_postgres_write_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        unit_number = metadata.get("unit_number", "")
        quarter = metadata.get("quarter", "")
        year = metadata.get("year", "")
        fund_type = metadata.get("fund_type")
        payment_type = metadata.get("payment_type", "standard")
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "id": receipt_id,
            "unit_number": unit_number,
            "amount": Decimal(amount_cents) / Decimal("100"),
            "payment_method": channel,
            "payment_reference": external_reference,
            "quarter": quarter,
            "year": year,
            "fund_type": fund_type,
            "payment_type": payment_type,
            "credit_amount": None,
            "status": "confirmed",
            "notes": notes,
            "receipt_number": f"PG-{receipt_id[:8]}",
            "paid_by": actor_id,
            "confirmed_by": actor_id,
            "confirmed_at": now_iso,
            "created_at": now_iso,
            "replayed": False,
        }

    def _receipt_reject_response(
        self,
        *,
        receipt_id: str,
        metadata: dict[str, Any],
        amount_cents: int,
        channel: str,
        external_reference: str | None,
        actor_id: str,
        rejection_reason: str,
        notes: str | None,
        reversal_entry_id: str | None,
    ) -> dict[str, Any]:
        """Generated function header.

        Function: FinancePostgresWriteService._receipt_reject_response
        Path: backend/services/finance_postgres_write_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        unit_number = metadata.get("unit_number", "")
        quarter = metadata.get("quarter", "")
        year = metadata.get("year", "")
        fund_type = metadata.get("fund_type")
        payment_type = metadata.get("payment_type", "standard")
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "id": receipt_id,
            "unit_number": unit_number,
            "amount": Decimal(amount_cents) / Decimal("100"),
            "payment_method": channel,
            "payment_reference": external_reference,
            "quarter": quarter,
            "year": year,
            "fund_type": fund_type,
            "payment_type": payment_type,
            "credit_amount": None,
            "status": "rejected",
            "notes": notes,
            "receipt_number": f"PG-{receipt_id[:8]}",
            "paid_by": actor_id,
            "confirmed_by": None,
            "confirmed_at": None,
            "rejected_by": actor_id,
            "rejected_at": now_iso,
            "rejection_reason": rejection_reason,
            "reversal_journal_entry_id": reversal_entry_id,
            "created_at": now_iso,
            "replayed": False,
        }

    def _response_shape(
        self,
        *,
        receipt_id: str,
        unit_number: str,
        amount_cents: int,
        payment_method: str,
        payment_reference: str | None,
        quarter: str,
        year: str,
        fund_type: str | None,
        payment_type: str | None,
        notes: str | None,
        actor_id: str,
        created_at: datetime,
        replayed: bool,
    ) -> dict[str, Any]:
        """Generated function header.

        Function: FinancePostgresWriteService._response_shape
        Path: backend/services/finance_postgres_write_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        created = created_at.astimezone(timezone.utc).isoformat() if created_at.tzinfo else created_at.isoformat()
        return {
            "id": receipt_id,
            "unit_number": unit_number,
            "amount": Decimal(amount_cents) / Decimal("100"),
            "payment_method": payment_method,
            "payment_reference": payment_reference,
            "quarter": quarter,
            "year": year,
            "fund_type": fund_type,
            "payment_type": payment_type or "standard",
            "credit_amount": None,
            "status": "confirmed",
            "notes": notes,
            "receipt_number": f"PG-{receipt_id[:8]}",
            "paid_by": actor_id,
            "confirmed_by": actor_id,
            "confirmed_at": created,
            "created_at": created,
            "replayed": replayed,
        }


finance_postgres_write_service = FinancePostgresWriteService()
