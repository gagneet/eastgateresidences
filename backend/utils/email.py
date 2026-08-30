"""
Email Utilities

Functions for sending emails and generating email templates.
"""

# @featuretrace:email-delivery — Central email composition, formatting preference, provider dispatch, and audit log.
# Layer: service
# Data flow: routers/cron/workers -> send_email_async() -> Resend/SMTP + db.email_sent_log (scope param: building|global).
# Related: backend/routers/communication.py, backend/routers/notifications.py, frontend/src/pages/dashboard/EmailPreferencesPage.jsx, docs/architecture/mindmap/email-delivery.md

import html as html_lib
import os
import re
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import asyncio
import logging
import nh3

from config import (
    EMAIL_PROVIDER,
    RESEND_API_KEY,
    SENDGRID_API_KEY,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SENDER_EMAIL,
    RESEND_AVAILABLE,
    TEST_EMAIL_INTERCEPT_ADDRESS,
)
from database import db
from utils.crypto import decrypt_sensitive, is_encrypted

logger = logging.getLogger(__name__)

# Try to import resend
if RESEND_AVAILABLE:
    import resend


EMAIL_FORMAT_HTML = "html"
EMAIL_FORMAT_PLAIN = "plain_text"
EMAIL_FORMAT_VALUES = {EMAIL_FORMAT_HTML, EMAIL_FORMAT_PLAIN}


