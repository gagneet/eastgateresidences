"""SM-Organisation administration — Postgres-only.

Implements the spec from
``docs/architecture/onboarding/02_endpoint_contracts.md`` §2.1.

Endpoints
---------
POST   /admin/sm-organisations
    SA directly creates an SM-managed tenant + sends invitation to its
    primary admin.  No trial-request needed.

POST   /admin/sm-organisations/self-managed
    SA creates a self-managed tenant + scheme + EC-admin invitation in a
    single atomic call.  The 1:1 tenant↔scheme invariant is enforced by
    the trigger added in migration 0028.

GET    /admin/sm-organisations
    List SM organisations.  Optional ``?include_self_managed=true``.

GET    /admin/sm-organisations/{tenant_id}
    Single org detail.

GET    /admin/sm-organisations/{tenant_id}/schemes
    Schemes belonging to this org.

GET    /admin/sm-organisations/{tenant_id}/members
    Active role assignments under this tenant.

PATCH  /admin/sm-organisations/{tenant_id}
    Update org details (tenant_name, legal_name, abn, status).

All endpoints are super_admin only.
"""
from __future__ import annotations

import hashlib
import html
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db_postgres.session import async_session_context, set_tenant
from utils.auth import get_current_user, effective_role
from utils.email import send_email_async
from models.user import UserRole

logger = logging.getLogger(__name__)
router = APIRouter(tags=["SM Organisations"])

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"
_VALID_INVITATION_ROLES = {UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER}
_VALID_JURISDICTIONS = {"ACT", "NSW", "VIC", "QLD", "WA", "SA", "TAS", "NT"}


# ──────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────────────────────────────────────

class CreateSmOrgRequest(BaseModel):
    tenant_name: str
    legal_name: Optional[str] = None
    abn: Optional[str] = None
    jurisdiction: str = "ACT"
    primary_admin_email: EmailStr
    primary_admin_first_name: str
    primary_admin_last_name: str
    invitation_role: str = "strata_admin"
    invitation_expires_days: int = Field(default=14, ge=1, le=30)

    @field_validator("jurisdiction")
    @classmethod
    def _check_jurisdiction(cls, v: str) -> str:
        """Generated function header.

        Function: CreateSmOrgRequest._check_jurisdiction
        Path: backend/routers/sm_organisations.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        v = v.upper()
        if v not in _VALID_JURISDICTIONS:
            raise ValueError(f"jurisdiction must be one of {sorted(_VALID_JURISDICTIONS)}")
        return v

    @field_validator("invitation_role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        """Generated function header.

        Function: CreateSmOrgRequest._check_role
        Path: backend/routers/sm_organisations.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v not in _VALID_INVITATION_ROLES:
            raise ValueError(f"invitation_role must be one of {sorted(_VALID_INVITATION_ROLES)}")
        return v


class CreateSelfManagedRequest(BaseModel):
    scheme_number: str
    scheme_name: str
    scheme_legal_name: Optional[str] = None
    scheme_abn: Optional[str] = None
    jurisdiction: str = "ACT"
    ec_admin_email: EmailStr
    ec_admin_first_name: str
    ec_admin_last_name: str
    invitation_role: str = "strata_admin"
    invitation_expires_days: int = Field(default=14, ge=1, le=30)

    @field_validator("jurisdiction")
    @classmethod
    def _check_jurisdiction(cls, v: str) -> str:
        """Generated function header.

        Function: CreateSelfManagedRequest._check_jurisdiction
        Path: backend/routers/sm_organisations.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        v = v.upper()
        if v not in _VALID_JURISDICTIONS:
            raise ValueError(f"jurisdiction must be one of {sorted(_VALID_JURISDICTIONS)}")
        return v

    @field_validator("invitation_role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        """Generated function header.

        Function: CreateSelfManagedRequest._check_role
        Path: backend/routers/sm_organisations.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v not in _VALID_INVITATION_ROLES:
            raise ValueError(f"invitation_role must be one of {sorted(_VALID_INVITATION_ROLES)}")
        return v


