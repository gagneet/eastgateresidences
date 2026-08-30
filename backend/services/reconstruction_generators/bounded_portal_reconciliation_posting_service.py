# @featuretrace:financial-onboarding — Workstream C posting orchestrator (GAP-ONBOARD-004 A2).
#   Wires bounded_portal_reconciliation_engine.py's classification decisions to the two
#   already-proven FinancialCoreService posting mechanisms under dual control.
# Layer: service
# Data flow: PG per-unit true balance (services/finance_metrics/lot_true_balance.py) +
#            Mongo portal snapshot (services/portal_ledger_reconciliation_service.py::
#            _latest_portal_by_unit) + PG one-period-levy bound (scripts/
#            preview_bounded_portal_reconciliation.py's SQL) ->
#            classify_bounded_adjustment() -> BoundedAdjustmentDecision ->
#            FinancialCoreService.record_adjustment() (debit direction) /
#            record_historical_payment()+allocate_payment() (credit direction).
# Related: services/reconstruction_generators/bounded_portal_reconciliation_engine.py
#              (the pure classifier this module posts against)
#          scripts/preview_bounded_portal_reconciliation.py (read-only preview; this module
#              reuses its _PERIOD_BOUND_SQL_SIMPLE so the two never drift)
#          scripts/post_bounded_portal_reconciliation.py (CLI wrapper over this module)
#          services/reconstruction_generators/expense_posting_service.py (the dual-control /
#              validate-build-post split this module's shape mirrors)
#          scripts/historical_levy_payment_posting.py (the credit-direction dual-control recipe)
#          scripts/data_repair/gap_fin_046_portal_evidenced_adjustment_20260806.py (the
#              debit-direction per-unit savepoint + hard-assert recipe)
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (A2)
# Toggle: none (posting itself is gated by dual control + an approved reconstruction batch,
#         not a feature toggle — same as the two mechanisms it wires together)
# Collection: demo_bank_reconstruction_batches (dual-control authorisation vehicle only —
#             this module does not read or require a manifest, unlike the expense pipeline;
#             the per-unit work list is computed live from PG + the portal snapshot)
# Table: finance.levy_items, finance.levy_runs, finance.receipts, finance.journal_entries,
#        core.lots
"""Post the BOUNDED gap for units bounded_portal_reconciliation_engine.py classifies as
postable (``post_debit_adjustment`` / ``post_credit_receipt``), under dual control.

Never posts the portal snapshot value itself — only the bounded, per-unit GAP between the PG
true balance and the portal snapshot, exactly as the classifier computed it. A unit classified
``halt_flag`` aborts the entire run before anything is posted (never auto-posted around).
``reconciled`` / ``no_portal_data`` units are skipped, not posted.

Dual control mirrors ``expense_posting_service.py``: an APPROVED ``demo_bank_reconstruction_
batches`` row is the authorisation vehicle (its ``approved_by`` must differ from the caller's
``executed_by``) — but unlike the expense pipeline, this module reads no manifest from that
batch. The batch exists purely to make approval auditable, the same role
``historical_levy_payment_posting.py``'s batch plays for its own independently-computed
payment plan.

Idempotent replay: neither ``record_adjustment`` nor ``record_historical_payment`` has a
built-in graceful-replay path (only ``record_historical_expense`` does) — the underlying
``finance.journal_entries`` table enforces a hard unique index on
``(tenant_id, idempotency_key)`` (migration 0008). This module therefore checks
``find_journal_entry_id_by_idempotency_key`` itself, BEFORE calling the command, and skips
(counts as ``replayed``) any unit that already has a posted journal for its deterministic key
— re-running this module for the same building/year is always a safe, zero-new-row no-op for
already-posted units.

Per-unit isolation: each postable unit is posted inside its own ``session.begin_nested()``
savepoint with a post-apply true-balance assertion (mirrors
``gap_fin_046_portal_evidenced_adjustment_20260806.py``). A unit that fails its own assertion
or raises during posting rolls back only its own savepoint and is reported as an ``error``
outcome — it does NOT abort units that already posted successfully in the same run (same
partial-success discipline as the gap_fin_046 script: commit what passed its own hard-assert,
report what didn't).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable, Optional, Protocol
from uuid import UUID

from services.financial_core.domain.entities import (
    AllocatePaymentCommand,
    HistoricalPostingAuthorisation,
    PaymentChannel,
    RecordAdjustmentCommand,
    RecordHistoricalPaymentCommand,
    SchemeRef,
)
from services.reconstruction_generators.bounded_portal_reconciliation_engine import (
    BoundedAdjustmentDecision,
    classify_bounded_adjustment,
)

_SOURCE_TAG = "portal_reconciliation"
_SNAPSHOT_SOURCE = "staging_strata_web_snapshots"
_VERIFY_TOLERANCE_CENTS = 100  # matches the classifier's own _TOLERANCE_CENTS

# Historical reconstructed credit receipts have no known real-world channel (the source is a
# bounded, evidenced correction, not an observed bank line) — same neutral choice
# historical_levy_payment_posting.py makes for its own derived receipts.
_RECONCILIATION_PAYMENT_CHANNEL = PaymentChannel.EFT


class BoundedReconciliationPostingError(ValueError):
    """Raised on a governance/integrity precondition failure (dual-control, halt-flag abort,
    missing owner, post-apply assertion failure). A catchable exception — not SystemExit — so
    both the CLI (which converts it) and any future HTTP endpoint can consume this module."""


# ------------------------------------------------------------------ data shapes

@dataclass(frozen=True)
class UnitInput:
    """One unit's raw, pre-classification inputs — the exact fields
    ``classify_bounded_adjustment`` needs, plus the identifiers needed to build a posting
    command for it."""

    unit_number: str
    lot_id: UUID
    owner_party_id: Optional[UUID]
    pg_true_balance_cents: Optional[int]
    portal_balance_cents: Optional[int]
    period_bound_cents: Optional[int]


@dataclass(frozen=True)
class ClassifiedUnit:
    unit_input: UnitInput
    decision: BoundedAdjustmentDecision

    @property
    def is_postable(self) -> bool:
        return self.decision.is_postable


@dataclass(frozen=True)
class ReconciliationPostingPlan:
    """The validated work list, ready to post. Construction (``build_posting_plan``) is where
    dual control and the halt-flag abort are enforced — by the time this exists, every
    ``classified_units`` entry is safe to iterate (no ``halt_flag`` present)."""

    classified_units: list[ClassifiedUnit]
    approved_by: UUID
    executed_by: UUID
    reason: str
    evidence_reference: str
    building_id: str
    financial_year: str

    @property
    def postable_units(self) -> list[ClassifiedUnit]:
        return [u for u in self.classified_units if u.is_postable]

    @property
    def expected_total_cents(self) -> int:
        """Sum of |gap_cents| across every postable unit, computed independently of the
        posting loop (at plan-build time) -- the target the run-total integrity check in
        ``post_reconciliation_plan`` compares its own incrementally-accumulated total against.

        ``or 0`` guards a gap_cents=None that should be structurally unreachable for a postable
        unit (classify_bounded_adjustment only returns None for "no_portal_data", which is never
        postable) -- if it ever did happen, build_debit_command/build_credit_command raise for
        that specific unit and it surfaces as an "error" outcome, which makes
        run_total_integrity_ok vacuously true (has_errors short-circuits it) rather than this
        property crashing before posting even starts."""
        return sum(abs(u.decision.gap_cents or 0) for u in self.postable_units)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for u in self.classified_units:
            out[u.decision.decision] = out.get(u.decision.decision, 0) + 1
        return out


@dataclass
class UnitPostingOutcome:
    unit_number: str
    decision: str
    status: str  # "posted" | "replayed" | "skipped" | "error"
    gap_cents: Optional[int] = None
    journal_entry_id: Optional[str] = None
    receipt_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ReconciliationPostingResult:
    outcomes: list[UnitPostingOutcome] = field(default_factory=list)
    posted: int = 0
    replayed: int = 0
    reconciled: int = 0
    no_portal_data: int = 0
    posted_total_cents: int = 0
    expected_total_cents: int = 0
    approved_by: Optional[str] = None
    executed_by: Optional[str] = None

    @property
    def has_errors(self) -> bool:
        return any(o.status == "error" for o in self.outcomes)

    @property
    def covered_total_cents(self) -> int:
        """posted + replayed gap totals -- everything the plan's postable units ended up
        represented by, either newly posted this run or already covered by a prior run."""
        return self.posted_total_cents + sum(
            abs(o.gap_cents or 0) for o in self.outcomes if o.status == "replayed"
        )

    @property
    def run_total_integrity_ok(self) -> bool:
        """The run-total integrity check: with no per-unit errors, every postable unit must be
        either posted or replayed, and their combined total must equal
        ``expected_total_cents`` (computed independently, at plan-build time, from the same
        classified units). Vacuously true when there were errors -- an errored unit legitimately
        reduces covered_total_cents below expected, and that is already visible via
        has_errors/the per-unit 'error' outcome, not a second, redundant failure mode here."""
        return self.has_errors or self.covered_total_cents == self.expected_total_cents

    def as_dict(self) -> dict[str, Any]:
        return {
            "posted": self.posted,
            "replayed": self.replayed,
            "reconciled": self.reconciled,
            "no_portal_data": self.no_portal_data,
            "posted_total_cents": self.posted_total_cents,
            "expected_total_cents": self.expected_total_cents,
            "covered_total_cents": self.covered_total_cents,
            "run_total_integrity_ok": self.run_total_integrity_ok,
            "approved_by": self.approved_by,
            "executed_by": self.executed_by,
            "has_errors": self.has_errors,
            "outcomes": [
                {
                    "unit_number": o.unit_number,
                    "decision": o.decision,
                    "status": o.status,
                    "gap_cents": o.gap_cents,
                    "journal_entry_id": o.journal_entry_id,
                    "receipt_id": o.receipt_id,
                    "error": o.error,
                }
                for o in self.outcomes
            ],
        }


# ------------------------------------------------------------------ dual control + classification

def validate_batch_authorisation(*, batch_doc: dict, executed_by: UUID) -> UUID:
    """Dual-control gate — pure, mirrors ``expense_posting_service.validate_batch_
    authorisation`` exactly. Returns the batch's ``approved_by`` UUID. Raises
    ``BoundedReconciliationPostingError`` if the batch isn't approved, has no approved_by, or
    the executor is the same person who approved it."""
    if batch_doc.get("status") != "approved":
        raise BoundedReconciliationPostingError(
            f"Batch {batch_doc.get('batch_id')!r} has status={batch_doc.get('status')!r}; "
            "posting requires an APPROVED reconstruction batch as the dual-control "
            "authorisation vehicle."
        )
    approved_by_raw = batch_doc.get("approved_by")
    if not approved_by_raw:
        raise BoundedReconciliationPostingError(
            f"Batch {batch_doc.get('batch_id')!r} has no approved_by — cannot establish dual control."
        )
    approved_by = UUID(str(approved_by_raw))
    if approved_by == executed_by:
        raise BoundedReconciliationPostingError(
            f"executed_by ({executed_by}) must differ from the batch's approved_by "
            f"({approved_by}) — dual control requires two distinct identities."
        )
    return approved_by


def classify_units(
    unit_inputs: list[UnitInput], *, bound_periods: float = 1.0,
) -> list[ClassifiedUnit]:
    """Pure: classify every unit's residual via ``classify_bounded_adjustment``."""
    out: list[ClassifiedUnit] = []
    for u in unit_inputs:
        decision = classify_bounded_adjustment(
            unit_number=u.unit_number,
            pg_true_balance_cents=u.pg_true_balance_cents,
            portal_balance_cents=u.portal_balance_cents,
            period_bound_cents=u.period_bound_cents,
            bound_periods=bound_periods,
        )
        out.append(ClassifiedUnit(unit_input=u, decision=decision))
    return out


