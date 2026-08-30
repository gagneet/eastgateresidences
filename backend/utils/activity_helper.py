"""
Activity Feed Helper

Handles logging activities to the dedicated activities collection.
"""
import uuid
from datetime import datetime, timezone

from typing import Dict, Any, Optional


async def log_activity(
        activity_type: str,
        title: str,
        entity_id: Optional[str] = None,
        building_id: str = "eastgate",
        visibility: str = "all",
        priority: int = 3,
        metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Log an activity to the activities collection.

    activity_type: "announcement" | "document" | "maintenance" | "vote" | "marketplace" | "resident"
    """
    from database import db

    activity_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    activity_doc = {
        "id": activity_id,
        "type": activity_type,
        "title": title,
        "entity_id": entity_id,
        "building_id": building_id,
        "visibility": visibility,
        "priority": priority,
        "metadata": metadata or {},
        "created_at": now
    }

    await db.activities.insert_one(activity_doc)
    return activity_id
