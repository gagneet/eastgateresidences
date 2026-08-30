#!/usr/bin/env python3
"""
Owner Approval Escalation Cron Job

Runs hourly. Finds tenant/guest registrations that have been in
pending_owner_approval status for 48+ hours with no owner response,
then escalates to super admins with one-click Approve/Reject token links.

Cron schedule: 0 * * * * (every hour)
Command: cd /path/to/backend && python3 cron/cron_approval_escalation.py
"""
import html as html_lib
import os
import secrets
import sys
from datetime import datetime, timezone, timedelta
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
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "noreply@eastgateresidences.com.au")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

GUEST_ESCALATION_HOURS = 2  # guests need quick approval (short-term stays)
TENANT_ESCALATION_HOURS = 48  # tenants get 48-hour window for owner response
TOKEN_VALIDITY_HOURS = 72  # extra window for admin after escalation

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
    text = f"\n\n---\n{building_name} | Executive Committee | Strata Manager\n"
    if contact_email:
        text += f"E: {contact_email}\n"
    if contact_phone:
        text += f"P: {contact_phone}\n"
    return html, text


_SIG_HTML, _SIG_TEXT = _build_sig(_BUILDING_NAME, _CONTACT_EMAIL, _CONTACT_PHONE)


async def send_email(to_email: str, subject: str, html_content: str, text_content: str = ""):
    """Send via the shared pipeline so this cron cannot bypass the outbound queue.

    This used to transmit directly through the Resend HTTP API or smtplib. It honoured
    the kill switch, but it never reached the outbound message queue — so mail from this
    job left the building without ever appearing in the review console, and the console's
    "nothing queued" was a false statement about outgoing mail.

    CLAUDE.md footgun 9b: a gate has to be applied at EVERY path capable of the write,
    not just the expected one. Delegating to send_email_async means the kill switch, the
    queue, the interception redirect and email_sent_log all apply here identically.
    """
    from utils.email import send_email_async

    try:
        result = await send_email_async(
            to_email, subject, html_content, text_content or "",
            context="cron_approval_escalation.py",
        )
        if result.get("queued"):
            print(f"  Queued for review: {to_email}")
        elif result.get("success"):
            print(f"  Email sent to {to_email}")
        else:
            print(f"  Not sent to {to_email}: {result.get('provider')}")
        return bool(result.get("success"))
    except Exception as exc:
        print(f"  Email failed for {to_email}: {exc}")
        return False


