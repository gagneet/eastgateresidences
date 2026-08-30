"""
Public trial request endpoint — no auth required.

Accepts inbound "Request a Walkthrough" form submissions from the StrataOS marketing page.
Validates reCAPTCHA v3 token, persists the lead to Postgres (core.trial_requests), sends
email to the platform contact address, and creates bell notifications for all active
super_admin users.

Admin endpoints (super_admin only):
  GET    /admin/trial-requests           — list with filters + aggregate counts
  PATCH  /admin/trial-requests/{id}      — update notes
  POST   /admin/trial-requests/{id}/approve — approve + link tenant/invitation
  POST   /admin/trial-requests/{id}/reject  — reject with optional reason

Postgres migration: replaces legacy MongoDB db.trial_requests (Task C+G, Phase F-Zero).
Field mapping from the marketing form:
  name    → contact_first_name (stored as full name; contact_last_name = "")
  company → org_name
  email   → contact_email
  state   → jurisdiction  (mapped to compliance.jurisdiction_code enum)
  lots    → appended to notes as "Lots: <value>"
  phone   → contact_phone
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, field_validator

from db_postgres.repos.trial_request_repo import (
    ALL_PG_STATUSES,
    approve_trial_request,
    create_trial_request,
    get_trial_request_by_id,
    list_trial_requests as _pg_list,
    reject_trial_request,
    update_trial_request as _pg_update,
    _map_jurisdiction,
)
from database import db
from utils.auth import get_current_user
from utils.email import send_email_async
from utils.helpers import create_user_notification
from utils.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"
RECAPTCHA_MIN_SCORE = 0.5

TRIAL_CONTACT_EMAIL = os.getenv(
    "TRIAL_REQUEST_EMAIL", "gagneet@silverfoxtechnologies.com.au"
)
TRIAL_CONTACT_NAME = os.getenv("TRIAL_REQUEST_NAME", "Silverfox Technologies")


# ── Pydantic models ────────────────────────────────────────────────────────────

class TrialRequestBody(BaseModel):
    name: str
    company: str
    email: EmailStr
    phone: str = ""
    lots: str = ""
    state: str = ""
    message: str = ""
    captcha_token: str

    @field_validator("name", "company")
    @classmethod
    def not_empty(cls, v: str) -> str:
        """Generated function header.

        Function: TrialRequestBody.not_empty
        Path: backend/routers/trial_request.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        v = v.strip()
        if not v:
            raise ValueError("Field cannot be empty")
        if len(v) > 200:
            raise ValueError("Field too long")
        return v

    @field_validator("message")
    @classmethod
    def sanitise_message(cls, v: str) -> str:
        """Generated function header.

        Function: TrialRequestBody.sanitise_message
        Path: backend/routers/trial_request.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return v.strip()[:2000]


class TrialRequestPatch(BaseModel):
    notes: Optional[str] = None

    @field_validator("notes")
    @classmethod
    def trim_notes(cls, v: Optional[str]) -> Optional[str]:
        """Generated function header.

        Function: TrialRequestPatch.trim_notes
        Path: backend/routers/trial_request.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return v.strip()[:4000] if v else v


