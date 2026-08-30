# @featuretrace:finance-postgres-read-cutover — finance routes dispatched through the seam.
# Layer: test
# Data flow: store_router.read_through + financial_read_service response-shaped readers
#            -> GET /expense-transactions, GET /income-transactions (building-scoped).
# Related: backend/services/store_router.py
#          backend/services/financial_read_service.py
"""finance_ledger is promoted with 17,967 rows; these routes now actually ask.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_finance_pg_wiring.py -q
"""
from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from models.cutover_status import DataSource  # noqa: E402
from services import store_router  # noqa: E402
from services.store_router import read_through  # noqa: E402


@dataclass
class _FakeDecision:
    source: DataSource
    shadow_enabled: bool = False
    blocked_reason: str | None = None


def _guard(source: DataSource):
    return patch.object(
        store_router, "require_domain_source",
        new=AsyncMock(return_value=_FakeDecision(source=source)),
    )


class TestReadThrough:
    """Four outcomes, four labels. Collapsing any two hides something that needs action."""

    @pytest.mark.asyncio
    async def test_postgres_answers_when_it_has_rows(self):
        with _guard(DataSource.postgres):
            r = await read_through(
                domain="finance_ledger", building_id="13195", route="r",
                postgres=AsyncMock(return_value=[{"a": 1}]),
                mongo=AsyncMock(return_value=[{"m": 1}]),
            )
        assert r.source == "postgres" and r.served_by_postgres
        assert r.items == [{"a": 1}]

    @pytest.mark.asyncio
    async def test_empty_postgres_falls_back_during_coexistence(self):
        with _guard(DataSource.postgres):
            r = await read_through(
                domain="finance_ledger", building_id="13195", route="r",
                postgres=AsyncMock(return_value=[]),
                mongo=AsyncMock(return_value=[{"m": 1}, {"m": 2}]),
            )
        assert r.source == "mongo_fallback_pg_empty"
        assert len(r.items) == 2

    @pytest.mark.asyncio
    async def test_none_means_unavailable_not_empty(self):
        """The readers return None when they cannot SCOPE the request — a real distinction."""
        with _guard(DataSource.postgres):
            r = await read_through(
                domain="finance_ledger", building_id="13195", route="r",
                postgres=AsyncMock(return_value=None),
                mongo=AsyncMock(return_value=[{"m": 1}]),
            )
        assert r.source == "mongo_fallback_pg_unavailable"
        assert "could not scope" in (r.error or "")

    @pytest.mark.asyncio
    async def test_a_raising_postgres_reader_falls_back_and_records_why(self):
        with _guard(DataSource.postgres):
            r = await read_through(
                domain="finance_ledger", building_id="13195", route="r",
                postgres=AsyncMock(side_effect=RuntimeError("pg down")),
                mongo=AsyncMock(return_value=[{"m": 1}]),
            )
        assert r.source == "mongo_fallback_pg_unavailable"
        assert "pg down" in (r.error or "")

    @pytest.mark.asyncio
    async def test_a_mongo_primary_domain_never_executes_the_postgres_query(self):
        """Callables, not coroutines — otherwise both stores run on every request."""
        pg = AsyncMock(return_value=[{"a": 1}])
        with _guard(DataSource.mongo):
            r = await read_through(
                domain="finance_ledger", building_id="13195", route="r",
                postgres=pg, mongo=AsyncMock(return_value=[{"m": 1}]),
            )
        assert r.source == "mongo"
        pg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_falls_back_can_be_switched_off_after_data_genesis(self):
        """Once a domain has its data, empty legitimately means empty."""
        with _guard(DataSource.postgres):
            r = await read_through(
                domain="finance_ledger", building_id="13195", route="r",
                postgres=AsyncMock(return_value=[]),
                mongo=AsyncMock(return_value=[{"m": 1}]),
                empty_falls_back=False,
            )
        assert r.source == "postgres"
        assert r.items == []


