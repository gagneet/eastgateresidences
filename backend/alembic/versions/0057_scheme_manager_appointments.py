"""0057 — Scheme manager appointments and extended EC positions.

Revision ID: 0057_scheme_manager_appointments
Revises: 0056_management_entity_model
Create Date: 2026-06-09

# @featuretrace:ec-internal-manager — EC-appointed internal manager support.
# Layer: migration
# Data flow: core.scheme_manager_appointments → management_hierarchy_service
#            → /api/management-hierarchy/schemes/{id}/appointments
# Related: backend/services/management_hierarchy_service.py
#          backend/routers/management_hierarchy.py
#          backend/models/user.py (ECPosition)
# Table: core.scheme_manager_appointments

Adds:
  1. core.scheme_manager_appointments — tracks any user appointed as manager
     for a specific scheme (EC-internal, independent, agency, volunteer).
  2. CHECK constraint on known appointment_type values.
  3. Unique constraint: one active appointment per (scheme, user, appointment_type).

ECPosition model changes (application-level, not DDL):
  - STRATA_MANAGER and BUILDING_MANAGER added as EC position constants.
  - These positions do NOT grant the strata_manager top-level role automatically;
    they require an explicit appointment row (scheme_manager_appointments) plus
    a permission grant.

Permission rules enforced at application layer (not DDL):
  - ec_internal_strata_manager → building-scoped manager operations only.
  - independent_strata_manager → portfolio of directly assigned buildings.
  - agency_strata_manager      → agency-assigned buildings only.
  - owner_volunteer_manager    → single building, explicitly approved.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0057_scheme_manager_appointments"
down_revision = "0056_management_entity_model"
branch_labels = None
depends_on = None

_VALID_APPOINTMENT_TYPES = (
    "agency_strata_manager",
    "independent_strata_manager",
    "ec_internal_strata_manager",
    "owner_volunteer_manager",
    "building_manager",
    "caretaker",
)


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0057_scheme_manager_appointments.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "scheme_manager_appointments",
        sa.Column(
            "appointment_id",
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
            "user_id",
            PG_UUID(as_uuid=True),
            nullable=False,
            comment="FK → core.users — the appointed manager",
        ),
        sa.Column(
            "management_entity_id",
            PG_UUID(as_uuid=True),
            nullable=False,
            comment="FK → core.management_entities — organisational context",
        ),
        # appointment_type values enforced by check constraint below
        sa.Column("appointment_type", sa.Text(), nullable=False),
        sa.Column(
            "role_title",
            sa.Text(),
            nullable=False,
            comment="Human-readable title e.g. 'Strata Manager', 'Building Manager'",
        ),
        sa.Column(
            "start_date",
            sa.Date(),
            nullable=False,
            server_default=sa.text("CURRENT_DATE"),
        ),
        sa.Column("end_date", sa.Date(), nullable=True),
        # status: active | inactive | terminated | expired
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "appointed_by",
            PG_UUID(as_uuid=True),
            nullable=True,
            comment="FK → core.users — who created the appointment",
        ),
        sa.Column(
            "approved_by_ec",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="TRUE when EC quorum has approved; required before is_active",
        ),
        sa.Column(
            "approval_reference",
            sa.Text(),
            nullable=True,
            comment="Meeting minute reference or motion number",
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
        sa.CheckConstraint(
            "appointment_type IN ('" + "', '".join(_VALID_APPOINTMENT_TYPES) + "')",
            name="ck_sma_appointment_type",
        ),
        schema="core",
    )

    op.create_index(
        "ix_core_smappt_scheme_id",
        "scheme_manager_appointments",
        ["scheme_id"],
        schema="core",
    )
    op.create_index(
        "ix_core_smappt_user_id",
        "scheme_manager_appointments",
        ["user_id"],
        schema="core",
    )
    op.create_index(
        "ix_core_smappt_entity_id",
        "scheme_manager_appointments",
        ["management_entity_id"],
        schema="core",
    )
    op.create_index(
        "ix_core_smappt_status",
        "scheme_manager_appointments",
        ["status"],
        schema="core",
    )
    # One active appointment of each type per (scheme, user)
    op.create_unique_constraint(
        "uq_smappt_scheme_user_type_active",
        "scheme_manager_appointments",
        ["scheme_id", "user_id", "appointment_type", "status"],
        schema="core",
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0057_scheme_manager_appointments.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_table("scheme_manager_appointments", schema="core")
