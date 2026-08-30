"""0107 — operator-configured benefit groups, replacing inferred cohorts.

Revision ID: 0107_benefit_groups
Revises: 0106_budget_categories
Create Date: 2026-08-30

# @featuretrace:levy-fairness — who is compared with whom, decided by the operator.
# Layer: migration
# Data flow: settings UI → core.benefit_groups + core.lot_benefit_groups →
#            levy_fairness_service group resolution (building-scoped).
# Related: backend/routers/benefit_groups.py
#          backend/services/levy_fairness_service.py
# Table: core.benefit_groups, core.lot_benefit_groups

Replaces cohort INFERENCE with cohort CONFIGURATION.

WHY INFERENCE HAD TO GO
-----------------------
`levy_fairness_service._group_key()` derives the cohort by string-matching `unit_type`
for "apartment"/"townhouse"/"villa"/"retail"/"commercial" and, failing that, by reading a
`UA`/`TH` prefix off the unit number. Both are wrong as platform logic:

* A scheme that is all apartments, or all townhouses, collapses to one group and the
  feature silently does nothing.
* A scheme split on something else entirely — a commercial ground floor, two towers
  sharing one basement, a staged development — is mis-grouped with no way to correct it.
* "Townhouse" and "apartment" are physical descriptors and carry no legal levy
  consequence in the ACT. Naming the analysis after them invites the reading that the
  building form is what justifies a different contribution, which is not the argument.

Groups are therefore NAMED BY THE OPERATOR — `Group A`, `Group B` by default — and lot
membership is assigned, not derived. East Gate configures units 1-70 and 71-87, and that
is a setting rather than a rule in the code.

MEMBERSHIP IS EXCLUSIVE, AND ENFORCED
-------------------------------------
A lot belongs to at most one comparison group; the primary key on `lot_benefit_groups` is
the lot, not the pair. A lot in two groups would be counted twice on both sides of a
redistribution that must be zero-sum, and the arithmetic would still balance — so nothing
downstream could detect it. The constraint is the only place it can be caught.

An UNASSIGNED lot is a legitimate state and is not an error: a scheme mid-configuration
has some. The engine reports unassigned lots rather than defaulting them into a group,
because a default here silently changes who subsidises whom.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0107_benefit_groups"
down_revision = "0106_budget_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benefit_groups",
        sa.Column("benefit_group_id", sa.UUID(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("scheme_id", sa.UUID(), nullable=False),
        # Operator-chosen. Defaults to "Group A"/"Group B" in the API, never to a
        # building-form word.
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("scheme_id", "name", name="benefit_groups_scheme_name_ux"),
        schema="core",
    )

    op.create_table(
        "lot_benefit_groups",
        # The LOT is the primary key, not (lot, group): membership is exclusive, and this
        # is the only place a double assignment can be caught — see the module docstring.
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("benefit_group_id", sa.UUID(),
                  sa.ForeignKey("core.benefit_groups.benefit_group_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("assigned_by", sa.UUID(), nullable=True),
        sa.Column("assigned_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="core",
    )
    op.create_index("lot_benefit_groups_group_idx", "lot_benefit_groups",
                    ["benefit_group_id"], schema="core")

    for table in ("benefit_groups", "lot_benefit_groups"):
        op.execute(f"ALTER TABLE core.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE core.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_core_{table} ON core.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    for table in ("lot_benefit_groups", "benefit_groups"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_core_{table} ON core.{table}")
    op.drop_index("lot_benefit_groups_group_idx", table_name="lot_benefit_groups", schema="core")
    op.drop_table("lot_benefit_groups", schema="core")
    op.drop_table("benefit_groups", schema="core")
