from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import utils.permissions as permissions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.communications_intake import router as communications_intake_router
from utils.auth import get_current_building, get_current_user


def _build_app(monkeypatch, current_user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(communications_intake_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_current_building] = lambda: "UP-13195"
    monkeypatch.setattr(permissions, "get_effective_feature_access", AsyncMock(return_value=True))
    return TestClient(app)


def _signed_headers(payload: dict) -> dict[str, str]:
    secret = "test-webhook-secret"
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {"X-StrataOS-Signature": sig, "Content-Type": "application/json"}


def test_receive_inbound_email_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("COMMUNICATIONS_INBOUND_EMAIL_WEBHOOK_SECRET", "test-webhook-secret")
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )

    response = client.post(
        "/api/communications/inbound/email",
        json={
            "provider": "resend",
            "message_id": "msg-1",
            "from_email": "owner@example.com",
            "subject": "Leak in unit 3",
            "text_body": "There is water coming through the ceiling",
            "to": ["UP-13195@mailer.example.com"],
        },
        headers={"X-StrataOS-Signature": "bad"},
    )

    assert response.status_code == 401


def test_receive_inbound_email_forbids_building_id_body(monkeypatch):
    monkeypatch.setenv("COMMUNICATIONS_INBOUND_EMAIL_WEBHOOK_SECRET", "test-webhook-secret")
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )

    response = client.post(
        "/api/communications/inbound/email",
        json={
            "provider": "resend",
            "message_id": "msg-1",
            "from_email": "owner@example.com",
            "subject": "Leak in unit 3",
            "text_body": "There is water coming through the ceiling",
            "to": ["UP-13195@mailer.example.com"],
            "building_id": "should-not-be-accepted",
        },
        headers=_signed_headers(
            {
                "provider": "resend",
                "message_id": "msg-1",
                "from_email": "owner@example.com",
                "subject": "Leak in unit 3",
                "text_body": "There is water coming through the ceiling",
                "to": ["UP-13195@mailer.example.com"],
                "building_id": "should-not-be-accepted",
            }
        ),
    )

    assert response.status_code == 422


def test_receive_inbound_email_calls_service(monkeypatch):
    monkeypatch.setenv("COMMUNICATIONS_INBOUND_EMAIL_WEBHOOK_SECRET", "test-webhook-secret")
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _create_inbound_email_case(payload, **kwargs):
        captured["payload"] = payload
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "intake_id": "8b274c7d-70f5-49cc-b8dc-67adf77477d6",
            "case_id": "8b274c7d-70f5-49cc-b8dc-67adf77477d6",
            "scheme_id": "scheme-1",
            "status": "triaged",
            "category": "repair",
            "priority": "high",
            "risk_level": "medium",
            "title": "Leak in unit 3",
            "description": "There is water coming through the ceiling",
            "sender_email": "owner@example.com",
            "sender_name": None,
            "received_at": now,
            "classification_confidence": 0.92,
            "classification_reason": "Matched repair keywords",
            "needs_review": False,
            "duplicate_candidate_case_id": None,
            "duplicate_of_case_id": None,
            "provider": "resend",
            "message_id": "msg-1",
            "recipient_emails": ["UP-13195@mailer.example.com"],
            "attachment_count": 0,
            "created_at": now,
            "updated_at": now,
        }

    monkeypatch.setattr("routers.communications_intake.email_intake_service.create_inbound_email_case", _create_inbound_email_case)

    payload = {
        "provider": "resend",
        "message_id": "msg-1",
        "from_email": "owner@example.com",
        "subject": "Leak in unit 3",
        "text_body": "There is water coming through the ceiling",
        "to": ["UP-13195@mailer.example.com"],
    }
    body = json.dumps(payload)
    signature = hmac.new(
        os.environ["COMMUNICATIONS_INBOUND_EMAIL_WEBHOOK_SECRET"].encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    response = client.post(
        "/api/communications/inbound/email",
        content=body,
        headers={"X-StrataOS-Signature": signature, "Content-Type": "application/json"},
    )

    assert response.status_code == 201
    assert captured["payload"].message_id == "msg-1"
    assert captured["header_building_id"] is None


def test_intake_queue_requires_manager_role(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "owner-1", "email": "owner@example.com", "role": "owner", "full_name": "Owner"},
    )

    response = client.get("/api/communications/intake-queue")

    assert response.status_code == 403


def test_intake_queue_passes_building_and_filters(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _list_intake_queue(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("routers.communications_intake.email_intake_service.list_intake_queue", _list_intake_queue)

    response = client.get("/api/communications/intake-queue?status=new&limit=20&offset=5")

    assert response.status_code == 200
    assert captured["building_id"] == "UP-13195"
    assert captured["status_filter"] == "new"
    assert captured["limit"] == 20
    assert captured["offset"] == 5
