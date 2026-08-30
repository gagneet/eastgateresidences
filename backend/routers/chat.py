# @featuretrace:chat — Resident group chat: create/manage groups, post messages, member management.
# Layer: router
# Data flow: ChatPage.jsx → GET/POST /chat-groups + /chat-groups/{id}/messages → db.chat_groups + db.group_messages (building-scoped)
# Related: backend/workers/notification_worker.py (new-message bell notifications)
#           frontend/src/pages/dashboard/ChatPage.jsx
#           tests/backend/test_chat.py
# Toggle: chat_groups
# Collection: chat_groups, group_messages
"""
Chat Group Routes — Resident group messaging within a building.
Implements group chat functionality with default system groups per building.

Registered in server.py via:
  from routers.chat import router as chat_router, ...
"""
import html as html_lib
import re
import uuid
from datetime import datetime, timezone

import asyncio
import logging
import nh3
import nh3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from typing import List, Optional

from database import db
from models.chat import (
    ChatGroupCreate,
    ChatGroupUpdate,
    ChatGroupResponse,
    GroupMessageCreate,
    GroupMessageResponse,
    AddGroupMemberRequest,
    GroupSettings,
)
from request_context import set_ctx_building_id
from utils.auth import get_approved_user, get_current_building, is_impersonating, effective_role, DEFAULT_BUILDING_ID
from utils.helpers import create_user_notification, create_audit_log, create_notifications_batch
from utils.permissions import get_user_permissions, require_feature

router = APIRouter()

# Logger
logger = logging.getLogger(__name__)


# ==================== HELPER FUNCTIONS ====================

async def check_group_membership(group_id: str, user_id: str) -> bool:
    """Check if user is a member of the group"""
    # Performance Optimization⚡: Using count_documents for efficient membership check
    count = await db.chat_groups.count_documents({"id": group_id, "members.user_id": user_id})
    return count > 0


async def check_group_admin(group_id: str, user_id: str) -> bool:
    """Check if user is an admin of the group"""
    # Performance Optimization⚡: Using count_documents with $elemMatch for efficient admin check
    count = await db.chat_groups.count_documents({
        "id": group_id,
        "members": {"$elemMatch": {"user_id": user_id, "role": "admin"}}
    })
    return count > 0


async def can_delete_message(group_id: str, message_id: str, user: dict) -> bool:
    """Check if user can delete a message"""
    group = await db.chat_groups.find_one({"id": group_id})
    if not group:
        return False

    message = await db.group_messages.find_one({"id": message_id})
    if not message:
        return False

    who_can_delete = group.get("settings", {}).get("who_can_delete", ["admin"])
    user_role = user.get("role", "")

    # Check permissions
    if "admin" in who_can_delete and user_role in ["super_admin"]:
        return True
    # NOTE: 'chairman' here is a chat_groups.settings.who_can_delete POLICY-TIER label
    # (seeded as ['admin', 'chairman', 'strata_admin']), not a user.role value — it is
    # never accompanied by 'ec_member' in seed data, so this is NOT the same class of bug
    # as a literal 'chairman' role-list entry (see rules/post-compact-critical.md) and must
    # NOT be collapsed into the 'ec_member' branch alone, or real chairmen lose delete access
    # on every group seeded with this policy.
    if ("chairman" in who_can_delete or "ec_member" in who_can_delete) and user_role in ["ec_member", "strata_admin", "super_admin"]:
        return True
    if "group_admin" in who_can_delete and await check_group_admin(group_id, user["id"]):
        return True
    if "message_owner" in who_can_delete and message["sender_id"] == user["id"]:
        return True

    return False


