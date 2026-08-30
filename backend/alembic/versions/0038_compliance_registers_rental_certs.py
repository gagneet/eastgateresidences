"""0038 — Compliance registers, compliance items, rental certificates + governance read toggle.

Adds tables to the existing `compliance` schema for compliance-register workflows
and rental certificates. Both domains have live data in MongoDB (compliance_registers,
rental_certificates) but no PostgreSQL equivalent as at 0037.

  compliance.compliance_registers  — per-scheme regulatory compliance register
                                     (fire safety, lift, essential services, pool, asbestos …)
  compliance.compliance_items      — individual checklist items within a register
  compliance.rental_certificates   — regulatory certificates (NSW s.184, ACT equivalent)
                                     with immutable PDF hash and 7-year retention

Also seeds the `governance_read_pg_enabled` feature toggle (off by default) which
gates per-domain Postgres read cutover for governance records (was missing from
migration 0031 which seeded only finance-domain toggles).

Statutory retention: ACT Unit Titles (Mgt) Act 2011 ss.115–116 /
NSW Strata Schemes Mgt Act 2015 ss.93–95 — 7-year minimum.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def upgrade() -> None:
    # ------------------------------------------------------------------
    # compliance.compliance_registers
    # One row per regulatory compliance category per scheme.
    # Mirrors the MongoDB `compliance_registers` collection.
    # ------------------------------------------------------------------
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0038_compliance_registers_rental_certs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "compliance_registers",
        sa.Column(
            "register_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        # fire_safety, lift_services, essential_services, pool_safety, asbestos,
        # electrical, plumbing, pest_control, building_defects, other
        sa.Column("register_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        # Jurisdiction context — defaults to ACT for existing buildings
        sa.Column(
            "jurisdiction",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'ACT'"),
        ),
        # Applicable standard / regulation citation (e.g. "AS 1851-2012")
        sa.Column("reference_standard", sa.Text(), nullable=True),
        # Party responsible for arranging compliance (contractor, owner, OC)
        sa.Column(
            "responsible_party_id",
            sa.UUID(),
            sa.ForeignKey("core.parties.party_id"),
            nullable=True,
        ),
        sa.Column("next_due_date", sa.Date(), nullable=True),
        sa.Column("last_completed_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
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
        sa.CheckConstraint(
            "status IN ('open', 'compliant', 'non_compliant', 'overdue', 'waived')",
            name="compliance_registers_status_chk",
        ),
        schema="compliance",
    )

    op.create_index(
        "compliance_registers_scheme_type_idx",
        "compliance_registers",
        ["scheme_id", "register_type"],
        schema="compliance",
    )
    op.create_index(
        "compliance_registers_scheme_due_idx",
        "compliance_registers",
        ["scheme_id", "next_due_date"],
        schema="compliance",
    )
    op.create_index(
        "compliance_registers_tenant_idx",
        "compliance_registers",
        ["tenant_id"],
        schema="compliance",
    )

    # ------------------------------------------------------------------
    # compliance.compliance_items
    # Individual checklist items within a register.
    # Many-to-one with compliance_registers.
    # ------------------------------------------------------------------
    op.create_table(
        "compliance_items",
        sa.Column(
            "item_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column(
            "register_id",
            sa.UUID(),
            sa.ForeignKey("compliance.compliance_registers.register_id"),
            nullable=False,
        ),
        sa.Column("item_description", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_date", sa.Date(), nullable=True),
        sa.Column(
            "completed_by_party_id",
            sa.UUID(),
            sa.ForeignKey("core.parties.party_id"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        # Optional link to a stored document (PDF inspection report, certificate, etc.)
        sa.Column("evidence_document_id", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
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
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'overdue', 'waived', 'not_applicable')",
            name="compliance_items_status_chk",
        ),
        schema="compliance",
    )

    op.create_index(
        "compliance_items_register_idx",
        "compliance_items",
        ["register_id"],
        schema="compliance",
    )
    op.create_index(
        "compliance_items_scheme_status_idx",
        "compliance_items",
        ["scheme_id", "status"],
        schema="compliance",
    )
    op.create_index(
        "compliance_items_tenant_idx",
        "compliance_items",
        ["tenant_id"],
        schema="compliance",
    )

    # ------------------------------------------------------------------
    # compliance.rental_certificates
    # Regulatory certificates issued to lenders/conveyancers.
    # NSW s.184 "Strata Information Certificate" (SIC) + ACT equivalent.
    # PDF hash provides an immutable evidence trail for 7-year retention.
    # ------------------------------------------------------------------
    op.create_table(
        "rental_certificates",
        sa.Column(
            "certificate_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        # Lot is nullable — some certificates cover the whole scheme
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        # rental_certificate, disclosure_statement, section_184_sic, section_109_certificate
        sa.Column("certificate_type", sa.Text(), nullable=False),
        sa.Column(
            "issued_to_party_id",
            sa.UUID(),
            sa.ForeignKey("core.parties.party_id"),
            nullable=False,
        ),
        sa.Column(
            "issued_by_user_id",
            sa.UUID(),
            sa.ForeignKey("core.users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "issued_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        # SHA-256 of the issued PDF — immutable evidence trail
        sa.Column("document_hash", sa.Text(), nullable=False),
        # Reference to object storage (S3 key or internal document_id)
        sa.Column("document_id", sa.Text(), nullable=True),
        # Jurisdiction determines which statutory form is used
        sa.Column(
            "jurisdiction",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'ACT'"),
        ),
        # Exact statutory citation (e.g. "NSW Strata Schemes Management Act 2015 s.184")
        sa.Column("statutory_reference", sa.Text(), nullable=True),
        # Soft-revoke — certificates cannot be hard-deleted (7-year retention)
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "revoked_by",
            sa.UUID(),
            sa.ForeignKey("core.users.user_id"),
            nullable=True,
        ),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="compliance",
    )

    op.create_index(
        "rental_certificates_scheme_type_issued_idx",
        "rental_certificates",
        ["scheme_id", "certificate_type", "issued_at"],
        schema="compliance",
    )
    op.create_index(
        "rental_certificates_lot_idx",
        "rental_certificates",
        ["lot_id"],
        schema="compliance",
    )
    op.create_index(
        "rental_certificates_tenant_idx",
        "rental_certificates",
        ["tenant_id"],
        schema="compliance",
    )

    # ------------------------------------------------------------------
    # RLS — same pattern as 0034 compliance tables (no bypass clause)
    # ------------------------------------------------------------------
    _NEW_TABLES = ["compliance_registers", "compliance_items", "rental_certificates"]
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE compliance.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE compliance.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_compliance_{table} ON compliance.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )

    # ------------------------------------------------------------------
    # Seed governance_read_pg_enabled toggle
    # This toggle was absent from 0031 (which seeded finance-only toggles).
    # Gates per-domain Postgres read cutover for governance records.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO core.feature_toggles
                (feature_key, feature_name, description, category,
                 is_enabled, routes, allowed_roles, depends_on,
                 seeded_version, created_at, last_modified_at)
            VALUES
                ('governance_read_pg_enabled',
                 'Governance PostgreSQL Reads',
                 'Enable PostgreSQL read models for AGM records, EC members, decisions, and by-laws. '
                 'Flip per-scheme after 7 days clean shadow-read parity.',
                 'governance',
                 false,
                 ARRAY['/dashboard/meetings', '/dashboard/governance']::text[],
                 ARRAY['super_admin', 'strata_manager', 'strata_admin']::text[],
                 ARRAY[]::text[],
                 1, now(), now())
            ON CONFLICT (feature_key) DO UPDATE
                SET feature_name = EXCLUDED.feature_name,
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    routes = EXCLUDED.routes,
                    allowed_roles = EXCLUDED.allowed_roles,
                    last_modified_at = now()
            """
        )
    )


