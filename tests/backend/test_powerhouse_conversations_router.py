from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.powerhouse_conversation import ConversationMessageCreate
from routers.powerhouse_conversations import router
from routers.powerhouse_conversations import add_thread_internal_note
from utils.auth import get_current_building, get_current_user


def _build_app(user: dict, building_id: str = "13195") -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app)


@pytest.fixture(autouse=True)
def all_powerhouse_features_enabled():
    """Patch get_effective_feature_access to return True for all Powerhouse keys.

    Individual tests that want to test the toggle-off behaviour should override
    this by patching the same function inside their own test body.
    """
    async def _always_enabled(_user, _key):
        return True

    with patch("utils.permissions.get_effective_feature_access", side_effect=_always_enabled):
        yield


def test_create_conversation_thread_passes_building_to_service(monkeypatch):
    captured: dict = {}

    async def _fake_create_thread(*, building_id: str, current_user: dict, payload, idempotency_key=None):
        captured["building_id"] = building_id
        captured["subject"] = payload.subject
        captured["created_by"] = current_user.get("id")
        return {"id": "thread-1", "building_id": building_id, "subject": payload.subject}

    monkeypatch.setattr("routers.powerhouse_conversations.svc.create_thread", _fake_create_thread)
    client = _build_app({"id": "u-1", "role": "strata_manager"})

    response = client.post(
        "/api/powerhouse/conversations/threads",
        json={
            "subject": "Maintenance noise complaint",
            "source_channel": "portal_message",
            "body": "Need help with noise from rooftop equipment.",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "thread-1"
    assert captured == {"building_id": "13195", "subject": "Maintenance noise complaint", "created_by": "u-1"}


def test_create_conversation_thread_forbids_unexpected_building_id():
    client = _build_app({"id": "u-1", "role": "strata_manager"})
    response = client.post(
        "/api/powerhouse/conversations/threads",
        json={
            "subject": "Levy follow up",
            "source_channel": "portal_message",
            "body": "Question about levy payment schedule.",
            "building_id": "other-building",
        },
    )
    assert response.status_code == 422


def test_inbox_config_requires_manager_role():
    client = _build_app({"id": "owner-1", "role": "owner"})
    response = client.post(
        "/api/powerhouse/inboxes/configure",
        json={
            "inbox_name": "Building shared inbox",
            "address": "inbox@example.com",
            "provider_key": "mock",
            "enabled": True,
            "allowed_roles": ["owner", "tenant"],
        },
    )
    assert response.status_code == 403
    assert "Manager access required" in response.text


def test_inbound_webhook_forwards_idempotency_key(monkeypatch):
    captured: dict = {}

    async def _fake_inbound(*, building_id: str, inbox_id: str, current_user: dict, payload, idempotency_key: str | None):
        captured["idempotency_key"] = idempotency_key
        captured["message_id"] = payload.message_id
        return {"event_id": "evt-1", "idempotent": False, "status": "received"}

    monkeypatch.setattr("routers.powerhouse_conversations.svc.process_inbound_email", _fake_inbound)
    client = _build_app({"id": "mgr-1", "role": "strata_manager"})
    response = client.post(
        "/api/powerhouse/inboxes/inbox-1/inbound-email",
        headers={"Idempotency-Key": "msg-abc"},
        json={
            "message_id": "<abc@example.com>",
            "references": [],
            "subject": "Test inbound",
            "from_email": "owner@example.com",
            "to": ["inbox@example.com"],
            "cc": [],
            "text_body": "Please help",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert captured == {"idempotency_key": "msg-abc", "message_id": "<abc@example.com>"}


def test_ai_response_draft_placeholder_requires_human_approval(monkeypatch):
    async def _fake_draft(*, building_id: str, thread_id: str, current_user: dict):
        return {"thread_id": thread_id, "safe_to_send": False, "requires_human_approval": True}

    monkeypatch.setattr("routers.powerhouse_conversations.svc.generate_ai_response_draft_placeholder", _fake_draft)
    client = _build_app({"id": "tenant-9", "role": "tenant"})
    response = client.post("/api/powerhouse/conversations/threads/thread-7/ai-response-draft")
    assert response.status_code == 200
    assert response.json()["safe_to_send"] is False
    assert response.json()["requires_human_approval"] is True


def test_add_thread_internal_note_does_not_mutate_request_payload(monkeypatch):
    captured: dict = {}

    async def _fake_add_message(*, building_id: str, thread_id: str, current_user: dict, payload, idempotency_key=None):
        captured["message_type"] = payload.message_type
        return {"id": "msg-1"}

    monkeypatch.setattr("routers.powerhouse_conversations.svc.add_message", _fake_add_message)
    payload = ConversationMessageCreate(body="Internal note body")

    response = add_thread_internal_note(
        thread_id="thread-7",
        payload=payload,
        current_user={"id": "mgr-1", "role": "strata_manager"},
        building_id="13195",
    )

    result = asyncio.run(response)
    assert result == {"id": "msg-1"}
    assert captured["message_type"] == "internal_note"
    assert payload.message_type == "message"


def test_convert_message_to_workflow_passes_ids_to_service(monkeypatch):
    captured: dict = {}

    async def _fake_convert_message(*, building_id: str, message_id: str, current_user: dict, payload):
        captured["building_id"] = building_id
        captured["message_id"] = message_id
        captured["workflow_template_key"] = payload.workflow_template_key
        captured["actor"] = current_user.get("id")
        return {"id": "wf-1", "source_message_id": message_id}

    monkeypatch.setattr("routers.powerhouse_conversations.svc.convert_message_to_workflow", _fake_convert_message)
    client = _build_app({"id": "mgr-1", "role": "strata_manager"})
    response = client.post(
        "/api/powerhouse/conversations/messages/msg-42/convert-to-workflow",
        json={"workflow_template_key": "maintenance-from-conversation", "reason": "Convert this message"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "wf-1"
    assert captured == {
        "building_id": "13195",
        "message_id": "msg-42",
        "workflow_template_key": "maintenance-from-conversation",
        "actor": "mgr-1",
    }


def test_workflow_instance_creation_requires_manager():
    client = _build_app({"id": "guest-1", "role": "guest"})
    response = client.post(
        "/api/powerhouse/workflows/instances",
        json={"template_key": "maintenance-from-conversation", "title": "Guest cannot create"},
    )
    assert response.status_code == 403
