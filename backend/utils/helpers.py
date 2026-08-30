"""
Common helper utilities for the backend application.

Provides reusable functions for datetime operations, data processing,
and other common tasks to reduce code duplication.
"""
import os
import uuid
from datetime import datetime, timezone

from typing import Dict, List, Any, Optional


def get_portal_url() -> str:
    """Resolve the public portal base URL from configuration.

    Single source of truth for every user-facing link the backend emits —
    registration invites, approval emails, password resets, decision pages.

    Resolution order is FRONTEND_URL -> PUBLIC_APP_URL -> NEXT_PUBLIC_APP_URL,
    falling back to localhost for local development. **No building's domain is
    ever hardcoded**: this platform is multi-tenant, and the previous
    `"https://www.eastgateresidences.com.au"` default sent every other
    building's residents to East Gate's portal.

    Kept here rather than in config.py because it is read per-call: tests and
    operators override the environment at runtime, and a module-level constant
    would freeze the value at import time.
    """
    return (
        os.environ.get("FRONTEND_URL")
        or os.environ.get("PUBLIC_APP_URL")
        or os.environ.get("NEXT_PUBLIC_APP_URL")
        or "http://localhost:3000"
    ).rstrip("/")


def mask_email(email: Optional[str]) -> Optional[str]:
    """
    Partially mask an email address for privacy.
    Example: john.doe@example.com -> j***@example.com
    """
    if not email or "@" not in email:
        return email
    try:
        name, domain = email.split("@", 1)
        if not name:
            return email
        if len(name) == 1:
            return f"*@{domain}"
        return f"{name[0]}***@{domain}"
    except (ValueError, AttributeError):
        return email


def mask_phone(phone: Optional[str]) -> Optional[str]:
    """
    Partially mask a phone number for privacy.
    Example: +61411222333 -> +6******33
    """
    if not phone:
        return phone
    try:
        phone_str = str(phone).strip()
        if len(phone_str) < 6:
            return "******"
        return f"{phone_str[:2]}******{phone_str[-2:]}"
    except (ValueError, AttributeError):
        return phone


def get_current_utc_iso() -> str:
    """
    Get current UTC timestamp in ISO 8601 format.
    
    This is a centralized function to ensure consistent datetime format
    across the application, replacing 40+ inline datetime calls.
    
    Note: Returns an ISO-formatted datetime string, not a Unix timestamp integer.
    
    Returns:
        str: ISO-formatted UTC datetime (e.g., "2024-03-15T10:30:00.123456+00:00")
    """
    return datetime.now(timezone.utc).isoformat()


# Alias for backward compatibility and convenience
get_current_timestamp = get_current_utc_iso


def aggregate_by_field(
        items: List[Dict[str, Any]],
        group_field: str,
        sum_field: str,
        default_group: str = "Other"
) -> Dict[str, float]:
    """
    Aggregate a list of dictionaries by grouping on one field and summing another.
    
    This helper reduces repetitive grouping logic found in finance and statistics
    calculations throughout the codebase.
    
    Args:
        items: List of dictionaries to aggregate
        group_field: Field name to group by (e.g., "category")
        sum_field: Field name to sum (e.g., "amount")
        default_group: Default value if group_field is missing (default: "Other")
    
    Returns:
        Dict mapping group values to summed amounts
        
    Example:
        >>> transactions = [
        ...     {"category": "Utilities", "amount": 100},
        ...     {"category": "Utilities", "amount": 50},
        ...     {"category": "Maintenance", "amount": 200}
        ... ]
        >>> aggregate_by_field(transactions, "category", "amount")
        {'Utilities': 150, 'Maintenance': 200}
    """
    result = {}
    for item in items:
        group = item.get(group_field, default_group)
        amount = item.get(sum_field, 0)
        result[group] = result.get(group, 0) + amount
    return result


