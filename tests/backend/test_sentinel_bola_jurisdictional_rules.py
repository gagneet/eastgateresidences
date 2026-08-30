"""
Sentinel tests: BOLA protection on GET /api/jurisdictional-rules/ (OWASP API1:2023).

## The vulnerability these pin

``GET /jurisdictional-rules/`` takes ``building_id`` as a query parameter and
returns that building's effective statutory rules *including its per-building
``jurisdiction_config.rule_overrides``* (interest rates, committee spending
caps, trust withdrawal authority roles, …).

The original guard, ``_require_admin_or_manager``, only asked *what role are
you* — ``{"super_admin", "strata_manager"}``. It never asked *may you see THIS
building*. A strata manager of building A could read building B's overrides by
editing one query parameter. That is textbook Broken Object Level
Authorization: the object identifier is attacker-controlled and never
authorised against the caller.

## Why no other layer caught it

``jurisdiction_config`` IS a tenant-scoped collection, so it is tempting to
assume ``TenantScopedDatabase`` saves us. It does not.
``database.TenantCollection._inject_bid`` short-circuits with
``if self._has_explicit_building_id(filter): return filter`` — a query that
already names a building is passed through untouched, precisely so callers can
address a building explicitly. ``JurisdictionService`` queries
``{"building_id": building_id}``, so the injection never fires and the foreign
id reaches Mongo verbatim. ``test_tenant_scoping_does_not_rescue_an_explicit_foreign_building_id``
pins that mechanism so nobody "fixes" a future instance of this bug class by
trusting the ORM layer.

## The fix these guard

The route now depends on
``require_capability("building.jurisdiction.view", scope_params={"building_id": "building_id"})``,
which reads the id out of the query string, hydrates the caller's *verified*
building assignments from ``core.user_role_assignments``, and denies unless the
caller is actually assigned to that building. super_admin keeps platform rank.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_sentinel_bola_jurisdictional_rules.py -v
"""

from __future__ import annotations

import contextlib
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from models.user import UserRole
from services.authorisation_context import hydrate_authorisation_claims
from services.capability_registry import CAPABILITY_REGISTRY, decide, require_capability


VICTIM_BUILDING = "16244"      # the building the attacker is NOT assigned to
ATTACKER_BUILDING = "13195"    # the building the attacker legitimately manages


# ── helpers ──────────────────────────────────────────────────────────────────

def _user(role: str, **over) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "33333333-3333-3333-3333-333333333333",
        "role": role,
        "effective_role": role,
        "building_id": ATTACKER_BUILDING,
        "is_approved": True,
    }
    base.update(over)
    return base


def _request(building_id: str) -> Request:
    """A GET /jurisdictional-rules/?building_id=<id> request."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/jurisdictional-rules/",
            "query_string": f"building_id={building_id}".encode(),
            "headers": Headers({}).raw,
            "path_params": {},
            "client": ("127.0.0.1", 12345),
        }
    )


def _hydrated(*assigned: str):
    """Stand in for the DB-backed claim hydration with a fixed assignment set.

    Mirrors services/authorisation_context.hydrate_authorisation_claims: the
    claims it owns are REPLACED, never merged, so a stale ``building_id`` on the
    user cannot widen the answer. Accepts the hydration hint kwargs so the stub
    keeps matching the real signature.
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


_HYDRATE = "services.authorisation_context.hydrate_authorisation_claims"


def _closure_strings(cell):
    """Yield string values reachable from a closure cell, one level deep.

    require_capability's returned checker closes over `_evaluate`, which in turn
    closes over `capability`, so the name is one frame further in than a naive
    scan would look.
    """
    try:
        contents = cell.cell_contents
    except ValueError:  # empty cell
        return
    if isinstance(contents, str):
        yield contents
    elif hasattr(contents, "__closure__"):
        for inner in (contents.__closure__ or ()):
            yield from _closure_strings(inner)


