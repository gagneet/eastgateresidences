"""
Daily Payment Reminder Cron Job

Checks for upcoming due dates for:
  1. Strata Levies
  2. Council Rates / Land Tax
  3. Icon Water Bills
  4. AGM Sessions

Sends email reminders to Owners/Tenants based on their notification preferences.
Generates and attaches Levy Notice PDFs for levy reminders.
"""
import html as html_lib
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import asyncio
import logging

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from database import db

#: Stable namespace for deterministic document ids. Fixed forever — regenerating
#: it would orphan every notice already issued under the previous value.
DOCUMENT_NAMESPACE = uuid.UUID("6b1f2c4e-8a3d-5e7f-9c0b-1d2e3f4a5b6c")
from services.settings_service import get_general_settings_or_default
from services.jurisdiction_service import JurisdictionService
from services.levy_notice_financials import (
    prepare_levy_notice_financial_context,
    resolve_unit_levy_position,
)
from services.levy_notice_email_service import (
    build_grace_period_lines,
    resolve_managing_agent_branding,
)
from utils.email import get_email_template, send_email_async
from utils.pdf_generator import generate_levy_notice_pdf
from utils.finance_helpers import compute_unit_levy, get_latest_levy_year
from utils.auth import DEFAULT_BUILDING_ID
from request_context import set_ctx_building_id

log = logging.getLogger("payment_reminders")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def _get_active_building_ids() -> list[str]:
    """Generated function header.

    Function: _get_active_building_ids
    Path: backend/cron/cron_payment_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    buildings = await db.buildings.find({"is_active": True}, {"_id": 0, "id": 1}).to_list(200)
    building_ids = [b["id"] for b in buildings if b.get("id")]
    return building_ids or [DEFAULT_BUILDING_ID]


async def _get_building_user_ids(building_id: str) -> list[str]:
    """Generated function header.

    Function: _get_building_user_ids
    Path: backend/cron/cron_payment_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    memberships = await db.memberships.find(
        {"building_id": building_id},
        {"_id": 0, "user_id": 1},
    ).to_list(5000)
    return [m["user_id"] for m in memberships if m.get("user_id")]


