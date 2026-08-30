# @featuretrace:resident-directory-chat — Legacy direct-message conversation API used by directory click-to-chat.
# Layer: router
# Data flow: ResidentDirectoryPage -> POST /conversations -> db.memberships + db.users (participants only;
#            the INITIATOR comes from the authenticated request, which may be a Postgres-only identity
#            with no db.users mirror) + db.conversations (building-scoped).
# Related: frontend/src/pages/dashboard/ResidentDirectoryPage.tsx, backend/server.py, docs/architecture/mindmap/featuretrace/resident-directory-chat_flow.md

# @featuretrace:email-delivery — Communication-originated manual emails, notices, announcements, and user preferences.
# Layer: router
# Data flow: EmailPreferencesPage/ManualEmailPage -> /notifications/preferences + communication send routes -> db.email_notification_preferences + send_email_async() (building-scoped).
# Related: backend/utils/email.py, backend/routers/notifications.py, frontend/src/pages/dashboard/EmailPreferencesPage.jsx, docs/architecture/mindmap/email-delivery.md

"""
Communication router module.

This module handles all communication-related routes including messages,
announcements, and conversations (private messaging).
"""

import html as html_lib
import uuid
from datetime import datetime, timezone

import asyncio
import logging
import nh3
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import List, Optional

from database import db
from models.communication import (
    MessageCreate,
    MessageResponse,
    AnnouncementCreate,
    AnnouncementResponse,
    AnnouncementUpdateExpiry,
    ConversationCreate,
    ConversationResponse,
    PrivateMessageCreate,
    PrivateMessageResponse,
    NoticeCreate,
    NoticeResponse,
    NoticeCommentCreate,
    NoticeCommentResponse,
    NoticeAcknowledgmentResponse,
    EmailNotificationPreferences,
    EmailNotificationPreferencesResponse,
    ManualEmailRequest,
)
from models.user import UserRole
from services.settings_service import get_general_settings_or_default
from utils.activity_helper import log_activity
from utils.auth import get_optional_user, get_approved_user, is_approved_user, get_current_building, \
    get_optional_building, get_building_or_400, is_impersonating, effective_role
from utils.email import send_email_async, get_email_template
from utils.helpers import create_user_notification, broadcast_user_notification, create_audit_log
from utils.permissions import get_user_permissions

# Create router
router = APIRouter(prefix="")

# Security
security = HTTPBearer(auto_error=False)

# Logger
logger = logging.getLogger(__name__)


# ==================== CHAT/MESSAGE ROUTES ====================

