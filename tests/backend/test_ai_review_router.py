from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import utils.permissions as permissions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.ai_review import router as ai_review_router
from utils.auth import get_current_building, get_current_user


def _build_app(monkeypatch, current_user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(ai_review_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_current_building] = lambda: "UP-13195"
    monkeypatch.setattr(permissions, "get_effective_feature_access", AsyncMock(return_value=True))
    return TestClient(app)


def test_run_building_assessment_wires_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _run_building_assessment(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "assessment_id": "assessment-1",
            "case_id": None,
            "assessment_type": "building_health",
            "subject_type": "building",
            "subject_id": "scheme-1",
            "summary": "Generated summary",
            "status": "pending_review",
            "model_name": "strataos-heuristic-v1",
            "prompt_version": "phase2-building-health-v1",
            "risk_level": "medium",
            "confidence_score": 0.82,
            "pii_redacted": True,
            "approval_required": False,
            "approval_request_id": None,
            "approval_status": None,
            "created_by": "manager-1",
            "updated_by": "manager-1",
            "created_at": now,
            "updated_at": now,
            "risk_scores": [],
            "recommendations": [],
        }

    monkeypatch.setattr("routers.ai_review.ai_review_service.run_building_assessment", _run_building_assessment)

    response = client.post(
        "/api/ai/building-assessments/run",
        json={"assessment_type": "building_health", "summary_hint": "Focus on utilities"},
    )

    assert response.status_code == 201
    assert captured["building_id"] == "UP-13195"
    assert captured["payload"].summary_hint == "Focus on utilities"


def test_run_building_assessment_rejects_building_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )

    response = client.post(
        "/api/ai/building-assessments/run",
        json={"building_id": "should-not-be-accepted"},
    )

    assert response.status_code == 422


def test_list_recommendations_wires_filters(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _list_recommendations(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("routers.ai_review.ai_review_service.list_recommendations", _list_recommendations)

    response = client.get("/api/ai/recommendations?status=pending_review&recommendation_type=utility_investigation")

    assert response.status_code == 200
    assert captured["status_filter"] == "pending_review"
    assert captured["recommendation_type"] == "utility_investigation"


def test_convert_recommendation_to_case_wires_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _convert_recommendation_to_case(**kwargs):
        captured.update(kwargs)
        return {
            "recommendation_id": kwargs["recommendation_id"],
            "case_id": "case-1",
            "case_status": "new",
            "recommendation_status": "executed",
        }

    monkeypatch.setattr("routers.ai_review.ai_review_service.convert_recommendation_to_case", _convert_recommendation_to_case)

    response = client.post("/api/ai/recommendations/20d937ec-4176-4617-b63c-e76fc7a032c0/convert-to-case")

    assert response.status_code == 200
    assert captured["recommendation_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"


def test_request_recommendation_approval_wires_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _request_recommendation_approval(**kwargs):
        captured.update(kwargs)
        return {
            "approval_request_id": "approval-1",
            "recommendation_id": kwargs["recommendation_id"],
            "policy_code": "ai_recommendation_high_risk",
            "status": "pending",
            "requested_by": "manager-1",
            "requested_at": datetime.now(timezone.utc),
            "required_roles": ["ec_member"],
        }

    monkeypatch.setattr("routers.ai_review.ai_review_service.request_recommendation_approval", _request_recommendation_approval)

    response = client.post(
        "/api/ai/recommendations/20d937ec-4176-4617-b63c-e76fc7a032c0/request-approval",
        json={"policy_code": "ai_recommendation_high_risk", "note": "Needs EC review"},
    )

    assert response.status_code == 200
    assert captured["payload"].policy_code == "ai_recommendation_high_risk"


def test_get_recommendation_evidence_wires_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _get_recommendation_evidence(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("routers.ai_review.ai_review_service.get_recommendation_evidence", _get_recommendation_evidence)

    response = client.get("/api/ai/recommendations/20d937ec-4176-4617-b63c-e76fc7a032c0/evidence")

    assert response.status_code == 200
    assert captured["recommendation_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"
