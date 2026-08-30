import uuid
from datetime import datetime, timezone


async def record_savings_event(
        building_id: str,
        category: str,
        original_cost_cents: int,
        final_cost_cents: int,
        saving_method: str,
        resident_summary: str,
        financial_year: str,
        db,
        user_id: str,
        description: str = "",
        evidence_documents: list = None,
        user_name: str = "",
) -> dict:
    """Generated function header.

    Function: record_savings_event
    Path: backend/services/savings_engine.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    saved_cents = original_cost_cents - final_cost_cents
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "category": category,
        "description": description,
        "original_cost_cents": original_cost_cents,
        "final_cost_cents": final_cost_cents,
        "saved_cents": saved_cents,
        "saving_method": saving_method,
        "resident_summary": resident_summary,
        "financial_year": financial_year,
        "evidence_documents": evidence_documents or [],
        "recorded_by": user_id,
        "recorded_by_name": user_name,
        "created_at": now,
        "updated_at": now,
    }
    await db.savings_events.insert_one(doc)
    return doc


async def get_savings_summary(building_id: str, financial_year: str, db) -> dict:
    """Generated function header.

    Function: get_savings_summary
    Path: backend/services/savings_engine.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    match_filter = {"financial_year": financial_year}

    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": "$category",
                "total_saved_cents": {"$sum": "$saved_cents"},
                "event_count": {"$sum": 1},
            }
        },
    ]
    by_category_cursor = db.savings_events.aggregate(pipeline)
    by_category = {}
    async for row in by_category_cursor:
        by_category[row["_id"]] = {
            "total_saved_cents": row["total_saved_cents"],
            "event_count": row["event_count"],
        }

    all_time_pipeline = [
        {"$group": {
            "_id": None,
            "total_saved_cents": {"$sum": "$saved_cents"},
            "event_count": {"$sum": 1},
        }},
    ]
    all_time_cursor = db.savings_events.aggregate(all_time_pipeline)
    all_time_total = 0
    all_time_count = 0
    async for row in all_time_cursor:
        all_time_total = row["total_saved_cents"]
        all_time_count = row["event_count"]

    ytd_total = sum(v["total_saved_cents"] for v in by_category.values())
    ytd_count = sum(v["event_count"] for v in by_category.values())

    return {
        "financial_year": financial_year,
        "ytd_saved_cents": ytd_total,
        "ytd_event_count": ytd_count,
        "all_time_saved_cents": all_time_total,
        "all_time_event_count": all_time_count,
        "by_category": by_category,
    }
