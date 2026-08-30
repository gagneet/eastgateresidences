import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta, date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        """Matches SQLAlchemy's Result.scalar() -- the raw single value from a
        one-column, one-row query (e.g. the levy_runs financial_year lookup), not a
        row/mapping. Construct with _FakeRows([value]).

        No longer used for unapplied credit: that moved to lot_true_balance, which
        returns per-lot ROWS and aggregates in Python."""
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        if not self._results:
            return _FakeRows([])
        return self._results.pop(0)


def _session_context(session):
    @asynccontextmanager
    async def _ctx():
        yield session
    return _ctx


def _scheme():
    return {
        "scheme_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
    }


@pytest.mark.asyncio
async def test_workflow_requests_list_uses_postgres_before_mongo():
    from routers.workflow_requests import list_workflow_requests

    now = datetime.now(timezone.utc)
    session = _FakeSession([
        _FakeRows([
            {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "title": "Lift service overdue",
                "description": "Quarterly service is late",
                "request_type": "maintenance",
                "priority": "urgent",
                "status": "assigned",
                "submitted_by_user_id": "33333333-3333-3333-3333-333333333333",
                "assigned_to": None,
                "created_at": now - timedelta(days=1),
                "updated_at": now,
                "due_at": now - timedelta(hours=2),
                "unit_number": "12",
                "sla_breached": True,
            }
        ])
    ])
    mock_db = MagicMock()
    mock_db.workflow_requests.find = MagicMock(side_effect=AssertionError("Mongo fallback should not run"))

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("services.cutover_config_service.is_cutover_feature_enabled", AsyncMock(return_value=True)),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("routers.workflow_requests.db", mock_db),
    ):
        result = await list_workflow_requests(
            status="overdue",
            request_type=None,
            limit=8,
            current_user={"id": "manager-1", "role": "strata_manager"},
            building_id="13195",
        )

    assert result[0]["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert result[0]["building_id"] == "13195"
    assert result[0]["status"] == "overdue"
    assert result[0]["sla_breached"] is True
    mock_db.workflow_requests.find.assert_not_called()
    assert session.executed[0][1]["limit"] == 8


@pytest.mark.asyncio
async def test_workflow_requests_list_falls_back_to_mongo_when_postgres_empty():
    from routers.workflow_requests import list_workflow_requests

    session = _FakeSession([_FakeRows([])])
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[{"id": "mongo-1", "building_id": "13195", "status": "open"}])
    mock_db = MagicMock()
    mock_db.workflow_requests.find.return_value = cursor

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("routers.workflow_requests.db", mock_db),
    ):
        result = await list_workflow_requests(
            status="open",
            request_type=None,
            current_user={"id": "owner-1", "role": "owner"},
            building_id="13195",
        )

    assert result[0]["id"] == "mongo-1"
    query = mock_db.workflow_requests.find.call_args[0][0]
    assert query["submitted_by_user_id"] == "owner-1"
    assert query["is_test_data"] == {"$ne": True}


@pytest.mark.asyncio
async def test_workflow_requests_overdue_mongo_filter_excludes_terminal_rows():
    from routers.workflow_requests import list_workflow_requests

    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    mock_db = MagicMock()
    mock_db.workflow_requests.find.return_value = cursor

    with (
        patch("services.cutover_config_service.is_cutover_feature_enabled", AsyncMock(return_value=False)),
        patch("routers.workflow_requests.db", mock_db),
    ):
        await list_workflow_requests(
            status="overdue",
            request_type=None,
            current_user={"id": "manager-1", "role": "strata_manager"},
            building_id="13195",
        )

    query = mock_db.workflow_requests.find.call_args[0][0]
    assert query["$or"] == [{"sla_breached": True}, {"status": "overdue"}]
    assert set(query["status"]["$nin"]) == {"closed", "auto_resolved", "completed", "cancelled"}


@pytest.mark.asyncio
async def test_update_request_status_clears_stale_overdue_flags_on_close():
    from models.community_os import WorkflowRequestStatusUpdate
    from routers.workflow_requests import update_request_status

    doc = {
        "id": "req-1",
        "building_id": "13195",
        "request_type": "maintenance",
        "status": "overdue",
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "sla_breached": True,
        "needs_human_review": True,
    }
    mock_db = MagicMock()
    mock_db.workflow_requests.find_one = AsyncMock(return_value=doc.copy())
    mock_db.workflow_requests.update_one = AsyncMock()

    with (
        patch("routers.workflow_requests.db", mock_db),
        patch("routers.workflow_requests.create_audit_log", AsyncMock()),
    ):
        result = await update_request_status(
            "req-1",
            WorkflowRequestStatusUpdate(status="closed", resolution_notes="Done"),
            current_user={"id": "manager-1", "role": "strata_manager", "full_name": "Manager"},
            building_id="13195",
        )

    update_doc = mock_db.workflow_requests.update_one.call_args[0][1]["$set"]
    assert update_doc["status"] == "closed"
    assert update_doc["sla_breached"] is False
    assert update_doc["needs_human_review"] is False
    assert update_doc["closed_at"]
    assert result.sla_breached is False


