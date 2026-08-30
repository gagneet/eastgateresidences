# @featuretrace:community-hub — Community blog, EC members, events, directory (dead router — server.py canonical).
# Layer: router
# Data flow: frontend → GET/POST /blog + /ec-members + /events → db.blog_posts + db.ec_members + db.community_events (building-scoped; server.py is canonical)
# Related: backend/server.py (lines ~6454–12740)
#           frontend/src/pages/public/BlogPage.jsx
#           frontend/src/pages/dashboard/BlogManagementPage.jsx
# Toggle: blog_posts_enabled, community_hub
# Collection: blog_posts, ec_members, community_events, emergency_services
# Scope: (building-scoped)

"""DEAD ROUTER — AUDIT-9 / F-011 Phase B  (2026-05-24)
====================================================
All routes in this file are duplicated verbatim in server.py and are NOT
registered (there is no include_router call for this file in server.py).
This file is retained for historical reference only. Do NOT add new routes
here. When F-011 Phase B lands this file will be deleted and its server.py
counterparts refactored into a proper router.

Routes covered by server.py:
  POST/GET/GET/{id}/DELETE  /blog
  POST/GET/PUT/{id}/DELETE  /ec-members
  POST/GET/DELETE           /emergency-services
  GET/PUT                   /directory (settings)
  POST/GET/DELETE/{id}      /events
  POST                      /events/generate-levy-dates
  POST                      /events/scrape-community
"""

# Community router module.
#
# This module handles all community-related routes including blog posts,
# EC members, emergency services, resident directory, and community events.

import html as html_lib
import re
import uuid
from datetime import datetime, timezone

import nh3
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from config import SYSTEM_HIDDEN_EMAIL
from database import db
from models.community import (
    BlogPostCreate,
    BlogPostResponse,
    CommunityEventCreate,
    CommunityEventResponse,
    ECMemberCreate,
    ECMemberResponse,
    EmergencyServiceCreate,
    EmergencyServiceResponse,
)
from services.settings_service import get_general_settings_or_default
from utils.auth import (
    effective_role,
    get_approved_user,
    get_building_or_400,
    get_current_building,
    get_current_user,
    get_optional_building,
    get_optional_user,
    is_approved_user,
)
from utils.permissions import get_user_permissions, require_feature

# Create router
router = APIRouter(prefix="")


# ==================== BLOG ROUTES ====================


@router.post("/blog", response_model=BlogPostResponse)
async def create_blog_post(
        post: BlogPostCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new blog post.

    Requires announcement posting permissions. Creates a blog post with the
    current user as the author.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    post_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    post_data = post.model_dump()
    if post_data.get("title"):
        post_data["title"] = html_lib.escape(post_data["title"])
    if post_data.get("content"):
        post_data["content"] = nh3.clean(post_data["content"])
    if post_data.get("excerpt"):
        post_data["excerpt"] = nh3.clean(post_data["excerpt"])

    post_doc = {
        "id": post_id,
        "building_id": building_id,
        **post_data,
        "author_id": current_user["id"],
        "author_name": current_user["full_name"],
        "views": 0,
        "created_at": now,
        "updated_at": now,
    }

    await db.blog_posts.insert_one(post_doc)
    return BlogPostResponse(**post_doc)


@router.get("/blog", response_model=List[BlogPostResponse])
async def get_blog_posts(
        is_published: bool = True,
        scope: Optional[str] = None,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_building_or_400)
):
    """
    Get a list of blog posts.

    Can optionally filter by publication status.
    Users can select the Global or their own specific building,
    but only if they are logged in and approved, else they see only public posts.
    Global shows all public posts and building-specific posts for approved users.
    """
    base_query = {}
    if is_published:
        base_query["is_published"] = True

    # SECURITY: Only approved residents or staff can see private building content
    is_approved = is_approved_user(current_user)

    if not is_approved:
        # Unapproved/Anonymous see only public posts
        query = {**base_query, "is_public": True}
    elif scope == "global":
        # Global view for approved users
        query = {
            **base_query,
            "$or": [
                {"scope": "global"},
                {"building_id": building_id},
                {"is_public": True}
            ]
        }
    else:
        # Building specific view for approved users
        query = {**base_query, "building_id": building_id}

    posts = (
        await db.blog_posts.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(100)
    )
    return [BlogPostResponse(**p) for p in posts]


