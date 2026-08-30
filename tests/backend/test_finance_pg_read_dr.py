"""Unit tests for services/finance_pg_read_dr.py.

Pure async logic (stdlib only), so these run without a DB or the backend venv's heavy deps.
The one rule under test: fall back to Mongo on PG FAILURE, never on PG EMPTY.

TestFreshnessGate below imports fastapi.HTTPException (a real backend dependency, not stdlib) --
that's fine here since it patches get_dr_snapshot rather than touching the real Mongo/PG database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.finance_pg_read_dr import (
    SERVED_MONGO,
    SERVED_MONGO_DR_FALLBACK,
    SERVED_POSTGRES,
    read_pg_first_with_mongo_dr,
)

RK = "finance.unit_dashboard_overview"
BID = "13195"


def _thunk(value):
    async def _f():
        return value
    return _f


def _raising_thunk(exc):
    async def _f():
        raise exc
    return _f


def _tracking_thunk(value, calls, name):
    async def _f():
        calls.append(name)
        return value
    return _f


class TestPgFirstWithMongoDr:
    @pytest.mark.asyncio
    async def test_postgres_payload_is_served_mongo_not_called(self):
        calls: list[str] = []
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_tracking_thunk({"arrears_cents": 500}, calls, "pg"),
            mongo_read=_tracking_thunk({"arrears_cents": 999}, calls, "mongo"),
        )
        assert payload == {"arrears_cents": 500}
        assert source == SERVED_POSTGRES
        assert calls == ["pg"]  # Mongo never touched

    @pytest.mark.asyncio
    async def test_empty_pg_payload_is_served_NOT_a_fallback(self):
        # The load-bearing rule: an empty/zero PG payload is a VALID answer, not a fallback trigger.
        calls: list[str] = []
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_tracking_thunk({}, calls, "pg"),
            mongo_read=_tracking_thunk({"arrears_cents": 999}, calls, "mongo"),
        )
        assert payload == {}
        assert source == SERVED_POSTGRES
        assert "mongo" not in calls

    @pytest.mark.asyncio
    async def test_zeroed_payload_is_served_NOT_a_fallback(self):
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_thunk({"arrears_cents": 0, "units": []}),
            mongo_read=_thunk({"arrears_cents": 999}),
        )
        assert payload == {"arrears_cents": 0, "units": []}
        assert source == SERVED_POSTGRES

    @pytest.mark.asyncio
    async def test_pg_exception_falls_back_to_mongo_and_fires_hook(self):
        fired: list[tuple] = []
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_raising_thunk(RuntimeError("connection refused")),
            mongo_read=_thunk({"arrears_cents": 999}),
            on_dr_fallback=lambda rk, bid, exc: fired.append((rk, bid, type(exc).__name__)),
        )
        assert payload == {"arrears_cents": 999}
        assert source == SERVED_MONGO_DR_FALLBACK
        assert fired == [(RK, BID, "RuntimeError")]

    @pytest.mark.asyncio
    async def test_pg_none_is_treated_as_unavailable_and_falls_back(self):
        # None = "PG builder produced no payload at all" (distinct from a zeroed payload) -> DR.
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_thunk(None),
            mongo_read=_thunk({"arrears_cents": 999}),
        )
        assert payload == {"arrears_cents": 999}
        assert source == SERVED_MONGO_DR_FALLBACK

    @pytest.mark.asyncio
    async def test_mongo_source_serves_mongo_pg_never_called(self):
        calls: list[str] = []
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="mongo",
            pg_read=_tracking_thunk({"arrears_cents": 1}, calls, "pg"),
            mongo_read=_tracking_thunk({"arrears_cents": 999}, calls, "mongo"),
        )
        assert payload == {"arrears_cents": 999}
        assert source == SERVED_MONGO
        assert calls == ["mongo"]  # PG never attempted

    @pytest.mark.asyncio
    async def test_timeout_error_triggers_dr_fallback(self):
        # TimeoutError / asyncio.TimeoutError are Exception subclasses — a real DR event, so a PG
        # timeout must fall back to Mongo (this is why `except Exception` is correct, not too narrow).
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_raising_thunk(TimeoutError("pg timeout")),
            mongo_read=_thunk({"arrears_cents": 999}),
        )
        assert payload == {"arrears_cents": 999}
        assert source == SERVED_MONGO_DR_FALLBACK

    @pytest.mark.asyncio
    async def test_cancelled_error_PROPAGATES_never_falls_back(self):
        # asyncio.CancelledError is a BaseException (not Exception). It MUST propagate so a cancelled
        # request / server shutdown actually cancels — swallowing it to run a Mongo fallback would
        # break cooperative cancellation. This guards against "widening" the except to BaseException.
        import asyncio
        with pytest.raises(asyncio.CancelledError):
            await read_pg_first_with_mongo_dr(
                route_key=RK, building_id=BID, source="postgres",
                pg_read=_raising_thunk(asyncio.CancelledError()),
                mongo_read=_thunk({"arrears_cents": 999}),
            )

    @pytest.mark.asyncio
    async def test_raising_hook_does_not_break_the_fallback(self):
        def _bad_hook(rk, bid, exc):
            raise ValueError("telemetry blew up")
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_raising_thunk(RuntimeError("pg down")),
            mongo_read=_thunk({"ok": True}),
            on_dr_fallback=_bad_hook,
        )
        assert payload == {"ok": True}
        assert source == SERVED_MONGO_DR_FALLBACK


class _FakeHTTPException(Exception):
    """Stand-in for FastAPI's HTTPException — a deliberate HTTP response, not a PG failure."""
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class TestReraise:
    """reraise= makes deliberate outcomes (auth 4xx) propagate instead of masking behind Mongo."""

    @pytest.mark.asyncio
    async def test_reraise_type_propagates_and_mongo_not_called(self):
        calls: list[str] = []
        with pytest.raises(_FakeHTTPException) as exc:
            await read_pg_first_with_mongo_dr(
                route_key=RK, building_id=BID, source="postgres",
                pg_read=_raising_thunk(_FakeHTTPException(403)),
                mongo_read=_tracking_thunk({"m": 1}, calls, "mongo"),
                reraise=(_FakeHTTPException,),
            )
        assert exc.value.status_code == 403
        assert calls == []  # DR fallback must NOT run — the 403 was deliberate

    @pytest.mark.asyncio
    async def test_non_reraise_exception_still_falls_back(self):
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_raising_thunk(RuntimeError("pg down")),
            mongo_read=_thunk({"m": 1}),
            reraise=(_FakeHTTPException,),
        )
        assert payload == {"m": 1}
        assert source == SERVED_MONGO_DR_FALLBACK

    @pytest.mark.asyncio
    async def test_default_reraise_empty_tuple_falls_back_on_everything(self):
        # Back-compat: with no reraise, even an HTTPException-like error is a DR event.
        payload, source = await read_pg_first_with_mongo_dr(
            route_key=RK, building_id=BID, source="postgres",
            pg_read=_raising_thunk(_FakeHTTPException(500)),
            mongo_read=_thunk({"m": 2}),
        )
        assert payload == {"m": 2}
        assert source == SERVED_MONGO_DR_FALLBACK


