"""
GAP-FIN-012: Levy Reminder Cadence — Admin-configurable reminder settings + trigger.

Endpoints:
  GET    /levy-reminders/settings          — get building reminder config
  PUT    /levy-reminders/settings          — update building reminder config
  POST   /levy-reminders/trigger           — manually trigger a reminder run (dry-run or live)
  GET    /levy-reminders/log               — list recent reminder dispatch events

The actual dispatch runs via the existing cron_levy_reminders.py cron job. This
router provides the admin UI surface.

Settings are stored PostgreSQL-first in `core.building_settings` under
`finance.levy_reminders`, with Mongo mirrors/fallbacks in
`levy_reminder_settings` and `settings(type='levy_reminder_settings')` for
compatibility during the cutover.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict

from services.levy_reminder_settings_service import (
    DEFAULT_LEVY_REMINDER_SETTINGS,
    get_levy_reminder_settings,
    upsert_levy_reminder_settings,
)
from utils.auth import get_current_user, get_current_building
from utils.route_guards import require_manager_surface
from utils.helpers import create_audit_log

logger = logging.getLogger(__name__)
# Function scoping (2026-08-28). Router-level, so every route in this file is
# covered including the reads — Privacy Act APP 6 limits USE to the purpose of
# collection, and looking is a use. This narrows nobody until an agency sets
# core.management_entities.function_scoping_enabled; see
# docs/architecture/strata_management_staff_access_model.md.
router = APIRouter(
    prefix="/levy-reminders", tags=["Levy Reminders"],
    dependencies=[Depends(require_manager_surface("levies"))],
)


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/routers/levy_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/routers/levy_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    return db


def _require_manager(user: dict) -> dict:
    """Generated function header.

    Function: _require_manager
    Path: backend/routers/levy_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed = {"super_admin", "strata_manager", "strata_admin", "ec_member", "admin_staff"}
    effective_role = user.get("effective_role") or user.get("role", "guest")
    if effective_role not in allowed:
        raise HTTPException(403, "Strata manager or EC access required.")
    return user


# ── Models ────────────────────────────────────────────────────────────────────

class ReminderChannel(BaseModel):
    email: bool = True
    in_app: bool = True
    sms: bool = False  # SMS not wired yet; flag for future channel


class LevyReminderSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    building_id: Optional[str] = None  # overridden server-side
    enabled: bool = True
    # Days before due date to send "upcoming" reminders
    pre_due_days: List[int] = Field(
        default=[14, 7, 3],
        description="Send reminders this many days BEFORE the due date",
    )
    # Days after due date to send "overdue" reminders
    overdue_days: List[int] = Field(
        default=[1, 7, 14, 30],
        description="Send reminders this many days AFTER the due date",
    )
    # Minimum overdue amount (cents) before sending overdue notices
    overdue_threshold_cents: int = Field(
        default=100,
        ge=0,
        description="Skip overdue reminder if balance < this (cents). Default $1.00.",
    )
    channels: ReminderChannel = Field(default_factory=ReminderChannel)
    # Stop sending after this many escalation tiers (prevents reminder spam)
    max_escalation_tier: int = Field(default=4, ge=1, le=10)
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None


class LevyReminderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: Optional[bool] = None
    pre_due_days: Optional[List[int]] = None
    overdue_days: Optional[List[int]] = None
    overdue_threshold_cents: Optional[int] = None
    channels: Optional[ReminderChannel] = None
    max_escalation_tier: Optional[int] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/settings", response_model=LevyReminderSettings)
