"""
Portfolio Operations Router — S3

Endpoints:
  DELETE /portfolio/buildings/{building_id}                  — soft-archive building (data retained, super_admin only)
  POST   /portfolio/buildings/{building_id}/revive           — restore an archived building (super_admin only)
  GET    /portfolio/buildings/archived                       — list archived buildings (super_admin only)
  GET    /portfolio/users/search                             — search users for building assignment
  GET    /portfolio/buildings/{id}/onboarding                — get checklist status
  PATCH  /portfolio/buildings/{id}/onboarding/steps/{step}   — mark step complete
  POST   /portfolio/buildings/{id}/onboarding/validate       — run go-live checks
  POST   /portfolio/buildings/{id}/onboarding/complete       — finalise go-live
  GET    /portfolio/dashboard                                — summary across buildings
  GET    /portfolio/buildings                                — list buildings with health
  GET    /portfolio/arrears-summary                          — cross-building arrears
  GET    /portfolio/compliance-calendar                      — compliance items next 90 days
  GET    /portfolio/workload                                 — strata manager tasks + SLA
  GET    /portfolio/templates/notices                        — list notice templates
  POST   /portfolio/templates/notices                        — create/update template
  GET    /portfolio/organisations                            — list organisations
  GET    /portfolio/organisations/{org_id}/buildings         — list org buildings
  GET    /portfolio/onboarding/template                      — get onboarding template
  GET    /portfolio/notices/templates                        — list notice templates
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional

from database import db
from models.user import UserRole
from services.capability_registry import require_capability
from utils.auth import get_current_user, effective_role
from utils.helpers import create_audit_log

router = APIRouter(prefix="/portfolio", tags=["Portfolio Operations"])
logger = logging.getLogger(__name__)

_MANAGER_ROLES = {UserRole.STRATA_MANAGER, UserRole.SUPER_ADMIN}
_BROAD_MANAGER_ROLES = {
    UserRole.EC_MEMBER,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.SUPER_ADMIN,
}
_ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER}
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


_MANAGER_WITH_CHAIRMAN = {
    UserRole.EC_MEMBER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.SUPER_ADMIN,
}


def _require_manager(user: dict) -> None:
    """Allow EC members, chairman, strata_manager, and super_admin access to portfolio endpoints."""
    if effective_role(user) not in _MANAGER_WITH_CHAIRMAN:
        raise HTTPException(status_code=403, detail="Strata manager or super admin required")


def _require_admin(user: dict):
    """Generated function header.

    Function: _require_admin
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Portfolio management access required")


def _require_super_admin(user: dict) -> None:
    """Restrict to super_admin only — used for platform-wide aggregate endpoints
    that return unfiltered data across ALL buildings (no per-manager scoping)."""
    if effective_role(user) != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super admin access required")


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class StepCompleteRequest(BaseModel):
    notes: str = ""


class BuildingArchiveRequest(BaseModel):
    reason: Optional[str] = None  # Optional human-readable reason recorded in audit log


class NoticeTemplateRequest(BaseModel):
    name: str
    category: str
    content: str
    jurisdiction: Optional[str] = None


# ---------------------------------------------------------------------------
# Organisation endpoints
# ---------------------------------------------------------------------------

@router.get("/organisations")
async def list_organisations(current_user: dict = Depends(get_current_user)):
    """Generated function header.

    Function: list_organisations
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_admin(current_user)
    cursor = db._db.organisations.find({}, {"_id": 0})
    return await cursor.to_list(100)


@router.get("/organisations/{org_id}/buildings")
async def list_org_buildings(org_id: str, current_user: dict = Depends(get_current_user)):
    """Generated function header.

    Function: list_org_buildings
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_admin(current_user)
    cursor = db._db.organisation_buildings.find({"organisation_id": org_id}, {"_id": 0})
    return await cursor.to_list(200)


# ---------------------------------------------------------------------------
# Building setup (full creation) + user search
# ---------------------------------------------------------------------------

