"""PG-backed communications campaigns, newsletters, and delivery tracking APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from models.communications_campaigns import (
    CommunicationAcknowledgementCreate,
    CommunicationAcknowledgementResponse,
    CommunicationApprovalRequest,
    CommunicationApprovalResponse,
    CommunicationCampaignCreate,
    CommunicationCampaignDetailResponse,
    CommunicationCampaignResponse,
    CommunicationCampaignUpdate,
    CommunicationDeliveryEventCreate,
    CommunicationDeliveryEventResponse,
    CommunicationDraftCreate,
    CommunicationDraftResponse,
    CommunicationPreviewResponse,
    CommunicationScheduleRequest,
    CommunicationSendResponse,
    NewsletterCreate,
    NewsletterDetailResponse,
    NewsletterResponse,
)
from services import communications_campaigns_service
from utils.auth import get_current_building
from utils.permissions import require_feature
from utils.request_metadata import request_audit_metadata

router = APIRouter(prefix="/communications", tags=["Communications Campaigns"])



@router.get("/drafts", response_model=list[CommunicationDraftResponse])
async def list_drafts(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: list_drafts
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.list_drafts(building_id=building_id, current_user=current_user)


@router.post("/drafts", response_model=CommunicationDraftResponse, status_code=201)
async def create_draft(
    payload: CommunicationDraftCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: create_draft
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.create_draft(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/drafts/{draft_id}", response_model=CommunicationDraftResponse)
async def get_draft(
    draft_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: get_draft
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.get_draft(
        building_id=building_id,
        draft_id=draft_id,
        current_user=current_user,
    )


@router.get("/newsletters", response_model=list[NewsletterResponse])
async def list_newsletters(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: list_newsletters
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.list_newsletters(building_id=building_id, current_user=current_user)


@router.post("/newsletters", response_model=NewsletterDetailResponse, status_code=201)
async def create_newsletter(
    payload: NewsletterCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: create_newsletter
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.create_newsletter(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/newsletters/{newsletter_id}", response_model=NewsletterDetailResponse)
async def get_newsletter(
    newsletter_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: get_newsletter
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.get_newsletter(
        building_id=building_id,
        newsletter_id=newsletter_id,
        current_user=current_user,
    )


@router.post("/newsletters/{newsletter_id}/request-approval", response_model=CommunicationApprovalResponse)
async def request_newsletter_approval(
    newsletter_id: str,
    payload: CommunicationApprovalRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: request_newsletter_approval
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.request_newsletter_approval(
        building_id=building_id,
        newsletter_id=newsletter_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/newsletters/{newsletter_id}/schedule", response_model=NewsletterDetailResponse)
async def schedule_newsletter(
    newsletter_id: str,
    payload: CommunicationScheduleRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: schedule_newsletter
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.schedule_newsletter(
        building_id=building_id,
        newsletter_id=newsletter_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/newsletters/{newsletter_id}/delivery-events", response_model=list[CommunicationDeliveryEventResponse])
async def list_newsletter_delivery_events(
    newsletter_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: list_newsletter_delivery_events
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.list_newsletter_delivery_events(
        building_id=building_id,
        newsletter_id=newsletter_id,
        current_user=current_user,
    )


@router.post("/newsletters/{newsletter_id}/delivery-events", response_model=CommunicationDeliveryEventResponse, status_code=201)
async def record_newsletter_delivery_event(
    newsletter_id: str,
    payload: CommunicationDeliveryEventCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: record_newsletter_delivery_event
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.record_newsletter_delivery_event(
        building_id=building_id,
        newsletter_id=newsletter_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/newsletters/{newsletter_id}/acknowledgements", response_model=list[CommunicationAcknowledgementResponse])
async def list_newsletter_acknowledgements(
    newsletter_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: list_newsletter_acknowledgements
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.list_newsletter_acknowledgements(
        building_id=building_id,
        newsletter_id=newsletter_id,
        current_user=current_user,
    )


@router.post("/newsletters/{newsletter_id}/acknowledgements", response_model=CommunicationAcknowledgementResponse, status_code=201)
async def acknowledge_newsletter(
    newsletter_id: str,
    payload: CommunicationAcknowledgementCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: acknowledge_newsletter
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.acknowledge_newsletter(
        building_id=building_id,
        newsletter_id=newsletter_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/campaigns", response_model=list[CommunicationCampaignResponse])
async def list_campaigns(
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: list_campaigns
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.list_campaigns(building_id=building_id, current_user=current_user)


@router.post("/campaigns", response_model=CommunicationCampaignDetailResponse, status_code=201)
async def create_campaign(
    payload: CommunicationCampaignCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: create_campaign
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.create_campaign(
        building_id=building_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/campaigns/{campaign_id}", response_model=CommunicationCampaignDetailResponse)
async def get_campaign(
    campaign_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: get_campaign
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.get_campaign(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
    )


@router.patch("/campaigns/{campaign_id}", response_model=CommunicationCampaignDetailResponse)
async def update_campaign(
    campaign_id: str,
    payload: CommunicationCampaignUpdate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: update_campaign
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.update_campaign(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/campaigns/{campaign_id}/preview", response_model=CommunicationPreviewResponse)
async def preview_campaign(
    campaign_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: preview_campaign
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.preview_campaign(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
    )


@router.post("/campaigns/{campaign_id}/submit-approval", response_model=CommunicationApprovalResponse)
async def submit_campaign_approval(
    campaign_id: str,
    payload: CommunicationApprovalRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: submit_campaign_approval
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.request_campaign_approval(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/campaigns/{campaign_id}/request-approval", response_model=CommunicationApprovalResponse, include_in_schema=False)
async def request_campaign_approval_alias(
    campaign_id: str,
    payload: CommunicationApprovalRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: request_campaign_approval_alias
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.request_campaign_approval(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/campaigns/{campaign_id}/schedule", response_model=CommunicationCampaignDetailResponse)
async def schedule_campaign(
    campaign_id: str,
    payload: CommunicationScheduleRequest,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: schedule_campaign
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.schedule_campaign(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/campaigns/{campaign_id}/send", response_model=CommunicationSendResponse)
async def send_campaign(
    campaign_id: str,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: send_campaign
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.send_campaign(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/campaigns/{campaign_id}/delivery", response_model=list[CommunicationDeliveryEventResponse])
async def list_campaign_delivery(
    campaign_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: list_campaign_delivery
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.list_campaign_delivery_events(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
    )


@router.get("/campaigns/{campaign_id}/delivery-events", response_model=list[CommunicationDeliveryEventResponse], include_in_schema=False)
async def list_campaign_delivery_events_alias(
    campaign_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: list_campaign_delivery_events_alias
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.list_campaign_delivery_events(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
    )


@router.post("/campaigns/{campaign_id}/delivery-events", response_model=CommunicationDeliveryEventResponse, status_code=201)
async def record_campaign_delivery_event(
    campaign_id: str,
    payload: CommunicationDeliveryEventCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: record_campaign_delivery_event
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.record_campaign_delivery_event(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/campaigns/{campaign_id}/acknowledgements", response_model=list[CommunicationAcknowledgementResponse])
async def list_campaign_acknowledgements(
    campaign_id: str,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: list_campaign_acknowledgements
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return await communications_campaigns_service.list_campaign_acknowledgements(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
    )


@router.post("/campaigns/{campaign_id}/acknowledgements", response_model=CommunicationAcknowledgementResponse, status_code=201)
async def acknowledge_campaign(
    campaign_id: str,
    payload: CommunicationAcknowledgementCreate,
    request: Request,
    building_id: str = Depends(get_current_building),
    current_user: dict = Depends(require_feature("communications_campaigns_pg_enabled")),
):
    """Generated function header.

    Function: acknowledge_campaign
    Path: backend/routers/communications_campaigns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    correlation_id, ip_address, user_agent = request_audit_metadata(request)
    return await communications_campaigns_service.acknowledge_campaign(
        building_id=building_id,
        campaign_id=campaign_id,
        current_user=current_user,
        payload=payload,
        correlation_id=correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
