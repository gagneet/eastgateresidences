# @featuretrace:financial_integration_v2 — Permanent regression coverage for the
#   `:name::cast` SQLAlchemy bind-param truncation bug found live 2026-07-24
#   (see backend/db_postgres/repos/ownership_repo.py for the original discovery).
# Layer: test
# Data flow: finance.journal_entries/journal_lines, finance.reconciliation_runs,
#            finance.trust_accounts, finance.bank_transactions (East Gate,
#            integration-only, RUN_INTEGRATION_TESTS=1).
# Related: backend/services/trust_read_service.py
#          tests/backend/test_ownership_repo_integration.py
"""Regression tests for two more instances of the same SQLAlchemy text() bug
found by grepping the codebase for `:name::cast` after fixing the original
instance in ownership_repo.py: `:name::cast` immediately truncates the bind
parameter name (`:meta::jsonb` parses as bindparam `met`), raising a raw
PostgresSyntaxError instead of executing — invisible to mocked tests since it
only fires when the real SQL text is actually compiled and sent to Postgres.

1. `TrustReadService.get_journal_entry()`'s `lines_query` used
   `:entry_id::uuid` — broke fetching a journal entry's debit/credit lines
   for any real journal_entry_id (the sibling `entry_query` in the same
   method already used the correct `CAST(:entry_id AS uuid)` form, so this
   was inconsistent within the same function, not a copy-paste-everywhere
   mistake).
2. `TrustReadService.get_reconciliation_summary()`'s query used
   `:trust_account_id::uuid` — broke the per-status matched/unmatched
   breakdown for any real reconciliation run.

Both fixed with `CAST(:name AS uuid)`, matching the pattern already used
correctly elsewhere in the same file (`get_reconciliation_run`,
`list_statement_lines`).

Fixing the cast bug surfaced a third, independent bug — not in either of the
two functions above, but in two *other* functions that both call
`get_reconciliation_run()` first: `list_statement_lines()` and
`get_reconciliation_summary()`. `get_reconciliation_run()` returns
`statement_start_date`/`statement_end_date` as `::text`-cast strings (correct
for its own callers — `routers/trust_reconciliation.py` compares them as
strings and serialises them straight to JSON), but both of these downstream
functions passed those strings directly as bind parameters compared against
`bt.transaction_date`, a native `date` column — asyncpg raises `DataError:
'str' object has no attribute 'toordinal'` for any real call, unconditionally.
`get_journal_entry()` is unaffected by this third bug — it never touches
period dates. Fixed by converting with `date.fromisoformat(...)` at the two
affected call sites, not by changing `get_reconciliation_run()`'s public
string contract.
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest

pytestmark = pytest.mark.integration

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def _skip_unless_integration():
    if not os.getenv("RUN_INTEGRATION_TESTS"):
        pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1")


@pytest.fixture(scope="module", autouse=True)
def check_integration_enabled():
    _skip_unless_integration()


@pytest.fixture
async def east_gate_ids():
    """East Gate's real tenant_id/scheme_id, resolved read-only."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    async with async_session_context() as session:
        await session.execute(text(f"SELECT set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true)"))
        row = (
            await session.execute(
                text("SELECT tenant_id::text, scheme_id::text FROM core.schemes WHERE lower(scheme_number) = lower('13195')")
            )
        ).fetchone()
        assert row is not None, "East Gate (13195) scheme row not found — cannot run integration test"
        return row  # (tenant_id, scheme_id)


class TestGetJournalEntryLinesQuery:
    """Failure mode #1: get_journal_entry()'s lines_query used :entry_id::uuid.
    entry_query (the first query in the method) already used the correct
    CAST(...) form and would succeed regardless, so this test specifically
    needs a journal entry that actually has lines to reach the buggy query."""

    @pytest.mark.asyncio
    async def test_get_journal_entry_with_real_lines_does_not_raise(self, east_gate_ids):
        from sqlalchemy import text
        from db_postgres.session import async_session_context, set_tenant
        from services.trust_read_service import TrustReadService

        tenant_id, scheme_id = east_gate_ids
        async with async_session_context() as session:
            await set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    text("""
                         SELECT je.journal_entry_id::text
                         FROM finance.journal_entries je
                         JOIN finance.journal_lines jl ON jl.journal_entry_id = je.journal_entry_id
                         WHERE je.scheme_id = :sid
                         LIMIT 1
                         """),
                    {"sid": scheme_id},
                )
            ).fetchone()
        assert row is not None, "East Gate has no journal entry with lines — cannot exercise the buggy query path"
        entry_id = row[0]

        result = await TrustReadService().get_journal_entry(building_id="13195", entry_id=entry_id)

        assert result is not None
        assert result["id"] == entry_id
        assert isinstance(result.get("lines"), list)
        assert len(result["lines"]) > 0, "journal entry has lines in the DB but none were returned"