@router.get("/users/search")
async def search_users_for_building(
        role: Optional[str] = Query(None),
        query: Optional[str] = Query(None),
        current_user: dict = Depends(get_current_user),
):
    """Search existing users by role for building assignment (strata manager / EC)."""
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role not in {"super_admin", "strata_manager"}:
        raise HTTPException(status_code=403, detail="Admin access required")

    # F3: allowlist role values — never pass raw user input as a MongoDB field value
    _VALID_ROLES = {"super_admin", "strata_manager", "strata_admin", "ec_member", "owner", "tenant",
                    "guest", "admin_staff", "reception", "real_estate_agent", "service_provider"}
    filter_q: dict = {"is_active": True}
    if role:
        if role not in _VALID_ROLES:
            raise HTTPException(status_code=422, detail=f"Invalid role filter: {role}")
        filter_q["role"] = role
    if query:
        filter_q["$or"] = [
            {"full_name": {"$regex": re.escape(query), "$options": "i"}},
            {"email": {"$regex": re.escape(query), "$options": "i"}},
        ]

    # F4: exclude all credential + PII fields from the projection
    _USER_SAFE_PROJECTION = {
        "_id": 0, "password": 0, "password_hash": 0, "hashed_password": 0,
        "totp_secret": 0, "totp_secret_encrypted": 0, "totp_backup_codes_hashed": 0,
        "mail_password": 0, "temp_elevation": 0, "custom_permissions": 0,
        "last_login_ip": 0, "home_address": 0, "postal_address": 0,
        "phone_home": 0, "phone_mobile": 0, "phone_business": 0,
    }
    users = await db._db.users.find(filter_q, _USER_SAFE_PROJECTION).to_list(50)
    return users


@router.delete("/buildings/{building_id}", status_code=204)
async def archive_building(
        building_id: str,
        request: BuildingArchiveRequest = BuildingArchiveRequest(),
        current_user: dict = Depends(get_current_user),
):
    """
    Soft-archive a building. Data is retained for audit/compliance (7-year rule).
    Sets is_archived=True and is_active=False — the building disappears from all
    normal UI views but remains fully queryable by super admins.
    Super admin only.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required to archive a building")

    existing = await db._db.buildings.find_one(
        {"$or": [{"id": building_id}, {"building_id": building_id}]}
    )
    if not existing:
        raise HTTPException(status_code=404, detail=f"Building '{building_id}' not found")
    if existing.get("is_archived"):
        raise HTTPException(status_code=409, detail=f"Building '{building_id}' is already archived")

    building_name = existing.get("name", building_id)
    now = _now_iso()
    archived_by = current_user.get("id", "")

    archive_fields = {
        "is_archived": True,
        "is_active": False,
        "archived_at": now,
        "archived_by": archived_by,
        "archived_reason": request.reason or None,
    }

    # Soft-archive the building document
    await db._db.buildings.update_one(
        {"$or": [{"id": building_id}, {"building_id": building_id}]},
        {"$set": archive_fields},
    )

    # Soft-archive associated global collection records
    membership_archive = {"is_archived": True, "is_active": False, "archived_at": now}
    await db._db.memberships.update_many({"building_id": building_id}, {"$set": membership_archive})
    await db._db.building_invitations.update_many({"building_id": building_id},
                                                  {"$set": {"is_archived": True, "archived_at": now}})
    await db._db.building_onboarding_checklists.update_many({"building_id": building_id},
                                                            {"$set": {"is_archived": True, "archived_at": now}})

    # Soft-archive tenant-scoped records
    await db._db.ec_members.update_many({"building_id": building_id},
                                        {"$set": {"is_archived": True, "archived_at": now}})

    await create_audit_log(
        action="building_archived",
        resource_type="building",
        resource_id=building_id,
        user_id=archived_by,
        user_name=current_user.get("full_name", current_user.get("email", "")),
        details={"building_name": building_name, "reason": request.reason},
    )


@router.post("/buildings/{building_id}/revive", status_code=200)
async def revive_building(
        building_id: str,
        current_user: dict = Depends(get_current_user),
):
    """
    Revive an archived building — restores is_active=True and clears is_archived.
    Also restores associated membership records.
    Super admin only.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required to revive a building")

    existing = await db._db.buildings.find_one(
        {"$or": [{"id": building_id}, {"building_id": building_id}]}
    )
    if not existing:
        raise HTTPException(status_code=404, detail=f"Building '{building_id}' not found")
    if not existing.get("is_archived"):
        raise HTTPException(status_code=409, detail=f"Building '{building_id}' is not archived")

    building_name = existing.get("name", building_id)
    now = _now_iso()

    revive_fields = {
        "is_archived": False,
        "is_active": True,
        "archived_at": None,
        "archived_by": None,
        "archived_reason": None,
        "revived_at": now,
        "revived_by": current_user.get("id", ""),
    }

    await db._db.buildings.update_one(
        {"$or": [{"id": building_id}, {"building_id": building_id}]},
        {"$set": revive_fields},
    )

    # Restore memberships and EC members
    membership_revive = {"is_archived": False, "is_active": True, "archived_at": None}
    await db._db.memberships.update_many({"building_id": building_id, "is_archived": True}, {"$set": membership_revive})
    await db._db.building_invitations.update_many({"building_id": building_id, "is_archived": True},
                                                  {"$set": {"is_archived": False, "archived_at": None}})
    await db._db.ec_members.update_many({"building_id": building_id, "is_archived": True},
                                        {"$set": {"is_archived": False, "archived_at": None}})

    await create_audit_log(
        action="building_revived",
        resource_type="building",
        resource_id=building_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", current_user.get("email", "")),
        details={"building_name": building_name},
    )

    existing.pop("_id", None)
    existing.update(revive_fields)
    return existing


