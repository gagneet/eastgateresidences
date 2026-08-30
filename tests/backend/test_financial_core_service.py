"""Unit tests for FinancialCoreService (Phase 2).

Tests cover all 6 public methods:
- create_levy
- record_payment
- allocate_payment
- generate_arrears
- reverse_entry
- reconcile_bank_transaction

Uses in-memory mock implementations of LedgerRepository and OutboxPort.
No database connection required.
"""
from __future__ import annotations

import pytest
import uuid
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from services.financial_core.domain.entities import (
    AccountingPeriod,
    AllocationToReverse,
    BulkUpsertHistoricalLevyItemsCommand,
    ChargeHistoricalLotFeeCommand,
    ChargeLotFeeCommand,
    CreateHistoricalLevyCommand,
    CreateLevyCommand,
    EntryDirection,
    Expense,
    HistoricalLevyItemSpec,
    HistoricalPostingAuthorisation,
    JournalClassificationEntry,
    JournalEntry,
    JournalLine,
    Levy,
    LotLevySpec,
    AllocatePaymentCommand,
    GenerateArrearsCommand,
    PaymentAllocation,
    PaymentChannel,
    PostGenesisJournalCommand,
    RebuildProvisionalLevyJournalsCommand,
    RecordAdjustmentCommand,
    RecordExpenseCommand,
    RecordHistoricalExpenseCommand,
    ReconcileBankTransactionCommand,
    Receipt,
    ReconcileResult,
    ReconciliationStatus,
    ReversedAllocationItem,
    RecordPaymentCommand,
    ReverseAndReplacePostedLevyJournalsCommand,
    ReverseEntryCommand,
    SchemeRef,
    Money,
    cents,
)
from services.financial_core.domain.exceptions import (
    AccountingPeriodNotPermitted,
    AmbiguousAccountingPeriod,
    BankTransactionClaimConflict,
    BankTransactionNotFound,
    NoAccountingPeriodForDate,
)
from services.financial_core.service import FinancialCoreService

# ------------------------------------------------------------------ fixtures

TENANT_ID = uuid.UUID("11111111-0000-0000-0000-000000000001")
SCHEME_ID = uuid.UUID("22222222-0000-0000-0000-000000000002")
LOT_ID = uuid.UUID("33333333-0000-0000-0000-000000000003")
OWNER_ID = uuid.UUID("44444444-0000-0000-0000-000000000004")
FUND_ID = uuid.UUID("55555555-0000-0000-0000-000000000005")
PERIOD_ID = uuid.UUID("66666666-0000-0000-0000-000000000006")
GL_AR = uuid.UUID("77777777-0000-0000-0000-000000000007")
GL_INCOME = uuid.UUID("88888888-0000-0000-0000-000000000008")
GL_BANK = uuid.UUID("99999999-0000-0000-0000-000000000009")
GL_EXPENSE = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
GL_RECOVERED_FEES = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")
GL_SINKING_INCOME = uuid.UUID("cccccccc-0000-0000-0000-00000000000c")
GL_SINKING_EXPENSE = uuid.UUID("dddddddd-0000-0000-0000-000000000010")
PERIOD_ID_2025 = uuid.UUID("cccccccc-0000-0000-0000-00000000000c")
BATCH_ID = uuid.UUID("dddddddd-0000-0000-0000-00000000000d")
APPROVER_ID = uuid.UUID("eeeeeeee-0000-0000-0000-00000000000e")
EXECUTOR_ID = uuid.UUID("ffffffff-0000-0000-0000-00000000000f")

SCHEME_REF = SchemeRef(tenant_id=TENANT_ID, scheme_id=SCHEME_ID)


def make_authorisation(**overrides) -> HistoricalPostingAuthorisation:
    defaults = dict(
        reconstruction_batch_id=BATCH_ID, reason="test reconstruction", approved_by=APPROVER_ID,
        executed_by=EXECUTOR_ID, approval_reference="test-ref",
    )
    defaults.update(overrides)
    return HistoricalPostingAuthorisation(**defaults)


class MockLedgerRepository:
    """In-memory stub for LedgerRepository — no DB required."""

    def __init__(self):
        self._entries: dict[UUID, JournalEntry] = {}
        self._runs: list[UUID] = []
        # natural key (scheme_id, financial_year, quarter_no, levy_run_type, fund_id) -> run_id,
        # mirrors find_or_create_levy_run()'s real Postgres lookup (levy_runs_natural_key_idx).
        self._runs_by_key: dict[tuple, UUID] = {}
        # Set by a test to exercise find_or_create_levy_run()'s concurrent-insert-loses path.
        self._force_levy_run_conflict_once = False
        self._items: list[Levy] = []
        self._receipts: dict[UUID, Receipt] = {}
        self._allocations: list[PaymentAllocation] = []
        self._owner_credits: dict[tuple, int] = {}
        self._owner_credit_calls: list[dict] = []
        self._reconcile_calls: list[ReconcileResult] = []
        self._expenses: dict[UUID, Expense] = {}
        # Test-controllable overrides for classify_levy_journals — set
        # ledger._forced_classifications[entry_id] = "posted_provisional" etc.
        # Defaults to "posted_canonical" for any entry not explicitly forced,
        # matching the real repo's conservative default.
        self._forced_classifications: dict[UUID, str] = {}
        self._last_levy_run_type: Optional[str] = None
        # Date-aware period resolution — seeded with one default OPEN period
        # covering 1900-2100 so every pre-existing test (none of which cares
        # about period resolution) keeps passing unmodified. Tests that DO
        # care push additional periods via add_period().
        self._periods: list[AccountingPeriod] = [
            AccountingPeriod(
                period_id=PERIOD_ID, period_label="open-all-time",
                starts_on=date(1900, 1, 1), ends_on=date(2100, 12, 31), status="open",
            )
        ]
        # bank_transaction_id -> reconstruction_batch_id claimed by.
        self._bank_tx_claims: dict = {}
        # bank_transaction_ids that should be treated as not existing at all
        # (tests opt in explicitly to exercise BankTransactionNotFound).
        self._missing_bank_tx_ids: set = set()
        # user_ids that should be treated as NOT real/active users (tests opt in
        # explicitly to exercise the _verify_authorisation_identities rejection path).
        # Defaults to "every user_id is real" so every pre-existing test's
        # make_authorisation() (APPROVER_ID/EXECUTOR_ID) keeps passing unmodified.
        self._missing_user_ids: set = set()
        # fund_id -> fund_type ('admin'/'sinking'/...), for get_fund_type().
        # Defaults to "admin" for any fund_id not explicitly registered here
        # (matches every existing test's implicit assumption pre-dating
        # create_levy()'s fund-aware GL account resolution).
        self._fund_types: dict[UUID, str] = {}

    def add_period(self, period_id: UUID, label: str, starts_on: date, ends_on: date, status: str) -> None:
        """Test helper (not part of the LedgerRepository port) — push an
        additional period so a test can prove date-based resolution actually
        distinguishes it from the default 1900-2100 open period."""
        self._periods.append(
            AccountingPeriod(period_id=period_id, period_label=label, starts_on=starts_on, ends_on=ends_on, status=status)
        )

    async def save_journal_entry(self, entry: JournalEntry) -> JournalEntry:
        eid = entry.journal_entry_id or uuid.uuid4()
        entry.journal_entry_id = eid
        self._entries[eid] = entry
        return entry

    async def post_journal_entry(self, entry_id: UUID, scheme_ref: SchemeRef) -> JournalEntry:
        entry = self._entries[entry_id]
        return entry

    async def get_journal_entry(self, entry_id: UUID, scheme_ref: SchemeRef) -> JournalEntry:
        return self._entries[entry_id]

    async def save_levy_run(
        self, scheme_ref, financial_year, quarter_no, issue_date, due_date, levy_run_type: str = "ordinary",
    ) -> UUID:
        rid = uuid.uuid4()
        self._runs.append(rid)
        self._last_levy_run_type = levy_run_type
        return rid

    async def find_or_create_levy_run(
        self, scheme_ref, financial_year, quarter_no, levy_run_type: str, fund_id: UUID,
        issue_date, due_date,
    ) -> tuple[UUID, bool]:
        key = (scheme_ref.scheme_id, financial_year, quarter_no, levy_run_type, fund_id)
        existing = self._runs_by_key.get(key)
        if existing:
            return existing, False
        if self._force_levy_run_conflict_once:
            # Simulate a concurrent insert winning the race: someone else's row
            # appears under this key between our lookup and our insert attempt.
            self._force_levy_run_conflict_once = False
            rid = uuid.uuid4()
            self._runs.append(rid)
            self._runs_by_key[key] = rid
            return rid, False
        rid = uuid.uuid4()
        self._runs.append(rid)
        self._last_levy_run_type = levy_run_type
        self._runs_by_key[key] = rid
        return rid, True

    async def save_levy_items(self, items: list[Levy]) -> list[Levy]:
        self._items.extend(items)
        return items

    async def get_levy_items_for_run(self, scheme_ref, levy_run_id) -> list[Levy]:
        return [i for i in self._items if i.levy_run_id == levy_run_id]

    async def save_receipt(self, receipt: Receipt) -> Receipt:
        self._receipts[receipt.receipt_id] = receipt
        return receipt

    async def get_receipt(self, receipt_id: UUID, scheme_ref: SchemeRef) -> Receipt:
        return self._receipts[receipt_id]

    async def get_allocated_total_for_receipt(self, receipt_id: UUID) -> int:
        return sum(a.allocated_cents for a in self._allocations if a.receipt_id == receipt_id)

    async def find_active_receipt_by_bank_transaction(
        self, scheme_ref, bank_transaction_id, lot_id, amount_cents,
    ) -> Optional[Receipt]:
        # Mirrors the real repo query: a receipt is "active" only if its journal
        # entry has not been reversed (reverse_entry adds a reversal_of_id-linked
        # mirror rather than mutating the original) and is not itself a reversal.
        reversed_entry_ids = {
            e.reversal_of_id for e in self._entries.values()
            if getattr(e, "reversal_of_id", None) is not None
        }
        for r in self._receipts.values():
            if (
                r.bank_transaction_id == bank_transaction_id
                and r.amount_cents == amount_cents
                and (lot_id is None or r.lot_id == lot_id)
                and r.journal_entry_id not in reversed_entry_ids
            ):
                entry = self._entries.get(r.journal_entry_id)
                if entry is not None and getattr(entry, "reversal_of_id", None) is not None:
                    continue
                return r
        return None

    async def get_open_levy_items(self, scheme_ref: SchemeRef, lot_id: UUID) -> list[Levy]:
        return [i for i in self._items if i.lot_id == lot_id and i.status != "paid"]

    async def upsert_owner_credit(
            self, scheme_ref, lot_id, owner_party_id, amount_cents,
            source_journal_entry_id=None,
    ) -> int:
        """Accumulate on the natural key, exactly as the Postgres upsert does — so a
        test cannot pass here and fail against the real adapter."""
        key = (lot_id, owner_party_id)
        self._owner_credits[key] = self._owner_credits.get(key, 0) + amount_cents
        self._owner_credit_calls.append({
            "lot_id": lot_id, "owner_party_id": owner_party_id,
            "amount_cents": amount_cents,
            "source_journal_entry_id": source_journal_entry_id,
        })
        return self._owner_credits[key]

    async def save_allocations(self, allocations: list[PaymentAllocation]) -> None:
        self._allocations.extend(allocations)
        # Mirror the real adapter (ledger_repo.save_allocations): paid_cents is an
        # add-only running counter bumped as each allocation lands. Modelling this here
        # lets reversal/recompute tests assert the paid_cents transition realistically.
        items_by_id = {i.levy_item_id: i for i in self._items}
        for alloc in allocations:
            if alloc.levy_item_id is not None and alloc.levy_item_id in items_by_id:
                items_by_id[alloc.levy_item_id].paid_cents += alloc.allocated_cents

    # ── GAP-FIN-057 receipt-allocation reversal (in-memory mirrors) ──────────

    async def get_receipt_ids_for_journal_entry(
            self, scheme_ref: SchemeRef, journal_entry_id: UUID,
    ) -> list[UUID]:
        return [
            r.receipt_id for r in self._receipts.values()
            if r.journal_entry_id == journal_entry_id
        ]

    async def get_allocations_for_reversed_receipts(
            self, scheme_ref: SchemeRef, receipt_ids=None,
    ) -> list[AllocationToReverse]:
        # A receipt's journal is "reversed" when some entry's reversal_of_id points at it
        # (mirrors the real EXISTS(... rev.reversal_of_id = orig ... posted) join).
        reversed_entry_ids = {
            e.reversal_of_id for e in self._entries.values()
            if getattr(e, "reversal_of_id", None) is not None
        }
        scope = set(receipt_ids) if receipt_ids is not None else None
        items_by_id = {i.levy_item_id: i for i in self._items}
        out: list[AllocationToReverse] = []
        for a in self._allocations:
            if a.levy_item_id is None:
                continue
            if scope is not None and a.receipt_id not in scope:
                continue
            receipt = self._receipts.get(a.receipt_id)
            if receipt is None or receipt.journal_entry_id not in reversed_entry_ids:
                continue
            li = items_by_id.get(a.levy_item_id)
            out.append(AllocationToReverse(
                allocation_id=a.allocation_id,
                receipt_id=a.receipt_id,
                levy_item_id=a.levy_item_id,
                allocated_cents=a.allocated_cents,
                fund_id=li.fund_id if li else uuid.uuid4(),
                lot_id=li.lot_id if li else uuid.uuid4(),
            ))
        out.sort(key=lambda x: (str(x.receipt_id), str(x.allocation_id)))
        return out

    async def delete_allocations(self, allocation_ids: list[UUID]) -> None:
        remove = set(allocation_ids)
        self._allocations = [a for a in self._allocations if a.allocation_id not in remove]

    async def recompute_paid_cents(
            self, scheme_ref: SchemeRef, levy_item_ids: list[UUID],
    ) -> list[ReversedAllocationItem]:
        results: list[ReversedAllocationItem] = []
        for item in self._items:
            if item.levy_item_id not in levy_item_ids:
                continue
            before = item.paid_cents
            surviving = sum(
                a.allocated_cents for a in self._allocations
                if a.levy_item_id == item.levy_item_id
            )
            charge = (
                item.principal_cents + item.gst_cents
                + item.interest_cents + item.recovery_costs_cents
            )
            item.paid_cents = surviving
            item.status = (
                "issued" if surviving <= 0
                else "paid" if charge > 0 and surviving >= charge
                else "partial"
            )
            results.append(ReversedAllocationItem(
                levy_item_id=item.levy_item_id, fund_id=item.fund_id, lot_id=item.lot_id,
                paid_cents_before=before, paid_cents_after=surviving,
            ))
        return results

    async def decrement_paid_for_year(
            self, scheme_ref: SchemeRef, lot_id: UUID, financial_year: str, amount_cents: int,
    ) -> list:
        """Mirrors the real repo's two-pass plan-then-apply: validate full sufficiency
        across all of the lot's financial_year items (most-recently-due first) before
        mutating anything, so a shortfall never leaves a partial write behind."""
        from services.financial_core.domain.entities import LevyItemAdjustment
        # No separate "levy run" object exists in this mock (only _runs_by_key's natural
        # key, keyed (scheme_id, financial_year, quarter_no, levy_run_type, fund_id) ->
        # run_id) — reverse it to find each item's financial_year via its levy_run_id,
        # rather than adding parallel state that could drift from _runs_by_key.
        run_id_to_year = {rid: key[1] for key, rid in self._runs_by_key.items()}
        candidates = [
            i for i in self._items
            if i.lot_id == lot_id and run_id_to_year.get(i.levy_run_id) == str(financial_year)
        ]
        candidates.sort(key=lambda i: (i.due_date or date.min), reverse=True)

        remaining = amount_cents
        plan = []
        for item in candidates:
            if remaining <= 0:
                break
            take = min(remaining, item.paid_cents)
            if take <= 0:
                continue
            plan.append((item, take))
            remaining -= take

        if remaining > 0:
            raise ValueError(
                f"decrement_paid_for_year: lot {lot_id} financial_year {financial_year} — "
                f"requested {amount_cents} cents but only {amount_cents - remaining} cents "
                f"available across {len(candidates)} levy_item(s)."
            )

        touched = []
        for item, take in plan:
            item.paid_cents -= take
            touched.append(LevyItemAdjustment(
                levy_item_id=item.levy_item_id, decremented_cents=take, paid_cents_after=item.paid_cents,
            ))
        return touched

    async def get_overdue_levy_items(self, scheme_ref, as_of_date) -> list[Levy]:
        return [i for i in self._items if i.status != "paid"]

    async def save_reconcile_result(self, result: ReconcileResult) -> None:
        self._reconcile_calls.append(result)

    async def count_journal_entries_for_fund(self, fund_id: UUID, scheme_ref: SchemeRef) -> int:
        return sum(1 for e in self._entries.values() if e.fund_id == fund_id)

    async def set_fund_opening_balance(self, fund_id: UUID, scheme_ref: SchemeRef, opening_balance_cents: int) -> None:
        pass

    async def get_current_accounting_period(self, scheme_ref: SchemeRef) -> UUID:
        return PERIOD_ID

    async def get_accounting_period_for_date(
            self, scheme_ref: SchemeRef, effective_on: date, *,
            permitted_statuses: frozenset = frozenset({"open"}),
    ) -> AccountingPeriod:
        matches = [p for p in self._periods if p.starts_on <= effective_on <= p.ends_on]
        if not matches:
            raise NoAccountingPeriodForDate(scheme_ref.scheme_id, effective_on)
        if len(matches) > 1:
            raise AmbiguousAccountingPeriod(scheme_ref.scheme_id, effective_on, [p.period_id for p in matches])
        period = matches[0]
        if period.status not in permitted_statuses:
            raise AccountingPeriodNotPermitted(
                scheme_ref.scheme_id, effective_on, period.period_id, period.status, frozenset(permitted_statuses)
            )
        return period

    async def claim_bank_transaction_for_batch(
            self, tenant_id: UUID, scheme_id: UUID, bank_transaction_id: UUID, reconstruction_batch_id: UUID,
    ) -> None:
        if bank_transaction_id in self._missing_bank_tx_ids:
            raise BankTransactionNotFound(bank_transaction_id, tenant_id, scheme_id)
        existing = self._bank_tx_claims.get(bank_transaction_id)
        if existing is not None and existing != reconstruction_batch_id:
            raise BankTransactionClaimConflict(bank_transaction_id, existing, reconstruction_batch_id)
        self._bank_tx_claims[bank_transaction_id] = reconstruction_batch_id

    async def user_has_financial_authority(self, scheme_ref: SchemeRef, user_id: UUID) -> bool:
        """Mock only models the pass/fail outcome via the opt-in denylist, not the
        real repo's scheme-role-resolution mechanics — that logic is Postgres-native
        (core.user_effective_role) and has no meaningful in-memory equivalent worth
        re-implementing here; the real query is verified separately against live data."""
        return user_id not in self._missing_user_ids

    async def get_gl_account(self, scheme_ref: SchemeRef, account_code: str) -> UUID:
        mapping = {
            "1010": GL_BANK, "1100": GL_AR, "4000": GL_INCOME, "5000": GL_EXPENSE,
            "4003": GL_RECOVERED_FEES, "4001": GL_SINKING_INCOME,
            "5100": GL_SINKING_EXPENSE,
        }
        return mapping.get(account_code, uuid.uuid4())

    async def get_fund_type(self, scheme_ref: SchemeRef, fund_id: UUID) -> str:
        return self._fund_types.get(fund_id, "admin")

    # ── Expense-side ──────────────────────────────────────────────────────

    async def save_expense(self, expense: Expense) -> Expense:
        self._expenses[expense.expense_id] = expense
        return expense

    async def find_journal_entry_id_by_idempotency_key(self, scheme_ref: SchemeRef, idempotency_key: str):
        for eid, entry in self._entries.items():
            if entry.idempotency_key == idempotency_key:
                return eid
        return None

    # ── Historical levy-item regeneration ────────────────────────────────

    async def upsert_levy_items(self, scheme_ref: SchemeRef, items: list) -> list[Levy]:
        results = []
        for spec in items:
            existing = next((i for i in self._items if i.levy_item_id == spec.levy_item_id), None)
            if existing:
                existing.principal_cents = spec.principal_cents
                existing.gst_cents = spec.gst_cents
                results.append(existing)
            else:
                new_item = Levy(
                    levy_item_id=spec.levy_item_id, scheme_ref=scheme_ref, levy_run_id=spec.levy_run_id,
                    lot_id=spec.lot_id, owner_party_id=spec.owner_party_id, fund_id=spec.fund_id,
                    principal_cents=spec.principal_cents, gst_cents=spec.gst_cents,
                )
                self._items.append(new_item)
                results.append(new_item)
        return results

    async def get_levy_item(self, scheme_ref: SchemeRef, levy_item_id: UUID):
        return next((i for i in self._items if i.levy_item_id == levy_item_id), None)

    async def classify_levy_journals(self, scheme_ref: SchemeRef, source_types: list[str]):
        result = []
        for eid, entry in self._entries.items():
            if entry.source_type not in source_types:
                continue
            classification = self._forced_classifications.get(eid, "posted_canonical")
            result.append(JournalClassificationEntry(
                journal_entry_id=eid, source_type=entry.source_type, classification=classification,
            ))
        return result

    async def rebuild_provisional_journals(self, scheme_ref: SchemeRef, source_types: list[str]) -> int:
        classifications = await self.classify_levy_journals(scheme_ref, source_types)
        unsafe = [c for c in classifications if c.classification in ("posted_canonical", "referenced_downstream")]
        if unsafe:
            raise ValueError(
                f"Refusing scoped rebuild: {len(unsafe)} journal entries classify as "
                f"posted_canonical/referenced_downstream (first offender: {unsafe[0].journal_entry_id})."
            )
        rebuildable = [c.journal_entry_id for c in classifications if c.classification in ("draft", "posted_provisional")]
        for eid in rebuildable:
            del self._entries[eid]
        return len(rebuildable)


