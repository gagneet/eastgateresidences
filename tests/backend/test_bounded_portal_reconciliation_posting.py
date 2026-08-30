"""Unit tests for services/reconstruction_generators/bounded_portal_reconciliation_posting_
service.py — GAP-ONBOARD-004 A2's posting orchestrator.

Covers dual-control enforcement, halt-flag whole-run abort, correct command construction per
classifier decision, idempotent replay (no DB write on a re-run), and partial-success posting
(one unit's failure doesn't roll back another unit's success). The pure functions
(validate_batch_authorisation, build_posting_plan, build_debit_command, build_credit_command)
need no DB; post_reconciliation_plan is exercised via fake sink/ledger/session doubles so the
posting loop's control flow is tested without Postgres.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from services.financial_core.domain.entities import SchemeRef
from services.reconstruction_generators.bounded_portal_reconciliation_engine import (
    BoundedAdjustmentDecision,
)
from services.reconstruction_generators.bounded_portal_reconciliation_posting_service import (
    BoundedReconciliationPostingError,
    ClassifiedUnit,
    ReconciliationPostingResult,
    UnitInput,
    UnitPostingOutcome,
    build_credit_command,
    build_debit_command,
    build_posting_plan,
    classify_units,
    post_reconciliation_plan,
    validate_batch_authorisation,
)

BUILDING_ID = "16244"
BATCH_ID = str(uuid.uuid4())
APPROVED_BY = uuid.uuid4()
EXECUTED_BY = uuid.uuid4()
SCHEME_REF = SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())


def _batch(status="approved", approved_by=None):
    return {"batch_id": BATCH_ID, "status": status, "approved_by": str(approved_by or APPROVED_BY)}


def _unit_input(unit_number="UA001", owner_party_id=None, **overrides):
    defaults = dict(
        unit_number=unit_number, lot_id=uuid.uuid4(), owner_party_id=owner_party_id,
        pg_true_balance_cents=1000, portal_balance_cents=1000, period_bound_cents=5000,
    )
    defaults.update(overrides)
    return UnitInput(**defaults)


def _decision(unit_number="UA001", decision="post_debit_adjustment", gap_cents=500, portal_balance_cents=1500):
    return BoundedAdjustmentDecision(
        unit_number=unit_number, pg_true_balance_cents=1000, portal_balance_cents=portal_balance_cents,
        period_bound_cents=5000, gap_cents=gap_cents, decision=decision, reason="test",
    )


def _classified(unit_number="UA001", decision="post_debit_adjustment", gap_cents=500,
                 portal_balance_cents=1500, owner_party_id=None):
    return ClassifiedUnit(
        unit_input=_unit_input(unit_number=unit_number, owner_party_id=owner_party_id),
        decision=_decision(unit_number, decision, gap_cents, portal_balance_cents),
    )


class TestValidateBatchAuthorisation:
    def test_non_approved_status_raises(self):
        with pytest.raises(BoundedReconciliationPostingError, match="APPROVED"):
            validate_batch_authorisation(batch_doc=_batch(status="draft"), executed_by=EXECUTED_BY)

    def test_missing_approved_by_raises(self):
        with pytest.raises(BoundedReconciliationPostingError, match="no approved_by"):
            validate_batch_authorisation(
                batch_doc={"batch_id": BATCH_ID, "status": "approved", "approved_by": None},
                executed_by=EXECUTED_BY,
            )

    def test_same_approver_and_executor_raises(self):
        with pytest.raises(BoundedReconciliationPostingError, match="dual control"):
            validate_batch_authorisation(
                batch_doc=_batch(approved_by=EXECUTED_BY), executed_by=EXECUTED_BY,
            )

    def test_distinct_actors_returns_approved_by(self):
        approved_by = validate_batch_authorisation(batch_doc=_batch(), executed_by=EXECUTED_BY)
        assert approved_by == APPROVED_BY


class TestClassifyUnits:
    def test_missing_data_yields_no_portal_data(self):
        out = classify_units([_unit_input(portal_balance_cents=None)])
        assert out[0].decision.decision == "no_portal_data"

    def test_within_tolerance_reconciled(self):
        out = classify_units([_unit_input(pg_true_balance_cents=1000, portal_balance_cents=1000)])
        assert out[0].decision.decision == "reconciled"


class TestBuildPostingPlan:
    def test_halt_flag_aborts_whole_run(self):
        classified = [_classified("UA001", "post_debit_adjustment"), _classified("UA002", "halt_flag")]
        with pytest.raises(BoundedReconciliationPostingError, match="halt_flag"):
            build_posting_plan(
                classified_units=classified, batch_doc=_batch(), executed_by=EXECUTED_BY,
                building_id=BUILDING_ID, financial_year="2026", snapshot_date="2026-08-01",
            )

    @pytest.mark.asyncio
    async def test_halt_flag_aborts_before_any_co_located_unit_is_posted(self):
        """Stronger than test_halt_flag_aborts_whole_run: proves this is "the whole run
        aborts", not merely "the halt_flag unit itself is skipped". Two otherwise fully
        valid, correctly-configured sibling units (one debit, one credit) sit in the same
        batch as the halt_flag unit -- build_posting_plan must raise before a
        ReconciliationPostingPlan even exists, so post_reconciliation_plan (and therefore
        the sink/ledger) can structurally never be reached for ANY unit in the batch, not
        just the halted one."""
        good_debit = _classified("UA001", "post_debit_adjustment", gap_cents=500)
        good_credit = _classified("UA002", "post_credit_receipt", gap_cents=-750, owner_party_id=uuid.uuid4())
        halted = _classified("UA003", "halt_flag")
        sink, ledger = _FakeSink(), _FakeLedger()

        with pytest.raises(BoundedReconciliationPostingError, match="halt_flag"):
            plan = build_posting_plan(
                classified_units=[good_debit, good_credit, halted], batch_doc=_batch(),
                executed_by=EXECUTED_BY, building_id=BUILDING_ID, financial_year="2026",
                snapshot_date="2026-08-01",
            )
            # Only reachable if the halt-flag guardrail regresses to a per-unit skip
            # instead of a whole-plan raise -- if it ever is, pytest.raises itself fails
            # with "DID NOT RAISE" here, and the assertions below would then show real
            # postings happened for the two otherwise-valid units.
            await post_reconciliation_plan(
                svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
                reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
                verify_unit_balance=_verify_exact_match,
            )

        assert not sink.adjustment_calls
        assert not sink.payment_calls
        assert ledger.lookups == []  # not even the idempotency pre-check ran for any unit

    def test_dual_control_checked_before_halt_scan(self):
        classified = [_classified("UA001", "halt_flag")]
        with pytest.raises(BoundedReconciliationPostingError, match="dual control"):
            build_posting_plan(
                classified_units=classified, batch_doc=_batch(approved_by=EXECUTED_BY),
                executed_by=EXECUTED_BY, building_id=BUILDING_ID, financial_year="2026",
                snapshot_date="2026-08-01",
            )

    def test_happy_path_plan(self):
        classified = [
            _classified("UA001", "post_debit_adjustment"),
            _classified("UA002", "post_credit_receipt"),
            _classified("UA003", "reconciled"),
        ]
        plan = build_posting_plan(
            classified_units=classified, batch_doc=_batch(), executed_by=EXECUTED_BY,
            building_id=BUILDING_ID, financial_year="2026", snapshot_date="2026-08-01",
        )
        assert plan.approved_by == APPROVED_BY
        assert plan.executed_by == EXECUTED_BY
        assert len(plan.postable_units) == 2
        assert plan.counts == {"post_debit_adjustment": 1, "post_credit_receipt": 1, "reconciled": 1}
        assert "staging_strata_web_snapshots" in plan.evidence_reference
        assert BUILDING_ID in plan.evidence_reference
        assert plan.expected_total_cents == 1000  # 500 (debit) + 500 (credit), both default gap_cents


def _plan(classified_units):
    return build_posting_plan(
        classified_units=classified_units, batch_doc=_batch(), executed_by=EXECUTED_BY,
        building_id=BUILDING_ID, financial_year="2026", snapshot_date="2026-08-01",
    )


class TestBuildCommands:
    def test_debit_command_uses_absolute_gap_and_idempotency_key(self):
        unit = _classified("UA001", "post_debit_adjustment", gap_cents=500)
        plan = _plan([unit])
        cmd = build_debit_command(
            unit=unit, plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=True,
        )
        assert cmd.amount_cents == 500
        assert cmd.financial_year == "2026"
        assert cmd.idempotency_key == "portal-recon-adj-UA001-2026"
        assert cmd.is_test_data is True
        assert cmd.metadata["source_tag"] == "portal_reconciliation"

    def test_credit_command_uses_absolute_gap_and_payer(self):
        owner_id = uuid.uuid4()
        unit = _classified("UA002", "post_credit_receipt", gap_cents=-750, owner_party_id=owner_id)
        plan = _plan([unit])
        cmd = build_credit_command(
            unit=unit, plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
        )
        assert cmd.amount_cents == 750
        assert cmd.payer_party_id == owner_id
        assert cmd.idempotency_key == "portal-recon-credit-UA002-2026"

    def test_credit_command_without_owner_raises(self):
        unit = _classified("UA003", "post_credit_receipt", gap_cents=-750, owner_party_id=None)
        plan = _plan([unit])
        with pytest.raises(BoundedReconciliationPostingError, match="no owner_party_id"):
            build_credit_command(
                unit=unit, plan=plan, scheme_ref=SCHEME_REF,
                reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            )


class _NestedCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False  # never suppress -- exceptions must propagate to the caller's try/except


class _FakeSession:
    def begin_nested(self):
        return _NestedCtx()


class _FakeLedger:
    def __init__(self, existing: dict | None = None):
        self.existing = existing or {}
        self.lookups: list[str] = []

    async def find_journal_entry_id_by_idempotency_key(self, scheme_ref, idempotency_key):
        self.lookups.append(idempotency_key)
        return self.existing.get(idempotency_key)


class _FakeAdjustmentResult:
    def __init__(self):
        self.journal_entry_id = uuid.uuid4()


class _FakeReceipt:
    def __init__(self):
        self.receipt_id = uuid.uuid4()
        self.journal_entry_id = uuid.uuid4()


class _FakeSink:
    def __init__(self):
        self.adjustment_calls = []
        self.payment_calls = []
        self.allocate_calls = []

    async def record_adjustment(self, cmd):
        self.adjustment_calls.append(cmd)
        return _FakeAdjustmentResult()

    async def record_historical_payment(self, cmd):
        self.payment_calls.append(cmd)
        return _FakeReceipt()

    async def allocate_payment(self, cmd):
        self.allocate_calls.append(cmd)
        return []


async def _verify_exact_match(unit_number: str, target_cents: int) -> int:
    """Always lands exactly on the portal target -- happy-path verifier."""
    return target_cents


class TestPostReconciliationPlan:
    @pytest.mark.asyncio
    async def test_reconciled_and_no_portal_data_are_skipped_not_posted(self):
        plan = _plan([
            _classified("UA001", "reconciled"),
            ClassifiedUnit(
                unit_input=_unit_input("UA002"),
                decision=BoundedAdjustmentDecision(
                    unit_number="UA002", pg_true_balance_cents=None, portal_balance_cents=None,
                    period_bound_cents=None, gap_cents=None, decision="no_portal_data", reason="x",
                ),
            ),
        ])
        sink, ledger = _FakeSink(), _FakeLedger()
        result = await post_reconciliation_plan(
            svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            verify_unit_balance=_verify_exact_match,
        )
        assert result.reconciled == 1
        assert result.no_portal_data == 1
        assert result.posted == 0
        assert not sink.adjustment_calls and not sink.payment_calls

    @pytest.mark.asyncio
    async def test_debit_unit_posts_via_record_adjustment(self):
        unit = _classified("UA001", "post_debit_adjustment", gap_cents=500)
        plan = _plan([unit])
        sink, ledger = _FakeSink(), _FakeLedger()
        result = await post_reconciliation_plan(
            svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            verify_unit_balance=_verify_exact_match,
        )
        assert result.posted == 1
        assert result.posted_total_cents == 500
        assert len(sink.adjustment_calls) == 1
        assert not sink.payment_calls
        assert result.outcomes[0].status == "posted"

    @pytest.mark.asyncio
    async def test_credit_unit_posts_via_payment_then_allocate(self):
        owner_id = uuid.uuid4()
        unit = _classified("UA002", "post_credit_receipt", gap_cents=-750, owner_party_id=owner_id)
        plan = _plan([unit])
        sink, ledger = _FakeSink(), _FakeLedger()
        result = await post_reconciliation_plan(
            svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            verify_unit_balance=_verify_exact_match,
        )
        assert result.posted == 1
        assert result.posted_total_cents == 750
        assert len(sink.payment_calls) == 1
        assert len(sink.allocate_calls) == 1
        assert str(sink.allocate_calls[0].receipt_id) == result.outcomes[0].receipt_id

    @pytest.mark.asyncio
    async def test_idempotent_replay_skips_without_calling_sink(self):
        unit = _classified("UA001", "post_debit_adjustment", gap_cents=500)
        plan = _plan([unit])
        existing_journal_id = uuid.uuid4()
        sink = _FakeSink()
        ledger = _FakeLedger(existing={"portal-recon-adj-UA001-2026": existing_journal_id})
        result = await post_reconciliation_plan(
            svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            verify_unit_balance=_verify_exact_match,
        )
        assert result.replayed == 1
        assert result.posted == 0
        assert not sink.adjustment_calls
        assert result.outcomes[0].journal_entry_id == str(existing_journal_id)

    @pytest.mark.asyncio
    async def test_unhandled_decision_value_is_error_not_silently_posted_as_credit(self):
        """A future classifier decision this module has no branch for must fail loudly as an
        'error' outcome, never fall through the old bare `else` and get silently posted as a
        credit receipt (regression test for the elif/else-raise fix)."""
        unit = ClassifiedUnit(
            unit_input=_unit_input("UA009"),
            decision=_decision("UA009", "post_manual_review", gap_cents=500, portal_balance_cents=1500),
        )
        plan = _plan([unit])
        sink, ledger = _FakeSink(), _FakeLedger()
        result = await post_reconciliation_plan(
            svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            verify_unit_balance=_verify_exact_match,
        )
        assert result.posted == 0
        assert result.has_errors is True
        assert result.outcomes[0].status == "error"
        assert "unhandled classifier decision" in result.outcomes[0].error
        assert not sink.adjustment_calls and not sink.payment_calls

    @pytest.mark.asyncio
    async def test_post_apply_assertion_failure_is_error_not_raised(self):
        unit = _classified("UA001", "post_debit_adjustment", gap_cents=500, portal_balance_cents=1500)
        plan = _plan([unit])
        sink, ledger = _FakeSink(), _FakeLedger()

        async def _wrong_balance(unit_number, target_cents):
            return target_cents + 99999  # wildly off -> fails tolerance

        result = await post_reconciliation_plan(
            svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            verify_unit_balance=_wrong_balance,
        )
        assert result.posted == 0
        assert result.has_errors is True
        assert result.outcomes[0].status == "error"
        assert "post-adjustment true_balance" in result.outcomes[0].error

    @pytest.mark.asyncio
    async def test_one_unit_error_does_not_roll_back_another_units_success(self):
        good_unit = _classified("UA001", "post_debit_adjustment", gap_cents=500, portal_balance_cents=1500)
        bad_unit = ClassifiedUnit(
            unit_input=_unit_input("UA002", owner_party_id=None),
            decision=_decision("UA002", "post_credit_receipt", gap_cents=-750, portal_balance_cents=250),
        )
        plan = _plan([good_unit, bad_unit])
        sink, ledger = _FakeSink(), _FakeLedger()
        result = await post_reconciliation_plan(
            svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            verify_unit_balance=_verify_exact_match,
        )
        assert result.posted == 1  # UA001 succeeded
        assert result.has_errors is True  # UA002 (no owner) errored
        statuses = {o.unit_number: o.status for o in result.outcomes}
        assert statuses["UA001"] == "posted"
        assert statuses["UA002"] == "error"
        assert len(sink.adjustment_calls) == 1  # only the good unit reached the sink

    @pytest.mark.asyncio
    async def test_full_run_wiring_satisfies_run_total_integrity(self):
        """End-to-end wiring sanity check: a normal successful run (mixed debit+credit) must
        naturally satisfy the run-total integrity check. This would have caught a real wiring
        bug found in review -- forgetting to pass plan.expected_total_cents into the
        ReconciliationPostingResult constructor left expected_total_cents at its 0 default."""
        owner_id = uuid.uuid4()
        debit_unit = _classified("UA001", "post_debit_adjustment", gap_cents=500, portal_balance_cents=1500)
        credit_unit = _classified("UA002", "post_credit_receipt", gap_cents=-750, portal_balance_cents=250,
                                   owner_party_id=owner_id)
        plan = _plan([debit_unit, credit_unit, _classified("UA003", "reconciled")])
        sink, ledger = _FakeSink(), _FakeLedger()
        result = await post_reconciliation_plan(
            svc=sink, ledger=ledger, session=_FakeSession(), plan=plan, scheme_ref=SCHEME_REF,
            reconstruction_batch_id=uuid.uuid4(), is_test_data=False,
            verify_unit_balance=_verify_exact_match,
        )
        assert result.posted == 2
        assert result.has_errors is False
        assert result.expected_total_cents == 1250  # 500 + 750
        assert result.covered_total_cents == 1250
        assert result.run_total_integrity_ok is True
        assert not any(o.unit_number == "__RUN_TOTAL__" for o in result.outcomes)


class TestReconciliationPostingResultIntegrity:
    """Direct, hand-crafted-outcome tests of run_total_integrity_ok/covered_total_cents --
    the full posting loop can only ever produce a matching total when there are no per-unit
    errors (by construction), so the mismatch branch is tested against the dataclass directly
    rather than through post_reconciliation_plan."""

    def test_ok_when_no_errors_and_totals_match(self):
        result = ReconciliationPostingResult(
            expected_total_cents=1000, posted_total_cents=1000,
            outcomes=[UnitPostingOutcome(unit_number="UA001", decision="post_debit_adjustment", status="posted")],
        )
        assert result.has_errors is False
        assert result.run_total_integrity_ok is True

    def test_covered_total_includes_replayed_units(self):
        result = ReconciliationPostingResult(
            expected_total_cents=1000, posted_total_cents=400,
            outcomes=[
                UnitPostingOutcome(unit_number="UA001", decision="post_debit_adjustment",
                                    status="posted", gap_cents=400),
                UnitPostingOutcome(unit_number="UA002", decision="post_credit_receipt",
                                    status="replayed", gap_cents=-600),
            ],
        )
        assert result.covered_total_cents == 1000  # 400 posted + 600 replayed
        assert result.run_total_integrity_ok is True

    def test_not_ok_when_no_errors_but_totals_mismatch(self):
        result = ReconciliationPostingResult(
            expected_total_cents=1000, posted_total_cents=400,
            outcomes=[UnitPostingOutcome(unit_number="UA001", decision="post_debit_adjustment",
                                          status="posted", gap_cents=400)],
        )
        assert result.has_errors is False
        assert result.run_total_integrity_ok is False

    def test_vacuously_ok_when_has_errors_even_if_totals_mismatch(self):
        """A real per-unit error already explains a totals shortfall -- the integrity check
        must not pile on a second, redundant failure signal for the same root cause."""
        result = ReconciliationPostingResult(
            expected_total_cents=1000, posted_total_cents=0,
            outcomes=[UnitPostingOutcome(unit_number="UA001", decision="post_debit_adjustment",
                                          status="error", error="boom")],
        )
        assert result.has_errors is True
        assert result.run_total_integrity_ok is True
