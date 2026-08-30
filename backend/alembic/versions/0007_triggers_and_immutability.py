"""0007 — Trigger functions and immutability guards.

Revision: 0007
Previous: 0006

Creates PL/pgSQL functions and triggers:

1. finance.prevent_posted_journal_mutation()
   → trg_prevent_posted_journal_update on finance.journal_entries
   Blocks any UPDATE on a posted entry. Corrections must use reversal entries.

2. finance.prevent_posted_line_mutation()
   → trg_prevent_posted_line_update on finance.journal_lines
   Blocks any UPDATE or DELETE on a line whose parent entry is posted.

3. finance.assert_journal_balanced(p_journal_entry_id UUID)
   Called by the financial_core service when transitioning a journal entry to
   'posted'. Raises exception if SUM(debit amounts) ≠ SUM(credit amounts).
   Also raises if either side is zero (prevents empty journals).

4. core.current_tenant_id()
   Returns current_setting('app.tenant_id', true)::uuid (or NULL if unset).
   Used in all RLS policies added in migration 0008.

5. core.outbox_set_published()
   Utility function called by the outbox relay worker via direct SQL —
   marks a row as published in a single round-trip.
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ helpers
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0007_triggers_and_immutability.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               CREATE
               OR REPLACE FUNCTION core.current_tenant_id()
        RETURNS UUID LANGUAGE SQL STABLE SECURITY DEFINER AS $$
               SELECT nullif(current_setting('app.tenant_id', true), '') ::uuid
        $$
               """)

    # ------------------------------------------------- journal immutability
    op.execute("""
               CREATE
               OR REPLACE FUNCTION finance.prevent_posted_journal_mutation()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
               BEGIN
            IF
               OLD.status = 'posted' THEN
                RAISE EXCEPTION
                    'journal entry % is posted and cannot be modified. '
                    'Use a reversal entry.',
                    OLD.journal_entry_id;
               END IF;
               RETURN NEW;
               END;
        $$
               """)

    op.execute("""
               CREATE TRIGGER trg_prevent_posted_journal_update
                   BEFORE UPDATE
                   ON finance.journal_entries
                   FOR EACH ROW EXECUTE FUNCTION finance.prevent_posted_journal_mutation()
               """)

    op.execute("""
               CREATE
               OR REPLACE FUNCTION finance.prevent_posted_line_mutation()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
               _parent_status TEXT;
               BEGIN
               SELECT status
               INTO _parent_status
               FROM finance.journal_entries
               WHERE journal_entry_id = OLD.journal_entry_id;

               IF
               _parent_status = 'posted' THEN
                RAISE EXCEPTION
                    'journal line % belongs to a posted entry and cannot be '
                    'modified or deleted.',
                    OLD.journal_line_id;
               END IF;

            -- On UPDATE return NEW; on DELETE return OLD (doesn't matter, won't reach here)
            IF
               TG_OP = 'DELETE' THEN
                RETURN OLD;
               END IF;
               RETURN NEW;
               END;
        $$
               """)

    op.execute("""
               CREATE TRIGGER trg_prevent_posted_line_update
                   BEFORE UPDATE OR
               DELETE
               ON finance.journal_lines
            FOR EACH ROW EXECUTE FUNCTION finance.prevent_posted_line_mutation()
               """)

    # -------------------------------------------- balanced journal assertion
    # Called explicitly by financial_core service BEFORE setting status='posted'.
    # The function must be called inside the same transaction as the status update.
    op.execute("""
               CREATE
               OR REPLACE FUNCTION finance.assert_journal_balanced(
            p_journal_entry_id UUID
        ) RETURNS VOID LANGUAGE plpgsql AS $$
        DECLARE
               _debit_sum  BIGINT;
            _credit_sum BIGINT;
               BEGIN
               SELECT COALESCE(SUM(amount_cents) FILTER(WHERE direction = 'debit'), 0),
                      COALESCE(SUM(amount_cents) FILTER(WHERE direction = 'credit'), 0)
               INTO _debit_sum, _credit_sum
               FROM finance.journal_lines
               WHERE journal_entry_id = p_journal_entry_id;

               IF
               _debit_sum = 0 AND _credit_sum = 0 THEN
                RAISE EXCEPTION
                    'journal entry % has no lines — cannot post an empty journal.',
                    p_journal_entry_id;
               END IF;

            IF
               _debit_sum != _credit_sum THEN
                RAISE EXCEPTION
                    'journal entry % is not balanced: debit=% credit=%',
                    p_journal_entry_id, _debit_sum, _credit_sum;
               END IF;
               END;
        $$
               """)

    # -------------------------------------------- outbox relay helper
    op.execute("""
               CREATE
               OR REPLACE FUNCTION core.outbox_mark_published(p_id UUID)
        RETURNS VOID LANGUAGE SQL AS $$
               UPDATE core.outbox
               SET published_at = now()
               WHERE id = p_id
                 AND published_at IS NULL
                   $$
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0007_triggers_and_immutability.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP FUNCTION IF EXISTS core.outbox_mark_published(UUID) CASCADE")
    op.execute("DROP FUNCTION IF EXISTS finance.assert_journal_balanced(UUID) CASCADE")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_posted_line_update ON finance.journal_lines")
    op.execute("DROP FUNCTION IF EXISTS finance.prevent_posted_line_mutation() CASCADE")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_posted_journal_update ON finance.journal_entries")
    op.execute("DROP FUNCTION IF EXISTS finance.prevent_posted_journal_mutation() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS core.current_tenant_id() CASCADE")
