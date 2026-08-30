# @featuretrace:financial_core — Domain entities and commands for the financial core.
# Layer: domain
# Data flow: services/financial_core/service.py orchestrates these against
#            services/financial_core/domain/ports.py (building-scoped).
# Related: backend/services/financial_core/service.py
#          backend/services/financial_core/domain/ports.py
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#          backend/services/levy_generation_service.py
"""Domain entities for the financial core.

These are plain dataclasses/Pydantic models — NO SQLAlchemy here.
They represent the business concepts that cross all layer boundaries.

All amount fields are in integer cents (BIGINT).
Dates are Python date objects; timestamps are datetime with UTC timezone.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID


# ------------------------------------------------------------------ value types

class EntryDirection(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class EntryStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    POSTED = "posted"
    REVERSED = "reversed"
    VOIDED = "voided"


class FundType(str, Enum):
    ADMIN = "admin"
    CAPITAL_WORKS = "capital_works"
    SINKING = "sinking"
    SPECIAL_PURPOSE = "special_purpose"
    OTHER = "other"


class PaymentChannel(str, Enum):
    BPAY = "bpay"
    DEFT = "deft"
    EFT = "eft"
    CARD = "card"
    CASH = "cash"
    CHEQUE = "cheque"
    BANK_TRANSFER = "bank_transfer"
    ABA = "aba"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class ReconciliationStatus(str, Enum):
    UNMATCHED = "unmatched"
    CANDIDATE = "candidate"
    MATCHED = "matched"
    IGNORED = "ignored"
    REVERSED = "reversed"


# ------------------------------------------------------------------ helpers

def cents(value: float | Decimal | int) -> int:
    """Convert a decimal dollar amount to integer cents. Raises on loss of precision."""
    c = int(round(Decimal(str(value)) * 100))
    if abs(c - float(value) * 100) > 0.005:
        raise ValueError(f"Precision loss converting {value!r} to cents")
    return c


# ------------------------------------------------------------------ domain entities

@dataclass(frozen=True)
class SchemeRef:
    """Immutable reference to a tenant/scheme combination."""
    tenant_id: UUID
    scheme_id: UUID


@dataclass(frozen=True)
class AccountingPeriod:
    """A resolved finance.accounting_periods row — the period whose date range
    covers a given effective_on date, returned by
    LedgerRepository.get_accounting_period_for_date()."""
    period_id: UUID
    period_label: str
    starts_on: date
    ends_on: date
    status: str


@dataclass(frozen=True)
class HistoricalPostingAuthorisation:
    """Explicit, audited, dual-control permission to post into a non-open
    accounting period. Only RecordHistoricalExpenseCommand and
    ChargeHistoricalLotFeeCommand carry this — never the ordinary
    RecordExpenseCommand/ChargeLotFeeCommand, and neither this type nor those
    two commands are importable from backend/routers/ or adapter.py (enforced
    by tests/backend/test_financial_core_service.py's isolation test).

    approved_by and executed_by are deliberately distinct fields: the person
    who signed off on the reconstruction batch is not necessarily the person
    running --apply. Dual control requires them to differ — checked here at
    construction time, and re-checked in the batch-approval/apply flow after
    both identities have been resolved against a real tenant user (see
    identity_repo.get_user_by_id()), so a merely-different UUID that doesn't
    belong to a real user can't slip through as satisfying dual control.
    """
    reconstruction_batch_id: UUID
    reason: str
    approved_by: UUID
    executed_by: UUID
    approval_reference: str
    evidence_reference: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise ValueError("HistoricalPostingAuthorisation.reason must be non-empty")
        if not self.approved_by or not self.executed_by:
            raise ValueError("HistoricalPostingAuthorisation.approved_by and executed_by are both required")
        if self.approved_by == self.executed_by:
            raise ValueError(
                "HistoricalPostingAuthorisation.approved_by and executed_by must be "
                "different users — dual control requires a second identity"
            )


@dataclass(frozen=True)
class Money:
    """Value object: a non-negative amount in integer cents."""
    cents: int

    def __post_init__(self) -> None:
        """Generated function header.

        Function: Money.__post_init__
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.cents < 0:
            raise ValueError(f"Money cannot be negative: {self.cents}")

    def __add__(self, other: "Money") -> "Money":
        """Generated function header.

        Function: Money.__add__
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return Money(self.cents + other.cents)

    def __sub__(self, other: "Money") -> "Money":
        """Generated function header.

        Function: Money.__sub__
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = self.cents - other.cents
        if result < 0:
            raise ValueError("Money subtraction would produce negative value")
        return Money(result)

    @classmethod
    def zero(cls) -> "Money":
        """Generated function header.

        Function: Money.zero
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return cls(0)

    @classmethod
    def from_dollars(cls, dollars: float | Decimal) -> "Money":
        """Generated function header.

        Function: Money.from_dollars
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return cls(cents(dollars))


@dataclass
class JournalLine:
    """A single debit or credit leg of a journal entry."""
    gl_account_id: UUID
    direction: EntryDirection
    amount_cents: int
    gst_cents: int = 0
    lot_id: Optional[UUID] = None
    party_id: Optional[UUID] = None
    narration: Optional[str] = None
    journal_line_id: Optional[UUID] = None

    def __post_init__(self) -> None:
        """Generated function header.

        Function: JournalLine.__post_init__
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.amount_cents <= 0:
            raise ValueError(f"Journal line amount must be positive, got {self.amount_cents}")
        if self.gst_cents < 0:
            raise ValueError(f"GST amount cannot be negative, got {self.gst_cents}")


@dataclass
class JournalEntry:
    """A balanced double-entry journal.

    Invariants enforced here (also enforced at DB level via trigger):
    - At least one debit and one credit line
    - Total debits == total credits
    - All amounts are positive integers
    """
    scheme_ref: SchemeRef
    period_id: UUID
    source_type: str
    narration: str
    effective_on: date
    lines: list[JournalLine]
    source_reference: Optional[str] = None
    idempotency_key: Optional[str] = None
    fund_id: Optional[UUID] = None
    reversal_of_id: Optional[UUID] = None
    is_test_data: bool = False
    metadata: dict = field(default_factory=dict)
    journal_entry_id: Optional[UUID] = None
    evidence_document_id: Optional[UUID] = None
    posted_by: Optional[UUID] = None
    approved_by: Optional[UUID] = None

    def __post_init__(self) -> None:
        """Generated function header.

        Function: JournalEntry.__post_init__
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._validate()

    def _validate(self) -> None:
        """Generated function header.

        Function: JournalEntry._validate
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not self.lines:
            raise ValueError("Journal entry must have at least one line")
        debits = sum(l.amount_cents for l in self.lines if l.direction == EntryDirection.DEBIT)
        credits = sum(l.amount_cents for l in self.lines if l.direction == EntryDirection.CREDIT)
        if debits == 0:
            raise ValueError("Journal entry must have at least one debit line")
        if credits == 0:
            raise ValueError("Journal entry must have at least one credit line")
        if debits != credits:
            raise ValueError(
                f"Journal entry is not balanced: debit={debits} credit={credits}"
            )

    @property
    def debit_total_cents(self) -> int:
        """Generated function header.

        Function: JournalEntry.debit_total_cents
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return sum(l.amount_cents for l in self.lines if l.direction == EntryDirection.DEBIT)

    @property
    def credit_total_cents(self) -> int:
        """Generated function header.

        Function: JournalEntry.credit_total_cents
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return sum(l.amount_cents for l in self.lines if l.direction == EntryDirection.CREDIT)


@dataclass
class Levy:
    """A levy charge against a specific lot."""
    levy_item_id: UUID
    scheme_ref: SchemeRef
    levy_run_id: UUID
    lot_id: UUID
    owner_party_id: UUID
    fund_id: UUID
    principal_cents: int
    gst_cents: int = 0
    interest_cents: int = 0
    recovery_costs_cents: int = 0
    paid_cents: int = 0
    status: str = "issued"
    due_date: Optional[date] = None
    # Present in the base schema (0004), ORM-mapped (PgLevyItem.journal_entry_id)
    # but never set by save_levy_items() until charge_lot_fee() started passing
    # it — links this levy_item back to the journal entry that created it.
    journal_entry_id: Optional[UUID] = None
    # "resolved" | "unresolved" — added by migration 0076 for
    # create_historical_levy()'s sentinel-placeholder-owner case; every non-historical
    # Levy stays "resolved" (the default, matching the column's DB default).
    owner_resolution_status: str = "resolved"

    @property
    def outstanding_cents(self) -> int:
        """Generated function header.

        Function: Levy.outstanding_cents
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        total = self.principal_cents + self.gst_cents + self.interest_cents + self.recovery_costs_cents
        return max(0, total - self.paid_cents)


@dataclass
class Receipt:
    """A payment received from an owner."""
    receipt_id: UUID
    scheme_ref: SchemeRef
    payer_party_id: UUID
    lot_id: UUID
    channel: PaymentChannel
    received_on: date
    amount_cents: int
    external_reference: Optional[str] = None
    bank_transaction_id: Optional[UUID] = None
    trust_account_id: Optional[UUID] = None
    journal_entry_id: Optional[UUID] = None
    # Provenance (Phase 0B, migration 0066) — set when the paying bank_transaction
    # carried reconstruction metadata. None for ordinary observed payments.
    reconstruction_batch_id: Optional[UUID] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Generated function header.

        Function: Receipt.__post_init__
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.amount_cents <= 0:
            raise ValueError(f"Receipt amount must be positive, got {self.amount_cents}")


@dataclass
class Expense:
    """A settled (already-paid or confirmed) expense transaction.

    derivation_level distinguishes how reliable the amount/date/vendor are:
      exact               — invoice/bank reference has supplier, date, amount, GST.
      source_derived       — AGM/audited category line has category + amount, not full detail.
      aggregate_balancing  — only a year/fund total is known; one small balancing record.
    """
    expense_id: UUID
    scheme_ref: SchemeRef
    category_name: str
    amount_cents: int  # ex-GST
    gst_cents: int
    transaction_date: date
    fund_id: Optional[UUID] = None
    gl_account_id: Optional[UUID] = None
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    financial_year: Optional[str] = None
    description: Optional[str] = None
    journal_entry_id: Optional[UUID] = None
    source: str = "historical_import"
    derivation_level: str = "aggregate_balancing"
    reconstruction_batch_id: Optional[UUID] = None
    is_test_data: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.amount_cents <= 0:
            raise ValueError(f"Expense amount must be positive, got {self.amount_cents}")
        if self.gst_cents < 0:
            raise ValueError(f"GST amount cannot be negative, got {self.gst_cents}")
        if self.derivation_level not in {"exact", "source_derived", "aggregate_balancing"}:
            raise ValueError(f"Invalid derivation_level: {self.derivation_level!r}")
        if self.source not in {"historical_import", "invoice_confirmed", "manual_adjustment"}:
            raise ValueError(f"Invalid source: {self.source!r}")


@dataclass
class JournalClassificationEntry:
    """One journal entry's classification for a scoped rebuild/reversal decision.

    - draft               — never posted; safe to edit/delete directly.
    - posted_provisional  — posted, but flagged migration-provisional and has no
                             downstream reference — safe for a scoped rebuild.
    - posted_canonical    — posted and outside the provisional window — must be
                             corrected via reversal + replacement, never rebuilt.
    - referenced_downstream — has receipt_allocations or falls inside a locked/
                             sealed accounting period — must be corrected via
                             reversal + replacement, never rebuilt.
    """
    journal_entry_id: UUID
    source_type: str
    classification: str
    lot_id: Optional[UUID] = None
    levy_item_id: Optional[UUID] = None
    has_receipt_allocations: bool = False
    in_locked_period: bool = False


@dataclass
class PaymentAllocation:
    """How a receipt is split across levy/interest/costs."""
    allocation_id: UUID
    receipt_id: UUID
    levy_item_id: Optional[UUID]
    allocation_type: str
    allocated_cents: int
    tenant_id: Optional[UUID] = None  # set by service layer; required before persisting


@dataclass
class ArrearsReport:
    """Arrears summary per lot as of a date."""
    scheme_ref: SchemeRef
    as_of_date: date
    items: list["ArrearsLineItem"] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.utcnow())

    @property
    def total_outstanding_cents(self) -> int:
        """Generated function header.

        Function: ArrearsReport.total_outstanding_cents
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return sum(i.outstanding_cents for i in self.items)


@dataclass
class ArrearsLineItem:
    lot_id: UUID
    lot_number: str
    owner_name: str
    principal_overdue_cents: int
    interest_cents: int
    recovery_costs_cents: int
    days_overdue: int

    @property
    def outstanding_cents(self) -> int:
        """Generated function header.

        Function: ArrearsLineItem.outstanding_cents
        Path: backend/services/financial_core/domain/entities.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self.principal_overdue_cents + self.interest_cents + self.recovery_costs_cents


