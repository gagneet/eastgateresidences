"""
tests/backend/test_sentinel_historical_reconstruction_rbac.py

Security/RBAC hardening for POST /financial/matching/historical-reconstruction/run
(GAP-FIN-015 criterion 6). Confirms:
  - Only strata_manager+ roles may trigger reconstruction (owner -> 403).
  - Requests are scoped to the caller's own building_id — the function never
    accepts an alternate building_id from the request body (there isn't one),
    only from Depends(get_current_building), so cross-tenant triggering is
    structurally impossible, not just policy.
  - from_year > to_year is rejected before any DB write is attempted.
  - A service-layer ValueError (e.g. no annual_levies for the building) surfaces
    as 409, not a raw 500 leaking internals.

Patch target: "routers.financial_matching.synthesize_historical_levy_payments"
(imported name inside the router module — patching the source module would not
affect the already-bound reference in financial_matching's namespace).

Note: endpoint functions are called directly (bypassing FastAPI DI), matching
tests/backend/routers/test_financial_matching.py's established convention.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers.financial_matching import (
    HistoricalReconstructionRequest,
    run_historical_reconstruction,
)

BUILDING_A = "13195"
BUILDING_B = "16244"

_MANAGER_USER = {"role": "strata_manager", "effective_role": "strata_manager",
                 "email": "manager@test.com", "_id": "user-mgr-001"}
_OWNER_USER = {"role": "owner", "effective_role": "owner",
               "email": "owner@test.com", "_id": "user-own-001"}
_EC_MEMBER_USER = {"role": "ec_member", "effective_role": "ec_member",
                    "email": "ec@test.com", "_id": "user-ec-001"}

_SUCCESS_RESULT = {
    "building_id": BUILDING_A,
    "dry_run": True,
    "years_range": "2022-2022",
    "units_processed": 2,
    "inserted": 16,
    "already_existed": 0,
    "years_skipped": 0,
    "by_strata_year": {"2022": 16},
}


@pytest.mark.asyncio
async def test_owner_role_returns_403():
    body = HistoricalReconstructionRequest(from_year=2022, to_year=2022, dry_run=True)
    with pytest.raises(HTTPException) as exc:
        await run_historical_reconstruction(
            body, current_user=_OWNER_USER, building_id=BUILDING_A,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_manager_role_succeeds():
    body = HistoricalReconstructionRequest(from_year=2022, to_year=2022, dry_run=True)
    with patch(
        "routers.financial_matching.synthesize_historical_levy_payments",
        AsyncMock(return_value=dict(_SUCCESS_RESULT)),
    ) as mock_synth:
        result = await run_historical_reconstruction(
            body, current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert result.building_id == BUILDING_A
    assert result.inserted == 16
    mock_synth.assert_awaited_once()
    _, kwargs = mock_synth.call_args
    assert kwargs["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_ec_member_role_succeeds():
    """ec_member is a valid _DECIDE_ROLES member alongside strata_manager+."""
    body = HistoricalReconstructionRequest(from_year=2022, to_year=2022, dry_run=True)
    with patch(
        "routers.financial_matching.synthesize_historical_levy_payments",
        AsyncMock(return_value=dict(_SUCCESS_RESULT)),
    ):
        result = await run_historical_reconstruction(
            body, current_user=_EC_MEMBER_USER, building_id=BUILDING_A,
        )
    assert result.building_id == BUILDING_A


@pytest.mark.asyncio
async def test_request_is_always_scoped_to_the_caller_own_building_id():
    """There is no building_id field on HistoricalReconstructionRequest — the
    only source of building scoping is Depends(get_current_building). Calling
    for BUILDING_B must pass BUILDING_B through, never BUILDING_A, proving
    cross-tenant triggering is structurally impossible (no field exists to
    smuggle another building's id through the request body)."""
    assert "building_id" not in HistoricalReconstructionRequest.model_fields

    body = HistoricalReconstructionRequest(from_year=2022, to_year=2022, dry_run=True)
    with patch(
        "routers.financial_matching.synthesize_historical_levy_payments",
        AsyncMock(return_value={**_SUCCESS_RESULT, "building_id": BUILDING_B}),
    ) as mock_synth:
        result = await run_historical_reconstruction(
            body, current_user=_MANAGER_USER, building_id=BUILDING_B,
        )

    _, kwargs = mock_synth.call_args
    assert kwargs["building_id"] == BUILDING_B
    assert result.building_id == BUILDING_B


@pytest.mark.asyncio
async def test_from_year_after_to_year_returns_400_before_any_db_call():
    body = HistoricalReconstructionRequest(from_year=2024, to_year=2022, dry_run=True)
    with patch(
        "routers.financial_matching.synthesize_historical_levy_payments",
        AsyncMock(),
    ) as mock_synth:
        with pytest.raises(HTTPException) as exc:
            await run_historical_reconstruction(
                body, current_user=_MANAGER_USER, building_id=BUILDING_A,
            )

    assert exc.value.status_code == 400
    mock_synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_value_error_surfaces_as_409_not_500():
    """No annual_levies/units for the building raises ValueError in the service
    layer — must become a clean 409, not an unhandled 500 leaking a stack trace."""
    body = HistoricalReconstructionRequest(from_year=2022, to_year=2022, dry_run=True)
    with patch(
        "routers.financial_matching.synthesize_historical_levy_payments",
        AsyncMock(side_effect=ValueError("No annual_levies documents found for building_id='13195'.")),
    ):
        with pytest.raises(HTTPException) as exc:
            await run_historical_reconstruction(
                body, current_user=_MANAGER_USER, building_id=BUILDING_A,
            )

    assert exc.value.status_code == 409
    assert "annual_levies" in exc.value.detail
