# @featuretrace:finance-postgres-write-cutover — Admin API exposes route-level finance write readiness for selected PostgreSQL write promotion.
# Data flow: CutoverStatusPage -> GET /admin/cutover/status-finance-write-routes/{building_id} -> finance_write_cutover_service (building-scoped).
# Related: backend/services/finance_write_cutover_service.py
#          frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx
# @featuretrace:cutover-control-plane — Admin API endpoints for cutover mode inspection and promotion.
# Layer: router
# Data flow: super_admin/strata_manager browser → /api/admin/cutover/* → cutover_status_service → core.* tables (building-scoped).
# Related: backend/services/cutover_status_service.py
#          backend/models/cutover_status.py
#          frontend/src/pages/dashboard/admin/CutoverStatusPage.tsx
#          docs/architecture/feature-toggle-governance.md
# Toggle: N/A — control plane is not feature-gated (guarded by RBAC only)
# Tests: tests/backend/test_cutover_admin_router.py

"""Cutover control plane admin API.

RBAC contract:
  - super_admin   → full access: list all buildings, promote, rollback, diffs
  - strata_manager / strata_admin → read-only status for assigned buildings only
  - ec_member / admin_staff       → read-only status for own building only
  - owner / tenant / guest        → 403 on every endpoint

Promotion safety is enforced entirely in cutover_status_service; the router
only handles auth and request serialisation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from models.cutover_status import (
    CutoverActionResponse,
    CutoverPromoteRequest,
    CutoverRollbackRequest,
    CutoverStatusDetailResponse,
    CutoverStatusListResponse,
    RecordShadowDiffRequest,
    ShadowDiffRecord,
)
from models.user import UserRole
from services import cutover_status_service as svc
from services.capability_registry import assert_capability_hydrated, can_hydrated, require_capability
from services.finance_route_cutover_service import (
    get_finance_route_readiness_table,
    has_any_promotable_finance_route,
)
from services.finance_write_cutover_service import get_finance_write_readiness_table
from services.powerhouse_command_foundation_health import get_powerhouse_command_foundation_health
from utils.auth import effective_role, get_current_building, get_current_user

router = APIRouter(prefix="/admin/cutover", tags=["Cutover Control Plane"])


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------

# Async because it hydrates verified building claims before deciding; the
# synchronous assert_capability() hydrated nothing, so the decision rested on
# the caller's INHERITED building_id claim (their stored default_scheme_id when
# the JWT names no building) rather than their live role assignments. GAP-SEC-014.
async def _assert_building_access(current_user: dict, building_id: str) -> None:
    """Enforce the canonical building-scoped cutover capability."""
    await assert_capability_hydrated(
        current_user,
        "building.cutover.view",
        {"building_id": building_id},
    )

# ---------------------------------------------------------------------------
# GET /admin/cutover/status
# List all registered domains across all buildings (super_admin only for all;
# non-super_admin gets their own building only).
# ---------------------------------------------------------------------------

@router.get("/status", response_model=CutoverStatusListResponse)
async def list_all_cutover_status(
    building_id: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """List cutover status for all (or filtered) building/domain pairs.

    super_admin sees all buildings.
    Other roles see only their own building.
    """
    if building_id is None and await can_hydrated(
        current_user,
        "platform.security.manage",
        {"platform_id": "platform"},
    ):
        pass
    else:
        user_building = (
            current_user.get("building_id")
            or current_user.get("current_building_id")
            or ""
        )
        building_id = building_id or user_building
        await _assert_building_access(current_user, building_id)

    items = await svc.list_all_cutover_status(
        building_id_filter=building_id,
        domain_filter=domain,
    )
    return CutoverStatusListResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# GET /admin/cutover/status/{building_id}
# All domains for a specific building.
# ---------------------------------------------------------------------------

@router.get("/status/{building_id}", response_model=CutoverStatusListResponse)
async def list_building_cutover_status(
    building_id: str,
    domain: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_building_cutover_status
    Path: backend/routers/cutover_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _assert_building_access(current_user, building_id)
    items = await svc.list_all_cutover_status(
        building_id_filter=building_id,
        domain_filter=domain,
    )
    return CutoverStatusListResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# GET /admin/cutover/status/{building_id}/{domain}
# Detail for a single domain including recent diffs and audit log.
# ---------------------------------------------------------------------------

@router.get(
    "/status/{building_id}/{domain}",
    response_model=CutoverStatusDetailResponse,
)
async def get_domain_cutover_status(
    building_id: str,
    domain: str,
    current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: get_domain_cutover_status
    Path: backend/routers/cutover_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _assert_building_access(current_user, building_id)

    status = await svc.get_cutover_status(building_id, domain)
    if not status:
        raise HTTPException(
            status_code=404,
            detail=f"No cutover status registered for domain '{domain}' in building {building_id}",
        )

    diffs = await svc.list_shadow_diffs(building_id=building_id, domain=domain, limit=10)
    audits = await svc.list_audit_log(building_id=building_id, domain=domain, limit=10)

    return CutoverStatusDetailResponse(
        status=status,
        recent_diffs=diffs,
        recent_audits=audits,
    )


# ---------------------------------------------------------------------------
# POST /admin/cutover/{building_id}/{domain}/shadow
# Enter shadow mode (mongo_primary → postgres_shadow).
# ---------------------------------------------------------------------------

@router.post(
    "/{building_id}/{domain}/shadow",
    response_model=CutoverActionResponse,
)
async def enter_shadow_mode(
    building_id: str,
    domain: str,
    payload: CutoverPromoteRequest,
    current_user: dict = Depends(require_capability(
        "building.cutover.manage",
        scope_params={"building_id": "building_id"},
    )),
):
    """Advance domain from mongo_primary to postgres_shadow."""
    # Shadow entry is the same code path as promote — promote handles the transition
    # from mongo_primary → postgres_shadow.
    actor_id = str(current_user.get("id") or current_user.get("_id") or "")
    actor_r = effective_role(current_user)

    current_status = await svc.get_or_default_cutover_status(building_id, domain)
    if current_status.mode.value != "mongo_primary":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Domain is in mode '{current_status.mode.value}'. "
                "Enter shadow mode is only valid from mongo_primary."
            ),
        )

    updated, audit_id = await svc.promote_domain(
        building_id=building_id,
        domain=domain,
        actor_user_id=actor_id,
        actor_role=actor_r,
        reason=payload.reason,
        skip_p0_check=payload.skip_p0_check and actor_r == UserRole.SUPER_ADMIN,
    )

    return CutoverActionResponse(
        building_id=building_id,
        domain=domain,
        previous_mode="mongo_primary",
        new_mode=updated.mode.value,
        action="entered_shadow",
        audit_id=audit_id,
        message=f"Domain '{domain}' is now in postgres_shadow mode for building {building_id}",
    )


# ---------------------------------------------------------------------------
# POST /admin/cutover/{building_id}/{domain}/promote-read
# postgres_shadow → postgres_read
# ---------------------------------------------------------------------------

@router.post(
    "/{building_id}/{domain}/promote-read",
    response_model=CutoverActionResponse,
)
async def promote_to_read(
    building_id: str,
    domain: str,
    payload: CutoverPromoteRequest,
    current_user: dict = Depends(require_capability(
        "building.cutover.manage",
        scope_params={"building_id": "building_id"},
    )),
):
    """Advance domain from postgres_shadow to postgres_read."""
    actor_id = str(current_user.get("id") or current_user.get("_id") or "")
    actor_r = effective_role(current_user)

    current_status = await svc.get_or_default_cutover_status(building_id, domain)
    if current_status.mode.value != "postgres_shadow":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Domain is in mode '{current_status.mode.value}'. "
                "promote-read is only valid from postgres_shadow."
            ),
        )

    if domain == "finance_ledger":
        promotable, rows = await has_any_promotable_finance_route(building_id=building_id)
        if not promotable:
            blocked = [
                f"{row['route_key']}: {row['blocked_reason'] or row['readiness_status']}"
                for row in rows
                if row["postgres_read_supported"]
            ]
            raise HTTPException(
                status_code=409,
                detail=(
                    "finance_ledger promote-read blocked: no route is eligible for postgres_read. "
                    f"Details: {', '.join(blocked) if blocked else 'no promotable routes configured'}"
                ),
            )

    updated, audit_id = await svc.promote_domain(
        building_id=building_id,
        domain=domain,
        actor_user_id=actor_id,
        actor_role=actor_r,
        reason=payload.reason,
        skip_p0_check=payload.skip_p0_check and actor_r == UserRole.SUPER_ADMIN,
    )

    return CutoverActionResponse(
        building_id=building_id,
        domain=domain,
        previous_mode="postgres_shadow",
        new_mode=updated.mode.value,
        action="promoted_read",
        audit_id=audit_id,
        message=f"Domain '{domain}' is now in postgres_read mode for building {building_id}",
    )


@router.get("/status-finance-routes/{building_id}", response_model=list[dict])
async def get_finance_route_readiness(
    building_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return finance route-level readiness and postgres_read eligibility."""
    await _assert_building_access(current_user, building_id)
    return await get_finance_route_readiness_table(building_id=building_id)


