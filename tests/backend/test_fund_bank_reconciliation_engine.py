"""Tests for services/finance_metrics/fund_bank_reconciliation_engine.py (GAP-ONBOARD-004 A3).

Pure-logic tests against a dispatch-based mocked session (no live DB) — the mock inspects each
SQL statement's own SQL text to decide which canned result to return, since
compute_fund_bank_reconciliation issues several distinct queries per fund in sequence.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.finance_metrics.fund_bank_reconciliation_engine import (
    FundBankReconciliationLine,
    _ADJUSTMENT_SQL,
    _EXPENSE_SQL,
    _LEVIED_SQL,
    _RECEIPT_ALLOCATION_GAP_SQL,
    _RECEIVED_SQL,
    _UNATTRIBUTED_ADJUSTMENT_SQL,
    _calendar_year_bounds,
    compute_fund_bank_reconciliation,
    persist_fund_bank_reconciliation,
)


def _scalar_result(value):
    return SimpleNamespace(scalar=lambda: value, first=lambda: None)


def _rows_result(rows):
    return SimpleNamespace(fetchall=lambda: rows, first=lambda: (rows[0] if rows else None))


def _first_result(row):
    return SimpleNamespace(first=lambda: row, fetchall=lambda: ([row] if row else []))


def _dispatching_session(*, funds, prior_closing=None, levied=None, received=None,
                          expense=None, adjustment=None, unattributed=0, gap=0):
    """A session whose execute() inspects the compiled SQL text of each query to route to the
    right canned response, rather than relying on strict call ordering."""
    prior_closing = prior_closing or {}
    levied = levied or {}
    received = received or {}
    expense = expense or {}
    adjustment = adjustment or {}

    async def _execute(clause, params=None):
        sql = str(clause)
        params = params or {}
        if "FROM finance.funds" in sql:
            return _rows_result(funds)
        if "unattributed_cents" in sql:
            return _scalar_result(unattributed)
        if "gap_cents" in sql:
            return _scalar_result(gap)
        if "FROM finance.fund_bank_reconciliations" in sql:
            fid = params.get("fid")
            if fid in prior_closing:
                return _first_result(SimpleNamespace(computed_closing_cents=prior_closing[fid]))
            return _first_result(None)
        if "levied_cents" in sql:
            return _scalar_result(levied.get(params.get("fid"), 0))
        if "received_cents" in sql:
            return _scalar_result(received.get(params.get("fid"), 0))
        if "expense_cents" in sql:
            return _scalar_result(expense.get(params.get("fid"), 0))
        if "adjustment_cents" in sql:
            return _scalar_result(adjustment.get(params.get("fid"), 0))
        raise AssertionError(f"Unexpected query in mock: {sql[:80]}")

    session = SimpleNamespace()
    session.execute = AsyncMock(side_effect=_execute)
    return session


_ADMIN_FUND = SimpleNamespace(fund_id="fund-admin", fund_type="admin", opening_balance_cents=0)
_SINKING_FUND = SimpleNamespace(fund_id="fund-sinking", fund_type="sinking", opening_balance_cents=0)


class TestCalendarYearBounds:
    def test_returns_date_objects_not_strings(self):
        start, end = _calendar_year_bounds("2026")
        assert start == date(2026, 1, 1)
        assert end == date(2027, 1, 1)

    def test_rejects_non_calendar_year(self):
        with pytest.raises(ValueError):
            _calendar_year_bounds("2025-2026")


class TestComputeFundBankReconciliation:
    @pytest.mark.asyncio
    async def test_genesis_opening_on_first_year(self):
        session = _dispatching_session(
            funds=[_ADMIN_FUND],
            levied={"fund-admin": 100_000},
            received={"fund-admin": 100_000},
            expense={"fund-admin": 40_000},
        )
        lines = await compute_fund_bank_reconciliation(
            session, scheme_id="s", tenant_id="t", financial_year="2021"
        )
        assert len(lines) == 1
        line = lines[0]
        assert line.opening_provenance == "genesis"
        assert line.opening_balance_cents == 0
        assert line.computed_closing_cents == 100_000 - 40_000

    @pytest.mark.asyncio
    async def test_carries_forward_from_prior_persisted_row(self):
        session = _dispatching_session(
            funds=[_ADMIN_FUND],
            prior_closing={"fund-admin": -36_123},
            levied={"fund-admin": 50_000},
            received={"fund-admin": 50_000},
            expense={"fund-admin": 10_000},
        )
        lines = await compute_fund_bank_reconciliation(
            session, scheme_id="s", tenant_id="t", financial_year="2022"
        )
        line = lines[0]
        assert line.opening_provenance == "carried_forward"
        assert line.opening_balance_cents == -36_123
        assert line.computed_closing_cents == -36_123 + 50_000 - 10_000

    @pytest.mark.asyncio
    async def test_prior_closing_override_takes_precedence_over_db_row(self):
        """The dry-run multi-year preview case: the DB has no persisted prior row, but the
        caller supplies the in-memory result from the previous iteration."""
        session = _dispatching_session(
            funds=[_ADMIN_FUND],
            prior_closing={},  # nothing persisted
            levied={"fund-admin": 10_000}, received={"fund-admin": 10_000}, expense={"fund-admin": 0},
        )
        lines = await compute_fund_bank_reconciliation(
            session, scheme_id="s", tenant_id="t", financial_year="2022",
            prior_closing_override={"fund-admin": 99_999},
        )
        line = lines[0]
        assert line.opening_provenance == "carried_forward"
        assert line.opening_balance_cents == 99_999

    @pytest.mark.asyncio
    async def test_interest_is_always_zero_with_a_notes_flag(self):
        session = _dispatching_session(funds=[_ADMIN_FUND])
        lines = await compute_fund_bank_reconciliation(
            session, scheme_id="s", tenant_id="t", financial_year="2026"
        )
        line = lines[0]
        assert line.interest_cents == 0
        assert "not a confirmed zero" in line.notes.lower()

    @pytest.mark.asyncio
    async def test_unattributed_adjustment_surfaced_not_folded_into_any_fund(self):
        session = _dispatching_session(funds=[_ADMIN_FUND, _SINKING_FUND], unattributed=785_130)
        lines = await compute_fund_bank_reconciliation(
            session, scheme_id="s", tenant_id="t", financial_year="2026"
        )
        for line in lines:
            assert line.unattributed_adjustment_cents == 785_130
            # never added into this fund's own adjustment_cents
            assert line.adjustment_cents == 0
            assert "785130" not in str(line.computed_closing_cents)
            assert "could not be attributed to a specific fund" in line.notes

    @pytest.mark.asyncio
    async def test_receipt_allocation_gap_surfaced_and_excluded_from_received(self):
        session = _dispatching_session(
            funds=[_ADMIN_FUND], received={"fund-admin": 119_707}, gap=1_728_345,
        )
        lines = await compute_fund_bank_reconciliation(
            session, scheme_id="s", tenant_id="t", financial_year="2021"
        )
        line = lines[0]
        assert line.receipt_allocation_gap_cents == 1_728_345
        assert line.received_cents == 119_707  # gap not added back in
        assert "receipt_allocation_gap_cents" in line.notes

    @pytest.mark.asyncio
    async def test_no_funds_returns_empty_list(self):
        session = _dispatching_session(funds=[])
        lines = await compute_fund_bank_reconciliation(
            session, scheme_id="s", tenant_id="t", financial_year="2026"
        )
        assert lines == []

    @pytest.mark.asyncio
    async def test_rejects_non_calendar_year(self):
        session = _dispatching_session(funds=[_ADMIN_FUND])
        with pytest.raises(ValueError):
            await compute_fund_bank_reconciliation(
                session, scheme_id="s", tenant_id="t", financial_year="FY2026"
            )


class TestOnlyPostedJournalEntriesCounted:
    """Self-audit fix (2026-08-10): finance.journal_entries.status is a real enum
    (draft/pending_approval/posted/reversed/voided) -- save_journal_entry() always creates
    entries as DRAFT first (services/financial_core/adapters/db_postgres/ledger_repo.py), and
    this codebase's whole reconstruction-batch workflow is built around entries sitting in
    draft/pending_approval for real periods pending dual-control approval. None of these
    queries originally filtered on status, so a draft/pending/voided journal entry's receipt,
    expense, or adjustment would have been wrongly counted as real cash movement. Static
    checks (not full SQL execution) that the fix is actually present in each query."""

    def test_received_sql_filters_posted_status(self):
        assert "je.status = 'posted'" in str(_RECEIVED_SQL)

    def test_expense_sql_filters_posted_status(self):
        assert "je.status = 'posted'" in str(_EXPENSE_SQL)

    def test_adjustment_sql_filters_posted_status(self):
        assert "je.status = 'posted'" in str(_ADJUSTMENT_SQL)

    def test_unattributed_adjustment_sql_filters_posted_status(self):
        assert "je.status = 'posted'" in str(_UNATTRIBUTED_ADJUSTMENT_SQL)

    def test_receipt_allocation_gap_sql_filters_posted_status(self):
        assert "je.status = 'posted'" in str(_RECEIPT_ALLOCATION_GAP_SQL)

    def test_levied_sql_filters_posted_status(self):
        assert "je.status = 'posted'" in str(_LEVIED_SQL)


class TestPersistFundBankReconciliation:
    @pytest.mark.asyncio
    async def test_upserts_each_line_and_returns_ids(self):
        session = SimpleNamespace()
        session.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar=lambda: "recon-id-1"),
                SimpleNamespace(scalar=lambda: "recon-id-2"),
            ]
        )
        lines = [
            FundBankReconciliationLine(
                scheme_id="s", fund_id="fund-admin", fund_type="admin", financial_year="2026",
                period_label="FY", as_of=date(2026, 12, 31), opening_balance_cents=0,
                opening_provenance="genesis", levied_cents=1000, received_cents=1000,
                expense_cents=200, interest_cents=0, adjustment_cents=0,
                computed_closing_cents=800, unattributed_adjustment_cents=0,
                receipt_allocation_gap_cents=0, notes="note-a",
            ),
            FundBankReconciliationLine(
                scheme_id="s", fund_id="fund-sinking", fund_type="sinking", financial_year="2026",
                period_label="FY", as_of=date(2026, 12, 31), opening_balance_cents=0,
                opening_provenance="genesis", levied_cents=500, received_cents=500,
                expense_cents=0, interest_cents=0, adjustment_cents=0,
                computed_closing_cents=500, unattributed_adjustment_cents=0,
                receipt_allocation_gap_cents=0, notes="note-b",
            ),
        ]
        ids = await persist_fund_bank_reconciliation(session, lines, tenant_id="t")
        assert ids == ["recon-id-1", "recon-id-2"]
        assert session.execute.await_count == 2
        # never posts a journal or calls financial_core -- confirmed by construction: this
        # module has no import of services.financial_core anywhere (see module docstring).

    @pytest.mark.asyncio
    async def test_metadata_is_valid_json_via_json_dumps(self):
        """Self-audit fix (2026-08-10): metadata used to be hand-built via f-string
        interpolation -- safe today only because opening_provenance is code-controlled to two
        fixed values, but fragile (any value containing a quote/backslash would produce
        invalid JSON and fail the DB-side jsonb cast). Now uses json.dumps()."""
        import json as json_module

        captured = {}

        async def _execute(clause, params=None):
            captured.update(params or {})
            return SimpleNamespace(scalar=lambda: "recon-id-1")

        session = SimpleNamespace()
        session.execute = AsyncMock(side_effect=_execute)
        line = FundBankReconciliationLine(
            scheme_id="s", fund_id="fund-admin", fund_type="admin", financial_year="2026",
            period_label="FY", as_of=date(2026, 12, 31), opening_balance_cents=0,
            opening_provenance="genesis", levied_cents=1000, received_cents=1000,
            expense_cents=200, interest_cents=0, adjustment_cents=0,
            computed_closing_cents=800, unattributed_adjustment_cents=12345,
            receipt_allocation_gap_cents=678, notes="note",
        )
        await persist_fund_bank_reconciliation(session, [line], tenant_id="t")

        parsed = json_module.loads(captured["metadata"])  # raises if not valid JSON
        assert parsed == {
            "unattributed_adjustment_cents": 12345,
            "receipt_allocation_gap_cents": 678,
            "opening_provenance": "genesis",
        }