@router.get("/buildings/archived", status_code=200)
async def list_archived_buildings(
        current_user: dict = Depends(get_current_user),
):
    """Return all archived buildings with archive metadata. Super admin only."""
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required to view archived buildings")

    buildings = await db._db.buildings.find(
        {"is_archived": True},
        {"_id": 0},
    ).sort("archived_at", -1).to_list(500)

    return {"buildings": buildings, "total": len(buildings)}


# ---------------------------------------------------------------------------
# Onboarding endpoints (S3 style)
# ---------------------------------------------------------------------------

@router.get("/buildings/{building_id}/onboarding")
async def get_onboarding_status(
        building_id: str,
        current_user: dict = Depends(
            require_capability(
                "building.onboarding.view",
                scope_params={"building_id": "building_id"},
            )
        ),
):
    """Go-live onboarding checklist and progress for one building.

    Authorisation (BOLA / OWASP API1:2023): ``building_id`` is caller-supplied, so
    the capability is scoped to the building NAMED IN THE PATH rather than to the
    caller's session building. These four routes previously ran a role-only
    ``_require_manager()`` and then queried
    ``db._db.building_onboarding_checklists`` — which bypasses
    ``TenantScopedDatabase`` — with that unverified id, so any ec_member,
    strata_admin or strata_manager of ANY building could read and mutate another
    building's go-live checklist.
    """

    checklist = await db._db.building_onboarding_checklists.find_one(
        {"building_id": building_id}
    )
    if not checklist:
        raise HTTPException(status_code=404, detail="Onboarding checklist not found")

    checklist.pop("_id", None)
    total = len(checklist.get("steps", []))
    done = sum(1 for s in checklist.get("steps", []) if s.get("completed"))
    checklist["progress"] = {"total": total, "completed": done, "percent": round(done / total * 100) if total else 0}
    return checklist


