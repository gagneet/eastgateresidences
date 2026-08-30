"""0103 — functional manager appointment types (levies / insurance / maintenance).

Revision ID: 0103_manager_function_types
Revises: 0102_owner_credit_null_fund_uq
Create Date: 2026-08-28

# @featuretrace:ec-internal-manager — management team functional specialisations.
# Layer: migration
# Data flow: core.scheme_manager_appointments → management_hierarchy_service
#            → /api/management-hierarchy/schemes/{id}/appointments (building-scoped).
#            An appointment names one scheme.
# Related: backend/models/user.py (ManagerFunction)
#          backend/services/management_hierarchy_service.py (VALID_APPOINTMENT_TYPES)
#          docs/architecture/strata_management_staff_access_model.md
# Table: core.scheme_manager_appointments

Widens ck_sma_appointment_type with three functional specialisations:
`levies_manager`, `insurance_manager`, `maintenance_manager`.

WHY
---
A managing agent fields specialists, and until now the schema could not say so.
East Gate's Civium team is a Strata Manager, a Levies Manager, an Insurance Manager
and a Maintenance Manager; all four could only be recorded as
`agency_strata_manager`, which loses exactly the distinction the owners corporation
sees on its own portal.

These are NOT new roles. Under the Unit Titles (Management) Act 2011 (ACT) s 58 the
owners corporation delegates functions to THE MANAGER — one legal person — and the
code of conduct (sch 1 pt 1.2) has the manager answerable for their employees
exercising "the manager's functions". Access Canberra's guide is explicit that
individual managers working for a licensed agent need no licence of their own. So a
functional title describes a job, not an authority: every one of these appointees is
a `strata_manager` whose reach is at most what that role already grants. See
backend/models/user.py::ManagerFunction for the full reasoning.

Additive and reversible: this only widens a CHECK constraint. No row changes, and
downgrade restores the original six values — safe because no existing row can hold
one of the new values before this migration runs.
"""
from __future__ import annotations

from alembic import op

revision = "0103_manager_function_types"
down_revision = "0102_owner_credit_null_fund_uq"
branch_labels = None
depends_on = None

_ORIGINAL_TYPES = (
    "agency_strata_manager",
    "independent_strata_manager",
    "ec_internal_strata_manager",
    "owner_volunteer_manager",
    "building_manager",
    "caretaker",
)

_ADDED_TYPES = (
    "levies_manager",
    "insurance_manager",
    "maintenance_manager",
)


def _check(values: tuple[str, ...]) -> str:
    """Render the appointment_type IN (...) predicate for `values`."""
    return "appointment_type IN ('" + "', '".join(values) + "')"


def upgrade() -> None:
    """Replace ck_sma_appointment_type with the widened value set."""
    op.drop_constraint(
        "ck_sma_appointment_type",
        "scheme_manager_appointments",
        schema="core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_sma_appointment_type",
        "scheme_manager_appointments",
        _check(_ORIGINAL_TYPES + _ADDED_TYPES),
        schema="core",
    )


def downgrade() -> None:
    """Restore the original six-value constraint.

    Any row holding one of the new values must be repointed first — the constraint
    is validated on creation, so this fails loudly rather than silently dropping the
    distinction.
    """
    op.drop_constraint(
        "ck_sma_appointment_type",
        "scheme_manager_appointments",
        schema="core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_sma_appointment_type",
        "scheme_manager_appointments",
        _check(_ORIGINAL_TYPES),
        schema="core",
    )
