"""Seed 13 feature toggles missing from core.feature_toggles.

These keys exist in MongoDB (seeds/feature_toggles.py DEFAULT_FEATURES) but
were never inserted into PostgreSQL core.feature_toggles via a migration.
Affected: Dashboard V2, Powerhouse shell (6 keys), BI Analytics Phase 1+2 (6 keys).

Revision ID: 0054_seed_bi_powerhouse_dashboard_toggles
Revises: 0053_agency_sm_hierarchy
Create Date: 2026-06-08
"""

from alembic import op

revision = "0054_seed_feature_toggles"
down_revision = "0053_agency_sm_hierarchy"
branch_labels = None
depends_on = None


_TOGGLES = [
    # ── Dashboard V2 ──────────────────────────────────────────────────────────
    {
        "feature_key": "ft_dashboard_v2",
        "feature_name": "Dashboard V2 — Personal Stake Layout",
        "description": (
            "Activates the dashboard-v2 redesign for both Manager (Building Pulse + "
            "Triage Queue + Since Last Visit) and Owner (Your Standing hero + "
            "Community Pulse + Capital For Me). Shipped behind this toggle so rollback "
            "is a single flag flip."
        ),
        "is_enabled": True,
        "category": "system",
        "icon": "LayoutDashboard",
    },
    # ── Powerhouse foundation shell ───────────────────────────────────────────
    # All disabled globally until each sub-feature passes its readiness gate.
    # Enable per-scheme via core.feature_toggle_overrides.
    {
        "feature_key": "powerhouse_conversations",
        "feature_name": "Powerhouse — Conversation Center",
        "description": (
            "Unified conversation center: shared inbox, thread timelines, internal notes, "
            "participants, watchers, entity links. Shell preview — disabled globally until "
            "PG write path is complete."
        ),
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "MessageSquare",
    },
    {
        "feature_key": "powerhouse_shared_inbox",
        "feature_name": "Powerhouse — Shared Inbox",
        "description": (
            "Building shared email inboxes (building@, committee@, strata@). "
            "Config, inbound intake, outbound drafts. Shell preview — provider is "
            "mock-only until real provider is wired."
        ),
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "Inbox",
    },
    {
        "feature_key": "powerhouse_email_intake",
        "feature_name": "Powerhouse — Email Intake Webhooks",
        "description": (
            "Inbound email webhook processing from external providers. Shell preview — "
            "idempotency key dedup in Mongo, auto-thread mapping not yet wired."
        ),
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "Mail",
    },
    {
        "feature_key": "powerhouse_ai_summary",
        "feature_name": "Powerhouse — AI Summary and Response Draft",
        "description": (
            "AI-assisted thread summary and response draft generation. Always returns "
            "placeholder with confidence=0.0 and requires_human_approval=True. "
            "Real AI model not connected."
        ),
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "Sparkles",
    },
    {
        "feature_key": "powerhouse_workflow_engine",
        "feature_name": "Powerhouse — Workflow Engine Shell",
        "description": (
            "Workflow template listing, instance creation, event appending, step completion, "
            "task assignment. Convert-to-workflow from conversation/message. Shell preview — "
            "no real orchestrator connected."
        ),
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "GitBranch",
    },
    {
        "feature_key": "powerhouse_automation_rules",
        "feature_name": "Powerhouse — Automation Rules Shell",
        "description": (
            "Automation rule run simulation (dry-run only). All runs are "
            "status=simulated and require_human_approval=True. No real rule engine connected."
        ),
        "is_enabled": False,
        "category": "powerhouse",
        "icon": "Zap",
    },
    # ── BI Analytics Phase 1+2 toggles ───────────────────────────────────────
    {
        "feature_key": "bi_analytics_enabled",
        "feature_name": "BI Analytics — Legacy Alias",
        "description": (
            "Backward-compatible alias. Prefer bi_analytics_building_enabled for new configs. "
            "Gates /bi/building/* endpoint reads. Falls back to MongoDB when off. "
            "Resolved by bi_service._toggle_on() and bi_toggle_service."
        ),
        "is_enabled": False,
        "category": "analytics",
        "icon": "BarChart3",
    },
    {
        "feature_key": "bi_analytics_platform_enabled",
        "feature_name": "BI Analytics — Platform Scope (Super Admin)",
        "description": (
            "Enables platform-wide BI analytics for super admins. "
            "Resolution order: building override → agency override → this platform default. "
            "Enables /api/bi/platform/* endpoints and the Platform Analytics page."
        ),
        "is_enabled": False,
        "category": "analytics",
        "icon": "Globe",
    },
    {
        "feature_key": "bi_analytics_agency_enabled",
        "feature_name": "BI Analytics — Agency Portfolio Scope",
        "description": (
            "Enables agency-level portfolio BI for strata_admin (agency principals). "
            "Grants access to /api/bi/agency/{id}/* endpoints across all assigned buildings. "
            "Resolution: checked at agency level before platform default."
        ),
        "is_enabled": False,
        "category": "analytics",
        "icon": "Building2",
    },
    {
        "feature_key": "bi_analytics_building_enabled",
        "feature_name": "BI Analytics — Building Scope",
        "description": (
            "Enables building-level BI analytics for EC members, strata managers, and admins. "
            "Building-specific override — takes precedence over agency and platform defaults. "
            "Enable per-building after ETL parity confirmed via /api/bi/building/{id}/cutover-status."
        ),
        "is_enabled": False,
        "category": "analytics",
        "icon": "BarChart3",
    },
    {
        "feature_key": "bi_portfolio_analytics_enabled",
        "feature_name": "BI Analytics — Portfolio Scope (Strata Manager)",
        "description": (
            "Enables cross-building portfolio BI for strata managers. "
            "Strata managers see only their assigned buildings, never the full platform. "
            "Enables /api/bi/manager/{id}/* endpoints."
        ),
        "is_enabled": False,
        "category": "analytics",
        "icon": "Layers",
    },
    {
        "feature_key": "bi_pg_primary_enabled",
        "feature_name": "BI Analytics — PostgreSQL Primary Mode",
        "description": (
            "Switches BI reads from MongoDB fallback to PostgreSQL analytics schema as primary. "
            "Independent from BI visibility — only enable after /cutover-status passes all checks. "
            "Safe default = False. Per-building override. Does not affect BI page visibility."
        ),
        "is_enabled": False,
        "category": "analytics",
        "icon": "Database",
    },
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0054_seed_bi_powerhouse_dashboard_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text

    conn = op.get_bind()
    for t in _TOGGLES:
        conn.execute(
            text(
                """
                INSERT INTO core.feature_toggles
                    (feature_key, feature_name, description, is_enabled, category,
                     icon, routes, depends_on, allowed_roles)
                VALUES
                    (:feature_key, :feature_name, :description, :is_enabled, :category,
                     :icon, '{}'::text[], '{}'::text[], '{}'::text[])
                ON CONFLICT (feature_key) DO NOTHING
                """
            ),
            {
                "feature_key": t["feature_key"],
                "feature_name": t["feature_name"],
                "description": t["description"],
                "is_enabled": t["is_enabled"],
                "category": t["category"],
                "icon": t["icon"],
            },
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0054_seed_bi_powerhouse_dashboard_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text

    conn = op.get_bind()
    keys = [t["feature_key"] for t in _TOGGLES]
    conn.execute(
        text("DELETE FROM core.feature_toggles WHERE feature_key = ANY(:keys)"),
        {"keys": keys},
    )