def filter_sum(items: List[Dict[str, Any]], filter_field: str, filter_value: Any, sum_field: str) -> float:
    """
    Filter items and sum a specific field.
    
    Simplifies common pattern of filtering a list and summing values,
    reducing nested list comprehensions throughout the codebase.
    
    Args:
        items: List of dictionaries to process
        filter_field: Field name to filter on
        filter_value: Value to match for filtering
        sum_field: Field name to sum
        
    Returns:
        Sum of the specified field for matching items
        
    Example:
        >>> entries = [
        ...     {"type": "income", "amount": 100},
        ...     {"type": "expense", "amount": 50},
        ...     {"type": "income", "amount": 75}
        ... ]
        >>> filter_sum(entries, "type", "income", "amount")
        175
    """
    return sum(item.get(sum_field, 0) for item in items if item.get(filter_field) == filter_value)


async def create_user_notification(
        user_id: str,
        title: str,
        message: str,
        notification_type: str,
        link: Optional[str] = None,
        building_id: Optional[str] = None
) -> str:
    """
    Create a notification for an individual user.

    Args:
        user_id: ID of the user to notify
        title: Notification title
        message: Notification message
        notification_type: Type of notification (chat, maintenance, etc.)
        link: Optional URL/route to navigate to when clicked
        building_id: Optional ID of the building this notification belongs to

    Returns:
        str: ID of the created notification or empty string on failure
    """
    if not all([user_id, title, message, notification_type]):
        return ""

    try:
        from database import db

        notif_id = str(uuid.uuid4())
        now = get_current_utc_iso()

        notif_doc = {
            "id": notif_id,
            "user_id": user_id,
            "title": title,
            "message": message,
            "type": notification_type,
            "link": link,
            "is_read": False,
            "created_at": now
        }
        if building_id is not None:
            notif_doc["building_id"] = building_id

        await db.user_notifications.insert_one(notif_doc)
        return notif_id
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to create user notification: {str(e)}")
        return ""


async def broadcast_user_notification(
        recipient_roles: List[str],
        title: str,
        message: str,
        notification_type: str,
        link: Optional[str] = None,
        building_id: Optional[str] = None
) -> int:
    """
    Send a notification to multiple users based on their roles.

    Args:
        recipient_roles: List of roles to target (e.g., ['owner', 'tenant'])
                        or ['all'] for all active users.
        title: Notification title
        message: Notification message
        notification_type: Type of notification
        link: Optional link
        building_id: Optional ID of the building these notifications belong to

    Returns:
        int: Number of users notified
    """
    try:
        from database import db

        query = {"is_active": True}
        if building_id:
            # Filter users by building membership
            memberships = await db.memberships.find({"building_id": building_id}).to_list(None)
            user_ids = [m["user_id"] for m in memberships]
            query["id"] = {"$in": user_ids}

        if "all" not in recipient_roles:
            query["role"] = {"$in": recipient_roles}

        users = await db.users.find(query, {"id": 1}).to_list(1000)

        if not users:
            return 0

        now = get_current_utc_iso()
        notifications = []

        for user in users:
            notif = {
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "title": title,
                "message": message,
                "type": notification_type,
                "link": link,
                "is_read": False,
                "created_at": now
            }
            if building_id is not None:
                notif["building_id"] = building_id
            notifications.append(notif)

        if notifications:
            await db.user_notifications.insert_many(notifications)

        return len(notifications)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to broadcast notifications: {str(e)}")
        return 0


