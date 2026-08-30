"""Function scoping: it narrows the right people and nobody else.

# @featuretrace:manager-function-scoping — behaviour tests for the narrowing layer.
# Layer: test
# Data flow: resolve_manager_scope() <- mocked control plane;
#            require_manager_surface() <- FastAPI (scope param: building|global).
#            Same mixed chain as the service under test.
# Related: backend/services/manager_function_service.py
#          backend/utils/route_guards.py (require_manager_surface)
#          docs/architecture/strata_management_staff_access_model.md

This layer only ever REMOVES access, and it is off by default. Both halves of that
sentence are load-bearing, so both are tested: the cases where it must narrow, and
the larger set of cases where it must keep its hands off.

The fail-open tests are the ones to read carefully. Fail-open is normally wrong in
an authorisation layer; it is right here only because a role guard has already
passed and "off" is a supported default. If someone later makes this fail closed,
these tests are where that decision gets made deliberately rather than by accident.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from models.user import ManagerFunction, UserRole
from services import manager_function_service as mfs
from services.manager_function_service import (
    ALL_SURFACES,
    FUNCTION_SURFACES,
    UNRESTRICTED_APPOINTMENT_TYPES,
    invalidate_manager_scope_cache,
    resolve_manager_scope,
    validate_surface,
)

SM = {"id": "11111111-1111-1111-1111-111111111111", "role": UserRole.STRATA_MANAGER,
      "email": "manager@example.invalid"}
BUILDING = "13195"


@pytest.fixture(autouse=True)
def _clear_cache():
    """The scope cache is process-global; a leak across tests would hide a bug."""
    invalidate_manager_scope_cache()
    yield
    invalidate_manager_scope_cache()


@pytest.fixture
def control_plane(monkeypatch):
    """Stand in for the three Postgres reads resolve_manager_scope makes."""
    state = {"scheme": "22222222-2222-2222-2222-222222222222",
             "pg_user": SM["id"], "appointments": [], "opted_in": False, "calls": 0}

    async def _scheme(building_id):
        return state["scheme"]

    async def _user(user):
        return state["pg_user"]

    async def _appts(user_id, scheme_id):
        state["calls"] += 1
        return state["appointments"]

    async def _opted(entity_ids):
        return state["opted_in"]

    monkeypatch.setattr(mfs, "_resolve_scheme_id", _scheme)
    monkeypatch.setattr(mfs, "_resolve_pg_user_id", _user)
    monkeypatch.setattr(mfs, "_load_appointments", _appts)
    monkeypatch.setattr(mfs, "_any_entity_opted_in", _opted)
    return state


def _appointment(atype: str) -> tuple[str, str]:
    return (atype, "33333333-3333-3333-3333-333333333333")


# ─── The surface vocabulary ───────────────────────────────────────────────────

def test_every_function_surface_is_a_known_surface() -> None:
    """A function pointing at a surface no route declares would silently deny."""
    for function, surfaces in FUNCTION_SURFACES.items():
        assert surfaces <= ALL_SURFACES, (function, sorted(surfaces - ALL_SURFACES))


def test_unknown_surface_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown manager surface"):
        validate_surface("maintenence")


def test_strata_manager_function_reaches_everything() -> None:
    """The appointed manager is not narrowed by holding their own title."""
    assert FUNCTION_SURFACES[ManagerFunction.STRATA_MANAGER] == ALL_SURFACES


def test_paying_a_supplier_invoice_belongs_to_no_specialist() -> None:
    """The licensee line, in one assertion.

    Agents Act 2003 (ACT) pt 7 puts the duties attaching to money on the licensed
    agent. A maintenance manager raises a purchase order and engages the contractor
    — that is the `contractors` surface — but approving and PAYING the resulting
    supplier invoice is not theirs.
    """
    for function in ManagerFunction.ALL:
        if function == ManagerFunction.STRATA_MANAGER:
            continue
        assert "invoices" not in FUNCTION_SURFACES[function], function
    assert "contractors" in FUNCTION_SURFACES[ManagerFunction.MAINTENANCE_MANAGER]


# ─── Who is never narrowed ────────────────────────────────────────────────────

@pytest.mark.parametrize("role", [
    UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.EC_MEMBER,
    UserRole.ADMIN_STAFF, UserRole.OWNER, UserRole.TENANT, UserRole.SERVICE_PROVIDER,
])
async def test_only_strata_manager_is_narrowable(role, control_plane) -> None:
    """An agency's internal division of labour says nothing about anyone else.

    A super_admin operates the platform, an ec_member is the client, an owner is a
    resident. None of them holds an appointment from the managing agent.
    """
    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope({"id": SM["id"], "role": role}, BUILDING)

    assert not scope.enforced
    assert scope.permits("whs")


async def test_elevation_is_respected(control_plane) -> None:
    """effective_role, not raw role — an elevated owner is not a strata manager."""
    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope(
        {"id": SM["id"], "role": UserRole.OWNER, "effective_role": UserRole.EC_MEMBER},
        BUILDING,
    )
    assert not scope.enforced


async def test_no_appointment_means_full_scope(control_plane) -> None:
    """Silence about someone's job is not a statement that they do nothing."""
    control_plane["appointments"] = []
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope(SM, BUILDING)

    assert not scope.enforced
    assert "no active appointment" in scope.reason


