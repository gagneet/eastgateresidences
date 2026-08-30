"""
Adaptive Navigation Service — computes per-user feature scores and nudges.

Scores use a 3-factor model (recency, frequency, peer percentile):
  score = (recency × 0.40) + (frequency × 0.35) + (peer_percentile × 0.25)

  recency   = max(0, 1 - (days_since_last_use × 0.07))  — linear decay, 0 after ~14 days
  frequency = min(1, uses_in_30d / 20)                  — caps at 20 uses
  peer      = percentile rank vs same-role users in this building

The nightly cron calls compute_scores_for_building() for each active building.
"""
from datetime import datetime, timedelta, timezone

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _recency_score(last_used_str: Optional[str], now: datetime) -> float:
    """Linear recency decay: 1.0 today, 0.0 at 14.3 days."""
    if not last_used_str:
        return 0.0
    try:
        last_used = datetime.fromisoformat(last_used_str.replace("Z", "+00:00"))
        days_ago = (now - last_used).total_seconds() / 86400
        return max(0.0, 1.0 - (days_ago * 0.07))
    except Exception:
        return 0.0


def _frequency_score(count: int) -> float:
    """Frequency score: linear 0→1, caps at 20 uses."""
    return min(1.0, count / 20.0)


def _peer_percentile(count: int, peer_counts: list) -> float:
    """Percentile rank of this user's count among peer counts."""
    if not peer_counts:
        return 0.5
    rank = sum(1 for c in peer_counts if c <= count)
    return rank / len(peer_counts)


# ─── compute_scores_for_building ─────────────────────────────────────────────

async def compute_scores_for_building(building_id: str, raw_db=None) -> int:
    """
    Recompute adaptive nav scores for all users in a building.
    Returns the number of user score documents written.

    raw_db: optional raw Motor database handle (e.g. passed by the cron job).
            If omitted, uses the application database singleton (database.db).
            When using raw_db, queries bypass TenantCollection but include explicit
            building_id filters for correct tenant isolation.
    """
    from pymongo import UpdateOne

    if raw_db is not None:
        # Cron context: raw_db is a Motor database (not TenantScopedDatabase).
        # All queries must include explicit building_id.
        usage_coll = raw_db.feature_usage_events
        scores_coll = raw_db.adaptive_nav_scores
        nav_cfg_coll = raw_db.navigation_configs
        users_coll = raw_db.users
    else:
        from database import db as app_db
        # TenantCollection reads work with explicit building_id in the filter.
        # For bulk_write we use the underlying raw collection (_db) to avoid
        # TenantCollection not supporting bulk_write.
        usage_coll = app_db.feature_usage_events
        scores_coll = app_db._db.adaptive_nav_scores
        nav_cfg_coll = app_db.navigation_configs
        users_coll = app_db.users

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    # 1. Get all active users in this building
    users = await users_coll.find(
        {"building_id": building_id, "is_active": True},
        {"_id": 0, "id": 1, "role": 1}
    ).to_list(length=None)

    if not users:
        return 0

    # 2. Aggregate usage counts per (user_id, feature_id) over 30 days
    pipeline = [
        {"$match": {
            "building_id": building_id,
            "created_at": {"$gte": since},
            "is_test_data": {"$ne": True},
        }},
        {"$group": {
            "_id": {"user_id": "$user_id", "feature_id": "$feature_id"},
            "usage_count_30d": {"$sum": 1},
            "last_used_at": {"$max": "$created_at"},
        }},
    ]
    usage_docs = await usage_coll.aggregate(pipeline).to_list(length=None)

    # 3. Build lookup: {user_id: {feature_id: {count, last_used}}}
    usage_map: dict = {}
    for doc in usage_docs:
        uid = doc["_id"]["user_id"]
        fid = doc["_id"]["feature_id"]
        usage_map.setdefault(uid, {})[fid] = {
            "count": doc["usage_count_30d"],
            "last_used": doc["last_used_at"],
        }

    # 4. Build peer map: {role: {feature_id: [counts]}}
    peer_map: dict = {}
    for uid, features in usage_map.items():
        user = next((u for u in users if u["id"] == uid), None)
        if not user:
            continue
        role = user["role"]
        for fid, data in features.items():
            peer_map.setdefault(role, {}).setdefault(fid, []).append(data["count"])

    # 5. Compute 3-factor score for each user
    now_str = now.isoformat()
    ops = []

    for user in users:
        uid = user["id"]
        role = user["role"]
        user_usage = usage_map.get(uid, {})

        # Get feature IDs for this role from navigation_configs
        nav_config = await nav_cfg_coll.find_one({"role": role})
        if not nav_config:
            continue
        all_features = (
                [item["id"] for item in nav_config.get("simple_items", [])] +
                [item["id"] for item in nav_config.get("advanced_items", [])]
        )

        scores: dict = {}
        for fid in all_features:
            usage = user_usage.get(fid, {})
            count = usage.get("count", 0)
            last_used_str = usage.get("last_used")

            recency = _recency_score(last_used_str, now)
            frequency = _frequency_score(count)
            peer = _peer_percentile(count, peer_map.get(role, {}).get(fid, []))

            scores[fid] = round(
                (recency * 0.40) + (frequency * 0.35) + (peer * 0.25), 4
            )

        ops.append(UpdateOne(
            {"user_id": uid, "building_id": building_id},
            {
                "$set": {
                    "user_id": uid,
                    "building_id": building_id,
                    "scores": scores,
                    "computed_at": now_str,
                    "is_test_data": False,
                },
                "$setOnInsert": {"created_at": now_str},
            },
            upsert=True,
        ))

    if not ops:
        return 0

    # Bulk-write all score documents in one round-trip (repo guideline: batch writes)
    await scores_coll.bulk_write(ops, ordered=False)
    return len(ops)


