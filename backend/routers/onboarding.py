"""Onboarding router — wizard + CSV historical-data import.

# @featuretrace:east-gate-import — East Gate historical data import
# Layer: router
# Data flow: scripts/data_extraction/extract_east_gate_import.py → imports/east_gate_2026_05_03/*.csv → POST /api/onboarding/scheme/{id}/import-* → MongoDB historical collections + Postgres core.ownership_periods/onboarding_sessions step_data (building-scoped).
# Related: backend/seeds/demo_customer.py; docs/architecture/mindmap/03_onboarding_flow.md; docs/architecture/adr/ADR-023_phase_f_zero_reset.md
# Scope: (building-scoped)

Supports the 3-phase "Switching Made Simple" flow described on the
public features page:
  1. Export (data gathered, nothing written to production tables)
  2. Import (scheme + tenant created; managed by this router)
  3. Go Live (scheme marked active; users invited)

Endpoints
---------
POST   /onboarding/scheme/start          — create tenant + scheme, begin wizard
GET    /onboarding/scheme/{session_id}   — wizard progress
PATCH  /onboarding/scheme/{session_id}   — update step data
POST   /onboarding/scheme/{session_id}/lots            — bulk-import lots
POST   /onboarding/scheme/{session_id}/import-historical-financials
POST   /onboarding/scheme/{session_id}/import-owner-transfers
POST   /onboarding/scheme/{session_id}/import-opening-balances
POST   /onboarding/scheme/{session_id}/finalize        — mark scheme active
"""
from __future__ import annotations

import hashlib
import html
import io
import logging
import os
import re
import secrets
import json
from datetime import datetime, timedelta, timezone, date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import db
from request_context import get_ctx_building_id, set_ctx_building_id

from db_postgres.session import async_session_context, set_tenant
from db_postgres.repos import config_repo
from utils.auth import get_current_user, effective_role
from utils.email import send_email_async
from utils.file_scan import scan_upload
from services.onboarding_templates import (
    HISTORICAL_FINANCIALS_FILE_GROUP,
    ONBOARDING_TEMPLATES,
    render_template_csv,
    render_template_xlsx,
)
from utils.tabular_upload import (
    assert_content_type_allowed,
    assert_required_columns,
    parse_tabular_bytes,
)
from models.user import UserRole
from services.financial_core.domain.entities import SchemeRef
from services.financial_core.genesis import (
    derive_building_id_from_scheme_number,
    enable_canonical_cutover_overrides,
    post_import_based_genesis_cutover,
    record_cutover_in_session_step_data,
)
from models.trust_accounting import TrustAccountPhase1Create
from routers.trust_phase1 import create_trust_account_v2_record
from services.financial_service import create_levy_plans_from_onboarding

logger = logging.getLogger(__name__)


def _generate_invite_token() -> tuple[str, bytes]:
    """Generate (raw_token, sha256_hash).

    Raw token is sent in the invitation email. Only the hash is stored
    in core.user_invitations.claim_token_sha256, so a DB compromise
    cannot be used to forge a claim — the same pattern used by
    db_postgres.repos.identity_repo.create_invitation.
    """
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).digest()


def _build_claim_url(raw_token: str) -> str:
    """Build the absolute claim URL the recipient clicks from their email.

    Reads FRONTEND_URL the same way routers/auth.py does — falls back to
    the eastgateresidences.com.au domain so the link is never empty.
    """
    base = os.getenv("FRONTEND_URL", "https://www.eastgateresidences.com.au").rstrip("/")
    return f"{base}/onboarding/claim/{raw_token}"


def _owner_invitation_email_html(*, scheme_name: str, claim_url: str) -> str:
    """HTML body for owner invitations. SA-supplied scheme_name is escaped."""
    return (
        f"<div style='font-family:Inter,sans-serif;max-width:600px;margin:0 auto;'>"
        f"<h2 style='color:#2F4F4F;'>Welcome to your StrataOS owners portal</h2>"
        f"<p>You have been invited to access the residents portal for "
        f"<strong>{html.escape(scheme_name)}</strong>.</p>"
        f"<p style='margin:32px 0;'>"
        f"<a href='{claim_url}' "
        f"style='background:#2F4F4F;color:#fff;padding:12px 24px;"
        f"border-radius:9999px;text-decoration:none;'>"
        f"Set your password &rarr;</a></p>"
        f"<p style='color:#666;font-size:13px;'>"
        f"This invitation expires in 14 days. If the button doesn't work, "
        f"copy and paste this link into your browser:<br>"
        f"<code>{claim_url}</code></p></div>"
    )


def _ec_invitation_email_html(*, scheme_name: str, claim_url: str, role_label: str) -> str:
    """HTML body for EC member invitations."""
    return (
        f"<div style='font-family:Inter,sans-serif;max-width:600px;margin:0 auto;'>"
        f"<h2 style='color:#2F4F4F;'>You've been invited to the Executive Committee</h2>"
        f"<p>You have been invited to join the EC of "
        f"<strong>{html.escape(scheme_name)}</strong> on StrataOS as a "
        f"<strong>{html.escape(role_label)}</strong>.</p>"
        f"<p style='margin:32px 0;'>"
        f"<a href='{claim_url}' "
        f"style='background:#2F4F4F;color:#fff;padding:12px 24px;"
        f"border-radius:9999px;text-decoration:none;'>"
        f"Set your password &rarr;</a></p>"
        f"<p style='color:#666;font-size:13px;'>"
        f"This invitation expires in 14 days. If the button doesn't work, "
        f"copy and paste this link into your browser:<br>"
        f"<code>{claim_url}</code></p></div>"
    )


router = APIRouter(tags=["Onboarding"])


def _scheme_number_identity_keys(scheme_number: str | None) -> set[str]:
    """Return comparable keys for scheme numbers that users enter in different formats."""
    raw = str(scheme_number or "").strip()
    compact = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    keys = {raw.upper()} if raw else set()
    if compact:
        keys.add(compact)
        for prefix in ("UP", "SP"):
            if compact.startswith(prefix) and len(compact) > len(prefix):
                keys.add(compact[len(prefix):])
    return {key for key in keys if key}


def _normalise_scheme_name(value: str | None) -> str | None:
    normalised = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    return normalised or None


async def _existing_scheme_conflict(
    session,
    *,
    jurisdiction: str,
    scheme_number: str,
    scheme_name: str,
    legal_name: str | None,
) -> dict[str, str] | None:
    """Find an active non-test scheme that would collide with a new onboarding request."""
    requested_number_keys = _scheme_number_identity_keys(scheme_number)
    requested_names = {
        name for name in (
            _normalise_scheme_name(scheme_name),
            _normalise_scheme_name(legal_name),
        ) if name
    }
    result = await session.execute(
        text("""
             SELECT s.scheme_id::TEXT,
                    s.scheme_number,
                    s.scheme_name,
                    s.legal_name,
                    t.tenant_name
             FROM core.schemes s
             JOIN core.tenants t ON t.tenant_id = s.tenant_id
             WHERE s.jurisdiction = CAST(:jur AS compliance.jurisdiction_code)
               AND s.status <> CAST('archived' AS core.record_status)
               AND COALESCE(s.is_test_data, FALSE) = FALSE
             ORDER BY s.created_at NULLS LAST, s.scheme_number
             """),
        {"jur": jurisdiction},
    )
    for row in result.mappings().all():
        existing_number_keys = _scheme_number_identity_keys(row["scheme_number"])
        if requested_number_keys & existing_number_keys:
            return {
                "code": "scheme_number_taken_in_jurisdiction",
                "detail": (
                    f"Scheme number '{scheme_number}' already exists in {jurisdiction} "
                    f"as {row['scheme_name']} ({row['scheme_number']}). Open the existing "
                    "building instead of creating another onboarding session."
                ),
            }
        existing_names = {
            name for name in (
                _normalise_scheme_name(row["scheme_name"]),
                _normalise_scheme_name(row["legal_name"]),
            ) if name
        }
        if requested_names & existing_names:
            return {
                "code": "scheme_name_taken_in_jurisdiction",
                "detail": (
                    f"Legal scheme name '{scheme_name}' already exists in {jurisdiction} "
                    f"for scheme {row['scheme_number']}. Open the existing building or "
                    "change the scheme details."
                ),
            }
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────────────────────────────────────

class SchemeStartRequest(BaseModel):
    scheme_number: str = Field(..., description="Unit plan / strata plan number, e.g. '13195'")
    scheme_name: str = Field(..., description="Full legal name of the strata scheme")
    jurisdiction: str = Field(default="ACT", description="ACT | NSW | VIC | QLD | WA | SA | TAS | NT")
    tenant_id: str | None = Field(None,
                                  description="Existing tenant (super_admin must supply; ignored for strata_admin/manager).")
    legal_name: str | None = None
    abn: str | None = None


class LotInput(BaseModel):
    lot_number: str
    unit_number: str | None = None
    lot_use: str = "residential"
    floor_area_sqm: float | None = None
    entitlement_units: float | None = None
    owner_email: str | None = None


class SchemeLotsRequest(BaseModel):
    lots: list[LotInput]


class SchemeStepUpdate(BaseModel):
    current_step: int | None = None
    step_data: dict[str, Any] | None = None


class OnboardingStatusResponse(BaseModel):
    session_id: str
    tenant_id: str
    scheme_id: str | None
    status: str
    current_step: int
    step_data: dict
    created_at: str
    updated_at: str


class FinalizeRequest(BaseModel):
    """Body for ``POST /onboarding/scheme/{session_id}/finalize``.

    Both flags default to ON because by the time the wizard reaches
    step 4 the user has already opted in to having those addresses
    invited; sending nothing is the surprising case.  Set to FALSE if
    you want to finalise the session without dispatching emails (e.g.
    from a CLI script that handles invitations separately).
    """
    send_owner_invites: bool = True
    send_ec_invites: bool = True


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_ONBOARDING_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER}