@pytest.mark.parametrize("engagement", sorted(UNRESTRICTED_APPOINTMENT_TYPES))
async def test_engagement_appointment_beats_a_functional_one(engagement, control_plane) -> None:
    """Rule 3, and the footgun it removes.

    A user recorded as BOTH the agency's strata manager and its maintenance manager
    is the manager who also owns maintenance — not someone restricted to maintenance.
    Without this rule, adding a functional appointment to the general manager would
    silently strip them of everything else.
    """
    control_plane["appointments"] = [
        _appointment(engagement), _appointment("maintenance_manager"),
    ]
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope(SM, BUILDING)

    assert not scope.enforced
    assert scope.permits("levies")
    assert engagement in scope.reason


async def test_agency_that_has_not_opted_in_is_untouched(control_plane) -> None:
    """The default state, and the one almost every tenant is in."""
    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = False

    scope = await resolve_manager_scope(SM, BUILDING)

    assert not scope.enforced
    assert scope.permits("whs")
    assert "has not enabled" in scope.reason


# ─── Who IS narrowed ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("atype,function,allowed,denied", [
    ("levies_manager", ManagerFunction.LEVIES_MANAGER, "arrears", "whs"),
    ("insurance_manager", ManagerFunction.INSURANCE_MANAGER, "claims", "levies"),
    ("maintenance_manager", ManagerFunction.MAINTENANCE_MANAGER, "whs", "levies"),
    ("building_manager", ManagerFunction.BUILDING_MANAGER, "amenities", "insurance"),
])
async def test_a_functional_appointment_narrows_to_its_surfaces(
    atype, function, allowed, denied, control_plane,
) -> None:
    control_plane["appointments"] = [_appointment(atype)]
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope(SM, BUILDING)

    assert scope.enforced
    assert scope.functions == frozenset({function})
    assert scope.permits(allowed)
    assert not scope.permits(denied)


async def test_two_functions_union_their_surfaces(control_plane) -> None:
    """Someone who does levies AND insurance reaches both, and still not WHS."""
    control_plane["appointments"] = [
        _appointment("levies_manager"), _appointment("insurance_manager"),
    ]
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope(SM, BUILDING)

    assert scope.enforced
    assert scope.permits("arrears") and scope.permits("claims")
    assert not scope.permits("whs")


async def test_narrowing_never_reaches_invoices(control_plane) -> None:
    control_plane["appointments"] = [_appointment("maintenance_manager")]
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope(SM, BUILDING)

    assert scope.permits("contractors")
    assert not scope.permits("invoices")


# ─── Fail-open ────────────────────────────────────────────────────────────────

async def test_unresolvable_building_does_not_narrow(control_plane) -> None:
    control_plane["scheme"] = None
    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope(SM, BUILDING)

    assert not scope.enforced
    assert scope.reason == "scheme not found"


async def test_unresolvable_user_does_not_narrow(control_plane) -> None:
    control_plane["pg_user"] = None
    control_plane["opted_in"] = True

    scope = await resolve_manager_scope(SM, BUILDING)

    assert not scope.enforced
    assert scope.reason == "user not resolved in postgres"


async def test_database_failure_does_not_narrow(monkeypatch) -> None:
    """The deliberate fail-open, stated as a test so it cannot become accidental.

    A role guard has already passed. If the control plane is unreadable, the caller
    falls back to what their role alone grants — identical to a non-opted-in agency
    and to every agency before this feature existed. Failing closed would 403 whole
    teams on a transient blip, for a feature nearly every tenant has switched off.
    """
    async def _boom(building_id):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(mfs, "_resolve_scheme_id", _boom)

    scope = await resolve_manager_scope(SM, BUILDING)

    assert not scope.enforced
    assert "postgres is down" in scope.reason


async def test_missing_building_context_does_not_narrow() -> None:
    scope = await resolve_manager_scope(SM, None)
    assert not scope.enforced
    assert scope.reason == "no building context"


# ─── Cache ────────────────────────────────────────────────────────────────────

