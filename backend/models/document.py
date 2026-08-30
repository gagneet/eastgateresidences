"""
Document-related Pydantic models.

Contains models for document management, categories, access levels, and permissions.
"""

from pydantic import BaseModel, ConfigDict
from typing import List, Optional


class DocumentAccess:
    ALL_MEMBERS = "all_members"
    OWNERS_VIEW = "owners_view"
    OWNERS_EDIT = "owners_edit"
    EC_VIEW = "ec_view"
    EC_EDIT = "ec_edit"
    CHAIRMAN_ONLY = "chairman_only"


class DocumentCategory:
    EC_DOCUMENTS = "ec_documents"
    PUBLIC_DOCUMENTS = "public_documents"
    MEETING_MINUTES = "meeting_minutes"
    FINANCIAL_REPORTS = "financial_reports"
    BYLAWS = "bylaws"
    NOTICES = "notices"


class DocumentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    category: str
    is_public: bool = False
    allowed_roles: List[str] = []


class DocumentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    category: str
    file_name: str
    file_type: str
    file_size: int
    file_data: Optional[str] = None
    is_public: bool
    allowed_roles: List[str]
    uploaded_by: str
    uploaded_by_name: str
    created_at: str
    updated_at: str
    is_test_data: bool = False


class DocumentPermissionUpdate(BaseModel):
    access_level: str  # all_members, owners_view, owners_edit, ec_view, ec_edit, chairman_only
    allowed_roles: List[str] = []
    allowed_users: List[str] = []  # specific user IDs


__all__ = [
    "DocumentAccess",
    "DocumentCategory",
    "DocumentCreate",
    "DocumentResponse",
    "DocumentPermissionUpdate",
]