async def get_reminder_settings(
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Return the levy reminder cadence config for this building.
    Falls back to sensible defaults if not yet configured.
    """
    _require_manager(current_user)
    doc = await get_levy_reminder_settings(building_id, settings_db=_get_db())
    if not doc:
        return {**DEFAULT_LEVY_REMINDER_SETTINGS, "building_id": building_id, "is_default": True}

    doc["is_default"] = False
    return doc


@router.put("/settings")
async def update_reminder_settings(
        payload: LevyReminderSettingsUpdate,
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Update levy reminder cadence config for this building (upsert)."""
    _require_manager(current_user)

    update_fields = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update_fields:
        raise HTTPException(400, "No fields to update.")

    update_fields["updated_at"] = _now_iso()
    update_fields["updated_by"] = current_user.get("id", "unknown")

    await upsert_levy_reminder_settings(
        building_id,
        update_fields,
        updated_by=current_user.get("id", "unknown"),
        settings_db=_get_db(),
    )

    asyncio.create_task(create_audit_log(
        action="levy_reminder_settings_updated",
        resource_type="levy_reminder_settings",
        resource_id=building_id,
        user_id=current_user.get("id", "unknown"),
        user_name=current_user.get("full_name", ""),
        details={"updated_fields": list(update_fields.keys())},
        building_id=building_id,
    ))

    return {"message": "Reminder settings updated.", "building_id": building_id}


@router.post("/trigger")
async def trigger_reminder_run(
        dry_run: bool = Query(default=True, description="True = report only, no emails sent"),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """Manually trigger a levy reminder run for this building.

    In dry_run mode (default) the endpoint returns which units would receive reminders
    without sending any emails or notifications. Set dry_run=false to dispatch live.
    """
    _require_manager(current_user)
    db = _get_db()

    # Load settings (fallback to defaults)
    settings = await get_levy_reminder_settings(building_id, settings_db=db) or DEFAULT_LEVY_REMINDER_SETTINGS

    if not settings.get("enabled", True) and not dry_run:
        return {"message": "Reminders are disabled for this building. Enable in settings first.", "dispatched": 0}

    # Find units with outstanding levies
    year = str(datetime.now(timezone.utc).year)
    threshold_cents = settings.get("overdue_threshold_cents", 100)

    ledger_docs = await db.unit_levy_ledger.find(
        {"building_id": building_id, "year": {"$regex": f"^{year}"}},
        {"unit_number": 1, "admin_balance": 1, "sinking_balance": 1},
    ).to_list(500)

    overdue_units = []
    for doc in ledger_docs:
        balance = round(
            (doc.get("admin_balance") or 0) + (doc.get("sinking_balance") or 0), 2
        )
        balance_cents = int(balance * 100)
        if balance_cents >= threshold_cents:
            overdue_units.append({
                "unit_number": doc.get("unit_number"),
                "outstanding_cents": balance_cents,
                "outstanding_aud": round(balance_cents / 100, 2),
            })

    if not dry_run and overdue_units:
        # Fire-and-forget: run the existing cron logic
        try:
            from cron.cron_levy_reminders import run_levy_reminders
            asyncio.create_task(run_levy_reminders(building_id=building_id, tier=1))
        except Exception as exc:
            logger.warning("Could not trigger reminder cron: %s", exc)

    asyncio.create_task(create_audit_log(
        action="levy_reminder_triggered",
        resource_type="levy_reminders",
        resource_id=building_id,
        user_id=current_user.get("id", "unknown"),
        user_name=current_user.get("full_name", ""),
        details={
            "dry_run": dry_run,
            "overdue_unit_count": len(overdue_units),
            "total_overdue_cents": sum(u["outstanding_cents"] for u in overdue_units),
        },
        building_id=building_id,
    ))

    return {
        "dry_run": dry_run,
        "overdue_unit_count": len(overdue_units),
        "overdue_units": overdue_units,
        "message": (
            f"Dry run complete — {len(overdue_units)} unit(s) would receive reminders."
            if dry_run
            else f"Reminder run dispatched for {len(overdue_units)} unit(s)."
        ),
    }


@router.get("/log")
async def list_reminder_log(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        building_id: str = Depends(get_current_building),
        current_user: dict = Depends(get_current_user),
):
    """List recent levy reminder dispatch events from the audit log."""
    _require_manager(current_user)
    db = _get_db()

    query = {
        "building_id": building_id,
        "action": {"$in": ["levy_reminder_triggered", "levy_reminder_sent"]},
    }
    total = await db.audit_logs.count_documents(query)
    skip = (page - 1) * page_size
    cursor = db.audit_logs.find(query).sort("created_at", -1).skip(skip).limit(page_size)

    logs = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        logs.append(doc)

    return {"data": logs, "total": total, "page": page, "page_size": page_size}
