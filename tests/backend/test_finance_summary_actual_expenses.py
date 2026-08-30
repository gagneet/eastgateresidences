"""
Regression tests: GAP-FIN-030 Root Cause B — GET /finance/summary's admin_fund/
sinking_fund actual-expense figures must be sourced from the same live
financial_transactions aggregate the "Budget vs Actual" chart already uses
(_get_actual_overrides_from_financial_transactions/_resolve_category_actual_amount),
not the stale levy_categories.actual_amount field (only ever written by the manual
/expense-transactions/reconcile endpoint). Previously these two page elements could
disagree; and total_expenses (the field the frontend tile actually reads,
FinancePage.tsx:522) was never set at all by this endpoint, always $0.00 unless a
separate, rarely-run CSV-upload sync job had happened to populate it on the raw
annual_levies document.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.permissions import Permission


def _cursor(rows):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=rows)
    return c


def _make_mock_db(levy_doc, cats, tx_groups):
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
    mock_db.settings = MagicMock()
    mock_db.settings.find_one = AsyncMock(return_value={
        "grace_period_days": 14,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
    })
    mock_db.levy_categories.find = MagicMock(return_value=_cursor(cats))
    mock_db.financial_transactions.aggregate = MagicMock(return_value=_cursor(tx_groups))
    mock_db.unit_levy_ledger.aggregate = MagicMock(return_value=_cursor([]))
    mock_db.unit_levy_ledger.count_documents = AsyncMock(return_value=0)
    mock_db.unit_levy_ledger.find = MagicMock(return_value=_cursor([]))
    mock_db.units.find = MagicMock(return_value=_cursor([{"unit_number": "TH001"}]))
    mock_db.levy_payments.aggregate = MagicMock(return_value=_cursor([]))
    mock_db.building_summaries.find_one = AsyncMock(return_value=None)
    return mock_db


async def _call(mock_db):
    from routers.finance import get_finance_summary
    # building_id="13195" is real East Gate, which since 2026-08-09 is genuinely
    # PG-eligible for finance.summary -- pin the route to Mongo so these tests keep
    # exercising the Mongo-path actual-expense computation under test against the
    # hand-crafted mock_db above, not real Postgres.
    with patch("routers.finance.db", mock_db), \
         patch("utils.finance_helpers.db", mock_db), \
         patch("routers.finance.get_user_permissions",
               return_value=Permission(can_view_finances=True)), \
         patch("routers.finance.get_finance_route_runtime_state", AsyncMock(return_value={
             "route_key": "finance.summary", "source": "mongo", "run_shadow": False,
             "eligible_for_postgres_read": False, "blocked_reason": "forced mongo for unit test",
             "domain_mode": "mongo_primary", "route_readiness": {"status": "not_started"},
         })):
        return await get_finance_summary(
            year="2026",
            current_user={"id": "u1", "building_id": "13195", "role": "super_admin"},
            building_id="13195",
        )


LEVY_DOC = {
    "year": "2026",
    "status": "confirmed",
    "admin_fund": {"total_income": 100000.0, "levy_income": 100000.0},
    "sinking_fund": {"total_income": 40000.0, "levy_income": 40000.0},
    "payment_schedule": [],
    "total_uoe": 1000,
}


@pytest.mark.asyncio
async def test_actual_expenses_sourced_from_financial_transactions_not_stale_field():
    """A category's stale levy_categories.actual_amount ($0, never reconciled)
    must be overridden by a real financial_transactions expense total."""
    cats = [{
        "id": "cat-1", "name": "Insurance", "fund_type": "administrative",
        "budgeted_amount": 20000.0, "actual_amount": 0.0,
    }]
    tx_groups = [{
        "_id": {"fund_type": "administrative", "category_id": "cat-1", "category_name": "Insurance"},
        "total": 18500.0,
    }]
    result = await _call(_make_mock_db(LEVY_DOC, cats, tx_groups))

    assert result["admin_fund"]["actual_expenses"] == 18500.0
    assert result["admin_fund"]["total_expenses"] == 18500.0


@pytest.mark.asyncio
async def test_admin_and_sinking_total_expenses_agree_with_actual_expenses():
    """total_expenses (frontend tile field) and actual_expenses (existing key) must
    carry the identical corrected value for both funds -- the exact field-name
    mismatch that caused the Admin Fund Expenses tile to always show $0.00."""
    cats = [
        {"id": "a1", "name": "Admin Cat", "fund_type": "administrative",
         "budgeted_amount": 10000.0, "actual_amount": 0.0},
        {"id": "s1", "name": "Sinking Cat", "fund_type": "sinking",
         "budgeted_amount": 5000.0, "actual_amount": 0.0},
    ]
    tx_groups = [
        {"_id": {"fund_type": "administrative", "category_id": "a1", "category_name": "Admin Cat"}, "total": 9250.5},
        {"_id": {"fund_type": "sinking", "category_id": "s1", "category_name": "Sinking Cat"}, "total": 4100.0},
    ]
    result = await _call(_make_mock_db(LEVY_DOC, cats, tx_groups))

    assert result["admin_fund"]["total_expenses"] == result["admin_fund"]["actual_expenses"] == 9250.5
    assert result["sinking_fund"]["total_expenses"] == result["sinking_fund"]["actual_expenses"] == 4100.0


@pytest.mark.asyncio
async def test_falls_back_to_legacy_actual_amount_when_no_transactions_exist():
    """No financial_transactions rows for a category -> falls back to the legacy
    levy_categories.actual_amount field (manual reconcile path), never $0 by default
    when a real reconciled figure exists -- strict improvement, not a regression."""
    cats = [{
        "id": "cat-2", "name": "Cleaning", "fund_type": "administrative",
        "budgeted_amount": 8000.0, "actual_amount": 7654.32,
    }]
    result = await _call(_make_mock_db(LEVY_DOC, cats, tx_groups=[]))

    assert result["admin_fund"]["actual_expenses"] == 7654.32
    assert result["admin_fund"]["total_expenses"] == 7654.32


@pytest.mark.asyncio
async def test_zero_categories_and_zero_transactions_yields_zero_not_error():
    """No categories at all -> both figures are 0.0, endpoint doesn't error."""
    result = await _call(_make_mock_db(LEVY_DOC, cats=[], tx_groups=[]))

    assert result["admin_fund"]["total_expenses"] == 0.0
    assert result["sinking_fund"]["total_expenses"] == 0.0