@router.get("/blog/{post_id}", response_model=BlogPostResponse)
async def get_blog_post(
        post_id: str,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_building_or_400)
):
    """
    Get a specific blog post by ID.

    Increments the view count when accessed.
    """
    # Use find_one with a query that allows cross-building access for public/global posts
    # by including building_id: {"$exists": True} to bypass TenantCollection auto-injection
    # if we want to support that. But wait, get_blog_post was already doing it via $or.
    # Actually, as noted before, $or doesn't prevent injection if building_id is not top-level.
    # We add an explicit building_id filter to signal TenantCollection to NOT inject its own.
    post_query = {
        "id": post_id,
        "building_id": {"$exists": True},  # Signals bypass of auto-injection
        "$or": [
            {"building_id": building_id},
            {"scope": "global"},
            {"is_public": True}
        ]
    }

    post = await db.blog_posts.find_one(post_query, {"_id": 0})

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # SECURITY: Verify access to private posts
    if not post.get("is_public", True):
        if not is_approved_user(current_user):
            raise HTTPException(status_code=403, detail="Not authorized to view this post")

        # Check building context if building scoped
        if post.get("scope") == "building" and post.get("building_id") != building_id:
            # Check if user has membership in that building
            # Super admins are always allowed
            if current_user.get("role") != "super_admin":
                membership = await db.memberships.find_one({
                    "user_id": current_user["id"],
                    "building_id": post["building_id"],
                    "is_active": True
                })
                if not membership:
                    raise HTTPException(status_code=403, detail="Not authorized to view this building's content")

    # Increment views
    await db.blog_posts.update_one({"id": post_id}, {"$inc": {"views": 1}})
    post["views"] += 1

    return BlogPostResponse(**post)