@dataclass
class ReverseResult:
    """Result of reversing a journal entry."""
    original_entry_id: UUID
    reversal_entry_id: UUID
    reason: str
    # GAP-FIN-057: set when reverse_entry() cascaded the receipt-allocation unwind
    # (the reversed entry was receipt-sourced and cascade_allocations was on). None when
    # the entry was not receipt-sourced or the cascade was disabled. Typed loosely to
    # avoid a forward-reference on ReverseAllocationsResult (defined later in this
    # module); it always holds a ReverseAllocationsResult when present.
    cascade: Optional["ReverseAllocationsResult"] = None


@dataclass
class LevyItemAdjustment:
    """One levy_item touched by a record_adjustment() decrement."""
    levy_item_id: UUID
    decremented_cents: int
    paid_cents_after: int


@dataclass
class AdjustmentResult:
    """Result of record_adjustment()."""
    journal_entry_id: UUID
    lot_id: UUID
    amount_cents: int
    financial_year: str
    items_adjusted: list[LevyItemAdjustment]


@dataclass
class RebuildResult:
    """Result of reverse_and_replace_posted_levy_journals()."""
    reversed_count: int
    replaced_count: int
    skipped_count: int = 0


@dataclass
class ReconcileResult:
    """Result of reconciling a bank transaction to a receipt."""
    bank_transaction_id: UUID
    receipt_id: UUID
    status: ReconciliationStatus
    difference_cents: int