class MockOutboxPort:
    """Captures outbox publishes for assertion."""

    def __init__(self):
        self.events: list[dict] = []

    async def publish(self, scheme_ref, event_type: str, payload: dict) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


@pytest.fixture
def ledger():
    return MockLedgerRepository()


@pytest.fixture
def outbox():
    return MockOutboxPort()


@pytest.fixture
def svc(ledger, outbox):
    return FinancialCoreService(ledger, outbox)


def make_levy_cmd(amount_cents: int = 100000) -> CreateLevyCommand:
    return CreateLevyCommand(
        scheme_ref=SCHEME_REF,
        financial_year="2026",
        quarter_no=1,
        issue_date=date(2026, 1, 1),
        due_date=date(2026, 1, 31),
        lot_levies=[LotLevySpec(
            lot_id=LOT_ID,
            owner_party_id=OWNER_ID,
            fund_id=FUND_ID,
            principal_cents=amount_cents,
        )],
    )


def make_payment_cmd(amount_cents: int = 100000) -> RecordPaymentCommand:
    return RecordPaymentCommand(
        scheme_ref=SCHEME_REF,
        payer_party_id=OWNER_ID,
        lot_id=LOT_ID,
        channel=PaymentChannel.EFT,
        received_on=date(2026, 2, 1),
        amount_cents=amount_cents,
        idempotency_key=f"test-payment-{amount_cents}",
        posted_by=OWNER_ID,
        approved_by=OWNER_ID,
    )


# ------------------------------------------------------------------ unit tests

class TestCreateLevy:
    @pytest.mark.asyncio
    async def test_create_levy_basic(self, svc, ledger, outbox):
        cmd = make_levy_cmd(amount_cents=250000)
        items = await svc.create_levy(cmd)

        assert len(items) == 1
        assert items[0].principal_cents == 250000
        assert items[0].lot_id == LOT_ID
        assert items[0].fund_id == FUND_ID

    @pytest.mark.asyncio
    async def test_create_levy_posts_journal(self, svc, ledger, outbox):
        cmd = make_levy_cmd(amount_cents=100000)
        await svc.create_levy(cmd)

        assert len(ledger._entries) == 1
        entry = list(ledger._entries.values())[0]
        assert entry.source_type == "levy_run"
        assert entry.debit_total_cents == entry.credit_total_cents == 100000

    @pytest.mark.asyncio
    async def test_create_levy_emits_outbox_event(self, svc, ledger, outbox):
        cmd = make_levy_cmd(amount_cents=100000)
        await svc.create_levy(cmd)

        assert len(outbox.events) >= 1
        event_types = [e["event_type"] for e in outbox.events]
        assert "levy.run.created" in event_types

    @pytest.mark.asyncio
    async def test_create_levy_rejects_invalid_dates(self, svc):
        cmd = CreateLevyCommand(
            scheme_ref=SCHEME_REF,
            financial_year="2026",
            quarter_no=1,
            issue_date=date(2026, 2, 1),
            due_date=date(2026, 1, 1),  # due before issue
            lot_levies=[LotLevySpec(LOT_ID, OWNER_ID, FUND_ID, 100000)],
        )
        with pytest.raises(ValueError, match="due_date"):
            await svc.create_levy(cmd)

    @pytest.mark.asyncio
    async def test_create_levy_rejects_empty_lot_list(self, svc):
        cmd = CreateLevyCommand(
            scheme_ref=SCHEME_REF,
            financial_year="2026",
            quarter_no=1,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            lot_levies=[],
        )
        with pytest.raises(ValueError, match="at least one"):
            await svc.create_levy(cmd)

    @pytest.mark.asyncio
    async def test_multi_lot_levy_balanced_journal(self, svc, ledger, outbox):
        """Journal must be balanced when multiple lots are charged."""
        lot2 = uuid.uuid4()
        owner2 = uuid.uuid4()
        cmd = CreateLevyCommand(
            scheme_ref=SCHEME_REF,
            financial_year="2026",
            quarter_no=1,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            lot_levies=[
                LotLevySpec(LOT_ID, OWNER_ID, FUND_ID, 100000),
                LotLevySpec(lot2, owner2, FUND_ID, 200000),
            ],
        )
        await svc.create_levy(cmd)
        entry = list(ledger._entries.values())[0]
        assert entry.debit_total_cents == entry.credit_total_cents == 300000

    @pytest.mark.asyncio
    async def test_sinking_fund_levy_credits_4001_not_4000(self, svc, ledger, outbox):
        """Real bug found 2026-07-25 posting East Gate's FY2022 sinking-fund
        shortfall: create_levy() always credited GL 4000 (Admin Levy Income)
        regardless of the levy's fund, silently misposting every non-admin
        levy as admin income. Fixed to resolve the income account per the
        lot_levies' actual fund_type."""
        sinking_fund_id = uuid.uuid4()
        ledger._fund_types[sinking_fund_id] = "sinking"
        cmd = CreateLevyCommand(
            scheme_ref=SCHEME_REF,
            financial_year="2022",
            quarter_no=0,
            issue_date=date(2022, 11, 10),
            due_date=date(2022, 12, 31),
            lot_levies=[LotLevySpec(LOT_ID, OWNER_ID, sinking_fund_id, 850000)],
        )
        await svc.create_levy(cmd)
        entry = list(ledger._entries.values())[0]
        credit_accounts = {l.gl_account_id for l in entry.lines if l.direction == EntryDirection.CREDIT}
        assert credit_accounts == {GL_SINKING_INCOME}
        assert GL_INCOME not in credit_accounts

    @pytest.mark.asyncio
    async def test_create_levy_rejects_mixed_fund_lot_levies(self, svc):
        other_fund_id = uuid.uuid4()
        cmd = CreateLevyCommand(
            scheme_ref=SCHEME_REF,
            financial_year="2026",
            quarter_no=1,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            lot_levies=[
                LotLevySpec(LOT_ID, OWNER_ID, FUND_ID, 100000),
                LotLevySpec(uuid.uuid4(), uuid.uuid4(), other_fund_id, 100000),
            ],
        )
        with pytest.raises(ValueError, match="one fund_id"):
            await svc.create_levy(cmd)


