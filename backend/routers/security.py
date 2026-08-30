# @featuretrace:security-ip-logging — Security & IP Logs API: stats, login activity, IP intelligence.
# Layer: router
# Data flow: SecurityIPLogsPage.jsx -> /security/stats + /security/login-attempts
#            -> login_audit_logs + core.users name resolution -> table + cards (global).
# Related: backend/utils/audit_search.py
#          backend/utils/client_ip.py
#          frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
# Tests: tests/backend/test_client_ip_and_audit_search.py
"""
Security Router — IP Logging & Login Audit

Provides endpoints for super admins to monitor login activity,
detect suspicious events, and view IP intelligence.
Owners can view their own login history via /security/my-activity.
"""

import math
import re
import uuid
from datetime import datetime, timezone, timedelta

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pymongo import DESCENDING
from typing import Optional

from database import db
from models.security import (
    FlagIPRequest,
)
from models.user import UserRole
from utils.audit_search import SEARCH_HELP, parse_audit_query
from utils.auth import get_current_user, effective_role
from utils.geo import get_real_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security")

_ADMIN_ROLES = {UserRole.SUPER_ADMIN}


def _require_super_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_super_admin
    Path: backend/routers/security.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_user


def _audit_doc_to_response(doc: dict) -> dict:
    """Convert a MongoDB audit doc to a clean response dict."""
    doc.pop("_id", None)
    return doc