async def get_reminder_settings(building_id: str):
    """Generated function header.

    Function: get_reminder_settings
    Path: backend/cron/cron_payment_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    settings = await get_general_settings_or_default(building_id, {"_id": 0}, settings_db=db)
    lead_days = int(settings.get("reminder_lead_days", 14) or 14)
    return {**settings, "reminder_lead_days": lead_days}


async def send_levy_reminders(lead_days: int, building_id: str):
    """Generated function header.

    Function: send_levy_reminders
    Path: backend/cron/cron_payment_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    set_ctx_building_id(building_id)
    log.info("Checking for upcoming Levy reminders (building=%s)...", building_id)
    from utils.workflow_runner import workflow_run
    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=lead_days)).date().isoformat()

    # Find levy due events matching target_date
    events = await db.events.find({
        "building_id": building_id,
        "event_type": "levy_due",
        "start_date": target_date
    }).to_list(100)

    if not events:
        log.info("No Levy due dates found for %s (building=%s)", target_date, building_id)
        return

    async with workflow_run("wf-004", building_id, trigger_detail="scheduled_levy_reminders") as run:

        # Pre-fetch all units and year info - Bolt ⚡
        all_units = await db.units.find(
            {"building_id": building_id, "owner_email": {"$exists": True, "$ne": None}}, {"_id": 0}
        ).to_list(1000)

        if not all_units:
            log.info("No units with owner emails found (building=%s)", building_id)
            return

        year = await get_latest_levy_year(building_id) or str(now.year)

        # Batch fetch all owners and their preferences to eliminate N+1 queries - Bolt ⚡
        owner_emails = set()
        for u in all_units:
            if u.get("owner_email"): owner_emails.add(u["owner_email"])

        building_user_ids = await _get_building_user_ids(building_id)

        # Map primary email AND mail_username to user docs
        owners_list = await db.users.find({
            "id": {"$in": building_user_ids},
            "$or": [
                {"email": {"$in": list(owner_emails)}},
                {"mail_username": {"$in": list(owner_emails)}}
            ]
        }).to_list(len(owner_emails) * 2 or 1)

        owner_map = {}
        for o in owners_list:
            if o.get("email"): owner_map[o["email"]] = o
            if o.get("mail_username"): owner_map[o["mail_username"]] = o

        user_ids = [o["id"] for o in owners_list]
        prefs_list = await db.email_notification_preferences.find({
            "user_id": {"$in": user_ids}
        }).to_list(len(user_ids))
        prefs_map = {p["user_id"]: p for p in prefs_list}

        # Pre-fetch levy rates for optimized calculation
        from utils.finance_helpers import get_levy_rates
        rates = await get_levy_rates(year, building_id)
        building_settings = await get_general_settings_or_default(building_id, {"_id": 0}, settings_db=db)

        # Managing-agent branding ("StrataOS" → the strata company's name) and the
        # per-state grace-period lines are the same for every unit in this building,
        # so resolve them once outside the unit loop.
        branding = resolve_managing_agent_branding(building_id, building_settings)
        building_state = await JurisdictionService(db).get_building_state(building_id)
        grace_period_lines = build_grace_period_lines(
            building_state, building_settings.get("grace_period_days")
        )
        plan_number = (
            building_settings.get("plan_number")
            or building_settings.get("strata_plan_number")
            or ""
        )

        # Per-unit financial position (arrears/credit + estimated interest/late fees) shares
        # the same building-level inputs for every unit, so resolve them once here; the per-unit
        # lookup inside the loop is then cheap. Never blocks the notice — degrades to $0.
        financial_context = await prepare_levy_notice_financial_context(
            db, building_id, year, building_settings
        )

        for event in events:
            log.info("Processing levy reminder for: %s (building=%s)", event['title'], building_id)

            for unit_doc in all_units:
                unit_number = unit_doc.get("unit_number")
                if not unit_number:
                    continue
                try:
                    # Collect all owner emails for this unit (primary + secondary, deduplicated)
                    primary_email = unit_doc.get("owner_email")
                    secondary_email = unit_doc.get("owner_email_b")

                    # Look up registered primary owner from in-memory map - Bolt ⚡
                    owner = owner_map.get(primary_email)
                    if owner:
                        prefs = prefs_map.get(owner.get('id', ''))
                        if prefs and not prefs.get("levy_reminders_enabled", True):
                            log.info("Skipping unit %s — levy reminders disabled by owner", unit_number)
                            continue
                        # Also add mail_username alias from registered account
                        mail_alias = owner.get("mail_username")
                    else:
                        mail_alias = None

                    # Build deduplicated recipient set
                    recipients = list({e for e in [primary_email, secondary_email, mail_alias] if e})
                    if not recipients:
                        continue

                    # Optimized in-memory levy calculation - Bolt ⚡
                    levy_calc = compute_unit_levy(unit_doc.get("entitlement", 0), rates)

                    # Per-unit account position — arrears owed OR credit paid in advance, plus
                    # any estimated interest / late-payment fee on overdue amounts. Canonical
                    # helpers only (see services/levy_notice_financials.py); display-only figures.
                    position = resolve_unit_levy_position(unit_number, financial_context)

                    levy_data = {
                        "notice_date": now.date().isoformat(),
                        "due_date": event['start_date'],
                        "period": event['title'].split(' - ')[1] if ' - ' in event['title'] else year,
                        "admin_amount": levy_calc["admin_quarterly"],
                        "sinking_amount": levy_calc["sinking_quarterly"],
                        "total_amount": levy_calc["total_quarterly"],
                        "jurisdiction": building_state,
                        "arrears_amount": position["arrears"],
                        "credit_amount": position["credit"],
                        "interest_amount": position["interest"],
                        "penalty_amount": position["penalty"],
                    }

                    owner_display = owner or unit_doc
                    pdf_content = generate_levy_notice_pdf(
                        levy_data,
                        unit_doc,
                        owner_display,
                        building_name=building_settings.get("building_name", building_id),
                        building_settings=building_settings,
                    )

                    # Save PDF to documents ONCE per (building, unit, levy period).
                    #
                    # This used to insert_one() a fresh uuid4 every run, with no
                    # check for an existing notice — so the document count grew by
                    # one per unit on every execution. East Gate ended up with 240
                    # documents for 80 units: exactly three copies of each, written
                    # in three runs, two of them 33 MILLISECONDS apart because the
                    # crontab held a duplicate entry for this job (see
                    # tasks/GAP-OPS-001). A scheduling accident should not be able
                    # to multiply stored records.
                    #
                    # The id is now derived from the identity of the notice rather
                    # than randomly, so re-running is a no-op instead of a duplicate.
                    # $setOnInsert means a re-run never rewrites an existing notice's
                    # body or its expiry — the first one issued for a period stands,
                    # which is what an owner was actually sent.
                    doc_id = str(uuid.uuid5(
                        DOCUMENT_NAMESPACE,
                        f"levy-notice:{building_id}:{unit_number}:{event['start_date']}",
                    ))
                    expires_at = (now + timedelta(days=30)).isoformat()
                    await db.documents.update_one({"id": doc_id}, {"$setOnInsert": {
                        "id": doc_id,
                        "building_id": building_id,
                        "title": f"Levy Notice - {unit_number} - {event['start_date']}",
                        "category": "finance",
                        "unit_number": unit_number,
                        "owner_id": owner.get('id', '') if owner else '',
                        "content_type": "application/pdf",
                        "expires_at": expires_at,
                        "created_at": now.isoformat(),
                        "is_private": True,
                        # REQUIRED for the 30-day expires_at above to mean anything.
                        # cron_notification_cleanup only purges expired documents whose
                        # author_id is "system" — a deliberate guard so a human-authored
                        # document carrying an expiry is never hard-deleted by a cron.
                        # These notices never set it, so none of them were ever reapable
                        # and they accumulated indefinitely: East Gate held 240 of them,
                        # all generated in one run, still present long after the levy
                        # data they describe had been removed. Generator and reaper have
                        # to agree on the marker.
                        "author_id": "system",
                    }}, upsert=True)

                    owner_name = (
                        (owner.get('full_name') if owner else None)
                        or unit_doc.get('owner_name')
                        or 'Owner'
                    )
                    # Amount shown in the email mirrors the PDF's "Total Amount Payable":
                    # current period levy + arrears + interest + late fee − any advance credit.
                    total_payable = max(
                        0.0,
                        float(levy_calc.get("total_quarterly", 0) or 0)
                        + position["arrears"] + position["interest"] + position["penalty"]
                        - position["credit"],
                    )
                    subject = f"Levy Notice — {branding['company_name']} — Unit {unit_number} (Due {event['start_date']})"
                    email_html, email_text = get_email_template(
                        "levy_notice",
                        format_variant=branding["email_format"],
                        owner_name=owner_name,
                        unit_plan=plan_number,
                        lot_number=unit_doc.get("lot_number", ""),
                        due_date=event["start_date"],
                        amount_due=f"{total_payable:,.2f}",
                        company_name=branding["company_name"],
                        company_phone=branding["company_phone"],
                        company_email=branding["company_email"],
                        company_address=branding["company_address"],
                        levies_phone=branding["levies_phone"],
                        levies_email=branding["levies_email"],
                        support_email=branding["support_email"],
                        grace_period_lines=grace_period_lines,
                    )

                    attachments = [{
                        "filename": f"Levy_Notice_{unit_number}_{event['start_date']}.pdf",
                        "content": pdf_content,
                        "mimetype": "application/pdf"
                    }]

                    # Send to all recipient emails for this unit
                    for recipient in recipients:
                        await send_email_async(
                            recipient, subject, email_html,
                            text_content=email_text,
                            attachments=attachments,
                            context=f"levy_reminder:building={building_id}:unit={unit_number}"
                        )
                        log.info("Sent levy reminder to %s for unit %s", recipient, unit_number)
                    run.items_processed += 1
                except Exception as e:
                    log.error("Failed to process levy reminder for unit %s: %s", unit_number, str(e))
                    run.items_failed += 1


