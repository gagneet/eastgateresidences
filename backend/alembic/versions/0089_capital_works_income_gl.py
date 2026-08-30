"""Backfill sinking income GL for capital works funds.

Revision ID: 0089_capital_works_income_gl
Revises: 0088_fund_gl_accounts
Create Date: 2026-08-11 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial_core — Capital Works fund compatibility for the
# Sinking/Reserve levy income account used by FinancialCoreService.
# Layer: migration
# Data flow: services/financial_core/service.py create_levy/create_historical_levy
#            -> finance.gl_accounts account_code 4001.
# Related: backend/alembic/versions/0088_backfill_fund_expense_gl_accounts.py

from alembic import op
from sqlalchemy import text as sa_text

revision = "0089_capital_works_income_gl"
down_revision = "0088_fund_gl_accounts"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


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
                SELECT gen_random_uuid(), :tenant_id, :scheme_id, f.fund_id, '4001',
                       'Sinking Levy Income', CAST('income' AS finance.account_type), FALSE,
                       CAST('active' AS core.record_status), NOW()
                FROM finance.funds f
                WHERE f.scheme_id = CAST(:scheme_id AS UUID)
                  AND f.fund_type::text IN ('sinking', 'capital_works')
                  AND COALESCE(f.status, 'active') = 'active'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM finance.gl_accounts ga
                      WHERE ga.scheme_id = CAST(:scheme_id AS UUID)
                        AND ga.account_code = '4001'
                  )
                ORDER BY CASE WHEN f.fund_type::text = 'sinking' THEN 0 ELSE 1 END
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "scheme_id": scheme_id},
        )

    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))


def downgrade() -> None:
    # Non-destructive: account 4001 may be referenced by levy journal lines.
    pass