def build_posting_plan(
    *,
    classified_units: list[ClassifiedUnit],
    batch_doc: dict,
    executed_by: UUID,
    building_id: str,
    financial_year: str,
    snapshot_date: Any,
) -> ReconciliationPostingPlan:
    """Dual-control gate + halt-flag abort. Raises ``BoundedReconciliationPostingError`` if
    dual control fails OR if any unit is ``halt_flag`` — a halt anywhere aborts the whole run,
    never posts around it. Pure (no I/O) once given already-classified units."""
    approved_by = validate_batch_authorisation(batch_doc=batch_doc, executed_by=executed_by)

    halted = [u for u in classified_units if u.decision.decision == "halt_flag"]
    if halted:
        names = ", ".join(u.unit_input.unit_number for u in halted)
        raise BoundedReconciliationPostingError(
            f"{len(halted)} unit(s) exceed the bound and are halt_flag — aborting the whole "
            f"run rather than posting around them: {names}"
        )

    evidence_reference = f"{_SNAPSHOT_SOURCE}:{building_id}:{financial_year}:{snapshot_date}"
    reason = (
        f"Bounded portal reconciliation posting for building {building_id}, FY{financial_year}: "
        f"batch={batch_doc.get('batch_id')}, approved_by={approved_by}, snapshot={snapshot_date}."
    )
    return ReconciliationPostingPlan(
        classified_units=classified_units,
        approved_by=approved_by,
        executed_by=executed_by,
        reason=reason,
        evidence_reference=evidence_reference,
        building_id=building_id,
        financial_year=financial_year,
    )