@router.post("/messages", response_model=MessageResponse)
async def send_message(
        message: MessageCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Send a message.
    
    Creates a new message in the public chat or as a private message to a specific user.
    Requires chat permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_chat:
        raise HTTPException(status_code=403, detail="Not authorized to send messages")

    msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    msg_doc = {
        "id": msg_id,
        "building_id": building_id,
        "content": html_lib.escape(message.content),
        "sender_id": current_user["id"],
        "sender_name": current_user["full_name"],
        "recipient_id": message.recipient_id,
        "is_private": message.is_private,
        "created_at": now
    }

    await db.messages.insert_one(msg_doc)
    return MessageResponse(**msg_doc)


@router.get("/messages", response_model=List[MessageResponse])
async def get_messages(
        recipient_id: Optional[str] = None,
        limit: int = 50,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get messages.
    
    Retrieves messages from public chat or private conversation with a specific user.
    If recipient_id is provided, returns private conversation.
    Otherwise returns public chat messages.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_chat:
        raise HTTPException(status_code=403, detail="Not authorized to view messages")

    if recipient_id:
        # Private conversation
        query = {
            "building_id": building_id,
            "$or": [
                {"sender_id": current_user["id"], "recipient_id": recipient_id},
                {"sender_id": recipient_id, "recipient_id": current_user["id"]}
            ]
        }
    else:
        # Public chat
        query = {"building_id": building_id, "is_private": False}

    messages = await db.messages.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit)

    if is_impersonating(current_user):
        for m in messages:
            m["sender_name"] = "Resident"

    return [MessageResponse(**m) for m in reversed(messages)]


# ==================== ANNOUNCEMENT ROUTES ====================

@router.post("/announcements", response_model=AnnouncementResponse)
async def create_announcement(
        announcement: AnnouncementCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new announcement.
    
    Posts a new announcement with optional expiration date.
    Sends email notifications to users based on their preferences.
    Requires post announcements permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized to post announcements")

    ann_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Store title as plain text — html.escape() must NOT be applied here because
    # React renders the title as a plain text node (automatic XSS-safe). Escaping
    # before storage causes literal &#x27; entities to appear in the UI.
    # html_lib.escape() is applied at email-build time (not storage time).
    ann_dict = announcement.model_dump()
    ann_dict["title"] = ann_dict.get("title", "").strip()
    ann_dict["content"] = nh3.clean(ann_dict.get("content", ""))

    ann_doc = {
        "id": ann_id,
        "building_id": building_id,
        **ann_dict,
        "created_by": current_user["id"],
        "created_by_name": current_user["full_name"],
        "history": [{
            "action": "created",
            "user_id": current_user["id"],
            "user_name": current_user["full_name"],
            "timestamp": now,
            "details": announcement.model_dump()
        }],
        "created_at": now
    }

    await db.announcements.insert_one(ann_doc)

    # Create audit log
    asyncio.create_task(create_audit_log(
        action="created",
        resource_type="announcement",
        resource_id=ann_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"title": announcement.title}
    ))

    # Create in-app notifications for all users
    asyncio.create_task(_create_announcement_notifications(ann_doc))

    # Send email notifications to users based on their preferences
    asyncio.create_task(_send_announcement_emails(ann_doc))

    # Log to community activity feed
    asyncio.create_task(log_activity(
        activity_type="announcement",
        title=announcement.title,
        entity_id=ann_id,
        priority=2 if announcement.priority == "urgent" else 3,
        metadata={"priority": announcement.priority}
    ))

    return AnnouncementResponse(**ann_doc)


@router.post("/announcements/broadcast")
async def broadcast_announcement(
        announcement: AnnouncementCreate,
        target_building_ids: Optional[List[str]] = None,
        current_user: dict = Depends(get_approved_user),
):
    """
    Broadcast an announcement to multiple buildings simultaneously.

    Creates the same announcement in each target building. If target_building_ids
    is not provided, broadcasts to ALL buildings the user has access to.
    Only super_admin and strata_manager roles can broadcast cross-building.
    """
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Only super admins and strata managers can broadcast announcements")

    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized to post announcements")

    # Resolve buildings to broadcast to
    if effective_role(current_user) == UserRole.SUPER_ADMIN:
        if target_building_ids:
            buildings = await db.buildings.find(
                {"id": {"$in": target_building_ids}, "is_active": True}, {"_id": 0}
            ).to_list(100)
        else:
            buildings = await db.buildings.find({"is_active": True}, {"_id": 0}).to_list(100)
    else:
        # strata_manager: only buildings they have membership in
        memberships = await db.memberships.find(
            {"user_id": current_user["id"], "is_active": True}, {"building_id": 1}
        ).to_list(50)
        accessible_ids = [m["building_id"] for m in memberships]
        if target_building_ids:
            accessible_ids = [bid for bid in target_building_ids if bid in accessible_ids]
        if not accessible_ids:
            raise HTTPException(status_code=403, detail="No accessible buildings to broadcast to")
        buildings = await db.buildings.find(
            {"id": {"$in": accessible_ids}, "is_active": True}, {"_id": 0}
        ).to_list(50)

    if not buildings:
        raise HTTPException(status_code=404, detail="No target buildings found")

    now = datetime.now(timezone.utc).isoformat()
    created_ids = []

    ann_dict = announcement.model_dump()
    ann_dict["title"] = ann_dict.get("title", "").strip()
    ann_dict["content"] = nh3.clean(ann_dict.get("content", ""))

    # Performance Optimization⚡: Parallelize announcement creation across buildings.
    # Using asyncio.gather reduces cumulative I/O wait time from O(N) to O(1) concurrent requests.
    async def _process_building_broadcast(building_doc):
        """Generated function header.

        Function: _process_building_broadcast
        Path: backend/routers/communication.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        bid = building_doc["id"]
        ann_id = str(uuid.uuid4())
        ann_doc = {
            "id": ann_id,
            "building_id": bid,
            **ann_dict,
            "created_by": current_user["id"],
            "created_by_name": current_user["full_name"],
            "history": [{
                "action": "broadcast",
                "user_id": current_user["id"],
                "user_name": current_user["full_name"],
                "timestamp": now,
                "details": {**ann_dict, "broadcast": True},
            }],
            "created_at": now,
        }
        await db.announcements.insert_one(ann_doc)
        # Notifications and emails are already launched as background tasks
        asyncio.create_task(_create_announcement_notifications(ann_doc))
        asyncio.create_task(_send_announcement_emails(ann_doc))
        return ann_id

    results = await asyncio.gather(*[_process_building_broadcast(b) for b in buildings], return_exceptions=True)

    for res in results:
        if isinstance(res, Exception):
            logger.error(f"Failed to broadcast announcement to a building: {res}")
        else:
            created_ids.append(res)

    asyncio.create_task(create_audit_log(
        action="broadcast",
        resource_type="announcement",
        resource_id=",".join(created_ids),
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"title": announcement.title, "buildings": len(buildings)},
    ))

    return {
        "created": len(created_ids),
        "buildings": [{"id": b["id"], "name": b.get("name", b["id"])} for b in buildings],
        "announcement_ids": created_ids,
    }


@router.get("/announcements", response_model=List[AnnouncementResponse])
async def get_announcements(
        limit: int = 100,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_building_or_400)
):
    """
    Get all active announcements visible to the user in the current building context.
    
    Retrieves all announcements that haven't expired and are targeted to the user.
    Public announcements are visible to unauthenticated users.
    Authenticated users can see all announcements.
    """
    now = datetime.now(timezone.utc).isoformat()

    query = {
        "building_id": building_id,
        "is_test_data": {"$ne": True},
        "$or": [
            {"expires_at": None},
            {"expires_at": {"$gt": now}}
        ]
    }

    if not current_user or not is_approved_user(current_user):
        query["is_public"] = True
    else:
        # Filter by target audience for authenticated users
        # Admins, Chairmen, and Managers see all announcements
        if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
            role_filter = {
                "$or": [
                    {"is_public": True},
                    {"target_roles": None},
                    {"target_roles": []},
                    {"target_roles": effective_role(current_user)},
                    {"target_users": current_user["id"]}
                ]
            }
            query = {"$and": [query, role_filter]}

    # Performance Optimization⚡: Batch retrieval of announcements.
    announcements = await db.announcements.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit)

    if is_impersonating(current_user):
        for a in announcements:
            a["created_by_name"] = "Management"
            if a.get("history"):
                for h in a["history"]:
                    h["user_name"] = "Management"

    return [AnnouncementResponse(**a) for a in announcements]


@router.patch("/announcements/{ann_id}/expiry", response_model=AnnouncementResponse)
async def update_announcement_expiry(
        ann_id: str,
        data: AnnouncementUpdateExpiry,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update the expiry date of an announcement.

    Only the expiry date can be modified. Records change in history.
    Requires post announcements permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized to edit announcements")

    ann = await db.announcements.find_one({"id": ann_id, "building_id": building_id})
    if not ann:
        raise HTTPException(status_code=404, detail="Announcement not found")

    new_expiry = data.expires_at
    now = datetime.now(timezone.utc).isoformat()

    history_entry = {
        "action": "updated_expiry",
        "user_id": current_user["id"],
        "user_name": current_user["full_name"],
        "timestamp": now,
        "old_expiry": ann.get("expires_at"),
        "new_expiry": new_expiry
    }

    await db.announcements.update_one(
        {"id": ann_id, "building_id": building_id},
        {
            "$set": {"expires_at": new_expiry},
            "$push": {"history": history_entry}
        }
    )

    updated_ann = await db.announcements.find_one({"id": ann_id, "building_id": building_id}, {"_id": 0})
    return AnnouncementResponse(**updated_ann)


@router.delete("/announcements/{ann_id}")
async def delete_announcement(
        ann_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Delete an announcement.
    
    Removes an announcement by ID.
    Requires post announcements permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    ann = await db.announcements.find_one({"id": ann_id, "building_id": building_id}, {"_id": 0, "title": 1})
    if not ann:
        raise HTTPException(status_code=404, detail="Announcement not found")

    result = await db.announcements.delete_one({"id": ann_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Announcement not found")

    asyncio.create_task(create_audit_log(
        action="deleted", resource_type="announcement", resource_id=ann_id,
        user_id=current_user["id"], user_name=current_user["full_name"],
        details={"title": ann.get("title")},
        building_id=building_id
    ))

    return {"message": "Announcement deleted successfully"}


# ==================== CONVERSATION ROUTES ====================

@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
        data: ConversationCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new conversation.
    
    Creates a new private conversation or group chat.
    For 1-on-1 chats, returns existing conversation if one already exists.
    Requires chat permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_chat:
        raise HTTPException(status_code=403, detail="Not authorized")

    requested_member_ids = [mid for mid in dict.fromkeys(data.member_ids) if mid and mid != current_user["id"]]
    if not requested_member_ids:
        raise HTTPException(status_code=400, detail="Select another resident to start a chat")

    member_ids = list(dict.fromkeys(requested_member_ids + [current_user["id"]]))

    # Validate the requested participants before reusing or creating a conversation.
    # This keeps stale directory rows and cross-building IDs from opening old
    # conversations whose participant can no longer be resolved safely.
    other_member_ids = [mid for mid in member_ids if mid != current_user["id"]]
    if other_member_ids:
        active_member_ids = set(await db.memberships.distinct(
            "user_id",
            {
                "building_id": building_id,
                "user_id": {"$in": other_member_ids},
                "is_active": True,
            },
        ))
        if active_member_ids != set(other_member_ids):
            raise HTTPException(
                status_code=400,
                detail="One or more participants do not belong to this building",
            )

    member_docs = await db.users.find(
        {"id": {"$in": member_ids}, "is_active": True},
        {"id": 1, "full_name": 1, "profile_image": 1, "_id": 0}
    ).to_list(len(member_ids))
    member_map = {m["id"]: m for m in member_docs}

    # The INITIATOR is resolved from the authenticated request, never from a
    # db.users lookup.
    #
    # Identity is served by Postgres for any building whose identity_core is
    # promoted, and a Postgres user is not guaranteed to have a MongoDB mirror row
    # — super_admins in particular live in the platform tenant and may have no
    # db.users document at all (verified live 2026-08-28: administrator@ has none,
    # while core.users has the row that authenticated the request). Requiring one
    # here made the lookup below fail on the caller's OWN id, so every attempt to
    # start a chat from /community/directory answered 400 "This resident is no
    # longer available for chat" — naming the other participant for a problem that
    # was entirely on the caller's side. get_approved_user has already established
    # who this user is; re-deriving that from a mirror store can only lose.
    if current_user["id"] not in member_map:
        member_map[current_user["id"]] = {
            "id": current_user["id"],
            "full_name": current_user.get("full_name") or current_user.get("email") or "Unknown",
            "profile_image": current_user.get("profile_image"),
        }

    # A REQUESTED participant that cannot be resolved is still a hard error: the
    # directory row is stale and opening a conversation with an unresolvable member
    # would produce a chat nobody can be addressed in.
    missing_member_ids = [mid for mid in member_ids if mid not in member_map]
    if missing_member_ids:
        logger.warning(
            "Conversation creation rejected: missing active user docs for building=%s missing_count=%s",
            building_id,
            len(missing_member_ids),
        )
        raise HTTPException(
            status_code=400,
            detail="This resident is no longer available for chat. Refresh the directory and try again.",
        )

    # For 1-on-1 chat, check if conversation already exists after participant validation.
    if not data.is_group and len(requested_member_ids) == 1:
        other_user_id = requested_member_ids[0]
        existing = await db.conversations.find_one({
            "building_id": building_id,
            "is_group": False,
            "member_ids": {"$all": [current_user["id"], other_user_id], "$size": 2}
        }, {"_id": 0})

        if existing:
            # Return existing conversation
            existing["members"] = []
            for mid in existing["member_ids"]:
                if mid not in member_map:
                    logger.warning(
                        "Conversation reuse rejected: conversation=%s has unresolved member for building=%s",
                        existing.get("id"),
                        building_id,
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="This conversation is no longer available. Refresh the directory and try again.",
                    )
                existing["members"].append({
                    "id": mid,
                    "full_name": member_map[mid]["full_name"],
                    "profile_image": member_map[mid].get("profile_image")
                })
            return ConversationResponse(**existing)

    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    members = []
    for mid in member_ids:
        members.append({
            "id": mid,
            "full_name": member_map[mid]["full_name"],
            "profile_image": member_map[mid].get("profile_image")
        })

    conv_doc = {
        "id": conv_id,
        "building_id": building_id,
        "name": data.name if data.is_group else None,
        "is_group": data.is_group,
        "member_ids": member_ids,
        "created_by": current_user["id"],
        "last_message": None,
        "created_at": now,
        "updated_at": now
    }

    await db.conversations.insert_one(conv_doc)
    conv_doc["members"] = members

    return ConversationResponse(**conv_doc)


@router.get("/conversations", response_model=List[ConversationResponse])
async def get_conversations(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all conversations for the current user.
    
    Retrieves all conversations where the current user is a member,
    sorted by most recent activity.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_chat:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Performance Optimization⚡: Using a single MongoDB aggregation pipeline with $lookup to join user details.
    # This eliminates the N+M query problem (1 query for conversations + M queries for members of each N conversation)
    # reducing database round-trips from O(N*M) to O(1).
    pipeline = [
        {"$match": {"building_id": building_id, "member_ids": current_user["id"]}},
        {"$sort": {"updated_at": -1}},
        {"$limit": 100},
        {"$lookup": {
            "from": "users",
            "localField": "member_ids",
            "foreignField": "id",
            "as": "member_details",
            "pipeline": [
                {"$project": {"password_hash": 0}}
            ]
        }},
        {"$addFields": {
            "members": {
                "$map": {
                    "input": "$member_details",
                    "as": "m",
                    "in": {
                        "id": "$$m.id",
                        "full_name": "$$m.full_name",
                        "profile_image": "$$m.profile_image"
                    }
                }
            }
        }},
        {"$project": {
            "_id": 0,
            "member_details": 0
        }}
    ]

    results = await db.conversations.aggregate(pipeline).to_list(100)

    if is_impersonating(current_user):
        for conv in results:
            if conv.get("members"):
                for member in conv["members"]:
                    member["full_name"] = "Resident"
            if conv.get("last_message"):
                conv["last_message"]["sender_name"] = "Resident"

    return [ConversationResponse(**conv) for conv in results]


@router.post("/conversations/{conv_id}/members")
async def add_conversation_member(
        conv_id: str,
        user_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Add a member to a group conversation.
    
    Adds a new user to an existing group conversation.
    Only works for group conversations, not direct messages.
    Requires the current user to be a member of the conversation.
    """
    conv = await db.conversations.find_one({"id": conv_id, "building_id": building_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user["id"] not in conv["member_ids"]:
        raise HTTPException(status_code=403, detail="Not a member of this conversation")

    if not conv["is_group"]:
        raise HTTPException(status_code=400, detail="Cannot add members to direct messages")

    # SECURITY FIX (BOLA): Verify that the target user belongs to the current building
    target_membership = await db.memberships.find_one({
        "building_id": building_id,
        "user_id": user_id
    })
    if not target_membership:
        raise HTTPException(
            status_code=400,
            detail="The invited user does not belong to this building"
        )

    await db.conversations.update_one(
        {"id": conv_id, "building_id": building_id},
        {"$addToSet": {"member_ids": user_id}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}}
    )

    return {"message": "Member added successfully"}


@router.post("/conversations/{conv_id}/messages", response_model=PrivateMessageResponse)
async def send_private_message(
        conv_id: str,
        data: PrivateMessageCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Send a message in a conversation.
    
    Sends a new message to a specific conversation.
    The message is marked as read by the sender automatically.
    Requires the current user to be a member of the conversation.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_chat:
        raise HTTPException(status_code=403, detail="Not authorized")

    conv = await db.conversations.find_one({"id": conv_id, "building_id": building_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user["id"] not in conv["member_ids"]:
        raise HTTPException(status_code=403, detail="Not a member of this conversation")

    msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    msg_doc = {
        "id": msg_id,
        "building_id": building_id,
        "content": html_lib.escape(data.content),
        "conversation_id": conv_id,
        "sender_id": current_user["id"],
        "sender_name": current_user["full_name"],
        "read_by": [current_user["id"]],
        "created_at": now
    }

    await db.private_messages.insert_one(msg_doc)

    # Create in-app notification for recipients
    for member_id in conv["member_ids"]:
        if member_id != current_user["id"]:
            asyncio.create_task(create_user_notification(
                user_id=member_id,
                title=f"New Message from {current_user['full_name']}",
                message=data.content[:100] + ("..." if len(data.content) > 100 else ""),
                notification_type="private_message",
                link=f"/community/messages?conv={conv_id}"
            ))

    # Update conversation
    await db.conversations.update_one(
        {"id": conv_id, "building_id": building_id},
        {"$set": {
            "last_message": {"content": data.content[:50], "sender_name": current_user["full_name"], "created_at": now},
            "updated_at": now
        }}
    )

    return PrivateMessageResponse(**msg_doc)


@router.get("/conversations/{conv_id}/messages", response_model=List[PrivateMessageResponse])
async def get_conversation_messages(
        conv_id: str,
        limit: int = 50,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get messages from a conversation.
    
    Retrieves messages from a specific conversation, sorted by creation time.
    Automatically marks retrieved messages as read by the current user.
    Requires the current user to be a member of the conversation.
    """
    conv = await db.conversations.find_one({"id": conv_id, "building_id": building_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user["id"] not in conv["member_ids"]:
        raise HTTPException(status_code=403, detail="Not a member of this conversation")

    messages = await db.private_messages.find(
        {"conversation_id": conv_id, "building_id": building_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(limit)

    if is_impersonating(current_user):
        for m in messages:
            m["sender_name"] = "Resident"

    # Mark as read
    await db.private_messages.update_many(
        {"conversation_id": conv_id, "read_by": {"$ne": current_user["id"]}},
        {"$addToSet": {"read_by": current_user["id"]}}
    )

    return [PrivateMessageResponse(**m) for m in reversed(messages)]


# ==================== NOTICE BOARD ROUTES ====================

@router.post("/notices", response_model=NoticeResponse)
async def create_notice(
        notice: NoticeCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a new official notice.
    
    Creates a formal notice for the community with optional pinning and acknowledgment tracking.
    Sends email notifications to relevant users based on their preferences.
    Requires post announcements permission.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized to post notices")

    now = datetime.now(timezone.utc).isoformat()
    notice_doc = _build_notice_doc(notice, current_user, building_id, now, "created")

    await db.notices.insert_one(notice_doc)

    # Create audit log
    asyncio.create_task(create_audit_log(
        action="created",
        resource_type="notice",
        resource_id=notice_doc["id"],
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"title": notice.title}
    ))

    # Create in-app notifications for target users
    asyncio.create_task(_create_notice_notifications(notice_doc))

    # Send email notifications to users based on their preferences and target roles
    asyncio.create_task(_send_notice_emails(notice_doc))

    # Log to community activity feed
    asyncio.create_task(log_activity(
        activity_type="announcement",
        title=f"Notice: {notice.title}",
        entity_id=notice_doc["id"],
        priority=2 if notice.priority in ["high", "urgent"] else 3,
        metadata={"category": notice.category}
    ))

    return NoticeResponse(**notice_doc)


@router.post("/notices/broadcast")
async def broadcast_notice(
        notice: NoticeCreate,
        target_building_ids: Optional[List[str]] = None,
        current_user: dict = Depends(get_approved_user),
):
    """
    Broadcast an official notice to multiple buildings.

    GAP-COMMS-002 hard cutover: new UI broadcasts write to notices. The legacy
    /announcements API remains only as a compatibility read/write surface until
    existing announcement data has a separately verified migration/read-model.
    """
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Only super admins and strata managers can broadcast notices")

    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized to post notices")

    buildings = await _resolve_broadcast_buildings(current_user, target_building_ids)
    if not buildings:
        raise HTTPException(status_code=404, detail="No target buildings found")

    now = datetime.now(timezone.utc).isoformat()
    created_ids = []

    async def _process_building_broadcast(building_doc):
        bid = building_doc["id"]
        notice_doc = _build_notice_doc(notice, current_user, bid, now, "broadcast")
        await db.notices.insert_one(notice_doc)
        asyncio.create_task(_create_notice_notifications(notice_doc))
        asyncio.create_task(_send_notice_emails(notice_doc))
        return notice_doc["id"]

    results = await asyncio.gather(*[_process_building_broadcast(b) for b in buildings], return_exceptions=True)

    for res in results:
        if isinstance(res, Exception):
            logger.error(f"Failed to broadcast notice to a building: {res}")
        else:
            created_ids.append(res)

    asyncio.create_task(create_audit_log(
        action="broadcast",
        resource_type="notice",
        resource_id=",".join(created_ids),
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"title": notice.title, "buildings": len(buildings)},
    ))

    return {
        "created": len(created_ids),
        "buildings": [{"id": b["id"], "name": b.get("name", b["id"])} for b in buildings],
        "notice_ids": created_ids,
    }


@router.get("/notices", response_model=List[NoticeResponse])
async def get_notices(
        include_expired: bool = False,
        category: Optional[str] = None,
        limit: int = 100,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all notices visible to the current user.
    
    Retrieves notices filtered by expiration, category, and user role.
    Pinned notices are returned first.
    """
    now = datetime.now(timezone.utc).isoformat()

    query = {"building_id": building_id}

    # Filter by expiration
    if not include_expired:
        query["$or"] = [
            {"expires_at": None},
            {"expires_at": {"$gt": now}}
        ]

    # Filter by category
    if category:
        query["category"] = category

    # Build the list of roles whose notices this user can see.
    # Rules:
    #   1. super_admin sees ALL notices (no role filter).
    #   2. ec_member is a subset of owner: if a notice targets "owner", ec_member
    #      users should also see it (EC member IS an owner). The reverse is NOT
    #      true — owner-only notices must not be shown when only "ec_member" is
    #      targeted.
    #   3. For every other role, match only notices that explicitly include that
    #      role OR that have no target (broadcast to all).
    user_role = current_user.get("role", "")

    admin_roles = {"super_admin", "strata_manager", "ec_member", "strata_admin"}

    if user_role in admin_roles:
        # Admins see all notices — no role filter applied.
        pass
    else:
        # Build the effective roles this user belongs to.
        effective_roles = [user_role]
        if user_role == "ec_member":
            # EC members are also owners; include "owner" notices for them.
            effective_roles.append("owner")

        role_query = {
            "$or": [
                {"target_roles": None},
                {"target_roles": []},
                # Array field contains at least one of the user's effective roles
                {"target_roles": {"$in": effective_roles}},
            ]
        }
        query = {"$and": [query, role_query]}

    # Performance Optimization⚡: Batch retrieval of notices.
    notices = await db.notices.find(query, {"_id": 0}).sort([("is_pinned", -1), ("created_at", -1)]).to_list(limit)

    if is_impersonating(current_user):
        for n in notices:
            n["created_by_name"] = "Management"
            if n.get("history"):
                for h in n["history"]:
                    h["user_name"] = "Management"

    return [NoticeResponse(**n) for n in notices]


@router.patch("/notices/{notice_id}/expiry", response_model=NoticeResponse)
async def update_notice_expiry(
        notice_id: str,
        data: AnnouncementUpdateExpiry,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update the expiry date of a notice.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    notice = await db.notices.find_one({"id": notice_id, "building_id": building_id})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    new_expiry = data.expires_at
    now = datetime.now(timezone.utc).isoformat()

    history_entry = {
        "action": "updated_expiry",
        "user_id": current_user["id"],
        "user_name": current_user["full_name"],
        "timestamp": now,
        "old_expiry": notice.get("expires_at"),
        "new_expiry": new_expiry
    }

    await db.notices.update_one(
        {"id": notice_id, "building_id": building_id},
        {
            "$set": {"expires_at": new_expiry, "updated_at": now},
            "$push": {"history": history_entry}
        }
    )

    updated_notice = await db.notices.find_one({"id": notice_id, "building_id": building_id}, {"_id": 0})
    return NoticeResponse(**updated_notice)


@router.put("/notices/{notice_id}/pin")
async def toggle_notice_pin(
        notice_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Toggle pin status of a notice"""
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    notice = await db.notices.find_one({"id": notice_id, "building_id": building_id}, {"_id": 0})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    new_pin_status = not notice.get("is_pinned", False)
    await db.notices.update_one(
        {"id": notice_id, "building_id": building_id},
        {"$set": {"is_pinned": new_pin_status, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )

    return {"message": "Notice pin status updated", "is_pinned": new_pin_status}


@router.delete("/notices/{notice_id}")
async def delete_notice(
        notice_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Delete a notice"""
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.notices.delete_one({"id": notice_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Notice not found")

    # Also delete related comments and acknowledgments
    await db.notice_comments.delete_many({"notice_id": notice_id})
    await db.notice_acknowledgments.delete_many({"notice_id": notice_id})

    return {"message": "Notice deleted successfully"}


# ==================== NOTICE COMMENTS ROUTES ====================

@router.post("/notices/{notice_id}/comments", response_model=NoticeCommentResponse)
async def create_notice_comment(
        notice_id: str,
        comment: NoticeCommentCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Add a comment to a notice.
    
    Creates a comment or reply on a notice.
    All authenticated users can comment.
    """
    # Verify notice exists
    notice = await db.notices.find_one({"id": notice_id, "building_id": building_id}, {"_id": 0})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    comment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    comment_doc = {
        "id": comment_id,
        "building_id": building_id,
        "notice_id": notice_id,
        "content": html_lib.escape(comment.content),  # SECURITY: Prevent XSS in comments
        "author_id": current_user["id"],
        "author_name": current_user["full_name"],
        "parent_comment_id": comment.parent_comment_id,
        "created_at": now,
        "updated_at": now
    }

    await db.notice_comments.insert_one(comment_doc)

    # Update comment count
    await db.notices.update_one(
        {"id": notice_id, "building_id": building_id},
        {"$inc": {"comment_count": 1}, "$set": {"updated_at": now}}
    )

    # Send email notification for reply if parent comment exists
    if comment.parent_comment_id:
        asyncio.create_task(_send_reply_notification(notice_id, comment_doc))

        # Also create in-app notification
        asyncio.create_task(_create_reply_notification(notice_id, comment_doc))

    return NoticeCommentResponse(**comment_doc)


@router.get("/notices/{notice_id}/comments", response_model=List[NoticeCommentResponse])
async def get_notice_comments(
        notice_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get all comments for a notice"""
    comments = await db.notice_comments.find(
        {"notice_id": notice_id, "building_id": building_id},
        {"_id": 0}
    ).sort("created_at", 1).to_list(500)

    if is_impersonating(current_user):
        for c in comments:
            c["author_name"] = "Resident"

    return [NoticeCommentResponse(**c) for c in comments]


# ==================== NOTICE ACKNOWLEDGMENT ROUTES ====================

@router.post("/notices/{notice_id}/acknowledge", response_model=NoticeAcknowledgmentResponse)
async def acknowledge_notice(
        notice_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Acknowledge reading a notice.
    
    Records that the user has read and acknowledged the notice.
    """
    # Verify notice exists
    notice = await db.notices.find_one({"id": notice_id, "building_id": building_id}, {"_id": 0})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    # Check if already acknowledged
    existing = await db.notice_acknowledgments.find_one({
        "building_id": building_id,
        "notice_id": notice_id,
        "user_id": current_user["id"]
    })

    if existing:
        return NoticeAcknowledgmentResponse(**{k: v for k, v in existing.items() if k != "_id"})

    ack_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    ack_doc = {
        "id": ack_id,
        "building_id": building_id,
        "notice_id": notice_id,
        "user_id": current_user["id"],
        "user_name": current_user["full_name"],
        "acknowledged_at": now
    }

    await db.notice_acknowledgments.insert_one(ack_doc)

    # Update acknowledgment count
    await db.notices.update_one(
        {"id": notice_id, "building_id": building_id},
        {"$inc": {"acknowledgment_count": 1}}
    )

    return NoticeAcknowledgmentResponse(**ack_doc)


@router.get("/notices/{notice_id}/acknowledgments", response_model=List[NoticeAcknowledgmentResponse])
async def get_notice_acknowledgments(
        notice_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get all acknowledgments for a notice"""
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized to view acknowledgments")

    acks = await db.notice_acknowledgments.find(
        {"notice_id": notice_id, "building_id": building_id},
        {"_id": 0}
    ).sort("acknowledged_at", -1).to_list(500)

    return [NoticeAcknowledgmentResponse(**a) for a in acks]


# ==================== EMAIL NOTIFICATION PREFERENCES ====================

@router.get("/notifications/preferences", response_model=EmailNotificationPreferencesResponse)
async def get_notification_preferences(current_user: dict = Depends(get_approved_user)):
    """Get email notification preferences for current user"""
    prefs = await db.email_notification_preferences.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0}
    )

    if not prefs:
        # Return defaults
        return EmailNotificationPreferencesResponse(
            user_id=current_user["id"],
            notices_enabled=True,
            announcements_enabled=True,
            maintenance_updates_enabled=True,
            discussion_replies_enabled=True,
            levy_reminders_enabled=True,
            tax_reminders_enabled=False,
            water_reminders_enabled=False,
            agm_reminders_enabled=False,
            digest_frequency="immediate",
            email_format="html",
            updated_at=datetime.now(timezone.utc).isoformat()
        )

    # Backfill new fields missing from older documents
    prefs.setdefault("levy_reminders_enabled", True)
    prefs.setdefault("tax_reminders_enabled", False)
    prefs.setdefault("water_reminders_enabled", False)
    prefs.setdefault("agm_reminders_enabled", False)
    prefs.setdefault("email_format", "html")
    return EmailNotificationPreferencesResponse(**prefs)


@router.get("/communication/recipients")
async def get_recipients(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get list of recipient groups and unit numbers for manual emails.
    Roles allowed: Chairman, EC Member, Strata Manager, Super Admin.
    PII (names) is masked.
    """
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 1. Standard Groups
    groups = [
        {"id": "all", "name": "All Users"},
        {"id": "owner", "name": "All Owners"},
        {"id": "tenant", "name": "All Tenants"},
        {"id": "ec_member", "name": "All EC Members (incl. Chairman)"},
    ]

    # 2. Individual Units
    units = await db.units.find({"building_id": building_id}, {"_id": 0, "unit_number": 1}).sort("unit_number",
                                                                                                 1).to_list(200)
    unit_list = [{"id": u["unit_number"], "name": f"Unit {u['unit_number']}"} for u in units]

    return {
        "groups": groups,
        "units": unit_list
    }


@router.post("/communication/send-manual-email")
async def send_manual_email(
        data: ManualEmailRequest,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Send a manual email to selected recipients.
    Roles allowed: Chairman, EC Member, Strata Manager, Super Admin.
    """
    if effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Sanitize HTML content from Tiptap to prevent XSS
    sanitized_content = nh3.clean(data.content)

    # Resolve recipients
    # Performance Optimization⚡: Consolidated three sequential database queries into a single $or query.
    # This reduces database round-trips from O(3) to O(1).
    or_filters = []

    if data.target_roles:
        if "all" in data.target_roles:
            or_filters.append({"building_id": building_id, "is_active": True})
        else:
            # Handle EC Members specially (includes chairman)
            roles = list(data.target_roles)
            if "ec_member" in roles:
                roles = list(set(roles + [UserRole.EC_MEMBER]))
            or_filters.append({"building_id": building_id, "role": {"$in": roles}, "is_active": True})

    if data.target_units:
        or_filters.append({"building_id": building_id, "unit_number": {"$in": data.target_units}, "is_active": True})

    if data.target_users:
        or_filters.append({"id": {"$in": data.target_users}})

    if or_filters:
        users = await db.users.find({"$or": or_filters}, {"email": 1}).to_list(3000)
        target_emails = [u["email"] for u in users if u.get("email")]
    else:
        target_emails = []

    # Deduplicate
    unique_emails = list(set(target_emails))

    if not unique_emails:
        raise HTTPException(status_code=400, detail="No valid recipients found")

    # Performance Optimization⚡: Hoisted template generation and parallelized email dispatch.
    # This reduces cumulative latency for bulk emails from O(N*latency) to O(latency).

    # Get building settings for branding
    settings_doc = await get_general_settings_or_default(building_id, {"_id": 0})
    b_name = settings_doc.get("building_name", "Our Residences")
    b_addr = settings_doc.get("building_address", "")
    safe_b_name = html_lib.escape(b_name)
    safe_b_addr = html_lib.escape(b_addr)
    # The sender line sits in the same HTML body as safe_b_name/safe_b_addr but was
    # interpolated raw. full_name is a user-editable profile field, so anyone able to
    # send a building-wide email could inject markup into the footer of every copy.
    safe_sender = html_lib.escape(str(current_user.get("full_name") or ""))
    safe_sender_role = html_lib.escape(str(current_user.get("role") or "").replace("_", " ").title())

    html_body = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #2F4F4F; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
            <h1>{safe_b_name}</h1>
        </div>
        <div style="padding: 20px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 8px 8px;">
            {sanitized_content}
        </div>
        <div style="text-align: center; color: #6b7280; font-size: 12px; margin-top: 20px;">
            <p>Sent by {safe_sender} ({safe_sender_role})</p>
            <p>{safe_b_name} - {safe_b_addr}</p>
        </div>
    </div>
    """

    email_tasks = [
        send_email_async(email, data.subject, html_body, context="manual_email")
        for email in unique_emails
    ]

    sent_count = 0
    if email_tasks:
        results = await asyncio.gather(*email_tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Failed to send manual email to {unique_emails[i]}: {res}")
            elif isinstance(res, dict) and res.get("success"):
                sent_count += 1
            else:
                error_msg = res.get("error") if isinstance(res, dict) else "Unknown failure"
                logger.error(f"Failed to send manual email to {unique_emails[i]}: {error_msg}")

    # Create audit log
    await create_audit_log(
        action="send_manual_email",
        resource_type="communication",
        resource_id=str(uuid.uuid4()),
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "subject": data.subject,
            "recipient_count": len(unique_emails),
            "sent_count": sent_count,
            "target_roles": data.target_roles,
            "target_units": data.target_units
        }
    )

    return {"success": True, "sent_count": sent_count, "total_recipients": len(unique_emails)}


@router.put("/notifications/preferences", response_model=EmailNotificationPreferencesResponse)
async def update_notification_preferences(
        preferences: EmailNotificationPreferences,
        current_user: dict = Depends(get_approved_user)
):
    """Update email notification preferences"""
    now = datetime.now(timezone.utc).isoformat()

    prefs_doc = {
        "user_id": current_user["id"],
        **preferences.model_dump(),
        "updated_at": now
    }

    await db.email_notification_preferences.update_one(
        {"user_id": current_user["id"]},
        {"$set": prefs_doc},
        upsert=True
    )

    return EmailNotificationPreferencesResponse(**prefs_doc)


# ==================== HELPER FUNCTIONS FOR EMAIL NOTIFICATIONS ====================

def _build_notice_doc(notice: NoticeCreate, current_user: dict, building_id: str, now: str, action: str) -> dict:
    # Title stored as plain text because React renders text nodes safely.
    # HTML content is sanitized before storage; email builders escape at render time.
    notice_dict = notice.model_dump()
    notice_dict["title"] = notice_dict.get("title", "").strip()
    notice_dict["content"] = nh3.clean(notice_dict.get("content", ""))

    return {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        **notice_dict,
        "created_by": current_user["id"],
        "created_by_name": current_user["full_name"],
        "history": [{
            "action": action,
            "user_id": current_user["id"],
            "user_name": current_user["full_name"],
            "timestamp": now,
            "details": {**notice_dict, "broadcast": action == "broadcast"},
        }],
        "created_at": now,
        "updated_at": now,
        "acknowledgment_count": 0,
        "comment_count": 0,
    }


async def _resolve_broadcast_buildings(current_user: dict, target_building_ids: Optional[List[str]]) -> List[dict]:
    if effective_role(current_user) == UserRole.SUPER_ADMIN:
        if target_building_ids:
            return await db.buildings.find(
                {"id": {"$in": target_building_ids}, "is_active": True}, {"_id": 0}
            ).to_list(100)
        return await db.buildings.find({"is_active": True}, {"_id": 0}).to_list(100)

    memberships = await db.memberships.find(
        {"user_id": current_user["id"], "is_active": True}, {"building_id": 1}
    ).to_list(50)
    accessible_ids = [m["building_id"] for m in memberships]
    if target_building_ids:
        accessible_ids = [bid for bid in target_building_ids if bid in accessible_ids]
    if not accessible_ids:
        raise HTTPException(status_code=403, detail="No accessible buildings to broadcast to")
    return await db.buildings.find(
        {"id": {"$in": accessible_ids}, "is_active": True}, {"_id": 0}
    ).to_list(50)


async def _send_notice_emails(notice_doc: dict):
    """
    Send email notifications for a new notice.
    Performance Optimization⚡: Batch fetched preferences to eliminate N+1 query pattern and parallelized email dispatch.
    """
    try:
        # Get all users in the building who should receive this notice
        building_id = notice_doc.get("building_id")
        target_lots = notice_doc.get("target_lots")
        memberships = await db.memberships.find({"building_id": building_id}).to_list(1000)
        user_ids = [m["user_id"] for m in memberships]

        if target_lots:
            unit_assignments = await db.user_units.find(
                {"building_id": building_id, "unit_number": {"$in": target_lots}},
                {"_id": 0, "user_id": 1},
            ).to_list(None)
            lot_user_ids = {ua["user_id"] for ua in unit_assignments}
            user_ids = [uid for uid in user_ids if uid in lot_user_ids]

        query = {"id": {"$in": user_ids}}
        if notice_doc.get("target_roles"):
            query["role"] = {"$in": notice_doc["target_roles"]}

        users = await db.users.find(query, {"_id": 0, "id": 1, "email": 1, "full_name": 1}).to_list(1000)
        if not users:
            return

        # Performance Optimization⚡: Batch fetch all relevant preferences in one round-trip
        user_ids = [u["id"] for u in users]
        prefs_list = await db.email_notification_preferences.find({"user_id": {"$in": user_ids}}).to_list(len(user_ids))
        prefs_map = {p["user_id"]: p for p in prefs_list}

        # Performance Optimization⚡: Hoisted template generation outside the loop
        html, text = get_email_template(
            "notice",
            title=notice_doc["title"],
            content=notice_doc["content"],
            priority=notice_doc["priority"],
            category=notice_doc["category"],
            requires_acknowledgment=notice_doc.get("requires_acknowledgment", False)
        )
        subject = f"[{notice_doc['priority'].upper()}] New Notice: {notice_doc['title']}"

        email_tasks = []
        target_emails = []

        for user in users:
            # Check user's notification preferences from in-memory map
            prefs = prefs_map.get(user["id"])
            if prefs and not prefs.get("notices_enabled", True):
                continue

            if user.get("email"):
                target_emails.append(user["email"])
                email_tasks.append(send_email_async(user["email"], subject, html, text, context="notice_email"))

        if email_tasks:
            results = await asyncio.gather(*email_tasks, return_exceptions=True)
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error(f"Failed to send notice email to {target_emails[i]}: {res}")
                elif isinstance(res, dict) and not res.get("success"):
                    logger.error(f"Failed to send notice email to {target_emails[i]}: {res.get('error')}")

    except Exception as e:
        logger.error(f"Failed to send notice emails: {str(e)}")


async def _send_reply_notification(notice_id: str, comment_doc: dict):
    """Send email notification for a reply to a comment"""
    try:
        # Get parent comment to find original author
        parent = await db.notice_comments.find_one(
            {"id": comment_doc["parent_comment_id"]},
            {"_id": 0}
        )

        if not parent:
            return

        # Get parent author's details
        parent_author = await db.users.find_one(
            {"id": parent["author_id"]},
            {"_id": 0, "id": 1, "email": 1, "full_name": 1}
        )

        if not parent_author:
            return

        # Check notification preferences
        prefs = await db.email_notification_preferences.find_one({"user_id": parent_author["id"]})
        if prefs and not prefs.get("discussion_replies_enabled", True):
            return

        # Get notice details
        notice = await db.notices.find_one({"id": notice_id}, {"_id": 0})
        if not notice:
            return

        # Generate email
        safe_author_name = html_lib.escape(str(comment_doc.get('author_name') or 'Someone'))
        safe_notice_title = html_lib.escape(str(notice.get('title') or 'a notice'))
        safe_comment_content = html_lib.escape(str(comment_doc.get('content') or ''))
        # Get building settings for branding
        settings_doc = await get_general_settings_or_default(notice.get("building_id"), {"_id": 0})
        b_name = settings_doc.get("building_name", "Our Residences")
        b_addr = settings_doc.get("building_address", "")
        safe_b_name = html_lib.escape(b_name)
        safe_b_addr = html_lib.escape(b_addr)

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #2F4F4F; color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
                .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 8px 8px; }}
                .comment {{ background: white; padding: 15px; border-left: 3px solid #2F4F4F; margin: 15px 0; }}
                .footer {{ text-align: center; color: #666; font-size: 12px; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{safe_b_name}</h1>
                </div>
                <div class="content">
                    <h2>New Reply to Your Comment</h2>
                    <p><strong>{safe_author_name}</strong> replied to your comment on notice: <strong>{safe_notice_title}</strong></p>
                    <div class="comment">
                        <p>{safe_comment_content}</p>
                    </div>
                </div>
                <div class="footer">
                    <p>{safe_b_name} - {safe_b_addr}</p>
                </div>
            </div>
        </body>
        </html>
        """

        text = f"New reply from {comment_doc['author_name']} on notice '{notice['title']}':\n\n{comment_doc['content']}"

        await send_email_async(
            parent_author["email"],
            f"New reply to your comment on: {notice['title']}",
            html,
            text
        )

    except Exception as e:
        logger.error(f"Failed to send reply notification: {str(e)}")


async def _create_announcement_notifications(announcement_doc: dict):
    """Create in-app notifications for a new announcement.

    GAP-CMS-006: When target_lots is set, only notify users assigned to those lots
    (via user_units collection).  Falls back to broadcast when no lot filter is set.
    """
    try:
        building_id = announcement_doc.get("building_id")
        target_lots = announcement_doc.get("target_lots")

        if target_lots:
            # Distinct user_ids across targeted lots — deduplicates co-owners with multiple rows
            unit_assignments = await db.user_units.find(
                {"building_id": building_id, "unit_number": {"$in": target_lots}},
                {"_id": 0, "user_id": 1},
            ).to_list(None)
            unique_user_ids = list({ua["user_id"] for ua in unit_assignments})
            tasks = [
                create_user_notification(
                    user_id=uid,
                    title="New Announcement",
                    message=announcement_doc["title"],
                    notification_type="announcement",
                    link="/community/notices",
                    building_id=building_id,
                )
                for uid in unique_user_ids
            ]
            if tasks:
                await asyncio.gather(*tasks)
        else:
            await broadcast_user_notification(
                recipient_roles=["all"],
                title="New Announcement",
                message=announcement_doc["title"],
                notification_type="announcement",
                link="/community/notices",
                building_id=building_id
            )
    except Exception as e:
        logger.error(f"Failed to create announcement notifications: {str(e)}")


async def _create_notice_notifications(notice_doc: dict):
    """Create in-app notifications for a new notice"""
    try:
        building_id = notice_doc.get("building_id")
        target_lots = notice_doc.get("target_lots")

        if target_lots:
            unit_assignments = await db.user_units.find(
                {"building_id": building_id, "unit_number": {"$in": target_lots}},
                {"_id": 0, "user_id": 1},
            ).to_list(None)
            unique_user_ids = list({ua["user_id"] for ua in unit_assignments})
            tasks = [
                create_user_notification(
                    user_id=uid,
                    title="New Official Notice",
                    message=notice_doc["title"],
                    notification_type="notice",
                    link="/community/notices",
                    building_id=building_id,
                )
                for uid in unique_user_ids
            ]
            if tasks:
                await asyncio.gather(*tasks)
        else:
            await broadcast_user_notification(
                recipient_roles=notice_doc.get("target_roles") or ["all"],
                title="New Official Notice",
                message=notice_doc["title"],
                notification_type="notice",
                link="/community/notices",
                building_id=building_id
            )
    except Exception as e:
        logger.error(f"Failed to create notice notifications: {str(e)}")


async def _create_reply_notification(notice_id: str, comment_doc: dict):
    """Create in-app notification for a reply to a comment"""
    try:
        parent = await db.notice_comments.find_one({"id": comment_doc["parent_comment_id"]})
        if parent and parent["author_id"] != comment_doc["author_id"]:
            await create_user_notification(
                user_id=parent["author_id"],
                title="New Reply to Your Comment",
                message=f"{comment_doc['author_name']} replied to your comment",
                notification_type="notice_reply",
                link=f"/community/notices"
            )
    except Exception as e:
        logger.error(f"Failed to create reply notification: {str(e)}")


async def _send_announcement_emails(announcement_doc: dict):
    """
    Send email notifications for a new announcement.
    Performance Optimization⚡: Batch fetched preferences and parallelized email dispatch.

    Sends to BOTH:
    - User's login email (e.g., owner@eastgate.com)
    - User's @eastgateresidences.com.au email (if different and exists)

    Respects user's email notification preferences.
    """
    try:
        # Get all active users in this building
        building_id = announcement_doc.get("building_id")
        target_lots = announcement_doc.get("target_lots")
        memberships = await db.memberships.find({"building_id": building_id}).to_list(1000)
        user_ids = [m["user_id"] for m in memberships]

        # GAP-CMS-006: when target_lots is set, restrict to users assigned to those lots.
        # to_list(None) + set deduplication handles co-owners with multiple user_units rows.
        if target_lots:
            unit_assignments = await db.user_units.find(
                {"building_id": building_id, "unit_number": {"$in": target_lots}},
                {"_id": 0, "user_id": 1},
            ).to_list(None)
            lot_user_ids = {ua["user_id"] for ua in unit_assignments}
            user_ids = [uid for uid in user_ids if uid in lot_user_ids]

        users = await db.users.find(
            {"id": {"$in": user_ids}, "is_active": True},
            {"_id": 0, "id": 1, "email": 1, "mail_username": 1, "full_name": 1}
        ).to_list(1000)

        if not users:
            return {"sent": 0, "skipped": 0}

        # Performance Optimization⚡: Batch fetch all relevant preferences in one round-trip
        user_ids = [u["id"] for u in users]
        prefs_list = await db.email_notification_preferences.find({"user_id": {"$in": user_ids}}).to_list(len(user_ids))
        prefs_map = {p["user_id"]: p for p in prefs_list}

        # Performance Optimization⚡: Hoisted template generation outside the loop
        html, text = get_email_template(
            "announcement",
            title=announcement_doc["title"],
            content=announcement_doc["content"],
            priority=announcement_doc["priority"]
        )
        subject = f"[{announcement_doc['priority'].upper()}] New Announcement: {announcement_doc['title']}"

        email_tasks = []
        all_target_emails = []
        skipped_count = 0

        for user in users:
            # Check user's notification preferences from in-memory map (default to enabled)
            prefs = prefs_map.get(user["id"])
            if prefs and not prefs.get("announcements_enabled", True):
                skipped_count += 1
                continue

            # Collect unique email addresses for this user
            email_addresses = list({
                e for e in [user.get("email"), user.get("mail_username")] if e
            })

            for email in email_addresses:
                all_target_emails.append(email)
                email_tasks.append(send_email_async(email, subject, html, text, context="announcement_email"))

        sent_count = 0
        if email_tasks:
            results = await asyncio.gather(*email_tasks, return_exceptions=True)
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error(f"Failed to send announcement email to {all_target_emails[i]}: {res}")
                elif isinstance(res, dict) and res.get("success"):
                    sent_count += 1
                else:
                    error_msg = res.get("error") if isinstance(res, dict) else "Unknown failure"
                    logger.error(f"Failed to send announcement email to {all_target_emails[i]}: {error_msg}")

        logger.info(f"Announcement emails sent: {sent_count}, skipped: {skipped_count}")
        return {"sent": sent_count, "skipped": skipped_count}

    except Exception as e:
        logger.error(f"Failed to send announcement emails: {str(e)}")
        return {"error": str(e)}


__all__ = ["router"]