class UpdateOrgRequest(BaseModel):
    tenant_name: Optional[str] = None
    legal_name: Optional[str] = None
    abn: Optional[str] = None
    status: Optional[str] = None  # active|suspended|archived

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: Optional[str]) -> Optional[str]:
        """Generated function header.

        Function: UpdateOrgRequest._check_status
        Path: backend/routers/sm_organisations.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if v is None:
            return v
        if v not in {"active", "inactive", "archived"}:
            raise ValueError("status must be one of: active, inactive, archived")
        return v


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _require_super_admin(user: dict) -> None:
    """Generated function header.

    Function: _require_super_admin
    Path: backend/routers/sm_organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(user) != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super admin access required.")


def _generate_invite_token() -> tuple[str, bytes]:
    """Generated function header.

    Function: _generate_invite_token
    Path: backend/routers/sm_organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).digest()
    return raw, digest


def _build_invitation_email(*, recipient_name: str, claim_url: str, role: str,
                            org_name: str, expires_days: int) -> str:
    # SA-supplied strings are HTML-escaped; only claim_url is trusted (we generated it).
    """Generated function header.

    Function: _build_invitation_email
    Path: backend/routers/sm_organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"""
<div style="font-family:Inter,sans-serif;max-width:600px;margin:0 auto;">
  <h2 style="color:#2F4F4F;">Welcome to StrataOS</h2>
  <p>Hi {html.escape(recipient_name)},</p>
  <p>You've been invited to join <strong>{html.escape(org_name)}</strong> on StrataOS as a
     <strong>{html.escape(role.replace('_', ' ').title())}</strong>.</p>
  <p style="margin:32px 0;">
    <a href="{claim_url}"
       style="background:#2F4F4F;color:#fff;padding:12px 24px;border-radius:9999px;text-decoration:none;">
      Set your password &rarr;
    </a>
  </p>
  <p style="color:#666;font-size:13px;">This invitation expires in {expires_days} days.</p>
</div>
"""


async def _abn_already_active(abn: Optional[str]) -> bool:
    """Return True if an active core.tenants row already has this ABN."""
    if not abn:
        return False
    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        result = await session.execute(
            text("SELECT 1 FROM core.tenants WHERE abn = :abn AND status = 'active' LIMIT 1"),
            {"abn": abn},
        )
        return result.scalar() is not None


