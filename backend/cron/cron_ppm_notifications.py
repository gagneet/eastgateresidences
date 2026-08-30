"""
Daily PPM Notification Cron Task.

Schedule: 07:00 AEST daily (see docs/deployment/eastgate-ppm-notifications.service)

Logic:
  1. Query all active buildings from the database (multi-tenant)
  2. For each building, query PPM items and compute days_until_due
  3. Send manager notifications at thresholds: 30, 14, 7, 0 days (and daily if overdue)
  4. Send owner/resident notifications only for compliance items within 7 days
  5. Log all sends to audit_logs and update notification_log to avoid duplicates
"""

import sys
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import asyncio
import logging

# Allow running as a standalone script from the repo root
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from database import db
from utils.email import send_email_async
from utils.helpers import create_audit_log, get_current_utc_iso

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# BUILDING_ID is no longer hardcoded — loop over all active buildings at runtime.
DASHBOARD_LINK = "/maintenance?tab=ppm"

# Thresholds for manager notifications (days until due)
MANAGER_THRESHOLDS = {30, 14, 7, 0}

# Manager roles that receive notifications
MANAGER_ROLES = {"super_admin", "strata_admin", "strata_manager", "ec_member"}


# ─── Email helpers ────────────────────────────────────────────────────────────

def _manager_email_html(item: dict, days_until_due: int) -> str:
    """Build HTML email body for a manager PPM notification."""
    section = item.get("section", "")
    description = item.get("description", "")
    vendor = item.get("vendor_name") or "Not assigned"
    vendor_contact = item.get("vendor_contact") or "N/A"
    frequency = item.get("frequency", "")
    standard = item.get("standard") or "N/A"
    last_completed = item.get("last_completed_date") or "Never"
    is_compliance = item.get("is_compliance", False)
    status = item.get("status", "")

    if days_until_due < 0:
        urgency_html = f'<p style="color:red;font-weight:bold;">⚠ OVERDUE by {abs(days_until_due)} day(s)</p>'
    elif days_until_due == 0:
        urgency_html = '<p style="color:orange;font-weight:bold;">⚠ DUE TODAY</p>'
    else:
        urgency_html = f'<p style="color:#666;">Due in {days_until_due} day(s)</p>'

    compliance_banner = ""
    if is_compliance:
        compliance_banner = f"""
        <div style="background:#fee2e2;border:1px solid #f87171;padding:12px;margin:12px 0;border-radius:4px;">
            <strong style="color:#dc2626;">🔴 COMPLIANCE ITEM</strong>
            <p style="margin:4px 0;color:#991b1b;">
                This item must meet <strong>{standard}</strong> Australian Standard.
                Failure to maintain compliance may expose the Owners Corporation to legal liability
                and potential fines under ACT legislation.
            </p>
        </div>
        """

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <h2 style="color:#1e40af;">East Gate Residences — PPM Maintenance Alert</h2>
        {compliance_banner}
        {urgency_html}
        <table style="width:100%;border-collapse:collapse;">
            <tr><td style="padding:6px;font-weight:bold;width:35%;">Section</td>
                <td style="padding:6px;">{section}</td></tr>
            <tr style="background:#f9fafb;"><td style="padding:6px;font-weight:bold;">Description</td>
                <td style="padding:6px;">{description}</td></tr>
            <tr><td style="padding:6px;font-weight:bold;">Frequency</td>
                <td style="padding:6px;">{frequency}</td></tr>
            <tr style="background:#f9fafb;"><td style="padding:6px;font-weight:bold;">Vendor / Contractor</td>
                <td style="padding:6px;">{vendor} — {vendor_contact}</td></tr>
            <tr><td style="padding:6px;font-weight:bold;">Relevant Standard</td>
                <td style="padding:6px;">{standard}</td></tr>
            <tr style="background:#f9fafb;"><td style="padding:6px;font-weight:bold;">Last Completed</td>
                <td style="padding:6px;">{last_completed}</td></tr>
        </table>
        <p style="margin-top:16px;">
            <a href="{DASHBOARD_LINK}" style="background:#1e40af;color:white;padding:10px 20px;text-decoration:none;border-radius:4px;">
                View on Dashboard →
            </a>
        </p>
        <p style="color:#9ca3af;font-size:12px;margin-top:24px;">
            East Gate Residences, 14 Hoolihan Street, Denman Prospect ACT 2611
        </p>
    </body></html>
    """


def _owner_compliance_email_html(item: dict) -> str:
    """Short compliance notification email for owners/residents."""
    description = item.get("description", "")
    next_due = item.get("next_due_date", "")

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <h2 style="color:#1e40af;">East Gate Residences — Scheduled Safety Inspection</h2>
        <p>Dear Resident,</p>
        <p>
            A fire safety inspection has been scheduled for <strong>{next_due}</strong>:
            <em>{description}</em>.
        </p>
        <p>
            Contractors will access common areas only. <strong>No action is required from residents.</strong>
        </p>
        <p style="color:#9ca3af;font-size:12px;margin-top:24px;">
            East Gate Residences, 14 Hoolihan Street, Denman Prospect ACT
        </p>
    </body></html>
    """