# ─── Nudge evaluation helpers ─────────────────────────────────────────────────

async def _evaluate_trigger(trigger: str, user_id: str, building_id: str, db_instance) -> bool:
    """Evaluate a nudge trigger expression. Returns True if the trigger fires."""
    if not trigger:
        return False

    try:
        if trigger == "multiple_buildings_accessible":
            user = await db_instance.users.find_one(
                {"id": user_id}, {"_id": 0, "accessible_building_ids": 1}
            )
            return len((user or {}).get("accessible_building_ids", [])) > 1

        if trigger == "open_requests_gte_5":
            count = await db_instance.workflow_requests.count_documents(
                {"building_id": building_id, "status": "open",
                 "is_test_data": {"$ne": True}}
            )
            return count >= 5

        if trigger == "levy_page_visits_7d_gte_3":
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            count = await db_instance.feature_usage_events.count_documents(
                {"building_id": building_id, "user_id": user_id,
                 "feature_id": "my-levies",
                 "created_at": {"$gte": since},
                 "is_test_data": {"$ne": True}}
            )
            return count >= 3

        if trigger == "login_count_gte_10_and_property_finance_never_seen":
            user = await db_instance.users.find_one(
                {"id": user_id}, {"_id": 0, "login_count": 1}
            )
            if not user or user.get("login_count", 0) < 10:
                return False
            prefs = await db_instance.user_nav_preferences.find_one(
                {"user_id": user_id, "building_id": building_id},
                {"_id": 0, "features_seen": 1}
            )
            return not (prefs or {}).get("features_seen", {}).get("property-finance", False)

        if trigger == "savings_event_created_last_7d":
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            count = await db_instance.savings_events.count_documents(
                {"building_id": building_id, "verified": True,
                 "created_at": {"$gte": since},
                 "is_test_data": {"$ne": True}}
            )
            return count > 0

    except Exception as e:
        logger.warning(f"nudge trigger eval failed ({trigger}): {e}")

    return False


# ─── get_nudge_for_user ───────────────────────────────────────────────────────

def _rotate_nudge_candidates(candidates: list[dict], last_feature_id: Optional[str]) -> list[dict]:
    """
    Return priority-ordered candidates starting after the last served feature.

    The old behaviour always evaluated the lowest-priority-number candidate
    first, so a user could see the same suggestion on every login until they
    clicked or dismissed it. Rotation is intentionally based on the last served
    feature, not the last clicked feature, so login reloads progress through the
    eligible unseen set even when the user ignores the tooltip.
    """
    ordered = sorted(candidates, key=lambda x: (x.get("priority", 99), x.get("id", "")))
    if not last_feature_id:
        return ordered

    last_idx = next(
        (idx for idx, item in enumerate(ordered) if item.get("id") == last_feature_id),
        -1,
    )
    if last_idx < 0:
        return ordered
    return ordered[last_idx + 1:] + ordered[:last_idx + 1]


async def _remember_served_nudge(
        user_id: str,
        building_id: str,
        feature_id: str,
        db_instance,
) -> None:
    """
    Persist the nudge rotation cursor without changing discovery state.

    This does not mark the feature as seen. Only frontend navigation tracking
    writes features_seen, because a surfaced tooltip is not the same as the user
    opening the menu item.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db_instance.user_nav_preferences.update_one(
            {"user_id": user_id, "building_id": building_id},
            {
                "$set": {
                    "last_nudge_feature_id": feature_id,
                    "last_nudge_served_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "id": f"navpref-{user_id}-{building_id}",
                    "user_id": user_id,
                    "building_id": building_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning("adaptive_nav_service: failed to persist nudge cursor: %s", exc)


async def get_nudge_for_user(
        user_id: str,
        role: str,
        building_id: str,
        nav_config: dict,
        prefs: dict,
        login_count: int,
) -> Optional[dict]:
    """
    Return a single nudge dict for the user, or None.

    Nudge shape returned: {"feature_id": str, "hint": str, "route": str}
    The caller (navigation router) normalises this to the full frontend payload.
    """
    from database import db

    # Respect cooldown (default 1 day) so one suggestion can surface each login day.
    cooldown_days = prefs.get("nudge_cooldown_days", 1)
    if cooldown_days == 0:
        return None

    last_dismissed = prefs.get("last_nudge_dismissed_at")
    if last_dismissed:
        try:
            dismissed_dt = datetime.fromisoformat(last_dismissed.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - dismissed_dt).days < cooldown_days:
                return None
        except Exception as exc:
            logger.warning("adaptive_nav_service: nudge dismissed-at timestamp parse failed: %s", exc)

    features_seen = prefs.get("features_seen", {})
    candidates = [
        item for item in nav_config.get("advanced_items", [])
        if item.get("route") and not features_seen.get(item["id"], False)
    ]

    if not candidates:
        return None

    # Rotate after the last served nudge so repeated logins/config fetches do
    # not keep selecting the same first-priority item. Dismissal cooldown still
    # suppresses all nudges; features_seen still permanently removes items from
    # the candidate set.
    for item in _rotate_nudge_candidates(candidates, prefs.get("last_nudge_feature_id")):
        trigger = item.get("nudge_trigger", "")
        hint = item.get("discovery_hint") or f"Try {item.get('label', 'this feature')}."
        triggered = True
        if trigger:
            triggered = await _evaluate_trigger(trigger, user_id, building_id, db)
        if triggered:
            await _remember_served_nudge(user_id, building_id, item["id"], db)
            return {
                "feature_id": item["id"],
                "hint": hint,
                "route": item.get("route", ""),
            }

    return None
