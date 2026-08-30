"""0003 — Finance core tables: funds, trust accounts, GL, periods, journal, outbox.

Revision: 0003
Previous: 0002

Creates:
- finance.funds                — admin/sinking/capital-works fund definitions
- finance.trust_accounts       — physical trust bank accounts (BSB/account encrypted)
- finance.gl_accounts          — chart of accounts per scheme
- finance.accounting_periods   — financial period management (open/soft_closed/locked/sealed)
- finance.journal_entries      — double-entry journal header (append-only after posting)
- finance.journal_lines        — debit/credit lines (direction enum + single amount_cents)
- core.outbox                  — transactional outbox for async MongoDB relay (ADR-003)

Money invariant: ALL amount_cents columns are BIGINT. Never NUMERIC in transactional rows.
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0003_finance_core_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               CREATE TABLE finance.funds
               (
                   fund_id    UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   tenant_id  UUID               NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id  UUID               NOT NULL REFERENCES core.schemes (scheme_id),
                   fund_code  TEXT               NOT NULL,
                   fund_name  TEXT               NOT NULL,
                   fund_type  finance.fund_type  NOT NULL,
                   status     core.record_status NOT NULL DEFAULT 'active',
                   created_at TIMESTAMPTZ        NOT NULL DEFAULT now(),
                   UNIQUE (scheme_id, fund_code)
               )
               """)

    op.execute("""
               CREATE TABLE finance.trust_accounts
               (
                   trust_account_id         UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   tenant_id                UUID               NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id                UUID               NOT NULL REFERENCES core.schemes (scheme_id),
                   fund_id                  UUID REFERENCES finance.funds (fund_id),
                   bank_name                TEXT               NOT NULL,
                   account_name             TEXT               NOT NULL,
                   bsb_encrypted            BYTEA,
                   account_number_encrypted BYTEA,
                   masked_bsb               TEXT,
                   masked_account_number    TEXT,
                   external_uid             TEXT,
                   opened_on                DATE,
                   closed_on                DATE,
                   status                   core.record_status NOT NULL DEFAULT 'active',
                   created_at               TIMESTAMPTZ        NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE finance.gl_accounts
               (
                   gl_account_id      UUID PRIMARY KEY              DEFAULT gen_random_uuid(),
                   tenant_id          UUID                 NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id          UUID                 NOT NULL REFERENCES core.schemes (scheme_id),
                   fund_id            UUID REFERENCES finance.funds (fund_id),
                   account_code       TEXT                 NOT NULL,
                   account_name       TEXT                 NOT NULL,
                   account_type       finance.account_type NOT NULL,
                   is_control_account BOOLEAN              NOT NULL DEFAULT false,
                   status             core.record_status   NOT NULL DEFAULT 'active',
                   created_at         TIMESTAMPTZ          NOT NULL DEFAULT now(),
                   UNIQUE (scheme_id, account_code)
               )
               """)

    op.execute("""
               CREATE TABLE finance.accounting_periods
               (
                   period_id    UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id    UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id    UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   period_label TEXT        NOT NULL,
                   starts_on    DATE        NOT NULL,
                   ends_on      DATE        NOT NULL,
                   status       TEXT        NOT NULL DEFAULT 'open',
                   locked_at    TIMESTAMPTZ,
                   locked_by    UUID REFERENCES core.users (user_id),
                   created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                   CHECK (ends_on >= starts_on),
                   UNIQUE (scheme_id, period_label)
               )
               """)

    # journal_entries: append-only double-entry header.
    # idempotency_key prevents duplicate submissions (UNIQUE per tenant).
    # entry_number is a per-scheme monotonic counter used in hash chaining.
    op.execute("""
               CREATE TABLE finance.journal_entries
               (
                   journal_entry_id UUID PRIMARY KEY              DEFAULT gen_random_uuid(),
                   tenant_id        UUID                 NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id        UUID                 NOT NULL REFERENCES core.schemes (scheme_id),
                   fund_id          UUID REFERENCES finance.funds (fund_id),
                   period_id        UUID                 NOT NULL REFERENCES finance.accounting_periods (period_id),
                   entry_number     BIGINT               NOT NULL GENERATED ALWAYS AS IDENTITY,
                   source_type      TEXT                 NOT NULL,
                   source_reference TEXT,
                   narration        TEXT                 NOT NULL,
                   status           finance.entry_status NOT NULL DEFAULT 'draft',
                   effective_on     DATE                 NOT NULL,
                   posted_at        TIMESTAMPTZ,
                   posted_by        UUID REFERENCES core.users (user_id),
                   approved_by      UUID REFERENCES core.users (user_id),
                   reversal_of_id   UUID REFERENCES finance.journal_entries (journal_entry_id),
                   idempotency_key  TEXT,
                   prev_entry_hash  TEXT,
                   entry_hash       TEXT,
                   is_test_data     BOOLEAN              NOT NULL DEFAULT false,
                   metadata         JSONB                NOT NULL DEFAULT '{}'::jsonb,
                   created_at       TIMESTAMPTZ          NOT NULL DEFAULT now(),
                   UNIQUE (tenant_id, idempotency_key),
                   UNIQUE (scheme_id, entry_number)
               )
               """)

    # journal_lines: one row per debit or credit leg.
    # direction enum + positive amount_cents ensures single-side clarity.
    # CHECK guarantees amount_cents is always positive (never zero, never negative).
    op.execute("""
               CREATE TABLE finance.journal_lines
               (
                   journal_line_id  UUID PRIMARY KEY                 DEFAULT gen_random_uuid(),
                   tenant_id        UUID                    NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id        UUID                    NOT NULL REFERENCES core.schemes (scheme_id),
                   journal_entry_id UUID                    NOT NULL
                       REFERENCES finance.journal_entries (journal_entry_id)
                           ON DELETE RESTRICT,
                   gl_account_id    UUID                    NOT NULL REFERENCES finance.gl_accounts (gl_account_id),
                   direction        finance.entry_direction NOT NULL,
                   amount_cents     BIGINT                  NOT NULL CHECK (amount_cents > 0),
                   gst_cents        BIGINT                  NOT NULL DEFAULT 0 CHECK (gst_cents >= 0),
                   lot_id           UUID REFERENCES core.lots (lot_id),
                   party_id         UUID REFERENCES core.parties (party_id),
                   narration        TEXT,
                   created_at       TIMESTAMPTZ             NOT NULL DEFAULT now()
               )
               """)

    # Transactional outbox (ADR-003): written in the same transaction as
    # finance.journal_entries. A background relay reads unprocessed rows and
    # propagates events to MongoDB, then marks published_at.
    # Index on (created_at) WHERE published_at IS NULL makes polling cheap.
    op.execute("""
               CREATE TABLE core.outbox
               (
                   id           UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id    UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id    UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   event_type   TEXT        NOT NULL,
                   payload      JSONB       NOT NULL,
                   created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                   published_at TIMESTAMPTZ
               )
               """)
    op.execute(
        "CREATE INDEX outbox_unpub_idx ON core.outbox (created_at) "
        "WHERE published_at IS NULL"
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0003_finance_core_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP INDEX IF EXISTS outbox_unpub_idx")
    op.execute("DROP TABLE IF EXISTS core.outbox CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.journal_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.journal_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.accounting_periods CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.gl_accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.trust_accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS finance.funds CASCADE")
