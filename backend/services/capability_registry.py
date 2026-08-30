# @featuretrace:scoped-capability-access — Canonical fail-closed capability registry and scope evaluator.
# Layer: service
# Data flow: authenticated user claims + route scope -> can()/require_capability() -> allow or HTTP 403 (scope param: building|global).
# Related: backend/routers/bi.py
#          backend/routers/cutover_admin.py
#          backend/routers/feature_toggles.py
# Tests: tests/backend/test_capability_registry.py
"""Canonical capability registry and explicit-scope authorization contract.

The evaluator is synchronous and side-effect free. Authentication and membership
services put verified scope claims on the user mapping; authorization remains a
predictable, allocation-light lookup on each request. Unknown roles, unknown
capabilities, missing scope, and malformed claims all fail closed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status

from models.user import UserRole
from services.authorisation_context import POLICY_VERSION
from services.field_masking import obligations_for
from utils.auth import effective_role, get_current_building, get_current_user

logger = logging.getLogger(__name__)

ScopeType = Literal["platform", "organisation", "building", "unit", "work_order"]


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    name: str
    scope_type: ScopeType
    roles: frozenset[str]
    description: str
    governance_offices: frozenset[str] = frozenset()
    required_authority: str | None = None
    legal_basis: tuple[str, ...] = ()


_SUPER = UserRole.SUPER_ADMIN
_ORG_ADMIN = frozenset({_SUPER, UserRole.STRATA_ADMIN})
_BUILDING_MANAGERS = frozenset({_SUPER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER})
_BUILDING_GOVERNANCE = frozenset({*_BUILDING_MANAGERS, UserRole.EC_MEMBER})
_BUILDING_OPERATIONS = frozenset({*_BUILDING_MANAGERS, UserRole.ADMIN_STAFF})
_EC_ONLY = frozenset({UserRole.EC_MEMBER})

_GOVERNANCE_OFFICE_ALIASES = {
    "chairman": "chairperson",
    "chair": "chairperson",
    "chairperson": "chairperson",
    "secretary": "secretary",
    "treasurer": "treasurer",
    "member": "ordinary_member",
    "ordinary_member": "ordinary_member",
}


def _definition(
    name: str,
    scope_type: ScopeType,
    roles: frozenset[str],
    description: str,
    *,
    governance_offices: frozenset[str] = frozenset(),
    required_authority: str | None = None,
    legal_basis: tuple[str, ...] = (),
) -> tuple[str, CapabilityDefinition]:
    """Generated function header.

    Function: _definition
    Path: backend/services/capability_registry.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return name, CapabilityDefinition(
        name,
        scope_type,
        roles,
        description,
        governance_offices,
        required_authority,
        legal_basis,
    )