def _require_can_onboard(user: dict) -> str:
    """Authorise scheme-onboarding callers and return their effective role.

    Per docs/architecture/onboarding/02_endpoint_contracts.md §2.2:
      - super_admin: must supply ``tenant_id`` in the request body
      - strata_admin / strata_manager: ``tenant_id`` is forced to caller's tenant
    """
    role = effective_role(user)
    if role not in _ONBOARDING_ROLES:
        raise HTTPException(
            status_code=403,
            detail={"detail": "Only super admins, strata admins, or strata managers can onboard buildings.",
                    "code": "not_a_strata_admin"},
        )
    return role


# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# Import templates
# ──────────────────────────────────────────────────────────────────────────────

_TEMPLATE_MEDIA_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.get("/onboarding/templates")
async def list_onboarding_templates(current_user: dict = Depends(get_current_user)):
    """List every onboarding import template with its exact column contract.

    The wizard renders its per-step "Download Template" control from this, so the
    template a manager downloads is generated from the same registry the import
    endpoints validate against — they cannot drift.
    """
    _require_can_onboard(current_user)
    return {
        "templates": [
            {
                "template_type": key,
                "description": spec["description"],
                "endpoint": spec["endpoint"],
                "multipart_field": spec["field"],
                "required_columns": spec["required"],
                "optional_columns": spec["optional"],
                "formats": ["csv", "xlsx"],
            }
            for key, spec in ONBOARDING_TEMPLATES.items()
        ],
        "historical_financials_file_group": HISTORICAL_FINANCIALS_FILE_GROUP,
    }


@router.get("/onboarding/templates/{template_type}")
async def download_onboarding_template(
        template_type: str,
        file_format: str = Query("csv", alias="format", pattern="^(csv|xlsx)$"),
        current_user: dict = Depends(get_current_user),
):
    """Download one onboarding import template as CSV or XLSX."""
    _require_can_onboard(current_user)
    if template_type not in ONBOARDING_TEMPLATES:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown template type '{template_type}'. "
                f"Valid types: {', '.join(ONBOARDING_TEMPLATES)}"
            ),
        )
    if file_format == "xlsx":
        payload = render_template_xlsx(template_type)
    else:
        payload = render_template_csv(template_type)
    filename = f"{ONBOARDING_TEMPLATES[template_type]['filename']}_template.{file_format}"
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=_TEMPLATE_MEDIA_TYPES[file_format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/onboarding/templates/{template_type}/parse")
async def parse_onboarding_template_upload(
        template_type: str,
        file: UploadFile = File(..., description="A filled-in CSV or XLSX template"),
        current_user: dict = Depends(get_current_user),
):
    """Parse a filled-in template to rows WITHOUT storing anything.

    Stateless preview/transcode helper. It exists so the browser never has to
    parse a spreadsheet itself: the lot-import step posts JSON (not multipart) to
    ``/lots``, so before this endpoint it could only read plain CSV via
    ``File.text()`` and an .xlsx of lots was unusable. Required columns are
    validated here, so schema errors surface at the preview step rather than
    after the operator commits.

    Writes nothing, touches no building context.
    """
    _require_can_onboard(current_user)
    if template_type not in ONBOARDING_TEMPLATES:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown template type '{template_type}'. "
                f"Valid types: {', '.join(ONBOARDING_TEMPLATES)}"
            ),
        )
    rows = await _parse_csv(file, required_columns=ONBOARDING_TEMPLATES[template_type]["required"])
    return {
        "template_type": template_type,
        "row_count": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows,
    }


@router.post("/onboarding/scheme/start", status_code=201)
async def start_onboarding(
        body: SchemeStartRequest,
        current_user: dict = Depends(get_current_user),
):
    """Begin a new building onboarding session.

    Auth (per spec §2.2):
      - super_admin: MUST supply ``tenant_id`` in the body. Endpoint creates
        the new scheme under that existing tenant. Tenant is NOT created.
      - strata_admin / strata_manager: ``tenant_id`` is forced to the caller's
        own tenant. Body field is ignored.

    Creates the new ``core.schemes`` row + ``core.onboarding_sessions`` row.
    Self-managed tenants cannot have a second scheme — the trigger added in
    migration 0028 returns a unique-violation translated to 409.
    """
    role = _require_can_onboard(current_user)

    jurisdiction = body.jurisdiction.upper()
    valid_jurisdictions = {"ACT", "NSW", "VIC", "QLD", "WA", "SA", "TAS", "NT"}
    if jurisdiction not in valid_jurisdictions:
        raise HTTPException(
            status_code=400,
            detail={"detail": f"Invalid jurisdiction. Must be one of: {', '.join(sorted(valid_jurisdictions))}",
                    "code": "invalid_jurisdiction"},
        )

    # Resolve tenant_id from caller role
    if role == UserRole.SUPER_ADMIN:
        if not body.tenant_id:
            raise HTTPException(
                status_code=400,
                detail={"detail": "Super admin must supply tenant_id when starting scheme onboarding.",
                        "code": "tenant_id_required_for_super_admin"},
            )
        tenant_id = body.tenant_id
    else:
        # strata_admin / strata_manager — force own tenant
        tenant_id = current_user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Caller has no tenant context.")

    # The session row lives under the CALLER's tenant so they can find it
    # via tenant context. For an SA without a tenant_id we'd be unable to
    # locate the session for subsequent /lots and /finalize calls — refuse.
    sa_tenant_id = current_user.get("tenant_id")
    if not sa_tenant_id:
        raise HTTPException(
            status_code=400,
            detail={"detail": "Calling user has no tenant context.",
                    "code": "no_tenant_context"},
        )

    scheme_number = body.scheme_number.strip()
    scheme_name = body.scheme_name.strip()
    legal_name = body.legal_name.strip() if body.legal_name else None
    if not scheme_number or not scheme_name:
        raise HTTPException(
            status_code=400,
            detail={"detail": "Scheme number and scheme name are required.",
                    "code": "scheme_identity_required"},
        )

    try:
        async with async_session_context() as session:
            # Duplicate detection must be global within a jurisdiction because
            # scheme_number becomes the building_id used across tenant-scoped
            # MongoDB and Postgres workflows.
            await session.execute(
                text("SELECT set_config('app.tenant_id', :u, true)"),
                {"u": "00000000-0000-0000-0000-000000000000"},
            )
            if role == UserRole.SUPER_ADMIN:
                # Verify the target tenant exists and isn't archived
                t_row = await session.execute(
                    text("SELECT tenant_id::TEXT, status::TEXT FROM core.tenants WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
                t_rec = t_row.fetchone()
                if not t_rec:
                    raise HTTPException(
                        status_code=404,
                        detail={"detail": "Tenant not found.",
                                "code": "tenant_not_found"},
                    )
                if t_rec[1] == "archived":
                    raise HTTPException(
                        status_code=404,
                        detail={"detail": "Tenant is archived.",
                                "code": "tenant_not_found"},
                    )

            conflict = await _existing_scheme_conflict(
                session,
                jurisdiction=jurisdiction,
                scheme_number=scheme_number,
                scheme_name=scheme_name,
                legal_name=legal_name,
            )
            if conflict:
                raise HTTPException(status_code=409, detail=conflict)

            if role != UserRole.SUPER_ADMIN:
                await set_tenant(session, tenant_id)

            # Create scheme under the resolved tenant
            result = await session.execute(
                text("""
                     INSERT INTO core.schemes
                     (tenant_id, jurisdiction, scheme_number, scheme_name, legal_name, abn, status)
                     VALUES (:tid, CAST(:jur AS compliance.jurisdiction_code), :num, :name, :legal, :abn,
                             CAST('active' AS core.record_status)) RETURNING scheme_id::TEXT
                     """),
                {
                    "tid": tenant_id,
                    "jur": jurisdiction,
                    "num": scheme_number,
                    "name": scheme_name,
                    "legal": legal_name,
                    "abn": body.abn,
                },
            )
            scheme_id = result.scalar()

            # Onboarding session — owned by caller's tenant for SA so they can
            # find their own session via tenant context; for non-SA same tenant.
            if role == UserRole.SUPER_ADMIN:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :u, true)"),
                    {"u": sa_tenant_id},
                )
            result = await session.execute(
                text("""
                     INSERT INTO core.onboarding_sessions
                         (tenant_id, scheme_id, initiated_by, status, current_step, step_data)
                     VALUES (:tenant_id, :scheme_id, :user_id, 'started', 1, '{}') RETURNING session_id::TEXT, tenant_id::TEXT, scheme_id::TEXT,
                              status, current_step, step_data,
                              created_at::TEXT, updated_at::TEXT
                     """),
                {
                    "tenant_id": sa_tenant_id,
                    "scheme_id": scheme_id,
                    "user_id": current_user["id"],
                },
            )
            row = result.fetchone()
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e).lower()
        if "scheme_number" in msg and "duplicate key" in msg:
            raise HTTPException(
                status_code=409,
                detail={"detail": f"Scheme '{body.scheme_number}' already exists in {jurisdiction} for this tenant.",
                        "code": "scheme_number_taken_in_jurisdiction"},
            )
        if "self-managed tenant" in msg or "schemes_self_managed_one_per_tenant" in msg:
            raise HTTPException(
                status_code=409,
                detail={"detail": "This self-managed tenant already has a scheme.",
                        "code": "self_managed_already_has_scheme"},
            )
        raise

    return {
        "session_id": row[0],
        "tenant_id": row[1],
        "scheme_id": row[2],
        "status": row[3],
        "current_step": row[4],
        "step_data": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "message": f"Onboarding session started for scheme {scheme_number}.",
    }


@router.post("/admin/onboarding/scheme", status_code=201)
async def start_admin_onboarding(
        body: SchemeStartRequest,
        current_user: dict = Depends(get_current_user),
):
    """Compatibility alias for the admin onboarding start flow."""
    return await start_onboarding(body=body, current_user=current_user)


