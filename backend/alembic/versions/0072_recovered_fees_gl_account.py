"""finance.gl_accounts: add 4003 Recovered Fees & Collection Costs (income) for every scheme.

Revision ID: 0072_recovered_fees_gl_account
Revises: 0071_powerhouse_cmd_foundation
Create Date: 2026-07-24 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:demo_bank — New income GL account backing FinancialCoreService.charge_lot_fee().
# Layer: migration
# Data flow: services/financial_core/service.py::charge_lot_fee() → finance.gl_accounts (4003) →
#            finance.journal_lines (building-scoped).
# Related: backend/services/financial_core/service.py
#          backend/services/financial_core/domain/entities.py (ChargeLotFeeCommand)
#          backend/services/financial_core/genesis.py (DEFAULT_GL_ACCOUNTS — future-onboarded schemes)
#          tasks/GAP-FIN-018-east-gate-demo-bank-to-ledger-pipeline.md
# Table: finance.gl_accounts

from alembic import op
from sqlalchemy import text as sa_text

revision = "0072_recovered_fees_gl_account"
down_revision = "0071_powerhouse_cmd_foundation"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # Data migration, not DDL: back-fills the new account onto every scheme
    # already onboarded (their chart of accounts was seeded before this
    # account existed). Idempotent (WHERE NOT EXISTS) — safe to re-run.
    # Also added to genesis.py::DEFAULT_GL_ACCOUNTS in the same change so
    # future-onboarded schemes get it at genesis time instead of needing a
    # second backfill.
    #
    # Unlike core.schemes/core.tenants, finance.gl_accounts has NO RLS bypass
    # clause (same category as core.lots — see CLAUDE.md's documented
    # footgun: "an ad-hoc connection with no app.tenant_id set silently
    # returns 0 rows, not an error", and separately, a cross-tenant INSERT
    # under the bypass id is flatly rejected, not silently scoped). A single
    # cross-tenant INSERT...SELECT under the bypass id was tried first and
    # failed with InsufficientPrivilegeError — confirmed live, not assumed.
    # Must switch app.tenant_id to each real tenant in turn instead.
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
                    (gl_account_id, tenant_id, scheme_id, account_code, account_name,
                     account_type, is_control_account, status, created_at)
                SELECT gen_random_uuid(), :tenant_id, :scheme_id, '4003',
                       'Recovered Fees & Collection Costs',
                       CAST('income' AS finance.account_type), FALSE,
                       CAST('active' AS core.record_status), NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM finance.gl_accounts
                    WHERE scheme_id = CAST(:scheme_id AS UUID) AND account_code = '4003'
                )
                """
            ),
            {"tenant_id": tenant_id, "scheme_id": scheme_id},
        )
    # Restore the bypass context for whatever migration/session runs next.
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))
    scheme_rows = bind.execute(
        sa_text("SELECT tenant_id::text FROM core.schemes")
    ).fetchall()
    for (tenant_id,) in scheme_rows:
        bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        bind.execute(sa_text("DELETE FROM finance.gl_accounts WHERE account_code = '4003'"))
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))