class TestRecordPayment:
    @pytest.mark.asyncio
    async def test_record_payment_basic(self, svc, ledger, outbox):
        cmd = make_payment_cmd(100000)
        receipt = await svc.record_payment(cmd)

        assert receipt.amount_cents == 100000
        assert receipt.lot_id == LOT_ID
        assert receipt.channel == PaymentChannel.EFT

    @pytest.mark.asyncio
    async def test_record_payment_creates_journal(self, svc, ledger, outbox):
        cmd = make_payment_cmd(75000)
        await svc.record_payment(cmd)

        assert len(ledger._entries) == 1
        entry = list(ledger._entries.values())[0]
        assert entry.debit_total_cents == 75000
        assert entry.credit_total_cents == 75000

    @pytest.mark.asyncio
    async def test_record_payment_emits_outbox_event(self, svc, ledger, outbox):
        cmd = make_payment_cmd(50000)
        await svc.record_payment(cmd)

        event_types = [e["event_type"] for e in outbox.events]
        assert "payment.received" in event_types

    @pytest.mark.asyncio
    async def test_record_payment_rejects_zero_amount(self, svc):
        cmd = make_payment_cmd(0)
        with pytest.raises(ValueError, match="positive"):
            await svc.record_payment(cmd)

    @pytest.mark.asyncio
    async def test_record_payment_rejects_negative_amount(self, svc):
        cmd = make_payment_cmd(-500)
        with pytest.raises(ValueError, match="positive"):
            await svc.record_payment(cmd)

    @pytest.mark.asyncio
    async def test_record_payment_propagates_reconstruction_batch_id_to_receipt(self, svc, ledger):
        """Regression test (2026-07-17 audit): record_payment() built a Receipt
        without ever passing reconstruction_batch_id/metadata through from the
        command, even though both fields existed on Receipt/finance.receipts
        since Phase 2 — every receipt silently lost this provenance regardless
        of source. Caught only by this follow-up audit, not by the original
        Phase 2/3 test suite (which never asserted on these two fields)."""
        batch_id = uuid.uuid4()
        cmd = RecordPaymentCommand(
            scheme_ref=SCHEME_REF, payer_party_id=OWNER_ID, lot_id=LOT_ID,
            channel=PaymentChannel.EFT, received_on=date(2026, 2, 1), amount_cents=100000,
            idempotency_key="test-provenance-payment", posted_by=OWNER_ID, approved_by=OWNER_ID,
            reconstruction_batch_id=batch_id,
            metadata={"transaction_origin": "reconstructed_historical", "assumption_code": "quarterly_regular"},
        )
        receipt = await svc.record_payment(cmd)

        assert receipt.reconstruction_batch_id == batch_id
        assert receipt.metadata.get("transaction_origin") == "reconstructed_historical"

    @pytest.mark.asyncio
    async def test_record_payment_reconstruction_batch_id_defaults_to_none(self, svc):
        """An ordinary (non-reconstructed) payment must not silently pick up
        provenance — the default must stay None/empty, not require every
        caller to pass an explicit null."""
        cmd = make_payment_cmd(100000)
        receipt = await svc.record_payment(cmd)

        assert receipt.reconstruction_batch_id is None


class TestAllocatePayment:
    @pytest.mark.asyncio
    async def test_allocate_payment_full(self, svc, ledger, outbox):
        # Seed: issue a levy then record a matching payment
        levy_cmd = make_levy_cmd(100000)
        levy_items = await svc.create_levy(levy_cmd)

        payment_cmd = make_payment_cmd(100000)
        receipt = await svc.record_payment(payment_cmd)

        alloc_cmd = AllocatePaymentCommand(
            scheme_ref=SCHEME_REF,
            receipt_id=receipt.receipt_id,
        )
        allocations = await svc.allocate_payment(alloc_cmd)

        assert len(allocations) == 1
        assert allocations[0].allocated_cents == 100000

    @pytest.mark.asyncio
    async def test_allocate_payment_partial(self, svc, ledger, outbox):
        """When payment < outstanding levy, allocation is partial."""
        levy_cmd = make_levy_cmd(100000)
        await svc.create_levy(levy_cmd)

        payment_cmd = make_payment_cmd(60000)
        receipt = await svc.record_payment(payment_cmd)

        alloc_cmd = AllocatePaymentCommand(
            scheme_ref=SCHEME_REF,
            receipt_id=receipt.receipt_id,
        )
        allocations = await svc.allocate_payment(alloc_cmd)
        total_allocated = sum(a.allocated_cents for a in allocations)
        assert total_allocated == 60000

    @pytest.mark.asyncio
    async def test_allocate_payment_tops_up_pre_existing_allocation(self, svc, ledger, outbox):
        """A receipt that already carries a partial allocation (e.g. a stray
        cross-period allocation from historical reconstruction) must have only its
        REMAINING balance allocated -- not its full amount_cents re-allocated on top
        of what's already posted. Regression for the East Gate UA030 finding
        (2026-08-09): a 2021 receipt with $101.94 already allocated left its
        remaining $181.90 permanently invisible to the GAP-FIN-046 Bug-B repair,
        which calls this command per receipt_id."""
        levy_cmd = make_levy_cmd(100000)
        await svc.create_levy(levy_cmd)

        payment_cmd = make_payment_cmd(28384)  # UA030's real receipt amount, in cents
        receipt = await svc.record_payment(payment_cmd)

        # Simulate a pre-existing partial allocation already sitting on this receipt
        # (as if a prior process had allocated 10194 of it already).
        pre_existing = PaymentAllocation(
            allocation_id=uuid.uuid4(),
            receipt_id=receipt.receipt_id,
            levy_item_id=uuid.uuid4(),
            allocation_type="levy",
            allocated_cents=10194,
            tenant_id=SCHEME_REF.tenant_id,
        )
        await ledger.save_allocations([pre_existing])

        alloc_cmd = AllocatePaymentCommand(scheme_ref=SCHEME_REF, receipt_id=receipt.receipt_id)
        allocations = await svc.allocate_payment(alloc_cmd)
        total_new = sum(a.allocated_cents for a in allocations)

        # Only the REMAINDER (28384 - 10194 = 18190) may be newly allocated here --
        # never the full receipt amount again.
        assert total_new == 28384 - 10194
        total_ever_allocated = await ledger.get_allocated_total_for_receipt(receipt.receipt_id)
        assert total_ever_allocated == 28384  # exactly the receipt's own amount, never more

    @pytest.mark.asyncio
    async def test_allocate_emits_outbox_event(self, svc, ledger, outbox):
        levy_cmd = make_levy_cmd(100000)
        await svc.create_levy(levy_cmd)
        receipt = await svc.record_payment(make_payment_cmd(100000))
        outbox.events.clear()

        await svc.allocate_payment(AllocatePaymentCommand(SCHEME_REF, receipt.receipt_id))
        event_types = [e["event_type"] for e in outbox.events]
        assert "payment.allocated" in event_types


class TestGenerateArrears:
    @pytest.mark.asyncio
    async def test_generate_arrears_returns_report(self, svc, ledger, outbox):
        levy_cmd = make_levy_cmd(100000)
        await svc.create_levy(levy_cmd)

        cmd = GenerateArrearsCommand(scheme_ref=SCHEME_REF, as_of_date=date(2026, 3, 1))
        report = await svc.generate_arrears(cmd)

        assert report.scheme_ref == SCHEME_REF
        assert report.as_of_date == date(2026, 3, 1)
        assert len(report.items) == 1

    @pytest.mark.asyncio
    async def test_arrears_total_matches_outstanding(self, svc, ledger, outbox):
        levy_cmd = make_levy_cmd(100000)
        await svc.create_levy(levy_cmd)

        cmd = GenerateArrearsCommand(scheme_ref=SCHEME_REF, as_of_date=date(2026, 3, 1))
        report = await svc.generate_arrears(cmd)

        assert report.total_outstanding_cents == 100000


BANK_TX_ID = uuid.UUID("ba11ba11-0000-0000-0000-00000000ba11")


def make_bank_payment_cmd(amount_cents, bank_transaction_id, idempotency_key, lot_id=LOT_ID):
    """A bank-matched payment carrying a bank_transaction_id — the identity the
    cross-pipeline duplicate guard keys on (GAP-FIN-045)."""
    return RecordPaymentCommand(
        scheme_ref=SCHEME_REF,
        payer_party_id=OWNER_ID,
        lot_id=lot_id,
        channel=PaymentChannel.BANK_TRANSFER,
        received_on=date(2026, 2, 1),
        amount_cents=amount_cents,
        bank_transaction_id=bank_transaction_id,
        idempotency_key=idempotency_key,
        posted_by=OWNER_ID,
        approved_by=OWNER_ID,
    )


class TestRecordPaymentBankTransactionDedup:
    """GAP-FIN-045 — cross-pipeline duplicate guard keyed on bank_transaction_id.

    Two independent posting paths (live match-promotion + GAP-FIN-031 derived
    backfill) keyed idempotency on different strings, so each posted a receipt for
    the same real cash line; the batch-scoped 2026-08-03 reversal caught only the
    tagged twin. The core writer now suppresses the duplicate on the shared
    bank_transaction_id even when the idempotency_key differs.
    """

    @pytest.mark.asyncio
    async def test_same_bank_tx_suppresses_duplicate_despite_different_idempotency_key(self, svc, ledger, outbox):
        first = await svc.record_payment(
            make_bank_payment_cmd(100000, BANK_TX_ID, "live-promote:abc")
        )
        # Same bank line + lot + amount, DIFFERENT idempotency key (the exact
        # condition the per-pipeline idempotency checks miss).
        second = await svc.record_payment(
            make_bank_payment_cmd(100000, BANK_TX_ID, "gap-fin-031-derived:abc")
        )
        assert second.receipt_id == first.receipt_id
        assert len(ledger._receipts) == 1
        assert len(ledger._entries) == 1  # no twin journal posted

    @pytest.mark.asyncio
    async def test_same_bank_tx_different_amount_not_suppressed(self, svc, ledger, outbox):
        # A single bank line split into two fund components is not a duplicate.
        await svc.record_payment(make_bank_payment_cmd(136348, BANK_TX_ID, "k1"))
        await svc.record_payment(make_bank_payment_cmd(39802, BANK_TX_ID, "k2"))
        assert len(ledger._receipts) == 2

    @pytest.mark.asyncio
    async def test_same_bank_tx_different_lot_not_suppressed(self, svc, ledger, outbox):
        # A batch deposit covering two lots is not a duplicate.
        other_lot = uuid.uuid4()
        await svc.record_payment(make_bank_payment_cmd(50000, BANK_TX_ID, "k1", lot_id=LOT_ID))
        await svc.record_payment(make_bank_payment_cmd(50000, BANK_TX_ID, "k2", lot_id=other_lot))
        assert len(ledger._receipts) == 2

    @pytest.mark.asyncio
    async def test_no_bank_transaction_id_not_guarded(self, svc, ledger, outbox):
        # Manual receipts / reconstructions with no bank line keep their own
        # idempotency semantics — the guard only engages when bank_transaction_id
        # is present.
        await svc.record_payment(make_payment_cmd(100000))
        cmd2 = make_payment_cmd(100000)
        object.__setattr__(cmd2, "idempotency_key", "different-key-same-amount")
        await svc.record_payment(cmd2)
        assert len(ledger._receipts) == 2

    @pytest.mark.asyncio
    async def test_reversed_receipt_allows_repost(self, svc, ledger, outbox):
        # After a receipt is reversed, re-posting the same cash line is a
        # legitimate correction, NOT a duplicate — the guard must not block it.
        first = await svc.record_payment(make_bank_payment_cmd(50000, BANK_TX_ID, "orig"))
        await svc.reverse_entry(
            ReverseEntryCommand(SCHEME_REF, first.journal_entry_id, "dup fix")
        )
        second = await svc.record_payment(make_bank_payment_cmd(50000, BANK_TX_ID, "repost"))
        assert second.receipt_id != first.receipt_id
        assert len(ledger._receipts) == 2