async def auto_join_groups(user: dict):
    """Auto-join user to applicable system groups"""
    user_id = user["id"]
    user_role = user.get("role", "")
    unit_number = user.get("unit_number", "")

    # Get all system groups where user is not already a member
    # Performance Optimization⚡: Filter out groups the user is already in and skip archived groups at the DB level.
    # This avoids fetching large member lists for groups the user is already in.
    system_groups = await db.chat_groups.find({
        "is_system": True,
        "archived": {"$ne": True},
        "members.user_id": {"$ne": user_id}
    }).to_list(100)

    for group in system_groups:
        criteria = group.get("settings", {}).get("auto_join_criteria", {})
        should_join = False

        # Check role-based criteria (EC Members group)
        if criteria.get("roles"):
            if user_role in criteria["roles"]:
                should_join = True

        # Check unit_pattern regex (Townhouse Residents, Apartment Residents)
        if criteria.get("unit_pattern") and unit_number:
            try:
                if re.match(criteria["unit_pattern"], unit_number):
                    should_join = True
            except re.error:
                pass

        # Legacy unit_types check (kept for backward compat)
        if criteria.get("unit_types"):
            if unit_number and unit_number.startswith("TH"):
                if "townhouse" in criteria["unit_types"]:
                    should_join = True
            elif unit_number and unit_number.startswith("UA"):
                if "apartment" in criteria["unit_types"]:
                    should_join = True

        # General group - everyone joins
        if group["type"] == "general":
            should_join = True

        # Add member if they should join
        if should_join:
            # Performance Optimization⚡: Use atomic check in update filter to prevent duplicate memberships
            # even if multiple tasks run concurrently.
            await db.chat_groups.update_one(
                {"_id": group["_id"], "members.user_id": {"$ne": user_id}},
                {
                    "$push": {
                        "members": {
                            "user_id": user_id,
                            "full_name": user["full_name"],
                            "role": "member",
                            "joined_at": datetime.now(timezone.utc).isoformat()
                        }
                    }
                }
            )


# ==================== GROUP MANAGEMENT ====================