# ------------------------------------------------------------------ commands

@dataclass
class CreateLevyCommand:
    """Command to issue a levy run for a scheme."""
    scheme_ref: SchemeRef
    financial_year: str
    quarter_no: Optional[int]
    issue_date: date
    due_date: date
    lot_levies: list["LotLevySpec"]
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    levy_run_type: str = "ordinary"


@dataclass
class LotLevySpec:
    """Per-lot levy specification within a CreateLevyCommand."""
    lot_id: UUID
    owner_party_id: UUID
    fund_id: UUID
    principal_cents: int
    gst_cents: int = 0
    # Set by CreateHistoricalLevyCommand callers when owner_party_id resolves
    # to a per-scheme sentinel placeholder party (unresolvable historical
    # ownership) rather than a real owner — "resolved" for every ordinary
    # LotLevySpec built by the live create_levy() path. Never read by
    # create_levy()/create_historical_levy() itself; carried through to
    # JournalEntry.metadata so the charge is retained, not dropped, while
    # remaining flagged for later correction.
    owner_resolution_status: str = "resolved"


@dataclass
class CreateHistoricalLevyCommand:
    """Historical-reconstruction-only variant of CreateLevyCommand.

    Structurally separate (not an optional field on the public command) so
    the privileged closed-period pathway has no HTTP-reachable entry point —
    adapter.py/routers never construct this type, matching
    RecordHistoricalExpenseCommand/ChargeHistoricalLotFeeCommand. Only
    FinancialCoreService.create_historical_levy() accepts it, and only that
    method may resolve a `closed` accounting period for a levy run. Posts the
    ordinary per-lot levy CHARGE (AR debit / income credit) that the doc
    `docs/finances/next-phase-financial-recontruction-demo-bank-eastgate-2021-2026.md`
    distinguishes from a bank receipt — this command never touches Demo Bank
    or `demo_bank_transactions`.
    """
    scheme_ref: SchemeRef
    financial_year: str
    quarter_no: Optional[int]
    issue_date: date
    due_date: date
    lot_levies: list["LotLevySpec"]
    authorisation: HistoricalPostingAuthorisation
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    levy_run_type: str = "ordinary"
    levy_type: str = "ordinary"  # "ordinary" | "special" | "adjustment" — forward-compatible tag

    def __post_init__(self) -> None:
        if self.due_date < self.issue_date:
            raise ValueError("CreateHistoricalLevyCommand due_date must be on or after issue_date")
        if not self.lot_levies:
            raise ValueError("CreateHistoricalLevyCommand requires at least one lot levy spec")


