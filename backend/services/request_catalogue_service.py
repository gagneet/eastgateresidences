# @featuretrace:request-catalogue — Authoritative catalogue metadata and eligibility.
# Layer: service
# Data flow: GET /request-catalogue -> role + feature resolution -> eligible definitions.
# Related: backend/routers/request_catalogue.py
#          frontend/src/config/requestCatalogue.js

"""Server-owned request catalogue definitions and eligibility resolution.

Definitions remain code-versioned in Phase 1. Existing PostgreSQL feature toggles
provide building and user availability; no new table or migration is required.
Domain request endpoints continue to own request validation and persistence.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import HTTPException
from models.user import UserRole
from utils.auth import effective_role
from utils.permissions import get_effective_feature_access, get_user_permissions


_RESIDENT_AND_ADMIN_ROLES = frozenset({
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
    UserRole.ADMIN_STAFF,
    UserRole.OWNER,
    UserRole.TENANT,
    UserRole.REAL_ESTATE_AGENT,
})

_ADMIN_OWNER_AGENT_ROLES = frozenset({
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
    UserRole.ADMIN_STAFF,
    UserRole.OWNER,
    UserRole.REAL_ESTATE_AGENT,
})

_ADMIN_OWNER_ROLES = frozenset({
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
    UserRole.ADMIN_STAFF,
    UserRole.OWNER,
})


class RequestPolicyStage(str, Enum):
    DISCOVERY = "discovery"
    FORM_ACCESS = "form_access"
    SUBMISSION = "submission"
    REVIEW = "review"
    FULFILMENT = "fulfilment"


@dataclass(frozen=True)
class RequestEligibilityResult:
    allowed: bool
    reason: str = ""


REQUEST_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "maintenance",
        "version": 1,
        "title": "Maintenance request",
        "description": "Report a repair, defect, hazard or common-property maintenance issue.",
        "category": "Repairs & Maintenance",
        "search_terms": ["repair", "broken", "leak", "damage", "hazard", "maintenance"],
        "expected_response": "Initial review within 2 business days",
        "documents": "Photos are helpful",
        "fee": None,
        "legacy_tab_aliases": ["maintenance"],
        "recommended": True,
        "required_feature": "maintenance",
        "requires_lot_relationship": False,
        "stage_permissions": {},
        "allowed_roles": _RESIDENT_AND_ADMIN_ROLES,
    },
    {
        "key": "insurance-claim",
        "version": 1,
        "title": "Insurance claim",
        "description": "Report property damage and provide information for an insurance claim.",
        "category": "Insurance",
        "search_terms": ["insurance", "claim", "damage", "incident"],
        "expected_response": "Initial review within 2 business days",
        "documents": "Photos, invoices or reports may be required",
        "fee": None,
        "legacy_tab_aliases": ["insurance-claim"],
        "recommended": False,
        "required_feature": "insurance_claims",
        "requires_lot_relationship": False,
        "stage_permissions": {"review": ("can_manage_requests",)},
        "allowed_roles": _RESIDENT_AND_ADMIN_ROLES,
    },
    {
        "key": "insurance-enquiry",
        "version": 1,
        "title": "Insurance question",
        "description": "Ask about building insurance, coverage or an existing claim.",
        "category": "Insurance",
        "search_terms": ["insurance", "coverage", "policy", "question"],
        "expected_response": "Response timeframe is set by the building team",
        "documents": "No documents required to start",
        "fee": None,
        "legacy_tab_aliases": ["insurance-enquiry"],
        "recommended": False,
        "required_feature": "requests",
        "requires_lot_relationship": False,
        "stage_permissions": {"review": ("can_manage_requests",)},
        "allowed_roles": _RESIDENT_AND_ADMIN_ROLES,
    },
    {
        "key": "pet",
        "version": 1,
        "title": "Pet application",
        "description": "Apply for approval to keep a pet at your unit.",
        "category": "Pets & Residents",
        "search_terms": ["pet", "dog", "cat", "animal", "approval"],
        "expected_response": "Decision timeframe depends on building rules",
        "documents": "Pet photo and supporting records may be required",
        "fee": None,
        "legacy_tab_aliases": ["pet"],
        "recommended": True,
        "required_feature": "requests",
        "requires_lot_relationship": True,
        "stage_permissions": {"review": ("can_manage_requests",)},
        "allowed_roles": _RESIDENT_AND_ADMIN_ROLES,
    },
    {
        "key": "access-control",
        "version": 1,
        "title": "Access device request",
        "description": "Request review for a building access device or replacement.",
        "category": "Keys, Fobs & Access",
        "search_terms": ["key", "fob", "swipe", "remote", "garage", "access"],
        "expected_response": "Eligibility review before any issue or replacement",
        "documents": "Reason for request is required",
        "fee": None,
        "legacy_tab_aliases": ["access-control", "remote", "key"],
        "recommended": True,
        "required_feature": "requests",
        "requires_lot_relationship": True,
        "stage_permissions": {"review": ("can_manage_requests",), "fulfilment": ("can_manage_requests",)},
        "allowed_roles": _RESIDENT_AND_ADMIN_ROLES,
    },
    {
        "key": "alterations",
        "version": 1,
        "title": "Alterations application",
        "description": "Request approval for renovations, installations or changes to your lot.",
        "category": "Renovations & Moving",
        "search_terms": ["alteration", "renovation", "flooring", "air conditioning", "solar", "works"],
        "expected_response": "Timeframe depends on the approval pathway",
        "documents": "Plans and contractor details may be required",
        "fee": None,
        "legacy_tab_aliases": ["alterations"],
        "recommended": False,
        "required_feature": "requests",
        "requires_lot_relationship": True,
        "stage_permissions": {"review": ("can_manage_requests",)},
        "allowed_roles": _ADMIN_OWNER_AGENT_ROLES,
    },
    {
        "key": "reimbursement",
        "version": 1,
        "title": "Reimbursement request",
        "description": "Request review of an out-of-pocket building expense with receipts.",
        "category": "Money & Levies",
        "search_terms": ["reimbursement", "refund", "expense", "receipt", "payment"],
        "expected_response": "Finance review within 5 business days",
        "documents": "Receipt or tax invoice required",
        "fee": None,
        "legacy_tab_aliases": ["reimbursement"],
        "recommended": False,
        "required_feature": "requests",
        "requires_lot_relationship": True,
        "stage_permissions": {"review": ("can_manage_finances",), "fulfilment": ("can_manage_finances",)},
        "allowed_roles": _ADMIN_OWNER_ROLES,
    },
    {
        "key": "work-order",
        "version": 1,
        "title": "Work orders",
        "description": "Review work orders, assigned work and vendor quotations.",
        "category": "Repairs & Maintenance",
        "search_terms": ["work order", "vendor", "quote", "contractor", "job"],
        "expected_response": "Operational workspace",
        "documents": "Varies by work order",
        "fee": None,
        "legacy_tab_aliases": ["work-order"],
        "recommended": False,
        "required_feature": "work_orders",
        "requires_lot_relationship": False,
        "stage_permissions": {"submission": ("can_manage_meetings", "can_manage_finances")},
        "allowed_roles": frozenset({
            UserRole.SUPER_ADMIN,
            UserRole.STRATA_ADMIN,
            UserRole.STRATA_MANAGER,
            UserRole.EC_MEMBER,
            UserRole.ADMIN_STAFF,
        }),
    },
)


_DEFINITIONS_BY_KEY = {definition["key"]: definition for definition in REQUEST_DEFINITIONS}
_LOT_EXEMPT_ROLES = frozenset({
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
    UserRole.ADMIN_STAFF,
})


def is_role_eligible(definition: dict[str, Any], user: dict) -> bool:
    """Return whether the user's effective canonical role may see a definition."""
    return effective_role(user) in definition["allowed_roles"]


