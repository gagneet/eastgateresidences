#!/usr/bin/env python3
"""
Automated Expiration Check System
Runs daily to check for expired tenants/guests and send renewal reminders
"""
import asyncio
import html as html_lib
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pymongo import AsyncMongoClient

# Load environment variables
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# These cron scripts are self-contained (stdlib + pymongo only) and are launched as bare
# scripts — `cd backend && python3 cron/<name>.py` — so sys.path[0] is backend/cron and
# NOTHING from backend/ is importable by default. The email kill switch lives in
# backend/utils/, so backend/ has to go on the path before it can be imported.
# Without this the import raises ImportError and the guard never runs.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Imported at module level ON PURPOSE, with no try/except. If the kill switch cannot be
# loaded this cron must not run at all: failing loudly and sending nothing is the correct
# outcome for a switch whose job is to guarantee no mail leaves. The previous version
# imported inside the function under `except ImportError: pass`, which fails OPEN — the
# guard silently vanished and the cron sent anyway.
from utils.email_suppression import suppress_if_blocked  # noqa: E402


# Email configuration
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
SMTP_HOST = os.getenv('SMTP_HOST')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
SENDER_EMAIL = os.getenv('SENDER_EMAIL', 'noreply@eastgateresidences.com.au')
APP_URL = os.getenv('APP_URL', 'https://eastgateresidences.com.au')


def normalize_datetime_string(date_str: str) -> str:
    """
    Normalize datetime strings by replacing trailing 'Z' with '+00:00'
    for compatibility with datetime.fromisoformat()
    """
    if isinstance(date_str, str) and date_str.endswith("Z"):
        return date_str[:-1] + "+00:00"
    return date_str


def parse_datetime_safe(date_str: str) -> datetime:
    """
    Safely parse a datetime string, handling 'Z' suffix
    """
    try:
        normalized = normalize_datetime_string(date_str)
        dt = datetime.fromisoformat(normalized)
        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError) as e:
        print(f"  ⚠️  Error parsing date '{date_str}': {e}")
        raise


async def send_email_notification(to_email: str, subject: str, html_content: str):
    """
    Send email notification using Resend or SMTP fallback
    """
    # Kill switch — this cron transmits directly via the Resend HTTP API /
    # smtplib and never calls utils.email.send_email_async, so the guard there
    # does not cover it (CLAUDE.md footgun #9b).
    if await suppress_if_blocked(to_email, subject, context="cron_expiration_check.py"):
        return False

    try:
        if RESEND_API_KEY:
            # Use Resend API
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    'https://api.resend.com/emails',
                    headers={
                        'Authorization': f'Bearer {RESEND_API_KEY}',
                        'Content-Type': 'application/json',
                    },
                    json={
                        'from': SENDER_EMAIL,
                        'to': [to_email],
                        'subject': subject,
                        'html': html_content,
                    }
                )
                if response.status_code == 200:
                    print(f"  ✅ Email sent to {to_email}")
                    return True
                else:
                    print(f"  ⚠️  Resend API error: {response.text}")
                    return False
        elif SMTP_HOST and SMTP_USER:
            # Use SMTP
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = SENDER_EMAIL
            msg['To'] = to_email
            msg.attach(MIMEText(html_content, 'html'))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
                print(f"  ✅ Email sent to {to_email}")
                return True
        else:
            print(f"  ⚠️  No email provider configured (Resend or SMTP)")
            return False
    except Exception as e:
        print(f"  ❌ Email send failed: {e}")
        return False


