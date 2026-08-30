# @featuretrace:levy-fairness — measured drivers, and the remainder nobody holds.
# Layer: service
# Data flow: core.cost_allocation_rules -> allocate_by_rule() -> levy_fairness_service
#            benefit shares (building-scoped).
# Related: backend/services/levy_fairness_service.py
#          backend/routers/cost_allocation_rules.py
#          backend/alembic/versions/0108_cost_alloc_rules.py
# Tests: tests/backend/test_cost_allocation_rules.py
"""Apportion a cost line by a measured driver rather than by group membership.

A benefit-group tag answers "who may use this". A driver answers "how much of it does
each group hold or consume", and for a partly-shared cost the second is the only one
that can be defended. East Gate's garage is 139 bays -- 39 held by one group, 89 by the
other, 11 visitor bays held by nobody -- and no tag can express 28/64/8.

THE REMAINDER IS THE PART THAT GOES WRONG
-----------------------------------------
Every real driver has capacity or consumption attributable to no group: visitor bays,
common supply on a shared meter, corridor lighting on the house account. Two tempting
shortcuts both corrupt the result while still summing to 1.0, so neither announces
itself:

  * folding the remainder into a group's count states something untrue about who holds it;
  * dropping it apportions 139 bays' worth of cost across 128 bays.

So it is carried separately and disposed of explicitly, by `unassigned_treatment`:

  entitlement  (default) the remainder goes to every lot on unit entitlement -- the
               statutory default under UTMA s.78 for anything not otherwise attributable
  pro_rata     the remainder follows the measured shares
  excluded     the remainder leaves the cost base entirely

`entitlement` is the default because it is the position an owners corporation is already
in before it decides anything. A default of `pro_rata` would quietly assert that visitors
arrive in proportion to bays held, which may be true and is not a thing this module knows.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_BASES = frozenset({
    "entitlement", "equal_per_lot", "measured", "group_exclusive",
    "shared_measured", "excluded", "undetermined",
})
VALID_TREATMENTS = frozenset({"entitlement", "pro_rata", "excluded"})

# A rule only DRIVES an allocation in these two bases. Every other basis is either the
# behaviour the engine already had, or an explicit refusal to allocate.
_DRIVEN_BASES = frozenset({"measured", "shared_measured"})


class RuleNotApplicable(Exception):
    """The rule cannot produce an allocation, and the caller must fall back.

    Raised rather than returned as an empty dict because an empty allocation and "this
    rule does not apply" are different facts: the first would silently zero a real cost
    line, and the caller cannot tell them apart from the return value alone.
    """


def _f(value: Any) -> float:
    """Numeric coercion that refuses rather than guesses.

    Driver values arrive from JSONB and may be int, float, str or Decimal. A value that
    is not a number is a data-entry fault, and returning 0.0 for it would silently drop
    that group's entire share -- the group would read as benefiting from nothing.
    """
    if value is None:
        raise RuleNotApplicable("driver value is null")
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuleNotApplicable(f"driver value {value!r} is not numeric") from exc


def allocate_by_rule(
    rule: Dict[str, Any],
    cost: float,
    lot_groups: Dict[str, str],
    entitlements: Dict[str, float],
) -> Dict[str, float]:
    """Split ``cost`` across lots using the rule's measured driver.

    ``lot_groups``  maps unit_number -> benefit group key (the same keys used in
                    ``rule["driver_values"]``).
    ``entitlements`` maps unit_number -> unit entitlement, used to split WITHIN a group
                    and to dispose of an ``entitlement``-treated remainder.

    Raises :class:`RuleNotApplicable` whenever the rule cannot yield a defensible split,
    so the caller falls back to the group-tag allocation instead of receiving a number
    that looks computed.
    """
    basis = (rule.get("basis") or "undetermined").strip()
    if basis not in VALID_BASES:
        raise RuleNotApplicable(f"unknown basis {basis!r}")
    if basis == "excluded":
        # A deliberate zero, not a failure to compute. The caller must still not treat
        # this as "no rule" -- hence a distinct, empty-but-valid allocation.
        return {un: 0.0 for un in lot_groups}
    if basis not in _DRIVEN_BASES:
        raise RuleNotApplicable(f"basis {basis!r} is not driver-based")

    raw_values = rule.get("driver_values") or {}
    if not raw_values:
        raise RuleNotApplicable("no driver values recorded")

    # Only groups that actually hold lots. A driver value naming a group that has since
    # been emptied or deleted would otherwise consume a share and hand it to nobody,
    # shrinking every real group's cost with nothing to show where it went.
    live_groups = set(lot_groups.values())
    values: Dict[str, float] = {}
    for group, raw in raw_values.items():
        if group not in live_groups:
            logger.warning(
                "cost_allocation_rules: driver value for group %r on cost line %r has no "
                "lots; its share is dropped", group, rule.get("cost_line"),
            )
            continue
        v = _f(raw)
        if v < 0:
            raise RuleNotApplicable(f"driver value for {group!r} is negative")
        values[group] = v

    if not values:
        raise RuleNotApplicable("no driver value maps to a group that holds lots")

    unassigned = _f(rule.get("unassigned_units")) if rule.get("unassigned_units") is not None else 0.0
    if unassigned < 0:
        raise RuleNotApplicable("unassigned units is negative")

    treatment = (rule.get("unassigned_treatment") or "entitlement").strip()
    if treatment not in VALID_TREATMENTS:
        raise RuleNotApplicable(f"unknown unassigned treatment {treatment!r}")

    measured_total = sum(values.values())
    denominator = measured_total if treatment == "excluded" else measured_total + unassigned
    if denominator <= 0:
        raise RuleNotApplicable("driver total is zero")

    # `pro_rata` folds the remainder back into the measured shares, which is the same
    # arithmetic as excluding it. Stated once here rather than branching twice below.
    if treatment == "pro_rata":
        denominator = measured_total

    group_cost = {g: cost * (v / denominator) for g, v in values.items()}

    alloc: Dict[str, float] = {un: 0.0 for un in lot_groups}

    for group, amount in group_cost.items():
        members = [un for un, g in lot_groups.items() if g == group]
        member_ue = sum(entitlements.get(un, 0.0) for un in members)
        if member_ue > 0:
            for un in members:
                alloc[un] += amount * (entitlements.get(un, 0.0) / member_ue)
        elif members:
            # No entitlement data for the group: split equally rather than dropping the
            # cost. Equal-per-lot is a defensible fallback; silently losing the amount
            # is not, and it would make the allocation fail to sum to `cost`.
            share = amount / len(members)
            for un in members:
                alloc[un] += share

    if treatment == "entitlement" and unassigned > 0:
        remainder = cost * (unassigned / denominator)
        total_ue = sum(entitlements.get(un, 0.0) for un in lot_groups)
        if total_ue > 0:
            for un in lot_groups:
                alloc[un] += remainder * (entitlements.get(un, 0.0) / total_ue)
        elif lot_groups:
            share = remainder / len(lot_groups)
            for un in lot_groups:
                alloc[un] += share

    return alloc


def rule_reasoning(rule: Dict[str, Any], cost: float) -> Dict[str, Any]:
    """The checkable account of one rule, for the row that renders it.

    Carries the evidence reference and whether the driver repeats, because those are what
    decide admissibility rather than presentation: UTMA s.78(3) requires the corporation
    to have informed itself, and a one-off observation cannot support a standing
    contribution however precise it is.
    """
    values = rule.get("driver_values") or {}
    unassigned = rule.get("unassigned_units")
    parts = ", ".join(f"{g}: {v}" for g, v in sorted(values.items()))
    unit = rule.get("driver_unit") or "units"
    return {
        "basis": rule.get("basis"),
        "driver": rule.get("driver"),
        "driver_unit": unit,
        "driver_period": rule.get("driver_period"),
        "driver_values": values,
        "unassigned_units": float(unassigned) if unassigned is not None else None,
        "unassigned_treatment": rule.get("unassigned_treatment"),
        "arithmetic": (
            f"${cost:,.2f} split on {rule.get('driver') or 'a measured driver'} "
            f"({parts}{f'; {unassigned} {unit} unassigned' if unassigned else ''})"
        ),
        "evidence_ref": rule.get("evidence_ref"),
        "evidence_source": rule.get("evidence_source"),
        # A rule with no evidence is the assumption ACAT sets decisions aside for. Named
        # so the UI can mark it, rather than left for a reader to notice its absence.
        "evidence_recorded": bool(rule.get("evidence_ref") or rule.get("evidence_source")),
        "repeatable_measurement": (rule.get("driver_period") or "").lower() not in ("", "one_off", "once"),
        "editable": True,
        "overridden": False,
    }


async def load_rules_for_building(building_id: str) -> Dict[str, Dict[str, Any]]:
    """Rules for one building, keyed by cost line.

    Returns ``{}`` -- never raises -- when the control plane is unreachable, the scheme
    has no Postgres row, or the table does not exist yet. The fairness engine must keep
    producing its group-tag answer in all three cases; a rules table that is merely
    absent is not a reason to stop apportioning cost.
    """
    try:
        from db_postgres.session import async_session_context, set_tenant
        from db_postgres.repos.identity_repo import get_scheme_by_number
        from sqlalchemy import text
    except ImportError:  # pragma: no cover - Postgres layer not installed
        return {}

    try:
        async with async_session_context() as session:
            scheme = await get_scheme_by_number(building_id)
            if not scheme:
                return {}
            # FORCE-RLS: without tenant context every SELECT here returns zero rows and
            # no error, which is indistinguishable from "no rules configured".
            await set_tenant(session, str(scheme["tenant_id"]))
            rows = (await session.execute(
                text(
                    "SELECT cost_line, cost_line_label, basis, driver, driver_unit, "
                    "       driver_period, driver_values, unassigned_units, "
                    "       unassigned_treatment, evidence_ref, evidence_source, notes "
                    "FROM core.cost_allocation_rules "
                    "WHERE scheme_id = :sid AND is_test_data = FALSE"
                ),
                {"sid": str(scheme["scheme_id"])},
            )).mappings().all()
            return {r["cost_line"]: dict(r) for r in rows}
    except Exception as exc:  # noqa: BLE001 - degrade to the tag-based answer
        logger.warning(
            "cost_allocation_rules: could not load rules for building %s (%s); "
            "falling back to benefit-group tags", building_id, exc,
        )
        return {}
