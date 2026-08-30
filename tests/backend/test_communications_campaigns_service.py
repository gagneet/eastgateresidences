from services.communications_campaigns_service import (
    _campaign_edit_invalidates_approval,
    _campaign_requires_human_approval,
)


def test_campaign_requires_human_approval_for_explicit_flag():
    assert _campaign_requires_human_approval({"approval_required": True, "campaign_type": "maintenance_notice"}) is True


def test_campaign_requires_human_approval_for_guarded_types():
    assert _campaign_requires_human_approval({"approval_required": False, "campaign_type": "compliance_notice"}) is True
    assert _campaign_requires_human_approval({"approval_required": False, "campaign_type": "agm_notice"}) is True


def test_campaign_edit_invalidates_approval_for_reviewed_campaigns():
    assert _campaign_edit_invalidates_approval({"status": "approved"}, {"subject": "Changed"}) is True
    assert _campaign_edit_invalidates_approval({"status": "in_review"}, {"audience_segments": []}) is True
    assert _campaign_edit_invalidates_approval({"status": "draft"}, {"subject": "Changed"}) is False