class TestReverseEntry:
    @pytest.mark.asyncio
    async def test_reverse_entry_creates_reversal(self, svc, ledger, outbox):
        payment_cmd = make_payment_cmd(50000)
        receipt = await svc.record_payment(payment_cmd)

        original_entry_id = list(ledger._entries.keys())[0]

        reverse_cmd = ReverseEntryCommand(
            scheme_ref=SCHEME_REF,
            journal_entry_id=original_entry_id,
            reason="Test reversal",
        )
        result = await svc.reverse_entry(reverse_cmd)

        assert result.original_entry_id == original_entry_id
        assert result.reversal_entry_id != original_entry_id
        assert result.reason == "Test reversal"

    @pytest.mark.asyncio
    async def test_reversal_is_balanced(self, svc, ledger, outbox):
        await svc.record_payment(make_payment_cmd(50000))
        original_id = list(ledger._entries.keys())[0]

        result = await svc.reverse_entry(
            ReverseEntryCommand(SCHEME_REF, original_id, "refund")
        )
        reversal = ledger._entries[result.reversal_entry_id]
        assert reversal.debit_total_cents == reversal.credit_total_cents

    @pytest.mark.asyncio
    async def test_reversal_mirrors_original_lines(self, svc, ledger, outbox):
        await svc.record_payment(make_payment_cmd(50000))
        original_id = list(ledger._entries.keys())[0]
        original = ledger._entries[original_id]

        result = await svc.reverse_entry(
            ReverseEntryCommand(SCHEME_REF, original_id, "refund")
        )
        reversal = ledger._entries[result.reversal_entry_id]

        # Reversal lines should have opposite directions to original
        for orig_line, rev_line in zip(original.lines, reversal.lines):
            assert orig_line.direction != rev_line.direction
            assert orig_line.amount_cents == rev_line.amount_cents

    @pytest.mark.asyncio
    async def test_reversal_emits_outbox_event(self, svc, ledger, outbox):
        await svc.record_payment(make_payment_cmd(50000))
        original_id = list(ledger._entries.keys())[0]
        outbox.events.clear()

        await svc.reverse_entry(ReverseEntryCommand(SCHEME_REF, original_id, "refund"))
        event_types = [e["event_type"] for e in outbox.events]
        assert "journal.entry.reversed" in event_types


class TestReverseEntryAllocationCascade:
    """GAP-FIN-057 structural fix: reversing a receipt-sourced entry must also unwind
    the receipt_allocations it left behind, so paid_cents stops reading reversed cash as
    applied. Exercised end-to-end through the in-memory ledger (levy → pay → allocate →
    reverse)."""

    async def _seed_paid_levy(self, svc):
        await svc.create_levy(make_levy_cmd(100000))
        receipt = await svc.record_payment(make_payment_cmd(100000))
        await svc.allocate_payment(AllocatePaymentCommand(SCHEME_REF, receipt.receipt_id))
        return receipt

    @pytest.mark.asyncio
    async def test_reversing_receipt_cascades_allocation_unwind(self, svc, ledger, outbox):
        receipt = await self._seed_paid_levy(svc)
        # Precondition: the allocation applied 100000 to the levy item.
        item = next(i for i in ledger._items if i.lot_id == LOT_ID)
        assert item.paid_cents == 100000
        assert len(ledger._allocations) == 1
        outbox.events.clear()

        result = await svc.reverse_entry(
            ReverseEntryCommand(SCHEME_REF, receipt.journal_entry_id, "duplicate credit")
        )

        # The cascade ran: allocation deleted, paid_cents recomputed from survivors (0).
        assert result.cascade is not None
        assert result.cascade.is_noop is False
        assert len(result.cascade.deleted_allocation_ids) == 1
        assert result.cascade.reversed_allocated_cents == 100000
        assert item.paid_cents == 0
        assert item.status == "issued"
        assert ledger._allocations == []
        # Both events fire: the journal reversal AND the allocation unwind.
        event_types = [e["event_type"] for e in outbox.events]
        assert "journal.entry.reversed" in event_types
        assert "payment.allocation_reversed" in event_types

    @pytest.mark.asyncio
    async def test_cascade_is_idempotent_on_replay(self, svc, ledger, outbox):
        receipt = await self._seed_paid_levy(svc)
        await svc.reverse_entry(
            ReverseEntryCommand(SCHEME_REF, receipt.journal_entry_id, "first")
        )
        item = next(i for i in ledger._items if i.lot_id == LOT_ID)
        assert item.paid_cents == 0
        outbox.events.clear()

        # A second reverse_allocations pass (e.g. re-run) finds nothing and no-ops.
        from services.financial_core.domain.entities import ReverseAllocationsCommand
        second = await svc.reverse_allocations(
            ReverseAllocationsCommand(SCHEME_REF, reason="replay", receipt_ids=[receipt.receipt_id])
        )
        assert second.is_noop is True
        assert item.paid_cents == 0
        assert outbox.events == []

    @pytest.mark.asyncio
    async def test_cascade_disabled_leaves_allocations(self, svc, ledger, outbox):
        """cascade_allocations=False reverts to journal-only reversal (the data-repair
        driver path, which handles the unwind in its own controlled transaction)."""
        receipt = await self._seed_paid_levy(svc)
        item = next(i for i in ledger._items if i.lot_id == LOT_ID)
        outbox.events.clear()

        result = await svc.reverse_entry(
            ReverseEntryCommand(
                SCHEME_REF, receipt.journal_entry_id, "journal only",
                cascade_allocations=False,
            )
        )

        assert result.cascade is None
        assert item.paid_cents == 100000  # untouched — the historical bug's state
        assert len(ledger._allocations) == 1
        event_types = [e["event_type"] for e in outbox.events]
        assert "payment.allocation_reversed" not in event_types

    @pytest.mark.asyncio
    async def test_non_receipt_reversal_has_no_cascade(self, svc, ledger, outbox):
        """Reversing a levy_run entry (not receipt-sourced) triggers no cascade."""
        await svc.create_levy(make_levy_cmd(100000))
        levy_entry_id = list(ledger._entries.keys())[0]

        result = await svc.reverse_entry(
            ReverseEntryCommand(SCHEME_REF, levy_entry_id, "levy reversal")
        )
        assert result.cascade is None


class TestReconcileBankTransaction:
    @pytest.mark.asyncio
    async def test_reconcile_basic(self, svc, ledger, outbox):
        receipt = await svc.record_payment(make_payment_cmd(100000))
        bank_tx_id = uuid.uuid4()

        result = await svc.reconcile_bank_transaction(
            ReconcileBankTransactionCommand(
                scheme_ref=SCHEME_REF,
                bank_transaction_id=bank_tx_id,
                receipt_id=receipt.receipt_id,
            )
        )

        assert result.bank_transaction_id == bank_tx_id
        assert result.receipt_id == receipt.receipt_id
        assert result.status == ReconciliationStatus.MATCHED

    @pytest.mark.asyncio
    async def test_reconcile_emits_outbox_event(self, svc, ledger, outbox):
        receipt = await svc.record_payment(make_payment_cmd(100000))
        outbox.events.clear()

        await svc.reconcile_bank_transaction(
            ReconcileBankTransactionCommand(SCHEME_REF, uuid.uuid4(), receipt.receipt_id)
        )
        event_types = [e["event_type"] for e in outbox.events]
        assert "bank.transaction.reconciled" in event_types


class TestRecordExpense:
    @pytest.mark.asyncio
    async def test_record_expense_basic(self, svc, ledger, outbox):
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Insurance", amount_cents=100000, gst_cents=10000,
            transaction_date=date(2026, 3, 1), vendor_name="Acme Insurance", idempotency_key="exp-1",
        )
        expense = await svc.record_expense(cmd)

        assert expense.amount_cents == 100000
        assert expense.gst_cents == 10000
        assert expense.journal_entry_id in ledger._entries

    @pytest.mark.asyncio
    async def test_record_expense_journal_balanced_and_includes_gst(self, svc, ledger, outbox):
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=50000, gst_cents=5000,
            transaction_date=date(2026, 3, 1), idempotency_key="exp-2",
        )
        expense = await svc.record_expense(cmd)
        entry = ledger._entries[expense.journal_entry_id]

        assert entry.debit_total_cents == entry.credit_total_cents == 55000
        debit_line = next(l for l in entry.lines if l.direction == EntryDirection.DEBIT)
        assert debit_line.gst_cents == 5000

    @pytest.mark.asyncio
    async def test_record_sinking_expense_uses_sinking_expense_gl(self, svc, ledger, outbox):
        sinking_fund_id = uuid.UUID("55555555-0000-0000-0000-000000000555")
        ledger._fund_types[sinking_fund_id] = "sinking"
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF,
            category_name="Roof Repairs",
            amount_cents=6816000,
            gst_cents=0,
            transaction_date=date(2026, 4, 8),
            fund_id=sinking_fund_id,
            idempotency_key="exp-sinking-1",
        )
        expense = await svc.record_expense(cmd)
        entry = ledger._entries[expense.journal_entry_id]
        debit_line = next(l for l in entry.lines if l.direction == EntryDirection.DEBIT)

        assert entry.fund_id == sinking_fund_id
        assert expense.fund_id == sinking_fund_id
        assert expense.gl_account_id == GL_SINKING_EXPENSE
        assert debit_line.gl_account_id == GL_SINKING_EXPENSE

    @pytest.mark.asyncio
    async def test_record_expense_rejects_non_positive_amount(self, svc):
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=0, gst_cents=0,
            transaction_date=date(2026, 3, 1),
        )
        with pytest.raises(ValueError, match="positive"):
            await svc.record_expense(cmd)

    @pytest.mark.asyncio
    async def test_record_expense_rejects_negative_gst(self, svc):
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=-1,
            transaction_date=date(2026, 3, 1),
        )
        with pytest.raises(ValueError, match="GST"):
            await svc.record_expense(cmd)

    @pytest.mark.asyncio
    async def test_record_expense_idempotent_replay(self, svc, ledger, outbox):
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=100,
            transaction_date=date(2026, 3, 1), idempotency_key="exp-replay",
        )
        first = await svc.record_expense(cmd)
        entries_after_first = len(ledger._entries)

        second = await svc.record_expense(cmd)

        assert second.journal_entry_id == first.journal_entry_id
        assert second.metadata.get("replayed") is True
        assert len(ledger._entries) == entries_after_first  # no duplicate journal posted

    @pytest.mark.asyncio
    async def test_record_expense_propagates_is_test_data_to_expense(self, svc, ledger, outbox):
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=100,
            transaction_date=date(2026, 3, 1), idempotency_key="exp-test-flag",
            is_test_data=True,
        )
        expense = await svc.record_expense(cmd)
        assert expense.is_test_data is True

    @pytest.mark.asyncio
    async def test_record_expense_is_test_data_defaults_to_false(self, svc, ledger, outbox):
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=100,
            transaction_date=date(2026, 3, 1), idempotency_key="exp-no-test-flag",
        )
        expense = await svc.record_expense(cmd)
        assert expense.is_test_data is False

    @pytest.mark.asyncio
    async def test_record_expense_emits_outbox_event(self, svc, ledger, outbox):
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2026, 3, 1), idempotency_key="exp-outbox",
        )
        await svc.record_expense(cmd)
        event_types = [e["event_type"] for e in outbox.events]
        assert "expense.recorded" in event_types


