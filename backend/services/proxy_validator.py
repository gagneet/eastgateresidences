# @featuretrace:evoting — Proxy eligibility validation: role prohibitions + NSW numerical cap.
# Layer: service
# Data flow: routers/voting.py submit_proxy → validate_proxy_eligibility()
#             → db.users, db.agm, db.agm_attendance, db.units, db.user_units (building-scoped)
#             Also called from server.py cast_vote (belt-and-suspenders cap check).
# Related: backend/routers/voting.py
#           backend/domain/jurisdictional_rules.py
#           backend/domain/rules/nsw.json (proxy_cap_scheme_pct, proxy_cap_scheme_size_threshold)
# Toggle: evoting
# Collection: agm_attendance, agm, users, units, user_units
"""
Proxy Validator Service — Multi-jurisdiction proxy eligibility validation.

Covers ACT (Unit Titles (Management) Act 2011) and NSW (Strata Schemes Management
Act 2015) proxy rules.  Additional jurisdictions are read from the rule engine.

ACT key sections:
  - s.76(2): A strata manager or building manager CANNOT hold a proxy at any vote.
  - s.77: Proxy form must be in writing for a specific named meeting.
  - No numerical cap on proxies per holder (unlike NSW's 5% rule).
  - A grantor may revoke a proxy at any time before the vote is cast.

NSW rules (GAP-GOV-001) are read from the jurisdictional rule engine, not hardcoded:
  - SSMA 2015 s.60: schemes of 20+ lots → max 5% of lots per holder.
  - SSMA 2015 s.60: schemes of < 20 lots → max 1 proxy per holder (small-scheme rule).
  Cap thresholds and percentages live in backend/domain/rules/nsw.json; change them there.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database import db
from domain.jurisdictional_rules import rule_engine
from models.user import UserRole

logger = logging.getLogger(__name__)

# Roles PROHIBITED from holding a proxy under ACT s.76(2).
# Note: "building_manager" has no UserRole constant — kept as raw string for DB compat.
_ACT_PROHIBITED_PROXY_ROLES: frozenset[str] = frozenset({
    UserRole.STRATA_MANAGER,
    "building_manager",
})


async def validate_proxy_eligibility(
        proxy_holder_id: str,
        agm_id: str,
        grantor_user_id: str,
        building_id: str,
        jurisdiction: str = "ACT",
) -> Dict[str, Any]:
    """Validate proxy holder eligibility for an AGM vote.

    Returns a dict:
      {
        "valid": bool,
        "reason": str,                   # human-readable explanation
        "legislation": str,              # statute reference (informational)
        "jurisdiction": str,
        "proxy_holder_id": str,
        "grantor_user_id": str,
      }
    """
    jurisdiction = (jurisdiction or "ACT").upper()

    # ── 1. Resolve the proxy holder's user record ──────────────────────────────
    holder = await db.users.find_one({"id": proxy_holder_id})
    if not holder:
        return _result(
            False,
            f"Proxy holder user '{proxy_holder_id}' not found.",
            legislation="",
            jurisdiction=jurisdiction,
            proxy_holder_id=proxy_holder_id,
            grantor_user_id=grantor_user_id,
        )

    holder_role: str = (holder.get("effective_role") or holder.get("role") or UserRole.GUEST).lower()
    holder_name: str = holder.get("full_name", proxy_holder_id)

    # ── 2. Check the AGM exists and is not already completed ──────────────────
    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id})
    if not agm:
        return _result(
            False,
            f"AGM '{agm_id}' not found for this building.",
            legislation="",
            jurisdiction=jurisdiction,
            proxy_holder_id=proxy_holder_id,
            grantor_user_id=grantor_user_id,
        )

    agm_status = agm.get("status", "upcoming")
    if agm_status == "completed":
        return _result(
            False,
            "The AGM has already been completed. No proxy can be registered.",
            legislation="ACT Unit Titles (Management) Act 2011 s.77",
            jurisdiction=jurisdiction,
            proxy_holder_id=proxy_holder_id,
            grantor_user_id=grantor_user_id,
        )

    # ── 3. Jurisdiction-specific rule: ACT s.76(2) — prohibited roles ─────────
    if jurisdiction == "ACT":
        if holder_role in _ACT_PROHIBITED_PROXY_ROLES:
            return _result(
                False,
                (
                    f"'{holder_name}' holds the role '{holder_role}' and is prohibited "
                    "from acting as a proxy holder under ACT s.76(2) of the Unit Titles "
                    "(Management) Act 2011. Strata managers and building managers cannot "
                    "hold proxies at any meeting."
                ),
                legislation="ACT Unit Titles (Management) Act 2011 s.76(2)",
                jurisdiction=jurisdiction,
                proxy_holder_id=proxy_holder_id,
                grantor_user_id=grantor_user_id,
            )

    # ── 4. NSW numerical cap check (GAP-GOV-001) — SSMA 2015 s.60 ────────────
    # Cap thresholds come from the rule engine (backend/domain/rules/nsw.json),
    # never from hardcoded constants.
    #   Schemes of 20+ lots: cap = max(1, floor(total_lots × 5%))
    #   Schemes of  < 20 lots (small-scheme rule): cap = 1 proxy per holder
    if jurisdiction == "NSW":
        total_lots = await db.units.count_documents({"building_id": building_id})
        if total_lots > 0:
            size_threshold = rule_engine.proxy_cap_scheme_size_threshold("NSW")  # 20
            if total_lots < size_threshold:
                # Small-scheme rule: maximum 1 proxy per holder regardless of scheme size
                cap = rule_engine.proxy_cap_small_scheme_lots("NSW")  # 1
                cap_desc = f"small-scheme rule (< {size_threshold} lots): max {cap} proxy"
            else:
                cap_pct = rule_engine.proxy_cap_scheme_pct("NSW")  # Decimal("5")
                cap = max(1, int(total_lots * float(cap_pct) / 100))
                cap_desc = f"{float(cap_pct):.0f}% of {total_lots} lots = {cap} proxy slot(s)"

            # Exclude the grantor's own record: if they already proxy to this holder
            # the router will upsert (no net increase), so their slot must not count
            # against the cap — otherwise a re-submission of the same proxy is rejected.
            existing_count = await db.agm_attendance.count_documents({
                "building_id": building_id,
                "agm_id": agm_id,
                "proxy_id": proxy_holder_id,
                "user_id": {"$ne": grantor_user_id},
            })
            if existing_count >= cap:
                return _result(
                    False,
                    (
                        f"'{holder_name}' already holds proxies for {existing_count} lot(s). "
                        f"NSW proxy cap ({cap_desc}). "
                        "Strata Schemes Management Act 2015 s.60."
                    ),
                    legislation="NSW Strata Schemes Management Act 2015 s.60",
                    jurisdiction=jurisdiction,
                    proxy_holder_id=proxy_holder_id,
                    grantor_user_id=grantor_user_id,
                )

    # ── 5. Proxy holder must be a member of THIS building's scheme ───────────
    # Sentinel 🛡️: Check ALL roles (not just GUEST) — a user who owns units in
    # another building carries a non-guest role but has no standing in this one.
    # ACT UTMA 2011 s.77: proxy holder must be a lot owner or occupier of THIS scheme,
    # or an authorised attorney under a formal power of attorney.
    is_member = await db.user_units.find_one({
        "building_id": building_id,
        "user_id": proxy_holder_id,
        "is_active": True,
    })
    if not is_member:
        return _result(
            False,
            (
                f"'{holder_name}' is not an owner or occupier of any lot in this building "
                "and cannot hold a proxy. A proxy holder must have an active lot association "
                "in this scheme (ACT UTMA 2011 s.77)."
            ),
            legislation="ACT Unit Titles (Management) Act 2011 s.77",
            jurisdiction=jurisdiction,
            proxy_holder_id=proxy_holder_id,
            grantor_user_id=grantor_user_id,
        )

    # ── 6. Grantor must actually own a lot in this building ───────────────────
    grantor = await db.users.find_one({"id": grantor_user_id})
    if not grantor:
        return _result(
            False,
            f"Grantor user '{grantor_user_id}' not found.",
            legislation="",
            jurisdiction=jurisdiction,
            proxy_holder_id=proxy_holder_id,
            grantor_user_id=grantor_user_id,
        )

    # ── 7. Proxy holder must not be the grantor themselves ─────────────────────
    if proxy_holder_id == grantor_user_id:
        return _result(
            False,
            "A lot owner cannot appoint themselves as their own proxy.",
            legislation="ACT Unit Titles (Management) Act 2011 s.77",
            jurisdiction=jurisdiction,
            proxy_holder_id=proxy_holder_id,
            grantor_user_id=grantor_user_id,
        )

    # ── All checks passed ─────────────────────────────────────────────────────
    return _result(
        True,
        (
            f"'{holder_name}' is eligible to hold this proxy. "
            "Proxy recorded in accordance with ACT Unit Titles (Management) Act 2011 s.77."
        ),
        legislation="ACT Unit Titles (Management) Act 2011 s.77",
        jurisdiction=jurisdiction,
        proxy_holder_id=proxy_holder_id,
        grantor_user_id=grantor_user_id,
    )


async def get_proxy_summary(agm_id: str, building_id: str) -> Dict[str, Any]:
    """Return a summary of all proxies registered for an AGM.

    Returns:
      {
        "agm_id": str,
        "total_proxies": int,
        "holders": [
          {
            "holder_user_id": str,
            "holder_name": str,
            "granted_by": [
              {"grantor_user_id": str, "grantor_name": str, "unit_number": str, "submitted_at": str}
            ],
            "lot_count": int,
          }
        ],
        "generated_at": str (ISO-8601),
      }
    """
    pipeline = [
        {
            "$match": {
                "agm_id": agm_id,
                "building_id": building_id,
                "proxy_id": {"$ne": None, "$exists": True},
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "id",
                "as": "_grantor",
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "proxy_id",
                "foreignField": "id",
                "as": "_holder",
            }
        },
        {
            "$addFields": {
                "_grantor_doc": {"$arrayElemAt": ["$_grantor", 0]},
                "_holder_doc": {"$arrayElemAt": ["$_holder", 0]},
            }
        },
        {
            "$project": {
                "_id": 0,
                "proxy_id": 1,
                "user_id": 1,
                "proxy_submitted_at": 1,
                "grantor_name": "$_grantor_doc.full_name",
                "grantor_unit": "$_grantor_doc.unit_number",
                "holder_name": "$_holder_doc.full_name",
            }
        },
    ]

    rows = await db.agm_attendance.aggregate(pipeline).to_list(None)

    # Group by holder
    holders: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        holder_id = row["proxy_id"]
        if holder_id not in holders:
            holders[holder_id] = {
                "holder_user_id": holder_id,
                "holder_name": row.get("holder_name") or holder_id,
                "granted_by": [],
                "lot_count": 0,
            }
        holders[holder_id]["granted_by"].append({
            "grantor_user_id": row["user_id"],
            "grantor_name": row.get("grantor_name", ""),
            "unit_number": row.get("grantor_unit", ""),
            "submitted_at": row.get("proxy_submitted_at", ""),
        })
        holders[holder_id]["lot_count"] += 1

    return {
        "agm_id": agm_id,
        "total_proxies": len(rows),
        "holders": list(holders.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _result(
        valid: bool,
        reason: str,
        *,
        legislation: str,
        jurisdiction: str,
        proxy_holder_id: str,
        grantor_user_id: str,
) -> Dict[str, Any]:
    """Generated function header.

    Function: _result
    Path: backend/services/proxy_validator.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "valid": valid,
        "reason": reason,
        "legislation": legislation,
        "jurisdiction": jurisdiction,
        "proxy_holder_id": proxy_holder_id,
        "grantor_user_id": grantor_user_id,
    }
