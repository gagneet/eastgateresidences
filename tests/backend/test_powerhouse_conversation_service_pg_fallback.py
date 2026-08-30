from __future__ import annotations

# @featuretrace:powerhouse-foundation — PG-first read fallback behavior tests for powerhouse service.
# Layer: test
# Data flow: powerhouse service PG read attempts → fallback decisions → Mongo continuity (building-scoped).
# Related: backend/services/powerhouse_conversation_service.py
#          docs/architecture/conversation-engine.md

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi import HTTPException

from models.cutover_status import CutoverMode, DataSource
from services import powerhouse_conversation_service as svc


def _pg_read_status():
    return MagicMock(mode=CutoverMode.postgres_read, read_source=DataSource.postgres)


def _mongo_cursor(items: list[dict]):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=items)
    return cursor


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    def __init__(self, *, row=None, exc: Exception | None = None):
        self._row = row
        self._exc = exc

    async def execute(self, *_args, **_kwargs):
        if self._exc is not None:
            raise self._exc
        return _FakeResult(self._row)


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_list_threads_prefers_postgres_when_rows_available(monkeypatch):
    pg_items = [{"id": "pg-thread-1", "subject": "PG thread"}]
    monkeypatch.setattr(svc, "_list_threads_from_pg", AsyncMock(return_value=pg_items))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))

    mongo_find = MagicMock(side_effect=AssertionError("Mongo should not be called when PG has data"))
    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find", mongo_find)

    result = await svc.list_threads(
        building_id="13195",
        current_user={"id": "mgr-1", "role": "strata_manager"},
        status=None,
        source_channel=None,
        limit=25,
        offset=0,
    )

    assert result == {"items": pg_items, "limit": 25, "offset": 0}


@pytest.mark.asyncio
async def test_list_threads_keeps_empty_postgres_result(monkeypatch):
    monkeypatch.setattr(svc, "_list_threads_from_pg", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))

    mongo_find = MagicMock(side_effect=AssertionError("Mongo fallback must not run for an empty PG result"))
    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find", mongo_find)

    result = await svc.list_threads(
        building_id="13195",
        current_user={"id": "mgr-1", "role": "strata_manager"},
        status=None,
        source_channel=None,
        limit=25,
        offset=0,
    )

    assert result == {"items": [], "limit": 25, "offset": 0}


@pytest.mark.asyncio
async def test_list_threads_falls_back_to_mongo_when_pg_unavailable(monkeypatch):
    monkeypatch.setattr(svc, "_list_threads_from_pg", AsyncMock(return_value=None))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))
    monkeypatch.setattr(svc, "_record_mongo_fallback_event", AsyncMock())
    mongo_items = [{"id": "mongo-thread-1", "subject": "Mongo thread"}]
    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find", MagicMock(return_value=_mongo_cursor(mongo_items)))

    result = await svc.list_threads(
        building_id="13195",
        current_user={"id": "mgr-1", "role": "strata_manager"},
        status=None,
        source_channel=None,
        limit=25,
        offset=0,
    )

    assert result == {"items": mongo_items, "limit": 25, "offset": 0}
    svc._record_mongo_fallback_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_thread_detail_uses_pg_when_available(monkeypatch):
    pg_detail = {"thread": {"id": "pg-thread-1"}, "messages": []}
    monkeypatch.setattr(svc, "_get_thread_detail_from_pg", AsyncMock(return_value=pg_detail))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))

    mongo_thread_find = AsyncMock(side_effect=AssertionError("Mongo should not be called when PG has thread detail"))
    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find_one", mongo_thread_find)

    result = await svc.get_thread_detail(
        building_id="13195",
        thread_id="pg-thread-1",
        current_user={"id": "mgr-1", "role": "strata_manager"},
    )

    assert result == pg_detail


@pytest.mark.asyncio
async def test_get_thread_detail_falls_back_to_mongo_when_pg_missing(monkeypatch):
    monkeypatch.setattr(svc, "_get_thread_detail_from_pg", AsyncMock(return_value=None))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))
    monkeypatch.setattr(svc, "_record_mongo_fallback_event", AsyncMock())

    mongo_thread = {
        "id": "mongo-thread-1",
        "building_id": "13195",
        "participant_ids": ["u-1"],
        "watcher_ids": [],
        "created_by": "u-1",
        "visibility": "participants_only",
    }
    mongo_messages = [{"id": "mongo-msg-1", "thread_id": "mongo-thread-1", "visibility": "participants_only"}]

    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find_one", AsyncMock(return_value=mongo_thread))
    monkeypatch.setattr(svc.db.powerhouse_conversation_messages, "find", MagicMock(return_value=_mongo_cursor(mongo_messages)))

    result = await svc.get_thread_detail(
        building_id="13195",
        thread_id="mongo-thread-1",
        current_user={"id": "u-1", "role": "owner"},
    )

    assert result["thread"]["id"] == "mongo-thread-1"
    assert result["messages"] == mongo_messages


@pytest.mark.asyncio
async def test_list_inboxes_prefers_postgres_when_rows_available(monkeypatch):
    pg_inboxes = [{"id": "inbox-1", "address": "hello@example.com"}]
    monkeypatch.setattr(svc, "_list_inboxes_from_pg", AsyncMock(return_value=pg_inboxes))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))

    mongo_find = MagicMock(side_effect=AssertionError("Mongo should not be called when PG has inbox rows"))
    monkeypatch.setattr(svc.db.powerhouse_inboxes, "find", mongo_find)

    result = await svc.list_inboxes(building_id="13195", current_user={"id": "mgr-1", "role": "strata_manager"})

    assert result == pg_inboxes


