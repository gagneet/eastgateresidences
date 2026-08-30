# @featuretrace:financial_core — Postgres implementation of LedgerRepository.
# Layer: service
# Data flow: services/financial_core/domain/ports.py (Protocol) -> this file -> finance.* (building-scoped).
# Related: backend/services/financial_core/service.py
#          backend/services/financial_core/domain/ports.py
#          backend/services/financial_core/adapters/db_postgres/models.py
# Table: finance.journal_entries, finance.journal_lines, finance.levy_runs,
#        finance.levy_items, finance.receipts, finance.receipt_allocations,
#        finance.expense_transactions
"""PostgreSQL implementation of LedgerRepository.

Uses SQLAlchemy 2.x async ORM within the session provided by the caller.
The session must have `SET LOCAL app.tenant_id = '<uuid>'` already called
(enforced by db_postgres/session.py:set_tenant()).

All queries here are tenant-safe because RLS is active on every table.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import delete, select, update, text, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.financial_core.adapters.db_postgres.models import (
    PgAccountingPeriod,
    PgExpenseTransaction,
    PgFund,
    PgGlAccount,
    PgJournalEntry,
    PgJournalLine,
    PgLevyItem,
    PgLevyRun,
    PgReceipt,
    PgReceiptAllocation,
)
from services.financial_core.domain.entities import (
    AccountingPeriod,
    AllocationToReverse,
    EntryDirection,
    EntryStatus,
    Expense,
    HistoricalLevyItemSpec,
    JournalClassificationEntry,
    JournalEntry,
    JournalLine,
    Levy,
    LevyItemAdjustment,
    PaymentAllocation,
    Receipt,
    ReconcileResult,
    ReconciliationStatus,
    ReversedAllocationItem,
    SchemeRef,
)
from services.financial_core.domain.exceptions import (
    AccountingPeriodNotPermitted,
    AmbiguousAccountingPeriod,
    BankTransactionClaimConflict,
    BankTransactionNotFound,
    NoAccountingPeriodForDate,
)


def _now() -> datetime:
    """Generated function header.

    Function: _now
    Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(tz=timezone.utc)


def _derive_levy_status(paid_cents: int, charge_cents: int) -> str:
    """Canonical levy_item status from paid vs. total charge (integer cents).

    Mirrors Levy.outstanding_cents' definition of the charge total (principal +
    gst + interest + recovery_costs). Three states only, matching how status is
    consumed across the finance layer (the ``status != 'paid'`` open-item filters
    in get_open_levy_items / the 0008 partial index):
      - paid_cents <= 0                → 'issued'  (nothing applied)
      - 0 < paid_cents < charge_cents  → 'partial'
      - paid_cents >= charge_cents > 0 → 'paid'

    A zero-charge item (charge_cents <= 0) with any non-positive paid stays
    'issued' — there is nothing to mark paid.
    """
    if paid_cents <= 0:
        return "issued"
    if charge_cents > 0 and paid_cents >= charge_cents:
        return "paid"
    return "partial"


