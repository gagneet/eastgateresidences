"""0050 — Add import tracking fields to core identity tables.

Revision ID: 0050_identity_import_tracking
Revises: 0049_cutover_control_plane
Create Date: 2026-06-03

# @featuretrace:postgres-identity-foundation — Additive migration: import tracking fields on core identity tables.
# Layer: migration
# Data flow: identity_bootstrap_service → core.schemes / core.tenants / core.lots / core.parties / core.ownership_periods (building-scoped).
# Related: backend/services/identity_bootstrap_service.py
#          backend/scripts/bootstrap_postgres_identity_foundation.py
#          docs/migration/east-gate-postgres-identity-foundation.md

These fields allow the bootstrap service to record:
  - which external system the record came from (source_system)
  - which MongoDB collection the record originated in (source_collection)
  - the MongoDB _id or external reference (source_record_id)
  - the batch ID of the import run (import_batch_id)
  - when the record was imported (imported_at)

All columns are NULLABLE with no server default so existing rows are
unaffected. The migration is fully additive: no column is removed and no
existing column is altered.

is_test_data is added to tables that don't already have it (core.schemes,
core.tenants, core.lots, core.parties, core.ownership_periods).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_identity_import_tracking"
down_revision = "0049_cutover_control_plane"
branch_labels = None
depends_on = None

# Tables and their existing column sets (verified from 0002 + 0010):
# core.tenants         — no is_test_data, no import tracking
# core.schemes         — no is_test_data, no import tracking
# core.lots            — no is_test_data, no import tracking
# core.parties         — no is_test_data, has metadata JSONB
# core.ownership_periods — no is_test_data, no import tracking
# core.users           — already has is_test_data (migration 0010)
# core.user_units      — already has is_test_data (migration 0010)


def _add_import_tracking(table: str, schema: str = "core") -> None:
    """Add all five import-tracking columns to a table if they don't exist."""
    full = f"{schema}.{table}"
    for col_name, col_type, col_default in [
        ("source_system",    "TEXT", None),
        ("source_collection","TEXT", None),
        ("source_record_id", "TEXT", None),
        ("import_batch_id",  "TEXT", None),
        ("imported_at",      "TIMESTAMPTZ", None),
    ]:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = '{schema}'
                      AND table_name   = '{table}'
                      AND column_name  = '{col_name}'
                ) THEN
                    ALTER TABLE "{schema}"."{table}"
                    ADD COLUMN "{col_name}" {col_type};
                END IF;
            END
            $$;
            """
        )


def _add_is_test_data(table: str, schema: str = "core") -> None:
    """Add is_test_data BOOLEAN NOT NULL DEFAULT false if missing."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{schema}'
                  AND table_name   = '{table}'
                  AND column_name  = 'is_test_data'
            ) THEN
                ALTER TABLE "{schema}"."{table}"
                ADD COLUMN is_test_data BOOLEAN NOT NULL DEFAULT false;
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    # ── core.tenants ──────────────────────────────────────────────────────────
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0050_identity_import_tracking.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _add_is_test_data("tenants")
    _add_import_tracking("tenants")

    # ── core.schemes ──────────────────────────────────────────────────────────
    _add_is_test_data("schemes")
    _add_import_tracking("schemes")

    # ── core.lots ─────────────────────────────────────────────────────────────
    _add_is_test_data("lots")
    _add_import_tracking("lots")
    # Add metadata JSONB to lots if missing (parties already has it)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'core'
                  AND table_name   = 'lots'
                  AND column_name  = 'metadata'
            ) THEN
                ALTER TABLE core.lots ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
            END IF;
        END
        $$;
        """
    )

    # ── core.parties ──────────────────────────────────────────────────────────
    _add_is_test_data("parties")
    _add_import_tracking("parties")
    # source_external_id: stable business key from external system
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'core'
                  AND table_name   = 'parties'
                  AND column_name  = 'source_external_id'
            ) THEN
                ALTER TABLE core.parties ADD COLUMN source_external_id TEXT;
            END IF;
        END
        $$;
        """
    )

    # ── core.ownership_periods ────────────────────────────────────────────────
    _add_is_test_data("ownership_periods")
    _add_import_tracking("ownership_periods")

    # ── indexes on import_batch_id for fast batch-level queries ───────────────
    for table in ("tenants", "schemes", "lots", "parties", "ownership_periods"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname = 'core'
                      AND tablename  = '{table}'
                      AND indexname  = 'ix_{table}_import_batch_id'
                ) THEN
                    CREATE INDEX ix_{table}_import_batch_id
                    ON core.{table} (import_batch_id)
                    WHERE import_batch_id IS NOT NULL;
                END IF;
            END
            $$;
            """
        )

    # ── index on source_record_id for idempotency lookups ─────────────────────
    for table in ("lots", "parties", "ownership_periods"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname = 'core'
                      AND tablename  = '{table}'
                      AND indexname  = 'ix_{table}_source_record_id'
                ) THEN
                    CREATE INDEX ix_{table}_source_record_id
                    ON core.{table} (source_record_id)
                    WHERE source_record_id IS NOT NULL;
                END IF;
            END
            $$;
            """
        )

    # ── index on parties.source_external_id ───────────────────────────────────
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'core'
                  AND tablename  = 'parties'
                  AND indexname  = 'ix_parties_source_external_id'
            ) THEN
                CREATE INDEX ix_parties_source_external_id
                ON core.parties (source_external_id)
                WHERE source_external_id IS NOT NULL;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Drop indexes first (safe regardless of data presence)
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0050_identity_import_tracking.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table in ("lots", "parties", "ownership_periods"):
        op.execute(f"DROP INDEX IF EXISTS core.ix_{table}_source_record_id")
    op.execute("DROP INDEX IF EXISTS core.ix_parties_source_external_id")
    for table in ("tenants", "schemes", "lots", "parties", "ownership_periods"):
        op.execute(f"DROP INDEX IF EXISTS core.ix_{table}_import_batch_id")

    # Remove columns — only safe if no import has been run yet.
    # If a bootstrap has already been applied, data in these columns will be lost.
    # Document this as a known risk in the runbook before running downgrade.
    for table in ("tenants", "schemes", "lots", "parties", "ownership_periods"):
        for col in ("source_system", "source_collection", "source_record_id", "import_batch_id", "imported_at"):
            op.execute(f'ALTER TABLE core."{table}" DROP COLUMN IF EXISTS "{col}"')

    op.execute('ALTER TABLE core.parties DROP COLUMN IF EXISTS source_external_id')
    op.execute('ALTER TABLE core.lots DROP COLUMN IF EXISTS metadata')

    for table in ("lots", "parties", "ownership_periods"):
        op.execute(f'ALTER TABLE core."{table}" DROP COLUMN IF EXISTS is_test_data')