def public_definition(definition: dict[str, Any]) -> dict[str, Any]:
    """Remove internal authorisation metadata from an eligible response."""
    return {
        key: value
        for key, value in definition.items()
        if key not in {
            "allowed_roles",
            "required_feature",
            "requires_lot_relationship",
            "stage_permissions",
        }
    }


def get_request_definition(definition_key: str) -> dict[str, Any]:
    """Return a catalogue definition or raise a stable 404."""
    definition = _DEFINITIONS_BY_KEY.get(definition_key)
    if not definition:
        raise HTTPException(status_code=404, detail="Request definition not found")
    return definition


def _normalise_stage(stage: RequestPolicyStage | str) -> RequestPolicyStage:
    if isinstance(stage, RequestPolicyStage):
        return stage
    return RequestPolicyStage(stage)


def _with_building(user: dict, building_id: str) -> dict:
    scoped_user = dict(user)
    scoped_user["building_id"] = building_id
    return scoped_user


def _has_stage_permission(user: dict, definition: dict[str, Any], stage: RequestPolicyStage) -> bool:
    permission_names = definition.get("stage_permissions", {}).get(stage.value, ())
    if not permission_names:
        return True
    permissions = get_user_permissions(user)
    return any(getattr(permissions, name, False) for name in permission_names)


