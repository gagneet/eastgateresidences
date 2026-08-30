"""0108 — how each cost line is apportioned, recorded with its evidence.

Revision ID: 0108_cost_alloc_rules
Revises: 0107_benefit_groups
Create Date: 2026-08-30

# @featuretrace:levy-fairness — a measured driver, and the document it came from.
# Layer: migration
# Data flow: benefit-assignment UI → core.cost_allocation_rules →
#            levy_fairness_service apportionment (building-scoped).
# Related: backend/services/levy_fairness_service.py
#          backend/routers/cost_allocation_rules.py
# Table: core.cost_allocation_rules

THE CASE THIS EXISTS FOR
------------------------
Until now a facility was tagged to one benefit group or to all. That expresses SHARED and
GROUP-EXCLUSIVE and nothing else — and the contested case is neither. It is PARTLY SHARED,
where both groups benefit in different, measurable amounts.

East Gate's garage is the type case: 139 bays, of which 39 are held by one group, 89 by
the other and 11 are visitor bays held by nobody. Tagged to a single group it resolved to
all 87 lots, i.e. modelled as fully shared, when the physical driver says 28% / 64% / 8%.
No tag can carry that, because the information is a QUANTITY, not a membership.

THE SHAPE IS THE SAME EVERY TIME: MEASURED PARTS PLUS A REMAINDER
-----------------------------------------------------------------
Three real cost lines, one structure:

  garage      39 bays / 89 bays,        11 visitor bays attributable to no group
  lift        600 trips / 23,400 trips, 0 unattributable
  water       one common meter covering ALL supply — residential and irrigation alike —
              with some lots separately sub-metered, so the measured parts are those
              readings and the remainder is genuinely common

`driver_values` holds the measured part per group; `unassigned_units` holds the remainder.
Separating them matters: pushing the 11 visitor bays into either group's count would state
something untrue about who holds them, and dropping them would apportion 139 bays' worth
of cost across 128 bays.

`unassigned_treatment` decides the remainder, and defaults to `entitlement` because that
is the statutory default for anything not otherwise attributable (UTMA s.78). `pro_rata`
spreads it across the measured shares; `excluded` removes it from the base entirely.

undetermined IS THE DEFAULT, AND IT IS AN ANSWER
------------------------------------------------
A rule with no driver values is `undetermined`, which records the QUESTION. Every value
here is entered by a person; nothing is inferred. A confident wrong classification is
worse than none, because it gets acted on and it looks exactly like a decision.

EVIDENCE IS NOT BOOKKEEPING
---------------------------
UTMA s.78(3) requires an owners corporation setting contributions on a different basis to
consider the nature of the buildings, the features and character of the units and common
property, the purposes for which units are used and their likely impact on common
property, and whether the burden is commensurate with that use. In Lanfranchi v Units
Plan 806 ACAT set a decision aside as "based on erroneous assumptions, not supported by
evidence or information."

A driver value with no recorded source IS that assumption. `evidence_ref` and
`evidence_source` are therefore columns, not notes — and the distinction they capture is
the one that decides admissibility: East Gate's lift figure is a MONTHLY report averaged
over two years, which can support a standing contribution, whereas a one-off count cannot.
`driver_period` records which it is.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0108_cost_alloc_rules"
down_revision = "0107_benefit_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_allocation_rules",
        sa.Column("rule_id", sa.UUID(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("scheme_id", sa.UUID(), nullable=False),
        # The cost line this rule governs — a facility id or a budget category key.
        # Free text rather than an FK: facilities live in Mongo for most buildings, and
        # an FK to a table half the platform does not use would block the feature.
        sa.Column("cost_line", sa.Text(), nullable=False),
        sa.Column("cost_line_label", sa.Text(), nullable=True),
        sa.Column("basis", sa.Text(), nullable=False,
                  server_default=sa.text("'undetermined'")),
        sa.Column("driver", sa.Text(), nullable=True),
        sa.Column("driver_unit", sa.Text(), nullable=True),
        # Whether the driver is a repeatable measurement or a single observation. Only the
        # first can support a standing contribution; see the module docstring.
        sa.Column("driver_period", sa.Text(), nullable=True),
        # {benefit_group_id: numeric}. Measured part per group.
        sa.Column("driver_values", postgresql.JSONB(), nullable=True),
        # Capacity or consumption attributable to no group (visitor bays, common supply).
        sa.Column("unassigned_units", sa.Numeric(18, 4), nullable=True),
        sa.Column("unassigned_treatment", sa.Text(), nullable=False,
                  server_default=sa.text("'entitlement'")),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("evidence_source", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("scheme_id", "cost_line", name="cost_alloc_rules_line_ux"),
        sa.CheckConstraint(
            "basis IN ('entitlement','equal_per_lot','measured','group_exclusive',"
            "'shared_measured','excluded','undetermined')",
            name="cost_alloc_rules_basis_ck",
        ),
        sa.CheckConstraint(
            "unassigned_treatment IN ('entitlement','pro_rata','excluded')",
            name="cost_alloc_rules_unassigned_ck",
        ),
        # A negative remainder is not a small error, it is a driver total that exceeds the
        # capacity it was measured against, and every share computed from it would be
        # wrong in a way that still sums to 1.0.
        sa.CheckConstraint(
            "unassigned_units IS NULL OR unassigned_units >= 0",
            name="cost_alloc_rules_unassigned_nonneg_ck",
        ),
        schema="core",
    )
    op.create_index("cost_alloc_rules_scheme_idx", "cost_allocation_rules",
                    ["scheme_id"], schema="core")

    op.execute("ALTER TABLE core.cost_allocation_rules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.cost_allocation_rules FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_core_cost_allocation_rules "
        "ON core.cost_allocation_rules USING (tenant_id = core.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_cost_allocation_rules "
               "ON core.cost_allocation_rules")
    op.drop_index("cost_alloc_rules_scheme_idx", table_name="cost_allocation_rules", schema="core")
    op.drop_table("cost_allocation_rules", schema="core")
