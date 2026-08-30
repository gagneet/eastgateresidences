# @featuretrace:finance-postgres-cutover — Regression coverage for the
#   gl_accounts.fund_id backfill (2026-07-25): get_fund_balances() joins
#   `finance.gl_accounts ga ON ga.fund_id = f.fund_id`. Before the backfill,
#   every gl_accounts.fund_id was NULL for East Gate, so the join never
#   matched and the fallback silently returned $0.00 for both Admin and
#   Sinking balances on the building-overview page.
# Layer: test
# Data flow: finance.gl_accounts, finance.journal_entries, finance.journal_lines,
#            finance.funds (East Gate, integration-only, RUN_INTEGRATION_TESTS=1).
# Related: backend/services/financial_read_service.py (get_fund_balances)
#          backend/scripts/backfill_east_gate_journal_fund_id.py
#          tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md
"""Live regression test: East Gate's real Admin/Sinking fund balances must
never silently collapse back to $0.00.

This deliberately does NOT re-derive the expected dollar figures (those will
drift as more real transactions post) — it only asserts the structural
invariant the 2026-07-25 bug violated: at least one gl_account per fund has
a non-NULL fund_id, and get_fund_balances() returns a nonzero balance for
both Admin and Sinking. A regression here means either the fund_id backfill
was reverted/reset, or get_fund_balances()'s join condition changed again.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def _skip_unless_integration():
    if not os.getenv("RUN_INTEGRATION_TESTS"):
        pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1")


@pytest.fixture(scope="module", autouse=True)
def check_integration_enabled():
    _skip_unless_integration()


@pytest.mark.asyncio
async def test_east_gate_gl_accounts_have_fund_specific_accounts_tagged():
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    _tenant_bypass_id = "00000000-0000-0000-0000-000000000000"
    async with async_session_context() as session:
        await session.execute(text(f"SELECT set_config('app.tenant_id', '{_tenant_bypass_id}', true)"))
        row = (
            await session.execute(
                text("SELECT tenant_id::text, scheme_id::text FROM core.schemes WHERE lower(scheme_number) = lower('13195')")
            )
        ).first()
        assert row is not None, "East Gate (13195) scheme not found"
        tenant_id, scheme_id = row

        await set_tenant(session, tenant_id)
        counts = (
            await session.execute(
                text(
                    """
                    SELECT f.fund_type::text AS fund_type, count(ga.gl_account_id) AS n
                    FROM finance.funds f
                    LEFT JOIN finance.gl_accounts ga
                        ON ga.fund_id = f.fund_id AND ga.account_type = 'income'
                    WHERE f.scheme_id = :sid AND f.fund_type IN ('admin', 'sinking')
                    GROUP BY f.fund_type
                    """
                ),
                {"sid": scheme_id},
            )
        ).all()

    by_fund = {r.fund_type: r.n for r in counts}
    assert by_fund.get("admin", 0) >= 1, (
        "No income gl_account tagged with the admin fund's fund_id — "
        "get_fund_balances() would silently return $0.00 for Admin again."
    )
    assert by_fund.get("sinking", 0) >= 1, (
        "No income gl_account tagged with the sinking fund's fund_id — "
        "get_fund_balances() would silently return $0.00 for Sinking again."
    )


@pytest.mark.asyncio
async def test_get_fund_balances_returns_nonzero_for_east_gate():
    from services.financial_read_service import FinancialReadService

    result = await FinancialReadService().get_fund_balances(building_id="13195")

    assert result is not None
    assert result["admin_balance_cents"] != 0, (
        "get_fund_balances() returned $0.00 for East Gate's Admin fund — "
        "the gl_accounts.fund_id join likely regressed (see GAP-FIN-018)."
    )
    assert result["sinking_balance_cents"] != 0, (
        "get_fund_balances() returned $0.00 for East Gate's Sinking fund — "
        "the gl_accounts.fund_id join likely regressed (see GAP-FIN-018)."
    )
