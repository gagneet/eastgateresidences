"""Tests for POST .../reconstruction-batches/{batch_id}/reject in routers/financial_onboarding.py.

Uses the real FastAPI app + TestClient + dependency_overrides pattern (matching
test_financial_onboarding_dispatch_and_toggles.py) so role gating and the service-layer
dispatch are exercised for real, not against stand-in stubs. reject_batch()'s own status-machine
semantics are covered by tests/backend/test_reconstruction_batch_service.py — this file only
covers the router's role gate, request validation, and audit-log wiring.
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
    # full_name, not name -- current_user's real shape (see utils/auth.py's Postgres and
    # legacy-Mongo paths, both of which populate full_name). A "name" key here used to mask the
    # 2026-08-01 audit-log bug: routers/financial_onboarding.py read current_user.get("name", ...)
    # everywhere, which never matched this fixture's real-shape mismatch either -- both were
    # wrong in the same direction, so no test caught it.
    return {"id": "user-1", "full_name": "Test User", "role": role, "effective_role": role}


def _build_app(user: dict, building_id: str = BUILDING_ID) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app)


class _FakeBatch:
    def model_dump(self, mode="json"):
        return {"batch_id": "b1", "status": "extracted"}


class TestRejectEndpointRoleGate:
    @pytest.mark.asyncio
    async def test_strata_manager_cannot_reject(self):
        """_RECONSTRUCTION_REVIEW_ROLES = {super_admin, strata_admin} — strata_manager is
        upload-eligible but not review/reject-eligible, matching /review's own role gate."""
        client = _build_app(_user("strata_manager"))
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/b1/reject",
                json={"reason": "Bad data — implausible closing balance."},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_strata_admin_can_reject(self):
        client = _build_app(_user("strata_admin"))
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("services.reconstruction_batch_service.reject_batch", new=AsyncMock(return_value=_FakeBatch())) as reject_mock, \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()) as audit_mock:
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/b1/reject",
                json={"reason": "Bad data — implausible closing balance."},
            )
        assert resp.status_code == 200
        reject_mock.assert_awaited_once()
        _, kwargs = reject_mock.call_args
        assert kwargs["reason"] == "Bad data — implausible closing balance."
        assert kwargs["batch_id"] == "b1"

        # Regression: routers/financial_onboarding.py used to read current_user.get("name",
        # "unknown") -- current_user has no "name" key (real shape is full_name), so every
        # audit-log entry's user_name was silently "unknown" regardless of who acted. Fixed
        # 2026-08-01 to read full_name.
        audit_mock.assert_awaited_once()
        _, audit_kwargs = audit_mock.call_args
        assert audit_kwargs["user_name"] == "Test User"

    @pytest.mark.asyncio
    async def test_reject_requires_reason(self):
        client = _build_app(_user("strata_admin"))
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/b1/reject",
                json={},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_reject_invalid_transition_returns_409(self):
        from services.reconstruction_batch_service import ReconstructionWorkflowError

        client = _build_app(_user("strata_admin"))
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch(
                 "services.reconstruction_batch_service.reject_batch",
                 new=AsyncMock(side_effect=ReconstructionWorkflowError("reject_batch requires status=needs_review, got 'draft'")),
             ), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/b1/reject",
                json={"reason": "wrong data"},
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_reject_rejects_whitespace_only_reason(self):
        """min_length=3 alone would accept "   " (3 spaces) — the router strips and
        re-checks before persisting, matching RegenerateLevyApplyRequest's same defence.
        _assert_building_access is mocked (as in test_strata_admin_can_reject) because the
        blank-reason check happens inside the handler body, after that call — an unmocked
        real building lookup for a test-only building_id would 400 before reaching it."""
        client = _build_app(_user("strata_admin"))
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/b1/reject",
                json={"reason": "     "},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_reject_rejects_too_short_reason(self):
        client = _build_app(_user("strata_admin"))
        with patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True)):
            resp = client.post(
                f"/api/financials/onboarding/building/{BUILDING_ID}/reconstruction-batches/b1/reject",
                json={"reason": "no"},
            )
        assert resp.status_code == 422