# ------------------------------------------------------------------ command builders

def _idempotency_key(*, direction: str, unit_number: str, financial_year: str) -> str:
    return f"portal-recon-{direction}-{unit_number}-{financial_year}"


def build_debit_command(
    *,
    unit: ClassifiedUnit,
    plan: ReconciliationPostingPlan,
    scheme_ref: SchemeRef,
    reconstruction_batch_id: UUID,
    is_test_data: bool,
) -> RecordAdjustmentCommand:
    """Build the debit-direction command for a ``post_debit_adjustment`` unit (portal implies
    more owed than PG shows -> PG's paid_cents is overstated)."""
    decision = unit.decision
    if decision.gap_cents is None:
        # BoundedAdjustmentDecision.gap_cents is Optional[int] at the type level (None only for
        # "no_portal_data", per classify_bounded_adjustment's own contract) -- a
        # "post_debit_adjustment" decision should never carry gap_cents=None today, but this
        # guard converts a hypothetical future classifier bug into a clear, catchable error here
        # rather than an uncaught `TypeError: bad operand type for abs(): 'NoneType'` at the
        # abs() call below. Caught upstream in post_reconciliation_plan's per-unit try/except.
        raise BoundedReconciliationPostingError(
            f"{unit.unit_input.unit_number}: decision={decision.decision!r} but gap_cents is "
            "None -- refusing to build a debit command with an unknown amount."
        )
    idempotency_key = _idempotency_key(
        direction="adj", unit_number=unit.unit_input.unit_number, financial_year=plan.financial_year,
    )
    authorisation = HistoricalPostingAuthorisation(
        reconstruction_batch_id=reconstruction_batch_id,
        reason=plan.reason,
        approved_by=plan.approved_by,
        executed_by=plan.executed_by,
        approval_reference=f"batch:{reconstruction_batch_id}:approved_by:{plan.approved_by}",
        evidence_reference=plan.evidence_reference,
    )
    return RecordAdjustmentCommand(
        scheme_ref=scheme_ref,
        lot_id=unit.unit_input.lot_id,
        amount_cents=abs(decision.gap_cents),
        financial_year=plan.financial_year,
        authorisation=authorisation,
        effective_on=date.today(),
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        metadata={
            "source_tag": _SOURCE_TAG,
            "unit_number": unit.unit_input.unit_number,
            "portal_balance_cents": decision.portal_balance_cents,
            "pg_true_balance_cents": decision.pg_true_balance_cents,
        },
    )


