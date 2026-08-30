"""
Chat Group Models for East Gate Residences
Supports group chats with role-based and unit-based access control
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict


class GroupMember(BaseModel):
    """Member of a chat group"""
    user_id: str
    full_name: str
    role: str = "member"  # member or admin
    joined_at: str


class GroupSettings(BaseModel):
    """Settings for a chat group"""
    allow_member_invite: bool = True
    member_approval_required: bool = False
    who_can_delete: List[str] = ["admin"]  # admin, chairman, group_admin, message_owner
    auto_join_criteria: Optional[Dict] = None  # {"roles": [...]} or {"unit_types": [...]}


class ChatGroupCreate(BaseModel):
    """Create a new chat group"""
    name: str = Field(..., max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    type: str = "custom"  # general, role_based, unit_based, custom
    settings: Optional[GroupSettings] = None
    initial_members: Optional[List[str]] = None  # user IDs


class ChatGroupUpdate(BaseModel):
    """Update chat group"""
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    settings: Optional[GroupSettings] = None


class ChatGroupResponse(BaseModel):
    """Chat group response"""
    id: str
    name: str
    description: Optional[str] = None
    type: str
    is_system: bool
    created_by: str
    created_at: str
    settings: GroupSettings
    members: List[GroupMember]
    member_count: int
    last_message: Optional[Dict] = None
    unread_count: int = 0
    archived: bool = False
    archived_at: Optional[str] = None
    archived_year: Optional[int] = None


class GroupMessageCreate(BaseModel):
    """Send a message to a group"""
    content: str = Field(..., max_length=1000)


class GroupMessageResponse(BaseModel):
    """Group message response"""
    id: str
    content: str
    group_id: str
    sender_id: str
    sender_name: str
    sender_role: Optional[str] = None
    created_at: str
    edited_at: Optional[str] = None
    deleted_at: Optional[str] = None
    deleted_by: Optional[str] = None
    is_deleted: bool = False


class AddGroupMemberRequest(BaseModel):
    """Add member to group"""
    user_id: str = Field(..., max_length=50)


__all__ = [
    "GroupMember",
    "GroupSettings",
    "ChatGroupCreate",
    "ChatGroupUpdate",
    "ChatGroupResponse",
    "GroupMessageCreate",
    "GroupMessageResponse",
    "AddGroupMemberRequest",
]
