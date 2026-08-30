"""
Tests — trial_request Postgres migration (Task C+G)

Tests the refactored trial_request router and trial_request_repo functions.
All Postgres calls are mocked; no live database required.

Coverage:
- POST /public/trial-request (happy path, duplicate idempotency, captcha failure)
- GET  /admin/trial-requests (filters, pagination)
- PATCH /admin/trial-requests/{id} (notes update, 404)
- POST /admin/trial-requests/{id}/approve (happy path, 409 already approved)
- POST /admin/trial-requests/{id}/reject  (happy path, 409 already rejected)
- Field mapping: name/company/state → contact_first_name/org_name/jurisdiction
- _map_jurisdiction correctness
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_REQUEST_ID = str(uuid.uuid4())
_NOW = datetime.now(timezone.utc)


def _make_row(overrides: dict | None = None) -> dict:
    base = {
        "request_id": uuid.UUID(_REQUEST_ID),
        "submitted_at": _NOW,
        "status": "submitted",
        "org_name": "Acme Corp",
        "contact_first_name": "Jane Smith",
        "contact_last_name": "",
        "contact_email": "jane@acme.com",
        "contact_phone": "0400000000",
        "jurisdiction": "NSW",
        "message": "Interested",
        "notes": "Lots: 50",
        "reviewed_at": None,
        "reject_reason": None,
        "expires_at": _NOW,
        "created_tenant_id": None,
        "invitation_id": None,
    }
    if overrides:
        base.update(overrides)
    return base


def _make_super_admin() -> dict:
    return {"id": "user-001", "user_uuid": str(uuid.uuid4()), "role": "super_admin", "effective_role": "super_admin"}


# ── _map_jurisdiction unit tests ───────────────────────────────────────────────

class TestMapJurisdiction:
    def test_uppercase_code(self):
        from db_postgres.repos.trial_request_repo import _map_jurisdiction
        assert _map_jurisdiction("NSW") == "NSW"

    def test_lowercase_code(self):
        from db_postgres.repos.trial_request_repo import _map_jurisdiction
        assert _map_jurisdiction("nsw") == "NSW"

    def test_long_name(self):
        from db_postgres.repos.trial_request_repo import _map_jurisdiction
        assert _map_jurisdiction("Western Australia") == "WA"

    def test_unknown_defaults_to_nsw(self):
        from db_postgres.repos.trial_request_repo import _map_jurisdiction
        assert _map_jurisdiction("Narnia") == "NSW"

    def test_empty_defaults_to_nsw(self):
        from db_postgres.repos.trial_request_repo import _map_jurisdiction
        assert _map_jurisdiction("") == "NSW"


# ── POST /public/trial-request ─────────────────────────────────────────────────

class TestSubmitTrialRequest:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from fastapi import FastAPI
        from routers.trial_request import router
        self.app = FastAPI()
        self.app.add_middleware(
            __import__("starlette.middleware.base", fromlist=["BaseHTTPMiddleware"]).BaseHTTPMiddleware,
            dispatch=lambda req, call_next: call_next(req)
        )
        self.app.include_router(router)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    @patch("routers.trial_request._verify_recaptcha", new_callable=AsyncMock, return_value=True)
    @patch("routers.trial_request.create_trial_request", new_callable=AsyncMock)
    @patch("routers.trial_request.send_email_async", new_callable=AsyncMock)
    @patch("routers.trial_request.create_user_notification", new_callable=AsyncMock)
    def test_happy_path(self, mock_notif, mock_email, mock_create, mock_recap):
        mock_create.return_value = _make_row()
        # Mock db.users.find(...).to_list(50)
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[{"id": "admin-1"}])
        with patch("routers.trial_request.db") as mock_db:
            mock_db.users.find.return_value = cursor
            resp = self.client.post("/public/trial-request", json={
                "name": "Jane Smith",
                "company": "Acme Corp",
                "email": "jane@acme.com",
                "phone": "0400000000",
                "lots": "50",
                "state": "nsw",
                "message": "Interested",
                "captcha_token": "tok",
            })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["org_name"] == "Acme Corp"
        assert call_kwargs["contact_name"] == "Jane Smith"
        assert call_kwargs["jurisdiction"] == "NSW"  # mapped from 'nsw'
        assert "Lots: 50" in call_kwargs["notes"]

    @patch("routers.trial_request._verify_recaptcha", new_callable=AsyncMock, return_value=False)
    def test_captcha_failure_returns_400(self, _mock):
        resp = self.client.post("/public/trial-request", json={
            "name": "Jane", "company": "Acme", "email": "j@a.com",
            "captcha_token": "bad"
        })
        assert resp.status_code == 400

    @patch("routers.trial_request._verify_recaptcha", new_callable=AsyncMock, return_value=True)
    @patch("routers.trial_request.send_email_async", new_callable=AsyncMock)
    @patch("routers.trial_request.create_user_notification", new_callable=AsyncMock)
    def test_duplicate_returns_ok_not_error(self, mock_notif, mock_email, mock_recap):
        """Duplicate submission returns 200 ok (idempotent)."""
        dup_row = _make_row({"_duplicate": True})
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        with patch("routers.trial_request.create_trial_request", new_callable=AsyncMock, return_value=dup_row):
            with patch("routers.trial_request.db") as mock_db:
                mock_db.users.find.return_value = cursor
                resp = self.client.post("/public/trial-request", json={
                    "name": "Jane", "company": "Acme", "email": "jane@acme.com",
                    "captcha_token": "tok",
                })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── GET /admin/trial-requests ─────────────────────────────────────────────────

def _make_client(user: dict):
    """Helper: build a TestClient with get_current_user overridden."""
    from fastapi import FastAPI
    from routers.trial_request import router
    from utils.auth import get_current_user as _dep
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_dep] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


class TestListTrialRequests:
    @patch("routers.trial_request._pg_list", new_callable=AsyncMock)
    def test_happy_path(self, mock_list):
        mock_list.return_value = {
            "data": [_make_row()],
            "total": 1,
            "counts": {"submitted": 1, "approved": 0, "rejected": 0, "expired": 0, "total": 1},
        }
        client = _make_client(_make_super_admin())
        resp = client.get("/admin/trial-requests")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert body["counts"]["submitted"] == 1

    @patch("routers.trial_request._pg_list", new_callable=AsyncMock)
    def test_invalid_status_422(self, mock_list):
        client = _make_client(_make_super_admin())
        resp = client.get("/admin/trial-requests?status=old_mongo_status")
        assert resp.status_code == 422

    def test_non_admin_403(self):
        non_admin = {"id": "u1", "role": "owner", "effective_role": "owner"}
        client = _make_client(non_admin)
        resp = client.get("/admin/trial-requests")
        assert resp.status_code == 403


# ── PATCH /admin/trial-requests/{id} ──────────────────────────────────────────

class TestPatchTrialRequest:
    @patch("routers.trial_request._pg_update", new_callable=AsyncMock)
    def test_update_notes(self, mock_update):
        mock_update.return_value = _make_row({"notes": "Updated notes"})
        client = _make_client(_make_super_admin())
        resp = client.patch(
            f"/admin/trial-requests/{_REQUEST_ID}",
            json={"notes": "Updated notes"},
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Updated notes"

    @patch("routers.trial_request._pg_update", new_callable=AsyncMock, return_value=None)
    def test_not_found_404(self, _mock):
        client = _make_client(_make_super_admin())
        resp = client.patch(
            f"/admin/trial-requests/{_REQUEST_ID}",
            json={"notes": "x"},
        )
        assert resp.status_code == 404


# ── POST /admin/trial-requests/{id}/approve ───────────────────────────────────

class TestApproveTrial:
    @patch("routers.trial_request.approve_trial_request", new_callable=AsyncMock)
    def test_approve_happy_path(self, mock_approve):
        mock_approve.return_value = _make_row({"status": "approved"})
        client = _make_client(_make_super_admin())
        resp = client.post(f"/admin/trial-requests/{_REQUEST_ID}/approve", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    @patch("routers.trial_request.approve_trial_request", new_callable=AsyncMock, return_value=None)
    @patch("routers.trial_request.get_trial_request_by_id", new_callable=AsyncMock)
    def test_already_approved_409(self, mock_get, mock_approve):
        mock_get.return_value = _make_row({"status": "approved"})
        client = _make_client(_make_super_admin())
        resp = client.post(f"/admin/trial-requests/{_REQUEST_ID}/approve", json={})
        assert resp.status_code == 409

    @patch("routers.trial_request.approve_trial_request", new_callable=AsyncMock, return_value=None)
    @patch("routers.trial_request.get_trial_request_by_id", new_callable=AsyncMock, return_value=None)
    def test_not_found_404(self, _mock_get, _mock_approve):
        client = _make_client(_make_super_admin())
        resp = client.post(f"/admin/trial-requests/{_REQUEST_ID}/approve", json={})
        assert resp.status_code == 404


# ── POST /admin/trial-requests/{id}/reject ────────────────────────────────────

class TestRejectTrial:
    @patch("routers.trial_request.reject_trial_request", new_callable=AsyncMock)
    def test_reject_happy_path(self, mock_reject):
        mock_reject.return_value = _make_row({"status": "rejected", "reject_reason": "Not a fit"})
        client = _make_client(_make_super_admin())
        resp = client.post(
            f"/admin/trial-requests/{_REQUEST_ID}/reject",
            json={"reason": "Not a fit"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["reject_reason"] == "Not a fit"

    @patch("routers.trial_request.reject_trial_request", new_callable=AsyncMock, return_value=None)
    @patch("routers.trial_request.get_trial_request_by_id", new_callable=AsyncMock)
    def test_already_rejected_409(self, mock_get, _mock_rej):
        mock_get.return_value = _make_row({"status": "rejected"})
        client = _make_client(_make_super_admin())
        resp = client.post(
            f"/admin/trial-requests/{_REQUEST_ID}/reject",
            json={"reason": "x"},
        )
        assert resp.status_code == 409