# ──────────────────────────────────────────────────────────────────────────────
# POST /admin/sm-organisations  — direct create SM-managed tenant
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/admin/sm-organisations", status_code=201)
async def create_sm_organisation(
        body: CreateSmOrgRequest,
        current_user: dict = Depends(get_current_user),
        *,
        _is_test_data: bool = False,
):
    """Create an SM-managed tenant and dispatch its primary admin invitation.

    Single Postgres transaction: tenant insert + invitation insert.
    Email send happens after commit; failures don't roll back.

    ``_is_test_data`` is a keyword-only internal parameter — production HTTP
    callers cannot reach it (FastAPI binds the request body to ``body`` only).
    Tests that call this function directly pass ``_is_test_data=True`` so the
    row is filtered out of the SA building switcher and swept by the pytest
    session-end teardown.
    """
    _require_super_admin(current_user)

    if await _abn_already_active(body.abn):
        raise HTTPException(
            status_code=409,
            detail={"detail": "An active organisation already has this ABN.",
                    "code": "abn_already_active"},
        )

    raw_token, token_hash = _generate_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.invitation_expires_days)

    try:
        async with async_session_context() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :u, true)"),
                {"u": _BYPASS_UUID},
            )

            # 1. Create tenant
            tenant_row = await session.execute(
                text("""
                     INSERT INTO core.tenants
                         (tenant_name, legal_name, abn, is_self_managed, status, is_test_data)
                     VALUES (:name, :legal, :abn, FALSE, 'active', :is_test) RETURNING tenant_id::TEXT
                     """),
                {"name": body.tenant_name, "legal": body.legal_name, "abn": body.abn,
                 "is_test": _is_test_data},
            )
            tenant_id = tenant_row.scalar()

            # 2. Create invitation under the new tenant
            await set_tenant(session, tenant_id)
            inv_row = await session.execute(
                text("""
                     INSERT INTO core.user_invitations
                     (tenant_id, scheme_id, email, invited_role, invited_by,
                      claim_token_sha256, expires_at,
                      prefill_first_name, prefill_last_name)
                     VALUES (:tid, NULL, :email, CAST(:role AS core.user_role), :by,
                             :hash, :exp, :fn, :ln) RETURNING invitation_id::TEXT
                     """),
                {
                    "tid": tenant_id,
                    "email": body.primary_admin_email.strip().lower(),
                    "role": body.invitation_role,
                    "by": current_user["id"],
                    "hash": token_hash,
                    "exp": expires_at,
                    "fn": body.primary_admin_first_name,
                    "ln": body.primary_admin_last_name,
                },
            )
            invitation_id = inv_row.scalar()
    except IntegrityError as e:
        msg = str(e.orig).lower() if e.orig else str(e).lower()
        if "tenants_abn_unique_active" in msg:
            raise HTTPException(
                status_code=409,
                detail={"detail": "Active tenant with this ABN was created concurrently.",
                        "code": "abn_already_active"},
            )
        logger.exception("Unexpected integrity error creating SM org")
        raise HTTPException(status_code=500, detail="Database integrity error.")

    # Send invitation email — best-effort
    email_failed = False
    claim_url = f"/onboarding/claim/{raw_token}"
    try:
        await send_email_async(
            to_email=body.primary_admin_email,
            subject=f"You've been invited to manage {body.tenant_name} on StrataOS",
            html_content=_build_invitation_email(
                recipient_name=body.primary_admin_first_name,
                claim_url=claim_url,
                role=body.invitation_role,
                org_name=body.tenant_name,
                expires_days=body.invitation_expires_days,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Invitation email failed for %s: %s", body.primary_admin_email, exc)
        email_failed = True

    response = {
        "tenant_id": tenant_id,
        "invitation_id": invitation_id,
        "message": f"SM organisation '{body.tenant_name}' created. "
                   f"Invitation sent to {body.primary_admin_email}.",
    }
    if email_failed:
        return {**response,
                "warning": "Invitation email failed to send. Use the resend endpoint.",
                "code": "invitation_email_failed"}
    return response


# ──────────────────────────────────────────────────────────────────────────────
# POST /admin/sm-organisations/self-managed  — tenant + scheme + invite atomic
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/admin/sm-organisations/self-managed", status_code=201)
async def create_self_managed_scheme(
        body: CreateSelfManagedRequest,
        current_user: dict = Depends(get_current_user),
        *,
        _is_test_data: bool = False,
):
    """Create a self-managed scheme: tenant (is_self_managed=TRUE) + scheme + invitation.

    All three rows in a single Postgres transaction. The 1:1 invariant is
    enforced by the schemes_self_managed_one_per_tenant trigger.

    ``_is_test_data`` is a keyword-only internal parameter — production HTTP
    callers cannot reach it. Tests pass ``_is_test_data=True`` so the tenant
    AND its scheme get the test flag set; both are filtered out of the SA
    building switcher and swept by the pytest session-end teardown.
    """
    _require_super_admin(current_user)

    if await _abn_already_active(body.scheme_abn):
        raise HTTPException(
            status_code=409,
            detail={"detail": "An active organisation already has this ABN.",
                    "code": "abn_already_active"},
        )

    raw_token, token_hash = _generate_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.invitation_expires_days)

    try:
        async with async_session_context() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :u, true)"),
                {"u": _BYPASS_UUID},
            )

            # 1. Tenant: name = scheme name (self-managed convention)
            tenant_row = await session.execute(
                text("""
                     INSERT INTO core.tenants
                         (tenant_name, legal_name, abn, is_self_managed, status, is_test_data)
                     VALUES (:name, :legal, :abn, TRUE, 'active', :is_test) RETURNING tenant_id::TEXT
                     """),
                {
                    "name": body.scheme_name,
                    "legal": body.scheme_legal_name,
                    "abn": body.scheme_abn,
                    "is_test": _is_test_data,
                },
            )
            tenant_id = tenant_row.scalar()

            # 2. Scheme under that tenant
            await set_tenant(session, tenant_id)
            scheme_row = await session.execute(
                text("""
                     INSERT INTO core.schemes
                     (tenant_id, jurisdiction, scheme_number, scheme_name,
                      legal_name, abn, status, is_test_data)
                     VALUES (:tid, CAST(:jur AS compliance.jurisdiction_code), :num, :name,
                             :legal, :abn, 'active', :is_test) RETURNING scheme_id::TEXT
                     """),
                {
                    "tid": tenant_id,
                    "jur": body.jurisdiction,
                    "num": body.scheme_number.strip(),
                    "name": body.scheme_name.strip(),
                    "legal": body.scheme_legal_name,
                    "abn": body.scheme_abn,
                    "is_test": _is_test_data,
                },
            )
            scheme_id = scheme_row.scalar()

            # 3. Invitation for the EC admin, scoped to this scheme
            inv_row = await session.execute(
                text("""
                     INSERT INTO core.user_invitations
                     (tenant_id, scheme_id, email, invited_role, invited_by,
                      claim_token_sha256, expires_at,
                      prefill_first_name, prefill_last_name)
                     VALUES (:tid, :sid, :email, CAST(:role AS core.user_role), :by,
                             :hash, :exp, :fn, :ln) RETURNING invitation_id::TEXT
                     """),
                {
                    "tid": tenant_id,
                    "sid": scheme_id,
                    "email": body.ec_admin_email.strip().lower(),
                    "role": body.invitation_role,
                    "by": current_user["id"],
                    "hash": token_hash,
                    "exp": expires_at,
                    "fn": body.ec_admin_first_name,
                    "ln": body.ec_admin_last_name,
                },
            )
            invitation_id = inv_row.scalar()
    except IntegrityError as e:
        msg = str(e.orig).lower() if e.orig else str(e).lower()
        if "tenants_abn_unique_active" in msg:
            raise HTTPException(
                status_code=409,
                detail={"detail": "Active tenant with this ABN was created concurrently.",
                        "code": "abn_already_active"},
            )
        if "schemes_pkey" in msg or "scheme_number" in msg:
            raise HTTPException(
                status_code=409,
                detail={"detail": f"Scheme '{body.scheme_number}' already exists in {body.jurisdiction}.",
                        "code": "scheme_number_taken_in_jurisdiction"},
            )
        if "self-managed tenant" in msg or "schemes_self_managed_one_per_tenant" in msg:
            raise HTTPException(
                status_code=409,
                detail={"detail": "This self-managed tenant already has a scheme.",
                        "code": "self_managed_already_has_scheme"},
            )
        logger.exception("Unexpected integrity error creating self-managed scheme")
        raise HTTPException(status_code=500, detail="Database integrity error.")

    email_failed = False
    claim_url = f"/onboarding/claim/{raw_token}"
    try:
        await send_email_async(
            to_email=body.ec_admin_email,
            subject=f"You've been invited to manage {body.scheme_name} on StrataOS",
            html_content=_build_invitation_email(
                recipient_name=body.ec_admin_first_name,
                claim_url=claim_url,
                role=body.invitation_role,
                org_name=body.scheme_name,
                expires_days=body.invitation_expires_days,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Invitation email failed for %s: %s", body.ec_admin_email, exc)
        email_failed = True

    response = {
        "tenant_id": tenant_id,
        "scheme_id": scheme_id,
        "invitation_id": invitation_id,
        "message": f"Self-managed scheme '{body.scheme_name}' "
                   f"({body.scheme_number}) created.",
    }
    if email_failed:
        return {**response,
                "warning": "Invitation email failed to send.",
                "code": "invitation_email_failed"}
    return response


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/sm-organisations  — list orgs
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/admin/sm-organisations")
async def list_sm_organisations(
        include_self_managed: bool = Query(default=False),
        status: Optional[str] = Query(default=None),
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_sm_organisations
    Path: backend/routers/sm_organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_super_admin(current_user)

    where = ["COALESCE(is_test_data, FALSE) = FALSE"]
    params: dict = {}
    if not include_self_managed:
        where.append("is_self_managed = FALSE")
    if status:
        where.append("status = :status")
        params["status"] = status
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        rows = await session.execute(
            text(f"""
                SELECT tenant_id::TEXT, tenant_name, legal_name, abn,
                       is_self_managed, status::TEXT, created_at::TEXT,
                       (SELECT COUNT(*) FROM core.schemes s WHERE s.tenant_id = t.tenant_id AND s.status = 'active') AS scheme_count,
                       (SELECT COUNT(*) FROM core.user_role_assignments r WHERE r.tenant_id = t.tenant_id AND r.is_active) AS member_count
                FROM core.tenants t
                {where_sql}
                ORDER BY created_at DESC
            """),
            params,
        )
        results = []
        for r in rows.mappings():
            results.append(dict(r))
        return {"items": results, "total": len(results)}


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/sm-organisations/{tenant_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/admin/sm-organisations/{tenant_id}")
async def get_sm_organisation(
        tenant_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: get_sm_organisation
    Path: backend/routers/sm_organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_super_admin(current_user)

    try:
        UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id.")

    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        row = await session.execute(
            text("""
                 SELECT tenant_id::TEXT, tenant_name,
                        legal_name,
                        abn,
                        is_self_managed,
                        status::TEXT, created_at::TEXT, created_from_trial_id::TEXT
                 FROM core.tenants
                 WHERE tenant_id = :tid
                 """),
            {"tid": tenant_id},
        )
        rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    return dict(rec)


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/sm-organisations/{tenant_id}/schemes
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/admin/sm-organisations/{tenant_id}/schemes")
async def list_sm_organisation_schemes(
        tenant_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_sm_organisation_schemes
    Path: backend/routers/sm_organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_super_admin(current_user)

    try:
        UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id.")

    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        rows = await session.execute(
            text("""
                 SELECT scheme_id::TEXT, scheme_number,
                        scheme_name,
                        legal_name,
                        abn,
                        jurisdiction::TEXT, status::TEXT, created_at::TEXT
                 FROM core.schemes
                 WHERE tenant_id = :tid
                 ORDER BY scheme_number
                 """),
            {"tid": tenant_id},
        )
        return {"items": [dict(r) for r in rows.mappings()]}


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/sm-organisations/{tenant_id}/members
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/admin/sm-organisations/{tenant_id}/members")
async def list_sm_organisation_members(
        tenant_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_sm_organisation_members
    Path: backend/routers/sm_organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_super_admin(current_user)

    try:
        UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id.")

    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        rows = await session.execute(
            text("""
                 SELECT u.user_id::TEXT, u.email::TEXT, u.full_name,
                        r.role::TEXT, r.scheme_id::TEXT, s.scheme_name,
                        r.is_active,
                        r.granted_at::TEXT
                 FROM core.user_role_assignments r
                          JOIN core.users u ON u.user_id = r.user_id
                          LEFT JOIN core.schemes s ON s.scheme_id = r.scheme_id
                 WHERE r.tenant_id = :tid
                   AND r.is_active
                 ORDER BY u.full_name
                 """),
            {"tid": tenant_id},
        )
        return {"items": [dict(r) for r in rows.mappings()]}


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /admin/sm-organisations/{tenant_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.patch("/admin/sm-organisations/{tenant_id}")
async def update_sm_organisation(
        tenant_id: str,
        body: UpdateOrgRequest,
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: update_sm_organisation
    Path: backend/routers/sm_organisations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_super_admin(current_user)

    try:
        UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id.")

    sets = []
    params: dict = {"tid": tenant_id}
    if body.tenant_name is not None:
        sets.append("tenant_name = :name")
        params["name"] = body.tenant_name
    if body.legal_name is not None:
        sets.append("legal_name = :legal")
        params["legal"] = body.legal_name
    if body.abn is not None:
        sets.append("abn = :abn")
        params["abn"] = body.abn
    if body.status is not None:
        sets.append("status = CAST(:status AS core.record_status)")
        params["status"] = body.status

    if not sets:
        raise HTTPException(status_code=400, detail="No updatable fields supplied.")

    sets.append("updated_at = NOW()")

    try:
        async with async_session_context() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :u, true)"),
                {"u": _BYPASS_UUID},
            )
            row = await session.execute(
                text(f"""
                    UPDATE core.tenants
                       SET {', '.join(sets)}
                     WHERE tenant_id = :tid
                    RETURNING tenant_id::TEXT, tenant_name, status::TEXT
                """),
                params,
            )
            rec = row.mappings().first()
    except IntegrityError as e:
        # e.orig may be None depending on the SA driver layer — fall back
        # to str(e) for the constraint-name match, matching the pattern
        # used by the create handlers above.
        msg = str(e.orig).lower() if e.orig else str(e).lower()
        if "tenants_abn_unique_active" in msg:
            raise HTTPException(
                status_code=409,
                detail={"detail": "Another active org has this ABN.",
                        "code": "abn_already_active"},
            )
        raise

    if not rec:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    return dict(rec)
