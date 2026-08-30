"""Add the activation gate to core.users.

Revision ID: 0096_user_activation_state
Revises: 0095_mock_boundary_toggles
Create Date: 2026-08-27

# @featuretrace:owner-activation — the column that keeps a restored account unusable until claimed.
# Layer: migration
# Data flow: alembic upgrade head → core.users.requires_activation → /auth/login gate
#            → /auth/reset-password clears it (building-scoped).
# Related: backend/routers/auth.py
#          backend/services/owner_activation_service.py
#          tasks/GAP-COMMS-003-outbound-message-queue-and-activation.md

Why a dedicated column rather than reusing is_active/is_approved.

Restoring East Gate's owners from the 2026-08-21 export exposed a split between the
two stores. MongoDB held those 106 accounts as is_active=False, is_approved=False, with
no password_hash — unusable, which was the intent. PostgreSQL held the SAME accounts as
is_active=TRUE, is_approved=TRUE, WITH their pre-purge password hashes. Login resolves
Postgres first, so the Mongo state was decorative: anyone holding an owner's old
password could sign in to an account nobody had claimed.

Overloading is_active would have hidden that. `is_active=False` already means
"deactivated by an administrator" and login answers it with "Account is deactivated",
which is both the wrong message for an unclaimed account and indistinguishable from a
genuine suspension. is_approved means "a manager vetted this person". Neither means
"this account exists but its owner has never set a password on it", so neither can be
read back to tell an operator why someone cannot get in.

Defaults to FALSE so every existing account is unaffected; the backfill that marks
restored owners is a separate, reviewable step rather than a side effect of this DDL.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0096_user_activation_state"
down_revision = "0095_mock_boundary_toggles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE core.users
            ADD COLUMN IF NOT EXISTS requires_activation BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ NULL
    """))
    # Partial index: the only query that reads this column looks for the accounts still
    # waiting, which is a shrinking minority. Indexing the FALSE majority would cost
    # write throughput on every login for no read benefit.
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS users_requires_activation_idx
            ON core.users (tenant_id)
            WHERE requires_activation
    """))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS core.users_requires_activation_idx"))
    op.execute(text("""
        ALTER TABLE core.users
            DROP COLUMN IF EXISTS requires_activation,
            DROP COLUMN IF EXISTS activated_at
    """))
