"""0104 — per-agency opt-in for manager function scoping.

Revision ID: 0104_manager_fn_scoping
Revises: 0103_manager_function_types
Create Date: 2026-08-28

# @featuretrace:manager-function-scoping — the agency-level opt-in switch.
# Layer: migration
# Data flow: core.management_entities.function_scoping_enabled
#            → services/manager_function_service.resolve_manager_scope
#            → utils/route_guards.require_manager_surface (global).
#            Global, not building-scoped: the flag lives on the management ENTITY
#            and applies across every scheme in that agency's book.
# Related: backend/services/manager_function_service.py
#          backend/models/user.py (ManagerFunction)
#          docs/architecture/strata_management_staff_access_model.md
# Table: core.management_entities

Adds `function_scoping_enabled BOOLEAN NOT NULL DEFAULT FALSE`.

WHY AN OPT-IN, AND WHY IT DEFAULTS OFF
--------------------------------------
Narrowing a manager to their function is the direction privacy law indicates —
Privacy Act 1988 (Cth) APP 6 limits use to the purpose of collection, and a levies
clerk has no purpose that requires WHS incident reports. But switching it on for an
agency that did not ask for it 403s a working team mid-shift, on schemes whose
managing agent never agreed to it.

So the switch is per management ENTITY, not per building and not global: an agency
adopts function scoping for the whole of its book at once, which is how it is
actually decided. `FALSE` reproduces today's behaviour exactly — a strata_manager
sees everything strata_manager has always seen.

Deliberately NOT a core.feature_toggles row: those resolve per BUILDING (or
globally), and the subject here is the agency. A per-building toggle would let one
scheme in an agency's book narrow while its siblings did not, which is not a state
any agency would ask for and is one more way for a team to hit an inconsistent 403.

Additive and reversible: one nullable-free boolean with a safe default, no row
changes, no backfill.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0104_manager_fn_scoping"
down_revision = "0103_manager_function_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the opt-in flag, defaulted off so nothing changes on deploy."""
    op.add_column(
        "management_entities",
        sa.Column(
            "function_scoping_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "Opt-in: when TRUE, a strata_manager holding a FUNCTIONAL appointment "
                "for one of this entity's schemes is narrowed to that function's "
                "surface. FALSE (default) reproduces pre-2026-08-28 behaviour."
            ),
        ),
        schema="core",
    )


def downgrade() -> None:
    """Drop the flag. Every entity reverts to unscoped, which is the default anyway."""
    op.drop_column("management_entities", "function_scoping_enabled", schema="core")
