"""Add secondary_email to core.parties.

Revision ID: 0063_parties_secondary_email
Revises: 0062_seed_demo_bank_toggles
Create Date: 2026-07-02

# @featuretrace:postgres-identity-foundation — Store secondary owner contact email on canonical parties.
# Layer: migration
# Data flow: East Gate Mongo unit owner_email_b repair → core.parties.secondary_email → owner read models.
# Related: backend/scripts/repair_east_gate_owner_emails_pg.py
#          backend/services/owner_read_service.py
"""
from __future__ import annotations

from alembic import op

revision = "0063_parties_secondary_email"
down_revision = "0062_seed_demo_bank_toggles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0063_parties_secondary_email.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        """
        ALTER TABLE core.parties
            ADD COLUMN IF NOT EXISTS secondary_email CITEXT
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS parties_tenant_secondary_email_idx
            ON core.parties (tenant_id, secondary_email)
            WHERE secondary_email IS NOT NULL
        """
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0063_parties_secondary_email.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP INDEX IF EXISTS core.parties_tenant_secondary_email_idx")
    op.execute(
        """
        ALTER TABLE core.parties
            DROP COLUMN IF EXISTS secondary_email
        """
    )
