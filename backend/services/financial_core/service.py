# @featuretrace:financial_core — The ONLY authorised writer to the PostgreSQL ledger.
# Layer: service
# Data flow: FastAPI routes → FinancialCoreAdapter → FinancialCoreService → finance.* + core.outbox (building-scoped).
# Related: backend/services/financial_core/adapter.py
#           backend/workers/outbox_relay.py
#           backend/alembic/versions/0003_finance_core_tables.py
"""FinancialCoreService — the ONLY authorised writer to the PostgreSQL ledger.

This is the application service layer (hexagonal architecture).
It orchestrates domain logic, calls plugin hooks, writes to Postgres,
and inserts outbox rows — all within a single database transaction.

RULE: No FastAPI route or any other service may call finance.* tables directly.
      All financial writes must go through this service.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from utils.helpers import create_audit_log

from services.financial_core.domain.entities import (
    AdjustmentResult,
    AllocatePaymentCommand,
    ArrearsLineItem,
    ArrearsReport,
    BulkUpsertHistoricalLevyItemsCommand,
    ChargeHistoricalLotFeeCommand,
    ChargeLotFeeCommand,
    CreateHistoricalLevyCommand,
    CreateLevyCommand,
    EntryDirection,
    EntryStatus,
    Expense,
    GenerateArrearsCommand,
    HistoricalPostingAuthorisation,
    JournalEntry,
    JournalLine,
    Levy,
    LotLevySpec,
    PaymentAllocation,
    PaymentChannel,
    PostGenesisJournalCommand,
    RebuildResult,
    ReconcileBankTransactionCommand,
    ReconcileResult,
    ReconciliationStatus,
    RebuildProvisionalLevyJournalsCommand,
    RecordAdjustmentCommand,
    RecordExpenseCommand,
    RecordHistoricalExpenseCommand,
    RecordHistoricalPaymentCommand,
    RecordPaymentCommand,
    Receipt,
    ReverseAllocationsCommand,
    ReverseAllocationsResult,
    ReverseAndReplacePostedLevyJournalsCommand,
    ReverseEntryCommand,
    ReverseResult,
    SchemeRef,
)
from services.financial_core.domain.exceptions import IdempotencyKeyCollision
from services.financial_core.domain.ports import (
    LedgerRepository,
    LegacyReadPort,
    OutboxPort,
)

logger = logging.getLogger(__name__)


def _stable_unique(values):
    """Return the unique items from `values` preserving first-seen order (used by
    reverse_allocations to dedup levy_item/receipt ids deterministically for
    recompute + tests)."""
    seen: set = set()
    out: list = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


# Category → expense GL account code. Keys must be pre-normalised the same way
# routers/finance.py::_normalise_category_name() does (lowercase, whitespace-
# collapsed, space-separated — NOT imported here to avoid a routers→services
# reverse dependency; the normaliser itself is a one-liner, duplicated below).
_EXPENSE_CATEGORY_GL_CODES: dict[str, str] = {
    "insurance": "5001",
    "repairs and maintenance": "5002",
    "management fees": "5003",
    "legal and compliance": "5004",
}
_DEFAULT_EXPENSE_GL_CODE = "5000"  # Administration Expenses — catch-all
_SINKING_EXPENSE_GL_CODE = "5100"  # Sinking Fund Expenses — migration 0087
_RECOVERED_FEES_GL_CODE = "4003"  # Recovered Fees & Collection Costs (income) — migration 0072


def _normalise_category_name(value: object) -> str:
    """Mirrors routers/finance.py::_normalise_category_name exactly."""
    return " ".join(str(value or "").strip().lower().split())


def _authorisation_metadata(authorisation: HistoricalPostingAuthorisation) -> dict:
    """Serialise a HistoricalPostingAuthorisation for a journal_entries.metadata payload."""
    return {
        "reconstruction_batch_id": str(authorisation.reconstruction_batch_id),
        "reason": authorisation.reason,
        "approved_by": str(authorisation.approved_by),
        "executed_by": str(authorisation.executed_by),
        "approval_reference": authorisation.approval_reference,
        "evidence_reference": authorisation.evidence_reference,
    }


class FinancialCoreService:
    """
    Application service — the single authorised writer to the financial ledger.

    Parameters
    ----------
    ledger_repo:
        Implementation of LedgerRepository (Postgres via SQLAlchemy).
    outbox_port:
        Implementation of OutboxPort (writes to core.outbox in same txn).
    plugin_registry:
        Optional PluginRegistry instance. When provided, plugins receive
        before/after hook calls for each command.
    legacy_read_port:
        Optional MongoDB read adapter for shadow validation only.
    """

    def __init__(
            self,
            ledger_repo: LedgerRepository,
            outbox_port: OutboxPort,
            plugin_registry=None,
            legacy_read_port: Optional[LegacyReadPort] = None,
    ) -> None:
        """Generated function header.

        Function: FinancialCoreService.__init__
        Path: backend/services/financial_core/service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._ledger = ledger_repo
        self._outbox = outbox_port
        self._plugins = plugin_registry
        self._legacy = legacy_read_port

    async def _verify_authorisation_identities(
            self, scheme_ref: SchemeRef, authorisation: HistoricalPostingAuthorisation,
    ) -> None:
        """Confirm approved_by and executed_by are both real, active users whose
        EFFECTIVE ROLE FOR THIS SCHEME carries genuine financial/governance authority
        — called at the top of every privileged historical handler (every command
        carrying a HistoricalPostingAuthorisation), before anything else runs.
        HistoricalPostingAuthorisation.__post_init__ only checks the two UUIDs are
        non-empty and mutually distinct; it cannot check who they actually are (a
        dataclass __post_init__ is synchronous, and this needs a DB lookup).

        Added 2026-08-06 after an audit found a real gap: a script's --executed-by
        UUID resolved to no user at all in core.users, and nothing caught it before
        13 adjustment journal entries were posted with an unverifiable second
        identity. Pulling the check up here, rather than leaving it as a bespoke
        check in one caller, closes it for every existing Historical* command
        (RecordHistoricalPaymentCommand, RecordHistoricalExpenseCommand,
        ChargeHistoricalLotFeeCommand, CreateHistoricalLevyCommand) as well as
        RecordAdjustmentCommand, including the ~8 existing production scripts that
        construct HistoricalPostingAuthorisation directly.

        AUDIT CORRECTION, same day: the first version of this check only verified
        tenant membership + is_active — see user_has_financial_authority's docstring
        for why that's insufficient (empirically proven against real data: a real,
        active `owner`-role user from this same tenant, with zero role assignment for
        this scheme, passed the old tenant-only check and correctly fails the
        rebuilt one).
        """
        for label, user_id in (
                ("approved_by", authorisation.approved_by),
                ("executed_by", authorisation.executed_by),
        ):
            if not await self._ledger.user_has_financial_authority(scheme_ref, user_id):
                raise ValueError(
                    f"HistoricalPostingAuthorisation.{label}={user_id} is not a real, active "
                    "user with financial/governance authority for this scheme — dual control "
                    "requires two verifiable, real, distinct, authorised people."
                )

    # ------------------------------------------------------------------
    # 1. create_levy
    # ------------------------------------------------------------------

    async def create_levy(
            self,
            cmd: CreateLevyCommand,
    ) -> list[Levy]:
        """Issue a levy run and create per-lot levy items with journal entries.

        Returns the list of created Levy objects.
        Writes are atomic: levy run + levy items + journal entry + outbox row.
        """
        if cmd.due_date < cmd.issue_date:
            raise ValueError("Levy due_date must be on or after issue_date")
        if not cmd.lot_levies:
            raise ValueError("create_levy requires at least one lot levy spec")

        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd

        resolved_period = await self._ledger.get_accounting_period_for_date(
            cmd.scheme_ref, enriched_cmd.issue_date
        )
        period_id = resolved_period.period_id
        levy_run_id = await self._ledger.save_levy_run(
            scheme_ref=cmd.scheme_ref,
            financial_year=enriched_cmd.financial_year,
            quarter_no=enriched_cmd.quarter_no,
            issue_date=enriched_cmd.issue_date,
            due_date=enriched_cmd.due_date,
            levy_run_type=enriched_cmd.levy_run_type,
        )

        levy_items: list[Levy] = []
        journal_lines: list[JournalLine] = []

        ar_account_id = await self._ledger.get_gl_account(cmd.scheme_ref, "1100")

        # Resolve the income GL account per-fund rather than always crediting
        # Admin's 4000 — a levy run against the sinking/capital-works fund
        # must credit 4001, not 4000, or the sinking levy silently posts as
        # admin income. A single run is expected to be single-fund (matches
        # every levy run created so far); mixed-fund lot_levies in one call
        # would be ambiguous, so that's rejected explicitly rather than
        # guessing which fund's account to use.
        distinct_fund_ids = {spec.fund_id for spec in enriched_cmd.lot_levies}
        if len(distinct_fund_ids) != 1:
            raise ValueError(
                f"create_levy requires all lot_levies to share one fund_id, got {len(distinct_fund_ids)}"
            )
        fund_type = await self._ledger.get_fund_type(cmd.scheme_ref, next(iter(distinct_fund_ids)))
        income_account_code = "4001" if fund_type in ("sinking", "capital_works") else "4000"
        levy_income_account_id = await self._ledger.get_gl_account(cmd.scheme_ref, income_account_code)

        for spec in enriched_cmd.lot_levies:
            item = Levy(
                levy_item_id=uuid.uuid4(),
                scheme_ref=cmd.scheme_ref,
                levy_run_id=levy_run_id,
                lot_id=spec.lot_id,
                owner_party_id=spec.owner_party_id,
                fund_id=spec.fund_id,
                principal_cents=spec.principal_cents,
                gst_cents=spec.gst_cents,
                due_date=enriched_cmd.due_date,
            )
            levy_items.append(item)

            total = spec.principal_cents + spec.gst_cents
            journal_lines.append(JournalLine(
                gl_account_id=ar_account_id,
                direction=EntryDirection.DEBIT,
                amount_cents=total,
                gst_cents=spec.gst_cents,
                lot_id=spec.lot_id,
                party_id=spec.owner_party_id,
                narration=f"Levy {enriched_cmd.financial_year} Q{enriched_cmd.quarter_no}",
            ))
            journal_lines.append(JournalLine(
                gl_account_id=levy_income_account_id,
                direction=EntryDirection.CREDIT,
                amount_cents=total,
                gst_cents=spec.gst_cents,
                lot_id=spec.lot_id,
                narration=f"Levy income {enriched_cmd.financial_year}",
            ))

        entry = JournalEntry(
            scheme_ref=cmd.scheme_ref,
            period_id=period_id,
            source_type="levy_run",
            source_reference=str(levy_run_id),
            narration=f"Levy run {enriched_cmd.financial_year} Q{enriched_cmd.quarter_no}",
            effective_on=enriched_cmd.issue_date,
            lines=journal_lines,
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
        )

        saved_entry = await self._ledger.save_journal_entry(entry)
        posted_entry = await self._ledger.post_journal_entry(
            saved_entry.journal_entry_id, cmd.scheme_ref
        )

        saved_items = await self._ledger.save_levy_items(levy_items)

        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="levy.run.created",
            payload={
                "levy_run_id": str(levy_run_id),
                "journal_entry_id": str(posted_entry.journal_entry_id),
                "financial_year": enriched_cmd.financial_year,
                "quarter_no": enriched_cmd.quarter_no,
                "lot_count": len(levy_items),
                "total_cents": sum(s.principal_cents + s.gst_cents for s in enriched_cmd.lot_levies),
            },
        )

        await self._run_plugin_hook("log_event", {"type": "levy.created", "run_id": str(levy_run_id)})
        logger.info("Levy run created: %s (%d lots)", levy_run_id, len(saved_items))
        return saved_items

    # ------------------------------------------------------------------
    # 1h. create_historical_levy — privileged closed-period reconstruction
    # ------------------------------------------------------------------

    async def create_historical_levy(self, cmd: CreateHistoricalLevyCommand) -> list[Levy]:
        """Historical-reconstruction-only variant of create_levy().

        Posts the ordinary per-lot levy CHARGE (levy_run + levy_items + AR
        debit / income credit journal) for a single year+fund+period across
        all lots — the receivable obligation
        `docs/finances/next-phase-financial-recontruction-demo-bank-eastgate-2021-2026.md`
        distinguishes from a bank receipt. This command never touches Demo
        Bank / demo_bank_transactions.

        The ONLY levy-charge posting path permitted to resolve into a
        `closed` accounting period — gated by a mandatory, dual-control-
        validated HistoricalPostingAuthorisation. Not reachable from any HTTP
        route: CreateHistoricalLevyCommand is never constructed by adapter.py
        or any router; only backend/scripts/historical_levy_charge_posting.py
        calls this directly (enforced by
        tests/backend/test_financial_core_service.py::
        TestHistoricalPathwayIsolation).

        Idempotency, two layers (see
        docs/finances/reconcilation-2021-2022-finances-plan02.md — a bare
        levy_run lookup-then-insert is not concurrency-safe by itself):

        1. `idempotency_key` (run-level — one key per year/fund/levy_type/
           period, never a per-lot key) is checked FIRST against
           `finance.journal_entries.je_idempotency_key_idx`, the real
           DB-enforced backstop. A replay whose stored journal already
           matches the requested totals returns the run's current items
           without posting anything new. If the stored journal exists but
           has zero levy_items (a prior attempt posted the journal, then
           crashed before `save_levy_items()`), posting RESUMES against that
           same already-posted journal_entry_id rather than posting a
           second journal.
        2. `find_or_create_levy_run()` (migration 0076's
           `levy_runs_natural_key_idx`, scoped by fund_id — finance.levy_runs
           has no fund column for the live create_levy() path) is the
           administrative safeguard for the run itself, with an
           insert-then-fetch-on-conflict fallback at the repository layer,
           not a bare lookup-then-insert race.
        """
        if cmd.due_date < cmd.issue_date:
            raise ValueError("create_historical_levy due_date must be on or after issue_date")
        if not cmd.lot_levies:
            raise ValueError("create_historical_levy requires at least one lot levy spec")

        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd
        # See the other 4 Historical* handlers for why this checks enriched_cmd, not cmd.
        await self._verify_authorisation_identities(enriched_cmd.scheme_ref, enriched_cmd.authorisation)

        distinct_fund_ids = {spec.fund_id for spec in enriched_cmd.lot_levies}
        if len(distinct_fund_ids) != 1:
            raise ValueError(
                f"create_historical_levy requires all lot_levies to share one fund_id, got {len(distinct_fund_ids)}"
            )
        fund_id = next(iter(distinct_fund_ids))
        expected_total_cents = sum(spec.principal_cents + spec.gst_cents for spec in enriched_cmd.lot_levies)

        posted_entry = None
        if enriched_cmd.idempotency_key:
            existing_journal_id = await self._ledger.find_journal_entry_id_by_idempotency_key(
                cmd.scheme_ref, enriched_cmd.idempotency_key
            )
            if existing_journal_id:
                existing_entry = await self._ledger.get_journal_entry(existing_journal_id, cmd.scheme_ref)
                if existing_entry.debit_total_cents != expected_total_cents or existing_entry.effective_on != enriched_cmd.issue_date:
                    raise IdempotencyKeyCollision(
                        enriched_cmd.idempotency_key, existing_journal_id,
                        f"expected amount_cents={expected_total_cents} effective_on={enriched_cmd.issue_date}, "
                        f"existing journal has amount_cents={existing_entry.debit_total_cents} "
                        f"effective_on={existing_entry.effective_on}",
                    )
                run_id_from_journal = UUID(existing_entry.source_reference)
                existing_items = await self._ledger.get_levy_items_for_run(cmd.scheme_ref, run_id_from_journal)
                if existing_items:
                    logger.info(
                        "Historical levy replay detected (idempotency_key=%s): run=%s already has "
                        "%d item(s), no-op", enriched_cmd.idempotency_key, run_id_from_journal, len(existing_items),
                    )
                    return existing_items
                # Journal posted, items missing — resume under the SAME journal/run rather than
                # posting a second journal (which the DB's idempotency_key uniqueness would
                # reject anyway).
                logger.warning(
                    "Historical levy resume: journal %s (idempotency_key=%s) already posted with "
                    "zero levy_items — completing the interrupted post now.",
                    existing_journal_id, enriched_cmd.idempotency_key,
                )
                posted_entry = existing_entry
                levy_run_id = run_id_from_journal

        resolved_period = await self._ledger.get_accounting_period_for_date(
            cmd.scheme_ref, enriched_cmd.issue_date, permitted_statuses=frozenset({"open", "closed"}),
        )

        if posted_entry is None:
            levy_run_id, _created = await self._ledger.find_or_create_levy_run(
                scheme_ref=cmd.scheme_ref,
                financial_year=enriched_cmd.financial_year,
                quarter_no=enriched_cmd.quarter_no,
                levy_run_type=enriched_cmd.levy_run_type,
                fund_id=fund_id,
                issue_date=enriched_cmd.issue_date,
                due_date=enriched_cmd.due_date,
            )
            existing_items = await self._ledger.get_levy_items_for_run(cmd.scheme_ref, levy_run_id)
            if existing_items:
                logger.info(
                    "Historical levy replay detected (run natural key): run=%s already has %d "
                    "item(s), no-op", levy_run_id, len(existing_items),
                )
                return existing_items

        ar_account_id = await self._ledger.get_gl_account(cmd.scheme_ref, "1100")
        fund_type = await self._ledger.get_fund_type(cmd.scheme_ref, fund_id)
        income_account_code = "4001" if fund_type in ("sinking", "capital_works") else "4000"
        levy_income_account_id = await self._ledger.get_gl_account(cmd.scheme_ref, income_account_code)

        levy_items: list[Levy] = []
        journal_lines: list[JournalLine] = []
        unresolved_lot_ids: list[str] = []

        for spec in enriched_cmd.lot_levies:
            if spec.owner_resolution_status != "resolved":
                unresolved_lot_ids.append(str(spec.lot_id))

            item = Levy(
                levy_item_id=uuid.uuid4(),
                scheme_ref=cmd.scheme_ref,
                levy_run_id=levy_run_id,
                lot_id=spec.lot_id,
                owner_party_id=spec.owner_party_id,
                fund_id=spec.fund_id,
                principal_cents=spec.principal_cents,
                gst_cents=spec.gst_cents,
                due_date=enriched_cmd.due_date,
                owner_resolution_status=spec.owner_resolution_status,
            )
            levy_items.append(item)

            total = spec.principal_cents + spec.gst_cents
            narration = f"Historical levy {enriched_cmd.financial_year} Q{enriched_cmd.quarter_no}"
            if spec.owner_resolution_status != "resolved":
                narration += " [unresolved owner — placeholder party]"
            journal_lines.append(JournalLine(
                gl_account_id=ar_account_id,
                direction=EntryDirection.DEBIT,
                amount_cents=total,
                gst_cents=spec.gst_cents,
                lot_id=spec.lot_id,
                party_id=spec.owner_party_id,
                narration=narration,
            ))
            journal_lines.append(JournalLine(
                gl_account_id=levy_income_account_id,
                direction=EntryDirection.CREDIT,
                amount_cents=total,
                gst_cents=spec.gst_cents,
                lot_id=spec.lot_id,
                narration=f"Historical levy income {enriched_cmd.financial_year}",
            ))

        if posted_entry is None:
            entry_metadata = {
                "levy_type": enriched_cmd.levy_type,
                "historical_authorisation": _authorisation_metadata(enriched_cmd.authorisation),
                "unresolved_owner_lot_ids": unresolved_lot_ids,
            }
            entry = JournalEntry(
                scheme_ref=cmd.scheme_ref,
                period_id=resolved_period.period_id,
                source_type="historical_levy_run",
                source_reference=str(levy_run_id),
                narration=f"Historical levy run {enriched_cmd.financial_year} Q{enriched_cmd.quarter_no}",
                effective_on=enriched_cmd.issue_date,
                lines=journal_lines,
                idempotency_key=enriched_cmd.idempotency_key,
                is_test_data=enriched_cmd.is_test_data,
                posted_by=enriched_cmd.authorisation.executed_by,
                approved_by=enriched_cmd.authorisation.approved_by,
                metadata=entry_metadata,
            )
            saved_entry = await self._ledger.save_journal_entry(entry)
            posted_entry = await self._ledger.post_journal_entry(
                saved_entry.journal_entry_id, cmd.scheme_ref
            )

        for item in levy_items:
            item.journal_entry_id = posted_entry.journal_entry_id
        saved_items = await self._ledger.save_levy_items(levy_items)

        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="levy.historical_run.created",
            payload={
                "levy_run_id": str(levy_run_id),
                "journal_entry_id": str(posted_entry.journal_entry_id),
                "financial_year": enriched_cmd.financial_year,
                "quarter_no": enriched_cmd.quarter_no,
                "levy_type": enriched_cmd.levy_type,
                "lot_count": len(levy_items),
                "unresolved_owner_lot_count": len(unresolved_lot_ids),
                "total_cents": expected_total_cents,
            },
        )

        await self._publish_historical_authorisation_events(
            handler="create_historical_levy",
            scheme_ref=cmd.scheme_ref,
            journal_entry_id=posted_entry.journal_entry_id,
            resolved_period=resolved_period,
            effective_on=enriched_cmd.issue_date,
            amount_cents=expected_total_cents,
            idempotency_key=enriched_cmd.idempotency_key,
            bank_transaction_id=None,
            authorisation=enriched_cmd.authorisation,
        )

        await self._run_plugin_hook("log_event", {"type": "levy.historical_created", "run_id": str(levy_run_id)})
        logger.info(
            "Historical levy run created: %s (%d lots, %d unresolved owner)",
            levy_run_id, len(saved_items), len(unresolved_lot_ids),
        )
        return saved_items

    # ------------------------------------------------------------------
    # 2. record_payment
    # ------------------------------------------------------------------

    async def record_payment(self, cmd: RecordPaymentCommand) -> Receipt:
        """Record an inbound payment receipt. Does NOT allocate — call allocate_payment next.

        Returns the created Receipt.
        """
        # Same validation order as before the _post_payment_journal extraction
        # (amount -> idempotency_key -> posted_by -> approved_by) -- callers
        # match on the specific ValueError message, so this order is load-bearing.
        if cmd.amount_cents <= 0:
            raise ValueError(f"Payment amount must be positive, got {cmd.amount_cents}")
        if not cmd.idempotency_key:
            raise ValueError("Payment idempotency_key is required")
        if not cmd.posted_by:
            raise ValueError("Payment posted_by is required")
        if not cmd.approved_by:
            raise ValueError("Payment approved_by is required")

        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd

        return await self._post_payment_journal(
            scheme_ref=cmd.scheme_ref,
            payer_party_id=enriched_cmd.payer_party_id,
            lot_id=enriched_cmd.lot_id,
            channel=enriched_cmd.channel,
            received_on=enriched_cmd.received_on,
            amount_cents=enriched_cmd.amount_cents,
            trust_account_id=enriched_cmd.trust_account_id,
            bank_transaction_id=enriched_cmd.bank_transaction_id,
            external_reference=enriched_cmd.external_reference,
            reconstruction_batch_id=enriched_cmd.reconstruction_batch_id,
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
            posted_by=enriched_cmd.posted_by,
            approved_by=enriched_cmd.approved_by,
            metadata=enriched_cmd.metadata,
            authorisation=None,
        )

    async def record_historical_payment(self, cmd: RecordHistoricalPaymentCommand) -> Receipt:
        """Historical-reconstruction-only variant of record_payment().

        The ONLY payment-posting path permitted to resolve into a `closed`
        accounting period — gated by a mandatory, dual-control-validated
        HistoricalPostingAuthorisation. Not reachable from any HTTP route:
        RecordHistoricalPaymentCommand is never constructed by adapter.py or
        any router; only privileged scripts (e.g. a dedicated East Gate
        2021-2025 levy-payment backfill script) call this directly.
        """
        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd
        # Verify the ENRICHED command's authorisation, not the raw cmd's — this is
        # what actually gets used below (posted_by/approved_by, metadata). No
        # registered plugin currently touches .authorisation in enrich_command (only
        # act_plugin.py exists, and it only touches CreateLevyCommand.metadata), but
        # verifying pre-enrichment data would silently stop being correct the moment
        # one did.
        await self._verify_authorisation_identities(enriched_cmd.scheme_ref, enriched_cmd.authorisation)

        return await self._post_payment_journal(
            scheme_ref=cmd.scheme_ref,
            payer_party_id=enriched_cmd.payer_party_id,
            lot_id=enriched_cmd.lot_id,
            channel=enriched_cmd.channel,
            received_on=enriched_cmd.received_on,
            amount_cents=enriched_cmd.amount_cents,
            trust_account_id=enriched_cmd.trust_account_id,
            bank_transaction_id=enriched_cmd.bank_transaction_id,
            external_reference=enriched_cmd.external_reference,
            reconstruction_batch_id=enriched_cmd.authorisation.reconstruction_batch_id,
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
            posted_by=enriched_cmd.authorisation.executed_by,
            approved_by=enriched_cmd.authorisation.approved_by,
            metadata=enriched_cmd.metadata,
            authorisation=enriched_cmd.authorisation,
        )

    async def _post_payment_journal(
            self,
            *,
            scheme_ref,
            payer_party_id: UUID,
            lot_id: UUID,
            channel,
            received_on: date,
            amount_cents: int,
            trust_account_id: Optional[UUID],
            bank_transaction_id: Optional[UUID],
            external_reference: Optional[str],
            reconstruction_batch_id: Optional[UUID],
            idempotency_key: Optional[str],
            is_test_data: bool,
            posted_by: Optional[UUID],
            approved_by: Optional[UUID],
            metadata: dict,
            authorisation: Optional[HistoricalPostingAuthorisation],
    ) -> Receipt:
        """Shared implementation for record_payment() and record_historical_payment().

        Mirrors _post_expense_journal()'s authorisation-gated period resolution:
        permitted_statuses only includes 'closed' when a HistoricalPostingAuthorisation
        is supplied, so an ordinary record_payment() call still fails fast against a
        closed period exactly as before this refactor.
        """
        if amount_cents <= 0:
            raise ValueError(f"Payment amount must be positive, got {amount_cents}")
        if not idempotency_key:
            raise ValueError("Payment idempotency_key is required")

        # Cross-pipeline duplicate guard (GAP-FIN-045). A bank_transaction_id is the
        # real-world identity of one cash event, so at most one active receipt may
        # exist per (scheme, bank_transaction_id, lot, amount). Two posting paths —
        # the live match-promotion route and the GAP-FIN-031 derived-receipt backfill —
        # key their idempotency on different strings, so neither's own idempotency_key
        # check sees the other's row; this shared-key check does. Returns the existing
        # receipt idempotently rather than posting a twin (skipped when there is no
        # bank_transaction_id, e.g. manual receipts or reconstructions with no bank line).
        if bank_transaction_id is not None:
            finder = getattr(self._ledger, "find_active_receipt_by_bank_transaction", None)
            if finder is not None:
                existing_receipt = await finder(
                    scheme_ref, bank_transaction_id, lot_id, amount_cents
                )
                if existing_receipt is not None:
                    logger.warning(
                        "Duplicate payment suppressed: active receipt %s already exists "
                        "for bank_transaction_id=%s lot=%s amount=%d cents — returning it "
                        "idempotently instead of posting a twin.",
                        existing_receipt.receipt_id, bank_transaction_id, lot_id, amount_cents,
                    )
                    return existing_receipt

        permitted_statuses = frozenset({"open", "closed"}) if authorisation is not None else frozenset({"open"})
        resolved_period = await self._ledger.get_accounting_period_for_date(
            scheme_ref, received_on, permitted_statuses=permitted_statuses
        )
        bank_account_id = await self._ledger.get_gl_account(scheme_ref, "1010")
        ar_account_id = await self._ledger.get_gl_account(scheme_ref, "1100")

        entry_metadata = dict(metadata)
        if authorisation is not None:
            entry_metadata["historical_authorisation"] = _authorisation_metadata(authorisation)

        entry = JournalEntry(
            scheme_ref=scheme_ref,
            period_id=resolved_period.period_id,
            source_type="payment_receipt",
            narration=f"Payment from lot {lot_id}",
            effective_on=received_on,
            lines=[
                JournalLine(
                    gl_account_id=bank_account_id,
                    direction=EntryDirection.DEBIT,
                    amount_cents=amount_cents,
                    lot_id=lot_id,
                    party_id=payer_party_id,
                ),
                JournalLine(
                    gl_account_id=ar_account_id,
                    direction=EntryDirection.CREDIT,
                    amount_cents=amount_cents,
                    lot_id=lot_id,
                    party_id=payer_party_id,
                ),
            ],
            idempotency_key=idempotency_key,
            is_test_data=is_test_data,
            posted_by=posted_by,
            approved_by=approved_by,
            metadata=entry_metadata,
        )

        saved_entry = await self._ledger.save_journal_entry(entry)
        posted_entry = await self._ledger.post_journal_entry(
            saved_entry.journal_entry_id, scheme_ref
        )

        receipt = Receipt(
            receipt_id=uuid.uuid4(),
            scheme_ref=scheme_ref,
            payer_party_id=payer_party_id,
            lot_id=lot_id,
            channel=channel,
            received_on=received_on,
            amount_cents=amount_cents,
            external_reference=external_reference,
            bank_transaction_id=bank_transaction_id,
            trust_account_id=trust_account_id,
            journal_entry_id=posted_entry.journal_entry_id,
            # 2026-07-17 audit fix: these two fields existed on Receipt/
            # finance.receipts/save_receipt() since Phase 2 but were never
            # actually passed through here, so reconstruction_batch_id was
            # always None on every receipt regardless of its source.
            reconstruction_batch_id=reconstruction_batch_id,
            metadata=metadata,
        )
        saved_receipt = await self._ledger.save_receipt(receipt)

        await self._outbox.publish(
            scheme_ref=scheme_ref,
            event_type="payment.received",
            payload={
                "receipt_id": str(saved_receipt.receipt_id),
                "lot_id": str(lot_id),
                "amount_cents": amount_cents,
                "channel": channel.value,
                "received_on": received_on.isoformat(),
            },
        )

        if authorisation is not None:
            await self._publish_historical_authorisation_events(
                handler="record_payment",
                scheme_ref=scheme_ref,
                journal_entry_id=posted_entry.journal_entry_id,
                resolved_period=resolved_period,
                effective_on=received_on,
                amount_cents=amount_cents,
                idempotency_key=idempotency_key,
                bank_transaction_id=bank_transaction_id,
                authorisation=authorisation,
            )

        logger.info(
            "Payment recorded: receipt=%s lot=%s amount=%d cents",
            saved_receipt.receipt_id, lot_id, amount_cents,
        )
        return saved_receipt

    # ------------------------------------------------------------------
    # 3. allocate_payment
    # ------------------------------------------------------------------

    async def allocate_payment(self, cmd: AllocatePaymentCommand) -> list[PaymentAllocation]:
        """Allocate a receipt across open levy items using the specified order.

        allocation_order defaults to ["levy", "interest", "costs"] — plugins can override.
        Returns the list of created allocations.
        """
        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd

        receipt = await self._ledger.get_receipt(enriched_cmd.receipt_id, enriched_cmd.scheme_ref)
        open_items = await self._ledger.get_open_levy_items(enriched_cmd.scheme_ref, receipt.lot_id)
        already_allocated = await self._ledger.get_allocated_total_for_receipt(receipt.receipt_id)

        # Top up the remainder only. Without this, a receipt that already carries a
        # partial allocation (e.g. a stray cross-period allocation from historical
        # reconstruction) would have its FULL amount re-allocated on top of what's
        # already posted — silently double-counting the already-allocated portion, and
        # making this command unsafe to call twice on the same receipt. Confirmed live
        # 2026-08-09 on East Gate UA030: a 2021 receipt with $101.94 already allocated
        # (to an unrelated 2026 item) left its remaining $181.90 invisible to both "fully
        # unallocated" and "already allocated" bookkeeping in the GAP-FIN-046 Bug-B
        # repair, which calls this command per receipt_id.
        remaining = receipt.amount_cents - already_allocated
        allocations: list[PaymentAllocation] = []

        for item in open_items:
            if remaining <= 0:
                break
            allocatable = min(remaining, item.outstanding_cents)
            if allocatable <= 0:
                continue
            alloc = PaymentAllocation(
                allocation_id=uuid.uuid4(),
                receipt_id=receipt.receipt_id,
                levy_item_id=item.levy_item_id,
                allocation_type="levy",
                allocated_cents=allocatable,
                tenant_id=enriched_cmd.scheme_ref.tenant_id,
            )
            allocations.append(alloc)
            remaining -= allocatable

        await self._ledger.save_allocations(allocations)

        # GAP-FIN-036 — the surplus used to be dropped here.
        #
        # The loop above stops when it runs out of OPEN levy items, and whatever is left
        # of the receipt simply fell out of scope. The receipt and its journal entry were
        # real (cash genuinely received, correctly recorded at the GL control account),
        # but `finance.levy_items` — which every per-lot balance query sums — had no row
        # capable of representing money paid beyond what was ever charged. A genuinely
        # in-credit owner therefore read as $0.00, and the only way anything downstream
        # could see the credit was to re-derive it as GREATEST(0, received - levied),
        # which is a second source of truth and drifts the moment a receipt is reversed
        # or retired.
        #
        # Recording it makes the credit a stored fact with a source journal entry behind
        # it, rather than an inference.
        credit_cents = max(0, remaining)
        if credit_cents:
            await self._ledger.upsert_owner_credit(
                scheme_ref=enriched_cmd.scheme_ref,
                lot_id=receipt.lot_id,
                owner_party_id=receipt.payer_party_id,
                amount_cents=credit_cents,
                source_journal_entry_id=receipt.journal_entry_id,
            )
            logger.info(
                "Receipt %s exceeded open levy items by %d cents on lot %s — recorded as "
                "unapplied owner credit", receipt.receipt_id, credit_cents, receipt.lot_id,
            )

        await self._outbox.publish(
            scheme_ref=enriched_cmd.scheme_ref,
            event_type="payment.allocated",
            payload={
                "receipt_id": str(enriched_cmd.receipt_id),
                "allocation_count": len(allocations),
                "total_allocated_cents": sum(a.allocated_cents for a in allocations),
            },
        )

        return allocations

    # ------------------------------------------------------------------
    # 4. generate_arrears
    # ------------------------------------------------------------------

    async def generate_arrears(self, cmd: GenerateArrearsCommand) -> ArrearsReport:
        """Generate an arrears report as of a given date.

        Fetches all overdue levy items and computes per-lot totals.
        """
        overdue = await self._ledger.get_overdue_levy_items(cmd.scheme_ref, cmd.as_of_date)

        items: list[ArrearsLineItem] = []
        for levy in overdue:
            days = (cmd.as_of_date - levy.due_date).days if levy.due_date else 0
            items.append(ArrearsLineItem(
                lot_id=levy.lot_id,
                lot_number="",
                owner_name="",
                principal_overdue_cents=levy.outstanding_cents,
                interest_cents=levy.interest_cents,
                recovery_costs_cents=levy.recovery_costs_cents,
                days_overdue=max(0, days),
            ))

        report = ArrearsReport(
            scheme_ref=cmd.scheme_ref,
            as_of_date=cmd.as_of_date,
            items=items,
        )

        await self._run_plugin_hook("log_event", {
            "type": "arrears.report.generated",
            "total_cents": report.total_outstanding_cents,
        })

        return report

    # ------------------------------------------------------------------
    # 5. reverse_entry
    # ------------------------------------------------------------------

    async def reverse_entry(self, cmd: ReverseEntryCommand) -> ReverseResult:
        """Create a reversal journal entry for a posted entry.

        The original entry is NOT modified (immutability invariant).
        A new entry with mirrored debit/credit lines is created and posted.
        """
        original = await self._ledger.get_journal_entry(
            cmd.journal_entry_id, cmd.scheme_ref
        )
        if original.journal_entry_id is None:
            raise ValueError(f"Journal entry {cmd.journal_entry_id} not found")

        effective = cmd.effective_on or date.today()
        resolved_period = await self._ledger.get_accounting_period_for_date(cmd.scheme_ref, effective)
        period_id = resolved_period.period_id

        reversal_lines = [
            JournalLine(
                gl_account_id=line.gl_account_id,
                direction=(
                    EntryDirection.CREDIT
                    if line.direction == EntryDirection.DEBIT
                    else EntryDirection.DEBIT
                ),
                amount_cents=line.amount_cents,
                gst_cents=line.gst_cents,
                lot_id=line.lot_id,
                party_id=line.party_id,
                narration=f"Reversal: {line.narration or ''}",
            )
            for line in original.lines
        ]

        reversal_entry = JournalEntry(
            scheme_ref=cmd.scheme_ref,
            period_id=period_id,
            source_type="reversal",
            source_reference=str(cmd.journal_entry_id),
            narration=f"Reversal of {cmd.journal_entry_id}: {cmd.reason}",
            effective_on=effective,
            lines=reversal_lines,
            reversal_of_id=cmd.journal_entry_id,
            idempotency_key=cmd.idempotency_key,
        )

        saved = await self._ledger.save_journal_entry(reversal_entry)
        posted = await self._ledger.post_journal_entry(saved.journal_entry_id, cmd.scheme_ref)

        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="journal.entry.reversed",
            payload={
                "original_entry_id": str(cmd.journal_entry_id),
                "reversal_entry_id": str(posted.journal_entry_id),
                "reason": cmd.reason,
            },
        )

        logger.info(
            "Journal entry reversed: original=%s reversal=%s",
            cmd.journal_entry_id, posted.journal_entry_id,
        )

        # GAP-FIN-057 structural fix — cascade the receipt-allocation unwind.
        # reverse_entry() only mirrors the GL journal; it never touched
        # receipt_allocations / levy_items.paid_cents, so a reversed payment-receipt
        # historically left its allocations in place and paid_cents overstated (the exact
        # bug that produced East Gate's phantom "paid"). When the reversed entry is
        # receipt-sourced, immediately unwind just that receipt's allocations — scoped to
        # the receipt(s) whose journal we just reversed, recompute-from-survivors, so it
        # is safe and idempotent. Runs in the same transaction/tenant context as the
        # reversal above. See tasks/GAP-FIN-057-reversal-command-spec.md §3.
        cascade: Optional[ReverseAllocationsResult] = None
        if cmd.cascade_allocations:
            receipt_ids = await self._ledger.get_receipt_ids_for_journal_entry(
                cmd.scheme_ref, cmd.journal_entry_id,
            )
            if receipt_ids:
                cascade = await self.reverse_allocations(
                    ReverseAllocationsCommand(
                        scheme_ref=cmd.scheme_ref,
                        reason=f"cascade of reverse_entry {cmd.journal_entry_id}: {cmd.reason}",
                        receipt_ids=receipt_ids,
                        idempotency_key=(
                            f"{cmd.idempotency_key}:alloc_cascade"
                            if cmd.idempotency_key else None
                        ),
                    )
                )

        return ReverseResult(
            original_entry_id=cmd.journal_entry_id,
            reversal_entry_id=posted.journal_entry_id,
            reason=cmd.reason,
            cascade=cascade,
        )

    # ------------------------------------------------------------------
    # 5b. reverse_allocations (GAP-FIN-057)
    # ------------------------------------------------------------------

    async def reverse_allocations(self, cmd: ReverseAllocationsCommand) -> ReverseAllocationsResult:
        """Unwind the receipt_allocations left behind by a journal-only reverse_entry().

        See ReverseAllocationsCommand's docstring and
        tasks/GAP-FIN-057-reversal-command-spec.md. Posts NO journal (the GL was
        already corrected by the earlier reverse_entry()); it deletes the stale
        allocations whose parent receipt's journal has a posted reversal, then
        recomputes the affected levy_items.paid_cents from the SURVIVING allocations,
        so arrears/collection stop reading reversed cash as applied.

        Idempotent: a replay finds no reversed-receipt allocations and returns a
        result whose is_noop is True — no delete, no recompute, no event.
        """
        stale = await self._ledger.get_allocations_for_reversed_receipts(
            cmd.scheme_ref, cmd.receipt_ids,
        )
        if not stale:
            logger.info(
                "reverse_allocations: nothing to reverse for scheme=%s (idempotent no-op)",
                cmd.scheme_ref.scheme_id,
            )
            return ReverseAllocationsResult(
                reversed_receipt_ids=[],
                deleted_allocation_ids=[],
                reversed_allocated_cents=0,
                affected_items=[],
            )

        allocation_ids = [a.allocation_id for a in stale]
        total_cents = sum(a.allocated_cents for a in stale)
        # Distinct affected levy_items / receipts, order-stable for deterministic
        # recompute + tests.
        affected_item_ids = _stable_unique(a.levy_item_id for a in stale)
        reversed_receipt_ids = _stable_unique(a.receipt_id for a in stale)

        # Order matters: delete the stale allocations first, THEN recompute paid_cents
        # from the survivors (recompute, never decrement — idempotent by construction).
        await self._ledger.delete_allocations(allocation_ids)
        affected_items = await self._ledger.recompute_paid_cents(
            cmd.scheme_ref, affected_item_ids,
        )

        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="payment.allocation_reversed",
            payload={
                "reason": cmd.reason,
                "reversed_receipt_ids": [str(r) for r in reversed_receipt_ids],
                "deleted_allocation_count": len(allocation_ids),
                "reversed_allocated_cents": total_cents,
                "affected_levy_item_ids": [str(i) for i in affected_item_ids],
            },
        )
        logger.info(
            "reverse_allocations: reversed %d allocation(s) (%d cents) across %d receipt(s), "
            "recomputed %d levy_item(s) for scheme=%s",
            len(allocation_ids), total_cents, len(reversed_receipt_ids),
            len(affected_item_ids), cmd.scheme_ref.scheme_id,
        )
        return ReverseAllocationsResult(
            reversed_receipt_ids=reversed_receipt_ids,
            deleted_allocation_ids=allocation_ids,
            reversed_allocated_cents=total_cents,
            affected_items=affected_items,
        )

    # ------------------------------------------------------------------
    # 5a. record_adjustment
    # ------------------------------------------------------------------

    async def record_adjustment(self, cmd: RecordAdjustmentCommand) -> AdjustmentResult:
        """Post an evidenced correction against a lot's recorded paid balance.

        See RecordAdjustmentCommand's docstring for when this applies (a proven
        over-statement spread across years of reconstruction, with no single receipt
        safely identifiable to reverse) and why it is distinct from reverse_entry
        (mirrors one specific existing entry) and record_historical_payment (posts a
        new positive receipt).

        Posts DEBIT ar / CREDIT bank for cmd.amount_cents — the mirror image of
        _post_payment_journal's DEBIT bank / CREDIT ar, since this undoes money that
        was recorded as received but wasn't — then decrements finance.levy_items.
        paid_cents for cmd.financial_year via the ledger repo (most-recently-due item
        first; raises rather than partially applying if the year's recorded paid_cents
        can't cover the full amount — see decrement_paid_for_year's docstring).

        Always historical-authorisation-gated (same dual-control, privileged-script-
        only pattern as record_historical_payment/record_historical_expense) — there
        is no non-historical variant, since an adjustment is by definition a
        correction to something already posted, never a live/day-of transaction a
        router would construct.
        """
        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd
        # Verify the ENRICHED command's authorisation, not the raw cmd's — this is
        # what actually gets used below (posted_by/approved_by, metadata). No
        # registered plugin currently touches .authorisation in enrich_command (only
        # act_plugin.py exists, and it only touches CreateLevyCommand.metadata), but
        # verifying pre-enrichment data would silently stop being correct the moment
        # one did.
        await self._verify_authorisation_identities(enriched_cmd.scheme_ref, enriched_cmd.authorisation)

        effective = enriched_cmd.effective_on or date.today()
        resolved_period = await self._ledger.get_accounting_period_for_date(
            enriched_cmd.scheme_ref, effective, permitted_statuses=frozenset({"open", "closed"}),
        )
        bank_account_id = await self._ledger.get_gl_account(enriched_cmd.scheme_ref, "1010")
        ar_account_id = await self._ledger.get_gl_account(enriched_cmd.scheme_ref, "1100")

        entry_metadata = dict(enriched_cmd.metadata)
        entry_metadata["historical_authorisation"] = _authorisation_metadata(enriched_cmd.authorisation)

        entry = JournalEntry(
            scheme_ref=enriched_cmd.scheme_ref,
            period_id=resolved_period.period_id,
            source_type="reconstruction_adjustment",
            narration=(
                f"Adjustment for lot {enriched_cmd.lot_id}, FY{enriched_cmd.financial_year}: "
                f"{enriched_cmd.authorisation.reason}"
            ),
            effective_on=effective,
            lines=[
                JournalLine(
                    gl_account_id=ar_account_id,
                    direction=EntryDirection.DEBIT,
                    amount_cents=enriched_cmd.amount_cents,
                    lot_id=enriched_cmd.lot_id,
                ),
                JournalLine(
                    gl_account_id=bank_account_id,
                    direction=EntryDirection.CREDIT,
                    amount_cents=enriched_cmd.amount_cents,
                    lot_id=enriched_cmd.lot_id,
                ),
            ],
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
            posted_by=enriched_cmd.authorisation.executed_by,
            approved_by=enriched_cmd.authorisation.approved_by,
            metadata=entry_metadata,
        )

        saved_entry = await self._ledger.save_journal_entry(entry)
        posted_entry = await self._ledger.post_journal_entry(saved_entry.journal_entry_id, enriched_cmd.scheme_ref)

        items_adjusted = await self._ledger.decrement_paid_for_year(
            enriched_cmd.scheme_ref, enriched_cmd.lot_id, enriched_cmd.financial_year, enriched_cmd.amount_cents,
        )

        await self._outbox.publish(
            scheme_ref=enriched_cmd.scheme_ref,
            event_type="financial.adjustment.recorded",
            payload={
                "journal_entry_id": str(posted_entry.journal_entry_id),
                "lot_id": str(enriched_cmd.lot_id),
                "amount_cents": enriched_cmd.amount_cents,
                "financial_year": enriched_cmd.financial_year,
                "items_adjusted": [str(i.levy_item_id) for i in items_adjusted],
            },
        )
        await self._publish_historical_authorisation_events(
            handler="record_adjustment",
            scheme_ref=enriched_cmd.scheme_ref,
            journal_entry_id=posted_entry.journal_entry_id,
            resolved_period=resolved_period,
            effective_on=effective,
            amount_cents=enriched_cmd.amount_cents,
            idempotency_key=enriched_cmd.idempotency_key,
            bank_transaction_id=None,
            authorisation=enriched_cmd.authorisation,
        )

        logger.info(
            "Adjustment recorded: journal=%s lot=%s amount=%d cents FY%s (%d levy_item(s) touched)",
            posted_entry.journal_entry_id, enriched_cmd.lot_id, enriched_cmd.amount_cents,
            enriched_cmd.financial_year, len(items_adjusted),
        )
        return AdjustmentResult(
            journal_entry_id=posted_entry.journal_entry_id,
            lot_id=enriched_cmd.lot_id,
            amount_cents=enriched_cmd.amount_cents,
            financial_year=enriched_cmd.financial_year,
            items_adjusted=items_adjusted,
        )

    # ------------------------------------------------------------------
    # 6. reconcile_bank_transaction
    # ------------------------------------------------------------------

    async def reconcile_bank_transaction(
            self, cmd: ReconcileBankTransactionCommand
    ) -> ReconcileResult:
        """Match a bank statement transaction to a receipt.

        Sets the bank_transaction.reconciliation_status to 'matched'.
        If amounts differ, records difference as candidate (manual review needed).
        """
        receipt = await self._ledger.get_receipt(cmd.receipt_id, cmd.scheme_ref)

        # Difference would be loaded from the bank_transaction row by the repo
        # For now, assume zero difference (perfect match path)
        difference_cents = 0
        status = ReconciliationStatus.MATCHED

        result = ReconcileResult(
            bank_transaction_id=cmd.bank_transaction_id,
            receipt_id=cmd.receipt_id,
            status=status,
            difference_cents=difference_cents,
        )
        await self._ledger.save_reconcile_result(result)

        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="bank.transaction.reconciled",
            payload={
                "bank_transaction_id": str(cmd.bank_transaction_id),
                "receipt_id": str(cmd.receipt_id),
                "status": status.value,
                "difference_cents": difference_cents,
            },
        )

        return result

    # ------------------------------------------------------------------
    # 7. post_genesis_journal — Initialize fund with opening balance
    # ------------------------------------------------------------------

    async def post_genesis_journal(
            self, cmd: PostGenesisJournalCommand
    ) -> JournalEntry:
        """Post a genesis (opening balance) journal entry for a fund.

        Genesis journals are the first entry posted for a fund. They initialize
        the fund's ledger with an opening balance by creating a balanced entry.
        Direction is sign-dependent:
        - Positive opening balance (fund in credit):
            Debit: assets:bank (account code 1000)
            Credit: equity:opening_balances_clearing (account code 3100)
        - Negative opening balance (fund in deficit):
            Debit: equity:opening_balances_clearing (account code 3100)
            Credit: assets:bank (account code 1000)
        abs(opening_balance_cents) is used as the line amount in both cases.

        Validation:
        - No prior journal entries must exist for this fund
        - opening_balance_cents != 0

        Returns the posted JournalEntry with hash chain initialized.
        Writes are atomic: journal entry + journal lines + fund opening_balance_cents
        + outbox row + audit log, all in one transaction.
        """
        # Validate preconditions
        if cmd.opening_balance_cents == 0:
            raise ValueError("Opening balance must be non-zero for genesis posting")

        # Check that no journal entries exist yet for this fund
        existing_count = await self._ledger.count_journal_entries_for_fund(cmd.fund_id, cmd.scheme_ref)
        if existing_count > 0:
            raise ValueError(
                f"Fund {cmd.fund_id} already has journal entries. "
                "Genesis journal can only be posted once per fund."
            )

        # Run plugin validation hooks
        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd

        # Get accounting period from the genesis entry's own as-at date.
        resolved_period = await self._ledger.get_accounting_period_for_date(
            cmd.scheme_ref, enriched_cmd.as_at_date
        )
        period_id = resolved_period.period_id

        # Get GL accounts
        bank_account_id = await self._ledger.get_gl_account(cmd.scheme_ref, "1000")  # assets:bank
        opening_equity_account_id = await self._ledger.get_gl_account(cmd.scheme_ref,
                                                                      "3100")  # equity:opening_balances_clearing

        amount_cents = abs(enriched_cmd.opening_balance_cents)
        bank_direction = EntryDirection.DEBIT if enriched_cmd.opening_balance_cents > 0 else EntryDirection.CREDIT
        equity_direction = EntryDirection.CREDIT if enriched_cmd.opening_balance_cents > 0 else EntryDirection.DEBIT

        # Build journal lines
        journal_lines = [
            JournalLine(
                gl_account_id=bank_account_id,
                direction=bank_direction,
                amount_cents=amount_cents,
                gst_cents=0,
                narration="Genesis opening balance (assets)",
            ),
            JournalLine(
                gl_account_id=opening_equity_account_id,
                direction=equity_direction,
                amount_cents=amount_cents,
                gst_cents=0,
                narration="Genesis opening balance (equity)",
            ),
        ]

        # Build journal entry
        entry = JournalEntry(
            scheme_ref=cmd.scheme_ref,
            period_id=period_id,
            source_type="genesis",
            source_reference=str(cmd.fund_id),
            narration=f"Genesis opening balance for fund {cmd.fund_id}",
            effective_on=enriched_cmd.as_at_date,
            lines=journal_lines,
            fund_id=cmd.fund_id,
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
            evidence_document_id=enriched_cmd.evidence_doc_id,
            posted_by=enriched_cmd.posted_by_user_id,
            approved_by=enriched_cmd.approved_by_user_id or enriched_cmd.posted_by_user_id,
            metadata={
                "evidence_doc_id": str(enriched_cmd.evidence_doc_id) if enriched_cmd.evidence_doc_id else None,
                "evidence_doc_hash": enriched_cmd.evidence_doc_hash,
                "posted_by_user_id": str(enriched_cmd.posted_by_user_id) if enriched_cmd.posted_by_user_id else None,
                "approved_by_user_id": str(enriched_cmd.approved_by_user_id or enriched_cmd.posted_by_user_id) if (enriched_cmd.approved_by_user_id or enriched_cmd.posted_by_user_id) else None,
                "posted_by_user_name": enriched_cmd.posted_by_user_name,
                "building_id": enriched_cmd.building_id,
            },
        )

        # Save the journal entry (creates header + lines)
        saved_entry = await self._ledger.save_journal_entry(entry)

        # Post the entry (transitions to posted status, computes hash chain)
        posted_entry = await self._ledger.post_journal_entry(
            saved_entry.journal_entry_id, cmd.scheme_ref
        )

        # Update the fund's opening_balance_cents column
        await self._ledger.set_fund_opening_balance(
            cmd.fund_id, cmd.scheme_ref, enriched_cmd.opening_balance_cents
        )

        # Publish outbox event
        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="fund.genesis_posted",
            payload={
                "fund_id": str(cmd.fund_id),
                "journal_entry_id": str(posted_entry.journal_entry_id),
                "opening_balance_cents": enriched_cmd.opening_balance_cents,
                "as_at_date": enriched_cmd.as_at_date.isoformat(),
                "evidence_doc_id": str(enriched_cmd.evidence_doc_id) if enriched_cmd.evidence_doc_id else None,
                "evidence_doc_hash": enriched_cmd.evidence_doc_hash,
            },
        )

        # Audit log
        await create_audit_log(
            action="genesis_journal_posted",
            resource_type="financial_genesis",
            resource_id=str(posted_entry.journal_entry_id),
            user_id=str(enriched_cmd.posted_by_user_id) if enriched_cmd.posted_by_user_id else "system",
            user_name=enriched_cmd.posted_by_user_name or "System Cutover",
            details={
                "fund_id": str(cmd.fund_id),
                "opening_balance_cents": enriched_cmd.opening_balance_cents,
                "evidence_doc_id": str(enriched_cmd.evidence_doc_id) if enriched_cmd.evidence_doc_id else None,
                "evidence_doc_hash": enriched_cmd.evidence_doc_hash,
            },
            building_id=enriched_cmd.building_id,
        )
        await self._run_plugin_hook("log_event", {
            "type": "fund.genesis_posted",
            "fund_id": str(cmd.fund_id),
            "opening_balance_cents": enriched_cmd.opening_balance_cents,
        })

        logger.info(
            "Genesis journal posted: fund_id=%s, balance_cents=%d, entry_id=%s",
            cmd.fund_id, enriched_cmd.opening_balance_cents, posted_entry.journal_entry_id
        )

        return posted_entry

    # ------------------------------------------------------------------
    # 8. record_expense
    # ------------------------------------------------------------------

    async def record_expense(self, cmd: RecordExpenseCommand) -> Expense:
        """Record a settled/confirmed expense: DR expense GL (+ GST) / CR bank GL.

        Idempotent via finance.journal_entries UNIQUE(tenant_id, idempotency_key) —
        a replay with the same idempotency_key returns without re-posting.
        Ordinary path: always resolves an OPEN period from transaction_date;
        no closed-period override available here — use
        record_historical_expense() for that, which requires a
        HistoricalPostingAuthorisation.
        """
        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd

        return await self._post_expense_journal(
            scheme_ref=cmd.scheme_ref,
            category_name=enriched_cmd.category_name,
            amount_cents=enriched_cmd.amount_cents,
            gst_cents=enriched_cmd.gst_cents,
            transaction_date=enriched_cmd.transaction_date,
            fund_id=enriched_cmd.fund_id,
            vendor_name=enriched_cmd.vendor_name,
            invoice_number=enriched_cmd.invoice_number,
            financial_year=enriched_cmd.financial_year,
            description=enriched_cmd.description,
            source=enriched_cmd.source,
            derivation_level=enriched_cmd.derivation_level,
            reconstruction_batch_id=enriched_cmd.reconstruction_batch_id,
            bank_transaction_id=enriched_cmd.bank_transaction_id,
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
            posted_by=enriched_cmd.posted_by,
            approved_by=enriched_cmd.approved_by,
            metadata=enriched_cmd.metadata,
            authorisation=None,
        )

    # ------------------------------------------------------------------
    # 8h. record_historical_expense — privileged closed-period reconstruction
    # ------------------------------------------------------------------

    async def record_historical_expense(self, cmd: RecordHistoricalExpenseCommand) -> Expense:
        """Historical-reconstruction-only variant of record_expense().

        The ONLY expense-posting path permitted to resolve into a `closed`
        accounting period — gated by a mandatory, dual-control-validated
        HistoricalPostingAuthorisation. Not reachable from any HTTP route:
        RecordHistoricalExpenseCommand is never constructed by adapter.py or
        any router; only privileged scripts (e.g.
        scripts/east_gate_2025_expense_reconstruction.py) call this directly.
        """
        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd
        # Verify the ENRICHED command's authorisation, not the raw cmd's — this is
        # what actually gets used below (posted_by/approved_by, metadata). No
        # registered plugin currently touches .authorisation in enrich_command (only
        # act_plugin.py exists, and it only touches CreateLevyCommand.metadata), but
        # verifying pre-enrichment data would silently stop being correct the moment
        # one did.
        await self._verify_authorisation_identities(enriched_cmd.scheme_ref, enriched_cmd.authorisation)

        return await self._post_expense_journal(
            scheme_ref=cmd.scheme_ref,
            category_name=enriched_cmd.category_name,
            amount_cents=enriched_cmd.amount_cents,
            gst_cents=enriched_cmd.gst_cents,
            transaction_date=enriched_cmd.transaction_date,
            fund_id=enriched_cmd.fund_id,
            vendor_name=enriched_cmd.vendor_name,
            invoice_number=enriched_cmd.invoice_number,
            financial_year=enriched_cmd.financial_year,
            description=enriched_cmd.description,
            source="historical_import",
            derivation_level=enriched_cmd.derivation_level,
            reconstruction_batch_id=enriched_cmd.authorisation.reconstruction_batch_id,
            bank_transaction_id=enriched_cmd.bank_transaction_id,
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
            posted_by=enriched_cmd.authorisation.executed_by,
            approved_by=enriched_cmd.authorisation.approved_by,
            metadata=enriched_cmd.metadata,
            authorisation=enriched_cmd.authorisation,
        )

    async def _post_expense_journal(
            self,
            *,
            scheme_ref: SchemeRef,
            category_name: str,
            amount_cents: int,
            gst_cents: int,
            transaction_date: date,
            fund_id: Optional[UUID],
            vendor_name: Optional[str],
            invoice_number: Optional[str],
            financial_year: Optional[str],
            description: Optional[str],
            source: str,
            derivation_level: str,
            reconstruction_batch_id: Optional[UUID],
            bank_transaction_id: Optional[UUID],
            idempotency_key: Optional[str],
            is_test_data: bool,
            posted_by: Optional[UUID],
            approved_by: Optional[UUID],
            metadata: dict,
            authorisation: Optional[HistoricalPostingAuthorisation],
    ) -> Expense:
        """Shared implementation for record_expense() and
        record_historical_expense(). Ordering (see plan §4): claim the source
        bank transaction FIRST (before the idempotency check), so a partial
        prior attempt never leaves a posted journal with an unclaimed source
        row. A replayed idempotency_key whose stored amount/date disagrees
        with this call raises IdempotencyKeyCollision rather than silently
        returning stale content.
        """
        if amount_cents <= 0:
            raise ValueError(f"Expense amount must be positive, got {amount_cents}")
        if gst_cents < 0:
            raise ValueError(f"GST amount cannot be negative, got {gst_cents}")

        total_cents = amount_cents + gst_cents

        if bank_transaction_id is not None and reconstruction_batch_id is not None:
            await self._ledger.claim_bank_transaction_for_batch(
                scheme_ref.tenant_id, scheme_ref.scheme_id, bank_transaction_id, reconstruction_batch_id,
            )

        if idempotency_key:
            existing_id = await self._ledger.find_journal_entry_id_by_idempotency_key(
                scheme_ref, idempotency_key
            )
            if existing_id:
                existing_entry = await self._ledger.get_journal_entry(existing_id, scheme_ref)
                if existing_entry.debit_total_cents != total_cents or existing_entry.effective_on != transaction_date:
                    raise IdempotencyKeyCollision(
                        idempotency_key, existing_id,
                        f"expected amount_cents={total_cents} effective_on={transaction_date}, "
                        f"existing journal has amount_cents={existing_entry.debit_total_cents} "
                        f"effective_on={existing_entry.effective_on}",
                    )
                logger.info(
                    "Expense replay detected (idempotency_key=%s): journal_entry_id=%s",
                    idempotency_key, existing_id,
                )
                return Expense(
                    expense_id=uuid.uuid4(),
                    scheme_ref=scheme_ref,
                    category_name=category_name,
                    amount_cents=amount_cents,
                    gst_cents=gst_cents,
                    transaction_date=transaction_date,
                    fund_id=fund_id,
                    vendor_name=vendor_name,
                    invoice_number=invoice_number,
                    financial_year=financial_year,
                    description=description,
                    journal_entry_id=existing_id,
                    source=source,
                    derivation_level=derivation_level,
                    reconstruction_batch_id=reconstruction_batch_id,
                    is_test_data=is_test_data,
                    metadata={**metadata, "replayed": True},
                )

        permitted_statuses = frozenset({"open", "closed"}) if authorisation is not None else frozenset({"open"})
        resolved_period = await self._ledger.get_accounting_period_for_date(
            scheme_ref, transaction_date, permitted_statuses=permitted_statuses
        )

        fund_type = await self._ledger.get_fund_type(scheme_ref, fund_id) if fund_id is not None else "admin"
        if fund_type in ("sinking", "capital_works"):
            expense_gl_code = _SINKING_EXPENSE_GL_CODE
        else:
            expense_gl_code = _EXPENSE_CATEGORY_GL_CODES.get(
                _normalise_category_name(category_name), _DEFAULT_EXPENSE_GL_CODE
            )
        expense_gl_id = await self._ledger.get_gl_account(scheme_ref, expense_gl_code)
        bank_gl_id = await self._ledger.get_gl_account(scheme_ref, "1010")

        entry_metadata = {
            **metadata,
            "reconstruction_batch_id": str(reconstruction_batch_id) if reconstruction_batch_id else None,
            "derivation_level": derivation_level,
            "bank_transaction_id": str(bank_transaction_id) if bank_transaction_id else None,
        }
        if authorisation is not None:
            entry_metadata["historical_authorisation"] = _authorisation_metadata(authorisation)

        entry = JournalEntry(
            scheme_ref=scheme_ref,
            period_id=resolved_period.period_id,
            source_type="expense",
            source_reference=invoice_number,
            narration=description or f"Expense: {category_name}",
            effective_on=transaction_date,
            lines=[
                JournalLine(
                    gl_account_id=expense_gl_id,
                    direction=EntryDirection.DEBIT,
                    amount_cents=total_cents,
                    gst_cents=gst_cents,
                    narration=vendor_name,
                ),
                JournalLine(
                    gl_account_id=bank_gl_id,
                    direction=EntryDirection.CREDIT,
                    amount_cents=total_cents,
                    narration=vendor_name,
                ),
            ],
            fund_id=fund_id,
            idempotency_key=idempotency_key,
            is_test_data=is_test_data,
            posted_by=posted_by,
            approved_by=approved_by,
            metadata=entry_metadata,
        )

        saved_entry = await self._ledger.save_journal_entry(entry)
        posted_entry = await self._ledger.post_journal_entry(
            saved_entry.journal_entry_id, scheme_ref
        )

        expense = Expense(
            expense_id=uuid.uuid4(),
            scheme_ref=scheme_ref,
            category_name=category_name,
            amount_cents=amount_cents,
            gst_cents=gst_cents,
            transaction_date=transaction_date,
            fund_id=fund_id,
            gl_account_id=expense_gl_id,
            vendor_name=vendor_name,
            invoice_number=invoice_number,
            financial_year=financial_year,
            description=description,
            journal_entry_id=posted_entry.journal_entry_id,
            source=source,
            derivation_level=derivation_level,
            reconstruction_batch_id=reconstruction_batch_id,
            is_test_data=is_test_data,
            metadata=metadata,
        )
        saved_expense = await self._ledger.save_expense(expense)

        await self._outbox.publish(
            scheme_ref=scheme_ref,
            event_type="expense.recorded",
            payload={
                "expense_id": str(saved_expense.expense_id),
                "category_name": category_name,
                "amount_cents": amount_cents,
                "gst_cents": gst_cents,
                "journal_entry_id": str(posted_entry.journal_entry_id),
                "derivation_level": derivation_level,
            },
        )

        if authorisation is not None:
            await self._publish_historical_authorisation_events(
                handler="record_expense",
                scheme_ref=scheme_ref,
                journal_entry_id=posted_entry.journal_entry_id,
                resolved_period=resolved_period,
                effective_on=transaction_date,
                amount_cents=total_cents,
                idempotency_key=idempotency_key,
                bank_transaction_id=bank_transaction_id,
                authorisation=authorisation,
            )

        logger.info(
            "Expense recorded: category=%s amount_cents=%d journal=%s",
            category_name, total_cents, posted_entry.journal_entry_id,
        )
        return saved_expense

    # ------------------------------------------------------------------
    # 8a. charge_lot_fee — on-charge a single lot for a recovery cost or fee
    # ------------------------------------------------------------------

    async def charge_lot_fee(self, cmd: ChargeLotFeeCommand) -> UUID:
        """On-charge a single lot for a recovery cost or ad-hoc fee.

        DR "1100" Accounts Receivable (journal line tagged to cmd.lot_id) /
        CR "4003" Recovered Fees & Collection Costs — reversed (CR AR / DR
        income) when cmd.amount_cents is negative, so a tribunal-ordered
        credit back to a lot still produces a balanced, correctly-signed
        entry rather than a fake "negative charge".

        Creates exactly one finance.levy_runs row (levy_run_type=
        cmd.charge_type) and one finance.levy_items row per call — never
        appends to or edits an existing levy_run/levy_item, since a posted
        journal entry is immutable (trg_prevent_posted_journal_update); any
        future correction must be a new, opposite-signed charge via this same
        method, not an edit to what this call creates.

        This command intentionally does NOT touch the bank/expense side of
        the transaction — call record_expense() separately for the OC's
        actual cash outflow (if any). The two are independent, linked only by
        sharing the same source bank_transaction_id in each call's metadata.

        Returns the posted journal_entry_id. Idempotent via
        finance.journal_entries UNIQUE(tenant_id, idempotency_key) — a replay
        with the same idempotency_key returns the existing journal_entry_id
        without charging the lot a second time. Ordinary path: always
        resolves an OPEN period; no closed-period override available here —
        use charge_historical_lot_fee() for that.
        """
        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd

        return await self._post_lot_fee_journal(
            scheme_ref=cmd.scheme_ref,
            lot_id=enriched_cmd.lot_id,
            owner_party_id=enriched_cmd.owner_party_id,
            fund_id=enriched_cmd.fund_id,
            charge_type=enriched_cmd.charge_type,
            amount_cents=enriched_cmd.amount_cents,
            gst_cents=enriched_cmd.gst_cents,
            transaction_date=enriched_cmd.transaction_date,
            description=enriched_cmd.description,
            financial_year=enriched_cmd.financial_year,
            bank_transaction_id=enriched_cmd.bank_transaction_id,
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
            metadata=enriched_cmd.metadata,
            authorisation=None,
        )

    # ------------------------------------------------------------------
    # 8b. charge_historical_lot_fee — privileged closed-period reconstruction
    # ------------------------------------------------------------------

    async def charge_historical_lot_fee(self, cmd: ChargeHistoricalLotFeeCommand) -> UUID:
        """Historical-reconstruction-only variant of charge_lot_fee().

        The ONLY lot-fee-charging path permitted to resolve into a `closed`
        accounting period — gated by a mandatory, dual-control-validated
        HistoricalPostingAuthorisation. Not reachable from any HTTP route:
        ChargeHistoricalLotFeeCommand is never constructed by adapter.py or
        any router; only privileged scripts (e.g.
        scripts/east_gate_lot_fee_charge_backfill.py) call this directly.
        """
        await self._run_plugin_hook("validate_command", cmd)
        enriched_cmd = await self._run_plugin_hook("enrich_command", cmd) or cmd
        # Verify the ENRICHED command's authorisation, not the raw cmd's — this is
        # what actually gets used below (posted_by/approved_by, metadata). No
        # registered plugin currently touches .authorisation in enrich_command (only
        # act_plugin.py exists, and it only touches CreateLevyCommand.metadata), but
        # verifying pre-enrichment data would silently stop being correct the moment
        # one did.
        await self._verify_authorisation_identities(enriched_cmd.scheme_ref, enriched_cmd.authorisation)

        return await self._post_lot_fee_journal(
            scheme_ref=cmd.scheme_ref,
            lot_id=enriched_cmd.lot_id,
            owner_party_id=enriched_cmd.owner_party_id,
            fund_id=enriched_cmd.fund_id,
            charge_type=enriched_cmd.charge_type,
            amount_cents=enriched_cmd.amount_cents,
            gst_cents=enriched_cmd.gst_cents,
            transaction_date=enriched_cmd.transaction_date,
            description=enriched_cmd.description,
            financial_year=enriched_cmd.financial_year,
            bank_transaction_id=enriched_cmd.bank_transaction_id,
            idempotency_key=enriched_cmd.idempotency_key,
            is_test_data=enriched_cmd.is_test_data,
            metadata=enriched_cmd.metadata,
            authorisation=enriched_cmd.authorisation,
        )

    async def _post_lot_fee_journal(
            self,
            *,
            scheme_ref: SchemeRef,
            lot_id: UUID,
            owner_party_id: UUID,
            fund_id: UUID,
            charge_type: str,
            amount_cents: int,
            gst_cents: int,
            transaction_date: date,
            description: str,
            financial_year: Optional[str],
            bank_transaction_id: Optional[UUID],
            idempotency_key: Optional[str],
            is_test_data: bool,
            metadata: dict,
            authorisation: Optional[HistoricalPostingAuthorisation],
    ) -> UUID:
        """Shared implementation for charge_lot_fee() and
        charge_historical_lot_fee(). Same claim-then-idempotency-then-post
        ordering as _post_expense_journal (see plan §4)."""
        magnitude_cents = abs(amount_cents) + gst_cents
        is_reversal = amount_cents < 0
        reconstruction_batch_id = authorisation.reconstruction_batch_id if authorisation else None

        if bank_transaction_id is not None and reconstruction_batch_id is not None:
            await self._ledger.claim_bank_transaction_for_batch(
                scheme_ref.tenant_id, scheme_ref.scheme_id, bank_transaction_id, reconstruction_batch_id,
            )

        if idempotency_key:
            existing_id = await self._ledger.find_journal_entry_id_by_idempotency_key(
                scheme_ref, idempotency_key
            )
            if existing_id:
                existing_entry = await self._ledger.get_journal_entry(existing_id, scheme_ref)
                if existing_entry.debit_total_cents != magnitude_cents or existing_entry.effective_on != transaction_date:
                    raise IdempotencyKeyCollision(
                        idempotency_key, existing_id,
                        f"expected amount_cents={magnitude_cents} effective_on={transaction_date}, "
                        f"existing journal has amount_cents={existing_entry.debit_total_cents} "
                        f"effective_on={existing_entry.effective_on}",
                    )
                logger.info(
                    "Lot fee charge replay detected (idempotency_key=%s): journal_entry_id=%s",
                    idempotency_key, existing_id,
                )
                return existing_id

        permitted_statuses = frozenset({"open", "closed"}) if authorisation is not None else frozenset({"open"})
        resolved_period = await self._ledger.get_accounting_period_for_date(
            scheme_ref, transaction_date, permitted_statuses=permitted_statuses
        )
        ar_account_id = await self._ledger.get_gl_account(scheme_ref, "1100")
        income_account_id = await self._ledger.get_gl_account(scheme_ref, _RECOVERED_FEES_GL_CODE)

        levy_run_id = await self._ledger.save_levy_run(
            scheme_ref=scheme_ref,
            financial_year=financial_year or str(transaction_date.year),
            quarter_no=None,
            issue_date=transaction_date,
            due_date=transaction_date,
            levy_run_type=charge_type,
        )

        # Signed amount: a positive charge debits AR/credits income; a negative
        # reversal debits income/credits AR. abs() on the lines, direction
        # chosen by sign — matches how a reversing entry should read (both
        # lines are always positive amounts on their own side of the ledger).
        ar_direction = EntryDirection.CREDIT if is_reversal else EntryDirection.DEBIT
        income_direction = EntryDirection.DEBIT if is_reversal else EntryDirection.CREDIT

        entry_metadata = {
            **metadata,
            "charge_type": charge_type,
            "is_reversal": is_reversal,
            "bank_transaction_id": str(bank_transaction_id) if bank_transaction_id else None,
        }
        if authorisation is not None:
            entry_metadata["historical_authorisation"] = _authorisation_metadata(authorisation)

        entry = JournalEntry(
            scheme_ref=scheme_ref,
            period_id=resolved_period.period_id,
            source_type="lot_fee_charge",
            source_reference=str(bank_transaction_id) if bank_transaction_id else None,
            narration=description,
            effective_on=transaction_date,
            lines=[
                JournalLine(
                    gl_account_id=ar_account_id,
                    direction=ar_direction,
                    amount_cents=magnitude_cents,
                    gst_cents=gst_cents,
                    lot_id=lot_id,
                    party_id=owner_party_id,
                    narration=description,
                ),
                JournalLine(
                    gl_account_id=income_account_id,
                    direction=income_direction,
                    amount_cents=magnitude_cents,
                    lot_id=lot_id,
                    narration=description,
                ),
            ],
            fund_id=fund_id,
            idempotency_key=idempotency_key,
            is_test_data=is_test_data,
            posted_by=authorisation.executed_by if authorisation else None,
            approved_by=authorisation.approved_by if authorisation else None,
            metadata=entry_metadata,
        )

        saved_entry = await self._ledger.save_journal_entry(entry)
        posted_entry = await self._ledger.post_journal_entry(
            saved_entry.journal_entry_id, scheme_ref
        )

        levy_item = Levy(
            levy_item_id=uuid.uuid4(),
            scheme_ref=scheme_ref,
            levy_run_id=levy_run_id,
            lot_id=lot_id,
            owner_party_id=owner_party_id,
            fund_id=fund_id,
            principal_cents=amount_cents if charge_type == "adjustment" else 0,
            gst_cents=gst_cents,
            recovery_costs_cents=amount_cents if charge_type == "recovery" else 0,
            due_date=transaction_date,
            journal_entry_id=posted_entry.journal_entry_id,
        )
        await self._ledger.save_levy_items([levy_item])

        await self._outbox.publish(
            scheme_ref=scheme_ref,
            event_type="lot_fee.charged",
            payload={
                "lot_id": str(lot_id),
                "charge_type": charge_type,
                "amount_cents": amount_cents,
                "journal_entry_id": str(posted_entry.journal_entry_id),
            },
        )

        if authorisation is not None:
            await self._publish_historical_authorisation_events(
                handler="charge_lot_fee",
                scheme_ref=scheme_ref,
                journal_entry_id=posted_entry.journal_entry_id,
                resolved_period=resolved_period,
                effective_on=transaction_date,
                amount_cents=magnitude_cents,
                idempotency_key=idempotency_key,
                bank_transaction_id=bank_transaction_id,
                authorisation=authorisation,
            )

        logger.info(
            "Lot fee charged: lot=%s charge_type=%s amount_cents=%d journal=%s",
            lot_id, charge_type, amount_cents, posted_entry.journal_entry_id,
        )
        return posted_entry.journal_entry_id

    # ------------------------------------------------------------------
    # 9. bulk_upsert_historical_levy_items — historical backfill only
    # ------------------------------------------------------------------

    async def bulk_upsert_historical_levy_items(
            self, cmd: BulkUpsertHistoricalLevyItemsCommand
    ) -> list[Levy]:
        """Regenerate finance.levy_items principal_cents/gst_cents in place for a
        historical backfill/GST-correction. Never used by the live create_levy()
        path. Does NOT post a journal entry — call rebuild_provisional_levy_journals
        or reverse_and_replace_posted_levy_journals afterwards for that, keeping
        GST correction decoupled from re-posting income on every regeneration run.
        Never overwrites paid_cents, receipt allocations, historical statuses,
        existing identifiers, or downstream references (see plan02 amendment 5).
        """
        if not cmd.confirm:
            raise ValueError(
                "bulk_upsert_historical_levy_items requires confirm=True — "
                "this is a historical-backfill-only operation."
            )
        if not cmd.items:
            return []

        results = await self._ledger.upsert_levy_items(cmd.scheme_ref, cmd.items)

        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="levy_items.historical_regenerated",
            payload={
                "item_count": len(results),
                "total_principal_cents": sum(i.principal_cents for i in results),
                "total_gst_cents": sum(i.gst_cents for i in results),
                "is_test_data": cmd.is_test_data,
            },
        )
        logger.info(
            "Historical levy items upserted: %d items for scheme %s",
            len(results), cmd.scheme_ref.scheme_id,
        )
        return results

    # ------------------------------------------------------------------
    # 10. rebuild_provisional_levy_journals — draft/provisional only
    # ------------------------------------------------------------------

    async def rebuild_provisional_levy_journals(
            self, cmd: RebuildProvisionalLevyJournalsCommand
    ) -> int:
        """Scoped wipe of ONLY draft/posted_provisional journal_entries for the
        given source_types. Refuses (via the repo's classification guard) if any
        in-scope entry is posted_canonical/referenced_downstream — use
        reverse_and_replace_posted_levy_journals for those instead. Returns the
        count of entries removed; re-posting the corrected entries is a separate,
        explicit step (e.g. re-running record_payment/a levy-charge posting helper
        for the affected lots)."""
        if not cmd.confirm:
            raise ValueError("rebuild_provisional_levy_journals requires confirm=True")

        count = await self._ledger.rebuild_provisional_journals(cmd.scheme_ref, cmd.source_types)

        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="journals.provisional_rebuilt",
            payload={"source_types": cmd.source_types, "rebuilt_count": count},
        )
        logger.info(
            "Provisional journals rebuilt: %d entries removed (source_types=%s, scheme=%s)",
            count, cmd.source_types, cmd.scheme_ref.scheme_id,
        )
        return count

    # ------------------------------------------------------------------
    # 11. reverse_and_replace_posted_levy_journals — posted/canonical only
    # ------------------------------------------------------------------

    async def reverse_and_replace_posted_levy_journals(
            self, cmd: ReverseAndReplacePostedLevyJournalsCommand
    ) -> RebuildResult:
        """Corrects posted_canonical/referenced_downstream levy-charge journal
        entries via reverse_entry() (mirrors DR/CR, posts a new reversing entry —
        the original is never mutated) followed by a freshly-posted entry built
        from the CURRENT finance.levy_items row for the same levy_item — never
        deletes or edits a posted entry in place.
        """
        if not cmd.confirm:
            raise ValueError("reverse_and_replace_posted_levy_journals requires confirm=True")

        classifications = await self._ledger.classify_levy_journals(cmd.scheme_ref, cmd.source_types)
        to_correct = [
            c for c in classifications
            if c.classification in ("posted_canonical", "referenced_downstream") and c.levy_item_id
        ]

        period_id = await self._ledger.get_current_accounting_period(cmd.scheme_ref)
        ar_account_id = await self._ledger.get_gl_account(cmd.scheme_ref, "1100")
        income_account_id = await self._ledger.get_gl_account(cmd.scheme_ref, "4000")

        reversed_count = 0
        replaced_count = 0
        skipped_no_current_item = 0

        for entry in to_correct:
            await self.reverse_entry(ReverseEntryCommand(
                scheme_ref=cmd.scheme_ref,
                journal_entry_id=entry.journal_entry_id,
                reason=cmd.reason,
            ))
            reversed_count += 1

            current_item = await self._ledger.get_levy_item(cmd.scheme_ref, entry.levy_item_id)
            if current_item is None:
                skipped_no_current_item += 1
                continue

            total_cents = current_item.principal_cents + current_item.gst_cents
            if total_cents <= 0:
                continue

            replacement = JournalEntry(
                scheme_ref=cmd.scheme_ref,
                period_id=period_id,
                source_type=entry.source_type,
                source_reference=str(current_item.levy_run_id),
                narration=f"Corrected levy charge (replaces {entry.journal_entry_id}): {cmd.reason}",
                effective_on=date.today(),
                lines=[
                    JournalLine(
                        gl_account_id=ar_account_id,
                        direction=EntryDirection.DEBIT,
                        amount_cents=total_cents,
                        gst_cents=current_item.gst_cents,
                        lot_id=current_item.lot_id,
                        party_id=current_item.owner_party_id,
                    ),
                    JournalLine(
                        gl_account_id=income_account_id,
                        direction=EntryDirection.CREDIT,
                        amount_cents=total_cents,
                        gst_cents=current_item.gst_cents,
                        lot_id=current_item.lot_id,
                    ),
                ],
                metadata={"replaces_journal_entry_id": str(entry.journal_entry_id), "reason": cmd.reason},
            )
            saved = await self._ledger.save_journal_entry(replacement)
            await self._ledger.post_journal_entry(saved.journal_entry_id, cmd.scheme_ref)
            replaced_count += 1

        await self._outbox.publish(
            scheme_ref=cmd.scheme_ref,
            event_type="journals.posted_reversed_and_replaced",
            payload={
                "source_types": cmd.source_types,
                "reversed_count": reversed_count,
                "replaced_count": replaced_count,
                "skipped_no_current_item": skipped_no_current_item,
                "reason": cmd.reason,
            },
        )
        logger.info(
            "Posted levy journals corrected: reversed=%d replaced=%d skipped=%d (scheme=%s)",
            reversed_count, replaced_count, skipped_no_current_item, cmd.scheme_ref.scheme_id,
        )
        return RebuildResult(
            reversed_count=reversed_count,
            replaced_count=replaced_count,
            skipped_count=skipped_no_current_item,
        )

    # ------------------------------------------------------------------

    async def _publish_historical_authorisation_events(
            self,
            *,
            handler: str,
            scheme_ref: SchemeRef,
            journal_entry_id: UUID,
            resolved_period,  # AccountingPeriod
            effective_on: date,
            amount_cents: int,
            idempotency_key: Optional[str],
            bank_transaction_id: Optional[UUID],
            authorisation: HistoricalPostingAuthorisation,
    ) -> None:
        """Fixed audit-event semantics (see plan §4): always emit
        ledger.historical_posting_authorisation_used when a historical command
        was used — this documents the privileged path was invoked at all,
        even if the target period happened to still be open. Only emit
        ledger.closed_period_override, additionally, when the *actual
        resolved* period status wasn't 'open' — i.e. only when the
        authorisation was genuinely load-bearing, not merely supplied.
        """
        payload = {
            "handler": handler,
            "journal_entry_id": str(journal_entry_id),
            "period_id": str(resolved_period.period_id),
            "period_label": resolved_period.period_label,
            "period_status": resolved_period.status,
            "effective_on": effective_on.isoformat(),
            "amount_cents": amount_cents,
            "idempotency_key": idempotency_key,
            "reconstruction_batch_id": str(authorisation.reconstruction_batch_id),
            "reason": authorisation.reason,
            "approved_by": str(authorisation.approved_by),
            "executed_by": str(authorisation.executed_by),
            "approval_reference": authorisation.approval_reference,
            "evidence_reference": authorisation.evidence_reference,
            "bank_transaction_id": str(bank_transaction_id) if bank_transaction_id else None,
        }
        await self._outbox.publish(
            scheme_ref=scheme_ref,
            event_type="ledger.historical_posting_authorisation_used",
            payload=payload,
        )
        if resolved_period.status != "open":
            await self._outbox.publish(
                scheme_ref=scheme_ref,
                event_type="ledger.closed_period_override",
                payload=payload,
            )
            logger.warning(
                "Closed-period override used: handler=%s journal=%s period=%s(%s) "
                "effective_on=%s approved_by=%s executed_by=%s reason=%s",
                handler, journal_entry_id, resolved_period.period_id, resolved_period.status,
                effective_on, authorisation.approved_by, authorisation.executed_by, authorisation.reason,
            )

    async def _run_plugin_hook(self, hook_name: str, payload) -> object:
        """Dispatch a plugin hook. Returns enriched payload if hook returns one, else None.

        ValueError from validate_command hooks is intentional rejection — re-raise.
        Other exceptions are swallowed (non-blocking hooks).
        """
        if self._plugins is None:
            return None
        try:
            return await self._plugins.run_hook(hook_name, payload)
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("Plugin hook %s raised: %s", hook_name, exc)
            return None