@pytest.mark.asyncio
async def test_triage_stats_mongo_first_postgres_supplements_auto_resolved():
    """MongoDB runs first for all counts; Postgres supplements auto-resolution metrics
    when GOVERNANCE_READ_PG_ENABLED is on."""
    from routers.workflow_requests import get_triage_stats

    # Postgres session returns auto-resolution supplement rows
    session = _FakeSession([
        _FakeRows([{
            "total_received_week": 10,
            "auto_resolved_today": 2,
            "auto_resolved_week": 4,
            "sla_breaches": 3,
        }])
    ])

    mock_db = MagicMock()
    # 8 gather tasks: 6x workflow_requests.count_documents, 1x units, 1x memberships aggregate
    # side_effect order matches tasks list in get_triage_stats:
    #   [0] auto_resolved_today=0, [1] total_week=10, [2] auto_resolved_week=0,
    #   [3] pending_total=5, [4] open_total=12, [5] sla_breaches=1
    mock_db.workflow_requests.count_documents = AsyncMock(
        side_effect=[0, 10, 0, 5, 12, 1]
    )
    mock_db.units.count_documents = AsyncMock(return_value=87)   # total_lots
    pending_cursor = MagicMock()
    pending_cursor.to_list = AsyncMock(return_value=[{"count": 2}])
    mock_db.memberships.aggregate = MagicMock(return_value=pending_cursor)

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("routers.workflow_requests.db", mock_db),
        patch("services.cutover_config_service.is_cutover_feature_enabled",
              AsyncMock(return_value=True)),
    ):
        result = await get_triage_stats(
            current_user={"id": "manager-1", "role": "strata_manager"},
            building_id="13195",
        )

    # MongoDB was called (primary source)
    mock_db.workflow_requests.count_documents.assert_called()
    mock_db.memberships.aggregate.assert_called()
    # Postgres supplemented the auto-resolution metrics
    assert result["source"] == "mongodb+postgresql"
    assert result["auto_resolved_today"] == 2        # overridden by Postgres
    assert result["deflection_rate_pct"] == 40.0     # 4/10 * 100
    assert result["total_received_week"] == 10       # overridden by Postgres
    assert result["sla_breaches"] == 3          # higher PG count is retained
    assert result["pending_registrations"] == 2      # from MongoDB memberships+users aggregation


@pytest.mark.asyncio
async def test_triage_stats_postgres_sla_breaches_do_not_require_weekly_received_cases():
    from routers.workflow_requests import get_triage_stats

    session = _FakeSession([
        _FakeRows([{
            "total_received_week": 0,
            "auto_resolved_today": 0,
            "auto_resolved_week": 0,
            "sla_breaches": 2,
        }])
    ])

    mock_db = MagicMock()
    mock_db.workflow_requests.count_documents = AsyncMock(
        side_effect=[0, 0, 0, 0, 0, 0]
    )
    mock_db.units.count_documents = AsyncMock(return_value=87)
    pending_cursor = MagicMock()
    pending_cursor.to_list = AsyncMock(return_value=[])
    mock_db.memberships.aggregate = MagicMock(return_value=pending_cursor)

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("routers.workflow_requests.db", mock_db),
        patch("services.cutover_config_service.is_cutover_feature_enabled",
              AsyncMock(return_value=True)),
    ):
        result = await get_triage_stats(
            current_user={"id": "manager-1", "role": "strata_manager"},
            building_id="13195",
        )

    assert result["total_received_week"] == 0
    assert result["sla_breaches"] == 2
    assert result["source"] == "mongodb+postgresql"


@pytest.mark.asyncio
async def test_triage_stats_postgres_zero_does_not_hide_mongo_sla_breaches():
    from routers.workflow_requests import get_triage_stats

    session = _FakeSession([
        _FakeRows([{
            "total_received_week": 0,
            "auto_resolved_today": 0,
            "auto_resolved_week": 0,
            "sla_breaches": 0,
        }])
    ])

    mock_db = MagicMock()
    mock_db.workflow_requests.count_documents = AsyncMock(
        side_effect=[0, 0, 0, 0, 0, 4]
    )
    mock_db.units.count_documents = AsyncMock(return_value=87)
    pending_cursor = MagicMock()
    pending_cursor.to_list = AsyncMock(return_value=[])
    mock_db.memberships.aggregate = MagicMock(return_value=pending_cursor)

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("routers.workflow_requests.db", mock_db),
        patch("services.cutover_config_service.is_cutover_feature_enabled",
              AsyncMock(return_value=True)),
    ):
        result = await get_triage_stats(
            current_user={"id": "manager-1", "role": "strata_manager"},
            building_id="13195",
        )

    assert result["sla_breaches"] == 4
    assert result["source"] == "mongodb"


@pytest.mark.asyncio
async def test_triage_stats_allows_strata_admin_role():
    from routers.workflow_requests import get_triage_stats

    mock_db = MagicMock()
    mock_db.workflow_requests.count_documents = AsyncMock(
        side_effect=[1, 5, 2, 3, 4, 2]
    )
    mock_db.units.count_documents = AsyncMock(return_value=50)
    pending_cursor = MagicMock()
    pending_cursor.to_list = AsyncMock(return_value=[{"count": 1}])
    mock_db.memberships.aggregate = MagicMock(return_value=pending_cursor)

    with (
        patch("routers.workflow_requests.db", mock_db),
        patch("services.cutover_config_service.is_cutover_feature_enabled", AsyncMock(return_value=False)),
    ):
        result = await get_triage_stats(
            current_user={"id": "admin-1", "role": "strata_admin"},
            building_id="13195",
        )

    assert result["pending_registrations"] == 1
    assert result["sla_breaches"] == 2


@pytest.mark.asyncio
async def test_maintenance_spend_trend_uses_postgres_work_orders_before_mongo():
    from routers.analytics import get_maintenance_spend_trend

    session = _FakeSession([
        _FakeRows([
            {
                "month": "2026-05",
                "vendor_name": "BluePoint Plumbing",
                "jobs": 3,
                "spend_cents": 125000,
            }
        ])
    ])
    mock_db = MagicMock()
    mock_db.maintenance_requests.aggregate = MagicMock()

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("routers.analytics.db", mock_db),
    ):
        result = await get_maintenance_spend_trend(
            current_user={"id": "manager-1", "role": "strata_manager"},
            building_id="13195",
        )

    # ops.work_orders has no due/completion timestamps, so on-time completion is not
    # tracked yet — the endpoint must not fabricate a value (was hardcoded 100.0).
    assert result == [{
        "month": "2026-05",
        "vendor_name": "BluePoint Plumbing",
        "vendor": "BluePoint Plumbing",
        "jobs": 3,
        "spend": 1250.0,
        "source": "postgresql",
    }]
    assert "on_time" not in result[0]
    mock_db.maintenance_requests.aggregate.assert_not_called()