# ─────────────────────────────────────────────
# GET /security/stats
# ─────────────────────────────────────────────
@router.get("/stats")
async def get_security_stats(
        current_user: dict = Depends(_require_super_admin),
):
    """Aggregate stats for the security dashboard overview."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    base_filter = {"attempted_at": {"$gte": cutoff}, "is_test_data": {"$ne": True}}

    # Performance Optimization⚡: Consolidated 9 sequential database calls into a single MongoDB aggregation pipeline using $facet.
    # This reduces database round-trips from 9 to 1, significantly improving performance of the security dashboard.
    pipeline = [
        {"$match": base_filter},
        {"$facet": {
            "total_logins": [
                {"$match": {"status": "success"}},
                {"$count": "count"}
            ],
            "failed_attempts": [
                {"$match": {"status": "failed"}},
                {"$count": "count"}
            ],
            "suspicious_events": [
                {"$match": {"risk_score": {"$gte": 50}}},
                {"$count": "count"}
            ],
            "unique_ips": [
                {"$group": {"_id": "$ip_address"}},
                {"$count": "count"}
            ],
            "unique_countries": [
                {"$group": {"_id": "$geo.country_code"}},
                {"$count": "count"}
            ],
            "daily_activity": [
                {"$addFields": {"date": {"$substr": ["$attempted_at", 0, 10]}}},
                {"$group": {
                    "_id": {"date": "$date", "status": "$status"},
                    "count": {"$sum": 1}
                }},
                {"$sort": {"_id.date": 1}}
            ],
            "country_distribution": [
                {"$group": {
                    "_id": {"code": "$geo.country_code", "name": "$geo.country_name"},
                    "count": {"$sum": 1}
                }},
                {"$sort": {"count": -1}},
                {"$limit": 10}
            ],
            "device_distribution": [
                {"$group": {"_id": "$device_info.device_type", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}}
            ],
            "top_failed_ips": [
                {"$match": {"status": "failed"}},
                {"$group": {
                    "_id": "$ip_address",
                    "count": {"$sum": 1},
                    "last_seen": {"$max": "$attempted_at"},
                    "country_code": {"$first": "$geo.country_code"},
                    "country_name": {"$first": "$geo.country_name"},
                }},
                {"$sort": {"count": -1}},
                {"$limit": 10}
            ]
        }}
    ]

    try:
        agg_results = await db.login_audit_logs.aggregate(pipeline).to_list(1)
        res = agg_results[0] if agg_results else {}
    except Exception as e:
        logger.error(f"Security stats aggregation failed: {e}")
        res = {}

    # Extract single values from facet results
    total_logins = res.get("total_logins", [{}])[0].get("count", 0) if res.get("total_logins") else 0
    failed_attempts = res.get("failed_attempts", [{}])[0].get("count", 0) if res.get("failed_attempts") else 0
    suspicious_events = res.get("suspicious_events", [{}])[0].get("count", 0) if res.get("suspicious_events") else 0
    unique_ips = res.get("unique_ips", [{}])[0].get("count", 0) if res.get("unique_ips") else 0
    unique_countries = res.get("unique_countries", [{}])[0].get("count", 0) if res.get("unique_countries") else 0

    # Process daily activity
    daily_map = {}
    for row in res.get("daily_activity", []):
        date = row["_id"]["date"]
        status = row["_id"]["status"]
        if date not in daily_map:
            daily_map[date] = {"date": date, "success": 0, "failed": 0}
        if status == "success":
            daily_map[date]["success"] = row["count"]
        elif status in ("failed", "deactivated"):
            daily_map[date]["failed"] += row["count"]
    daily_activity = sorted(daily_map.values(), key=lambda x: x["date"])

    # Format distributions
    country_distribution = [
        {"country_code": r["_id"]["code"], "country_name": r["_id"]["name"], "count": r["count"]}
        for r in res.get("country_distribution", [])
    ]
    device_distribution = [
        {"device_type": r["_id"] or "unknown", "count": r["count"]}
        for r in res.get("device_distribution", [])
    ]
    top_failed_ips = [
        {
            "ip": r["_id"],
            "count": r["count"],
            "last_seen": r["last_seen"],
            "country_code": r.get("country_code"),
            "country_name": r.get("country_name"),
        }
        for r in res.get("top_failed_ips", [])
    ]

    return {
        "total_logins_30d": total_logins,
        "failed_attempts_30d": failed_attempts,
        "unique_ips_30d": unique_ips,
        "unique_countries_30d": unique_countries,
        "suspicious_events_30d": suspicious_events,
        "daily_activity": daily_activity,
        "country_distribution": country_distribution,
        "device_distribution": device_distribution,
        "top_failed_ips": top_failed_ips,
    }


# ─────────────────────────────────────────────
# GET /security/login-attempts
# ─────────────────────────────────────────────
@router.get("/login-attempts")
async def get_login_attempts(
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=100),
        status: Optional[str] = Query(None),  # success | failed | deactivated
        user_id: Optional[str] = Query(None),
        ip: Optional[str] = Query(None),
        country: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),  # ISO date string
        date_to: Optional[str] = Query(None),
        search: Optional[str] = Query(None),  # search by email
        current_user: dict = Depends(_require_super_admin),
):
    """Paginated login attempt log with filters."""
    query: dict = {"is_test_data": {"$ne": True}}

    if status:
        query["status"] = status
    if user_id:
        query["user_id"] = user_id
    if ip:
        query["ip_address"] = {"$regex": re.escape(ip), "$options": "i"}
    if country:
        query["geo.country_code"] = country.upper()
    # Field-scoped search with exclusions. The previous behaviour — substring on
    # email only — could not express "everything EXCEPT the monitoring probe",
    # which is the first thing anyone needs from a security log. Grammar and the
    # help payload live in utils/audit_search.py so the UI cannot drift from it.
    search_filter, unknown_search_fields = parse_audit_query(search)

    date_filter = {}
    if date_from:
        date_filter["$gte"] = date_from
    if date_to:
        date_filter["$lte"] = date_to
    if date_filter:
        query["attempted_at"] = date_filter

    if search_filter:
        query = {"$and": [query, search_filter]}

    skip = (page - 1) * per_page
    total = await db.login_audit_logs.count_documents(query)

    cursor = db.login_audit_logs.find(query, {"_id": 0}).sort(
        "attempted_at", DESCENDING
    ).skip(skip).limit(per_page)
    docs = await cursor.to_list(per_page)

    await _resolve_display_names(docs)

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "items": docs,
        # Surfaced so a mistyped field shows a warning instead of silently
        # matching everything and reading as "no results".
        "unknown_search_fields": unknown_search_fields,
        "search_help": SEARCH_HELP,
    }


async def _resolve_display_names(docs: list[dict]) -> None:
    """Attach ``user_full_name`` to each audit row, in place.

    Three lookups, because one is not enough and the original only did the first:

    1. **Mongo ``db.users`` by id.** Works for Mongo-backed buildings.
    2. **Postgres ``core.users`` by id.** East Gate's identity is on Postgres, so
       ids from those logins resolve to nothing in Mongo — every such row showed
       an em-dash where the name should be. ``core.users`` carries an RLS bypass
       clause (migration 0014), so the sentinel tenant is the correct context
       here and a per-tenant session would be wrong: an actor may belong to a
       different tenant than the row (footgun #11).
    3. **By email, for rows with no ``user_id`` at all.** A failed login against
       an unknown address genuinely has no user, but a failed PASSWORD attempt
       against a real account does — and those rows were losing the name purely
       because the attempt never got far enough to attach an id.

    Never raises: a name is a convenience, and a lookup failure must not take
    down the security log that is being consulted precisely when things are
    going wrong.
    """
    if not docs:
        return

    pending_ids = {d["user_id"] for d in docs if d.get("user_id")}
    names_by_id: dict[str, str] = {}
    names_by_email: dict[str, str] = {}

    # 1. Mongo
    try:
        if pending_ids:
            async for u in db.users.find(
                {"id": {"$in": list(pending_ids)}}, {"_id": 0, "id": 1, "full_name": 1}
            ):
                if u.get("full_name"):
                    names_by_id[u["id"]] = u["full_name"]
    except Exception:  # noqa: BLE001
        logger.warning("login activity: Mongo name lookup failed", exc_info=True)

    unresolved_ids = [i for i in pending_ids if i not in names_by_id]
    missing_emails = sorted({
        (d.get("email") or "").lower()
        for d in docs
        if d.get("email") and not names_by_id.get(d.get("user_id") or "")
    })

    # 2 + 3. Postgres, by id then by email, under the bypass sentinel.
    if unresolved_ids or missing_emails:
        try:
            from sqlalchemy import text

            from db_postgres.session import async_session_context, set_tenant

            async with async_session_context() as session:
                await set_tenant(session, "00000000-0000-0000-0000-000000000000")

                if unresolved_ids:
                    result = await session.execute(
                        text("""
                            SELECT user_id, full_name, email
                              FROM core.users
                             WHERE user_id = ANY(CAST(:ids AS UUID[]))
                        """),
                        {"ids": unresolved_ids},
                    )
                    for user_id, full_name, _email in result:
                        if full_name:
                            names_by_id[str(user_id)] = full_name

                if missing_emails:
                    result = await session.execute(
                        text("""
                            SELECT LOWER(email), full_name
                              FROM core.users
                             WHERE LOWER(email) = ANY(:emails)
                        """),
                        {"emails": missing_emails},
                    )
                    for email, full_name in result:
                        if full_name:
                            names_by_email[email] = full_name
        except Exception:  # noqa: BLE001
            logger.warning("login activity: Postgres name lookup failed", exc_info=True)

    for doc in docs:
        doc["user_full_name"] = (
            names_by_id.get(doc.get("user_id") or "")
            or names_by_email.get((doc.get("email") or "").lower())
        )


# ─────────────────────────────────────────────
# GET /security/login-attempts/{user_id}
# ─────────────────────────────────────────────
@router.get("/login-attempts/{user_id}")
async def get_user_login_history(
        user_id: str,
        limit: int = Query(50, ge=1, le=200),
        current_user: dict = Depends(_require_super_admin),
):
    """Full login history for a single user."""
    cursor = db.login_audit_logs.find(
        {"user_id": user_id, "is_test_data": {"$ne": True}}, {"_id": 0}
    ).sort("attempted_at", DESCENDING).limit(limit)
    docs = await cursor.to_list(limit)

    # Get user info
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "full_name": 1, "email": 1})
    user_full_name = user.get("full_name") if user else None

    for doc in docs:
        doc["user_full_name"] = user_full_name

    return {"user_id": user_id, "user_full_name": user_full_name, "items": docs}


# ─────────────────────────────────────────────
# GET /security/my-activity
# ─────────────────────────────────────────────
@router.get("/my-activity")
async def get_my_activity(
        request: Request,
        current_user: dict = Depends(get_current_user),
):
    """
    Own login history for the authenticated user (last 20 events).
    Available to any authenticated user — for the owner security card.

    Also answers "is my account being used somewhere else?" via `concurrent_access`.
    See the notes above _build_concurrent_access for what that can and cannot claim:
    there is no session store, so it reports sign-ins whose token could still be valid,
    never live sessions.
    """
    user_id = current_user.get("id")
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=30)).isoformat()

    cursor = db.login_audit_logs.find(
        {"user_id": user_id, "is_test_data": {"$ne": True}}, {"_id": 0}
    ).sort("attempted_at", DESCENDING).limit(20)
    recent_logins = await cursor.to_list(20)

    failed_30d = await db.login_audit_logs.count_documents({
        "user_id": user_id,
        "status": "failed",
        "attempted_at": {"$gte": cutoff},
        "is_test_data": {"$ne": True},
    })
    suspicious_30d = await db.login_audit_logs.count_documents({
        "user_id": user_id,
        "risk_score": {"$gte": 50},
        "attempted_at": {"$gte": cutoff},
        "is_test_data": {"$ne": True},
    })

    # Unique IPs in 30d
    unique_ips_pipeline = [
        {"$match": {"user_id": user_id, "attempted_at": {"$gte": cutoff}, "is_test_data": {"$ne": True}}},
        {"$group": {"_id": "$ip_address"}},
        {"$count": "total"}
    ]
    unique_ips_result = await db.login_audit_logs.aggregate(unique_ips_pipeline).to_list(1)
    unique_ips = unique_ips_result[0]["total"] if unique_ips_result else 0

    # Last successful login
    last_success = await db.login_audit_logs.find_one(
        {"user_id": user_id, "status": "success", "is_test_data": {"$ne": True}},
        {"_id": 0},
        sort=[("attempted_at", DESCENDING)]
    )

    # Successful logins inside the token lifetime — the window in which a sign-in made
    # elsewhere could still hold a valid session. Read from config so it tracks the
    # actual token policy instead of assuming 24h.
    from config import JWT_EXPIRATION_HOURS
    window_hours = int(JWT_EXPIRATION_HOURS or 24)
    session_cutoff = (now - timedelta(hours=window_hours)).isoformat()
    window_logins = await db.login_audit_logs.find(
        {
            "user_id": user_id,
            "status": "success",
            "attempted_at": {"$gte": session_cutoff},
            "is_test_data": {"$ne": True},
        },
        {"_id": 0},
    ).sort("attempted_at", DESCENDING).to_list(200)

    current_ip = get_real_ip(request)

    # Index into the stored documents rather than .get() chains: these keys were assumed
    # present, and a row missing `geo` or `device_info` raised a KeyError that surfaced as
    # a 500 on the owner dashboard's security card. Restored and legacy rows do not all
    # carry them.
    last_geo = (last_success or {}).get("geo") or {}
    last_device = (last_success or {}).get("device_info") or {}
    last_device_label = None
    if last_success:
        browser, os_name = last_device.get("browser"), last_device.get("os")
        last_device_label = f"{browser} on {os_name}" if browser and os_name else (browser or os_name)

    summary = {
        "last_login_at": last_success.get("attempted_at") if last_success else None,
        "last_login_ip": last_success.get("ip_address") if last_success else None,
        "last_login_country": last_geo.get("country_name") if last_success else None,
        "last_login_city": last_geo.get("city") if last_success else None,
        "last_login_device": last_device_label,
        "failed_attempts_30d": failed_30d,
        "suspicious_events_30d": suspicious_30d,
        "unique_ips_30d": unique_ips,
        "recent_logins": recent_logins,
        "current_ip": current_ip,
        "concurrent_access": _build_concurrent_access(window_logins, current_ip, window_hours),
    }
    return summary


# ─────────────────────────────────────────────
# Concurrent-access detection
# ─────────────────────────────────────────────
#
# WHAT THIS CAN AND CANNOT SAY.
#
# There is no session store. Auth is a stateless Bearer JWT, so nothing on the server
# enumerates "who is logged in right now" — a token, once issued, is valid until it
# expires and no record tracks its use. Any claim of live concurrent sessions would be
# invented.
#
# What IS recorded is every successful login: IP, geo (with lat/long), parsed device,
# and a fingerprint. So the honest question this answers is:
#
#     "Has this account signed in from somewhere else recently enough that the token
#      issued there could still be valid?"
#
# The window is therefore the JWT lifetime, read from config rather than hardcoded, so
# it stays true if the token policy changes. Copy must say "could still be active",
# never "is active now".
#
# GROUPING IS NOT BY device_fingerprint. That value is sha256(ip|user_agent), so it
# changes whenever the IP changes — a phone moving between cell towers produces a new
# fingerprint on every login and would read as a fleet of unknown devices. Grouping is
# by (device shape, city, country) instead, which survives IP churn, and each group
# carries the distinct IPs seen for it.

#: Cruising speed of a commercial airliner. Travel implied faster than this between two
#: consecutive logins cannot be one person moving.
_IMPOSSIBLE_TRAVEL_KMH = 900.0

#: Below this, a "journey" is more likely GeoIP imprecision than movement. City-level
#: GeoIP routinely disagrees by tens of km for the same connection.
_MIN_TRAVEL_DISTANCE_KM = 100.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _parse_ts(value) -> Optional[datetime]:
    """login_audit_logs stores attempted_at as an ISO string; tolerate both forms."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


#: parse_user_agent() yields the literal string "Unknown" for a field it cannot read,
#: not None. Treated as absent, or real rows render as "Unknown on Unknown".
_UNKNOWN_UA_VALUES = {"", "unknown", "none", "null"}


def _clean_ua_field(value) -> Optional[str]:
    if value is None or str(value).strip().lower() in _UNKNOWN_UA_VALUES:
        return None
    return str(value)


def _describe_device(doc: dict) -> str:
    info = doc.get("device_info") or {}
    browser = _clean_ua_field(info.get("browser"))
    os_name = _clean_ua_field(info.get("os"))
    if browser and os_name:
        return f"{browser} on {os_name}"
    return browser or os_name or "Unrecognised client"


def _detect_impossible_travel(logins: list[dict]) -> Optional[dict]:
    """Two logins too far apart, too close together in time, to be one person.

    Unlike the counts above this is positive evidence of a second party rather than a
    prompt to look: one account cannot be in two places at once. Returns the single
    worst pair in the window, or None when no pair qualifies or coordinates are absent.
    """
    points = []
    for doc in logins:
        geo = doc.get("geo") or {}
        lat, lon = geo.get("latitude"), geo.get("longitude")
        ts = _parse_ts(doc.get("attempted_at"))
        if lat is None or lon is None or ts is None:
            continue
        points.append((ts, float(lat), float(lon), doc))
    points.sort(key=lambda p: p[0])

    worst = None
    for (t1, la1, lo1, d1), (t2, la2, lo2, d2) in zip(points, points[1:]):
        hours = (t2 - t1).total_seconds() / 3600.0
        if hours <= 0:
            continue
        km = _haversine_km(la1, lo1, la2, lo2)
        if km < _MIN_TRAVEL_DISTANCE_KM:
            continue
        kmh = km / hours
        if kmh <= _IMPOSSIBLE_TRAVEL_KMH:
            continue
        if worst is None or kmh > worst["implied_speed_kmh"]:
            worst = {
                "from": {
                    "city": (d1.get("geo") or {}).get("city"),
                    "country": (d1.get("geo") or {}).get("country_name"),
                    "ip_address": d1.get("ip_address"),
                    "at": d1.get("attempted_at"),
                },
                "to": {
                    "city": (d2.get("geo") or {}).get("city"),
                    "country": (d2.get("geo") or {}).get("country_name"),
                    "ip_address": d2.get("ip_address"),
                    "at": d2.get("attempted_at"),
                },
                "distance_km": round(km, 1),
                "hours_apart": round(hours, 2),
                "implied_speed_kmh": round(kmh, 1),
            }
    return worst


def _build_concurrent_access(logins: list[dict], current_ip: str, window_hours: int) -> dict:
    """Group this window's successful logins into distinct sign-in sources."""
    sources: dict[tuple, dict] = {}
    for doc in logins:
        geo = doc.get("geo") or {}
        info = doc.get("device_info") or {}
        # Group on the cleaned values so "Unknown"/None/"" do not split one real source
        # into several rows that each look like a separate place the account is in use.
        key = (_clean_ua_field(info.get("browser")), _clean_ua_field(info.get("os")),
               _clean_ua_field(info.get("device_type")),
               _clean_ua_field(geo.get("city")), _clean_ua_field(geo.get("country_code")))
        ts = doc.get("attempted_at")
        entry = sources.get(key)
        if entry is None:
            entry = sources[key] = {
                "device": _describe_device(doc),
                "device_type": _clean_ua_field(info.get("device_type")),
                "city": _clean_ua_field(geo.get("city")),
                "country": _clean_ua_field(geo.get("country_name")),
                "ip_addresses": [],
                "last_seen_at": ts,
                "login_count": 0,
            }
        entry["login_count"] += 1
        ip = doc.get("ip_address")
        if ip and ip not in entry["ip_addresses"]:
            entry["ip_addresses"].append(ip)
        if str(ts or "") > str(entry["last_seen_at"] or ""):
            entry["last_seen_at"] = ts

    ordered = sorted(sources.values(), key=lambda e: str(e["last_seen_at"] or ""), reverse=True)
    for entry in ordered:
        # "This one is you" is decided by the IP the request arrived on. It is a hint for
        # the reader, not an identity claim: two people behind one office NAT share an IP.
        entry["is_current_ip"] = bool(current_ip) and current_ip in entry["ip_addresses"]

    return {
        "window_hours": window_hours,
        # No login records at all is NOT "no other sessions". An empty history renders as
        # a reassuring all-clear while meaning nothing was ever recorded — the same
        # missing-vs-zero error this project has hit repeatedly. East Gate's
        # login_audit_logs is empty right now, so this is the live state, not a hypothetical.
        "history_available": bool(logins),
        "source_count": len(ordered),
        "other_source_count": max(0, len([e for e in ordered if not e["is_current_ip"]])),
        "sources": ordered,
        "impossible_travel": _detect_impossible_travel(logins),
    }


# ─────────────────────────────────────────────
# POST /security/sign-out-everywhere
# ─────────────────────────────────────────────
@router.post("/sign-out-everywhere")
async def sign_out_everywhere(
        request: Request,
        current_user: dict = Depends(get_current_user),
):
    """End every other session for the calling user; keep this one alive.

    Auth is a stateless Bearer JWT, so a token cannot be withdrawn once issued — it is
    valid until `exp` no matter what happens to the password. That is the gap this
    closes: seeing your account signed in somewhere you do not recognise is useless if
    the only remedy is a password change that leaves the existing token working.

    The revocation instant is written to the user record and enforced in
    get_current_user(); the caller's own `jti` is recorded as the single exception, so
    the device that pressed the button stays signed in. Everything else 401s on its
    next request.

    The write goes to whichever store get_current_user() READS for this user, and both
    when both exist. Writing only to Mongo would be a no-op for a Postgres-resident
    user — 119 of 119 active East Gate users are exactly that — and the endpoint would
    report success while revoking nothing.
    """
    user_id = current_user.get("id")
    tenant_id = current_user.get("tenant_id")

    # The caller's own token, read back from the Authorization header. current_user is
    # the resolved user record and does not carry the claims.
    keep_jti = None
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        try:
            from utils.auth import decode_token
            keep_jti = decode_token(auth_header.split(" ", 1)[1]).get("jti")
        except Exception:  # noqa: BLE001
            keep_jti = None

    now_iso = datetime.now(timezone.utc).isoformat()
    revoked_in = []

    if tenant_id:
        try:
            from db_postgres.repos.identity_repo import revoke_other_sessions
            if await revoke_other_sessions(user_id, str(tenant_id), keep_jti):
                revoked_in.append("postgres")
        except Exception as exc:  # noqa: BLE001
            logger.error("sign-out-everywhere: Postgres revocation failed for %s: %s", user_id, exc)

    mongo_result = await db.users.update_one(
        {"id": user_id},
        {"$set": {"sessions_invalidated_at": now_iso, "session_keep_jti": keep_jti}},
    )
    if mongo_result.matched_count:
        revoked_in.append("mongo")

    if not revoked_in:
        # "No exception" is not "revoked something". Returning 200 here would tell the
        # user their other sessions are dead while every one of them still works —
        # the most dangerous possible lie for this particular button.
        logger.error("sign-out-everywhere: no store accepted the revocation for user %s", user_id)
        raise HTTPException(
            status_code=500,
            detail="Could not sign out your other sessions. Please change your password instead.",
        )

    logger.info("sign-out-everywhere: user %s revoked in %s (kept jti=%s)",
                user_id, "+".join(revoked_in), bool(keep_jti))
    return {
        "ok": True,
        "revoked_at": now_iso,
        # False means this device will also be signed out on its next request: the
        # token carried no jti to spare. Say so rather than letting it surprise them.
        "current_session_kept": bool(keep_jti),
        "stores": revoked_in,
    }

# ─────────────────────────────────────────────
# GET /security/suspicious-events
# ─────────────────────────────────────────────
@router.get("/suspicious-events")
async def get_suspicious_events(
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=100),
        current_user: dict = Depends(_require_super_admin),
):
    """Return login events with risk_score >= 50."""
    query = {"risk_score": {"$gte": 50}, "is_test_data": {"$ne": True}}
    skip = (page - 1) * per_page
    total = await db.login_audit_logs.count_documents(query)

    cursor = db.login_audit_logs.find(query, {"_id": 0}).sort(
        "attempted_at", DESCENDING
    ).skip(skip).limit(per_page)
    docs = await cursor.to_list(per_page)

    user_ids = [d["user_id"] for d in docs if d.get("user_id")]
    users_map = {}
    if user_ids:
        users_cursor = db.users.find({"id": {"$in": user_ids}}, {"_id": 0, "id": 1, "full_name": 1})
        async for u in users_cursor:
            users_map[u["id"]] = u.get("full_name")

    for doc in docs:
        doc["user_full_name"] = users_map.get(doc.get("user_id"))

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "items": docs,
    }


