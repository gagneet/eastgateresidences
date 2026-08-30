"""Harden finance.reconstruction_execution_batches(_items): FKs, RLS,
non-negative/status-consistency checks, and manifest-immutability triggers.

Applied while both tables are still empty (guarded — refuses to run
otherwise) so adding NOT NULL-compatible FKs and constraints is trivial and
low-risk. Deliberately a separate revision from 0073 rather than an amendment
to it — 0073 is already applied to the live database; editing an
already-applied migration's DDL in place would make its recorded revision id
lie about what actually ran, and risks silent drift between databases that
ran the original 0073 and ones that would run an edited version. This
revision is the correct, standard way to add hardening after the fact.

Revision ID: 0074_harden_recon_batches
Revises: 0073_accounting_period_integrity
Create Date: 2026-07-24 00:00:00.000001
"""

from __future__ import annotations

# @featuretrace:financial_integration_v2 — Hardens the reconstruction batch
#   control-plane tables added in 0073: FKs, RLS, checks, immutability triggers.
# Layer: migration
# Data flow: finance.reconstruction_execution_batches/_items (constraints only).
# Related: backend/alembic/versions/0073_accounting_period_integrity.py
#          backend/services/financial_core/adapters/db_postgres/models.py
#          backend/scripts/east_gate_2025_expense_reconstruction.py
#          backend/scripts/east_gate_lot_fee_charge_backfill.py
# Table: finance.reconstruction_execution_batches, finance.reconstruction_execution_batch_items

from alembic import op
from sqlalchemy import text as sa_text

revision = "0074_harden_recon_batches"
down_revision = "0073_accounting_period_integrity"
branch_labels = None
depends_on = None


def _run_empty_table_guard(bind) -> None:
    """This migration adds FKs/checks that are only trivially safe to add
    while both tables are empty (no existing rows to validate against, no
    risk of an existing row violating a new constraint mid-migration). Both
    tables were only just created by 0073 in this same deployment and have
    never been written to by any script yet — confirm that's still true
    before proceeding, rather than assuming it."""
    batch_count = bind.execute(
        sa_text("SELECT count(*) FROM finance.reconstruction_execution_batches")
    ).scalar()
    item_count = bind.execute(
        sa_text("SELECT count(*) FROM finance.reconstruction_execution_batch_items")
    ).scalar()
    if batch_count or item_count:
        raise RuntimeError(
            f"0074_harden_recon_batches: expected both tables empty, found "
            f"{batch_count} batch row(s) and {item_count} item row(s) — this migration's "
            f"FK/check additions were only designed to be trivially safe against an empty "
            f"table; review before proceeding on non-empty data."
        )


