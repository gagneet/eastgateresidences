"""0008 — RLS policies and production indexes.

Revision: 0008
Previous: 0007

Enables Row-Level Security on every tenant-scoped table and creates the
ENABLE ROW LEVEL SECURITY + CREATE POLICY statements using the
core.current_tenant_id() helper defined in 0007.

Also creates all production indexes per docs/migration/01_postgresql_schema_production.md §17.

IMPORTANT: The application connection role (strataos_app) must NOT be a superuser.
Superusers bypass RLS. The migration must be run by a separate migration role
(strataos_migrate) that holds BYPASSRLS for the duration of setup only.
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# All tenant-scoped tables that need RLS.
# Format: (schema, table)
_TENANT_TABLES = [
    # core
    ("core", "schemes"),
    ("core", "buildings"),
    ("core", "lots"),
    ("core", "parties"),
    ("core", "party_roles"),
    ("core", "ownership_periods"),
    ("core", "outbox"),
    ("core", "audit_events"),
    ("core", "approval_policies"),
    ("core", "approval_requests"),
    # NOTE: core.approval_steps has no direct tenant_id column — tenant_id is
    # added and RLS is created in migration 0009_tenant_id_child_tables.
    # finance
    ("finance", "funds"),
    ("finance", "trust_accounts"),
    ("finance", "gl_accounts"),
    ("finance", "accounting_periods"),
    ("finance", "journal_entries"),
    ("finance", "journal_lines"),
    ("finance", "levy_rules"),
    ("finance", "levy_runs"),
    ("finance", "levy_items"),
    ("finance", "owner_credit_balances"),
    ("finance", "payment_plans"),
    # NOTE: finance.payment_plan_installments has no direct tenant_id — added in 0009.
    ("finance", "bank_statement_imports"),
    ("finance", "bank_transactions"),
    ("finance", "receipts"),
    # NOTE: finance.receipt_allocations has no direct tenant_id — added in 0009.
    ("finance", "reconciliation_runs"),
    ("finance", "payment_batches"),
    # ops
    ("ops", "vendors"),
    ("ops", "work_requests"),
    ("ops", "work_orders"),
    ("ops", "quote_requests"),
    ("ops", "vendor_quotes"),
    ("ops", "vendor_invoices"),
    # compliance
    ("compliance", "entitlement_schedules"),
    ("compliance", "lot_entitlements"),
    ("compliance", "disclosure_events"),
    ("compliance", "generated_artifacts"),
    ("compliance", "manager_licences"),
    ("compliance", "ec_training_records"),
    ("compliance", "conflict_disclosures"),
    ("compliance", "manager_contracts"),
    ("compliance", "tenant_meeting_rights"),
    ("compliance", "acat_dispute_events"),
    # modules
    ("modules", "tenant_modules"),
]

# Tables where the RLS key is scheme_id rather than tenant_id.
# (These have tenant_id too, but scheme is the primary isolation boundary.)
_SCHEME_TABLES: set[tuple[str, str]] = set()


def _policy_name(schema: str, table: str) -> str:
    """Generated function header.

    Function: _policy_name
    Path: backend/alembic/versions/0008_rls_and_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"tenant_isolation_{schema}_{table}"


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0008_rls_and_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for schema, table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_policy_name(schema, table)} ON {schema}.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )

    # payment_plan_installments, receipt_allocations, and approval_steps have no
    # direct tenant_id column — they are handled in migration 0009 (ADD COLUMN + RLS).

    # ----------------------------------------------------------------- indexes
    # finance.journal_entries — most frequently queried table
    op.execute(
        "CREATE INDEX je_scheme_period_idx "
        "ON finance.journal_entries (scheme_id, period_id, effective_on)"
    )
    op.execute(
        "CREATE INDEX je_status_idx "
        "ON finance.journal_entries (scheme_id, status)"
    )
    op.execute(
        "CREATE UNIQUE INDEX je_idempotency_key_idx "
        "ON finance.journal_entries (tenant_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )

    # finance.journal_lines — always joined to entries
    op.execute(
        "CREATE INDEX jl_entry_idx ON finance.journal_lines (journal_entry_id)"
    )
    op.execute(
        "CREATE INDEX jl_lot_idx ON finance.journal_lines (lot_id) WHERE lot_id IS NOT NULL"
    )

    # finance.levy_items — per-run per-lot queries
    op.execute(
        "CREATE INDEX li_run_lot_idx ON finance.levy_items (levy_run_id, lot_id)"
    )
    op.execute(
        "CREATE INDEX li_lot_outstanding_idx "
        "ON finance.levy_items (lot_id, status) WHERE status != 'paid'"
    )

    # finance.bank_transactions — reconciliation queries
    op.execute(
        "CREATE INDEX bt_account_date_idx "
        "ON finance.bank_transactions (trust_account_id, transaction_date)"
    )
    op.execute(
        "CREATE INDEX bt_unmatched_idx "
        "ON finance.bank_transactions (trust_account_id, reconciliation_status) "
        "WHERE reconciliation_status = 'unmatched'"
    )

    # core.outbox — relay worker polling query
    op.execute(
        "CREATE INDEX outbox_created_pending_idx "
        "ON core.outbox (created_at ASC) "
        "WHERE published_at IS NULL"
    )

    # core.ownership_periods — bitemporal range queries
    op.execute(
        "CREATE INDEX op_lot_valid_idx "
        "ON core.ownership_periods (lot_id, valid_from, valid_to)"
    )

    # core.audit_events — recent events per scheme
    op.execute(
        "CREATE INDEX ae_scheme_created_idx "
        "ON core.audit_events (scheme_id, created_at DESC)"
    )

    # compliance.conflict_disclosures — hash chain integrity check
    op.execute(
        "CREATE INDEX cd_party_disclosed_idx "
        "ON compliance.conflict_disclosures (party_id, disclosed_at DESC)"
    )

    # modules.tenant_modules — active plugin lookup per scheme
    op.execute(
        "CREATE INDEX tm_scheme_active_idx "
        "ON modules.tenant_modules (tenant_id, scheme_id) "
        "WHERE is_active = true"
    )


def downgrade() -> None:
    # Drop indexes
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0008_rls_and_indexes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for idx in [
        "tm_scheme_active_idx",
        "cd_party_disclosed_idx",
        "ae_scheme_created_idx",
        "op_lot_valid_idx",
        "outbox_created_pending_idx",
        "bt_unmatched_idx",
        "bt_account_date_idx",
        "li_lot_outstanding_idx",
        "li_run_lot_idx",
        "jl_lot_idx",
        "jl_entry_idx",
        "je_idempotency_key_idx",
        "je_status_idx",
        "je_scheme_period_idx",
    ]:
        op.execute(f"DROP INDEX IF EXISTS {idx} CASCADE")

    # Drop RLS policies and disable RLS
    for schema, table in reversed(_TENANT_TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS {_policy_name(schema, table)} ON {schema}.{table}"
        )
        op.execute(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY")