class TestPostgresReadersMatchTheResponseModels:
    """A reader shaped like its TABLE 500s the route the moment the domain is promoted.

    Found in documents_repo by audit on 2026-08-29 and again here on 2026-08-30, where
    the income reader was missing `created_by` while the expense reader was complete —
    a discrepancy no amount of reading the model definition surfaced, only validating a
    real row against the real model did.
    """

    def _expense_row(self):
        return {
            "id": "e1", "building_id": "13195", "plan_id": "13195",
            "financial_year": "2026", "date": "2026-01-01", "amount": 12.34,
            "description": "d", "category_name": "c", "supplier_name": "s",
            "created_at": "t", "updated_at": "t", "created_by": "",
        }

    def _income_row(self):
        return {
            "id": "i1", "building_id": "13195", "plan_id": "13195",
            "financial_year": "2026", "date": "2026-01-01", "amount": 56.78,
            "description": "d", "category_name": "Levy receipt", "source": "levy",
            "created_at": "t", "updated_at": "t", "created_by": "",
        }

    def test_expense_shape_validates(self):
        from models.finance import ExpenseTransactionResponse

        assert ExpenseTransactionResponse(**self._expense_row()).id == "e1"

    def test_income_shape_validates(self):
        from models.finance import IncomeTransactionResponse

        assert IncomeTransactionResponse(**self._income_row()).id == "i1"

    def test_income_source_is_the_income_type_not_the_datastore(self):
        """`source` on IncomeTransactionResponse means interest/rebate/grant/levy."""
        from models.finance import IncomeTransactionResponse

        assert IncomeTransactionResponse(**self._income_row()).source == "levy"

    def test_the_income_reader_filters_retired_receipts(self):
        """70 retired receipts exist. Showing a reversed receipt as income is the bug
        that made $1,769,655.36 of reversals read as owner credit."""
        from services.financial_read_service import FinancialReadService

        src = inspect.getsource(FinancialReadService.get_income_transactions)
        assert "retired_at IS NULL" in src

    def test_both_readers_return_none_when_there_is_no_financial_year_window(self):
        """`None` means "could not scope", which read_through reports as unavailable.

        Asserting on the source rather than executing is deliberate: the branch needs a
        building with no FY window, and manufacturing one against the live database
        would be a fixture with real consequences. What matters is that the readers
        signal unavailable rather than returning [] and having an empty list read as
        "this year genuinely had no transactions".
        """
        from services.financial_read_service import FinancialReadService

        for fn in (FinancialReadService.get_expense_transactions,
                   FinancialReadService.get_income_transactions,
                   FinancialReadService.get_available_levy_years):
            src = inspect.getsource(fn)
            assert "return None" in src, f"{fn.__name__} must signal unavailable"

    def test_an_unresolvable_building_reaches_read_through_as_unavailable(self):
        """_resolve_scheme RAISES; it does not return None.

        The `if not scheme: return None` branch in each reader is defensive and does not
        fire. The safety property still holds — read_through catches the exception — but
        via a different mechanism than the code first claimed, which is why the
        docstrings now say so. Verified live: an unknown building_id raises RuntimeError
        and read_through reports mongo_fallback_pg_unavailable.
        """
        from services.financial_read_service import FinancialReadService

        src = inspect.getsource(FinancialReadService._resolve_scheme)
        assert "raise RuntimeError" in src, (
            "if _resolve_scheme ever starts returning None instead of raising, the "
            "readers' defensive branches become live and their docstrings need updating"
        )


class TestRoutesAreWired:
    def test_expense_and_income_routes_use_read_through(self):
        src = (Path(__file__).resolve().parents[2] / "backend" / "routers" / "finance.py").read_text()
        assert src.count('route="finance.expense_transactions"') == 1
        assert src.count('route="finance.income_transactions"') == 1

    def test_the_shadow_comparison_still_uses_the_mongo_aggregate(self):
        """Comparing whatever was served would compare Postgres with itself once
        promoted, and report a permanent clean pass that means nothing."""
        src = (Path(__file__).resolve().parents[2] / "backend" / "routers" / "finance.py").read_text()
        assert "_maybe_shadow_transactions(building_id, year, await _mongo_expenses(), [], \"expense\")" in src
        assert "_maybe_shadow_transactions(building_id, year, [], await _mongo_income(), \"income\")" in src


class TestTheMongoReaderIsCalledOncePerRequest:
    """The Mongo aggregate is needed twice — as the possible response, and as the
    shadow comparison's Mongo side. Calling the reader twice issues every query behind
    it twice on EVERY request, including the _fallback_financial_transactions chain.

    Caught by a test whose mock cursor was single-use; the production cost would have
    been invisible.
    """

    def test_both_handlers_memoise_their_mongo_reader(self):
        src = (Path(__file__).resolve().parents[2] / "backend" / "routers" / "finance.py").read_text()
        for cache in ("_mongo_expense_cache", "_mongo_income_cache"):
            assert f"nonlocal {cache}" in src, (
                f"{cache} must be memoised — the reader is called twice per request"
            )
            assert f"if {cache} is not None:" in src

    @pytest.mark.asyncio
    async def test_a_memoised_reader_returns_the_same_rows_twice(self):
        """The behavioural shape, independent of the finance module."""
        calls = {"n": 0}
        cache: list | None = None

        async def reader():
            nonlocal cache
            if cache is not None:
                return cache
            calls["n"] += 1
            cache = [{"row": calls["n"]}]
            return cache

        first, second = await reader(), await reader()
        assert first == second
        assert calls["n"] == 1


class TestAvailableYears:
    """The one finance route that maps cleanly — a bare year string, no response model."""

    def test_the_route_is_wired(self):
        src = (Path(__file__).resolve().parents[2] / "backend" / "routers" / "finance.py").read_text()
        assert src.count('route="finance.available_years"') == 1

    def test_the_not_yet_started_filter_stays_in_the_router(self):
        """Which years are SELECTABLE is a levy-cycle rule owned by the route.

        Applying it inside the Postgres reader would be a second implementation of it,
        and the two would drift the first time the cycle rule changed.
        """
        from services.financial_read_service import FinancialReadService

        src = inspect.getsource(FinancialReadService.get_available_levy_years)
        assert "_resolve_current_levy_year" not in src
        assert "current_levy_year" not in src

    def test_the_reader_asks_postgres_for_distinct_financial_years(self):
        from services.financial_read_service import FinancialReadService

        src = inspect.getsource(FinancialReadService.get_available_levy_years)
        assert "SELECT DISTINCT financial_year" in src
        assert "finance.levy_runs" in src