def build_credit_command(
    *,
    unit: ClassifiedUnit,
    plan: ReconciliationPostingPlan,
    scheme_ref: SchemeRef,
    reconstruction_batch_id: UUID,
    is_test_data: bool,
) -> RecordHistoricalPaymentCommand:
    """Build the credit-direction command for a ``post_credit_receipt`` unit (portal implies
    less owed / more credit than PG shows -> a real payment PG never captured)."""
    decision = unit.decision
    if decision.gap_cents is None:
        # See the matching guard in build_debit_command for why this is checked explicitly
        # despite being unreachable under the current classifier's invariants.
        raise BoundedReconciliationPostingError(
            f"{unit.unit_input.unit_number}: decision={decision.decision!r} but gap_cents is "
            "None -- refusing to build a credit command with an unknown amount."
        )
    if unit.unit_input.owner_party_id is None:
        raise BoundedReconciliationPostingError(
            f"{unit.unit_input.unit_number}: no owner_party_id resolved for FY"
            f"{plan.financial_year} — cannot post a credit receipt without a payer."
        )
    idempotency_key = _idempotency_key(
        direction="credit", unit_number=unit.unit_input.unit_number, financial_year=plan.financial_year,
    )
    authorisation = HistoricalPostingAuthorisation(
        reconstruction_batch_id=reconstruction_batch_id,
        reason=plan.reason,
        approved_by=plan.approved_by,
        executed_by=plan.executed_by,
        approval_reference=f"batch:{reconstruction_batch_id}:approved_by:{plan.approved_by}",
        evidence_reference=plan.evidence_reference,
    )
    return RecordHistoricalPaymentCommand(
        scheme_ref=scheme_ref,
        payer_party_id=unit.unit_input.owner_party_id,
        lot_id=unit.unit_input.lot_id,
        channel=_RECONCILIATION_PAYMENT_CHANNEL,
        received_on=date.today(),
        amount_cents=abs(decision.gap_cents),
        authorisation=authorisation,
        external_reference=idempotency_key,
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
        metadata={
            "source_tag": _SOURCE_TAG,
            "unit_number": unit.unit_input.unit_number,
            "portal_balance_cents": decision.portal_balance_cents,
            "pg_true_balance_cents": decision.pg_true_balance_cents,
        },
    )


