# @featuretrace:progressive-navigation — Merged nav config, badges, preferences, and route→feature-toggle aliasing.
# Layer: router
# Data flow: NavigationContext.tsx (GET /navigation/config, /navigation/badges, PATCH /navigation/preferences) →
#            this router → navigation_configs.py (seed) + feature_toggles collection (building-scoped).
# Related: frontend/src/contexts/NavigationContext.tsx
#           frontend/src/components/layout/DashboardLayout.tsx
#           backend/seeds/navigation_configs.py
#           backend/seeds/feature_toggles.py
#           backend/routers/feature_toggles.py
# Tests: tests/frontend/unit/navigation/NavigationContext.test.tsx
#        tests/frontend/test_nav_links.spec.ts
"""Navigation API — Progressive Navigation System."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional

from database import db
from routers.feature_toggles import _resolve_effective_feature_access_entries
from utils.auth import get_current_building, get_current_user
from utils.permissions import get_user_permissions

router = APIRouter(prefix="/navigation", tags=["Navigation"])

ROUTE_FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "/requests/my-approvals": ("approvals",),
    "/community/events": ("events",),
    "/insurance": ("insurance_claims",),
    "/financials/levy-payments": ("finance", "levy_payments"),
}


# ─── Models ────────────────────────────────────────────────────────────────────

class PreferencesUpdate(BaseModel):
    preferred_mode: Optional[str] = None  # "simple" | "advanced"
    pinned_items: Optional[list] = None
    hidden_items: Optional[list] = None
    custom_order: Optional[list] = None
    features_seen: Optional[dict] = None
    nudge_cooldown_days: Optional[int] = None
    last_nudge_dismissed_at: Optional[str] = None


class TrackEvent(BaseModel):
    feature_id: str
    route: str
    event_type: str = "page_view"  # "page_view" | "action" | "return_visit"
    session_id: str = ""


# ─── Helpers ────────────────────────────────────────────────────────────────

def _normalise_route(route: str) -> str:
    """Generated function header.

    Function: _normalise_route
    Path: backend/routers/navigation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not route:
        return ""
    normalised = route.rstrip("/")
    return normalised or "/"


def _route_matches(item_route: str, feature_route: str) -> bool:
    """Generated function header.

    Function: _route_matches
    Path: backend/routers/navigation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not item_route or not feature_route:
        return False
    return item_route == feature_route or item_route.startswith(f"{feature_route}/")


def _matching_feature_keys(item_route: str, feature_routes: dict[str, list[str]]) -> set[str]:
    """Generated function header.

    Function: _matching_feature_keys
    Path: backend/routers/navigation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    matches = set(ROUTE_FEATURE_ALIASES.get(item_route, ()))
    for feature_key, routes in feature_routes.items():
        if any(_route_matches(item_route, route) for route in routes):
            matches.add(feature_key)
    return matches


# ─── GET /navigation/config ──────────────────────────────────────────────────