@contextlib.contextmanager
def _stub_hydration_db(assigned: list[str]):
    """Run the REAL hydrate_authorisation_claims with its DB calls stubbed.

    Only the four query helpers and the session factory are replaced, so the
    claim-vetting logic under test executes for real. Patched on
    db_postgres.session because authorisation_context imports the factory inside
    the function body.
    """
    @contextlib.asynccontextmanager
    async def _session():
        yield MagicMock()

    with patch("db_postgres.session.async_session_context", new=_session), \
         patch("db_postgres.session.set_tenant", new=AsyncMock(return_value=None)), \
         patch("services.authorisation_context._resolve_target_scheme",
               new=AsyncMock(return_value={"scheme_id": "SCHEME-A"})), \
         patch("services.authorisation_context._offices_for_scheme",
               new=AsyncMock(return_value=[])), \
         patch("services.authorisation_context._assigned_buildings",
               new=AsyncMock(return_value=assigned)), \
         patch("services.authorisation_context._active_authority_ids",
               new=AsyncMock(return_value=[])), \
         patch("services.authorisation_context._active_delegation_ids",
               new=AsyncMock(return_value=[])):
        yield


# ── the capability exists and is building-scoped ─────────────────────────────

def test_jurisdiction_capability_is_registered_and_building_scoped():
    """A platform- or organisation-scoped definition would not gate per building."""
    definition = CAPABILITY_REGISTRY["building.jurisdiction.view"]
    assert definition.scope_type == "building"


def test_jurisdiction_capability_reproduces_the_routes_original_role_set():
    """Scoping the route must not have widened WHO may call it.

    tests/backend/routers/test_jurisdictional_rules_router.py::TestRoleGuard is
    the authority here: it asserts ec_member is denied jurisdictional rules, and
    strata_admin was never granted them. The frontend nav shows the page to
    isManager() — a broader set — but a nav gate is not the access policy.
    """
    definition = CAPABILITY_REGISTRY["building.jurisdiction.view"]
    assert definition.roles == frozenset({UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER})
    for role in (UserRole.OWNER, UserRole.TENANT, UserRole.GUEST, UserRole.EC_MEMBER,
                 UserRole.STRATA_ADMIN, UserRole.SERVICE_PROVIDER,
                 UserRole.REAL_ESTATE_AGENT):
        assert role not in definition.roles, role


def test_decide_denies_a_building_the_subject_is_not_assigned_to():
    """The pure decision, with no route or DB in the way."""
    subject = {
        **_user(UserRole.STRATA_MANAGER),
        "assigned_building_ids": [ATTACKER_BUILDING],
        "managed_building_ids": [ATTACKER_BUILDING],
    }
    allowed = decide(subject, "building.jurisdiction.view", {"building_id": ATTACKER_BUILDING})
    denied = decide(subject, "building.jurisdiction.view", {"building_id": VICTIM_BUILDING})

    assert allowed.allowed is True
    assert denied.allowed is False
    assert "DENY_BUILDING_NOT_ASSIGNED" in denied.reason_codes


def test_decide_denies_when_scope_names_no_building():
    """Fail closed rather than falling back to the caller's own building."""
    subject = {
        **_user(UserRole.STRATA_MANAGER),
        "assigned_building_ids": [ATTACKER_BUILDING],
    }
    decision = decide(subject, "building.jurisdiction.view", {})
    assert decision.allowed is False
    assert "DENY_SCOPE_INCOMPLETE" in decision.reason_codes


# ── the route dependency: the actual BOLA regression ─────────────────────────

@pytest.mark.asyncio
async def test_manager_cannot_read_another_buildings_rules():
    """THE regression: strata manager of A requests B's rules and is refused."""
    dependency = require_capability(
        "building.jurisdiction.view",
        scope_params={"building_id": "building_id"},
    )
    attacker = _user(UserRole.STRATA_MANAGER)

    with patch(_HYDRATE, new=_hydrated(ATTACKER_BUILDING)):
        with pytest.raises(HTTPException) as excinfo:
            await dependency(request=_request(VICTIM_BUILDING), current_user=attacker)

    assert excinfo.value.status_code == 403
    # The denial must not disclose WHY — reason codes confirm the existence and
    # shape of another tenant's resource. See assert_capability's docstring.
    assert "DENY_BUILDING_NOT_ASSIGNED" not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_manager_can_read_their_own_buildings_rules():
    """The fix must not break the legitimate flow the admin UI uses."""
    dependency = require_capability(
        "building.jurisdiction.view",
        scope_params={"building_id": "building_id"},
    )
    manager = _user(UserRole.STRATA_MANAGER)

    with patch(_HYDRATE, new=_hydrated(ATTACKER_BUILDING)):
        assert await dependency(
            request=_request(ATTACKER_BUILDING), current_user=manager
        ) is manager


