from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import utils.permissions as permissions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.communications_campaigns import router as communications_campaigns_router
from utils.auth import get_current_building, get_current_user


def _build_app(monkeypatch, current_user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(communications_campaigns_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_current_building] = lambda: "UP-13195"
    monkeypatch.setattr(permissions, "get_effective_feature_access", AsyncMock(return_value=True))
    return TestClient(app)


def test_list_campaigns_passes_building(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _list_campaigns(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("routers.communications_campaigns.communications_campaigns_service.list_campaigns", _list_campaigns)

    response = client.get("/api/communications/campaigns")

    assert response.status_code == 200
    assert captured["building_id"] == "UP-13195"


def test_create_newsletter_wires_sections_and_recipients(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _create_newsletter(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "newsletter_id": "news-1",
            "title": kwargs["payload"].title,
            "subject": kwargs["payload"].subject,
            "summary": kwargs["payload"].summary,
            "status": "draft",
            "scheduled_for": None,
            "sent_at": None,
            "approval_required": kwargs["payload"].approval_required,
            "approval_request_id": None,
            "approval_status": None,
            "section_count": len(kwargs["payload"].sections),
            "recipient_count": len(kwargs["payload"].recipients),
            "acknowledgement_count": 0,
            "delivery_event_count": 0,
            "created_by": "manager-1",
            "updated_by": "manager-1",
            "created_at": now,
            "updated_at": now,
            "sections": [
                {"section_id": "sec-1", "section_type": "headline", "title": "Works", "body": "Lift outage", "sort_order": 0}
            ],
            "recipients": [
                {
                    "recipient_id": "rec-1",
                    "party_id": None,
                    "recipient_email": "owner@example.com",
                    "recipient_type": "owner",
                    "delivery_status": "pending",
                    "delivered_at": None,
                    "opened_at": None,
                    "acknowledged_at": None,
                }
            ],
        }

    monkeypatch.setattr("routers.communications_campaigns.communications_campaigns_service.create_newsletter", _create_newsletter)

    response = client.post(
        "/api/communications/newsletters",
        json={
            "title": "May newsletter",
            "subject": "Important updates",
            "summary": "Key works this month",
            "sections": [{"section_type": "headline", "title": "Works", "body": "Lift outage"}],
            "recipients": [{"recipient_email": "owner@example.com", "recipient_type": "owner"}],
        },
    )

    assert response.status_code == 201
    assert captured["building_id"] == "UP-13195"
    assert captured["payload"].sections[0].section_type == "headline"
    assert captured["payload"].recipients[0].recipient_email == "owner@example.com"


def test_create_campaign_rejects_building_id_in_body(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )

    response = client.post(
        "/api/communications/campaigns",
        json={
            "building_id": "should-not-be-accepted",
            "campaign_type": "disruption_notice",
            "title": "Water outage",
            "audience_scope": "building",
        },
    )

    assert response.status_code == 422


def test_request_campaign_approval_wires_ids(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _request_campaign_approval(**kwargs):
        captured.update(kwargs)
        return {
            "approval_request_id": "approval-1",
            "policy_code": "communications-high-risk",
            "required_roles": ["ec_member"],
            "status": "pending",
            "requested_by": "manager-1",
            "requested_at": datetime.now(timezone.utc),
        }

    monkeypatch.setattr("routers.communications_campaigns.communications_campaigns_service.request_campaign_approval", _request_campaign_approval)

    response = client.post(
        "/api/communications/campaigns/20d937ec-4176-4617-b63c-e76fc7a032c0/submit-approval",
        json={"policy_code": "communications-high-risk", "note": "Needs EC review"},
    )

    assert response.status_code == 200
    assert captured["campaign_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"
    assert captured["payload"].policy_code == "communications-high-risk"


def test_patch_campaign_wires_update_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _update_campaign(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "campaign_id": kwargs["campaign_id"],
            "campaign_type": "maintenance_notice",
            "title": kwargs["payload"].title,
            "subject": kwargs["payload"].subject,
            "body": "Updated body",
            "status": "draft",
            "audience_scope": "building",
            "scheduled_for": None,
            "sent_at": None,
            "approval_required": True,
            "approval_request_id": None,
            "approval_status": None,
            "audience_segment_count": 1,
            "acknowledgement_count": 0,
            "delivery_event_count": 0,
            "created_by": "manager-1",
            "updated_by": "manager-1",
            "created_at": now,
            "updated_at": now,
            "audience_segments": [{"segment_id": "seg-1", "segment_type": "building", "segment_definition": {}, "estimated_recipient_count": 42}],
        }

    monkeypatch.setattr("routers.communications_campaigns.communications_campaigns_service.update_campaign", _update_campaign)

    response = client.patch(
        "/api/communications/campaigns/20d937ec-4176-4617-b63c-e76fc7a032c0",
        json={"title": "Updated outage notice", "subject": "Updated subject"},
    )

    assert response.status_code == 200
    assert captured["campaign_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"
    assert captured["payload"].title == "Updated outage notice"


def test_preview_campaign_wires_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _preview_campaign(**kwargs):
        captured.update(kwargs)
        return {
            "campaign_id": kwargs["campaign_id"],
            "title": "Preview title",
            "subject": "Preview subject",
            "body": "Preview body",
            "audience_scope": "building",
            "approval_required": True,
            "needs_human_approval": True,
            "preview_channels": ["email"],
            "audience_segments": [],
        }

    monkeypatch.setattr("routers.communications_campaigns.communications_campaigns_service.preview_campaign", _preview_campaign)

    response = client.post("/api/communications/campaigns/20d937ec-4176-4617-b63c-e76fc7a032c0/preview")

    assert response.status_code == 200
    assert captured["campaign_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"


def test_record_newsletter_delivery_event_wires_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _record_newsletter_delivery_event(**kwargs):
        captured.update(kwargs)
        return {
            "event_id": "event-1",
            "draft_id": None,
            "newsletter_id": kwargs["newsletter_id"],
            "campaign_id": None,
            "recipient_party_id": None,
            "recipient_email": kwargs["payload"].recipient_email,
            "channel": kwargs["payload"].channel,
            "provider_message_id": kwargs["payload"].provider_message_id,
            "event_type": kwargs["payload"].event_type,
            "occurred_at": datetime.now(timezone.utc),
            "event_payload": kwargs["payload"].event_payload,
        }

    monkeypatch.setattr(
        "routers.communications_campaigns.communications_campaigns_service.record_newsletter_delivery_event",
        _record_newsletter_delivery_event,
    )

    response = client.post(
        "/api/communications/newsletters/20d937ec-4176-4617-b63c-e76fc7a032c0/delivery-events",
        json={
            "recipient_email": "owner@example.com",
            "channel": "email",
            "provider_message_id": "msg-123",
            "event_type": "delivered",
            "event_payload": {"provider": "resend"},
        },
    )

    assert response.status_code == 201
    assert captured["newsletter_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"
    assert captured["payload"].event_type == "delivered"


def test_send_campaign_wires_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _send_campaign(**kwargs):
        captured.update(kwargs)
        return {
            "campaign_id": kwargs["campaign_id"],
            "status": "sent",
            "sent_at": datetime.now(timezone.utc),
        }

    monkeypatch.setattr("routers.communications_campaigns.communications_campaigns_service.send_campaign", _send_campaign)

    response = client.post("/api/communications/campaigns/20d937ec-4176-4617-b63c-e76fc7a032c0/send")

    assert response.status_code == 200
    assert captured["campaign_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"


def test_acknowledge_campaign_wires_party_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _acknowledge_campaign(**kwargs):
        captured.update(kwargs)
        return {
            "acknowledgement_id": "ack-1",
            "newsletter_id": None,
            "campaign_id": kwargs["campaign_id"],
            "party_id": kwargs["payload"].party_id,
            "acknowledgement_type": kwargs["payload"].acknowledgement_type,
            "response_text": kwargs["payload"].response_text,
            "acknowledged_at": datetime.now(timezone.utc),
        }

    monkeypatch.setattr("routers.communications_campaigns.communications_campaigns_service.acknowledge_campaign", _acknowledge_campaign)

    response = client.post(
        "/api/communications/campaigns/20d937ec-4176-4617-b63c-e76fc7a032c0/acknowledgements",
        json={
            "party_id": "b4486cac-2e5f-4f75-a1c6-2e46964f6d8b",
            "acknowledgement_type": "seen",
            "response_text": "Received",
        },
    )

    assert response.status_code == 201
    assert captured["campaign_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"
    assert captured["payload"].party_id == "b4486cac-2e5f-4f75-a1c6-2e46964f6d8b"