@router.patch("/buildings/{building_id}/onboarding/steps/{step_id}")
async def complete_onboarding_step(
        building_id: str,
        step_id: str,
        request: StepCompleteRequest,
        current_user: dict = Depends(
            require_capability(
                "building.onboarding.manage",
                scope_params={"building_id": "building_id"},
            )
        ),
):
    """Mark one go-live onboarding step complete.

    Authorisation (BOLA / OWASP API1:2023): ``building_id`` is caller-supplied, so
    the capability is scoped to the building NAMED IN THE PATH rather than to the
    caller's session building. These four routes previously ran a role-only
    ``_require_manager()`` and then queried
    ``db._db.building_onboarding_checklists`` — which bypasses
    ``TenantScopedDatabase`` — with that unverified id, so any ec_member,
    strata_admin or strata_manager of ANY building could read and mutate another
    building's go-live checklist.
    """

    checklist = await db._db.building_onboarding_checklists.find_one(
        {"building_id": building_id}
    )
    if not checklist:
        raise HTTPException(status_code=404, detail="Onboarding checklist not found")

    steps = checklist.get("steps", [])
    step = next((s for s in steps if s["id"] == step_id), None)
    if not step:
        raise HTTPException(status_code=404, detail=f"Step '{step_id}' not found")

    now = _now_iso()
    step["completed"] = True
    step["completed_at"] = now
    step["completed_by"] = current_user.get("id")
    step["notes"] = request.notes

    await db._db.building_onboarding_checklists.update_one(
        {"building_id": building_id},
        {"$set": {"steps": steps, "updated_at": now}},
    )

    return {
        "step_id": step_id,
        "completed": True,
        "completed_at": now,
        "completed_by": current_user.get("id"),
    }


@router.post("/buildings/{building_id}/onboarding/validate")
async def validate_go_live(
        building_id: str,
        current_user: dict = Depends(
            require_capability(
                "building.onboarding.view",
                scope_params={"building_id": "building_id"},
            )
        ),
):
    """Run the go-live readiness checks for one building.

    Authorisation (BOLA / OWASP API1:2023): ``building_id`` is caller-supplied, so
    the capability is scoped to the building NAMED IN THE PATH rather than to the
    caller's session building. These four routes previously ran a role-only
    ``_require_manager()`` and then queried
    ``db._db.building_onboarding_checklists`` — which bypasses
    ``TenantScopedDatabase`` — with that unverified id, so any ec_member,
    strata_admin or strata_manager of ANY building could read and mutate another
    building's go-live checklist.

    Every check is filtered on the path ``building_id`` explicitly. Checks 1-3 used
    to rely on ``TenantScopedDatabase``'s automatic injection, which scopes to the
    caller's SESSION building — so a super admin validating building B was shown
    building A's EC members, units and folders — and check 4 counted active users
    across the whole platform, so it passed for every building unconditionally.
    """
    building_filter = {"building_id": building_id, "is_test_data": {"$ne": True}}
    checks = []

    # Check 1: EC members assigned
    ec_count = await db._db.ec_members.count_documents(building_filter)
    checks.append({
        "check": "ec_members_assigned",
        "passed": ec_count > 0,
        "detail": f"{ec_count} EC member(s) found",
    })

    # Check 2: Units with UOE
    units_with_uoe = await db._db.units.count_documents(
        {**building_filter, "entitlement": {"$gt": 0}}
    )
    total_units = await db._db.units.count_documents(building_filter)
    checks.append({
        "check": "units_have_uoe",
        "passed": total_units > 0 and units_with_uoe == total_units,
        "detail": f"{units_with_uoe}/{total_units} units have UOE",
    })

    # Check 3: Document folders exist
    folder_count = await db._db.document_folders.count_documents({"building_id": building_id})
    checks.append({
        "check": "document_folders_exist",
        "passed": folder_count >= 4,
        "detail": f"{folder_count} document folder(s) found",
    })

    # Check 4: At least one active user OF THIS BUILDING. `users` is a global
    # collection, so membership is what ties a user to a building.
    member_ids = await db._db.memberships.distinct(
        "user_id", {"building_id": building_id, "is_active": True}
    )
    active_users = await db._db.users.count_documents(
        {"id": {"$in": member_ids}, "is_active": True, "is_test_data": {"$ne": True}}
    ) if member_ids else 0
    checks.append({
        "check": "active_users_exist",
        "passed": active_users > 0,
        "detail": f"{active_users} active user(s) found",
    })

    # Check 5: Required checklist steps complete
    checklist = await db._db.building_onboarding_checklists.find_one(
        {"building_id": building_id}
    )
    required_steps = []
    if checklist:
        required_steps = [s for s in checklist.get("steps", []) if s.get("required")]
        incomplete_required = [s for s in required_steps if not s.get("completed")]
    else:
        incomplete_required = ["checklist_missing"]

    checks.append({
        "check": "required_steps_complete",
        "passed": len(incomplete_required) == 0,
        "detail": f"{len(required_steps) - len(incomplete_required)}/{len(required_steps)} required steps done",
    })

    all_passed = all(c["passed"] for c in checks)
    return {"checks": checks, "all_passed": all_passed, "ready_for_go_live": all_passed}