async def _has_lot_relationship(user: dict, building_id: str, db: Any = None) -> bool:
    role = effective_role(user)
    if role in _LOT_EXEMPT_ROLES:
        return True

    unit_number = str(user.get("unit_number") or "").strip()
    if not unit_number:
        return False

    if db is None:
        return True

    user_id = user.get("id")
    if hasattr(db, "user_units"):
        if not user_id:
            return False
        # user_units is the canonical user-to-lot relationship source during
        # Phase 1B. A unit existing in the building is not enough to authorise
        # a resident request from a specific user.
        relation = await db.user_units.find_one({
            "building_id": building_id,
            "user_id": user_id,
            "unit_number": unit_number,
            "is_active": {"$ne": False},
        })
        return bool(relation)

    if hasattr(db, "units"):
        # Legacy adapter fallback for tests or call sites that cannot provide
        # user_units yet. Production submission endpoints pass the real DB.
        unit = await db.units.find_one({
            "building_id": building_id,
            "unit_number": unit_number,
            "is_archived": {"$ne": True},
        })
        if unit:
            return True

    return False


async def evaluate_request_policy(
    user: dict,
    building_id: str,
    definition_key: str,
    *,
    version: int | None = None,
    stage: RequestPolicyStage | str = RequestPolicyStage.DISCOVERY,
    db: Any = None,
) -> RequestEligibilityResult:
    """Evaluate shared catalogue eligibility for a user, building and workflow stage."""
    if not user:
        return RequestEligibilityResult(False, "Authentication required")
    if not building_id:
        return RequestEligibilityResult(False, "Active building context required")

    definition = get_request_definition(definition_key)
    if version is not None and version != definition["version"]:
        return RequestEligibilityResult(False, "Request definition version is no longer current")

    scoped_user = _with_building(user, building_id)
    if not is_role_eligible(definition, scoped_user):
        return RequestEligibilityResult(False, "Request is not available for this role")

    if not await get_effective_feature_access(scoped_user, definition["required_feature"]):
        return RequestEligibilityResult(False, "Required feature is disabled")

    policy_stage = _normalise_stage(stage)
    if not _has_stage_permission(scoped_user, definition, policy_stage):
        return RequestEligibilityResult(False, "Workflow permission is required")

    if definition.get("requires_lot_relationship") and not await _has_lot_relationship(scoped_user, building_id, db):
        return RequestEligibilityResult(False, "A lot relationship in this building is required")

    return RequestEligibilityResult(True)


async def enforce_request_policy(
    user: dict,
    building_id: str,
    definition_key: str,
    *,
    version: int | None = None,
    stage: RequestPolicyStage | str = RequestPolicyStage.SUBMISSION,
    db: Any = None,
) -> None:
    """Raise a stable HTTP error when request policy denies access."""
    result = await evaluate_request_policy(
        user,
        building_id,
        definition_key,
        version=version,
        stage=stage,
        db=db,
    )
    if result.allowed:
        return
    if result.reason == "Request definition version is no longer current":
        raise HTTPException(status_code=409, detail=result.reason)
    if result.reason == "Authentication required":
        raise HTTPException(status_code=401, detail=result.reason)
    raise HTTPException(status_code=403, detail=result.reason)


async def resolve_request_catalogue(user: dict, building_id: str) -> list[dict[str, Any]]:
    """Return role- and feature-eligible definitions for the authenticated context."""
    scoped_user = _with_building(user, building_id)
    checks = await asyncio.gather(*(
        evaluate_request_policy(
            scoped_user,
            building_id,
            definition["key"],
            version=definition["version"],
            stage=RequestPolicyStage.DISCOVERY,
        )
        for definition in REQUEST_DEFINITIONS
    ))
    return [
        public_definition(definition)
        for definition, result in zip(REQUEST_DEFINITIONS, checks)
        if result.allowed
    ]
