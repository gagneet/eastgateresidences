"""
TOTP (Time-based One-Time Password) router — P0-02.

Provides 2FA setup and verification for high-privilege roles.
Soft enforcement only — does not lock out existing accounts.
Accounts where totp_enabled=False continue to log in normally.

Endpoints:
  POST /auth/totp/setup      — generate secret + QR code (does not enable yet)
  POST /auth/totp/verify     — verify code and enable 2FA
  GET  /auth/totp/status     — current 2FA state for the authenticated user
  POST /auth/totp/disable    — disable 2FA (requires valid TOTP code)
  POST /auth/totp/challenge  — exchange totp_pending token + code for full JWT
"""
from __future__ import annotations

import base64
import io
import uuid

import asyncio
import bcrypt
import logging
import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional

from database import db
from utils.auth import get_current_user, create_token
from utils.rate_limit import rate_limit
from utils.crypto import encrypt_sensitive, decrypt_sensitive
from utils.helpers import create_audit_log, get_current_utc_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/totp", tags=["TOTP"])

TOTP_REQUIRED_ROLES = {"super_admin", "ec_member", "strata_admin", "strata_manager"}
TOTP_ISSUER = "EastGate Residences"
BACKUP_CODE_COUNT = 8


# ─── Request / response models ────────────────────────────────────────────────

class TOTPSetupResponse(BaseModel):
    secret: str
    qr_code_uri: str
    qr_code_image_base64: str  # PNG as base64 — display in <img src="data:image/png;base64,…">
    backup_codes: List[str]  # Shown once — store safely — NOT stored in plain text


class TOTPVerifyRequest(BaseModel):
    code: str  # 6-digit TOTP code from authenticator app


class TOTPStatusResponse(BaseModel):
    enabled: bool
    verified_at: Optional[str]
    backup_codes_remaining: int
    role_requires_totp: bool


class TOTPChallengeRequest(BaseModel):
    totp_token: str  # Short-lived pending token from login response
    code: str  # 6-digit TOTP code OR 8-char backup code
    is_backup_code: bool = False  # True → verify against backup_codes_hashed list


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/setup", response_model=TOTPSetupResponse)
@rate_limit("rate_limit_totp_setup", 10)
async def setup_totp(request: Request, current_user: dict = Depends(get_current_user)):
    """
    Generate a TOTP secret and QR code for the authenticated user.

    Does NOT enable 2FA — the user must call /verify with a valid code first.
    Calling /setup again replaces any pending (unverified) secret.
    """
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)

    user_email = current_user.get("email", current_user.get("id", "user"))
    uri = totp.provisioning_uri(name=user_email, issuer_name=TOTP_ISSUER)

    # Build QR code PNG as base64
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        # qrcode not installed — return empty string; caller can use the URI directly
        qr_b64 = ""

    # Generate backup codes (plain text shown once; stored hashed)
    backup_codes = [
        uuid.uuid4().hex[:8].upper() for _ in range(BACKUP_CODE_COUNT)
    ]
    hashed_codes = [
        bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()
        for code in backup_codes
    ]

    # Store secret encrypted; mark as not-yet-enabled
    await db.users.update_one(
        {"id": current_user["id"]},
        {
            "$set": {
                "totp_secret_encrypted": encrypt_sensitive(secret),
                "totp_enabled": False,
                "totp_backup_codes_hashed": hashed_codes,
            }
        },
    )

    return TOTPSetupResponse(
        secret=secret,
        qr_code_uri=uri,
        qr_code_image_base64=qr_b64,
        backup_codes=backup_codes,  # Show once; not stored plain text
    )


@router.post("/verify")
@rate_limit("rate_limit_totp_verify", 20)
async def verify_totp(
        request: Request,
        body: TOTPVerifyRequest,
        current_user: dict = Depends(get_current_user),
):
    """
    Verify a TOTP code and enable 2FA for the account.

    Must be called after /setup with a code from the authenticator app.
    On success, sets totp_enabled=True on the user document.
    """
    user = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
    if not user or not user.get("totp_secret_encrypted"):
        raise HTTPException(status_code=400, detail="TOTP not set up. Call /auth/totp/setup first.")

    secret = decrypt_sensitive(user["totp_secret_encrypted"])
    if not secret:
        raise HTTPException(status_code=500, detail="TOTP secret could not be decrypted.")

    totp = pyotp.TOTP(secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid TOTP code. Check your authenticator app and try again.")

    now = get_current_utc_iso()
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"totp_enabled": True, "totp_verified_at": now}},
    )

    asyncio.create_task(
        create_audit_log(
            action="totp_enabled",
            resource_type="user",
            resource_id=current_user["id"],
            user_id=current_user["id"],
            user_name=current_user.get("full_name", current_user.get("email", "")),
        )
    ) if _asyncio_available() else None

    return {"success": True, "message": "Two-factor authentication enabled."}


