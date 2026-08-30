"""0106 — finance.budget_categories: the per-year budget line, actuals derived not stored.

Revision ID: 0106_budget_categories
Revises: 0105_documents_is_public
Create Date: 2026-08-30

# @featuretrace:finance-postgres-read-cutover — the PostgreSQL home for levy_categories.
# Layer: migration
# Data flow: MongoDB levy_categories → finance.budget_categories → financial_read_service
#            → GET /levy-categories, /levy-categories/budget-summary (building-scoped).
# Related: backend/services/financial_read_service.py
#          backend/scripts/data_migration/backfill_budget_categories.py
#          docs/architecture/mongo_collection_disposition.yaml
# Table: finance.budget_categories

Gives `levy_categories` (322 documents, no PostgreSQL target at all) a home. It is the
single highest-leverage gap in the finance domain: FIVE of the fourteen `needs_data`
routes are blocked on it alone.

WHY THERE IS NO actual_cents COLUMN
-----------------------------------
The MongoDB document carries `actual_amount` on every row. This table deliberately does
not, because the codebase already made that decision and wrote it down —
`services/financial_service.py`:

    "actual_amount is NEVER stored on financial_categories; it is always derived."

A stored actual is a second copy of a number that `finance.expense_transactions` already
holds, and the two drift the moment an expense is posted, reversed or re-categorised.
That is not hypothetical here: East Gate carried two disconnected 2021-2025 expense
totals ($415,031.21 staged vs $1,502,451.24 posted) that diverged 3.6x precisely because
two pipelines each maintained their own copy without either checking the other.

The actual is derivable — `expense_transactions` carries `scheme_id`, `financial_year`,
`fund_id` and `category_name`, which is exactly this table's grain. So the budget lives
here and the actual is computed at read time, once.

WHAT IS STORED, AND WHY EACH NULLABLE COLUMN IS NULLABLE
--------------------------------------------------------
* `budgeted_cents` — nullable because only 213 of 322 live documents carry a
  budgeted_amount. A category can exist as an actual-only line (an expense that no
  budget anticipated), and forcing 0 there would state a budget of zero that nobody set.
  Missing and zero are different, and this codebase has been burned by conflating them.
* `canonical_key` — nullable for the same reason: 213 of 322 have one. It is the
  post-consolidation identity; pre-consolidation rows legitimately have none.
* `merged_into` — a self-reference recording that this line was folded into another
  during the 2021-2026 rebuild. Kept rather than dropped so the merge history survives
  the migration; 85 rows have it.

Amounts are integer CENTS. `levy_categories.budgeted_amount` is a dollar FLOAT in
MongoDB (a documented, still-current violation of the cents-only rule), so the backfill
converts at the boundary, exactly once, and nothing downstream re-derives it.

Additive: a new empty table. No existing row is touched and no read changes until
`financial_read_service` is pointed at it.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0106_budget_categories"
down_revision = "0105_documents_is_public"
branch_labels = None
depends_on = None


def _policy_name(schema: str, table: str) -> str:
    return f"tenant_isolation_{schema}_{table}"


def upgrade() -> None:
    op.create_table(
        "budget_categories",
        sa.Column("budget_category_id", sa.UUID(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("scheme_id", sa.UUID(), nullable=False),
        sa.Column("fund_id", sa.UUID(), sa.ForeignKey("finance.funds.fund_id"), nullable=False),
        sa.Column("financial_year", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=True),
        sa.Column("canonical_name", sa.Text(), nullable=True),
        sa.Column("budgeted_cents", sa.BigInteger(), nullable=True,
                  comment="NULL means no budget was set — distinct from a budget of zero."),
        sa.Column("budget_source", sa.Text(), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'budgeted'")),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("archived_reason", sa.Text(), nullable=True),
        sa.Column("merged_into", sa.UUID(), nullable=True,
                  comment="This line was folded into another during a consolidation."),
        # The MongoDB `id`, kept so the backfill is idempotent on re-run and so a row
        # here can always be traced to the document it came from. Without it a re-run
        # duplicates every category, and there is no natural key that survives the
        # archived duplicates.
        sa.Column("legacy_mongo_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "legacy_mongo_id",
                            name="budget_categories_legacy_id_ux"),
        schema="finance",
    )

    op.create_index(
        "budget_categories_scheme_year_idx", "budget_categories",
        ["scheme_id", "financial_year"], schema="finance",
    )
    # The read path filters archived rows out, and 109 of 322 are archived, so the
    # partial index is what the common query actually uses.
    op.create_index(
        "budget_categories_live_idx", "budget_categories",
        ["scheme_id", "financial_year", "fund_id"], schema="finance",
        postgresql_where=sa.text("is_archived = FALSE"),
    )

    # RLS — same pattern as every other tenant-scoped finance table (0008, 0067).
    op.execute("ALTER TABLE finance.budget_categories ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.budget_categories FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_policy_name('finance', 'budget_categories')} "
        f"ON finance.budget_categories "
        f"USING (tenant_id = core.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute(
        f"DROP POLICY IF EXISTS {_policy_name('finance', 'budget_categories')} "
        f"ON finance.budget_categories"
    )
    op.drop_index("budget_categories_live_idx", table_name="budget_categories", schema="finance")
    op.drop_index("budget_categories_scheme_year_idx", table_name="budget_categories", schema="finance")
    op.drop_table("budget_categories", schema="finance")
