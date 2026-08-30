"""0004 — Finance extension tables: levy, receipts, bank, reconciliation, payment plans.

Revision: 0004
Previous: 0003

Creates:
- finance.levy_rules             — levy configuration (rate, frequency, allocation order)
- finance.levy_runs              — one per billing cycle (quarterly, annual, special)
- finance.levy_items             — per-lot levy charges within a run
- finance.owner_credit_balances  — over-payment credit tracking
- finance.payment_plans          — hardship / instalment arrangements
- finance.payment_plan_installments
- finance.bank_statement_imports — import metadata for statement files
- finance.bank_transactions      — individual bank statement lines
- finance.receipts               — receipt records matched to bank transactions
- finance.receipt_allocations    — how each receipt is split across levy / interest / costs
- finance.reconciliation_runs    — three-way reconciliation snapshots
- finance.payment_batches        — ABA / EFT disbursement batches
- finance.payment_batch_items    — individual invoice lines within a batch

All *_cents columns are BIGINT (never NUMERIC).
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0004_finance_extension_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               CREATE TABLE finance.levy_rules
               (
                   levy_rule_id                UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id                   UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id                   UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   fund_id                     UUID        NOT NULL REFERENCES finance.funds (fund_id),
                   frequency                   TEXT        NOT NULL,
                   interest_rate_basis_points  INT         NOT NULL DEFAULT 1000,
                   grace_days                  INT         NOT NULL DEFAULT 0,
                   payment_allocation_order    TEXT[] NOT NULL DEFAULT ARRAY['levy', 'interest', 'costs'],
                   hardship_statement_required BOOLEAN     NOT NULL DEFAULT false,
                   effective_from              DATE        NOT NULL,
                   effective_to                DATE,
                   created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE finance.levy_runs
               (
                   levy_run_id    UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id      UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id      UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   financial_year TEXT        NOT NULL,
                   quarter_no     INT,
                   issue_date     DATE        NOT NULL,
                   due_date       DATE        NOT NULL,
                   status         TEXT        NOT NULL DEFAULT 'draft',
                   generated_by   UUID REFERENCES core.users (user_id),
                   approved_by    UUID REFERENCES core.users (user_id),
                   approved_at    TIMESTAMPTZ,
                   created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                   CHECK (due_date >= issue_date)
               )
               """)

    op.execute("""
               CREATE TABLE finance.levy_items
               (
                   levy_item_id         UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id            UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id            UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   levy_run_id          UUID        NOT NULL REFERENCES finance.levy_runs (levy_run_id),
                   lot_id               UUID        NOT NULL REFERENCES core.lots (lot_id),
                   owner_party_id       UUID        NOT NULL REFERENCES core.parties (party_id),
                   fund_id              UUID        NOT NULL REFERENCES finance.funds (fund_id),
                   principal_cents      BIGINT      NOT NULL CHECK (principal_cents >= 0),
                   gst_cents            BIGINT      NOT NULL DEFAULT 0 CHECK (gst_cents >= 0),
                   interest_cents       BIGINT      NOT NULL DEFAULT 0 CHECK (interest_cents >= 0),
                   recovery_costs_cents BIGINT      NOT NULL DEFAULT 0 CHECK (recovery_costs_cents >= 0),
                   paid_cents           BIGINT      NOT NULL DEFAULT 0 CHECK (paid_cents >= 0),
                   journal_entry_id     UUID REFERENCES finance.journal_entries (journal_entry_id),
                   status               TEXT        NOT NULL DEFAULT 'issued',
                   created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                   UNIQUE (levy_run_id, lot_id, fund_id)
               )
               """)

    op.execute("""
               CREATE TABLE finance.owner_credit_balances
               (
                   credit_balance_id       UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id               UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id               UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   lot_id                  UUID        NOT NULL REFERENCES core.lots (lot_id),
                   owner_party_id          UUID        NOT NULL REFERENCES core.parties (party_id),
                   fund_id                 UUID REFERENCES finance.funds (fund_id),
                   available_cents         BIGINT      NOT NULL DEFAULT 0,
                   source_journal_entry_id UUID REFERENCES finance.journal_entries (journal_entry_id),
                   created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                   updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                   UNIQUE (scheme_id, lot_id, owner_party_id, fund_id)
               )
               """)

    op.execute("""
               CREATE TABLE finance.payment_plans
               (
                   payment_plan_id         UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id               UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id               UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   lot_id                  UUID        NOT NULL REFERENCES core.lots (lot_id),
                   owner_party_id          UUID        NOT NULL REFERENCES core.parties (party_id),
                   requested_on            DATE        NOT NULL,
                   decision_due_on         DATE        NOT NULL,
                   decision_on             DATE,
                   decision                TEXT,
                   decision_reason         TEXT,
                   status                  TEXT        NOT NULL DEFAULT 'requested',
                   source_form_document_id TEXT,
                   created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE finance.payment_plan_installments
               (
                   installment_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                   payment_plan_id UUID   NOT NULL
                       REFERENCES finance.payment_plans (payment_plan_id),
                   due_on          DATE   NOT NULL,
                   amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0),
                   paid_cents      BIGINT NOT NULL  DEFAULT 0,
                   status          TEXT   NOT NULL  DEFAULT 'scheduled'
               )
               """)

    op.execute("""
               CREATE TABLE finance.bank_statement_imports
               (
                   bank_statement_import_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id                UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id                UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   trust_account_id         UUID        NOT NULL
                       REFERENCES finance.trust_accounts (trust_account_id),
                   import_source            TEXT        NOT NULL,
                   file_hash                TEXT,
                   imported_by              UUID REFERENCES core.users (user_id),
                   imported_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                   row_count                INT         NOT NULL DEFAULT 0,
                   metadata                 JSONB       NOT NULL DEFAULT '{}'::jsonb
               )
               """)

    op.execute("""
               CREATE TABLE finance.bank_transactions
               (
                   bank_transaction_id      UUID PRIMARY KEY                       DEFAULT gen_random_uuid(),
                   tenant_id                UUID                          NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id                UUID                          NOT NULL REFERENCES core.schemes (scheme_id),
                   trust_account_id         UUID                          NOT NULL
                       REFERENCES finance.trust_accounts (trust_account_id),
                   bank_statement_import_id UUID
                       REFERENCES finance.bank_statement_imports (bank_statement_import_id),
                   transaction_date         DATE                          NOT NULL,
                   description              TEXT                          NOT NULL,
                   reference                TEXT,
                   amount_cents             BIGINT                        NOT NULL,
                   balance_after_cents      BIGINT,
                   external_transaction_id  TEXT,
                   reconciliation_status    finance.reconciliation_status NOT NULL DEFAULT 'unmatched',
                   created_at               TIMESTAMPTZ                   NOT NULL DEFAULT now(),
                   UNIQUE (trust_account_id, external_transaction_id)
               )
               """)

    op.execute("""
               CREATE TABLE finance.receipts
               (
                   receipt_id          UUID PRIMARY KEY                 DEFAULT gen_random_uuid(),
                   tenant_id           UUID                    NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id           UUID                    NOT NULL REFERENCES core.schemes (scheme_id),
                   trust_account_id    UUID REFERENCES finance.trust_accounts (trust_account_id),
                   bank_transaction_id UUID REFERENCES finance.bank_transactions (bank_transaction_id),
                   payer_party_id      UUID REFERENCES core.parties (party_id),
                   lot_id              UUID REFERENCES core.lots (lot_id),
                   channel             finance.payment_channel NOT NULL,
                   received_on         DATE                    NOT NULL,
                   amount_cents        BIGINT                  NOT NULL CHECK (amount_cents > 0),
                   external_reference  TEXT,
                   journal_entry_id    UUID REFERENCES finance.journal_entries (journal_entry_id),
                   created_at          TIMESTAMPTZ             NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE finance.receipt_allocations
               (
                   allocation_id   UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   receipt_id      UUID        NOT NULL REFERENCES finance.receipts (receipt_id),
                   levy_item_id    UUID REFERENCES finance.levy_items (levy_item_id),
                   allocation_type TEXT        NOT NULL,
                   allocated_cents BIGINT      NOT NULL CHECK (allocated_cents > 0),
                   created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE finance.reconciliation_runs
               (
                   reconciliation_run_id      UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id                  UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id                  UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   trust_account_id           UUID        NOT NULL
                       REFERENCES finance.trust_accounts (trust_account_id),
                   period_start               DATE        NOT NULL,
                   period_end                 DATE        NOT NULL,
                   cashbook_balance_cents     BIGINT      NOT NULL,
                   bank_balance_cents         BIGINT      NOT NULL,
                   owner_ledger_balance_cents BIGINT      NOT NULL,
                   difference_cents           BIGINT      NOT NULL,
                   status                     TEXT        NOT NULL DEFAULT 'draft',
                   generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
                   approved_by                UUID REFERENCES core.users (user_id),
                   approved_at                TIMESTAMPTZ,
                   CHECK (period_end >= period_start)
               )
               """)

    op.execute("""
               CREATE TABLE finance.payment_batches
               (
                   payment_batch_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id        UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id        UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   trust_account_id UUID        NOT NULL
                       REFERENCES finance.trust_accounts (trust_account_id),
                   batch_type       TEXT        NOT NULL DEFAULT 'aba',
                   total_cents      BIGINT      NOT NULL DEFAULT 0,
                   status           TEXT        NOT NULL DEFAULT 'draft',
                   aba_file_hash    TEXT,
                   created_by       UUID REFERENCES core.users (user_id),
                   approved_by      UUID REFERENCES core.users (user_id),
                   approved_at      TIMESTAMPTZ,
                   created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE finance.payment_batch_items
               (
                   payment_batch_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                   payment_batch_id      UUID   NOT NULL
                       REFERENCES finance.payment_batches (payment_batch_id),
                   amount_cents          BIGINT NOT NULL CHECK (amount_cents > 0),
                   journal_entry_id      UUID REFERENCES finance.journal_entries (journal_entry_id)
               )
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0004_finance_extension_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP TABLE IF EXISTS finance.payment_batch_items CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.payment_batches CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.reconciliation_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.receipt_allocations CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.receipts CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.bank_transactions CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.bank_statement_imports CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.payment_plan_installments CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.payment_plans CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.owner_credit_balances CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.levy_items CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.levy_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.levy_rules CASCADE")