@router.get("/status-finance-write-routes/{building_id}", response_model=list[dict])
async def get_finance_write_route_readiness(
    building_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return finance write route-level readiness and postgres_write eligibility."""
    await _assert_building_access(current_user, building_id)
    return await get_finance_write_readiness_table(building_id=building_id)


# ---------------------------------------------------------------------------
# GET /admin/cutover/powerhouse-command-foundation/{building_id}
# Read-only outbox/idempotency/domain-readiness snapshot for the P2A command
# foundation. Diagnostic only — does not promote or mutate anything.
# ---------------------------------------------------------------------------

@router.get("/powerhouse-command-foundation/{building_id}", response_model=dict)
async def get_powerhouse_command_foundation_health_endpoint(
    building_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return Powerhouse command-foundation health: outbox, idempotency, per-domain readiness."""
    await _assert_building_access(current_user, building_id)
    return await get_powerhouse_command_foundation_health(building_id)


# ---------------------------------------------------------------------------
# POST /admin/cutover/{building_id}/{domain}/promote-write
# postgres_read → postgres_write
# ---------------------------------------------------------------------------

@router.post(
    "/{building_id}/{domain}/promote-write",
    response_model=CutoverActionResponse,
)
async def promote_to_write(
    building_id: str,
    domain: str,
    payload: CutoverPromoteRequest,
    current_user: dict = Depends(require_capability(
        "building.cutover.manage",
        scope_params={"building_id": "building_id"},
    )),
):
    """Advance domain from postgres_read to postgres_write."""
    actor_id = str(current_user.get("id") or current_user.get("_id") or "")
    actor_r = effective_role(current_user)

    current_status = await svc.get_or_default_cutover_status(building_id, domain)
    if current_status.mode.value != "postgres_read":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Domain is in mode '{current_status.mode.value}'. "
                "promote-write is only valid from postgres_read."
            ),
        )

    updated, audit_id = await svc.promote_domain(
        building_id=building_id,
        domain=domain,
        actor_user_id=actor_id,
        actor_role=actor_r,
        reason=payload.reason,
        skip_p0_check=payload.skip_p0_check and actor_r == UserRole.SUPER_ADMIN,
    )

    return CutoverActionResponse(
        building_id=building_id,
        domain=domain,
        previous_mode="postgres_read",
        new_mode=updated.mode.value,
        action="promoted_write",
        audit_id=audit_id,
        message=f"Domain '{domain}' is now in postgres_write mode for building {building_id}",
    )


