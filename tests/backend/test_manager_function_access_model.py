"""Who on a strata management team may reach what.

# @featuretrace:route-auth-guard — strata management staff access model.
# Layer: test
# Data flow: models.user.ManagerFunction + utils.route_guards -> router _require_* guards (global).
#            Role sets and the function vocabulary are platform-wide policy, not
#            per-building configuration.
# Related: backend/models/user.py (ManagerFunction)
#          backend/services/management_hierarchy_service.py (VALID_APPOINTMENT_TYPES)
#          backend/alembic/versions/0103_manager_function_types.py
#          docs/architecture/strata_management_staff_access_model.md

Two things are pinned here, and they fail for different reasons.

1. A manager FUNCTION never becomes a role. Under the Unit Titles (Management) Act
   2011 (ACT) s 58 the owners corporation delegates to THE MANAGER — one legal
   person — so "Levies Manager" is a job, not a trust boundary. A UserRole for it
   would repeat the `chairman` mistake that migration 0025 had to undo.

2. admin_staff is out of finance and in records administration. That split is an
   operator decision (2026-08-28: "they are for administrative work and financials
   are not administrative"), and it agrees with the permission model — admin_staff
   carries can_view_finances=False — and with the Agents Act 2003 (ACT) pt 7, which
   puts trust-money duties on the licensed agent, not on unlicensed staff.

Both are the kind of thing a future cleanup "tidies" by making the sets uniform.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from models.user import ManagerFunction, UserRole
from services.management_hierarchy_service import VALID_APPOINTMENT_TYPES
from utils.route_guards import VALID_ROLES

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _probe(guard, role: str) -> bool:
    """True if `guard` admits a user whose effective role is `role`."""
    try:
        guard({"role": role})
    except HTTPException:
        return False
    return True


# ─── 1. A function is not a role ──────────────────────────────────────────────

#: The functions this codebase did not already have a name for. STRATA_MANAGER and
#: BUILDING_MANAGER are deliberately excluded: the first shares its name with the
#: role, the second with ECPosition.BUILDING_MANAGER, and both overlaps are intended.
NEW_SPECIALISATIONS = ("LEVIES_MANAGER", "INSURANCE_MANAGER", "MAINTENANCE_MANAGER")


@pytest.mark.parametrize("function", ManagerFunction.ALL)
def test_manager_function_value_is_not_itself_a_role_string(function: str) -> None:
    """A function value must never be usable where a role is expected.

    route_guards._validate compares against VALID_ROLES, so a value that satisfied
    both vocabularies could sit in an allowed-set and read as deliberate.
    """
    assert function not in VALID_ROLES


@pytest.mark.parametrize("function", NEW_SPECIALISATIONS)
def test_specialisations_did_not_become_roles(function: str) -> None:
    """Levies / Insurance / Maintenance are jobs, not trust boundaries.

    Under UTMA s 58 the owners corporation delegates to THE MANAGER, one legal
    person; there is no statutory office of "levies manager". A UserRole for one
    would repeat the `chairman` mistake migration 0025 had to undo — and unlike
    that one, it would be a role nothing in the Act can justify.
    """
    assert function.lower() not in VALID_ROLES
    assert not hasattr(UserRole, function)


def test_every_function_maps_to_a_real_appointment_type() -> None:
    """A function whose appointment_type the DB rejects cannot be recorded at all."""
    for function, atype in ManagerFunction.APPOINTMENT_TYPE.items():
        assert ManagerFunction.is_valid(function), function
        assert atype in VALID_APPOINTMENT_TYPES, (function, atype)


def test_strata_manager_function_has_no_appointment_type() -> None:
    """How a strata manager is ENGAGED is a different axis from what they DO.

    agency / independent / ec_internal / owner_volunteer all describe engagement,
    and picking one for the caller would be a guess.
    """
    assert "STRATA_MANAGER" not in ManagerFunction.APPOINTMENT_TYPE


# ─── 2. The appointment-type vocabulary has one owner ────────────────────────

def test_router_does_not_keep_its_own_copy_of_the_vocabulary() -> None:
    """The router must import the set, not restate it.

    A stale copy fails silently in the worst direction: the CHECK constraint accepts
    a new appointment type while the router's validator rejects it, so the value is
    legal in Postgres and unreachable through the API.
    """
    from routers import management_hierarchy

    assert management_hierarchy._VALID_APPOINTMENT_TYPES is VALID_APPOINTMENT_TYPES


def test_migration_check_constraint_matches_the_owner() -> None:
    """Alembic 0103's CHECK must list exactly what the service considers valid.

    Parsed rather than imported: the migration module imports `alembic.op`, which is
    only bound inside a migration run.
    """
    path = BACKEND / "alembic" / "versions" / "0103_manager_function_types.py"
    tree = ast.parse(path.read_text())
    literals: dict[str, tuple[str, ...]] = {
        target.id: tuple(ast.literal_eval(node.value))
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"_ORIGINAL_TYPES", "_ADDED_TYPES"}
    }
    migration_types = set(literals["_ORIGINAL_TYPES"]) | set(literals["_ADDED_TYPES"])
    assert migration_types == set(VALID_APPOINTMENT_TYPES)


def test_revision_id_fits_the_version_column() -> None:
    """core.alembic_version.version_num is VARCHAR(32) (footgun #9)."""
    assert len("0103_manager_function_types") <= 32


# ─── 3. admin_staff: out of finance, in records administration ───────────────

FINANCE_GUARDS = [
    ("audit_management", "_require_finance"),
    ("bank_feeds", "_require_finance"),
    ("insurance", "_require_finance"),
    ("matching", "_require_finance"),
    ("trust_accounting", "_require_finance_role"),
]


@pytest.mark.parametrize("module_name,guard_name", FINANCE_GUARDS)
def test_admin_staff_is_denied_every_finance_guard(module_name: str, guard_name: str) -> None:
    """Operator decision 2026-08-28, and it is not a style choice.

    admin_staff's own Permission has can_view_finances=False and
    can_manage_finances=False, and under the Agents Act 2003 (ACT) pt 7 the duties
    that attach to trust money sit on the LICENSED AGENT. These guards carried a dead
    "admin" string until 2026-08-28; resolving it to admin_staff would have widened
    every one of them.
    """
    module = __import__(f"routers.{module_name}", fromlist=[guard_name])
    guard = getattr(module, guard_name)

    assert not _probe(guard, UserRole.ADMIN_STAFF)
    # The guard still works for whom it is meant to.
    assert _probe(guard, UserRole.STRATA_MANAGER)
    assert _probe(guard, UserRole.EC_MEMBER)


def test_admin_staff_may_keep_the_statutory_registers() -> None:
    """Register upkeep is records administration, which is what admin_staff is for.

    Their four sibling registers — pool_safety, essential_services, nsw_compliance,
    defects_register — have always admitted admin_staff; compliance_registers and whs
    were outliers only because their "admin" string never matched anything.
    """
    from routers import compliance_registers, whs

    assert _probe(compliance_registers._require_manager, UserRole.ADMIN_STAFF)
    assert _probe(whs._require_whs, UserRole.ADMIN_STAFF)


def test_service_provider_may_not_keep_the_registers_that_record_them() -> None:
    """The dead "maintenance" string is NOT resolved to service_provider.

    A contractor is the SUBJECT of an induction, a SWMS and a compliance item. Letting
    them maintain the register that records their own compliance is a different trust
    boundary from the one those guards were written for.
    """
    from routers import compliance_registers, whs

    assert not _probe(compliance_registers._require_manager, UserRole.SERVICE_PROVIDER)
    assert not _probe(whs._require_whs, UserRole.SERVICE_PROVIDER)


def test_decisions_split_governance_from_transparency() -> None:
    """admin_staff may READ the decisions register and may not WRITE to it.

    Minuting a committee decision is a governance act. Reading one is not — the same
    guard already admits owner and tenant, so excluding the managing agent's back
    office was an artefact of the dead "admin" string, not a boundary anyone chose.
    """
    from routers import decisions

    assert not _probe(decisions._require_ec, UserRole.ADMIN_STAFF)
    assert _probe(decisions._require_view, UserRole.ADMIN_STAFF)
    assert _probe(decisions._require_view, UserRole.OWNER)
    assert not _probe(decisions._require_view, UserRole.SERVICE_PROVIDER)
