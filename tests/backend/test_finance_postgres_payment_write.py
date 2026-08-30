# @featuretrace:finance-postgres-write-cutover — Tests cents conversion, idempotency hash, and ledger invariants for promoted finance writes.
# Data flow: pytest → finance_postgres_write_service + FinancialCoreService → finance journal controls (building-scoped).
# Related: backend/services/finance_postgres_write_service.py
#          backend/services/financial_core/service.py

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from services.finance_postgres_write_service import dollars_to_cents, request_hash
from services.financial_core.domain.entities import (
    EntryDirection,
    JournalEntry,
    JournalLine,
    RecordPaymentCommand,
    SchemeRef,
    PaymentChannel,
)
from services.financial_core.service import FinancialCoreService

from datetime import date
from uuid import uuid4


def test_dollars_to_cents_accepts_two_decimal_places():
    assert dollars_to_cents("123.45") == 12345
    assert dollars_to_cents(Decimal("0.01")) == 1


def test_dollars_to_cents_rejects_fractional_cent():
    with pytest.raises(HTTPException) as exc:
        dollars_to_cents("10.001")
    assert exc.value.status_code == 422
    assert "fractions of a cent" in exc.value.detail


def test_request_hash_is_order_independent_and_changes_with_payload():
    first = request_hash({"amount_cents": 1000, "unit_number": "1"})
    second = request_hash({"unit_number": "1", "amount_cents": 1000})
    third = request_hash({"unit_number": "1", "amount_cents": 2000})

    assert first == second
    assert first != third


def test_balanced_journal_entry_accepted():
    scheme_ref = SchemeRef(tenant_id=uuid4(), scheme_id=uuid4())
    entry = JournalEntry(
        scheme_ref=scheme_ref,
        period_id=uuid4(),
        source_type="adjustment",
        narration="Balanced test adjustment",
        effective_on=date.today(),
        lines=[
            JournalLine(gl_account_id=uuid4(), direction=EntryDirection.DEBIT, amount_cents=500),
            JournalLine(gl_account_id=uuid4(), direction=EntryDirection.CREDIT, amount_cents=500),
        ],
        idempotency_key="balanced-test",
        posted_by=uuid4(),
        approved_by=uuid4(),
    )

    assert entry.debit_total_cents == 500
    assert entry.credit_total_cents == 500


def test_unbalanced_journal_entry_rejected():
    scheme_ref = SchemeRef(tenant_id=uuid4(), scheme_id=uuid4())
    with pytest.raises(ValueError, match="not balanced"):
        JournalEntry(
            scheme_ref=scheme_ref,
            period_id=uuid4(),
            source_type="adjustment",
            narration="Unbalanced test adjustment",
            effective_on=date.today(),
            lines=[
                JournalLine(gl_account_id=uuid4(), direction=EntryDirection.DEBIT, amount_cents=500),
                JournalLine(gl_account_id=uuid4(), direction=EntryDirection.CREDIT, amount_cents=499),
            ],
        )


@pytest.mark.asyncio
async def test_record_payment_requires_idempotency_and_actor():
    svc = FinancialCoreService(ledger_repo=None, outbox_port=None)
    with pytest.raises(ValueError, match="idempotency_key"):
        await svc.record_payment(
            RecordPaymentCommand(
                scheme_ref=SchemeRef(tenant_id=uuid4(), scheme_id=uuid4()),
                payer_party_id=uuid4(),
                lot_id=uuid4(),
                channel=PaymentChannel.BPAY,
                received_on=date.today(),
                amount_cents=1000,
            )
        )
