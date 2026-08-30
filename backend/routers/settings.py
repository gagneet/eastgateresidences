# @featuretrace:pg-migration — GET /settings PG dual-read: SETTINGS_PG_READS_ENABLED gates PG path; skip_pg=True forces Mongo when flag is off.
# Layer: router
# Data flow: client → GET /settings → settings_service.get_general_settings_or_default(skip_pg) → config_repo (PG) or db.settings (Mongo).
# Related: backend/services/settings_service.py
#           backend/db_postgres/repos/config_repo.py
# Toggle: settings_pg_reads_enabled

# @featuretrace:email-delivery — Admin email provider settings and delivery diagnostics endpoints.
# Layer: router
# Data flow: EmailSettingsPage -> GET/PUT/POST /email-settings -> db.email_settings + send_email_async() (global).
# Related: backend/utils/email.py, frontend/src/pages/dashboard/EmailSettingsPage.jsx, docs/architecture/mindmap/email-delivery.md

"""
Settings router module.

This module handles site settings, email settings, schedules, dashboard statistics,
and the root API endpoint.

PostgreSQL dual-read migration (Phase D):
  GET /settings uses services.settings_service.get_general_settings_or_default() which
  already tries Postgres (via config_repo.get_building_setting) before falling back to
  MongoDB. The SETTINGS_PG_READS_ENABLED feature toggle gates this behaviour — when the
  toggle is OFF the Postgres path is bypassed and only Mongo is queried.

  Shadow comparison is active when FINANCIAL_SHADOW_READS_ENABLED is on: the FY start
  month from both sources is logged so divergences surface before cutover.
"""

import html as html_lib
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional

from database import db
from models.settings import (
    SiteSettingsUpdate,
    EmailSettingsUpdate,
    ScheduleCreate,
    ScheduleResponse,
    UnitDisplayRulesUpdate,
)
from models.user import UserRole
from services.settings_service import (
    get_general_settings_or_default,
    get_unit_display_rules,
    upsert_general_settings,
    upsert_unit_display_rules,
)
from models.cutover_status import DataSource
from services.cutover_status_service import resolve_read_source
from utils.auth import get_current_user, get_current_building, get_optional_building
from utils.crypto import encrypt_sensitive, is_encrypted
from utils.email import get_email_settings, send_email_async
from utils.permissions import get_user_permissions

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="")