@router.post("/chat-groups", response_model=ChatGroupResponse)
async def create_chat_group(
        group_data: ChatGroupCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("chat"))
):
    """
    Create a new chat group.

    Users can create custom groups and invite members.
    Only admins can create system groups.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_chat:
        raise HTTPException(status_code=403, detail="Not authorized to create chat groups")

    # Only admins can create system groups
    if group_data.type != "custom" and effective_role(current_user) not in ["super_admin", "ec_member", "strata_admin"]:
        raise HTTPException(status_code=403, detail="Only admins can create system groups")

    group_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Create initial members list (creator is always an admin)
    members = [{
        "user_id": current_user["id"],
        "full_name": current_user["full_name"],
        "role": "admin",
        "joined_at": now
    }]

    # Performance Optimization⚡: Batch fetch initial member details to eliminate N+1 query pattern.
    if group_data.initial_members:
        # Filter out current user and deduplicate
        other_member_ids = list(set(mid for mid in group_data.initial_members if mid != current_user["id"]))
        if other_member_ids:
            # SECURITY FIX (BOLA): Verify that all invited members belong to the current building
            # We use the memberships collection to ensure strict tenant isolation.
            member_count = await db.memberships.count_documents({
                "building_id": building_id,
                "user_id": {"$in": other_member_ids}
            })
            if member_count != len(other_member_ids):
                raise HTTPException(
                    status_code=400,
                    detail="One or more invited users do not belong to this building"
                )

            user_docs = await db.users.find(
                {"id": {"$in": other_member_ids}},
                {"id": 1, "full_name": 1, "_id": 0}
            ).to_list(len(other_member_ids))

            for u in user_docs:
                members.append({
                    "user_id": u["id"],
                    "full_name": u["full_name"],
                    "role": "member",
                    "joined_at": now
                })

    # SECURITY: Sanitize group metadata to prevent Stored XSS
    sanitized_name = html_lib.escape(group_data.name)
    sanitized_description = html_lib.escape(group_data.description) if group_data.description else None

    group_doc = {
        "id": group_id,
        "name": sanitized_name,
        "description": sanitized_description,
        "type": group_data.type,
        "is_system": False,
        "created_by": current_user["id"],
        "created_at": now,
        "settings": (group_data.settings or GroupSettings()).model_dump(),
        "members": members
    }

    await db.chat_groups.insert_one(group_doc)

    # Create audit log
    asyncio.create_task(create_audit_log(
        action="created",
        resource_type="chat_group",
        resource_id=group_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"name": group_data.name}
    ))

    # Notify invited members - Bolt ⚡: Batch notifications to reduce DB round-trips
    notif_data = [
        {
            "user_id": member["user_id"],
            "title": "Added to Group Chat",
            "message": f"You were added to the group chat: {group_data.name}",
            "type": "chat",
            "link": f"/community/chat?group={group_id}"
        }
        for member in members if member["user_id"] != current_user["id"]
    ]
    if notif_data:
        asyncio.create_task(create_notifications_batch(notif_data))

    return ChatGroupResponse(
        **group_doc,
        member_count=len(members),
        last_message=None,
        unread_count=0
    )


@router.get("/chat-groups", response_model=List[ChatGroupResponse])
async def get_chat_groups(
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("chat"))
):
    """
    Get all chat groups the user is a member of.

    Returns groups with member count and last message info.
    Auto-joins user to applicable system groups if not already a member.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_chat:
        raise HTTPException(status_code=403, detail="Not authorized to view chat groups")

    # Auto-join user to applicable system groups (General, EC Members, Townhouse)
    # This ensures users are automatically added to groups they're eligible for.
    # Performance Optimization⚡: Offloaded to background task to reduce latency of the chat list endpoint.
    # We use FastAPI BackgroundTasks for proper task handling and wrap it in a safe handler.
    async def _safe_auto_join():
        """Generated function header.

        Function: _safe_auto_join
        Path: backend/routers/chat.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            await auto_join_groups(current_user)
        except Exception as e:
            logger.error(f"Background auto_join_groups failed: {e}")

    background_tasks.add_task(_safe_auto_join)

    # Performance Optimization⚡: Using a single aggregation pipeline to fetch
    # groups and their last messages. This avoids the N+1 query problem where
    # a separate query was executed for each group.
    pipeline = [
        # 1. Match groups where the current user is a member (exclude archived by default)
        {"$match": {"members.user_id": current_user["id"], "archived": {"$ne": True}}},

        # 2. Normalize the ID (supporting both UUID and ObjectId fallback)
        # to ensure it can be used as a join key for group_messages
        {"$addFields": {
            "normalized_id": {"$ifNull": ["$id", {"$toString": "$_id"}]}
        }},

        # 3. Join with group_messages to find the most recent message
        {"$lookup": {
            "from": "group_messages",
            "let": {"gid": "$normalized_id"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$group_id", "$$gid"]},
                    "deleted_at": None
                }},
                {"$sort": {"created_at": -1}},
                {"$limit": 1},
                {"$project": {"_id": 0}}
            ],
            "as": "last_messages_arr"
        }},

        # 4. Add derived fields and finalize the ID
        {"$addFields": {
            "id": "$normalized_id",
            "last_message": {"$arrayElemAt": ["$last_messages_arr", 0]},
            "member_count": {"$size": "$members"},
            "unread_count": 0  # Placeholder as per original logic
        }},

        # 5. Create a sort key (last message date or group creation date)
        # 5. Create a sort key (last message date or group creation date)
        {"$addFields": {
            "sort_key": {
                "$ifNull": [
                    "$last_message.created_at",
                    {"$cond": {
                        "if": {"$eq": [{"$type": "$created_at"}, "date"]},
                        "then": "$created_at",
                        "else": {"$dateFromString": {"dateString": "$created_at"}}
                    }}
                ]
            }
        }},

        # 6. Sort by last activity (most recent first)
        {"$sort": {"sort_key": -1}},

        # 7. Final projection and cleanup
        {"$project": {
            "last_messages_arr": 0,
            "normalized_id": 0,
            "_id": 0,
            "sort_key": 0
        }}
    ]

    groups = await db.chat_groups.aggregate(pipeline).to_list(100)

    # Return as list of ChatGroupResponse models
    # Return as list of ChatGroupResponse models
    # Ensure datetime fields are ISO strings for Pydantic validation
    for g in groups:
        if isinstance(g.get("created_at"), datetime):
            g["created_at"] = g["created_at"].isoformat()
        if isinstance(g.get("updated_at"), datetime):
            g["updated_at"] = g["updated_at"].isoformat()
        if g.get("last_message") and isinstance(g["last_message"].get("created_at"), datetime):
            g["last_message"]["created_at"] = g["last_message"]["created_at"].isoformat()

        # SECURITY: Mask PII during impersonation
        if is_impersonating(current_user):
            if g.get("members"):
                for member in g["members"]:
                    member["full_name"] = "Resident"
            if g.get("last_message"):
                g["last_message"]["sender_name"] = "Resident"

    return [ChatGroupResponse(**g) for g in groups]


@router.get("/chat-groups/{group_id}", response_model=ChatGroupResponse)
async def get_chat_group(
        group_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get details of a specific chat group."""
    # Performance Optimization⚡: Parallelize group details and last message fetch to reduce latency.
    group_task = db.chat_groups.find_one({"id": group_id, "building_id": building_id}, {"_id": 0})
    last_msg_task = db.group_messages.find_one(
        {"group_id": group_id, "deleted_at": None},
        {"_id": 0},
        sort=[("created_at", -1)]
    )

    group, last_message = await asyncio.gather(group_task, last_msg_task)

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not any(m["user_id"] == current_user["id"] for m in group.get("members", [])):
        raise HTTPException(status_code=403, detail="Not a member of this group")

    # SECURITY: Mask PII during impersonation
    if is_impersonating(current_user):
        if group.get("members"):
            for member in group["members"]:
                member["full_name"] = "Resident"
        if last_message:
            last_message["sender_name"] = "Resident"

    return ChatGroupResponse(
        **group,
        member_count=len(group.get("members", [])),
        last_message=last_message,
        unread_count=0
    )


