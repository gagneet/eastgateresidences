# @featuretrace:financial-mock-boundary — per-building mock/live switches for external financial integrations.
# Layer: router
# Data flow: BuildingIntegrationsCard.tsx → GET/PUT /api/buildings/{building_id}/integrations/mock-mode
#            → config_repo.upsert_feature_toggle_override → core.feature_toggle_overrides
#            → services/financial_mock_mode.py → DEFT / Stripe / ProviderRegistry / ABA.
# Related: backend/services/financial_mock_mode.py
#          backend/core/toggle_classification.py
#          backend/db_postgres/repos/config_repo.py
#          backend/alembic/versions/0095_mock_boundary_toggles.py
# Toggle: financial_services_mock, bank_direct_debit_mock
# Table: core.feature_toggles, core.feature_toggle_overrides
# Tests: tests/backend/test_building_integration_mock_mode.py
"""Per-building mock/live boundary, manageable by the building's own managers.

Deliberately NOT part of routers/feature_toggles.py. That surface is the platform
toggle catalogue: it is gated on ``platform.feature_flags.manage`` (super_admin
only) and lets a caller write GLOBAL rows. Neither is right here — these switches
belong to one building, and strata managers and strata admins need to hold them
for the buildings they are assigned to.

So this router exposes exactly two keys, writes only per-building overrides, and
authorises with the building-scoped ``building.integrations.*`` capabilities,
which resolve against the caller's verified role assignments. A manager of one
building cannot read or change another's.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db_postgres.repos import config_repo
from services.capability_registry import require_capability
from services.financial_mock_mode import (
    BANK_DIRECT_DEBIT_MOCK_KEY,
    FINANCIAL_SERVICES_MOCK_KEY,
    bank_direct_debit_mocked,
    financial_services_mocked,
    global_mock_override_active,
)
from utils.auth import effective_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/buildings", tags=["Building Integrations"])

#: The only keys this router will write. An allow-list rather than a parameter:
#: it is the difference between a narrow per-building control and a second,
#: less-guarded way to write arbitrary toggle overrides.
_MANAGED_KEYS = {
    FINANCIAL_SERVICES_MOCK_KEY: {
        "label": "Mock financial services",
        "detail": (
            "DEFT/BPAY, Stripe, the provider protocols and outbound payment (ABA) "
            "execution run against mocks. Demo Bank is unaffected — it is a first-party "
            "emulator with its own toggles."
        ),
    },
    BANK_DIRECT_DEBIT_MOCK_KEY: {
        "label": "Mock bank direct debit & transaction history",
        "detail": (
            "Direct debit and real transaction-history retrieval are mocked. Not yet "
            "consumed by any live code path — the switch exists ahead of that work."
        ),
    },
}


class IntegrationMockState(BaseModel):
    """One switch's effective state for one building."""

    feature_key: str
    label: str
    detail: str
    is_mocked: bool = Field(..., description="True = this building uses mock implementations.")
    forced_by_environment: bool = Field(
        ...,
        description=(
            "True when MOCK_EXTERNAL_SERVICES forces mock process-wide, in which case "
            "is_mocked is True regardless of the per-building override."
        ),
    )


class IntegrationMockStatusResponse(BaseModel):
    building_id: str
    forced_by_environment: bool
    switches: list[IntegrationMockState]


class IntegrationMockUpdate(BaseModel):
    is_mocked: bool = Field(
        ...,
        description="False points this building's integrations at LIVE financial providers.",
    )
    reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Recorded on the override row. Required when going live.",
    )


async def _state_for(building_id: str) -> IntegrationMockStatusResponse:
    """Resolve both switches through the same helper the call sites use."""
    forced = global_mock_override_active()
    financial = await financial_services_mocked(building_id)
    direct_debit = await bank_direct_debit_mocked(building_id)
    resolved = {
        FINANCIAL_SERVICES_MOCK_KEY: financial,
        BANK_DIRECT_DEBIT_MOCK_KEY: direct_debit,
    }
    return IntegrationMockStatusResponse(
        building_id=building_id,
        forced_by_environment=forced,
        switches=[
            IntegrationMockState(
                feature_key=key,
                label=meta["label"],
                detail=meta["detail"],
                is_mocked=resolved[key],
                forced_by_environment=forced,
            )
            for key, meta in _MANAGED_KEYS.items()
        ],
    )


