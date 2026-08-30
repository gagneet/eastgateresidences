"""Unit tests for services/reconstruction_generators/expense_posting_service.py — the shared,
single-sourced posting core consumed by BOTH scripts/historical_expense_posting.py and the in-app
endpoint routers/financial_onboarding.py::post_reconstruction_batch_expenses.

All the financial-risk logic (dual-control, manifest drift/integrity, GST-split amounts, per-fund
totals, post-apply integrity) lives here, so it is tested directly and thoroughly. The pure
functions need no DB; run_expense_posting's DB path is covered only for its pre-session
short-circuits (a full posting round-trip is exercised through post_expense_plan directly).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from services.reconstruction_generators.expense_posting_service import (
    ExpensePostingError,
    ExpensePostingResult,
    _extract_category_name,
    build_expense_command,
    post_expense_plan,
    run_expense_posting,
    validate_batch_authorisation,
    validate_manifest_and_extract_debits,
)
from services.financial_core.domain.entities import SchemeRef

BUILDING_ID = "16244"
BATCH_ID = str(uuid.uuid4())
APPROVED_BY = uuid.uuid4()
EXECUTED_BY = uuid.uuid4()


def _debit_row(**overrides) -> dict:
    defaults = dict(
        account_ref="ADMIN-16244", unit_number="", financial_year="2024", quarter=None,
        fund_type="admin", levy_component="ordinary", posted_date="2024-12-31",
        amount_cents=11000, amount_ex_gst_cents=10000, gst_cents=1000, direction="debit",
        assumption_code="actual_category_spend",
        description=(
            "Estimated 2024 spend summary: Cleaning (Admin Fund) — reported ANNUAL TOTAL, "
            "not a single dated transaction; posted_date is a summary effective date, not a "
            "real invoice/payment date."
        ),
        transaction_sequence=1, payment_group_id=None,
    )
    defaults.update(overrides)
    return defaults


def _credit_row(**overrides) -> dict:
    row = _debit_row(**overrides)
    row["direction"] = "credit"
    return row


class _FakeSink:
    """Minimal FinancialCoreService stand-in: records commands, returns a receipt whose
    metadata.replayed is controllable so posted-vs-replayed counting can be asserted."""

    def __init__(self, replayed: bool = False):
        self.commands = []
        self._replayed = replayed

    async def record_historical_expense(self, cmd):
        self.commands.append(cmd)

        class _Receipt:
            metadata = {"replayed": self._replayed}

        return _Receipt()


class TestExtractCategoryName:
    def test_admin_and_sinking(self):
        assert _extract_category_name(
            "Estimated 2023 spend summary: Cleaning (Admin Fund) — x"
        ) == "Cleaning"
        assert _extract_category_name(
            "Estimated 2025 spend summary: Roof Repairs (Sinking Fund) — x"
        ) == "Roof Repairs"

    def test_fund_keyword_in_name_still_extracts(self):
        assert _extract_category_name(
            "Estimated 2025 spend summary: Sinking Fund Contribution (Sinking Fund) — x"
        ) == "Sinking Fund Contribution"

    def test_fallbacks(self):
        assert _extract_category_name("free text") == "free text"
        assert _extract_category_name("") == "Unspecified category"


class TestValidateBatchAuthorisation:
    def test_missing_approved_by_raises(self):
        with pytest.raises(ExpensePostingError, match="no approved_by"):
            validate_batch_authorisation(
                batch_doc={"batch_id": BATCH_ID, "approved_by": None}, executed_by=EXECUTED_BY,
            )

    def test_same_approver_and_executor_raises(self):
        with pytest.raises(ExpensePostingError, match="dual control"):
            validate_batch_authorisation(
                batch_doc={"batch_id": BATCH_ID, "approved_by": str(EXECUTED_BY)},
                executed_by=EXECUTED_BY,
            )

    def test_distinct_actors_returns_approved_by(self):
        approved_by = validate_batch_authorisation(
            batch_doc={"batch_id": BATCH_ID, "approved_by": str(APPROVED_BY)},
            executed_by=EXECUTED_BY,
        )
        assert approved_by == APPROVED_BY


class TestValidateManifestAndExtractDebits:
    def _batch(self):
        return {"batch_id": BATCH_ID, "reviewed_by": str(uuid.uuid4()), "manifest_hash": "h"}

    def test_stale_manifest_hash_raises(self):
        with pytest.raises(ExpensePostingError, match="Manifest has changed"):
            validate_manifest_and_extract_debits(
                batch_doc=self._batch(),
                manifest_doc={"manifest_hash": "stale", "transactions": [_debit_row()]},
                approved_by=APPROVED_BY, building_id=BUILDING_ID, batch_id=BATCH_ID,
            )

    def test_self_inconsistent_manifest_raises(self):
        with pytest.raises(ExpensePostingError, match="integrity check failed"):
            validate_manifest_and_extract_debits(
                batch_doc=self._batch(),
                manifest_doc={
                    "manifest_hash": "h",
                    "transactions": [_debit_row(amount_cents=11000)],
                    "expected_debit_cents": 999999,
                },
                approved_by=APPROVED_BY, building_id=BUILDING_ID, batch_id=BATCH_ID,
            )

    def test_credit_rows_excluded_and_totals_sum_ex_plus_gst(self):
        plan = validate_manifest_and_extract_debits(
            batch_doc=self._batch(),
            manifest_doc={
                "manifest_hash": "h",
                "transactions": [
                    _credit_row(amount_cents=500000),
                    _debit_row(amount_cents=11000, amount_ex_gst_cents=10000, gst_cents=1000),
                    _debit_row(
                        fund_type="sinking", amount_cents=22000,
                        amount_ex_gst_cents=20000, gst_cents=2000,
                    ),
                ],
                "expected_debit_cents": 33000,
            },
            approved_by=APPROVED_BY, building_id=BUILDING_ID, batch_id=BATCH_ID,
        )
        assert len(plan.expense_rows) == 2  # credit row dropped
        assert plan.expected_total_cents == 33000  # (10000+1000) + (20000+2000)

    def test_credit_only_manifest_is_empty_plan_not_error(self):
        plan = validate_manifest_and_extract_debits(
            batch_doc=self._batch(),
            manifest_doc={"manifest_hash": "h", "transactions": [_credit_row()]},
            approved_by=APPROVED_BY, building_id=BUILDING_ID, batch_id=BATCH_ID,
        )
        assert plan.expense_rows == []
        assert plan.expected_total_cents == 0


class TestBuildExpenseCommand:
    def test_builds_gst_split_command_with_idempotency_key(self):
        cmd = build_expense_command(
            row=_debit_row(),
            building_id=BUILDING_ID, batch_id=BATCH_ID,
            scheme_ref=SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4()),
            fund_ids={"admin": uuid.uuid4()},
            reason="r", approved_by=APPROVED_BY, executed_by=EXECUTED_BY, is_test_data=True,
        )
        assert cmd.amount_cents == 10000  # ex-GST
        assert cmd.gst_cents == 1000
        assert cmd.category_name == "Cleaning"
        assert cmd.idempotency_key == f"historical-expense:{BUILDING_ID}:2024:admin:cleaning"
        assert cmd.is_test_data is True

    def test_unresolved_fund_id_raises(self):
        with pytest.raises(ExpensePostingError, match="No fund_id resolved"):
            build_expense_command(
                row=_debit_row(fund_type="sinking"),
                building_id=BUILDING_ID, batch_id=BATCH_ID,
                scheme_ref=SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4()),
                fund_ids={"admin": uuid.uuid4()},  # no 'sinking'
                reason="r", approved_by=APPROVED_BY, executed_by=EXECUTED_BY, is_test_data=False,
            )


def _plan(rows, expected_total):
    from services.reconstruction_generators.expense_posting_service import ExpensePostingPlan

    return ExpensePostingPlan(
        expense_rows=rows, approved_by=APPROVED_BY, reason="r", expected_total_cents=expected_total,
    )


class TestPostExpensePlan:
    @pytest.mark.asyncio
    async def test_posts_all_debit_rows_with_per_fund_totals(self):
        sink = _FakeSink()
        plan = _plan(
            [
                _debit_row(amount_ex_gst_cents=10000, gst_cents=1000),
                _debit_row(fund_type="sinking", amount_ex_gst_cents=20000, gst_cents=2000),
            ],
            expected_total=33000,
        )
        result = await post_expense_plan(
            svc=sink, plan=plan, building_id=BUILDING_ID, batch_id=BATCH_ID,
            scheme_ref=SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4()),
            fund_ids={"admin": uuid.uuid4(), "sinking": uuid.uuid4()},
            executed_by=EXECUTED_BY, is_test_data=False,
        )
        assert len(sink.commands) == 2
        assert result.posted == 2
        assert result.replayed == 0
        assert result.posted_total_cents == 33000
        assert result.per_fund_cents == {"admin": 11000, "sinking": 22000}

    @pytest.mark.asyncio
    async def test_replays_counted_separately(self):
        sink = _FakeSink(replayed=True)
        plan = _plan([_debit_row(amount_ex_gst_cents=10000, gst_cents=1000)], expected_total=11000)
        result = await post_expense_plan(
            svc=sink, plan=plan, building_id=BUILDING_ID, batch_id=BATCH_ID,
            scheme_ref=SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4()),
            fund_ids={"admin": uuid.uuid4()}, executed_by=EXECUTED_BY, is_test_data=False,
        )
        assert result.posted == 0
        assert result.replayed == 1

    @pytest.mark.asyncio
    async def test_post_apply_total_mismatch_raises(self):
        sink = _FakeSink()
        # Plan claims 99999 expected but rows sum to 11000 -> integrity failure after posting.
        plan = _plan([_debit_row(amount_ex_gst_cents=10000, gst_cents=1000)], expected_total=99999)
        with pytest.raises(ExpensePostingError, match="Post-apply integrity check failed"):
            await post_expense_plan(
                svc=sink, plan=plan, building_id=BUILDING_ID, batch_id=BATCH_ID,
                scheme_ref=SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4()),
                fund_ids={"admin": uuid.uuid4()}, executed_by=EXECUTED_BY, is_test_data=False,
            )


_MODULE = "services.reconstruction_generators.expense_posting_service"


class TestRunExpensePostingShortCircuits:
    @pytest.mark.asyncio
    async def test_dual_control_rejected_before_session(self, monkeypatch):
        """A same-approver attempt must raise before any Postgres session is opened."""
        monkeypatch.setattr(
            f"{_MODULE}._get_batch_doc",
            AsyncMock(return_value={"batch_id": BATCH_ID, "approved_by": str(EXECUTED_BY)}),
        )
        # validate_batch_authorisation runs before the Postgres session block, so a same-approver
        # attempt raises without ever opening a session (mongo_db is a bare object() here).
        with pytest.raises(ExpensePostingError, match="dual control"):
            await run_expense_posting(
                mongo_db=object(), building_id=BUILDING_ID, batch_id=BATCH_ID,
                executed_by=EXECUTED_BY, is_test_data=False,
            )

    @pytest.mark.asyncio
    async def test_credit_only_returns_empty_result_without_session(self, monkeypatch):
        monkeypatch.setattr(
            f"{_MODULE}._get_batch_doc",
            AsyncMock(return_value={
                "batch_id": BATCH_ID, "approved_by": str(APPROVED_BY),
                "reviewed_by": str(uuid.uuid4()), "manifest_hash": "h",
            }),
        )
        monkeypatch.setattr(
            f"{_MODULE}._get_latest_manifest_doc",
            AsyncMock(return_value={"manifest_hash": "h", "transactions": [_credit_row()]}),
        )
        result = await run_expense_posting(
            mongo_db=object(), building_id=BUILDING_ID, batch_id=BATCH_ID,
            executed_by=EXECUTED_BY, is_test_data=False,
        )
        assert isinstance(result, ExpensePostingResult)
        assert result.line_count == 0
        assert result.posted == 0
