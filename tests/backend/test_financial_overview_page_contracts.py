from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date

from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from utils.permissions import Permission


class _FixedToday(date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 2)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, *args, **kwargs):
        return self.rows


def _collection(find_rows=None, aggregate_rows=None, find_one=None):
    return SimpleNamespace(
        find=MagicMock(return_value=_Cursor(find_rows or [])),
        aggregate=MagicMock(return_value=_Cursor(aggregate_rows or [])),
        find_one=AsyncMock(return_value=find_one),
        count_documents=AsyncMock(return_value=0),
    )


def _settings():
    return {
        "grace_period_days": 14,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
        "financial_year_start_month": 1,
    }




@pytest.mark.asyncio
async def test_summary_total_paid_is_year_scoped_not_raw_cumulative_total():
    from routers.finance import get_finance_summary



    levy_doc = {
        "year": "2026",
        "status": "confirmed",
        "admin_fund": {"levy_income": 0.0, "total_income": 0.0},
        "sinking_fund": {"levy_income": 0.0, "total_income": 0.0},
        "proposed_admin_expenses": 1000.0,
        "proposed_sinking_expenses": 500.0,
        "payment_schedule": [],
        "total_uoe": 100,
    }
    ledger_summary = [{
        "_id": None,
        "total_levied": 1500.0,
        "total_paid": 9999.0,
        "total_net_balance": 200.0,
        "units_owing": 1,
        "units_credit": 0,
        "units_paid_up": 1,
        "total_outstanding": 200.0,
    }]
    mock_db = SimpleNamespace(
        annual_levies=_collection(find_one=levy_doc),
        settings=_collection(find_one=_settings()),
        levy_categories=_collection(find_rows=[]),
        unit_levy_ledger=_collection(
            # total_levied matches ledger_summary's aggregate total (1500.0) so the
            # per-unit-clamped get_collection_rate_metrics() path (GAP-FIN-035) and
            # the raw aggregate path agree on this fixture's collected-to-date figure.
            find_rows=[
                {"unit_number": "TH001", "net_balance": 200.0, "total_levied": 1500.0},
                {"unit_number": "TH002", "net_balance": 0.0, "total_levied": 0.0},
            ],
            aggregate_rows=ledger_summary,
        ),
        units=_collection(find_rows=[{"unit_number": "TH001"}, {"unit_number": "TH002"}]),
        levy_payments=_collection(aggregate_rows=[]),
        building_summaries=_collection(find_one=None),
        buildings=_collection(find_one={}),
        financial_transactions=_collection(aggregate_rows=[]),
    )

    # building_id="13195" is real East Gate, which since 2026-08-09 is genuinely
    # PG-eligible for finance.summary -- pin the route to Mongo so this test keeps
    # exercising the Mongo-path raw_total_paid figure under test, not real Postgres.
    with patch("routers.finance.db", mock_db), \
         patch("utils.finance_helpers.db", mock_db), \
         patch("routers.finance.get_user_permissions", return_value=Permission(can_view_finances=True)), \
         patch("routers.finance.get_finance_route_runtime_state", AsyncMock(return_value={
             "route_key": "finance.summary", "source": "mongo", "run_shadow": False,
             "eligible_for_postgres_read": False, "blocked_reason": "forced mongo for unit test",
             "domain_mode": "mongo_primary", "route_readiness": {"status": "not_started"},
         })):
        result = await get_finance_summary(
            year="2026",
            current_user={"id": "u1", "role": "super_admin", "building_id": "13195"},
            building_id="13195",
        )

    assert result["unit_ledger_summary"]["total_paid"] == 1300.0
    assert result["unit_ledger_summary"]["raw_total_paid"] == 9999.0
    assert result["admin_fund"]["annual_levy_proposed"] == 1000.0
    assert result["admin_fund"]["proposed_income"] == 1000.0
    assert result["admin_fund"]["budgeted_expenses"] == 1000.0
    assert result["sinking_fund"]["annual_levy_proposed"] == 500.0
    assert result["sinking_fund"]["proposed_income"] == 500.0


@pytest.mark.asyncio
async def test_finance_charts_excludes_gst_as_income_and_hides_future_quarter_actuals():
    from routers.finance import get_finance_charts

    mock_db = SimpleNamespace(
        annual_levies=_collection(find_one={
            "year": "2026",
            "admin_fund": {"levy_income": 1000.0, "other_income": 0.0},
            "sinking_fund": {"levy_income": 500.0, "other_income": 0.0},
        }),
        levy_categories=_collection(find_rows=[]),
        levy_payments=_collection(aggregate_rows=[{"_id": "Q1", "total": 1500.0, "count": 1}]),
        settings=_collection(find_one={
            **_settings(),
            "levy_due_custom_dates": {"Q1": "2026-01-01", "Q2": "2026-02-01", "Q3": "2099-09-01", "Q4": "2099-12-01"},
        }),
        financial_transactions=_collection(aggregate_rows=[]),
    )

    with patch("routers.finance.db", mock_db), \
         patch("routers.finance.get_user_permissions", return_value=Permission(can_view_finances=True)):
        result = await get_finance_charts(
            year="2026",
            current_user={"id": "u1", "role": "super_admin", "building_id": "13195"},
            building_id="13195",
        )

    names = [row["name"] for row in result["income_by_category"]]
    assert "GST (10%)" not in names
    assert result["gst_summary"]["gst_registered"] is True
    assert result["gst_summary"]["gst_component"] == 150.0
    assert result["gst_summary"]["classification"] == "tax_collected_not_income"
    trend = {row["month"]: row for row in result["monthly_trend"]}
    assert trend["Q3 2026"]["income"] == 0
    assert trend["Q3 2026"]["levies"] == 0
    assert trend["Q3 2026"]["expenses"] == 0