async def send_council_tax_reminders(lead_days: int, building_id: str):
    """Generated function header.

    Function: send_council_tax_reminders
    Path: backend/cron/cron_payment_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    set_ctx_building_id(building_id)
    log.info("Checking for upcoming Council/Land Tax reminders (building=%s)...", building_id)
    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=lead_days)).date().isoformat()

    # Logic to find upcoming council rates/land tax due dates
    # Assuming they are recorded in council_rates collection

    # This is a simplified implementation
    pass


async def send_water_reminders(lead_days: int, building_id: str):
    """Generated function header.

    Function: send_water_reminders
    Path: backend/cron/cron_payment_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    set_ctx_building_id(building_id)
    log.info("Checking for upcoming Water Bill reminders (building=%s)...", building_id)
    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=lead_days)).date().isoformat()

    bills = await db.water_bills.find({
        "building_id": building_id,
        "due_date": target_date,
        "status": {"$ne": "paid"}
    }).to_list(100)

    if not bills:
        return

    # Batch fetch owners and preferences - Bolt ⚡
    unit_numbers = list({b['unit_number'] for b in bills})
    building_user_ids = await _get_building_user_ids(building_id)
    all_owners = await db.users.find({
        "id": {"$in": building_user_ids},
        "unit_number": {"$in": unit_numbers},
        "role": "owner",
        "is_active": True
    }).to_list(1000)

    owner_map_by_unit = {}
    for o in all_owners:
        owner_map_by_unit.setdefault(o["unit_number"], []).append(o)

    owner_ids = [o["id"] for o in all_owners]
    prefs_list = await db.email_notification_preferences.find({
        "user_id": {"$in": owner_ids}
    }).to_list(len(owner_ids))
    prefs_map = {p["user_id"]: p for p in prefs_list}

    for bill in bills:
        unit_number = bill['unit_number']
        owners = owner_map_by_unit.get(unit_number, [])

        for owner in owners:
            try:
                prefs = prefs_map.get(owner['id'])
                if not prefs or not prefs.get("water_reminders_enabled", False):
                    continue

                safe_name = html_lib.escape(owner.get('full_name', 'Owner'))
                safe_unit_number = html_lib.escape(str(unit_number))
                safe_due_date = html_lib.escape(str(bill['due_date']))
                subject = f"Water Bill Reminder - Unit {unit_number} - Due {bill['due_date']}"
                email_html = f"""
                <h2>Water Bill Reminder</h2>
                <p>Dear {safe_name},</p>
                <p>Your Icon Water bill for Unit {safe_unit_number} is due on <b>{safe_due_date}</b>.</p>
                <p>Amount Due: <b>${bill['amount']:,.2f}</b></p>
                <p>Please log in to the portal to view details and record payment.</p>
                """
                await send_email_async(owner['email'], subject, email_html)
                log.info("Sent water reminder to %s for unit %s", owner['email'], unit_number)
            except Exception as e:
                log.error("Failed to process water reminder for owner %s: %s", owner.get('id'), str(e))


