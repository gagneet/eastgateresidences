from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import utils.permissions as permissions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.ops_cases import router as ops_cases_router
from utils.auth import get_current_building, get_current_user


def _build_app(monkeypatch, current_user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(ops_cases_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_current_building] = lambda: "UP-13195"
    monkeypatch.setattr(permissions, "get_effective_feature_access", AsyncMock(return_value=True))
    return TestClient(app)


def test_create_ops_case_rejects_body_building_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "user-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )

    response = client.post(
        "/api/ops/cases",
        json={
            "building_id": "should-not-be-accepted",
            "title": "Water leak",
            "category": "maintenance",
        },
    )

    assert response.status_code == 422


def test_create_ops_case_uses_dependency_building_context(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "user-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured: dict = {}

    async def _create_case(**kwargs):
        captured.update(kwargs)
        return {
            "id": "case-1",
            "scheme_id": "scheme-1",
            "title": kwargs["payload"].title,
            "description": None,
            "category": kwargs["payload"].category,
            "priority": "normal",
            "risk_level": "low",
            "status": "new",
            "source_type": None,
            "source_id": None,
            "related_lot_ids": [],
            "related_owner_party_ids": [],
            "related_tenant_party_ids": [],
            "related_asset_ids": [],
            "assigned_to": None,
            "assigned_to_name": None,
            "due_at": None,
            "sla_policy_code": None,
            "approval_required": False,
            "approval_request_id": None,
            "approval_status": None,
            "visibility_scope": "building",
            "pii_classification": "internal",
            "created_by": "user-1",
            "created_by_name": "Manager",
            "updated_by": "user-1",
            "updated_by_name": "Manager",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "comment_count": 0,
            "link_count": 0,
        }

    monkeypatch.setattr("routers.ops_cases.ops_case_service.create_case", _create_case)

    response = client.post(
        "/api/ops/cases",
        json={
            "title": "Water leak",
            "description": "Water ingress from ceiling",
            "category": "maintenance",
        },
    )

    assert response.status_code == 201
    assert captured["building_id"] == "UP-13195"
    assert captured["payload"].title == "Water leak"


def test_assign_ops_case_requires_manager_role(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "owner-1", "email": "owner@example.com", "role": "owner", "full_name": "Owner Resident"},
    )

    response = client.post(
        "/api/ops/cases/case-1/assign",
        json={"assigned_to": "4c61a4d9-cfb8-4e21-b6ea-fa88057bd6aa"},
    )

    assert response.status_code == 403


def test_list_ops_cases_passes_filters(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "user-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured: dict = {}

    async def _list_cases(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("routers.ops_cases.ops_case_service.list_cases", _list_cases)

    response = client.get("/api/ops/cases?status=triaged&category=maintenance&limit=25&offset=5")

    assert response.status_code == 200
    assert captured["status_filter"] == "triaged"
    assert captured["category"] == "maintenance"
    assert captured["limit"] == 25
    assert captured["offset"] == 5


def test_audit_endpoint_requires_manager_role(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "tenant-1", "email": "tenant@example.com", "role": "tenant", "full_name": "Tenant"},
    )

    response = client.get("/api/ops/cases/case-1/audit")

    assert response.status_code == 403
