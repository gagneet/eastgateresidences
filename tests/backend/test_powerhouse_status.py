"""Powerhouse UI visibility status endpoint tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.powerhouse_status import build_powerhouse_status, router
from utils.auth import get_current_building, get_current_user


def _build_app(user: dict, building_id: str = "13195") -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app)


def _patch_config(enabled_keys: set[str] | None = None):
    enabled_keys = enabled_keys or set()

    async def _global(feature_key):
        return {"feature_key": feature_key, "is_enabled": False}

    async def _resolved(building_id, feature_key, default=False):
        return feature_key in enabled_keys

    return patch.multiple(
        "routers.powerhouse_status.config_repo",
        get_global_feature_toggle=AsyncMock(side_effect=_global),
        resolve_feature_toggle=AsyncMock(side_effect=_resolved),
    )


@pytest.mark.asyncio
async def test_super_admin_sees_all_powerhouse_features():
    with _patch_config({"powerhouse_conversations"}):
        payload = await build_powerhouse_status({"id": "sa", "role": "super_admin"}, "13195")
    keys = {item["key"] for item in payload["features"]}
    assert "cutover_control_plane" in keys
    assert "future_mongo_archive_controls" in keys
    assert payload["diagnostic"] is True


@pytest.mark.asyncio
async def test_strata_manager_sees_allowed_subset():
    with _patch_config({"powerhouse_conversations"}):
        payload = await build_powerhouse_status({"id": "mgr", "role": "strata_manager"}, "13195")
    keys = {item["key"] for item in payload["features"]}
    assert "powerhouse_conversations" in keys
    assert "cutover_control_plane" not in keys
    assert "future_mongo_archive_controls" not in keys


@pytest.mark.asyncio
async def test_owner_sees_only_owner_safe_features():
    with _patch_config({"powerhouse_conversations"}):
        payload = await build_powerhouse_status({"id": "owner", "role": "owner"}, "13195")
    keys = {item["key"] for item in payload["features"]}
    assert keys == {"powerhouse_conversations"}
    assert payload["diagnostic"] is False
    assert "apiRoute" not in payload["features"][0]


@pytest.mark.asyncio
async def test_tenant_guest_see_restricted_subset():
    with _patch_config({"powerhouse_conversations"}):
        tenant = await build_powerhouse_status({"id": "tenant", "role": "tenant"}, "13195")
        guest = await build_powerhouse_status({"id": "guest", "role": "guest"}, "13195")
    assert {item["key"] for item in tenant["features"]} == {"powerhouse_conversations"}
    assert {item["key"] for item in guest["features"]} == {"powerhouse_conversations"}


@pytest.mark.asyncio
async def test_disabled_toggle_returns_disabled_reason():
    with _patch_config(set()):
        payload = await build_powerhouse_status({"id": "owner", "role": "owner"}, "13195")
    feature = payload["features"][0]
    assert feature["enabled"] is False
    assert "disabled" in feature["reason"].lower()


@pytest.mark.asyncio
async def test_cross_building_visibility_enforced():
    with _patch_config({"powerhouse_conversations"}):
        enabled = await build_powerhouse_status({"id": "owner", "role": "owner"}, "13195")
    with _patch_config(set()):
        disabled = await build_powerhouse_status({"id": "owner", "role": "owner"}, "16244")
    assert enabled["features"][0]["enabled"] is True
    assert disabled["features"][0]["enabled"] is False
    assert disabled["building_id"] == "16244"


def test_endpoint_returns_status_payload():
    with _patch_config({"powerhouse_conversations"}):
        resp = _build_app({"id": "sa", "role": "super_admin"}).get("/api/features/powerhouse/status")
    assert resp.status_code == 200
    assert resp.json()["role"] == "super_admin"
