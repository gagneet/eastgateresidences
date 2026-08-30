from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import utils.permissions as permissions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.ops_repairs import router as ops_repairs_router
from utils.auth import get_current_building, get_current_user


def _build_app(monkeypatch, current_user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(ops_repairs_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_current_building] = lambda: "UP-13195"
    monkeypatch.setattr(permissions, "get_effective_feature_access", AsyncMock(return_value=True))
    return TestClient(app)


def test_list_service_providers_passes_building(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _list_service_providers(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("routers.ops_repairs.ops_repairs_service.list_service_providers", _list_service_providers)

    response = client.get("/api/ops/service-providers")

    assert response.status_code == 200
    assert captured["building_id"] == "UP-13195"


def test_create_service_request_wires_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _create_service_request(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "service_request_id": "sr-1",
            "case_id": kwargs["payload"].case_id,
            "request_kind": kwargs["payload"].request_kind,
            "status": "triaged",
            "lot_id": None,
            "asset_reference": None,
            "current_vendor_id": None,
            "current_vendor_name": None,
            "disturbance_level": kwargs["payload"].disturbance_level,
            "preferred_contact_method": kwargs["payload"].preferred_contact_method,
            "created_by": "manager-1",
            "updated_by": "manager-1",
            "created_at": now,
            "updated_at": now,
        }

    monkeypatch.setattr("routers.ops_repairs.ops_repairs_service.create_service_request", _create_service_request)

    response = client.post(
        "/api/ops/service-requests",
        json={
            "case_id": "2d581c7b-f372-47f8-8bb6-16bf6d4e5508",
            "request_kind": "repair",
            "disturbance_level": "medium",
            "preferred_contact_method": "email",
        },
    )

    assert response.status_code == 201
    assert captured["building_id"] == "UP-13195"
    assert captured["payload"].request_kind == "repair"


def test_assign_vendor_wires_ids(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _assign_vendor(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "assignment_id": "assign-1",
            "service_request_id": kwargs["service_request_id"],
            "vendor_id": kwargs["payload"].vendor_id,
            "vendor_name": "Acme Plumbing",
            "assignment_status": "assigned",
            "assigned_by": "manager-1",
            "response_due_at": None,
            "responded_at": None,
            "response_note": None,
            "created_at": now,
            "updated_at": now,
        }

    monkeypatch.setattr("routers.ops_repairs.ops_repairs_service.assign_vendor", _assign_vendor)

    response = client.post(
        "/api/ops/service-requests/9023ac60-a04f-4360-a726-c0d37f4b284a/assign-vendor",
        json={"vendor_id": "a7a8b546-c5ab-4bb9-b8a2-ccbe1cacfe0f"},
    )

    assert response.status_code == 201
    assert captured["service_request_id"] == "9023ac60-a04f-4360-a726-c0d37f4b284a"


def test_create_recurring_template_rejects_building_id_in_body(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )

    response = client.post(
        "/api/ops/recurring-task-templates",
        json={
            "building_id": "should-not-be-accepted",
            "title": "Monthly fire pump inspection",
            "category": "preventive_maintenance",
            "cadence_unit": "month",
            "cadence_interval": 1,
        },
    )

    assert response.status_code == 422


def test_generate_recurring_task_wires_template_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _generate_recurring_task(**kwargs):
        captured.update(kwargs)
        return {
            "template_id": kwargs["template_id"],
            "case_id": "case-1",
            "service_request_id": "sr-1",
            "next_due_at": datetime.now(timezone.utc),
        }

    monkeypatch.setattr("routers.ops_repairs.ops_repairs_service.generate_recurring_task", _generate_recurring_task)

    response = client.post("/api/ops/recurring-task-templates/20d937ec-4176-4617-b63c-e76fc7a032c0/generate-now")

    assert response.status_code == 200
    assert captured["template_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"