# ---------------------------------------------------------------------------
# POST /admin/cutover/{building_id}/{domain}/rollback
# Roll back by one step.
# ---------------------------------------------------------------------------

@router.post(
    "/{building_id}/{domain}/rollback",
    response_model=CutoverActionResponse,
)
async def rollback_domain(
    building_id: str,
    domain: str,
    payload: CutoverRollbackRequest,
    current_user: dict = Depends(require_capability(
        "building.cutover.manage",
        scope_params={"building_id": "building_id"},
    )),
):
    """Roll domain back by one step in the cutover lifecycle."""
    actor_id = str(current_user.get("id") or current_user.get("_id") or "")
    actor_r = effective_role(current_user)

    current_status = await svc.get_or_default_cutover_status(building_id, domain)
    previous_mode = current_status.mode.value

    updated, audit_id = await svc.rollback_domain(
        building_id=building_id,
        domain=domain,
        actor_user_id=actor_id,
        actor_role=actor_r,
        reason=payload.reason,
    )

    return CutoverActionResponse(
        building_id=building_id,
        domain=domain,
        previous_mode=previous_mode,
        new_mode=updated.mode.value,
        action="rolled_back",
        audit_id=audit_id,
        message=(
            f"Domain '{domain}' for building {building_id} rolled back "
            f"from '{previous_mode}' to '{updated.mode.value}'"
        ),
    )


# ---------------------------------------------------------------------------
# GET /admin/cutover/{building_id}/{domain}/diffs
# Shadow diff records for a domain.
# ---------------------------------------------------------------------------

@router.get(
    "/{building_id}/{domain}/diffs",
    response_model=list[ShadowDiffRecord],
)
async def list_domain_diffs(
    building_id: str,
    domain: str,
    resolved: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: list_domain_diffs
    Path: backend/routers/cutover_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await _assert_building_access(current_user, building_id)
    return await svc.list_shadow_diffs(
        building_id=building_id,
        domain=domain,
        resolved=resolved,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# POST /admin/cutover/{building_id}/{domain}/record-diff
# Record a shadow divergence (called by service layer during shadow reads).
# super_admin only — internal use.
# ---------------------------------------------------------------------------

@router.post(
    "/{building_id}/{domain}/record-diff",
    response_model=dict,
)
async def record_shadow_diff(
    building_id: str,
    domain: str,
    payload: RecordShadowDiffRequest,
    current_user: dict = Depends(require_capability(
        "building.cutover.manage",
        scope_params={"building_id": "building_id"},
    )),
):
    """Generated function header.

    Function: record_shadow_diff
    Path: backend/routers/cutover_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    diff_id = await svc.record_shadow_diff(
        building_id=building_id,
        domain=domain,
        route=payload.route,
        diff_type=payload.diff_type,
        mongo_value=payload.mongo_value,
        pg_value=payload.pg_value,
        divergence_score=payload.divergence_score,
        notes=payload.notes,
    )
    return {"diff_id": diff_id, "building_id": building_id, "domain": domain}