@pytest.mark.asyncio
async def test_hydration_drops_a_stale_building_claim_the_assignments_contradict():
    """A revoked assignment must not survive as the user's default building.

    ``user['building_id']`` is only membership-checked when the JWT names a
    building; otherwise it is ``default_scheme_id`` straight off the user row —
    a stored preference, not proof of a live assignment. capability_registry's
    _building_matches() accepts it as evidence either way, so a manager removed
    from scheme B but whose default still points at B kept passing
    building-scoped checks for B.

    Hydration now vets it against the live assignment set. This exercises the
    REAL hydrate_authorisation_claims (only its DB helpers are stubbed), because
    a stubbed hydration cannot demonstrate the vetting.
    """
    stale = _user(UserRole.STRATA_MANAGER, building_id=VICTIM_BUILDING)

    with _stub_hydration_db([ATTACKER_BUILDING]):
        claims = await hydrate_authorisation_claims(stale, {"building_id": VICTIM_BUILDING})

    assert claims["building_id"] is None, "stale default building claim survived"
    assert claims["assigned_building_ids"] == [ATTACKER_BUILDING]
    # And the decision built on those claims denies.
    assert decide(claims, "building.jurisdiction.view",
                  {"building_id": VICTIM_BUILDING}).allowed is False


@pytest.mark.asyncio
async def test_hydration_keeps_a_building_claim_the_assignments_confirm():
    """The vetting must only ever REMOVE an uncorroborated claim."""
    manager = _user(UserRole.STRATA_MANAGER, building_id=ATTACKER_BUILDING)

    with _stub_hydration_db([ATTACKER_BUILDING]):
        claims = await hydrate_authorisation_claims(manager, {"building_id": ATTACKER_BUILDING})

    assert claims["building_id"] == ATTACKER_BUILDING
    assert decide(claims, "building.jurisdiction.view",
                  {"building_id": ATTACKER_BUILDING}).allowed is True


@pytest.mark.asyncio
async def test_ec_member_is_denied_even_for_their_own_building():
    """Role rank fails first, before scope is ever considered.

    ec_member is outside this capability's role set (see the capability test
    above), so the denial reason is the role, not the building.
    """
    dependency = require_capability(
        "building.jurisdiction.view",
        scope_params={"building_id": "building_id"},
    )
    ec = _user(UserRole.EC_MEMBER)

    with patch(_HYDRATE, new=_hydrated(ATTACKER_BUILDING)):
        with pytest.raises(HTTPException) as excinfo:
            await dependency(request=_request(ATTACKER_BUILDING), current_user=ec)
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.OWNER, UserRole.TENANT, UserRole.GUEST])
async def test_unprivileged_roles_are_denied_even_for_their_own_building(role):
    dependency = require_capability(
        "building.jurisdiction.view",
        scope_params={"building_id": "building_id"},
    )
    user = _user(role)

    with patch(_HYDRATE, new=_hydrated(ATTACKER_BUILDING)):
        with pytest.raises(HTTPException) as excinfo:
            await dependency(request=_request(ATTACKER_BUILDING), current_user=user)
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_elevation_is_judged_on_effective_role_and_still_building_scoped():
    """decide() reads effective_role, so an elevation is honoured — within scope.

    Guards that read ``user['role']`` instead silently 403 elevated users
    (CLAUDE.md role-guard rule). Elevation raises WHAT you may do; it never
    changes WHICH building you may do it to, which is the half worth pinning.
    """
    dependency = require_capability(
        "building.jurisdiction.view",
        scope_params={"building_id": "building_id"},
    )
    elevated = _user(UserRole.OWNER, effective_role=UserRole.STRATA_MANAGER)

    with patch(_HYDRATE, new=_hydrated(ATTACKER_BUILDING)):
        assert await dependency(
            request=_request(ATTACKER_BUILDING), current_user=elevated
        ) is elevated

        with pytest.raises(HTTPException) as excinfo:
            await dependency(request=_request(VICTIM_BUILDING), current_user=elevated)
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_retains_platform_rank():
    dependency = require_capability(
        "building.jurisdiction.view",
        scope_params={"building_id": "building_id"},
    )
    sa = _user(UserRole.SUPER_ADMIN)

    with patch(_HYDRATE, new=_hydrated()):  # assigned to nothing
        assert await dependency(
            request=_request(VICTIM_BUILDING), current_user=sa
        ) is sa


