"""Backfill fund-specific GL accounts used by finance writers.

Revision ID: 0088_fund_gl_accounts
Revises: 0087_sinking_expense_gl
Create Date: 2026-08-11 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial_core — Existing-scheme chart-of-accounts backfill for
# ledger writer dependencies.
# Layer: migration
# Data flow: services/financial_core/genesis.py DEFAULT_FUND_GL_ACCOUNTS ->
#            finance.gl_accounts.
# Related: backend/services/financial_core/service.py
#          backend/services/financial_core/genesis.py

from alembic import op
from sqlalchemy import text as sa_text

revision = "0088_fund_gl_accounts"
down_revision = "0087_sinking_expense_gl"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"

_ACCOUNTS = (
    ("admin", "5000", "Administration Expenses", "expense"),
    ("admin", "5001", "Insurance", "expense"),
    ("admin", "5002", "Repairs and Maintenance", "expense"),
    ("admin", "5003", "Management Fees", "expense"),
    ("admin", "5004", "Legal and Compliance", "expense"),
    ("sinking", "4001", "Sinking Levy Income", "income"),
    ("sinking", "5100", "Sinking Fund Expenses", "expense"),
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))
    scheme_rows = bind.execute(
        sa_text("SELECT tenant_id::text, scheme_id::text FROM core.schemes")
    ).fetchall()

    for tenant_id, scheme_id in scheme_rows:
        bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        for fund_type, account_code, account_name, account_type in _ACCOUNTS:
            bind.execute(
                sa_text(
                    """
                    INSERT INTO finance.gl_accounts
                        (gl_account_id, tenant_id, scheme_id, fund_id, account_code,
                         account_name, account_type, is_control_account, status, created_at)
                    SELECT gen_random_uuid(), :tenant_id, :scheme_id, f.fund_id, :account_code,
                           :account_name, CAST(:account_type AS finance.account_type), FALSE,
                           CAST('active' AS core.record_status), NOW()
                    FROM finance.funds f
                    WHERE f.scheme_id = CAST(:scheme_id AS UUID)
                      AND f.fund_type::text = :fund_type
                      AND COALESCE(f.status, 'active') = 'active'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM finance.gl_accounts ga
                          WHERE ga.scheme_id = CAST(:scheme_id AS UUID)
                            AND ga.account_code = :account_code
                      )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "fund_type": fund_type,
                    "account_code": account_code,
                    "account_name": account_name,
                    "account_type": account_type,
                },
            )

    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))


def downgrade() -> None:
    # Do not delete chart-of-accounts rows on downgrade. By the time this
    # migration has run, journal_lines may legitimately reference these
    # accounts. Removing them would break ledger referential integrity.
    pass
