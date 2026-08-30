# @featuretrace:dashboard-v2 — Regression tests for workflow list parity and streak tenant-context safety.
# Layer: test
# Data flow: Dashboard v2 cards → workflow_requests/analytics endpoints → PG-first + Mongo services (building-scoped).
# Related: backend/routers/workflow_requests.py
#          backend/services/streak_service.py
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest
import db_postgres.repos.config_repo  # noqa: F401

from request_context import get_ctx_building_id, set_ctx_building_id
from routers.workflow_requests import list_workflow_requests
from services.streak_service import on_time_quarters


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Rows:
    def __init__(self, items):
        self._items = items

    def mappings(self):
        return self

    def all(self):
        return self._items


@pytest.mark.asyncio
async def test_list_workflow_requests_pg_unbounded_when_limit_omitted():
    now = datetime.now(timezone.utc)
    rows = _Rows([
        {
            "id": "case-1",
            "title": "Case 1",
            "description": "Desc 1",
            "request_type": "maintenance",
            "priority": "normal",
            "status": "triaged",
            "submitted_by_user_id": "u1",
            "assigned_to": None,
            "created_at": now,
            "updated_at": now,
            "due_at": None,
            "unit_number": "A1",
            "sla_breached": False,
        },
        {
            "id": "case-2",
            "title": "Case 2",
            "description": "Desc 2",
            "request_type": "compliance",
            "priority": "high",
            "status": "in_progress",
            "submitted_by_user_id": "u2",
            "assigned_to": "u3",
            "created_at": now,
            "updated_at": now,
            "due_at": None,
            "unit_number": "A2",
            "sla_breached": True,
        },
    ])
    session = MagicMock()
    session.execute = AsyncMock(return_value=rows)

    with (
        # Toggle must be ON so the Postgres path is exercised (what this test asserts).
        # Patch on the module where it is defined — lazy import resolves there.
        patch("services.cutover_config_service.is_cutover_feature_enabled", AsyncMock(return_value=True)),
        patch("db_postgres.repos.config_repo.resolve_scheme_context", AsyncMock(return_value={"scheme_id": "scheme-1", "tenant_id": "tenant-1"})),
        patch("db_postgres.session.async_session_context", return_value=_AsyncSessionContext(session)),
        patch("db_postgres.session.set_tenant", AsyncMock()),
    ):
        data = await list_workflow_requests(
            status=None,
            request_type=None,
            limit=None,
            current_user={"id": "mgr", "role": "strata_manager"},
            building_id="13195",
        )

    assert len(data) == 2
    sql = str(session.execute.await_args.args[0])
    params = session.execute.await_args.args[1]
    assert "LIMIT :limit" not in sql
    assert "limit" not in params


@pytest.mark.asyncio
async def test_on_time_quarters_restores_previous_building_context():
    previous = "old-building"
    set_ctx_building_id(previous)

    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[{"year": "2026", "quarter": "1", "status": "paid", "paid_on_time": True}])
    mock_db = MagicMock()
    mock_db.unit_levy_ledger.find.return_value = cursor

    data = await on_time_quarters("A1", "UP-DEMO-001", db=mock_db)

    assert data["streak"] == 1
    assert get_ctx_building_id() == previous