# ──────────────────────────────────────────────────────────────────────────────
# POST /onboarding/scheme/{session_id}/lots — bulk lot import (idempotent)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/onboarding/scheme/{session_id}/lots")
async def import_lots(
        session_id: str,
        body: SchemeLotsRequest,
        current_user: dict = Depends(get_current_user),
):
    """Bulk-import lots/units for an in-progress onboarding session.

    Idempotent on ``(scheme_id, lot_number)`` — running twice with the
    same lot_number updates rather than duplicates. Owner emails are
    queued in step_data for invitation send at finalize.
    """
    _require_can_onboard(current_user)

    if not body.lots:
        raise HTTPException(
            status_code=422,
            detail={"detail": "At least one lot is required.", "code": "invalid_lot_payload"},
        )

    # Detect duplicate lot_numbers in the request
    seen = set()
    for lot in body.lots:
        if lot.lot_number in seen:
            raise HTTPException(
                status_code=409,
                detail={"detail": f"Duplicate lot_number '{lot.lot_number}' in request.",
                        "code": "duplicate_lot_number_in_request"},
            )
        seen.add(lot.lot_number)
        if lot.entitlement_units is not None and lot.entitlement_units < 1:
            raise HTTPException(
                status_code=422,
                detail={"detail": f"Lot {lot.lot_number}: entitlement_units must be >= 1.",
                        "code": "invalid_lot_payload"},
            )

    sa_tenant_id = current_user.get("tenant_id")
    async with async_session_context() as session:
        await set_tenant(session, sa_tenant_id)

        # Look up session + scheme
        sess_row = await session.execute(
            text("""
                 SELECT s.scheme_id::TEXT,
                        s.tenant_id::TEXT,
                        s.status,
                        s.step_data,
                        sch.tenant_id::TEXT
                 FROM core.onboarding_sessions s
                          LEFT JOIN core.schemes sch ON sch.scheme_id = s.scheme_id
                 WHERE s.session_id = :sid
                    AND initiated_by = :uid
                  """),
            {"sid": session_id, "uid": current_user["id"]},
        )
        sess = sess_row.fetchone()
        if not sess:
            raise HTTPException(
                status_code=404,
                detail={"detail": "Session not found.", "code": "session_not_found"},
            )
        if sess[2] in ("completed", "abandoned"):
            raise HTTPException(
                status_code=409,
                detail={"detail": "Session already finalized.",
                        "code": "session_already_finalized"},
            )
        scheme_id = sess[0]
        existing_step_data = sess[3] or {}
        scheme_tenant_id = sess[4]
        if not scheme_tenant_id and scheme_id:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :u, true)"),
                {"u": "00000000-0000-0000-0000-000000000000"},
            )
            scheme_tenant_result = await session.execute(
                text("SELECT tenant_id::TEXT FROM core.schemes WHERE scheme_id = :sid"),
                {"sid": scheme_id},
            )
            scheme_tenant_id = scheme_tenant_result.scalar()
            await session.execute(
                text("SELECT set_config('app.tenant_id', :u, true)"),
                {"u": sa_tenant_id},
            )
        if not scheme_tenant_id:
            raise HTTPException(status_code=400, detail="Scheme tenant not found.")

        # Insert lots — RLS context = scheme's tenant
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": scheme_tenant_id},
        )

        inserted = 0
        updated = 0
        owners_queued: list[str] = []
        for lot in body.lots:
            res = await session.execute(
                text("""
                     INSERT INTO core.lots
                     (tenant_id, scheme_id, lot_number, unit_number, lot_use,
                      floor_area_sqm, entitlement_units, status)
                     VALUES (:tid, :sid, :ln, :un, :use, :area, :ent, 'active') ON CONFLICT (scheme_id, lot_number)
                    DO
                     UPDATE SET
                         unit_number = EXCLUDED.unit_number,
                         lot_use = EXCLUDED.lot_use,
                         floor_area_sqm = EXCLUDED.floor_area_sqm,
                         entitlement_units = EXCLUDED.entitlement_units,
                         updated_at = NOW()
                         RETURNING (xmax = 0) AS inserted
                     """),
                {
                    "tid": scheme_tenant_id,
                    "sid": scheme_id,
                    "ln": lot.lot_number,
                    "un": lot.unit_number,
                    "use": lot.lot_use,
                    "area": lot.floor_area_sqm,
                    "ent": lot.entitlement_units,
                },
            )
            was_inserted = res.scalar()
            if was_inserted:
                inserted += 1
            else:
                updated += 1
            if lot.owner_email:
                owners_queued.append(lot.owner_email.strip().lower())

        # Stash owner emails in step_data for finalize-time invitation send.
        # PATCH semantics: merge with existing step_data.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": sa_tenant_id},
        )
        merged = {**existing_step_data, "owner_emails": owners_queued}
        await session.execute(
            text("""
                 UPDATE core.onboarding_sessions
                 SET step_data = CAST(:data AS JSONB),
                       current_step = GREATEST(current_step, 2),
                       status       = CASE WHEN status = 'started' THEN 'in_progress' ELSE status
                 END,
                       updated_at   = NOW()
                 WHERE session_id  = :sid
                   AND initiated_by = :uid
                 """),
            {"data": json.dumps(merged), "sid": session_id, "uid": current_user["id"]},
        )

    return {
        "session_id": session_id,
        "lots_inserted": inserted,
        "lots_updated": updated,
        "owners_queued_for_invite": len(owners_queued),
    }


