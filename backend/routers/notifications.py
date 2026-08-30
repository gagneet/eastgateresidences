"""
Notifications router module.

This module handles all notification-related routes including sending
notifications via email/SMS/WhatsApp and automated levy reminders.
"""

# @featuretrace:email-delivery — Notification emails, levy reminder emails, and scoped preference fallback.
# Layer: router
# Data flow: NotificationsPage/EmailPreferencesPage -> /notifications/send + /notifications/preferences -> db.notifications + db.email_preferences + send_email_async() (building-scoped).
# Related: backend/utils/email.py, backend/routers/communication.py, frontend/src/pages/dashboard/EmailPreferencesPage.jsx, docs/architecture/mindmap/email-delivery.md

import html as html_lib
import uuid
from datetime import datetime, timezone

import asyncio
import logging
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request
from typing import List, Optional

from database import db
from models.notification import (
    NotificationCreate,
    NotificationResponse,
    UserNotificationResponse,
)
from services.settings_service import get_general_settings_or_default
from utils.auth import get_current_user, get_current_building, effective_role
from utils.email import send_email_async
from utils.finance_helpers import get_latest_levy_year, get_levy_rates
from utils.helpers import get_current_timestamp, broadcast_user_notification, create_audit_log
from utils.permissions import get_user_permissions
from utils.rate_limit import limiter

# Email providers
try:
    import resend

    RESEND_AVAILABLE = True
except ImportError:
    RESEND_AVAILABLE = False

# Create router
router = APIRouter(prefix="")

# Configure logging
logger = logging.getLogger(__name__)


# ==================== NOTIFICATIONS & LEVY REMINDERS ====================