def downgrade() -> None:
    # Remove toggle
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0038_compliance_registers_rental_certs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM core.feature_toggles WHERE feature_key = 'governance_read_pg_enabled'"
        )
    )

    # Remove RLS policies and tables (reverse dependency order)
    for table in ["rental_certificates", "compliance_items", "compliance_registers"]:
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation_compliance_{table} ON compliance.{table}"
        )
        op.execute(f"ALTER TABLE compliance.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("rental_certificates_tenant_idx", table_name="rental_certificates", schema="compliance")
    op.drop_index("rental_certificates_lot_idx", table_name="rental_certificates", schema="compliance")
    op.drop_index("rental_certificates_scheme_type_issued_idx", table_name="rental_certificates", schema="compliance")
    op.drop_table("rental_certificates", schema="compliance")

    op.drop_index("compliance_items_tenant_idx", table_name="compliance_items", schema="compliance")
    op.drop_index("compliance_items_scheme_status_idx", table_name="compliance_items", schema="compliance")
    op.drop_index("compliance_items_register_idx", table_name="compliance_items", schema="compliance")
    op.drop_table("compliance_items", schema="compliance")

    op.drop_index("compliance_registers_tenant_idx", table_name="compliance_registers", schema="compliance")
    op.drop_index("compliance_registers_scheme_due_idx", table_name="compliance_registers", schema="compliance")
    op.drop_index("compliance_registers_scheme_type_idx", table_name="compliance_registers", schema="compliance")
    op.drop_table("compliance_registers", schema="compliance")