@pytest.mark.asyncio
async def test_hydration_failure_grants_no_building_the_subject_never_had():
    """A broken claims store must not become a cross-tenant pass.

    On any internal failure hydration returns ``{**user, **_empty_claims()}`` —
    every claim it OWNS is emptied, so the assignment set is gone. The user's own
    inherited ``building_id`` deliberately survives, which is why this asserts
    the boundary that matters (no foreign building) rather than a blanket denial.
    Widening that into full denial is tracked separately — see
    tasks/GAP-SEC-005-unverified-building-claim.md.
    """
    dependency = require_capability(
        "building.jurisdiction.view",
        scope_params={"building_id": "building_id"},
    )
    manager = _user(UserRole.STRATA_MANAGER)

    async def _empty(subject, scope, **_hydration_hints):
        return {**subject, "assigned_building_ids": [], "managed_building_ids": [],
                "governance_offices": [], "active_resolution_ids": [],
                "active_delegation_ids": []}

    with patch(_HYDRATE, new=AsyncMock(side_effect=_empty)):
        with pytest.raises(HTTPException) as excinfo:
            await dependency(request=_request(VICTIM_BUILDING), current_user=manager)
    assert excinfo.value.status_code == 403


# ── the route is actually wired to the guard ─────────────────────────────────

def test_route_declares_the_building_scoped_dependency():
    """A future refactor must not quietly drop the guard from the signature.

    Asserting on the route object rather than on a helper name, because the bug
    was precisely that a *present* guard checked the wrong thing.
    """
    import routers.jurisdictional_rules_router as mod

    route = next(
        r for r in mod.router.routes
        if getattr(r, "path", None) == "/jurisdictional-rules/" and "GET" in getattr(r, "methods", set())
    )
    param = inspect.signature(route.endpoint).parameters["current_user"]
    dependency = param.default.dependency

    # require_capability() returns a closure; the capability name lives in the
    # enclosing scope, so read it off the closure cells of the factory frame.
    captured = {
        value
        for cell in (dependency.__closure__ or ())
        for value in _closure_strings(cell)
    }
    assert "building.jurisdiction.view" in captured, captured


def test_rank_only_helper_is_not_used_on_the_building_scoped_route():
    """Pin the specific mistake: rank-only guard on a per-building response."""
    import routers.jurisdictional_rules_router as mod

    route = next(
        r for r in mod.router.routes
        if getattr(r, "path", None) == "/jurisdictional-rules/" and "GET" in getattr(r, "methods", set())
    )
    param = inspect.signature(route.endpoint).parameters["current_user"]
    assert param.default.dependency is not mod._require_admin_or_manager


# ── the mechanism that made it exploitable ───────────────────────────────────

def test_tenant_scoping_does_not_rescue_an_explicit_foreign_building_id():
    """TenantScopedDatabase is NOT a BOLA backstop — the route guard is.

    ``_inject_bid`` returns a filter untouched when it already names a building.
    So a foreign ``building_id`` that reaches a query is used verbatim, whatever
    the request's tenant context says. This test exists so the next person who
    finds an untrusted ``building_id`` parameter does not close the ticket with
    "the DB wrapper scopes it anyway".
    """
    from database import TenantCollection
    from request_context import set_ctx_building_id

    mock_motor_collection = MagicMock()
    mock_motor_collection.name = "jurisdiction_config"
    coll = TenantCollection(mock_motor_collection)
    set_ctx_building_id(ATTACKER_BUILDING)
    try:
        injected = coll._inject_bid({"building_id": VICTIM_BUILDING})
    finally:
        set_ctx_building_id(None)

    assert injected == {"building_id": VICTIM_BUILDING}, (
        "explicit building_id passed through unchanged — object-level "
        "authorisation must happen at the route, not in the DB wrapper"
    )
