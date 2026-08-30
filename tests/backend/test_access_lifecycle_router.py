from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import utils.permissions as permissions
import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.access_lifecycle import router as access_lifecycle_router
from services.access_lifecycle_service import _assert_device_type_editor
from utils.auth import get_current_building, get_current_user


def _build_app(monkeypatch, current_user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(access_lifecycle_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_current_building] = lambda: "UP-13195"
    monkeypatch.setattr(permissions, "get_effective_feature_access", AsyncMock(return_value=True))
    return TestClient(app)


def test_create_access_request_wires_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "owner-1", "email": "owner@example.com", "role": "owner", "full_name": "Owner"},
    )
    captured = {}

    async def _create_request(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "request_id": "req-1",
            "case_id": "case-1",
            "device_type_id": kwargs["payload"].device_type_id,
            "device_type_name": "Garage Fob",
            "requester_user_id": "owner-1",
            "requester_party_id": None,
            "lot_id": None,
            "request_type": kwargs["payload"].request_type,
            "reason": kwargs["payload"].reason,
            "status": "requested",
            "approval_required": False,
            "approval_request_id": None,
            "fee_cents": 0,
            "deposit_cents": 0,
            "requested_at": now,
            "due_at": None,
            "created_at": now,
            "updated_at": now,
        }

    monkeypatch.setattr("routers.access_lifecycle.access_lifecycle_service.create_request", _create_request)

    response = client.post(
        "/api/access/requests",
        json={
            "device_type_id": "2d581c7b-f372-47f8-8bb6-16bf6d4e5508",
            "request_type": "new_issue",
            "reason": "Need a replacement garage fob",
        },
    )

    assert response.status_code == 201
    assert captured["building_id"] == "UP-13195"
    assert captured["payload"].request_type == "new_issue"


def test_create_access_request_rejects_building_id_body(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "owner-1", "email": "owner@example.com", "role": "owner", "full_name": "Owner"},
    )

    response = client.post(
        "/api/access/requests",
        json={
            "building_id": "should-not-be-accepted",
            "device_type_id": "2d581c7b-f372-47f8-8bb6-16bf6d4e5508",
            "request_type": "new_issue",
        },
    )

    assert response.status_code == 422


def test_list_devices_uses_building_dependency(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _list_devices(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("routers.access_lifecycle.access_lifecycle_service.list_devices", _list_devices)

    response = client.get("/api/access/devices")

    assert response.status_code == 200
    assert captured["building_id"] == "UP-13195"


def test_list_device_types_includes_inactive_flag(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _list_device_types(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return [
            {
                "device_type_id": "type-1",
                "type_key": "garage_remote",
                "name": "Garage remote",
                "description": None,
                "request_available": True,
                "fee_cents": 7200,
                "deposit_cents": 0,
                "replacement_lost_fee_cents": 7200,
                "max_quantity": 2,
                "requires_approval": True,
                "requires_return": True,
                "effective_date": None,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
        ]

    monkeypatch.setattr("routers.access_lifecycle.access_lifecycle_service.list_device_types", _list_device_types)

    response = client.get("/api/access/device-types?include_inactive=true")

    assert response.status_code == 200
    assert captured["building_id"] == "UP-13195"
    assert captured["include_inactive"] is True
    assert response.json()[0]["type_key"] == "garage_remote"


def test_create_device_type_rejects_building_id_body(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )

    response = client.post(
        "/api/access/device-types",
        json={
            "building_id": "wrong",
            "name": "Garage remote",
            "fee_cents": 7200,
        },
    )

    assert response.status_code == 422


def test_admin_staff_needs_specific_access_device_settings_permission():
    ctx = SimpleNamespace(role="admin_staff")

    with pytest.raises(HTTPException) as exc:
        _assert_device_type_editor(ctx, {"role": "admin_staff", "is_approved": True})

    assert exc.value.status_code == 403


def test_authorised_admin_staff_can_edit_access_device_settings():
    ctx = SimpleNamespace(role="admin_staff")

    _assert_device_type_editor(
        ctx,
        {
            "role": "admin_staff",
            "is_approved": True,
            "custom_permissions": {"can_manage_access_device_settings": True},
        },
    )


def test_issue_request_wires_ids(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _issue_request(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "request_id": kwargs["request_id"],
            "case_id": "case-1",
            "device_type_id": "type-1",
            "device_type_name": "Garage Fob",
            "requester_user_id": "owner-1",
            "requester_party_id": None,
            "lot_id": None,
            "request_type": "new_issue",
            "reason": None,
            "status": "issued",
            "approval_required": False,
            "approval_request_id": None,
            "fee_cents": 0,
            "deposit_cents": 0,
            "requested_at": now,
            "due_at": None,
            "created_at": now,
            "updated_at": now,
        }

    monkeypatch.setattr("routers.access_lifecycle.access_lifecycle_service.issue_request", _issue_request)

    response = client.post(
        "/api/access/requests/9023ac60-a04f-4360-a726-c0d37f4b284a/issue",
        json={"device_id": "a7a8b546-c5ab-4bb9-b8a2-ccbe1cacfe0f"},
    )

    assert response.status_code == 200
    assert captured["request_id"] == "9023ac60-a04f-4360-a726-c0d37f4b284a"


def test_disable_request_wires_reason(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _disable_request(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "request_id": kwargs["request_id"],
            "case_id": "case-1",
            "device_type_id": "type-1",
            "device_type_name": "Garage Fob",
            "requester_user_id": "owner-1",
            "requester_party_id": None,
            "lot_id": None,
            "request_type": "disablement",
            "reason": "Resident moved out",
            "status": "completed",
            "approval_required": True,
            "approval_request_id": None,
            "fee_cents": 0,
            "deposit_cents": 0,
            "requested_at": now,
            "due_at": None,
            "created_at": now,
            "updated_at": now,
        }

    monkeypatch.setattr("routers.access_lifecycle.access_lifecycle_service.disable_request", _disable_request)

    response = client.post(
        "/api/access/requests/9023ac60-a04f-4360-a726-c0d37f4b284a/disable",
        json={"reason": "Resident moved out"},
    )

    assert response.status_code == 200
    assert captured["payload"].reason == "Resident moved out"
