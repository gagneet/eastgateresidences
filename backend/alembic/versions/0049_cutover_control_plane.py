"""Prompt 2 — Cutover control plane: domain_cutover_status, shadow_diffs, cutover_audit_log.

Revision ID: 0049_cutover_control_plane
Revises: 0048_powerhouse_id_mapping
Create Date: 2026-06-02 10:00:00.000000
"""

from __future__ import annotations

# @featuretrace:cutover-control-plane — Adds three tables for per-building/domain SOT tracking.
# Layer: migration
# Data flow: cutover admin API → core.domain_cutover_status / core.shadow_diffs / core.cutover_audit_log (building-scoped).
# Related: backend/services/cutover_status_service.py
#          backend/routers/cutover_admin.py
#          docs/architecture/source-of-truth-matrix.md

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049_cutover_control_plane"
down_revision = "0048_powerhouse_id_mapping"
branch_labels = None
depends_on = None

TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"

_CUTOVER_MODES = (
    "mongo_primary",
    "postgres_shadow",
    "postgres_read",
    "postgres_write",
    "mongo_archive",
    "disabled",
)

_READINESS_STATUSES = (
    "unknown",
    "not_started",
    "shadow_active",
    "shadow_passing",
    "shadow_clean",
    "promoted",
    "rolled_back",
    "blocked",
)

_AUDIT_ACTIONS = (
    "entered_shadow",
    "promoted_read",
    "promoted_write",
    "archived_mongo",
    "rolled_back",
    "readiness_checked",
    "shadow_diff_recorded",
)

_SOURCE_VALUES = ("mongo", "postgres", "dual", "none")


def _rls(schema: str, table: str) -> None:
    """Generated function header.

    Function: _rls
    Path: backend/alembic/versions/0049_cutover_control_plane.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(f'ALTER TABLE "{schema}"."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{schema}"."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "{table}_tenant_isolation"
        ON "{schema}"."{table}"
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


def _grant(schema: str) -> None:
    """Generated function header.

    Function: _grant
    Path: backend/alembic/versions/0049_cutover_control_plane.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'strataos_user') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {schema} TO strataos_user';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO strataos_user';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {schema} TO app_user';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_readonly') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {schema} TO app_readonly';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. core.domain_cutover_status
    #    One row per (building_id, domain). Tracks the current mode and
    #    readiness of each domain for a given building.
    # -------------------------------------------------------------------------
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0049_cutover_control_plane.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "domain_cutover_status",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", sa.Text, nullable=True),
        sa.Column("building_id", sa.Text, nullable=False),
        sa.Column(
            "scheme_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Resolved PG UUID for this building; may be NULL if building not yet onboarded to PG",
        ),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("route_group", sa.Text, nullable=True),
        sa.Column(
            "read_source",
            sa.Text,
            nullable=False,
            server_default="mongo",
        ),
        sa.Column(
            "write_source",
            sa.Text,
            nullable=False,
            server_default="mongo",
        ),
        sa.Column(
            "mode",
            sa.Text,
            nullable=False,
            server_default="mongo_primary",
        ),
        sa.Column("toggle_name", sa.Text, nullable=True),
        sa.Column(
            "readiness_status",
            sa.Text,
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("last_readiness_check_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_shadow_diff_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_promoted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("promoted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_mode", sa.Text, nullable=True, comment="Stored for one-step rollback"),
        sa.Column("rollback_available", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("p0_snapshot", postgresql.JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
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
        sa.UniqueConstraint("building_id", "domain", name="uq_cutover_status_building_domain"),
        sa.CheckConstraint(
            "mode IN " + str(_CUTOVER_MODES).replace("[", "(").replace("]", ")"),
            name="ck_cutover_status_mode",
        ),
        sa.CheckConstraint(
            "readiness_status IN " + str(_READINESS_STATUSES).replace("[", "(").replace("]", ")"),
            name="ck_cutover_status_readiness",
        ),
        sa.CheckConstraint(
            "read_source IN " + str(_SOURCE_VALUES).replace("[", "(").replace("]", ")"),
            name="ck_cutover_status_read_source",
        ),
        sa.CheckConstraint(
            "write_source IN " + str(_SOURCE_VALUES).replace("[", "(").replace("]", ")"),
            name="ck_cutover_status_write_source",
        ),
        schema="core",
    )

    op.create_index(
        "ix_cutover_status_building_domain",
        "domain_cutover_status",
        ["building_id", "domain"],
        schema="core",
    )
    op.create_index(
        "ix_cutover_status_mode",
        "domain_cutover_status",
        ["mode"],
        schema="core",
    )
    op.create_index(
        "ix_cutover_status_tenant",
        "domain_cutover_status",
        ["tenant_id"],
        schema="core",
    )

    _rls("core", "domain_cutover_status")

    # -------------------------------------------------------------------------
    # 2. core.shadow_diffs
    #    Records individual divergences caught during shadow-read comparison.
    #    Append-only — never updated, only inserted.
    # -------------------------------------------------------------------------
    op.create_table(
        "shadow_diffs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("building_id", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("route", sa.Text, nullable=True),
        sa.Column(
            "diff_type",
            sa.Text,
            nullable=False,
            comment="e.g. count_mismatch, field_mismatch, missing_record, extra_record",
        ),
        sa.Column("mongo_value", postgresql.JSONB, nullable=True),
        sa.Column("pg_value", postgresql.JSONB, nullable=True),
        sa.Column(
            "divergence_score",
            sa.Float,
            nullable=False,
            server_default=sa.text("1.0"),
            comment="0.0 = identical, 1.0 = completely different",
        ),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="core",
    )

    op.create_index(
        "ix_shadow_diffs_building_domain",
        "shadow_diffs",
        ["building_id", "domain"],
        schema="core",
    )
    op.create_index(
        "ix_shadow_diffs_created_at",
        "shadow_diffs",
        ["building_id", "domain", "created_at"],
        schema="core",
    )
    op.create_index(
        "ix_shadow_diffs_unresolved",
        "shadow_diffs",
        ["building_id", "domain", "resolved"],
        postgresql_where=sa.text("resolved = FALSE"),
        schema="core",
    )

    _rls("core", "shadow_diffs")

    # -------------------------------------------------------------------------
    # 3. core.cutover_audit_log
    #    Immutable append-only record of every promotion and rollback action.
    # -------------------------------------------------------------------------
    op.create_table(
        "cutover_audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("building_id", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("from_mode", sa.Text, nullable=True),
        sa.Column("to_mode", sa.Text, nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "p0_snapshot",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Snapshot of P0 readiness check at time of action",
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "action IN " + str(_AUDIT_ACTIONS).replace("[", "(").replace("]", ")"),
            name="ck_cutover_audit_action",
        ),
        schema="core",
    )

    op.create_index(
        "ix_cutover_audit_building_domain",
        "cutover_audit_log",
        ["building_id", "domain"],
        schema="core",
    )
    op.create_index(
        "ix_cutover_audit_created_at",
        "cutover_audit_log",
        ["building_id", "domain", "created_at"],
        schema="core",
    )

    _rls("core", "cutover_audit_log")
    _grant("core")


def downgrade() -> None:
    # Additive-only migration — downgrade drops only what this migration added.
    # Never removes pre-existing tables or data.
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0049_cutover_control_plane.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute('DROP TABLE IF EXISTS "core"."cutover_audit_log" CASCADE')
    op.execute('DROP TABLE IF EXISTS "core"."shadow_diffs" CASCADE')
    op.execute('DROP TABLE IF EXISTS "core"."domain_cutover_status" CASCADE')
