"""0009 — Add tenant_id to child tables and apply RLS.

Revision: 0009
Previous: 0008

Three tables created in earlier migrations lack a direct tenant_id column
because they are child tables that logically inherit tenant isolation from
their parent:

  core.approval_steps          → parent: core.approval_requests
  finance.payment_plan_installments → parent: finance.payment_plans
  finance.receipt_allocations  → parent: finance.receipts

This migration:
  1. ADD COLUMN tenant_id (nullable FK) to each child table
  2. Back-fill tenant_id from the parent via a single UPDATE … FROM join
  3. ALTER COLUMN … SET NOT NULL (fails fast if any orphaned rows exist)
  4. ENABLE RLS + CREATE POLICY using the standard core.current_tenant_id()
     helper created in migration 0007

Safe to run against an empty DB (no data → back-fill is a no-op, NOT NULL
constraint is satisfied trivially).
"""
from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# (child_schema, child_table, parent_schema, parent_table, join_col)
_CHILD_TABLES = [
    (
        "core",
        "approval_steps",
        "core",
        "approval_requests",
        "approval_request_id",
    ),
    (
        "finance",
        "payment_plan_installments",
        "finance",
        "payment_plans",
        "payment_plan_id",
    ),
    (
        "finance",
        "receipt_allocations",
        "finance",
        "receipts",
        "receipt_id",
    ),
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0009_tenant_id_child_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for child_schema, child_table, parent_schema, parent_table, join_col in _CHILD_TABLES:
        full_child = f"{child_schema}.{child_table}"
        full_parent = f"{parent_schema}.{parent_table}"

        # 1. Add nullable column first so the ALTER does not fail before backfill
        op.execute(
            f"ALTER TABLE {full_child} "
            f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
            f"REFERENCES core.tenants(tenant_id)"
        )

        # 2. Back-fill from parent — single-pass join
        op.execute(
            f"UPDATE {full_child} AS c "
            f"SET tenant_id = p.tenant_id "
            f"FROM {full_parent} AS p "
            f"WHERE c.{join_col} = p.{join_col} "
            f"AND c.tenant_id IS NULL"
        )

        # 3. Make NOT NULL — will raise if any orphaned child rows remain
        op.execute(
            f"ALTER TABLE {full_child} ALTER COLUMN tenant_id SET NOT NULL"
        )

        # 4. Enable RLS with the standard tenant isolation policy
        policy = f"tenant_isolation_{child_schema}_{child_table}"
        op.execute(f"ALTER TABLE {full_child} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {full_child} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {policy} ON {full_child} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0009_tenant_id_child_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for child_schema, child_table, _ps, _pt, _jc in reversed(_CHILD_TABLES):
        full_child = f"{child_schema}.{child_table}"
        policy = f"tenant_isolation_{child_schema}_{child_table}"

        op.execute(f"DROP POLICY IF EXISTS {policy} ON {full_child}")
        op.execute(f"ALTER TABLE {full_child} DISABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {full_child} DROP COLUMN IF EXISTS tenant_id")
