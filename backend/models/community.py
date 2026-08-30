"""
Community-related Pydantic models.

Contains models for meetings, todos, blog posts, EC members, emergency services,
AGM motions and voting, community events, and resident directory.
"""

import logging

from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional

_community_logger = logging.getLogger(__name__)


class MeetingCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    meeting_date: str = Field(..., max_length=50)
    location: str = Field(..., max_length=200)
    agenda: List[str] = Field(default_factory=list, max_length=100)
    attendees: List[str] = Field(default_factory=list, max_length=500)


class MeetingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    meeting_date: str
    location: str
    agenda: List[str]
    attendees: List[str]
    minutes: Optional[str] = None
    status: str
    created_by: str
    created_at: str
    updated_at: str


class TodoCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    assigned_to: Optional[str] = Field(None, max_length=100)
    due_date: Optional[str] = Field(None, max_length=50)
    priority: str = Field("normal", max_length=50)
    meeting_id: Optional[str] = Field(None, max_length=100)


class TodoResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    due_date: Optional[str] = None
    priority: str
    status: str
    meeting_id: Optional[str] = None
    created_by: str
    created_at: str
    updated_at: str


class BlogPostCreate(BaseModel):
    title: str = Field(..., max_length=200)
    content: str = Field(..., max_length=10000)
    excerpt: Optional[str] = Field(None, max_length=500)
    cover_image: Optional[str] = Field(None, max_length=1000)
    tags: List[str] = Field(default_factory=list, max_length=50)
    is_published: bool = True
    is_public: bool = True
    scope: str = Field("building", pattern="^(building|global)$")


class BlogPostResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    content: str
    excerpt: Optional[str] = None
    cover_image: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    is_published: bool
    is_public: bool = True
    author_id: str
    author_name: str
    views: int
    created_at: str
    updated_at: str
    scope: str = "building"
    building_id: Optional[str] = None


class ECMemberCreate(BaseModel):
    name: str = Field(..., max_length=100)
    position: str = Field(..., max_length=100)
    bio: Optional[str] = Field(None, max_length=2000)
    image: Optional[str] = Field(None, max_length=1000)
    email: Optional[str] = Field(None, max_length=254)
    phone: Optional[str] = Field(None, max_length=20)
    order: int = 0


class ECMemberResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    position: str
    bio: Optional[str] = None
    image: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    order: int
    created_at: str


class EmergencyServiceCreate(BaseModel):
    name: str = Field(..., max_length=100)
    category: str = Field(..., max_length=100)  # fire, police, medical, utility, building, management
    phone: str = Field(..., max_length=20)
    description: Optional[str] = Field(None, max_length=2000)
    address: Optional[str] = Field(None, max_length=200)
    is_24_7: bool = False
    is_private: bool = False
    order: int = 0


class EmergencyServiceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    category: str
    phone: str
    description: Optional[str] = None
    address: Optional[str] = None
    is_24_7: bool
    is_private: bool = False
    order: int
    created_at: str


class AGMMotionCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    motion_type: str = Field(..., max_length=50)  # ordinary, special, unanimous
    agm_id: str = Field(..., max_length=100)


class AGMMotionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    agm_id: str
    title: str
    description: str
    motion_type: str
    votes_for: int = 0
    votes_against: int = 0
    votes_abstain: int = 0
    status: str = "pending"  # pending, passed, failed
    voters: List[str] = []
    created_at: str


class AGMVoteCreate(BaseModel):
    motion_id: str
    vote: str  # for, against, abstain
    proxy_for: Optional[str] = None  # unit number if voting as proxy
    is_pre_vote: bool = False


class AGMAttendanceUpdate(BaseModel):
    user_id: str
    status: str  # attending, apology
    proxy_id: Optional[str] = None  # User ID of nominated proxy


class AGMCreate(BaseModel):
    title: str = Field(..., max_length=200)
    date: str = Field(..., max_length=50)
    location: str = Field(..., max_length=200)
    agenda: List[str] = Field(default_factory=list, max_length=100)


class AGMResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    date: str
    location: str
    agenda: List[str]
    status: str = "upcoming"  # upcoming, in_progress, completed
    motions: List[dict] = []
    created_at: str


def coalesce_agm_response(doc: dict) -> Optional["AGMResponse"]:
    """Build an AGMResponse from a possibly-legacy/dirty ``db.agm`` document.

    AGMResponse declares agenda/date/location/created_at as required, non-optional
    fields. A stored doc with any of them null (e.g. a scraped/legacy AGM with
    ``agenda: null``) therefore raised ResponseValidationError and 500'd the whole
    ``GET /agm`` list — the null agenda list also surfaced client-side as
    "can't access property 'length'". This coalesces the known-nullable fields to
    safe defaults and returns ``None`` (logged) for a doc that still can't be
    parsed (e.g. missing ``id``), so one dirty row never takes down the page.

    Shared by ``server.py``'s inline route and ``routers/meetings.py`` so the
    resilience lives in one place instead of being duplicated per handler.
    """
    d = dict(doc)
    if d.get("agenda") is None:
        d["agenda"] = []
    if d.get("motions") is None:
        d["motions"] = []
    for _k in ("title", "location", "date", "created_at"):
        if d.get(_k) is None:
            d[_k] = ""
    if not d.get("status"):
        d["status"] = "upcoming"
    try:
        return AGMResponse(**d)
    except Exception as exc:  # noqa: BLE001
        _community_logger.warning(
            "coalesce_agm_response: skipping malformed AGM doc id=%s: %s", d.get("id"), exc
        )
        return None


class CommunityEventCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    event_type: str = Field(..., max_length=50)  # agm, levy_due, community, maintenance, meeting, other
    start_date: str = Field(..., max_length=50)
    end_date: Optional[str] = Field(None, max_length=50)
    location: Optional[str] = Field(None, max_length=200)
    is_recurring: bool = False
    recurrence_rule: Optional[str] = Field(None, max_length=50)  # daily, weekly, monthly, quarterly, yearly
    source: str = Field("manual", max_length=100)  # manual, facebook, scraper
    source_url: Optional[str] = Field(None, max_length=1000)
    is_public: bool = True


class CommunityEventResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str]
    event_type: str
    start_date: str
    end_date: Optional[str]
    location: Optional[str]
    is_recurring: bool
    recurrence_rule: Optional[str]
    source: str
    source_url: Optional[str]
    is_public: bool
    created_by: str
    created_at: str


class ResidentDirectoryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str
    full_name: str
    unit_number: str
    unit_type: Optional[str]
    phone: Optional[str]
    email_visible: bool = False
    phone_visible: bool = False
    move_in_date: Optional[str]
    is_visible: bool = True  # Opt-in to directory
    entitlement_units: Optional[int]
    annual_levy: Optional[float]


__all__ = [
    "MeetingCreate",
    "MeetingResponse",
    "TodoCreate",
    "TodoResponse",
    "BlogPostCreate",
    "BlogPostResponse",
    "ECMemberCreate",
    "ECMemberResponse",
    "EmergencyServiceCreate",
    "EmergencyServiceResponse",
    "AGMMotionCreate",
    "AGMMotionResponse",
    "AGMVoteCreate",
    "AGMCreate",
    "AGMResponse",
    "CommunityEventCreate",
    "CommunityEventResponse",
    "ResidentDirectoryEntry",
]
