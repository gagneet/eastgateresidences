"""Role guards for router endpoints.

# @featuretrace:route-auth-guard — Canonical role-set enforcement for router endpoints.
# Layer: service
# Data flow: router endpoint -> require_roles()/assert_roles() -> utils.auth.effective_role (global).
# Related: backend/utils/auth.py (effective_role), tests/backend/test_route_guards.py,
#          tests/backend/test_elevated_role_guards.py, docs/architecture/canonical_owners.yaml

THE SINGLE HOME for "is this user allowed to call this endpoint?".

WHY THIS EXISTS
---------------
There were 116 private `_require_*` guards across 70 router files. `_require_manager`
alone had 19 copies with 9 different role sets, and nothing said which was intended:

  routers/defects_register.py       super_admin, strata_manager, strata_admin, ec_member, admin_staff
  routers/compliance_registers.py   super_admin, strata_manager, strata_admin, ec_member  (+ two dead strings)
  routers/arrears_recovery.py       super_admin, strata_manager, ec_member                 (no strata_admin)

Twelve of them tested for role strings that DO NOT EXIST — "admin", "treasurer",
"maintenance" — so those conditions could never match and the guard was silently
NARROWER than its author believed. `routers/defects_register.py` even carries a
comment diagnosing exactly that mistake; it was fixed in that one file and never
reached the other eighteen copies. That is the duplicate-concept failure recorded
in tasks/P0-CANONICAL-OWNER-REGISTRY.md, sitting in the authorisation layer.

WHY THIS TAKES THE ROLE SET AS AN ARGUMENT
------------------------------------------
Deliberately NOT `require_manager()`. Nineteen copies disagreeing proves there is
no single agreed meaning of "manager"; a helper that baked one in would just be the
twentieth opinion, and every endpoint that disagreed would go back to rolling its
own. Naming the roles at the call site makes each endpoint's trust boundary
reviewable in the diff that changes it.

TWO INVARIANTS THIS ENFORCES
----------------------------
1. The role is always resolved through `effective_role()`. A temporarily elevated
   user keeps their underlying role ("owner") and exposes the elevated one via
   `effective_role`; a guard reading the raw role 403s exactly the users elevation
   was meant to admit.
2. Every role must be a real `UserRole`. An unknown string raises at IMPORT time,
   not silently at request time, so "admin" can never again be mistaken for
   "admin_staff" and quietly lock a whole role out of a module.
"""
from __future__ import annotations

from typing import Callable, Iterable

from fastapi import Depends, HTTPException, status

from models.user import UserRole
from utils.auth import effective_role, get_current_user
from utils.auth import get_current_building as _get_current_building

# Every valid role value, derived from the model rather than restated here — a
# second list would drift from UserRole the moment a role is added or removed.
VALID_ROLES: frozenset[str] = frozenset(
    value
    for key, value in vars(UserRole).items()
    if not key.startswith("_") and isinstance(value, str)
)


def _validate(roles: Iterable[str]) -> frozenset[str]:
    """Reject unknown role names loudly, at import time.

    This is the whole point of the module. A dead string in an allowed-set is
    invisible: the guard keeps working, just for fewer people than intended, and
    nothing raises. Failing on import turns a silent permissions narrowing into a
    startup error that names the offending value.
    """
    resolved = frozenset(str(r) for r in roles)
    if not resolved:
        raise ValueError("a role guard must name at least one role")
    unknown = sorted(resolved - VALID_ROLES)
    if unknown:
        raise ValueError(
            f"unknown role(s) {unknown} in a route guard. "
            f"Valid roles are {sorted(VALID_ROLES)}. "
            "Note 'admin' is not a role — the back-office role is 'admin_staff'; "
            "'chairman' is not a role either — it is ECPosition.CHAIRMAN on an ec_member."
        )
    return resolved


def assert_roles(
    user: dict,
    roles: Iterable[str],
    detail: str = "You do not have permission to perform this action.",
) -> dict:
    """Raise 403 unless the user's EFFECTIVE role is in `roles`; else return the user.

    Imperative form, for the existing `_require_x(user)` call convention. Returns
    the user so a guard can stay a one-liner and callers that use the return value
    keep working.
    """
    allowed = _validate(roles)
    if effective_role(user) not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return user