class TrialRequestReject(BaseModel):
    reason: str = ""

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, v: str) -> str:
        """Generated function header.

        Function: TrialRequestReject.trim_reason
        Path: backend/routers/trial_request.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return v.strip()[:1000]


class TrialRequestApprove(BaseModel):
    """Optional links set by the approval workflow (populated after tenant + invite creation)."""
    created_tenant_id: Optional[UUID] = None
    invitation_id: Optional[UUID] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _verify_recaptcha(token: str, remote_ip: str) -> bool:
    """Return True if the reCAPTCHA v3 token meets our score threshold."""
    if not RECAPTCHA_SECRET:
        logger.warning("RECAPTCHA_SECRET_KEY not configured — skipping verification")
        return True
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                RECAPTCHA_VERIFY_URL,
                data={"secret": RECAPTCHA_SECRET, "response": token, "remoteip": remote_ip},
            )
        data = resp.json()
        if not data.get("success"):
            logger.warning("reCAPTCHA verification failed: %s", data.get("error-codes"))
            return False
        score = data.get("score", 0)
        logger.info("reCAPTCHA score %.2f for %s", score, remote_ip)
        return score >= RECAPTCHA_MIN_SCORE
    except Exception as exc:
        logger.error("reCAPTCHA request error: %s", exc)
        return False


def _build_email_html(body: TrialRequestBody) -> str:
    """Generated function header.

    Function: _build_email_html
    Path: backend/routers/trial_request.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"""
<div style="font-family:Inter,sans-serif;max-width:600px;margin:0 auto;background:#f8faf8;border-radius:8px;overflow:hidden;">
  <div style="background:#2F4F4F;padding:24px 32px;">
    <h2 style="color:#F2CC8F;margin:0;font-size:22px;">New StrataOS Trial Request</h2>
    <p style="color:rgba(255,255,255,0.7);margin:4px 0 0;font-size:13px;">Submitted via StrataOS marketing page</p>
  </div>
  <div style="padding:28px 32px;background:#fff;">
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <tr><td style="padding:8px 0;color:#666;width:130px;">Name</td><td style="padding:8px 0;font-weight:600;">{body.name}</td></tr>
      <tr><td style="padding:8px 0;color:#666;">Company</td><td style="padding:8px 0;font-weight:600;">{body.company}</td></tr>
      <tr><td style="padding:8px 0;color:#666;">Email</td><td style="padding:8px 0;"><a href="mailto:{body.email}" style="color:#2F4F4F;">{body.email}</a></td></tr>
      <tr><td style="padding:8px 0;color:#666;">Phone</td><td style="padding:8px 0;">{body.phone or '—'}</td></tr>
      <tr><td style="padding:8px 0;color:#666;">Number of lots</td><td style="padding:8px 0;">{body.lots or '—'}</td></tr>
      <tr><td style="padding:8px 0;color:#666;">State / Territory</td><td style="padding:8px 0;">{body.state or '—'}</td></tr>
    </table>
    {f'<div style="margin-top:16px;padding:14px;background:#f8faf8;border-radius:6px;font-size:14px;color:#333;"><strong>Message:</strong><br>{body.message}</div>' if body.message else ''}
  </div>
  <div style="padding:16px 32px;background:#f8faf8;font-size:12px;color:#888;">
    StrataOS · Silverfox Technologies · <a href="mailto:gagneet@silverfoxtechnologies.com.au" style="color:#2F4F4F;">Gagneet Singh</a>
  </div>
</div>
"""