# ─── Notification dedup logic ─────────────────────────────────────────────────

def _already_notified(item: dict, threshold_days: int) -> bool:
    """Return True if the same threshold notification was already sent today."""
    today_str = date.today().isoformat()
    for entry in item.get("notification_log", []):
        if (
                entry.get("threshold_days") == threshold_days
                and entry.get("date", "").startswith(today_str)
        ):
            return True
    return False


def _already_notified_owner(item: dict) -> bool:
    """Return True if owner compliance notification was already sent this week."""
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    for entry in item.get("notification_log", []):
        if entry.get("audience") == "owner" and entry.get("date", "") >= week_ago:
            return True
    return False


async def _log_notification(
        building_id: str, schedule_id: str, threshold_days: int, sent_to: str, audience: str = "manager"
) -> None:
    """Append a notification_log entry to avoid duplicate sends."""
    entry = {
        "date": get_current_utc_iso(),
        "threshold_days": threshold_days,
        "sent_to": sent_to,
        "audience": audience,
    }
    await db.maintenance_schedules.update_one(
        {"building_id": building_id, "schedule_id": schedule_id},
        {"$push": {"notification_log": entry}},
    )


async def _ppm_enabled(building_id: str) -> bool:
    """Is the PPM feature actually on for this building?

    This cron never asked. East Gate (13195) has had `ppm` resolve to FALSE the whole
    time and still received 912 manager notifications between 2026-08-05 and 2026-08-20
    — roughly 130 each to seven people — because the feature gate was only ever enforced
    on the UI/API paths and this scheduled sender bypassed it entirely. That is
    CLAUDE.md footgun #9b: a flag meant to gate an action has to be checked on every path
    capable of performing it, not just the expected one.

    Fails CLOSED. If the toggle cannot be resolved, the notification is skipped: an
    unsent PPM reminder is recoverable on the next run, a wrongly-sent one is not.
    """
    try:
        from db_postgres.repos import config_repo

        return bool(await config_repo.resolve_feature_toggle(building_id, "ppm", default=False))
    except Exception as exc:
        logger.warning(
            "PPM toggle unresolved for building %s (%s) — skipping notifications", building_id, exc
        )
        return False


