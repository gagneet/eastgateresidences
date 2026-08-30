from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models.ai_review import BuildingAssessmentRunRequest
from services.ai_review_service import _ensure_recommendation_action_allowed, _require_policy_roles


def test_building_assessment_request_rejects_subject_overrides() -> None:
    with pytest.raises(ValidationError):
        BuildingAssessmentRunRequest(subject_type="lot")

    with pytest.raises(ValidationError):
        BuildingAssessmentRunRequest(subject_id="external-id")


def test_require_policy_roles_rejects_missing_or_empty_policy() -> None:
    with pytest.raises(HTTPException, match="Approval policy is missing"):
        _require_policy_roles(None, detail="Approval policy is missing")

    with pytest.raises(HTTPException, match="Approval policy is missing"):
        _require_policy_roles({"policy_code": "x", "required_roles": []}, detail="Approval policy is missing")


def test_recommendation_actions_require_pending_review() -> None:
    _ensure_recommendation_action_allowed({"status": "pending_review"}, allowed_statuses={"pending_review"}, action_label="accepted")

    with pytest.raises(HTTPException, match="cannot be accepted from status 'approved'"):
        _ensure_recommendation_action_allowed(
            {"status": "approved"},
            allowed_statuses={"pending_review"},
            action_label="accepted",
        )