@router.get("/status", response_model=TOTPStatusResponse)
async def get_totp_status(current_user: dict = Depends(get_current_user)):
    """Return current 2FA state for the authenticated user."""
    user = await db.users.find_one({"id": current_user["id"]}, {"_id": 0}) or {}
    backup_count = len([c for c in user.get("totp_backup_codes_hashed", []) if c])
    return TOTPStatusResponse(
        enabled=bool(user.get("totp_enabled")),
        verified_at=user.get("totp_verified_at"),
        backup_codes_remaining=backup_count,
        role_requires_totp=current_user.get("role") in TOTP_REQUIRED_ROLES,
    )


@router.post("/disable")
@rate_limit("rate_limit_totp_disable", 10)
async def disable_totp(
        request: Request,
        body: TOTPVerifyRequest,
        current_user: dict = Depends(get_current_user),
):
    """
    Disable TOTP on the account.

    Requires a currently valid TOTP code for safety.
    """
    user = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
    if not user or not user.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="TOTP is not enabled on this account.")

    secret = decrypt_sensitive(user.get("totp_secret_encrypted", ""))
    if not secret or not pyotp.TOTP(secret).verify(body.code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid TOTP code.")

    await db.users.update_one(
        {"id": current_user["id"]},
        {
            "$set": {"totp_enabled": False},
            "$unset": {"totp_secret_encrypted": "", "totp_backup_codes_hashed": ""},
        },
    )

    asyncio.create_task(
        create_audit_log(
            action="totp_disabled",
            resource_type="user",
            resource_id=current_user["id"],
            user_id=current_user["id"],
            user_name=current_user.get("full_name", current_user.get("email", "")),
        )
    ) if _asyncio_available() else None

    return {"success": True, "message": "Two-factor authentication disabled."}


@router.post("/challenge")
@rate_limit("rate_limit_totp_challenge", 10)
async def totp_challenge(request: Request, body: TOTPChallengeRequest):
    """
    Exchange a short-lived totp_pending token + TOTP code for a full session JWT.

    Called by the login flow when the server responded with status=totp_required.
    On success, returns the same token shape as POST /auth/login.
    """
    from config import JWT_SECRET, JWT_ALGORITHM
    import jwt as pyjwt

    try:
        payload = pyjwt.decode(
            body.totp_token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="TOTP challenge token has expired. Please log in again.")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid TOTP challenge token.")

    if payload.get("type") != "totp_pending":
        raise HTTPException(status_code=400, detail="Not a TOTP challenge token.")

    user_id = payload.get("sub")
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")

    if not user.get("totp_enabled") or not user.get("totp_secret_encrypted"):
        raise HTTPException(status_code=400, detail="TOTP not enabled on this account.")

    if body.is_backup_code:
        # Verify against the hashed backup codes list and consume the matching one
        backup_hashes: list[str] = user.get("totp_backup_codes_hashed", [])
        matched_index = None
        for i, h in enumerate(backup_hashes):
            try:
                if h and bcrypt.checkpw(body.code.upper().encode(), h.encode()):
                    matched_index = i
                    break
            except Exception:
                continue
        if matched_index is None:
            raise HTTPException(status_code=400, detail="Invalid backup code.")
        # Remove the used backup code so it cannot be reused
        backup_hashes.pop(matched_index)
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"totp_backup_codes_hashed": backup_hashes}},
        )
    else:
        secret = decrypt_sensitive(user["totp_secret_encrypted"])
        if not secret or not pyotp.TOTP(secret).verify(body.code, valid_window=1):
            raise HTTPException(status_code=400, detail="Invalid TOTP code.")

    # Issue full JWT
    token = create_token(
        user_id=user["id"],
        email=user.get("email", ""),
        role=user.get("role", "owner"),
        building_id=user.get("building_id"),
        organisation_id=user.get("organisation_id", "org-silverfox-001"),
    )
    # Strip sensitive fields before returning
    _SENSITIVE = {"hashed_password", "totp_secret_encrypted", "totp_backup_codes_hashed", "mail_password"}
    safe_user = {k: v for k, v in user.items() if k not in _SENSITIVE}
    return {"token": token, "user": safe_user}


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _asyncio_available() -> bool:
    """Generated function header.

    Function: _asyncio_available
    Path: backend/routers/totp.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