@router.delete("/test-login-audits")
async def cleanup_test_login_audits(
        current_user: dict = Depends(_require_super_admin),
):
    """Delete login-audit rows explicitly marked as test data."""
    result = await db.login_audit_logs.delete_many({"is_test_data": True})
    return {"deleted": result.deleted_count}


# ─────────────────────────────────────────────
# GET /security/ip-intelligence
# ─────────────────────────────────────────────
@router.get("/ip-intelligence")
async def get_ip_intelligence(
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=100),
        current_user: dict = Depends(_require_super_admin),
):
    """Aggregate per-IP statistics for the IP Intelligence tab."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    pipeline = [
        {"$match": {"attempted_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$ip_address",
            "last_seen": {"$max": "$attempted_at"},
            "unique_users": {"$addToSet": "$user_id"},
            "success_count": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
            "failed_count": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
            "country_code": {"$first": "$geo.country_code"},
            "country_name": {"$first": "$geo.country_name"},
            "city": {"$first": "$geo.city"},
            "isp": {"$first": "$geo.isp"},
        }},
        {"$addFields": {"unique_user_count": {"$size": "$unique_users"}}},
        {"$project": {"unique_users": 0}},
        {"$sort": {"failed_count": -1, "last_seen": -1}},
    ]

    all_results = await db.login_audit_logs.aggregate(pipeline).to_list(10000)
    total = len(all_results)
    skip = (page - 1) * per_page
    page_results = all_results[skip:skip + per_page]

    # Look up manually flagged IPs
    flagged = await db.flagged_ips.find({}, {"_id": 0}).to_list(1000)
    flagged_map = {f["ip_address"]: f for f in flagged}

    items = []
    for r in page_results:
        ip = r["_id"]
        flag_info = flagged_map.get(ip)
        items.append({
            "ip_address": ip,
            "last_seen": r["last_seen"],
            "unique_user_count": r["unique_user_count"],
            "success_count": r["success_count"],
            "failed_count": r["failed_count"],
            "country_code": r.get("country_code"),
            "country_name": r.get("country_name"),
            "city": r.get("city"),
            "isp": r.get("isp"),
            "is_flagged": bool(flag_info),
            "flag_reason": flag_info.get("reason") if flag_info else None,
            "flag_action": flag_info.get("action") if flag_info else None,
        })

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "items": items,
    }


# ─────────────────────────────────────────────
# POST /security/flag-ip
# ─────────────────────────────────────────────
@router.post("/flag-ip")
async def flag_ip(
        body: FlagIPRequest,
        current_user: dict = Depends(_require_super_admin),
):
    """Manually flag an IP as suspicious or blocked."""
    doc = {
        "id": str(uuid.uuid4()),
        "ip_address": body.ip_address,
        "reason": body.reason,
        "action": body.action,
        "flagged_by": current_user.get("id"),
        "flagged_at": datetime.now(timezone.utc).isoformat(),
    }
    doc_set = {k: v for k, v in doc.items() if k != "ip_address"}
    await db.flagged_ips.update_one(
        {"ip_address": body.ip_address},
        {"$set": doc_set},
        upsert=True,
    )
    return {"success": True, "ip_address": body.ip_address, "action": body.action}
