from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.outbox_admin import router as outbox_admin_router
from utils.auth import get_current_user
from workers.outbox_relay import DEAD_LETTER_PREFIX, _relay_batch


class _FakeResult:
    def __init__(self, rows=None, scalar_value=None, one_row=None):
        self._rows = rows or []
        self._scalar_value = scalar_value
        self._one_row = one_row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one_row

    def scalar_one(self):
        return self._scalar_value


class _FakeRelaySession:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []
        self.committed = False
        self.tenant_bypass_calls = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        # RLS bypass sentinel (migration 0081) -- SET LOCAL is transaction-scoped, so
        # _relay_batch() issues this on every call, before the SELECT below.
        if "SET LOCAL app.tenant_id" in sql:
            self.tenant_bypass_calls += 1
            return _FakeResult()
        if "FROM core.outbox" in sql and "FOR UPDATE SKIP LOCKED" in sql:
            return _FakeResult(rows=self.rows)
        if sql.lstrip().startswith("UPDATE core.outbox"):
            self.updates.append(params)
            return _FakeResult()
        raise AssertionError(f"Unexpected SQL: {sql}")

    async def commit(self):
        self.committed = True


class _FakeAdminSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "set_config('app.tenant_id'" in sql:
            return _FakeResult()
        if "SELECT COUNT(*)" in sql:
            return _FakeResult(scalar_value=len(self.rows))
        if "FROM core.outbox" in sql:
            return _FakeResult(rows=self.rows)
        raise AssertionError(f"Unexpected SQL: {sql}")


def _make_row(*, attempts=0, last_error=None, created_at=None):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        scheme_id=uuid4(),
        event_type="levy_credit_applied",
        aggregate_id=uuid4(),
        aggregate_type="journal_entry",
        payload={"mongo_building_id": "13195"},
        attempts=attempts,
        last_error=last_error,
        created_at=created_at or (datetime.now(tz=timezone.utc) - timedelta(days=1)),
        last_attempt_at=None,
        published_at=None,
    )


@pytest.mark.asyncio
async def test_relay_increments_attempts_on_failure():
    session = _FakeRelaySession([_make_row(attempts=0)])

    with patch("workers.outbox_relay._dispatch_event", new=AsyncMock(side_effect=RuntimeError("boom"))):
        processed = await _relay_batch(session)

    assert processed == 0
    assert session.committed is True
    assert session.updates[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_relay_stores_last_error():
    session = _FakeRelaySession([_make_row(attempts=0)])
    huge_error = "x" * 5000

    with patch("workers.outbox_relay._dispatch_event", new=AsyncMock(side_effect=RuntimeError(huge_error))):
        await _relay_batch(session)

    assert len(session.updates[0]["error"]) == 1024
    assert session.updates[0]["error"] == huge_error[:1024]


@pytest.mark.asyncio
async def test_relay_skips_dead_letter_rows():
    dead_letter_row = _make_row(
        attempts=5,
        last_error=f"{DEAD_LETTER_PREFIX} permanent failure",
    )
    session = _FakeRelaySession([dead_letter_row])
    dispatch = AsyncMock()

    with patch("workers.outbox_relay._dispatch_event", new=dispatch):
        processed = await _relay_batch(session)

    assert processed == 0
    assert dispatch.await_count == 0
    assert session.updates == []


@pytest.mark.asyncio
async def test_relay_succeeds_on_retry():
    retry_row = _make_row(
        attempts=1,
        last_error="temporary failure",
        created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=10),
    )
    session = _FakeRelaySession([retry_row])

    with patch("workers.outbox_relay._dispatch_event", new=AsyncMock()):
        processed = await _relay_batch(session)

    assert processed == 1
    assert session.updates[0]["id"] == retry_row.id
    assert session.updates[0]["now"] is not None
    # Regression guard (856ff86b): the RLS-bypass SET LOCAL must actually run every
    # call, not just be silently tolerated -- without it, core.outbox's forced RLS
    # makes the SELECT above match zero rows in production, forever.
    assert session.tenant_bypass_calls == 1


class _FakeHealthSession:
    def __init__(self, health_row):
        self.health_row = health_row

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "set_config('app.tenant_id'" in sql:
            return _FakeResult()
        if "COUNT(*) FILTER" in sql:
            return _FakeResult(one_row=self.health_row)
        raise AssertionError(f"Unexpected SQL: {sql}")


def _health_row(*, unpublished=0, dead_lettered=0, oldest_unpublished_at=None):
    return SimpleNamespace(
        unpublished=unpublished,
        dead_lettered=dead_lettered,
        oldest_unpublished_at=oldest_unpublished_at,
    )


def _health_client(health_row):
    app = FastAPI()
    app.include_router(outbox_admin_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"role": "super_admin", "effective_role": "super_admin"}

    @asynccontextmanager
    async def _fake_session_context():
        yield _FakeHealthSession(health_row)

    return app, _fake_session_context


def test_outbox_health_reports_clean_state():
    app, fake_ctx = _health_client(_health_row())
    with patch("routers.outbox_admin.async_session_context", fake_ctx):
        client = TestClient(app)
        response = client.get("/api/admin/outbox/health")

    assert response.status_code == 200
    body = response.json()
    assert body["unpublished"] == 0
    assert body["dead_lettered"] == 0
    assert body["stale"] is False
    assert body["oldest_unpublished_at"] is None


def test_outbox_health_flags_stale_backlog():
    """GAP-FIN-061 regression: this is the exact signal that was missing for 3+ months --
    an old, unpublished row must be surfaced as 'stale', not just silently counted."""
    old_row = _health_row(
        unpublished=15256,
        dead_lettered=15256,
        oldest_unpublished_at=datetime(2026, 5, 4, 12, 47, 24, tzinfo=timezone.utc),
    )
    app, fake_ctx = _health_client(old_row)
    with patch("routers.outbox_admin.async_session_context", fake_ctx):
        client = TestClient(app)
        response = client.get("/api/admin/outbox/health")

    assert response.status_code == 200
    body = response.json()
    assert body["unpublished"] == 15256
    assert body["dead_lettered"] == 15256
    assert body["stale"] is True
    assert body["oldest_unpublished_age_minutes"] > body["stale_threshold_minutes"]


def test_dead_letter_endpoint_returns_failed_rows():
    app = FastAPI()
    app.include_router(outbox_admin_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"role": "super_admin", "effective_role": "super_admin"}
    rows = [
        _make_row(
            attempts=5,
            last_error=f"{DEAD_LETTER_PREFIX} failed permanently",
            created_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
        )
    ]

    @asynccontextmanager
    async def _fake_session_context():
        yield _FakeAdminSession(rows)

    with patch("routers.outbox_admin.async_session_context", _fake_session_context):
        client = TestClient(app)
        response = client.get("/api/admin/outbox/dead-letter?page=1&page_size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["attempts"] == 5
    assert payload["items"][0]["last_error"].startswith(DEAD_LETTER_PREFIX)
