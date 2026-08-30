"""Tests for services/finance_metrics/lot_true_balance.py (GAP-FIN-036).

Verifies the canonical per-lot TRUE balance: arrears (outstanding) plus the unapplied-credit
dimension that finance.levy_items cannot represent, computed strictly per-lot and never netted
across lots (CLAUDE.md rule 10). Pure-logic tests — the two SELECTs are mocked, so no live DB.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.finance_metrics.lot_true_balance import (
    LotTrueBalance,
    building_unapplied_credit_cents,
    compute_lot_true_balances,
    compute_unit_true_balance,
)


def _outstanding_row(lot_id: str, cents: int) -> SimpleNamespace:
    return SimpleNamespace(lot_id=lot_id, outstanding_cents=cents)


def _credit_row(lot_id: str, cents: int) -> SimpleNamespace:
    return SimpleNamespace(lot_id=lot_id, credit_cents=cents)


def _mock_session_for_building(outstanding_rows, credit_rows):
    """A session whose execute() returns outstanding rows on the 1st call, credit rows on the
    2nd — the exact call order in compute_lot_true_balances.
    """
    result_outstanding = SimpleNamespace(fetchall=lambda: outstanding_rows)
    result_credit = SimpleNamespace(fetchall=lambda: credit_rows)
    session = SimpleNamespace()
    session.execute = AsyncMock(side_effect=[result_outstanding, result_credit])
    return session


class TestLotTrueBalanceRecord:
    def test_true_balance_is_signed(self):
        arrears = LotTrueBalance(lot_id="a", outstanding_cents=5000, unapplied_credit_cents=0)
        assert arrears.true_balance_cents == 5000
        assert arrears.is_in_credit is False

        credit = LotTrueBalance(lot_id="b", outstanding_cents=0, unapplied_credit_cents=1900)
        assert credit.true_balance_cents == -1900
        assert credit.is_in_credit is True


class TestComputeLotTrueBalances:
    @pytest.mark.asyncio
    async def test_arrears_lot_has_positive_true_balance(self):
        session = _mock_session_for_building(
            outstanding_rows=[_outstanding_row("lot-arrears", 40000)],
            credit_rows=[],
        )
        out = await compute_lot_true_balances(
            session, scheme_id="s", tenant_id="t", financial_year="2026"
        )
        assert out["lot-arrears"].outstanding_cents == 40000
        assert out["lot-arrears"].unapplied_credit_cents == 0
        assert out["lot-arrears"].true_balance_cents == 40000

    @pytest.mark.asyncio
    async def test_overpaid_lot_with_no_open_items_still_appears_as_credit(self):
        """The GAP-FIN-036 core case: a lot fully paid then overpaid has NO outstanding row
        but a real credit — it must appear with a negative true balance, not vanish."""
        session = _mock_session_for_building(
            outstanding_rows=[],  # nothing outstanding for this lot
            credit_rows=[_credit_row("lot-credit", 19000)],
        )
        out = await compute_lot_true_balances(
            session, scheme_id="s", tenant_id="t", financial_year="2026"
        )
        assert "lot-credit" in out
        assert out["lot-credit"].outstanding_cents == 0
        assert out["lot-credit"].unapplied_credit_cents == 19000
        assert out["lot-credit"].true_balance_cents == -19000

    @pytest.mark.asyncio
    async def test_credit_is_never_netted_across_lots(self):
        """One lot's credit must never reduce another lot's arrears."""
        session = _mock_session_for_building(
            outstanding_rows=[_outstanding_row("owes", 30000)],
            credit_rows=[_credit_row("ahead", 25000)],
        )
        out = await compute_lot_true_balances(
            session, scheme_id="s", tenant_id="t", financial_year="2026"
        )
        assert out["owes"].true_balance_cents == 30000  # unchanged by the other lot's credit
        assert out["ahead"].true_balance_cents == -25000
        # building rollup is the SUM of per-lot credit, not a net of arrears vs credit
        assert building_unapplied_credit_cents(out) == 25000

    @pytest.mark.asyncio
    async def test_rejects_non_calendar_year(self):
        session = _mock_session_for_building([], [])
        with pytest.raises(ValueError):
            await compute_lot_true_balances(
                session, scheme_id="s", tenant_id="t", financial_year="2025-2026"
            )


class TestComputeUnitTrueBalance:
    def _mock_unit_session(self, outstanding_cents, credit_cents, lot_id="lot-1"):
        result_out = SimpleNamespace(first=lambda: SimpleNamespace(outstanding_cents=outstanding_cents))
        result_credit = SimpleNamespace(first=lambda: SimpleNamespace(credit_cents=credit_cents))
        result_lot = SimpleNamespace(first=lambda: SimpleNamespace(lot_id=lot_id))
        session = SimpleNamespace()
        session.execute = AsyncMock(side_effect=[result_out, result_credit, result_lot])
        return session

    @pytest.mark.asyncio
    async def test_unit_in_credit(self):
        session = self._mock_unit_session(outstanding_cents=0, credit_cents=25498)
        tb = await compute_unit_true_balance(
            session, scheme_id="s", tenant_id="t", unit_number="TH087", financial_year="2026"
        )
        assert tb is not None
        assert tb.unapplied_credit_cents == 25498
        assert tb.true_balance_cents == -25498

    @pytest.mark.asyncio
    async def test_unit_with_no_ledger_rows_returns_none_not_zero(self):
        """Missing != zero: a unit with no levy_items and no receipts is unknown, not $0."""
        session = SimpleNamespace()
        session.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(first=lambda: SimpleNamespace(outstanding_cents=0)),
                SimpleNamespace(first=lambda: SimpleNamespace(credit_cents=0)),
            ]
        )
        tb = await compute_unit_true_balance(
            session, scheme_id="s", tenant_id="t", unit_number="UA999", financial_year="2026"
        )
        assert tb is None

    @pytest.mark.asyncio
    async def test_unit_rejects_non_calendar_year(self):
        session = SimpleNamespace(execute=AsyncMock())
        with pytest.raises(ValueError):
            await compute_unit_true_balance(
                session, scheme_id="s", tenant_id="t", unit_number="UA001",
                financial_year="FY2026",
            )


# ---------------------------------------------------------------------------
# Reversed and retired receipts must not read as owner credit (2026-08-28)
# ---------------------------------------------------------------------------

class TestReversedAndRetiredReceiptsAreNotCredit:
    """Two clauses that were missing, each worth real money on live data.

    1. A reversal may be linked to its target by ``reversal_of_id`` OR by
       ``source_reference`` alone. ``FinancialCoreService.reverse_entry`` sets both,
       but reversals created by earlier one-off repair scripts set only
       ``source_reference`` — on East Gate that was **127 of 155** reversal entries.
       An anti-join on ``reversal_of_id`` alone therefore matched nothing and counted
       $1,769,655.36 of REVERSED back-solve receipts as owner credit.

    2. There was no ``retired_at IS NULL`` filter at all, so the 70 receipts retired
       under GAP-FIN-073 kept counting as credit too.

    Together these inflated FY2026 unapplied credit to roughly $28,000 per lot. With
    both clauses the live figure is $4,394.32 across 4 lots, and three of those four
    match the strata portal to the cent.

    The column cannot simply be backfilled: ``finance.journal_entries`` carries
    ``trg_prevent_posted_journal_update``, which refuses any UPDATE to a posted entry
    ("Use a reversal entry"). That immutability guard is correct and outranks the
    convenience of a backfill, so the fix belongs in the query — which is why these
    are asserted structurally.
    """

    @staticmethod
    def _sql(name: str) -> str:
        from services.finance_metrics import lot_true_balance
        return str(getattr(lot_true_balance, name))

    @pytest.mark.parametrize("sql_name", ["_CREDIT_SQL", "_UNIT_CREDIT_SQL"])
    def test_reversal_matched_by_either_link(self, sql_name):
        sql = self._sql(sql_name)
        assert "rev.reversal_of_id = r.journal_entry_id" in sql
        assert "rev.source_reference = r.journal_entry_id::text" in sql, (
            f"{sql_name} must also match a reversal linked only by source_reference — "
            "127 of East Gate's 155 reversals are that shape"
        )

    @pytest.mark.parametrize("sql_name", ["_CREDIT_SQL", "_UNIT_CREDIT_SQL"])
    def test_retired_receipts_excluded(self, sql_name):
        assert "r.retired_at IS NULL" in self._sql(sql_name), (
            f"{sql_name} must exclude retired receipts — a retired receipt is not "
            "money the owner holds"
        )

    @pytest.mark.parametrize("sql_name", ["_CREDIT_SQL", "_UNIT_CREDIT_SQL"])
    def test_reversal_join_is_scoped(self, sql_name):
        """The widened join must not match another tenant's or a non-reversal entry."""
        sql = self._sql(sql_name)
        assert "rev.source_type = 'reversal'" in sql
        assert "rev.tenant_id = r.tenant_id" in sql

    def test_both_credit_queries_agree_on_their_filters(self):
        """The building-wide and single-unit paths return the same number for the same
        lot, so a clause added to one must be added to the other."""
        building, unit = self._sql("_CREDIT_SQL"), self._sql("_UNIT_CREDIT_SQL")
        for clause in (
            "r.retired_at IS NULL",
            "rev.source_reference = r.journal_entry_id::text",
            "rev.source_type = 'reversal'",
        ):
            assert (clause in building) == (clause in unit), (
                f"{clause!r} present in only one of the two credit queries — they must agree"
            )