async def test_scope_is_cached_and_invalidatable(control_plane) -> None:
    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    await resolve_manager_scope(SM, BUILDING)
    await resolve_manager_scope(SM, BUILDING)
    assert control_plane["calls"] == 1, "second call should have hit the cache"

    invalidate_manager_scope_cache(SM["id"])
    await resolve_manager_scope(SM, BUILDING)
    assert control_plane["calls"] == 2, "invalidation should force a re-read"


# ─── The dependency ───────────────────────────────────────────────────────────

async def test_dependency_403s_with_a_typed_code(control_plane) -> None:
    """The frontend needs to say WHICH part of the job is missing, not "forbidden"."""
    from utils.route_guards import require_manager_surface

    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    dependency = require_manager_surface("whs")
    with pytest.raises(HTTPException) as exc:
        await dependency(current_user=SM, building_id=BUILDING)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "MANAGER_FUNCTION_SCOPE"
    assert exc.value.detail["surface"] == "whs"
    assert exc.value.detail["functions"] == [ManagerFunction.LEVIES_MANAGER]


async def test_dependency_passes_the_user_through_when_permitted(control_plane) -> None:
    from utils.route_guards import require_manager_surface

    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    dependency = require_manager_surface("arrears")
    assert await dependency(current_user=SM, building_id=BUILDING) is SM


def test_dependency_rejects_a_typo_at_import_time() -> None:
    from utils.route_guards import require_manager_surface

    with pytest.raises(ValueError, match="unknown manager surface"):
        require_manager_surface("complience")


# ─── Wiring ───────────────────────────────────────────────────────────────────

ROUTER_SURFACES = [
    ("insurance", "insurance"),
    ("insurance_claims", "claims"),
    ("whs", "whs"),
    ("compliance_registers", "compliance"),
    ("pool_safety", "compliance"),
    ("essential_services", "compliance"),
    ("defects_register", "defects"),
    ("levy_reminders", "levies"),
    ("arrears_recovery", "arrears"),
]


@pytest.mark.parametrize("module_name,_surface", ROUTER_SURFACES)
def test_wired_routers_carry_the_dependency(module_name, _surface) -> None:
    """A router that loses its dependency stops narrowing silently."""
    module = __import__(f"routers.{module_name}", fromlist=["router"])
    assert module.router.dependencies, module_name


def test_every_maintenance_route_is_scoped_and_invoices_are_separate() -> None:
    """maintenance.py is wired per route because it holds four surfaces.

    The split that matters: /purchase-orders is the maintenance manager's, /invoices
    is not.
    """
    from routers import maintenance

    unscoped = [r.path for r in maintenance.router.routes if not getattr(r, "dependencies", None)]
    assert not unscoped, f"unscoped maintenance routes: {unscoped}"

    invoice_routes = [r for r in maintenance.router.routes if r.path.startswith("/invoices")]
    assert invoice_routes, "expected /invoices routes in maintenance.py"

# ─── RLS: the bug the mocks hid ───────────────────────────────────────────────
#
# Every test above mocks the four Postgres reads, which is right for testing the
# DECISION logic but means none of them can see whether the SQL works. It did not:
# core.schemes and core.users are FORCE-RLS with a sentinel bypass, the service set
# no tenant context, both reads returned zero rows silently, and the resolver took
# its fail-open path forever. Function scoping was inert in production while this
# file was green.
#
# This is a static check on purpose. It needs no database, so it runs in CI, and it
# covers reads added later rather than only the four that exist today.

#: core tables whose RLS policy denies a session with no tenant context. Verified
#: live 2026-08-29 via pg_class.relrowsecurity / relforcerowsecurity.
FORCE_RLS_TABLES = ("core.schemes", "core.users")

#: Deliberately NOT in the list above: core.scheme_manager_appointments and
#: core.management_entities both have relrowsecurity = f, so they read fine with no
#: context. If either is ever brought under RLS, add it here and the guard will
#: point at whichever function needs updating.
NO_RLS_TABLES = ("core.scheme_manager_appointments", "core.management_entities")


def _sql_by_function() -> dict[str, tuple[str, str]]:
    """{function name: (executed SQL, full source)} for the service module.

    Only strings passed to `text(...)` count as SQL. Matching the raw source instead
    would flag any function whose DOCSTRING names a table — the first version of this
    guard did exactly that and reported `_candidate_uuid` and
    `invalidate_manager_scope_cache` as offenders because they mention core.users in
    prose. That is the same "match code, not comments" rule the canonical-owner
    scanner follows, and a check that cries wolf gets deleted.
    """
    import ast
    import inspect

    src = inspect.getsource(mfs)
    tree = ast.parse(src)
    out: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        sql: list[str] = []
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "text"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
                and isinstance(sub.args[0].value, str)
            ):
                sql.append(sub.args[0].value)
        out[node.name] = ("\n".join(sql), ast.get_source_segment(src, node) or "")
    return out


