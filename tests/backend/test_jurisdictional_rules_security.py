"""End-to-end HTTP tests for GET /jurisdictional-rules/ authorisation.

Companion to tests/backend/test_sentinel_bola_jurisdictional_rules.py, which
exercises the decision and the dependency in isolation. This file drives the
real ASGI app, so it proves the guard is actually WIRED to the route — the
half a unit test cannot show.

## History worth keeping

The first fix here (PR #723) added a bespoke ``_verify_building_access`` that
looked up ``db.memberships`` with ``{"status": "active"}``. Nothing in this
codebase writes a ``status`` field to that collection — all 24 other membership
queries and every writer use ``is_active`` — so the lookup could never match a
document. The guard silently degraded to its fallback, comparing the requested
id against ``current_user["building_id"]``, which is the user's stored default
scheme rather than proof of a live assignment. Two consequences: a manager
assigned to several buildings was denied all but their default, and a stale
default still passed.

Its own test did not catch this because it mocked ``memberships.find_one`` to
return ``None`` — asserting on the fallback path while the real query was never
evaluated. Hence the rule this file follows: **do not mock the authorisation
lookup you are testing.** Stub the claim source, then let the guard decide.

The route now uses ``require_capability("building.jurisdiction.view", ...)``,
which resolves assignments from ``core.user_role_assignments`` (where they
actually live now that East Gate is on Postgres) rather than from Mongo only.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_jurisdictional_rules_security.py -v
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.jurisdictional_rules_router import router
from utils.auth import get_current_user

app = FastAPI()
app.include_router(router)
client = TestClient(app)

ASSIGNED = "bldg_111"      # the caller's real assignment
UNASSIGNED = "bldg_999"    # someone else's building

_HYDRATE = "services.authorisation_context.hydrate_authorisation_claims"


def _claims_for(*assigned: str):
    """Stand in for the DB-backed hydration, returning a fixed assignment set.

    Replaces every claim hydration owns, exactly as the real function does, so a
    value inherited from the token cannot widen the answer.
    """
    async def _fake(subject, scope, **_hydration_hints):
        return {
            **subject,
            "assigned_building_ids": list(assigned),
            "managed_building_ids": list(assigned),
            "governance_offices": [],
            "active_resolution_ids": [],
            "active_delegation_ids": [],
        }
    return AsyncMock(side_effect=_fake)


@pytest.fixture
def as_user():
    """Authenticate the request as a given user, cleaning up the override after."""
    def _apply(user: dict):
        app.dependency_overrides[get_current_user] = lambda: user
        return user
    yield _apply
    app.dependency_overrides.clear()


def _user(role: str, **over) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "33333333-3333-3333-3333-333333333333",
        "role": role,
        "effective_role": role,
        "is_approved": True,
        "building_id": ASSIGNED,
    }
    base.update(over)
    return base


def test_strata_manager_is_denied_a_building_they_are_not_assigned_to(as_user):
    """THE BOLA regression: the id is caller-supplied, so it must be authorised."""
    as_user(_user("strata_manager"))
    with patch(_HYDRATE, new=_claims_for(ASSIGNED)):
        response = client.get(f"/jurisdictional-rules/?building_id={UNASSIGNED}")
    assert response.status_code == 403


def test_strata_manager_can_read_their_own_building(as_user):
    """The authorised case PR #723's suite never covered.

    Without it, a guard that denies everything would have passed review.
    """
    as_user(_user("strata_manager"))
    with patch(_HYDRATE, new=_claims_for(ASSIGNED)), \
         patch("routers.jurisdictional_rules_router.JurisdictionService.get_state_and_all_rules",
               AsyncMock(return_value=("NSW", {}))):
        response = client.get(f"/jurisdictional-rules/?building_id={ASSIGNED}")
    assert response.status_code == 200
    assert response.json()["building_id"] == ASSIGNED


def test_manager_of_several_buildings_reaches_all_of_them(as_user):
    """A multi-building manager must not be pinned to one default scheme.

    The previous implementation compared against current_user['building_id']
    alone, so every building except the default 403'd.
    """
    other = "bldg_222"
    as_user(_user("strata_manager"))
    with patch(_HYDRATE, new=_claims_for(ASSIGNED, other)), \
         patch("routers.jurisdictional_rules_router.JurisdictionService.get_state_and_all_rules",
               AsyncMock(return_value=("ACT", {}))):
        assert client.get(f"/jurisdictional-rules/?building_id={ASSIGNED}").status_code == 200
        assert client.get(f"/jurisdictional-rules/?building_id={other}").status_code == 200


def test_super_admin_may_read_any_building(as_user):
    as_user(_user("super_admin"))
    with patch(_HYDRATE, new=_claims_for()), \
         patch("routers.jurisdictional_rules_router.JurisdictionService.get_state_and_all_rules",
               AsyncMock(return_value=("NSW", {}))):
        response = client.get(f"/jurisdictional-rules/?building_id={UNASSIGNED}")
    assert response.status_code == 200
    assert response.json()["building_id"] == UNASSIGNED


def test_owner_is_denied_even_for_their_own_building(as_user):
    """Per-building statutory overrides are governance data, not resident data."""
    as_user(_user("owner"))
    with patch(_HYDRATE, new=_claims_for(ASSIGNED)):
        response = client.get(f"/jurisdictional-rules/?building_id={ASSIGNED}")
    assert response.status_code == 403


def test_unauthenticated_request_is_rejected():
    response = client.get(f"/jurisdictional-rules/?building_id={ASSIGNED}")
    assert response.status_code in (401, 403)


def test_building_id_remains_a_required_query_parameter():
    """Fail closed on a missing id rather than defaulting to a building."""
    response = client.get("/jurisdictional-rules/")
    assert response.status_code != 200