def get_tenant_expiration_warning_html(full_name: str, unit_number: str, expiration_date: str, days_until: int):
    """Generate HTML for tenant expiration warning email.

    full_name and unit_number are user-editable profile fields going into an HTML
    body, so they are escaped here at the sink. The plain-text companion below keeps
    the raw values — escaping them there would show the reader literal entities.
    """
    full_name = html_lib.escape(str(full_name or ""))
    unit_number = html_lib.escape(str(unit_number or ""))
    expiration_date = html_lib.escape(str(expiration_date or ""))
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #2F4F4F; color: white; padding: 20px; text-align: center; }}
            .content {{ background: #f9f9f9; padding: 20px; margin-top: 20px; }}
            .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; }}
            .button {{ display: inline-block; background: #E07A5F; color: white; padding: 12px 24px;
                      text-decoration: none; border-radius: 5px; margin-top: 15px; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Lease Expiration Notice</h1>
            </div>
            <div class="content">
                <p>Dear {full_name},</p>

                <div class="warning">
                    <strong>⚠️ Your lease is expiring soon</strong>
                </div>

                <p><strong>Unit:</strong> {unit_number}</p>
                <p><strong>Expiration Date:</strong> {expiration_date}</p>
                <p><strong>Days Remaining:</strong> {days_until} days</p>

                <p>If you wish to renew your lease, please log in to the East Gate Residences portal and submit a renewal request:</p>

                <a href="{APP_URL}/dashboard" class="button">Request Renewal</a>

                <p style="margin-top: 20px;">If you do not plan to renew, please ensure you:</p>
                <ul>
                    <li>Notify your landlord</li>
                    <li>Arrange for property inspection</li>
                    <li>Plan your move-out before the expiration date</li>
                </ul>

                <p>Your account will be automatically deactivated after the expiration date unless a renewal is approved.</p>
            </div>
            <div class="footer">
                <p>This is an automated message from East Gate Residences Strata Management</p>
                <p>© 2026 East Gate Residences. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """


def get_tenant_expired_html(full_name: str, unit_number: str, expiration_date: str, days_since: int):
    """Generate HTML for tenant expired email"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #dc3545; color: white; padding: 20px; text-align: center; }}
            .content {{ background: #f9f9f9; padding: 20px; margin-top: 20px; }}
            .alert {{ background: #f8d7da; border-left: 4px solid #dc3545; padding: 15px; margin: 20px 0; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Lease Expired - Account Deactivated</h1>
            </div>
            <div class="content">
                <p>Dear {full_name},</p>

                <div class="alert">
                    <strong>🔴 Your lease has expired and your account has been deactivated</strong>
                </div>

                <p><strong>Unit:</strong> {unit_number}</p>
                <p><strong>Expired On:</strong> {expiration_date}</p>
                <p><strong>Days Since Expiration:</strong> {days_since} days</p>

                <p>Your account access has been disabled as your lease period has ended without an approved renewal.</p>

                <p>If you believe this is an error or if you have renewed your lease with your landlord, please contact:</p>
                <ul>
                    <li>Your landlord directly</li>
                    <li>Strata management at admin@eastgateresidences.com.au</li>
                </ul>

                <p>The strata committee can manually reactivate your account if you provide updated lease documentation.</p>
            </div>
            <div class="footer">
                <p>This is an automated message from East Gate Residences Strata Management</p>
                <p>© 2026 East Gate Residences. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """


def get_guest_ending_soon_html(full_name: str, unit_number: str, end_date: str, days_until: int):
    """Generate HTML for guest ending soon email"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #2F4F4F; color: white; padding: 20px; text-align: center; }}
            .content {{ background: #f9f9f9; padding: 20px; margin-top: 20px; }}
            .info {{ background: #d1ecf1; border-left: 4px solid #17a2b8; padding: 15px; margin: 20px 0; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Guest Access Ending Soon</h1>
            </div>
            <div class="content">
                <p>Dear {full_name},</p>

                <div class="info">
                    <strong>ℹ️ Your guest access is ending soon</strong>
                </div>

                <p><strong>Unit:</strong> {unit_number}</p>
                <p><strong>End Date:</strong> {end_date}</p>
                <p><strong>Days Remaining:</strong> {days_until} days</p>

                <p>Your temporary guest access to East Gate Residences will expire on the date shown above.</p>

                <p><strong>Please note:</strong></p>
                <ul>
                    <li>Guest access cannot be renewed (maximum stay is 364 days)</li>
                    <li>Your account will be automatically deactivated after the end date</li>
                    <li>Please coordinate with your host for any access needs</li>
                </ul>

                <p>If you need to extend your stay beyond the original end date, please contact the unit owner/tenant who registered you as a guest.</p>
            </div>
            <div class="footer">
                <p>This is an automated message from East Gate Residences Strata Management</p>
                <p>© 2026 East Gate Residences. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """


async def check_expirations():
    """
    Main function to check for expirations and send notifications
    """
    mongo_url = os.getenv('MONGO_URL', 'mongodb://localhost:27018')
    db_name = os.getenv('DB_NAME', 'strata_production')

    client = AsyncMongoClient(mongo_url)
    db = client[db_name]

    try:
        print(f"\n{'=' * 60}")
        print(f"🔍 EXPIRATION CHECK - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"{'=' * 60}\n")

        # Connect to database
        await client.admin.command('ping')
        print(f"✅ Connected to database: {db_name}\n")

        today = datetime.now(timezone.utc)
        today_iso = today.isoformat()
        warning_30_days = (today + timedelta(days=30)).isoformat()
        warning_7_days = (today + timedelta(days=7)).isoformat()

        # ============ CHECK TENANTS EXPIRING IN 30 DAYS ============
        print("📊 Checking tenants expiring in 30 days...")
        tenants_30 = await db.user_units.find({
            "role_at_unit": "tenant",
            "is_active": True,
            "expiration_date": {"$lte": warning_30_days, "$gt": warning_7_days}
        }, {"_id": 0}).to_list(100)

        for rel in tenants_30:
            user = await db.users.find_one({"id": rel["user_id"]}, {"_id": 0})
            if user and not user.get("renewal_reminder_sent", False):
                exp_date = parse_datetime_safe(rel["expiration_date"])
                days_until = (exp_date - today).days

                print(f"  ⚠️  {user['full_name']} ({rel['unit_number']}) - {days_until} days until expiration")

                # Send warning email
                html = get_tenant_expiration_warning_html(
                    user['full_name'],
                    rel['unit_number'],
                    exp_date.strftime('%Y-%m-%d'),
                    days_until
                )
                await send_email_notification(
                    user['email'],
                    f"Lease Expiration Notice - {rel['unit_number']}",
                    html
                )

                # Mark reminder sent
                await db.users.update_one(
                    {"id": user["id"]},
                    {"$set": {"renewal_reminder_sent": True, "requires_renewal": True}}
                )

        print(f"  ✅ Processed {len(tenants_30)} tenants (30-day warning)\n")

        # ============ CHECK TENANTS EXPIRING IN 7 DAYS ============
        print("📊 Checking tenants expiring in 7 days...")
        tenants_7 = await db.user_units.find({
            "role_at_unit": "tenant",
            "is_active": True,
            "expiration_date": {"$lte": warning_7_days, "$gt": today_iso}
        }, {"_id": 0}).to_list(100)

        for rel in tenants_7:
            user = await db.users.find_one({"id": rel["user_id"]}, {"_id": 0})
            if user:
                exp_date = parse_datetime_safe(rel["expiration_date"])
                days_until = (exp_date - today).days

                print(f"  🔴 URGENT: {user['full_name']} ({rel['unit_number']}) - {days_until} days until expiration")

                # Send urgent warning email
                html = get_tenant_expiration_warning_html(
                    user['full_name'],
                    rel['unit_number'],
                    exp_date.strftime('%Y-%m-%d'),
                    days_until
                )
                await send_email_notification(
                    user['email'],
                    f"URGENT: Lease Expiring Soon - {rel['unit_number']}",
                    html
                )

        print(f"  ✅ Processed {len(tenants_7)} tenants (7-day warning)\n")

        # ============ CHECK EXPIRED TENANTS ============
        print("📊 Checking expired tenants...")
        expired_tenants = await db.user_units.find({
            "role_at_unit": "tenant",
            "is_active": True,
            "expiration_date": {"$lt": today_iso}
        }, {"_id": 0}).to_list(100)

        for rel in expired_tenants:
            user = await db.users.find_one({"id": rel["user_id"]}, {"_id": 0})
            if user:
                exp_date = parse_datetime_safe(rel["expiration_date"])
                days_since = (today - exp_date).days

                print(f"  ❌ EXPIRED: {user['full_name']} ({rel['unit_number']}) - {days_since} days ago")

                # Deactivate user-unit relationship
                await db.user_units.update_one(
                    {"id": rel["id"]},
                    {"$set": {
                        "is_active": False,
                        "actual_end_date": today_iso,
                        "updated_at": today_iso
                    }}
                )

                # Only deactivate user account if no other active relationships remain
                other_active_rel = await db.user_units.find_one({
                    "user_id": rel["user_id"],
                    "is_active": True,
                    "id": {"$ne": rel["id"]}
                })

                if not other_active_rel:
                    await db.users.update_one(
                        {"id": user["id"]},
                        {"$set": {
                            "is_active": False,
                            "deactivation_date": today_iso,
                            "deactivation_reason": "Lease expired without renewal"
                        }}
                    )
                    print(f"    └─ User account deactivated (no other active relationships)")
                else:
                    print(f"    └─ User account kept active (has other active relationships)")

                # Send expiration email
                html = get_tenant_expired_html(
                    user['full_name'],
                    rel['unit_number'],
                    exp_date.strftime('%Y-%m-%d'),
                    days_since
                )
                await send_email_notification(
                    user['email'],
                    f"Account Deactivated - Lease Expired",
                    html
                )

        print(f"  ✅ Deactivated {len(expired_tenants)} expired tenants\n")

        # ============ CHECK GUESTS ENDING IN 7 DAYS ============
        print("📊 Checking guests ending in 7 days...")
        guests_7 = await db.user_units.find({
            "role_at_unit": "guest",
            "is_active": True,
            "end_date": {"$lte": warning_7_days, "$gt": today_iso}
        }, {"_id": 0}).to_list(100)

        for rel in guests_7:
            user = await db.users.find_one({"id": rel["user_id"]}, {"_id": 0})
            if user:
                end_date = parse_datetime_safe(rel["end_date"])
                days_until = (end_date - today).days

                print(f"  ⚠️  Guest: {user['full_name']} ({rel['unit_number']}) - {days_until} days remaining")

                # Send warning email
                html = get_guest_ending_soon_html(
                    user['full_name'],
                    rel['unit_number'],
                    end_date.strftime('%Y-%m-%d'),
                    days_until
                )
                await send_email_notification(
                    user['email'],
                    f"Guest Access Ending Soon - {rel['unit_number']}",
                    html
                )

        print(f"  ✅ Processed {len(guests_7)} guests (7-day warning)\n")

        # ============ CHECK EXPIRED GUESTS ============
        print("📊 Checking expired guests...")
        expired_guests = await db.user_units.find({
            "role_at_unit": "guest",
            "is_active": True,
            "end_date": {"$lt": today_iso}
        }, {"_id": 0}).to_list(100)

        for rel in expired_guests:
            user = await db.users.find_one({"id": rel["user_id"]}, {"_id": 0})
            if user:
                end_date = parse_datetime_safe(rel["end_date"])
                days_since = (today - end_date).days

                print(f"  ❌ EXPIRED: Guest {user['full_name']} ({rel['unit_number']}) - {days_since} days ago")

                # Deactivate user-unit relationship
                await db.user_units.update_one(
                    {"id": rel["id"]},
                    {"$set": {
                        "is_active": False,
                        "actual_end_date": today_iso,
                        "updated_at": today_iso
                    }}
                )

                # Only deactivate user account if no other active relationships remain
                other_active_rel = await db.user_units.find_one({
                    "user_id": rel["user_id"],
                    "is_active": True,
                    "id": {"$ne": rel["id"]}
                })

                if not other_active_rel:
                    await db.users.update_one(
                        {"id": user["id"]},
                        {"$set": {
                            "is_active": False,
                            "deactivation_date": today_iso,
                            "deactivation_reason": "Guest period ended"
                        }}
                    )
                    print(f"    └─ User account deactivated (no other active relationships)")
                else:
                    print(f"    └─ User account kept active (has other active relationships)")

        print(f"  ✅ Deactivated {len(expired_guests)} expired guests\n")

        # ============ SUMMARY ============
        print(f"{'=' * 60}")
        print("📋 SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Tenants warned (30 days): {len(tenants_30)}")
        print(f"  Tenants warned (7 days):  {len(tenants_7)}")
        print(f"  Tenants deactivated:      {len(expired_tenants)}")
        print(f"  Guests warned (7 days):   {len(guests_7)}")
        print(f"  Guests deactivated:       {len(expired_guests)}")
        print(f"{'=' * 60}\n")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(check_expirations())