@router.delete("/blog/{post_id}")
async def delete_blog_post(
        post_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete a blog post.

    Requires announcement posting permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Verify ownership or admin
    post = await db.blog_posts.find_one({"id": post_id, "building_id": building_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if (post["author_id"] != current_user["id"] and
            effective_role(current_user) not in ["super_admin", "ec_member", "strata_admin"]):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this post"
        )

    result = await db.blog_posts.delete_one(
        {"id": post_id, "building_id": building_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Post not found")

    return {"message": "Post deleted successfully"}


# ==================== EC MEMBER ROUTES ====================


@router.post("/ec-members", response_model=ECMemberResponse)
async def create_ec_member(
        member: ECMemberCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new Executive Committee member entry.

    Requires user management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    member_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    member_data = member.model_dump()
    if member_data.get("name"):
        member_data["name"] = html_lib.escape(member_data["name"])
    if member_data.get("position"):
        member_data["position"] = html_lib.escape(member_data["position"])
    if member_data.get("bio"):
        member_data["bio"] = nh3.clean(member_data["bio"])

    member_doc = {
        "id": member_id,
        "building_id": building_id,
        **member_data,
        "created_at": now
    }

    await db.ec_members.insert_one(member_doc)
    return ECMemberResponse(**member_doc)


@router.get("/ec-members", response_model=List[ECMemberResponse])
async def get_ec_members(building_id: str = Depends(get_building_or_400)):
    """
    Get a list of all Executive Committee members.

    Returns members sorted by order field.
    """
    members = (
        await db.ec_members.find({"building_id": building_id}, {"_id": 0})
        .sort("order", 1)
        .to_list(100)
    )
    return [ECMemberResponse(**m) for m in members]


@router.put("/ec-members/{member_id}", response_model=ECMemberResponse)
async def update_ec_member(
        member_id: str,
        member: ECMemberCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update an Executive Committee member.

    Requires user management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    # SECURITY: Sanitize user input to prevent Stored XSS
    member_data = member.model_dump()
    member_data["name"] = html_lib.escape(member_data.get("name", ""))
    if member_data.get("name"):
        member_data["name"] = html_lib.escape(member_data["name"])
    if member_data.get("position"):
        member_data["position"] = html_lib.escape(member_data["position"])
        member_data["bio"] = nh3.clean(member_data["bio"])

    if member_data.get("email"):
        member_data["email"] = html_lib.escape(member_data["email"])
    if member_data.get("phone"):
        member_data["phone"] = html_lib.escape(member_data["phone"])
    if member_data.get("photo_url"):
        member_data["photo_url"] = html_lib.escape(member_data["photo_url"])
    await db.ec_members.update_one(
        {"id": member_id, "building_id": building_id},
        {"$set": member_data}
    )

    updated = await db.ec_members.find_one(
        {"id": member_id, "building_id": building_id},
        {"_id": 0}
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Member not found")

    return ECMemberResponse(**updated)


@router.delete("/ec-members/{member_id}")
async def delete_ec_member(
        member_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete an Executive Committee member.

    Requires user management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.ec_members.delete_one(
        {"id": member_id, "building_id": building_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Member not found")

    return {"message": "Member deleted successfully"}


# ==================== EMERGENCY SERVICES ROUTES ====================


@router.post("/emergency-services", response_model=EmergencyServiceResponse)
async def create_emergency_service(
        service: EmergencyServiceCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Add a new emergency service contact.

    Requires user management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    service_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    service_data = service.model_dump()
    if service_data.get("name"):
        service_data["name"] = html_lib.escape(service_data["name"])
    if service_data.get("description"):
        service_data["description"] = nh3.clean(service_data["description"])
    if service_data.get("address"):
        service_data["address"] = html_lib.escape(service_data["address"])

    service_doc = {
        "id": service_id,
        "building_id": building_id,
        **service_data,
        "created_at": now
    }

    await db.emergency_services.insert_one(service_doc)
    return EmergencyServiceResponse(**service_doc)


@router.get("/emergency-services", response_model=List[EmergencyServiceResponse])
async def get_emergency_services(
        category: Optional[str] = None,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_building_or_400)
):
    """
    Get a list of emergency services.

    Can optionally filter by category.
    Unauthenticated users only see non-private services.
    """
    query = {"building_id": building_id}
    if not current_user:
        query["is_private"] = False

    if category:
        query["category"] = category

    services = (
        await db.emergency_services.find(query, {"_id": 0})
        .sort("order", 1)
        .to_list(100)
    )
    return [EmergencyServiceResponse(**s) for s in services]


@router.delete("/emergency-services/{service_id}")
async def delete_emergency_service(
        service_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete an emergency service contact.

    Requires user management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.emergency_services.delete_one(
        {"id": service_id, "building_id": building_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Service not found")

    return {"message": "Service deleted successfully"}


# ==================== RESIDENT DIRECTORY ROUTES ====================


@router.get("/directory")
async def get_resident_directory(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature_check: dict = Depends(require_feature("directory")),
):
    """
    Get the resident directory. Scoped to building context.

    Returns only residents who have opted into the directory, with visibility
    settings respected for contact information.

    Performance Optimization [lightning]: Consolidated 2 database round-trips into 1 single
    aggregation pipeline and added strict building isolation via memberships.
    """
    pipeline = [
        # 1. Start with memberships for this building to ensure strict multi-tenant isolation
        {"$match": {"building_id": building_id, "is_active": True}},
        # 2. Join with user profiles
        {
            "$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "id",
                "as": "user_info",
            }
        },
        {"$unwind": "$user_info"},
        # 3. Filter for active users who opted into the directory
        {
            "$match": {
                "user_info.directory_visible": True,
                "user_info.is_active": True,
                "user_info.email": {"$ne": SYSTEM_HIDDEN_EMAIL},
            }
        },
        # 4. Join with unit details (filtered by building_id for security)
        {
            "$lookup": {
                "from": "units",
                "let": {"u_num": "$user_info.unit_number"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$unit_number", "$$u_num"]},
                                    {"$eq": ["$building_id", building_id]},
                                ]
                            }
                        }
                    }
                ],
                "as": "unit_details",
            }
        },
        {"$addFields": {"unit": {"$arrayElemAt": ["$unit_details", 0]}}},
        # 5. Project fields according to ResidentDirectoryEntry model and visibility preferences
        {
            "$project": {
                "_id": 0,
                "id": "$user_info.id",
                "chat_user_id": "$user_info.id",
                "full_name": "$user_info.full_name",
                "building_id": building_id,
                "unit_number": "$user_info.unit_number",
                "unit_type": "$unit.unit_type",
                "move_in_date": "$user_info.move_in_date",
                "entitlement_units": {
                    "$ifNull": ["$unit.entitlement", "$unit.entitlement_units"]
                },
                "annual_levy": "$unit.annual_levy",
                "email": {
                    "$cond": [
                        "$user_info.email_visible",
                        "$user_info.email",
                        "$$REMOVE",
                    ]
                },
                "phone": {
                    "$cond": [
                        "$user_info.phone_visible",
                        "$user_info.phone",
                        "$$REMOVE",
                    ]
                },
            }
        },
        # 6. Sort by unit number
        {"$sort": {"unit_number": 1}},
    ]

    directory = await db.memberships.aggregate(pipeline).to_list(1000)
    return directory


@router.put("/directory/settings")
async def update_directory_settings(
        directory_visible: bool = True,
        email_visible: bool = False,
        phone_visible: bool = False,
        move_in_date: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
):
    """
    Update current user's directory visibility settings.

    Controls whether the user appears in the directory and what contact
    information is visible.
    """
    update_dict = {
        "directory_visible": directory_visible,
        "email_visible": email_visible,
        "phone_visible": phone_visible,
    }
    if move_in_date:
        update_dict["move_in_date"] = move_in_date

    await db.users.update_one({"id": current_user["id"]}, {"$set": update_dict})
    return {"message": "Directory settings updated"}


# ==================== COMMUNITY EVENTS ROUTES ====================


@router.post("/events", response_model=CommunityEventResponse)
async def create_event(
        data: CommunityEventCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new community event.

    Requires meeting management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    event_data = data.model_dump()
    if event_data.get("title"):
        event_data["title"] = html_lib.escape(event_data["title"])
    if event_data.get("description"):
        event_data["description"] = nh3.clean(event_data["description"])
    if event_data.get("location"):
        event_data["location"] = html_lib.escape(event_data["location"])

    event_doc = {
        "id": event_id,
        "building_id": building_id,
        **event_data,
        "created_by": current_user["id"],
        "created_at": now,
    }

    await db.events.insert_one(event_doc)
    return CommunityEventResponse(**event_doc)


@router.get("/events", response_model=List[CommunityEventResponse])
async def get_events(
        month: Optional[str] = None,
        event_type: Optional[str] = None,
        current_user: dict = Depends(require_feature("events")),
        building_id: str = Depends(get_current_building)
):
    """
    Get a list of community events.

    Can filter by month (YYYY-MM format) and event type.
    """
    query = {"building_id": building_id}
    if month:
        query["start_date"] = {"$regex": f"^{re.escape(month)}"}
    if event_type:
        query["event_type"] = event_type

    events = await db.events.find(query, {"_id": 0}).sort("start_date", 1).to_list(200)
    return [CommunityEventResponse(**e) for e in events]


@router.delete("/events/{event_id}")
async def delete_event(
        event_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete a community event.

    Requires meeting management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.events.delete_one({"id": event_id, "building_id": building_id})
    return {"message": "Event deleted"}


@router.post("/events/generate-levy-dates")
async def generate_levy_due_dates(
        year: int = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Auto-generate levy due date events for a calendar year.

    Reads levy schedule from building settings. Upserts: deletes stale levy
    events for the year then re-inserts with dates from current settings.
    Requires meeting management permissions.
    """
    import calendar as cal_mod

    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not year:
        year = datetime.now().year

    settings_doc = await get_general_settings_or_default(building_id, {"_id": 0})
    levy_months = settings_doc.get("levy_due_months", [3, 6, 9, 12]) if settings_doc else [3, 6, 9, 12]
    levy_due_day_type = settings_doc.get("levy_due_day_type", "first") if settings_doc else "first"
    levy_due_day = settings_doc.get("levy_due_day") if settings_doc else None
    levy_due_custom_dates = (settings_doc.get("levy_due_custom_dates") or {}) if settings_doc else {}

    levy_dates = []
    for idx, month in enumerate(sorted(levy_months)):
        last_day = cal_mod.monthrange(year, month)[1]
        if levy_due_day_type == "first":
            day = 1
        elif levy_due_day_type == "middle":
            day = 15
        elif levy_due_day_type == "last":
            day = last_day
        elif levy_due_day_type == "custom":
            m_str = str(month)
            day = min(int(levy_due_custom_dates[m_str]), last_day) if m_str in levy_due_custom_dates else min(
                levy_due_day or 1, last_day)
        else:
            day = min(levy_due_day or 1, last_day)
        levy_dates.append({
            "quarter": f"Q{idx + 1}",
            "date": f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}",
            "title": f"Q{idx + 1} Levy Due - FY {year}-{year + 1}",
        })

    # Upsert: delete stale levy events for this year then re-insert
    fy_suffix = f"FY {year}-{year + 1}"
    await db.events.delete_many({
        "building_id": building_id,
        "event_type": "levy_due",
        "title": {"$regex": fy_suffix},
    })
    now_iso = datetime.now(timezone.utc).isoformat()

    # Performance Optimization [lightning]: Parallelize event inserts while maintaining individual error granularity.
    # Using asyncio.gather reduces cumulative I/O wait time from O(N) to O(1) concurrent requests.
    async def _insert_levy_event(levy_item):
        """Generated function header.

        Function: _insert_levy_event
        Path: backend/routers/community.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        await db.events.insert_one({
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "title": levy_item["title"],
            "description": f"Quarterly strata levy payment due for {levy_item['quarter']}",
            "event_type": "levy_due",
            "start_date": levy_item["date"],
            "end_date": None,
            "location": None,
            "is_recurring": True,
            "recurrence_rule": "yearly",
            "source": "system",
            "source_url": None,
            "is_public": True,
            "created_by": current_user["id"],
            "created_at": now_iso,
        })

    results = await asyncio.gather(*[_insert_levy_event(l) for l in levy_dates], return_exceptions=True)

    error_count = 0
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            error_count += 1
            logger.error(f"Failed to generate levy event '{levy_dates[i]['title']}': {res}")

    return {"message": f"Generated {len(levy_dates) - error_count} levy due date events for FY {year}-{year + 1}"}


@router.post("/events/scrape-community")
async def scrape_community_events(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Scrape community events from local Canberra pages.

    Placeholder for future implementation of web scraping functionality.
    Requires meeting management permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Import httpx only when needed (optional dependency)
    try:
        import httpx  # noqa: F401
    except ImportError:
        return {
            "message": "Web scraping not available",
            "note": "httpx library not installed. Install it to enable web scraping.",
            "sources_checked": [],
        }

    events_created = 0
    sources = [
        {
            "name": "Molonglo Valley Community",
            "url": "https://www.communitycouncil.com.au/molonglo",
        },
        {"name": "Denman Prospect", "url": "https://www.denmanprospect.com.au/events"},
    ]

    # Note: Full scraping would require proper web scraping implementation
    # For now, return a placeholder response
    return {
        "message": f"Scraped {events_created} events from community sources",
        "note": "Facebook integration requires Graph API setup. Contact admin for configuration.",
        "sources_checked": [s["name"] for s in sources],
    }


__all__ = [
    "router",
]
