# @featuretrace:evoting — E-voting with jurisdiction-aware proxy caps, ballot audit, and quorum.
# Layer: router
# Data flow: frontend/src/pages/dashboard/AGMPage.jsx → POST /agm/{id}/proxy, POST /agm/motions/{id}/vote
#             → db.agm_attendance, db.agm_votes, db.ballot_entries (building-scoped)
# Related: backend/services/proxy_validator.py
#           backend/domain/jurisdictional_rules.py
#           backend/models/ballot_audit.py
#           backend/server.py (cast_vote endpoint at /agm/motions/{id}/vote)
#           tests/backend/test_voting_evoting.py
# Toggle: evoting
# Collection: agm, agm_attendance, agm_votes, agm_motions, ballot_entries
"""
Voting router — E-voting with jurisdiction-aware proxy caps and ballot audit.

Endpoints:
  POST   /agm/{agm_id}/proxy             — Submit a proxy form
  DELETE /agm/{agm_id}/proxy             — Revoke own proxy
  GET    /agm/{agm_id}/quorum            — Live quorum status (UE-weighted)
  GET    /agm/{agm_id}/results/weighted  — UE-weighted per-motion results
  POST   /agm/{agm_id}/close-ballot      — Seal ballot and mark AGM completed
  GET    /agm/{agm_id}/evidence-pack     — Full governance evidence pack

Note: POST /agm/motions/{motion_id}/vote (ballot cast) lives in server.py pending
migration to this router. Both share proxy_validator.py for cap enforcement.

Jurisdiction: ACT (Unit Titles (Management) Act 2011) with NSW fallback.
All data is building-scoped via the TenantScopedDatabase wrapper.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import db
from models.ballot_audit import (
    BallotEntryCreate,
    BallotSealCreate,
    verify_chain,
)
from models.user import UserRole
from services.proxy_validator import get_proxy_summary, validate_proxy_eligibility
from utils.activity_helper import log_activity
from utils.auth import effective_role, get_approved_user, get_current_building
from utils.helpers import create_audit_log, get_current_utc_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

# ── Role constants ─────────────────────────────────────────────────────────────
_BALLOT_CLOSE_ROLES = frozenset({
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,  # admin of the Strata Management Company; can close ballots
    UserRole.STRATA_MANAGER,
})

_EVIDENCE_ROLES = frozenset({
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,  # canonical role
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
})


# ── Request / Response models ─────────────────────────────────────────────────


class ProxySubmitRequest(BaseModel):
    """Body for POST /agm/{agm_id}/proxy."""

    proxy_holder_id: str = Field(
        ..., description="User ID of the person who will hold the proxy"
    )
    proxy_form_text: str = Field(
        ...,
        min_length=10,
        max_length=4000,
        description=(
            "Full text of the written proxy form as required by "
            "ACT Unit Titles (Management) Act 2011 s.77"
        ),
    )
    special_instructions: Optional[str] = Field(
        None,
        max_length=1000,
        description="Optional instructions limiting or directing the proxy holder's vote",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_agm_or_404(agm_id: str, building_id: str) -> dict:
    """Generated function header.

    Function: _get_agm_or_404
    Path: backend/routers/voting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id})
    if not agm:
        raise HTTPException(status_code=404, detail="AGM not found")
    return agm


async def _get_last_ballot_hash(agm_id: str, building_id: str) -> str:
    """Return the entry_hash of the most recent ballot entry, or the agm_id for genesis."""
    last = await db.ballot_entries.find_one(
        {"agm_id": agm_id, "building_id": building_id},
        sort=[("created_at", -1)],
    )
    if last:
        return last.get("entry_hash", agm_id)
    return agm_id  # Genesis anchor: use agm_id as prev_hash for the first entry