@pytest.mark.asyncio
async def test_building_overview_uses_postgres_ledger_contract_without_mongo():
    from routers.finance import get_building_fund_overview

    pg_service = MagicMock()
    pg_service.get_consolidated_fund_balances = AsyncMock(return_value={
        "admin_balance_cents": 918744,
        "sinking_balance_cents": 19333703,
        "source": "analytics.fact_financial_balance",
    })
    pg_service.get_fund_balances = AsyncMock(return_value={
        "admin_balance_cents": 400000,
        "sinking_balance_cents": 600000,
    })
    session = _FakeSession([
        _FakeRows([
            SimpleNamespace(fund_type="admin", levied_cents=600000, paid_cents=450000),
            SimpleNamespace(fund_type="sinking", levied_cents=400000, paid_cents=300000),
        ]),
        _FakeRows([
            SimpleNamespace(lot_id="lot-1", arrears_cents=100000),
            SimpleNamespace(lot_id="lot-2", arrears_cents=100000),
            SimpleNamespace(lot_id="lot-3", arrears_cents=100000),
        ]),
        # Unapplied credit is no longer one scalar query. /finance/building-overview
        # delegates to services.finance_metrics.lot_true_balance, which issues TWO
        # SELECTs -- outstanding-per-lot, then credit-per-lot -- and combines them in
        # Python. Both empty here: this fixture has no credit lots, and the assertion
        # below is still unapplied_credit == 0.0.
        #
        # The inline query this replaced under-counted reversals and ignored
        # retired_at; live on East Gate FY2026 it reported $1,783,940.36 against a
        # true $13,478.55.
        _FakeRows([]),  # lot_true_balance: outstanding-per-lot
        _FakeRows([]),  # lot_true_balance: credit-per-lot
    ])
    mock_db = MagicMock()
    mock_db.unit_levy_ledger.aggregate = MagicMock(side_effect=AssertionError("Mongo ledger must not be read"))
    mock_db.annual_levies.find = MagicMock(side_effect=AssertionError("Mongo annual_levies must not be read"))

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("routers.finance._financial_read_service", pg_service),
        patch("routers.finance.db", mock_db),
        patch(
            "routers.finance.get_finance_route_runtime_state",
            AsyncMock(return_value={"source": "postgres", "run_shadow": False}),
        ),
    ):
        result = await get_building_fund_overview(
            year="2026",
            current_user={"id": "owner-1", "role": "owner"},
            building_id="13195",
        )

    assert result["source"] == "postgres_ledger"
    assert result["total_levied"] == 10000.0
    assert result["total_paid"] == 7500.0
    assert result["total_outstanding"] == 3000.0
    assert result["units_in_arrears"] == 3
    # 2026-08-05 additive fields -- must not change total_paid/levies_paid_pct above.
    assert result["unapplied_credit"] == 0.0
    assert result["total_received_including_credit"] == result["total_paid"]
    assert result["admin_fund"]["current_balance"] == 9187.44
    assert result["sinking_fund"]["current_balance"] == 193337.03
    # GAP-FIN-016 Item C regression guard (corrected in same-day audit):
    # fund_health/total_obligations use current_year_collection_rate() with
    # opening_arrears_cents=0 (Postgres has no source for it — this branch
    # must not read Mongo, see unit_levy_ledger.aggregate.assert_not_called()
    # below) — total_obligations=10000, net_collected=max(0,10000-3000)=7000,
    # fund_health=7000/10000*100=70.0.
    #
    # levies_paid_pct deliberately stays paid/levied=7500/10000*100=75.0, NOT
    # the same formula as fund_health — outstanding (3000) is set to differ
    # from levied-paid (2500) precisely so this test cannot pass by
    # coincidence if the two formulas were ever conflated again. See the
    # audit correction comment on this branch's levies_paid_pct assignment in
    # routers/finance.py for why they must stay different (a documented,
    # pre-existing "PG overview: paid/levied" rate family, per
    # docs/features/StrataOS_Financial_UI_Label_and_Calculation_Register.md).
    assert result["total_opening_arrears"] == 0.0
    assert result["total_obligations"] == 10000.0
    assert result["fund_health"] == 70.0
    assert result["levies_paid_pct"] == 75.0
    # GAP-FIN-016 Phase 2b Item B1: admin=450000/600000*100=75.0, sinking=300000/400000*100=75.0
    assert result["admin_fund"]["collection_rate"] == 75.0
    assert result["sinking_fund"]["collection_rate"] == 75.0
    pg_service.get_consolidated_fund_balances.assert_awaited_once_with(
        building_id="13195",
        financial_year="2026",
    )
    pg_service.get_fund_balances.assert_not_awaited()
    mock_db.unit_levy_ledger.aggregate.assert_not_called()
    mock_db.annual_levies.find.assert_not_called()


@pytest.mark.asyncio
async def test_building_overview_serves_mongo_when_cutover_gate_not_promoted():
    """Today's real default: finance_ledger's domain_mode is postgres_shadow,
    so get_finance_route_runtime_state() resolves source="mongo" for this
    route. The route must honour that gate rather than reading Postgres
    unconditionally — this is the fix for the gate-bypass bug (both this
    route and get_unit_dashboard_overview previously ignored the gate
    entirely and always read Postgres first)."""
    from routers.finance import get_building_fund_overview

    mongo_fallback_result = {"source": "mongodb_fallback", "total_levied": 5000.0}

    with (
        patch(
            "routers.finance.get_finance_route_runtime_state",
            AsyncMock(return_value={"source": "mongo", "run_shadow": True}),
        ),
        patch(
            "routers.finance._get_building_overview_mongo_fallback",
            AsyncMock(return_value=mongo_fallback_result),
        ) as mongo_fallback,
        patch("routers.finance._maybe_shadow_building_overview", AsyncMock()),
        patch(
            "db_postgres.repos.config_repo.resolve_scheme_context",
            AsyncMock(side_effect=AssertionError("Postgres must not be read when gate resolves to mongo")),
        ),
    ):
        result = await get_building_fund_overview(
            year="2026",
            current_user={"id": "owner-1", "role": "owner"},
            building_id="13195",
        )

    assert result is mongo_fallback_result
    mongo_fallback.assert_awaited_once_with("13195", "2026")