# ------------------------------------------------------------------ posting loop

class _LedgerLookup(Protocol):
    async def find_journal_entry_id_by_idempotency_key(
        self, scheme_ref: SchemeRef, idempotency_key: str,
    ) -> Optional[UUID]: ...


class _ReconciliationSink(Protocol):
    """The three FinancialCoreService methods this module needs — declared as a Protocol so
    the posting loop is trivially mockable in tests with a fake sink."""

    async def record_adjustment(self, cmd: RecordAdjustmentCommand) -> Any: ...
    async def record_historical_payment(self, cmd: RecordHistoricalPaymentCommand) -> Any: ...
    async def allocate_payment(self, cmd: AllocatePaymentCommand) -> Any: ...


class _Savepoint(Protocol):
    """The one method this module needs from a SQLAlchemy AsyncSession — a nested-transaction
    context manager, so the posting loop is mockable without a real session."""

    def begin_nested(self) -> Any: ...


VerifyUnitBalance = Callable[[str, int], Awaitable[int]]


async def post_reconciliation_plan(
    *,
    svc: _ReconciliationSink,
    ledger: _LedgerLookup,
    session: _Savepoint,
    plan: ReconciliationPostingPlan,
    scheme_ref: SchemeRef,
    reconstruction_batch_id: UUID,
    is_test_data: bool,
    verify_unit_balance: VerifyUnitBalance,
) -> ReconciliationPostingResult:
    """Post every postable unit in ``plan`` (halt_flag already aborted the whole plan at
    ``build_posting_plan`` time, so only ``post_debit_adjustment``/``post_credit_receipt``/
    ``reconciled``/``no_portal_data`` remain here).

    Each postable unit: idempotency pre-check (skip -> ``replayed`` if already posted) -> post
    inside its own savepoint -> ``verify_unit_balance`` post-apply assertion within
    ``_VERIFY_TOLERANCE_CENTS`` of the portal figure. A unit whose post or assertion raises
    rolls back only its own savepoint and is recorded as an ``error`` outcome — it does not
    abort units that already posted successfully (partial-success discipline, mirrors
    gap_fin_046_portal_evidenced_adjustment_20260806.py). Never raises itself; inspect
    ``result.has_errors``.

    Run-total integrity check: after every unit has been attempted, if no unit errored but the
    posted+replayed total still doesn't match ``plan.expected_total_cents`` (computed
    independently at plan-build time), that is an internal accounting inconsistency rather than
    a governance/per-unit failure -- it is surfaced as a synthetic ``__RUN_TOTAL__`` error
    outcome so it trips ``result.has_errors`` the same way a real per-unit failure would, instead
    of silently returning a mismatched summary.
    """
    result = ReconciliationPostingResult(
        approved_by=str(plan.approved_by), executed_by=str(plan.executed_by),
        expected_total_cents=plan.expected_total_cents,
    )

    for unit in plan.classified_units:
        decision = unit.decision
        unit_number = unit.unit_input.unit_number

        if decision.decision == "reconciled":
            result.reconciled += 1
            result.outcomes.append(
                UnitPostingOutcome(unit_number=unit_number, decision=decision.decision, status="skipped")
            )
            continue
        if decision.decision == "no_portal_data":
            result.no_portal_data += 1
            result.outcomes.append(
                UnitPostingOutcome(unit_number=unit_number, decision=decision.decision, status="skipped")
            )
            continue

        # Only post_debit_adjustment / post_credit_receipt remain (halt_flag was pre-aborted).
        # Explicit elif + a defensive else (rather than a bare else defaulting to credit) so a
        # future decision value added to bounded_portal_reconciliation_engine.py without a
        # matching branch here fails loudly as an "error" outcome instead of silently being
        # posted as a credit receipt.
        try:
            if decision.decision == "post_debit_adjustment":
                cmd = build_debit_command(
                    unit=unit, plan=plan, scheme_ref=scheme_ref,
                    reconstruction_batch_id=reconstruction_batch_id, is_test_data=is_test_data,
                )
            elif decision.decision == "post_credit_receipt":
                cmd = build_credit_command(
                    unit=unit, plan=plan, scheme_ref=scheme_ref,
                    reconstruction_batch_id=reconstruction_batch_id, is_test_data=is_test_data,
                )
            else:
                raise BoundedReconciliationPostingError(
                    f"{unit_number}: unhandled classifier decision {decision.decision!r} -- "
                    "no posting mechanism wired for it; refusing to guess a direction."
                )
        except BoundedReconciliationPostingError as exc:
            result.outcomes.append(UnitPostingOutcome(
                unit_number=unit_number, decision=decision.decision, status="error",
                gap_cents=decision.gap_cents, error=str(exc),
            ))
            continue

        existing_id = await ledger.find_journal_entry_id_by_idempotency_key(
            scheme_ref, cmd.idempotency_key
        )
        if existing_id is not None:
            result.replayed += 1
            result.outcomes.append(UnitPostingOutcome(
                unit_number=unit_number, decision=decision.decision, status="replayed",
                gap_cents=decision.gap_cents, journal_entry_id=str(existing_id),
            ))
            continue

        try:
            async with session.begin_nested():
                if decision.decision == "post_debit_adjustment":
                    posted = await svc.record_adjustment(cmd)
                    journal_entry_id = posted.journal_entry_id
                    receipt_id = None
                else:
                    receipt = await svc.record_historical_payment(cmd)
                    await svc.allocate_payment(
                        AllocatePaymentCommand(scheme_ref=scheme_ref, receipt_id=receipt.receipt_id)
                    )
                    journal_entry_id = receipt.journal_entry_id
                    receipt_id = receipt.receipt_id

                actual_cents = await verify_unit_balance(unit_number, decision.portal_balance_cents)
                if abs(actual_cents - decision.portal_balance_cents) > _VERIFY_TOLERANCE_CENTS:
                    raise BoundedReconciliationPostingError(
                        f"{unit_number}: post-adjustment true_balance="
                        f"${actual_cents / 100:,.2f} != portal target="
                        f"${decision.portal_balance_cents / 100:,.2f}"
                    )
        except Exception as exc:
            result.outcomes.append(UnitPostingOutcome(
                unit_number=unit_number, decision=decision.decision, status="error",
                gap_cents=decision.gap_cents, error=str(exc),
            ))
            continue

        result.posted += 1
        # abs(decision.gap_cents) is safe here without a None-check: reaching this line required
        # build_debit_command/build_credit_command to have already succeeded on this exact,
        # immutable `decision` object above, and both raise BoundedReconciliationPostingError
        # (caught two blocks up) before ever returning a command when gap_cents is None.
        result.posted_total_cents += abs(decision.gap_cents)
        result.outcomes.append(UnitPostingOutcome(
            unit_number=unit_number, decision=decision.decision, status="posted",
            gap_cents=decision.gap_cents, journal_entry_id=str(journal_entry_id),
            receipt_id=str(receipt_id) if receipt_id else None,
        ))

    if not result.run_total_integrity_ok:
        result.outcomes.append(UnitPostingOutcome(
            unit_number="__RUN_TOTAL__", decision="integrity_check", status="error",
            error=(
                f"Run-total integrity check failed: covered {result.covered_total_cents} cents "
                f"(posted+replayed) but the plan expected {result.expected_total_cents} cents "
                "across all postable units, with no per-unit error to explain the gap."
            ),
        ))

    return result