@router.post("/notifications/send", response_model=NotificationResponse)
@limiter.limit("5/minute")
async def send_notification(
        request: Request,
        data: NotificationCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Send a notification to specified recipients.
    
    Supports multiple channels (email, SMS, WhatsApp) and can target specific
    users or groups (all, owners, tenants). Requires notification sending or
    announcement posting permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_send_notifications and not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    notif_id = str(uuid.uuid4())
    now = get_current_timestamp()

    # Determine recipients
    recipients_list = []
    memberships = await db.memberships.find({"building_id": building_id}).to_list(None)
    building_user_ids = [m["user_id"] for m in memberships]

    if "all" in data.recipients:
        users = await db.users.find(
            {"id": {"$in": building_user_ids}, "is_active": True},
            {"_id": 0, "id": 1, "email": 1, "phone": 1},
        ).to_list(500)
        recipients_list = users
    elif "owners" in data.recipients:
        users = await db.users.find(
            {"id": {"$in": building_user_ids}, "is_active": True, "role": "owner"},
            {"_id": 0, "id": 1, "email": 1, "phone": 1},
        ).to_list(500)
        recipients_list = users
    elif "tenants" in data.recipients:
        users = await db.users.find(
            {"id": {"$in": building_user_ids}, "is_active": True, "role": "tenant"},
            {"_id": 0, "id": 1, "email": 1, "phone": 1},
        ).to_list(500)
        recipients_list = users
    else:
        target_ids = [uid for uid in data.recipients if uid in building_user_ids]
        users = await db.users.find(
            {"id": {"$in": target_ids}},
            {"_id": 0, "id": 1, "email": 1, "phone": 1},
        ).to_list(len(target_ids) + 1)
        recipients_list = users

    sent_count = 0
    failed_count = 0

    # Create in-app notifications
    if "in_app" in data.channels:
        roles = []
        if "all" in data.recipients:
            roles = ["all"]
        elif "owners" in data.recipients:
            roles = ["owner", "strata_admin", "ec_member"]
        elif "tenants" in data.recipients:
            roles = ["tenant"]

        if roles:
            await broadcast_user_notification(
                recipient_roles=roles,
                title=data.title,
                message=data.message,
                notification_type="general",
                link="/dashboard",
                building_id=building_id,
            )
        else:
            # Individual user IDs
            for user_id in data.recipients:
                from utils.helpers import create_user_notification
                await create_user_notification(
                    user_id=user_id,
                    title=data.title,
                    message=data.message,
                    notification_type="general",
                    link="/dashboard"
                )

    # Send via configured channels
    # Performance Optimization⚡: Using asyncio.gather for parallel email dispatch.
    # This avoids blocking the event loop and speeds up bulk notifications.
    email_tasks = []

    # Escape user input to prevent HTML injection - Security 🛡️
    safe_title = html_lib.escape(data.title)
    safe_message = html_lib.escape(data.message)

    # Get building settings for branding
    settings = await get_general_settings_or_default(building_id, {"_id": 0})
    building_name = settings.get("building_name", "Our Residences")
    building_address = settings.get("building_address", "")
    safe_building_name = html_lib.escape(building_name)
    safe_building_address = html_lib.escape(building_address)

    html_template = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #111827; background: #ffffff;">
        <h2 style="color: #1a365d;">{safe_title}</h2>
        <p style="color: #111827; line-height: 1.5;">{safe_message}</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <p style="color: #718096; font-size: 12px;">
            {safe_building_name}<br>
            {safe_building_address}
        </p>
    </div>
    """

    for recipient in recipients_list:
        if "email" in data.channels and recipient.get("email"):
            # Performance Optimization⚡: Maintain original behavior of only attempting
            # email if Resend is available, while using centralized async helper.
            if RESEND_AVAILABLE:
                email_tasks.append(send_email_async(
                    to_email=recipient["email"],
                    subject=data.title,
                    html_content=html_template,
                    text_content=f"{data.title}\n\n{data.message}\n\n{building_name}\n{building_address}".strip(),
                    context=f"notification:{notif_id}"
                ))

        if "sms" in data.channels and recipient.get("phone"):
            # SMS would be sent via Twilio - placeholder
            logger.info(f"SMS to {recipient.get('phone')}: {data.title}")

        if "whatsapp" in data.channels and recipient.get("phone"):
            # WhatsApp would be sent via Twilio/WhatsApp Business API - placeholder
            logger.info(f"WhatsApp to {recipient.get('phone')}: {data.title}")

    if email_tasks:
        email_results = await asyncio.gather(*email_tasks, return_exceptions=True)
        for res in email_results:
            if isinstance(res, Exception):
                logger.error(f"Email task failed: {res}")
                failed_count += 1
            elif isinstance(res, dict) and res.get("success"):
                sent_count += 1
            else:
                # Handle unexpected response types or error dictionaries safely
                error_msg = res.get("error") if isinstance(res, dict) else "Unknown response format"
                logger.error(f"Email delivery failed: {error_msg}")
                failed_count += 1

    notif_doc = {
        "id": notif_id,
        **data.model_dump(),
        "status": "sent" if sent_count > 0 else "failed",
        "sent_count": sent_count,
        "failed_count": failed_count,
        "created_by": current_user["id"],
        "created_at": now,
        "sent_at": now if sent_count > 0 else None
    }

    await db.notifications.insert_one(notif_doc)

    # Audit notification sending - Security 🛡️
    try:
        await create_audit_log(
            "notification_sent",
            "notification",
            notif_id,
            current_user["id"],
            current_user.get("full_name", "Unknown"),
            {
                "recipients_count": len(recipients_list),
                "channels": data.channels,
                "type": data.notification_type
            },
            building_id
        )
    except Exception as e:
        logger.error(f"Failed to create audit log for notification {notif_id}: {e}")

    return NotificationResponse(**notif_doc)


@router.post("/notifications/levy-reminder")
async def send_levy_reminder(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Send levy reminder to all owners.
    
    Automatically finds the next levy due date and sends personalized reminders
    to all active owners with their unit-specific levy amount. Requires notification
    sending permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_send_notifications:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get next levy due date
    today = datetime.now().date()
    levy_events = await db.events.find(
        {"event_type": "levy_due", "building_id": building_id}, {"_id": 0}
    ).to_list(10)

    next_levy = None
    for event in levy_events:
        event_date = datetime.fromisoformat(event["start_date"]).date()
        if event_date > today:
            next_levy = event
            break

    if not next_levy:
        return {"message": "No upcoming levy due dates found"}

    # Get levy rates from annual_levies (new schema) - Performance Optimization⚡
    year = await get_latest_levy_year(building_id) or str(datetime.now().year)
    levy_rates = await get_levy_rates(year, building_id)

    # Get building settings for branding
    settings_doc = await get_general_settings_or_default(building_id, {"_id": 0})
    building_name = settings_doc.get("building_name", "Our Residences")
    building_address = settings_doc.get("building_address", "")
    safe_building_name = html_lib.escape(building_name)
    safe_building_address = html_lib.escape(building_address)

    # Parallel fetch units and owners to avoid N+1 queries - Performance Optimization⚡
    memberships_task = db.memberships.find({"building_id": building_id, "is_active": True}).to_list(None)
    units_task = db.units.find({"building_id": building_id}, {"_id": 0}).to_list(200)

    memberships, units = await asyncio.gather(memberships_task, units_task)
    member_ids = [m["user_id"] for m in memberships]
    # 'chairman' is not a top-level role (see rules/post-compact-critical.md) — a chairman is
    # a user with role='ec_member' and ec_position='CHAIRMAN', already covered below.
    owners = await db.users.find(
        {"id": {"$in": member_ids}, "role": {"$in": ["owner", "strata_admin", "ec_member", "super_admin"]},
         "is_active": True},
        {"_id": 0, "email": 1, "mail_username": 1, "full_name": 1}
    ).to_list(500)

    # Pre-map owners by email and mail alias for O(1) lookup
    owner_map = {}
    for o in owners:
        if o.get("email"):
            owner_map[o["email"]] = o
        if o.get("mail_username"):
            owner_map[o["mail_username"]] = o

    # Use unit-centric approach: cover primary AND secondary owners (owner_email_b)
    email_tasks = []
    # Limit concurrent network I/O tasks to avoid hitting descriptor limits - Bolt ⚡
    semaphore = asyncio.Semaphore(10)

    async def _throttled_send(recipient, subject, html_content, text_content, context):
        """Generated function header.

        Function: _throttled_send
        Path: backend/routers/notifications.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        async with semaphore:
            return await send_email_async(
                to_email=recipient,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                context=context
            )

    for unit in units:
        unit_number = unit.get("unit_number")
        primary_email = unit.get("owner_email")
        if not unit_number or not primary_email:
            continue

        secondary_email = unit.get("owner_email_b")
        entitlement = unit.get("entitlement", 115)

        # Calculate levy from UOE × levy_per_uoe rates (new schema)
        admin_annual = round(levy_rates.get("admin_annual", 0) * entitlement, 2)
        sinking_annual = round(levy_rates.get("sinking_annual", 0) * entitlement, 2)
        annual_levy = admin_annual + sinking_annual
        quarterly_levy = round(annual_levy / 4, 2)

        # Look up registered user to get full_name and mail alias
        # Bolt ⚡: Ensure owner is a dict even if lookup fails to avoid AttributeError
        owner = owner_map.get(primary_email) or {}
        mail_alias = owner.get("mail_username")
        display_name = owner.get("full_name") or unit.get("owner_name", "Resident")

        # Deduplicated recipient set for this unit
        recipients = list({e for e in [primary_email, secondary_email, mail_alias] if e})

        # Escape user input to prevent HTML injection - Security 🛡️
        safe_display_name = html_lib.escape(str(display_name or "Resident"))
        safe_due_date = html_lib.escape(str(next_levy['start_date'] or ""))
        safe_unit_number = html_lib.escape(str(unit_number))

        email_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #111827; background: #ffffff;">
            <h2 style="color: #1a365d;">Levy Payment Reminder</h2>
            <p style="color: #111827;">Dear {safe_display_name},</p>
            <p style="color: #111827;">This is a friendly reminder that your quarterly strata levy is due on <strong>{safe_due_date}</strong>.</p>
            <div style="background: #f7fafc; color: #111827; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <p style="margin: 5px 0;"><strong>Amount Due:</strong> ${quarterly_levy:,.2f}</p>
                <p style="margin: 5px 0;"><strong>Due Date:</strong> {safe_due_date}</p>
                <p style="margin: 5px 0;"><strong>Unit:</strong> {safe_unit_number}</p>
            </div>
            <h3 style="color: #2d3748;">Payment Methods</h3>
            <ul style="color: #4a5568;">
                <li><strong>BPAY:</strong> Biller Code XXXXXX, Ref: Your Unit Number</li>
                <li><strong>DEFT:</strong> <a href="https://www.dfrportal.com.au">www.dfrportal.com.au</a></li>
                <li><strong>Direct Debit:</strong> Contact strata manager</li>
                <li><strong>Credit Card:</strong> 2% surcharge applies</li>
            </ul>
            <p style="color: #111827;">If you have already made this payment, please disregard this notice.</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #718096; font-size: 12px;">
                {safe_building_name} | {safe_building_address}<br>
                This is an automated reminder from your strata management system.
            </p>
        </div>
        """
        text_body = (
            f"Levy Payment Reminder\n\n"
            f"Dear {display_name or 'Resident'},\n\n"
            f"Your quarterly strata levy is due on {next_levy['start_date']}.\n\n"
            f"Amount Due: ${quarterly_levy:,.2f}\n"
            f"Due Date: {next_levy['start_date']}\n"
            f"Unit: {unit_number}\n\n"
            "Payment methods: BPAY, DEFT, direct debit, or credit card.\n\n"
            "If you have already made this payment, please disregard this notice."
        )
        subject = f"Levy Reminder - Due {next_levy['start_date']}"
        for recipient in recipients:
            # Performance Optimization⚡: Parallelized email dispatch using asyncio.gather.
            # This avoids blocking the event loop and reduces latency from O(N) to O(1).
            email_tasks.append(_throttled_send(
                recipient,
                subject,
                email_body,
                text_body,
                f"manual_levy_reminder:unit={unit_number}"
            ))

    sent_count = 0
    if email_tasks:
        results = await asyncio.gather(*email_tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Levy reminder email task failed: {res}")
            elif isinstance(res, dict) and res.get("success"):
                sent_count += 1
            else:
                logger.error(f"Levy reminder email delivery failed: {res}")

    return {"message": f"Sent {sent_count} levy reminders", "next_due_date": next_levy["start_date"]}


@router.get("/notifications", response_model=List[NotificationResponse])
async def get_notifications(current_user: dict = Depends(get_current_user)):
    """
    Get a list of all sent notifications.
    
    Returns notification history with send statistics. Requires notification
    sending or announcement posting permissions.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_send_notifications and not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    notifications = await db.notifications.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return [NotificationResponse(**n) for n in notifications]


# ==================== USER-SPECIFIC NOTIFICATIONS ====================

@router.get("/notifications/unread", response_model=List[UserNotificationResponse])
async def get_unread_user_notifications(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Get unread notifications for the current user."""
    notifications = await db.user_notifications.find(
        {"user_id": current_user["id"], "is_read": False, "is_test_data": {"$ne": True}},
        {"_id": 0}
    ).sort("created_at", -1).to_list(50)
    return [UserNotificationResponse(**n) for n in notifications]


@router.get("/notifications/history", response_model=List[UserNotificationResponse])
async def get_user_notification_history(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        limit: int = 50,
):
    """Get all notifications for the current user (read and unread)."""
    # Cap limit at 200
    safe_limit = min(max(1, limit), 200)

    notifications = await db.user_notifications.find(
        {"user_id": current_user["id"], "is_test_data": {"$ne": True}},
        {"_id": 0}
    ).sort("created_at", -1).to_list(safe_limit)
    return [UserNotificationResponse(**n) for n in notifications]


@router.put("/notifications/{notif_id}/read")
async def mark_notification_as_read(
        notif_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Mark a specific notification as read."""
    result = await db.user_notifications.update_one(
        {"id": notif_id, "user_id": current_user["id"]},
        {"$set": {"is_read": True}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}


@router.put("/notifications/read-all")
async def mark_all_notifications_as_read(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Mark all unread notifications for the current user as read."""
    await db.user_notifications.update_many(
        {"user_id": current_user["id"], "is_read": False},
        {"$set": {"is_read": True}}
    )
    return {"message": "All notifications marked as read"}


@router.get("/notifications/unread-count")
async def get_unread_count(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Get the count of unread notifications for the current user."""
    count = await db.user_notifications.count_documents(
        {"user_id": current_user["id"], "is_read": False, "is_test_data": {"$ne": True}}
    )
    return {"unread_count": count}


# ==================== EMAIL PREFERENCES ====================

@router.get("/notifications/preferences")
async def get_email_preferences(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Get email notification preferences for the current user."""
    prefs = await db.email_preferences.find_one(
        {"user_id": current_user["id"], "building_id": building_id},
        {"_id": 0}
    )

    # Default preferences if none exist
    if not prefs:
        return {
            "user_id": current_user["id"],
            "building_id": building_id,
            "notices_enabled": True,
            "announcements_enabled": True,
            "maintenance_updates_enabled": True,
            "discussion_replies_enabled": True,
            "digest_frequency": "immediate",
            "email_format": "html",
        }

    prefs.setdefault("email_format", "html")
    return prefs


@router.put("/notifications/preferences")
async def update_email_preferences(
        preferences: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Update email notification preferences for the current user."""
    # Ensure user_id is set
    preferences["updated_at"] = datetime.now(timezone.utc).isoformat()
    prefs_set = {k: v for k, v in preferences.items() if k not in ("user_id", "building_id")}

    # Upsert preferences
    await db.email_preferences.update_one(
        {"user_id": current_user["id"], "building_id": building_id},
        {"$set": prefs_set},
        upsert=True
    )
    preferences["user_id"] = current_user["id"]
    preferences["building_id"] = building_id

    return {"message": "Preferences updated successfully", **preferences}


# ==================== AUDIT LOGS (ADMIN ONLY) ====================

@router.get("/notifications/admin/audit-logs")
async def get_audit_logs(
        resource_type: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
        current_user: dict = Depends(get_current_user)
):
    """Get system audit logs (Admin only)."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users and effective_role(current_user) not in ["super_admin", "ec_member",
                                                                                 "strata_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to view audit logs")

    # Cap limit at 1000
    safe_limit = min(max(1, limit), 1000)

    query = {}
    if resource_type:
        query["resource_type"] = resource_type
    if action:
        query["action"] = action

    logs = await db.audit_logs.find(query, {"_id": 0}).sort("created_at", -1).to_list(safe_limit)
    return logs


__all__ = [
    "router",
]