class TestChargeLotFee:
    def _make_cmd(self, **overrides) -> ChargeLotFeeCommand:
        defaults = dict(
            scheme_ref=SCHEME_REF, lot_id=LOT_ID, owner_party_id=OWNER_ID, fund_id=FUND_ID,
            charge_type="recovery", amount_cents=4200, gst_cents=0,
            transaction_date=date(2026, 2, 20), description="Arrears notice fee",
        )
        defaults.update(overrides)
        return ChargeLotFeeCommand(**defaults)

    @pytest.mark.asyncio
    async def test_recovery_charge_debits_ar_credits_income_and_sets_recovery_costs(self, svc, ledger, outbox):
        cmd = self._make_cmd(idempotency_key="lotfee-1")
        journal_entry_id = await svc.charge_lot_fee(cmd)

        entry = ledger._entries[journal_entry_id]
        assert entry.debit_total_cents == entry.credit_total_cents == 4200
        debit_line = next(l for l in entry.lines if l.direction == EntryDirection.DEBIT)
        credit_line = next(l for l in entry.lines if l.direction == EntryDirection.CREDIT)
        assert debit_line.gl_account_id == GL_AR
        assert debit_line.lot_id == LOT_ID
        assert credit_line.gl_account_id == GL_RECOVERED_FEES

        item = next(i for i in ledger._items if i.levy_item_id)
        assert item.recovery_costs_cents == 4200
        assert item.principal_cents == 0
        assert ledger._last_levy_run_type == "recovery"

    @pytest.mark.asyncio
    async def test_adjustment_charge_sets_principal_not_recovery_costs(self, svc, ledger, outbox):
        cmd = self._make_cmd(
            charge_type="adjustment", amount_cents=33200, description="Rental certificate fee",
            idempotency_key="lotfee-2",
        )
        await svc.charge_lot_fee(cmd)

        item = ledger._items[-1]
        assert item.principal_cents == 33200
        assert item.recovery_costs_cents == 0
        assert ledger._last_levy_run_type == "adjustment"

    @pytest.mark.asyncio
    async def test_negative_amount_reverses_debit_credit_direction(self, svc, ledger, outbox):
        cmd = self._make_cmd(
            amount_cents=-3409, description="Arrears reversal per ACAT hearing",
            idempotency_key="lotfee-reversal",
        )
        journal_entry_id = await svc.charge_lot_fee(cmd)

        entry = ledger._entries[journal_entry_id]
        # Reversed: AR is credited (reduces what the lot owes), income is debited.
        ar_line = next(l for l in entry.lines if l.gl_account_id == GL_AR)
        income_line = next(l for l in entry.lines if l.gl_account_id == GL_RECOVERED_FEES)
        assert ar_line.direction == EntryDirection.CREDIT
        assert income_line.direction == EntryDirection.DEBIT
        assert ar_line.amount_cents == income_line.amount_cents == 3409
        assert entry.metadata["is_reversal"] is True

        item = ledger._items[-1]
        assert item.recovery_costs_cents == -3409

    @pytest.mark.asyncio
    async def test_rejects_zero_amount(self):
        with pytest.raises(ValueError, match="must not be zero"):
            self._make_cmd(amount_cents=0)

    @pytest.mark.asyncio
    async def test_rejects_invalid_charge_type(self):
        with pytest.raises(ValueError, match="charge_type"):
            self._make_cmd(charge_type="bogus")

    @pytest.mark.asyncio
    async def test_rejects_negative_gst(self):
        with pytest.raises(ValueError, match="GST"):
            self._make_cmd(gst_cents=-1)

    @pytest.mark.asyncio
    async def test_idempotent_replay_does_not_double_charge(self, svc, ledger, outbox):
        cmd = self._make_cmd(idempotency_key="lotfee-replay")
        first_id = await svc.charge_lot_fee(cmd)
        items_after_first = len(ledger._items)
        entries_after_first = len(ledger._entries)

        second_id = await svc.charge_lot_fee(cmd)

        assert second_id == first_id
        assert len(ledger._entries) == entries_after_first  # no duplicate journal entry
        assert len(ledger._items) == items_after_first  # no duplicate levy_item either

    @pytest.mark.asyncio
    async def test_does_not_touch_bank_or_expense_accounts(self, svc, ledger, outbox):
        """charge_lot_fee is the recharge side only — record_expense (the cash
        side) is a separate, independent call. Neither GL_BANK nor GL_EXPENSE
        should appear anywhere in this entry's lines."""
        cmd = self._make_cmd(idempotency_key="lotfee-no-cash-side")
        journal_entry_id = await svc.charge_lot_fee(cmd)
        entry = ledger._entries[journal_entry_id]
        account_ids = {l.gl_account_id for l in entry.lines}
        assert GL_BANK not in account_ids
        assert GL_EXPENSE not in account_ids

    @pytest.mark.asyncio
    async def test_emits_outbox_event(self, svc, ledger, outbox):
        cmd = self._make_cmd(idempotency_key="lotfee-outbox")
        await svc.charge_lot_fee(cmd)
        event_types = [e["event_type"] for e in outbox.events]
        assert "lot_fee.charged" in event_types


class TestBulkUpsertHistoricalLevyItems:
    @pytest.mark.asyncio
    async def test_requires_confirm(self, svc):
        cmd = BulkUpsertHistoricalLevyItemsCommand(scheme_ref=SCHEME_REF, items=[], confirm=False)
        with pytest.raises(ValueError, match="confirm"):
            await svc.bulk_upsert_historical_levy_items(cmd)

    @pytest.mark.asyncio
    async def test_inserts_new_item(self, svc, ledger):
        levy_item_id = uuid.uuid4()
        spec = HistoricalLevyItemSpec(
            levy_item_id=levy_item_id, levy_run_id=uuid.uuid4(), lot_id=LOT_ID,
            owner_party_id=OWNER_ID, fund_id=FUND_ID, principal_cents=100000, gst_cents=10000,
        )
        cmd = BulkUpsertHistoricalLevyItemsCommand(scheme_ref=SCHEME_REF, items=[spec], confirm=True)
        result = await svc.bulk_upsert_historical_levy_items(cmd)

        assert len(result) == 1
        assert result[0].levy_item_id == levy_item_id
        assert result[0].gst_cents == 10000

    @pytest.mark.asyncio
    async def test_update_never_touches_paid_cents(self, svc, ledger):
        """Regenerating an existing item's GST must never reset paid_cents."""
        levy_item_id = uuid.uuid4()
        run_id = uuid.uuid4()
        existing = Levy(
            levy_item_id=levy_item_id, scheme_ref=SCHEME_REF, levy_run_id=run_id, lot_id=LOT_ID,
            owner_party_id=OWNER_ID, fund_id=FUND_ID, principal_cents=100000, gst_cents=0, paid_cents=100000,
        )
        ledger._items.append(existing)

        spec = HistoricalLevyItemSpec(
            levy_item_id=levy_item_id, levy_run_id=run_id, lot_id=LOT_ID,
            owner_party_id=OWNER_ID, fund_id=FUND_ID, principal_cents=100000, gst_cents=10000,
        )
        cmd = BulkUpsertHistoricalLevyItemsCommand(scheme_ref=SCHEME_REF, items=[spec], confirm=True)
        result = await svc.bulk_upsert_historical_levy_items(cmd)

        assert result[0].gst_cents == 10000  # corrected
        assert result[0].paid_cents == 100000  # untouched

    @pytest.mark.asyncio
    async def test_does_not_auto_post_journal(self, svc, ledger):
        """bulk_upsert must never post a journal itself — decoupled from re-posting income."""
        spec = HistoricalLevyItemSpec(
            levy_item_id=uuid.uuid4(), levy_run_id=uuid.uuid4(), lot_id=LOT_ID,
            owner_party_id=OWNER_ID, fund_id=FUND_ID, principal_cents=100000, gst_cents=10000,
        )
        cmd = BulkUpsertHistoricalLevyItemsCommand(scheme_ref=SCHEME_REF, items=[spec], confirm=True)
        await svc.bulk_upsert_historical_levy_items(cmd)

        assert len(ledger._entries) == 0

    @pytest.mark.asyncio
    async def test_empty_items_is_noop(self, svc):
        cmd = BulkUpsertHistoricalLevyItemsCommand(scheme_ref=SCHEME_REF, items=[], confirm=True)
        result = await svc.bulk_upsert_historical_levy_items(cmd)
        assert result == []


class TestRebuildProvisionalVsReverseReplace:
    @pytest.mark.asyncio
    async def test_rebuild_requires_confirm(self, svc):
        cmd = RebuildProvisionalLevyJournalsCommand(scheme_ref=SCHEME_REF, source_types=["levy_charge"], confirm=False)
        with pytest.raises(ValueError, match="confirm"):
            await svc.rebuild_provisional_levy_journals(cmd)

    @pytest.mark.asyncio
    async def test_rebuild_removes_only_provisional_entries(self, svc, ledger):
        receipt = await svc.record_payment(make_payment_cmd(1000))
        provisional_id = receipt.journal_entry_id
        # tag it provisional and force the classifier
        entry = ledger._entries[provisional_id]
        entry.source_type = "levy_charge"
        ledger._forced_classifications[provisional_id] = "posted_provisional"

        cmd = RebuildProvisionalLevyJournalsCommand(scheme_ref=SCHEME_REF, source_types=["levy_charge"], confirm=True)
        count = await svc.rebuild_provisional_levy_journals(cmd)

        assert count == 1
        assert provisional_id not in ledger._entries

    @pytest.mark.asyncio
    async def test_rebuild_refuses_when_any_entry_is_posted_canonical(self, svc, ledger):
        """A posted_canonical entry in scope must block the ENTIRE scoped rebuild,
        not just be skipped — this is the safety gate from plan02 amendment 3."""
        receipt1 = await svc.record_payment(make_payment_cmd(1000))
        provisional_id = receipt1.journal_entry_id
        ledger._entries[provisional_id].source_type = "levy_charge"
        ledger._forced_classifications[provisional_id] = "posted_provisional"

        receipt2 = await svc.record_payment(RecordPaymentCommand(
            scheme_ref=SCHEME_REF, payer_party_id=OWNER_ID, lot_id=LOT_ID, channel=PaymentChannel.EFT,
            received_on=date(2026, 2, 1), amount_cents=2000, idempotency_key="canonical-entry",
            posted_by=OWNER_ID, approved_by=OWNER_ID,
        ))
        canonical_id = receipt2.journal_entry_id
        ledger._entries[canonical_id].source_type = "levy_charge"
        # no forced classification -> defaults to posted_canonical

        cmd = RebuildProvisionalLevyJournalsCommand(scheme_ref=SCHEME_REF, source_types=["levy_charge"], confirm=True)
        with pytest.raises(ValueError, match="posted_canonical"):
            await svc.rebuild_provisional_levy_journals(cmd)

        # Neither entry was touched — refusal must be all-or-nothing for the scope.
        assert provisional_id in ledger._entries
        assert canonical_id in ledger._entries

    @pytest.mark.asyncio
    async def test_reverse_and_replace_requires_confirm(self, svc):
        cmd = ReverseAndReplacePostedLevyJournalsCommand(
            scheme_ref=SCHEME_REF, source_types=["levy_charge"], reason="GST correction", confirm=False,
        )
        with pytest.raises(ValueError, match="confirm"):
            await svc.reverse_and_replace_posted_levy_journals(cmd)

    @pytest.mark.asyncio
    async def test_reverse_and_replace_posts_reversal_and_new_entry(self, svc, ledger):
        levy_item_id = uuid.uuid4()
        run_id = uuid.uuid4()
        ledger._items.append(Levy(
            levy_item_id=levy_item_id, scheme_ref=SCHEME_REF, levy_run_id=run_id, lot_id=LOT_ID,
            owner_party_id=OWNER_ID, fund_id=FUND_ID, principal_cents=100000, gst_cents=10000,
        ))
        receipt = await svc.record_payment(make_payment_cmd(1000))
        canonical_id = receipt.journal_entry_id
        ledger._entries[canonical_id].source_type = "levy_charge"
        # classify_levy_journals in the mock needs levy_item_id set on the classification;
        # patch it directly since the mock's classify doesn't derive it automatically.
        original_classify = ledger.classify_levy_journals

        async def _classify_with_levy_item(scheme_ref, source_types):
            entries = await original_classify(scheme_ref, source_types)
            return [
                JournalClassificationEntry(
                    journal_entry_id=e.journal_entry_id, source_type=e.source_type,
                    classification=e.classification, levy_item_id=levy_item_id,
                )
                for e in entries
            ]
        ledger.classify_levy_journals = _classify_with_levy_item

        entries_before = len(ledger._entries)
        cmd = ReverseAndReplacePostedLevyJournalsCommand(
            scheme_ref=SCHEME_REF, source_types=["levy_charge"], reason="GST correction", confirm=True,
        )
        result = await svc.reverse_and_replace_posted_levy_journals(cmd)

        assert result.reversed_count == 1
        assert result.replaced_count == 1
        # original entry untouched (never deleted/edited), plus a reversal, plus a replacement
        assert canonical_id in ledger._entries
        assert len(ledger._entries) == entries_before + 2


# ------------------------------------------------------------------ domain entity tests

class TestDomainEntities:
    def test_money_zero(self):
        m = Money.zero()
        assert m.cents == 0

    def test_money_addition(self):
        assert (Money(100) + Money(200)).cents == 300

    def test_money_subtraction(self):
        assert (Money(300) - Money(100)).cents == 200

    def test_money_negative_raises(self):
        with pytest.raises(ValueError):
            Money(-1)

    def test_cents_helper(self):
        assert cents(1.50) == 150
        assert cents(0) == 0
        assert cents(100) == 10000

    def test_journal_entry_balance_validation(self):
        with pytest.raises(ValueError, match="balanced"):
            JournalEntry(
                scheme_ref=SCHEME_REF,
                period_id=PERIOD_ID,
                source_type="test",
                narration="unbalanced",
                effective_on=date.today(),
                lines=[
                    JournalLine(GL_AR, EntryDirection.DEBIT, 100),
                    JournalLine(GL_INCOME, EntryDirection.CREDIT, 50),  # unbalanced!
                ],
            )

    def test_journal_entry_no_debit_raises(self):
        with pytest.raises(ValueError, match="debit"):
            JournalEntry(
                scheme_ref=SCHEME_REF,
                period_id=PERIOD_ID,
                source_type="test",
                narration="no debit",
                effective_on=date.today(),
                lines=[
                    JournalLine(GL_INCOME, EntryDirection.CREDIT, 100),
                ],
            )

    def test_journal_line_zero_amount_raises(self):
        with pytest.raises(ValueError, match="positive"):
            JournalLine(GL_AR, EntryDirection.DEBIT, 0)

    def test_journal_line_negative_amount_raises(self):
        with pytest.raises(ValueError, match="positive"):
            JournalLine(GL_AR, EntryDirection.DEBIT, -100)