CAPABILITY_REGISTRY: Mapping[str, CapabilityDefinition] = MappingProxyType(dict([
    _definition("platform.security.manage", "platform", frozenset({_SUPER}), "Manage platform security."),
    _definition("platform.feature_flags.manage", "platform", frozenset({_SUPER}), "Manage feature flags."),
    _definition("platform.bi.view", "platform", frozenset({_SUPER}), "View platform-wide BI."),
    _definition("organisation.portfolio.view", "organisation", _ORG_ADMIN, "View an organisation portfolio."),
    _definition("organisation.buildings.view", "organisation", _ORG_ADMIN, "View organisation buildings."),
    _definition("organisation.buildings.manage", "organisation", _ORG_ADMIN, "Manage organisation buildings."),
    _definition("organisation.users.manage", "organisation", _ORG_ADMIN, "Manage organisation users."),
    _definition("organisation.cutover.view", "organisation", _ORG_ADMIN, "View organisation cutover state."),
    _definition("organisation.cutover.manage", "organisation", frozenset({_SUPER}), "Change cutover state."),
    _definition("building.dashboard.view", "building", _BUILDING_GOVERNANCE, "View a building dashboard."),
    _definition("building.finance.view", "building", _BUILDING_GOVERNANCE, "View building finance."),
    _definition("building.finance.manage", "building", _BUILDING_MANAGERS, "Manage building finance."),
    _definition("building.finance.generate", "building", _ORG_ADMIN, "Generate finance forecasts and simulations."),
    _definition("building.finance.plan.manage", "building", _BUILDING_GOVERNANCE, "Manage the capital works plan."),
    _definition("building.arrears.view", "building", _BUILDING_GOVERNANCE, "View building arrears."),
    _definition("building.arrears.manage", "building", _BUILDING_MANAGERS, "Manage building arrears."),
    _definition("building.maintenance.view", "building", _BUILDING_OPERATIONS, "View maintenance."),
    _definition("building.maintenance.manage", "building", _BUILDING_OPERATIONS, "Manage maintenance."),
    _definition("building.documents.view", "building", _BUILDING_GOVERNANCE, "View documents."),
    _definition("building.documents.manage", "building", _BUILDING_OPERATIONS, "Manage documents."),
    _definition("building.meetings.view", "building", _BUILDING_GOVERNANCE, "View meetings."),
    _definition("building.meetings.manage", "building", _BUILDING_GOVERNANCE, "Legacy broad meeting management; migrate routes to the office-specific capabilities below."),
    _definition(
        "building.governance.vote",
        "building",
        _EC_ONLY,
        "Vote as an executive committee member.",
        legal_basis=("UTMA 2011 (ACT) sch 2 ss 2.10-2.11",),
    ),
    _definition(
        "building.meetings.call",
        "building",
        _EC_ONLY,
        "Call an executive committee meeting with the statutory notice.",
        legal_basis=("UTMA 2011 (ACT) sch 2 s 2.8",),
    ),
    _definition(
        "building.meetings.agenda.manage",
        "building",
        _EC_ONLY,
        "Set meeting agendas as chairperson.",
        governance_offices=frozenset({"chairperson"}),
        legal_basis=("UTMA 2011 (ACT) s 41",),
    ),
    _definition(
        "building.meetings.minutes.prepare",
        "building",
        _EC_ONLY,
        "Prepare and distribute meeting minutes as secretary.",
        governance_offices=frozenset({"secretary"}),
        legal_basis=("UTMA 2011 (ACT) s 42",),
    ),
    _definition(
        "building.finance.records.prepare",
        "building",
        _EC_ONLY,
        "Maintain statutory financial records and prepare statements as treasurer.",
        governance_offices=frozenset({"treasurer"}),
        legal_basis=("UTMA 2011 (ACT) s 43",),
    ),
    _definition(
        "building.finance.payment.execute",
        "building",
        _EC_ONLY,
        "Execute a payment as treasurer after executive committee authorisation.",
        governance_offices=frozenset({"treasurer"}),
        required_authority="resolution",
        legal_basis=("UTMA 2011 (ACT) s 43(c)",),
    ),
    _definition(
        "building.management.delegated.execute",
        "building",
        frozenset({UserRole.STRATA_MANAGER, UserRole.ADMIN_STAFF}),
        "Exercise a building function covered by a verified written delegation.",
        required_authority="delegation",
        legal_basis=("UTMA 2011 (ACT) ss 50(2), 52 and 58",),
    ),
    _definition("building.people.view", "building", _BUILDING_OPERATIONS, "View building people."),
    _definition("building.people.manage", "building", _BUILDING_MANAGERS, "Manage building people."),
    # Separate from building.people.manage because admin_staff are the registration
    # reviewers (_STAFF_REVIEWER_ROLES in routers/auth.py) — they receive the
    # approval email and must be able to act on it — but must not inherit account
    # deletion, archival or role assignment along with it. Splitting the capability
    # is how the matrix's "staff: assigned only, PII masked by default" row becomes
    # enforceable without breaking the onboarding flow.
    _definition(
        "building.people.onboarding.manage",
        "building",
        _BUILDING_OPERATIONS,
        "Review and decide resident registration and onboarding requests.",
    ),
    # Building go-live onboarding checklist (routers/portfolio.py). The role set
    # reproduces the `_MANAGER_WITH_CHAIRMAN` guard those routes already enforced —
    # this adds building scoping, not reach. Distinct from
    # building.people.onboarding.manage, which is about resident registrations.
    _definition("building.onboarding.view", "building", _BUILDING_GOVERNANCE, "View a building's go-live onboarding checklist."),
    _definition("building.onboarding.manage", "building", _BUILDING_GOVERNANCE, "Complete go-live onboarding steps and mark a building live."),
    # Per-building mock/live boundary for the external financial integrations
    # (routers/building_integrations.py). _BUILDING_GOVERNANCE minus ec_member:
    # pointing a building at a real financial institution is a management act, not a
    # committee one, so the role set is the managers plus the platform owner.
    _definition(
        "building.integrations.view",
        "building",
        _BUILDING_MANAGERS,
        "View which external financial integrations are mocked for a building.",
    ),
    _definition(
        "building.integrations.manage",
        "building",
        _BUILDING_MANAGERS,
        "Switch a building's external financial integrations between mock and live.",
    ),
    _definition("building.powerhouse.view", "building", _BUILDING_MANAGERS, "View Powerhouse."),
    _definition("building.bi.view", "building", _BUILDING_GOVERNANCE, "View building BI."),
    _definition("building.bi.manage", "building", frozenset({_SUPER, UserRole.STRATA_ADMIN}), "Manage building BI and ETL."),
    _definition("building.bi.cutover.view", "building", _BUILDING_MANAGERS, "Inspect BI cutover readiness."),
    _definition("building.cutover.view", "building", _BUILDING_GOVERNANCE, "Inspect cutover state."),
    _definition("building.cutover.manage", "building", frozenset({_SUPER}), "Change cutover state."),
    # Deliberately NOT _BUILDING_GOVERNANCE. tests/backend/routers/test_jurisdictional_rules_router.py
    # asserts ec_member is denied jurisdictional rules ("former chairman position does not have
    # access"), and strata_admin was never granted it either. This capability reproduces the role
    # set the route already enforced; it adds building scoping, not reach.
    _definition("building.jurisdiction.view", "building", frozenset({_SUPER, UserRole.STRATA_MANAGER}), "View a building's effective jurisdictional (statutory) rules and per-building overrides."),
    _definition("unit.levies.view", "unit", frozenset({_SUPER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.OWNER}), "View unit levies."),
    _definition("unit.documents.view", "unit", frozenset({_SUPER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.OWNER, UserRole.TENANT, UserRole.REAL_ESTATE_AGENT}), "View unit documents."),
    _definition("unit.maintenance.create", "unit", frozenset({_SUPER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.OWNER, UserRole.TENANT, UserRole.REAL_ESTATE_AGENT}), "Create unit maintenance."),
    _definition("unit.ownerhub.view", "unit", frozenset({_SUPER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.OWNER}), "View Owner Hub."),
    _definition("unit.tenancy.manage", "unit", frozenset({_SUPER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.OWNER, UserRole.REAL_ESTATE_AGENT}), "Manage unit tenancy."),
    _definition("work_order.assigned.view", "work_order", frozenset({_SUPER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.ADMIN_STAFF, UserRole.SERVICE_PROVIDER}), "View assigned work."),
]))