@pytest.mark.asyncio
async def test_building_overview_empty_postgres_ledger_returns_zero_contract():
    from routers.finance import get_building_fund_overview

    pg_service = MagicMock()
    pg_service.get_consolidated_fund_balances = AsyncMock(return_value=None)
    pg_service.get_fund_balances = AsyncMock(return_value={
        "admin_balance_cents": 0,
        "sinking_balance_cents": 0,
    })
    # Three empty results became four: the credit scalar is now two SELECTs from
    # services.finance_metrics.lot_true_balance (outstanding-per-lot, credit-per-lot).
    session = _FakeSession([_FakeRows([]), _FakeRows([]), _FakeRows([]), _FakeRows([])])
    mock_db = MagicMock()
    mock_db.unit_levy_ledger.aggregate = MagicMock(side_effect=AssertionError("Mongo ledger must not be read"))

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
        patch("routers.finance._financial_read_service", pg_service),
        patch("routers.finance.db", mock_db),
        patch(
            "routers.finance.get_finance_route_runtime_state",
            AsyncMock(return_value={"source": "postgres", "run_shadow": False}),
        ),
    ):
        result = await get_building_fund_overview(
            year="2027",
            current_user={"id": "owner-1", "role": "owner"},
            building_id="13195",
        )

    assert result["source"] == "postgres_ledger"
    assert result["year"] == "2027"
    assert result["total_levied"] == 0.0
    assert result["admin_fund"]["current_balance"] == 0.0
    mock_db.unit_levy_ledger.aggregate.assert_not_called()


@pytest.mark.asyncio
async def test_building_overview_falls_back_to_mongo_on_postgres_error():
    """A genuine PG failure (connection/query error, not 'no data yet') must
    fall back to MongoDB rather than 503ing the whole dashboard card. This is
    the opposite case from test_building_overview_*_without_mongo above: those
    assert Mongo is NOT touched when PG succeeds (with or without data); this
    asserts Mongo IS used, correctly, when PG genuinely errors."""
    from routers.finance import get_building_fund_overview

    pg_service = MagicMock()
    # resolve_scheme_context raising simulates a genuine PG connectivity/query
    # failure — distinct from resolve_scheme_context returning None (no scheme
    # onboarded yet), which is the empty-contract path and must NOT fall back.
    mongo_agg_cursor = MagicMock()
    mongo_agg_cursor.to_list = AsyncMock(return_value=[{
        "admin_levied": 3000.0, "admin_paid": 2000.0,
        "sinking_levied": 2000.0, "sinking_paid": 1500.0,
        "total_levied": 5000.0, "total_paid": 3500.0,
        "total_opening_arrears": 100.0, "total_outstanding": 1600.0,
    }])
    units_agg_cursor = MagicMock()
    units_agg_cursor.to_list = AsyncMock(return_value=[{"count": 4}])
    trend_cursor = MagicMock()
    trend_cursor.sort = MagicMock(return_value=trend_cursor)
    trend_cursor.limit = MagicMock(return_value=trend_cursor)
    trend_cursor.to_list = AsyncMock(return_value=[])

    mock_db = MagicMock()
    mock_db.unit_levy_ledger.aggregate = MagicMock(side_effect=[mongo_agg_cursor, units_agg_cursor])
    mock_db.annual_levies.find = MagicMock(return_value=trend_cursor)
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)

    with (
        patch(
            "db_postgres.repos.config_repo.resolve_scheme_context",
            AsyncMock(side_effect=RuntimeError("connection refused")),
        ),
        patch("routers.finance._financial_read_service", pg_service),
        patch("routers.finance.db", mock_db),
        patch("routers.finance._resolve_default_levy_year", AsyncMock(return_value="2026")),
        patch(
            "routers.finance._financial_read_service.get_consolidated_fund_balances",
            AsyncMock(return_value=None),
        ),
        patch(
            "routers.finance.get_finance_route_runtime_state",
            AsyncMock(return_value={"source": "postgres", "run_shadow": False}),
        ),
    ):
        result = await get_building_fund_overview(
            year="2026",
            current_user={"id": "owner-1", "role": "owner"},
            building_id="13195",
        )

    assert result["source"] == "mongodb_fallback"
    assert result["total_levied"] == 5000.0
    assert result["total_outstanding"] == 1600.0
    assert result["units_in_arrears"] == 4
    mock_db.unit_levy_ledger.aggregate.assert_called()
    trend_cursor.sort.assert_called_once_with("year", -1)
    trend_cursor.limit.assert_called_once_with(8)