@dataclass
class RecordPaymentCommand:
    """Command to record an inbound payment."""
    scheme_ref: SchemeRef
    payer_party_id: UUID
    lot_id: UUID
    channel: PaymentChannel
    received_on: date
    amount_cents: int
    trust_account_id: Optional[UUID] = None
    bank_transaction_id: Optional[UUID] = None
    external_reference: Optional[str] = None
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    posted_by: Optional[UUID] = None
    approved_by: Optional[UUID] = None
    metadata: dict = field(default_factory=dict)
    # Provenance (2026-07-17 audit fix): set when the paying bank_transaction
    # carries reconstruction metadata (see BankTxObserved.metadata, Phase 0B).
    # Added because record_payment() previously had no way to propagate this
    # onto the created Receipt even though finance.receipts and the Receipt
    # dataclass both already carry the column/field for it.
    reconstruction_batch_id: Optional[UUID] = None


@dataclass
class RecordHistoricalPaymentCommand:
    """Historical-reconstruction-only variant of RecordPaymentCommand.

    Structurally separate (not an optional field on the public command) so
    the privileged closed-period pathway has no HTTP-reachable entry point —
    same pattern as RecordHistoricalExpenseCommand/ChargeHistoricalLotFeeCommand.
    adapter.py's _record_payment_postgres() constructs RecordPaymentCommand
    only, never this type. Only FinancialCoreService.record_historical_payment()
    accepts it, and only that method may resolve a `closed` accounting period.

    Added 2026-08-01 for East Gate's 2021-2025 levy-payment backfill: the
    ordinary record_payment() unconditionally rejects any received_on falling
    in a closed accounting period, and East Gate's 2021-2025 periods are all
    closed (only 2020 genesis and 2026 current are open) — there was
    previously no supported way to post a real historical levy payment at all.
    """
    scheme_ref: SchemeRef
    payer_party_id: UUID
    lot_id: UUID
    channel: PaymentChannel
    received_on: date
    amount_cents: int
    authorisation: HistoricalPostingAuthorisation
    trust_account_id: Optional[UUID] = None
    bank_transaction_id: Optional[UUID] = None
    external_reference: Optional[str] = None
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class RecordAdjustmentCommand:
    """Corrects a lot's recorded paid balance by an evidenced amount — NOT a payment,
    and NOT a reversal of any specific prior entry.

    Used when reconstructed/historical receipts are proven, via independent evidence
    (e.g. a portal balance snapshot), to have overstated how much a lot actually paid,
    and no single receipt can be safely identified as "the wrong one" to reverse.
    GAP-FIN-046's duplicate-receipt-finder (2026-08-05) proved receipt-attribute
    heuristics are unsafe for this class: real duplicates carry distinct UUID
    external_references invisible to any signature, and the heuristic false-positived
    on legitimate same-amount/timestamp receipts (UA008/UA014). Where the
    over-statement is spread across years of "assume paid on time" reconstruction
    rather than concentrated in one bad receipt, a single documented adjustment
    against verified evidence is the only safe correction — not a search for which
    receipt to reverse.

    Posts a DEBIT ar / CREDIT bank journal entry (amount_cents is always positive —
    the amount to claw back) and decrements finance.levy_items.paid_cents for
    financial_year, most-recently-due item first, capped per item at that item's own
    paid_cents (never driving a single line negative). Structurally separate from
    ReverseEntryCommand (mirrors one specific existing entry) and from
    RecordHistoricalPaymentCommand (posts a new positive receipt) — this posts a new
    entry not tied to reversing anything, sized from external evidence, not from any
    stored receipt amount. Same privileged-script-only, HistoricalPostingAuthorisation-
    gated pattern as the other Historical* commands: never constructed by adapter.py
    or any router.
    """
    scheme_ref: SchemeRef
    lot_id: UUID
    amount_cents: int
    financial_year: str
    authorisation: HistoricalPostingAuthorisation
    effective_on: Optional[date] = None
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.amount_cents <= 0:
            raise ValueError(f"Adjustment amount must be positive, got {self.amount_cents}")
        if not self.financial_year or not str(self.financial_year).strip():
            raise ValueError("RecordAdjustmentCommand.financial_year must be non-empty")


