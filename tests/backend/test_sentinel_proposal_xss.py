import pytest
from pydantic import ValidationError
from models.community_os import ProposalStatusUpdate
from routers.proposals import update_proposal_status
from unittest.mock import AsyncMock, patch
import html

def test_proposal_status_update_model_max_length():
    # Valid length
    valid_update = ProposalStatusUpdate(status="open", outcome_notes="Valid notes")
    assert valid_update.outcome_notes == "Valid notes"

    # Exceeding max length (2000 chars)
    long_notes = "a" * 2001
    with pytest.raises(ValidationError):
        ProposalStatusUpdate(status="open", outcome_notes=long_notes)

@pytest.mark.asyncio
async def test_update_proposal_status_escapes_xss():
    proposal_id = "test-prop-123"
    xss_payload = "<script>alert('XSS')</script> Notes with <b>HTML</b>"
    update_data = ProposalStatusUpdate(status="passed", outcome_notes=xss_payload)

    fake_proposal = {
        "id": proposal_id,
        "status": "open",
        "title": "Test Proposal",
        "description": "Desc",
        "proposal_number": "PROP-2026-0001",
        "year": 2026,
        "proposal_type": "expenditure",
        "voting_type": "simple_majority",
        "created_by": "user-1",
        "created_by_name": "Test User",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    mock_db = AsyncMock()
    mock_db.proposals.find_one = AsyncMock(return_value=fake_proposal)
    mock_db.proposals.update_one = AsyncMock()

    current_user = {
        "id": "admin-1",
        "full_name": "Admin User",
        "role": "super_admin",
        "effective_role": "super_admin",
    }

    with patch("routers.proposals.db", mock_db), patch("routers.proposals.create_audit_log", AsyncMock()):
        response = await update_proposal_status(
            proposal_id=proposal_id,
            data=update_data,
            current_user=current_user,
            building_id="building-1",
        )

    # Verify update payload passed to MongoDB contains escaped outcome_notes
    mock_db.proposals.update_one.assert_called_once()
    call_args = mock_db.proposals.update_one.call_args
    update_dict = call_args[0][1]["$set"]

    expected_escaped = html.escape(xss_payload)
    assert update_dict["outcome_notes"] == expected_escaped
    assert response.outcome_notes == expected_escaped