@router.get(
    "/{building_id}/integrations/mock-mode",
    response_model=IntegrationMockStatusResponse,
)
async def get_building_integration_mock_mode(
        building_id: str,
        current_user: dict = Depends(
            require_capability(
                "building.integrations.view",
                scope_params={"building_id": "building_id"},
            )
        ),
):
    """Effective mock/live state for the building named in the path.

    Authorisation (BOLA / OWASP API1:2023): ``building_id`` is caller-supplied, so
    the capability is scoped to the building in the PATH rather than the caller's
    session building. A strata manager sees only buildings their verified role
    assignments cover.
    """
    del current_user
    return await _state_for(building_id)


@router.put(
    "/{building_id}/integrations/mock-mode/{feature_key}",
    response_model=IntegrationMockStatusResponse,
)
async def set_building_integration_mock_mode(
        building_id: str,
        feature_key: str,
        payload: IntegrationMockUpdate,
        current_user: dict = Depends(
            require_capability(
                "building.integrations.manage",
                scope_params={"building_id": "building_id"},
            )
        ),
):
    """Switch one integration between mock and live FOR THIS BUILDING ONLY.

    Writes a row in core.feature_toggle_overrides; the global default stays mocked.
    There is deliberately no global write path here — a single call that took every
    building live at once is the failure this whole classification exists to prevent
    (see the P0.3 note in core/toggle_classification.py).
    """
    if feature_key not in _MANAGED_KEYS:
        raise HTTPException(
            status_code=404,
            detail=f"'{feature_key}' is not a building integration switch.",
        )

    if not payload.is_mocked and not (payload.reason or "").strip():
        # Going live is the consequential direction, so it must say why. Returning to
        # mock is the safe direction and needs no justification.
        raise HTTPException(
            status_code=422,
            detail="A reason is required when switching an integration to live providers.",
        )

    if global_mock_override_active() and not payload.is_mocked:
        raise HTTPException(
            status_code=409,
            detail=(
                "MOCK_EXTERNAL_SERVICES is set process-wide, so this building cannot be "
                "switched to live providers. Clear the environment override first."
            ),
        )

    # Resolve the actor BEFORE writing, and refuse if we cannot.
    #
    # upsert_feature_toggle_override passes require_existing=True, and that fallback
    # returns the OLDEST ACTIVE SUPER_ADMIN when the caller cannot be matched by uuid
    # or email (config_repo._fallback_actor_user_id). For most toggle writes an
    # approximate actor is an acceptable trade for not failing the write. Here it is
    # not: core.feature_toggle_overrides.set_by is the record of who took a building
    # to live financial providers, and silently attributing that to an uninvolved
    # super admin is worse than refusing the change.
    actor_uuid = await config_repo.resolve_actor_user_id(
        str(current_user.get("id")) if current_user.get("id") else None,
        current_user.get("email"),
    )
    if actor_uuid is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Your account could not be resolved in the control plane, so this "
                "change cannot be attributed to you. Ask a super admin to make it."
            ),
        )

    try:
        await config_repo.upsert_feature_toggle_override(
            building_id,
            feature_key,
            is_enabled=payload.is_mocked,
            actor_user_id=actor_uuid,
            actor_email=current_user.get("email"),
            reason=payload.reason,
        )
    except RuntimeError as exc:
        # resolve_scheme_context found no Postgres scheme for this building.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.warning(
        "building integration switch: %s set to is_mocked=%s for building_id=%s by "
        "user=%s role=%s reason=%r",
        feature_key, payload.is_mocked, building_id,
        current_user.get("id"), effective_role(current_user), payload.reason,
    )
    return await _state_for(building_id)
