"""0042 — Phase 1 strata-manager operating-system foundations.

Adds the remaining PostgreSQL schema foundations required for the next StrataOS
operations wave, while keeping runtime behavior unchanged behind feature flags.

New schemas:
  access          — access device lifecycle and audit trail
  ai_assist       — AI assessment / recommendation / review audit surfaces
  sustainability  — building sustainability profile and project planning

Extended schemas:
  ops             — unified cases, tasking, service requests, recurring tasks
  communications  — newsletters, campaigns, drafts, delivery + acknowledgements
  finance         — generic utility bills/readings/anomalies
  compliance      — obligation templates, rule-pack bridge, compliance events
  documents       — versions, grants, retention policies, document audit trail

All new tables are tenant-scoped with RLS via core.current_tenant_id().
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def _uuid_pk() -> sa.Column:
    """Generated function header.

    Function: _uuid_pk
    Path: backend/alembic/versions/0042_phase1_ops_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _tenant_scope_columns() -> list[sa.Column]:
    """Generated function header.

    Function: _tenant_scope_columns
    Path: backend/alembic/versions/0042_phase1_ops_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return [
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
    ]


def _common_columns(*, created_by: bool = False, updated_by: bool = False, soft_delete: bool = False) -> list[sa.Column]:
    """Generated function header.

    Function: _common_columns
    Path: backend/alembic/versions/0042_phase1_ops_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    cols: list[sa.Column] = []
    if created_by:
        cols.append(sa.Column("created_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True))
    if updated_by:
        cols.append(sa.Column("updated_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True))
    if soft_delete:
        cols.append(sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True))
    cols.extend(
        [
            sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
            sa.Column("source_correlation_id", sa.Text(), nullable=True),
            sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        ]
    )
    return cols


def _grant_schema(schema: str) -> None:
    """Generated function header.

    Function: _grant_schema
    Path: backend/alembic/versions/0042_phase1_ops_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        f"""
        DO $$ BEGIN
            GRANT USAGE ON SCHEMA {schema} TO strataos_user;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO strataos_user;
            ALTER DEFAULT PRIVILEGES IN SCHEMA {schema}
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user;
        EXCEPTION WHEN others THEN NULL;
        END $$;
        """
    )


def _enable_rls(schema: str, tables: list[str]) -> None:
    """Generated function header.

    Function: _enable_rls
    Path: backend/alembic/versions/0042_phase1_ops_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table in tables:
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{schema}_{table} ON {schema}.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def _disable_rls(schema: str, tables: list[str]) -> None:
    """Generated function header.

    Function: _disable_rls
    Path: backend/alembic/versions/0042_phase1_ops_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table in tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{schema}_{table} ON {schema}.{table}")
        op.execute(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY")


_OPS_TABLES = [
    "cases",
    "case_events",
    "case_links",
    "task_assignments",
    "task_comments",
    "task_checklists",
    "task_sla_events",
    "task_status_history",
    "service_request_intake_sources",
    "service_requests",
    "vendor_assignments",
    "recurring_task_templates",
]

_DOCUMENTS_TABLES = [
    "document_versions",
    "document_access_grants",
    "document_retention_policies",
    "document_audit_events",
]

_COMPLIANCE_TABLES = [
    "obligation_templates",
    "compliance_events",
]

_FINANCE_TABLES = [
    "utility_bills",
    "utility_usage_readings",
    "utility_anomalies",
]

_ACCESS_TABLES = [
    "access_device_types",
    "access_device_procurement_batches",
    "access_devices",
    "access_device_requests",
    "access_device_issuance",
    "access_device_returns",
    "access_device_deactivation",
    "access_device_audit_events",
]

_COMMUNICATIONS_TABLES = [
    "newsletters",
    "newsletter_sections",
    "newsletter_recipients",
    "communication_campaigns",
    "campaign_audience_segments",
    "communication_drafts",
    "communication_approval_links",
    "communication_delivery_events",
    "communication_acknowledgements",
]

_AI_TABLES = [
    "ai_assessments",
    "ai_recommendations",
    "ai_recommendation_evidence",
    "ai_risk_scores",
    "ai_review_decisions",
    "ai_prompt_audit",
    "ai_redaction_events",
]

_SUSTAINABILITY_TABLES = [
    "building_sustainability_profiles",
    "solar_readiness_assessments",
    "common_area_lighting_assets",
    "lighting_control_recommendations",
    "energy_efficiency_projects",
    "sustainability_project_benefit_cases",
]

_PHASE1_TOGGLES = [
    (
        "ops_case_management_pg_enabled",
        "Ops Case Management PG",
        "Enable the PostgreSQL-backed unified case/task foundation for the strata-manager operating system.",
        "operations",
        ["super_admin"],
        [],
        ["/dashboard/ops/cases"],
        "Briefcase",
    ),
    (
        "communications_campaigns_pg_enabled",
        "Communications Campaigns PG",
        "Enable PostgreSQL-backed newsletters, campaigns, acknowledgements, and campaign drafting workflows.",
        "communications",
        ["super_admin"],
        [],
        ["/dashboard/communications"],
        "Megaphone",
    ),
    (
        "access_device_lifecycle_pg_enabled",
        "Access Device Lifecycle PG",
        "Enable PostgreSQL-backed access device request, issuance, return, and deactivation workflows.",
        "operations",
        ["super_admin", "strata_admin", "strata_manager", "admin_staff", "owner", "tenant", "real_estate_agent"],
        [],
        ["/dashboard/settings?tab=access-devices", "/dashboard/requests?tab=access-control"],
        "KeyRound",
    ),
    (
        "ai_review_panel_enabled",
        "AI Review Panel",
        "Expose tenant-scoped AI assessment, recommendation, and human review panel workflows. No automated high-risk actions are enabled by this toggle alone.",
        "ai",
        ["super_admin"],
        [],
        ["/dashboard/ops/review-panel"],
        "Bot",
    ),
    (
        "sustainability_assessments_pg_enabled",
        "Sustainability Assessments PG",
        "Enable PostgreSQL-backed sustainability profile, lighting, solar-readiness, and project-benefit foundations.",
        "operations",
        ["super_admin"],
        [],
        ["/dashboard/sustainability"],
        "Leaf",
    ),
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0042_phase1_ops_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS access")
    op.execute("CREATE SCHEMA IF NOT EXISTS ai_assist")
    op.execute("CREATE SCHEMA IF NOT EXISTS sustainability")

    # ------------------------------------------------------------------
    # ops.*
    # ------------------------------------------------------------------
    op.create_table(
        "cases",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default=sa.text("'low'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'new'")),
        sa.Column("source_type", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column("related_lot_ids", sa.ARRAY(sa.UUID()), nullable=False, server_default=sa.text("ARRAY[]::uuid[]")),
        sa.Column("related_owner_party_ids", sa.ARRAY(sa.UUID()), nullable=False, server_default=sa.text("ARRAY[]::uuid[]")),
        sa.Column("related_tenant_party_ids", sa.ARRAY(sa.UUID()), nullable=False, server_default=sa.text("ARRAY[]::uuid[]")),
        sa.Column("related_asset_ids", sa.ARRAY(sa.Text()), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("assigned_to", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sla_policy_code", sa.Text(), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=True),
        sa.Column("visibility_scope", sa.Text(), nullable=False, server_default=sa.text("'building'")),
        sa.Column("pii_classification", sa.Text(), nullable=False, server_default=sa.text("'internal'")),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent', 'critical')",
            name="ops_cases_priority_chk",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ops_cases_risk_level_chk",
        ),
        sa.CheckConstraint(
            "status IN ('new', 'triaged', 'waiting_for_info', 'assigned', 'in_progress', "
            "'waiting_for_approval', 'approved', 'rejected', 'scheduled', "
            "'resident_notified', 'completed', 'closed', 'cancelled')",
            name="ops_cases_status_chk",
        ),
        sa.CheckConstraint(
            "visibility_scope IN ('private', 'manager_ec_only', 'affected_residents', 'building')",
            name="ops_cases_visibility_scope_chk",
        ),
        sa.CheckConstraint(
            "pii_classification IN ('none', 'internal', 'restricted', 'sensitive')",
            name="ops_cases_pii_classification_chk",
        ),
        schema="ops",
    )
    op.create_index("ops_cases_scheme_status_idx", "cases", ["scheme_id", "status"], schema="ops")
    op.create_index("ops_cases_scheme_created_idx", "cases", ["scheme_id", "created_at"], schema="ops")
    op.create_index("ops_cases_scheme_assigned_idx", "cases", ["scheme_id", "assigned_to"], schema="ops")
    op.create_index("ops_cases_scheme_due_idx", "cases", ["scheme_id", "due_at"], schema="ops")
    op.create_index("ops_cases_scheme_source_idx", "cases", ["scheme_id", "source_type", "source_id"], schema="ops")

    op.create_table(
        "case_events",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=True),
        sa.Column("event_payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actor_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="ops",
    )
    op.create_index("case_events_case_created_idx", "case_events", ["case_id", "created_at"], schema="ops")

    op.create_table(
        "case_links",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=False),
        sa.Column("linked_case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=True),
        sa.Column("linked_entity_type", sa.Text(), nullable=True),
        sa.Column("linked_entity_id", sa.Text(), nullable=True),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "(linked_case_id IS NOT NULL) OR (linked_entity_type IS NOT NULL AND linked_entity_id IS NOT NULL)",
            name="case_links_target_chk",
        ),
        schema="ops",
    )
    op.create_index("case_links_case_idx", "case_links", ["case_id"], schema="ops")
    op.create_index("case_links_entity_idx", "case_links", ["scheme_id", "linked_entity_type", "linked_entity_id"], schema="ops")

    op.create_table(
        "task_assignments",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=False),
        sa.Column("assigned_to", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=False),
        sa.Column("assigned_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("assignment_type", sa.Text(), nullable=False, server_default=sa.text("'primary'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'assigned'")),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("declined_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("decline_reason", sa.Text(), nullable=True),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "assignment_type IN ('primary', 'secondary', 'reviewer', 'watcher')",
            name="task_assignments_type_chk",
        ),
        sa.CheckConstraint(
            "status IN ('assigned', 'accepted', 'declined', 'completed', 'cancelled')",
            name="task_assignments_status_chk",
        ),
        schema="ops",
    )
    op.create_index("task_assignments_scheme_status_idx", "task_assignments", ["scheme_id", "status"], schema="ops")
    op.create_index("task_assignments_scheme_assignee_idx", "task_assignments", ["scheme_id", "assigned_to"], schema="ops")
    op.create_index("task_assignments_scheme_due_idx", "task_assignments", ["scheme_id", "due_at"], schema="ops")

    op.create_table(
        "task_comments",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=False),
        sa.Column("assignment_id", sa.UUID(), sa.ForeignKey("ops.task_assignments.id"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("pii_classification", sa.Text(), nullable=False, server_default=sa.text("'internal'")),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "pii_classification IN ('none', 'internal', 'restricted', 'sensitive')",
            name="task_comments_pii_classification_chk",
        ),
        schema="ops",
    )
    op.create_index("task_comments_case_created_idx", "task_comments", ["case_id", "created_at"], schema="ops")

    op.create_table(
        "task_checklists",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=False),
        sa.Column("checklist_label", sa.Text(), nullable=True),
        sa.Column("item_text", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'skipped', 'cancelled')",
            name="task_checklists_status_chk",
        ),
        schema="ops",
    )
    op.create_index("task_checklists_case_status_idx", "task_checklists", ["case_id", "status"], schema="ops")

    op.create_table(
        "task_sla_events",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("breached_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "event_type IN ('scheduled', 'warning', 'breached', 'paused', 'resumed', 'resolved')",
            name="task_sla_events_type_chk",
        ),
        schema="ops",
    )
    op.create_index("task_sla_events_case_due_idx", "task_sla_events", ["case_id", "due_at"], schema="ops")

    op.create_table(
        "task_status_history",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("changed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="ops",
    )
    op.create_index("task_status_history_case_changed_idx", "task_status_history", ["case_id", "changed_at"], schema="ops")

    op.create_table(
        "service_request_intake_sources",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("channel_type", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_common_columns(created_by=True, updated_by=True),
        sa.UniqueConstraint("scheme_id", "source_key", name="service_request_intake_sources_scheme_key_ux"),
        sa.CheckConstraint(
            "channel_type IN ('email', 'portal', 'phone', 'onsite', 'cron', 'api', 'manual', 'sensor')",
            name="service_request_intake_sources_channel_chk",
        ),
        schema="ops",
    )
    op.create_index("service_request_intake_sources_scheme_active_idx", "service_request_intake_sources", ["scheme_id", "is_active"], schema="ops")

    op.create_table(
        "service_requests",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=True),
        sa.Column("intake_source_id", sa.UUID(), sa.ForeignKey("ops.service_request_intake_sources.id"), nullable=True),
        sa.Column("request_kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'received'")),
        sa.Column("reported_by_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("asset_reference", sa.Text(), nullable=True),
        sa.Column("current_vendor_id", sa.UUID(), sa.ForeignKey("ops.vendors.vendor_id"), nullable=True),
        sa.Column("disturbance_level", sa.Text(), nullable=False, server_default=sa.text("'low'")),
        sa.Column("preferred_contact_method", sa.Text(), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "status IN ('received', 'triaged', 'quoted', 'scheduled', 'in_progress', 'awaiting_review', 'completed', 'cancelled')",
            name="service_requests_status_chk",
        ),
        sa.CheckConstraint(
            "disturbance_level IN ('low', 'medium', 'high', 'critical')",
            name="service_requests_disturbance_level_chk",
        ),
        schema="ops",
    )
    op.create_index("service_requests_scheme_status_idx", "service_requests", ["scheme_id", "status"], schema="ops")
    op.create_index("service_requests_scheme_created_idx", "service_requests", ["scheme_id", "created_at"], schema="ops")
    op.create_index("service_requests_lot_idx", "service_requests", ["lot_id"], schema="ops")

    op.create_table(
        "vendor_assignments",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("service_request_id", sa.UUID(), sa.ForeignKey("ops.service_requests.id"), nullable=True),
        sa.Column("work_order_id", sa.UUID(), sa.ForeignKey("ops.work_orders.work_order_id"), nullable=True),
        sa.Column("vendor_id", sa.UUID(), sa.ForeignKey("ops.vendors.vendor_id"), nullable=False),
        sa.Column("assignment_status", sa.Text(), nullable=False, server_default=sa.text("'assigned'")),
        sa.Column("assigned_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("assigned_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("response_due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("responded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("response_note", sa.Text(), nullable=True),
        *_common_columns(updated_by=True),
        sa.CheckConstraint(
            "(service_request_id IS NOT NULL) OR (work_order_id IS NOT NULL)",
            name="vendor_assignments_target_chk",
        ),
        sa.CheckConstraint(
            "assignment_status IN ('assigned', 'accepted', 'declined', 'completed', 'cancelled')",
            name="vendor_assignments_status_chk",
        ),
        schema="ops",
    )
    op.create_index("vendor_assignments_scheme_status_idx", "vendor_assignments", ["scheme_id", "assignment_status"], schema="ops")
    op.create_index("vendor_assignments_vendor_idx", "vendor_assignments", ["vendor_id"], schema="ops")
    op.create_index("vendor_assignments_response_due_idx", "vendor_assignments", ["scheme_id", "response_due_at"], schema="ops")

    op.create_table(
        "recurring_task_templates",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("cadence_unit", sa.Text(), nullable=False),
        sa.Column("cadence_interval", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("priority", sa.Text(), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("default_assignee", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("vendor_id", sa.UUID(), sa.ForeignKey("ops.vendors.vendor_id"), nullable=True),
        sa.Column("next_due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "cadence_unit IN ('day', 'week', 'month', 'quarter', 'year')",
            name="recurring_task_templates_cadence_unit_chk",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent', 'critical')",
            name="recurring_task_templates_priority_chk",
        ),
        schema="ops",
    )
    op.create_index("recurring_task_templates_scheme_active_due_idx", "recurring_task_templates", ["scheme_id", "is_active", "next_due_at"], schema="ops")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION ops.assert_case_status_transition()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;

            IF OLD.status = 'new' AND NEW.status = ANY (ARRAY['triaged', 'waiting_for_info', 'assigned', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'triaged' AND NEW.status = ANY (ARRAY['waiting_for_info', 'assigned', 'waiting_for_approval', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'waiting_for_info' AND NEW.status = ANY (ARRAY['triaged', 'assigned', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'assigned' AND NEW.status = ANY (ARRAY['in_progress', 'waiting_for_approval', 'scheduled', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'in_progress' AND NEW.status = ANY (ARRAY['waiting_for_info', 'waiting_for_approval', 'scheduled', 'resident_notified', 'completed', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'waiting_for_approval' AND NEW.status = ANY (ARRAY['approved', 'rejected', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'approved' AND NEW.status = ANY (ARRAY['assigned', 'scheduled', 'resident_notified', 'completed', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'rejected' AND NEW.status = ANY (ARRAY['triaged', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'scheduled' AND NEW.status = ANY (ARRAY['resident_notified', 'in_progress', 'completed', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'resident_notified' AND NEW.status = ANY (ARRAY['in_progress', 'completed', 'cancelled']) THEN RETURN NEW; END IF;
            IF OLD.status = 'completed' AND NEW.status = 'closed' THEN RETURN NEW; END IF;

            RAISE EXCEPTION 'invalid ops.cases status transition % -> %', OLD.status, NEW.status
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS cases_status_transition_guard ON ops.cases")
    op.execute(
        """
        CREATE TRIGGER cases_status_transition_guard
        BEFORE UPDATE OF status ON ops.cases
        FOR EACH ROW
        EXECUTE FUNCTION ops.assert_case_status_transition()
        """
    )

    # ------------------------------------------------------------------
    # documents.*
    # ------------------------------------------------------------------
    op.create_table(
        "document_versions",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.documents.document_id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
        *_common_columns(created_by=True),
        sa.UniqueConstraint("document_id", "version_number", name="document_versions_document_version_ux"),
        schema="documents",
    )
    op.create_index("document_versions_document_idx", "document_versions", ["document_id", "version_number"], schema="documents")

    op.create_table(
        "document_access_grants",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.documents.document_id"), nullable=False),
        sa.Column("grantee_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("grantee_role", sa.Text(), nullable=True),
        sa.Column("permission_level", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_common_columns(created_by=True, updated_by=True),
        sa.CheckConstraint(
            "(grantee_user_id IS NOT NULL) OR (grantee_role IS NOT NULL)",
            name="document_access_grants_grantee_chk",
        ),
        sa.CheckConstraint(
            "permission_level IN ('view', 'download', 'comment', 'edit', 'admin')",
            name="document_access_grants_permission_level_chk",
        ),
        schema="documents",
    )
    op.create_index("document_access_grants_document_idx", "document_access_grants", ["document_id", "permission_level"], schema="documents")

    op.create_table(
        "document_retention_policies",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("retention_class", sa.Text(), nullable=False),
        sa.Column("minimum_years", sa.Integer(), nullable=False),
        sa.Column("legal_basis", sa.Text(), nullable=True),
        sa.Column("applies_to_folder_type", sa.Text(), nullable=True),
        sa.Column("hard_delete_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_common_columns(created_by=True, updated_by=True),
        sa.UniqueConstraint("scheme_id", "retention_class", name="document_retention_policies_scheme_class_ux"),
        schema="documents",
    )

    op.create_table(
        "document_audit_events",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.documents.document_id"), nullable=False),
        sa.Column("version_id", sa.UUID(), sa.ForeignKey("documents.document_versions.id"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("event_payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="documents",
    )
    op.create_index("document_audit_events_document_created_idx", "document_audit_events", ["document_id", "created_at"], schema="documents")

    # ------------------------------------------------------------------
    # compliance.*
    # ------------------------------------------------------------------
    op.create_table(
        "jurisdiction_rule_packs",
        _uuid_pk(),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        sa.Column("pack_code", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("rule_pack_id", sa.UUID(), sa.ForeignKey("compliance.rule_packs.rule_pack_id"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "jurisdiction IN ('ACT', 'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT')",
            name="jurisdiction_rule_packs_jurisdiction_chk",
        ),
        sa.UniqueConstraint("jurisdiction", "pack_code", "version", name="jurisdiction_rule_packs_nat_ux"),
        schema="compliance",
    )
    op.create_index("jurisdiction_rule_packs_active_idx", "jurisdiction_rule_packs", ["jurisdiction", "is_active"], schema="compliance")

    op.create_table(
        "obligation_templates",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("rule_pack_bridge_id", sa.UUID(), sa.ForeignKey("compliance.jurisdiction_rule_packs.id"), nullable=True),
        sa.Column("obligation_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("interval_unit", sa.Text(), nullable=True),
        sa.Column("interval_value", sa.Integer(), nullable=True),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("default_owner_role", sa.Text(), nullable=True),
        sa.Column("evidence_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_common_columns(created_by=True, updated_by=True),
        sa.UniqueConstraint("scheme_id", "obligation_code", name="obligation_templates_scheme_code_ux"),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="obligation_templates_risk_level_chk",
        ),
        schema="compliance",
    )
    op.create_index("obligation_templates_scheme_risk_idx", "obligation_templates", ["scheme_id", "risk_level"], schema="compliance")

    op.create_table(
        "compliance_events",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("register_id", sa.UUID(), sa.ForeignKey("compliance.compliance_registers.register_id"), nullable=True),
        sa.Column("item_id", sa.UUID(), sa.ForeignKey("compliance.compliance_items.item_id"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'logged'")),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("event_payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('logged', 'pending', 'scheduled', 'completed', 'waived', 'cancelled')",
            name="compliance_events_status_chk",
        ),
        schema="compliance",
    )
    op.create_index("compliance_events_scheme_status_idx", "compliance_events", ["scheme_id", "status"], schema="compliance")
    op.create_index("compliance_events_item_idx", "compliance_events", ["item_id"], schema="compliance")

    # ------------------------------------------------------------------
    # finance.*
    # ------------------------------------------------------------------
    op.create_table(
        "utility_bills",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("utility_type", sa.Text(), nullable=False),
        sa.Column("billing_period_start", sa.Date(), nullable=False),
        sa.Column("billing_period_end", sa.Date(), nullable=False),
        sa.Column("usage_quantity", sa.Numeric(14, 3), nullable=True),
        sa.Column("usage_unit", sa.Text(), nullable=True),
        sa.Column("total_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("payment_status", sa.Text(), nullable=False, server_default=sa.text("'unpaid'")),
        sa.Column("provider_name", sa.Text(), nullable=True),
        sa.Column("provider_reference", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("receipt_id", sa.UUID(), sa.ForeignKey("finance.receipts.receipt_id"), nullable=True),
        *_common_columns(created_by=True, updated_by=True),
        sa.CheckConstraint(
            "billing_period_end >= billing_period_start",
            name="utility_bills_period_chk",
        ),
        sa.CheckConstraint(
            "payment_status IN ('unpaid', 'paid', 'partial', 'waived', 'disputed')",
            name="utility_bills_payment_status_chk",
        ),
        sa.CheckConstraint("total_amount_cents >= 0", name="utility_bills_total_amount_positive_chk"),
        schema="finance",
    )
    op.create_index("utility_bills_scheme_status_idx", "utility_bills", ["scheme_id", "payment_status"], schema="finance")
    op.create_index("utility_bills_scheme_period_idx", "utility_bills", ["scheme_id", "billing_period_start"], schema="finance")

    op.create_table(
        "utility_usage_readings",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("utility_bill_id", sa.UUID(), sa.ForeignKey("finance.utility_bills.id"), nullable=True),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("utility_type", sa.Text(), nullable=False),
        sa.Column("meter_identifier", sa.Text(), nullable=True),
        sa.Column("asset_reference", sa.Text(), nullable=True),
        sa.Column("reading_taken_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reading_value", sa.Numeric(14, 3), nullable=False),
        sa.Column("reading_unit", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "source_type IN ('manual', 'import', 'sensor', 'estimated')",
            name="utility_usage_readings_source_type_chk",
        ),
        schema="finance",
    )
    op.create_index("utility_usage_readings_scheme_taken_idx", "utility_usage_readings", ["scheme_id", "reading_taken_at"], schema="finance")
    op.create_index("utility_usage_readings_bill_idx", "utility_usage_readings", ["utility_bill_id"], schema="finance")

    op.create_table(
        "utility_anomalies",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("utility_bill_id", sa.UUID(), sa.ForeignKey("finance.utility_bills.id"), nullable=True),
        sa.Column("reading_id", sa.UUID(), sa.ForeignKey("finance.utility_usage_readings.id"), nullable=True),
        sa.Column("anomaly_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("assigned_to", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        *_common_columns(created_by=True, updated_by=True),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="utility_anomalies_severity_chk",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'triaged', 'review_required', 'in_progress', 'resolved', 'cancelled')",
            name="utility_anomalies_status_chk",
        ),
        schema="finance",
    )
    op.create_index("utility_anomalies_scheme_status_idx", "utility_anomalies", ["scheme_id", "status"], schema="finance")
    op.create_index("utility_anomalies_scheme_assigned_idx", "utility_anomalies", ["scheme_id", "assigned_to"], schema="finance")

    # ------------------------------------------------------------------
    # access.*
    # ------------------------------------------------------------------
    op.create_table(
        "access_device_types",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("type_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("fee_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("deposit_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("requires_return", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lifecycle_policy", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_common_columns(created_by=True, updated_by=True),
        sa.UniqueConstraint("scheme_id", "type_key", name="access_device_types_scheme_key_ux"),
        schema="access",
    )
    op.create_index("access_device_types_scheme_active_idx", "access_device_types", ["scheme_id", "is_active"], schema="access")

    op.create_table(
        "access_device_procurement_batches",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("device_type_id", sa.UUID(), sa.ForeignKey("access.access_device_types.id"), nullable=False),
        sa.Column("supplier_name", sa.Text(), nullable=True),
        sa.Column("batch_reference", sa.Text(), nullable=True),
        sa.Column("ordered_quantity", sa.Integer(), nullable=False),
        sa.Column("received_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'ordered'")),
        sa.Column("ordered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_common_columns(created_by=True, updated_by=True),
        sa.CheckConstraint(
            "status IN ('ordered', 'partially_received', 'received', 'cancelled')",
            name="access_device_procurement_batches_status_chk",
        ),
        schema="access",
    )
    op.create_index("access_device_procurement_batches_scheme_status_idx", "access_device_procurement_batches", ["scheme_id", "status"], schema="access")

    op.create_table(
        "access_devices",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("device_type_id", sa.UUID(), sa.ForeignKey("access.access_device_types.id"), nullable=False),
        sa.Column("procurement_batch_id", sa.UUID(), sa.ForeignKey("access.access_device_procurement_batches.id"), nullable=True),
        sa.Column("serial_number", sa.Text(), nullable=True),
        sa.Column("inventory_code", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'in_stock'")),
        sa.Column("current_holder_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("issued_to_lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("returned_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_common_columns(created_by=True, updated_by=True),
        sa.CheckConstraint(
            "status IN ('in_stock', 'reserved', 'issued', 'lost', 'returned', 'deactivated', 'disposed')",
            name="access_devices_status_chk",
        ),
        schema="access",
    )
    op.create_index("access_devices_scheme_status_idx", "access_devices", ["scheme_id", "status"], schema="access")
    op.execute(
        """
        CREATE UNIQUE INDEX access_devices_scheme_inventory_ux
            ON access.access_devices (scheme_id, inventory_code)
            WHERE inventory_code IS NOT NULL
        """
    )

    op.create_table(
        "access_device_requests",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=True),
        sa.Column("device_type_id", sa.UUID(), sa.ForeignKey("access.access_device_types.id"), nullable=False),
        sa.Column("requester_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("requester_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("request_type", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'requested'")),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=True),
        sa.Column("fee_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("deposit_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("requested_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_common_columns(created_by=True, updated_by=True),
        sa.CheckConstraint(
            "request_type IN ('new_issue', 'reissue', 'replacement', 'return', 'disablement', 'lost', 'stolen')",
            name="access_device_requests_type_chk",
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'verifying', 'awaiting_approval', 'approved', 'rejected', 'allocated', 'issued', 'completed', 'cancelled')",
            name="access_device_requests_status_chk",
        ),
        schema="access",
    )
    op.create_index("access_device_requests_scheme_status_idx", "access_device_requests", ["scheme_id", "status"], schema="access")
    op.create_index("access_device_requests_scheme_due_idx", "access_device_requests", ["scheme_id", "due_at"], schema="access")

    op.create_table(
        "access_device_issuance",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("request_id", sa.UUID(), sa.ForeignKey("access.access_device_requests.id"), nullable=True),
        sa.Column("device_id", sa.UUID(), sa.ForeignKey("access.access_devices.id"), nullable=False),
        sa.Column("issued_to_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("issued_to_lot_id", sa.UUID(), sa.ForeignKey("core.lots.lot_id"), nullable=True),
        sa.Column("issued_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("handoff_method", sa.Text(), nullable=True),
        sa.Column("acknowledgement_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="access",
    )
    op.create_index("access_device_issuance_device_idx", "access_device_issuance", ["device_id"], schema="access")

    op.create_table(
        "access_device_returns",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("issuance_id", sa.UUID(), sa.ForeignKey("access.access_device_issuance.id"), nullable=False),
        sa.Column("device_id", sa.UUID(), sa.ForeignKey("access.access_devices.id"), nullable=False),
        sa.Column("returned_by_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("received_by_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("returned_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("condition_note", sa.Text(), nullable=True),
        sa.Column("refund_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="access",
    )
    op.create_index("access_device_returns_device_idx", "access_device_returns", ["device_id"], schema="access")

    op.create_table(
        "access_device_deactivation",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("device_id", sa.UUID(), sa.ForeignKey("access.access_devices.id"), nullable=False),
        sa.Column("request_id", sa.UUID(), sa.ForeignKey("access.access_device_requests.id"), nullable=True),
        sa.Column("deactivated_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("deactivated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("external_reference", sa.Text(), nullable=True),
        sa.Column("reactivated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="access",
    )
    op.create_index("access_device_deactivation_device_idx", "access_device_deactivation", ["device_id"], schema="access")

    op.create_table(
        "access_device_audit_events",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("device_id", sa.UUID(), sa.ForeignKey("access.access_devices.id"), nullable=True),
        sa.Column("request_id", sa.UUID(), sa.ForeignKey("access.access_device_requests.id"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actor_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="access",
    )
    op.create_index("access_device_audit_events_device_created_idx", "access_device_audit_events", ["device_id", "created_at"], schema="access")
    op.create_index("access_device_audit_events_request_created_idx", "access_device_audit_events", ["request_id", "created_at"], schema="access")

    # ------------------------------------------------------------------
    # communications.*
    # ------------------------------------------------------------------
    op.create_table(
        "newsletters",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("scheduled_for", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'approved', 'scheduled', 'sent', 'cancelled', 'archived')",
            name="newsletters_status_chk",
        ),
        schema="communications",
    )
    op.create_index("newsletters_scheme_status_idx", "newsletters", ["scheme_id", "status"], schema="communications")
    op.create_index("newsletters_scheme_scheduled_idx", "newsletters", ["scheme_id", "scheduled_for"], schema="communications")

    op.create_table(
        "newsletter_sections",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("newsletter_id", sa.UUID(), sa.ForeignKey("communications.newsletters.id"), nullable=False),
        sa.Column("section_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("newsletter_id", "sort_order", name="newsletter_sections_order_ux"),
        schema="communications",
    )

    op.create_table(
        "newsletter_recipients",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("newsletter_id", sa.UUID(), sa.ForeignKey("communications.newsletters.id"), nullable=False),
        sa.Column("party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("recipient_email", sa.Text(), nullable=False),
        sa.Column("recipient_type", sa.Text(), nullable=False),
        sa.Column("delivery_status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "delivery_status IN ('pending', 'queued', 'sent', 'delivered', 'bounced', 'opened', 'acknowledged', 'failed')",
            name="newsletter_recipients_delivery_status_chk",
        ),
        schema="communications",
    )
    op.create_index("newsletter_recipients_newsletter_status_idx", "newsletter_recipients", ["newsletter_id", "delivery_status"], schema="communications")

    op.create_table(
        "communication_campaigns",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("campaign_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("audience_scope", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'approved', 'scheduled', 'sending', 'sent', 'cancelled', 'archived')",
            name="communication_campaigns_status_chk",
        ),
        sa.CheckConstraint(
            "audience_scope IN ('affected_residents', 'owners_only', 'tenants_only', 'ec_only', 'building', 'custom')",
            name="communication_campaigns_audience_scope_chk",
        ),
        schema="communications",
    )
    op.create_index("communication_campaigns_scheme_status_idx", "communication_campaigns", ["scheme_id", "status"], schema="communications")
    op.create_index("communication_campaigns_scheme_scheduled_idx", "communication_campaigns", ["scheme_id", "scheduled_for"], schema="communications")

    op.create_table(
        "campaign_audience_segments",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("campaign_id", sa.UUID(), sa.ForeignKey("communications.communication_campaigns.id"), nullable=False),
        sa.Column("segment_type", sa.Text(), nullable=False),
        sa.Column("segment_definition", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("estimated_recipient_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="communications",
    )
    op.create_index("campaign_audience_segments_campaign_idx", "campaign_audience_segments", ["campaign_id"], schema="communications")

    op.create_table(
        "communication_drafts",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("newsletter_id", sa.UUID(), sa.ForeignKey("communications.newsletters.id"), nullable=True),
        sa.Column("campaign_id", sa.UUID(), sa.ForeignKey("communications.communication_campaigns.id"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("draft_kind", sa.Text(), nullable=False),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default=sa.text("'low'")),
        sa.Column("pii_redacted", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'approved', 'rejected', 'archived')",
            name="communication_drafts_status_chk",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="communication_drafts_risk_level_chk",
        ),
        schema="communications",
    )
    op.create_index("communication_drafts_scheme_status_idx", "communication_drafts", ["scheme_id", "status"], schema="communications")
    op.create_index("communication_drafts_scheme_template_idx", "communication_drafts", ["scheme_id", "is_template"], schema="communications")

    op.create_table(
        "communication_approval_links",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("draft_id", sa.UUID(), sa.ForeignKey("communications.communication_drafts.id"), nullable=True),
        sa.Column("newsletter_id", sa.UUID(), sa.ForeignKey("communications.newsletters.id"), nullable=True),
        sa.Column("campaign_id", sa.UUID(), sa.ForeignKey("communications.communication_campaigns.id"), nullable=True),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=False),
        sa.Column("approval_kind", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "(draft_id IS NOT NULL) OR (newsletter_id IS NOT NULL) OR (campaign_id IS NOT NULL)",
            name="communication_approval_links_target_chk",
        ),
        schema="communications",
    )
    op.create_index("communication_approval_links_approval_idx", "communication_approval_links", ["approval_request_id"], schema="communications")

    op.create_table(
        "communication_delivery_events",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("draft_id", sa.UUID(), sa.ForeignKey("communications.communication_drafts.id"), nullable=True),
        sa.Column("newsletter_id", sa.UUID(), sa.ForeignKey("communications.newsletters.id"), nullable=True),
        sa.Column("campaign_id", sa.UUID(), sa.ForeignKey("communications.communication_campaigns.id"), nullable=True),
        sa.Column("recipient_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("recipient_email", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("event_payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "(draft_id IS NOT NULL) OR (newsletter_id IS NOT NULL) OR (campaign_id IS NOT NULL)",
            name="communication_delivery_events_target_chk",
        ),
        schema="communications",
    )
    op.create_index("communication_delivery_events_newsletter_occurred_idx", "communication_delivery_events", ["newsletter_id", "occurred_at"], schema="communications")
    op.create_index("communication_delivery_events_campaign_occurred_idx", "communication_delivery_events", ["campaign_id", "occurred_at"], schema="communications")

    op.create_table(
        "communication_acknowledgements",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("newsletter_id", sa.UUID(), sa.ForeignKey("communications.newsletters.id"), nullable=True),
        sa.Column("campaign_id", sa.UUID(), sa.ForeignKey("communications.communication_campaigns.id"), nullable=True),
        sa.Column("party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=False),
        sa.Column("acknowledgement_type", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "(newsletter_id IS NOT NULL) OR (campaign_id IS NOT NULL)",
            name="communication_acknowledgements_target_chk",
        ),
        schema="communications",
    )
    op.create_index("communication_acknowledgements_newsletter_party_idx", "communication_acknowledgements", ["newsletter_id", "party_id"], schema="communications")
    op.create_index("communication_acknowledgements_campaign_party_idx", "communication_acknowledgements", ["campaign_id", "party_id"], schema="communications")

    # ------------------------------------------------------------------
    # ai_assist.*
    # ------------------------------------------------------------------
    op.create_table(
        "ai_assessments",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=True),
        sa.Column("assessment_type", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("pii_redacted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'rejected', 'applied', 'cancelled')",
            name="ai_assessments_status_chk",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ai_assessments_risk_level_chk",
        ),
        schema="ai_assist",
    )
    op.create_index("ai_assessments_scheme_status_idx", "ai_assessments", ["scheme_id", "status"], schema="ai_assist")
    op.create_index("ai_assessments_scheme_created_idx", "ai_assessments", ["scheme_id", "created_at"], schema="ai_assist")

    op.create_table(
        "ai_recommendations",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("assessment_id", sa.UUID(), sa.ForeignKey("ai_assist.ai_assessments.id"), nullable=False),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("ops.cases.id"), nullable=True),
        sa.Column("recommendation_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("recommendation_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("proposed_action", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'rejected', 'executed', 'cancelled')",
            name="ai_recommendations_status_chk",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ai_recommendations_risk_level_chk",
        ),
        schema="ai_assist",
    )
    op.create_index("ai_recommendations_assessment_idx", "ai_recommendations", ["assessment_id"], schema="ai_assist")
    op.create_index("ai_recommendations_scheme_status_idx", "ai_recommendations", ["scheme_id", "status"], schema="ai_assist")

    op.create_table(
        "ai_recommendation_evidence",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("recommendation_id", sa.UUID(), sa.ForeignKey("ai_assist.ai_recommendations.id"), nullable=False),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="ai_assist",
    )
    op.create_index("ai_recommendation_evidence_recommendation_idx", "ai_recommendation_evidence", ["recommendation_id"], schema="ai_assist")

    op.create_table(
        "ai_risk_scores",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("assessment_id", sa.UUID(), sa.ForeignKey("ai_assist.ai_assessments.id"), nullable=True),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("risk_category", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(6, 3), nullable=False),
        sa.Column("score_band", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="ai_assist",
    )
    op.create_index("ai_risk_scores_subject_idx", "ai_risk_scores", ["scheme_id", "subject_type", "subject_id"], schema="ai_assist")

    op.create_table(
        "ai_review_decisions",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("assessment_id", sa.UUID(), sa.ForeignKey("ai_assist.ai_assessments.id"), nullable=True),
        sa.Column("recommendation_id", sa.UUID(), sa.ForeignKey("ai_assist.ai_recommendations.id"), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("reviewer_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=False),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "(assessment_id IS NOT NULL) OR (recommendation_id IS NOT NULL)",
            name="ai_review_decisions_target_chk",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected', 'needs_changes', 'escalated')",
            name="ai_review_decisions_decision_chk",
        ),
        schema="ai_assist",
    )

    op.create_table(
        "ai_prompt_audit",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("assessment_id", sa.UUID(), sa.ForeignKey("ai_assist.ai_assessments.id"), nullable=True),
        sa.Column("prompt_template_key", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("redacted_prompt", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("response_hash", sa.Text(), nullable=True),
        sa.Column("token_count_input", sa.Integer(), nullable=True),
        sa.Column("token_count_output", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="ai_assist",
    )
    op.create_index("ai_prompt_audit_assessment_idx", "ai_prompt_audit", ["assessment_id"], schema="ai_assist")

    op.create_table(
        "ai_redaction_events",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("assessment_id", sa.UUID(), sa.ForeignKey("ai_assist.ai_assessments.id"), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("redaction_profile", sa.Text(), nullable=False),
        sa.Column("redacted_fields", sa.ARRAY(sa.Text()), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="ai_assist",
    )
    op.create_index("ai_redaction_events_source_idx", "ai_redaction_events", ["scheme_id", "source_type", "source_id"], schema="ai_assist")

    # ------------------------------------------------------------------
    # sustainability.*
    # ------------------------------------------------------------------
    op.create_table(
        "building_sustainability_profiles",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("overall_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("emissions_baseline_kg", sa.Numeric(18, 3), nullable=True),
        sa.Column("solar_readiness_level", sa.Text(), nullable=True),
        sa.Column("water_efficiency_level", sa.Text(), nullable=True),
        sa.Column("lighting_efficiency_level", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.UniqueConstraint("scheme_id", name="building_sustainability_profiles_scheme_ux"),
        schema="sustainability",
    )

    op.create_table(
        "solar_readiness_assessments",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("profile_id", sa.UUID(), sa.ForeignKey("sustainability.building_sustainability_profiles.id"), nullable=True),
        sa.Column("assessment_status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("roof_condition", sa.Text(), nullable=True),
        sa.Column("switchboard_capacity", sa.Text(), nullable=True),
        sa.Column("metering_readiness", sa.Text(), nullable=True),
        sa.Column("shading_rating", sa.Text(), nullable=True),
        sa.Column("estimated_system_kw", sa.Numeric(10, 2), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "assessment_status IN ('draft', 'review_required', 'approved', 'archived')",
            name="solar_readiness_assessments_status_chk",
        ),
        schema="sustainability",
    )
    op.create_index("solar_readiness_assessments_scheme_status_idx", "solar_readiness_assessments", ["scheme_id", "assessment_status"], schema="sustainability")

    op.create_table(
        "common_area_lighting_assets",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("asset_reference", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("fitting_type", sa.Text(), nullable=True),
        sa.Column("lamp_type", sa.Text(), nullable=True),
        sa.Column("wattage", sa.Numeric(10, 2), nullable=True),
        sa.Column("control_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.UniqueConstraint("scheme_id", "asset_reference", name="common_area_lighting_assets_scheme_asset_ux"),
        schema="sustainability",
    )
    op.create_index("common_area_lighting_assets_scheme_status_idx", "common_area_lighting_assets", ["scheme_id", "status"], schema="sustainability")

    op.create_table(
        "lighting_control_recommendations",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("profile_id", sa.UUID(), sa.ForeignKey("sustainability.building_sustainability_profiles.id"), nullable=True),
        sa.Column("lighting_asset_id", sa.UUID(), sa.ForeignKey("sustainability.common_area_lighting_assets.id"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("recommendation_text", sa.Text(), nullable=False),
        sa.Column("estimated_savings_kwh", sa.Numeric(18, 3), nullable=True),
        sa.Column("estimated_savings_cents", sa.BigInteger(), nullable=True),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default=sa.text("'low'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_request_id", sa.UUID(), sa.ForeignKey("core.approval_requests.approval_request_id"), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="lighting_control_recommendations_risk_level_chk",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'rejected', 'implemented', 'archived')",
            name="lighting_control_recommendations_status_chk",
        ),
        schema="sustainability",
    )
    op.create_index("lighting_control_recommendations_scheme_status_idx", "lighting_control_recommendations", ["scheme_id", "status"], schema="sustainability")

    op.create_table(
        "energy_efficiency_projects",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("profile_id", sa.UUID(), sa.ForeignKey("sustainability.building_sustainability_profiles.id"), nullable=True),
        sa.Column("project_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("capex_estimate_cents", sa.BigInteger(), nullable=True),
        sa.Column("payback_months", sa.Integer(), nullable=True),
        sa.Column("emissions_savings_kg", sa.Numeric(18, 3), nullable=True),
        *_common_columns(created_by=True, updated_by=True, soft_delete=True),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'approved', 'rejected', 'funded', 'in_progress', 'completed', 'archived')",
            name="energy_efficiency_projects_status_chk",
        ),
        schema="sustainability",
    )
    op.create_index("energy_efficiency_projects_scheme_status_idx", "energy_efficiency_projects", ["scheme_id", "status"], schema="sustainability")

    op.create_table(
        "sustainability_project_benefit_cases",
        _uuid_pk(),
        *_tenant_scope_columns(),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("sustainability.energy_efficiency_projects.id"), nullable=False),
        sa.Column("scenario_name", sa.Text(), nullable=False),
        sa.Column("assumptions", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("benefit_summary", sa.Text(), nullable=True),
        sa.Column("npv_cents", sa.BigInteger(), nullable=True),
        sa.Column("roi_percentage", sa.Numeric(10, 3), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source_system", sa.Text(), nullable=False, server_default=sa.text("'strataos'")),
        sa.Column("source_correlation_id", sa.Text(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="sustainability",
    )
    op.create_index("sustainability_project_benefit_cases_project_idx", "sustainability_project_benefit_cases", ["project_id"], schema="sustainability")

    # Grants + RLS
    for schema in ["access", "ai_assist", "sustainability", "ops", "documents", "compliance", "finance", "communications"]:
        _grant_schema(schema)

    _enable_rls("ops", _OPS_TABLES)
    _enable_rls("documents", _DOCUMENTS_TABLES)
    _enable_rls("compliance", _COMPLIANCE_TABLES)
    _enable_rls("finance", _FINANCE_TABLES)
    _enable_rls("access", _ACCESS_TABLES)
    _enable_rls("communications", _COMMUNICATIONS_TABLES)
    _enable_rls("ai_assist", _AI_TABLES)
    _enable_rls("sustainability", _SUSTAINABILITY_TABLES)

    bind = op.get_bind()
    for (
        feature_key,
        feature_name,
        description,
        category,
        allowed_roles,
        depends_on,
        routes,
        icon,
    ) in _PHASE1_TOGGLES:
        bind.execute(
            sa.text(
                """
                INSERT INTO core.feature_toggles
                    (feature_key, feature_name, description, category,
                     is_enabled, routes, allowed_roles, depends_on,
                     seeded_version, created_at, last_modified_at, icon)
                VALUES
                    (:feature_key, :feature_name, :description, :category,
                     false, :routes, :allowed_roles, :depends_on,
                     1, now(), now(), :icon)
                ON CONFLICT (feature_key) DO UPDATE
                    SET feature_name = EXCLUDED.feature_name,
                        description = EXCLUDED.description,
                        category = EXCLUDED.category,
                        routes = EXCLUDED.routes,
                        allowed_roles = EXCLUDED.allowed_roles,
                        depends_on = EXCLUDED.depends_on,
                        icon = EXCLUDED.icon,
                        last_modified_at = now()
                """
            ),
            {
                "feature_key": feature_key,
                "feature_name": feature_name,
                "description": description,
                "category": category,
                "allowed_roles": allowed_roles,
                "depends_on": depends_on,
                "routes": routes,
                "icon": icon,
            },
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0042_phase1_ops_foundation.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM core.feature_toggles WHERE feature_key = ANY(:keys)"),
        {"keys": [toggle[0] for toggle in _PHASE1_TOGGLES]},
    )

    _disable_rls("sustainability", _SUSTAINABILITY_TABLES)
    _disable_rls("ai_assist", _AI_TABLES)
    _disable_rls("communications", _COMMUNICATIONS_TABLES)
    _disable_rls("access", _ACCESS_TABLES)
    _disable_rls("finance", _FINANCE_TABLES)
    _disable_rls("compliance", _COMPLIANCE_TABLES)
    _disable_rls("documents", _DOCUMENTS_TABLES)
    _disable_rls("ops", _OPS_TABLES)

    op.execute("DROP TRIGGER IF EXISTS cases_status_transition_guard ON ops.cases")
    op.execute("DROP FUNCTION IF EXISTS ops.assert_case_status_transition()")

    op.drop_table("sustainability_project_benefit_cases", schema="sustainability")
    op.drop_table("energy_efficiency_projects", schema="sustainability")
    op.drop_table("lighting_control_recommendations", schema="sustainability")
    op.drop_table("common_area_lighting_assets", schema="sustainability")
    op.drop_table("solar_readiness_assessments", schema="sustainability")
    op.drop_table("building_sustainability_profiles", schema="sustainability")

    op.drop_table("ai_redaction_events", schema="ai_assist")
    op.drop_table("ai_prompt_audit", schema="ai_assist")
    op.drop_table("ai_review_decisions", schema="ai_assist")
    op.drop_table("ai_risk_scores", schema="ai_assist")
    op.drop_table("ai_recommendation_evidence", schema="ai_assist")
    op.drop_table("ai_recommendations", schema="ai_assist")
    op.drop_table("ai_assessments", schema="ai_assist")

    op.drop_table("communication_acknowledgements", schema="communications")
    op.drop_table("communication_delivery_events", schema="communications")
    op.drop_table("communication_approval_links", schema="communications")
    op.drop_table("communication_drafts", schema="communications")
    op.drop_table("campaign_audience_segments", schema="communications")
    op.drop_table("communication_campaigns", schema="communications")
    op.drop_table("newsletter_recipients", schema="communications")
    op.drop_table("newsletter_sections", schema="communications")
    op.drop_table("newsletters", schema="communications")

    op.drop_table("access_device_audit_events", schema="access")
    op.drop_table("access_device_deactivation", schema="access")
    op.drop_table("access_device_returns", schema="access")
    op.drop_table("access_device_issuance", schema="access")
    op.drop_table("access_device_requests", schema="access")
    op.execute("DROP INDEX IF EXISTS access.access_devices_scheme_inventory_ux")
    op.drop_table("access_devices", schema="access")
    op.drop_table("access_device_procurement_batches", schema="access")
    op.drop_table("access_device_types", schema="access")

    op.drop_table("utility_anomalies", schema="finance")
    op.drop_table("utility_usage_readings", schema="finance")
    op.drop_table("utility_bills", schema="finance")

    op.drop_table("compliance_events", schema="compliance")
    op.drop_table("obligation_templates", schema="compliance")
    op.drop_table("jurisdiction_rule_packs", schema="compliance")

    op.drop_table("document_audit_events", schema="documents")
    op.drop_table("document_retention_policies", schema="documents")
    op.drop_table("document_access_grants", schema="documents")
    op.drop_table("document_versions", schema="documents")

    op.drop_table("recurring_task_templates", schema="ops")
    op.drop_table("vendor_assignments", schema="ops")
    op.drop_table("service_requests", schema="ops")
    op.drop_table("service_request_intake_sources", schema="ops")
    op.drop_table("task_status_history", schema="ops")
    op.drop_table("task_sla_events", schema="ops")
    op.drop_table("task_checklists", schema="ops")
    op.drop_table("task_comments", schema="ops")
    op.drop_table("task_assignments", schema="ops")
    op.drop_table("case_links", schema="ops")
    op.drop_table("case_events", schema="ops")
    op.drop_table("cases", schema="ops")

    op.execute("DROP SCHEMA IF EXISTS sustainability")
    op.execute("DROP SCHEMA IF EXISTS ai_assist")
    op.execute("DROP SCHEMA IF EXISTS access")