@pytest.mark.asyncio
async def test_building_overview_mongo_fallback_uses_spend_trend_and_current_balance():
    from routers.finance import _get_building_overview_mongo_fallback

    summary_cursor = MagicMock()
    summary_cursor.to_list = AsyncMock(return_value=[])
    arrears_cursor = MagicMock()
    arrears_cursor.to_list = AsyncMock(return_value=[])
    trend_cursor = MagicMock()
    trend_cursor.sort = MagicMock(return_value=trend_cursor)
    trend_cursor.limit = MagicMock(return_value=trend_cursor)
    trend_cursor.to_list = AsyncMock(return_value=[
        {
            "year": "2026",
            "admin_fund": {"current_balance": 9187.44, "closing_balance": 180000, "total_expenses": 145628.22},
            "sinking_fund": {"current_balance": 193337.03, "closing_balance": 298398.0, "total_expenses": 72084.22},
        },
        {"year": "2025", "admin_fund": {"closing_balance": 2500, "total_expenses": 277998.8}, "sinking_fund": {"closing_balance": 5000, "total_expenses": 14950.0}},
        {"year": "2024", "admin_fund": {"closing_balance": 2400, "total_expenses": 311666.8}, "sinking_fund": {"closing_balance": 4800, "total_expenses": 9815.0}},
        {"year": "2023", "admin_fund": {"closing_balance": 2300, "total_expenses": 169266.08}, "sinking_fund": {"closing_balance": 4600, "total_expenses": 8447.0}},
        {"year": "2022", "admin_fund": {"closing_balance": 2200, "total_expenses": 216901.65}, "sinking_fund": {"closing_balance": 4400, "total_expenses": 4350.0}},
        {"year": "2021", "admin_fund": {"closing_balance": 2100, "total_expenses": 162221.63}, "sinking_fund": {"closing_balance": 4200, "total_expenses": 0.0}},
        {"year": "2020", "admin_fund": {"closing_balance": 2000, "total_expenses": 1000.0}, "sinking_fund": {"closing_balance": 4000, "total_expenses": 500.0}},
        {"year": "2019", "admin_fund": {"closing_balance": 1900, "total_expenses": 900.0}, "sinking_fund": {"closing_balance": 3800, "total_expenses": 400.0}},
    ])

    mock_db = MagicMock()
    mock_db.unit_levy_ledger.aggregate = MagicMock(side_effect=[summary_cursor, arrears_cursor])
    mock_db.annual_levies.find = MagicMock(return_value=trend_cursor)

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance._resolve_current_levy_year", AsyncMock(return_value=2026)),
        patch(
            "routers.finance._financial_read_service.get_consolidated_fund_balances",
            AsyncMock(return_value=None),
        ),
    ):
        result = await _get_building_overview_mongo_fallback(building_id="13195", year="2026")

    trend_cursor.sort.assert_called_once_with("year", -1)
    trend_cursor.limit.assert_called_once_with(8)
    # GAP-DASH-001: every trend point is now the fund BALANCE (get_annual_fund_balance's
    # current_balance -> closing_balance_actual -> closing_balance -> opening_balance
    # precedence), not total_expenses for prior years mixed with balance for the
    # current year — that mixing crushed the small prior-year expense points flat
    # against the large current-year balance. Each mock doc below only has
    # closing_balance (no current_balance) except 2026, which has current_balance
    # and wins per that precedence.
    assert result["admin_fund_trend"] == [1900.0, 2000.0, 2100.0, 2200.0, 2300.0, 2400.0, 2500.0, 9187.44]
    assert result["sinking_fund_trend"] == [3800.0, 4000.0, 4200.0, 4400.0, 4600.0, 4800.0, 5000.0, 193337.03]
    assert result["admin_fund"]["current_balance"] == 9187.44
    assert result["admin_fund"]["closing_balance"] == 180000.0
    assert result["admin_fund"]["balance_source"] == "current_balance"
    assert result["sinking_fund"]["current_balance"] == 193337.03


@pytest.mark.asyncio
async def test_building_overview_mongo_fallback_overlays_consolidated_fund_balances():
    from routers.finance import _get_building_overview_mongo_fallback

    summary_cursor = MagicMock()
    summary_cursor.to_list = AsyncMock(return_value=[])
    arrears_cursor = MagicMock()
    arrears_cursor.to_list = AsyncMock(return_value=[])
    trend_cursor = MagicMock()
    trend_cursor.sort = MagicMock(return_value=trend_cursor)
    trend_cursor.limit = MagicMock(return_value=trend_cursor)
    trend_cursor.to_list = AsyncMock(return_value=[
        {
            "year": "2026",
            "admin_fund": {"current_balance": 1.0, "closing_balance": 180000},
            "sinking_fund": {"current_balance": 2.0, "closing_balance": 298398.0},
        },
    ])

    mock_db = MagicMock()
    mock_db.unit_levy_ledger.aggregate = MagicMock(side_effect=[summary_cursor, arrears_cursor])
    mock_db.annual_levies.find = MagicMock(return_value=trend_cursor)

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance._resolve_current_levy_year", AsyncMock(return_value=2026)),
        patch(
            "routers.finance._financial_read_service.get_consolidated_fund_balances",
            AsyncMock(return_value={
                "admin_balance_cents": 918744,
                "sinking_balance_cents": 19333703,
                "source": "analytics.fact_financial_balance",
            }),
        ),
    ):
        result = await _get_building_overview_mongo_fallback(building_id="13195", year="2026")

    assert result["admin_fund"]["current_balance"] == 9187.44
    assert result["admin_fund"]["balance_cents"] == 918744
    assert result["admin_fund"]["balance_source"] == "analytics.fact_financial_balance"
    assert result["admin_fund_trend"][-1] == 9187.44
    assert result["sinking_fund"]["current_balance"] == 193337.03
    assert result["sinking_fund"]["balance_cents"] == 19333703
    assert result["sinking_fund"]["balance_source"] == "analytics.fact_financial_balance"
    assert result["sinking_fund_trend"][-1] == 193337.03


