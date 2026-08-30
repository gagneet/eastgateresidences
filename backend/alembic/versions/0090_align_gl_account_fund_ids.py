"""Align fund ids on existing writer-required GL accounts.

Revision ID: 0090_align_gl_fund_ids
Revises: 0089_capital_works_income_gl
Create Date: 2026-08-11 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial_core — Existing GL account fund-id alignment for
# fund-aware reporting and writer correctness.
# Layer: migration
# Data flow: finance.funds -> finance.gl_accounts.fund_id.
# Related: backend/alembic/versions/0088_backfill_fund_expense_gl_accounts.py
#          backend/alembic/versions/0089_capital_works_income_gl.py

from alembic import op
from sqlalchemy import text as sa_text

revision = "0090_align_gl_fund_ids"
down_revision = "0089_capital_works_income_gl"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
_ADMIN_CODES = ("4000", "4003", "5000", "5001", "5002", "5003", "5004")
_RESERVE_CODES = ("4001", "5100")


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
                UPDATE finance.gl_accounts ga
                SET fund_id = f.fund_id
                FROM finance.funds f
                WHERE ga.scheme_id = CAST(:scheme_id AS UUID)
                  AND f.scheme_id = ga.scheme_id
                  AND f.fund_type::text = 'admin'
                  AND ga.account_code = ANY(CAST(:admin_codes AS text[]))
                  AND ga.fund_id IS DISTINCT FROM f.fund_id
                """
            ),
            {"scheme_id": scheme_id, "admin_codes": list(_ADMIN_CODES)},
        )
        bind.execute(
            sa_text(
                """
                WITH reserve_fund AS (
                    SELECT f.fund_id
                    FROM finance.funds f
                    WHERE f.scheme_id = CAST(:scheme_id AS UUID)
                      AND f.fund_type::text IN ('sinking', 'capital_works')
                      AND COALESCE(f.status, 'active') = 'active'
                    ORDER BY CASE WHEN f.fund_type::text = 'sinking' THEN 0 ELSE 1 END
                    LIMIT 1
                )
                UPDATE finance.gl_accounts ga
                SET fund_id = reserve_fund.fund_id
                FROM reserve_fund
                WHERE ga.scheme_id = CAST(:scheme_id AS UUID)
                  AND ga.account_code = ANY(CAST(:reserve_codes AS text[]))
                  AND ga.fund_id IS DISTINCT FROM reserve_fund.fund_id
                """
            ),
            {"scheme_id": scheme_id, "reserve_codes": list(_RESERVE_CODES)},
        )

    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))


def downgrade() -> None:
    # Non-destructive: removing fund ids from live GL accounts would recreate
    # the reporting ambiguity this migration fixes.
    pass