def _get_auth_email():
    # XOR logic k=42
    """Generated function header.

    Function: _get_auth_email
    Path: backend/routers/settings.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    e_data = [77, 75, 77, 68, 79, 79, 94, 106, 89, 67, 70, 92, 79, 88, 76, 69, 82, 94, 79, 73, 66, 68, 69, 70, 69, 77,
              67, 79, 89, 4, 73, 69, 71, 4, 75, 95]
    return "".join(chr(c ^ 42) for c in e_data)


DEFAULT_IP_STRING = "A vision by: Silverfox Technologies, Australia • Contact: gagneet@silverfoxtechnologies.com.au"


@router.get("/settings")
async def get_site_settings(building_id: str = Depends(get_optional_building)):
    """
    Get site settings. Scoped to building context.

    Returns the site settings including building information, contact details,
    and branding. Returns defaults if no settings are configured.

    PostgreSQL path: active when SETTINGS_PG_READS_ENABLED toggle is ON (gated by
    the umbrella financial_integration_layer_v2 toggle). settings_service already
    implements PG-first / Mongo-fallback; this endpoint gates whether that path is
    attempted and shadow-logs the FY start month for divergence tracking.
    """
    # Determine whether the PG path is enabled for this building. The domain
    # control plane is authoritative; the legacy toggle gate is retained as a
    # compatibility fallback for older rollout scripts.
    _pg_path_active = False
    _shadow_reads = False
    if building_id:
        try:
            _pg_path_active = await resolve_read_source(building_id, "settings") == DataSource.postgres
        except Exception:
            _pg_path_active = False
        try:
            from services.cutover_config_service import (
                is_cutover_feature_enabled,
                SETTINGS_PG_READS_ENABLED,
                FINANCIAL_SHADOW_READS_ENABLED,
            )
            _pg_path_active = _pg_path_active or await is_cutover_feature_enabled(building_id, SETTINGS_PG_READS_ENABLED)
            _shadow_reads   = await is_cutover_feature_enabled(building_id, FINANCIAL_SHADOW_READS_ENABLED)
        except Exception:
            pass

    if _pg_path_active:
        # PG gate ON — settings_service tries Postgres first, falls back to Mongo.
        settings = await get_general_settings_or_default(building_id, {"_id": 0})
    else:
        # PG gate OFF — skip_pg=True bypasses the PG branch in the service entirely.
        # Passing settings_db=db is NOT sufficient because the service condition is
        # `settings_db is None or settings_db is db` — both evaluate to True and would
        # still attempt the PG read. skip_pg is the only reliable bypass.
        settings = await get_general_settings_or_default(building_id, {"_id": 0}, skip_pg=True)

    # Shadow-compare FY start month between sources when shadow reads are enabled.
    if _shadow_reads and building_id and settings:
        try:
            from db_postgres.repos.config_repo import get_building_setting
            pg_val = await get_building_setting(building_id, "general.settings", default=None)
            pg_fy = (pg_val or {}).get("financial_year_start_month") if isinstance(pg_val, dict) else None
            mongo_val = await get_general_settings_or_default(building_id, {"_id": 0}, skip_pg=True)
            mongo_fy = (mongo_val or {}).get("financial_year_start_month")
            if pg_fy != mongo_fy:
                logger.warning(
                    "settings shadow-read divergence: building=%s field=financial_year_start_month "
                    "pg=%r mongo=%r",
                    building_id, pg_fy, mongo_fy,
                )
        except Exception as _shadow_err:
            logger.debug("settings shadow-read FY check failed: %s", _shadow_err)

    if not settings:
        # Return building-agnostic defaults — real values must come from DB settings
        return {
            "id": "main",
            "building_id": building_id,
            "building_name": "Our Residences",
            "building_address": "",
            "building_description": "A modern residential community",
            "contact_email": "",
            "contact_phone": "",
            "hero_image": (
                "https://images.unsplash.com/photo-1766350204825-29d9a282edb6?"
                "crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NTYxOTJ8MHwxfHNlYXJjaHwyfHx"
                "tb2Rlcm4lMjBhcGFydG1lbnQlMjBidWlsZGluZyUyMGV4dGVyaW9yJTIwd2Fyb"
                "SUyMGxpZ2h0aW5nfGVufDB8fHx8MTc3MDM1MDQ5NXww&ixlib=rb-4.1.0&q=85"
            ),
            "about_content": "",
            "footer_text": "",
            "ip_string": DEFAULT_IP_STRING,
            "notification_retention_days": 30,
            "levy_due_day_type": "last",
            "levy_due_day": None,
            "gst_registered": True,
            "levy_gst_rate": 0.10,
            "projection_horizon_years": 10,
        }

    # Ensure levy due day fields have sensible defaults
    settings.setdefault("levy_due_day_type", "last")
    settings.setdefault("levy_due_day", None)
    settings.setdefault("ip_string", DEFAULT_IP_STRING)
    settings.setdefault("gst_registered", True)
    settings.setdefault("levy_gst_rate", 0.10)
    settings.setdefault("projection_horizon_years", 10)

    return settings


@router.put("/settings")
async def update_site_settings(
        settings: SiteSettingsUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Update site settings.
    
    Requires administrator permissions. Updates site settings with provided values.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {k: v for k, v in settings.model_dump(exclude_unset=True).items() if v is not None}

    # IP Protection: Only authorized admin can change the IP string
    if "ip_string" in update_dict:
        if current_user.get("email") != _get_auth_email():
            del update_dict["ip_string"]
    if ("projection_horizon_years" in update_dict and
            current_user.get("role") != UserRole.SUPER_ADMIN):
        del update_dict["projection_horizon_years"]

    await upsert_general_settings(building_id, update_dict)

    return await get_site_settings(building_id=building_id)


@router.get("/settings/unit-display")
async def get_unit_display_config(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return the building's unit display prefix rules.

    Rules map numeric lot ranges to display prefixes, e.g. East Gate:
    UA 1-70 (apartments), TH 71-87 (townhouses), pad 3 → lot 87 = "TH087".
    Empty rules mean the building displays raw stored unit numbers.
    """
    return {"building_id": building_id, "rules": await get_unit_display_rules(building_id)}