@router.put("/chat-groups/{group_id}", response_model=ChatGroupResponse)
async def update_chat_group(
        group_id: str,
        update_data: ChatGroupUpdate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Update chat group details. Only group admins can update."""
    # Performance Optimization⚡: Consolidated database fetch.
    group = await db.chat_groups.find_one({"id": group_id, "building_id": building_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    is_admin = any(
        m["user_id"] == current_user["id"] and m.get("role") == "admin"
        for m in group.get("members", [])
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="Only group admins can update the group")

    # SECURITY: Sanitize group metadata to prevent Stored XSS
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    if update_dict.get("name"):
        update_dict["name"] = html_lib.escape(update_dict["name"])
    if update_dict.get("description"):
        update_dict["description"] = html_lib.escape(update_dict["description"])

    if update_dict:
        await db.chat_groups.update_one({"id": group_id}, {"$set": update_dict})
        # Update in-memory for response
        group.update(update_dict)

    if "_id" in group:
        del group["_id"]

    return ChatGroupResponse(
        **group,
        member_count=len(group.get("members", [])),
        last_message=None,
        unread_count=0
    )


@router.delete("/chat-groups/{group_id}")
async def delete_chat_group(
        group_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Delete a chat group. Only creator or admins can delete."""
    group = await db.chat_groups.find_one({"id": group_id, "building_id": building_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # System groups cannot be deleted
    if group.get("is_system"):
        raise HTTPException(status_code=403, detail="Cannot delete system groups")

    # Only creator or super admin can delete
    if group["created_by"] != current_user["id"] and effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Only group creator or admin can delete")

    await db.chat_groups.delete_one({"id": group_id})
    await db.group_messages.delete_many({"group_id": group_id})

    return {"message": "Group deleted successfully"}


@router.put("/chat-groups/{group_id}/archive")
async def archive_chat_group(
        group_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Archive an EC Members chat group (end of year).
    Allowed roles: super_admin, ec_member, strata_admin.
    Auto-creates a fresh EC Members group for the current year.
    """
    allowed_roles = {"super_admin", "ec_member", "strata_admin"}
    if effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=403, detail="Only EC members or super admin can archive groups")

    group = await db.chat_groups.find_one({"id": group_id, "building_id": building_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not group.get("is_system"):
        raise HTTPException(status_code=400, detail="Only system groups can be archived")

    if group.get("archived"):
        raise HTTPException(status_code=400, detail="Group is already archived")

    now = datetime.now(timezone.utc)
    year = now.year

    # Archive the group
    await db.chat_groups.update_one(
        {"id": group_id},
        {"$set": {
            "archived": True,
            "archived_at": now.isoformat(),
            "archived_year": year,
            "archived_by": current_user["id"],
            "updated_at": now.isoformat(),
        }}
    )

    # Auto-create a new EC Members group for the current year if archiving EC Members
    if group.get("type") == "role_based":
        new_group_name = f"EC Members {year}"
        existing_new = await db.chat_groups.find_one({"name": new_group_name})
        if not existing_new:
            new_group_id = str(uuid.uuid4())
            now_iso = now.isoformat()
            # Copy settings from archived group
            new_settings = group.get("settings", {
                "auto_join_criteria": {"type": "role",
                                       "roles": ["super_admin", "strata_admin", "ec_member"]},
                "who_can_post": ["member"],
                "who_can_delete": ["admin", "ec_member", "strata_admin"],
                "notifications_enabled": True
            })
            new_group_doc = {
                "id": new_group_id,
                "name": new_group_name,
                "description": f"Private group for Executive Committee members ({year})",
                "type": "role_based",
                "is_system": True,
                "created_by": current_user["id"],
                "created_at": now_iso,
                "updated_at": now_iso,
                "members": [],
                "settings": new_settings,
                "message_count": 0,
                "last_message_at": None,
                "archived": False,
            }
            await db.chat_groups.insert_one(new_group_doc)

    return {"message": f"Group archived for {year}", "archived": True}


@router.put("/chat-groups/{group_id}/unarchive")
async def unarchive_chat_group(
        group_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Unarchive a chat group. Only super_admin can unarchive."""
    if effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admin can unarchive groups")

    group = await db.chat_groups.find_one({"id": group_id, "building_id": building_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if not group.get("archived"):
        raise HTTPException(status_code=400, detail="Group is not archived")

    now = datetime.now(timezone.utc).isoformat()
    await db.chat_groups.update_one(
        {"id": group_id},
        {"$unset": {"archived_at": "", "archived_year": "", "archived_by": ""},
         "$set": {"archived": False, "updated_at": now}}
    )

    return {"message": "Group unarchived", "archived": False}


# ==================== MEMBER MANAGEMENT ====================

@router.post("/chat-groups/{group_id}/members")
async def add_group_member(
        group_id: str,
        request: AddGroupMemberRequest,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Add a member to the group."""
    # Security: Verify group authorization before checking target user details.
    group = await db.chat_groups.find_one({"id": group_id, "building_id": building_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Check if user can add members
    # SECURITY FIX: Ensure the current user is actually a member of the group before allowing them to invite others.
    member = next((m for m in group.get("members", []) if m["user_id"] == current_user["id"]), None)
    if not member:
        raise HTTPException(status_code=403, detail="Only members can add users to this group")

    is_admin = member.get("role") == "admin"
    allow_member_invite = group.get("settings", {}).get("allow_member_invite", True)

    if not is_admin and not allow_member_invite:
        raise HTTPException(status_code=403, detail="Only admins can add members to this group")

    # SECURITY FIX (BOLA): Verify that the invited member belongs to the current building
    target_membership = await db.memberships.find_one({
        "building_id": building_id,
        "user_id": request.user_id
    })
    if not target_membership:
        raise HTTPException(
            status_code=400,
            detail="The invited user does not belong to this building"
        )

    # Get user details
    user = await db.users.find_one({"id": request.user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if member already exists
    existing = any(m["user_id"] == request.user_id for m in group.get("members", []))
    if existing:
        raise HTTPException(status_code=400, detail="User is already a member")

    # Add member
    new_member = {
        "user_id": request.user_id,
        "full_name": user["full_name"],
        "role": "member",
        "joined_at": datetime.now(timezone.utc).isoformat()
    }

    await db.chat_groups.update_one(
        {"id": group_id},
        {"$push": {"members": new_member}}
    )

    # Notify the user
    asyncio.create_task(create_user_notification(
        user_id=request.user_id,
        title="Added to Group Chat",
        message=f"You were added to the group chat: {group['name']}",
        notification_type="chat",
        link=f"/community/chat?group={group_id}"
    ))

    return {"message": "Member added successfully", "member": new_member}


@router.delete("/chat-groups/{group_id}/members/{user_id}")
async def remove_group_member(
        group_id: str,
        user_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Remove a member from the group."""
    # Performance Optimization⚡: Consolidated database fetch.
    group = await db.chat_groups.find_one({"id": group_id, "building_id": building_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Only group admins or the member themselves can remove
    member = next((m for m in group.get("members", []) if m["user_id"] == current_user["id"]), None)
    is_admin = member and member.get("role") == "admin"
    is_self = user_id == current_user["id"]

    if not is_admin and not is_self:
        raise HTTPException(status_code=403, detail="Not authorized to remove this member")

    # Cannot remove from system groups (except self-leave)
    if group.get("is_system") and not is_self:
        raise HTTPException(status_code=403, detail="Cannot remove members from system groups")

    await db.chat_groups.update_one(
        {"id": group_id},
        {"$pull": {"members": {"user_id": user_id}}}
    )

    return {"message": "Member removed successfully"}


# ==================== GROUP MESSAGES ====================

@router.post("/chat-groups/{group_id}/messages", response_model=GroupMessageResponse)
async def send_group_message(
        group_id: str,
        message_data: GroupMessageCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Send a message to a group."""
    if not await check_group_membership(group_id, current_user["id"]):
        raise HTTPException(status_code=403, detail="Not a member of this group")

    message_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize message content to prevent Stored XSS
    sanitized_content = html_lib.escape(message_data.content)

    message_doc = {
        "id": message_id,
        "content": html_lib.escape(message_data.content),
        "group_id": group_id,
        "sender_id": current_user["id"],
        "sender_name": current_user["full_name"],
        "sender_role": current_user.get("role"),
        "created_at": now,
        "edited_at": None,
        "deleted_at": None,
        "deleted_by": None,
        "is_deleted": False
    }

    await db.group_messages.insert_one(message_doc)

    return GroupMessageResponse(**message_doc)


@router.get("/chat-groups/{group_id}/messages", response_model=List[GroupMessageResponse])
async def get_group_messages(
        group_id: str,
        limit: int = 100,
        before: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get messages from a group."""
    # Security: Verify membership authorization before resource access.
    if not await check_group_membership(group_id, current_user["id"]):
        raise HTTPException(status_code=403, detail="Not a member of this group")

    query = {"group_id": group_id, "building_id": building_id, "deleted_at": None}
    if before:
        query["created_at"] = {"$lt": before}

    messages = await db.group_messages.find(
        query,
        {"_id": 0}
    ).sort("created_at", -1).limit(limit).to_list(limit)

    # SECURITY: Mask PII during impersonation
    if is_impersonating(current_user):
        for m in messages:
            m["sender_name"] = "Resident"

    return [GroupMessageResponse(**m) for m in reversed(messages)]


@router.delete("/chat-groups/{group_id}/messages/{message_id}")
async def delete_group_message(
        group_id: str,
        message_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Delete a message from a group."""
    if not await can_delete_message(group_id, message_id, current_user):
        raise HTTPException(status_code=403, detail="Not authorized to delete this message")

    # Soft delete - keep the message but mark as deleted
    now = datetime.now(timezone.utc).isoformat()
    await db.group_messages.update_one(
        {"id": message_id},
        {
            "$set": {
                "deleted_at": now,
                "deleted_by": current_user["id"],
                "is_deleted": True
            }
        }
    )

    return {"message": "Message deleted successfully"}


# ==================== PUBLIC FUNCTIONS ====================

async def initialize_system_groups():
    """
    Initialize the 3 default system groups if they don't exist.
    Called on application startup.
    """
    buildings = await db.buildings.find({"is_active": True}, {"_id": 0, "id": 1}).to_list(500)
    if not buildings:
        buildings = [{"id": DEFAULT_BUILDING_ID}]

    for building in buildings:
        bid = building.get("id")
        if not bid:
            continue

        set_ctx_building_id(bid)
        try:
            existing_count = await db.chat_groups.count_documents({"is_system": True})
            if existing_count >= 4:
                continue  # Already initialized for this building

            now = datetime.now(timezone.utc).isoformat()
            system_user_id = "system"

            default_groups = [
                {
                    "id": str(uuid.uuid4()),
                    "name": "General",
                    "description": "Community-wide chat for all residents",
                    "type": "general",
                    "is_system": True,
                    "created_by": system_user_id,
                    "created_at": now,
                    "settings": {
                        "allow_member_invite": False,
                        "member_approval_required": False,
                        "who_can_delete": ["admin"],
                        "auto_join_criteria": {}
                    },
                    "members": [],
                },
                {
                    "id": str(uuid.uuid4()),
                    "name": "EC Members",
                    "description": "Private chat for Executive Committee members and Chairman",
                    "type": "role_based",
                    "is_system": True,
                    "created_by": system_user_id,
                    "created_at": now,
                    "settings": {
                        "allow_member_invite": False,
                        "member_approval_required": False,
                        "who_can_delete": ["admin", "ec_member", "strata_admin"],
                        "auto_join_criteria": {
                            "roles": ["super_admin", "strata_admin", "ec_member"]
                        }
                    },
                    "members": [],
                },
                {
                    "id": str(uuid.uuid4()),
                    "name": "Townhouse Residents",
                    "description": "Chat for townhouse residents (T01-T17)",
                    "type": "unit_based",
                    "is_system": True,
                    "created_by": system_user_id,
                    "created_at": now,
                    "settings": {
                        "allow_member_invite": False,
                        "member_approval_required": False,
                        "who_can_delete": ["admin"],
                        "auto_join_criteria": {
                            "unit_types": ["townhouse"]
                        }
                    },
                    "members": [],
                },
                {
                    "id": str(uuid.uuid4()),
                    "name": "Apartment Residents",
                    "description": "Chat for apartment residents (UA001-UA070)",
                    "type": "unit_based",
                    "is_system": True,
                    "created_by": system_user_id,
                    "created_at": now,
                    "settings": {
                        "allow_member_invite": False,
                        "member_approval_required": False,
                        "who_can_delete": ["admin"],
                        "auto_join_criteria": {
                            "unit_types": ["apartment"]
                        }
                    },
                    "members": [],
                },
            ]

            for group in default_groups:
                group_doc = dict(group)
                group_doc["building_id"] = bid
                existing = await db.chat_groups.find_one({"name": group_doc["name"], "is_system": True})
                if not existing:
                    await db.chat_groups.insert_one(group_doc)
                    print(f"✓ Created system group: {group_doc['name']} (building {bid})")
        finally:
            set_ctx_building_id(None)


__all__ = [
    "router",
    "auto_join_groups",
    "initialize_system_groups",
]