async def _get_building_jurisdiction(building_id: str) -> str:
    """Return the jurisdiction code for a building (defaults to 'ACT' if not set)."""
    building = await db.buildings.find_one({"id": building_id}, {"jurisdiction": 1, "_id": 0})
    if building and building.get("jurisdiction"):
        return building["jurisdiction"].upper()
    return "ACT"


async def _get_total_building_ue(building_id: str) -> int:
    """Return the sum of entitlement values for all lots in the building."""
    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": None, "total": {"$sum": "$entitlement"}}},
    ]
    result = await db.units.aggregate(pipeline).to_list(1)
    if result:
        return int(result[0].get("total", 0))
    return 0


async def _compute_quorum(agm_id: str, building_id: str) -> dict:
    """Compute UE-weighted quorum status for an AGM (ACT UTMA 2011 s.73)."""
    # Iterate cursor rather than capping with to_list() so large schemes are correct.
    attendance_records = []
    async for record in db.agm_attendance.find(
            {"agm_id": agm_id, "building_id": building_id},
            {"user_id": 1, "status": 1, "proxy_id": 1, "_id": 0},
    ):
        attendance_records.append(record)

    attending_user_ids = [
        r["user_id"]
        for r in attendance_records
        if r.get("status") in {"attending", "confirmed"}
    ]
    proxy_records = [r for r in attendance_records if r.get("proxy_id")]
    proxy_grantor_ids = [r["user_id"] for r in proxy_records]

    def _ue_pipeline(user_ids: list) -> list:
        """Generated function header.

        Function: _ue_pipeline
        Path: backend/routers/voting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return [
            {"$match": {"id": {"$in": user_ids}}},
            {"$project": {"unit_number": 1, "_id": 0}},
            {
                "$lookup": {
                    "from": "units",
                    "let": {"unit_num": "$unit_number"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$unit_number", "$$unit_num"]},
                                        {"$eq": ["$building_id", building_id]},
                                    ]
                                }
                            }
                        },
                        {"$project": {"entitlement": 1, "_id": 0}},
                    ],
                    "as": "_unit",
                }
            },
            {
                "$addFields": {
                    "ue": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$_unit.entitlement", 0]},
                            1,
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_ue": {"$sum": "$ue"},
                    "lot_count": {"$sum": 1},
                }
            },
        ]

    async def _ue_agg(user_ids: list) -> list:
        """Generated function header.

        Function: _ue_agg
        Path: backend/routers/voting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not user_ids:
            return []
        return await db.users.aggregate(_ue_pipeline(user_ids)).to_list(1)

    (
        attending_agg,
        proxy_agg,
        total_building_ue,
        total_lots,
    ) = await asyncio.gather(
        _ue_agg(attending_user_ids),
        _ue_agg(proxy_grantor_ids),
        _get_total_building_ue(building_id),
        db.units.count_documents({"building_id": building_id}),
    )

    attending_ue = int(attending_agg[0].get("total_ue", 0)) if attending_agg else 0
    attending_lots = int(attending_agg[0].get("lot_count", 0)) if attending_agg else 0
    proxy_ue = int(proxy_agg[0].get("total_ue", 0)) if proxy_agg else 0
    proxy_lots = int(proxy_agg[0].get("lot_count", 0)) if proxy_agg else 0

    total_present_ue = attending_ue + proxy_ue
    total_lots_present = attending_lots + proxy_lots
    quorum_threshold_ue = math.ceil(total_building_ue / 3) if total_building_ue > 0 else 1

    return {
        "attending_ue": attending_ue,
        "proxy_ue": proxy_ue,
        "total_present_ue": total_present_ue,
        "total_building_ue": total_building_ue,
        "quorum_threshold_ue": quorum_threshold_ue,
        "quorum_met": total_present_ue >= quorum_threshold_ue,
        "attending_lots": attending_lots,
        "proxy_lots": proxy_lots,
        "total_lots_present": total_lots_present,
        "total_lots": total_lots,
        "legislation": "ACT Unit Titles (Management) Act 2011 s.73",
    }