# ------------------------------------------------------------------ plugin tests

class TestPluginSystem:
    def test_act_plugin_registers(self):
        from plugins.examples.act_plugin import ACT2026ReformPlugin
        from plugins.registry import PluginRegistry

        registry = PluginRegistry()
        registry.register(ACT2026ReformPlugin())
        assert len(registry.registered_plugins) == 1

    @pytest.mark.asyncio
    async def test_act_plugin_validates_levy(self):
        from plugins.examples.act_plugin import ACT2026ReformPlugin
        from plugins.base import ValidationResult

        plugin = ACT2026ReformPlugin()
        cmd = make_levy_cmd(100000)
        result = plugin.validate_command(cmd)
        assert result.is_valid

    @pytest.mark.asyncio
    async def test_act_plugin_rejects_zero_levy(self):
        from plugins.examples.act_plugin import ACT2026ReformPlugin

        plugin = ACT2026ReformPlugin()
        cmd = make_levy_cmd(0)
        # Zero principal is caught by service, not the plugin validate; plugin passes
        result = plugin.validate_command(cmd)
        # Plugin should fail zero total
        # Note: zero amounts are filtered at service level by the LotLevySpec
        # The plugin returns fail only when total <= 0
        # In this test the cmd has principal=0 so plugin should fail it
        assert not result.is_valid

    @pytest.mark.asyncio
    async def test_failing_plugin_blocks_command(self):
        from plugins.base import StrataPlugin, CapabilitySpec, ValidationResult
        from plugins.registry import PluginRegistry

        class RejectAllPlugin(StrataPlugin):
            def validate_command(self, cmd):
                return ValidationResult.fail("blocked by policy")

            def expose_capability(self):
                return CapabilitySpec("reject_all", "RejectAll", hooks=["validate_command"])

        registry = PluginRegistry()
        registry.register(RejectAllPlugin())
        svc = FinancialCoreService(MockLedgerRepository(), MockOutboxPort(), registry)

        with pytest.raises(ValueError, match="blocked by policy"):
            await svc.create_levy(make_levy_cmd(100000))


# ------------------------------------------------------------------
# Date-aware accounting-period resolution + historical reconstruction
# ------------------------------------------------------------------

def _two_period_ledger() -> "MockLedgerRepository":
    """A ledger with the default wide-open period cleared, replaced by two
    non-overlapping periods: 2025 (closed) and 2026 (open) — mirrors East
    Gate's real live state that motivated this feature."""
    ledger = MockLedgerRepository()
    ledger._periods = []
    ledger.add_period(PERIOD_ID_2025, "2025", date(2025, 1, 1), date(2025, 12, 31), "closed")
    ledger.add_period(PERIOD_ID, "2026", date(2026, 1, 1), date(2026, 12, 31), "open")
    return ledger


class TestGetAccountingPeriodForDate:
    @pytest.mark.asyncio
    async def test_exact_match_single_open_period(self):
        ledger = _two_period_ledger()
        period = await ledger.get_accounting_period_for_date(SCHEME_REF, date(2026, 6, 15))
        assert period.period_id == PERIOD_ID
        assert period.status == "open"

    @pytest.mark.asyncio
    async def test_no_covering_period_raises(self):
        ledger = _two_period_ledger()
        with pytest.raises(NoAccountingPeriodForDate):
            await ledger.get_accounting_period_for_date(SCHEME_REF, date(2024, 1, 1))

    @pytest.mark.asyncio
    async def test_closed_period_without_override_raises_not_permitted(self):
        ledger = _two_period_ledger()
        with pytest.raises(AccountingPeriodNotPermitted):
            await ledger.get_accounting_period_for_date(SCHEME_REF, date(2025, 6, 15))

    @pytest.mark.asyncio
    async def test_closed_period_with_permitted_statuses_succeeds(self):
        ledger = _two_period_ledger()
        period = await ledger.get_accounting_period_for_date(
            SCHEME_REF, date(2025, 6, 15), permitted_statuses=frozenset({"open", "closed"})
        )
        assert period.period_id == PERIOD_ID_2025
        assert period.status == "closed"

    @pytest.mark.asyncio
    async def test_two_overlapping_periods_raises_ambiguous(self):
        ledger = MockLedgerRepository()
        ledger._periods = []
        ledger.add_period(PERIOD_ID, "2026-a", date(2026, 1, 1), date(2026, 12, 31), "open")
        ledger.add_period(PERIOD_ID_2025, "2026-b", date(2026, 6, 1), date(2027, 6, 30), "open")
        with pytest.raises(AmbiguousAccountingPeriod):
            await ledger.get_accounting_period_for_date(SCHEME_REF, date(2026, 7, 1))


class TestHandlerDateAwareResolution:
    """Each of the 6 journal-producing handlers must resolve its posting
    period from its own command date, not always the same period — the
    single-default-period fixture can't distinguish "date-aware" from
    "always returns the same period", so every test here uses
    _two_period_ledger() and a command dated in 2025 (closed) or 2026
    (open) to prove the period actually differs by date."""

    @pytest.mark.asyncio
    async def test_create_levy_resolves_by_issue_date(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = CreateLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2026", quarter_no=1,
            issue_date=date(2026, 3, 1), due_date=date(2026, 3, 31),
            lot_levies=[LotLevySpec(lot_id=LOT_ID, owner_party_id=OWNER_ID, fund_id=FUND_ID, principal_cents=1000)],
        )
        items = await svc.create_levy(cmd)
        entry = next(e for e in ledger._entries.values() if e.source_type == "levy_run")
        assert entry.period_id == PERIOD_ID

    @pytest.mark.asyncio
    async def test_record_payment_resolves_by_received_on(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordPaymentCommand(
            scheme_ref=SCHEME_REF, payer_party_id=OWNER_ID, lot_id=LOT_ID, channel=PaymentChannel.EFT,
            received_on=date(2025, 5, 1), amount_cents=5000, idempotency_key="date-aware-payment",
            posted_by=OWNER_ID, approved_by=OWNER_ID,
        )
        with pytest.raises(AccountingPeriodNotPermitted):
            await svc.record_payment(cmd)

    @pytest.mark.asyncio
    async def test_record_historical_payment_posts_into_closed_period(self):
        """RecordHistoricalPaymentCommand (added 2026-08-01 for East Gate's
        2021-2025 levy-payment backfill) must succeed against the exact same
        closed 2025 period that the ordinary record_payment() test above
        rejects -- proving the historical variant is the intended, gated
        escape hatch, not a bypass of the underlying rule."""
        from services.financial_core.domain.entities import RecordHistoricalPaymentCommand

        ledger = _two_period_ledger()
        outbox = MockOutboxPort()
        svc = FinancialCoreService(ledger, outbox)
        cmd = RecordHistoricalPaymentCommand(
            scheme_ref=SCHEME_REF, payer_party_id=OWNER_ID, lot_id=LOT_ID, channel=PaymentChannel.EFT,
            received_on=date(2025, 5, 1), amount_cents=5000,
            authorisation=make_authorisation(), idempotency_key="historical-payment-1",
        )
        receipt = await svc.record_historical_payment(cmd)
        entry = ledger._entries[receipt.journal_entry_id]
        assert entry.period_id == PERIOD_ID_2025

        event_types = [e["event_type"] for e in outbox.events]
        assert "ledger.historical_posting_authorisation_used" in event_types
        assert "ledger.closed_period_override" in event_types

    @pytest.mark.asyncio
    async def test_record_historical_payment_against_open_period_no_override_event(self):
        ledger = _two_period_ledger()
        outbox = MockOutboxPort()
        svc = FinancialCoreService(ledger, outbox)
        from services.financial_core.domain.entities import RecordHistoricalPaymentCommand
        cmd = RecordHistoricalPaymentCommand(
            scheme_ref=SCHEME_REF, payer_party_id=OWNER_ID, lot_id=LOT_ID, channel=PaymentChannel.EFT,
            received_on=date(2026, 3, 1), amount_cents=5000,
            authorisation=make_authorisation(), idempotency_key="historical-payment-open-period",
        )
        await svc.record_historical_payment(cmd)
        event_types = [e["event_type"] for e in outbox.events]
        assert "ledger.historical_posting_authorisation_used" in event_types
        assert "ledger.closed_period_override" not in event_types

    @pytest.mark.asyncio
    async def test_post_genesis_journal_resolves_by_as_at_date(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = PostGenesisJournalCommand(
            scheme_ref=SCHEME_REF, fund_id=FUND_ID, opening_balance_cents=10000,
            as_at_date=date(2026, 1, 1), idempotency_key="genesis-date-aware",
        )
        entry = await svc.post_genesis_journal(cmd)
        assert entry.period_id == PERIOD_ID

    @pytest.mark.asyncio
    async def test_reverse_entry_resolves_by_effective_on(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        original = JournalEntry(
            scheme_ref=SCHEME_REF, period_id=PERIOD_ID, source_type="expense",
            narration="original", effective_on=date(2026, 1, 1),
            lines=[
                JournalLine(gl_account_id=GL_EXPENSE, direction=EntryDirection.DEBIT, amount_cents=100),
                JournalLine(gl_account_id=GL_BANK, direction=EntryDirection.CREDIT, amount_cents=100),
            ],
        )
        saved = await ledger.save_journal_entry(original)
        cmd = ReverseEntryCommand(
            scheme_ref=SCHEME_REF, journal_entry_id=saved.journal_entry_id, reason="test reversal",
            effective_on=date(2025, 6, 1),
        )
        with pytest.raises(AccountingPeriodNotPermitted):
            await svc.reverse_entry(cmd)

    @pytest.mark.asyncio
    async def test_record_expense_resolves_by_transaction_date(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2025, 6, 1), idempotency_key="expense-date-aware",
        )
        with pytest.raises(AccountingPeriodNotPermitted):
            await svc.record_expense(cmd)

    @pytest.mark.asyncio
    async def test_charge_lot_fee_resolves_by_transaction_date(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = ChargeLotFeeCommand(
            scheme_ref=SCHEME_REF, lot_id=LOT_ID, owner_party_id=OWNER_ID, fund_id=FUND_ID,
            charge_type="recovery", amount_cents=1000, gst_cents=0,
            transaction_date=date(2025, 6, 1), description="test", idempotency_key="lotfee-date-aware",
        )
        with pytest.raises(AccountingPeriodNotPermitted):
            await svc.charge_lot_fee(cmd)


class TestHistoricalPostingAuthorisation:
    def test_empty_reason_rejected(self):
        with pytest.raises(ValueError, match="reason"):
            make_authorisation(reason="  ")

    def test_missing_approved_by_rejected(self):
        with pytest.raises(ValueError):
            make_authorisation(approved_by=None)

    def test_approved_by_equals_executed_by_rejected(self):
        with pytest.raises(ValueError, match="different users"):
            make_authorisation(approved_by=EXECUTOR_ID, executed_by=EXECUTOR_ID)

    @pytest.mark.asyncio
    async def test_ordinary_command_has_no_override_available(self):
        """RecordExpenseCommand/ChargeLotFeeCommand have no authorisation
        field at all — a closed-period date always raises, unconditionally."""
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2025, 3, 1), idempotency_key="no-override-1",
        )
        with pytest.raises(AccountingPeriodNotPermitted):
            await svc.record_expense(cmd)

    @pytest.mark.asyncio
    async def test_record_historical_expense_posts_into_closed_period(self):
        ledger = _two_period_ledger()
        outbox = MockOutboxPort()
        svc = FinancialCoreService(ledger, outbox)
        cmd = RecordHistoricalExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2025, 3, 1), authorisation=make_authorisation(),
            idempotency_key="historical-expense-1",
        )
        expense = await svc.record_historical_expense(cmd)
        entry = ledger._entries[expense.journal_entry_id]
        assert entry.period_id == PERIOD_ID_2025

        event_types = [e["event_type"] for e in outbox.events]
        assert "ledger.historical_posting_authorisation_used" in event_types
        assert "ledger.closed_period_override" in event_types

    @pytest.mark.asyncio
    async def test_record_historical_expense_against_open_period_no_override_event(self):
        """Authorisation supplied but the target period is actually still
        open — the closed_period_override event must NOT fire (it wasn't
        load-bearing), only the generic authorisation-used event."""
        ledger = _two_period_ledger()
        outbox = MockOutboxPort()
        svc = FinancialCoreService(ledger, outbox)
        cmd = RecordHistoricalExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2026, 3, 1), authorisation=make_authorisation(),
            idempotency_key="historical-expense-open-period",
        )
        await svc.record_historical_expense(cmd)
        event_types = [e["event_type"] for e in outbox.events]
        assert "ledger.historical_posting_authorisation_used" in event_types
        assert "ledger.closed_period_override" not in event_types

    @pytest.mark.asyncio
    async def test_ordinary_record_expense_never_emits_authorisation_events(self):
        ledger = _two_period_ledger()
        outbox = MockOutboxPort()
        svc = FinancialCoreService(ledger, outbox)
        cmd = RecordExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2026, 3, 1), idempotency_key="ordinary-no-auth-events",
        )
        await svc.record_expense(cmd)
        event_types = [e["event_type"] for e in outbox.events]
        assert "ledger.historical_posting_authorisation_used" not in event_types
        assert "ledger.closed_period_override" not in event_types

    @pytest.mark.asyncio
    async def test_charge_historical_lot_fee_posts_into_closed_period(self):
        ledger = _two_period_ledger()
        outbox = MockOutboxPort()
        svc = FinancialCoreService(ledger, outbox)
        cmd = ChargeHistoricalLotFeeCommand(
            scheme_ref=SCHEME_REF, lot_id=LOT_ID, owner_party_id=OWNER_ID, fund_id=FUND_ID,
            charge_type="recovery", amount_cents=4200, gst_cents=0,
            transaction_date=date(2025, 6, 1), description="Historical arrears fee",
            authorisation=make_authorisation(), idempotency_key="historical-lotfee-1",
        )
        journal_entry_id = await svc.charge_historical_lot_fee(cmd)
        entry = ledger._entries[journal_entry_id]
        assert entry.period_id == PERIOD_ID_2025
        item = ledger._items[-1]
        assert item.journal_entry_id == journal_entry_id

        event_types = [e["event_type"] for e in outbox.events]
        assert "ledger.closed_period_override" in event_types