@pytest.mark.asyncio
async def test_my_streak_uses_selected_unit_postgres_ledger_rows():
    from routers.analytics import get_my_streak

    session = _FakeSession([
        _FakeRows([{
            "lot_id": "lot-selected",
            "lot_number": "TH087",
            "entitlement_units": 120,
            "scheme_id": _scheme()["scheme_id"],
            "total_entitlement": 1200,
        }]),
        _FakeRows([
            SimpleNamespace(financial_year="2026", quarter_no=2, due_date=datetime(2026, 6, 1).date(), levied_cents=100000, paid_cents=100000),
            SimpleNamespace(financial_year="2026", quarter_no=1, due_date=datetime(2026, 3, 1).date(), levied_cents=100000, paid_cents=50000),
        ]),
    ])

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value=_scheme())),
        patch("db_postgres.session.async_session_context", _session_context(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
    ):
        result = await get_my_streak(
            unit_number="TH087",
            current_user={"id": "owner-1", "role": "owner", "unit_number": "TH017"},
            building_id="13195",
        )

    assert result["source"] == "postgres_ledger"
    assert result["entitlement_pct"] == 10.0
    assert result["total_quarters"] == 2
    assert result["on_time_count"] == 1
    assert result["streak"] == 1
    assert result["recent_quarters"][0]["quarter"] == "2"
    assert result["recent_quarters"][0]["status"] == "paid"
    assert result["recent_quarters"][1]["quarter"] == "1"
    assert result["recent_quarters"][1]["status"] == "partial"
    assert session.executed[0][1]["unit_number"] == "TH087"


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_computes_from_ledger():
    """Direct unit test of _get_unit_dashboard_overview_mongo_fallback — the
    helper GET /finance/unit-dashboard-overview/{unit} calls only when the PG
    ledger read raises a genuine error (see get_unit_dashboard_overview's
    except block). Tested directly rather than through the full endpoint to
    avoid re-mocking the unrelated canonical-unit-number/permission layers,
    which this change does not touch."""
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 100})
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "year": "2026", "total_levied": 902.77, "total_paid": 450.0,
        "net_balance": 452.77, "admin_paid": 300.0, "sinking_paid": 150.0,
        "next_due_date": "2026-09-01",
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value={
        "year": "2026", "payment_schedule": [],
    })

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.get_levy_rates", AsyncMock(return_value={"admin_annual": 5.0, "sinking_annual": 3.0})),
        patch("routers.finance.compute_mongo_quarter_statuses", MagicMock(return_value=[])),
    ):
        result = await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year="2026",
        )

    assert result["source"] == "mongodb_fallback"
    assert result["unit_number"] == "TH087"
    assert result["total_levied"] == 902.77
    assert result["total_paid"] == 450.0
    assert result["balance_owing"] == 452.77
    assert result["balance_credit"] == 0.0


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_computes_paid_this_year():
    """Regression test for the 2026-08-01 "Paid to date" bug: total_paid is not reliably
    scoped to one year (confirmed live via a real East Gate unit's own reconciliation_note --
    "back-solved from the portal's live outstanding balance... cumulative payment history
    through the scrape date, not payments received within this calendar year specifically").
    paid_this_year (= total_levied - net_balance) is the field that must be used instead.
    Real numbers: East Gate unit TH087, FY2026."""
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 161})
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "year": "2026", "total_levied": 3545.02, "total_paid": 28783.04,
        "net_balance": -254.98, "admin_paid": 22149.54, "sinking_paid": 6633.50,
        "next_due_date": "2026-09-01",
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2026", "payment_schedule": None})

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.get_levy_rates", AsyncMock(return_value={"admin_annual": 34.09, "sinking_annual": 9.95})),
        patch("routers.finance.compute_mongo_quarter_statuses", MagicMock(return_value=[])),
    ):
        result = await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year="2026",
        )

    assert result["total_paid"] == 28783.04       # kept for backward compatibility, NOT for display
    assert result["paid_this_year"] == 3800.0     # 3545.02 - (-254.98)
    assert result["balance_owing"] == 0.0         # in credit -- correctly $0 arrears, a DIFFERENT concept
    assert result["balance_credit"] == 254.98


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_computes_next_due_from_settings():
    """Regression test for the 2026-08-01 "Next Due: Not scheduled" bug: ledger.next_due_date
    and annual_levies.payment_schedule can both be absent (confirmed live for East Gate FY2026),
    even though the building's own configured levy schedule already defines the next due date.
    Must fall back to compute_period_due_dates() using settings, not show "Not scheduled"."""
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 161})
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "year": "2026", "total_levied": 3545.02, "total_paid": 28783.04, "net_balance": -254.98,
        # no next_due_date field at all
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2026", "payment_schedule": None})

    settings_doc = {
        "levy_due_months": [3, 6, 9, 12], "levy_due_day_type": "custom",
        "levy_due_custom_dates": {"3": 31, "6": 1, "9": 1, "12": 1},
        "financial_year_start_month": 1,
    }

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.get_levy_rates", AsyncMock(return_value={"admin_annual": 34.09, "sinking_annual": 9.95})),
        patch("routers.finance.compute_mongo_quarter_statuses", MagicMock(return_value=[])),
        patch("routers.finance._get_general_settings", AsyncMock(return_value=settings_doc)),
    ):
        result = await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year="2026",
        )

    # Runs against the real current date rather than mocking it -- mirrors the source's own
    # "no upcoming date -> None" behaviour if this test is ever run after 2026-12-01 (the last
    # of FY2026's four configured due dates), rather than crashing on an empty candidate list.
    from datetime import date as _date
    today_iso = _date.today().isoformat()
    candidates = sorted(d for d in ["2026-03-31", "2026-06-01", "2026-09-01", "2026-12-01"] if d >= today_iso)
    assert result["next_due_date"] == (candidates[0] if candidates else None)
    # Even split of the annual total across 4 configured periods, offset by net_balance: this
    # unit is in credit (net_balance=-254.98, the real TH087 figure), so that credit reduces
    # what's actually owed next time. Mocked rates here (34.09/9.95) approximate but don't
    # exactly reproduce TH087's real per-UOE rates, so this asserts against the formula's own
    # output rather than the real dollar figure -- the real-figure case (base $1,772.51 -> net
    # $1,517.53, confirmed against the reporting owner's own expected calculation) is verified
    # directly against the live endpoint, not re-derived from approximated test rates here.
    base_instalment = (34.09 * 161 + 9.95 * 161) / 4
    assert result["next_payment_amount"] == round(base_instalment - 254.98, 2)


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_passes_paid_this_year_to_quarter_statuses():
    """Regression test for a re-audit finding (2026-08-01): compute_mongo_quarter_statuses
    waterfalls its third argument against quarters earliest-first to derive each one's paid/
    partial/overdue/unpaid status. The paid_this_year fix passed total_paid into it unchanged --
    missed because the reporting unit (TH087) has payment_schedule=None, so quarters=[]
    regardless of which figure was passed, masking the bug. Feeding the uncapped, potentially
    many-years-cumulative total_paid here would waterfall an amount several years too large
    across a single year's quarters, marking all of them "paid" regardless of what was actually
    paid toward THIS year's charges."""
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 161})
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "year": "2026", "total_levied": 3545.02, "total_paid": 28783.04, "net_balance": -254.98,
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2026", "payment_schedule": []})

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.get_levy_rates", AsyncMock(return_value={"admin_annual": 5.0, "sinking_annual": 3.0})),
        patch("routers.finance.compute_mongo_quarter_statuses", MagicMock(return_value=[])) as mock_quarters,
    ):
        await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year="2026",
        )

    args, _ = mock_quarters.call_args
    assert args[2] == 3800.0        # paid_this_year (3545.02 - (-254.98)), NOT total_paid (28783.04)
    assert args[2] != 28783.04


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_skips_estimate_when_real_quarter_is_unpaid():
    """Regression test for a re-audit finding (2026-08-01): the next_payment_amount fallback
    estimate must not be computed at all when quarters already has a genuine unpaid/partial
    upcoming period -- a prior version's comment claimed the frontend's own fallback chain
    already guaranteed this, which was false (that chain checks next_payment_amount BEFORE the
    quarters-derived value, so setting it here would have taken priority over real data)."""
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 161})
    # net_balance positive (arrears) -> paid_this_year = 7090.04 - 3290.04 = 3800.0, less than
    # total_levied -- leaves later quarters genuinely unpaid once waterfalled.
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "year": "2026", "total_levied": 7090.04, "total_paid": 28783.04, "net_balance": 3290.04,
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value={
        "year": "2026",
        "payment_schedule": [
            {"quarter": "Q1", "due_date": "2026-03-31"},
            {"quarter": "Q2", "due_date": "2026-06-30"},
            {"quarter": "Q3", "due_date": "2026-09-01"},
            {"quarter": "Q4", "due_date": "2026-12-01"},
        ],
    })
    settings_doc = {
        "levy_due_months": [3, 6, 9, 12], "levy_due_day_type": "custom",
        "levy_due_custom_dates": {"3": 31, "6": 1, "9": 1, "12": 1},
        "financial_year_start_month": 1,
    }

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.get_levy_rates", AsyncMock(return_value={"admin_annual": 34.09, "sinking_annual": 9.95})),
        patch("routers.finance._get_general_settings", AsyncMock(return_value=settings_doc)),
    ):
        result = await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year="2026",
        )

    unpaid_quarters = [q for q in result["quarters"] if q["status"] != "paid"]
    assert len(unpaid_quarters) > 0  # sanity check: this scenario does leave real unpaid quarters
    assert result["next_payment_amount"] is None


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_skips_ahead_by_whole_prepaid_quarters():
    """Superseded 2026-08-01: a unit sitting on multiple quarters' credit must have
    next_due_date advanced by however many WHOLE quarters that credit covers, with only
    the residual charged at that later date -- not have the estimate silently clamped
    to $0.00 forever at the immediately-next (already-covered) due date. With a $5,000
    credit against a $1,772.61 base instalment, 2 whole quarters (index 0 and 1, e.g.
    Mar/Jun) are fully prepaid; the 3rd (index 2, e.g. Sep) is where the $317.83
    residual is actually owed. Never negative, and never $0.00 unless the credit
    covers every computed period (see the dedicated exhausted-schedule test below)."""
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 161})
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "year": "2026", "total_levied": 3545.02, "total_paid": 10000.0, "net_balance": -5000.0,
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2026", "payment_schedule": None})

    settings_doc = {
        "levy_due_months": [3, 6, 9, 12], "levy_due_day_type": "custom",
        "levy_due_custom_dates": {"3": 31, "6": 1, "9": 1, "12": 1},
        "financial_year_start_month": 1,
    }

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.get_levy_rates", AsyncMock(return_value={"admin_annual": 34.09, "sinking_annual": 9.95})),
        patch("routers.finance.compute_mongo_quarter_statuses", MagicMock(return_value=[])),
        patch("routers.finance._get_general_settings", AsyncMock(return_value=settings_doc)),
        patch("routers.finance.date") as mock_date,
    ):
        mock_date.today.return_value = date(2026, 1, 1)
        mock_date.fromisoformat = date.fromisoformat
        result = await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year="2026",
        )

    # base_instalment = (34.09*161 + 9.95*161) / 4 = 1772.61; 2 whole quarters (3554.22
    # cents-rounded) fit inside the $5,000 credit, leaving 500000 - 354522 = 145478
    # cents of credit still to apply against the 3rd upcoming due date.
    assert result["next_due_date"] == "2026-09-01"
    assert result["next_payment_amount"] == 317.83
    assert result["next_payment_amount"] >= 0


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_clamped_to_zero_when_credit_exceeds_entire_schedule():
    """When credit exceeds EVERY period in the computed schedule window (not just
    some of them), there is no further-out date this function can compute (it only
    ever derives one year's worth of due dates) -- next_due_date must clamp to the
    last computed date rather than guessing beyond the window, and the amount must
    still never go negative."""
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 161})
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "year": "2026", "total_levied": 3545.02, "total_paid": 50000.0, "net_balance": -45000.0,
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2026", "payment_schedule": None})

    settings_doc = {
        "levy_due_months": [3, 6, 9, 12], "levy_due_day_type": "custom",
        "levy_due_custom_dates": {"3": 31, "6": 1, "9": 1, "12": 1},
        "financial_year_start_month": 1,
    }

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.get_levy_rates", AsyncMock(return_value={"admin_annual": 34.09, "sinking_annual": 9.95})),
        patch("routers.finance.compute_mongo_quarter_statuses", MagicMock(return_value=[])),
        patch("routers.finance._get_general_settings", AsyncMock(return_value=settings_doc)),
        patch("routers.finance.date") as mock_date,
    ):
        mock_date.today.return_value = date(2026, 1, 1)
        mock_date.fromisoformat = date.fromisoformat
        result = await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year="2026",
        )

    assert result["next_due_date"] == "2026-12-01"  # last computed due date, not a guess beyond it
    assert result["next_payment_amount"] >= 0


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_lot63_scenario_no_subcent_residue():
    """Regression for the exact live report (Lot 63 / UA063, 2026-08-01): net_balance
    of precisely one quarter's credit produced "$0.01 due 1 Sept" instead of "$0.00 due,
    real amount owed 1 Dec" -- a dollar-float (admin_annual+sinking_annual)/4 division
    carries thirds-of-a-cent that never exactly cancel a cents-rounded net_balance.
    Rates chosen so the annual total divides evenly by 4 (no residual cents at all),
    isolating the date-skip behaviour from the sub-cent-rounding behaviour covered by
    the dedicated rounding test below."""
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 100})
    # Annual = (40 + 20) * 100 = 6000.00 exactly / 4 = 1500.00 per quarter, no remainder.
    # One quarter already fully prepaid via credit -> net_balance = -1500.00 exactly.
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={
        "year": "2026", "total_levied": 3000.0, "total_paid": 4500.0, "net_balance": -1500.0,
    })
    mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2026", "payment_schedule": None})

    settings_doc = {
        "levy_due_months": [3, 6, 9, 12], "levy_due_day_type": "custom",
        "levy_due_custom_dates": {"3": 31, "6": 1, "9": 1, "12": 1},
        "financial_year_start_month": 1,
    }

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance.get_levy_rates", AsyncMock(return_value={"admin_annual": 40.0, "sinking_annual": 20.0})),
        patch("routers.finance.compute_mongo_quarter_statuses", MagicMock(return_value=[])),
        patch("routers.finance._get_general_settings", AsyncMock(return_value=settings_doc)),
        patch("routers.finance.date") as mock_date,
    ):
        mock_date.today.return_value = date(2026, 8, 1)
        mock_date.fromisoformat = date.fromisoformat
        result = await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year="2026",
        )

    assert result["next_due_date"] == "2026-12-01"  # skips Sept -- already fully prepaid
    assert result["next_payment_amount"] == 1500.00  # not $0.01, not $0.00 -- the real next quarter's amount


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_uses_resolved_year_without_docs():
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value={"entitlement": 100})
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)
    get_rates = AsyncMock(return_value={"admin_annual": 5.0, "sinking_annual": 3.0})

    with (
        patch("routers.finance.db", mock_db),
        patch("routers.finance._resolve_default_levy_year", AsyncMock(return_value="2026")),
        patch("routers.finance.get_levy_rates", get_rates),
    ):
        result = await _get_unit_dashboard_overview_mongo_fallback(
            building_id="13195", unit_number="TH087", year=None,
        )

    get_rates.assert_awaited_once_with("2026", "13195")
    assert result["financial_year"] == "2026"
    assert result["total_levied"] == 800.0