class TestNetReversalParity:
    """A reversal that was itself reversed does not reverse anything.

    Journal entries are immutable, so undoing a reversal means posting a SECOND
    reversal on top of it. A receipt reversed and then un-reversed is live again —
    but an "does any reversal exist for this entry?" test excludes it forever.

    Found live on UA005 (2026-08-28): entry 9167 (a $467.51 receipt) was reversed by
    9808, and 9808 was itself reversed by 9822 during a rollback. The receipt is live;
    the naive test dropped $467.51 of real owner credit, and building-wide it held the
    FY2026 credit figure at $4,394.32 when the true value is $13,478.55.
    """

    @staticmethod
    def _sql(name: str) -> str:
        from services.finance_metrics import lot_true_balance
        return str(getattr(lot_true_balance, name))

    @pytest.mark.parametrize("sql_name", ["_CREDIT_SQL", "_UNIT_CREDIT_SQL"])
    def test_excludes_a_reversal_that_was_itself_reversed(self, sql_name):
        sql = self._sql(sql_name)
        assert "unrev" in sql, (
            f"{sql_name} must ignore a reversal that has itself been reversed, or a "
            "rolled-back reversal permanently hides live money"
        )
        assert "NOT EXISTS" in sql

    @pytest.mark.parametrize("sql_name", ["_CREDIT_SQL", "_UNIT_CREDIT_SQL"])
    def test_the_unreversal_check_matches_both_link_forms(self, sql_name):
        """The counter-reversal may link by column or by source_reference, exactly as
        the first-level reversal may — so it must be matched both ways too."""
        sql = self._sql(sql_name)
        assert "unrev.reversal_of_id = rev.journal_entry_id" in sql
        assert "unrev.source_reference = rev.journal_entry_id::text" in sql

    @pytest.mark.parametrize("sql_name", ["_CREDIT_SQL", "_UNIT_CREDIT_SQL"])
    def test_the_unreversal_check_is_tenant_scoped(self, sql_name):
        assert "unrev.tenant_id = rev.tenant_id" in self._sql(sql_name)
