"""0077 — Arrears grace-deadline metadata.

Revision ID: 0077_arrears_grace_meta
Previous: 0076_levy_charge_uniqueness
Create Date: 2026-08-03

# @featuretrace:arrears-grace-deadline-metadata
# Layer: migration
# Data flow: finance.levy_items gains item-level grace metadata; finance.levy_runs gains a
#            run-level earliest grace date; core.schemes gains a default grace-period setting;
#            finance.arrears_status_snapshot can be refreshed by application jobs for efficient
#            grace-aware read paths.
# Note: revision id must stay <= 32 characters because the deployed
# core.alembic_version.version_num column is varchar(32).
# Related: backend/domain/finance/formulas/arrears.py
#          backend/routers/finance.py (get_levy_kpi, get_finance_summary, get_arrears_board)
#          backend/utils/finance_helpers.py (compute_quarter_grace_deadlines)
#          docs/architecture/arrears_calculation_spec_2026_08_03.md
# Table: finance.levy_items, finance.levy_runs, core.schemes, finance.arrears_status_snapshot

Context
-------
The reported arrears discrepancy came from treating positive balances as arrears without proving
that the relevant grace deadline had passed. Reported symptoms were:

1. Overview tab showing 31 units in arrears when the target count was 14
2. Levy Status filter returning 0 rows
3. Arrears Recovery Board showing $0.00 recoverable when the target was $11,359.73

CRITICAL FIX (2026-08-03): Arrears must only count unpaid amounts from quarters where
(due_date + grace_period_days) < today.

Schema Changes
--------------
1. finance.levy_items gains:
   - grace_deadline_date (DATE NOT NULL) — due_date + grace_period_days from rule
   - is_past_grace (BOOLEAN NOT NULL DEFAULT FALSE) — computed flag for indexed filtering
   - days_overdue (INT DEFAULT 0) — (today - grace_deadline_date).days, updated by backfill/trigger/refresh

2. finance.levy_runs gains:
   - grace_deadline_date (DATE NOT NULL) — min grace deadline of all items in run
   - earliest_past_grace_item_id (UUID REFERENCES levy_items) — optimization hint

3. core.schemes gains:
   - grace_period_days (INT NOT NULL DEFAULT 14) — system setting, replaces per-rule setting
   - grace_deadline_snapshot_refreshed_at (TIMESTAMPTZ) — tracks when snapshot was refreshed

4. NEW TABLE: finance.arrears_status_snapshot
   - One row per lot-scheme-fund-quarter combination
   - Stores: grace_deadline, is_past_grace, days_overdue, arrears_amount_cents, status
   - Refreshed by application code; scheduling is outside this migration
   - Enables O(1) filtering on reports without runtime calculation
   - Scoped by RLS to scheme/tenant boundary

5. NEW INDEX: levy_items_grace_deadline
   - ON (scheme_id, grace_deadline_date, is_past_grace)
   - Enables fast "units past grace deadline" queries

Why This Approach
-----------------
- Precalculated grace_deadline_date avoids runtime date arithmetic in WHERE clauses
- is_past_grace flag enables index-driven filtering (WHERE is_past_grace = true)
- arrears_status_snapshot separates snapshot state from transactional levy_items
- days_overdue is advisory/display only (computed from grace_deadline_date vs now())
- Backward compatible: existing levy_items rows get grace_deadline = due_date + 14

Migration Path
--------------
1. Add columns to finance.levy_items (null-able initially)
2. Add columns to finance.levy_runs (null-able initially)
3. Create arrears_status_snapshot table
4. Backfill: for each levy_items row, set grace_deadline = due_date + grace_days from rule
5. Backfill: is_past_grace = (grace_deadline < current_date)
6. Make columns NOT NULL
7. Create indexes
8. Create trigger to auto-update derived grace status when levy_items are inserted/updated
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0077_arrears_grace_meta"
down_revision = "0076_levy_charge_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add grace deadline tracking to finance.levy_items
    op.add_column(
        "levy_items",
        sa.Column(
            "grace_deadline_date",
            sa.Date(),
            nullable=True,
            comment="due_date + grace_period_days from levy_rules; date after which item is in arrears"
        ),
        schema="finance"
    )

    op.add_column(
        "levy_items",
        sa.Column(
            "is_past_grace",
            sa.Boolean(),
            nullable=True,
            server_default="false",
            comment="true if current_date > grace_deadline_date; indexed for fast filtering"
        ),
        schema="finance"
    )

    op.add_column(
        "levy_items",
        sa.Column(
            "days_overdue",
            sa.Integer(),
            nullable=True,
            server_default="0",
            comment="(current_date - grace_deadline_date).days if past grace, else 0; display only"
        ),
        schema="finance"
    )

    # 2. Add grace deadline summary to finance.levy_runs
    op.add_column(
        "levy_runs",
        sa.Column(
            "grace_deadline_date",
            sa.Date(),
            nullable=True,
            comment="Minimum grace_deadline_date of all items in this run"
        ),
        schema="finance"
    )

    op.add_column(
        "levy_runs",
        sa.Column(
            "earliest_past_grace_item_id",
            sa.UUID(),
            nullable=True,
            comment="First item that crossed grace deadline (optimization hint)"
        ),
        schema="finance"
    )
    op.create_foreign_key(
        "fk_levy_runs_earliest_grace_item",
        "levy_runs",
        "levy_items",
        ["earliest_past_grace_item_id"],
        ["levy_item_id"],
        source_schema="finance",
        referent_schema="finance",
        ondelete="SET NULL",
    )

    # 3. Add grace period configuration to core.schemes
    op.add_column(
        "schemes",
        sa.Column(
            "grace_period_days",
            sa.Integer(),
            nullable=True,
            server_default="14",
            comment="Default grace period for all levies in this scheme (can be overridden per levy_rules)"
        ),
        schema="core"
    )

    op.add_column(
        "schemes",
        sa.Column(
            "grace_deadline_snapshot_refreshed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of last arrears_status_snapshot refresh (background job)"
        ),
        schema="core"
    )

    # 4. Create arrears_status_snapshot table
    op.execute("""
        CREATE TABLE finance.arrears_status_snapshot (
            snapshot_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                    UUID        NOT NULL REFERENCES core.tenants (tenant_id),
            scheme_id                    UUID        NOT NULL REFERENCES core.schemes (scheme_id),
            lot_id                       UUID        NOT NULL REFERENCES core.lots (lot_id),
            owner_party_id               UUID        NOT NULL REFERENCES core.parties (party_id),
            fund_id                      UUID        NOT NULL REFERENCES finance.funds (fund_id),
            financial_year               TEXT        NOT NULL,
            quarter_no                   INT,
            
            due_date                     DATE        NOT NULL,
            grace_deadline_date          DATE        NOT NULL,
            is_past_grace                BOOLEAN     NOT NULL DEFAULT false,
            days_overdue                 INT         NOT NULL DEFAULT 0,
            
            principal_cents              BIGINT      NOT NULL DEFAULT 0,
            interest_cents               BIGINT      NOT NULL DEFAULT 0,
            recovery_costs_cents         BIGINT      NOT NULL DEFAULT 0,
            paid_cents                   BIGINT      NOT NULL DEFAULT 0,
            
            arrears_amount_cents         BIGINT      NOT NULL DEFAULT 0,
            status                       TEXT        NOT NULL,
            
            last_payment_date            DATE,
            last_payment_amount_cents    BIGINT,
            
            created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
            refreshed_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            
            CHECK (quarter_no IS NULL OR quarter_no BETWEEN 1 AND 4)
        )
    """)
    op.execute("""
        COMMENT ON TABLE finance.arrears_status_snapshot IS
            'Precomputed arrears status snapshot for grace-deadline-aware reads. '
            'Separates state (grace deadline, arrears amount, status) from transactional levy_items. '
            'Refresh scheduling is intentionally implemented outside this migration. '
            'RLS scoped by tenant_id.'
    """)

    # 5. Create indexes for grace-deadline queries
    op.execute("""
        CREATE INDEX levy_items_grace_deadline_idx 
        ON finance.levy_items (scheme_id, grace_deadline_date, is_past_grace)
        WHERE grace_deadline_date IS NOT NULL
    """)
    op.execute("""
        COMMENT ON INDEX finance.levy_items_grace_deadline_idx IS
            'Enables fast filtering: WHERE scheme_id = $1 AND is_past_grace = true'
    """)

    op.execute("""
        CREATE INDEX levy_runs_grace_deadline_idx
        ON finance.levy_runs (scheme_id, grace_deadline_date)
        WHERE grace_deadline_date IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX arrears_status_snapshot_past_grace_idx
        ON finance.arrears_status_snapshot (scheme_id, is_past_grace, financial_year)
        WHERE is_past_grace = true
    """)
    op.execute("""
        COMMENT ON INDEX finance.arrears_status_snapshot_past_grace_idx IS
            'Enables fast "units in arrears" queries for reports'
    """)

    op.execute("""
        CREATE UNIQUE INDEX arrears_status_snapshot_period_ux
        ON finance.arrears_status_snapshot (
            scheme_id,
            lot_id,
            fund_id,
            financial_year,
            COALESCE(quarter_no, 0)
        )
    """)
    op.execute("""
        COMMENT ON INDEX finance.arrears_status_snapshot_period_ux IS
            'One snapshot per lot/fund/year/quarter; treats NULL quarter_no as annual period 0'
    """)

    # finance.levy_items and finance.levy_runs have had RLS FORCE-enabled since
    # migration 0008 with a bare `tenant_id = core.current_tenant_id()` policy
    # and no bypass clause (unlike core.schemes, which gained one in 0026).
    # alembic/env.py never sets app.tenant_id, so core.current_tenant_id() is
    # NULL for the duration of every migration — the backfill UPDATEs below
    # would silently match zero rows under RLS (not an error; see
    # rules/post-compact-critical.md footgun #8) and leave grace_deadline_date
    # NULL for every pre-existing row, which then fails the NOT NULL ALTER in
    # step 11. Disable RLS on these two tables for the backfill and re-enable
    # it (with FORCE, matching 0008) before the NOT NULL constraints are applied.
    op.execute("ALTER TABLE finance.levy_items DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.levy_runs DISABLE ROW LEVEL SECURITY")

    # 6. Backfill grace_deadline_date for existing levy_items.
    # Prefer levy_run.due_date + matching levy_rule.grace_days. If old or malformed
    # rows cannot be joined to a run/rule, use a conservative future deadline so
    # the migration remains deployable without inventing past-due arrears.
    op.execute("""
        UPDATE finance.levy_items li
        SET grace_deadline_date = COALESCE((
            SELECT (lr.due_date + (COALESCE(lrule.grace_days, 14) * INTERVAL '1 day'))::DATE
            FROM finance.levy_runs lr
            LEFT JOIN finance.levy_rules lrule 
                ON lrule.scheme_id = lr.scheme_id
                AND lrule.fund_id = COALESCE(lr.fund_id, li.fund_id)
                AND lrule.effective_from <= lr.issue_date
                AND (lrule.effective_to IS NULL OR lrule.effective_to > lr.issue_date)
            WHERE lr.levy_run_id = li.levy_run_id
            LIMIT 1
        ), (CURRENT_DATE + INTERVAL '14 days')::DATE)
        WHERE li.grace_deadline_date IS NULL;
    """)

    # 7. Backfill is_past_grace flag
    op.execute("""
        UPDATE finance.levy_items
        SET is_past_grace = (CURRENT_DATE > grace_deadline_date)
        WHERE grace_deadline_date IS NOT NULL;
    """)

    # 8. Backfill days_overdue
    op.execute("""
        UPDATE finance.levy_items
        SET days_overdue = CASE
            WHEN CURRENT_DATE > grace_deadline_date THEN (CURRENT_DATE - grace_deadline_date)
            ELSE 0
        END
        WHERE grace_deadline_date IS NOT NULL;
    """)

    # 9. Backfill grace_deadline_date and earliest_past_grace_item_id on levy_runs
    op.execute("""
        UPDATE finance.levy_runs lr
        SET grace_deadline_date = COALESCE((
            SELECT MIN(grace_deadline_date)
            FROM finance.levy_items
            WHERE levy_run_id = lr.levy_run_id
        ), (lr.due_date + INTERVAL '14 days')::DATE),
        earliest_past_grace_item_id = (
            SELECT levy_item_id
            FROM finance.levy_items
            WHERE levy_run_id = lr.levy_run_id
            AND is_past_grace = true
            ORDER BY grace_deadline_date ASC
            LIMIT 1
        );
    """)

    # 10. Set default grace_period_days on schemes where not set
    op.execute("""
        UPDATE core.schemes
        SET grace_period_days = 14
        WHERE grace_period_days IS NULL;
    """)

    # Re-enable RLS on levy_items/levy_runs now that the backfill is done.
    # The policy created in migration 0008 was never dropped, only bypassed
    # above, so no CREATE POLICY is needed here.
    op.execute("ALTER TABLE finance.levy_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.levy_items FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.levy_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.levy_runs FORCE ROW LEVEL SECURITY")

    # 11. Make columns NOT NULL
    op.alter_column(
        "levy_items",
        "grace_deadline_date",
        existing_type=sa.Date(),
        nullable=False,
        schema="finance"
    )

    op.alter_column(
        "levy_items",
        "is_past_grace",
        existing_type=sa.Boolean(),
        nullable=False,
        schema="finance"
    )

    op.alter_column(
        "levy_runs",
        "grace_deadline_date",
        existing_type=sa.Date(),
        nullable=False,
        schema="finance"
    )

    op.alter_column(
        "schemes",
        "grace_period_days",
        existing_type=sa.Integer(),
        nullable=False,
        schema="core"
    )

    # 12. Create trigger to auto-update is_past_grace when inserted/updated
    op.execute("""
        CREATE OR REPLACE FUNCTION finance.levy_items_update_grace_deadline()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.is_past_grace := (CURRENT_DATE > NEW.grace_deadline_date);
            NEW.days_overdue := CASE
                WHEN CURRENT_DATE > NEW.grace_deadline_date THEN (CURRENT_DATE - NEW.grace_deadline_date)
                ELSE 0
            END;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER levy_items_grace_deadline_trigger
        BEFORE INSERT OR UPDATE ON finance.levy_items
        FOR EACH ROW
        EXECUTE FUNCTION finance.levy_items_update_grace_deadline();
    """)

    # 13. Apply RLS to arrears_status_snapshot
    op.execute("""
        ALTER TABLE finance.arrears_status_snapshot ENABLE ROW LEVEL SECURITY;
    """)

    op.execute("""
        ALTER TABLE finance.arrears_status_snapshot FORCE ROW LEVEL SECURITY;
    """)

    op.execute("""
        CREATE POLICY arrears_snapshot_tenant_isolation
        ON finance.arrears_status_snapshot
        FOR ALL
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        WITH CHECK (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '00000000-0000-0000-0000-000000000000'
        );
    """)


def downgrade() -> None:
    # Drop trigger
    op.execute("DROP TRIGGER IF EXISTS levy_items_grace_deadline_trigger ON finance.levy_items")
    op.execute("DROP FUNCTION IF EXISTS finance.levy_items_update_grace_deadline()")

    # Drop RLS
    op.execute("DROP POLICY IF EXISTS arrears_snapshot_tenant_isolation ON finance.arrears_status_snapshot")

    # Drop indexes
    op.execute("DROP INDEX IF EXISTS finance.levy_items_grace_deadline_idx")
    op.execute("DROP INDEX IF EXISTS finance.levy_runs_grace_deadline_idx")
    op.execute("DROP INDEX IF EXISTS finance.arrears_status_snapshot_past_grace_idx")
    op.execute("DROP INDEX IF EXISTS finance.arrears_status_snapshot_period_ux")

    # Drop table
    op.execute("DROP TABLE IF EXISTS finance.arrears_status_snapshot")

    # Drop columns from schemes
    op.drop_column("schemes", "grace_deadline_snapshot_refreshed_at", schema="core")
    op.drop_column("schemes", "grace_period_days", schema="core")

    # Drop columns from levy_runs
    op.drop_constraint("fk_levy_runs_earliest_grace_item", "levy_runs", schema="finance", type_="foreignkey")
    op.drop_column("levy_runs", "earliest_past_grace_item_id", schema="finance")
    op.drop_column("levy_runs", "grace_deadline_date", schema="finance")

    # Drop columns from levy_items
    op.drop_column("levy_items", "days_overdue", schema="finance")
    op.drop_column("levy_items", "is_past_grace", schema="finance")
    op.drop_column("levy_items", "grace_deadline_date", schema="finance")
