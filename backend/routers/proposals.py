import html as html_lib
import uuid
from datetime import date, datetime, timezone

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from database import db
from models.community_os import (
    ProposalCreate,
    ProposalResponse,
    ProposalStatus,
    ProposalStatusUpdate,
    VoteRequest,
)
from models.user import UserRole
from services.owner_service import is_user_current_owner_of_unit
from utils.auth import get_approved_user, get_current_building, effective_role
from utils.helpers import create_audit_log

router = APIRouter(prefix="/proposals", tags=["Proposals"])
logger = logging.getLogger(__name__)

_MANAGER_ROLES = {
    UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
    UserRole.SUPER_ADMIN,
}

_CREATE_ROLES = _MANAGER_ROLES | {UserRole.OWNER}


def _require_role(user: dict, allowed: set):
    """Generated function header.

    Function: _require_role
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    if role not in allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _next_proposal_number(building_id: str) -> str:
    """Generated function header.

    Function: _next_proposal_number
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    year = datetime.now(timezone.utc).year
    # Tenant scoping is injected automatically by TenantCollection
    # (matches both building_id and legacy plan_id documents — required for correct numbering).
    count = await db.proposals.count_documents({"year": year})
    return f"PROP-{year}-{(count + 1):04d}"


@router.get("", response_model=list)
async def list_proposals(
        status: Optional[str] = Query(None),
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    # TenantCollection injects building/plan compatibility filter automatically.
    # to_list(None) preserves the previous unbounded behavior of cursor iteration.
    """Generated function header.

    Function: list_proposals
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict = {"is_test_data": {"$ne": True}}
    if status:
        query["status"] = status

    return await db.proposals.find(query, {"_id": 0}).sort("created_at", -1).to_list(None)


@router.post("", response_model=ProposalResponse)
async def create_proposal(
        data: ProposalCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_proposal
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_role(current_user, _CREATE_ROLES)
    now = datetime.now(timezone.utc).isoformat()
    proposal_number = await _next_proposal_number(building_id)
    doc = {
        "id": str(uuid.uuid4()),
        "proposal_number": proposal_number,
        "year": datetime.now(timezone.utc).year,
        "title": html_lib.escape(data.title),
        "description": html_lib.escape(data.description),
        "proposal_type": data.proposal_type,
        "voting_type": data.voting_type,
        "status": ProposalStatus.DRAFT,
        "amount_cents": data.amount_cents,
        "voting_deadline": data.voting_deadline,
        "documents": data.documents,
        "created_by": current_user["id"],
        "created_by_name": current_user.get("full_name", ""),
        "votes_for": 0,
        "votes_against": 0,
        "votes_abstain": 0,
        "total_lots": 0,
        "outcome_notes": None,
        "opened_at": None,
        "closed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.proposals.insert_one(doc)
    asyncio.create_task(
        create_audit_log("created", "proposal", doc["id"], current_user["id"], current_user.get("full_name", ""),
                         {"proposal_number": proposal_number}, building_id)
    )
    return ProposalResponse(**doc)


@router.get("/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(
        proposal_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    # TenantCollection scopes the lookup; relying on it preserves legacy plan_id-only docs.
    """Generated function header.

    Function: get_proposal
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.proposals.find_one({"id": proposal_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return ProposalResponse(**doc)


@router.put("/{proposal_id}/status", response_model=ProposalResponse)
async def update_proposal_status(
        proposal_id: str,
        data: ProposalStatusUpdate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: update_proposal_status
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_role(current_user, _MANAGER_ROLES)
    doc = await db.proposals.find_one({"id": proposal_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Proposal not found")
    now = datetime.now(timezone.utc).isoformat()
    update = {"status": data.status, "updated_at": now}
    if data.outcome_notes:
        # Sentinel 🛡️: Stored XSS Protection — escape HTML tags in outcome notes
        update["outcome_notes"] = html_lib.escape(data.outcome_notes)
    await db.proposals.update_one({"id": proposal_id}, {"$set": update})
    doc.update(update)
    asyncio.create_task(
        create_audit_log("status_changed", "proposal", proposal_id, current_user["id"],
                         current_user.get("full_name", ""), {"new_status": data.status}, building_id)
    )
    return ProposalResponse(**doc)


@router.post("/{proposal_id}/open", response_model=ProposalResponse)
async def open_proposal(
        proposal_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: open_proposal
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_role(current_user, _MANAGER_ROLES)
    doc = await db.proposals.find_one({"id": proposal_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if doc["status"] not in (ProposalStatus.DRAFT,):
        raise HTTPException(status_code=400, detail="Proposal cannot be opened in its current state")
    total_lots = await db.units.count_documents({})
    now = datetime.now(timezone.utc).isoformat()
    update = {
        "status": ProposalStatus.OPEN,
        "opened_at": now,
        "updated_at": now,
        "total_lots": total_lots,
    }
    await db.proposals.update_one({"id": proposal_id}, {"$set": update})
    doc.update(update)
    asyncio.create_task(
        create_audit_log("opened", "proposal", proposal_id, current_user["id"], current_user.get("full_name", ""), {},
                         building_id)
    )
    return ProposalResponse(**doc)


@router.post("/{proposal_id}/close", response_model=ProposalResponse)
async def close_proposal(
        proposal_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: close_proposal
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_role(current_user, _MANAGER_ROLES)
    doc = await db.proposals.find_one({"id": proposal_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if doc["status"] != ProposalStatus.OPEN:
        raise HTTPException(status_code=400, detail="Proposal is not open")

    votes_for = doc.get("votes_for", 0)
    votes_against = doc.get("votes_against", 0)
    votes_abstain = doc.get("votes_abstain", 0)
    total_votes = votes_for + votes_against + votes_abstain

    voting_type = doc.get("voting_type", "simple_majority")
    if voting_type == "unanimous":
        passed = votes_for == total_votes and total_votes > 0
    elif voting_type == "special_resolution":
        passed = total_votes > 0 and (votes_for / total_votes) >= 0.75
    else:
        passed = votes_for > votes_against

    outcome = ProposalStatus.PASSED if passed else ProposalStatus.FAILED
    now = datetime.now(timezone.utc).isoformat()
    update = {"status": outcome, "closed_at": now, "updated_at": now}
    await db.proposals.update_one({"id": proposal_id}, {"$set": update})
    doc.update(update)
    asyncio.create_task(
        create_audit_log("closed", "proposal", proposal_id, current_user["id"], current_user.get("full_name", ""),
                         {"outcome": outcome}, building_id)
    )
    return ProposalResponse(**doc)


@router.post("/{proposal_id}/vote", response_model=dict)
async def cast_vote(
        proposal_id: str,
        data: VoteRequest,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: cast_vote
    Path: backend/routers/proposals.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = effective_role(current_user)
    if role not in (UserRole.OWNER, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
                    UserRole.SUPER_ADMIN):
        raise HTTPException(status_code=403, detail="Only lot owners and authorised managers may vote")

    requested_unit_number = data.lot_id.strip()

    # Sentinel 🛡️: BOLA Protection — verify that the voter is an active owner of the lot
    # via the owner read service. Targeted (user_id, unit_number) existence check
    # so the eligibility path stays O(1) per request while inheriting the same
    # Mongo-primary/Postgres-shadow and Postgres-primary/Mongo-shadow behavior
    # as the rest of the owner cutover slice.
    if role != UserRole.SUPER_ADMIN:
        has_ownership_claim = await is_user_current_owner_of_unit(
            building_id=building_id,
            user_id=current_user["id"],
            unit_identifier=requested_unit_number,
            as_at_date=date.today(),
        )
        if not has_ownership_claim:
            raise HTTPException(
                status_code=403,
                detail=f"You do not have an active registered ownership claim for Unit {requested_unit_number}"
            )

    # Performance Optimization⚡: Parallelize proposal existence check and duplicate vote check.
    # Tenant scoping is injected by TenantCollection — explicit building_id would skip legacy plan_id docs.
    proposal_task = db.proposals.find_one({"id": proposal_id})
    existing_vote_task = db.proposal_votes.find_one({"proposal_id": proposal_id, "lot_id": requested_unit_number})

    doc, existing_vote = await asyncio.gather(proposal_task, existing_vote_task)

    if not doc:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if doc["status"] != ProposalStatus.OPEN:
        raise HTTPException(status_code=400, detail="Voting is not open for this proposal")

    if existing_vote:
        raise HTTPException(status_code=409, detail="This lot has already voted on this proposal")

    now = datetime.now(timezone.utc).isoformat()
    vote_doc = {
        "id": str(uuid.uuid4()),
        "proposal_id": proposal_id,
        "lot_id": requested_unit_number,
        "user_id": current_user["id"],
        "vote": data.vote,
        "created_at": now,
    }
    await db.proposal_votes.insert_one(vote_doc)

    field_map = {"for": "votes_for", "against": "votes_against", "abstain": "votes_abstain"}
    field = field_map.get(data.vote)
    if field:
        await db.proposals.update_one({"id": proposal_id}, {"$inc": {field: 1}, "$set": {"updated_at": now}})

    return {"status": "ok", "vote": data.vote}
