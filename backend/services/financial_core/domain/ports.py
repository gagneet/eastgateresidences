# @featuretrace:financial_core — Domain ports (Protocols) for the financial core.
# Layer: domain
# Data flow: services/financial_core/service.py -> LedgerRepository Protocol ->
#            services/financial_core/adapters/db_postgres/ledger_repo.py (building-scoped).
# Related: backend/services/financial_core/service.py
#          backend/services/financial_core/domain/entities.py
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py
"""Domain ports (Protocols) for the financial core.

These are the abstract interfaces between the domain and the infrastructure.
SQLAlchemy models implement LedgerRepository.
MongoDB read adapter implements LegacyReadPort.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable
from uuid import UUID
from datetime import date

from services.financial_core.domain.entities import (
    AccountingPeriod,
    AllocationToReverse,
    ArrearsReport,
    Expense,
    HistoricalLevyItemSpec,
    JournalClassificationEntry,
    JournalEntry,
    Levy,
    LevyItemAdjustment,
    PaymentAllocation,
    Receipt,
    ReconcileResult,
    ReversedAllocationItem,
    SchemeRef,
)


@runtime_checkable
class LedgerRepository(Protocol):
    """Primary port: persistence of financial domain objects to Postgres."""

    async def save_journal_entry(self, entry: JournalEntry) -> JournalEntry:
        """Persist a new journal entry and its lines, return with assigned IDs."""
        ...

    async def post_journal_entry(self, entry_id: UUID, scheme_ref: SchemeRef) -> JournalEntry:
        """
        Validate balance (calls finance.assert_journal_balanced) then set status=posted.
        Must be called inside the same transaction as save_journal_entry.
        """
        ...

    async def get_journal_entry(self, entry_id: UUID, scheme_ref: SchemeRef) -> JournalEntry:
        """Fetch a journal entry and its lines by ID."""
        ...

    async def save_levy_run(
            self,
            scheme_ref: SchemeRef,
            financial_year: str,
            quarter_no: Optional[int],
            issue_date: date,
            due_date: date,
            levy_run_type: str = "ordinary",
    ) -> UUID:
        """Create a levy run and return its run_id."""
        ...

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
        """Historical-reconstruction-only levy_run lookup-or-create, scoped by
        the FULL natural key including fund_id — finance.levy_runs has no
        fund column for the live create_levy() path (a single run is
        enforced single-fund only at the application layer there), but
        create_historical_levy() always populates one (migration 0076) so two
        calls for the same period but different funds are never conflated.

        Returns (levy_run_id, created). The real concurrency guarantee is the
        DB-level partial unique index `levy_runs_natural_key_idx` (WHERE
        fund_id IS NOT NULL) — implementations should attempt the insert
        inside a SAVEPOINT and fall back to a fetch on a unique-violation,
        not rely on the initial SELECT alone (a bare lookup-then-insert is a
        real TOCTOU race under concurrent callers).
        """
        ...

    async def save_levy_items(self, items: list[Levy]) -> list[Levy]:
        """Bulk-insert levy items for a run."""
        ...

    async def get_levy_items_for_run(
            self, scheme_ref: SchemeRef, levy_run_id: UUID,
    ) -> list[Levy]:
        """Return every levy_item already posted under this run — used by
        create_historical_levy()'s idempotent-replay path (find_levy_run_by_
        natural_key found an existing run) and by the unit_levy_ledger
        projection rebuild."""
        ...

    async def save_receipt(self, receipt: Receipt) -> Receipt:
        """Persist a payment receipt."""
        ...

    async def get_receipt(self, receipt_id: UUID, scheme_ref: SchemeRef) -> Receipt:
        """Fetch a receipt by ID."""
        ...

    async def get_allocated_total_for_receipt(self, receipt_id: UUID) -> int:
        """Sum of finance.receipt_allocations.allocated_cents already posted against
        this receipt (any levy_item, any prior call). allocate_payment() subtracts this
        from the receipt's own amount_cents before filling open items, so calling it on
        a receipt that already carries a partial allocation tops up the remainder
        instead of re-allocating the full amount from zero. Returns 0 for a receipt with
        no allocations yet."""
        ...

    async def find_active_receipt_by_bank_transaction(
            self,
            scheme_ref: SchemeRef,
            bank_transaction_id: UUID,
            lot_id: Optional[UUID],
            amount_cents: int,
    ) -> Optional[Receipt]:
        """Return an existing non-reversed receipt already posted for the same
        (scheme, bank_transaction_id, lot, amount), else None — the cross-pipeline
        duplicate guard keyed on the shared bank_transaction_id (GAP-FIN-045)."""
        ...

    async def get_open_levy_items(
            self, scheme_ref: SchemeRef, lot_id: UUID
    ) -> list[Levy]:
        """Return all unpaid/partial levy items for a lot, ordered by allocation priority."""
        ...

    async def upsert_owner_credit(
            self,
            scheme_ref: "SchemeRef",
            lot_id: UUID,
            owner_party_id: UUID,
            amount_cents: int,
            source_journal_entry_id: Optional[UUID] = None,
    ) -> int:
        """Add unapplied credit for a lot, returning the new available balance.

        Called when a receipt exceeds every open levy item on its lot. Before this
        existed the surplus was simply dropped on the floor by
        ``FinancialCoreService.allocate_payment`` — the receipt and its journal entry
        were real, but `finance.levy_items` (which every per-lot balance query sums)
        had no row that could represent money paid beyond what was ever charged, so a
        genuinely-in-credit owner read as $0.00. That is GAP-FIN-036.

        Additive: `available_cents` accumulates on the natural key
        (scheme_id, lot_id, owner_party_id, fund_id), so allocating two surplus receipts
        for one owner yields one row holding the sum, not two rows.
        """
        ...

    async def save_allocations(self, allocations: list[PaymentAllocation]) -> None:
        """Persist payment allocations and update levy_items.paid_cents."""
        ...

    async def decrement_paid_for_year(
            self, scheme_ref: SchemeRef, lot_id: UUID, financial_year: str, amount_cents: int,
    ) -> list[LevyItemAdjustment]:
        """Reduce levy_items.paid_cents for a lot's financial_year by amount_cents total,
        most-recently-due item first, capped per item at that item's own current
        paid_cents (never negative). Returns exactly which items were touched and by how
        much — never a guess the caller has to re-derive. No receipt/receipt_allocations
        row is touched (this is not tied to any specific receipt)."""
        ...

    async def get_allocations_for_reversed_receipts(
            self, scheme_ref: SchemeRef, receipt_ids: Optional[list[UUID]] = None,
    ) -> list[AllocationToReverse]:
        """GAP-FIN-057 identification (SELECT-only). Return every
        finance.receipt_allocations row whose parent receipt's journal entry has a
        POSTED reversal (finance.journal_entries.reversal_of_id = receipts.journal_entry_id).
        Scope to receipt_ids when given (single-unit canary), else the whole scheme.
        See tasks/GAP-FIN-057-reversal-command-spec.md §2."""
        ...

    async def get_receipt_ids_for_journal_entry(
            self, scheme_ref: SchemeRef, journal_entry_id: UUID,
    ) -> list[UUID]:
        """GAP-FIN-057 auto-trigger support. Return the receipt_ids sourced from a given
        journal entry (finance.receipts.journal_entry_id = journal_entry_id). Empty list
        means the entry is not receipt-sourced, so reverse_entry() has no allocations to
        cascade. See spec §3 (structural fix)."""
        ...

    async def delete_allocations(self, allocation_ids: list[UUID]) -> None:
        """GAP-FIN-057. Delete the given receipt_allocations rows. allocated_cents has a
        DB CHECK(>0), so a negative offset row is impossible; the receipt + its posted
        reversal journal preserve the audit trail. Must run in the same transaction as
        recompute_paid_cents. See spec §3."""
        ...

    async def recompute_paid_cents(
            self, scheme_ref: SchemeRef, levy_item_ids: list[UUID],
    ) -> list[ReversedAllocationItem]:
        """GAP-FIN-057. Set finance.levy_items.paid_cents := SUM(surviving
        receipt_allocations.allocated_cents) for each given item, refresh status, and
        return per-item before/after. Idempotent by construction (recompute, not
        decrement). MUST run AFTER delete_allocations. See spec §3/§4."""
        ...

    async def get_overdue_levy_items(
            self, scheme_ref: SchemeRef, as_of_date: date
    ) -> list[Levy]:
        """Return all overdue levy items across all lots."""
        ...

    async def save_reconcile_result(self, result: ReconcileResult) -> None:
        """Update bank_transaction reconciliation_status and link to receipt."""
        ...

    async def get_current_accounting_period(self, scheme_ref: SchemeRef) -> UUID:
        """Return the UUID of the currently open accounting period.

        Deprecated by get_accounting_period_for_date() below — retained only
        because reverse_and_replace_posted_levy_journals() still uses it for a
        per-entry rebuild that has no single command-level date. Do not add
        new callers.
        """
        ...

    async def get_accounting_period_for_date(
            self,
            scheme_ref: SchemeRef,
            effective_on: date,
            *,
            permitted_statuses: frozenset = frozenset({"open"}),
    ) -> AccountingPeriod:
        """Resolve the single accounting period whose [starts_on, ends_on]
        range contains effective_on and whose status is in permitted_statuses.

        Raises NoAccountingPeriodForDate if zero periods (any status) cover
        the date, AccountingPeriodNotPermitted if exactly one period covers
        the date but its status is excluded, AmbiguousAccountingPeriod if more
        than one period row covers the date (data integrity violation)."""
        ...

    async def claim_bank_transaction_for_batch(
            self,
            tenant_id: UUID,
            scheme_id: UUID,
            bank_transaction_id: UUID,
            reconstruction_batch_id: UUID,
    ) -> None:
        """Atomically claim a finance.bank_transactions row for a reconstruction
        batch (sets reconstruction_batch_id). Idempotent if already claimed by
        the same batch. Raises BankTransactionClaimConflict if claimed by a
        different batch, BankTransactionNotFound if the row doesn't exist for
        this tenant/scheme. Must be called inside the same transaction as the
        journal it authorises."""
        ...

    async def user_has_financial_authority(self, scheme_ref: SchemeRef, user_id: UUID) -> bool:
        """True iff user_id is a real, active user whose EFFECTIVE ROLE FOR THIS SCHEME
        (not just "belongs to the tenant") is one with genuine financial/governance
        authority. Used to verify HistoricalPostingAuthorisation.approved_by/
        executed_by before any privileged historical posting — the dataclass's own
        __post_init__ can only check the two UUIDs are non-empty and distinct
        (synchronous, no DB access); this is the actual identity+authority check.

        AUDIT CORRECTION (2026-08-06): the first version of this check (named
        user_exists) only verified tenant_id + is_active — it never checked
        scheme_id. Tenants in this system can manage MULTIPLE schemes (e.g. a
        strata-management agency tenant), and core.users.tenant_id alone says
        nothing about whether a user has any real relationship to THIS scheme — a
        real, active user from a completely unrelated building in the same tenant
        would have passed the old check. Renamed and rebuilt around
        core.user_effective_role(user_id, scheme_id) — the same canonical,
        already-existing RBAC function this codebase uses elsewhere for scheme-scoped
        authority (temp elevation, per-scheme role assignment, then the global/
        super_admin scheme_id IS NULL tier) — rather than reinventing a weaker check.
        That function always returns SOME role (falls back to the user's base
        core.users.role, defaulting to 'guest'), so implementations must additionally
        check the returned role is in the same admin-tier set this codebase already
        uses for equivalent-sensitivity actions elsewhere (financial_matching.py's
        _DECIDE_ROLES, strata_sync's _ADMIN_ROLES): super_admin, strata_admin,
        strata_manager, ec_member — not just "the function returned non-null".
        """
        ...

    async def get_gl_account(
            self, scheme_ref: SchemeRef, account_code: str
    ) -> UUID:
        """Look up a GL account UUID by scheme + account code."""
        ...

    async def get_fund_type(
            self, scheme_ref: SchemeRef, fund_id: UUID
    ) -> str:
        """Look up a fund's fund_type ('admin', 'sinking', 'special_purpose', ...) by fund_id."""
        ...

    async def count_journal_entries_for_fund(
            self, fund_id: UUID, scheme_ref: SchemeRef
    ) -> int:
        """Count how many journal entries exist for a fund. Used to check for genesis initialization."""
        ...

    async def set_fund_opening_balance(
            self, fund_id: UUID, scheme_ref: SchemeRef, opening_balance_cents: int
    ) -> None:
        """Update finance.funds.opening_balance_cents for a fund."""
        ...

    # ── Expense-side (2026-07-17 historical-reconstruction addition) ────────

    async def save_expense(self, expense: Expense) -> Expense:
        """Persist an expense_transactions row."""
        ...

    async def find_journal_entry_id_by_idempotency_key(
            self, scheme_ref: SchemeRef, idempotency_key: str
    ) -> Optional[UUID]:
        """Return an existing journal_entry_id for this idempotency_key, if any."""
        ...

    # ── Historical levy-item regeneration (bulk-backfill only) ───────────────

    async def upsert_levy_items(
            self, scheme_ref: SchemeRef, items: list[HistoricalLevyItemSpec]
    ) -> list[Levy]:
        """UPDATE principal_cents/gst_cents in place for existing (levy_run_id,
        lot_id, fund_id) rows, or INSERT using the caller-supplied deterministic
        levy_item_id if the row doesn't exist yet. Never deletes, never touches
        paid_cents. Returns the resulting Levy rows."""
        ...

    async def classify_levy_journals(
            self, scheme_ref: SchemeRef, source_types: list[str]
    ) -> list[JournalClassificationEntry]:
        """Classify every journal_entries row of the given source_types for this
        scheme as draft / posted_provisional / posted_canonical /
        referenced_downstream — the mandatory dry-run step before any rebuild
        or reversal decision (see plan02 amendment 3)."""
        ...

    async def get_levy_item(self, scheme_ref: SchemeRef, levy_item_id: UUID) -> Optional[Levy]:
        """Fetch a single levy_item's current state (used to re-derive a
        corrected journal entry after reverse_and_replace_posted_journals)."""
        ...

    async def rebuild_provisional_journals(
            self, scheme_ref: SchemeRef, source_types: list[str]
    ) -> int:
        """Scoped wipe+rebuild of ONLY draft/posted_provisional journal_entries
        (and their lines) for the given source_types, re-derived from current
        finance.levy_items + finance.receipts, hash-chain preserved. Caller
        (the service) must have already confirmed via classify_levy_journals
        that no in-scope entry is posted_canonical/referenced_downstream.
        Returns the count of entries rebuilt."""
        ...


@runtime_checkable
class OutboxPort(Protocol):
    """Secondary port: write to the transactional outbox within the same session."""

    async def publish(
            self,
            scheme_ref: SchemeRef,
            event_type: str,
            payload: dict,
    ) -> None:
        """Insert an outbox row in the current transaction."""
        ...


@runtime_checkable
class LegacyReadPort(Protocol):
    """Secondary port: read-only access to MongoDB for shadow comparison."""

    async def get_lot_balance_cents(
            self, building_id: str, lot_id: str, as_of_date: date
    ) -> int:
        """Return the computed lot balance from MongoDB (for shadow validation)."""
        ...

    async def get_trust_account_balance_cents(
            self, building_id: str, trust_account_id: str
    ) -> int:
        """Return trust account balance from MongoDB."""
        ...