async def _process_building(building_id: str, today: date, now: datetime) -> tuple[int, int]:
    """Run PPM notifications for a single building. Returns (manager_sends, owner_sends)."""
    if not await _ppm_enabled(building_id):
        logger.info("PPM feature disabled for building %s — no notifications sent", building_id)
        return 0, 0

    items = await db.maintenance_schedules.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(length=1000)

    if not items:
        logger.info("No PPM items for building %s", building_id)
        return 0, 0

    managers = await db.users.find(
        {
            "building_id": building_id,
            "role": {"$in": list(MANAGER_ROLES)},
            "status": "active",
        },
        {"email": 1, "full_name": 1, "_id": 0},
    ).to_list(length=100)
    manager_emails = [m["email"] for m in managers if m.get("email")]

    residents = await db.users.find(
        {
            "building_id": building_id,
            "role": {"$in": ["owner", "tenant"]},
            "status": "active",
        },
        {"email": 1, "_id": 0},
    ).to_list(length=300)
    resident_emails = [r["email"] for r in residents if r.get("email")]

    manager_sends = 0
    owner_sends = 0

    for item in items:
        schedule_id = item.get("schedule_id", "")
        next_due_str = item.get("next_due_date")
        if not next_due_str:
            continue

        try:
            next_due = date.fromisoformat(next_due_str)
        except ValueError:
            continue

        days_until_due = (next_due - today).days
        is_overdue = days_until_due < 0
        is_compliance = item.get("is_compliance", False)
        description = item.get("description", "")
        section = item.get("section", "")

        should_notify_manager = (
                days_until_due in MANAGER_THRESHOLDS
                or (is_overdue and not _already_notified(item, days_until_due))
        )

        if should_notify_manager and manager_emails and not _already_notified(item, days_until_due):
            subject = (
                f"⚠ PPM Overdue [{abs(days_until_due)}d]: {section} — {description[:50]}"
                if is_overdue
                else f"PPM Alert [{days_until_due}d]: {section} — {description[:50]}"
            )
            html_body = _manager_email_html(item, days_until_due)
            success_count = 0
            for email in manager_emails:
                try:
                    await send_email_async(
                        to_email=email,
                        subject=subject,
                        html_content=html_body,
                        context="ppm_manager_notification",
                    )
                    manager_sends += 1
                    success_count += 1
                except Exception as exc:
                    logger.error("Failed to send manager PPM email to %s: %s", email, exc)

            # Only log the notification if at least one email succeeded (avoid phantom escalation)
            if success_count > 0:
                await _log_notification(building_id, schedule_id, days_until_due, ",".join(manager_emails))
                await create_audit_log(
                    action="ppm_notification_sent",
                    resource_type="ppm_schedule",
                    resource_id=schedule_id,
                    user_id="system",
                    user_name="PPM Cron",
                    details={
                        "building_id": building_id,
                        "audience": "manager",
                        "days_until_due": days_until_due,
                        "recipient_count": success_count,
                    },
                )

        if is_compliance and 0 <= days_until_due <= 7 and resident_emails and not _already_notified_owner(item):
            subject = f"Safety Inspection Scheduled — {section}"
            html_body = _owner_compliance_email_html(item)
            success_count = 0
            for email in resident_emails:
                try:
                    await send_email_async(
                        to_email=email,
                        subject=subject,
                        html_content=html_body,
                        context="ppm_owner_notification",
                    )
                    owner_sends += 1
                    success_count += 1
                except Exception as exc:
                    logger.error("Failed to send owner PPM email to %s: %s", email, exc)

            if success_count > 0:
                await _log_notification(building_id, schedule_id, days_until_due, ",".join(resident_emails),
                                        audience="owner")
                await create_audit_log(
                    action="ppm_owner_notification_sent",
                    resource_type="ppm_schedule",
                    resource_id=schedule_id,
                    user_id="system",
                    user_name="PPM Cron",
                    details={
                        "building_id": building_id,
                        "audience": "owner",
                        "days_until_due": days_until_due,
                        "recipient_count": success_count,
                    },
                )

    return manager_sends, owner_sends


# ─── Main cron function ───────────────────────────────────────────────────────

async def run_ppm_notifications(building_id: str | None = None) -> None:
    """
    Main entry point for the daily PPM notification cron.
    Runs at 07:00 AEST. Iterates all active buildings (multi-tenant).
    """
    today = date.today()
    now = datetime.now(timezone.utc)
    logger.info("PPM notification cron starting — %s", today)

    # ── Acquire execution lock ────────────────────────────────────────────────
    lock_key = "cron_ppm_notifications_lock"
    lock_expiry = now - timedelta(hours=4)

    res = await db.locks.update_one(
        {
            "key": lock_key,
            "$or": [
                {"acquired_at": {"$lt": lock_expiry.isoformat()}},
                {"acquired_at": {"$exists": False}},
            ],
        },
        {"$set": {"acquired_at": now.isoformat(), "worker": "ppm_notifications"}},
        upsert=True,
    )

    if res.modified_count == 0 and res.upserted_id is None:
        logger.warning("PPM notification cron already running or locked. Skipping.")
        return

    try:
        # Fetch all active buildings — global collection, no tenant scope needed
        buildings = await db._db.buildings.find(
            {"is_active": {"$ne": False}},
            {"building_id": 1, "plan_id": 1},
        ).to_list(100)
        building_ids = [
            b.get("building_id") or b.get("plan_id")
            for b in buildings
            if b.get("building_id") or b.get("plan_id")
        ]

        total_manager = total_owner = 0
        for bid in building_ids:
            try:
                m, o = await _process_building(bid, today, now)
                total_manager += m
                total_owner += o
            except Exception as exc:
                logger.error("PPM cron failed for building %s: %s", bid, exc)

        logger.info(
            "PPM notifications complete — buildings: %d, manager sends: %d, owner sends: %d",
            len(building_ids),
            total_manager,
            total_owner,
        )
    finally:
        # Release the lock regardless of success/failure
        await db.locks.update_one(
            {"key": lock_key},
            {"$unset": {"acquired_at": ""}},
        )


if __name__ == "__main__":
    asyncio.run(run_ppm_notifications())