def _claim_values(user: Mapping[str, Any], *keys: str) -> frozenset[str]:
    """Generated function header.

    Function: _claim_values
    Path: backend/services/capability_registry.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    values: set[str] = set()
    for key in keys:
        value = user.get(key)
        if value in (None, ""):
            continue
        candidates = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
        values.update(str(item) for item in candidates if item not in (None, ""))
    return frozenset(values)


def _is_own_resource(user: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    """True only when the scope names a unit this subject is verifiably related to.

    Used to un-mask a subject's own arrears and contact details. Returns False
    whenever the scope does not carry a unit, because "not established" must
    resolve to masked, not to visible.
    """
    return _scope_value(scope, "unit_id") is not None and _unit_matches(user, scope)


def _governance_offices(user: Mapping[str, Any]) -> frozenset[str]:
    """Return canonical governance offices from verified identity claims."""
    raw = _claim_values(user, "governance_office", "governance_offices", "ec_position")
    return frozenset(
        _GOVERNANCE_OFFICE_ALIASES.get(value.lower(), value.lower())
        for value in raw
    )


def _authority_matches(
    user: Mapping[str, Any],
    scope: Mapping[str, Any],
    authority_type: str,
) -> bool:
    """Match a requested authority record to verified active authority claims."""
    authority_id = _scope_value(scope, f"{authority_type}_id")
    if not authority_id:
        return False
    return authority_id in _claim_values(
        user,
        f"{authority_type}_ids",
        f"active_{authority_type}_ids",
    )


def _scope_value(scope: Mapping[str, Any], key: str) -> str | None:
    """Generated function header.

    Function: _scope_value
    Path: backend/services/capability_registry.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    value = scope.get(key)
    return str(value) if value not in (None, "") else None