@dataclass
class AllocatePaymentCommand:
    """Command to allocate an existing receipt across levy items."""
    scheme_ref: SchemeRef
    receipt_id: UUID
    allocation_order: list[str] = field(default_factory=lambda: ["levy", "interest", "costs"])


@dataclass
class GenerateArrearsCommand:
    """Command to compute arrears as of a date."""
    scheme_ref: SchemeRef
    as_of_date: date


@dataclass
class ReverseEntryCommand:
    """Command to reverse a posted journal entry."""
    scheme_ref: SchemeRef
    journal_entry_id: UUID
    reason: str
    effective_on: Optional[date] = None
    idempotency_key: Optional[str] = None
    # GAP-FIN-057 structural fix: when the reversed entry is receipt-sourced, also
    # unwind the receipt_allocations it left behind (reverse_allocations) so paid_cents
    # stops reading reversed cash as applied. Defaults ON — this is the durable fix that
    # stops every future reversal re-opening the gap. Set False only for a bare
    # journal-only reversal whose allocation cleanup is handled separately (e.g. the
    # one-time data-repair driver, which reverses and reconciles in its own controlled
    # per-unit transaction).
    cascade_allocations: bool = True


@dataclass
class ReverseAllocationsCommand:
    """Command to unwind the finance.receipt_allocations left behind when
    reverse_entry() reversed only the GL journal (GAP-FIN-057).

    reverse_entry() mirrors journal lines but never touches receipt_allocations or
    levy_items.paid_cents, so a reversed payment-receipt leaves its allocations in
    place and paid_cents overstated. This command finds the allocations whose parent
    receipt's journal entry has a POSTED reversal, removes them, and recomputes the
    affected levy_items.paid_cents from the SURVIVING allocations.

    Distinct from record_adjustment (which decrements paid_cents when NO single
    receipt is safely identifiable): here the reversed receipt IS identifiable, so the
    unwind is exact, not spread heuristically. Posts NO journal — the GL was already
    corrected by the earlier reverse_entry().
    """
    scheme_ref: SchemeRef
    reason: str
    # Optional narrowing to specific receipts (e.g. a single-unit canary in the
    # data-repair driver). None = every reversed-receipt allocation in the scheme.
    receipt_ids: Optional[list[UUID]] = None
    idempotency_key: Optional[str] = None