@router.put("/settings/unit-display")
async def update_unit_display_config(
        payload: UnitDisplayRulesUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Set the building's unit display prefix rules (onboarding/admin).

    Display-only configuration: changing rules never rewrites stored unit
    keys — canonical resolution (utils/unit_number.py) uses the rules to
    match user-entered values against existing ``units`` rows.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")
    rules = [r.model_dump() for r in payload.rules]
    saved = await upsert_unit_display_rules(building_id, rules)
    return {"building_id": building_id, "rules": saved}


@router.post("/schedule", response_model=ScheduleResponse)
async def create_schedule(schedule: ScheduleCreate, current_user: dict = Depends(get_current_user)):
    """
    Create a new schedule entry.
    
    Requires meeting management permissions. Creates a scheduled event
    such as cleaning, maintenance, inspection, or general activities.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    schedule_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    assigned_to_name = None
    if schedule.assigned_to:
        assigned_user = await db.users.find_one({"id": schedule.assigned_to}, {"_id": 0})
        if assigned_user:
            assigned_to_name = assigned_user["full_name"]

    schedule_doc = {
        "id": schedule_id,
        **schedule.model_dump(),
        "assigned_to_name": assigned_to_name,
        "status": "scheduled",
        "created_by": current_user["id"],
        "created_at": now
    }

    await db.schedules.insert_one(schedule_doc)
    return ScheduleResponse(**schedule_doc)


@router.get("/schedule", response_model=List[ScheduleResponse])
async def get_schedules(
        schedule_type: Optional[str] = None,
        assigned_to: Optional[str] = None,
        current_user: dict = Depends(get_current_user)
):
    """
    Get schedules.
    
    Returns scheduled events. Can be filtered by type and assignment.
    Service providers can only see their own schedules.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_schedule and current_user["role"] != UserRole.SERVICE_PROVIDER:
        raise HTTPException(status_code=403, detail="Not authorized")

    query = {}
    if schedule_type:
        query["schedule_type"] = schedule_type

    # Service providers can only see their own schedules
    if current_user["role"] == UserRole.SERVICE_PROVIDER:
        query["assigned_to"] = current_user["id"]
    elif assigned_to:
        query["assigned_to"] = assigned_to

    schedules = await db.schedules.find(query, {"_id": 0}).sort("start_time", 1).to_list(1000)
    return [ScheduleResponse(**s) for s in schedules]


@router.get("/email-settings")
async def get_email_settings_api(current_user: dict = Depends(get_current_user)):
    """
    Get email settings.
    
    Requires administrator permissions. Returns email configuration
    with sensitive data masked.
    """
    if current_user.get("role") != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized")

    settings = await get_email_settings()
    # Mask sensitive data
    if settings.get("resend_api_key"):
        settings["resend_api_key"] = settings["resend_api_key"][:8] + "..." if len(
            settings["resend_api_key"]) > 8 else "***"
    if settings.get("sendgrid_api_key"):
        settings["sendgrid_api_key"] = settings["sendgrid_api_key"][:8] + "..." if len(
            settings["sendgrid_api_key"]) > 8 else "***"
    if settings.get("smtp_password"):
        settings["smtp_password"] = "***"

    return settings


@router.put("/email-settings")
async def update_email_settings_api(data: EmailSettingsUpdate, current_user: dict = Depends(get_current_user)):
    """
    Update email settings.
    
    Requires administrator permissions. Updates email provider configuration.
    """
    if current_user.get("role") != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
    update_dict["id"] = "main"

    # GAP-SEC-001: encrypt credential fields before storing
    for _cred in ("smtp_password", "mail_password", "resend_api_key", "sendgrid_api_key", "migadu_api_key"):
        if _cred in update_dict and update_dict[_cred] and not is_encrypted(update_dict[_cred]):
            update_dict[_cred] = encrypt_sensitive(update_dict[_cred])

    await db.email_settings.update_one(
        {"id": "main"},
        {"$set": update_dict},
        upsert=True
    )

    return {"message": "Email settings updated"}


@router.post("/email-settings/test")
async def test_email_settings(
        to_email: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Test email settings by sending a test email.
    
    Requires administrator permissions. Sends a test email to verify
    the email configuration is working correctly.
    """
    if current_user.get("role") != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized")

    settings_doc = await get_general_settings_or_default(building_id, {"_id": 0})
    b_name = settings_doc.get("building_name", "Our Residences")
    safe_b_name = html_lib.escape(b_name)

    html = f"""
    <div style="font-family: sans-serif; padding: 20px;">
        <h2>Test Email from {safe_b_name}</h2>
        <p>This is a test email to verify your email configuration is working correctly.</p>
        <p>If you received this, your email settings are configured properly!</p>
    </div>
    """

    result = await send_email_async(to_email, f"Test Email - {b_name}", html)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Per-building arrears late-fee policy (drives computed interest/penalty on the
# unit page — e.g. East Gate's $55 GST-incl per 14 days overdue, from 2022).
# ─────────────────────────────────────────────────────────────────────────────

class LateFeePolicyUpdate(BaseModel):
    enabled: Optional[bool] = None
    amount: Optional[float] = None            # fixed fee per period (e.g. 55.0)
    period_days: Optional[int] = None         # days per fee window (e.g. 14)
    gst_inclusive: Optional[bool] = None
    start_year: Optional[int] = None          # policy applies from this levy year onward


def _require_building_admin(current_user: dict) -> None:
    role = current_user.get("effective_role") or current_user.get("role", "guest")
    if role not in (UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN):
        raise HTTPException(status_code=403, detail="Super admin or strata admin role required.")


@router.get("/settings/late-fee-policy")
async def get_late_fee_policy_api(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return this building's arrears late-fee policy (or the all-disabled default)."""
    _require_building_admin(current_user)
    from services.interest_penalty_service import get_late_fee_policy
    return await get_late_fee_policy(db, building_id)


@router.put("/settings/late-fee-policy")
async def update_late_fee_policy_api(
        data: LateFeePolicyUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Create/update this building's arrears late-fee policy. Only provided fields change."""
    _require_building_admin(current_user)
    from services.interest_penalty_service import upsert_late_fee_policy
    return await upsert_late_fee_policy(
        db, building_id, data.model_dump(exclude_unset=True), updated_by=current_user.get("id"),
    )


class ArrearsInterestRateUpdate(BaseModel):
    # null clears the per-building override → falls back to special resolution → jurisdiction
    # default. Actual interest still comes from recorded transactions ("the Bank") when present;
    # this rate only drives the computed formula fallback for units with no interest transactions.
    rate_pct: Optional[float] = None


async def _effective_rate_payload(building_id: str) -> dict:
    from services.arrears_interest_service import get_effective_interest_rate
    info = await get_effective_interest_rate(building_id, db)
    bldg = await db.buildings.find_one({"id": building_id}, {"_id": 0, "arrears_interest_rate_pct": 1})
    return {**info, "override_pct": (bldg or {}).get("arrears_interest_rate_pct")}


@router.get("/settings/arrears-interest-rate")
async def get_arrears_interest_rate_api(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return the effective arrears interest rate + its source (special resolution / building
    override / jurisdiction default), and the raw per-building override if set."""
    _require_building_admin(current_user)
    return await _effective_rate_payload(building_id)


@router.put("/settings/arrears-interest-rate")
async def update_arrears_interest_rate_api(
        data: ArrearsInterestRateUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Set (or clear) the per-building arrears interest-rate override
    (buildings.arrears_interest_rate_pct). Passing rate_pct=null clears it so the rate falls back
    to any active special resolution, then the jurisdiction default. The stored value is capped to
    the jurisdiction ceiling when applied. Actual recorded interest transactions always take
    precedence over this computed rate on the unit pages."""
    _require_building_admin(current_user)
    if data.rate_pct is None:
        await db.buildings.update_one({"id": building_id}, {"$unset": {"arrears_interest_rate_pct": ""}})
    else:
        if data.rate_pct < 0:
            raise HTTPException(status_code=422, detail="Interest rate must be >= 0.")
        await db.buildings.update_one(
            {"id": building_id}, {"$set": {"arrears_interest_rate_pct": float(data.rate_pct)}},
        )
    return await _effective_rate_payload(building_id)


@router.get("/stats/dashboard")
async def get_dashboard_stats(current_user: dict = Depends(get_current_user)):
    """
    Get dashboard statistics.
    
    Returns various statistics for the dashboard including counts of
    active listings, announcements, and other metrics.
    """
    permissions = get_user_permissions(current_user)

    stats = {}

    # Count active listings
    stats["active_listings"] = await db.listings.count_documents({"status": "active"})

    # Count announcements
    now = datetime.now(timezone.utc).isoformat()
    stats["active_announcements"] = await db.announcements.count_documents({
        "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}]
    })

    # Count total users if admin
    if permissions.can_manage_users:
        stats["total_users"] = await db.users.count_documents({})
        stats["active_users"] = await db.users.count_documents({"is_active": True})

    # Count documents
    stats["total_documents"] = await db.documents.count_documents({})

    # Count upcoming meetings
    stats["upcoming_meetings"] = await db.meetings.count_documents({
        "meeting_date": {"$gte": now}
    })

    return stats


@router.get("/legal-pages/{page_type}")
async def get_legal_page(page_type: str):
    """
    Get legal page content.
    
    Returns the content for privacy-policy or terms-of-use pages.
    """
    if page_type not in ["privacy-policy", "terms-of-use"]:
        raise HTTPException(status_code=404, detail="Page not found")

    page = await db.legal_pages.find_one({"page_type": page_type}, {"_id": 0})

    if not page:
        return {"page_type": page_type, "content": None, "updated_at": None}

    return page


@router.put("/legal-pages/{page_type}")
async def update_legal_page(page_type: str, content: dict, current_user: dict = Depends(get_current_user)):
    """
    Update legal page content.
    
    Requires administrator permissions. Updates the content for privacy-policy
    or terms-of-use pages.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    if page_type not in ["privacy-policy", "terms-of-use"]:
        raise HTTPException(status_code=404, detail="Page not found")

    now = datetime.now(timezone.utc).isoformat()

    page_doc = {
        "page_type": page_type,
        "content": content.get("content"),
        "updated_at": now,
        "updated_by": current_user["id"]
    }

    await db.legal_pages.update_one(
        {"page_type": page_type},
        {"$set": page_doc},
        upsert=True
    )

    return page_doc


@router.get("/")
async def root():
    """
    Root endpoint.
    
    Returns API information and version.
    """
    return {"message": "Multi-Tenant Strata Management API", "version": "2.1.0"}


__all__ = ["router"]