class TestBankTransactionLinkage:
    @pytest.mark.asyncio
    async def test_historical_expense_claims_source_bank_transaction(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        bank_tx_id = uuid.uuid4()
        cmd = RecordHistoricalExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2025, 3, 1), authorisation=make_authorisation(),
            bank_transaction_id=bank_tx_id, idempotency_key="claim-test-1",
        )
        await svc.record_historical_expense(cmd)
        assert ledger._bank_tx_claims[bank_tx_id] == BATCH_ID

    @pytest.mark.asyncio
    async def test_claim_conflicting_batch_raises(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        bank_tx_id = uuid.uuid4()
        other_batch_id = uuid.uuid4()
        ledger._bank_tx_claims[bank_tx_id] = other_batch_id

        cmd = RecordHistoricalExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2025, 3, 1), authorisation=make_authorisation(),
            bank_transaction_id=bank_tx_id, idempotency_key="claim-conflict-1",
        )
        with pytest.raises(BankTransactionClaimConflict):
            await svc.record_historical_expense(cmd)
        # No journal should have been posted — the claim conflict must abort
        # before any journal write, not merely warn.
        assert not any(e.idempotency_key == "claim-conflict-1" for e in ledger._entries.values())

    @pytest.mark.asyncio
    async def test_claim_missing_bank_transaction_raises(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        bank_tx_id = uuid.uuid4()
        ledger._missing_bank_tx_ids.add(bank_tx_id)

        cmd = RecordHistoricalExpenseCommand(
            scheme_ref=SCHEME_REF, category_name="Repairs", amount_cents=1000, gst_cents=0,
            transaction_date=date(2025, 3, 1), authorisation=make_authorisation(),
            bank_transaction_id=bank_tx_id, idempotency_key="claim-missing-1",
        )
        with pytest.raises(BankTransactionNotFound):
            await svc.record_historical_expense(cmd)

    @pytest.mark.asyncio
    async def test_repeated_claim_by_same_batch_is_idempotent(self):
        ledger = _two_period_ledger()
        bank_tx_id = uuid.uuid4()
        await ledger.claim_bank_transaction_for_batch(TENANT_ID, SCHEME_ID, bank_tx_id, BATCH_ID)
        # Second claim by the SAME batch must not raise.
        await ledger.claim_bank_transaction_for_batch(TENANT_ID, SCHEME_ID, bank_tx_id, BATCH_ID)
        assert ledger._bank_tx_claims[bank_tx_id] == BATCH_ID


class TestCreateHistoricalLevy:
    """create_historical_levy() — the levy CHARGE poster (distinct from create_levy(), never
    HTTP-reachable — see TestHistoricalPathwayIsolation below)."""

    def _spec(self, **overrides) -> LotLevySpec:
        defaults = dict(
            lot_id=LOT_ID, owner_party_id=OWNER_ID, fund_id=FUND_ID,
            principal_cents=10000, gst_cents=1000,
        )
        defaults.update(overrides)
        return LotLevySpec(**defaults)

    @pytest.mark.asyncio
    async def test_closed_period_posting_and_gl_balance(self):
        ledger = _two_period_ledger()
        outbox = MockOutboxPort()
        svc = FinancialCoreService(ledger, outbox)
        cmd = CreateHistoricalLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2025", quarter_no=1,
            issue_date=date(2025, 3, 31), due_date=date(2025, 3, 31),
            lot_levies=[self._spec()], authorisation=make_authorisation(),
            idempotency_key="hist-levy-2025-q1-admin",
        )
        items = await svc.create_historical_levy(cmd)
        assert len(items) == 1
        assert items[0].principal_cents == 10000
        assert items[0].gst_cents == 1000

        entry = ledger._entries[items[0].journal_entry_id]
        assert entry.period_id == PERIOD_ID_2025
        debit_total = sum(l.amount_cents for l in entry.lines if l.direction == EntryDirection.DEBIT)
        credit_total = sum(l.amount_cents for l in entry.lines if l.direction == EntryDirection.CREDIT)
        assert debit_total == credit_total == 11000
        assert any(l.gl_account_id == GL_AR and l.direction == EntryDirection.DEBIT for l in entry.lines)
        assert any(l.gl_account_id == GL_INCOME and l.direction == EntryDirection.CREDIT for l in entry.lines)

        event_types = [e["event_type"] for e in outbox.events]
        assert "levy.historical_run.created" in event_types
        assert "ledger.closed_period_override" in event_types

    @pytest.mark.asyncio
    async def test_open_period_no_override_event(self):
        ledger = _two_period_ledger()
        outbox = MockOutboxPort()
        svc = FinancialCoreService(ledger, outbox)
        cmd = CreateHistoricalLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2026", quarter_no=1,
            issue_date=date(2026, 3, 31), due_date=date(2026, 3, 31),
            lot_levies=[self._spec()], authorisation=make_authorisation(),
            idempotency_key="hist-levy-2026-q1-admin",
        )
        await svc.create_historical_levy(cmd)
        event_types = [e["event_type"] for e in outbox.events]
        assert "ledger.historical_posting_authorisation_used" in event_types
        assert "ledger.closed_period_override" not in event_types

    @pytest.mark.asyncio
    async def test_idempotency_key_replay_no_duplicate_journal(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = CreateHistoricalLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2025", quarter_no=2,
            issue_date=date(2025, 6, 1), due_date=date(2025, 6, 30),
            lot_levies=[self._spec()], authorisation=make_authorisation(),
            idempotency_key="hist-levy-2025-q2-admin",
        )
        first = await svc.create_historical_levy(cmd)
        assert len(ledger._entries) == 1
        second = await svc.create_historical_levy(cmd)
        assert len(ledger._entries) == 1  # no second journal posted
        assert first[0].levy_item_id == second[0].levy_item_id

    @pytest.mark.asyncio
    async def test_run_natural_key_disambiguates_by_fund(self):
        """Regression test for the bug plan02 caught: two calls for the same
        year/quarter/levy_run_type but DIFFERENT funds must never share a levy_run_id —
        finance.levy_runs has no fund column, so this is enforced by including fund_id in
        find_or_create_levy_run()'s lookup key, not by the run table itself."""
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        sinking_fund_id = uuid.uuid4()
        ledger._fund_types[sinking_fund_id] = "sinking"

        admin_cmd = CreateHistoricalLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2025", quarter_no=1,
            issue_date=date(2025, 3, 31), due_date=date(2025, 3, 31),
            lot_levies=[self._spec(fund_id=FUND_ID)], authorisation=make_authorisation(),
            idempotency_key="hist-levy-2025-q1-admin-fund-test",
        )
        sinking_cmd = CreateHistoricalLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2025", quarter_no=1,
            issue_date=date(2025, 3, 31), due_date=date(2025, 3, 31),
            lot_levies=[self._spec(fund_id=sinking_fund_id)], authorisation=make_authorisation(),
            idempotency_key="hist-levy-2025-q1-sinking-fund-test",
        )
        admin_items = await svc.create_historical_levy(admin_cmd)
        sinking_items = await svc.create_historical_levy(sinking_cmd)
        assert admin_items[0].levy_run_id != sinking_items[0].levy_run_id

        sinking_entry = ledger._entries[sinking_items[0].journal_entry_id]
        assert any(l.gl_account_id == GL_SINKING_INCOME and l.direction == EntryDirection.CREDIT
                   for l in sinking_entry.lines)

    @pytest.mark.asyncio
    async def test_resume_after_journal_posted_but_items_missing(self):
        """Simulates a crash between post_journal_entry() and save_levy_items() — a retry with
        the same idempotency_key must post items under the ALREADY-POSTED journal, not post a
        second journal entry."""
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = CreateHistoricalLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2025", quarter_no=3,
            issue_date=date(2025, 9, 1), due_date=date(2025, 9, 30),
            lot_levies=[self._spec()], authorisation=make_authorisation(),
            idempotency_key="hist-levy-2025-q3-admin-resume",
        )
        # Pre-seed a posted journal (as if create_historical_levy crashed right after posting
        # it) with zero levy_items — same idempotency_key, same total/date the real call would use.
        pre_run_id = uuid.uuid4()
        pre_entry = JournalEntry(
            scheme_ref=SCHEME_REF, period_id=PERIOD_ID_2025, source_type="historical_levy_run",
            source_reference=str(pre_run_id), narration="pre-seeded", effective_on=date(2025, 9, 1),
            lines=[
                JournalLine(gl_account_id=GL_AR, direction=EntryDirection.DEBIT, amount_cents=11000, lot_id=LOT_ID),
                JournalLine(gl_account_id=GL_INCOME, direction=EntryDirection.CREDIT, amount_cents=11000, lot_id=LOT_ID),
            ],
            idempotency_key="hist-levy-2025-q3-admin-resume",
        )
        await ledger.save_journal_entry(pre_entry)
        assert len(ledger._entries) == 1

        items = await svc.create_historical_levy(cmd)
        assert len(ledger._entries) == 1  # still just the pre-seeded journal, no second one
        assert len(items) == 1
        assert items[0].levy_run_id == pre_run_id
        assert items[0].journal_entry_id == pre_entry.journal_entry_id

    @pytest.mark.asyncio
    async def test_rejects_mixed_fund_lot_levies(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        other_fund = uuid.uuid4()
        cmd = CreateHistoricalLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2025", quarter_no=1,
            issue_date=date(2025, 3, 31), due_date=date(2025, 3, 31),
            lot_levies=[self._spec(fund_id=FUND_ID), self._spec(fund_id=other_fund)],
            authorisation=make_authorisation(), idempotency_key="hist-levy-mixed-fund",
        )
        with pytest.raises(ValueError, match="share one fund_id"):
            await svc.create_historical_levy(cmd)

    @pytest.mark.asyncio
    async def test_unresolved_owner_retains_charge_and_flags_metadata(self):
        ledger = _two_period_ledger()
        svc = FinancialCoreService(ledger, MockOutboxPort())
        placeholder_owner = uuid.uuid4()
        cmd = CreateHistoricalLevyCommand(
            scheme_ref=SCHEME_REF, financial_year="2025", quarter_no=1,
            issue_date=date(2025, 3, 31), due_date=date(2025, 3, 31),
            lot_levies=[self._spec(owner_party_id=placeholder_owner, owner_resolution_status="unresolved")],
            authorisation=make_authorisation(), idempotency_key="hist-levy-unresolved-owner",
        )
        items = await svc.create_historical_levy(cmd)
        assert items[0].owner_resolution_status == "unresolved"
        assert items[0].owner_party_id == placeholder_owner  # charge retained, not dropped

        entry = ledger._entries[items[0].journal_entry_id]
        assert entry.metadata["unresolved_owner_lot_ids"] == [str(LOT_ID)]


class TestHistoricalPathwayIsolation:
    """The historical-reconstruction pathway must have zero HTTP-reachable
    entry points — a comment in adapter.py is not a real control. Scan the
    actual source of every router and adapter.py for references to the
    historical types; fail if any are found."""

    @pytest.mark.asyncio
    async def test_no_router_or_adapter_references_historical_types(self):
        import pathlib
        import re

        repo_backend = pathlib.Path(__file__).resolve().parents[2] / "backend"
        forbidden = [
            "HistoricalPostingAuthorisation",
            "RecordHistoricalExpenseCommand",
            "RecordHistoricalPaymentCommand",
            "ChargeHistoricalLotFeeCommand",
            "CreateHistoricalLevyCommand",
            "RecordAdjustmentCommand",
        ]
        offenders = []

        targets = list((repo_backend / "routers").glob("*.py"))
        adapter_path = repo_backend / "services" / "financial_core" / "adapter.py"
        if adapter_path.exists():
            targets.append(adapter_path)

        for path in targets:
            text = path.read_text()
            for name in forbidden:
                if re.search(rf"\b{name}\b", text):
                    offenders.append(f"{path}: {name}")

        assert not offenders, f"Historical pathway leaked into HTTP-reachable code: {offenders}"