async def _build_weighted_results(agm_id: str, building_id: str) -> List[Dict[str, Any]]:
    """Compute UE-weighted vote tallies for every motion in the AGM."""
    motions = await db.agm_motions.find(
        {"agm_id": agm_id, "building_id": building_id}, {"_id": 0}
    ).to_list(100)

    if not motions:
        return []

    motion_ids = [m["id"] for m in motions]

    # Join votes with units to get UE weight per vote in a single aggregation.
    pipeline = [
        {
            "$match": {
                "agm_id": agm_id,
                "building_id": building_id,
                "motion_id": {"$in": motion_ids},
            }
        },
        {
            "$lookup": {
                "from": "units",
                "let": {"unit_num": "$unit_number", "bid": "$building_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$unit_number", "$$unit_num"]},
                                    {"$eq": ["$building_id", "$$bid"]},
                                ]
                            }
                        }
                    },
                    {"$project": {"entitlement": 1, "_id": 0}},
                ],
                "as": "_unit",
            }
        },
        {
            "$addFields": {
                "ue_weight": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$_unit.entitlement", 0]},
                        1,
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": "$motion_id",
                "votes_for_ue": {
                    "$sum": {"$cond": [{"$eq": ["$vote", "for"]}, "$ue_weight", 0]}
                },
                "votes_against_ue": {
                    "$sum": {"$cond": [{"$eq": ["$vote", "against"]}, "$ue_weight", 0]}
                },
                "votes_abstain_ue": {
                    "$sum": {"$cond": [{"$eq": ["$vote", "abstain"]}, "$ue_weight", 0]}
                },
                "votes_for_count": {
                    "$sum": {"$cond": [{"$eq": ["$vote", "for"]}, 1, 0]}
                },
                "votes_against_count": {
                    "$sum": {"$cond": [{"$eq": ["$vote", "against"]}, 1, 0]}
                },
                "votes_abstain_count": {
                    "$sum": {"$cond": [{"$eq": ["$vote", "abstain"]}, 1, 0]}
                },
            }
        },
    ]

    vote_rows = await db.agm_votes.aggregate(pipeline).to_list(200)
    votes_by_motion: Dict[str, dict] = {row["_id"]: row for row in vote_rows}

    results = []
    for motion in motions:
        mid = motion["id"]
        tally = votes_by_motion.get(mid, {})
        for_ue = int(tally.get("votes_for_ue", 0))
        against_ue = int(tally.get("votes_against_ue", 0))
        abstain_ue = int(tally.get("votes_abstain_ue", 0))

        contested_ue = for_ue + against_ue
        ordinary_passed = for_ue > against_ue
        # Special resolution: >= 75% of (for + against) UE
        special_passed = (
            (for_ue / contested_ue) >= 0.75 if contested_ue > 0 else False
        )

        results.append(
            {
                "motion_id": mid,
                "title": motion.get("title", ""),
                "motion_type": motion.get("motion_type", "ordinary"),
                "votes_for_ue": for_ue,
                "votes_against_ue": against_ue,
                "votes_abstain_ue": abstain_ue,
                "votes_for_count": int(tally.get("votes_for_count", 0)),
                "votes_against_count": int(tally.get("votes_against_count", 0)),
                "votes_abstain_count": int(tally.get("votes_abstain_count", 0)),
                "ordinary_resolution_passed": ordinary_passed,
                "special_resolution_passed": special_passed,
            }
        )

    return results


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/agm/{agm_id}/proxy")
async def submit_proxy(
        agm_id: str,
        data: ProxySubmitRequest,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Submit a written proxy form for an AGM.

    The grantor is always the authenticated user (current_user).  The proxy
    form must satisfy ACT s.77 requirements:
      - Must be in writing (proxy_form_text)
      - Must name a specific meeting (the AGM identified by agm_id)
      - The holder must not be a strata manager (s.76(2))

    The proxy is stored as a upsert on the grantor's agm_attendance record.
    A pre-existing proxy is silently replaced (last-write wins), consistent
    with the grantor's right to revoke and re-appoint at any time before the
    vote is cast.
    """
    agm = await _get_agm_or_404(agm_id, building_id)

    if agm.get("status") == "completed":
        raise HTTPException(
            status_code=409, detail="AGM is completed. Proxy submission is closed."
        )

    grantor_id = current_user["id"]

    # Validate proxy eligibility (jurisdiction-aware — derive from building record)
    jurisdiction = await _get_building_jurisdiction(building_id)
    validation = await validate_proxy_eligibility(
        proxy_holder_id=data.proxy_holder_id,
        agm_id=agm_id,
        grantor_user_id=grantor_id,
        building_id=building_id,
        jurisdiction=jurisdiction,
    )
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail=validation["reason"])

    now = get_current_utc_iso()

    # Upsert proxy fields onto the grantor's attendance record.
    # Creates the attendance record if it does not yet exist.
    attendance_update = {
        "agm_id": agm_id,
        "user_id": grantor_id,
        "building_id": building_id,
        "proxy_id": data.proxy_holder_id,
        "proxy_form_text": data.proxy_form_text,
        "special_instructions": data.special_instructions,
        "proxy_submitted_at": now,
        "status": "proxy",
        "updated_at": now,
    }

    await db.agm_attendance.update_one(
        {"agm_id": agm_id, "user_id": grantor_id, "building_id": building_id},
        {"$set": attendance_update},
        upsert=True,
    )

    asyncio.create_task(create_audit_log(
        action="proxy_submitted", resource_type="agm_attendance", resource_id=agm_id,
        user_id=grantor_id, user_name=current_user.get("full_name", grantor_id),
        details={"proxy_holder_id": data.proxy_holder_id, "agm_id": agm_id},
        building_id=building_id
    ))

    asyncio.create_task(
        log_activity(
            activity_type="vote",
            title=f"Proxy submitted for AGM {agm.get('title', agm_id)}",
            entity_id=agm_id,
            priority=2,
            metadata={
                "grantor_id": grantor_id,
                "holder_id": data.proxy_holder_id,
                "building_id": building_id,
            },
        )
    )

    return {
        "message": "Proxy submitted successfully.",
        "validation": validation,
        "attendance": attendance_update,
    }


@router.delete("/agm/{agm_id}/proxy")
async def revoke_proxy(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Revoke the current user's proxy for an AGM.

    Only the grantor can revoke their own proxy (ACT s.77: revocable at any
    time before the vote is cast).  If no proxy exists this is a no-op.
    """
    agm = await _get_agm_or_404(agm_id, building_id)

    if agm.get("status") == "completed":
        raise HTTPException(
            status_code=409,
            detail="AGM is completed. Proxy cannot be revoked after ballot close.",
        )

    grantor_id = current_user["id"]

    # Check that a vote has not yet been cast by the proxy holder for this unit.
    grantor_unit = current_user.get("unit_number")
    if grantor_unit:
        voted = await db.agm_votes.find_one(
            {
                "building_id": building_id,
                "agm_id": agm_id,
                "unit_number": grantor_unit,
            }
        )
        if voted:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A vote has already been cast for your unit by the proxy holder. "
                    "The proxy can no longer be revoked."
                ),
            )

    result = await db.agm_attendance.update_one(
        {"agm_id": agm_id, "user_id": grantor_id, "building_id": building_id},
        {
            "$unset": {
                "proxy_id": "",
                "proxy_form_text": "",
                "special_instructions": "",
                "proxy_submitted_at": "",
            },
            "$set": {
                "status": "attending",
                "updated_at": get_current_utc_iso(),
            },
        },
    )

    if result.matched_count == 0:
        return {"message": "No proxy record found — nothing to revoke."}

    asyncio.create_task(create_audit_log(
        action="proxy_revoked", resource_type="agm_attendance", resource_id=agm_id,
        user_id=grantor_id, user_name=current_user.get("full_name", grantor_id),
        details={"agm_id": agm_id},
        building_id=building_id
    ))

    asyncio.create_task(
        log_activity(
            activity_type="vote",
            title=f"Proxy revoked for AGM {agm.get('title', agm_id)}",
            entity_id=agm_id,
            priority=2,
            metadata={"grantor_id": grantor_id, "building_id": building_id},
        )
    )

    return {"message": "Proxy revoked successfully."}