def _compute_entry_hash(entry_id: UUID, narration: str, prev_hash: Optional[str]) -> str:
    """Generated function header.

    Function: _compute_entry_hash
    Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = json.dumps({
        "id": str(entry_id),
        "narration": narration,
        "prev": prev_hash or "",
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class PostgresLedgerRepository:
    """Implements LedgerRepository using SQLAlchemy async session."""

    def __init__(self, session: AsyncSession) -> None:
        """Generated function header.

        Function: PostgresLedgerRepository.__init__
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._session = session

    async def save_journal_entry(self, entry: JournalEntry) -> JournalEntry:
        """Generated function header.

        Function: PostgresLedgerRepository.save_journal_entry
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        entry_id = entry.journal_entry_id or uuid.uuid4()
        now = _now()

        # Genesis entries start a fund's chain: prev_entry_hash = NULL only when
        # the fund has no prior entries. Non-genesis entries always chain to the
        # fund's latest hash to preserve tamper-evidence per-fund.
        prev_hash = None
        if entry.source_type != "genesis":
            last_hash_row = await self._session.execute(
                select(PgJournalEntry.entry_hash)
                .where(PgJournalEntry.scheme_id == entry.scheme_ref.scheme_id)
                .where(PgJournalEntry.fund_id == entry.fund_id)
                .order_by(PgJournalEntry.entry_number.desc())
                .limit(1)
            )
            prev_hash = last_hash_row.scalar()
        else:
            # For genesis: only NULL if no entries exist yet for this fund.
            # A second fund's genesis starts its own chain independently.
            existing_row = await self._session.execute(
                select(PgJournalEntry.entry_hash)
                .where(PgJournalEntry.scheme_id == entry.scheme_ref.scheme_id)
                .where(PgJournalEntry.fund_id == entry.fund_id)
                .limit(1)
            )
            if existing_row.scalar() is not None:
                raise ValueError(
                    f"Fund {entry.fund_id} already has journal entries; "
                    "genesis can only be posted once per fund."
                )
        entry_hash = _compute_entry_hash(entry_id, entry.narration, prev_hash)

        pg_entry = PgJournalEntry(
            journal_entry_id=entry_id,
            tenant_id=entry.scheme_ref.tenant_id,
            scheme_id=entry.scheme_ref.scheme_id,
            fund_id=entry.fund_id,
            period_id=entry.period_id,
            source_type=entry.source_type,
            source_reference=entry.source_reference,
            narration=entry.narration,
            status=EntryStatus.DRAFT.value,
            effective_on=entry.effective_on,
            posted_by=entry.posted_by,
            approved_by=entry.approved_by,
            evidence_document_id=entry.evidence_document_id,
            reversal_of_id=entry.reversal_of_id,
            idempotency_key=entry.idempotency_key,
            prev_entry_hash=prev_hash,
            entry_hash=entry_hash,
            is_test_data=entry.is_test_data,
            metadata_=entry.metadata,
            created_at=now,
        )
        self._session.add(pg_entry)

        for line in entry.lines:
            line_id = line.journal_line_id or uuid.uuid4()
            pg_line = PgJournalLine(
                journal_line_id=line_id,
                tenant_id=entry.scheme_ref.tenant_id,
                scheme_id=entry.scheme_ref.scheme_id,
                journal_entry_id=entry_id,
                gl_account_id=line.gl_account_id,
                direction=line.direction.value,
                amount_cents=line.amount_cents,
                gst_cents=line.gst_cents,
                lot_id=line.lot_id,
                party_id=line.party_id,
                narration=line.narration,
                created_at=now,
            )
            self._session.add(pg_line)

        await self._session.flush()

        object.__setattr__(entry, "journal_entry_id", entry_id) if hasattr(entry, "__dataclass_fields__") else None
        entry.journal_entry_id = entry_id
        return entry

    async def post_journal_entry(self, entry_id: UUID, scheme_ref: SchemeRef) -> JournalEntry:
        # Call the DB-level balance assertion function before flipping to 'posted'
        """Generated function header.

        Function: PostgresLedgerRepository.post_journal_entry
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        await self._session.execute(
            text("SELECT finance.assert_journal_balanced(:eid)"),
            {"eid": entry_id},
        )
        await self._session.execute(
            update(PgJournalEntry)
            .where(PgJournalEntry.journal_entry_id == entry_id)
            .values(status="posted", posted_at=_now())
        )
        await self._session.flush()
        return await self.get_journal_entry(entry_id, scheme_ref)

    async def get_journal_entry(self, entry_id: UUID, scheme_ref: SchemeRef) -> JournalEntry:
        """Generated function header.

        Function: PostgresLedgerRepository.get_journal_entry
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await self._session.execute(
            select(PgJournalEntry).where(PgJournalEntry.journal_entry_id == entry_id)
        )
        pg_entry = result.scalar_one()
        return self._to_domain_entry(pg_entry)

    def _to_domain_entry(self, pg: PgJournalEntry) -> JournalEntry:
        """Generated function header.

        Function: PostgresLedgerRepository._to_domain_entry
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        lines = [
            JournalLine(
                journal_line_id=ln.journal_line_id,
                gl_account_id=ln.gl_account_id,
                direction=EntryDirection(ln.direction),
                amount_cents=ln.amount_cents,
                gst_cents=ln.gst_cents,
                lot_id=ln.lot_id,
                party_id=ln.party_id,
                narration=ln.narration,
            )
            for ln in pg.lines
        ]
        return JournalEntry(
            journal_entry_id=pg.journal_entry_id,
            scheme_ref=SchemeRef(tenant_id=pg.tenant_id, scheme_id=pg.scheme_id),
            period_id=pg.period_id,
            source_type=pg.source_type,
            source_reference=pg.source_reference,
            narration=pg.narration,
            effective_on=pg.effective_on,
            lines=lines,
            fund_id=pg.fund_id,
            reversal_of_id=pg.reversal_of_id,
            idempotency_key=pg.idempotency_key,
            is_test_data=pg.is_test_data,
            metadata=pg.metadata_,
            evidence_document_id=pg.evidence_document_id,
            posted_by=pg.posted_by,
            approved_by=pg.approved_by,
        )

    async def find_or_create_levy_run(
            self,
            scheme_ref: SchemeRef,
            financial_year: str,
            quarter_no: Optional[int],
            levy_run_type: str,
            fund_id: UUID,
            issue_date: date,
            due_date: date,
    ) -> tuple[UUID, bool]:
        """See LedgerRepository.find_or_create_levy_run docstring. Insert
        attempt runs inside a SAVEPOINT (session.begin_nested()) so a
        unique-violation on levy_runs_natural_key_idx only unwinds this
        failed insert, never the caller's own in-progress transaction."""
        stmt = select(PgLevyRun.levy_run_id).where(
            PgLevyRun.scheme_id == scheme_ref.scheme_id,
            PgLevyRun.financial_year == financial_year,
            PgLevyRun.quarter_no == quarter_no,
            PgLevyRun.levy_run_type == levy_run_type,
            PgLevyRun.fund_id == fund_id,
        )
        existing = (await self._session.execute(stmt)).first()
        if existing:
            return existing[0], False

        run_id = uuid.uuid4()
        pg = PgLevyRun(
            levy_run_id=run_id,
            tenant_id=scheme_ref.tenant_id,
            scheme_id=scheme_ref.scheme_id,
            financial_year=financial_year,
            quarter_no=quarter_no,
            issue_date=issue_date,
            due_date=due_date,
            status="draft",
            levy_run_type=levy_run_type,
            fund_id=fund_id,
            created_at=_now(),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(pg)
                await self._session.flush()
        except IntegrityError:
            existing = (await self._session.execute(stmt)).first()
            if existing:
                return existing[0], False
            raise
        return run_id, True

    async def save_levy_run(
            self,
            scheme_ref: SchemeRef,
            financial_year: str,
            quarter_no: Optional[int],
            issue_date: date,
            due_date: date,
            levy_run_type: str = "ordinary",
    ) -> UUID:
        """Create a levy run row. ``levy_run_type`` must satisfy
        finance.levy_runs' ``levy_runs_type_chk`` constraint (migration 0069):
        'ordinary', 'special', 'adjustment', 'interest', or 'recovery' — an
        invalid value fails at the database, not silently coerced here.
        """
        run_id = uuid.uuid4()
        pg = PgLevyRun(
            levy_run_id=run_id,
            tenant_id=scheme_ref.tenant_id,
            scheme_id=scheme_ref.scheme_id,
            financial_year=financial_year,
            quarter_no=quarter_no,
            issue_date=issue_date,
            due_date=due_date,
            status="draft",
            levy_run_type=levy_run_type,
            created_at=_now(),
        )
        self._session.add(pg)
        await self._session.flush()
        return run_id

    async def save_levy_items(self, items: list[Levy]) -> list[Levy]:
        """Generated function header.

        Function: PostgresLedgerRepository.save_levy_items
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        for item in items:
            pg = PgLevyItem(
                levy_item_id=item.levy_item_id,
                tenant_id=item.scheme_ref.tenant_id,
                scheme_id=item.scheme_ref.scheme_id,
                levy_run_id=item.levy_run_id,
                lot_id=item.lot_id,
                owner_party_id=item.owner_party_id,
                fund_id=item.fund_id,
                principal_cents=item.principal_cents,
                gst_cents=item.gst_cents,
                interest_cents=item.interest_cents,
                recovery_costs_cents=item.recovery_costs_cents,
                paid_cents=item.paid_cents,
                status=item.status,
                journal_entry_id=item.journal_entry_id,
                owner_resolution_status=item.owner_resolution_status,
                created_at=_now(),
            )
            self._session.add(pg)
        await self._session.flush()
        return items

    async def get_levy_items_for_run(
            self, scheme_ref: SchemeRef, levy_run_id: UUID,
    ) -> list[Levy]:
        stmt = select(PgLevyItem).where(
            PgLevyItem.scheme_id == scheme_ref.scheme_id,
            PgLevyItem.levy_run_id == levy_run_id,
        )
        result = await self._session.execute(stmt)
        return [
            Levy(
                levy_item_id=pg.levy_item_id,
                scheme_ref=scheme_ref,
                levy_run_id=pg.levy_run_id,
                lot_id=pg.lot_id,
                owner_party_id=pg.owner_party_id,
                fund_id=pg.fund_id,
                principal_cents=pg.principal_cents,
                gst_cents=pg.gst_cents,
                interest_cents=pg.interest_cents,
                recovery_costs_cents=pg.recovery_costs_cents,
                paid_cents=pg.paid_cents,
                status=pg.status,
                journal_entry_id=pg.journal_entry_id,
                owner_resolution_status=pg.owner_resolution_status,
            )
            for pg in result.scalars().all()
        ]

    async def save_receipt(self, receipt: Receipt) -> Receipt:
        """Generated function header.

        Function: PostgresLedgerRepository.save_receipt
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pg = PgReceipt(
            receipt_id=receipt.receipt_id,
            tenant_id=receipt.scheme_ref.tenant_id,
            scheme_id=receipt.scheme_ref.scheme_id,
            trust_account_id=receipt.trust_account_id,
            bank_transaction_id=receipt.bank_transaction_id,
            payer_party_id=receipt.payer_party_id,
            lot_id=receipt.lot_id,
            channel=receipt.channel.value,
            received_on=receipt.received_on,
            amount_cents=receipt.amount_cents,
            external_reference=receipt.external_reference,
            journal_entry_id=receipt.journal_entry_id,
            reconstruction_batch_id=receipt.reconstruction_batch_id,
            metadata_=receipt.metadata or {},
            created_at=_now(),
        )
        self._session.add(pg)
        await self._session.flush()
        return receipt

    async def get_receipt(self, receipt_id: UUID, scheme_ref: SchemeRef) -> Receipt:
        """Generated function header.

        Function: PostgresLedgerRepository.get_receipt
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await self._session.execute(
            select(PgReceipt).where(PgReceipt.receipt_id == receipt_id)
        )
        pg = result.scalar_one()
        from services.financial_core.domain.entities import PaymentChannel
        return Receipt(
            receipt_id=pg.receipt_id,
            scheme_ref=scheme_ref,
            payer_party_id=pg.payer_party_id,
            lot_id=pg.lot_id,
            channel=PaymentChannel(pg.channel),
            received_on=pg.received_on,
            amount_cents=pg.amount_cents,
            external_reference=pg.external_reference,
            bank_transaction_id=pg.bank_transaction_id,
            trust_account_id=pg.trust_account_id,
            journal_entry_id=pg.journal_entry_id,
            reconstruction_batch_id=pg.reconstruction_batch_id,
            metadata=pg.metadata_ or {},
        )

    async def get_allocated_total_for_receipt(self, receipt_id: UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.sum(PgReceiptAllocation.allocated_cents), 0))
            .where(PgReceiptAllocation.receipt_id == receipt_id)
        )
        return int(result.scalar_one())

    async def find_active_receipt_by_bank_transaction(
            self,
            scheme_ref: SchemeRef,
            bank_transaction_id: UUID,
            lot_id: Optional[UUID],
            amount_cents: int,
    ) -> Optional[Receipt]:
        """Return an existing NON-REVERSED receipt already posted for this exact
        bank cash line (same scheme + bank_transaction_id + lot + amount), else None.

        A ``bank_transaction_id`` is the real-world identity of a single cash event,
        so at most one *active* receipt may exist per
        ``(scheme, bank_transaction_id, lot, amount)``. Two independent posting paths
        — the live match-promotion route (`routers/financial_matching.py`) and the
        GAP-FIN-031 derived-receipt backfill (`scripts/gap_fin_031_post_fy2026_...`) —
        key their idempotency on *different* strings (a bare `bank_transaction_id` UUID
        in `external_reference` vs a `gap-fin-031-derived:<uuid>` idempotency_key), so
        neither's own idempotency check ever sees the other's row. This guard, keyed on
        the ``bank_transaction_id`` they *share*, is the one that does — it is why the
        2026-08-03 batch-scoped reversal left an un-tagged twin behind (GAP-FIN-045).

        "Active" excludes reversed receipts: ``reverse_entry`` leaves the original entry
        ``posted`` and adds a separate ``reversal_of_id``-linked mirror rather than
        mutating it, so a receipt is live only when its entry is posted, is not itself a
        reversal, and has no posted mirror pointing back at it. Split deposits stay
        allowed — a different ``lot_id`` or ``amount_cents`` from the same bank line is
        not a duplicate.

        NOTE: this is a read-before-write check, not a hard constraint; it closes the
        cross-pipeline *sequential* duplicate (the two pipelines ran at different times).
        A "not reversed" condition cannot be expressed as a static partial unique index
        (it depends on the existence of a mirror row, and a plain unique index would
        wrongly block a legitimate re-post after a reversal), so the invariant is
        enforced at this layer.
        """
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT r.receipt_id
                    FROM finance.receipts r
                    JOIN finance.journal_entries je
                      ON je.journal_entry_id = r.journal_entry_id
                    WHERE r.scheme_id = :scheme_id
                      AND r.bank_transaction_id = :btid
                      AND r.amount_cents = :amount_cents
                      AND (CAST(:lot_id AS uuid) IS NULL OR r.lot_id = CAST(:lot_id AS uuid))
                      AND je.status = 'posted'
                      AND je.reversal_of_id IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM finance.journal_entries rev
                          WHERE rev.reversal_of_id = je.journal_entry_id
                            AND rev.status = 'posted'
                      )
                    ORDER BY r.created_at
                    LIMIT 1
                    """
                ),
                {
                    "scheme_id": str(scheme_ref.scheme_id),
                    "btid": str(bank_transaction_id),
                    "amount_cents": amount_cents,
                    "lot_id": str(lot_id) if lot_id else None,
                },
            )
        ).scalar()
        if row is None:
            return None
        return await self.get_receipt(row, scheme_ref)

    async def get_open_levy_items(
            self, scheme_ref: SchemeRef, lot_id: UUID
    ) -> list[Levy]:
        """Generated function header.

        Function: PostgresLedgerRepository.get_open_levy_items
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await self._session.execute(
            select(PgLevyItem)
            .where(
                PgLevyItem.scheme_id == scheme_ref.scheme_id,
                PgLevyItem.lot_id == lot_id,
                PgLevyItem.status != "paid",
            )
            .order_by(PgLevyItem.created_at.asc())
        )
        rows = result.scalars().all()
        return [
            Levy(
                levy_item_id=r.levy_item_id,
                scheme_ref=scheme_ref,
                levy_run_id=r.levy_run_id,
                lot_id=r.lot_id,
                owner_party_id=r.owner_party_id,
                fund_id=r.fund_id,
                principal_cents=r.principal_cents,
                gst_cents=r.gst_cents,
                interest_cents=r.interest_cents,
                recovery_costs_cents=r.recovery_costs_cents,
                paid_cents=r.paid_cents,
                status=r.status,
            )
            for r in rows
        ]

    async def upsert_owner_credit(
            self,
            scheme_ref: SchemeRef,
            lot_id: UUID,
            owner_party_id: UUID,
            amount_cents: int,
            source_journal_entry_id: UUID | None = None,
    ) -> int:
        """Accumulate unapplied credit on (scheme, lot, owner, fund), returning the new total.

        Raw SQL rather than the ORM because this is an atomic read-modify-write on a
        UNIQUE key and must not lose a concurrent increment. `ON CONFLICT ... DO UPDATE`
        makes the accumulate happen inside the statement; a select-then-update in Python
        would drop one of two receipts allocated at the same moment.

        `fund_id` is left NULL deliberately. The surplus is credit against the LOT, not
        against a particular fund — the owner overpaid their account, and which fund a
        future levy draws it down into is decided when that levy is raised, not now. The
        table's unique key includes fund_id, so NULL is a real member of the key here.

        NOTE on the unique key and NULL: Postgres treats NULLs as distinct in a UNIQUE
        constraint, so `ON CONFLICT (scheme_id, lot_id, owner_party_id, fund_id)` will
        NOT match an existing NULL-fund row. The partial unique index below is what makes
        the fund-less case actually conflict, and the WHERE clause must match it exactly.
        """
        if amount_cents <= 0:
            raise ValueError(
                f"upsert_owner_credit called with amount_cents={amount_cents}; "
                "credit must be positive — a negative surplus is an allocation bug, "
                "not a credit"
            )
        row = await self._session.execute(
            text(
                """
                INSERT INTO finance.owner_credit_balances
                    (tenant_id, scheme_id, lot_id, owner_party_id, fund_id,
                     available_cents, source_journal_entry_id, created_at, updated_at)
                VALUES
                    (:tenant_id, :scheme_id, :lot_id, :owner_party_id, NULL,
                     :amount, :source_je, NOW(), NOW())
                ON CONFLICT (scheme_id, lot_id, owner_party_id)
                    WHERE fund_id IS NULL
                DO UPDATE SET
                    available_cents = finance.owner_credit_balances.available_cents
                                      + EXCLUDED.available_cents,
                    updated_at = NOW()
                RETURNING available_cents
                """
            ),
            {
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "lot_id": str(lot_id),
                "owner_party_id": str(owner_party_id),
                "amount": int(amount_cents),
                "source_je": str(source_journal_entry_id) if source_journal_entry_id else None,
            },
        )
        return int(row.scalar_one())

    async def save_allocations(self, allocations: list[PaymentAllocation]) -> None:
        """Generated function header.

        Function: PostgresLedgerRepository.save_allocations
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        for alloc in allocations:
            if alloc.tenant_id is None:
                raise ValueError(
                    f"PaymentAllocation {alloc.allocation_id} missing tenant_id — "
                    "set it in the service layer before calling save_allocations"
                )
            pg = PgReceiptAllocation(
                allocation_id=alloc.allocation_id,
                tenant_id=alloc.tenant_id,
                receipt_id=alloc.receipt_id,
                levy_item_id=alloc.levy_item_id,
                allocation_type=alloc.allocation_type,
                allocated_cents=alloc.allocated_cents,
                created_at=_now(),
            )
            self._session.add(pg)
            if alloc.levy_item_id:
                await self._session.execute(
                    update(PgLevyItem)
                    .where(PgLevyItem.levy_item_id == alloc.levy_item_id)
                    .values(paid_cents=PgLevyItem.paid_cents + alloc.allocated_cents)
                )
        await self._session.flush()

    async def decrement_paid_for_year(
            self, scheme_ref: SchemeRef, lot_id: UUID, financial_year: str, amount_cents: int,
    ) -> list[LevyItemAdjustment]:
        """Most-recently-due item first (mirror of allocate_payment's oldest-open-item-
        first fill order — clawing back undoes the most recent postings first), capped
        per item at that item's own current paid_cents so no single line goes negative.
        Raw UPDATE, not a canonical allocation: this decrement isn't tied to any specific
        receipt/receipt_allocations row (there is deliberately none to point at — see
        RecordAdjustmentCommand's docstring), matching the accepted precedent already in
        this codebase for paid_cents corrections with no receipt to reference (GAP-FIN-046
        Bug A's allocation cleanup used the same raw-SQL-with-full-logging pattern)."""
        result = await self._session.execute(
            select(PgLevyItem)
            .join(PgLevyRun, PgLevyItem.levy_run_id == PgLevyRun.levy_run_id)
            .where(
                PgLevyItem.scheme_id == scheme_ref.scheme_id,
                PgLevyItem.lot_id == lot_id,
                PgLevyRun.financial_year == str(financial_year),
            )
            .order_by(PgLevyRun.due_date.desc(), PgLevyItem.created_at.desc())
        )
        items = result.scalars().all()

        # Pass 1 — PLAN ONLY, no writes: walk items in claw-back order and compute exactly
        # what would be taken from each, entirely in memory. This must fully validate
        # sufficiency BEFORE any UPDATE is issued — a single-pass write-then-check-after
        # would leave partial UPDATEs staged in the session the instant a shortfall is
        # discovered partway through, correct only if every caller reliably wraps this in
        # a savepoint. Validating first removes that dependency: an insufficient-funds
        # error here is guaranteed to mean zero rows were touched, not "maybe some were."
        remaining = amount_cents
        plan: list[tuple[PgLevyItem, int, int]] = []  # (item, take, new_paid)
        for item in items:
            if remaining <= 0:
                break
            take = min(remaining, item.paid_cents)
            if take <= 0:
                continue
            plan.append((item, take, item.paid_cents - take))
            remaining -= take

        if remaining > 0:
            raise ValueError(
                f"decrement_paid_for_year: lot {lot_id} financial_year {financial_year} — "
                f"requested {amount_cents} cents but only {amount_cents - remaining} cents of "
                f"paid_cents was available across {len(items)} levy_item(s) to claw back. "
                "Refusing to drive a line negative or apply a partial adjustment — no rows touched."
            )

        # Pass 2 — apply. Sufficiency is already proven, so every write here is expected
        # to succeed; nothing here should be able to raise for a "not enough funds" reason.
        touched: list[LevyItemAdjustment] = []
        for item, take, new_paid in plan:
            await self._session.execute(
                update(PgLevyItem)
                .where(PgLevyItem.levy_item_id == item.levy_item_id)
                .values(paid_cents=new_paid)
            )
            touched.append(LevyItemAdjustment(
                levy_item_id=item.levy_item_id, decremented_cents=take, paid_cents_after=new_paid,
            ))
        await self._session.flush()
        return touched

    # ------------------------------------------------------------------
    # GAP-FIN-057 — receipt-allocation reversal (adapter side)
    #
    # These three methods are the SERVER-SIDE / LIVE-VERIFICATION half of the
    # GAP-FIN-057 command (see tasks/GAP-FIN-057-reversal-command-spec.md). They are
    # deliberately left NOT IMPLEMENTED here: the service-layer orchestration
    # (FinancialCoreService.reverse_allocations) and its unit tests are complete and
    # testable against a mocked port, but the SQL below touches real production finance
    # tables and MUST be implemented + reconciliation-verified live (spec §6) before any
    # --apply. The intended SQL is inlined as comments so the implementer has the exact
    # shape; wire it up + prove the invariants (spec §4) on the server, not from code.
    # ------------------------------------------------------------------

    async def get_allocations_for_reversed_receipts(
            self, scheme_ref: SchemeRef, receipt_ids: Optional[list[UUID]] = None,
    ) -> list["AllocationToReverse"]:
        """GAP-FIN-057 §2 — SELECT-only identification of stale allocations.

        Returns every finance.receipt_allocations row whose parent receipt's journal
        entry has a POSTED reversal (finance.journal_entries.reversal_of_id =
        receipts.journal_entry_id, rev.status='posted'). Scoped to ``receipt_ids`` when
        given (single-unit canary / the reverse_entry auto-trigger), else the whole
        scheme. RLS restricts this to the tenant whose app.tenant_id the caller set.

        Caveat (spec §2): receipts.journal_entry_id is nullable — a reversed receipt
        with a NULL journal_entry_id is NOT matched by this join. The data-repair driver
        surfaces any such receipt as a counted, explicit exception (missing ≠ zero); this
        adapter deliberately does not embed a building-specific external_reference
        fallback (building-agnostic rule).
        """
        params: dict = {"scheme_id": str(scheme_ref.scheme_id)}
        receipt_filter = ""
        if receipt_ids is not None:
            # Empty scope → empty result, without issuing a query that ANY(ARRAY[]) would
            # make ambiguous to the driver.
            if not receipt_ids:
                return []
            receipt_filter = "AND r.receipt_id = ANY(:receipt_ids)"
            params["receipt_ids"] = [str(rid) for rid in receipt_ids]

        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT ra.allocation_id, ra.receipt_id, ra.levy_item_id,
                           ra.allocated_cents, li.fund_id, li.lot_id
                    FROM finance.receipt_allocations ra
                    JOIN finance.receipts        r    ON r.receipt_id       = ra.receipt_id
                    JOIN finance.levy_items      li   ON li.levy_item_id    = ra.levy_item_id
                    JOIN finance.journal_entries orig ON orig.journal_entry_id = r.journal_entry_id
                    WHERE r.scheme_id = :scheme_id
                      {receipt_filter}
                      AND ra.levy_item_id IS NOT NULL
                      AND EXISTS (
                          SELECT 1 FROM finance.journal_entries rev
                          WHERE rev.reversal_of_id = orig.journal_entry_id
                            AND rev.status = 'posted'
                      )
                    ORDER BY ra.receipt_id, ra.allocation_id
                    """
                ),
                params,
            )
        ).all()
        return [
            AllocationToReverse(
                allocation_id=row.allocation_id,
                receipt_id=row.receipt_id,
                levy_item_id=row.levy_item_id,
                allocated_cents=row.allocated_cents,
                fund_id=row.fund_id,
                lot_id=row.lot_id,
            )
            for row in rows
        ]

    async def get_receipt_ids_for_journal_entry(
            self, scheme_ref: SchemeRef, journal_entry_id: UUID,
    ) -> list[UUID]:
        """GAP-FIN-057 auto-trigger support — receipts sourced from a given journal
        entry (receipts.journal_entry_id = :jeid). Used by reverse_entry() to decide,
        after posting a reversal, whether the reversed entry was receipt-sourced and
        therefore needs its allocations cascaded. Empty list ⇒ not receipt-sourced
        (nothing to cascade)."""
        rows = (
            await self._session.execute(
                select(PgReceipt.receipt_id).where(
                    PgReceipt.scheme_id == scheme_ref.scheme_id,
                    PgReceipt.journal_entry_id == journal_entry_id,
                )
            )
        ).scalars().all()
        return list(rows)

    async def delete_allocations(self, allocation_ids: list[UUID]) -> None:
        """GAP-FIN-057 §3 — delete the stale receipt_allocations rows.

        allocated_cents carries a DB CHECK(> 0), so a negative offset row is impossible;
        the receipt + its posted reversal journal preserve the audit trail (an allocation
        is a derived linkage, not itself financial evidence — the GAP-FIN-046 decision).
        MUST run in the same transaction as, and BEFORE, recompute_paid_cents so the
        recompute sums only the survivors. No-op on an empty list.
        """
        if not allocation_ids:
            return
        await self._session.execute(
            delete(PgReceiptAllocation).where(
                PgReceiptAllocation.allocation_id.in_(allocation_ids)
            )
        )
        await self._session.flush()

    # @featuretrace:portal-anchored-paid-cents — The repair that must not be run blanket.
    # Layer: service
    # Data flow: caller-supplied levy_item_ids -> paid_cents := SUM(surviving
    #            receipt_allocations) -> finance.levy_items (building-scoped).
    # Related: backend/scripts/data_repair/gap_fin_046_phantom_removal_portal_anchored_20260809.py
    #          tests/backend/test_portal_anchored_paid_cents_exception.py
    #
    # LESSON (2026-08-27): taking an EXPLICIT id list, rather than "every item in the
    # scheme", is load-bearing safety, not an API style choice. Thirteen East Gate units
    # carry a deliberate, signed-off gap between paid_cents and their allocations because
    # the portal balance is ground truth and the receipts that would back it were proven
    # fabricated. Feeding those ids in here -- the natural response to an integrity report
    # listing 521 "mismatched" items -- would inflate their arrears by ~$224,733. Never
    # widen this to a scheme-wide sweep without excluding that documented exception.
    async def recompute_paid_cents(
            self, scheme_ref: SchemeRef, levy_item_ids: list[UUID],
    ) -> list["ReversedAllocationItem"]:
        """GAP-FIN-057 §3/§4 — recompute paid_cents from SURVIVING allocations.

        For each affected levy_item, sets ``paid_cents := Σ surviving
        receipt_allocations.allocated_cents`` and refreshes status. This is a RECOMPUTE,
        never a decrement — naturally idempotent and self-healing: a replay (after
        delete_allocations already removed the stale rows) recomputes the identical
        value. MUST run AFTER delete_allocations in the same transaction.

        Captures paid_cents_before by reading the current rows first, then updates —
        both within the caller's transaction, so before/after are consistent. Returns
        one ReversedAllocationItem per requested id (before + after, per-fund via
        fund_id) for the driver's §4 invariant asserts (per-fund, directional,
        replay-idempotent).
        """
        if not levy_item_ids:
            return []

        # 1. Current (BEFORE) state + charge components for status re-derivation.
        before_rows = (
            await self._session.execute(
                select(
                    PgLevyItem.levy_item_id,
                    PgLevyItem.fund_id,
                    PgLevyItem.lot_id,
                    PgLevyItem.paid_cents,
                    PgLevyItem.principal_cents,
                    PgLevyItem.gst_cents,
                    PgLevyItem.interest_cents,
                    PgLevyItem.recovery_costs_cents,
                ).where(PgLevyItem.levy_item_id.in_(levy_item_ids))
            )
        ).all()

        # 2. Sum of SURVIVING allocations per item (the stale rows are already deleted).
        survivor_rows = (
            await self._session.execute(
                select(
                    PgReceiptAllocation.levy_item_id,
                    func.coalesce(func.sum(PgReceiptAllocation.allocated_cents), 0),
                )
                .where(PgReceiptAllocation.levy_item_id.in_(levy_item_ids))
                .group_by(PgReceiptAllocation.levy_item_id)
            )
        ).all()
        surviving_by_item = {lid: int(total) for lid, total in survivor_rows}

        # 3. Per-item recompute + status, capturing before/after.
        results: list[ReversedAllocationItem] = []
        for row in before_rows:
            new_paid = surviving_by_item.get(row.levy_item_id, 0)
            charge = (
                row.principal_cents + row.gst_cents
                + row.interest_cents + row.recovery_costs_cents
            )
            new_status = _derive_levy_status(new_paid, charge)
            await self._session.execute(
                update(PgLevyItem)
                .where(PgLevyItem.levy_item_id == row.levy_item_id)
                .values(paid_cents=new_paid, status=new_status)
            )
            results.append(
                ReversedAllocationItem(
                    levy_item_id=row.levy_item_id,
                    fund_id=row.fund_id,
                    lot_id=row.lot_id,
                    paid_cents_before=row.paid_cents,
                    paid_cents_after=new_paid,
                )
            )
        await self._session.flush()
        return results

    async def get_overdue_levy_items(
            self, scheme_ref: SchemeRef, as_of_date: date
    ) -> list[Levy]:
        """Generated function header.

        Function: PostgresLedgerRepository.get_overdue_levy_items
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await self._session.execute(
            select(PgLevyItem, PgLevyRun.due_date)
            .join(PgLevyRun, PgLevyItem.levy_run_id == PgLevyRun.levy_run_id)
            .where(
                PgLevyItem.scheme_id == scheme_ref.scheme_id,
                PgLevyItem.status != "paid",
                PgLevyRun.due_date <= as_of_date,
            )
            .order_by(PgLevyRun.due_date.asc())
        )
        rows = result.all()
        return [
            Levy(
                levy_item_id=r.levy_item_id,
                scheme_ref=scheme_ref,
                levy_run_id=r.levy_run_id,
                lot_id=r.lot_id,
                owner_party_id=r.owner_party_id,
                fund_id=r.fund_id,
                principal_cents=r.principal_cents,
                gst_cents=r.gst_cents,
                interest_cents=r.interest_cents,
                recovery_costs_cents=r.recovery_costs_cents,
                paid_cents=r.paid_cents,
                status=r.status,
                due_date=due_date,
            )
            for r, due_date in rows
        ]

    async def save_reconcile_result(self, result: ReconcileResult) -> None:
        """Generated function header.

        Function: PostgresLedgerRepository.save_reconcile_result
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        from services.financial_core.adapters.db_postgres.models import PgOutbox
        from sqlalchemy import update as sa_update
        from services.financial_core.adapters.db_postgres.models import (
            PgReceipt,
        )
        # The bank_transactions table is not directly in the ORM models for this file,
        # so we use raw SQL update for the reconciliation_status.
        await self._session.execute(
            text(
                "UPDATE finance.bank_transactions "
                "SET reconciliation_status = :status "
                "WHERE bank_transaction_id = :btid"
            ),
            {
                "status": result.status.value,
                "btid": result.bank_transaction_id,
            },
        )
        await self._session.flush()

    async def get_current_accounting_period(self, scheme_ref: SchemeRef) -> UUID:
        """Generated function header.

        Function: PostgresLedgerRepository.get_current_accounting_period
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await self._session.execute(
            select(PgAccountingPeriod.period_id)
            .where(
                PgAccountingPeriod.scheme_id == scheme_ref.scheme_id,
                PgAccountingPeriod.status == "open",
            )
            .order_by(PgAccountingPeriod.starts_on.desc())
            .limit(1)
        )
        period_id = result.scalar()
        if period_id is None:
            raise RuntimeError(
                f"No open accounting period found for scheme {scheme_ref.scheme_id}"
            )
        return period_id

    async def get_accounting_period_for_date(
            self,
            scheme_ref: SchemeRef,
            effective_on: date,
            *,
            permitted_statuses: frozenset = frozenset({"open"}),
    ) -> AccountingPeriod:
        """Resolve the period whose [starts_on, ends_on] range covers
        effective_on. Deliberately does NOT filter by status in the WHERE
        clause — every covering row (any status) is fetched first, so
        AmbiguousAccountingPeriod is raised against the true overlap, not just
        the permitted subset (a scheme with two overlapping periods is still a
        data-integrity violation even if only one of them would pass the
        caller's permitted_statuses filter)."""
        result = await self._session.execute(
            select(PgAccountingPeriod).where(
                PgAccountingPeriod.scheme_id == scheme_ref.scheme_id,
                PgAccountingPeriod.starts_on <= effective_on,
                PgAccountingPeriod.ends_on >= effective_on,
            )
        )
        rows = result.scalars().all()

        if not rows:
            raise NoAccountingPeriodForDate(scheme_ref.scheme_id, effective_on)
        if len(rows) > 1:
            raise AmbiguousAccountingPeriod(
                scheme_ref.scheme_id, effective_on, [r.period_id for r in rows]
            )

        row = rows[0]
        if row.status not in permitted_statuses:
            raise AccountingPeriodNotPermitted(
                scheme_ref.scheme_id, effective_on, row.period_id, row.status,
                frozenset(permitted_statuses),
            )
        return AccountingPeriod(
            period_id=row.period_id,
            period_label=row.period_label,
            starts_on=row.starts_on,
            ends_on=row.ends_on,
            status=row.status,
        )

    async def claim_bank_transaction_for_batch(
            self,
            tenant_id: UUID,
            scheme_id: UUID,
            bank_transaction_id: UUID,
            reconstruction_batch_id: UUID,
    ) -> None:
        """Atomic RETURNING-based claim — a bare UPDATE affecting zero rows
        could mean either "already claimed by a different batch" or "row
        doesn't exist"; both must raise, never silently let the caller's
        journal post anyway. Idempotent when the row is already claimed by
        this same batch (a retry/resume of a partially-applied batch)."""
        result = await self._session.execute(
            text(
                """
                UPDATE finance.bank_transactions
                SET reconstruction_batch_id = :batch_id
                WHERE tenant_id = :tenant_id AND scheme_id = :scheme_id
                  AND bank_transaction_id = :btid
                  AND (reconstruction_batch_id IS NULL OR reconstruction_batch_id = :batch_id)
                RETURNING reconstruction_batch_id
                """
            ),
            {
                "batch_id": reconstruction_batch_id,
                "tenant_id": tenant_id,
                "scheme_id": scheme_id,
                "btid": bank_transaction_id,
            },
        )
        claimed = result.scalar()
        if claimed is not None:
            return

        existing = (
            await self._session.execute(
                text(
                    "SELECT reconstruction_batch_id FROM finance.bank_transactions "
                    "WHERE bank_transaction_id = :btid"
                ),
                {"btid": bank_transaction_id},
            )
        ).scalar()
        if existing is None:
            raise BankTransactionNotFound(bank_transaction_id, tenant_id, scheme_id)
        raise BankTransactionClaimConflict(bank_transaction_id, existing, reconstruction_batch_id)

    # Same admin-tier set financial_matching.py (_DECIDE_ROLES) and strata_sync
    # (_ADMIN_ROLES) already use for equivalent-sensitivity actions — reused here for
    # consistency rather than inventing a separate allowlist for this one check.
    _FINANCIAL_AUTHORITY_ROLES = frozenset({"super_admin", "strata_admin", "strata_manager", "ec_member"})

    async def user_has_financial_authority(self, scheme_ref: SchemeRef, user_id: UUID) -> bool:
        """Real, active user whose EFFECTIVE ROLE FOR THIS SCHEME has genuine
        financial/governance authority. See the port's docstring for the audit finding
        this corrects (2026-08-06): a tenant-only, scheme-blind check would pass a real,
        active user with zero relationship to this specific building.

        core.user_effective_role(user_id, scheme_id) is the canonical, already-existing
        function for scheme-scoped authority resolution (temp elevation → per-scheme
        role assignment → global/super_admin scheme_id-IS-NULL tier → base
        core.users.role, defaulting to 'guest'). It always returns SOME role, so the
        role value itself — not mere non-nullness — must be checked against
        _FINANCIAL_AUTHORITY_ROLES.
        """
        result = await self._session.execute(
            text(
                "SELECT core.user_effective_role(:user_id, :scheme_id)::text AS role "
                "FROM core.users "
                "WHERE user_id = :user_id AND tenant_id = :tenant_id AND is_active = TRUE "
                "LIMIT 1"
            ),
            {
                "user_id": str(user_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "tenant_id": str(scheme_ref.tenant_id),
            },
        )
        role = result.scalar()
        return role is not None and role in self._FINANCIAL_AUTHORITY_ROLES

    async def get_gl_account(self, scheme_ref: SchemeRef, account_code: str) -> UUID:
        """Generated function header.

        Function: PostgresLedgerRepository.get_gl_account
        Path: backend/services/financial_core/adapters/db_postgres/ledger_repo.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await self._session.execute(
            text(
                "SELECT gl_account_id "
                "FROM finance.gl_accounts "
                "WHERE scheme_id = :scheme_id "
                "  AND account_code = :account_code "
                "  AND status = CAST('active' AS core.record_status) "
                "LIMIT 1"
            ),
            {
                "scheme_id": str(scheme_ref.scheme_id),
                "account_code": account_code,
            },
        )
        gl_id = result.scalar()
        if gl_id is None:
            raise RuntimeError(
                f"GL account '{account_code}' not found for scheme {scheme_ref.scheme_id}"
            )
        return gl_id

    async def get_fund_type(self, scheme_ref: SchemeRef, fund_id: UUID) -> str:
        result = await self._session.execute(
            text("SELECT fund_type::text FROM finance.funds WHERE scheme_id = :scheme_id AND fund_id = :fund_id"),
            {"scheme_id": str(scheme_ref.scheme_id), "fund_id": str(fund_id)},
        )
        fund_type = result.scalar()
        if fund_type is None:
            raise RuntimeError(f"Fund {fund_id} not found for scheme {scheme_ref.scheme_id}")
        return fund_type

    async def count_journal_entries_for_fund(
            self, fund_id: UUID, scheme_ref: SchemeRef
    ) -> int:
        """Count journal entries for a fund. Used to validate genesis initialization."""
        from sqlalchemy import func
        result = await self._session.execute(
            select(func.count(PgJournalEntry.journal_entry_id))
            .where(
                PgJournalEntry.scheme_id == scheme_ref.scheme_id,
                PgJournalEntry.fund_id == fund_id,
            )
        )
        return result.scalar() or 0

    async def set_fund_opening_balance(
            self, fund_id: UUID, scheme_ref: SchemeRef, opening_balance_cents: int
    ) -> None:
        """Update finance.funds.opening_balance_cents."""
        from sqlalchemy import update, text
        await self._session.execute(
            update(PgFund)
            .where(
                PgFund.fund_id == fund_id,
                PgFund.scheme_id == scheme_ref.scheme_id,
            )
            .values(opening_balance_cents=opening_balance_cents)
        )

    # ── Expense-side (2026-07-17 historical-reconstruction addition) ────────

    async def save_expense(self, expense: Expense) -> Expense:
        """Persist a finance.expense_transactions row."""
        pg = PgExpenseTransaction(
            expense_id=expense.expense_id,
            tenant_id=expense.scheme_ref.tenant_id,
            scheme_id=expense.scheme_ref.scheme_id,
            fund_id=expense.fund_id,
            gl_account_id=expense.gl_account_id,
            vendor_name=expense.vendor_name,
            invoice_number=expense.invoice_number,
            category_name=expense.category_name,
            description=expense.description,
            financial_year=expense.financial_year,
            amount_cents=expense.amount_cents,
            gst_cents=expense.gst_cents,
            transaction_date=expense.transaction_date,
            journal_entry_id=expense.journal_entry_id,
            source=expense.source,
            derivation_level=expense.derivation_level,
            reconstruction_batch_id=expense.reconstruction_batch_id,
            metadata_=expense.metadata or {},
            idempotency_key=None,  # set via caller's idempotency_key on the journal, not duplicated here
            is_test_data=expense.is_test_data,
            created_at=_now(),
        )
        self._session.add(pg)
        await self._session.flush()
        return expense

    async def find_journal_entry_id_by_idempotency_key(
            self, scheme_ref: SchemeRef, idempotency_key: str
    ) -> Optional[UUID]:
        result = await self._session.execute(
            select(PgJournalEntry.journal_entry_id)
            .where(
                PgJournalEntry.tenant_id == scheme_ref.tenant_id,
                PgJournalEntry.idempotency_key == idempotency_key,
            )
            .limit(1)
        )
        return result.scalar()

    # ── Historical levy-item regeneration (bulk-backfill only) ───────────────

    async def upsert_levy_items(
            self, scheme_ref: SchemeRef, items: list[HistoricalLevyItemSpec]
    ) -> list[Levy]:
        """UPDATE principal_cents/gst_cents in place, or INSERT with the caller's
        deterministic levy_item_id if the (levy_run_id, lot_id, fund_id) row
        doesn't exist yet. Never touches paid_cents/status/levy_item_id/receipt
        allocations of an existing row — see plan02 amendment 5.
        """
        now = _now()
        results: list[Levy] = []
        for item in items:
            row = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO finance.levy_items
                            (levy_item_id, tenant_id, scheme_id, levy_run_id, lot_id,
                             owner_party_id, fund_id, principal_cents, gst_cents,
                             interest_cents, recovery_costs_cents, paid_cents, status, created_at)
                        VALUES
                            (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:sid AS uuid),
                             CAST(:run_id AS uuid), CAST(:lot_id AS uuid), CAST(:owner_id AS uuid),
                             CAST(:fund_id AS uuid), :principal, :gst, 0, 0, 0, 'issued', :now)
                        ON CONFLICT (levy_run_id, lot_id, fund_id) DO UPDATE SET
                            principal_cents = EXCLUDED.principal_cents,
                            gst_cents = EXCLUDED.gst_cents,
                            status = CASE
                                WHEN finance.levy_items.paid_cents >= (EXCLUDED.principal_cents + EXCLUDED.gst_cents)
                                THEN 'paid' ELSE 'issued'
                            END
                        RETURNING levy_item_id, paid_cents, status
                        """
                    ),
                    {
                        "id": str(item.levy_item_id),
                        "tid": str(scheme_ref.tenant_id),
                        "sid": str(scheme_ref.scheme_id),
                        "run_id": str(item.levy_run_id),
                        "lot_id": str(item.lot_id),
                        "owner_id": str(item.owner_party_id),
                        "fund_id": str(item.fund_id),
                        "principal": item.principal_cents,
                        "gst": item.gst_cents,
                        "now": now,
                    },
                )
            ).first()
            results.append(
                Levy(
                    levy_item_id=row.levy_item_id,
                    scheme_ref=scheme_ref,
                    levy_run_id=item.levy_run_id,
                    lot_id=item.lot_id,
                    owner_party_id=item.owner_party_id,
                    fund_id=item.fund_id,
                    principal_cents=item.principal_cents,
                    gst_cents=item.gst_cents,
                    paid_cents=row.paid_cents,
                    status=row.status,
                )
            )
        await self._session.flush()
        return results

    async def get_levy_item(self, scheme_ref: SchemeRef, levy_item_id: UUID) -> Optional[Levy]:
        result = await self._session.execute(
            select(PgLevyItem).where(
                PgLevyItem.levy_item_id == levy_item_id,
                PgLevyItem.scheme_id == scheme_ref.scheme_id,
            )
        )
        r = result.scalar_one_or_none()
        if r is None:
            return None
        return Levy(
            levy_item_id=r.levy_item_id,
            scheme_ref=scheme_ref,
            levy_run_id=r.levy_run_id,
            lot_id=r.lot_id,
            owner_party_id=r.owner_party_id,
            fund_id=r.fund_id,
            principal_cents=r.principal_cents,
            gst_cents=r.gst_cents,
            interest_cents=r.interest_cents,
            recovery_costs_cents=r.recovery_costs_cents,
            paid_cents=r.paid_cents,
            status=r.status,
        )

    async def classify_levy_journals(
            self, scheme_ref: SchemeRef, source_types: list[str]
    ) -> list[JournalClassificationEntry]:
        """Batched (not N+1) classification of every journal_entries row of the
        given source_types for this scheme. See JournalClassificationEntry
        docstring for the four categories."""
        entries_result = await self._session.execute(
            select(PgJournalEntry).where(
                PgJournalEntry.scheme_id == scheme_ref.scheme_id,
                PgJournalEntry.source_type.in_(source_types),
            )
        )
        entries = entries_result.scalars().all()
        if not entries:
            return []

        entry_ids = [e.journal_entry_id for e in entries]
        period_ids = {e.period_id for e in entries}

        period_rows = await self._session.execute(
            select(PgAccountingPeriod.period_id, PgAccountingPeriod.status)
            .where(PgAccountingPeriod.period_id.in_(period_ids))
        )
        period_status_by_id = {pid: status for pid, status in period_rows.all()}

        li_rows = await self._session.execute(
            select(
                PgLevyItem.journal_entry_id, PgLevyItem.levy_item_id,
                PgLevyItem.paid_cents, PgLevyItem.lot_id,
            ).where(PgLevyItem.journal_entry_id.in_(entry_ids))
        )
        levy_items_by_entry: dict = {}
        for jeid, li_id, paid, lot_id in li_rows.all():
            levy_items_by_entry.setdefault(jeid, []).append((li_id, paid, lot_id))

        receipt_alloc_rows = await self._session.execute(
            select(PgReceipt.journal_entry_id, func.count(PgReceiptAllocation.allocation_id))
            .select_from(PgReceiptAllocation)
            .join(PgReceipt, PgReceipt.receipt_id == PgReceiptAllocation.receipt_id)
            .where(PgReceipt.journal_entry_id.in_(entry_ids))
            .group_by(PgReceipt.journal_entry_id)
        )
        alloc_count_by_entry = {jeid: cnt for jeid, cnt in receipt_alloc_rows.all()}

        classifications: list[JournalClassificationEntry] = []
        for e in entries:
            if e.status == "draft":
                classifications.append(JournalClassificationEntry(
                    journal_entry_id=e.journal_entry_id,
                    source_type=e.source_type,
                    classification="draft",
                ))
                continue

            in_locked = period_status_by_id.get(e.period_id) in ("locked", "sealed")
            li_list = levy_items_by_entry.get(e.journal_entry_id, [])
            levy_paid = any(paid > 0 for (_, paid, _) in li_list)
            has_alloc = levy_paid or alloc_count_by_entry.get(e.journal_entry_id, 0) > 0
            is_provisional = bool((e.metadata_ or {}).get("provisional"))

            if in_locked or has_alloc:
                classification = "referenced_downstream"
            elif is_provisional:
                classification = "posted_provisional"
            else:
                # Conservative default: an existing posted entry with no explicit
                # provisional tag is treated as canonical, never auto-rebuilt.
                classification = "posted_canonical"

            classifications.append(JournalClassificationEntry(
                journal_entry_id=e.journal_entry_id,
                source_type=e.source_type,
                classification=classification,
                lot_id=li_list[0][2] if li_list else None,
                levy_item_id=li_list[0][0] if li_list else None,
                has_receipt_allocations=has_alloc,
                in_locked_period=in_locked,
            ))
        return classifications

    async def rebuild_provisional_journals(
            self, scheme_ref: SchemeRef, source_types: list[str]
    ) -> int:
        """Scoped delete of ONLY draft/posted_provisional journal_entries (+lines)
        for the given source_types. Refuses (raises) if any in-scope entry
        classifies as posted_canonical/referenced_downstream — the service layer
        must route those through reverse_and_replace instead.

        Deletion only — re-derivation/re-posting of the deleted scope is the
        caller's responsibility (e.g. re-running create_levy/record_payment for
        the affected lots), matching how a scoped correction should require an
        explicit, reviewed re-post rather than an implicit auto-rebuild.
        """
        classifications = await self.classify_levy_journals(scheme_ref, source_types)
        unsafe = [c for c in classifications if c.classification in ("posted_canonical", "referenced_downstream")]
        if unsafe:
            raise ValueError(
                f"Refusing scoped rebuild: {len(unsafe)} of {len(classifications)} journal "
                f"entries classify as posted_canonical/referenced_downstream (first offender: "
                f"{unsafe[0].journal_entry_id}, classification={unsafe[0].classification}). "
                f"Use reverse_and_replace_posted_journals for those instead."
            )
        rebuildable_ids = [c.journal_entry_id for c in classifications if c.classification in ("draft", "posted_provisional")]
        if not rebuildable_ids:
            return 0

        await self._session.execute(text("ALTER TABLE finance.journal_entries DISABLE TRIGGER trg_prevent_posted_journal_update"))
        try:
            await self._session.execute(delete(PgJournalLine).where(PgJournalLine.journal_entry_id.in_(rebuildable_ids)))
            await self._session.execute(delete(PgJournalEntry).where(PgJournalEntry.journal_entry_id.in_(rebuildable_ids)))
        finally:
            await self._session.execute(text("ALTER TABLE finance.journal_entries ENABLE TRIGGER trg_prevent_posted_journal_update"))
        await self._session.flush()
        return len(rebuildable_ids)
