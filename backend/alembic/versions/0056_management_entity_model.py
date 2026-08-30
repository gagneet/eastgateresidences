"""0056 — Unified management entity model.

Revision ID: 0056_management_entity_model
Revises: 0055_enable_bi_analytics_toggles
Create Date: 2026-06-09

# @featuretrace:management-hierarchy — Unified management entity abstraction.
# Layer: migration
# Data flow: core.management_entities → core.scheme_management_assignments
#            → management_hierarchy_service → /api/management-hierarchy/* (building-scoped)
# Related: backend/services/management_hierarchy_service.py
#          backend/routers/management_hierarchy.py
#          backend/alembic/versions/0053_agency_strata_manager_hierarchy.py
# Table: core.management_entities, core.scheme_management_assignments

Introduces a generic management entity layer so all four management models
(agency, self-managed OC, independent manager, unknown/transitional) share
one consistent assignment path.

core.agencies is NOT removed — existing rows continue to work and are
cross-referenced via management_entities.source_agency_id.

Resolution precedence for any feature that needs "who manages this building":
  core.scheme_management_assignments (is_primary=TRUE, status='active')
    └── → core.management_entities (entity_type)
          ├── type='agency'              → cross-ref core.agencies
          ├── type='self_managed_oc'     → is_self_managed=TRUE
          ├── type='independent_manager' → is_independent=TRUE
          └── type='unknown'             → status='pending' placeholder
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0056_management_entity_model"
down_revision = "0055_enable_bi_analytics_toggles"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────────────────
# upgrade
# ─────────────────────────────────────────────────────────────────────────────

def upgrade() -> None:
    # ── core.management_entities ──────────────────────────────────────────────
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0056_management_entity_model.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "management_entities",
        sa.Column(
            "management_entity_id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Soft link to a tenant (NULL = platform-level entity such as an agency)
        sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=True),
        # Discriminator: agency | self_managed_oc | independent_manager |
        #                owners_corporation | platform_demo | unknown
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column("abn", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        # status: active | inactive | pending | archived
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "is_system_generated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="TRUE when auto-created by ensure_scheme_management_hierarchy",
        ),
        sa.Column(
            "is_self_managed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="TRUE for self-managed OC entities",
        ),
        sa.Column(
            "is_independent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="TRUE for independent (sole-trader) strata manager entities",
        ),
        # source: manual | auto_agency_sync | onboarding | migration
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        # Cross-reference to legacy core.agencies row (when entity_type='agency')
        sa.Column(
            "source_agency_id",
            PG_UUID(as_uuid=True),
            nullable=True,
            comment="FK → core.agencies when entity_type='agency'; NULL otherwise",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "archived_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="core",
    )
    op.create_index(
        "ix_core_me_entity_type",
        "management_entities",
        ["entity_type"],
        schema="core",
    )
    op.create_index(
        "ix_core_me_status",
        "management_entities",
        ["status"],
        schema="core",
    )
    op.create_index(
        "ix_core_me_source_agency_id",
        "management_entities",
        ["source_agency_id"],
        schema="core",
        postgresql_where=sa.text("source_agency_id IS NOT NULL"),
    )
    # One active management entity per agency (when synced from core.agencies)
    op.create_unique_constraint(
        "uq_management_entities_agency",
        "management_entities",
        ["source_agency_id"],
        schema="core",
    )

    # ── core.scheme_management_assignments ────────────────────────────────────
    op.create_table(
        "scheme_management_assignments",
        sa.Column(
            "assignment_id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "scheme_id",
            PG_UUID(as_uuid=True),
            nullable=False,
            comment="FK → core.schemes",
        ),
        sa.Column(
            "management_entity_id",
            PG_UUID(as_uuid=True),
            nullable=False,
            comment="FK → core.management_entities",
        ),
        # assignment_type: agency_managed | self_managed | independent_manager |
        #                  ec_internal_manager | caretaker | contractor | unknown
        sa.Column("assignment_type", sa.Text(), nullable=False),
        sa.Column(
            "start_date",
            sa.Date(),
            nullable=False,
            server_default=sa.text("CURRENT_DATE"),
        ),
        sa.Column("end_date", sa.Date(), nullable=True),
        # status: active | inactive | terminated | superseded
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            comment="TRUE = primary management assignment for this scheme",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="core",
    )
    op.create_index(
        "ix_core_sma_scheme_id",
        "scheme_management_assignments",
        ["scheme_id"],
        schema="core",
    )
    op.create_index(
        "ix_core_sma_entity_id",
        "scheme_management_assignments",
        ["management_entity_id"],
        schema="core",
    )
    op.create_index(
        "ix_core_sma_status",
        "scheme_management_assignments",
        ["status"],
        schema="core",
    )
    # Enforce: at most one active primary assignment per scheme
    op.create_unique_constraint(
        "uq_sma_scheme_primary_active",
        "scheme_management_assignments",
        ["scheme_id", "is_primary", "status"],
        schema="core",
    )

    # ── analytics.dim_building: add management_entity_id column ───────────────
    op.add_column(
        "dim_building",
        sa.Column(
            "management_entity_id",
            PG_UUID(as_uuid=True),
            nullable=True,
            comment="FK → core.management_entities (populated by ETL)",
        ),
        schema="analytics",
    )
    op.add_column(
        "dim_building",
        sa.Column("management_entity_type", sa.Text(), nullable=True),
        schema="analytics",
    )
    op.add_column(
        "dim_building",
        sa.Column("management_entity_name", sa.Text(), nullable=True),
        schema="analytics",
    )


# ─────────────────────────────────────────────────────────────────────────────
# downgrade
# ─────────────────────────────────────────────────────────────────────────────

def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0056_management_entity_model.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_column("dim_building", "management_entity_name", schema="analytics")
    op.drop_column("dim_building", "management_entity_type", schema="analytics")
    op.drop_column("dim_building", "management_entity_id", schema="analytics")
    op.drop_table("scheme_management_assignments", schema="core")
    op.drop_table("management_entities", schema="core")
