"""
Communication-related Pydantic models.

Contains models for messages, announcements, conversations, and private messaging.
"""

# @featuretrace:resident-directory-chat — Conversation request and response schemas for directory direct messages.
# Layer: model
# Data flow: POST /conversations payload -> ConversationCreate -> ConversationResponse (building-scoped).
# Related: backend/routers/communication.py, frontend/src/pages/dashboard/ResidentDirectoryPage.tsx

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional


class MessageCreate(BaseModel):
    content: str = Field(..., max_length=1000)
    recipient_id: Optional[str] = None  # None for public chat
    is_private: bool = False


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    content: str
    sender_id: str
    sender_name: str
    recipient_id: Optional[str] = None
    is_private: bool
    created_at: str


class AnnouncementCreate(BaseModel):
    title: str = Field(..., max_length=200)
    content: str = Field(..., max_length=5000)
    priority: str = "normal"  # low, normal, high, urgent
    is_public: bool = False
    expires_at: Optional[str] = None
    target_roles: Optional[List[str]] = None
    target_users: Optional[List[str]] = None
    # GAP-CMS-006: lot/unit-level segmentation — None means all lots
    target_lots: Optional[List[str]] = Field(
        None,
        description="Restrict delivery to specific lot/unit numbers. None = all lots.",
    )


class AnnouncementUpdateExpiry(BaseModel):
    expires_at: Optional[str] = None


class AnnouncementResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    content: str
    priority: str
    is_public: bool
    created_by: str
    created_by_name: str
    expires_at: Optional[str] = None
    target_roles: Optional[List[str]] = None
    target_users: Optional[List[str]] = None
    target_lots: Optional[List[str]] = None
    history: List[dict] = []
    created_at: str


class ConversationCreate(BaseModel):
    name: Optional[str] = None  # For group chats
    is_group: bool = False
    member_ids: List[str] = Field(default_factory=list)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: Optional[str] = None
    is_group: bool
    members: List[dict]
    created_by: str
    last_message: Optional[dict] = None
    created_at: str
    updated_at: str


class PrivateMessageCreate(BaseModel):
    content: str = Field(..., max_length=1000)
    conversation_id: str


class PrivateMessageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    content: str
    conversation_id: str
    sender_id: str
    sender_name: str
    read_by: List[str]
    created_at: str


class NoticeCreate(BaseModel):
    """Official notice - more formal than announcements"""
    title: str = Field(..., max_length=200)
    content: str = Field(..., max_length=5000)
    category: str = "general"  # general, maintenance, financial, legal, meeting
    priority: str = "normal"  # low, normal, high, urgent
    is_pinned: bool = False
    requires_acknowledgment: bool = False
    target_roles: Optional[List[str]] = None  # None = all users, or specific roles
    # GAP-COMMS-002: parity with the retired Announcements UI broadcast targeting.
    target_lots: Optional[List[str]] = Field(
        None,
        description="Restrict delivery to specific lot/unit numbers. None = all lots.",
    )
    attachments: Optional[List[dict]] = None
    expires_at: Optional[str] = None


class NoticeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    content: str
    category: str
    priority: str
    is_pinned: bool
    requires_acknowledgment: bool
    target_roles: Optional[List[str]] = None
    target_lots: Optional[List[str]] = None
    attachments: Optional[List[dict]] = None
    created_by: str
    created_by_name: str
    expires_at: Optional[str] = None
    history: List[dict] = []
    created_at: str
    updated_at: str
    acknowledgment_count: int = 0
    comment_count: int = 0


class NoticeCommentCreate(BaseModel):
    """Comment/discussion on a notice"""
    content: str = Field(..., max_length=1000)
    parent_comment_id: Optional[str] = None  # For threaded replies


class NoticeCommentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    notice_id: str
    content: str
    author_id: str
    author_name: str
    parent_comment_id: Optional[str] = None
    created_at: str
    updated_at: str


class NoticeAcknowledgmentCreate(BaseModel):
    """User acknowledgment of a notice"""
    notice_id: str


class NoticeAcknowledgmentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    notice_id: str
    user_id: str
    user_name: str
    acknowledged_at: str


class EmailNotificationPreferences(BaseModel):
    """User preferences for email notifications"""
    notices_enabled: bool = True
    announcements_enabled: bool = True
    maintenance_updates_enabled: bool = True
    discussion_replies_enabled: bool = True
    levy_reminders_enabled: bool = True
    tax_reminders_enabled: bool = False
    water_reminders_enabled: bool = False
    agm_reminders_enabled: bool = False
    digest_frequency: str = "immediate"  # immediate, daily, weekly, never
    email_format: str = Field("html", pattern="^(html|plain_text)$")


class EmailNotificationPreferencesResponse(BaseModel):
    # Pydantic v2: fields without defaults are *required* on validation. DB docs written
    # before tax/water/agm fields were added will raise ValidationError on read without
    # defaults here — schema evolution requires matching defaults in all response models.
    model_config = ConfigDict(extra="ignore")
    user_id: str
    notices_enabled: bool = True
    announcements_enabled: bool = True
    maintenance_updates_enabled: bool = True
    discussion_replies_enabled: bool = True
    levy_reminders_enabled: bool = True
    tax_reminders_enabled: bool = False
    water_reminders_enabled: bool = False
    agm_reminders_enabled: bool = False
    digest_frequency: str = "immediate"
    email_format: str = "html"
    updated_at: str = ""


class ManualEmailRequest(BaseModel):
    """Request model for sending a manual email"""
    target_roles: Optional[List[str]] = None
    target_units: Optional[List[str]] = None
    target_users: Optional[List[str]] = None
    subject: str
    content: str  # HTML content from Tiptap
    priority: str = "normal"


__all__ = [
    "MessageCreate",
    "MessageResponse",
    "AnnouncementCreate",
    "AnnouncementResponse",
    "ConversationCreate",
    "ConversationResponse",
    "PrivateMessageCreate",
    "PrivateMessageResponse",
    "NoticeCreate",
    "NoticeResponse",
    "NoticeCommentCreate",
    "NoticeCommentResponse",
    "NoticeAcknowledgmentCreate",
    "NoticeAcknowledgmentResponse",
    "EmailNotificationPreferences",
    "EmailNotificationPreferencesResponse",
    "ManualEmailRequest",
]