def _html_to_text(html_content: str) -> str:
    """Produce a readable plaintext fallback from sanitized or trusted HTML."""
    if not html_content:
        return ""
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html_content)
    text = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|tr)\s*>", "\n", text)
    text = re.sub(r"(?s)<style.*?</style>|<script.*?</script>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _normalize_email_format(value: str | None) -> str:
    """Generated function header.

    Function: _normalize_email_format
    Path: backend/utils/email.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value in EMAIL_FORMAT_VALUES:
        return value
    return EMAIL_FORMAT_HTML


async def _get_recipient_email_format(to_email: str, requested_format: str | None = None) -> str:
    """Resolve a recipient's preferred email format, defaulting to HTML."""
    if requested_format:
        return _normalize_email_format(requested_format)

    try:
        from request_context import get_ctx_building_id

        # email_preferences and email_notification_preferences are tenant-scoped
        # collections. Background jobs and one-off scripts often send outside a
        # request context, so they cannot safely read recipient preferences unless
        # the caller supplies an explicit email_format.
        if not get_ctx_building_id():
            return EMAIL_FORMAT_HTML

        user = await db.users.find_one(
            {"$or": [{"email": to_email}, {"mail_username": to_email}]},
            {"_id": 0, "id": 1},
        )
        if not user or not user.get("id"):
            return EMAIL_FORMAT_HTML

        scoped = await db.email_preferences.find_one(
            {"user_id": user["id"]},
            {"_id": 0, "email_format": 1},
        )
        if scoped and scoped.get("email_format"):
            return _normalize_email_format(scoped.get("email_format"))

        legacy = await db.email_notification_preferences.find_one(
            {"user_id": user["id"]},
            {"_id": 0, "email_format": 1},
        )
        if legacy and legacy.get("email_format"):
            return _normalize_email_format(legacy.get("email_format"))
    except Exception as err:
        logger.warning("Email format preference lookup failed for %s: %s", to_email, err)

    return EMAIL_FORMAT_HTML


def _wrap_email_html(body_html: str, preheader: str = "") -> str:
    """Wrap fragments in a high-contrast, email-client-safe document."""
    content = body_html or ""
    if "<html" in content.lower():
        # Some older call sites already build complete HTML documents. They
        # still pass through the central sender, so enforce a defensive light
        # colour baseline instead of assuming those templates chose readable
        # foreground/background pairs.
        contrast_css = (
            "<style>"
            ":root{color-scheme:light;supported-color-schemes:light;}"
            "body{background:#f3f6f8;color:#111827;}"
            "p,li,td,div,span,h1,h2,h3,h4,h5,h6{color:inherit;}"
            "</style>"
        )
        if "</head>" in content.lower():
            return re.sub(r"(?i)</head>", f"{contrast_css}</head>", content, count=1)
        # No <head> at all — match the opening <html> tag itself rather than
        # the literal substring "<html>", so a full document that declares
        # attributes (e.g. <html lang="en">, <html xmlns:o="...">) still gets
        # the contrast baseline instead of silently passing through unwrapped.
        return re.sub(
            r"(?i)(<html\b[^>]*>)",
            lambda m: f"{m.group(1)}<head>{contrast_css}</head>",
            content,
            count=1,
        )
    safe_preheader = html_lib.escape(preheader)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
</head>
<body style="margin:0;padding:0;background:#f3f6f8;color:#111827;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;color:#f3f6f8;opacity:0;">{safe_preheader}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f6f8;color:#111827;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <div style="max-width:640px;margin:0 auto;background:#ffffff;color:#111827;border:1px solid #d7dee8;border-radius:8px;overflow:hidden;">
          {content}
        </div>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _build_email_message(
        subject: str,
        sender: str,
        sender_name: str,
        to_email: str,
        html_content: str,
        text_content: str | None,
        email_format: str,
        attachments: list | None = None,
        bcc_email: str | None = None,
        bcc_name: str | None = None,
) -> MIMEMultipart:
    """Build a MIME message that honors HTML/plaintext delivery preference."""
    text_body = text_content or _html_to_text(html_content)
    html_body = _wrap_email_html(html_content, subject)

    msg = MIMEMultipart("mixed") if attachments else MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = to_email
    if bcc_email:
        msg["Bcc"] = f"{bcc_name} <{bcc_email}>" if bcc_name else bcc_email

    if attachments:
        body_part = MIMEMultipart("alternative")
    else:
        body_part = msg

    if email_format == EMAIL_FORMAT_PLAIN:
        body_part.attach(MIMEText(text_body, "plain", "utf-8"))
    else:
        body_part.attach(MIMEText(text_body, "plain", "utf-8"))
        body_part.attach(MIMEText(html_body, "html", "utf-8"))

    if attachments:
        msg.attach(body_part)
        for a in attachments:
            part = MIMEApplication(a["content"])
            part.add_header("Content-Disposition", "attachment", filename=a["filename"])
            msg.attach(part)

    return msg


async def get_email_settings():
    """Get email settings from database or use environment defaults"""
    settings = await db.email_settings.find_one({"id": "main"}, {"_id": 0})
    if not settings:
        default_provider = EMAIL_PROVIDER or ("resend" if RESEND_API_KEY else "smtp")
        return {
            "provider": default_provider,
            "resend_api_key": RESEND_API_KEY,
            "sendgrid_api_key": SENDGRID_API_KEY,
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "smtp_security": "tls",
            "smtp_user": SMTP_USER,
            "smtp_password": SMTP_PASSWORD,
            "sender_email": SENDER_EMAIL,
            "sender_name": "StrataOS",
        }
    # Decrypt any Fernet-encrypted credential fields before returning.
    # is_encrypted() check makes this safe during the migration window when
    # some DB documents may still hold plaintext values.
    for _field in ("smtp_password", "mail_password", "resend_api_key", "sendgrid_api_key", "migadu_api_key"):
        _val = settings.get(_field)
        if _val and is_encrypted(_val):
            settings[_field] = decrypt_sensitive(_val)
    # mail_password is the legacy field name; expose it under smtp_password too
    if not settings.get("smtp_password") and settings.get("mail_password"):
        settings["smtp_password"] = settings["mail_password"]
    return settings


async def _log_email_sent(
        to_email: str,
        subject: str,
        success: bool,
        provider: str = "",
        error: str = "",
        context: str = "",
):
    """Persist a record to email_sent_log for audit purposes (fire-and-forget)."""
    try:
        await db.email_sent_log.insert_one(
            {
                "id": str(uuid.uuid4()),
                "to_email": to_email,
                "subject": subject,
                "success": success,
                "provider": provider,
                "error": error,
                "context": context,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as log_err:
        logger.warning(f"email_sent_log write failed: {log_err}")



def _outbound_queue_enabled() -> bool:
    """Deployment-level switch for the outbound queue. Default OFF (see above)."""
    return str(os.getenv("OUTBOUND_QUEUE_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}


def _ambient_building_id_for_queue():
    """Best-effort building for a queued message, from the current request context."""
    try:
        from request_context import get_ctx_building_id
        return get_ctx_building_id()
    except Exception:
        return None


async def send_email_async(
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str = None,
        attachments: list = None,
        context: str = "",
        bcc_email: str = None,
        bcc_name: str = None,
        email_format: str = None,
        building_id: str = None,
        category: str = "automated",
        hold_seconds: int = None,
        created_by: str = None,
        _from_worker: bool = False,
):
    """
    Send email using configured provider.

    attachments: list of dicts: {"filename": str, "content": bytes, "mimetype": str}

    When the outbound queue is active (GAP-COMMS-003) this does NOT transmit: it
    persists the message in `outbound_messages` for review and returns immediately.
    The worker calls back in with ``_from_worker=True`` to perform the actual send, so
    that flag is the ONLY thing separating "enqueue" from "transmit" — never set it
    from a route handler or a cron, or the message skips the review window entirely.
    """
    # Kill switch FIRST — before interception, before provider selection, before any
    # network call. Interception only REDIRECTS mail (it still transmits, just to one
    # inbox); suppression must actually stop it, so it cannot sit downstream of that.
    from utils.email_suppression import suppress_if_blocked

    if await suppress_if_blocked(to_email, subject, context=context):
        # Recorded in email_sent_log so a suppressed send is auditable rather than
        # invisible — success=False with an explicit provider marker, never a silent drop.
        await _log_email_sent(
            to_email, subject, False, "suppressed",
            error="blocked by EMAIL_SEND_DISABLED_* configuration", context=context,
        )
        return {"success": False, "provider": "suppressed", "suppressed": True}

    # ── Outbound queue (GAP-COMMS-003) ───────────────────────────────────────────
    # Placed AFTER the kill switch so the env-level stop still wins over everything,
    # and BEFORE provider selection so no message can reach a provider un-reviewed.
    #
    # Default OFF: flipping this on without the admin console in place would strand
    # every outgoing message with nobody able to release it. It is enabled
    # deliberately, per deployment, once operators can see the queue.
    if not _from_worker and _outbound_queue_enabled():
        try:
            from services.outbound_queue_service import enqueue as _enqueue
            from models.outbound_message import MessageCategory

            _bid = building_id or _ambient_building_id_for_queue()
            if _bid:
                _row = await _enqueue(
                    building_id=_bid,
                    to_email=to_email,
                    subject=subject,
                    html_body=html_content,
                    text_body=text_content,
                    context=context,
                    category=(MessageCategory.MANUAL if category == "manual"
                              else MessageCategory.AUTOMATED),
                    created_by=created_by,
                    hold_seconds=hold_seconds,
                )
                return {"success": True, "queued": True, "provider": "queued",
                        "message_id": _row["id"]}
            # No building resolved: fall through and transmit. Holding a message we
            # cannot attribute to a building would put it in a queue no operator owns.
            logger.warning(
                "outbound queue active but no building resolved for %s — sending inline",
                to_email,
            )
        except Exception as queue_err:
            # Never let a queue fault silently swallow mail; fall back to the direct
            # path rather than dropping the message.
            logger.error("outbound queue enqueue failed (%s) — sending inline", queue_err)

    # Test email interception: redirect all outgoing email to a single address.
    # Activated by setting TEST_EMAIL_INTERCEPT_ADDRESS in backend/.env.
    # This prevents test-created data from spamming real users.
    if TEST_EMAIL_INTERCEPT_ADDRESS:
        original_to = to_email
        to_email = TEST_EMAIL_INTERCEPT_ADDRESS
        subject = f"[TEST→{original_to}] {subject}"
        bcc_email = None  # suppress BCC in test mode
        logger.info(f"TEST INTERCEPT: email redirected from {original_to} to {to_email}")

    settings = await get_email_settings()
    provider = settings.get("provider", "resend")
    sender = settings.get("sender_email", SENDER_EMAIL)
    sender_name = settings.get("sender_name", "StrataOS")
    resolved_email_format = await _get_recipient_email_format(to_email, email_format)
    text_body = text_content or _html_to_text(html_content)
    html_body = _wrap_email_html(html_content, subject)

    try:
        if provider == "resend" and RESEND_AVAILABLE:
            api_key = settings.get("resend_api_key") or RESEND_API_KEY
            if api_key:
                resend.api_key = api_key
                params = {
                    "from": f"{sender_name} <{sender}>",
                    "to": [to_email],
                    "subject": subject,
                }
                if resolved_email_format == EMAIL_FORMAT_PLAIN:
                    params["text"] = text_body
                else:
                    params["html"] = html_body
                    params["text"] = text_body
                if bcc_email:
                    bcc_display = f"{bcc_name} <{bcc_email}>" if bcc_name else bcc_email
                    params["bcc"] = [bcc_display]
                if attachments:
                    params["attachments"] = [
                        {"filename": a["filename"], "content": list(a["content"])}
                        for a in attachments
                    ]

                result = await asyncio.to_thread(resend.Emails.send, params)
                logger.info(f"Email sent via Resend to {to_email}")
                await _log_email_sent(
                    to_email, subject, True, "resend", context=context
                )
                return {"success": True, "provider": "resend", "id": result.get("id")}

        elif provider == "sendgrid":
            # SendGrid implementation placeholder
            logger.warning("SendGrid not yet implemented, falling back to SMTP")
            provider = "smtp"

        if provider == "smtp" or not RESEND_AVAILABLE:
            smtp_host = settings.get("smtp_host") or SMTP_HOST
            smtp_port = settings.get("smtp_port") or SMTP_PORT
            smtp_security = settings.get("smtp_security", "tls")
            smtp_user = settings.get("smtp_user") or SMTP_USER
            smtp_pass = settings.get("smtp_password") or SMTP_PASSWORD

            if smtp_host:
                msg = _build_email_message(
                    subject=subject,
                    sender=sender,
                    sender_name=sender_name,
                    to_email=to_email,
                    html_content=html_content,
                    text_content=text_content,
                    email_format=resolved_email_format,
                    attachments=attachments,
                    bcc_email=bcc_email,
                    bcc_name=bcc_name,
                )

                # Port 465 uses implicit SSL (SMTP_SSL)
                # Port 587 uses explicit TLS (STARTTLS)
                if smtp_port == 465 or smtp_security == "ssl":
                    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                        if smtp_user and smtp_pass:
                            server.login(smtp_user, smtp_pass)
                        server.send_message(msg)
                else:
                    # Port 587 or other ports use STARTTLS
                    with smtplib.SMTP(smtp_host, smtp_port) as server:
                        if smtp_security == "tls":
                            server.starttls()
                        if smtp_user and smtp_pass:
                            server.login(smtp_user, smtp_pass)
                        server.send_message(msg)

                logger.info(
                    f"Email sent via SMTP ({smtp_security.upper()}) to {to_email}"
                )
                await _log_email_sent(to_email, subject, True, "smtp", context=context)
                return {"success": True, "provider": "smtp"}

        logger.warning(f"No email provider configured, email not sent to {to_email}")
        await _log_email_sent(
            to_email,
            subject,
            False,
            "",
            "No email provider configured",
            context=context,
        )
        return {"success": False, "error": "No email provider configured"}

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        await _log_email_sent(to_email, subject, False, "", str(e), context=context)
        return {"success": False, "error": str(e)}


def get_email_template(template_type: str, **kwargs) -> tuple:
    """Generate HTML and text email content from template"""

    base_style = """
    <style>
        body { margin: 0; background: #f3f6f8; color: #111827; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: #2F4F4F; color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }
        .content { background: #ffffff; color: #111827; padding: 30px; border: 1px solid #d7dee8; border-top: 0; border-radius: 0 0 8px 8px; }
        .content p, .content li, .content td, .content div { color: #111827; }
        .content h1, .content h2, .content h3 { color: #111827; }
        .button { display: inline-block; background: #2F4F4F; color: white; padding: 12px 30px; text-decoration: none; border-radius: 25px; margin: 20px 0; }
        .footer { text-align: center; color: #666; font-size: 12px; margin-top: 20px; }
    </style>
    """

    if template_type == "owner_transfer_invite":
        raw_invite_link = kwargs.get("invite_link", "#")
        invite_link = html_lib.escape(raw_invite_link)
        raw_unit = html_lib.escape(str(kwargs.get("unit_number", "")))
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>{base_style}</head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Your building portal</h1>
                </div>
                <div class="content">
                    <h2>Welcome — you've been added as an owner</h2>
                    <p>An ownership transfer for <strong>Unit {raw_unit}</strong> has been approved and your email address has been registered on the resident portal.</p>
                    <p>Click the button below to set your password and activate your account. The link expires in 72 hours.</p>
                    <p style="text-align: center;">
                        <a href="{invite_link}" class="button">Set Up Your Account</a>
                    </p>
                    <p style="font-size:13px;color:#666;">If you weren't expecting this email, please contact building management.</p>
                </div>
                <div class="footer">
                    <p>Resident Portal — strata management platform</p>
                </div>
            </div>
        </body>
        </html>
        """
        text = (
            f"Welcome — you've been added as an owner for Unit {kwargs.get('unit_number', '')}.\n\n"
            f"Set up your portal account here: {raw_invite_link}\n\n"
            "This link expires in 72 hours."
        )
        return html, text

    elif template_type == "password_reset":
        raw_reset_link = kwargs.get("reset_link", "#")
        reset_link = html_lib.escape(raw_reset_link)
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>{base_style}</head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>StrataOS</h1>
                </div>
                <div class="content">
                    <h2>Password Reset Request</h2>
                    <p>You requested to reset your password. Click the button below to set a new password:</p>
                    <p style="text-align: center;">
                        <a href="{reset_link}" class="button">Reset Password</a>
                    </p>
                    <p>This link will expire in 1 hour.</p>
                    <p>If you didn't request this, please ignore this email.</p>
                </div>
                <div class="footer">
                    <p>East Gate Residences - 14 Hoolihan Street, Denman Prospect, ACT</p>
                </div>
            </div>
        </body>
        </html>
        """
        text = f"Password Reset Request\n\nClick this link to reset your password: {raw_reset_link}\n\nThis link expires in 1 hour."
        return html, text

    elif template_type == "announcement":
        raw_title = kwargs.get("title", "New Announcement")
        title = html_lib.escape(raw_title)
        # content is expected to be HTML from Tiptap editor
        # SECURITY FIX: Sanitize HTML content to prevent XSS
        content = nh3.clean(kwargs.get("content", ""))
        raw_priority = kwargs.get("priority", "normal")

        priority_color = {
            "urgent": "#dc2626",
            "high": "#f97316",
            "normal": "#2563eb",
            "low": "#6b7280",
        }.get(raw_priority, "#2563eb")

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>{base_style}</head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>StrataOS</h1>
                </div>
                <div class="content">
                    <span style="display: inline-block; background: {priority_color}; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px; margin-bottom: 15px;">{raw_priority.upper()}</span>
                    <h2>{title}</h2>
                    <div>{content}</div>
                </div>
                <div class="footer">
                    <p>East Gate Residences - 14 Hoolihan Street, Denman Prospect, ACT</p>
                </div>
            </div>
        </body>
        </html>
        """
        text = f"[{raw_priority.upper()}] {raw_title}\n\n{content}"
        return html, text

    elif template_type == "maintenance_update":
        raw_request_title = kwargs.get("request_title", "")
        raw_status = kwargs.get("status", "")
        raw_message = kwargs.get("message", "")

        request_title = html_lib.escape(raw_request_title)
        status = html_lib.escape(raw_status)
        message = html_lib.escape(raw_message)

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>{base_style}</head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>StrataOS</h1>
                </div>
                <div class="content">
                    <h2>Maintenance Request Update</h2>
                    <p><strong>Request:</strong> {request_title}</p>
                    <p><strong>Status:</strong> {status}</p>
                    <p>{message}</p>
                </div>
                <div class="footer">
                    <p>East Gate Residences - 14 Hoolihan Street, Denman Prospect, ACT</p>
                </div>
            </div>
        </body>
        </html>
        """
        text = f"Maintenance Request Update\n\nRequest: {raw_request_title}\nStatus: {raw_status}\n\n{raw_message}"
        return html, text

    elif template_type == "notice":
        raw_title = kwargs.get("title", "")
        raw_content = kwargs.get("content", "")
        raw_priority = kwargs.get("priority", "normal")
        raw_category = kwargs.get("category", "general")
        requires_acknowledgment = kwargs.get("requires_acknowledgment", False)

        title = html_lib.escape(raw_title)
        content = html_lib.escape(raw_content)
        priority = html_lib.escape(raw_priority)
        category = html_lib.escape(raw_category)

        priority_color = {
            "urgent": "#dc2626",
            "high": "#f97316",
            "normal": "#2563eb",
            "low": "#6b7280",
        }.get(raw_priority, "#2563eb")

        ack_text = ""
        if requires_acknowledgment:
            ack_text = "<p style='background: #fef3c7; padding: 15px; border-left: 3px solid #f59e0b; margin-top: 20px;'><strong>⚠️ Action Required:</strong> This notice requires your acknowledgment. Please log in to the portal to acknowledge.</p>"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>{base_style}</head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>StrataOS</h1>
                    <p style="margin: 0; opacity: 0.9;">Official Notice</p>
                </div>
                <div class="content">
                    <div style="margin-bottom: 15px;">
                        <span style="display: inline-block; background: {priority_color}; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px; margin-right: 10px;">{priority.upper()}</span>
                        <span style="display: inline-block; background: #e5e7eb; color: #374151; padding: 4px 12px; border-radius: 12px; font-size: 12px;">{category.upper()}</span>
                    </div>
                    <h2>{title}</h2>
                    <p style="white-space: pre-wrap;">{content}</p>
                    {ack_text}
                </div>
                <div class="footer">
                    <p>East Gate Residences - 14 Hoolihan Street, Denman Prospect, ACT</p>
                </div>
            </div>
        </body>
        </html>
        """
        text = f"[{raw_priority.upper()}] [{raw_category.upper()}] Official Notice\n\n{raw_title}\n\n{raw_content}"
        if requires_acknowledgment:
            text += "\n\n⚠️ This notice requires your acknowledgment. Please log in to acknowledge."
        return html, text

    elif template_type == "levy_notice":
        # Automated Levy Notice notification email. Two selectable formats
        # ("standard" | "levies_team"); "StrataOS" is substituted by the managing
        # strata company's name resolved by services.levy_notice_email_service.
        # Amounts/branding/contacts arrive as already-resolved kwargs so this stays
        # pure and building-agnostic (no DB access here).
        variant = str(kwargs.get("format_variant", "standard")).strip().lower()

        company = html_lib.escape(str(kwargs.get("company_name", "StrataOS")))
        company_phone = html_lib.escape(str(kwargs.get("company_phone", "")))
        company_email = html_lib.escape(str(kwargs.get("company_email", "")))
        company_address = html_lib.escape(str(kwargs.get("company_address", "")))
        levies_phone = html_lib.escape(str(kwargs.get("levies_phone", "")))
        levies_email = html_lib.escape(str(kwargs.get("levies_email", "")))
        support_email = html_lib.escape(str(kwargs.get("support_email", "")))

        owner_name = html_lib.escape(str(kwargs.get("owner_name", "Owner")))
        unit_plan = html_lib.escape(str(kwargs.get("unit_plan", "")))
        lot_number = html_lib.escape(str(kwargs.get("lot_number", "")))
        due_date = html_lib.escape(str(kwargs.get("due_date", "")))
        amount_due = kwargs.get("amount_due")  # optional pre-formatted string, e.g. "1,517.53"
        grace_lines = kwargs.get("grace_period_lines") or []

        property_ref = f"U/Plan {unit_plan} Lot {lot_number}".strip()
        amount_html = (
            f"<p>Amount Due: <b>${html_lib.escape(str(amount_due))}</b></p>"
            if amount_due not in (None, "")
            else ""
        )

        if variant == "levies_team":
            grace_html = "".join(
                f"<li>{html_lib.escape(str(line))}</li>" for line in grace_lines
            )
            grace_block = (
                "<p>Please note, the below grace period applies to the below states:</p>"
                f"<ul>{grace_html}</ul>"
                if grace_html
                else ""
            )
            body = f"""
                    <h2>Levy Notice</h2>
                    <p>Please find attached a levy notice from <b>{company}</b>.</p>
                    {amount_html}
                    <p>Should you have any queries please contact:</p>
                    <p style="margin:0;"><b>Levies Department</b><br/>
                       P: {levies_phone}<br/>
                       <a href="mailto:{levies_email}">{levies_email}</a></p>
                    {grace_block}
                    <p>The grace period is where no interest or arrears apply to your account from
                       the due date of this notice unless these specific terms have been changed by
                       the Owners Corporation.</p>
                    <p>Failure to pay after the provided grace period can result in arrears charges
                       and interest charges applying.</p>
                    <p>Regards,<br/>{company} Levies and Arrears Team</p>
            """
            text = (
                f"Please find attached a levy notice from {kwargs.get('company_name', 'StrataOS')}.\n\n"
                "Should you have any queries please contact:\n\n"
                f"Levies Department\nP: {kwargs.get('levies_phone', '')}\n"
                f"{kwargs.get('levies_email', '')}\n\n"
                + (
                    "Please note, the below grace period applies to the below states:\n"
                    + "\n".join(str(line) for line in grace_lines)
                    + "\n\n"
                    if grace_lines
                    else ""
                )
                + "The grace period is where no interest or arrears apply to your account from the "
                "due date of this notice unless these specific terms have been changed by the "
                "Owners Corporation.\n\n"
                "Failure to pay after the provided grace period can result in arrears charges and "
                "interest charges applying.\n\n"
                f"Regards,\n{kwargs.get('company_name', 'StrataOS')} Levies and Arrears Team"
            )
        else:  # "standard"
            body = f"""
                    <h2>Levy Notice</h2>
                    <p>Dear {owner_name},</p>
                    <p>This is an automated notification regarding the levy notice for your property
                       at <b>{property_ref}</b>. Please find the notice attached for your reference.</p>
                    {amount_html}
                    <p>Kindly review the details and ensure payment is made by the Due Date
                       {('of <b>' + due_date + '</b> ') if due_date else ''}stated in the attached
                       levy notice to avoid any late fees.</p>
                    <p>If payment has already been made, please disregard this notice. For any
                       inquiries or assistance, feel free to email us at
                       <a href="mailto:{support_email}">{support_email}</a>.</p>
                    <p>Thank you for your prompt attention to this matter.</p>
                    <p>Best regards,<br/>{company}</p>
            """
            text = (
                f"Dear {kwargs.get('owner_name', 'Owner')},\n\n"
                "This is an automated notification regarding the levy notice for your property at "
                f"U/Plan {kwargs.get('unit_plan', '')} Lot {kwargs.get('lot_number', '')}. "
                "Please find the notice attached for your reference.\n\n"
                "Kindly review the details and ensure payment is made by the Due Date "
                + (f"of {kwargs.get('due_date')} " if kwargs.get("due_date") else "")
                + "stated in the attached levy notice to avoid any late fees.\n\n"
                "If payment has already been made, please disregard this notice. For any inquiries "
                f"or assistance, feel free to email us at {kwargs.get('support_email', '')}.\n\n"
                "Thank you for your prompt attention to this matter.\n\n"
                f"Best regards,\n{kwargs.get('company_name', 'StrataOS')}"
            )

        # Shared contact footer (managing agent's details substitute the platform defaults).
        contact_lines = []
        if company_phone:
            contact_lines.append(f"Phone: {company_phone}")
        if company_email:
            contact_lines.append(f'Email: <a href="mailto:{company_email}">{company_email}</a>')
        if company_address:
            contact_lines.append(f"Address: {company_address}")
        contact_html = "<br/>".join(contact_lines)

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>{base_style}</head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{company}</h1>
                    <p style="margin: 0; opacity: 0.9;">Levy Notice</p>
                </div>
                <div class="content">
                    {body}
                </div>
                <div class="footer">
                    <p><strong>{company}</strong></p>
                    <p>{contact_html}</p>
                </div>
            </div>
        </body>
        </html>
        """

        # Append the plain-text contact footer to both variants.
        text_footer_parts = []
        if company_phone:
            text_footer_parts.append(f"Phone: {kwargs.get('company_phone', '')}")
        if company_email:
            text_footer_parts.append(f"Email: {kwargs.get('company_email', '')}")
        if company_address:
            text_footer_parts.append(f"Address: {kwargs.get('company_address', '')}")
        if text_footer_parts:
            text += "\n\n" + "\n".join(text_footer_parts)

        return html, text

    return "", ""


__all__ = [
    "get_email_settings",
    "send_email_async",
    "get_email_template",
]