@router.post("/buildings/{building_id}/onboarding/complete")
async def complete_onboarding(
        building_id: str,
        current_user: dict = Depends(
            require_capability(
                "building.onboarding.manage",
                scope_params={"building_id": "building_id"},
            )
        ),
):
    """Mark a building's go-live onboarding complete once every check passes.

    Authorisation (BOLA / OWASP API1:2023): ``building_id`` is caller-supplied, so
    the capability is scoped to the building NAMED IN THE PATH rather than to the
    caller's session building. These four routes previously ran a role-only
    ``_require_manager()`` and then queried
    ``db._db.building_onboarding_checklists`` — which bypasses
    ``TenantScopedDatabase`` — with that unverified id, so any ec_member,
    strata_admin or strata_manager of ANY building could read and mutate another
    building's go-live checklist.
    """

    validation = await validate_go_live(building_id, current_user=current_user)
    if not validation["all_passed"]:
        raise HTTPException(status_code=400, detail="Go-live validation failed. Resolve all checks first.")

    now = _now_iso()
    await db._db.building_onboarding_checklists.update_one(
        {"building_id": building_id},
        {"$set": {"status": "complete", "completed_at": now}},
    )
    await create_audit_log(
        action="onboarding_completed",
        resource_type="building_onboarding",
        resource_id=building_id,
        user_id=current_user.get("id", ""),
        user_name=current_user.get("full_name", current_user.get("email", "")),
        details={"completed_at": now},
    )
    return {"building_id": building_id, "status": "complete", "completed_at": now}