def _snapshot(*, status="ok", age_minutes=5):
    return {
        "building_id": BID,
        "route_key": RK,
        "reconciliation_status": status,
        "completed_at": datetime.now(tz=timezone.utc) - timedelta(minutes=age_minutes),
    }


class TestFreshnessGate:
    """require_fresh_snapshot=True: a DR fallback must never silently serve indeterminately-stale
    data. Added 2026-08-11 for the right-sized DR design (dr_mongo_snapshot.py writes the
    dr_snapshot_meta document this gate checks)."""

    @pytest.mark.asyncio
    async def test_default_false_falls_back_without_checking_any_snapshot(self):
        """Backward compatibility: existing callers that don't pass require_fresh_snapshot must
        see unchanged behaviour -- no snapshot lookup at all."""
        with patch("services.finance_pg_read_dr.get_dr_snapshot", new=AsyncMock()) as get_snap:
            payload, source = await read_pg_first_with_mongo_dr(
                route_key=RK, building_id=BID, source="postgres",
                pg_read=_raising_thunk(RuntimeError("pg down")),
                mongo_read=_thunk({"m": 1}),
            )
        assert payload == {"m": 1}
        assert source == SERVED_MONGO_DR_FALLBACK
        get_snap.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fresh_reconciled_snapshot_allows_dr_fallback(self):
        with patch(
            "services.finance_pg_read_dr.get_dr_snapshot",
            new=AsyncMock(return_value=_snapshot(status="ok", age_minutes=5)),
        ):
            payload, source = await read_pg_first_with_mongo_dr(
                route_key=RK, building_id=BID, source="postgres",
                pg_read=_raising_thunk(RuntimeError("pg down")),
                mongo_read=_thunk({"m": 1}),
                require_fresh_snapshot=True,
            )
        assert payload == {"m": 1}
        assert source == SERVED_MONGO_DR_FALLBACK

    @pytest.mark.asyncio
    async def test_missing_snapshot_returns_503_mongo_never_called(self):
        from fastapi import HTTPException

        calls: list[str] = []
        with patch("services.finance_pg_read_dr.get_dr_snapshot", new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await read_pg_first_with_mongo_dr(
                    route_key=RK, building_id=BID, source="postgres",
                    pg_read=_raising_thunk(RuntimeError("pg down")),
                    mongo_read=_tracking_thunk({"m": 1}, calls, "mongo"),
                    require_fresh_snapshot=True,
                )
        assert exc_info.value.status_code == 503
        assert calls == []

    @pytest.mark.asyncio
    async def test_unreconciled_snapshot_returns_503(self):
        from fastapi import HTTPException

        with patch(
            "services.finance_pg_read_dr.get_dr_snapshot",
            new=AsyncMock(return_value=_snapshot(status="control_total_mismatch", age_minutes=5)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await read_pg_first_with_mongo_dr(
                    route_key=RK, building_id=BID, source="postgres",
                    pg_read=_raising_thunk(RuntimeError("pg down")),
                    mongo_read=_thunk({"m": 1}),
                    require_fresh_snapshot=True,
                )
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_stale_snapshot_returns_503(self):
        from fastapi import HTTPException

        with patch(
            "services.finance_pg_read_dr.get_dr_snapshot",
            new=AsyncMock(return_value=_snapshot(status="ok", age_minutes=999)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await read_pg_first_with_mongo_dr(
                    route_key=RK, building_id=BID, source="postgres",
                    pg_read=_raising_thunk(RuntimeError("pg down")),
                    mongo_read=_thunk({"m": 1}),
                    require_fresh_snapshot=True,
                    max_snapshot_age_minutes=30,
                )
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_snapshot_not_checked_when_pg_succeeds(self):
        """The freshness gate only matters on the DR path -- a healthy PG read must never even
        look at the snapshot."""
        with patch("services.finance_pg_read_dr.get_dr_snapshot", new=AsyncMock()) as get_snap:
            payload, source = await read_pg_first_with_mongo_dr(
                route_key=RK, building_id=BID, source="postgres",
                pg_read=_thunk({"ok": True}),
                mongo_read=_thunk({"m": 1}),
                require_fresh_snapshot=True,
            )
        assert payload == {"ok": True}
        assert source == SERVED_POSTGRES
        get_snap.assert_not_awaited()
