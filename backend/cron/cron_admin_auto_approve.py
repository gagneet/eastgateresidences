#!/usr/bin/env python3
"""
Admin Auto-Approve Cron Job

Runs every 5 minutes.  If a super admin has NOT manually approved a
guest/tenant registration within ADMIN_AUTO_APPROVE_MINUTES (default 15)
after the owner approved it (status changed to "active", is_approved=False),
the user is automatically approved.

This prevents registrations from being stuck waiting for an admin who is
offline or has missed the notification.

Cron schedule: */5 * * * * (every 5 minutes)
Command: cd /path/to/backend && python3 cron/cron_admin_auto_approve.py
"""
import html as html_lib
import os
from datetime import datetime, timezone, timedelta
import sys
from pathlib import Path

import asyncio
from dotenv import load_dotenv
from pymongo import AsyncMongoClient

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


MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27018")
DB_NAME = os.getenv("DB_NAME", "strata_production")
PORTAL_URL = os.getenv("FRONTEND_URL", "https://www.eastgateresidences.com.au").rstrip("/")

# Auto-approve after this many minutes of no admin action
ADMIN_AUTO_APPROVE_MINUTES = int(os.getenv("ADMIN_AUTO_APPROVE_MINUTES", "15"))

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "noreply@eastgateresidences.com.au")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

_BUILDING_NAME = os.getenv("BUILDING_NAME", "Your Strata Community")
_CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "")
_CONTACT_PHONE = os.getenv("CONTACT_PHONE", "")


def _build_sig(building_name: str, contact_email: str, contact_phone: str):
    """Return (html_sig, text_sig) built from building settings — no East Gate hardcoding."""
    email_esc = html_lib.escape(contact_email)
    html = (
        '<div style="border-top:1px solid #e2e8f0;margin-top:24px;padding-top:16px;'
        'font-size:12px;color:#64748b;line-height:1.8">'
        f'<p style="margin:0;font-weight:600;color:#2F4F4F;font-size:13px">'
        f'{html_lib.escape(building_name)} | Executive Committee | Strata Manager</p>'
    )
    if contact_email:
        html += (
            f'<p style="margin:3px 0 0">E: <a href="mailto:{email_esc}" '
            f'style="color:#2563eb;text-decoration:none">{email_esc}</a></p>'
        )
    if contact_phone:
        html += f'<p style="margin:2px 0 0">P: {html_lib.escape(contact_phone)}</p>'
    html += '</div>'
    text = f"\n---\n{building_name} | Executive Committee | Strata Manager\n"
    if contact_email:
        text += f"E: {contact_email}\n"
    if contact_phone:
        text += f"P: {contact_phone}\n"
    return html, text


_SIG_HTML, _SIG_TEXT = _build_sig(_BUILDING_NAME, _CONTACT_EMAIL, _CONTACT_PHONE)


async def send_email(db, to: str, subject: str, html: str, text: str) -> None:
    """Send via the shared pipeline so this cron cannot bypass the outbound queue.

    See the matching note in cron_approval_escalation.py: transmitting directly meant
    this job's mail never appeared in the review console, which made the console
    incomplete rather than merely empty. CLAUDE.md footgun 9b.
    """
    from utils.email import send_email_async

    try:
        result = await send_email_async(
            to, subject, html, text or "", context="cron_admin_auto_approve.py",
        )
        if result.get("queued"):
            print(f"[auto-approve] Queued for review: {to}")
    except Exception as e:
        print(f"[auto-approve] Email error to {to}: {e}")