# ------------------------------------------------------------------ DB-wiring orchestration
# (lazy imports throughout this section so the pure functions above stay importable in unit
# tests without the Postgres/Mongo stack — same discipline as expense_posting_service.py)

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


async def _get_batch_doc(mongo_db, building_id: str, batch_id: str) -> dict:
    doc = await mongo_db.demo_bank_reconstruction_batches.find_one(
        {"building_id": building_id, "batch_id": batch_id}
    )
    if doc is None:
        raise BoundedReconciliationPostingError(
            f"No reconstruction batch {batch_id!r} found for building_id={building_id!r}"
        )
    return doc


async def assemble_and_classify(
    *, mongo_db, session, building_id: str, financial_year: str, scheme_id: str, tenant_id: str,
    bound_periods: float = 1.0,
) -> tuple[Any, list[ClassifiedUnit]]:
    """Assemble every unit's inputs via the SAME sources as
    scripts/preview_bounded_portal_reconciliation.py (compute_lot_true_balances,
    _latest_portal_by_unit, the one-period-levy bound SQL) plus each lot's owner_party_id, then
    classify. Returns ``(snapshot_date, classified_units)``. Requires an already tenant-scoped
    ``session`` (caller must have run ``set_tenant``). Raises
    ``BoundedReconciliationPostingError`` if there is no staged portal snapshot for the year."""
    from sqlalchemy import text

    from services.finance_metrics.lot_true_balance import compute_lot_true_balances
    from services.portal_ledger_reconciliation_service import _latest_portal_by_unit
    from scripts.preview_bounded_portal_reconciliation import _PERIOD_BOUND_SQL_SIMPLE
    from utils.unit_number import extract_lot_int

    snapshot_date, portal_by_lot_int = await _latest_portal_by_unit(mongo_db, building_id, financial_year)
    if snapshot_date is None:
        raise BoundedReconciliationPostingError(
            f"No staged portal snapshot for building_id={building_id!r} financial_year={financial_year!r}"
        )

    pg_balances = await compute_lot_true_balances(
        session, scheme_id=scheme_id, tenant_id=tenant_id, financial_year=financial_year,
    )
    bound_rows = (
        await session.execute(_PERIOD_BOUND_SQL_SIMPLE, {"sid": scheme_id, "fy": financial_year})
    ).fetchall()
    bound_by_unit = {row.unit_number: int(row.period_bound_cents or 0) for row in bound_rows}

    lot_rows = (
        await session.execute(
            text(
                "SELECT lot_id::text, COALESCE(unit_number, lot_number) AS unit_number "
                "FROM core.lots WHERE scheme_id = CAST(:sid AS UUID)"
            ),
            {"sid": scheme_id},
        )
    ).fetchall()

    # Most-recently-due levy_item's owner_party_id per lot for the year -- a reasonable
    # "current owner" proxy, same source historical_levy_charge_posting.py's own charge
    # reconstruction already trusts for this exact field.
    owner_rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (li.lot_id) li.lot_id::text, li.owner_party_id::text
                FROM finance.levy_items li
                JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
                WHERE li.scheme_id = CAST(:sid AS UUID) AND lr.financial_year = :fy
                ORDER BY li.lot_id, lr.due_date DESC
                """
            ),
            {"sid": scheme_id, "fy": financial_year},
        )
    ).fetchall()
    owner_by_lot = {r.lot_id: r.owner_party_id for r in owner_rows}

    unit_inputs: list[UnitInput] = []
    for lot_id_text, unit_number in lot_rows:
        pg_balance = pg_balances.get(lot_id_text)
        pg_cents = pg_balance.true_balance_cents if pg_balance is not None else None
        portal_cents = portal_by_lot_int.get(extract_lot_int(unit_number))
        bound_cents = bound_by_unit.get(unit_number)
        owner_raw = owner_by_lot.get(lot_id_text)
        unit_inputs.append(UnitInput(
            unit_number=unit_number,
            lot_id=UUID(lot_id_text),
            owner_party_id=UUID(owner_raw) if owner_raw else None,
            pg_true_balance_cents=pg_cents,
            portal_balance_cents=portal_cents,
            period_bound_cents=bound_cents,
        ))

    classified = classify_units(unit_inputs, bound_periods=bound_periods)
    return snapshot_date, classified


async def run_reconciliation_posting(
    *,
    mongo_db,
    building_id: str,
    financial_year: str,
    batch_id: str,
    executed_by: UUID,
    bound_periods: float = 1.0,
    is_test_data: bool = False,
) -> ReconciliationPostingResult:
    """Full orchestration entry point: resolve the approved batch + PG scheme, assemble and
    classify every unit, build the plan (dual control + halt-flag abort), then post under
    per-unit savepoints with a live post-apply reconciliation assertion. Commits at the end
    regardless of whether individual units errored (partial-success discipline — see
    ``post_reconciliation_plan``'s docstring). Raises ``BoundedReconciliationPostingError`` on
    any pre-flight governance failure (missing batch/scheme/snapshot, dual control, halt-flag).
    """
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant
    from services.financial_core import get_financial_core_service
    from services.financial_core.adapters.db_postgres.ledger_repo import PostgresLedgerRepository
    from services.finance_metrics.lot_true_balance import compute_unit_true_balance

    batch_doc = await _get_batch_doc(mongo_db, building_id, batch_id)

    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        raise BoundedReconciliationPostingError(
            f"No PostgreSQL scheme found for building_id={building_id!r}"
        )
    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])
    scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(scheme_id))

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        snapshot_date, classified = await assemble_and_classify(
            mongo_db=mongo_db, session=session, building_id=building_id,
            financial_year=financial_year, scheme_id=scheme_id, tenant_id=tenant_id,
            bound_periods=bound_periods,
        )
        plan = build_posting_plan(
            classified_units=classified, batch_doc=batch_doc, executed_by=executed_by,
            building_id=building_id, financial_year=financial_year, snapshot_date=snapshot_date,
        )

        svc = get_financial_core_service(session)
        ledger = PostgresLedgerRepository(session)

        async def _verify_unit_balance(unit_number: str, _target_cents: int) -> int:
            balance = await compute_unit_true_balance(
                session, scheme_id=scheme_id, tenant_id=tenant_id,
                unit_number=unit_number, financial_year=financial_year,
            )
            return balance.true_balance_cents if balance is not None else 0

        result = await post_reconciliation_plan(
            svc=svc, ledger=ledger, session=session, plan=plan, scheme_ref=scheme_ref,
            reconstruction_batch_id=UUID(batch_id), is_test_data=is_test_data,
            verify_unit_balance=_verify_unit_balance,
        )
        # No explicit session.commit() here: async_session_context() (db_postgres/session.py)
        # already commits on normal exit from this `async with` block and rolls back on any
        # exception raised inside it. An earlier version of this function called
        # session.commit() here too -- harmless in practice (a second commit() with no pending
        # work is a no-op) but redundant, and a maintainer adding cleanup code after it could
        # wrongly assume that code still runs inside the same guarded transaction.

    return result