@pytest.mark.asyncio
async def test_transaction_endpoints_fallback_to_financial_transactions_when_legacy_empty(pin_store):
    from routers.finance import get_expenses, get_income_transactions

    expense_row = {
        "id": "tx-exp",
        "year": "2026",
        "type": "Expense",
        "amount_cents": 12345,
        "transaction_date": "2026-03-01",
        "category_id": "cat-1",
        "category_name": "Insurance",
        "fund_type": "administrative",
        "supplier_name": "Insurer",
    }
    income_row = {
        "id": "tx-inc",
        "levy_year": "2026",
        "direction": "credit",
        "amount_cents": 7700,
        "transaction_date": "2026-03-02",
        "category_name": "Interest",
    }
    mock_db = SimpleNamespace(
        expense_transactions=_collection(find_rows=[]),
        income_transactions=_collection(find_rows=[]),
        financial_transactions=SimpleNamespace(
            find=MagicMock(side_effect=[_Cursor([expense_row]), _Cursor([income_row])])
        ),
    )

    # The legacy-empty -> financial_transactions fallback is a MONGO-side behaviour.
    # Pin the store, or the dispatch sends both handlers to the live PostgreSQL ledger
    # and the fallback under test never runs.
    with pin_store("mongo"), patch("routers.finance.db", mock_db):
        expenses = await get_expenses(
            year="2026",
            current_user={"id": "u1", "role": "super_admin"},
            building_id="13195",
        )
        income = await get_income_transactions(
            year="2026",
            current_user={"id": "u1", "role": "super_admin"},
            building_id="13195",
        )

    assert expenses[0].id == "tx-exp"
    assert expenses[0].amount == 123.45
    assert expenses[0].financial_year == "2026"
    assert income[0].id == "tx-inc"
    assert income[0].amount == 77.0


@pytest.mark.asyncio
async def test_levy_status_exposes_opening_arrears_and_paid_this_year(monkeypatch):
    from routers import finance

    mock_db = SimpleNamespace(
        units=_collection(find_one={"entitlement": 1}),
        settings=_collection(find_one=_settings()),
        annual_levies=_collection(find_one={
            "year": "2026",
            "admin_fund": {"levy_income": 1000.0},
            "sinking_fund": {"levy_income": 500.0},
            "payment_schedule": [],
        }),
        unit_levy_ledger=SimpleNamespace(
            find_one=AsyncMock(side_effect=[
                {"total_paid": 2000.0, "net_balance": -100.0, "opening_arrears": 0.0},
                {"net_balance": 0.0},
            ]),
            # lifetime_paid_task ("Total Paid" tile, cumulative across all levy
            # years) was added to get_levy_status's asyncio.gather() after this
            # fixture was written -- an unmocked SimpleNamespace has no .aggregate
            # attribute at all (AttributeError, not just a wrong return value).
            aggregate=MagicMock(return_value=_Cursor([])),
        ),
        levy_payments=SimpleNamespace(find=MagicMock(return_value=_Cursor([]))),
        strata_owners=_collection(find_one=None),
        _db=SimpleNamespace(journal_entries=SimpleNamespace(find=MagicMock(return_value=_Cursor([])))),
    )

    monkeypatch.setattr(finance, "db", mock_db)
    monkeypatch.setattr(finance, "date", _FixedToday)
    monkeypatch.setattr(finance, "get_levy_rates", AsyncMock(return_value={"admin_annual": 1000.0, "sinking_annual": 500.0}))
    monkeypatch.setattr(finance, "get_user_permissions", lambda _user: Permission(can_manage_finances=True))
    # Settings come from _get_general_settings, NOT from the mocked db.settings.
    #
    # `mock_db.settings` above is never read on this path: get_levy_status resolves
    # settings through the service, which uses its own module-level database handle.
    # So this assertion was silently reading EAST GATE'S LIVE SETTINGS for
    # levy_due_months, and passed only because the suite ran against the production
    # database. Pointed at its own database (GAP-TEST-001 step 2) it returned 100.0:
    # no settings -> levy_due_months=[] -> no period's due date has passed ->
    # levied_due_to_date=0 -> paid_this_year collapses to the credit alone.
    #
    # The whole point of the assertion is the proration ("only Q1+Q2 have come due"),
    # which is driven entirely by these four keys. Reading them from whatever the
    # environment happens to hold made the test's own subject ambient.
    monkeypatch.setattr(finance, "_get_general_settings", AsyncMock(return_value=_settings()))

    result = await finance.get_levy_status(
        unit_number="TH001",
        year="2026",
        current_user={"id": "u1", "role": "super_admin"},
        building_id="13195",
    )

    assert result["opening_arrears"] == 0.0
    # GAP-FIN-033 Part A2: paid_this_year = levied-to-date minus net_balance, NOT
    # the full annual levy. As of the fixed "today" (2026-08-02) with due months
    # [Mar, Jun, Sep, Dec], only Q1+Q2 ($750 of the $1,500 annual total) have come
    # due; net_balance=-100.0 is a $100 credit on top -> 750 + 100 = 850.0. The
    # previous contract (1500.0, i.e. the full annual levy) was the exact bug
    # live-confirmed on TH087 in production: a paid-up-or-ahead unit's
    # paid_this_year was clamped to equal its full annual levy regardless of how
    # much of the year had actually come due.
    assert result["paid_this_year"] == 850.0