@pytest.fixture
async def synthetic_reconciliation_run(east_gate_ids):
    """A fully synthetic, real (committed) trust_account + reconciliation_run
    + one bank_transaction — TrustReadService opens its own separate
    async_session_context(), so fixture rows must be committed to be visible
    to it (Postgres doesn't share uncommitted state across connections).
    Cleans up explicitly, re-asserting tenant context first: session.commit()
    ends the transaction set_tenant()'s SET LOCAL app.tenant_id lived in, and
    RLS-protected tables silently match zero rows on a bare DELETE without
    re-setting it — found live writing this fixture's first draft, the exact
    footgun this repo's own CLAUDE.md documents; two rows were left committed
    in real tables until caught and cleaned up by hand."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    tenant_id, scheme_id = east_gate_ids
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        trust_account_id = (
            await session.execute(
                text("""
                     INSERT INTO finance.trust_accounts
                         (tenant_id, scheme_id, bank_name, account_name)
                     VALUES (:tid, :sid, 'Integration Test Bank', 'Integration Test Trust Account')
                     RETURNING trust_account_id::TEXT
                     """),
                {"tid": tenant_id, "sid": scheme_id},
            )
        ).scalar()

        period_start, period_end = date(2030, 1, 1), date(2030, 1, 31)
        run_id = (
            await session.execute(
                text("""
                     INSERT INTO finance.reconciliation_runs
                         (tenant_id, scheme_id, trust_account_id, period_start, period_end,
                          cashbook_balance_cents, bank_balance_cents, owner_ledger_balance_cents,
                          difference_cents, status)
                     VALUES (:tid, :sid, :taid, :ps, :pe, 0, 0, 0, 0, 'draft')
                     RETURNING reconciliation_run_id::TEXT
                     """),
                {"tid": tenant_id, "sid": scheme_id, "taid": trust_account_id, "ps": period_start, "pe": period_end},
            )
        ).scalar()

        await session.execute(
            text("""
                 INSERT INTO finance.bank_transactions
                     (tenant_id, scheme_id, trust_account_id, transaction_date, description,
                      amount_cents, reconciliation_status)
                 VALUES (:tid, :sid, :taid, :txdate, 'Integration test transaction', 12345, 'matched')
                 """),
            {"tid": tenant_id, "sid": scheme_id, "taid": trust_account_id, "txdate": date(2030, 1, 15)},
        )
        await session.commit()

        try:
            yield run_id, trust_account_id
        finally:
            await set_tenant(session, tenant_id)
            await session.execute(
                text("DELETE FROM finance.bank_transactions WHERE trust_account_id = :taid"),
                {"taid": trust_account_id},
            )
            await session.execute(
                text("DELETE FROM finance.reconciliation_runs WHERE trust_account_id = :taid"),
                {"taid": trust_account_id},
            )
            await session.execute(
                text("DELETE FROM finance.trust_accounts WHERE trust_account_id = :taid"),
                {"taid": trust_account_id},
            )
            await session.commit()


class TestGetReconciliationSummaryQuery:
    """Failure mode #2: get_reconciliation_summary()'s query used
    :trust_account_id::uuid."""

    @pytest.mark.asyncio
    async def test_get_reconciliation_summary_does_not_raise(self, synthetic_reconciliation_run):
        from services.trust_read_service import TrustReadService

        run_id, _trust_account_id = synthetic_reconciliation_run

        result = await TrustReadService().get_reconciliation_summary(building_id="13195", run_id=run_id)

        assert result is not None
        assert result["run_id"] == run_id
        assert result["matched_count"] == 1


class TestListStatementLinesQuery:
    """Failure mode #3 (date-typed string param — see module docstring):
    list_statement_lines() passed get_reconciliation_run()'s ::text-cast
    statement_start_date/statement_end_date straight through as bind params
    against bt.transaction_date, a native date column. Same fixture as
    TestGetReconciliationSummaryQuery — both functions share the exact same
    bug shape against the same underlying data."""

    @pytest.mark.asyncio
    async def test_list_statement_lines_does_not_raise(self, synthetic_reconciliation_run):
        from services.trust_read_service import TrustReadService

        run_id, _trust_account_id = synthetic_reconciliation_run

        result = await TrustReadService().list_statement_lines(building_id="13195", run_id=run_id)

        assert result is not None
        assert result["total"] == 1
        assert len(result["lines"]) == 1
        assert result["lines"][0]["description"] == "Integration test transaction"