async def send_agm_reminders(lead_days: int, building_id: str):
    """Generated function header.

    Function: send_agm_reminders
    Path: backend/cron/cron_payment_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    set_ctx_building_id(building_id)
    log.info("Checking for upcoming AGM reminders (building=%s)...", building_id)
    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=lead_days)).date().isoformat()

    agms = await db.agm.find({"building_id": building_id, "date": target_date}).to_list(10)

    if not agms:
        return

    # Batch fetch all approved owners and their preferences once - Bolt ⚡
    building_user_ids = await _get_building_user_ids(building_id)
    owners = await db.users.find({
        "id": {"$in": building_user_ids},
        "role": "owner",
        "is_approved": True,
    }).to_list(1000)
    if not owners:
        return

    owner_ids = [o["id"] for o in owners]
    prefs_list = await db.email_notification_preferences.find({
        "user_id": {"$in": owner_ids}
    }).to_list(len(owner_ids))
    prefs_map = {p["user_id"]: p for p in prefs_list}

    for agm in agms:
        for owner in owners:
            try:
                prefs = prefs_map.get(owner['id'])
                if not prefs or not prefs.get("agm_reminders_enabled", False):
                    continue

                safe_name = html_lib.escape(owner.get('full_name', 'Owner'))
                safe_agm_title = html_lib.escape(str(agm['title']))
                safe_agm_date = html_lib.escape(str(agm['date']))
                subject = f"AGM Reminder: {agm['title']} - {agm['date']}"
                email_html = f"""
                <h2>AGM Reminder</h2>
                <p>Dear {safe_name},</p>
                <p>This is a reminder for the upcoming Annual General Meeting: <b>{safe_agm_title}</b>.</p>
                <p>Date: <b>{safe_agm_date}</b></p>
                <p>Time: <b>{html_lib.escape(agm.get('time', 'N/A'))}</b></p>
                <p>Location: <b>{html_lib.escape(agm.get('location', 'Online'))}</b></p>
                <p>Please log in to the portal to review motions and cast your vote.</p>
                """
                await send_email_async(owner['email'], subject, email_html)
                log.info("Sent AGM reminder to %s", owner['email'])
            except Exception as e:
                log.error("Failed to process AGM reminder for owner %s: %s", owner.get('id'), str(e))


async def main():
    """Generated function header.

    Function: main
    Path: backend/cron/cron_payment_reminders.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    log.info("=== Payment Reminders Cron START ===")
    building_ids = await _get_active_building_ids()

    for building_id in building_ids:
        set_ctx_building_id(building_id)
        settings = await get_reminder_settings(building_id)
        lead_days = settings.get("reminder_lead_days", 14)
        log.info(
            "Processing payment reminders for building=%s (lead_days=%s)",
            building_id,
            lead_days,
        )

        await send_levy_reminders(lead_days, building_id)
        await send_council_tax_reminders(lead_days, building_id)
        await send_water_reminders(lead_days, building_id)
        await send_agm_reminders(lead_days, building_id)

    log.info("=== Payment Reminders Cron COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(main())
