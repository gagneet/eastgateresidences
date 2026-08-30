"""0037 — core.joint_owner_review table.

Stores per-lot manager decisions from the joint-owner parsing review
workflow (tools/parse_joint_owners.py).

Discipline:
  - This table is additive. It does NOT alter core.ownership_periods.
  - Applying a confirmed review appends new ownership_period rows and
    terminates the prior aggregated row — no mutations of existing rows.
  - status flow: pending → confirmed | edited | rejected
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0037_joint_owner_review_table.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "joint_owner_review",
        sa.Column(
            "review_id",
            PG_UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", PG_UUID(as_uuid=False), nullable=False),
        sa.Column("scheme_id", PG_UUID(as_uuid=False), nullable=False),
        sa.Column("lot_id", PG_UUID(as_uuid=False), nullable=False),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("unit_number", sa.Text(), nullable=True),
        # The combined-name string as it exists in core.parties
        sa.Column("original_party_id", PG_UUID(as_uuid=False), nullable=False),
        sa.Column("original_legal_name", sa.Text(), nullable=False),
        # Parser output — array of {name, email, ownership_share, confidence}
        sa.Column("parsed_owners", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Per-name confidence score from the parser (0.0–1.0)
        sa.Column("parser_confidence", sa.Numeric(4, 3), nullable=False),
        # Manager decision state
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        # Manager may override the parsed_owners before confirming
        sa.Column("manager_owners", JSONB(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("decided_by", PG_UUID(as_uuid=False), nullable=True),
        # Set True once --apply has run for this review
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Provenance: IDs of newly created ownership_period rows after apply
        sa.Column("applied_period_ids", JSONB(), nullable=True),
        # Outbox event ID for the ADR-025 restatement event
        sa.Column("restatement_event_id", PG_UUID(as_uuid=False), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'edited', 'rejected')",
            name="joint_owner_review_status_chk",
        ),
        schema="core",
    )

    op.create_index(
        "joint_owner_review_scheme_status_idx",
        "joint_owner_review",
        ["scheme_id", "status"],
        schema="core",
    )
    op.create_index(
        "joint_owner_review_lot_idx",
        "joint_owner_review",
        ["lot_id"],
        schema="core",
        unique=True,
    )

    # RLS — same tenant-isolation pattern as other core.* tables
    op.execute("ALTER TABLE core.joint_owner_review ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.joint_owner_review FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_joint_owner_review ON core.joint_owner_review "
        "USING (tenant_id = core.current_tenant_id() "
        "       OR core.current_tenant_id() = '00000000-0000-0000-0000-000000000000'::uuid)"
    )

    op.execute(
        """
        DO $$ BEGIN
            GRANT SELECT, INSERT, UPDATE ON core.joint_owner_review TO strataos_user;
        EXCEPTION WHEN others THEN NULL;
        END $$;
        """
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0037_joint_owner_review_table.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation_joint_owner_review ON core.joint_owner_review")
    op.execute("ALTER TABLE core.joint_owner_review DISABLE ROW LEVEL SECURITY")
    op.drop_index("joint_owner_review_lot_idx", table_name="joint_owner_review", schema="core")
    op.drop_index("joint_owner_review_scheme_status_idx", table_name="joint_owner_review", schema="core")
    op.drop_table("joint_owner_review", schema="core")
