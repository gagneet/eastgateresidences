"""API contract tests for request catalogue registration and auth boundary."""

import os
import sys
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

from routers.request_catalogue import router  # noqa: E402
from utils.auth import get_current_building, get_current_user  # noqa: E402


def _client(user=None, building_id="TEST-REQUEST-A"):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app)


def test_request_catalogue_requires_authentication():
    response = _client().get("/api/request-catalogue")
    assert response.status_code in {401, 403}


def test_request_catalogue_returns_public_stable_contract():
    user = {
        "id": "owner-a",
        "role": "owner",
        "building_id": "TEST-REQUEST-A",
        "unit_number": "101",
        "is_approved": True,
    }
    with patch(
        "services.request_catalogue_service.get_effective_feature_access",
        AsyncMock(return_value=True),
    ):
        response = _client(user).get("/api/request-catalogue")

    assert response.status_code == 200
    item = next(entry for entry in response.json() if entry["key"] == "pet")
    assert item["version"] == 1
    assert "allowed_roles" not in item
    assert "required_feature" not in item
    assert "stage_permissions" not in item


def test_request_catalogue_openapi_documents_route_and_errors():
    schema = _client({"id": "owner-a", "role": "owner", "building_id": "TEST-REQUEST-A"}).get("/openapi.json").json()
    path = schema["paths"]["/api/request-catalogue"]["get"]
    assert path["operationId"]
    for status in ["401", "403", "404", "409", "422"]:
        assert status in path["responses"]