def upgrade() -> None:
    bind = op.get_bind()
    _run_empty_table_guard(bind)

    # ---- reconstruction_execution_batches: FKs + checks -------------------
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES core.tenants(tenant_id)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_scheme_id_fkey "
        "FOREIGN KEY (scheme_id) REFERENCES core.schemes(scheme_id)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_created_by_fkey "
        "FOREIGN KEY (created_by) REFERENCES core.users(user_id)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_approved_by_fkey "
        "FOREIGN KEY (approved_by) REFERENCES core.users(user_id)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_applied_by_fkey "
        "FOREIGN KEY (applied_by) REFERENCES core.users(user_id)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_expected_row_count_chk "
        "CHECK (expected_row_count >= 0)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_expected_amount_chk "
        "CHECK (expected_amount_cents >= 0)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_actual_row_count_chk "
        "CHECK (actual_row_count IS NULL OR actual_row_count >= 0)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_actual_amount_chk "
        "CHECK (actual_amount_cents IS NULL OR actual_amount_cents >= 0)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_failure_count_chk "
        "CHECK (failure_count IS NULL OR failure_count >= 0)"
    )
    # Dual control: approver must differ from creator. executed_by (a
    # separate identity captured only at --apply time, never stored on this
    # header) is checked against approved_by by HistoricalPostingAuthorisation's
    # own __post_init__ at apply time, and re-validated by the apply script
    # after resolving both identities against core.users — this header
    # cannot check executed_by itself since it isn't known when the row is
    # approved.
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_approver_differs_chk "
        "CHECK (approved_by IS NULL OR approved_by != created_by)"
    )
    # Approval fields must be set together once status leaves manifest_pending.
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batches "
        "ADD CONSTRAINT reconstruction_execution_batches_approval_state_chk "
        "CHECK (status = 'manifest_pending' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))"
    )

    # ---- reconstruction_execution_batch_items: tenant/scheme columns + FKs + checks
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD COLUMN tenant_id UUID"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD COLUMN scheme_id UUID"
    )
    # Backfill from the parent batch (table is empty per the guard above,
    # but this UPDATE is here for completeness/defensiveness rather than
    # relying solely on the emptiness guard).
    op.execute(
        """
        UPDATE finance.reconstruction_execution_batch_items i
        SET tenant_id = b.tenant_id, scheme_id = b.scheme_id
        FROM finance.reconstruction_execution_batches b
        WHERE i.batch_id = b.batch_id
        """
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items ALTER COLUMN tenant_id SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items ALTER COLUMN scheme_id SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD CONSTRAINT reconstruction_execution_batch_items_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES core.tenants(tenant_id)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD CONSTRAINT reconstruction_execution_batch_items_scheme_id_fkey "
        "FOREIGN KEY (scheme_id) REFERENCES core.schemes(scheme_id)"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD CONSTRAINT reconstruction_execution_batch_items_bank_tx_fkey "
        "FOREIGN KEY (bank_transaction_id) REFERENCES finance.bank_transactions(bank_transaction_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD CONSTRAINT reconstruction_execution_batch_items_expense_je_fkey "
        "FOREIGN KEY (expense_journal_entry_id) REFERENCES finance.journal_entries(journal_entry_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD CONSTRAINT reconstruction_execution_batch_items_recharge_je_fkey "
        "FOREIGN KEY (recharge_journal_entry_id) REFERENCES finance.journal_entries(journal_entry_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD CONSTRAINT reconstruction_execution_batch_items_sequence_uniq "
        "UNIQUE (batch_id, sequence_number)"
    )
    # An 'applied' item must have posted at least one journal — a
    # lot_fee_reversal item legitimately has expense_journal_entry_id NULL
    # (no new cash outflow, just a recharge reversal), but every applied
    # item needs at least one journal link.
    op.execute(
        "ALTER TABLE finance.reconstruction_execution_batch_items "
        "ADD CONSTRAINT reconstruction_execution_batch_items_applied_has_journal_chk "
        "CHECK (status != 'applied' OR expense_journal_entry_id IS NOT NULL OR recharge_journal_entry_id IS NOT NULL)"
    )

    op.execute(
        "DROP INDEX IF EXISTS finance.reconstruction_execution_batch_items_batch_idx"
    )
    op.execute(
        "CREATE INDEX reconstruction_execution_batch_items_batch_idx "
        "ON finance.reconstruction_execution_batch_items (batch_id, status, sequence_number)"
    )

    # ---- RLS — consistent with every other finance.* table (migration 0008 pattern) ----
    op.execute("ALTER TABLE finance.reconstruction_execution_batches ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.reconstruction_execution_batches FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_finance_reconstruction_execution_batches "
        "ON finance.reconstruction_execution_batches USING (tenant_id = core.current_tenant_id())"
    )
    op.execute("ALTER TABLE finance.reconstruction_execution_batch_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.reconstruction_execution_batch_items FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_finance_reconstruction_execution_batch_items "
        "ON finance.reconstruction_execution_batch_items USING (tenant_id = core.current_tenant_id())"
    )

    # ---- Immutability triggers ---------------------------------------------
    # "A reconstruction batch is an immutable header plus immutable
    # batch-item snapshots" is a design promise, not just a convention.
    # Immutable (manifest-defining): batch_type, target_period_label,
    # description, manifest_hash, expected_row_count, expected_amount_cents,
    # created_by, created_at, tenant_id, scheme_id / (items) batch_id,
    # tenant_id, scheme_id, sequence_number, bank_transaction_id,
    # transaction_date, description, amount_cents, category_name,
    # lot_number, item_type, source_snapshot_hash.
    # Operationally mutable: status, approval/application identities and
    # timestamps, journal reference columns, error_message, applied_at,
    # actual_row_count/actual_amount_cents/failure_count.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION finance.prevent_reconstruction_batch_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.tenant_id != OLD.tenant_id OR NEW.scheme_id != OLD.scheme_id
               OR NEW.batch_type != OLD.batch_type OR NEW.target_period_label != OLD.target_period_label
               OR NEW.description != OLD.description OR NEW.manifest_hash != OLD.manifest_hash
               OR NEW.expected_row_count != OLD.expected_row_count
               OR NEW.expected_amount_cents != OLD.expected_amount_cents
               OR NEW.created_by != OLD.created_by OR NEW.created_at != OLD.created_at THEN
                RAISE EXCEPTION 'reconstruction_execution_batches: manifest-defining columns are immutable once inserted (batch_id=%)', OLD.batch_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_reconstruction_batch_mutation
        BEFORE UPDATE ON finance.reconstruction_execution_batches
        FOR EACH ROW EXECUTE FUNCTION finance.prevent_reconstruction_batch_mutation()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION finance.prevent_reconstruction_batch_item_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.batch_id != OLD.batch_id OR NEW.tenant_id != OLD.tenant_id OR NEW.scheme_id != OLD.scheme_id
               OR NEW.sequence_number != OLD.sequence_number OR NEW.bank_transaction_id != OLD.bank_transaction_id
               OR NEW.transaction_date != OLD.transaction_date OR NEW.description != OLD.description
               OR NEW.amount_cents != OLD.amount_cents
               OR NEW.category_name IS DISTINCT FROM OLD.category_name
               OR NEW.lot_number IS DISTINCT FROM OLD.lot_number
               OR NEW.item_type != OLD.item_type OR NEW.source_snapshot_hash != OLD.source_snapshot_hash THEN
                RAISE EXCEPTION 'reconstruction_execution_batch_items: snapshot columns are immutable once inserted (batch_item_id=%)', OLD.batch_item_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_reconstruction_batch_item_mutation
        BEFORE UPDATE ON finance.reconstruction_execution_batch_items
        FOR EACH ROW EXECUTE FUNCTION finance.prevent_reconstruction_batch_item_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_reconstruction_batch_item_mutation ON finance.reconstruction_execution_batch_items")
    op.execute("DROP FUNCTION IF EXISTS finance.prevent_reconstruction_batch_item_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_reconstruction_batch_mutation ON finance.reconstruction_execution_batches")
    op.execute("DROP FUNCTION IF EXISTS finance.prevent_reconstruction_batch_mutation()")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_finance_reconstruction_execution_batch_items ON finance.reconstruction_execution_batch_items")
    op.execute("ALTER TABLE finance.reconstruction_execution_batch_items NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.reconstruction_execution_batch_items DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_finance_reconstruction_execution_batches ON finance.reconstruction_execution_batches")
    op.execute("ALTER TABLE finance.reconstruction_execution_batches NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.reconstruction_execution_batches DISABLE ROW LEVEL SECURITY")

    op.execute("DROP INDEX IF EXISTS finance.reconstruction_execution_batch_items_batch_idx")
    op.execute(
        "CREATE INDEX reconstruction_execution_batch_items_batch_idx "
        "ON finance.reconstruction_execution_batch_items (batch_id, status)"
    )

    for constraint in [
        "reconstruction_execution_batch_items_applied_has_journal_chk",
        "reconstruction_execution_batch_items_sequence_uniq",
        "reconstruction_execution_batch_items_recharge_je_fkey",
        "reconstruction_execution_batch_items_expense_je_fkey",
        "reconstruction_execution_batch_items_bank_tx_fkey",
        "reconstruction_execution_batch_items_scheme_id_fkey",
        "reconstruction_execution_batch_items_tenant_id_fkey",
    ]:
        op.execute(f"ALTER TABLE finance.reconstruction_execution_batch_items DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute("ALTER TABLE finance.reconstruction_execution_batch_items DROP COLUMN IF EXISTS scheme_id")
    op.execute("ALTER TABLE finance.reconstruction_execution_batch_items DROP COLUMN IF EXISTS tenant_id")

    for constraint in [
        "reconstruction_execution_batches_approval_state_chk",
        "reconstruction_execution_batches_approver_differs_chk",
        "reconstruction_execution_batches_failure_count_chk",
        "reconstruction_execution_batches_actual_amount_chk",
        "reconstruction_execution_batches_actual_row_count_chk",
        "reconstruction_execution_batches_expected_amount_chk",
        "reconstruction_execution_batches_expected_row_count_chk",
        "reconstruction_execution_batches_applied_by_fkey",
        "reconstruction_execution_batches_approved_by_fkey",
        "reconstruction_execution_batches_created_by_fkey",
        "reconstruction_execution_batches_scheme_id_fkey",
        "reconstruction_execution_batches_tenant_id_fkey",
    ]:
        op.execute(f"ALTER TABLE finance.reconstruction_execution_batches DROP CONSTRAINT IF EXISTS {constraint}")
