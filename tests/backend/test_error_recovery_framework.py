# @featuretrace:error-recovery-framework — Backend tests for structured API error envelopes.
# Layer: test
# Data flow: FastAPI exceptions -> utils.error_response.build_error_response -> JSON envelope (global).
# Related: backend/utils/error_response.py
#          backend/server.py
#          backend/utils/permissions.py
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from utils.auth import get_current_user
from utils.error_response import build_error_response, log_unhandled_exception
from utils.permissions import require_feature


class Payload(BaseModel):
    name: str


def _build_app() -> TestClient:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc):
        return build_error_response(request, status_code=exc.status_code, detail=exc.detail, headers=getattr(exc, "headers", None))

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(request, exc):
        return build_error_response(request, status_code=exc.status_code, detail=getattr(exc, "detail", None), headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        return build_error_response(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            message="Some required information is missing or invalid.",
            detail={"fields": exc.errors()},
            retryable=False,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        log_unhandled_exception(request, exc)
        return build_error_response(
            request,
            status_code=500,
            code="SERVER_ERROR",
            message="Something went wrong on our side.",
            retryable=True,
        )

    @app.get("/feature", dependencies=[Depends(require_feature("powerhouse_conversations"))])
    async def feature_route():
        return {"ok": True}

    @app.get("/auth-required")
    async def auth_required():
        raise HTTPException(status_code=401, detail="Not authenticated")

    @app.get("/forbidden")
    async def forbidden():
        raise HTTPException(status_code=403, detail="Access denied")

    @app.post("/validation")
    async def validation(payload: Payload):
        return payload

    @app.get("/scope-error")
    async def scope_error():
        raise HTTPException(status_code=403, detail={"code": "BUILDING_SCOPE_DENIED", "message": "You do not have access to this building.", "retryable": False})

    @app.get("/service-down")
    async def service_down():
        raise HTTPException(status_code=503, detail="database password leaked")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("Traceback (most recent call last): database password leaked")

    app.dependency_overrides[get_current_user] = lambda: {"id": "user-1", "role": "owner", "building_id": "13195"}
    return TestClient(app, raise_server_exceptions=False)


def _error(resp):
    return resp.json()["error"]


def test_feature_disabled_route_returns_structured_error():
    with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=False)):
        resp = _build_app().get("/feature", headers={"X-Request-ID": "req-feature"})
    assert resp.status_code == 403
    err = _error(resp)
    assert err["code"] == "FEATURE_DISABLED"
    assert err["status"] == 403
    assert err["request_id"] == "req-feature"
    assert err["retryable"] is False
    assert "powerhouse_conversations" in str(err.get("metadata"))


def test_unauthenticated_and_forbidden_are_structured():
    client = _build_app()
    auth_resp = client.get("/auth-required")
    forbidden_resp = client.get("/forbidden")
    assert auth_resp.status_code == 401
    assert _error(auth_resp)["code"] == "UNAUTHENTICATED"
    assert forbidden_resp.status_code == 403
    assert _error(forbidden_resp)["code"] == "FORBIDDEN"


def test_missing_route_returns_structured_404():
    resp = _build_app().get("/missing")
    assert resp.status_code == 404
    err = _error(resp)
    assert err["code"] == "NOT_FOUND"
    assert err["message"]


def test_validation_returns_structured_422():
    resp = _build_app().post("/validation", json={})
    assert resp.status_code == 422
    err = _error(resp)
    assert err["code"] == "VALIDATION_ERROR"
    assert err["retryable"] is False


def test_server_exception_handler_redacts_details_and_includes_request_id():
    resp = _build_app().get("/boom", headers={"X-Request-ID": "req-boom"})
    assert resp.status_code == 500
    err = _error(resp)
    assert err["code"] == "SERVER_ERROR"
    assert err["request_id"] == "req-boom"
    assert "Traceback" not in resp.text
    assert "database password" not in resp.text


def test_service_unavailable_http_exception_redacts_detail():
    resp = _build_app().get("/service-down")
    assert resp.status_code == 503
    err = _error(resp)
    assert err["code"] == "SERVICE_UNAVAILABLE"
    assert err["message"] == "The service is temporarily unavailable."
    assert "database password" not in resp.text
    assert "detail" not in resp.json()


def test_role_scoping_errors_are_distinguishable_without_leaking_data():
    resp = _build_app().get("/scope-error")
    assert resp.status_code == 403
    err = _error(resp)
    assert err["code"] == "BUILDING_SCOPE_DENIED"
    assert "password" not in resp.text.lower()
