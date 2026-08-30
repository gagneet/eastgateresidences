"""0034 — Compliance schema extensions.

Adds tables to the existing compliance schema for domains present in live
MongoDB but absent from PostgreSQL as at 0032:

  compliance.insurance_policies   — mandatory building insurance (ACT s.87)
  compliance.data_breach_log      — NDB scheme mandatory reporting
  compliance.whs_incidents        — WHS Act incident register
  compliance.whs_inductions       — worker induction records
  compliance.whs_swms             — Safe Work Method Statements
  compliance.privacy_consents     — Privacy Act consent records
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0034_compliance_extensions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.create_table(
        "insurance_policies",
        sa.Column("policy_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("policy_number", sa.Text(), nullable=False),
        sa.Column("policy_type", sa.Text(), nullable=False),
        sa.Column("insurer_name", sa.Text(), nullable=False),
        sa.Column("premium_cents", sa.BigInteger(), nullable=True),
        sa.Column("coverage_amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("inception_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("expiry_date > inception_date", name="insurance_policies_dates_chk"),
        schema="compliance",
    )
    op.create_index("insurance_policies_scheme_expiry_idx", "insurance_policies", ["scheme_id", "expiry_date"], schema="compliance")
    op.create_index("insurance_policies_tenant_idx", "insurance_policies", ["tenant_id"], schema="compliance")

    op.create_table(
        "data_breach_log",
        sa.Column("breach_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=True),
        sa.Column("breach_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("affected_records", sa.Integer(), nullable=True),
        sa.Column("data_types_affected", sa.ARRAY(sa.Text()), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("reported_to_oaic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("oaic_reference", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("remediation_notes", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="compliance",
    )
    op.create_index("data_breach_log_tenant_date_idx", "data_breach_log", ["tenant_id", "breach_date"], schema="compliance")

    op.create_table(
        "whs_incidents",
        sa.Column("incident_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("incident_date", sa.Date(), nullable=False),
        sa.Column("incident_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("involved_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("injury_status", sa.Text(), nullable=True),
        sa.Column("safework_notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("investigation_status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="compliance",
    )
    op.create_index("whs_incidents_scheme_date_idx", "whs_incidents", ["scheme_id", "incident_date"], schema="compliance")

    op.create_table(
        "whs_inductions",
        sa.Column("induction_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=False),
        sa.Column("induction_type", sa.Text(), nullable=False, server_default="contractor"),
        sa.Column("inducted_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("inducted_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="compliance",
    )
    op.create_index("whs_inductions_scheme_party_idx", "whs_inductions", ["scheme_id", "party_id"], schema="compliance")

    op.create_table(
        "whs_swms",
        sa.Column("swms_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("vendor_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("work_description", sa.Text(), nullable=False),
        sa.Column("high_risk_activities", sa.ARRAY(sa.Text()), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("submitted_on", sa.Date(), nullable=False),
        sa.Column("reviewed_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("reviewed_on", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending_review"),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="compliance",
    )
    op.create_index("whs_swms_scheme_idx", "whs_swms", ["scheme_id", "submitted_on"], schema="compliance")

    op.create_table(
        "privacy_consents",
        sa.Column("consent_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=False),
        sa.Column("consent_type", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("given_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("withdrawn_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Text(), nullable=False, server_default="1.0"),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="compliance",
    )
    op.create_index("privacy_consents_party_type_idx", "privacy_consents", ["party_id", "consent_type"], schema="compliance")
    op.create_index("privacy_consents_scheme_idx", "privacy_consents", ["scheme_id"], schema="compliance")

    _COMPLIANCE_TABLES = [
        "insurance_policies", "data_breach_log", "whs_incidents",
        "whs_inductions", "whs_swms", "privacy_consents",
    ]
    for table in _COMPLIANCE_TABLES:
        op.execute(f"ALTER TABLE compliance.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE compliance.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_compliance_{table} ON compliance.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0034_compliance_extensions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _COMPLIANCE_TABLES_REV = [
        "privacy_consents", "whs_swms", "whs_inductions",
        "whs_incidents", "data_breach_log", "insurance_policies",
    ]
    for table in _COMPLIANCE_TABLES_REV:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_compliance_{table} ON compliance.{table}")
        op.execute(f"ALTER TABLE compliance.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("privacy_consents", schema="compliance")
    op.drop_table("whs_swms", schema="compliance")
    op.drop_table("whs_inductions", schema="compliance")
    op.drop_table("whs_incidents", schema="compliance")
    op.drop_table("data_breach_log", schema="compliance")
    op.drop_table("insurance_policies", schema="compliance")