@router.get("/onboarding/scheme/{session_id}", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
        session_id: str,
        current_user: dict = Depends(get_current_user),
):
    """Return the current wizard state for an onboarding session."""
    _require_can_onboard(current_user)

    sa_tenant_id = current_user.get("tenant_id")
    if not sa_tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context.")

    async with async_session_context() as session:
        await set_tenant(session, sa_tenant_id)
        result = await session.execute(
            text("""
                 SELECT session_id::TEXT, tenant_id::TEXT, scheme_id::TEXT, status,
                        current_step,
                        step_data,
                        created_at::TEXT, updated_at::TEXT
                 FROM core.onboarding_sessions
                 WHERE session_id = :sid
                   AND initiated_by = :uid
                 """),
            {"sid": session_id, "uid": current_user["id"]},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Onboarding session not found.")

    return OnboardingStatusResponse(
        session_id=row[0],
        tenant_id=row[1],
        scheme_id=row[2],
        status=row[3],
        current_step=row[4],
        step_data=row[5] or {},
        created_at=row[6],
        updated_at=row[7],
    )


@router.patch("/onboarding/scheme/{session_id}")
async def update_onboarding_step(
        session_id: str,
        body: SchemeStepUpdate,
        current_user: dict = Depends(get_current_user),
):
    """Save wizard step data.  Only updates provided fields."""
    # Auth relaxed to match start/lots/finalize per spec §2.2 — the SM-org's
    # own admin/manager must be able to PATCH their own session.
    _require_can_onboard(current_user)

    sa_tenant_id = current_user.get("tenant_id")
    if not sa_tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context.")

    async with async_session_context() as session:
        await set_tenant(session, sa_tenant_id)
        # `:data` MUST be cast the same way (CAST(:data AS JSONB)) everywhere it's
        # referenced in this statement — found live during the 2026-07-25 audit:
        # asyncpg's prepared-statement protocol raised AmbiguousParameterError
        # ("could not determine data type of parameter") when `:data` was compared
        # bare (`:data IS NOT NULL`) in one branch and cast in another, REGARDLESS
        # of whether `:step` was null — this endpoint had zero prior test coverage,
        # so nothing had exercised it end-to-end before. Same fix applied to `:step`
        # for consistency/defensiveness, though it wasn't the actual trigger.
        result = await session.execute(
            text("""
                 UPDATE core.onboarding_sessions
                 SET current_step = COALESCE(CAST(:step AS INTEGER), current_step),
                     step_data    = CASE
                                        WHEN CAST(:data AS JSONB) IS NOT NULL
                                            THEN step_data || CAST(:data AS JSONB)
                                        ELSE step_data
                         END,
                     status       = CASE WHEN status = 'started' THEN 'in_progress' ELSE status END,
                     updated_at   = NOW()
                 WHERE session_id = :sid
                   AND initiated_by = :uid
                   AND status NOT IN ('completed', 'abandoned') RETURNING session_id::TEXT, status, current_step
                 """),
            {
                "sid": session_id,
                "uid": current_user["id"],
                "step": body.current_step,
                # Was str(dict) — produces Python repr, not JSON; fails the
                # JSONB cast. Use json.dumps so step_data merges cleanly.
                "data": json.dumps(body.step_data) if body.step_data else None,
            },
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Session not found or already completed.")

    return {"session_id": row[0], "status": row[1], "current_step": row[2]}


# ──────────────────────────────────────────────────────────────────────────────
# CSV import helpers
# ──────────────────────────────────────────────────────────────────────────────

_MAX_CSV_SIZE = 10 * 1024 * 1024  # 10 MB


async def _parse_csv(upload: UploadFile, *, required_columns: list[str] | None = None) -> list[dict]:
    """Read an UploadFile as CSV **or** XLSX and return list of row dicts.

    Enforces a 10 MB size cap, validates the declared content-type against the
    tabular allowlist, and runs a Magika content scan before parsing the bytes.
    Format is then detected from the bytes themselves (see
    ``utils/tabular_upload.parse_tabular_bytes``) — a strata manager exporting
    from Excel gets the same import as one who saved to CSV, and XLSX cells are
    normalised to the exact string shape ``csv.DictReader`` would have produced.

    When `required_columns` is given, every listed header must be present in the
    file or this raises 422 before returning any rows. Without this check, a file
    with the wrong schema (e.g. a hand-curated financial-audit CSV fed into an
    endpoint that expects a specific generated-package format) parses without
    error and every mismatched field lookup silently coerces to 0.0/None —
    producing wrong imported data with no indication anything went wrong.
    """
    content = await upload.read()
    if len(content) > _MAX_CSV_SIZE:
        raise HTTPException(status_code=413, detail=f"File '{upload.filename}' exceeds the 10 MB limit.")
    assert_content_type_allowed(upload.content_type, upload.filename or "")
    await scan_upload(content, context="tabular", filename=upload.filename or "")
    headers, rows = parse_tabular_bytes(content, filename=upload.filename or "")
    if required_columns:
        assert_required_columns(headers, required_columns, upload.filename or "file")
    return rows


def _safe_float(value: str | None, default: float = 0.0) -> float:
    """Generated function header.

    Function: _safe_float
    Path: backend/routers/onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return float(value) if value not in (None, "", "N/A") else default
    except (ValueError, TypeError):
        return default


def _safe_date(value: str | None) -> date | None:
    """Generated function header.

    Function: _safe_date
    Path: backend/routers/onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not value or value.strip() == "":
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _valid_bsb(value: str | None) -> bool:
    """AU BSB: 6 digits, with or without a separating hyphen (e.g. '062-000' or '062000').

    A whitespace-only value is treated the same as an absent one (True — "optional field,
    absence is not an error"), not as invalid digits. The one current caller already strips
    trust_bsb before this runs, so this specific branch isn't reachable from that call site
    today, but this function is a general-purpose validator and should be correct standalone
    for any future caller that doesn't pre-strip. Flagged by Amazon Q's PR #547 review.
    """
    if not value or not value.strip():
        return True  # optional field — absence is not an error
    digits = value.replace("-", "").replace(" ", "")
    return digits.isdigit() and len(digits) == 6


def _valid_account_number(value: str | None) -> bool:
    """AU bank account numbers are numeric, typically 6-10 digits.

    See _valid_bsb's docstring — same whitespace-only-is-absent reasoning applies here.
    """
    if not value or not value.strip():
        return True  # optional field — absence is not an error
    digits = value.replace(" ", "")
    return digits.isdigit() and 4 <= len(digits) <= 10


async def _resolve_session(session_id: str, user_id: str, db_session) -> dict:
    """Return session row dict or raise 404.  Raises 409 if already finalized."""
    result = await db_session.execute(
        text("""
             SELECT s.session_id::TEXT, s.scheme_id::TEXT, s.tenant_id::TEXT,
                    s.status, s.step_data,
                    sch.tenant_id::TEXT,
                    sch.scheme_number
              FROM core.onboarding_sessions s
                       LEFT JOIN core.schemes sch ON sch.scheme_id = s.scheme_id
              WHERE s.session_id = :sid
                AND s.initiated_by = :uid
              """),
        {"sid": session_id, "uid": user_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found.")
    if row[3] in ("completed", "abandoned"):
        raise HTTPException(status_code=409, detail="Session already finalized.")
    if len(row) >= 7:
        scheme_tenant_id = row[5]
        scheme_number = row[6]
    else:
        scheme_tenant_id = row[2]
        scheme_number = row[5] if len(row) > 5 else None
    if (not scheme_tenant_id or not scheme_number) and row[1]:
        current_ctx_result = await db_session.execute(
            text("SELECT current_setting('app.tenant_id', true)")
        )
        current_ctx = current_ctx_result.scalar()
        await db_session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": "00000000-0000-0000-0000-000000000000"},
        )
        scheme_result = await db_session.execute(
            text("""
                 SELECT tenant_id::TEXT, scheme_number
                 FROM core.schemes
                 WHERE scheme_id = :scheme_id
                 """),
            {"scheme_id": row[1]},
        )
        scheme_row = scheme_result.fetchone()
        if scheme_row:
            scheme_tenant_id = scheme_row[0]
            scheme_number = scheme_row[1]
        await db_session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": current_ctx or ""},
        )
    return {
        "session_id": row[0],
        "scheme_id": row[1],
        "tenant_id": scheme_tenant_id,
        "session_tenant_id": row[2],
        "status": row[3],
        "step_data": row[4] or {},
        "scheme_number": scheme_number,
    }


async def _patch_step_data(session_id: str, user_id: str, patch: dict, db_session) -> None:
    """Generated function header.

    Function: _patch_step_data
    Path: backend/routers/onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await db_session.execute(
        text("""
             UPDATE core.onboarding_sessions
             SET step_data  = step_data || CAST(:patch AS JSONB),
                 updated_at = NOW()
             WHERE session_id   = :sid
               AND initiated_by = :uid
             """),
        {"patch": json.dumps(patch), "sid": session_id, "uid": user_id},
    )


# ──────────────────────────────────────────────────────────────────────────────
# C4 endpoint 1 — import-historical-financials
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/onboarding/scheme/{session_id}/import-historical-financials", status_code=200)
async def import_historical_financials(
        session_id: str,
        quarterly_levies: UploadFile = File(..., description="CSV 06 — historical quarterly levy issuances"),
        admin_fund_summary: UploadFile = File(..., description="CSV 07 — annual admin fund summaries"),
        sinking_fund_summary: UploadFile = File(..., description="CSV 08 — annual sinking fund summaries"),
        arrears: UploadFile = File(..., description="CSV 09 — arrears as at 2025-12-31"),
        outstanding: UploadFile = File(..., description="CSV 10 — outstanding balances as at 2026-06-01"),
        current_user: dict = Depends(get_current_user),
):
    """Import historical financial data from East Gate CSV package (files 06–10).

    # Historical financial import path shares the top-level onboarding trace.

    Deliberately writes to SEPARATE historical_* collections (not annual_levies
    or financial_summaries) to preserve live East Gate production data until
    Phase F-prime does the controlled cutover. See ADR-022.

    Collections written:
    - historical_annual_levies: one doc per (year, fund_type=admin|sinking)
      with budget/actual totals. Separate from live annual_levies.
    - historical_levy_issuances: one doc per (year, quarter, lot_number).
    - historical_financial_snapshots: arrears (2025-12-31) and outstanding
      (2026-06-01) snapshots per lot.

    Validates internal consistency before writing (levy totals sum per year).
    Stores ingest summary in step_data['historical_financials_summary'].

    Auth: super_admin / strata_admin / strata_manager (same as /start).
    """
    _require_can_onboard(current_user)

    sa_tenant_id = current_user.get("tenant_id")
    if not sa_tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context.")

    # Parse all CSVs upfront
    q_rows = await _parse_csv(quarterly_levies, required_columns=[
        "year", "quarter", "lot_number", "admin_levy_amount", "sinking_levy_amount",
    ])
    admin_rows = await _parse_csv(admin_fund_summary, required_columns=["year", "levy_income"])
    sinking_rows = await _parse_csv(sinking_fund_summary, required_columns=["year", "levy_income"])
    arrears_rows = await _parse_csv(arrears, required_columns=["lot_number", "admin_arrears", "sinking_arrears"])
    outstanding_rows = await _parse_csv(outstanding, required_columns=[
        "lot_number", "admin_outstanding", "sinking_outstanding",
    ])

    if not q_rows:
        raise HTTPException(status_code=422, detail="quarterly_levies CSV is empty.")

    # Resolve session and scheme_number (= MongoDB building_id)
    async with async_session_context() as pg:
        await set_tenant(pg, sa_tenant_id)
        sess = await _resolve_session(session_id, current_user["id"], pg)

    building_id = sess["scheme_number"]
    if not building_id:
        raise HTTPException(status_code=400, detail="Scheme has no scheme_number; cannot set MongoDB context.")

    # ── Validate internal consistency ─────────────────────────────────────────
    # Sum quarterly levy totals per year and check they match admin fund rows
    yearly_admin_from_q: dict[str, float] = {}
    yearly_sinking_from_q: dict[str, float] = {}
    for row in q_rows:
        yr = row.get("year", "")
        yearly_admin_from_q[yr] = yearly_admin_from_q.get(yr, 0.0) + _safe_float(row.get("admin_levy_amount"))
        yearly_sinking_from_q[yr] = yearly_sinking_from_q.get(yr, 0.0) + _safe_float(row.get("sinking_levy_amount"))

    validation_warnings: list[str] = []
    for yr, expected in yearly_admin_from_q.items():
        admin_match = next((r for r in admin_rows if r.get("year") == yr), None)
        if admin_match:
            stated = _safe_float(admin_match.get("levy_income"))
            if abs(stated - expected) > 1.0:  # allow $1 rounding tolerance
                validation_warnings.append(
                    f"Admin fund year {yr}: quarterly sum {expected:.2f} vs summary {stated:.2f}"
                )

    # ── Write to MongoDB ───────────────────────────────────────────────────────
    # Deliberately writes to SEPARATE historical_* collections, NOT to
    # annual_levies or financial_summaries directly.
    #
    # Rationale (ADR-022): East Gate's live annual_levies data is still in
    # production MongoDB and must not be touched until Phase F-prime does the
    # controlled cutover. Writing to historical_* collections keeps the
    # imported data fully isolated. Phase F-prime reconciles historical_*
    # against live data and migrates winners to Postgres before dropping both
    # MongoDB collections.
    set_ctx_building_id(building_id)

    imported_at = datetime.now(timezone.utc).isoformat()

    # ── historical_annual_levies ───────────────────────────────────────────────
    # One document per (year, fund_type). Separate admin/sinking docs are
    # deliberate here — this is import-only storage, not the live schema.
    annual_levy_docs: list[dict] = []
    for row in admin_rows:
        yr = row.get("year", "")
        annual_levy_docs.append({
            "building_id": building_id,
            "year": yr,
            "fund_type": "admin",
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "budget_proposed": _safe_float(row.get("budget_proposed")),
            "levy_income": _safe_float(row.get("levy_income")),
            "other_income": _safe_float(row.get("other_income")),
            "total_income": _safe_float(row.get("total_income")),
            "total_expenses_audited": _safe_float(row.get("total_expenses_audited")),
            "surplus_deficit": _safe_float(row.get("surplus_deficit")),
            "opening_balance": _safe_float(row.get("opening_balance")),
            "closing_balance": _safe_float(row.get("closing_balance")),
            "reconciliation_note": row.get("reconciliation_note"),
            "notes": row.get("notes"),
            "data_origin": "imported",
            "imported_at": imported_at,
        })
    for row in sinking_rows:
        yr = row.get("year", "")
        annual_levy_docs.append({
            "building_id": building_id,
            "year": yr,
            "fund_type": "sinking",
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "budget_proposed": _safe_float(row.get("budget_proposed")),
            "levy_income": _safe_float(row.get("levy_income")),
            "other_income": _safe_float(row.get("other_income")),
            "total_income": _safe_float(row.get("total_income")),
            "total_expenses_audited": _safe_float(row.get("total_expenses_audited")),
            "surplus_deficit": _safe_float(row.get("surplus_deficit")),
            "opening_balance": _safe_float(row.get("opening_balance")),
            "closing_balance": _safe_float(row.get("closing_balance")),
            "reconciliation_note": row.get("reconciliation_note"),
            "notes": row.get("notes"),
            "data_origin": "imported",
            "imported_at": imported_at,
        })

    # ── historical_levy_issuances ─────────────────────────────────────────────
    levy_issuance_docs = []
    for row in q_rows:
        levy_issuance_docs.append({
            "building_id": building_id,
            "year": row.get("year"),
            "quarter": row.get("quarter"),
            "due_date": row.get("due_date"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "lot_number": row.get("lot_number"),
            "unit_number": row.get("unit_number"),
            "uoe": _safe_float(row.get("uoe")),
            "total_uoe": _safe_float(row.get("total_uoe")),
            "admin_levy_amount": _safe_float(row.get("admin_levy_amount")),
            "sinking_levy_amount": _safe_float(row.get("sinking_levy_amount")),
            "total_levy_amount": _safe_float(row.get("total_levy_amount")),
            "admin_levy_amount_excl_gst": _safe_float(row.get("admin_levy_amount_excl_gst")),
            "sinking_levy_amount_excl_gst": _safe_float(row.get("sinking_levy_amount_excl_gst")),
            "notes": row.get("notes"),
            "imported_at": imported_at,
        })

    # ── historical_financial_snapshots ────────────────────────────────────────
    arrears_docs = []
    for row in arrears_rows:
        arrears_docs.append({
            "building_id": building_id,
            "snapshot_type": "arrears_2025_12_31",
            "snapshot_date": "2025-12-31",
            "lot_number": row.get("lot_number"),
            "unit_number": row.get("unit_number"),
            "admin_arrears": _safe_float(row.get("admin_arrears")),
            "sinking_arrears": _safe_float(row.get("sinking_arrears")),
            "total_arrears": _safe_float(row.get("total_arrears")),
            "admin_closing_balance": _safe_float(row.get("admin_closing_balance")),
            "sinking_closing_balance": _safe_float(row.get("sinking_closing_balance")),
            "data_source": row.get("data_source"),
            "notes": row.get("notes"),
            "imported_at": imported_at,
        })

    outstanding_docs = []
    for row in outstanding_rows:
        outstanding_docs.append({
            "building_id": building_id,
            "snapshot_type": "outstanding_2026_06_01",
            "snapshot_date": row.get("as_at_date", "2026-06-01"),
            "lot_number": row.get("lot_number"),
            "unit_number": row.get("unit_number"),
            "admin_outstanding": _safe_float(row.get("admin_outstanding")),
            "sinking_outstanding": _safe_float(row.get("sinking_outstanding")),
            "total_outstanding": _safe_float(row.get("total_outstanding")),
            "data_source": row.get("data_source"),
            "notes": row.get("notes"),
            "imported_at": imported_at,
        })

    # Idempotent: delete existing import docs for this building then re-insert.
    await db.historical_annual_levies.delete_many({"building_id": building_id})
    if annual_levy_docs:
        await db.historical_annual_levies.insert_many(annual_levy_docs)

    await db.historical_levy_issuances.delete_many({"building_id": building_id})
    if levy_issuance_docs:
        await db.historical_levy_issuances.insert_many(levy_issuance_docs)

    await db.historical_financial_snapshots.delete_many(
        {"building_id": building_id, "snapshot_type": "arrears_2025_12_31"}
    )
    if arrears_docs:
        await db.historical_financial_snapshots.insert_many(arrears_docs)

    await db.historical_financial_snapshots.delete_many(
        {"building_id": building_id, "snapshot_type": "outstanding_2026_06_01"}
    )
    if outstanding_docs:
        await db.historical_financial_snapshots.insert_many(outstanding_docs)

    # Persist summary in step_data
    summary = {
        "annual_levy_years": sorted({d["year"] for d in annual_levy_docs}),
        "annual_levy_docs": len(annual_levy_docs),
        "levy_issuance_docs": len(levy_issuance_docs),
        "arrears_docs": len(arrears_docs),
        "outstanding_docs": len(outstanding_docs),
        "validation_warnings": validation_warnings,
        "imported_at": imported_at,
    }

    async with async_session_context() as pg:
        await set_tenant(pg, sa_tenant_id)
        await _patch_step_data(
            session_id, current_user["id"],
            {"historical_financials_summary": summary},
            pg,
        )

    logger.info(
        "import-historical-financials: session=%s building=%s admin_years=%d issuance_rows=%d",
        session_id, building_id, len(admin_rows), len(levy_issuance_docs),
    )

    return {
        "session_id": session_id,
        "building_id": building_id,
        **summary,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Historical expense import (2026-07-17 — historical ledger reconciliation)
# See docs/migration/historical_ledger_reconciliation_plan01.md/plan02.md.
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/onboarding/scheme/{session_id}/import-historical-expenses", status_code=200)
async def import_historical_expenses(
        session_id: str,
        expenses: UploadFile = File(..., description="CSV — historical settled expense transactions"),
        current_user: dict = Depends(get_current_user),
):
    """Import historical expense transactions (settled, from AGM records/audited
    reports/invoices) into the historical_expense_transactions staging
    collection — the same isolated-staging pattern as
    import_historical_financials (ADR-022), not a live write.

    CSV columns: vendor_name, invoice_number, category_name, fund_type,
    financial_year, transaction_date, amount_ex_gst, gst_amount, description,
    evidence_references (semicolon-separated evidence_document_id list),
    derivation_level (exact | source_derived | aggregate_balancing — see
    plan02 amendment 10; defaults to aggregate_balancing when blank, since an
    unlabelled historical row should never silently claim invoice-level
    precision it doesn't have).

    Auth: super_admin / strata_admin / strata_manager (same as /start).
    """
    _require_can_onboard(current_user)

    sa_tenant_id = current_user.get("tenant_id")
    if not sa_tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context.")

    rows = await _parse_csv(expenses, required_columns=[
        "vendor_name", "category_name", "financial_year", "amount_ex_gst",
    ])
    if not rows:
        raise HTTPException(status_code=422, detail="expenses CSV is empty.")

    async with async_session_context() as pg:
        await set_tenant(pg, sa_tenant_id)
        sess = await _resolve_session(session_id, current_user["id"], pg)

    building_id = sess["scheme_number"]
    if not building_id:
        raise HTTPException(status_code=400, detail="Scheme has no scheme_number; cannot set MongoDB context.")

    set_ctx_building_id(building_id)
    imported_at = datetime.now(timezone.utc).isoformat()

    _valid_derivation_levels = {"exact", "source_derived", "aggregate_balancing"}
    expense_docs: list[dict] = []
    validation_warnings: list[str] = []
    for row in rows:
        derivation_level = (row.get("derivation_level") or "").strip().lower() or "aggregate_balancing"
        if derivation_level not in _valid_derivation_levels:
            validation_warnings.append(
                f"Row for vendor={row.get('vendor_name')!r} has invalid derivation_level "
                f"{derivation_level!r} — defaulted to aggregate_balancing."
            )
            derivation_level = "aggregate_balancing"
        evidence_refs = [r.strip() for r in (row.get("evidence_references") or "").split(";") if r.strip()]
        expense_docs.append({
            "building_id": building_id,
            "vendor_name": row.get("vendor_name"),
            "invoice_number": row.get("invoice_number"),
            "category_name": row.get("category_name"),
            "fund_type": (row.get("fund_type") or "admin").strip().lower(),
            "financial_year": row.get("financial_year"),
            "transaction_date": row.get("transaction_date"),
            "amount_ex_gst": _safe_float(row.get("amount_ex_gst")),
            "gst_amount": _safe_float(row.get("gst_amount")),
            "description": row.get("description"),
            "evidence_references": evidence_refs,
            "derivation_level": derivation_level,
            "data_origin": "imported",
            "imported_at": imported_at,
        })

    # Idempotent: delete existing import docs for this building then re-insert —
    # same pattern as import_historical_financials.
    await db.historical_expense_transactions.delete_many({"building_id": building_id})
    if expense_docs:
        await db.historical_expense_transactions.insert_many(expense_docs)

    summary = {
        "expense_docs": len(expense_docs),
        "financial_years": sorted({d["financial_year"] for d in expense_docs if d["financial_year"]}),
        "derivation_level_counts": {
            level: sum(1 for d in expense_docs if d["derivation_level"] == level)
            for level in _valid_derivation_levels
        },
        "validation_warnings": validation_warnings,
        "imported_at": imported_at,
    }

    async with async_session_context() as pg:
        await set_tenant(pg, sa_tenant_id)
        await _patch_step_data(
            session_id, current_user["id"],
            {"historical_expenses_summary": summary},
            pg,
        )

    logger.info(
        "import-historical-expenses: session=%s building=%s expense_docs=%d",
        session_id, building_id, len(expense_docs),
    )

    return {
        "session_id": session_id,
        "building_id": building_id,
        **summary,
    }


# ──────────────────────────────────────────────────────────────────────────────
# C4 endpoint 2 — import-owner-transfers
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/onboarding/scheme/{session_id}/import-owner-transfers", status_code=200)
async def import_owner_transfers(
        session_id: str,
        transfers_file: UploadFile = File(..., description="CSV 05 — owner transfer history"),
        current_user: dict = Depends(get_current_user),
):
    """Import historical owner transfers (CSV 05) as bitemporal ownership_periods.

    # Historical owner-transfer import path shares the top-level onboarding trace.

    For each row, upserts a core.parties record for the new owner and inserts a
    core.ownership_periods record.  The chain of periods for each lot must be
    non-overlapping; the endpoint validates this before writing and returns 422
    on conflict.

    For the earliest transfer of each lot, a period is also created for the
    previous_owner_name (starting 1900-01-01 as a historical placeholder,
    ending the day before the first transfer).

    Auth: super_admin / strata_admin / strata_manager.
    """
    _require_can_onboard(current_user)

    sa_tenant_id = current_user.get("tenant_id")
    if not sa_tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context.")

    rows = await _parse_csv(transfers_file, required_columns=[
        "lot_number", "effective_from", "new_owner_name", "previous_owner_name",
    ])
    if not rows:
        raise HTTPException(status_code=422, detail="transfers_file CSV is empty.")

    # ── Validate: non-overlapping periods per lot ──────────────────────────────
    from collections import defaultdict
    lot_periods: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        lot = row.get("lot_number", "").strip()
        eff_from = _safe_date(row.get("effective_from"))
        eff_to = _safe_date(row.get("effective_to"))
        if not lot or not eff_from:
            continue
        lot_periods[lot].append((eff_from, eff_to))

    for lot, periods in lot_periods.items():
        sorted_periods = sorted(periods, key=lambda p: p[0])
        for i in range(len(sorted_periods) - 1):
            _, end_a = sorted_periods[i]
            start_b, _ = sorted_periods[i + 1]
            end_a_resolved = end_a if end_a else date(9999, 12, 31)
            if end_a_resolved >= start_b:
                raise HTTPException(
                    status_code=422,
                    detail=f"Overlapping ownership periods for lot {lot}: "
                           f"period ending {end_a} overlaps with period starting {start_b}.",
                )

    # ── Write to Postgres ──────────────────────────────────────────────────────
    inserted = 0
    skipped = 0

    async with async_session_context() as pg:
        await set_tenant(pg, sa_tenant_id)
        sess = await _resolve_session(session_id, current_user["id"], pg)
        scheme_id = sess["scheme_id"]
        tenant_id = sess["tenant_id"]

        # Switch to the scheme's tenant for all inserts
        await pg.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": tenant_id},
        )

        # Build map of lot_number → lot_id (scheme-scoped)
        lots_result = await pg.execute(
            text("SELECT lot_number, lot_id::TEXT FROM core.lots WHERE scheme_id = :sid"),
            {"sid": scheme_id},
        )
        lot_map = {r[0]: r[1] for r in lots_result.fetchall()}

        # Group rows by lot for first-transfer detection
        from collections import defaultdict as _dd
        rows_by_lot: dict[str, list[dict]] = _dd(list)
        for row in rows:
            rows_by_lot[row.get("lot_number", "").strip()].append(row)

        for lot_number, lot_rows in rows_by_lot.items():
            lot_id = lot_map.get(lot_number)
            if not lot_id:
                logger.warning("import-owner-transfers: lot %s not in core.lots for scheme %s — skipping", lot_number, scheme_id)
                skipped += 1
                continue

            lot_rows_sorted = sorted(
                lot_rows,
                key=lambda r: _safe_date(r.get("effective_from")) or date.min,
            )

            for idx, row in enumerate(lot_rows_sorted):
                new_owner = (row.get("new_owner_name") or "").strip()
                prev_owner = (row.get("previous_owner_name") or "").strip()
                eff_from = _safe_date(row.get("effective_from"))
                eff_to = _safe_date(row.get("effective_to"))
                notes = row.get("notes", "")

                if not new_owner or not eff_from:
                    skipped += 1
                    continue

                # Upsert the previous owner's period on the first transfer for this lot
                if idx == 0 and prev_owner:
                    prev_from = date(1900, 1, 1)
                    prev_to = eff_from - timedelta(days=1)
                    prev_party_id = await _upsert_party(pg, tenant_id, prev_owner)
                    await _insert_ownership_period(
                        pg, tenant_id, scheme_id, lot_id, prev_party_id,
                        prev_from, prev_to, notes="Historical predecessor (placeholder start date)",
                    )
                    inserted += 1

                # Upsert new owner party
                new_party_id = await _upsert_party(pg, tenant_id, new_owner)

                # Insert ownership period for the new owner
                await _insert_ownership_period(
                    pg, tenant_id, scheme_id, lot_id, new_party_id,
                    eff_from, eff_to, notes=notes,
                )
                inserted += 1

        # Restore SA tenant and patch step_data
        await pg.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": sa_tenant_id},
        )
        summary = {
            "ownership_periods_inserted": inserted,
            "lots_skipped": skipped,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        await _patch_step_data(session_id, current_user["id"], {"owner_transfers_summary": summary}, pg)

    logger.info(
        "import-owner-transfers: session=%s inserted=%d skipped=%d",
        session_id, inserted, skipped,
    )
    return {"session_id": session_id, **summary}


async def _upsert_party(pg_session, tenant_id: str, legal_name: str) -> str:
    """Find or create a core.parties row for legal_name within tenant_id.

    Returns party_id as str.  The lookup is case-insensitive on legal_name to
    avoid creating duplicates when the same name appears with minor variations.
    """
    result = await pg_session.execute(
        text("""
             SELECT party_id::TEXT
             FROM core.parties
             WHERE tenant_id = :tid
               AND lower(legal_name) = lower(:name)
             LIMIT 1
             """),
        {"tid": tenant_id, "name": legal_name},
    )
    row = result.fetchone()
    if row:
        return row[0]

    result = await pg_session.execute(
        text("""
             INSERT INTO core.parties (tenant_id, party_type, legal_name, metadata)
             VALUES (:tid, 'individual', :name, '{}')
             RETURNING party_id::TEXT
             """),
        {"tid": tenant_id, "name": legal_name},
    )
    return result.scalar()


async def _insert_ownership_period(
        pg_session,
        tenant_id: str,
        scheme_id: str,
        lot_id: str,
        party_id: str,
        valid_from: date,
        valid_to: date | None,
        notes: str = "",
) -> None:
    """Insert a single core.ownership_periods row (no-op on EXCLUDE conflict)."""
    try:
        await pg_session.execute(
            text("""
                 INSERT INTO core.ownership_periods
                     (tenant_id, scheme_id, lot_id, owner_party_id, valid_from, valid_to, notes)
                 VALUES (:tid, :sid, :lid, :pid, :vf, :vt, :notes)
                 ON CONFLICT DO NOTHING
                 """),
            {
                "tid": tenant_id,
                "sid": scheme_id,
                "lid": lot_id,
                "pid": party_id,
                "vf": valid_from,
                "vt": valid_to,
                "notes": notes,
            },
        )
    except Exception as exc:
        # The EXCLUDE constraint raises a unique-like error for overlapping dateranges.
        # Translate to a user-friendly 422.
        raise HTTPException(
            status_code=422,
            detail=f"Overlapping ownership period for lot_id={lot_id}: {exc}",
        ) from exc


# ──────────────────────────────────────────────────────────────────────────────
# C4 endpoint 3 — import-opening-balances
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/onboarding/scheme/{session_id}/import-opening-balances", status_code=200)
async def import_opening_balances(
        session_id: str,
        opening_balances_file: UploadFile = File(..., description="CSV 11 — fund opening balances at cutover"),
        current_user: dict = Depends(get_current_user),
):
    """Capture fund opening balances from CSV 11 into step_data.

    # Opening-balance capture shares the top-level onboarding trace.

    Stores parsed balance data under step_data['opening_balances'].  Does NOT
    post genesis journals — that happens in Phase F-prime finalize.

    CSV columns: fund_type, opening_balance_amount, as_at_date, bsb,
                 account_number, account_name, evidence_source, notes.

    Auth: super_admin / strata_admin / strata_manager.
    """
    _require_can_onboard(current_user)

    sa_tenant_id = current_user.get("tenant_id")
    if not sa_tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context.")

    rows = await _parse_csv(opening_balances_file, required_columns=["fund_type", "opening_balance_amount"])
    if not rows:
        raise HTTPException(status_code=422, detail="opening_balances_file CSV is empty.")

    balances = []
    for row in rows:
        fund_type = (row.get("fund_type") or "").strip().lower()
        # Normalise: "Administrative Fund" → "admin", "Sinking Fund" → "sinking"
        if "admin" in fund_type:
            fund_type = "admin"
        elif "sink" in fund_type:
            fund_type = "sinking"
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Unrecognised fund_type '{row.get('fund_type')}'. Expected 'admin' or 'sinking'.",
            )

        raw_amount = row.get("opening_balance_amount", "")
        try:
            amount_cents = round(_safe_float(raw_amount) * 100)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid opening_balance_amount '{raw_amount}' for fund_type '{fund_type}'.",
            )

        as_at = _safe_date(row.get("as_at_date"))
        if not as_at:
            raise HTTPException(
                status_code=422,
                detail=f"Missing or invalid as_at_date for fund_type '{fund_type}'.",
            )

        balances.append({
            "fund_type": fund_type,
            "opening_balance_cents": amount_cents,
            "opening_balance_amount": _safe_float(raw_amount),
            "as_at_date": as_at.isoformat(),
            "bsb": row.get("bsb", ""),
            "account_number": row.get("account_number", ""),
            "account_name": row.get("account_name", ""),
            "evidence_source": row.get("evidence_source", ""),
            "notes": row.get("notes", ""),
        })

    async with async_session_context() as pg:
        await set_tenant(pg, sa_tenant_id)
        # Validate session exists and is active
        await _resolve_session(session_id, current_user["id"], pg)
        await _patch_step_data(
            session_id, current_user["id"],
            {"opening_balances": balances},
            pg,
        )

    logger.info(
        "import-opening-balances: session=%s funds=%s",
        session_id, [b["fund_type"] for b in balances],
    )
    return {
        "session_id": session_id,
        "opening_balances": balances,
        "funds_captured": len(balances),
    }


@router.post("/onboarding/scheme/{session_id}/finalize")
async def finalize_onboarding(
        session_id: str,
        body: FinalizeRequest = FinalizeRequest(),
        current_user: dict = Depends(get_current_user),
):
    """Mark the onboarding session as completed.

    The scheme is already set to 'active' at creation time.  This endpoint:
    1. Retrieves fund opening balances from step_data
    2. Posts genesis journals for each fund (Phase F Component 2 integration)
    3. Flips the financial_core.read_from_postgres toggle for this scheme
    4. Sends owner invitations for emails captured during /lots step
       (only if ``send_owner_invites=True``).
    5. Sends EC member invitations from ``step_data.ec_invites``
       (only if ``send_ec_invites=True``).
    6. Marks the onboarding session ``completed``.

    Auth: relaxed from super-admin only to {super_admin, strata_admin,
    strata_manager} so the SM-org's own admin can finalise their own
    building.  Same set used by ``/start`` and ``/lots`` per spec §2.2.

    Returns counts for genesis journals posted, owner invitations sent,
    EC invitations sent, and any per-invitation email failures.  Email
    failures are non-fatal — the underlying invitation rows are committed
    so admins can resend later via /admin/invitations/send.
    """
    _require_can_onboard(current_user)

    sa_tenant_id = current_user.get("tenant_id")
    if not sa_tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context.")

    async with async_session_context() as session:
        # Use bypass to read scheme_name across the tenant boundary (the
        # session row is in the SA tenant, the scheme is in the target
        # tenant). Bypass is bounded to this lookup; tenant context is
        # restored before any write.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": "00000000-0000-0000-0000-000000000000"},
        )
        result = await session.execute(
            text("""
                 SELECT s.session_id::TEXT, s.scheme_id::TEXT, s.tenant_id::TEXT, s.step_data
                      , sch.scheme_name, sch.scheme_number
                 FROM core.onboarding_sessions s
                          LEFT JOIN core.schemes sch ON sch.scheme_id = s.scheme_id
                 WHERE s.session_id = :sid
                   AND s.initiated_by = :uid
                   AND s.status NOT IN ('completed', 'abandoned')
                 """),
            {"sid": session_id, "uid": current_user["id"]},
        )
        row = result.fetchone()

        # Restore SA tenant before any further work
        await set_tenant(session, sa_tenant_id)

        if not row:
            raise HTTPException(status_code=404, detail="Session not found or already finalized.")

        session_id_val, scheme_id, tenant_id, step_data, scheme_name, scheme_number = row
        scheme_name_safe = scheme_name or "your strata scheme"
        building_id = derive_building_id_from_scheme_number(scheme_number)

        # ── Detect import-based vs clean-state onboarding ─────────────────────
        # An import-based session has opening_balances stored by the
        # import-opening-balances endpoint.  Genesis journals are NOT posted
        # here — that is Phase F-prime.  We log the balances and add them to
        # the response so the caller can confirm they were captured.
        import_opening_balances_data = step_data.get("opening_balances", []) if step_data else []
        historical_financials_summary = step_data.get("historical_financials_summary") if step_data else None
        owner_transfers_summary = step_data.get("owner_transfers_summary") if step_data else None
        is_import_based = bool(import_opening_balances_data or historical_financials_summary)

        if is_import_based:
            logger.info(
                "finalize: import-based session=%s — opening_balances=%d historical_financials=%s owner_transfers=%s",
                session_id, len(import_opening_balances_data),
                historical_financials_summary is not None,
                owner_transfers_summary is not None,
            )

        # Extract fund opening balances from step_data
        # Format: {"funds": [{"fund_type": "admin", "opening_balance_cents": 1000000, "as_at_date": "2026-01-01"}, ...]}
        # This is the clean-state ("Track A") manual-entry equivalent of
        # import_opening_balances_data — populated by OnboardingWizard.jsx's
        # buildCurrentBalancesFunds() right before finalize, never by the
        # CSV-upload (Track B) step.
        funds_data = step_data.get("funds", []) if step_data and not is_import_based else []
        genesis_count = 0
        cutover_record = None
        errors = []

        # Phase F Component 2 — Post genesis journals for each fund
        scheme_ref = SchemeRef(
            tenant_id=UUID(tenant_id),
            scheme_id=UUID(scheme_id)
        )

        # Track A (funds_data) and Track B (import_opening_balances_data) now post
        # through the SAME function, post_import_based_genesis_cutover: it
        # bootstraps finance.funds/accounting_periods/gl_accounts for the scheme
        # (idempotent — WHERE NOT EXISTS guards), resolves real fund_ids by type,
        # then posts. `genesis_opening_balances` is which dataset to post — kept
        # separate from `import_opening_balances_data`/`is_import_based` so the
        # response below still correctly reports whether this was a CSV import
        # or manual entry, even though both now share the posting call.
        #
        # Before this fix, Track A hand-rolled a separate, incomplete version of
        # this same logic — found broken two ways while adding live test coverage
        # for it on 2026-07-25 (the branch had never been reachable before this
        # session's GAP-ONBOARD-001 frontend change, since nothing populated
        # step_data["funds"] until buildCurrentBalancesFunds existed):
        # (1) `fund_id=UUID(fund_info.get("fund_id", uuid.uuid4()))` crashed with
        # "'UUID' object has no attribute 'replace'" whenever fund_id was absent —
        # the uuid.uuid4() default is already a UUID object, and wrapping it in
        # UUID(...) again is invalid; (2) even past that fix, it never called
        # ensure_scheme_finance_bootstrap, so a scheme with no pre-existing
        # accounting period failed with "No accounting period covers <date>" —
        # true for every brand-new scheme, i.e. every real caller. Reusing the
        # already-correct, already-tested Track B function fixes both at once
        # instead of patching a second, parallel implementation of the same logic.
        genesis_opening_balances = import_opening_balances_data if is_import_based else funds_data

        if genesis_opening_balances:
            try:
                posted_entries, cutover_record = await post_import_based_genesis_cutover(
                    session,
                    scheme_ref=scheme_ref,
                    opening_balances=genesis_opening_balances,
                    posted_by_user_id=UUID(current_user["id"]),
                    posted_by_user_name=current_user.get("full_name") or current_user.get("email") or "System Cutover",
                    building_id=building_id,
                    is_test_data=False,
                )
                genesis_count = len(posted_entries)
            except Exception as e:
                errors.append(str(e))

        if errors:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to post genesis for some funds: {'; '.join(errors)}"
            )

        if cutover_record:
            await record_cutover_in_session_step_data(
                session,
                session_id=session_id,
                initiated_by_user_id=current_user["id"],
                session_tenant_id=sa_tenant_id,
                cutover_record=cutover_record,
            )

        # ──────────────────────────────────────────────────────────────────
        # 3b. Trust bank account — captured on the "Trust Account Details"
        # step but, before this fix, silently discarded (see
        # tasks/GAP-ONBOARD-001). Creates one real trust_accounts_v2 row via
        # the same insert logic POST /trust/v2/accounts uses (the current,
        # non-deprecated trust-account API — NOT the legacy /trust/accounts
        # V1 API). Best-effort: a failure here must not block the rest of
        # finalize, matching the invitation-email failure pattern below.
        # Requires bank_name + bsb + account number — bsb/account_number are
        # mandatory on the V2 model, so an incomplete entry is skipped with a
        # note rather than posting a half-populated account. Also respects
        # the NEW building's own `trust_accounting` feature toggle — creating
        # a record while that toggle is off for this building would bypass
        # the same gate `POST /trust/v2/accounts` enforces via
        # `require_feature("trust_accounting")`, so this checks the toggle
        # explicitly rather than calling the shared insert helper directly.
        # ──────────────────────────────────────────────────────────────────
        trust_account_created = None
        trust_account_error = None
        trust_account_name = (step_data.get("trust_account_name") or "").strip() if step_data else ""
        if trust_account_name:
            trust_bank_name = (step_data.get("trust_bank_name") or "").strip()
            trust_bsb = (step_data.get("trust_bsb") or "").strip()
            trust_account_no = (step_data.get("trust_account_no") or "").strip()
            try:
                trust_accounting_enabled = await config_repo.resolve_feature_toggle(
                    building_id, "trust_accounting", default=True,
                )
            except Exception as e:  # noqa: BLE001
                # Best-effort, matching the rest of this block — a toggle-resolution
                # failure must not abort finalize. default=True (assume enabled) is
                # the fail-open choice consistent with resolve_feature_toggle's own
                # default and with trust_accounting being enabled globally by default.
                logger.warning("finalize: trust_accounting toggle check failed for building=%s: %s", building_id, e)
                trust_accounting_enabled = True
            if not trust_accounting_enabled:
                trust_account_error = (
                    "The Trust Accounting feature is disabled for this building — the trust "
                    "account was not created. Enable it in Feature Toggles, then create the "
                    "account manually via Trust Accounting."
                )
            elif not (trust_bank_name and trust_bsb and trust_account_no):
                trust_account_error = (
                    "Trust account name was entered but bank name, BSB, and account number are "
                    "all required to create the account — fill in all four fields, or create the "
                    "account later via Trust Accounting."
                )
            elif not _valid_bsb(trust_bsb):
                trust_account_error = f"Invalid BSB '{trust_bsb}' — expected 6 digits."
            elif not _valid_account_number(trust_account_no):
                trust_account_error = f"Invalid account number '{trust_account_no}'."
            else:
                try:
                    current_balance_cents = round(_safe_float(step_data.get("trust_opening_balance")) * 100)
                    # Mask the stripped digits, not the raw input — _valid_account_number already
                    # confirmed 4-10 digits once spaces are removed, so this always has >=4 digits.
                    # Explicit guard below (not just a comment) so a future change to the elif
                    # ordering or to _valid_account_number's rules fails loudly here rather than
                    # silently producing a malformed mask — flagged by Amazon Q's PR #547 review.
                    account_digits = trust_account_no.replace(" ", "")
                    if len(account_digits) < 4:
                        raise ValueError(f"Account number too short after validation: {len(account_digits)} digits")
                    masked_account_no = f"****{account_digits[-4:]}"
                    trust_account_created = await create_trust_account_v2_record(
                        TrustAccountPhase1Create(
                            account_type="admin_fund",
                            bsb=trust_bsb,
                            account_number_masked=masked_account_no,
                            bank_name=trust_bank_name,
                            account_name=trust_account_name,
                            current_balance_cents=current_balance_cents,
                        ),
                        building_id,
                        current_user,
                    )
                except Exception as e:  # noqa: BLE001
                    trust_account_error = str(e)
                    logger.warning("finalize: trust account creation failed for session=%s: %s", session_id, e)

        # ──────────────────────────────────────────────────────────────────
        # 3c. Levy plan(s) — Track A (clean-state) only. The wizard's
        # "Current Levy Schedule" step captures total_admin_levy / total_cw_levy
        # into step_data, but before this fix nothing consumed them — so a
        # Track A building finished onboarding with the levy totals recorded but
        # no levy_plans row to bill against (GAP-ONBOARD-002 Item 1). Create the
        # admin + capital-works (sinking) plans now from the captured totals.
        #
        # Best-effort: a failure here must not block finalize, matching the
        # trust-account block above. Import-based (Track B) sessions reconstruct
        # levies through a separate path and don't carry these keys, so they are
        # skipped. levy_plans is a tenant-scoped Mongo collection, so we set the
        # Mongo building context for the write and restore it afterwards —
        # finalize otherwise runs in a Postgres-native context with no Mongo
        # building scope set.
        # ──────────────────────────────────────────────────────────────────
        levy_plans_created = None
        levy_plan_error = None
        if not is_import_based and step_data and (
                step_data.get("total_admin_levy") or step_data.get("total_cw_levy")
        ):
            _prev_ctx_bid = get_ctx_building_id()
            try:
                set_ctx_building_id(building_id)
                levy_plan_summary = await create_levy_plans_from_onboarding(
                    building_id=building_id,
                    step_data=step_data,
                )
                levy_plans_created = levy_plan_summary.get("created") or []
            except Exception as e:  # noqa: BLE001
                levy_plan_error = str(e)
                logger.warning(
                    "finalize: levy-plan creation failed for session=%s building=%s: %s",
                    session_id, building_id, e,
                )
            finally:
                set_ctx_building_id(_prev_ctx_bid)

        # ──────────────────────────────────────────────────────────────────
        # 4 & 5. Invitations — owners (queued during /lots) + EC members
        # ──────────────────────────────────────────────────────────────────
        # Owner emails were stashed by /lots into step_data.owner_emails.
        # EC invites are objects {email, first_name, last_name, role?} pushed
        # via PATCH .../{session_id}.
        #
        # Both lists are deduplicated by (lower-cased email).  We don't
        # short-circuit on existing-user/active-invitation checks here; the
        # invitation-send endpoint applies those checks at claim time.
        #
        # Email send is best-effort — if it fails the invitation row is
        # still committed so an admin can resend.  Failures are returned in
        # the response under ``invitation_email_failures`` so the UI can
        # surface a "Resend" CTA per failed address.
        owner_emails = list({e.lower().strip()
                             for e in (step_data.get("owner_emails") or [])
                             if e}) if step_data else []
        ec_invites_data = step_data.get("ec_invites", []) if step_data else []

        # Each entry holds (email, raw_token, role_label) so the post-commit
        # email send can build the actual claim URL. Without this list, the
        # raw_token generated per-iteration would fall out of scope when the
        # for-loop ends — leaving the DB with a hash but no way to deliver
        # the corresponding claim link to the recipient (the bug Amazon Q
        # flagged on PR #393 and which my own audit had already noted).
        owner_invitations_pending: list[dict] = []
        ec_invitations_pending: list[dict] = []
        invitation_email_failures: list[dict] = []

        # Switch RLS context to the scheme's tenant so invitation rows land
        # against the right tenant.  ``tenant_id`` here is the scheme's
        # tenant (fetched in step 1, line ~539), not the SA's tenant.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": tenant_id},
        )

        if body.send_owner_invites and owner_emails:
            for email in owner_emails:
                try:
                    raw_token, token_hash = _generate_invite_token()
                    inv_expires = datetime.now(timezone.utc) + timedelta(days=14)
                    await session.execute(
                        text("""
                             INSERT INTO core.user_invitations
                             (tenant_id, scheme_id, email, invited_role,
                              invited_by, claim_token_sha256, expires_at)
                             VALUES (:tid, :sid, :email, CAST('owner' AS core.user_role),
                                     :by, :hash, :exp)
                             """),
                        {"tid": tenant_id, "sid": scheme_id, "email": email,
                         "by": current_user["id"], "hash": token_hash, "exp": inv_expires},
                    )
                    # Capture raw_token here — it's required AFTER commit when
                    # we send the email. We store it briefly in process memory
                    # (never logged, never returned in response) and clear it
                    # by letting the list go out of scope at end-of-handler.
                    owner_invitations_pending.append({
                        "email": email,
                        "raw_token": raw_token,
                    })
                except Exception as e:
                    invitation_email_failures.append({"email": email, "stage": "row_insert", "error": str(e)})

        if body.send_ec_invites and ec_invites_data:
            for ec in ec_invites_data:
                ec_email = (ec.get("email") or "").strip().lower()
                if not ec_email:
                    continue
                ec_role = ec.get("role") or "ec_member"
                try:
                    raw_token, token_hash = _generate_invite_token()
                    inv_expires = datetime.now(timezone.utc) + timedelta(days=14)
                    await session.execute(
                        text("""
                             INSERT INTO core.user_invitations
                             (tenant_id, scheme_id, email, invited_role,
                              invited_by, claim_token_sha256, expires_at,
                              prefill_first_name, prefill_last_name)
                             VALUES (:tid, :sid, :email, CAST(:role AS core.user_role),
                                     :by, :hash, :exp, :fn, :ln)
                             """),
                        {"tid": tenant_id, "sid": scheme_id, "email": ec_email,
                         "role": ec_role, "by": current_user["id"],
                         "hash": token_hash, "exp": inv_expires,
                         "fn": ec.get("first_name"), "ln": ec.get("last_name")},
                    )
                    ec_invitations_pending.append({
                        "email": ec_email,
                        "raw_token": raw_token,
                        "role_label": ec_role.replace("_", " ").title(),
                    })
                except Exception as e:
                    invitation_email_failures.append({"email": ec_email, "stage": "row_insert", "error": str(e)})

        # Restore SA tenant for the session UPDATE below
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": sa_tenant_id},
        )

        # Mark onboarding session as completed
        result = await session.execute(
            text("""
                 UPDATE core.onboarding_sessions
                 SET status     = 'completed',
                     updated_at = NOW()
                 WHERE session_id = :sid
                   AND initiated_by = :uid
                   AND status NOT IN ('completed', 'abandoned') RETURNING session_id::TEXT, scheme_id::TEXT, tenant_id::TEXT
                 """),
            {"sid": session_id, "uid": current_user["id"]},
        )
        final_row = result.fetchone()

    if not final_row:
        raise HTTPException(status_code=404, detail="Failed to finalize session.")

    # ──────────────────────────────────────────────────────────────────────
    # Email send happens AFTER the DB transaction commits (which it has by
    # this line — the ``async with`` block above has exited). For each
    # pending invitation we now have BOTH the email address AND the raw
    # token (held in process memory in owner_invitations_pending /
    # ec_invitations_pending) so we can construct a real claim URL the
    # recipient can click.  This fixes the bug Amazon Q flagged on PR
    # #393 and which my own audit had already noted (raw_token previously
    # fell out of scope at end-of-iteration, leaving the DB with a hash
    # but no way to deliver the corresponding link).
    #
    # Email send is best-effort — if it fails the invitation row is
    # already committed and an admin can resend via
    # /admin/invitations/send (existing endpoint).  Failures are returned
    # under ``invitation_email_failures`` so the UI can surface a Resend
    # CTA per failed address.
    #
    # SECURITY: raw_token is held briefly in process memory only.  It is
    # never logged, never stored to disk, never returned in the response.
    # The pending lists are local to this function and go out of scope on
    # return.
    # ──────────────────────────────────────────────────────────────────────
    owner_emails_sent: list[str] = []
    if body.send_owner_invites and owner_invitations_pending:
        for inv in owner_invitations_pending:
            email = inv["email"]
            claim_url = _build_claim_url(inv["raw_token"])
            try:
                await send_email_async(
                    to_email=email,
                    subject=f"You've been invited to the {scheme_name_safe} owners portal",
                    html_content=_owner_invitation_email_html(
                        scheme_name=scheme_name_safe,
                        claim_url=claim_url,
                    ),
                )
                owner_emails_sent.append(email)
            except Exception as exc:  # noqa: BLE001
                invitation_email_failures.append(
                    {"email": email, "stage": "email_send", "error": str(exc)}
                )
                logger.warning("Owner invitation email failed for %s: %s", email, exc)

    ec_emails_sent: list[str] = []
    if body.send_ec_invites and ec_invitations_pending:
        for inv in ec_invitations_pending:
            email = inv["email"]
            claim_url = _build_claim_url(inv["raw_token"])
            try:
                await send_email_async(
                    to_email=email,
                    subject=f"You've been invited to the {scheme_name_safe} EC",
                    html_content=_ec_invitation_email_html(
                        scheme_name=scheme_name_safe,
                        claim_url=claim_url,
                        role_label=inv["role_label"],
                    ),
                )
                ec_emails_sent.append(email)
            except Exception as exc:  # noqa: BLE001
                invitation_email_failures.append(
                    {"email": email, "stage": "email_send", "error": str(exc)}
                )
                logger.warning("EC invitation email failed for %s: %s", email, exc)

    response = {
        "message": "Onboarding complete. Scheme is live.",
        "session_id": final_row[0],
        "scheme_id": final_row[1],
        "tenant_id": final_row[2],
        "genesis_journals_posted": genesis_count,
        # Counts reflect what was actually committed + dispatched. A row
        # is "sent" only when both INSERT and email succeeded; partial
        # failures are listed in invitation_email_failures.
        "owner_invitations_created": len(owner_invitations_pending),
        "owner_emails_sent": len(owner_emails_sent),
        "ec_invitations_created": len(ec_invitations_pending),
        "ec_emails_sent": len(ec_emails_sent),
    }
    if trust_account_created:
        response["trust_account_created"] = trust_account_created
    if trust_account_error:
        response["trust_account_error"] = trust_account_error
    if levy_plans_created:
        response["levy_plans_created"] = levy_plans_created
    if levy_plan_error:
        response["levy_plan_error"] = levy_plan_error
    if is_import_based:
        response["import_based"] = True
        response["opening_balances_captured"] = len(import_opening_balances_data)
        if historical_financials_summary:
            response["historical_financials_summary"] = historical_financials_summary
        if owner_transfers_summary:
            response["owner_transfers_summary"] = owner_transfers_summary
        response["note"] = (
            "Import-based onboarding: genesis journals posted from captured opening balances "
            "and canonical financial cutover toggles enabled."
        )
    if cutover_record:
        response["cutover"] = cutover_record
    if invitation_email_failures:
        response["invitation_email_failures"] = invitation_email_failures
        response["code"] = "partial_invitation_failures"
    return response
