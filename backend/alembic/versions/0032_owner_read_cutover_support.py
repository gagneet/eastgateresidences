"""0032 — Owner read cutover support.

Adds:
1. Joint-owner support fields on core.ownership_periods.
2. Structured owner divergence fields on core.shadow_read_divergences.
3. Canonical owner_read_pg_enabled feature toggle.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _seed_owner_read_toggle() -> None:
    """Generated function header.

    Function: _seed_owner_read_toggle
    Path: backend/alembic/versions/0032_owner_read_cutover_support.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO core.feature_toggles
                (feature_key, feature_name, description, category,
                 is_enabled, routes, allowed_roles, depends_on,
                 seeded_version, created_at, last_modified_at)
            VALUES
                (:feature_key, :feature_name, :description, :category,
                 false, :routes, :allowed_roles, :depends_on,
                 1, now(), now())
            ON CONFLICT (feature_key) DO UPDATE
                SET feature_name = EXCLUDED.feature_name,
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    routes = EXCLUDED.routes,
                    allowed_roles = EXCLUDED.allowed_roles,
                    depends_on = EXCLUDED.depends_on,
                    last_modified_at = now()
            """
        ),
        {
            "feature_key": "owner_read_pg_enabled",
            "feature_name": "Owner PostgreSQL Reads",
            "description": "Enable PostgreSQL-backed owner read models and cut over owner-facing building-scoped reads from legacy MongoDB paths.",
            "category": "financial",
            "routes": [
                "/dashboard/owners-units",
                "/letters/send",
                "/proposals/{proposal_id}/vote",
            ],
            "allowed_roles": ["super_admin", "strata_manager", "strata_admin"],
            "depends_on": ["financial_integration_layer_v2"],
        },
    )


def upgrade() -> None:
    # ------------------------------------------------------------------
    # core.ownership_periods — joint-owner support
    # ------------------------------------------------------------------
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0032_owner_read_cutover_support.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.add_column(
        "ownership_periods",
        sa.Column(
            "is_primary_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        schema="core",
    )
    op.add_column(
        "ownership_periods",
        sa.Column(
            "ownership_share",
            sa.Numeric(10, 6),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        schema="core",
    )
    op.execute(
        """
        ALTER TABLE core.ownership_periods
        ADD CONSTRAINT ownership_periods_share_chk
        CHECK (ownership_share > 0 AND ownership_share <= 1.0)
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            SELECT con.conname
            INTO constraint_name
            FROM pg_constraint con
            JOIN pg_class rel
              ON rel.oid = con.conrelid
            JOIN pg_namespace nsp
              ON nsp.oid = rel.relnamespace
            WHERE nsp.nspname = 'core'
              AND rel.relname = 'ownership_periods'
              AND con.contype = 'x'
            LIMIT 1;

            IF constraint_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE core.ownership_periods DROP CONSTRAINT %I',
                    constraint_name
                );
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE core.ownership_periods
        ADD CONSTRAINT ownership_periods_lot_owner_period_excl
        EXCLUDE USING gist (
            lot_id WITH =,
            owner_party_id WITH =,
            daterange(valid_from, COALESCE(valid_to, 'infinity'::date), '[)') WITH &&
        ) WHERE (recorded_to IS NULL)
        """
    )

    # ------------------------------------------------------------------
    # core.shadow_read_divergences — structured owner-cutover fields
    # ------------------------------------------------------------------
    op.add_column(
        "shadow_read_divergences",
        sa.Column("method_name", sa.Text(), nullable=True),
        schema="core",
    )
    op.add_column(
        "shadow_read_divergences",
        sa.Column(
            "diverging_fields",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        schema="core",
    )
    op.add_column(
        "shadow_read_divergences",
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="core",
    )
    op.execute(
        """
        UPDATE core.shadow_read_divergences
        SET method_name = query_type
        WHERE method_name IS NULL
        """
    )
    op.alter_column(
        "shadow_read_divergences",
        "method_name",
        existing_type=sa.Text(),
        nullable=False,
        schema="core",
    )
    op.create_index(
        "shadow_divergences_tenant_method_detected_idx",
        "shadow_read_divergences",
        ["tenant_id", "method_name", "detected_at"],
        schema="core",
        if_not_exists=True,
    )
    op.create_index(
        "shadow_divergences_tenant_detected_idx",
        "shadow_read_divergences",
        ["tenant_id", "detected_at"],
        schema="core",
        if_not_exists=True,
    )

    # ------------------------------------------------------------------
    # feature toggle seed
    # ------------------------------------------------------------------
    _seed_owner_read_toggle()


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0032_owner_read_cutover_support.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM core.feature_toggles
            WHERE feature_key = 'owner_read_pg_enabled'
            """
        )
    )

    op.drop_index(
        "shadow_divergences_tenant_detected_idx",
        schema="core",
        table_name="shadow_read_divergences",
        if_exists=True,
    )
    op.drop_index(
        "shadow_divergences_tenant_method_detected_idx",
        schema="core",
        table_name="shadow_read_divergences",
        if_exists=True,
    )
    op.drop_column("shadow_read_divergences", "is_test_data", schema="core")
    op.drop_column("shadow_read_divergences", "diverging_fields", schema="core")
    op.drop_column("shadow_read_divergences", "method_name", schema="core")

    op.execute(
        """
        ALTER TABLE core.ownership_periods
        DROP CONSTRAINT IF EXISTS ownership_periods_lot_owner_period_excl
        """
    )
    op.execute(
        """
        ALTER TABLE core.ownership_periods
        ADD CONSTRAINT ownership_periods_lot_period_excl
        EXCLUDE USING gist (
            lot_id WITH =,
            daterange(valid_from, COALESCE(valid_to, 'infinity'::date), '[)') WITH &&
        ) WHERE (recorded_to IS NULL)
        """
    )
    op.execute(
        """
        ALTER TABLE core.ownership_periods
        DROP CONSTRAINT IF EXISTS ownership_periods_share_chk
        """
    )
    op.drop_column("ownership_periods", "ownership_share", schema="core")
    op.drop_column("ownership_periods", "is_primary_owner", schema="core")
