"""Tests for remaining portfolio onboarding helpers."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BUILDING_ID = "test-building-s3-001"
ORG_ID = "org-test-s3-001"


def test_onboarding_template_loads():
    """The onboarding template loads and has 12 steps."""
    import json
    from pathlib import Path

    path = Path("backend/data/onboarding_template.json")
    with open(path) as f:
        template = json.load(f)
    assert len(template["steps"]) == 12
    required_steps = [s for s in template["steps"] if s["required"]]
    assert len(required_steps) >= 11  # at least 11 required


@pytest.mark.asyncio
async def test_step_completion_recorded_with_timestamp():
    existing = {
        "id": "chk-002",
        "building_id": BUILDING_ID,
        "status": "in_progress",
        "steps": [
            {
                "id": "building_metadata",
                "name": "Building metadata",
                "completed": False,
                "required": True,
            }
        ],
    }
    mock_db = MagicMock()
    mock_db._db.building_onboarding_checklists.find_one = AsyncMock(return_value=existing)
    mock_db._db.building_onboarding_checklists.update_one = AsyncMock()
    with patch("routers.portfolio.db", mock_db), patch(
            "routers.portfolio.create_audit_log", new_callable=AsyncMock
    ):
        from routers.portfolio import complete_onboarding_step
        user = {"id": "user-001", "role": "strata_manager"}

        from pydantic import BaseModel

        class Req(BaseModel):
            notes: str = ""

        result = await complete_onboarding_step(
            BUILDING_ID, "building_metadata", Req(), current_user=user
        )
    assert result["completed"] is True
    assert result["completed_at"] is not None


@pytest.mark.asyncio
async def test_go_live_validation_structure():
    """Go-live validation returns checks list with all_passed field."""
    mock_db = MagicMock()
    # Checks are filtered on the PATH building_id via db._db, not on the session
    # building via the TenantScopedDatabase wrapper.
    mock_db._db.ec_members.count_documents = AsyncMock(return_value=3)
    mock_db._db.units.count_documents = AsyncMock(side_effect=[87, 87])
    mock_db._db.document_folders.count_documents = AsyncMock(return_value=6)
    mock_db._db.memberships.distinct = AsyncMock(return_value=["u-1"])
    mock_db._db.users.count_documents = AsyncMock(return_value=87)
    mock_db._db.building_onboarding_checklists.find_one = AsyncMock(
        return_value={
            "steps": [
                {"id": "building_metadata", "required": True, "completed": True}
            ]
        }
    )
    with patch("routers.portfolio.db", mock_db):
        from routers.portfolio import validate_go_live
        user = {"id": "user-001", "role": "strata_manager"}
        result = await validate_go_live(BUILDING_ID, current_user=user)
    assert "checks" in result
    assert "all_passed" in result
    assert isinstance(result["checks"], list)
    assert len(result["checks"]) > 0