@dataclass
class AllocationToReverse:
    """A finance.receipt_allocations row whose parent receipt's journal entry has a
    posted reversal — i.e. it must be unwound (GAP-FIN-057)."""
    allocation_id: UUID
    receipt_id: UUID
    levy_item_id: UUID
    allocated_cents: int
    fund_id: UUID
    lot_id: UUID


@dataclass
class ReversedAllocationItem:
    """One levy_item whose paid_cents was recomputed by reverse_allocations()."""
    levy_item_id: UUID
    fund_id: UUID
    lot_id: UUID
    paid_cents_before: int
    paid_cents_after: int


@dataclass
class ReverseAllocationsResult:
    """Result of reverse_allocations() (GAP-FIN-057). No GL journal is posted."""
    reversed_receipt_ids: list[UUID]
    deleted_allocation_ids: list[UUID]
    reversed_allocated_cents: int
    affected_items: list[ReversedAllocationItem]

    @property
    def is_noop(self) -> bool:
        """True when nothing needed reversing (already clean / idempotent replay)."""
        return not self.deleted_allocation_ids


@dataclass
class ReconcileBankTransactionCommand:
    """Command to match a bank transaction to a receipt."""
    scheme_ref: SchemeRef
    bank_transaction_id: UUID
    receipt_id: UUID