def require_roles(
    *roles: str,
    detail: str = "You do not have permission to perform this action.",
) -> Callable:
    """FastAPI dependency form, for new endpoints.

        @router.get("/x")
        async def read_x(user: dict = Depends(require_roles(UserRole.SUPER_ADMIN))):

    The role set is validated when the module is imported, so a typo fails at
    startup rather than on the first request from the one role it locked out.
    """
    allowed = _validate(roles)

    async def _dependency(current_user: dict = Depends(get_current_user)) -> dict:
        if effective_role(current_user) not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
        return current_user

    return _dependency


# ─── Named role sets ─────────────────────────────────────────────────────────
#
# Provided ONLY for sets that are genuinely uniform across the codebase, so that a
# shared meaning has a shared name. Anything contested stays spelled out at the
# call site — see the module docstring on why `require_manager()` is not offered.

#: Platform operators. Unambiguous: every _require_super_admin copy agreed.
PLATFORM_ADMIN: frozenset[str] = frozenset({UserRole.SUPER_ADMIN})

#: Everyone who can act on behalf of the owners corporation in an OPERATIONAL
#: capacity. Excludes admin_staff, which the copies disagreed about — name that
#: role explicitly when an endpoint intends to include it.
OPERATIONAL_MANAGEMENT: frozenset[str] = frozenset(
    {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER}
)

#: Committee governance decisions. Deliberately excludes strata_manager: submitting
#: a committee decision is a different trust boundary from operating the building
#: (CLAUDE.md, "Governance vs operational boundary").
GOVERNANCE: frozenset[str] = frozenset({UserRole.SUPER_ADMIN, UserRole.EC_MEMBER})


# ─── Function scoping (a second, narrowing layer) ────────────────────────────
#
# Everything above answers "does this ROLE have access?". This answers a different
# question that only applies once the first has said yes: "does this manager's JOB
# cover this part of the product?"
#
# The two are deliberately separate dependencies rather than one merged guard. Role
# is a trust boundary the legislation recognises; function is an agency's internal
# division of labour, opt-in per agency, and switched off for everyone by default.
# Merging them would make every role guard carry a Postgres round trip for a feature
# almost no tenant has enabled, and would make the narrowing impossible to remove
# without touching all 116 guards again.

def require_manager_surface(
    surface: str,
    detail: str | None = None,
) -> Callable:
    """FastAPI dependency: 403 unless the caller's manager function covers `surface`.

    Layer this ON TOP of a role guard; it does not replace one. It can only ever
    turn an allow into a deny — a caller who is not a narrowable `strata_manager`,
    or whose agency has not opted in, passes through untouched.

        router = APIRouter(
            prefix="/insurance",
            dependencies=[Depends(require_manager_surface("insurance"))],
        )

    Put it on the ROUTER where a whole file belongs to one surface. A read is
    narrowed as well as a write, on purpose: Privacy Act APP 6 limits use to the
    purpose of collection, and "I only looked" is a use.

    The surface name is validated at IMPORT time, for the same reason the role sets
    above are. A surface belonging to no function would deny every narrowed manager
    and nobody else — indistinguishable from a deliberate restriction, and invisible
    to anyone whose agency has scoping off.
    """
    from services.manager_function_service import validate_surface

    validated = validate_surface(surface)

    async def _dependency(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(_get_current_building),
    ) -> dict:
        from services.manager_function_service import resolve_manager_scope

        scope = await resolve_manager_scope(current_user, building_id)
        if not scope.permits(validated):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                # Typed so the frontend can say WHICH part of the job is missing
                # rather than rendering a bare "forbidden". Read it through
                # getApiErrorDetail() — the global handler rewraps `detail`
                # (footgun #15).
                detail={
                    "code": "MANAGER_FUNCTION_SCOPE",
                    "message": detail or (
                        "Your appointment at this agency does not cover "
                        f"{validated.replace('_', ' ')}."
                    ),
                    "surface": validated,
                    "functions": sorted(scope.functions),
                    "retryable": False,
                },
            )
        return current_user

    return _dependency