async def run_escalation():
    """Generated function header.

    Function: run_escalation
    Path: backend/cron/cron_approval_escalation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]

    try:
        await client.admin.command("ping")
        print(f"Connected to {DB_NAME}")
    except Exception as exc:
        print(f"DB connection failed: {exc}")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    guest_cutoff = (now - timedelta(hours=GUEST_ESCALATION_HOURS)).isoformat()
    tenant_cutoff = (now - timedelta(hours=TENANT_ESCALATION_HOURS)).isoformat()

    print(f"\nEscalation check — guest cutoff: {guest_cutoff}, tenant cutoff: {tenant_cutoff}")

    # Find pending_owner_approval users past their role-specific window and not yet escalated
    overdue = await db.users.find({
        "status": "pending_owner_approval",
        "is_active": True,
        "owner_escalated_at": {"$exists": False},
        "$or": [
            {"role": "guest", "owner_notified_at": {"$lte": guest_cutoff, "$exists": True}},
            {"role": "tenant", "owner_notified_at": {"$lte": tenant_cutoff, "$exists": True}},
        ],
    }, {"_id": 0, "id": 1, "full_name": 1, "email": 1, "role": 1, "unit_number": 1,
        "owner_notified_at": 1}).to_list(200)

    print(f"Found {len(overdue)} overdue registration(s)")

    if not overdue:
        print("Nothing to escalate.")
        client.close()
        return

    # Fetch super_admin / chairman recipients. 'chairman' is not a top-level role (see
    # rules/post-compact-critical.md) — a chairman is role='ec_member' + ec_position='CHAIRMAN',
    # so 'ec_member' must be in this list directly, not the literal 'chairman' (which can
    # never match and was previously excluding real chairmen from escalation emails).
    admins = await db.users.find(
        {"role": {"$in": ["super_admin", "ec_member", "strata_admin"]}, "is_active": True},
        {"id": 1, "email": 1, "full_name": 1},
    ).to_list(50)

    if not admins:
        print("No admin users found — cannot escalate.")
        client.close()
        return

    escalated_count = 0
    token_expiry = (now + timedelta(hours=TOKEN_VALIDITY_HOURS)).isoformat()

    for user in overdue:
        uid = user["id"]
        name = user.get("full_name", "Unknown")
        unit = user.get("unit_number", "N/A")
        role = (user.get("role") or "resident").capitalize()
        notified_at = user.get("owner_notified_at", "")

        print(f"\n  Escalating: {name} ({uid}) — Unit {unit} — notified {notified_at}")

        # Generate admin approve/reject tokens
        approve_token = secrets.token_urlsafe(32)
        reject_token = secrets.token_urlsafe(32)
        await db.registration_approval_tokens.insert_many([
            {
                "token": approve_token, "user_id": uid, "action": "approve",
                "created_at": now_iso, "expires_at": token_expiry,
                "used": False, "created_for": "admin_escalation",
            },
            {
                "token": reject_token, "user_id": uid, "action": "reject",
                "created_at": now_iso, "expires_at": token_expiry,
                "used": False, "created_for": "admin_escalation",
            },
        ])

        # Mark user as escalated (so we don't re-send)
        await db.users.update_one(
            {"id": uid},
            {"$set": {"owner_escalated_at": now_iso, "updated_at": now_iso}},
        )

        # Build escalation email
        approve_url = f"{PORTAL_URL}/api/auth/registration-decision?token={approve_token}&action=approve"
        reject_url = f"{PORTAL_URL}/api/auth/registration-decision?token={reject_token}&action=reject"
        portal_link = f"{PORTAL_URL}/admin/users?status=pending_owner_approval"

        s_name = html_lib.escape(name)
        s_email_reg = html_lib.escape(user.get("email", ""))
        s_role = html_lib.escape(role)
        s_unit = html_lib.escape(str(unit))
        s_approve_url = html_lib.escape(approve_url)
        s_reject_url = html_lib.escape(reject_url)
        s_portal_link = html_lib.escape(portal_link)

        escalation_window = GUEST_ESCALATION_HOURS if user.get("role") == "guest" else TENANT_ESCALATION_HOURS
        try:
            notified_dt = datetime.fromisoformat(notified_at.replace("Z", "+00:00"))
            hours_waiting = int((now - notified_dt).total_seconds() / 3600)
        except Exception:
            hours_waiting = escalation_window

        escalation_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;padding:0;background:#f1f5f9}}
    .wrap{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,.10)}}
    .hdr{{background:#2F4F4F;color:#fff;padding:30px;text-align:center}}
    .hdr h1{{margin:0;font-size:22px;font-weight:700}}
    .body{{padding:28px 30px}}
    .info{{background:#fff7ed;border-left:4px solid #ea580c;padding:14px 18px;margin:18px 0;border-radius:6px;font-size:14px;line-height:1.8}}
    .info p{{margin:0}}
    .badge{{display:inline-block;background:#ea580c;color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}}
    .btns{{display:flex;gap:14px;margin:24px 0;flex-wrap:wrap}}
    .btn{{display:inline-block;padding:13px 28px;border-radius:8px;font-weight:700;font-size:15px;text-decoration:none;text-align:center;min-width:150px}}
    .btn-approve{{background:#16a34a;color:#fff}}
    .btn-reject{{background:#dc2626;color:#fff}}
    .note{{font-size:12px;color:#64748b;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin-top:16px;line-height:1.6}}
    .footer{{background:#f8fafc;padding:20px 30px}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hdr">
      <h1>East Gate Residences</h1>
    </div>
    <div class="body">
      <span class="badge">Escalation — Action Required</span>
      <h2 style="margin-top:0;font-size:18px;color:#1e293b">Owner Has Not Responded — Your Decision Needed</h2>
      <p style="font-size:14px;color:#475569;line-height:1.6">
        The unit owner was notified <strong>{hours_waiting} hours ago</strong> about the following registration but has not yet responded.
        As Strata Manager, please review and take action.
      </p>
      <div class="info">
        <p><strong>Name:</strong> {s_name}</p>
        <p><strong>Email:</strong> {s_email_reg}</p>
        <p><strong>Role:</strong> {s_role}</p>
        <p><strong>Unit:</strong> {s_unit}</p>
        <p><strong>Waiting Since:</strong> {notified_at[:10] if notified_at else "Unknown"}</p>
      </div>
      <p style="font-size:14px;font-weight:600;color:#374151">Please approve or decline this registration:</p>
      <div class="btns">
        <a href="{s_approve_url}" class="btn btn-approve">✓ &nbsp;Approve Access</a>
        <a href="{s_reject_url}" class="btn btn-reject">✗ &nbsp;Decline Request</a>
      </div>
      <div class="note">
        You can also manage all pending approvals from the portal: <a href="{s_portal_link}" style="color:#2563eb">View Pending Approvals</a>
      </div>
    </div>
    <div class="footer">{_SIG_HTML}</div>
  </div>
</body></html>"""

        escalation_text = (
            f"ESCALATION: Owner Has Not Responded — Action Required\n\n"
            f"Name: {name}\nEmail: {user.get('email', '')}\nRole: {role}\nUnit: {unit}\n"
            f"Waiting: {hours_waiting} hours\n\n"
            f"APPROVE: {approve_url}\n"
            f"DECLINE: {reject_url}\n\n"
            f"View all pending: {portal_link}"
            f"{_SIG_TEXT}"
        )
        subject = f"Escalation: {name} (Unit {unit}) — Owner Not Responding, Your Decision Needed"

        sent = 0
        sent_emails: set = set()
        for adm in admins:
            addr = adm.get("email")
            if addr and addr not in sent_emails:
                ok = await send_email(addr, subject, escalation_html, escalation_text)
                if ok:
                    sent += 1
                sent_emails.add(addr)

        # Always CC the operations contact
        cc = "gagneet@eastgateresidences.com.au"
        if cc not in sent_emails:
            await send_email(cc, f"[CC] {subject}", escalation_html, escalation_text)

        # In-app notification for admins
        notifications = [
            {
                "id": str(__import__("uuid").uuid4()),
                "user_id": adm["id"],
                "title": f"Escalation: {name} — Owner Not Responding",
                "message": (
                    f"{name} ({user.get('email', '')}) registered as {role} for Unit {unit} "
                    f"{hours_waiting} hours ago. The unit owner has not responded. Your action is required."
                ),
                "type": "user_approval",
                "related_id": uid,
                "link": "/admin/users?status=pending_owner_approval",
                "is_read": False,
                "created_at": now_iso,
            }
            for adm in admins
        ]
        if notifications:
            await db.user_notifications.insert_many(notifications)

        print(f"    Escalated — {sent} admin(s) notified")
        escalated_count += 1

    print(f"\nDone. Escalated {escalated_count} registration(s).")
    client.close()


if __name__ == "__main__":
    asyncio.run(run_escalation())