async def create_audit_log(
        action: str,
        resource_type: str,
        resource_id: str,
        user_id: str,
        user_name: str,
        details: Optional[Dict[str, Any]] = None,
        building_id: Optional[str] = None
) -> str:
    """
    Create an audit log entry for a significant system action.

    Args:
        action: The action performed (created, updated, deleted, status_changed, etc.)
        resource_type: The type of resource (maintenance, announcement, etc.)
        resource_id: ID of the resource
        user_id: ID of the user who performed the action
        user_name: Name of the user who performed the action
        details: Optional dictionary with additional details (old/new values, etc.)
        building_id: Optional ID of the building this action belongs to

    Returns:
        str: ID of the created audit log entry or empty string on failure
    """
    if not all([action, resource_type, resource_id, user_id, user_name]):
        return ""

    try:
        from database import db

        log_id = str(uuid.uuid4())
        now = get_current_utc_iso()

        log_doc = {
            "id": log_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "user_id": user_id,
            "user_name": user_name,
            "details": details,
            "created_at": now
        }
        if building_id is not None:
            log_doc["building_id"] = building_id

        await db.audit_logs.insert_one(log_doc)
        return log_id
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to create audit log: {str(e)}")
        return ""


async def get_document_by_id(
        collection,
        document_id: str,
        not_found_message: str = "Resource not found",
        exclude_id: bool = True
) -> Dict[str, Any]:
    """
    Get a document from a MongoDB collection by ID with automatic 404 handling.
    
    This helper consolidates the repetitive pattern of finding a document and
    raising a 404 HTTPException if not found, which appears 50+ times across
    the codebase.
    
    Args:
        collection: MongoDB collection object (e.g., db.maintenance_requests)
        document_id: ID of the document to find
        not_found_message: Custom error message for 404 response
        exclude_id: Whether to exclude MongoDB's _id field from result
    
    Returns:
        Dict containing the document data
        
    Raises:
        HTTPException: 404 error if document not found
        
    Example:
        >>> request = await get_document_by_id(
        ...     db.maintenance_requests,
        ...     request_id,
        ...     "Maintenance request not found"
        ... )
    """
    from fastapi import HTTPException

    projection = {"_id": 0} if exclude_id else {}
    document = await collection.find_one({"id": document_id}, projection)

    if not document:
        raise HTTPException(status_code=404, detail=not_found_message)

    return document


async def create_notifications_batch(
        notifications: List[Dict[str, Any]],
        building_id: Optional[str] = None
) -> int:
    """
    Create multiple notifications in a single batch operation.
    
    This helper replaces N+1 query patterns where notifications are created
    in loops. Uses insert_many for better performance.
    
    Args:
        notifications: List of notification dictionaries. Each should contain:
            - user_id: str
            - title: str
            - message: str
            - type: str
            - link: Optional[str]
        building_id: Optional ID of the building these notifications belong to
            
    Returns:
        int: Number of notifications created
        
    Example:
        >>> notifications = [
        ...     {
        ...         "user_id": user1_id,
        ...         "title": "New Announcement",
        ...         "message": "Check it out",
        ...         "type": "announcement",
        ...         "link": "/community/notices"
        ...     },
        ...     {...}
        ... ]
        >>> count = await create_notifications_batch(notifications)
    """
    if not notifications:
        return 0

    try:
        from database import db

        now = get_current_utc_iso()
        notification_docs = []

        for notif in notifications:
            # Support both 'type' and 'notification_type' for backward compatibility
            notif_type = notif.get("type") or notif.get("notification_type")

            if not all([
                notif.get("user_id"),
                notif.get("title"),
                notif.get("message"),
                notif_type
            ]):
                continue

            notification_docs.append({
                "id": str(uuid.uuid4()),
                "user_id": notif["user_id"],
                "building_id": building_id,
                "title": notif["title"],
                "message": notif["message"],
                "type": notif_type,
                "link": notif.get("link"),
                "is_read": False,
                "created_at": now
            })

        if notification_docs:
            await db.user_notifications.insert_many(notification_docs)
            return len(notification_docs)

        return 0
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to create notifications batch: {str(e)}")
        return 0


__all__ = [
    'get_current_timestamp',
    'get_current_utc_iso',
    'mask_email',
    'mask_phone',
    'aggregate_by_field',
    'filter_sum',
    'create_user_notification',
    'broadcast_user_notification',
    'create_audit_log',
    'get_document_by_id',
    'create_notifications_batch',
]
