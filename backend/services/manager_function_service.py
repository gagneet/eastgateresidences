"""Narrowing a strata manager to the job they actually do.

# @featuretrace:manager-function-scoping — resolve and enforce a manager's function scope.
# Layer: service
# Data flow: core.scheme_manager_appointments + core.management_entities.function_scoping_enabled
#            -> resolve_manager_scope() -> utils.route_guards.require_manager_surface
#            -> router endpoints (scope param: building|global).
# Related: backend/models/user.py (ManagerFunction)
#          backend/utils/route_guards.py (require_manager_surface)
#          backend/alembic/versions/0104_manager_fn_scoping.py
#          docs/architecture/strata_management_staff_access_model.md
#          tests/backend/test_manager_function_scoping.py

SCOPE: MIXED, AND THAT IS NOT A SHORTCUT
----------------------------------------
The FeatureTrace scope is `scope param: building|global` because this one chain really
does cross both. `core.schemes` and `core.scheme_manager_appointments` are per-scheme;
`core.users` spans tenants (a super_admin's row lives in the platform tenant, not the
building's - footgun #11); and `core.management_entities` is per-AGENCY with a nullable
tenant_id, so its opt-in flag applies across every scheme in that agency's book.

WHAT THIS IS
------------
A strata management agency fields specialists. East Gate's managing agent has a
Strata Manager, a Levies Manager, an Insurance Manager and a Maintenance Manager,
and until now the platform gave all four the same reach: everything `strata_manager`
has ever been able to see.

The legal direction is clear enough. Unit Titles (Management) Act 2011 (ACT) s 58
delegates functions to THE MANAGER as one legal person, so none of these titles is
an office and none can be a `UserRole` (see models.user.ManagerFunction). What DOES
bear on them is the Privacy Act 1988 (Cth): APP 6 limits use of personal information
to the purpose it was collected for, and APP 11 requires protection against
unauthorised access. A levies clerk has no purpose that requires reading a WHS
incident report.

So this layer only ever REMOVES access. It runs after a role guard has already said
yes, and it can turn that yes into a no. It can never turn a no into a yes.

THE FOUR THINGS THAT MAKE IT SAFE
---------------------------------
1. Off by default, per agency. `core.management_entities.function_scoping_enabled`
   is FALSE until an agency opts in, and FALSE reproduces pre-2026-08-28 behaviour
   exactly.

2. Holding no functional appointment means full scope. Someone recorded only as
   `agency_strata_manager` IS the appointed manager; narrowing them would be
   nonsense.

3. An engagement-type appointment beats a functional one. If a user holds
   `agency_strata_manager` AND `maintenance_manager`, they are NOT narrowed — they
   are the manager who also happens to own maintenance. Without this rule, adding a
   functional appointment to the general manager would silently strip them of
   everything else, which is the opposite of what the person recording it intended.
   The rule can only ever widen relative to the naive union, so it is safe by
   construction.

4. It fails OPEN, loudly. See below.

WHY FAIL-OPEN IS RIGHT HERE, AND WHY THAT IS NOT THE USUAL ANSWER
-----------------------------------------------------------------
Fail-open in an authorisation layer is normally a bug. It is correct in this one
specific shape: the PRIMARY authorisation has already run and passed. This is a
secondary narrowing whose "off" state is a supported, default configuration. If the
control plane cannot be read, the user falls back to exactly what their role alone
grants — which is what a non-opted-in agency gets, and what every agency got before
this existed.

Failing closed would mean a transient Postgres blip 403s an entire management team
out of a product that, for almost every tenant, has this feature switched off. That
is a worse failure than the one it would prevent.

The tradeoff is real, so every fail-open path logs a WARNING naming the reason, and
`ManagerScope.reason` carries it to the caller for tests and diagnostics. A narrowing
that silently stops narrowing is the thing to watch for; it is observable here.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from models.user import ManagerFunction
from services.management_hierarchy_service import ENGAGEMENT_APPOINTMENT_TYPES
from utils.auth import effective_role
from models.user import UserRole

logger = logging.getLogger(__name__)

#: RLS bypass sentinel for reads of the two FORCE-RLS tables this module touches.
#:
#: `core.schemes` and `core.users` are both `relrowsecurity = t, relforcerowsecurity = t`
#: with a policy of the form
#:     tenant_id = core.current_tenant_id()
#:     OR current_setting('app.tenant_id', true) = '00000000-...-000000000000'
#: A session that sets NO tenant context satisfies neither branch, so the query returns
#: ZERO ROWS rather than raising — footgun #8 in rules/post-compact-critical.md.
#:
#: That is exactly what happened here. Until 2026-08-29 these lookups ran with no
#: context at all, so `_resolve_scheme_id("13195")` returned None on a building that
#: plainly exists, the resolver took its fail-open path, and function scoping was
#: INERT in production while every unit test passed — because the tests mock these
#: functions, which is precisely the seam where the bug lived.
#:
#: The sentinel is correct here rather than a real tenant UUID for two reasons:
#: resolving a building number to a scheme happens BEFORE any tenant is known, and
#: the caller may be a super_admin whose own `core.users` row lives in the platform
#: tenant, not the building's (footgun #11 — a real-tenant session silently drops
#: cross-tenant actors from a `core.users` read).
#:
#: Both reads are single-table, id/number-keyed lookups of non-sensitive columns
#: (scheme_id, user_id), never a join and never a scan, so the bypass is scoped as
#: narrowly as the guarantee allows.
_RLS_BYPASS_TENANT = "00000000-0000-0000-0000-000000000000"

# ── Surfaces ──────────────────────────────────────────────────────────────────
#
# A route declares the SURFACE it belongs to, never the function that may reach it.
# A route knows what it is ("insurance"); it should not have to know, or be updated
# when, the set of job titles that legitimately touch it changes.

#: Every surface a route may declare. Unknown names raise at import time, for the
#: same reason route_guards validates role names: a typo'd surface belongs to no
#: function, so it would silently 403 every narrowed manager and nobody else — the
#: hardest kind of access bug to notice.
ALL_SURFACES: frozenset[str] = frozenset({
    "levies",
    "receipts",
    "arrears",
    "ledger_read",
    "insurance",
    "claims",
    "valuations",
    "maintenance",
    "defects",
    "contractors",
    "whs",
    "compliance",
    "amenities",
    "governance",
    #: Supplier invoice approve / reject / PAY. Deliberately its own surface and
    #: deliberately NOT in any specialist's set: it lives in maintenance.py beside
    #: the work it pays for, but authorising an outgoing payment is the licensed
    #: agent's act (Agents Act 2003 (ACT) pt 7), not the maintenance manager's.
    #: Raising a purchase order and engaging a contractor IS theirs — that is the
    #: "contractors" surface — so the line falls between committing work and paying
    #: for it.
    "invoices",
})

#: What each function may reach. STRATA_MANAGER is deliberately everything — the
#: appointed manager is not narrowed by holding their own title.
FUNCTION_SURFACES: dict[str, frozenset[str]] = {
    #: NOT REACHABLE through an appointment, and that is correct rather than dead
    #: code. No appointment_type maps to STRATA_MANAGER (see
    #: ManagerFunction.APPOINTMENT_TYPE), and anyone holding an engagement-type
    #: appointment short-circuits in resolve_manager_scope before functions are
    #: computed at all. It is here so FUNCTION_SURFACES covers the whole
    #: ManagerFunction vocabulary and so the honest answer to "what can the
    #: appointed manager reach" is written down rather than implied by absence.
    ManagerFunction.STRATA_MANAGER: ALL_SURFACES,
    ManagerFunction.LEVIES_MANAGER: frozenset({
        "levies", "receipts", "arrears", "ledger_read",
    }),
    ManagerFunction.INSURANCE_MANAGER: frozenset({
        "insurance", "claims", "valuations",
    }),
    ManagerFunction.MAINTENANCE_MANAGER: frozenset({
        "maintenance", "defects", "contractors", "whs", "compliance",
    }),
    #: An on-site building manager runs the building day to day, so they get the
    #: maintenance surface plus the amenities they actually operate. Not levies,
    #: not insurance: those are the agency's back office, not the site.
    ManagerFunction.BUILDING_MANAGER: frozenset({
        "maintenance", "defects", "contractors", "whs", "compliance", "amenities",
    }),
}

#: appointment_type -> ManagerFunction. Derived from the model so the two cannot drift.
_TYPE_TO_FUNCTION: dict[str, str] = {
    atype: function for function, atype in ManagerFunction.APPOINTMENT_TYPE.items()
}

#: Appointment types that say HOW a manager is engaged rather than what they do.
#: Holding one means "you are the appointed manager" and disables narrowing outright
#: (rule 3 in the module docstring).
#:
#: Imported, not restated. management_hierarchy_service owns the appointment-type
#: vocabulary (canonical_owners.yaml: manager-appointment-type), and a copy of these
#: four literals here would drift the moment a fifth engagement type is added — this
#: module would keep narrowing a manager the other module considers unrestricted.
UNRESTRICTED_APPOINTMENT_TYPES: frozenset[str] = ENGAGEMENT_APPOINTMENT_TYPES

# Fail fast if a surface list and ALL_SURFACES ever disagree.
for _function, _surfaces in FUNCTION_SURFACES.items():
    _unknown = sorted(_surfaces - ALL_SURFACES)
    if _unknown:  # pragma: no cover - import-time guard
        raise ValueError(
            f"FUNCTION_SURFACES[{_function!r}] names unknown surface(s) {_unknown}. "
            f"Add them to ALL_SURFACES or fix the typo."
        )


def validate_surface(surface: str) -> str:
    """Reject an unknown surface name loudly, at import time.

    A surface that belongs to no function denies every narrowed manager and nobody
    else, which looks exactly like a deliberate restriction. Raising here turns that
    into a startup error naming the value.
    """
    if surface not in ALL_SURFACES:
        raise ValueError(
            f"unknown manager surface {surface!r}. "
            f"Valid surfaces are {sorted(ALL_SURFACES)}."
        )
    return surface


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ManagerScope:
    """The answer to "is this manager narrowed, and to what?".

    `enforced=False` means every surface is permitted — either because the agency
    has not opted in, because the user is not a narrowable manager, or because the
    control plane could not be read (in which case `reason` says so and a WARNING
    was logged).
    """

    enforced: bool
    functions: frozenset[str]
    surfaces: frozenset[str]
    reason: str

    def permits(self, surface: str) -> bool:
        """True if this scope allows `surface`."""
        return (not self.enforced) or (surface in self.surfaces)


#: The unenforced answer, built with the reason it was reached.
def _unscoped(reason: str) -> ManagerScope:
    return ManagerScope(
        enforced=False,
        functions=frozenset(),
        surfaces=ALL_SURFACES,
        reason=reason,
    )


# ── Cache ─────────────────────────────────────────────────────────────────────
#
# Appointments and the opt-in flag change rarely; this is read on every request to a
# scoped route. A short TTL keeps a change visible within a minute without making a
# manager's page load wait on three Postgres round trips.

_CACHE_TTL_SECONDS = 60.0
_scope_cache: dict[tuple[str, str], tuple[ManagerScope, float]] = {}


def _cache_identity(user: dict[str, Any]) -> str | None:
    """A stable per-user cache key part, or None when there isn't one.

    Returning None means DO NOT CACHE. An earlier version fell back to `""` when a
    user dict carried neither `id` nor `user_id`, which silently made `("", building)`
    a shared key: two such callers would have read each other's scope. In a layer that
    decides access, a cache collision is not a performance bug.
    """
    for key in ("user_uuid", "id", "user_id"):
        raw = user.get(key)
        if raw:
            return str(raw)
    return None


def invalidate_manager_scope_cache(user_id: str | None = None) -> None:
    """Drop cached scopes so an appointment or opt-in change takes effect at once.

    Called by the endpoints that change either input. Without it a manager keeps
    their old reach for up to `_CACHE_TTL_SECONDS` after being appointed, which is
    confusing to the person who just made the change and looks like a bug.

    `user_id` is a HINT, not a guarantee. The cache is keyed by whatever identity the
    caller's session carried, and an appointment names the Postgres `core.users` id;
    for a legacy Mongo-token session those differ (footgun #24), so a targeted drop
    can miss. Callers that change appointments should therefore pass nothing and
    clear the whole cache — it holds at most one small tuple per signed-in manager,
    and appointment changes are rare administrative actions.
    """
    if user_id is None:
        _scope_cache.clear()
        return
    for key in [k for k in _scope_cache if k[0] == str(user_id)]:
        _scope_cache.pop(key, None)


# ── Identity resolution ───────────────────────────────────────────────────────

def _candidate_uuid(user: dict[str, Any]) -> str | None:
    """The user's Postgres user_id, if one of the identity keys parses as a UUID.

    A Postgres-path session carries it as both `user_uuid` and `id`. A legacy
    Mongo-token session carries a Mongo id that may or may not be the same value —
    footgun #24 — so this is a candidate, verified against core.users below.
    """
    for key in ("user_uuid", "id", "user_id", "_id"):
        raw = user.get(key)
        if not raw:
            continue
        try:
            return str(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            continue
    return None


async def _resolve_pg_user_id(user: dict[str, Any]) -> str | None:
    """Resolve the caller to a core.users row, by id then by email.

    The email fallback exists because Mongo and Postgres rows for the same person
    can carry different ids (footgun #24), and email is the only identifier the two
    stores share. Cheap in practice: this whole path runs only for `strata_manager`
    callers, of which a tenant has a handful, and the result is cached.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    candidate = _candidate_uuid(user)
    email = (user.get("email") or "").strip().lower()

    async with async_session_context() as session:
        # core.users is FORCE-RLS. Without this the SELECTs below return zero rows
        # and this function reports "user not found" for a user who is signed in.
        await set_tenant(session, _RLS_BYPASS_TENANT)
        if candidate:
            row = await session.execute(
                text("SELECT user_id::text FROM core.users WHERE user_id = CAST(:uid AS UUID)"),
                {"uid": candidate},
            )
            found = row.fetchone()
            if found:
                return found[0]

        if email:
            row = await session.execute(
                text("SELECT user_id::text FROM core.users WHERE email = :email LIMIT 1"),
                {"email": email},
            )
            found = row.fetchone()
            if found:
                logger.info(
                    "manager scope: resolved %s by email, not by id "
                    "(legacy session or divergent ids)", email,
                )
                return found[0]

    return None


async def _resolve_scheme_id(building_id: str) -> str | None:
    """core.schemes.scheme_id for a building/plan number."""
    from sqlalchemy import text
    from db_postgres.session import async_session_context, set_tenant

    async with async_session_context() as session:
        # core.schemes is FORCE-RLS, and this lookup runs before any tenant is known
        # (resolving the building number is how we would find the tenant). Without
        # the sentinel this returns None for every building that exists.
        await set_tenant(session, _RLS_BYPASS_TENANT)
        row = await session.execute(
            text("SELECT scheme_id::text FROM core.schemes WHERE scheme_number = :bid LIMIT 1"),
            {"bid": str(building_id)},
        )
        found = row.fetchone()
        return found[0] if found else None


async def _load_appointments(user_id: str, scheme_id: str) -> list[tuple[str, str]]:
    """Active (appointment_type, management_entity_id) pairs for this user + scheme.

    No tenant context is set, and that is correct rather than an oversight:
    core.scheme_manager_appointments has `relrowsecurity = f` (verified live
    2026-08-29). It is already filtered by the caller's own scheme_id, which was
    itself resolved from the authenticated building context.
    """
    from sqlalchemy import text
    from db_postgres.session import async_session_context

    async with async_session_context() as session:
        rows = await session.execute(
            text("""
                SELECT appointment_type, management_entity_id::text
                FROM core.scheme_manager_appointments
                WHERE user_id = CAST(:uid AS UUID)
                  AND scheme_id = CAST(:sid AS UUID)
                  AND status = 'active'
                  AND (end_date IS NULL OR end_date >= CURRENT_DATE)
                  AND COALESCE(is_test_data, FALSE) = FALSE
            """),
            {"uid": user_id, "sid": scheme_id},
        )
        return [(r[0], r[1]) for r in rows.fetchall()]


async def _any_entity_opted_in(entity_ids: Iterable[str]) -> bool:
    """True if ANY of these management entities has switched function scoping on.

    ANY rather than ALL: a manager appointed by two entities, one of which has opted
    in, is narrowed. The agency that adopted the policy would not accept "they also
    work for someone else" as a reason to exempt them.

    Like _load_appointments, no tenant context: core.management_entities has RLS
    disabled (`relrowsecurity = f`, verified live 2026-08-29) and is deliberately
    excluded from the FORCE-RLS set in migration 0091 because its tenant_id is
    nullable. The ids passed in come from appointments already scoped to the
    caller's scheme.
    """
    ids = [e for e in entity_ids if e]
    if not ids:
        return False

    from sqlalchemy import text
    from db_postgres.session import async_session_context

    async with async_session_context() as session:
        row = await session.execute(
            text("""
                SELECT 1
                FROM core.management_entities
                WHERE management_entity_id = ANY(CAST(:ids AS UUID[]))
                  AND function_scoping_enabled IS TRUE
                LIMIT 1
            """),
            {"ids": ids},
        )
        return row.fetchone() is not None


# ── The resolver ──────────────────────────────────────────────────────────────

async def resolve_manager_scope(
    user: dict[str, Any],
    building_id: str | None,
) -> ManagerScope:
    """What surfaces may this caller reach in this building?

    Returns an UNENFORCED scope for anyone this feature does not apply to, and for
    every case where the control plane cannot be read. See the module docstring on
    why that direction is correct here and what it costs.
    """
    # 1. Only a strata_manager is ever narrowed. super_admin and strata_admin operate
    #    the platform and the tenant; ec_member and the resident roles are governed by
    #    their own guards and have nothing to do with an agency's internal division of
    #    labour.
    role = effective_role(user)
    if role != UserRole.STRATA_MANAGER:
        return _unscoped(f"role {role!r} is not narrowable")

    if not building_id:
        logger.warning("manager scope: no building context; not narrowing")
        return _unscoped("no building context")

    identity = _cache_identity(user)
    cache_key = (identity, str(building_id)) if identity else None

    if cache_key is not None:
        cached = _scope_cache.get(cache_key)
        if cached is not None:
            scope, expires_at = cached
            if time.monotonic() < expires_at:
                return scope

    scope = await _resolve_uncached(user, str(building_id))

    # An unidentifiable caller is resolved every time rather than sharing a key.
    if cache_key is not None:
        _scope_cache[cache_key] = (scope, time.monotonic() + _CACHE_TTL_SECONDS)
    return scope


async def _resolve_uncached(user: dict[str, Any], building_id: str) -> ManagerScope:
    """The uncached body of resolve_manager_scope. Never raises."""
    try:
        scheme_id = await _resolve_scheme_id(building_id)
        if not scheme_id:
            logger.warning(
                "manager scope: building %s has no core.schemes row; not narrowing",
                building_id,
            )
            return _unscoped("scheme not found")

        pg_user_id = await _resolve_pg_user_id(user)
        if not pg_user_id:
            logger.warning(
                "manager scope: could not resolve %s to a core.users row; not narrowing",
                user.get("email") or user.get("id"),
            )
            return _unscoped("user not resolved in postgres")

        appointments = await _load_appointments(pg_user_id, scheme_id)
        if not appointments:
            # Rule 2: no appointment recorded means nothing has been said about this
            # person's job, which is not the same as saying they do nothing.
            return _unscoped("no active appointment for this scheme")

        types = {atype for atype, _ in appointments}

        # Rule 3: engagement beats function. They ARE the appointed manager.
        unrestricted = types & UNRESTRICTED_APPOINTMENT_TYPES
        if unrestricted:
            return _unscoped(
                f"holds engagement appointment {sorted(unrestricted)}"
            )

        functions = frozenset(
            _TYPE_TO_FUNCTION[atype] for atype in types if atype in _TYPE_TO_FUNCTION
        )
        if not functions:
            # e.g. `caretaker`, which names no function surface.
            return _unscoped(f"appointment types {sorted(types)} map to no function")

        if not await _any_entity_opted_in(eid for _, eid in appointments):
            return _unscoped("agency has not enabled function scoping")

        surfaces = frozenset().union(*(FUNCTION_SURFACES[f] for f in functions))
        return ManagerScope(
            enforced=True,
            functions=functions,
            surfaces=surfaces,
            reason=f"narrowed to {sorted(functions)}",
        )

    except Exception as exc:  # noqa: BLE001 - deliberate: see module docstring
        logger.warning(
            "manager scope: resolution failed for building %s (%s); NOT narrowing",
            building_id, exc, exc_info=True,
        )
        return _unscoped(f"resolution error: {exc}")


async def permits_surface(
    user: dict[str, Any],
    building_id: str | None,
    surface: str,
) -> bool:
    """True if this caller may reach `surface` in this building."""
    scope = await resolve_manager_scope(user, building_id)
    return scope.permits(validate_surface(surface))
