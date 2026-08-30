"""Shadow-read coverage table: core.shadow_read_coverage_daily.

Revision ID: 0065_shadow_read_coverage_daily
Revises: 0064_bank_tx_provenance_fields
Create Date: 2026-07-14 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:pg-migration-shadow — Per-route daily comparison-attempt counters, independent of core.shadow_diffs.
# Layer: migration
# Data flow: services/shadow_read_service.py (schedule_shadow_compare) → this table (building-scoped).
# Related: backend/services/shadow_read_service.py
#          backend/services/cutover_status_service.py
#          docs/migration/shadow-correction-updates-plan-p2.md
# Table: core.shadow_read_coverage_daily

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065_shadow_read_coverage_daily"
down_revision = "0064_bank_tx_provenance_fields"
branch_labels = None
depends_on = None

TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # core.shadow_diffs (0049) records mismatches/pg_unavailable/throttled shadow_ok
    # heartbeats only — it cannot reliably distinguish "light real traffic" from "no
    # traffic" for a given route/day. This table is a separate, cheap, atomically-
    # incremented counter of every comparison ATTEMPT (matched or not), so the
    # clean-day gate can require real coverage instead of inferring it from an
    # absence of diff rows (confirmed gap: shadow_ok is throttled to 1/30min/route).
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0065_shadow_read_coverage_daily.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "shadow_read_coverage_daily",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("building_id", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("route", sa.Text, nullable=False),
        sa.Column(
            "coverage_date",
            sa.Date,
            nullable=False,
            comment="Operational day in the building's configured timezone, not UTC",
        ),
        sa.Column("attempted_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("matched_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("mismatch_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("pg_unavailable_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("compare_error_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("timeout_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_capacity_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "building_id", "domain", "route", "coverage_date",
            name="uq_shadow_read_coverage_daily_row",
        ),
        schema="core",
    )

    op.create_index(
        "ix_shadow_read_coverage_daily_lookup",
        "shadow_read_coverage_daily",
        ["building_id", "domain", "coverage_date"],
        schema="core",
    )

    op.execute('ALTER TABLE "core"."shadow_read_coverage_daily" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "core"."shadow_read_coverage_daily" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "shadow_read_coverage_daily_tenant_isolation"
        ON "core"."shadow_read_coverage_daily"
        FOR ALL
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{TENANT_BYPASS_ID}'
        )
        WITH CHECK (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{TENANT_BYPASS_ID}'
        )
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'strataos_user') THEN
                GRANT SELECT, INSERT, UPDATE ON "core"."shadow_read_coverage_daily" TO strataos_user;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0065_shadow_read_coverage_daily.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_table("shadow_read_coverage_daily", schema="core")