@pytest.mark.parametrize("table", FORCE_RLS_TABLES)
def test_every_force_rls_read_sets_tenant_context(table: str) -> None:
    """A read of a FORCE-RLS table without `set_tenant` returns 0 rows, not an error.

    That is indistinguishable from "the row does not exist", which is how this
    shipped inert: `_resolve_scheme_id("13195")` returned None for a building that
    plainly exists, and every mocked test stayed green.
    """
    offenders = [
        name for name, (sql, body) in _sql_by_function().items()
        if table in sql and "set_tenant(" not in body
    ]
    assert not offenders, (
        f"{offenders} query {table} without set_tenant(). That table is FORCE-RLS "
        f"with only a sentinel bypass, so the query returns zero rows SILENTLY. "
        f"Call `await set_tenant(session, _RLS_BYPASS_TENANT)` first."
    )


@pytest.mark.parametrize("table", NO_RLS_TABLES)
def test_non_rls_reads_are_documented_as_deliberate(table: str) -> None:
    """A read with no tenant context must say why, so it is not mistaken for the bug."""
    for name, (sql, body) in _sql_by_function().items():
        if table in sql and "set_tenant(" not in body:
            assert "relrowsecurity" in body, (
                f"{name} queries {table} with no tenant context and does not say why. "
                f"State it — the next reader cannot otherwise tell a deliberate "
                f"omission from the FORCE-RLS bug fixed on 2026-08-29."
            )


def test_the_guard_would_have_caught_the_original_bug() -> None:
    """The guard is only worth having if it fails on the code that shipped broken."""
    import ast

    broken = ast.parse(
        "async def _resolve_scheme_id(building_id):\n"
        "    async with async_session_context() as session:\n"
        "        row = await session.execute(\n"
        "            text('SELECT scheme_id FROM core.schemes WHERE scheme_number = :bid'),\n"
        "            {'bid': building_id})\n"
        "        return row.fetchone()\n"
    )
    fn = broken.body[0]
    sql = [
        sub.args[0].value for sub in ast.walk(fn)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
        and sub.func.id == "text" and sub.args
        and isinstance(sub.args[0], ast.Constant)
    ]
    assert any("core.schemes" in q for q in sql)
    assert "set_tenant(" not in ast.unparse(fn)


def test_bypass_sentinel_is_the_documented_value() -> None:
    """Any other value satisfies neither policy branch and denies every row."""
    assert mfs._RLS_BYPASS_TENANT == "00000000-0000-0000-0000-000000000000"


# ─── The dependency through a real ASGI request ───────────────────────────────
#
# The dependency tests above call the closure directly with keyword arguments, which
# proves the decision but NOT that FastAPI can resolve the signature — a dependency
# that cannot be resolved fails at request time, on every route in nine routers.

def _asgi_app_with_surface(surface: str):
    """A one-route app whose only guard is require_manager_surface(surface)."""
    from fastapi import Depends, FastAPI
    from utils.auth import get_current_building, get_current_user
    from utils.route_guards import require_manager_surface

    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(require_manager_surface(surface))])
    async def probe():
        return {"ok": True}

    app.dependency_overrides[get_current_user] = lambda: SM
    app.dependency_overrides[get_current_building] = lambda: BUILDING
    return app


def test_dependency_resolves_and_denies_through_fastapi(control_plane) -> None:
    from fastapi.testclient import TestClient

    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    response = TestClient(_asgi_app_with_surface("whs")).get("/probe")

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "MANAGER_FUNCTION_SCOPE"
    assert detail["surface"] == "whs"


def test_dependency_resolves_and_allows_through_fastapi(control_plane) -> None:
    from fastapi.testclient import TestClient

    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    response = TestClient(_asgi_app_with_surface("arrears")).get("/probe")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_an_unidentifiable_caller_is_never_cached(control_plane) -> None:
    """Two callers with no id must not share a cache entry.

    An earlier version keyed those as `("", building)`. In a layer that decides
    access, a cache collision is not a performance bug.
    """
    control_plane["appointments"] = [_appointment("levies_manager")]
    control_plane["opted_in"] = True

    anonymous = {"role": UserRole.STRATA_MANAGER}
    assert mfs._cache_identity(anonymous) is None

    await resolve_manager_scope(anonymous, BUILDING)
    await resolve_manager_scope(anonymous, BUILDING)

    assert control_plane["calls"] == 2, "an unidentifiable caller must not be cached"
    assert not any(k[0] in (None, "") for k in mfs._scope_cache), (
        "no cache key may be built from a missing identity"
    )
