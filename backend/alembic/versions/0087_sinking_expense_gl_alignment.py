"""Add sinking expense GL account and align posted expense lines.

Revision ID: 0087_sinking_expense_gl
Revises: 0086_outbox_per_dest_delivery
Create Date: 2026-08-11 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial_core — Sinking expense GL classification repair.
# Layer: migration
# Data flow: finance.expense_transactions.fund_id / finance.journal_entries.fund_id
#            -> finance.journal_lines.gl_account_id -> finance.gl_accounts.fund_id.
# Related: backend/services/financial_core/service.py
#          backend/services/financial_read_service.py

from alembic import op
from sqlalchemy import text as sa_text

revision = "0087_sinking_expense_gl"
down_revision = "0086_outbox_per_dest_delivery"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
_SINKING_EXPENSE_CODE = "5100"
_SINKING_EXPENSE_NAME = "Sinking Fund Expenses"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))
    scheme_rows = bind.execute(
        sa_text("SELECT tenant_id::text, scheme_id::text FROM core.schemes")
    ).fetchall()

    for tenant_id, scheme_id in scheme_rows:
        bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        bind.execute(
            sa_text(
                """
                INSERT INTO finance.gl_accounts
                    (gl_account_id, tenant_id, scheme_id, fund_id, account_code,
                     account_name, account_type, is_control_account, status, created_at)
                SELECT gen_random_uuid(), :tenant_id, :scheme_id, f.fund_id, :account_code,
                       :account_name, CAST('expense' AS finance.account_type), FALSE,
                       CAST('active' AS core.record_status), NOW()
                FROM finance.funds f
                WHERE f.scheme_id = CAST(:scheme_id AS UUID)
                  AND f.fund_type::text IN ('sinking', 'capital_works')
                  AND COALESCE(f.status, 'active') = 'active'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM finance.gl_accounts ga
                      WHERE ga.scheme_id = CAST(:scheme_id AS UUID)
                        AND ga.account_code = :account_code
                  )
                ORDER BY CASE WHEN f.fund_type::text = 'sinking' THEN 0 ELSE 1 END
                LIMIT 1
                """
            ),
            {
                "tenant_id": tenant_id,
                "scheme_id": scheme_id,
                "account_code": _SINKING_EXPENSE_CODE,
                "account_name": _SINKING_EXPENSE_NAME,
            },
        )

        # Historical repair: posted expense journals are immutable in normal app
        # code, but this is a deterministic chart-of-accounts classification fix.
        # It changes only the debit expense GL account when the expense/journal
        # header fund is already sinking/capital_works and the existing debit GL
        # account belongs to a different fund. Amounts, directions, journal ids,
        # and posted status are left untouched.
        bind.execute(sa_text("ALTER TABLE finance.journal_lines DISABLE TRIGGER trg_prevent_posted_line_update"))
        try:
            bind.execute(
                sa_text(
                    """
                    WITH target AS (
                        SELECT ga.gl_account_id
                        FROM finance.gl_accounts ga
                        JOIN finance.funds f ON f.fund_id = ga.fund_id
                        WHERE ga.scheme_id = CAST(:scheme_id AS UUID)
                          AND ga.account_code = :account_code
                          AND ga.account_type = 'expense'
                          AND f.fund_type::text IN ('sinking', 'capital_works')
                        LIMIT 1
                    ),
                    candidates AS (
                        SELECT jl.journal_line_id, target.gl_account_id AS target_gl_account_id
                        FROM finance.expense_transactions et
                        JOIN finance.journal_entries je
                          ON je.journal_entry_id = et.journal_entry_id
                         AND je.scheme_id = et.scheme_id
                        JOIN finance.funds ef ON ef.fund_id = COALESCE(et.fund_id, je.fund_id)
                        JOIN finance.journal_lines jl
                          ON jl.journal_entry_id = je.journal_entry_id
                         AND jl.direction = 'debit'
                        JOIN finance.gl_accounts current_ga
                          ON current_ga.gl_account_id = jl.gl_account_id
                         AND current_ga.account_type = 'expense'
                        CROSS JOIN target
                        WHERE et.scheme_id = CAST(:scheme_id AS UUID)
                          AND je.status = 'posted'
                          AND ef.fund_type::text IN ('sinking', 'capital_works')
                          AND current_ga.fund_id IS DISTINCT FROM ef.fund_id
                    )
                    UPDATE finance.journal_lines jl
                    SET gl_account_id = candidates.target_gl_account_id
                    FROM candidates
                    WHERE jl.journal_line_id = candidates.journal_line_id
                    """
                ),
                {"scheme_id": scheme_id, "account_code": _SINKING_EXPENSE_CODE},
            )
            bind.execute(
                sa_text(
                    """
                    UPDATE finance.expense_transactions et
                    SET gl_account_id = ga.gl_account_id
                    FROM finance.gl_accounts ga
                    JOIN finance.funds f ON f.fund_id = ga.fund_id
                    WHERE et.scheme_id = CAST(:scheme_id AS UUID)
                      AND ga.scheme_id = CAST(:scheme_id AS UUID)
                      AND ga.account_code = :account_code
                      AND ga.account_type = 'expense'
                      AND f.fund_type::text IN ('sinking', 'capital_works')
                      AND et.fund_id = f.fund_id
                      AND et.gl_account_id IS DISTINCT FROM ga.gl_account_id
                    """
                ),
                {"scheme_id": scheme_id, "account_code": _SINKING_EXPENSE_CODE},
            )
        finally:
            bind.execute(sa_text("ALTER TABLE finance.journal_lines ENABLE TRIGGER trg_prevent_posted_line_update"))

    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))
    scheme_rows = bind.execute(
        sa_text("SELECT tenant_id::text, scheme_id::text FROM core.schemes")
    ).fetchall()
    for tenant_id, scheme_id in scheme_rows:
        bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        bind.execute(
            sa_text(
                """
                DELETE FROM finance.gl_accounts
                WHERE scheme_id = CAST(:scheme_id AS UUID)
                  AND account_code = :account_code
                  AND NOT EXISTS (
                      SELECT 1
                      FROM finance.journal_lines jl
                      WHERE jl.gl_account_id = finance.gl_accounts.gl_account_id
                  )
                """
            ),
            {"scheme_id": scheme_id, "account_code": _SINKING_EXPENSE_CODE},
        )
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))