@pytest.mark.asyncio
async def test_list_workflow_templates_keeps_empty_postgres_result(monkeypatch):
    monkeypatch.setattr(svc, "_list_workflow_templates_from_pg", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))
    mongo_find = MagicMock(side_effect=AssertionError("Mongo fallback must not run for an empty PG template result"))
    monkeypatch.setattr(svc.db.powerhouse_workflow_templates, "find", mongo_find)

    result = await svc.list_workflow_templates(
        building_id="13195",
        current_user={"id": "owner-1", "role": "owner"},
    )

    assert result == []


@pytest.mark.asyncio
async def test_list_workflow_templates_uses_placeholder_only_when_pg_unavailable(monkeypatch):
    monkeypatch.setattr(svc, "_list_workflow_templates_from_pg", AsyncMock(return_value=None))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))
    monkeypatch.setattr(svc, "_record_mongo_fallback_event", AsyncMock())
    monkeypatch.setattr(svc.db.powerhouse_workflow_templates, "find", MagicMock(return_value=_mongo_cursor([])))

    result = await svc.list_workflow_templates(
        building_id="13195",
        current_user={"id": "owner-1", "role": "owner"},
    )

    assert len(result) == 3
    assert all(item.get("is_placeholder") for item in result)


class _FakePgError(Exception):
    """Mimic an asyncpg driver error carrying a SQLSTATE code."""

    def __init__(self, sqlstate: str, msg: str = "pg error"):
        super().__init__(msg)
        self.sqlstate = sqlstate


@pytest.mark.asyncio
async def test_list_threads_programming_error_surfaces_500_without_fallback(monkeypatch):
    # A PG programming defect (e.g. undefined_column, SQLSTATE 42703 — the Finding 3
    # class of bug) must NOT be mistaken for "PostgreSQL unavailable": it surfaces as
    # a 500 and Mongo continuity is never activated.
    monkeypatch.setattr(svc, "_list_threads_from_pg", AsyncMock(side_effect=_FakePgError("42703")))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))

    fallback_spy = AsyncMock()
    monkeypatch.setattr(svc, "_record_mongo_fallback_event", fallback_spy)
    mongo_find = MagicMock(side_effect=AssertionError("Mongo fallback must not run for a PG programming error"))
    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find", mongo_find)

    with pytest.raises(HTTPException) as excinfo:
        await svc.list_threads(
            building_id="13195",
            current_user={"id": "mgr-1", "role": "strata_manager"},
            status=None,
            source_channel=None,
            limit=25,
            offset=0,
        )

    assert excinfo.value.status_code == 500
    fallback_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_threads_connection_error_still_falls_back(monkeypatch):
    # A genuine infrastructure failure (SQLSTATE class 08 — connection_exception)
    # is still allowed to activate controlled Mongo continuity.
    monkeypatch.setattr(svc, "_list_threads_from_pg", AsyncMock(side_effect=_FakePgError("08006")))
    monkeypatch.setattr("services.cutover_status_service.get_or_default_cutover_status", AsyncMock(return_value=_pg_read_status()))
    monkeypatch.setattr(svc, "_record_mongo_fallback_event", AsyncMock())
    mongo_items = [{"id": "mongo-thread-1", "subject": "Mongo thread"}]
    monkeypatch.setattr(svc.db.powerhouse_conversation_threads, "find", MagicMock(return_value=_mongo_cursor(mongo_items)))

    result = await svc.list_threads(
        building_id="13195",
        current_user={"id": "mgr-1", "role": "strata_manager"},
        status=None,
        source_channel=None,
        limit=25,
        offset=0,
    )

    assert result == {"items": mongo_items, "limit": 25, "offset": 0}
    svc._record_mongo_fallback_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_pg_mapped_entity_id_returns_mapped_pg_id(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=("tenant-1", "scheme-1")))
    monkeypatch.setattr("db_postgres.session.set_tenant", AsyncMock())
    monkeypatch.setattr(
        "db_postgres.session.async_session_context",
        lambda: _FakeSessionContext(_FakeSession(row=SimpleNamespace(pg_id="pg-thread-42"))),
    )

    resolved = await svc._resolve_pg_mapped_entity_id(
        building_id="13195",
        entity_type="conversation_thread",
        candidate_id="legacy-thread-1",
    )

    assert resolved == "pg-thread-42"


@pytest.mark.asyncio
async def test_resolve_pg_mapped_entity_id_returns_raw_id_on_pg_error(monkeypatch):
    monkeypatch.setattr(svc, "_resolve_pg_context", AsyncMock(return_value=("tenant-1", "scheme-1")))
    monkeypatch.setattr("db_postgres.session.set_tenant", AsyncMock())
    monkeypatch.setattr(
        "db_postgres.session.async_session_context",
        lambda: _FakeSessionContext(_FakeSession(exc=RuntimeError("pg down"))),
    )

    resolved = await svc._resolve_pg_mapped_entity_id(
        building_id="13195",
        entity_type="conversation_thread",
        candidate_id="legacy-thread-2",
    )

    assert resolved == "legacy-thread-2"