def _organisation_matches(user: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    """Generated function header.

    Function: _organisation_matches
    Path: backend/services/capability_registry.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    target = _scope_value(scope, "organisation_id")
    return bool(target and target in _claim_values(
        user, "organisation_id", "organisation_ids", "tenant_id", "tenant_ids"
    ))


def _building_matches(user: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    """Generated function header.

    Function: _building_matches
    Path: backend/services/capability_registry.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    target = _scope_value(scope, "building_id")
    return bool(target and target in _claim_values(
        user,
        "building_id",
        "current_building_id",
        "building_ids",
        "assigned_building_ids",
        "managed_building_ids",
    ))


def _unit_matches(user: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    """Generated function header.

    Function: _unit_matches
    Path: backend/services/capability_registry.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    target = _scope_value(scope, "unit_id")
    return bool(
        target
        and _building_matches(user, scope)
        and target in _claim_values(user, "unit_id", "unit_ids", "unit_number", "lot_number")
    )


def _work_order_matches(user: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    """Generated function header.

    Function: _work_order_matches
    Path: backend/services/capability_registry.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    target = _scope_value(scope, "work_order_id")
    return bool(
        target
        and _building_matches(user, scope)
        and target in _claim_values(user, "work_order_id", "assigned_work_order_ids", "work_order_ids")
    )


def _required_scope_present(scope_type: ScopeType, scope: Mapping[str, Any]) -> bool:
    """Generated function header.

    Function: _required_scope_present
    Path: backend/services/capability_registry.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if scope_type == "platform":
        return _scope_value(scope, "platform_id") == "platform"
    if scope_type == "organisation":
        return _scope_value(scope, "organisation_id") is not None
    if scope_type == "building":
        return _scope_value(scope, "building_id") is not None
    if scope_type == "unit":
        return (
            _scope_value(scope, "building_id") is not None
            and _scope_value(scope, "unit_id") is not None
        )
    return (
        _scope_value(scope, "building_id") is not None
        and _scope_value(scope, "work_order_id") is not None
    )


@dataclass(frozen=True)
class Decision:
    """The outcome of one authorisation question, with its reasoning.

    ``allowed`` is the answer. Everything else exists so a denial can be
    explained after the fact without re-running the request:

    - ``reason_codes`` are stable, machine-readable identifiers safe to log.
      Never build a user-facing message from them directly — a denial message
      must not disclose another tenant's identifiers or the shape of the policy.
    - ``obligations`` are conditions the CALLER must honour when allowed, most
      importantly field masking. A read is rarely allow/deny; it is usually
      allow-with-these-fields-withheld. ``require_capability`` records them on
      ``request.state`` and ``services.obligation_enforcement`` applies them to
      the serialised response, so a route cannot hold a Decision and forget its
      obligations.
    - ``policy_version`` records which ruleset decided, so a decision stays
      replayable after the rules change.
    - ``decision_id`` correlates the decision with its audit record.
    """

    allowed: bool
    capability: str
    reason_codes: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION
    decision_id: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.allowed


def _decision(
    allowed: bool,
    capability: str,
    *reason_codes: str,
    obligations: tuple[str, ...] = (),
) -> Decision:
    return Decision(
        allowed=allowed,
        capability=str(capability or ""),
        reason_codes=reason_codes,
        obligations=obligations,
        policy_version=POLICY_VERSION,
        decision_id=str(uuid4()),
    )


def decide(
    user: Mapping[str, Any] | None,
    capability: str,
    scope: Mapping[str, Any] | None,
) -> Decision:
    """Evaluate one capability against an explicit scope, with reasoning.

    Synchronous and I/O-free by contract. Verified claims must already be on
    ``user`` — ``services/authorisation_context.hydrate_authorisation_claims``
    puts them there, per requested scope. Anything this function cannot verify
    from the mapping it was given is a denial.

    Order matters. Role, office and authority are all checked BEFORE the
    super-admin bypass, so platform rank alone never confers a governance office
    or a recorded authority. That is deliberate: a super admin can repair
    platform configuration, not silently approve a scheme payment.
    """
    definition = CAPABILITY_REGISTRY.get(str(capability or ""))
    if user is None:
        return _decision(False, capability, "DENY_NO_SUBJECT")
    if definition is None:
        return _decision(False, capability, "DENY_UNKNOWN_CAPABILITY")
    if not isinstance(scope, Mapping):
        return _decision(False, capability, "DENY_MALFORMED_SCOPE")

    role = effective_role(dict(user))
    # 'chairman' is not a top-level role (see rules/post-compact-critical.md) — effective_role()
    # never returns it, so no CAPABILITY_REGISTRY definition lists it in .roles either.
    if role not in definition.roles:
        return _decision(False, capability, "DENY_ROLE_NOT_PERMITTED")

    if definition.governance_offices:
        held = definition.governance_offices.intersection(_governance_offices(user))
        if not held:
            return _decision(False, capability, "DENY_OFFICE_NOT_HELD")
        office_reason = f"OFFICE_{sorted(held)[0].upper()}"
    else:
        office_reason = None

    if definition.required_authority:
        if not _authority_matches(user, scope, definition.required_authority):
            return _decision(
                False, capability,
                f"DENY_AUTHORITY_MISSING_{definition.required_authority.upper()}",
            )
        authority_reason = f"AUTHORITY_{definition.required_authority.upper()}"
    else:
        authority_reason = None

    if not _required_scope_present(definition.scope_type, scope):
        return _decision(False, capability, "DENY_SCOPE_INCOMPLETE")

    granted = [f"ROLE_{role.upper()}"]
    if office_reason:
        granted.append(office_reason)
    if authority_reason:
        granted.append(authority_reason)

    # Field-level obligations ride on every allow. A read is rarely allow/deny —
    # it is usually allow-with-these-fields-withheld (plan §5). Computing them
    # here means a caller cannot forget to ask; applying them is the serialiser's
    # job via services.field_masking.apply_obligations().
    #
    # `own_resource` is true when the subject is reading their own lot: an owner
    # always sees their own arrears and contact details. Anything the scope does
    # not positively establish as the subject's own stays masked, which is the
    # fail-closed direction.
    obligations = obligations_for(
        role,
        _governance_offices(user),
        own_resource=_is_own_resource(user, scope),
    )

    if role == _SUPER:
        return _decision(True, capability, *granted, "ALLOW_PLATFORM_RANK", obligations=obligations)

    if definition.scope_type == "platform":
        return _decision(False, capability, "DENY_PLATFORM_SCOPE_REQUIRES_SUPER_ADMIN")
    if definition.scope_type == "organisation":
        if _organisation_matches(user, scope):
            return _decision(True, capability, *granted, "ALLOW_ORGANISATION_MATCH", obligations=obligations)
        return _decision(False, capability, "DENY_ORGANISATION_MISMATCH")
    if definition.scope_type == "building":
        if _building_matches(user, scope):
            return _decision(True, capability, *granted, "ALLOW_BUILDING_ASSIGNMENT", obligations=obligations)
        if role == UserRole.STRATA_ADMIN and _organisation_matches(user, scope):
            return _decision(True, capability, *granted, "ALLOW_ORGANISATION_MATCH", obligations=obligations)
        return _decision(False, capability, "DENY_BUILDING_NOT_ASSIGNED")
    if definition.scope_type == "unit":
        if _unit_matches(user, scope):
            return _decision(True, capability, *granted, "ALLOW_UNIT_RELATIONSHIP", obligations=obligations)
        return _decision(False, capability, "DENY_UNIT_NOT_RELATED")
    if _work_order_matches(user, scope):
        return _decision(True, capability, *granted, "ALLOW_WORK_ORDER_ASSIGNMENT", obligations=obligations)
    return _decision(False, capability, "DENY_WORK_ORDER_NOT_ASSIGNED")


def can(
    user: Mapping[str, Any] | None,
    capability: str,
    scope: Mapping[str, Any] | None,
) -> bool:
    """Return whether user has capability within an explicit scope.

    Boolean face of :func:`decide`. Kept as the primary API because most call
    sites only need the answer; use ``decide()`` when you need the reasoning.
    """
    return decide(user, capability, scope).allowed


def assert_capability(
    user: Mapping[str, Any],
    capability: str,
    scope: Mapping[str, Any],
) -> Decision:
    """Raise 403 unless the capability is granted; return the Decision if it is.

    The denial message deliberately does NOT include the reason codes. Those are
    for logs: telling a caller *why* they were denied discloses the shape of the
    policy and can confirm the existence of another tenant's resource. The
    decision_id is safe to return — it is a random correlation id, and it lets
    support match a user's report to the audit record without the user having to
    describe what they were doing.
    """
    decision = decide(user, capability, scope)

    # Audit here as well as in require_capability's dependency, because this is
    # the OTHER enforcement boundary: bi.py, cutover_admin.py and
    # finance_intelligence.py call assert_capability directly from their handler
    # bodies rather than declaring the dependency. Recording only in the
    # dependency left roughly a dozen live call sites — including every
    # building.finance.* check in finance_intelligence — producing decisions that
    # reached no audit trail at all.
    #
    # There is no double-recording: require_capability's dependency calls
    # decide() and raise_denied() itself and never routes through this function.
    # can() is deliberately NOT audited — it answers menu-visibility questions,
    # not enforcement, and auditing it would bury real decisions in noise.
    from services.authorisation_audit import record_decision

    record_decision(decision, subject=user, scope=scope)

    if not decision.allowed:
        raise_denied(decision)
    return decision


async def assert_capability_hydrated(
    user: Mapping[str, Any],
    capability: str,
    scope: Mapping[str, Any],
) -> Decision:
    """Hydrate verified claims for ``scope``, then enforce — the async form.

    Prefer the ``require_capability`` dependency. This exists for the handler
    bodies that cannot declare one because they resolve the target building from
    several places (a path param, a query param, or the session) before they know
    what to ask about: routers/bi.py, routers/cutover_admin.py and
    routers/finance_intelligence.py.

    Those call sites used the synchronous :func:`assert_capability`, which does no
    hydration at all. ``_building_matches`` then had only the two INHERITED claims
    to test — ``building_id`` / ``current_building_id`` — because the three
    verified ones (``building_ids``, ``assigned_building_ids``,
    ``managed_building_ids``) are written by hydration and were absent. The
    inherited pair is a stored preference (``default_scheme_id``) whenever the JWT
    carries no building, so a manager whose assignment for a scheme was revoked
    kept passing building-scoped checks for it. See GAP-SEC-014.

    Hydration is fail-closed: on any internal failure it yields empty office and
    authority claims, never broader ones.
    """
    from services.authorisation_context import hydrate_authorisation_claims

    # Same optimisation as the dependency: only a capability declaring
    # required_authority reads the authority/delegation claims, so tell hydration
    # when it can skip those two queries. An unknown capability hydrates fully —
    # decide() denies it on DENY_UNKNOWN_CAPABILITY regardless, and assuming "no
    # authority needed" for a definition we cannot see is the wrong default.
    capability_definition = CAPABILITY_REGISTRY.get(str(capability or ""))
    subject = await hydrate_authorisation_claims(
        user,
        scope,
        needs_authority=(
            capability_definition is None
            or capability_definition.required_authority is not None
        ),
    )
    return assert_capability(subject, capability, scope)


async def can_hydrated(
    user: Mapping[str, Any],
    capability: str,
    scope: Mapping[str, Any],
) -> bool:
    """The hydrating form of :func:`can`, for the same handler-body call sites.

    Still unaudited, like ``can`` — it answers visibility questions, not
    enforcement — but it is decided on verified claims rather than inherited ones.
    """
    from services.authorisation_context import hydrate_authorisation_claims

    capability_definition = CAPABILITY_REGISTRY.get(str(capability or ""))
    subject = await hydrate_authorisation_claims(
        user,
        scope,
        needs_authority=(
            capability_definition is None
            or capability_definition.required_authority is not None
        ),
    )
    return can(subject, capability, scope)


def raise_denied(decision: Decision) -> None:
    """Log and raise 403 for a decision that has ALREADY been made.

    Split out of :func:`assert_capability` so a caller holding a Decision can
    deny with *that* decision instead of computing a fresh one.

    This is not a refactor for tidiness. ``decide()`` stamps a random
    ``decision_id`` on every call, and the dependency in
    ``require_capability`` needs the decision twice: once to hand to the audit
    trail, once to deny with. Calling ``assert_capability`` after auditing
    re-ran ``decide()`` and produced a SECOND id — so the id written to
    ``core.audit_events`` was not the id returned to the caller in the 403 body.
    Support would have been given an id that matches nothing, which is the one
    job ``decision_id`` exists to do.

    The message deliberately omits reason codes: they are for logs, because
    telling a caller *why* they were denied discloses the shape of the policy and
    can confirm another tenant's resource exists.
    """
    logger.info(
        "authorisation denied capability=%s decision_id=%s reasons=%s policy=%s",
        decision.capability, decision.decision_id,
        ",".join(decision.reason_codes), decision.policy_version,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Capability '{decision.capability}' is not granted for the requested scope "
            f"(decision {decision.decision_id})"
        ),
    )


def require_capability(
    capability: str,
    *,
    scope_params: Mapping[str, str] | None = None,
    scope_values: Mapping[str, Any] | None = None,
    building_from_context: bool = False,
):
    """Create a dependency that extracts explicit path/query scope.

    ``building_from_context=True`` resolves ``building_id`` from the request's
    authenticated building context (``utils.auth.get_current_building``) instead
    of from a path or query parameter.

    Most routes in this codebase never name the building in their URL — it comes
    from the JWT claim, the user's membership, or a super admin's
    ``X-Building-ID`` override, all of which ``get_current_building`` already
    resolves and verifies. Without this flag those routes could not be migrated
    at all: ``scope_params`` would find nothing, ``_required_scope_present``
    would see no ``building_id``, and every request would deny with
    ``DENY_SCOPE_INCOMPLETE``.

    This is not a weakening. ``get_current_building`` verifies membership before
    returning, and an explicit scope value already present in ``scope_values`` or
    ``scope_params`` wins over the context — so a route that DOES name its
    building in the path keeps being decided on the building it named, not on
    whatever the caller's session happened to be pointed at.
    """
    params = dict(scope_params or {})
    static = dict(scope_values or {})

    async def _evaluate(
        request: Request,
        current_user: dict,
        context_building_id: str | None,
    ) -> dict:
        """Build the scope, hydrate claims, decide, and record the obligations."""
        scope = dict(static)
        for scope_key, parameter_name in params.items():
            value = request.path_params.get(parameter_name)
            if value in (None, ""):
                value = request.query_params.get(parameter_name)
            scope[scope_key] = value

        # An explicitly named building always wins. Falling back to the session's
        # building for a route that names one in its path would decide the wrong
        # question.
        if context_building_id and not _scope_value(scope, "building_id"):
            scope["building_id"] = context_building_id

        # Hydrate verified claims for the scope THIS request targets, before the
        # decision. can() stays synchronous and I/O-free; all lookups happen
        # here. Hydration is fail-closed: any failure yields empty office and
        # authority claims, never broader ones. See
        # services/authorisation_context.py for why this cannot be skipped —
        # without it, an office held at one building is tested against another.
        from services.authorisation_context import hydrate_authorisation_claims

        # Only a capability declaring required_authority consults the authority
        # and delegation claims, so tell hydration when it can skip those two
        # queries. Unknown capabilities hydrate fully: decide() denies them on
        # DENY_UNKNOWN_CAPABILITY anyway, and guessing "no authority needed" for
        # something we cannot see the definition of is the wrong default.
        # NB: not named `_definition` — that is the module-level factory used to
        # build CAPABILITY_REGISTRY, and shadowing it here would be a trap for
        # the next edit to this function.
        capability_definition = CAPABILITY_REGISTRY.get(str(capability or ""))
        subject = await hydrate_authorisation_claims(
            current_user,
            scope,
            needs_authority=(
                capability_definition is None
                or capability_definition.required_authority is not None
            ),
        )

        # Record BEFORE denying, so denials are audited too — they are the half
        # of the scope that is not negotiable (plan §8.8), and a denial that
        # raised before being recorded would be exactly the event nobody can
        # reconstruct later. Enqueue only; the single chain writer persists it
        # out of band.
        from services.authorisation_audit import record_decision as _audit_decision

        decision = decide(subject, capability, scope)
        _audit_decision(
            decision,
            subject=subject,
            scope=scope,
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
        )
        if not decision.allowed:
            # Deny with THIS decision, not a freshly computed one. Routing
            # through the other enforcement helper here would re-run decide()
            # and mint a second decision_id, so the id in the 403 body would not
            # match the id in the audit trail — breaking the correlation that is
            # the whole reason decision_id exists. It would also record the same
            # denial twice.
            raise_denied(decision)

        # Carry the obligations to the response. Until this line existed, every
        # guarded route computed a field mask and then discarded it, so nothing
        # was ever masked. The obligations are applied after serialisation by
        # ObligationEnforcementMiddleware — masking here would break any route
        # declaring a response_model, since WITHHELD is a string and the model
        # would reject it on a numeric field.
        from services.obligation_enforcement import record_obligations

        record_obligations(request, decision.obligations, decision_id=decision.decision_id)

        # Return the original user object: callers depend on its shape, and the
        # hydrated claims are an authorisation detail, not request state.
        return current_user

    if not building_from_context:

        async def capability_checker(
            request: Request,
            current_user: dict = Depends(get_current_user),
        ) -> dict:
            """Decide the capability against scope taken from the path/query."""
            return await _evaluate(request, current_user, None)

        return capability_checker

    # Declared as a real dependency rather than called inline, so FastAPI's
    # per-request dependency cache is used: a route that already depends on
    # get_current_building resolves it once, not twice. get_current_building
    # verifies membership and raises 403 itself when there is no building
    # context, which is the correct fail-closed outcome for a building-scoped
    # capability.
    async def capability_checker_with_building(
        request: Request,
        current_user: dict = Depends(get_current_user),
        context_building_id: str = Depends(get_current_building),
    ) -> dict:
        """Decide the capability against the authenticated building context."""
        return await _evaluate(request, current_user, context_building_id)

    return capability_checker_with_building


def serialise_capability_registry() -> list[dict[str, Any]]:
    """Stable frontend/code-generation representation for the P3 navigation pass."""
    return [
        {
            "name": item.name,
            "scope_type": item.scope_type,
            "roles": sorted(item.roles),
            "description": item.description,
            "governance_offices": sorted(item.governance_offices),
            "required_authority": item.required_authority,
            "legal_basis": list(item.legal_basis),
        }
        for item in sorted(CAPABILITY_REGISTRY.values(), key=lambda value: value.name)
    ]