@router.get("/agm/{agm_id}/quorum")
async def get_quorum_status(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Return live quorum status for an AGM (UE-weighted).

    Quorum threshold under ACT Unit Titles (Management) Act 2011 s.73:
    Owners of at least 1/3 of the total unit entitlements must be present
    (in person or by proxy) for the meeting to be quorate.

    Returns:
      {
        attending_ue:        int,  # UE of owners present in person
        proxy_ue:            int,  # UE represented by proxy holders
        total_present_ue:    int,  # attending_ue + proxy_ue
        total_building_ue:   int,  # sum of all unit entitlements
        quorum_threshold_ue: int,  # ceil(total_building_ue / 3)
        quorum_met:          bool,
        attending_lots:      int,  # count of lots present (person or proxy)
        total_lots:          int,
        legislation:         str,
      }
    """
    await _get_agm_or_404(agm_id, building_id)
    return await _compute_quorum(agm_id, building_id)


@router.get("/agm/{agm_id}/results/weighted")
async def get_weighted_results(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Return UE-weighted per-motion results.

    Each motion result includes:
      - votes_for_ue / votes_against_ue / votes_abstain_ue  (unit entitlement totals)
      - votes_for_count / votes_against_count (lot-count totals for reference)
      - ordinary_resolution_passed: for_ue > against_ue
      - special_resolution_passed:  for_ue >= 75% of (for + against) UE
    """
    await _get_agm_or_404(agm_id, building_id)

    results = await _build_weighted_results(agm_id, building_id)

    return {
        "agm_id": agm_id,
        "building_id": building_id,
        "results": results,
        "generated_at": get_current_utc_iso(),
    }


@router.post("/agm/{agm_id}/close-ballot")
async def close_ballot(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Close AGM voting and seal the ballot.

    Restricted to chairman, strata_manager, super_admin.

    Steps:
      1. Verify AGM is in_progress (not already completed).
      2. Retrieve all agm_votes for the AGM and write BallotEntry records for
         any votes not yet in the ballot_entries ledger (idempotent catch-up).
      3. Compute the final chain hash.
      4. Write a BallotSeal to db.ballot_seals.
      5. Update the AGM status to "completed".

    Returns the BallotSeal record.
    """
    _role = effective_role(current_user)
    if _role not in _BALLOT_CLOSE_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only strata_admin, strata_manager, or super_admin may close a ballot.",
        )

    agm = await _get_agm_or_404(agm_id, building_id)

    if agm.get("status") == "completed":
        raise HTTPException(status_code=409, detail="Ballot is already sealed.")

    if agm.get("status") not in {"in_progress", "upcoming"}:
        raise HTTPException(
            status_code=409,
            detail=f"AGM status is '{agm.get('status')}'. Must be 'in_progress' to close.",
        )

    # ── Write ballot entries for any votes not yet in the ledger ──────────────
    # Iterate cursors rather than capping with to_list() so AGMs with many lots
    # or motions are never silently truncated.
    all_votes = []
    async for vote in db.agm_votes.find(
            {"agm_id": agm_id, "building_id": building_id}, {"_id": 0}
    ):
        all_votes.append(vote)

    # Collect vote IDs already in the ledger to avoid duplicates.
    # Sort by created_at ascending so existing_entries[-1] is always the
    # chronologically-last entry (needed for chain-terminal hash recovery below).
    existing_entry_unit_motion_pairs: set[tuple[str, str]] = set()
    existing_entries = []
    async for entry in db.ballot_entries.find(
            {"agm_id": agm_id, "building_id": building_id},
            {"unit_number": 1, "motion_id": 1, "entry_hash": 1, "_id": 0},
    ).sort("created_at", 1):
        existing_entries.append(entry)
    for e in existing_entries:
        existing_entry_unit_motion_pairs.add((e["unit_number"], e["motion_id"]))

    # Determine UE for each unit in a single aggregation
    if all_votes:
        unit_numbers = list({v["unit_number"] for v in all_votes})
        unit_ue_pipeline = [
            {
                "$match": {
                    "building_id": building_id,
                    "unit_number": {"$in": unit_numbers},
                }
            },
            {"$project": {"unit_number": 1, "entitlement": 1, "_id": 0}},
        ]
        unit_ue_rows = await db.units.aggregate(unit_ue_pipeline).to_list(500)
        unit_ue_map: Dict[str, int] = {
            r["unit_number"]: int(r.get("entitlement", 1)) for r in unit_ue_rows
        }
    else:
        unit_ue_map = {}

    # Chain from the last existing entry (or AGM ID as genesis)
    prev_hash = await _get_last_ballot_hash(agm_id, building_id)
    new_entry_dicts: list[dict] = []

    for vote in all_votes:
        unit_number = vote.get("unit_number", "")
        motion_id = vote.get("motion_id", "")
        if (unit_number, motion_id) in existing_entry_unit_motion_pairs:
            continue

        ue_weight = unit_ue_map.get(unit_number, 1)
        creator = BallotEntryCreate(
            id=str(uuid.uuid4()),
            building_id=building_id,
            agm_id=agm_id,
            motion_id=motion_id,
            unit_number=unit_number,
            ue_weight=ue_weight,
            vote=vote.get("vote", "abstain"),
            proxy_holder_id=vote.get("proxy_for"),
            cast_by_user_id=vote.get("user_id", ""),
            prev_hash=prev_hash,
            created_at=get_current_utc_iso(),
        )
        entry = creator.to_entry()
        new_entry_dicts.append(entry.model_dump())
        prev_hash = entry.entry_hash

    if new_entry_dicts:
        await db.ballot_entries.insert_many(new_entry_dicts)

    # ── Determine final hash and total entry count ────────────────────────────
    # prev_hash already points to the last written entry after the loop above.
    # If no new entries were written, recover final_hash from the last pre-existing
    # entry we already fetched (avoiding a redundant find_one round-trip).
    if not new_entry_dicts and existing_entries:
        final_hash = existing_entries[-1].get("entry_hash", agm_id)
    else:
        final_hash = prev_hash

    total_entries = await db.ballot_entries.count_documents(
        {"agm_id": agm_id, "building_id": building_id}
    )

    # ── Write the seal ─────────────────────────────────────────────────────────
    seal_creator = BallotSealCreate(
        agm_id=agm_id,
        building_id=building_id,
        sealed_by_user_id=current_user["id"],
        total_entries=total_entries,
        final_hash=final_hash,
    )
    seal = seal_creator.to_seal()
    seal_dict = seal.model_dump()
    await db.ballot_seals.insert_one(seal_dict)

    # ── Update AGM status to completed ────────────────────────────────────────
    await db.agm.update_one(
        {"id": agm_id, "building_id": building_id},
        {
            "$set": {
                "status": "completed",
                "closed_at": get_current_utc_iso(),
                "closed_by": current_user["id"],
            }
        },
    )

    asyncio.create_task(create_audit_log(
        action="ballot_closed", resource_type="agm", resource_id=agm_id,
        user_id=current_user["id"], user_name=current_user.get("full_name", current_user["id"]),
        details={"total_entries": total_entries, "final_hash": final_hash,
                 "new_entries_written": len(new_entry_dicts)},
        building_id=building_id
    ))

    asyncio.create_task(
        log_activity(
            activity_type="vote",
            title=f"Ballot sealed for AGM {agm.get('title', agm_id)}",
            entity_id=agm_id,
            priority=1,
            metadata={
                "sealed_by": current_user["id"],
                "total_entries": total_entries,
                "final_hash": final_hash,
                "building_id": building_id,
            },
        )
    )

    return {
        "message": "Ballot sealed and AGM marked as completed.",
        "seal": seal_dict,
        "new_entries_written": len(new_entry_dicts),
    }


@router.get("/agm/{agm_id}/evidence-pack")
async def get_evidence_pack(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Return the full governance evidence pack for an AGM.

    Restricted to chairman, strata_manager, super_admin, ec_member.

    Returns a JSON document suitable for archival and audit:
      {
        agm:                dict,
        motions:            list[dict],
        weighted_results:   list[dict],
        ballot_seal:        dict | None,
        vote_count_by_motion: dict[motion_id -> int],
        proxy_summary:      dict,
        quorum_at_close:    dict | None,
        chain_integrity:    dict,
        generated_at:       str,
      }
    """
    _role = effective_role(current_user)
    if _role not in _EVIDENCE_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Evidence pack is restricted to EC members and above.",
        )

    agm = await _get_agm_or_404(agm_id, building_id)

    # Fetch motions, seal, and ballot entries concurrently.
    # Ballot entries are batched to avoid silent truncation for large AGMs.
    async def _get_all_ballot_entries() -> List[Dict[str, Any]]:
        """Generated function header.

        Function: _get_all_ballot_entries
        Path: backend/routers/voting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        entries: List[Dict[str, Any]] = []
        async for entry in db.ballot_entries.find(
                {"agm_id": agm_id, "building_id": building_id}, {"_id": 0}
        ).sort("created_at", 1):
            entries.append(entry)
        return entries

    motions_task = db.agm_motions.find(
        {"agm_id": agm_id, "building_id": building_id}, {"_id": 0}
    ).to_list(100)
    seal_task = db.ballot_seals.find_one(
        {"agm_id": agm_id, "building_id": building_id}, {"_id": 0}
    )

    motions, seal, entries = await asyncio.gather(
        motions_task, seal_task, _get_all_ballot_entries()
    )

    # Weighted results, proxy summary, and per-motion lot-counts — run concurrently.
    # vote_count_by_motion uses a single $group aggregation instead of N count_documents calls.
    async def _vote_counts() -> Dict[str, int]:
        """Generated function header.

        Function: _vote_counts
        Path: backend/routers/voting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pipeline = [
            {"$match": {"agm_id": agm_id, "building_id": building_id}},
            {"$group": {"_id": "$motion_id", "count": {"$sum": 1}}},
        ]
        rows = await db.agm_votes.aggregate(pipeline).to_list(200)
        return {r["_id"]: r["count"] for r in rows}

    weighted_results, proxy_summary, vote_count_by_motion, quorum_at_close = await asyncio.gather(
        _build_weighted_results(agm_id, building_id),
        get_proxy_summary(agm_id, building_id),
        _vote_counts(),
        _compute_quorum(agm_id, building_id),
    )

    # Chain integrity check
    chain_integrity = verify_chain(entries)

    # Strip MongoDB internal fields from AGM doc
    agm_clean = {k: v for k, v in agm.items() if k != "_id"}

    return {
        "agm": agm_clean,
        "motions": motions,
        "weighted_results": weighted_results,
        "ballot_seal": seal,
        "vote_count_by_motion": vote_count_by_motion,
        "proxy_summary": proxy_summary,
        "quorum_at_close": quorum_at_close,
        "chain_integrity": chain_integrity,
        "generated_at": get_current_utc_iso(),
    }