@dataclass
class PostGenesisJournalCommand:
    """Command to post a genesis (opening balance) journal entry for a fund.

    Genesis journals are the first entry posted for a fund, establishing the
    opening balance and initializing the hash chain. They are used during
    clean-slate onboarding to set up the initial state.

    The method creates a balanced double-entry with:
    - One debit to assets:bank for the opening balance
    - One credit to equity:opening_balances_clearing for the same amount
    """
    scheme_ref: SchemeRef
    fund_id: UUID
    opening_balance_cents: int
    as_at_date: date
    evidence_doc_id: Optional[UUID] = None
    evidence_doc_hash: Optional[str] = None
    posted_by_user_id: Optional[UUID] = None
    approved_by_user_id: Optional[UUID] = None
    posted_by_user_name: Optional[str] = None
    building_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    is_test_data: bool = False


@dataclass
class RecordExpenseCommand:
    """Command to record a settled/confirmed expense (historical import or AP invoice)."""
    scheme_ref: SchemeRef
    category_name: str
    amount_cents: int  # ex-GST
    gst_cents: int
    transaction_date: date
    fund_id: Optional[UUID] = None
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    financial_year: Optional[str] = None
    description: Optional[str] = None
    source: str = "historical_import"
    derivation_level: str = "aggregate_balancing"
    reconstruction_batch_id: Optional[UUID] = None
    bank_transaction_id: Optional[UUID] = None
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    posted_by: Optional[UUID] = None
    approved_by: Optional[UUID] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class RecordHistoricalExpenseCommand:
    """Historical-reconstruction-only variant of RecordExpenseCommand.

    Structurally separate (not an optional field on the public command) so
    the privileged closed-period pathway has no HTTP-reachable entry point —
    adapter.py's _record_expense_postgres() constructs RecordExpenseCommand
    only, never this type. Only FinancialCoreService.record_historical_expense()
    accepts it, and only that method may resolve a `closed` accounting period.
    """
    scheme_ref: SchemeRef
    category_name: str
    amount_cents: int  # ex-GST
    gst_cents: int
    transaction_date: date
    authorisation: HistoricalPostingAuthorisation
    fund_id: Optional[UUID] = None
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    financial_year: Optional[str] = None
    description: Optional[str] = None
    derivation_level: str = "exact"
    bank_transaction_id: Optional[UUID] = None
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.amount_cents <= 0:
            raise ValueError(f"Expense amount must be positive, got {self.amount_cents}")
        if self.gst_cents < 0:
            raise ValueError(f"GST amount cannot be negative, got {self.gst_cents}")


@dataclass
class ChargeLotFeeCommand:
    """Command to on-charge a single lot for a recovery cost or ad-hoc fee.

    Recharges the OC's cost of a specific, single-lot event (an arrears/legal
    notice, a rental certificate, a locksmith call-out) back onto that lot's
    account — the AR-increase side of the on-charge. The matching cash-outflow
    side (what the OC actually paid, if anything) is a separate, ordinary
    RecordExpenseCommand — this command never touches the bank/expense side,
    it only ever debits AR for one lot and credits recovery/fee income.

    ``amount_cents`` is signed: positive charges the lot, negative reverses an
    earlier charge (e.g. a tribunal-ordered credit) — the journal lines flip
    direction automatically so the entry always balances and reads correctly
    either way. Zero is rejected; there is no such thing as a $0 charge.

    ``charge_type`` selects both the `finance.levy_items` field the amount is
    recorded against and the `finance.levy_runs.levy_run_type` value:
      "recovery"   → `levy_items.recovery_costs_cents` — a cost of collecting
                     from a lot in arrears (SMS/notice fees, debt collection).
      "adjustment" → `levy_items.principal_cents` — a fee unrelated to arrears
                     (rental certificate, access-hardware replacement); not a
                     "recovery cost" in substance, so recorded honestly as a
                     plain one-off charge instead.
    Only these two values are supported — `levy_runs_type_chk` (migration
    0069) also allows "ordinary"/"special"/"interest", which this command has
    no use for and does not accept.
    """
    scheme_ref: SchemeRef
    lot_id: UUID
    owner_party_id: UUID
    fund_id: UUID
    charge_type: str  # "recovery" | "adjustment"
    amount_cents: int  # signed; ex-GST
    gst_cents: int
    transaction_date: date
    description: str
    financial_year: Optional[str] = None
    bank_transaction_id: Optional[UUID] = None
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.charge_type not in {"recovery", "adjustment"}:
            raise ValueError(f"Invalid charge_type: {self.charge_type!r}")
        if self.amount_cents == 0:
            raise ValueError("ChargeLotFeeCommand.amount_cents must not be zero")
        if self.gst_cents < 0:
            raise ValueError(f"GST amount cannot be negative, got {self.gst_cents}")


