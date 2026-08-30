"""
Notification-related Pydantic models.

Contains models for creating and managing notifications (email, SMS, WhatsApp).
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, List, Optional

# Pydantic v2: pattern constraint must be on the element type, not the List field.
_Channel = Annotated[str, Field(pattern="^(email|sms|whatsapp|in_app)$")]


class NotificationCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=5000)
    notification_type: str = Field(..., pattern="^(levy_reminder|announcement|maintenance|general)$")
    channels: List[_Channel] = Field(default_factory=lambda: ["email"], max_length=5)
    recipients: List[str] = Field(default_factory=list, max_length=500)  # user IDs or "all", "owners", "tenants"
    scheduled_date: Optional[str] = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    message: str
    notification_type: str
    channels: List[str]
    recipients: List[str]
    scheduled_date: Optional[str]
    status: str  # pending, sent, failed
    sent_count: int
    failed_count: int
    created_by: str
    created_at: str
    sent_at: Optional[str]


class UserNotification(BaseModel):
    """Notification for an individual user, shown on the bell icon."""
    id: str
    user_id: str
    title: str
    message: str
    type: str  # chat, maintenance, announcement, notice, levy, etc.
    link: Optional[str] = None
    is_read: bool = False
    created_at: str


class UserNotificationResponse(UserNotification):
    model_config = ConfigDict(extra="ignore")


__all__ = [
    "NotificationCreate",
    "NotificationResponse",
    "UserNotification",
    "UserNotificationResponse",
]