class TestRecordAdjustment:
    """RecordAdjustmentCommand / record_adjustment() — GAP-FIN-046 portal-evidenced
    correction for units whose reconstructed receipts overstated how much was
    actually paid, spread across years rather than concentrated in one bad receipt."""

    def _make_item(self, ledger, *, run_key, due_date, paid_cents, principal_cents=None):
        run_id = uuid.uuid4()
        ledger._runs_by_key[run_key] = run_id
        item = Levy(
            levy_item_id=uuid.uuid4(), scheme_ref=SCHEME_REF, levy_run_id=run_id,
            lot_id=LOT_ID, owner_party_id=OWNER_ID, fund_id=FUND_ID,
            principal_cents=principal_cents if principal_cents is not None else paid_cents,
            paid_cents=paid_cents, due_date=due_date,
        )
        ledger._items.append(item)
        return item

    @pytest.mark.asyncio
    async def test_single_item_adjustment_posts_journal_and_decrements(self):
        ledger = _two_period_ledger()
        item = self._make_item(
            ledger, run_key=(SCHEME_ID, "2026", 2, "ordinary", FUND_ID),
            due_date=date(2026, 6, 30), paid_cents=180552,
        )
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordAdjustmentCommand(
            scheme_ref=SCHEME_REF, lot_id=LOT_ID, amount_cents=92998, financial_year="2026",
            authorisation=make_authorisation(reason="portal-evidenced correction"),
            effective_on=date(2026, 8, 6), idempotency_key="adj-1",
        )
        result = await svc.record_adjustment(cmd)

        assert result.amount_cents == 92998
        assert len(result.items_adjusted) == 1
        assert result.items_adjusted[0].levy_item_id == item.levy_item_id
        assert item.paid_cents == 180552 - 92998  # mutated in place by the mock

        entry = ledger._entries[result.journal_entry_id]
        assert entry.source_type == "reconstruction_adjustment"
        debit = next(l for l in entry.lines if l.direction == EntryDirection.DEBIT)
        credit = next(l for l in entry.lines if l.direction == EntryDirection.CREDIT)
        assert debit.gl_account_id == GL_AR
        assert credit.gl_account_id == GL_BANK
        assert debit.amount_cents == credit.amount_cents == 92998

    @pytest.mark.asyncio
    async def test_spans_most_recently_due_item_first(self):
        """Two levy_items for the year; an adjustment bigger than the most-recent
        item's paid_cents must spill into the OLDER item next, not skip to it first."""
        ledger = _two_period_ledger()
        newer = self._make_item(
            ledger, run_key=(SCHEME_ID, "2026", 2, "ordinary", FUND_ID),
            due_date=date(2026, 6, 30), paid_cents=20000,
        )
        older = self._make_item(
            ledger, run_key=(SCHEME_ID, "2026", 1, "ordinary", FUND_ID),
            due_date=date(2026, 3, 31), paid_cents=20000,
        )
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordAdjustmentCommand(
            scheme_ref=SCHEME_REF, lot_id=LOT_ID, amount_cents=25000, financial_year="2026",
            authorisation=make_authorisation(), effective_on=date(2026, 8, 6), idempotency_key="adj-2",
        )
        result = await svc.record_adjustment(cmd)

        assert newer.paid_cents == 0        # fully clawed back first (20000 of 25000)
        assert older.paid_cents == 15000    # remainder (5000) taken from the older item
        assert {i.levy_item_id for i in result.items_adjusted} == {newer.levy_item_id, older.levy_item_id}

    @pytest.mark.asyncio
    async def test_insufficient_paid_cents_raises_and_touches_nothing(self):
        """The two-pass plan-then-apply design: a shortfall must be detected before any
        write, so paid_cents is provably unchanged after the raise — not "maybe partially
        decremented," which would be the case for a naive single-pass write-as-you-go loop."""
        ledger = _two_period_ledger()
        item = self._make_item(
            ledger, run_key=(SCHEME_ID, "2026", 2, "ordinary", FUND_ID),
            due_date=date(2026, 6, 30), paid_cents=1000,
        )
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordAdjustmentCommand(
            scheme_ref=SCHEME_REF, lot_id=LOT_ID, amount_cents=5000, financial_year="2026",
            authorisation=make_authorisation(), effective_on=date(2026, 8, 6), idempotency_key="adj-3",
        )
        with pytest.raises(ValueError, match="only 1000 cents"):
            await svc.record_adjustment(cmd)
        assert item.paid_cents == 1000  # untouched

    @pytest.mark.asyncio
    async def test_zero_or_negative_amount_rejected_at_construction(self):
        with pytest.raises(ValueError, match="must be positive"):
            RecordAdjustmentCommand(
                scheme_ref=SCHEME_REF, lot_id=LOT_ID, amount_cents=0, financial_year="2026",
                authorisation=make_authorisation(),
            )

    @pytest.mark.asyncio
    async def test_only_touches_the_named_financial_year(self):
        """A same-lot item in a different financial_year must be completely invisible to
        this decrement — proves the join filters correctly rather than matching on lot_id
        alone."""
        ledger = _two_period_ledger()
        target = self._make_item(
            ledger, run_key=(SCHEME_ID, "2026", 2, "ordinary", FUND_ID),
            due_date=date(2026, 6, 30), paid_cents=50000,
        )
        other_year = self._make_item(
            ledger, run_key=(SCHEME_ID, "2025", 4, "ordinary", FUND_ID),
            due_date=date(2025, 12, 1), paid_cents=99999,
        )
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordAdjustmentCommand(
            scheme_ref=SCHEME_REF, lot_id=LOT_ID, amount_cents=1000, financial_year="2026",
            authorisation=make_authorisation(), effective_on=date(2026, 8, 6), idempotency_key="adj-4",
        )
        await svc.record_adjustment(cmd)
        assert target.paid_cents == 49000
        assert other_year.paid_cents == 99999  # untouched


class TestAuthorisationIdentityVerification:
    """_verify_authorisation_identities() — added 2026-08-06 after an audit found
    HistoricalPostingAuthorisation's own validation never confirms approved_by/
    executed_by are real users (it can't — synchronous dataclass, no DB access).
    Pulled up into FinancialCoreService so every command carrying a
    HistoricalPostingAuthorisation is protected uniformly, not just the one that
    prompted the fix."""

    @pytest.mark.asyncio
    async def test_helper_rejects_unknown_approved_by(self):
        ledger = MockLedgerRepository()
        ledger._missing_user_ids.add(APPROVER_ID)
        svc = FinancialCoreService(ledger, MockOutboxPort())
        with pytest.raises(ValueError, match="approved_by.*not a real"):
            await svc._verify_authorisation_identities(SCHEME_REF, make_authorisation())

    @pytest.mark.asyncio
    async def test_helper_rejects_unknown_executed_by(self):
        """Mirrors the actual live incident: approved_by was real, executed_by wasn't."""
        ledger = MockLedgerRepository()
        ledger._missing_user_ids.add(EXECUTOR_ID)
        svc = FinancialCoreService(ledger, MockOutboxPort())
        with pytest.raises(ValueError, match="executed_by.*not a real"):
            await svc._verify_authorisation_identities(SCHEME_REF, make_authorisation())

    @pytest.mark.asyncio
    async def test_helper_passes_when_both_real(self):
        ledger = MockLedgerRepository()  # _missing_user_ids empty — both count as real
        svc = FinancialCoreService(ledger, MockOutboxPort())
        await svc._verify_authorisation_identities(SCHEME_REF, make_authorisation())  # no raise

    @pytest.mark.asyncio
    async def test_record_adjustment_rejects_before_touching_anything(self):
        """End-to-end through the real handler, not just the helper in isolation —
        proves the wiring, and proves rejection happens before any levy_item write."""
        ledger = _two_period_ledger()
        item = Levy(
            levy_item_id=uuid.uuid4(), scheme_ref=SCHEME_REF, levy_run_id=uuid.uuid4(),
            lot_id=LOT_ID, owner_party_id=OWNER_ID, fund_id=FUND_ID,
            principal_cents=100000, paid_cents=100000, due_date=date(2026, 6, 30),
        )
        ledger._runs_by_key[(SCHEME_ID, "2026", 2, "ordinary", FUND_ID)] = item.levy_run_id
        ledger._items.append(item)
        ledger._missing_user_ids.add(EXECUTOR_ID)
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordAdjustmentCommand(
            scheme_ref=SCHEME_REF, lot_id=LOT_ID, amount_cents=5000, financial_year="2026",
            authorisation=make_authorisation(), effective_on=date(2026, 8, 6), idempotency_key="adj-reject",
        )
        with pytest.raises(ValueError, match="executed_by.*not a real"):
            await svc.record_adjustment(cmd)
        assert item.paid_cents == 100000  # untouched
        assert not ledger._entries  # no journal entry posted either

    @pytest.mark.asyncio
    async def test_record_historical_payment_also_rejects(self):
        """A second, different Historical* command — proves this is genuinely shared,
        not something only record_adjustment happens to call."""
        from services.financial_core.domain.entities import RecordHistoricalPaymentCommand
        ledger = _two_period_ledger()
        ledger._missing_user_ids.add(APPROVER_ID)
        svc = FinancialCoreService(ledger, MockOutboxPort())
        cmd = RecordHistoricalPaymentCommand(
            scheme_ref=SCHEME_REF, payer_party_id=OWNER_ID, lot_id=LOT_ID, channel=PaymentChannel.EFT,
            received_on=date(2026, 6, 15), amount_cents=10000,
            authorisation=make_authorisation(), idempotency_key="hist-pay-reject",
        )
        with pytest.raises(ValueError, match="approved_by.*not a real"):
            await svc.record_historical_payment(cmd)
        assert not ledger._receipts


class TestOverpaymentBecomesOwnerCredit:
    """GAP-FIN-036 — a receipt exceeding every open levy item must not lose its surplus.

    `allocate_payment` fills open levy items oldest-first and stops when it runs out of
    them. Whatever remained of the receipt used to fall out of scope entirely: the
    receipt and its journal entry were real, but `finance.levy_items` — which every
    per-lot balance query sums — has no row that can represent money paid beyond what
    was ever charged, so a genuinely in-credit owner read as $0.00.
    """

    @pytest.mark.asyncio
    async def test_surplus_is_recorded_as_owner_credit(self, svc, ledger, outbox):
        await svc.create_levy(make_levy_cmd(100000))
        receipt = await svc.record_payment(make_payment_cmd(175000))

        allocations = await svc.allocate_payment(
            AllocatePaymentCommand(scheme_ref=SCHEME_REF, receipt_id=receipt.receipt_id)
        )

        assert sum(a.allocated_cents for a in allocations) == 100000
        assert len(ledger._owner_credit_calls) == 1
        call = ledger._owner_credit_calls[0]
        assert call["amount_cents"] == 75000, "the surplus, not the whole receipt"
        assert call["lot_id"] == receipt.lot_id
        assert call["owner_party_id"] == receipt.payer_party_id

    @pytest.mark.asyncio
    async def test_no_credit_when_the_receipt_is_fully_allocated(self, svc, ledger, outbox):
        """An exact payment must not create a zero-value credit row."""
        await svc.create_levy(make_levy_cmd(100000))
        receipt = await svc.record_payment(make_payment_cmd(100000))
        await svc.allocate_payment(
            AllocatePaymentCommand(scheme_ref=SCHEME_REF, receipt_id=receipt.receipt_id)
        )
        assert ledger._owner_credit_calls == []

    @pytest.mark.asyncio
    async def test_no_credit_when_the_receipt_is_short(self, svc, ledger, outbox):
        await svc.create_levy(make_levy_cmd(100000))
        receipt = await svc.record_payment(make_payment_cmd(60000))
        await svc.allocate_payment(
            AllocatePaymentCommand(scheme_ref=SCHEME_REF, receipt_id=receipt.receipt_id)
        )
        assert ledger._owner_credit_calls == []

    @pytest.mark.asyncio
    async def test_credit_when_nothing_is_charged_at_all(self, svc, ledger, outbox):
        """The extreme case the table exists for: a lot with zero open items. The whole
        receipt is credit — this is what read as $0.00 before."""
        receipt = await svc.record_payment(make_payment_cmd(50000))
        allocations = await svc.allocate_payment(
            AllocatePaymentCommand(scheme_ref=SCHEME_REF, receipt_id=receipt.receipt_id)
        )
        assert allocations == []
        assert ledger._owner_credit_calls[0]["amount_cents"] == 50000

    @pytest.mark.asyncio
    async def test_two_surplus_receipts_accumulate_on_one_balance(self, svc, ledger, outbox):
        """The Postgres upsert accumulates on (scheme, lot, owner, fund). The mock does
        too, so this test means the same thing against either."""
        receipt_a = await svc.record_payment(make_payment_cmd(30000))
        await svc.allocate_payment(
            AllocatePaymentCommand(scheme_ref=SCHEME_REF, receipt_id=receipt_a.receipt_id))
        receipt_b = await svc.record_payment(make_payment_cmd(20000))
        await svc.allocate_payment(
            AllocatePaymentCommand(scheme_ref=SCHEME_REF, receipt_id=receipt_b.receipt_id))

        assert len(ledger._owner_credit_calls) == 2
        assert sum(ledger._owner_credits.values()) == 50000
        assert len(ledger._owner_credits) == 1, "one balance per owner+lot, not one per receipt"
