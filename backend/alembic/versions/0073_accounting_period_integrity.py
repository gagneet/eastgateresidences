"""finance.accounting_periods: status/overlap/label integrity constraints,
plus finance.reconstruction_execution_batches(_items) for audited historical
reconstruction (see tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md).

Revision ID: 0073_accounting_period_integrity
Revises: 0072_recovered_fees_gl_account
Create Date: 2026-07-24 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial_integration_v2 — Accounting-period status/overlap integrity
#   + reconstruction batch control-plane tables.
# Layer: migration
# Data flow: finance.accounting_periods (constraints only) + new
#            finance.reconstruction_execution_batches/_items tables.
# Related: backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#          backend/services/financial_core/adapters/db_postgres/models.py
#          backend/scripts/accounting_period_integrity_preflight.py
#          backend/scripts/east_gate_2025_expense_reconstruction.py
#          backend/scripts/east_gate_lot_fee_charge_backfill.py
#          backend/alembic/versions/0074_harden_reconstruction_execution.py
#          tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md
# Table: finance.accounting_periods, finance.reconstruction_execution_batches,
#        finance.reconstruction_execution_batch_items

from alembic import op
from sqlalchemy import text as sa_text

revision = "0073_accounting_period_integrity"
down_revision = "0072_recovered_fees_gl_account"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def _run_preflight_guard(bind) -> None:
    """Hard backstop: re-check status validity, overlaps, duplicate labels,
    and boundary sanity across EVERY scheme before adding constraints that
    depend on those invariants already holding. finance.accounting_periods
    has NO RLS bypass clause (confirmed live) — must iterate each tenant's
    real tenant_id, the bypass id returns 0 rows silently. This checks the
    SAME invariants scripts/accounting_period_integrity_preflight.py checks
    (run that script by hand first for a human-readable report); this is the
    machine-enforced version that blocks the migration on failure.

    Does NOT check journal_entries.effective_on-vs-period consistency —
    that's a separate, expected-to-exist, pre-existing-data concern (fixing
    historical journal->period misassignment is an evidence-backed
    correction task, not something this migration's constraints touch or
    require).
    """
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))
    tenant_ids = [
        r[0] for r in bind.execute(sa_text("SELECT DISTINCT tenant_id::text FROM core.schemes")).fetchall()
    ]

    violations: list[str] = []
    for tenant_id in tenant_ids:
        bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))

        bad_status = bind.execute(
            sa_text(
                "SELECT period_id::text FROM finance.accounting_periods "
                "WHERE status NOT IN ('open','reconciling','closed','audited','locked')"
            )
        ).fetchall()
        if bad_status:
            violations.append(f"tenant={tenant_id}: {len(bad_status)} invalid status value(s)")

        overlaps = bind.execute(
            sa_text(
                """
                SELECT a.period_id::text, b.period_id::text
                FROM finance.accounting_periods a
                JOIN finance.accounting_periods b
                  ON a.scheme_id = b.scheme_id AND a.period_id < b.period_id
                 AND daterange(a.starts_on, a.ends_on, '[]') && daterange(b.starts_on, b.ends_on, '[]')
                """
            )
        ).fetchall()
        if overlaps:
            violations.append(f"tenant={tenant_id}: {len(overlaps)} overlapping date range pair(s)")

        dupes = bind.execute(
            sa_text(
                "SELECT scheme_id::text, period_label, COUNT(*) FROM finance.accounting_periods "
                "GROUP BY scheme_id, period_label HAVING COUNT(*) > 1"
            )
        ).fetchall()
        if dupes:
            violations.append(f"tenant={tenant_id}: {len(dupes)} duplicate (scheme_id, period_label) pair(s)")

        bad_bounds = bind.execute(
            sa_text(
                "SELECT period_id::text FROM finance.accounting_periods "
                "WHERE starts_on IS NULL OR ends_on IS NULL OR ends_on < starts_on"
            )
        ).fetchall()
        if bad_bounds:
            violations.append(f"tenant={tenant_id}: {len(bad_bounds)} invalid period boundary row(s)")

    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))

    if violations:
        raise RuntimeError(
            "accounting_period_integrity pre-flight guard failed — refusing to add "
            "constraints against data that already violates them:\n  " + "\n  ".join(violations)
        )


def upgrade() -> None:
    bind = op.get_bind()
    _run_preflight_guard(bind)

    op.execute(
        """
        ALTER TABLE finance.accounting_periods
        ADD CONSTRAINT accounting_periods_status_chk
        CHECK (status IN ('open','reconciling','closed','audited','locked'))
        """
    )
    op.execute(
        """
        ALTER TABLE finance.accounting_periods
        ADD CONSTRAINT accounting_periods_label_uniq
        UNIQUE (tenant_id, scheme_id, period_label)
        """
    )
    # btree_gist already enabled (migration 0001) — same pattern as
    # core.ownership_periods (0002). tenant_id included (not just scheme_id)
    # — no proof scheme_id is globally unique/immutable independent of
    # tenant, so scope the exclusion the same way RLS scopes everything else
    # in this schema. '[]' (both bounds inclusive) matches the existing
    # CHECK (ends_on >= starts_on) semantics — unlike ownership_periods'
    # '[)' half-open convention (which exists there specifically for
    # open-ended valid_to IS NULL rows; accounting periods always have both
    # bounds set).
    op.execute(
        """
        ALTER TABLE finance.accounting_periods
        ADD CONSTRAINT accounting_periods_scheme_daterange_excl
        EXCLUDE USING gist (
            tenant_id WITH =,
            scheme_id WITH =,
            daterange(starts_on, ends_on, '[]') WITH &&
        )
        """
    )
    # locked_by already exists on the live table (added by an earlier
    # migration, never ORM-mapped) — confirmed via a failed ADD COLUMN
    # DuplicateColumnError when this migration was first drafted. Only the
    # models.py mapping was missing (fixed alongside this migration); no DDL
    # needed here.

    # Minimal viable batch/item tables — deliberately created here WITHOUT
    # the extra FKs/RLS/immutability triggers/checks that migration 0074
    # adds. Splitting it this way (rather than amending this file in place)
    # preserves an accurate migration history: this revision's applied DDL
    # never changes after the fact once a database has run it.
    op.execute(
        """
        CREATE TABLE finance.reconstruction_execution_batches (
            batch_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            scheme_id UUID NOT NULL,
            batch_type TEXT NOT NULL,
            target_period_label TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'manifest_pending'
                CHECK (status IN ('manifest_pending','approved','applying','applied','partially_applied','failed')),
            manifest_hash TEXT NOT NULL,
            expected_row_count INTEGER NOT NULL,
            expected_amount_cents BIGINT NOT NULL,
            created_by UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            approved_by UUID,
            approved_at TIMESTAMPTZ,
            approval_reference TEXT,
            applied_by UUID,
            applied_at TIMESTAMPTZ,
            actual_row_count INTEGER,
            actual_amount_cents BIGINT,
            failure_count INTEGER
        )
        """
    )
    op.execute(
        """
        CREATE TABLE finance.reconstruction_execution_batch_items (
            batch_item_id UUID PRIMARY KEY,
            batch_id UUID NOT NULL REFERENCES finance.reconstruction_execution_batches(batch_id),
            sequence_number INTEGER NOT NULL,
            bank_transaction_id UUID NOT NULL,
            transaction_date DATE NOT NULL,
            description TEXT NOT NULL,
            amount_cents BIGINT NOT NULL,
            category_name TEXT,
            lot_number TEXT,
            item_type TEXT NOT NULL,
            source_snapshot_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','applying','applied','failed','skipped')),
            expense_journal_entry_id UUID,
            recharge_journal_entry_id UUID,
            error_message TEXT,
            applied_at TIMESTAMPTZ,
            UNIQUE (batch_id, bank_transaction_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX reconstruction_execution_batch_items_batch_idx "
        "ON finance.reconstruction_execution_batch_items (batch_id, status)"
    )
    op.execute(
        "CREATE INDEX reconstruction_execution_batches_scheme_idx "
        "ON finance.reconstruction_execution_batches (tenant_id, scheme_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS finance.reconstruction_execution_batch_items")
    op.execute("DROP TABLE IF EXISTS finance.reconstruction_execution_batches")
    op.execute(
        "ALTER TABLE finance.accounting_periods "
        "DROP CONSTRAINT IF EXISTS accounting_periods_scheme_daterange_excl"
    )
    op.execute(
        "ALTER TABLE finance.accounting_periods DROP CONSTRAINT IF EXISTS accounting_periods_label_uniq"
    )
    op.execute(
        "ALTER TABLE finance.accounting_periods DROP CONSTRAINT IF EXISTS accounting_periods_status_chk"
    )
