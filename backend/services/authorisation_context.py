# @featuretrace:scoped-capability-access — Per-request authorisation claim hydration.
# Layer: service
# Data flow: FastAPI dependency → hydrate_authorisation_claims(user, scope) →
#            core.user_role_assignments / governance.ec_members -> claims dict ->
#            capability_registry.can() (building-scoped).
# Related: backend/services/capability_registry.py
#           backend/db_postgres/repos/identity_repo.py
#           docs/security/acl_information_access_implementation_plan.md (Phase 2)
# Tests: tests/backend/test_authorisation_context.py

"""Hydrate verified authorisation claims for the scope a request actually targets.

## Why this exists

``capability_registry.can()`` is deliberately synchronous and performs no I/O. It
reads claims — governance offices, assigned buildings, active authority IDs — off
the user mapping it is handed. Something has to put them there, from
authoritative records, per request. That is this module.

Before it existed, three things were wrong:

1. **Cross-building office leak.** ``identity_repo.get_user_by_id`` resolves
   ``ec_position`` from ``core.user_role_assignments`` filtered on the user's
   ``default_scheme_id``. ``can()`` then tested that office against the
   *requested* building. A treasurer at building A passed the treasurer office
   gate at building B. This module resolves the office for the requested scheme
   and **overwrites** any inherited value, so a stale default-scheme office
   cannot carry across.
2. ``assigned_building_ids`` / ``managed_building_ids`` were read by ``can()``
   and written by nothing, so ``strata_manager`` scope collapsed to the single
   ``building_id`` claim.
3. ``active_resolution_ids`` / ``active_delegation_ids`` were read by nothing and
   written by nothing, making the two ``required_authority`` capabilities
   permanently unreachable.

## Fail-closed contract

Every failure path yields FEWER claims, never more. A database error, a missing
scheme, an unresolvable tenant or an absent office record all produce empty
office and authority sets, which ``can()`` treats as deny. This module must
never widen a claim it could not verify.

The browser cannot assert any of these. Scope values arriving from path/query
parameters are used only to say *which* scheme to resolve against; the answer
always comes from the database.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Mapping

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Bumped whenever the authorisation policy's meaning changes, so an audited
# decision can be replayed against the rules that produced it. Phase 3 records
# this on every Decision; Phase 4 moves the catalogue into core.policy_versions.
POLICY_VERSION = "act-r25-1"

# Canonical office vocabulary. core.user_role_assignments.ec_position and
# governance.ec_members.ec_position both store the legacy upper-case forms
# (CHAIRMAN/TREASURER/SECRETARY/MEMBER); capability_registry expects the
# canonical lower-case ACT vocabulary. 'chairman' is NOT a role here — it is a
# stored office string being mapped to its canonical name.
_OFFICE_ALIASES = {
    "chairman": "chairperson",
    "chair": "chairperson",
    "chairperson": "chairperson",
    "secretary": "secretary",
    "treasurer": "treasurer",
    "member": "ordinary_member",
    "ordinary_member": "ordinary_member",
}

# Claims this module owns. Every one is overwritten on every hydration — never
# merged with whatever the caller already had — so an unverified or stale value
# can never survive into a decision.
_OWNED_CLAIMS = (
    "governance_offices",
    "ec_position",
    "assigned_building_ids",
    "managed_building_ids",
    "active_resolution_ids",
    "active_delegation_ids",
    "policy_version",
)

# Claims this module does not create but must VET, because capability_registry's
# _building_matches() accepts them as evidence of a building relationship.
#
# ``building_id`` reaches the user mapping two ways. When the JWT names a
# building, utils.auth.get_current_user verifies it via is_user_in_scheme()
# first — trustworthy. When it does not, the value is whatever
# identity_repo.get_user_by_id read from the user's ``default_scheme_id``,
# which is a stored preference, NOT proof of a live role assignment. A manager
# removed from a scheme keeps their stale default and, through that claim
# alone, kept passing building-scoped capability checks for it.
#
# This is the same defect already fixed for ``ec_position`` (see the module
# docstring): a default-scheme value being tested against the requested scheme.
_VETTED_CLAIMS = ("building_id", "current_building_id")


def _canonical_office(value: Any) -> str | None:
    """Map a stored office string to the canonical ACT vocabulary."""
    if value in (None, ""):
        return None
    return _OFFICE_ALIASES.get(str(value).strip().lower())


def _empty_claims() -> dict[str, Any]:
    """The deny-shaped claim set. Used for every failure path."""
    return {
        "governance_offices": [],
        "ec_position": None,
        "assigned_building_ids": [],
        "managed_building_ids": [],
        "active_resolution_ids": [],
        "active_delegation_ids": [],
        "policy_version": POLICY_VERSION,
    }


async def _resolve_target_scheme(building_value: str | None) -> dict | None:
    """Resolve a scope building value to its scheme row.

    The value may be a scheme UUID or a legacy plan number (``"13195"``),
    mirroring ``utils.auth.get_current_building``.
    """
    if not building_value:
        return None
    from db_postgres.repos import identity_repo

    return (
        await identity_repo.get_scheme_by_id(building_value)
        or await identity_repo.get_scheme_by_number(building_value)
    )


async def _offices_for_scheme(session, user: Mapping[str, Any], scheme_id: str) -> list[str]:
    """Offices the user currently holds AT this scheme.

    ``governance.ec_members`` is the authoritative, term-dated record and is
    preferred. It keys on ``party_id``, so it only answers for users with a
    linked party. ``core.user_role_assignments`` is the fallback while
    ``ec_members`` is being populated — it is scheme-scoped too, so the
    cross-building leak is closed either way.
    """
    offices: set[str] = set()

    party_id = user.get("party_id")
    if party_id:
        result = await session.execute(
            text("""
                SELECT ec_position
                  FROM governance.ec_members
                 WHERE party_id  = CAST(:pid AS UUID)
                   AND scheme_id = CAST(:sid AS UUID)
                   AND term_start <= :today
                   AND (term_end IS NULL OR term_end > :today)
            """),
            {"pid": str(party_id), "sid": scheme_id, "today": date.today()},
        )
        for row in result:
            office = _canonical_office(row[0])
            if office:
                offices.add(office)
        if offices:
            return sorted(offices)

    # Fallback: the role-assignment record, scoped to THIS scheme (never the
    # user's default scheme — that substitution is the bug this closes).
    result = await session.execute(
        text("""
            SELECT ec_position
              FROM core.user_role_assignments
             WHERE user_id   = CAST(:uid AS UUID)
               AND scheme_id = CAST(:sid AS UUID)
               AND role      = CAST('ec_member' AS core.user_role)
               AND is_active = TRUE
               AND (expires_at IS NULL OR expires_at > now())
             ORDER BY granted_at DESC
        """),
        {"uid": str(user.get("id")), "sid": scheme_id},
    )
    for row in result:
        office = _canonical_office(row[0])
        if office:
            offices.add(office)
    return sorted(offices)


async def _active_authority_ids(
    session,
    user: Mapping[str, Any],
    scheme_id: str,
    offices: list[str] | None = None,
) -> list[str]:
    """Authority IDs (EC resolutions) this subject may currently exercise here.

    An authority is exercisable only when every one of these holds:

    * it belongs to THIS scheme;
    * ``status = 'active'`` — not exhausted, revoked or expired;
    * ``revoked_at IS NULL`` — belt and braces, because status is a denormalised
      summary and a revocation must never depend on a background job having run;
    * today falls inside ``effective_from``/``effective_to`` where those are set;
    * it is granted either to this user directly, or to an office this user
      currently holds at this scheme.

    ``amount_limit_cents`` is deliberately NOT applied here. Hydration does not
    know the amount being attempted — the call site does. The limit is enforced
    where the money is known; this function only says which authorities exist.

    ``offices`` may be passed in when the caller has already resolved them for
    this same scheme. hydrate_authorisation_claims() always has — recomputing
    here cost a second round trip to governance.ec_members (plus, for users with
    no party_id, a third to core.user_role_assignments) on every authorisation
    decision in the application. Omitting it resolves them, so standalone
    callers keep working unchanged.
    """
    if offices is None:
        offices = await _offices_for_scheme(session, user, scheme_id)
    offices = list(offices)
    result = await session.execute(
        text("""
            SELECT authority_id
              FROM governance.authorities
             WHERE scheme_id = CAST(:sid AS UUID)
               AND status     = 'active'
               AND revoked_at IS NULL
               AND (effective_from IS NULL OR effective_from <= :today)
               AND (effective_to   IS NULL OR effective_to   >= :today)
               AND (
                     granted_to_user_id = CAST(:uid AS UUID)
                  OR (granted_to_office IS NOT NULL AND granted_to_office = ANY(:offices))
               )
        """),
        {
            "sid": scheme_id,
            "uid": str(user.get("id")),
            "today": date.today(),
            "offices": offices,
        },
    )
    return sorted(str(row[0]) for row in result)


async def _active_delegation_ids(session, user: Mapping[str, Any], scheme_id: str) -> list[str]:
    """Delegation IDs currently in force for this subject at this scheme.

    Delegations are personal: they name a grantee, never an office. A delegation
    is live only while un-revoked and inside its window.
    """
    result = await session.execute(
        text("""
            SELECT delegation_id
              FROM governance.delegations
             WHERE scheme_id        = CAST(:sid AS UUID)
               AND grantee_user_id  = CAST(:uid AS UUID)
               AND revoked_at IS NULL
               AND starts_at <= now()
               AND (ends_at IS NULL OR ends_at > now())
        """),
        {"sid": scheme_id, "uid": str(user.get("id"))},
    )
    return sorted(str(row[0]) for row in result)


async def _assigned_buildings(session, user: Mapping[str, Any]) -> list[str]:
    """Every scheme the user holds a live role assignment for.

    Returns both the scheme UUID and its plan number, because scope values reach
    ``can()`` in either form depending on the call site.
    """
    result = await session.execute(
        text("""
            SELECT DISTINCT s.scheme_id, s.scheme_number
              FROM core.user_role_assignments ura
              JOIN core.schemes s ON s.scheme_id = ura.scheme_id
             WHERE ura.user_id   = CAST(:uid AS UUID)
               AND ura.is_active = TRUE
               AND (ura.expires_at IS NULL OR ura.expires_at > now())
        """),
        {"uid": str(user.get("id"))},
    )
    buildings: set[str] = set()
    for scheme_id, scheme_number in result:
        if scheme_id:
            buildings.add(str(scheme_id))
        if scheme_number:
            buildings.add(str(scheme_number))
    return sorted(buildings)


async def hydrate_authorisation_claims(
    user: Mapping[str, Any] | None,
    scope: Mapping[str, Any] | None,
    *,
    needs_authority: bool = True,
) -> dict[str, Any]:
    """Return a NEW user mapping with verified claims for the requested scope.

    The input mapping is never mutated. Claims this module owns are always
    replaced, never merged, so an inherited default-scheme office cannot leak
    into a decision about a different building.

    Authority claims (``active_resolution_ids`` / ``active_delegation_ids``) are
    resolved from ``governance.authorities`` and ``governance.delegations``
    (migration 0093), honouring effective dates, status and revocation. A
    subject with no live authority gets an empty list, so the two
    ``required_authority`` capabilities deny — the correct fail-closed outcome.

    ``needs_authority=False`` skips those two queries. Only a capability
    declaring ``required_authority`` ever reads the claims (two of them in the
    whole registry: building.finance.payment.execute and
    building.management.delegated.execute), so every other decision was paying
    two round trips for lists nothing would consult. The claims stay present and
    EMPTY when skipped, which is the deny-shaped value — a caller that
    wrongly passes False can only lose an authority, never gain one. The default
    stays True so no existing caller silently changes meaning.
    """
    if user is None:
        return {}

    hydrated = {**user, **_empty_claims()}

    scope = scope if isinstance(scope, Mapping) else {}
    building_value = scope.get("building_id") or user.get("building_id")
    tenant_id = user.get("tenant_id")

    if not building_value or not tenant_id or not user.get("id"):
        # No resolvable scope or no Postgres identity — deny-shaped claims.
        return hydrated

    try:
        scheme = await _resolve_target_scheme(str(building_value))
        if not scheme:
            return hydrated
        scheme_id = str(scheme["scheme_id"])

        from db_postgres.session import async_session_context, set_tenant

        async with async_session_context() as session:
            # RLS: core.user_role_assignments and governance.ec_members are both
            # tenant-isolated with no bypass clause, so without this every query
            # below returns zero rows rather than erroring (footgun #8).
            await set_tenant(session, str(tenant_id))

            offices = await _offices_for_scheme(session, user, scheme_id)
            buildings = await _assigned_buildings(session, user)
            if needs_authority:
                # Reuse the offices just resolved: _active_authority_ids would
                # otherwise re-query them for this same scheme.
                resolutions = await _active_authority_ids(
                    session, user, scheme_id, offices=offices
                )
                delegations = await _active_delegation_ids(session, user, scheme_id)
            else:
                resolutions = []
                delegations = []

        hydrated["governance_offices"] = offices
        hydrated["active_resolution_ids"] = resolutions
        hydrated["active_delegation_ids"] = delegations
        # Overwrite, deliberately: identity_repo resolves ec_position from the
        # user's default scheme, which is the wrong scheme for this request.
        hydrated["ec_position"] = offices[0] if offices else None
        hydrated["assigned_building_ids"] = buildings
        hydrated["managed_building_ids"] = buildings

        # Drop an inherited building claim the live assignment set does not
        # corroborate, so a stale default_scheme_id cannot stand in for a role
        # assignment that has been revoked.
        #
        # ONLY when `buildings` is non-empty. An empty set is ambiguous — it means
        # either "assigned to nothing" or "this user predates
        # core.user_role_assignments" (Mongo-era accounts mid-cutover legitimately
        # have no row there). Vetting against an empty set would deny those users
        # outright, so that case is left behaving exactly as it does today and is
        # logged instead, because it is also the shape a silent RLS/tenant
        # misconfiguration takes (footgun #8: zero rows, no error).
        #
        # Narrowing on a POSITIVE answer is safe in the fail-closed direction: it
        # can only ever remove a claim, never add one. `buildings` holds both the
        # scheme UUID and the plan number for each assignment (see
        # _assigned_buildings), so a claim in either form still matches.
        if buildings:
            for claim in _VETTED_CLAIMS:
                value = hydrated.get(claim)
                if value and str(value) not in buildings:
                    logger.info(
                        "authorisation: dropping unverified %s=%s — not among the "
                        "subject's live role assignments for this tenant",
                        claim,
                        value,
                    )
                    hydrated[claim] = None
        elif any(hydrated.get(claim) for claim in _VETTED_CLAIMS):
            # Cannot vet: the claim stands, as it did before this check existed.
            logger.info(
                "authorisation: no live role assignments resolved for subject; "
                "inherited building claim left unvetted"
            )
    except Exception:  # noqa: BLE001 — fail closed on ANY hydration failure
        logger.warning(
            "authorisation claim hydration failed; denying with empty claims",
            exc_info=True,
        )
        return {**user, **_empty_claims()}

    return hydrated