@router.get("/config", summary="Merged navigation config for current user")
async def get_nav_config(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_nav_config
    Path: backend/routers/navigation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = current_user.get("effective_role") or current_user.get("role", "owner")
    user_id = current_user["id"]

    # 1. Load role config
    nav_config = await db.navigation_configs.find_one({"role": role})
    if not nav_config:
        return {
            "role": role,
            "building_id": building_id,
            "mode": "simple",
            "simple_items": [],
            "advanced_items": [],
            "pinned_items": [],
            "nudge": None
        }

    # 2. Load feature toggles (enabled only)
    resolved_feature_entries = await _resolve_effective_feature_access_entries(
        current_user=current_user,
        building_id=building_id,
    )
    effective_feature_access = {
        feature["feature_key"]: feature["effective_access"]
        for feature in resolved_feature_entries
    }
    feature_routes = {
        feature["feature_key"]: [_normalise_route(route) for route in feature.get("routes", []) if route]
        for feature in resolved_feature_entries
    }
    nav_customisation_enabled = effective_feature_access.get("nav_customisation") is not False
    nav_discovery_nudges_enabled = effective_feature_access.get("nav_discovery_nudges") is not False

    # 3. Filter items by feature_flag + permission_flag
    # Permission flags are a legacy fallback until each item has a
    # backing_capability. Resolve them from the permission model because
    # authenticated user claims do not contain flattened can_* fields.
    can_flags = get_user_permissions(current_user).model_dump()

    def _is_visible(item: dict) -> bool:
        """Generated function header.

        Function: _is_visible
        Path: backend/routers/navigation.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        ff = item.get("feature_flag", "")
        if ff:
            effective_access = effective_feature_access.get(ff)
            if effective_access is False:
                return False
        elif item.get("route"):
            item_route = _normalise_route(item["route"])
            matched_feature_keys = _matching_feature_keys(item_route, feature_routes)
            if any(effective_feature_access.get(feature_key) is False for feature_key in matched_feature_keys):
                return False
        pf = item.get("permission_flag", "")
        if pf and not can_flags.get(pf, False):
            return False
        return True

    simple_items = [i for i in nav_config.get("simple_items", []) if _is_visible(i)]
    advanced_items = [i for i in nav_config.get("advanced_items", []) if _is_visible(i)]

    # 4. Load user preferences
    prefs = await db.user_nav_preferences.find_one(
        {"user_id": user_id, "building_id": building_id}
    ) or {}

    mode = "classic" if not nav_customisation_enabled else prefs.get("preferred_mode", "simple")
    hidden = set(prefs.get("hidden_items", []))
    pinned_ids = prefs.get("pinned_items", [])
    custom_order = prefs.get("custom_order", [])

    # 5. Remove hidden items from both lists. Hidden preferences are a user
    # contract, not a sidebar-mode implementation detail.
    simple_items = [i for i in simple_items if i["id"] not in hidden]
    advanced_items = [i for i in advanced_items if i["id"] not in hidden]

    # 6. Build pinned items list (advanced items pinned by user).
    # Items that are no longer visible (hidden by feature flag / permission) are
    # silently dropped — the stale preference is harmless and will self-heal.
    advanced_map = {i["id"]: i for i in advanced_items}
    pinned_items = [
        {**advanced_map[pid], "isPinned": True}
        for pid in pinned_ids
        if pid in advanced_map
    ]

    # 7. Apply custom_order to simple_items
    if custom_order:
        order_map = {iid: idx for idx, iid in enumerate(custom_order)}
        simple_items.sort(key=lambda x: order_map.get(x["id"], 9999))

    # Prepend pinned to simple
    pinned_set = set(pinned_ids)
    simple_with_pinned = pinned_items + [i for i in simple_items if i["id"] not in pinned_set]

    # 8. Load adaptive scores and sort advanced items
    scores_doc = await db.adaptive_nav_scores.find_one(
        {"user_id": user_id, "building_id": building_id}
    )
    scores = scores_doc.get("scores", {}) if scores_doc else {}

    def _adv_sort_key(item: dict):
        """Generated function header.

        Function: _adv_sort_key
        Path: backend/routers/navigation.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        score = scores.get(item["id"], 0.0)
        return (-score, item.get("priority", 99))

    advanced_items_sorted = sorted(advanced_items, key=_adv_sort_key)

    # 9. Compute nudge and normalise the payload to match frontend expectations
    login_count = current_user.get("login_count", 0)
    nudge = None
    if nav_customisation_enabled and nav_discovery_nudges_enabled:
        try:
            from services.adaptive_nav_service import get_nudge_for_user
            raw_nudge = await get_nudge_for_user(
                user_id=user_id,
                role=role,
                building_id=building_id,
                nav_config=nav_config,
                prefs=prefs,
                login_count=login_count,
            )
            if raw_nudge:
                # Normalise service output {feature_id, hint, route} to frontend shape
                nudge = {
                    "feature_id": raw_nudge.get("feature_id", ""),
                    "type": "tooltip",
                    "message": raw_nudge.get("hint", ""),
                    "discovery_hint": raw_nudge.get("hint", ""),
                    "target_route": raw_nudge.get("route", ""),
                    "action": "discover",
                }
        except Exception:
            pass

    # 10. Check for unseen advanced features
    features_seen = prefs.get("features_seen", {})
    has_unseen = nav_customisation_enabled and nav_discovery_nudges_enabled and any(
        not features_seen.get(item["id"], False)
        for item in advanced_items_sorted
    )

    return {
        "role": role,
        "building_id": building_id,
        "mode": mode,
        "simple_items": simple_with_pinned,
        "advanced_items": advanced_items_sorted,
        "pinned_items": pinned_items,
        "has_unseen_advanced_features": has_unseen,
        "nudge": nudge,
    }


# ─── PATCH /navigation/preferences ──────────────────────────────────────────

@router.patch("/preferences", summary="Upsert navigation preferences")
async def upsert_preferences(
        body: PreferencesUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: upsert_preferences
    Path: backend/routers/navigation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user_id = current_user["id"]
    role = current_user.get("effective_role") or current_user.get("role", "owner")

    # Validate pinned_items max 2
    if body.pinned_items is not None and len(body.pinned_items) > 2:
        raise HTTPException(status_code=400, detail="Maximum 2 pinned items allowed")

    # Validate item IDs exist in nav config
    if body.pinned_items is not None or body.hidden_items is not None:
        nav_config = await db.navigation_configs.find_one({"role": role})
        if nav_config:
            valid_ids = set(
                [i["id"] for i in nav_config.get("simple_items", [])] +
                [i["id"] for i in nav_config.get("advanced_items", [])]
            )
            if body.pinned_items:
                bad = [pid for pid in body.pinned_items if pid not in valid_ids]
                if bad:
                    raise HTTPException(status_code=400, detail=f"Unknown item IDs: {bad}")
            if body.hidden_items:
                bad = [hid for hid in body.hidden_items if hid not in valid_ids]
                if bad:
                    raise HTTPException(status_code=400, detail=f"Unknown item IDs: {bad}")

    now = datetime.now(timezone.utc).isoformat()
    update_fields: dict = {"updated_at": now}

    if body.preferred_mode is not None:
        if body.preferred_mode not in ("simple", "advanced", "classic"):
            raise HTTPException(status_code=400, detail="mode must be 'simple', 'advanced', or 'classic'")
        update_fields["preferred_mode"] = body.preferred_mode

    if body.pinned_items is not None:
        update_fields["pinned_items"] = body.pinned_items

    if body.hidden_items is not None:
        update_fields["hidden_items"] = body.hidden_items

    if body.custom_order is not None:
        update_fields["custom_order"] = body.custom_order

    if body.features_seen is not None:
        # Merge with existing features_seen (never delete old keys)
        for k, v in body.features_seen.items():
            update_fields[f"features_seen.{k}"] = v

    if body.nudge_cooldown_days is not None:
        update_fields["nudge_cooldown_days"] = body.nudge_cooldown_days

    if body.last_nudge_dismissed_at is not None:
        update_fields["last_nudge_dismissed_at"] = body.last_nudge_dismissed_at

    await db.user_nav_preferences.update_one(
        {"user_id": user_id, "building_id": building_id},
        {
            "$set": update_fields,
            "$setOnInsert": {
                "id": f"navpref-{user_id}-{building_id}",
                "user_id": user_id,
                "building_id": building_id,
                "created_at": now,
            }
        },
        upsert=True
    )

    return {"status": "ok"}


# ─── POST /navigation/track ──────────────────────────────────────────────────

async def _insert_usage_event(
        user_id: str,
        building_id: str,
        feature_id: str,
        route: str,
        event_type: str,
        session_id: str,
):
    """Background task — fire and forget. Errors are logged, not raised."""
    import uuid
    try:
        now = datetime.now(timezone.utc).isoformat()
        # Bypassing TenantCollection here because there is no request context in a
        # BackgroundTask. building_id MUST be included in the document manually —
        # omitting it would silently break multi-tenant isolation.
        await db._db.feature_usage_events.insert_one({
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "user_id": user_id,
            "feature_id": feature_id,
            "route": route,
            "event_type": event_type,
            "session_id": session_id,
            "created_at": now,
            "is_test_data": False,
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"nav track insert failed: {e}")


@router.post("/track", summary="Track navigation event (fire-and-forget)")
async def track_navigation(
        body: TrackEvent,
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: track_navigation
    Path: backend/routers/navigation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user_id = current_user["id"]

    # Never block — add to background
    background_tasks.add_task(
        _insert_usage_event,
        user_id=user_id,
        building_id=building_id,
        feature_id=body.feature_id,
        route=body.route,
        event_type=body.event_type,
        session_id=body.session_id,
    )

    return {"status": "ok"}


# ─── GET /navigation/badges ──────────────────────────────────────────────────

@router.get("/badges", summary="Badge counts for navigation items")
async def get_nav_badges(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: get_nav_badges
    Path: backend/routers/navigation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user_id = current_user["id"]
    role = current_user.get("effective_role") or current_user.get("role", "owner")
    unit_number = current_user.get("unit_number") or current_user.get("lot_id")
    now_str = datetime.now(timezone.utc).isoformat()

    result = {
        "requests_overdue": 0,
        "requests_new": 0,
        "notices_unread": 0,
        "parcels_waiting": 0,
        "proposals_open_vote": 0,
        "approvals_pending": 0,
        "compliance_overdue": 0,
        "sla_breached": 0,
        "levy_due_soon": 0,
        "tenant_approvals_pending": 0,
    }

    try:
        # Performance Optimization⚡: Parallelize independent badge queries into a single asyncio.gather call
        tasks = []
        keys = []

        # 1. requests_overdue / sla_breached (manager roles)
        if role in ("strata_manager", "super_admin", "ec_member", "strata_admin"):
            tasks.append(db.workflow_requests.count_documents(
                {"building_id": building_id,
                 "sla_breached": True, "status": {"$nin": ["closed", "auto_resolved"]},
                 "is_test_data": {"$ne": True}}
            ))
            keys.append("sla")

        # 2. requests_new (in_progress unread by user)
        tasks.append(db.workflow_requests.count_documents(
            {"building_id": building_id,
             "status": "in_progress", "unread_by": user_id,
             "is_test_data": {"$ne": True}}
        ))
        keys.append("requests_new")

        # 3. notices_unread — total notices VISIBLE to this user's role (non-expired, role-scoped)
        tasks.append(db.notices.count_documents({"$and": [
            {"building_id": building_id, "is_test_data": {"$ne": True}},
            {"$or": [{"expires_at": None}, {"expires_at": {"$gt": now_str}}]},
            {"$or": [
                {"target_roles": None},
                {"target_roles": []},
                {"target_roles": role},
            ]},
        ]}))
        keys.append("notices_unread")

        # 4. parcels_waiting
        if unit_number:
            tasks.append(db.parcels.count_documents(
                {"building_id": building_id,
                 "unit_number": unit_number, "status": "received",
                 "is_test_data": {"$ne": True}}
            ))
            keys.append("parcels_waiting")

        # 5. proposals_open_vote
        if role in ("owner", "strata_admin", "ec_member", "strata_manager", "super_admin"):
            tasks.append(db.proposals.find(
                {"building_id": building_id,
                 "status": "open", "voting_closes_at": {"$gte": now_str},
                 "is_test_data": {"$ne": True}},
                {"_id": 0, "votes": 1}
            ).to_list(50))
            keys.append("proposals_open_vote")

        # 6. approvals_pending (EC/manager only)
        if role in ("ec_member", "strata_admin", "strata_manager", "super_admin"):
            tasks.append(db.invoices.count_documents(
                {"building_id": building_id,
                 "approval_status": "pending", "is_test_data": {"$ne": True}}
            ))
            keys.append("approvals_pending")

        # 7. compliance_overdue (manager/chairman only)
        if role in ("strata_manager", "super_admin", "ec_member", "strata_admin"):
            today = datetime.now(timezone.utc).date().isoformat()
            tasks.append(db.compliance_items.count_documents(
                {"building_id": building_id,
                 "due_date": {"$lt": today}, "status": {"$ne": "completed"},
                 "is_test_data": {"$ne": True}}
            ))
            keys.append("compliance_overdue")

        # 8. tenant_approvals_pending — pending tenant/guest registrations awaiting owner approval
        if role in ("owner", "strata_admin", "ec_member", "strata_manager", "super_admin"):
            tenant_query: dict = {
                "building_id": building_id,
                "status": {"$in": ["pending_owner_approval", "pending"]},
                "role": {"$in": ["tenant", "guest"]},
                "is_approved": False,
                "is_test_data": {"$ne": True},
            }
            if role == "owner" and unit_number:
                tenant_query["unit_number"] = unit_number
            tasks.append(db.users.count_documents(tenant_query))
            keys.append("tenant_approvals_pending")

        res_list = await asyncio.gather(*tasks)
        res_map = dict(zip(keys, res_list))

        if "sla" in res_map:
            sla = res_map["sla"]
            result["requests_overdue"] = sla
            result["sla_breached"] = sla

        if "requests_new" in res_map:
            result["requests_new"] = res_map["requests_new"]

        if "notices_unread" in res_map:
            result["notices_unread"] = res_map["notices_unread"]

        if "parcels_waiting" in res_map:
            result["parcels_waiting"] = res_map["parcels_waiting"]

        if "proposals_open_vote" in res_map:
            open_props = res_map["proposals_open_vote"]
            result["proposals_open_vote"] = sum(
                1 for p in open_props
                if not any(v.get("user_id") == user_id for v in p.get("votes", []))
            )

        if "approvals_pending" in res_map:
            result["approvals_pending"] = res_map["approvals_pending"]

        if "compliance_overdue" in res_map:
            result["compliance_overdue"] = res_map["compliance_overdue"]

        if "tenant_approvals_pending" in res_map:
            result["tenant_approvals_pending"] = res_map["tenant_approvals_pending"]

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"nav badges query failed: {e}")

    return result