@pytest.mark.asyncio
async def test_unit_dashboard_overview_mongo_fallback_raises_404_when_unit_missing():
    from routers.finance import _get_unit_dashboard_overview_mongo_fallback
    from fastapi import HTTPException

    mock_db = MagicMock()
    mock_db.units.find_one = AsyncMock(return_value=None)
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
    mock_db.annual_levies.find_one = AsyncMock(return_value=None)

    with patch("routers.finance.db", mock_db):
        with pytest.raises(HTTPException) as exc_info:
            await _get_unit_dashboard_overview_mongo_fallback(
                building_id="13195", unit_number="NOPE", year="2026",
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_unit_dashboard_overview_serves_mongo_when_cutover_gate_not_promoted():
    """Same gate-bypass fix as test_building_overview_serves_mongo_when_cutover_gate_not_promoted,
    for the sibling route. Today's real default (finance_ledger domain_mode
    postgres_shadow) must resolve to Mongo, not read Postgres unconditionally."""
    from routers.finance import get_unit_dashboard_overview

    mongo_fallback_result = {"source": "mongodb_fallback", "unit_number": "TH087"}

    with (
        patch("routers.finance.resolve_canonical_unit_number", AsyncMock(return_value="TH087")),
        patch("routers.finance._unit_display_rules_safe", AsyncMock(return_value=[])),
        patch(
            "routers.finance.get_finance_route_runtime_state",
            AsyncMock(return_value={"source": "mongo", "run_shadow": True}),
        ),
        patch(
            "routers.finance._get_unit_dashboard_overview_mongo_fallback",
            AsyncMock(return_value=mongo_fallback_result),
        ) as mongo_fallback,
        patch("routers.finance._maybe_shadow_unit_dashboard_overview", AsyncMock()),
        patch(
            "routers.finance._financial_read_service.get_unit_levy_balance",
            AsyncMock(side_effect=AssertionError("Postgres must not be read when gate resolves to mongo")),
        ),
    ):
        result = await get_unit_dashboard_overview(
            unit_number="TH087",
            year="2026",
            current_user={"id": "owner-1", "role": "owner", "unit_number": "TH087"},
            building_id="13195",
        )

    assert result is mongo_fallback_result
    mongo_fallback.assert_awaited_once_with("13195", "TH087", "2026")
