"""
Tests for BUG-TRUST-001 Stage 1/2 V1 usage telemetry.

Covers backend/routers/trust_accounting.py's `_emit_v1_usage` and
`DeprecatedTrustV1Route`. No prior test in this repo exercised this code path
through actual ASGI routing — existing trust_accounting tests call route
functions directly, bypassing the `route_class=DeprecatedTrustV1Route` wrapper
entirely (see test_trust_accounting_phase1.py's own "Pure-unit tests only"
docstring). That gap is what let the underlying `extra={}` logging bug ship
unnoticed — the log line itself always "worked" (no exception), it just never
carried the fields it claimed to.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest


def _fake_request(building_id: str = "13195", actor_role: str = "strata_manager") -> StarletteRequest:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/trust/accounts",
        "query_string": b"",
        "headers": [(b"x-request-id", b"req-abc-123")],
        "client": ("127.0.0.1", 12345),
        "route": None,
    }
    request = StarletteRequest(scope)
    request.state.legacy_trust_v1_usage = {
        "event": "legacy_trust_v1_usage",
        "route": "/trust/accounts",
        "method": "GET",
        "building_id": building_id,
        "actor_hash": "deadbeef01234567",
        "actor_role": actor_role,
        "request_id": "req-abc-123",
        "timestamp": "2026-07-16T00:00:00+00:00",
    }
    return request


class TestEmitV1UsageDirect:
    """Direct unit coverage of _emit_v1_usage — the function whose extra={}
    fields never reached the actual log output before this fix."""

    @pytest.mark.asyncio
    async def test_writes_full_payload_to_telemetry_collection(self):
        from routers.trust_accounting import _emit_v1_usage

        mock_db = MagicMock()
        mock_db.trust_v1_usage_telemetry.insert_one = AsyncMock()

        with patch("routers.trust_accounting._get_db", return_value=mock_db):
            await _emit_v1_usage(_fake_request(), 200)

        mock_db.trust_v1_usage_telemetry.insert_one.assert_awaited_once()
        (doc,), _kwargs = mock_db.trust_v1_usage_telemetry.insert_one.call_args
        assert doc["event"] == "legacy_trust_v1_usage"
        assert doc["route"] == "/trust/accounts"
        assert doc["method"] == "GET"
        assert doc["building_id"] == "13195"
        assert doc["actor_role"] == "strata_manager"
        assert doc["actor_hash"] == "deadbeef01234567"
        assert doc["request_id"] == "req-abc-123"
        assert doc["response_status"] == 200
        assert isinstance(doc["created_at"], datetime)
        assert doc["created_at"].tzinfo is not None

        # Privacy contract from BUG-TRUST-001's Stage 1 design: never a raw
        # actor/user id, only the hash.
        assert "actor_id" not in doc
        assert "user_id" not in doc

    @pytest.mark.asyncio
    async def test_log_message_text_actually_contains_the_fields(self, caplog):
        """Regression test for the original bug: logging.basicConfig's format
        string only interpolates asctime/name/levelname/message, so passing
        fields via extra={} alone renders as the bare string
        "legacy_trust_v1_usage" with the payload silently dropped from the
        visible text (confirmed empirically against this app's actual
        logging.basicConfig config in server.py before this fix). The fields
        must now be embedded in the %-formatted message itself."""
        import logging as _logging

        from routers.trust_accounting import _emit_v1_usage

        mock_db = MagicMock()
        mock_db.trust_v1_usage_telemetry.insert_one = AsyncMock()

        with patch("routers.trust_accounting._get_db", return_value=mock_db):
            with caplog.at_level(_logging.INFO, logger="routers.trust_accounting"):
                await _emit_v1_usage(_fake_request(), 200)

        formatted = caplog.records[-1].getMessage()
        assert "route=/trust/accounts" in formatted
        assert "method=GET" in formatted
        assert "building_id=13195" in formatted
        assert "actor_role=strata_manager" in formatted
        assert "response_status=200" in formatted

    @pytest.mark.asyncio
    async def test_records_response_status_for_error_responses_too(self):
        from routers.trust_accounting import _emit_v1_usage

        mock_db = MagicMock()
        mock_db.trust_v1_usage_telemetry.insert_one = AsyncMock()

        with patch("routers.trust_accounting._get_db", return_value=mock_db):
            await _emit_v1_usage(_fake_request(), 404)

        doc = mock_db.trust_v1_usage_telemetry.insert_one.call_args[0][0]
        assert doc["response_status"] == 404

    @pytest.mark.asyncio
    async def test_telemetry_write_failure_never_raises(self):
        """Telemetry is best-effort by design — a Mongo hiccup must never break
        the (deprecated but still functioning) V1 response."""
        from routers.trust_accounting import _emit_v1_usage

        mock_db = MagicMock()
        mock_db.trust_v1_usage_telemetry.insert_one = AsyncMock(side_effect=RuntimeError("mongo down"))

        with patch("routers.trust_accounting._get_db", return_value=mock_db):
            await _emit_v1_usage(_fake_request(), 200)  # must not raise

    @pytest.mark.asyncio
    async def test_falls_back_to_minimal_payload_when_capture_dependency_did_not_run(self):
        """If _capture_v1_trust_usage never set request.state (e.g. an auth
        dependency short-circuited earlier), _emit_v1_usage must still degrade
        to a minimal payload rather than raising an AttributeError."""
        from routers.trust_accounting import _emit_v1_usage

        scope = {
            "type": "http", "method": "GET", "path": "/api/trust/balance",
            "query_string": b"", "headers": [], "client": ("127.0.0.1", 1), "route": None,
        }
        request = StarletteRequest(scope)  # no .state.legacy_trust_v1_usage set

        mock_db = MagicMock()
        mock_db.trust_v1_usage_telemetry.insert_one = AsyncMock()

        with patch("routers.trust_accounting._get_db", return_value=mock_db):
            await _emit_v1_usage(request, 200)

        doc = mock_db.trust_v1_usage_telemetry.insert_one.call_args[0][0]
        assert doc["event"] == "legacy_trust_v1_usage"
        assert doc["response_status"] == 200


class TestDeprecatedTrustV1RouteEndToEnd:
    """Exercises the actual APIRoute subclass through real ASGI routing —
    the only way DeprecatedTrustV1Route.get_route_handler()'s wrapper (and
    therefore _emit_v1_usage) is ever genuinely invoked."""

    def _build_app(self):
        from fastapi import APIRouter
        from routers.trust_accounting import DeprecatedTrustV1Route, _capture_v1_trust_usage
        from utils.auth import get_current_user, get_current_building

        router = APIRouter(
            route_class=DeprecatedTrustV1Route,
            dependencies=[Depends(_capture_v1_trust_usage)],
        )

        @router.get("/trust/accounts")
        async def ok_endpoint():
            return {"data": []}

        @router.get("/trust/broken")
        async def broken_endpoint():
            raise HTTPException(status_code=409, detail="conflict")

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-1", "role": "strata_manager"}
        app.dependency_overrides[get_current_building] = lambda: "13195"
        return app

    def test_success_response_gets_deprecation_headers_and_telemetry(self):
        app = self._build_app()
        mock_db = MagicMock()
        mock_db.trust_v1_usage_telemetry.insert_one = AsyncMock()

        with patch("routers.trust_accounting._get_db", return_value=mock_db):
            client = TestClient(app)
            resp = client.get("/api/trust/accounts")

        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") == "true"
        assert "successor-version" in resp.headers.get("Link", "")
        assert "Sunset" in resp.headers
        mock_db.trust_v1_usage_telemetry.insert_one.assert_awaited_once()
        doc = mock_db.trust_v1_usage_telemetry.insert_one.call_args[0][0]
        assert doc["response_status"] == 200
        assert doc["building_id"] == "13195"

    def test_http_exception_response_also_gets_deprecation_headers_and_telemetry(self):
        """Fourth re-audit correction in BUG-TRUST-001's history: handled
        HTTPExceptions must carry deprecation headers too, not just 2xx."""
        app = self._build_app()
        mock_db = MagicMock()
        mock_db.trust_v1_usage_telemetry.insert_one = AsyncMock()

        with patch("routers.trust_accounting._get_db", return_value=mock_db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/trust/broken")

        assert resp.status_code == 409
        assert resp.headers.get("Deprecation") == "true"
        mock_db.trust_v1_usage_telemetry.insert_one.assert_awaited_once()
        doc = mock_db.trust_v1_usage_telemetry.insert_one.call_args[0][0]
        assert doc["response_status"] == 409
