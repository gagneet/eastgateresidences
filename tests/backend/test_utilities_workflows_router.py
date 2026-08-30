from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import utils.permissions as permissions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.utilities_workflows import router as utilities_workflows_router
from utils.auth import get_current_building, get_current_user


def _build_app(monkeypatch, current_user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(utilities_workflows_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_current_building] = lambda: "UP-13195"
    monkeypatch.setattr(permissions, "get_effective_feature_access", AsyncMock(return_value=True))
    return TestClient(app)


def test_import_utility_bills_wires_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _import_utility_bills(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "imported_count": 1,
            "anomaly_count": 0,
            "bills": [
                {
                    "bill_id": "bill-1",
                    "lot_id": None,
                    "utility_type": "water",
                    "billing_period_start": date(2026, 4, 1),
                    "billing_period_end": date(2026, 4, 30),
                    "usage_quantity": 120.5,
                    "usage_unit": "kL",
                    "total_amount_cents": 55000,
                    "due_date": None,
                    "payment_status": "unpaid",
                    "provider_name": "Icon Water",
                    "provider_reference": "INV-1",
                    "document_id": None,
                    "receipt_id": None,
                    "created_by": "manager-1",
                    "updated_by": "manager-1",
                    "created_at": now,
                    "updated_at": now,
                    "reading_count": 1,
                    "readings": [],
                }
            ],
            "anomalies": [],
        }

    monkeypatch.setattr("routers.utilities_workflows.sustainability_service.import_utility_bills", _import_utility_bills)

    response = client.post(
        "/api/utilities/bills/import",
        json={
            "bills": [
                {
                    "utility_type": "water",
                    "billing_period_start": "2026-04-01",
                    "billing_period_end": "2026-04-30",
                    "usage_quantity": 120.5,
                    "usage_unit": "kL",
                    "total_amount_cents": 55000,
                    "provider_name": "Icon Water",
                    "provider_reference": "INV-1",
                    "readings": [
                        {
                            "reading_taken_at": "2026-04-30T00:00:00Z",
                            "reading_value": 120.5,
                            "reading_unit": "kL",
                        }
                    ],
                }
            ]
        },
    )

    assert response.status_code == 201
    assert captured["building_id"] == "UP-13195"
    assert captured["payload"].bills[0].utility_type == "water"


def test_import_utility_bills_rejects_building_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )

    response = client.post(
        "/api/utilities/bills/import",
        json={"building_id": "nope", "bills": []},
    )

    assert response.status_code == 422


def test_convert_anomaly_to_case_wires_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _convert_anomaly_to_case(**kwargs):
        captured.update(kwargs)
        return {
            "anomaly_id": kwargs["anomaly_id"],
            "case_id": "case-1",
            "case_status": "new",
            "anomaly_status": "triaged",
        }

    monkeypatch.setattr("routers.utilities_workflows.sustainability_service.convert_anomaly_to_case", _convert_anomaly_to_case)

    response = client.post("/api/utilities/anomalies/20d937ec-4176-4617-b63c-e76fc7a032c0/convert-to-case")

    assert response.status_code == 200
    assert captured["anomaly_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"


def test_run_sustainability_assessment_wires_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _run_sustainability_assessment(**kwargs):
        captured.update(kwargs)
        return {
            "assessment_id": "assessment-1",
            "profile_id": "profile-1",
            "overall_score": 62.5,
            "emissions_baseline_kg": 84250.0,
            "solar_readiness_level": "medium",
            "water_efficiency_level": "review_required",
            "lighting_efficiency_level": "low",
            "notes": "Generated",
            "recommendations": [],
        }

    monkeypatch.setattr("routers.utilities_workflows.sustainability_service.run_sustainability_assessment", _run_sustainability_assessment)

    response = client.post(
        "/api/sustainability/assessments/run",
        json={"notes": "Look at water and lighting"},
    )

    assert response.status_code == 201
    assert captured["payload"].notes == "Look at water and lighting"


def test_convert_recommendation_to_project_wires_id(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _convert_recommendation_to_project(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "project_id": "project-1",
            "profile_id": "profile-1",
            "project_type": "lighting_upgrade",
            "title": "LED Upgrade",
            "description": "Upgrade lights",
            "status": "draft",
            "capex_estimate_cents": 1850000,
            "payback_months": 26,
            "emissions_savings_kg": 12450.0,
            "created_by": "manager-1",
            "updated_by": "manager-1",
            "created_at": now,
            "updated_at": now,
            "approval_request_id": None,
            "approval_status": None,
        }

    monkeypatch.setattr("routers.utilities_workflows.sustainability_service.convert_recommendation_to_project", _convert_recommendation_to_project)

    response = client.post("/api/sustainability/recommendations/20d937ec-4176-4617-b63c-e76fc7a032c0/convert-to-project")

    assert response.status_code == 200
    assert captured["recommendation_id"] == "20d937ec-4176-4617-b63c-e76fc7a032c0"


def test_submit_project_approval_wires_payload(monkeypatch):
    client = _build_app(
        monkeypatch,
        {"id": "manager-1", "email": "manager@example.com", "role": "strata_manager", "full_name": "Manager"},
    )
    captured = {}

    async def _submit_project_approval(**kwargs):
        captured.update(kwargs)
        return {
            "approval_request_id": "approval-1",
            "project_id": kwargs["project_id"],
            "policy_code": "ai_recommendation_high_risk",
            "status": "pending",
            "requested_by": "manager-1",
            "requested_at": datetime.now(timezone.utc),
            "required_roles": ["ec_member"],
        }

    monkeypatch.setattr("routers.utilities_workflows.sustainability_service.submit_project_approval", _submit_project_approval)

    response = client.post(
        "/api/sustainability/projects/20d937ec-4176-4617-b63c-e76fc7a032c0/submit-approval",
        json={"policy_code": "ai_recommendation_high_risk", "note": "Needs committee review"},
    )

    assert response.status_code == 200
    assert captured["payload"].policy_code == "ai_recommendation_high_risk"