async def run(db) -> None:
    """Generated function header.

    Function: run
    Path: backend/cron/cron_admin_auto_approve.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=ADMIN_AUTO_APPROVE_MINUTES)).isoformat()

    # Candidates: owner-approved (status=active) but not yet admin-approved
    # These are users who:
    #   - are not yet is_approved
    #   - have status="active"  (set when owner approves via owner_registration_decision)
    #   - owner_decision was made > ADMIN_AUTO_APPROVE_MINUTES ago
    #   - role is guest or tenant
    candidates = await db.users.find(
        {
            "is_approved": False,
            "is_active": True,
            "status": "active",
            "role": {"$in": ["guest", "tenant"]},
            "owner_approved_at": {"$lte": cutoff},
        },
        {"_id": 0}
    ).to_list(200)

    if not candidates:
        print(f"[auto-approve] No candidates found at {now.isoformat()}")
        return

    print(f"[auto-approve] Auto-approving {len(candidates)} user(s)")

    for user in candidates:
        uid = user["id"]
        email = user.get("email", "")
        name = user.get("full_name", "Resident")
        role = user.get("role", "guest")
        role_label = role.title()

        # Pre-check: if activating this user's unit would violate the
        # unique_active_primary_owner_per_unit index, skip entirely so the
        # user stays pending rather than ending up approved-but-inaccessible.
        _building_id = user.get("building_id")
        _unit_number = user.get("unit_number")
        if _building_id and _unit_number:
            _conflict = await db.user_units.find_one({
                "building_id": _building_id,
                "unit_number": _unit_number,
                "role_at_unit": role,
                "is_active": True,
                "is_primary": True,
                "user_id": {"$ne": uid},
            })
            if _conflict:
                print(
                    f"[auto-approve] SKIP {uid} ({email}): unit {_unit_number} already "
                    f"has an active primary {role} — admin must resolve the conflict."
                )
                continue

        try:
            # 1. Approve the user
            await db.users.update_one(
                {"id": uid},
                {"$set": {
                    "is_approved": True,
                    "status": "active",
                    "auto_approved": True,
                    "auto_approved_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }}
            )

            # 2. Activate user_units
            await db.user_units.update_many(
                {"user_id": uid, "is_active": False},
                {"$set": {
                    "is_active": True,
                    "approved_by": "system_auto_approve",
                    "approved_date": now.isoformat(),
                    "updated_at": now.isoformat(),
                }}
            )

            # 3. Log in notifications collection
            await db.user_notifications.insert_one({
                "id": __import__('uuid').uuid4().hex,
                "user_id": uid,
                "title": "Account Approved",
                "message": f"Your {role_label} account has been automatically approved. Welcome!",
                "type": "approval",
                "is_read": False,
                "link": "/dashboard",
                "created_at": now.isoformat(),
            })

            # 4. Email the approved user
            guide_map = {"guest": "quick_role_guest.html", "tenant": "quick_role_tenant.html"}
            guide_path = guide_map.get(role, "quick_index.html")
            _e_user_name = html_lib.escape(str(name or ""))
            _e_user_role_label = html_lib.escape(str(role_label or ""))
            user_html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#2F4F4F;padding:20px;border-radius:8px 8px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">Account Approved</h1>
  </div>
  <div style="background:#f9f9f9;padding:24px;border:1px solid #e0e0e0;border-top:none">
    <p>Hi {_e_user_name},</p>
    <p>Your {_e_user_role_label} account has been <strong style="color:#2F4F4F">approved</strong>. You now have full access to the East Gate Residences portal.</p>
    <p style="text-align:center;margin:28px 0">
      <a href="{PORTAL_URL}/dashboard" style="background:#2F4F4F;color:#fff;padding:14px 32px;border-radius:6px;text-decoration:none;font-weight:bold">Sign In to Portal</a>
    </p>
    <p>Your quick guide: <a href="{PORTAL_URL}/user-guides/{guide_path}">{PORTAL_URL}/user-guides/{guide_path}</a></p>
    {_SIG_HTML}
  </div>
</div>"""
            user_text = (
                f"Hi {name},\n\nYour {role_label} account has been approved.\n"
                f"Sign in at: {PORTAL_URL}/dashboard\n{_SIG_TEXT}"
            )
            await send_email(db, email, "Account Approved — East Gate Residences", user_html, user_text)

            # 5. Notify admins/strata_managers
            admins = await db.users.find(
                {"role": {"$in": ["super_admin", "strata_manager"]}, "is_active": True},
                {"_id": 0, "id": 1, "email": 1, "full_name": 1}
            ).to_list(20)

            admin_subject = f"Auto-Approved {role_label}: {name}"
            # Escaped at the HTML sink; the admin_text/notification bodies below keep
            # the raw values, where markup is not interpreted.
            _e_name = html_lib.escape(str(name or ""))
            _e_email = html_lib.escape(str(email or ""))
            _e_role_label = html_lib.escape(str(role_label or ""))
            _e_unit = html_lib.escape(str(user.get("unit_number") or "—"))
            admin_html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#2F4F4F;padding:20px;border-radius:8px 8px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">Auto-Approval Notification</h1>
  </div>
  <div style="background:#f9f9f9;padding:24px;border:1px solid #e0e0e0;border-top:none">
    <p><strong>{_e_name}</strong> ({_e_email}) was <strong>auto-approved</strong> as a <strong>{_e_role_label}</strong>.</p>
    <p>The owner confirmed this registration but no admin reviewed it within {ADMIN_AUTO_APPROVE_MINUTES} minutes, so the system approved it automatically.</p>
    <p>Unit: {_e_unit}</p>
    <p style="text-align:center;margin:24px 0">
      <a href="{PORTAL_URL}/admin/users" style="background:#2F4F4F;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold">View Users</a>
    </p>
    {_SIG_HTML}
  </div>
</div>"""
            admin_text = (
                f"{name} ({email}) was auto-approved as {role_label} after "
                f"{ADMIN_AUTO_APPROVE_MINUTES} min with no admin action.{_SIG_TEXT}"
            )
            for admin in admins:
                await db.user_notifications.insert_one({
                    "id": __import__('uuid').uuid4().hex,
                    "user_id": admin["id"],
                    "title": admin_subject,
                    "message": f"{name} ({email}) auto-approved as {role_label}.",
                    "type": "approval",
                    "is_read": False,
                    "link": "/admin/users",
                    "created_at": now.isoformat(),
                })
                await send_email(db, admin["email"], admin_subject, admin_html, admin_text)

            print(f"[auto-approve] ✓ {name} ({email}) approved")

        except Exception as e:
            print(f"[auto-approve] ERROR processing {uid}: {e}")


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/cron/cron_admin_auto_approve.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]
    try:
        await run(db)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
