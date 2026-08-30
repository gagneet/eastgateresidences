"""0033 — Governance schema.

Creates the governance schema with tables required for 7-year compliance
retention:

  governance.agm_records         — AGM/EGM/EC meeting records
  governance.agm_motions         — motions submitted at meetings
  governance.agm_votes           — per-lot vote records
  governance.agm_attendance      — quorum tracking
  governance.ec_members          — EC composition history
  governance.decisions           — formal decision register
  governance.by_laws             — owners corporation rules register
  governance.by_laws_acks        — owner acknowledgment records

These collections exist in MongoDB (agm, ec_members, decisions, by_laws,
by_laws_acknowledgments) but have no PostgreSQL equivalent as at 0032.

Citation correction (2026-07-20, verified against ACT Unit Titles
(Management) Act 2011 republication R25 while building Phase G1's
genesis script): the original docstring here cited "ss.115-116" for the
whole schema and "s.109" specifically for by_laws. Both are wrong —
verified by reading the actual sections. Part 6 div 6.1 (ss.106-112) is
"Owners corporation rules": s.106 defines an owners corporation's rules
as the *default rules* (prescribed by regulation) *as modified by
alternative rules registered under the Land Titles (Unit Titles) Act
1970, s.27/27A* — not a single freeform numbered document. s.109 is
"Breach of rules — rule infringement notice", unrelated to a register.
Part 7 (ss.113-116) is "Owners corporation records" — the *corporate
register* of unit owner/occupier names and correspondence addresses
(s.114) plus notification obligations (s.115) and access rights (s.116)
— it does not mention AGMs, the EC, or rules/by-laws at all; it maps
onto core.parties/core.users/core.ownership_periods (already migrated
in Phase C), not this schema. No specific section citation for
AGM-minutes/EC-composition retention has been verified yet (Schedule 2
appears to govern general-meeting conduct, based on s.100(4) note 2's
"sch 2, s 2.3" cross-reference, but that schedule was not read as part
of this correction) — treat "7-year compliance retention" here as this
project's own documented records-retention policy
(docs: policy_strata_record_retention_australia in project memory),
not as a verified statutory citation, until someone reads Schedule 2.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0033_governance_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS governance")

    op.create_table(
        "agm_records",
        sa.Column("agm_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("meeting_type", sa.Text(), nullable=False, server_default="agm"),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("held_date", sa.Date(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("quorum_met", sa.Boolean(), nullable=True),
        sa.Column("lot_count_present", sa.Integer(), nullable=True),
        sa.Column("minutes_doc_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="scheduled"),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="governance",
    )
    op.create_index("agm_records_scheme_date_idx", "agm_records", ["scheme_id", "scheduled_date"], schema="governance")
    op.create_index("agm_records_tenant_idx", "agm_records", ["tenant_id"], schema="governance")

    op.create_table(
        "agm_motions",
        sa.Column("motion_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("agm_id", sa.UUID(), sa.ForeignKey("governance.agm_records.agm_id"), nullable=False),
        sa.Column("motion_number", sa.Integer(), nullable=False),
        sa.Column("motion_text", sa.Text(), nullable=False),
        sa.Column("sponsor_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column("result_note", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("agm_id", "motion_number", name="agm_motions_agm_number_ux"),
        schema="governance",
    )
    op.create_index("agm_motions_agm_idx", "agm_motions", ["agm_id"], schema="governance")

    op.create_table(
        "agm_votes",
        sa.Column("vote_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("motion_id", sa.UUID(), sa.ForeignKey("governance.agm_motions.motion_id"), nullable=False),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=False),
        sa.Column("voter_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("vote", sa.Text(), nullable=False),
        sa.Column("proxy_held_by_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("voted_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("motion_id", "lot_id", name="agm_votes_motion_lot_ux"),
        sa.CheckConstraint("vote IN ('yes', 'no', 'abstain')", name="agm_votes_vote_chk"),
        schema="governance",
    )
    op.create_index("agm_votes_motion_idx", "agm_votes", ["motion_id"], schema="governance")

    op.create_table(
        "agm_attendance",
        sa.Column("attendance_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("agm_id", sa.UUID(), sa.ForeignKey("governance.agm_records.agm_id"), nullable=False),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=False),
        sa.Column("party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("attendance_type", sa.Text(), nullable=False, server_default="in_person"),
        sa.Column("proxy_held_by_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("agm_id", "lot_id", name="agm_attendance_agm_lot_ux"),
        schema="governance",
    )
    op.create_index("agm_attendance_agm_idx", "agm_attendance", ["agm_id"], schema="governance")

    op.create_table(
        "ec_members",
        sa.Column("ec_member_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=False),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("ec_position", sa.Text(), nullable=False, server_default="member"),
        sa.Column("elected_at_agm_id", sa.UUID(), sa.ForeignKey("governance.agm_records.agm_id"), nullable=True),
        sa.Column("term_start", sa.Date(), nullable=False),
        sa.Column("term_end", sa.Date(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="governance",
    )
    op.create_index("ec_members_scheme_active_idx", "ec_members", ["scheme_id", "term_end"], schema="governance")

    op.create_table(
        "decisions",
        sa.Column("decision_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("decision_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("decision_text", sa.Text(), nullable=False),
        sa.Column("ratified_date", sa.Date(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="governance",
    )
    op.create_index("decisions_scheme_date_idx", "decisions", ["scheme_id", "ratified_date"], schema="governance")

    op.create_table(
        "by_laws",
        sa.Column("by_law_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("by_law_number", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("registered_with_actreg", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("scheme_id", "by_law_number", name="by_laws_scheme_number_ux"),
        schema="governance",
    )
    op.create_index("by_laws_scheme_idx", "by_laws", ["scheme_id"], schema="governance")

    op.create_table(
        "by_laws_acks",
        sa.Column("ack_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("by_law_id", sa.UUID(), sa.ForeignKey("governance.by_laws.by_law_id"), nullable=False),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=False),
        sa.Column("party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("acknowledged_via", sa.Text(), nullable=False, server_default="portal"),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="governance",
    )
    op.create_index("by_laws_acks_by_law_lot_idx", "by_laws_acks", ["by_law_id", "lot_id"], schema="governance")

    op.execute(
        """
        DO $$ BEGIN
            GRANT USAGE ON SCHEMA governance TO strataos_user;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA governance TO strataos_user;
            ALTER DEFAULT PRIVILEGES IN SCHEMA governance
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user;
        EXCEPTION WHEN others THEN NULL;
        END $$;
        """
    )

    _GOVERNANCE_TABLES = [
        "agm_records", "agm_motions", "agm_votes", "agm_attendance",
        "ec_members", "decisions", "by_laws", "by_laws_acks",
    ]
    for table in _GOVERNANCE_TABLES:
        op.execute(f"ALTER TABLE governance.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE governance.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_governance_{table} ON governance.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0033_governance_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _GOVERNANCE_TABLES_REV = [
        "by_laws_acks", "by_laws", "decisions", "ec_members",
        "agm_attendance", "agm_votes", "agm_motions", "agm_records",
    ]
    for table in _GOVERNANCE_TABLES_REV:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_governance_{table} ON governance.{table}")
        op.execute(f"ALTER TABLE governance.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("by_laws_acks", schema="governance")
    op.drop_table("by_laws", schema="governance")
    op.drop_table("decisions", schema="governance")
    op.drop_table("ec_members", schema="governance")
    op.drop_table("agm_attendance", schema="governance")
    op.drop_table("agm_votes", schema="governance")
    op.drop_table("agm_motions", schema="governance")
    op.drop_table("agm_records", schema="governance")
    op.execute("DROP SCHEMA IF EXISTS governance")
