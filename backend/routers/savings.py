import html as html_lib
from datetime import date

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from database import db
from models.community_os import SavingsEventCreate, SavingsEventResponse
from models.user import UserRole
from services.savings_engine import record_savings_event, get_savings_summary
from utils.auth import get_current_user, get_current_building
from utils.helpers import create_audit_log

router = APIRouter(prefix="/savings", tags=["Savings"])
logger = logging.getLogger(__name__)

_ALLOWED_ROLES = {
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
    UserRole.SUPER_ADMIN,
}


def _require_role(user: dict, allowed: set):
    """Generated function header.

    Function: _require_role
    Path: backend/routers/savings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    if role not in allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


@router.get("", response_model=list)
async def list_savings_events(
        financial_year: Optional[str] = Query(None),
        category: Optional[str] = Query(None),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: list_savings_events
    Path: backend/routers/savings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict = {"is_test_data": {"$ne": True}}
    if financial_year:
        query["financial_year"] = financial_year
    if category:
        query["category"] = category
    cursor = db.savings_events.find(query, {"_id": 0})
    results = []
    async for doc in cursor:
        results.append(doc)
    return results


@router.post("", response_model=SavingsEventResponse)
async def create_savings_event(
        data: SavingsEventCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_savings_event
    Path: backend/routers/savings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_role(current_user, _ALLOWED_ROLES)
    doc = await record_savings_event(
        building_id=building_id,
        category=data.category,
        original_cost_cents=data.original_cost_cents,
        final_cost_cents=data.final_cost_cents,
        saving_method=html_lib.escape(data.saving_method),
        resident_summary=html_lib.escape(data.resident_summary),
        financial_year=data.financial_year,
        db=db,
        user_id=current_user["id"],
        description=html_lib.escape(data.description),
        evidence_documents=data.evidence_documents,
        user_name=current_user.get("full_name", ""),
    )
    asyncio.create_task(
        create_audit_log("created", "savings_event", doc["id"], current_user["id"], current_user.get("full_name", ""),
                         {"category": data.category, "saved_cents": doc["saved_cents"]}, building_id)
    )
    return SavingsEventResponse(**doc)


@router.get("/summary", response_model=dict)
async def savings_summary(
        financial_year: Optional[str] = Query(None),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: savings_summary
    Path: backend/routers/savings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    year = financial_year or str(date.today().year)
    return await get_savings_summary(building_id, year, db)