# Legacy endpoints for backwards compatibility
@router.get("/onboarding/template")
async def get_onboarding_template(current_user: dict = Depends(get_current_user)):
    """Generated function header.

    Function: get_onboarding_template
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_admin(current_user)
    template_path = _DATA_DIR / "onboarding_template.json"
    if not template_path.exists():
        return {"steps": []}
    with open(template_path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Portfolio views
# ---------------------------------------------------------------------------

async def _get_portfolio_ledger_arrears(building_ids: list) -> dict:
    """Ledger-derived arrears per building, for each building's own latest levy year.

    GAP-FIN-014: cross-building dashboards must source dollar arrears figures from
    unit_levy_ledger (the accounting ledger), never from building_summaries (Strata
    Web portal cross-check data) or levy_payments (the receipts/detail layer, not
    the ledger). Uses raw db._db access, bypassing TenantScopedDatabase's
    single-building auto-injection, matching this file's existing cross-building
    query pattern (e.g. db._db.buildings.find(...) above).

    Returns {building_id: {total_outstanding, total_levied, units_in_arrears,
    arrears_rate}}. Buildings with no unit_levy_ledger rows are omitted —
    callers must default missing entries.
    """
    if not building_ids:
        return {}

    # TenantScopedDatabase.aggregate is async in this codebase. Await the
    # cursor before calling Motor's async to_list(); chaining would call
    # to_list() on the coroutine and bypass the mocked/tested contract.
    year_cursor = await db._db.unit_levy_ledger.aggregate([
        {"$match": {"building_id": {"$in": building_ids}, "is_test_data": {"$ne": True}}},
        {"$group": {"_id": "$building_id", "max_year": {"$max": "$year"}}},
    ])
    year_rows = await year_cursor.to_list(length=200)
    if not year_rows:
        return {}

    # Only the latest year per building — earlier years' net_balance is already
    # carried forward into that year's opening balance, so summing across all
    # years would double-count arrears.
    or_clauses = [{"building_id": r["_id"], "year": r["max_year"]} for r in year_rows]
    arrears_cursor = await db._db.unit_levy_ledger.aggregate([
        {"$match": {"$or": or_clauses, "is_test_data": {"$ne": True}}},
        {"$group": {
            "_id": "$building_id",
            # total_outstanding sums every positive net_balance (matches finance.py's
            # /finance/* KPI contract convention, e.g. get_ledger_quality's own
            # total_outstanding aggregation) — every cent of arrears counts toward the
            # dollar total. units_in_arrears uses the same >0.01 threshold finance.py
            # already uses for "units in arrears" counts, so fractional-cent rounding
            # noise doesn't inflate the unit count. These are deliberately different
            # thresholds for different metrics, not an inconsistency.
            "total_outstanding": {"$sum": {"$cond": [{"$gt": ["$net_balance", 0]}, "$net_balance", 0]}},
            "total_levied": {"$sum": "$total_levied"},
            "units_in_arrears": {"$sum": {"$cond": [{"$gt": ["$net_balance", 0.01]}, 1, 0]}},
        }},
    ])
    arrears_rows = await arrears_cursor.to_list(length=200)

    result = {}
    for r in arrears_rows:
        total_levied = round(float(r.get("total_levied", 0) or 0), 2)
        total_outstanding = round(float(r.get("total_outstanding", 0) or 0), 2)
        result[r["_id"]] = {
            "total_outstanding": total_outstanding,
            "total_levied": total_levied,
            "units_in_arrears": r.get("units_in_arrears", 0),
            "arrears_rate": round(total_outstanding / total_levied * 100, 1) if total_levied else 0.0,
        }
    return result


@router.get("/summary")
async def get_portfolio_summary(current_user: dict = Depends(get_current_user)):
    """Cross-building aggregate metrics — returns unfiltered data across ALL buildings."""
    _require_super_admin(current_user)

    buildings = await db._db.buildings.find({"is_active": True}).to_list(100)
    active_buildings = len(buildings)

    # lot_count field name inconsistency: seed stores "lots", newer code uses "lot_count"
    total_lots = sum(b.get("lot_count", b.get("lots", 0)) for b in buildings)

    building_ids = [b.get("id", str(b.get("_id", ""))) for b in buildings]
    # GAP-FIN-014: dollar arrears sourced from the ledger, not the portal snapshot.
    ledger_arrears = await _get_portfolio_ledger_arrears(building_ids)
    summaries = await db._db.building_summaries.find(
        {"building_id": {"$in": building_ids}}
    ).to_list(200)

    total_arrears_cents = round(sum(v["total_outstanding"] for v in ledger_arrears.values()) * 100)
    open_wos = sum(s.get("open_maintenance_requests", s.get("open_work_orders", 0)) for s in summaries)
    health_scores = [s["health_score"] for s in summaries if s.get("health_score") is not None]
    avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else 0

    return {
        "active_buildings": active_buildings,
        "total_lots": total_lots,
        "total_arrears_cents": total_arrears_cents,
        "avg_building_health": avg_health,
        "open_work_orders": open_wos,
        # GAP-FIN-014: avg_building_health is a composite operational score
        # (sinking fund %, maintenance, compliance) from the portal scraper
        # snapshot, not the accounting ledger. The arrears figure above is
        # ledger-derived; this score is not a financial ledger figure.
        "is_health_score_authoritative_finance_metric": False,
    }


@router.get("/dashboard")
async def get_portfolio_dashboard(
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: get_portfolio_dashboard
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)

    org_buildings = await db._db.organisation_buildings.find(
        {"is_active": {"$ne": False}}
    ).to_list(length=200)
    building_ids = [b["building_id"] for b in org_buildings]

    buildings_raw = await db._db.buildings.find(
        {"is_active": {"$ne": False}}
    ).to_list(length=200)
    buildings_map = {b.get("id", str(b.get("_id", ""))): b for b in buildings_raw}

    all_building_ids = [bld.get("id", str(bld.get("_id", ""))) for bld in buildings_raw]
    # GAP-FIN-014: dollar arrears sourced from the ledger, not the portal snapshot.
    ledger_arrears = await _get_portfolio_ledger_arrears(all_building_ids)
    summaries_raw = await db._db.building_summaries.find({}).to_list(length=200)
    summaries_map = {s["building_id"]: s for s in summaries_raw if "building_id" in s}

    buildings = []
    alerts = []

    for bld in buildings_raw:
        bid = bld.get("id", str(bld.get("_id", "")))
        summary = summaries_map.get(bid, {})
        health = summary.get("health_score", 100)  # default 100 = healthy when no data yet
        ledger = ledger_arrears.get(bid, {})
        arrears_rate = ledger.get("arrears_rate", 0.0)
        open_wos = summary.get("open_maintenance_requests", summary.get("open_work_orders", 0))
        next_compliance = summary.get("next_compliance_item")
        last_computed = summary.get("computed_at") or summary.get("last_computed_at")

        entry = {
            "building_id": bid,
            "name": bld.get("name", "Unknown"),
            # lot_count field renamed: seed uses "lots", newer code uses "lot_count"
            "lot_count": bld.get("lot_count", bld.get("lots", 0)),
            "health_score": health,
            "arrears_rate": arrears_rate,
            "total_outstanding": ledger.get("total_outstanding", 0.0),
            "open_work_orders": open_wos,
            "next_compliance_item": next_compliance,
            "last_computed_at": last_computed,
            "has_data": summary != {},
        }
        buildings.append(entry)

        if health is not None and health < 50:
            alerts.append({"type": "low_health", "building_id": bid, "value": health})
        if arrears_rate > 10:
            alerts.append({"type": "high_arrears", "building_id": bid, "value": arrears_rate})

    summary_totals = {
        "total_buildings": len(buildings),
        "total_lots": sum(b["lot_count"] for b in buildings),
        "avg_health_score": round(sum(b["health_score"] for b in buildings if b["health_score"] is not None) / len(
            [b for b in buildings if b["health_score"] is not None]), 1) if any(
            b["health_score"] is not None for b in buildings) else 0,
        "alert_count": len(alerts),
    }

    return {"buildings": buildings, "summary": summary_totals, "alerts": alerts}


@router.get("/buildings")
async def list_portfolio_buildings(
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_portfolio_buildings
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_super_admin(current_user)

    buildings_raw = await db._db.buildings.find(
        {"is_active": {"$ne": False}}
    ).to_list(length=200)
    building_ids = [bld.get("id", str(bld.get("_id", ""))) for bld in buildings_raw]
    # GAP-FIN-014: dollar arrears sourced from the ledger, not the portal snapshot.
    ledger_arrears = await _get_portfolio_ledger_arrears(building_ids)
    summaries_raw = await db._db.building_summaries.find({}).to_list(length=200)
    summaries_map = {s["building_id"]: s for s in summaries_raw if "building_id" in s}

    result = []
    for bld in buildings_raw:
        bid = bld.get("id", str(bld.get("_id", "")))
        summary = summaries_map.get(bid, {})
        ledger = ledger_arrears.get(bid, {})
        bld.pop("_id", None)
        # lot_count field inconsistency: seed stores "lots", newer code uses "lot_count"
        lot_count = bld.get("lot_count", bld.get("lots", 0))
        result.append({
            **{k: v for k, v in bld.items()},
            "lot_count": lot_count,
            "health_score": summary.get("health_score", 100),  # default 100 when no data
            "arrears_rate": ledger.get("arrears_rate", 0.0),
            "total_outstanding": ledger.get("total_outstanding", 0.0),
            "open_work_orders": summary.get("open_maintenance_requests", summary.get("open_work_orders", 0)),
            "last_computed_at": summary.get("computed_at") or summary.get("last_computed_at"),
        })

    return {"buildings": result, "total": len(result)}


@router.get("/arrears-summary")
async def get_arrears_summary(
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: get_arrears_summary
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)

    # GAP-FIN-014: arrears must come from unit_levy_ledger (the accounting
    # ledger), never from levy_payments (the receipts/detail layer — a
    # "balance" field there is per-payment-record state, not the accounting
    # position for the unit).
    buildings_raw = await db._db.buildings.find(
        {"is_active": {"$ne": False}}
    ).to_list(length=200)
    building_ids = [bld.get("id", str(bld.get("_id", ""))) for bld in buildings_raw]
    ledger_arrears = await _get_portfolio_ledger_arrears(building_ids)

    buildings = [
        {
            "building_id": bid,
            "units_in_arrears": v["units_in_arrears"],
            "total_arrears": v["total_outstanding"],
        }
        for bid, v in ledger_arrears.items()
        if v["units_in_arrears"] > 0
    ]
    return {"buildings": buildings, "total": len(buildings)}


@router.get("/compliance-calendar")
async def get_compliance_calendar(
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: get_compliance_calendar
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    now_iso = _now_iso()

    items = await db.ppm_items.find(
        {
            "next_due_date": {"$lte": cutoff, "$gte": now_iso},
            "is_test_data": {"$ne": True},
        }
    ).to_list(length=500)

    for item in items:
        item.pop("_id", None)

    return {"items": items, "total": len(items), "window_days": 90}


@router.get("/workload")
async def get_workload(
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: get_workload
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)

    buildings_raw = await db._db.buildings.find(
        {"is_active": {"$ne": False}}
    ).to_list(length=200)

    result = []
    for bld in buildings_raw:
        bid = bld.get("id", str(bld.get("_id", "")))
        open_wos = await db.work_orders.count_documents(
            {"status": {"$in": ["open", "pending", "in_progress"]}, "is_test_data": {"$ne": True}}
        )
        pending_requests = await db.workflow_requests.count_documents(
            {"status": "pending", "is_test_data": {"$ne": True}}
        )
        overdue_requests = await db.workflow_requests.count_documents(
            {"status": "overdue", "is_test_data": {"$ne": True}}
        )
        result.append({
            "building_id": bid,
            "name": bld.get("name", "Unknown"),
            "lot_count": bld.get("lot_count", 0),
            "open_work_orders": open_wos,
            "pending_requests": pending_requests,
            "sla_breaches": overdue_requests,
        })

    return {"buildings": result, "total": len(result)}


# ---------------------------------------------------------------------------
# Notice templates
# ---------------------------------------------------------------------------

@router.get("/notices/templates")
async def list_notice_templates_legacy(current_user: dict = Depends(get_current_user)):
    """Generated function header.

    Function: list_notice_templates_legacy
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_admin(current_user)
    cursor = db._db.notice_templates.find({}, {"_id": 0})
    return await cursor.to_list(100)


@router.get("/templates/notices")
async def list_notice_templates(
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_notice_templates
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in _BROAD_MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Access denied")

    templates = await db._db.notice_templates.find(
        {"is_test_data": {"$ne": True}}
    ).to_list(length=200)
    for t in templates:
        t.pop("_id", None)
    return {"templates": templates, "total": len(templates)}


@router.post("/templates/notices")
async def upsert_notice_template(
        request: NoticeTemplateRequest,
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: upsert_notice_template
    Path: backend/routers/portfolio.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_manager(current_user)

    now = _now_iso()
    doc = {
        "name": request.name,
        "category": request.category,
        "content": request.content,
        "jurisdiction": request.jurisdiction,
        "updated_at": now,
        "updated_by": current_user.get("id"),
    }
    result = await db._db.notice_templates.update_one(
        {"name": request.name},
        {"$set": doc, "$setOnInsert": {"created_at": now, "id": f"tpl-{uuid.uuid4().hex[:10]}"}},
        upsert=True,
    )
    return {"upserted": result.upserted_id is not None, "name": request.name}
