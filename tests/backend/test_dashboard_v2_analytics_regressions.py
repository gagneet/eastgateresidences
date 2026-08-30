# @featuretrace:dashboard-v2 — Regression coverage for PostgreSQL/Mongo analytics parity used by dashboard v2.
# Layer: test
# Data flow: Owner/Management dashboard → analytics endpoints → PG-first counts/streak + dashboard signals (building-scoped).
# Related: backend/routers/analytics.py
#          backend/services/streak_service.py
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import db_postgres.repos.config_repo  # noqa: F401

from routers.analytics import (
    get_diff_since,
    get_levy_allocation_breakdown,
    get_my_streak,
)


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _AsyncCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, _limit):
        return self._rows


@pytest.mark.asyncio
async def test_diff_since_mongo_fallback_counts_compliance_items():
    collections = {}
    for name, count in {
        "workflow_requests": 1,
        "levy_payments": 3,
        "compliance_items": 2,
        "announcements": 5,
    }.items():
        collection = MagicMock()
        collection.count_documents = AsyncMock(return_value=count)
        collections[name] = collection

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = collections.__getitem__
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=4)

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(side_effect=RuntimeError("pg unavailable"))),
        patch("routers.analytics.db", mock_db),
    ):
        data = await get_diff_since(
            since="2026-05-20T00:00:00Z",
            current_user={"id": "user-1"},
            building_id="13195",
        )

    assert data["source"] == "mongodb_fallback"
    assert data["counts"]["compliance_updates"] == 2
    assert any(call.args[0] == "compliance_items" for call in mock_db.__getitem__.call_args_list)


@pytest.mark.asyncio
async def test_diff_since_arrears_uses_selected_financial_year():
    collections = {}
    for name in ("workflow_requests", "levy_payments", "compliance_items", "announcements"):
        collection = MagicMock()
        collection.count_documents = AsyncMock(return_value=0)
        collections[name] = collection

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = collections.__getitem__
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.meetings.find_one = AsyncMock(return_value=None)
    mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2027"})

    # get_diff_since's arrears snapshot now routes through the canonical,
    # grace-aware get_building_arrears_summary() (GAP-FIN-040) rather than
    # aggregating unit_levy_ledger directly — mock at that boundary (a
    # separate `from database import db` inside utils/finance_helpers.py, so
    # patching routers.analytics.db alone leaves the real helper untouched and
    # this test previously fell through to whatever live-ish Mongo data
    # existed for 13195/2026).
    arrears_mock = AsyncMock(return_value={"true_arrears_amount": 1234.56, "units_in_arrears": 3})

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(side_effect=RuntimeError("pg unavailable"))),
        patch("routers.analytics.db", mock_db),
        patch("utils.finance_helpers.get_building_arrears_summary", arrears_mock),
    ):
        data = await get_diff_since(
            since="2026-05-20T00:00:00Z",
            year="2026",
            current_user={"id": "user-1"},
            building_id="13195",
        )

    arrears_mock.assert_awaited_once_with("13195", "2026")
    assert data["counts"]["total_arrears"] == pytest.approx(1234.56)
    assert data["counts"]["units_in_arrears"] == 3
    assert data["counts"]["arrears_year"] == "2026"
    mock_db.annual_levies.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_levy_allocation_breakdown_model_normalizes_admin_weights():
    levy_doc = {
        "year": "2026",
        "proposed_admin_expenses": 7200,
        "proposed_sinking_expenses": 2800,
    }

    # Disable Postgres path so the model/MongoDB branch executes.
    # is_cutover_feature_enabled is lazy-imported inside analytics.py try blocks,
    # so patch at the definition site (services.cutover_config_service).
    with patch("routers.analytics.db") as mock_db, \
         patch("services.cutover_config_service.is_cutover_feature_enabled",
               AsyncMock(return_value=False)):
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)

        data = await get_levy_allocation_breakdown(
            year="2026",
            current_user={"id": "user-1"},
            building_id="13195",
        )

    assert data["source"] == "model"
    assert data["total_annual"] == pytest.approx(10000.0, abs=0.01)
    assert sum(item["amount"] for item in data["categories"]) == pytest.approx(10000.0, abs=0.02)
    assert sum(item["pct"] for item in data["categories"]) == pytest.approx(100.0, abs=0.2)


@pytest.mark.asyncio
async def test_my_streak_uses_pg_ledger_history_and_keeps_pg_entitlement():
    lot_result = MagicMock()
    lot_result.mappings.return_value.first.return_value = {
        "lot_id": "lot-1",
        "entitlement_units": 25,
        "total_entitlement": 100,
    }

    quarter_result = MagicMock()
    quarter_result.fetchall.return_value = [
        SimpleNamespace(financial_year="2026", quarter_no=2, due_date=date(2026, 6, 1), levied_cents=10000, paid_cents=10000),
        SimpleNamespace(financial_year="2026", quarter_no=1, due_date=date(2026, 3, 1), levied_cents=10000, paid_cents=0),
    ]

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[lot_result, quarter_result])

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value={"scheme_id": "scheme-1", "tenant_id": "tenant-1"})),
        patch("db_postgres.session.async_session_context", return_value=_AsyncSessionContext(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
    ):
        data = await get_my_streak(
            current_user={"id": "user-1", "unit_number": "101"},
            building_id="13195",
        )

    assert data["source"] == "postgres_ledger"
    assert data["entitlement_pct"] == pytest.approx(25.0, abs=0.0001)
    assert data["streak"] == 1
    assert data["total_quarters"] == 2
    assert data["recent_quarters"][0]["quarter"] == "2"
    assert data["recent_quarters"][0]["status"] == "paid"
    assert data["recent_quarters"][1]["quarter"] == "1"
    assert data["recent_quarters"][1]["status"] == "overdue"


@pytest.mark.asyncio
async def test_diff_since_pg_receipts_query_filters_test_data():
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _ScalarResult(0),  # ops.cases
        _ScalarResult(2),  # finance.receipts
        _ScalarResult(0),  # compliance.compliance_items
        _ScalarResult(0),  # communications.announcements
        _ScalarResult(0),  # ops.task_sla_events
    ])

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value={"scheme_id": "scheme-1", "tenant_id": "tenant-1"})),
        patch("db_postgres.session.async_session_context", return_value=_AsyncSessionContext(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
    ):
        data = await get_diff_since(
            since="2026-05-20T00:00:00Z",
            current_user={"id": "user-1"},
            building_id="13195",
        )

    assert data["source"] == "postgresql"
    assert data["counts"]["payments_received"] == 2
    receipt_sql = str(session.execute.await_args_list[1].args[0])
    assert "finance.receipts" in receipt_sql
    assert "COALESCE(is_test_data, FALSE) = FALSE" in receipt_sql


@pytest.mark.asyncio
async def test_diff_since_pg_source_includes_compliance_only_changes():
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _ScalarResult(0),  # ops.cases
        _ScalarResult(0),  # finance.receipts
        _ScalarResult(3),  # compliance.compliance_items
        _ScalarResult(0),  # communications.announcements
        _ScalarResult(0),  # ops.task_sla_events
    ])

    with (
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value={"scheme_id": "scheme-1", "tenant_id": "tenant-1"})),
        patch("db_postgres.session.async_session_context", return_value=_AsyncSessionContext(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
    ):
        data = await get_diff_since(
            since="2026-05-20T00:00:00Z",
            current_user={"id": "user-1"},
            building_id="13195",
        )

    assert data["source"] == "postgresql"
    assert data["counts"]["compliance_updates"] == 3