@pytest.mark.asyncio
async def test_quarterly_budget_splits_ytd_levied_only_across_due_periods(monkeypatch):
    from routers import finance

    mock_db = SimpleNamespace(
        annual_levies=_collection(find_one={
            "year": "2026",
            "admin_fund": {"levy_income": 0.0},
            "sinking_fund": {"levy_income": 0.0},
            "proposed_admin_expenses": 1200.0,
            "proposed_sinking_expenses": 1000.0,
        }),
        levy_categories=_collection(find_rows=[]),
        unit_levy_ledger=_collection(find_rows=[
            {"total_levied": 1000.0, "net_balance": 0.0},
            {"total_levied": 1200.0, "net_balance": 0.0},
        ]),
        settings=_collection(find_one={
            "financial_year_start_month": 1,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "custom",
            "levy_due_custom_dates": {"3": 31, "6": 1, "9": 1, "12": 1},
        }),
        levy_payments=_collection(aggregate_rows=[]),
        financial_transactions=_collection(aggregate_rows=[]),
    )

    monkeypatch.setattr(finance, "db", mock_db)
    monkeypatch.setattr(finance, "_now", lambda: "2026-08-02T00:00:00")
    monkeypatch.setattr(finance, "get_user_permissions", lambda _user: Permission(can_view_finances=True))
    monkeypatch.setattr(finance, "get_finance_route_runtime_state", AsyncMock(return_value={"run_shadow": False}))

    result = await finance.get_quarterly_budget(
        year="2026",
        current_user={"id": "u1", "role": "super_admin"},
        building_id="13195",
    )

    q_by_label = {q["label"]: q for q in result["quarters"]}
    assert q_by_label["Q1"]["levied"] == 1100.0
    assert q_by_label["Q1"]["collected"] == 1100.0
    assert q_by_label["Q2"]["levied"] == 1100.0
    assert q_by_label["Q2"]["collected"] == 1100.0
    assert q_by_label["Q3"]["levied"] == 0.0
    assert q_by_label["Q3"]["has_ledger_data"] is False


@pytest.mark.asyncio
async def test_unit_levy_ledger_exposes_due_to_date_not_full_year_paid(monkeypatch):
    """The Finance page Levy Status tab must not treat the annual levy as fully
    due/paid in August. For a quarterly Jan-start building on 2026-08-02, only
    Q1 and Q2 are due; Q3/Q4 must not be shown as paid just because the annual
    ledger net_balance is zero."""
    from routers import finance

    mock_db = SimpleNamespace(
        unit_levy_ledger=_collection(find_rows=[{
            "building_id": "13195",
            "plan_id": "13195",
            "year": "2026",
            "unit_number": "TH001",
            "total_levied": 4000.0,
            "total_paid": 4000.0,
            "net_balance": 0.0,
        }]),
        units=_collection(find_rows=[{"unit_number": "TH001", "owner_name": "Owner One"}]),
        user_units=_collection(find_rows=[]),
        users=_collection(find_rows=[]),
    )

    monkeypatch.setattr(finance, "db", mock_db)
    monkeypatch.setattr(finance, "date", _FixedToday)
    monkeypatch.setattr(finance, "_get_general_settings", AsyncMock(return_value={
        "financial_year_start_month": 1,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "custom",
        "levy_due_custom_dates": {"3": 31, "6": 1, "9": 1, "12": 1},
    }))
    monkeypatch.setattr(finance, "_maybe_shadow_unit_levy_ledger", AsyncMock())
    monkeypatch.setattr(finance, "get_user_permissions", lambda _user: Permission(can_view_finances=True))

    rows = await finance.get_unit_levy_ledger(
        year="2026",
        current_user={"id": "u1", "role": "super_admin"},
        building_id="13195",
    )

    row = rows[0]
    assert row.annual_total_levied == 4000.0
    assert row.annual_paid_this_year == 4000.0
    assert row.levied_due_to_date == 2000.0
    assert row.paid_due_to_date == 2000.0
    assert row.outstanding_due_to_date == 0.0
    assert row.periods_due_to_date == 2
    assert row.total_periods == 4
    assert row.paid_this_year == 4000.0