def _require_super_admin(current_user: dict):
    """Generated function header.

    Function: _require_super_admin
    Path: backend/routers/trial_request.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    if _role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin only")


def _serialize_row(row: dict) -> dict:
    """Make Postgres row JSON-serialisable (UUID → str, datetime → ISO string)."""
    from uuid import UUID as _UUID
    out = {}
    for k, v in row.items():
        if isinstance(v, _UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ── POST /public/trial-request ─────────────────────────────────────────────────

@router.post("/public/trial-request")
@limiter.limit("3/minute")
async def submit_trial_request(request: Request, body: TrialRequestBody):
    """
    Accept a trial request from the marketing page.
    - Verifies reCAPTCHA v3 token
    - Persists lead to Postgres core.trial_requests (idempotent per email)
    - Emails the platform contact address
    - Creates bell notifications for all active super_admin users
    """
    client_ip = request.client.host if request.client else None

    if not await _verify_recaptcha(body.captcha_token, client_ip or "unknown"):
        raise HTTPException(status_code=400, detail="Captcha verification failed. Please try again.")

    # Build notes from lots field (not in PG schema as standalone column)
    notes_parts = []
    if body.lots:
        notes_parts.append(f"Lots: {body.lots}")
    notes = "; ".join(notes_parts)

    try:
        row = await create_trial_request(
            org_name=body.company,
            contact_name=body.name,
            contact_email=str(body.email),
            contact_phone=body.phone,
            jurisdiction=_map_jurisdiction(body.state),
            message=body.message,
            notes=notes,
            source_ip=client_ip,
        )
        request_id = str(row.get("request_id", ""))
        is_duplicate = row.get("_duplicate", False)
        logger.info(
            "Trial request %s for %s <%s>",
            "duplicate — existing returned" if is_duplicate else "persisted",
            body.company,
            body.email,
        )
    except Exception as exc:
        logger.error("Trial request DB insert failed: %s", exc)
        request_id = ""

    # Email notification — non-fatal
    subject = f"StrataOS Trial Request — {body.company} ({body.lots or '?'} lots)"
    html = _build_email_html(body)
    text_body = (
        f"New StrataOS trial request\n\n"
        f"Name: {body.name}\nCompany: {body.company}\nEmail: {body.email}\n"
        f"Phone: {body.phone}\nLots: {body.lots}\nState: {body.state}\n"
        f"Message: {body.message}"
    )
    try:
        await send_email_async(
            to_email=TRIAL_CONTACT_EMAIL,
            subject=subject,
            html_content=html,
            text_content=text_body,
        )
        logger.info("Trial request email sent for %s <%s>", body.company, body.email)
    except Exception as exc:
        logger.error("Trial request email failed: %s", exc)

    # Bell notifications for all active super_admins — non-fatal
    try:
        admins = await db.users.find(
            {"role": "super_admin", "is_active": True, "is_approved": True},
            {"_id": 0, "id": 1},
        ).to_list(50)
        ts = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
        notif_message = (
            f"{body.name} from {body.company} requested a StrataOS walkthrough "
            f"({body.lots or '?'} lots, {body.state or '?'}) — {ts}"
        )
        for admin in admins:
            await create_user_notification(
                user_id=admin["id"],
                title="New Trial Request",
                message=notif_message,
                notification_type="trial_request",
                link="/admin/leads",
            )
    except Exception as exc:
        logger.error("Trial request notification failed: %s", exc)

    return {"status": "ok", "message": "Thank you! We will be in touch within 1 business day."}


# ── GET /admin/trial-requests ─────────────────────────────────────────────────

@router.get("/admin/trial-requests")
async def list_trial_requests_endpoint(
        status: Optional[str] = Query(None),
        from_date: Optional[str] = Query(None),
        to_date: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        limit: int = Query(25, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_trial_requests_endpoint
    Path: backend/routers/trial_request.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_super_admin(current_user)

    if status and status not in ALL_PG_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of: {', '.join(sorted(ALL_PG_STATUSES))}")

    # Parse and validate date strings here so invalid input → 422, not a Postgres 500.
    parsed_from: Optional[datetime] = None
    parsed_to: Optional[datetime] = None
    if from_date:
        try:
            parsed_from = datetime.fromisoformat(from_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid from_date — expected ISO 8601 format")
    if to_date:
        try:
            parsed_to = datetime.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid to_date — expected ISO 8601 format")

    result = await _pg_list(
        status=status,
        from_date=parsed_from,
        to_date=parsed_to,
        search=search,
        page=page,
        limit=limit,
    )

    return {
        "data": [_serialize_row(r) for r in result["data"]],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": result["total"],
            "total_pages": max(1, -(-result["total"] // limit)),
        },
        "counts": result["counts"],
    }


# ── PATCH /admin/trial-requests/{request_id} ──────────────────────────────────

@router.patch("/admin/trial-requests/{request_id}")
async def patch_trial_request(
        request_id: str,
        body: TrialRequestPatch,
        current_user: dict = Depends(get_current_user),
):
    """Update mutable fields (notes only). Use /approve or /reject for status transitions."""
    _require_super_admin(current_user)

    row = await _pg_update(request_id, notes=body.notes)
    if row is None:
        raise HTTPException(status_code=404, detail="Trial request not found")

    return _serialize_row(row)


# ── POST /admin/trial-requests/{request_id}/approve ───────────────────────────

@router.post("/admin/trial-requests/{request_id}/approve")
async def approve_trial_request_endpoint(
        request_id: str,
        body: TrialRequestApprove,
        current_user: dict = Depends(get_current_user),
):
    """Mark a trial request as approved.

    Optionally links a created_tenant_id and invitation_id if the caller has
    already provisioned these (e.g. via /sm-organisations and /admin/invitations).
    Only open (status='submitted') requests can be approved.
    """
    _require_super_admin(current_user)

    reviewer_id = current_user.get("user_uuid") or current_user.get("id")
    if not reviewer_id:
        raise HTTPException(status_code=400, detail="User ID not found in authentication context.")
    row = await approve_trial_request(
        request_id,
        reviewed_by=str(reviewer_id),
        created_tenant_id=str(body.created_tenant_id) if body.created_tenant_id else None,
        invitation_id=str(body.invitation_id) if body.invitation_id else None,
    )
    if row is None:
        # Either not found or already closed
        existing = await get_trial_request_by_id(request_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Trial request not found")
        raise HTTPException(
            status_code=409,
            detail=f"Trial request is already {existing.get('status')} and cannot be approved.",
        )

    logger.info("Trial request %s approved by %s", request_id, reviewer_id)
    return _serialize_row(row)


# ── POST /admin/trial-requests/{request_id}/reject ────────────────────────────

@router.post("/admin/trial-requests/{request_id}/reject")
async def reject_trial_request_endpoint(
        request_id: str,
        body: TrialRequestReject,
        current_user: dict = Depends(get_current_user),
):
    """Mark a trial request as rejected with an optional reason.

    Only open (status='submitted') requests can be rejected.
    """
    _require_super_admin(current_user)

    reviewer_id = current_user.get("user_uuid") or current_user.get("id")
    if not reviewer_id:
        raise HTTPException(status_code=400, detail="User ID not found in authentication context.")
    row = await reject_trial_request(
        request_id,
        reviewed_by=str(reviewer_id),
        reason=body.reason,
    )
    if row is None:
        existing = await get_trial_request_by_id(request_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Trial request not found")
        raise HTTPException(
            status_code=409,
            detail=f"Trial request is already {existing.get('status')} and cannot be rejected.",
        )

    logger.info("Trial request %s rejected by %s — reason: %s", request_id, reviewer_id, body.reason)
    return _serialize_row(row)
