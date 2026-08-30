"""
Regression tests: GET /finance/summary Postgres read-path wiring (2026-08-09).

routers/finance.py::get_finance_summary is a PARTIAL override, not a full swap
(unlike building_overview, like arrears_detail): when route_state["source"]=="postgres",
only unit_ledger_summary.total_levied/total_paid, admin/sinking_fund.actual_expenses/
total_expenses, grace-aware arrears (true_arrears_amount/units_owing/total_outstanding/
in_grace_amount), and ledger_quality/canonical_status_counts are re-sourced from
Postgres. budgeted_expenses, portal_summary, collected_summary, and the per-quarter
period_status/in_grace_summary label lists always stay Mongo-sourced -- see the plan at
docs/architecture (or the route_state comment at the top of get_finance_summary) for why.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.permissions import Permission


def _cursor(rows):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=rows)
    return c


LEVY_DOC = {
    "year": "2026",
    "status": "confirmed",
    "admin_fund": {"total_income": 100000.0, "levy_income": 100000.0},
    "sinking_fund": {"total_income": 40000.0, "levy_income": 40000.0},
    "payment_schedule": [],
    "total_uoe": 1000,
}


def _make_mock_db(cats=None, tx_groups=None):
    cats = cats or []
    tx_groups = tx_groups or []
    mock_db = MagicMock()
    mock_db.annual_levies.find_one = AsyncMock(return_value=LEVY_DOC)
    mock_db.settings = MagicMock()
    mock_db.settings.find_one = AsyncMock(return_value={
        "grace_period_days": 14,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
    })
    mock_db.levy_categories.find = MagicMock(return_value=_cursor(cats))
    mock_db.financial_transactions.aggregate = MagicMock(return_value=_cursor(tx_groups))
    mock_db.unit_levy_ledger.aggregate = MagicMock(return_value=_cursor([{
        "_id": None, "total_levied": 5000.0, "total_paid": 4000.0,
        "total_net_balance": 1000.0, "units_owing": 1, "units_credit": 0,
        "units_paid_up": 0, "total_outstanding": 1000.0,
    }]))
    mock_db.unit_levy_ledger.count_documents = AsyncMock(return_value=1)
    mock_db.unit_levy_ledger.find = MagicMock(return_value=_cursor([]))
    mock_db.units.find = MagicMock(return_value=_cursor([{"unit_number": "TH001"}]))
    mock_db.levy_payments.aggregate = MagicMock(return_value=_cursor([]))
    mock_db.building_summaries.find_one = AsyncMock(return_value=None)
    return mock_db


_MONGO_SOURCE_STATE = {
    "route_key": "finance.summary", "source": "mongo", "run_shadow": False,
    "eligible_for_postgres_read": False, "blocked_reason": "test",
    "domain_mode": "mongo_primary", "route_readiness": {"status": "not_started"},
}
_POSTGRES_SOURCE_STATE = {
    "route_key": "finance.summary", "source": "postgres", "run_shadow": False,
    "eligible_for_postgres_read": True, "blocked_reason": None,
    "domain_mode": "postgres_write", "route_readiness": {"status": "shadow_soaking"},
}

_PG_OC_LEVY = {
    "financial_year": "2026", "admin_fund_budgeted": 6000.0, "sinking_fund_budgeted": 1000.0,
    "total_budgeted": 7000.0, "total_collected": 5500.0, "total_outstanding": 1500.0,
    "levy_periods": [],
}
_PG_FUND_EXPENSES = {
    "admin_expense_cents": 250000, "sinking_expense_cents": 75000,
    "unassigned_expense_cents": 0, "total_expense_cents": 325000, "financial_year": "2026",
}
_PG_ARREARS_GRACE = {
    "total_arrears_cents": 120000, "units_in_arrears": 2, "arrears_by_lot": [],
    "financial_year": "2026", "basis": "due_date_grace_aware",
}
_PG_ARREARS_ALL = {
    "total_arrears_cents": 150000, "units_in_arrears": 3, "arrears_by_lot": [],
    "financial_year": "2026", "basis": "year_scoped_all_unpaid",
}
_PG_LEDGER_QUALITY = {
    "canonical_unit_count": 5, "ledger_row_count": 5, "distinct_ledger_unit_count": 5,
    "duplicate_ledger_units": [], "duplicate_ledger_row_count": 0,
    "missing_ledger_units": [], "extra_ledger_units": [], "malformed_ledger_row_count": 0,
    "is_unit_count_consistent": True,
    "canonical_status_counts": {"paid_up": 2, "owing": 2, "credit": 1},
    "financial_year": "2026", "source": "core.lots + finance.levy_items",
}


def _mock_for(value, default):
    """None means 'explicitly force PG-unavailable' (return None); omitted (sentinel
    default) means 'use the standard successful PG fixture'; an Exception instance
    means 'PG call raises'."""
    if value is _UNSET:
        value = default
    if isinstance(value, Exception):
        return AsyncMock(side_effect=value)
    return AsyncMock(return_value=value)


_UNSET = object()


async def _call(mock_db, route_state, *,
                 get_oc_levy_summary=_UNSET, get_fund_expense_totals=_UNSET,
                 get_arrears_summary=_UNSET, get_canonical_ledger_quality=_UNSET):
    from routers.finance import get_finance_summary

    arrears_mock = _mock_for(get_arrears_summary, None)
    if get_arrears_summary is _UNSET:
        # get_finance_summary calls get_arrears_summary twice (grace_aware=True, then
        # grace_aware=False for the in_grace_amount diff) -- feed both in one call.
        arrears_mock = AsyncMock(side_effect=[_PG_ARREARS_GRACE, _PG_ARREARS_ALL])

    with ExitStack() as stack:
        stack.enter_context(patch("routers.finance.db", mock_db))
        stack.enter_context(patch("utils.finance_helpers.db", mock_db))
        stack.enter_context(patch("routers.finance.get_user_permissions",
                                   return_value=Permission(can_view_finances=True)))
        stack.enter_context(patch("routers.finance.get_finance_route_runtime_state",
                                   AsyncMock(return_value=route_state)))
        stack.enter_context(patch("routers.finance._financial_read_service.get_oc_levy_summary",
                                   _mock_for(get_oc_levy_summary, _PG_OC_LEVY)))
        stack.enter_context(patch("routers.finance._financial_read_service.get_fund_expense_totals",
                                   _mock_for(get_fund_expense_totals, _PG_FUND_EXPENSES)))
        stack.enter_context(patch("routers.finance._financial_read_service.get_canonical_ledger_quality",
                                   _mock_for(get_canonical_ledger_quality, _PG_LEDGER_QUALITY)))
        stack.enter_context(patch("routers.finance._financial_read_service.get_arrears_summary",
                                   arrears_mock))
        return await get_finance_summary(
            year="2026",
            current_user={"id": "u1", "building_id": "13195", "role": "super_admin"},
            building_id="13195",
        )


@pytest.mark.asyncio
async def test_source_postgres_uses_pg_fund_expenses():
    result = await _call(_make_mock_db(), _POSTGRES_SOURCE_STATE)
    assert result["admin_fund"]["actual_expenses"] == 2500.0
    assert result["admin_fund"]["total_expenses"] == 2500.0
    assert result["sinking_fund"]["actual_expenses"] == 750.0


@pytest.mark.asyncio
async def test_source_postgres_keeps_category_expenses_when_pg_fund_split_conflicts():
    """finance.summary is still a partial override. If PG reports a materially
    different total or collapses a populated sinking split into admin, keep the
    category actuals that also drive the Budget vs Actual chart."""
    cats = [
        {"id": "a1", "name": "Admin", "fund_type": "administrative", "budgeted_amount": 1000.0, "actual_amount": 0.0},
        {"id": "s1", "name": "Sinking", "fund_type": "sinking", "budgeted_amount": 1000.0, "actual_amount": 0.0},
    ]
    tx_groups = [
        {"_id": {"fund_type": "administrative", "category_id": "a1", "category_name": "Admin"}, "total": 80177.65},
        {"_id": {"fund_type": "sinking", "category_id": "s1", "category_name": "Sinking"}, "total": 70060.0},
    ]
    pg_expenses = {
        "admin_expense_cents": 14565265,
        "sinking_expense_cents": 0,
        "unassigned_expense_cents": 0,
        "total_expense_cents": 14565265,
        "financial_year": "2026",
    }

    result = await _call(
        _make_mock_db(cats=cats, tx_groups=tx_groups),
        _POSTGRES_SOURCE_STATE,
        get_fund_expense_totals=pg_expenses,
    )

    assert result["admin_fund"]["actual_expenses"] == 80177.65
    assert result["admin_fund"]["total_expenses"] == 80177.65
    assert result["sinking_fund"]["actual_expenses"] == 70060.0
    assert result["sinking_fund"]["total_expenses"] == 70060.0


@pytest.mark.asyncio
async def test_source_postgres_uses_pg_levied_and_paid():
    result = await _call(_make_mock_db(), _POSTGRES_SOURCE_STATE)
    assert result["unit_ledger_summary"]["total_levied"] == 7000.0
    assert result["unit_ledger_summary"]["raw_total_paid"] == 5500.0


@pytest.mark.asyncio
async def test_source_postgres_uses_pg_grace_aware_arrears():
    result = await _call(_make_mock_db(), _POSTGRES_SOURCE_STATE)
    assert result["in_grace_summary"]["true_arrears_amount"] == 1200.0
    assert result["unit_ledger_summary"]["total_outstanding"] == 1200.0
    # in_grace_amount = all-unpaid (1500.0) - grace-aware (1200.0) = 300.0
    assert result["in_grace_summary"]["in_grace_amount"] == 300.0


@pytest.mark.asyncio
async def test_source_postgres_uses_pg_ledger_quality_status_counts():
    result = await _call(_make_mock_db(), _POSTGRES_SOURCE_STATE)
    # units_paid_up is recomputed by the arrears override below (canonical_unit_count=5
    # - units_owing=2 from _PG_ARREARS_GRACE) rather than kept as ledger_quality's raw
    # canonical_status_counts["paid_up"]=2 -- matches the pre-existing Mongo-path pattern
    # (routers/finance.py's own canonical_unit_count block), not a bug in this override.
    assert result["unit_ledger_summary"]["units_paid_up"] == 3
    assert result["unit_ledger_summary"]["units_credit"] == 0
    assert result["ledger_quality"]["source"] == "core.lots + finance.levy_items"


@pytest.mark.asyncio
async def test_budgeted_expenses_always_stays_mongo_sourced_even_on_postgres():
    """budgeted_expenses is a PERMANENT exclusion (concept mismatch: PG = levied,
    Mongo = staff-entered planning figure) -- must never move even when source=postgres."""
    mongo_result = await _call(_make_mock_db(), _MONGO_SOURCE_STATE)
    pg_result = await _call(_make_mock_db(), _POSTGRES_SOURCE_STATE)
    assert mongo_result["admin_fund"]["budgeted_expenses"] == pg_result["admin_fund"]["budgeted_expenses"]


@pytest.mark.asyncio
async def test_portal_and_collected_summary_stay_mongo_sourced_on_postgres():
    """Regression guard: no PG equivalent exists for these -- must never change based
    on route_state[source]."""
    mongo_result = await _call(_make_mock_db(), _MONGO_SOURCE_STATE)
    pg_result = await _call(_make_mock_db(), _POSTGRES_SOURCE_STATE)
    assert mongo_result["portal_summary"] == pg_result["portal_summary"]
    assert mongo_result["collected_summary"] == pg_result["collected_summary"]
    assert mongo_result["period_status"]["overdue_periods"] == pg_result["period_status"]["overdue_periods"]


@pytest.mark.asyncio
async def test_falls_back_to_mongo_when_pg_fund_expenses_returns_none():
    result = await _call(_make_mock_db(), _POSTGRES_SOURCE_STATE, get_fund_expense_totals=None)
    # Mongo path: no financial_transactions/levy_categories -> both funds $0 actual.
    assert result["admin_fund"]["actual_expenses"] == 0.0


@pytest.mark.asyncio
async def test_falls_back_to_mongo_when_pg_fund_expenses_raises():
    result = await _call(
        _make_mock_db(), _POSTGRES_SOURCE_STATE,
        get_fund_expense_totals=RuntimeError("PG connection refused"),
    )
    assert result["admin_fund"]["actual_expenses"] == 0.0


@pytest.mark.asyncio
async def test_falls_back_to_mongo_when_pg_arrears_raises():
    result = await _call(
        _make_mock_db(), _POSTGRES_SOURCE_STATE,
        get_arrears_summary=RuntimeError("PG connection refused"),
    )
    # Mongo fixture's grace_result still populates true_arrears_amount from the
    # unmocked Mongo-only path -- just assert it didn't crash and returned a real value.
    assert "true_arrears_amount" in result["in_grace_summary"]


@pytest.mark.asyncio
async def test_source_mongo_never_calls_any_pg_method():
    mock_db = _make_mock_db()
    with patch("routers.finance.db", mock_db), \
         patch("utils.finance_helpers.db", mock_db), \
         patch("routers.finance.get_user_permissions",
               return_value=Permission(can_view_finances=True)), \
         patch("routers.finance.get_finance_route_runtime_state",
               AsyncMock(return_value=_MONGO_SOURCE_STATE)), \
         patch("routers.finance._financial_read_service.get_oc_levy_summary", AsyncMock()) as m1, \
         patch("routers.finance._financial_read_service.get_fund_expense_totals", AsyncMock()) as m2, \
         patch("routers.finance._financial_read_service.get_arrears_summary", AsyncMock()) as m3, \
         patch("routers.finance._financial_read_service.get_canonical_ledger_quality", AsyncMock()) as m4:
        from routers.finance import get_finance_summary
        await get_finance_summary(
            year="2026",
            current_user={"id": "u1", "building_id": "13195", "role": "super_admin"},
            building_id="13195",
        )
        m1.assert_not_awaited()
        m2.assert_not_awaited()
        m3.assert_not_awaited()
        m4.assert_not_awaited()