@dataclass
class ChargeHistoricalLotFeeCommand:
    """Historical-reconstruction-only variant of ChargeLotFeeCommand.

    Structurally separate for the same reason as RecordHistoricalExpenseCommand
    — no HTTP-reachable code constructs this type. Only
    FinancialCoreService.charge_historical_lot_fee() accepts it, and only that
    method may resolve a `closed` accounting period.
    """
    scheme_ref: SchemeRef
    lot_id: UUID
    owner_party_id: UUID
    fund_id: UUID
    charge_type: str  # "recovery" | "adjustment"
    amount_cents: int  # signed; ex-GST
    gst_cents: int
    transaction_date: date
    description: str
    authorisation: HistoricalPostingAuthorisation
    financial_year: Optional[str] = None
    bank_transaction_id: Optional[UUID] = None
    idempotency_key: Optional[str] = None
    is_test_data: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.charge_type not in {"recovery", "adjustment"}:
            raise ValueError(f"Invalid charge_type: {self.charge_type!r}")
        if self.amount_cents == 0:
            raise ValueError("ChargeHistoricalLotFeeCommand.amount_cents must not be zero")
        if self.gst_cents < 0:
            raise ValueError(f"GST amount cannot be negative, got {self.gst_cents}")


@dataclass
class HistoricalLevyItemSpec:
    """Bulk-backfill variant of LotLevySpec.

    Carries a caller-supplied DETERMINISTIC levy_item_id (UUIDv5 on the natural
    key year/lot/fund, not amount) so re-running the same regeneration plan
    UPDATEs the same row instead of inserting a duplicate — see
    bulk_upsert_historical_levy_items(). levy_run_id is also caller-supplied
    for the same reason (one run per scheme/year, deterministic).
    """
    levy_item_id: UUID
    levy_run_id: UUID
    lot_id: UUID
    owner_party_id: UUID
    fund_id: UUID
    principal_cents: int
    gst_cents: int = 0


@dataclass
class BulkUpsertHistoricalLevyItemsCommand:
    """Historical-backfill-only. Never used by the live create_levy() path.

    Only ever UPDATEs principal_cents/gst_cents (or INSERTs if the deterministic
    id doesn't exist yet). Never touches paid_cents, receipt_allocations,
    status history, or levy_item_id — see plan02 amendment 5.
    """
    scheme_ref: SchemeRef
    items: list[HistoricalLevyItemSpec]
    is_test_data: bool = False
    confirm: bool = False


@dataclass
class RebuildProvisionalLevyJournalsCommand:
    """Rebuilds ONLY journal entries classified draft/posted_provisional for the
    given source_types, scoped to a scheme. Refuses if any in-scope entry
    classifies as posted_canonical/referenced_downstream — use
    ReverseAndReplacePostedLevyJournalsCommand for those instead."""
    scheme_ref: SchemeRef
    source_types: list[str]
    confirm: bool = False


@dataclass
class ReverseAndReplacePostedLevyJournalsCommand:
    """Corrects posted_canonical/referenced_downstream journal entries via the
    existing reverse_entry() pattern (mirror DR/CR, post a new entry) followed
    by a freshly-posted correct entry from current finance.levy_items — never
    deletes or edits a posted entry in place."""
    scheme_ref: SchemeRef
    source_types: list[str]
    reason: str
    confirm: bool = False
