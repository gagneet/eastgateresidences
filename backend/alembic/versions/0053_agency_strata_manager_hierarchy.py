"""0053 — Agency and Strata Manager hierarchy tables.

Revision ID: 0053_agency_sm_hierarchy
Revises: 0052_canonical_bi_schema
Create Date: 2026-06-08

# @featuretrace:bi-analytics — Agency/Strata Manager entity hierarchy for portfolio-scoped BI.
# Layer: migration
# Data flow: core.agencies → core.agency_memberships → core.strata_manager_profiles
#            → core.building_agency_assignments → core.building_manager_assignments
#            → bi_toggle_service → /api/bi/agency/* + /api/bi/manager/* endpoints.
# Related: backend/services/bi_toggle_service.py
#          backend/routers/bi.py
#          backend/services/bi_service.py
# Table: core.agencies, core.agency_memberships, core.strata_manager_profiles,
#        core.building_agency_assignments, core.building_manager_assignments

Adds the Agency / Strata Manager / Building assignment model.

Entity hierarchy:
  Platform
    └── Agency (core.agencies)
          ├── Memberships (core.agency_memberships) → users
          ├── Strata Manager Profiles (core.strata_manager_profiles)
          │     └── Building Assignments (core.building_manager_assignments)
          └── Building Assignments (core.building_agency_assignments) → core.schemes

Self-managed pattern: a Strata Manager can exist without a real agency by
creating a synthetic agency row (is_self_managed = TRUE).  Owners Corporation
without an agency have an EC_member user with strata_manager effective role —
no agency row required.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

revision = "0053_agency_sm_hierarchy"
down_revision = "0052_canonical_bi_schema"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────────────────
# up
# ─────────────────────────────────────────────────────────────────────────────

def upgrade() -> None:
    # ── core.agencies ─────────────────────────────────────────────────────────
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0053_agency_strata_manager_hierarchy.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "agencies",
        sa.Column("agency_id", PG_UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("trading_name", sa.Text(), nullable=True),
        sa.Column("abn", sa.Text(), nullable=True),
        sa.Column("acn", sa.Text(), nullable=True),
        sa.Column("licence_number", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        # is_self_managed = True when a single strata manager creates a synthetic agency
        sa.Column("is_self_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Contact
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("address_line1", sa.Text(), nullable=True),
        sa.Column("address_suburb", sa.Text(), nullable=True),
        sa.Column("address_state", sa.Text(), nullable=True),
        sa.Column("address_postcode", sa.Text(), nullable=True),
        sa.Column("address_country", sa.Text(), nullable=False, server_default=sa.text("'AU'")),
        # Audit
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="core",
    )
    op.create_index("ix_core_agencies_status", "agencies", ["status"], schema="core")

    # ── core.agency_memberships ───────────────────────────────────────────────
    op.create_table(
        "agency_memberships",
        sa.Column("membership_id", PG_UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("agency_id", PG_UUID(as_uuid=True), nullable=False,
                  comment="FK → core.agencies"),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False,
                  comment="FK → core.users"),
        sa.Column("role_in_agency", sa.Text(), nullable=False,
                  comment="principal | manager | admin_staff | compliance_officer"),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("left_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="core",
    )
    op.create_index("ix_core_agency_memberships_agency_id", "agency_memberships",
                    ["agency_id"], schema="core")
    op.create_index("ix_core_agency_memberships_user_id", "agency_memberships",
                    ["user_id"], schema="core")
    op.create_unique_constraint(
        "uq_agency_memberships_agency_user",
        "agency_memberships",
        ["agency_id", "user_id"],
        schema="core",
    )

    # ── core.strata_manager_profiles ──────────────────────────────────────────
    op.create_table(
        "strata_manager_profiles",
        sa.Column("strata_manager_id", PG_UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False, unique=True,
                  comment="FK → core.users — one profile per platform user"),
        sa.Column("agency_id", PG_UUID(as_uuid=True), nullable=True,
                  comment="FK → core.agencies — NULL for self-managed OC managers"),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("licence_number", sa.Text(), nullable=True),
        sa.Column("licence_state", sa.Text(), nullable=True),
        sa.Column("licence_expiry", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="core",
    )
    op.create_index("ix_core_strata_manager_profiles_user_id", "strata_manager_profiles",
                    ["user_id"], schema="core")
    op.create_index("ix_core_strata_manager_profiles_agency_id", "strata_manager_profiles",
                    ["agency_id"], schema="core")

    # ── core.building_agency_assignments ──────────────────────────────────────
    op.create_table(
        "building_agency_assignments",
        sa.Column("assignment_id", PG_UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("scheme_id", PG_UUID(as_uuid=True), nullable=False,
                  comment="FK → core.schemes"),
        sa.Column("agency_id", PG_UUID(as_uuid=True), nullable=False,
                  comment="FK → core.agencies"),
        sa.Column("assignment_type", sa.Text(), nullable=False,
                  server_default=sa.text("'managing_agent'"),
                  comment="managing_agent | caretaker | consulting | self_managed"),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("contract_number", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="core",
    )
    op.create_index("ix_core_baa_scheme_id", "building_agency_assignments",
                    ["scheme_id"], schema="core")
    op.create_index("ix_core_baa_agency_id", "building_agency_assignments",
                    ["agency_id"], schema="core")
    # At most one active managing_agent per building
    op.create_unique_constraint(
        "uq_baa_scheme_agency_type",
        "building_agency_assignments",
        ["scheme_id", "agency_id", "assignment_type"],
        schema="core",
    )

    # ── core.building_manager_assignments ─────────────────────────────────────
    op.create_table(
        "building_manager_assignments",
        sa.Column("assignment_id", PG_UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("scheme_id", PG_UUID(as_uuid=True), nullable=False,
                  comment="FK → core.schemes"),
        sa.Column("strata_manager_id", PG_UUID(as_uuid=True), nullable=False,
                  comment="FK → core.strata_manager_profiles"),
        sa.Column("assignment_role", sa.Text(), nullable=False,
                  server_default=sa.text("'primary'"),
                  comment="primary | secondary | caretaker"),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="core",
    )
    op.create_index("ix_core_bma_scheme_id", "building_manager_assignments",
                    ["scheme_id"], schema="core")
    op.create_index("ix_core_bma_strata_manager_id", "building_manager_assignments",
                    ["strata_manager_id"], schema="core")
    op.create_unique_constraint(
        "uq_bma_scheme_manager_role",
        "building_manager_assignments",
        ["scheme_id", "strata_manager_id", "assignment_role"],
        schema="core",
    )

    # ── analytics: add agency_id column to dim_building ───────────────────────
    # Allow dim_building to carry the agency context for portfolio aggregations.
    op.add_column(
        "dim_building",
        sa.Column("agency_id", PG_UUID(as_uuid=True), nullable=True,
                  comment="FK → core.agencies — populated by ETL from building_agency_assignments"),
        schema="analytics",
    )
    op.add_column(
        "dim_building",
        sa.Column("agency_name", sa.Text(), nullable=True),
        schema="analytics",
    )


# ─────────────────────────────────────────────────────────────────────────────
# down
# ─────────────────────────────────────────────────────────────────────────────

def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0053_agency_strata_manager_hierarchy.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_column("dim_building", "agency_name", schema="analytics")
    op.drop_column("dim_building", "agency_id", schema="analytics")
    op.drop_table("building_manager_assignments", schema="core")
    op.drop_table("building_agency_assignments", schema="core")
    op.drop_table("strata_manager_profiles", schema="core")
    op.drop_table("agency_memberships", schema="core")
    op.drop_table("agencies", schema="core")
