"""levy_run_type classification on finance.levy_runs (ordinary/special/adjustment/interest/recovery).

Revision ID: 0069_levy_run_type
Revises: 0068_evidence_doc_types_exp
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:demo_bank — Run-level levy classification (replaces an earlier,
# rejected item-level finance.levy_items.levy_component design — see plan02
# amendment 4). A special levy is always a SEPARATE finance.levy_runs row, never
# a second component competing for the same (levy_run_id, lot_id, fund_id) key —
# so finance.levy_items' existing UNIQUE constraint is untouched by this migration.
# Layer: migration
# Data flow: services/levy_generation_service.py / FinancialCoreService.create_levy() →
#            finance.levy_runs.levy_run_type (building-scoped).
# Related: backend/services/financial_core/service.py
#          backend/services/levy_generation_service.py
#          docs/migration/historical_ledger_reconciliation_plan02.md (amendment 4)
# Table: finance.levy_runs

from alembic import op

revision = "0069_levy_run_type"
down_revision = "0068_evidence_doc_types_exp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE finance.levy_runs
            ADD COLUMN IF NOT EXISTS levy_run_type TEXT NOT NULL DEFAULT 'ordinary'
        """
    )
    op.execute(
        """
        ALTER TABLE finance.levy_runs
            ADD CONSTRAINT levy_runs_type_chk
            CHECK (levy_run_type IN ('ordinary', 'special', 'adjustment', 'interest', 'recovery'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE finance.levy_runs DROP CONSTRAINT IF EXISTS levy_runs_type_chk")
    op.execute("ALTER TABLE finance.levy_runs DROP COLUMN IF EXISTS levy_run_type")
