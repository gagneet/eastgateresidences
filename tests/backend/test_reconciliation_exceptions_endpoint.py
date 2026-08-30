"""Tests for POST/GET .../building/{building_id}/reconciliation-exceptions in
routers/financial_onboarding.py.

Uses the real FastAPI app + TestClient + dependency_overrides pattern (matching
test_reconstruction_batch_reject_endpoint.py from the same session).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.financial_onboarding import router
from utils.auth import get_current_building, get_current_user

BUILDING_ID = "16244"


def _user(role: str = "strata_admin") -> dict:
    return {"id": "user-1", "full_name": "Test User", "role": role, "effective_role": role}


def _build_app(user: dict, building_id: str = BUILDING_ID) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app)


class TestCreateReconciliationExceptionEndpoint:
    def test_strata_manager_can_create(self):
        """_RECONSTRUCTION_UPLOAD_ROLES = {super_admin, strata_admin, strata_manager} —
        matches the same role gate the import-financial-data endpoint uses."""
        client = _build_app(_user("strata_manager"))
        fake_doc = {"id": "exc-1", "building_id": BUILDING_ID, "year": 2021, "fund_type": "admin"}
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("services.reconstruction_generators.reconciliation_exceptions.create_exception", new=AsyncMock(return_value=fake_doc)), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconciliation-exceptions",
                json={
                    "year": 2021, "fund_type": "admin", "amount_cents": 4071781,
                    "direction": "credit", "reason_code": "OPENING_BALANCE_BRIDGE",
                    "effect_scope": "opening_rollforward", "note": "2021->2022 bridge",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["id"] == "exc-1"

    def test_owner_cannot_create(self):
        client = _build_app(_user("owner"))
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconciliation-exceptions",
                json={
                    "year": 2021, "fund_type": "admin", "amount_cents": 100,
                    "direction": "credit", "reason_code": "CUTOFF_MISMATCH",
                    "effect_scope": "informational",
                },
            )
        assert resp.status_code == 403

    def test_invalid_reason_code_returns_422(self):
        client = _build_app(_user("strata_admin"))
        from services.reconstruction_generators.reconciliation_exceptions import ReconciliationExceptionError

        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch(
                 "services.reconstruction_generators.reconciliation_exceptions.create_exception",
                 new=AsyncMock(side_effect=ReconciliationExceptionError("Unrecognised reason_code")),
             ):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconciliation-exceptions",
                json={
                    "year": 2021, "fund_type": "admin", "amount_cents": 100,
                    "direction": "credit", "reason_code": "NOT_REAL",
                    "effect_scope": "informational",
                },
            )
        assert resp.status_code == 422

    def test_zero_amount_rejected_by_pydantic(self):
        """amount_cents must be > 0 — a zero-amount exception is meaningless."""
        client = _build_app(_user("strata_admin"))
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconciliation-exceptions",
                json={
                    "year": 2021, "fund_type": "admin", "amount_cents": 0,
                    "direction": "credit", "reason_code": "CUTOFF_MISMATCH",
                    "effect_scope": "informational",
                },
            )
        assert resp.status_code == 422


class TestListReconciliationExceptionsEndpoint:
    def test_lists_within_requested_year_range(self):
        client = _build_app(_user("strata_admin"))
        fake_docs = [{"id": "exc-1", "year": 2021, "fund_type": "admin"}]
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("services.reconstruction_generators.reconciliation_exceptions.list_exceptions", new=AsyncMock(return_value=fake_docs)) as list_mock:
            resp = client.get(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconciliation-exceptions",
                params={"from_year": 2021, "to_year": 2022},
            )
        assert resp.status_code == 200
        assert resp.json()["exceptions"] == fake_docs
        list_mock.assert_awaited_once()
        _, kwargs = list_mock.call_args
        assert kwargs["financial_year_start"] == 2021
        assert kwargs["financial_year_end"] == 2022
