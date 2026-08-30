"""0001 — Create schemas, install extensions and define ENUM types.

Revision: 0001
Previous:  (none — base migration)

Creates:
- Schemas: core, finance, compliance, ops, modules, analytics
- Extensions: pgcrypto, btree_gist, citext, pg_trgm (pgaudit optional)
- ENUM types used across all subsequent migrations
"""
from __future__ import annotations

from alembic import op

# Alembic revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ schemas
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0001_create_schemas_and_types.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    op.execute("CREATE SCHEMA IF NOT EXISTS finance")
    op.execute("CREATE SCHEMA IF NOT EXISTS compliance")
    op.execute("CREATE SCHEMA IF NOT EXISTS ops")
    op.execute("CREATE SCHEMA IF NOT EXISTS modules")
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")

    # The alembic version table lives in core (set in env.py).
    # Create the schema before alembic tries to write to it.
    # (alembic will auto-create core.alembic_version on first run)

    # --------------------------------------------------------------- extensions
    # pgcrypto: gen_random_uuid(), digest() for hash chaining
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # btree_gist: required for EXCLUDE USING gist on daterange (ownership_periods)
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    # citext: case-insensitive text for email columns
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    # pg_trgm: trigram indexes for fuzzy party/vendor name search
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # pgaudit: statement-level audit logging (optional — skip if not available)
    op.execute(
        "DO $$ BEGIN CREATE EXTENSION IF NOT EXISTS pgaudit; "
        "EXCEPTION WHEN OTHERS THEN NULL; END $$"
    )

    # ------------------------------------------------------------------- enums
    op.execute(
        "CREATE TYPE core.record_status AS ENUM "
        "('draft', 'active', 'inactive', 'archived')"
    )
    op.execute(
        "CREATE TYPE finance.entry_status AS ENUM "
        "('draft', 'pending_approval', 'posted', 'reversed', 'voided')"
    )
    op.execute(
        "CREATE TYPE finance.entry_direction AS ENUM ('debit', 'credit')"
    )
    op.execute(
        "CREATE TYPE finance.account_type AS ENUM "
        "('asset', 'liability', 'equity', 'income', 'expense')"
    )
    op.execute(
        "CREATE TYPE finance.fund_type AS ENUM "
        "('admin', 'capital_works', 'sinking', 'special_purpose', 'other')"
    )
    op.execute(
        "CREATE TYPE finance.payment_channel AS ENUM "
        "('bpay', 'deft', 'eft', 'card', 'cash', 'cheque', "
        "'bank_transfer', 'aba', 'manual_adjustment')"
    )
    op.execute(
        "CREATE TYPE finance.reconciliation_status AS ENUM "
        "('unmatched', 'candidate', 'matched', 'ignored', 'reversed')"
    )
    op.execute(
        "CREATE TYPE compliance.jurisdiction_code AS ENUM "
        "('ACT', 'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT')"
    )


def downgrade() -> None:
    # Drop ENUM types first (order matters — no dependents yet in this migration)
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0001_create_schemas_and_types.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP TYPE IF EXISTS compliance.jurisdiction_code")
    op.execute("DROP TYPE IF EXISTS finance.reconciliation_status")
    op.execute("DROP TYPE IF EXISTS finance.payment_channel")
    op.execute("DROP TYPE IF EXISTS finance.fund_type")
    op.execute("DROP TYPE IF EXISTS finance.account_type")
    op.execute("DROP TYPE IF EXISTS finance.entry_direction")
    op.execute("DROP TYPE IF EXISTS finance.entry_status")
    op.execute("DROP TYPE IF EXISTS core.record_status")

    # Drop extensions (only if no other objects depend on them)
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS citext")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")

    # Drop schemas (CASCADE to remove all contained objects)
    op.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
    op.execute("DROP SCHEMA IF EXISTS modules CASCADE")
    op.execute("DROP SCHEMA IF EXISTS ops CASCADE")
    op.execute("DROP SCHEMA IF EXISTS compliance CASCADE")
    op.execute("DROP SCHEMA IF EXISTS finance CASCADE")
    op.execute("DROP SCHEMA IF EXISTS core CASCADE")
