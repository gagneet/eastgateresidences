"""Seed 7 compliance feature toggles missing from core.feature_toggles.

These keys exist in MongoDB and the DEFAULT_FEATURES seed but were never
inserted into the PostgreSQL core.feature_toggles table.  The UI reads
exclusively from PostgreSQL, so they were invisible to anyone hitting the
admin feature-toggles page.

All 7 are governance-category toggles added in the NSW compliance sprint
(GAP-JUR-NSW-005, GAP-JUR-NSW-013, GAP-GOV-001, GAP-INF-003, GAP-SEC-004).

Revision ID: 0043
Revises: 0042
Create Date: 2026-05-24
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


_TOGGLES = [
    {
        "feature_key": "agm_evoting",
        "feature_name": "AGM Electronic Voting",
        "description": (
            "Enable digital AGM voting with proxy submission and quorum tracking. "
            "NSW proxy cap (SSMA 2015 s.60) enforced via jurisdictional rule engine. "
            "GAP-GOV-001."
        ),
        "is_enabled": True,
        "category": "governance",
    },
    {
        "feature_key": "building_manager_duties",
        "feature_name": "Building Manager Duties Register",
        "description": (
            "Track and manage building manager duty delegations, licence expiry dates, "
            "and insurance certificates as required by SSMA 2015 s.46B. "
            "GAP-JUR-NSW-013. $5,500 penalty for non-compliance."
        ),
        "is_enabled": True,
        "category": "governance",
    },
    {
        "feature_key": "jurisdiction_rules",
        "feature_name": "Jurisdiction Rule Engine",
        "description": (
            "Enable the 4-jurisdiction statutory rule engine (ACT, NSW, VIC, QLD). "
            "Gates proxy cap enforcement, meeting thresholds, levy rules, and "
            "compliance deadline logic. GAP-INF-003."
        ),
        "is_enabled": True,
        "category": "governance",
    },
    {
        "feature_key": "nsw_commission_disclosures",
        "feature_name": "NSW Insurance Commission Disclosures",
        "description": (
            "Manage and store insurance commission disclosure records as required by "
            "SSMA 2015 s.264. Soft-cancel only (7-year retention). "
            "GAP-JUR-NSW. $5,500 penalty for non-compliance."
        ),
        "is_enabled": True,
        "category": "governance",
    },
    {
        "feature_key": "nsw_maintenance_schedule",
        "feature_name": "NSW Initial Maintenance Schedule",
        "description": (
            "Create and manage the initial maintenance schedule for new strata schemes "
            "as required by SSMA 2015 s.80 and Schedule 3. "
            "GAP-JUR-NSW-005. $5,500 penalty. 7-year retention."
        ),
        "is_enabled": True,
        "category": "governance",
    },
    {
        "feature_key": "nsw_strata_hub_returns",
        "feature_name": "NSW Strata Hub Annual Returns",
        "description": (
            "Submit and track annual returns to the NSW Strata Hub as required by "
            "the Strata Schemes Management Regulation 2016. Supports online, portal, "
            "and manual submission methods. GAP-JUR-NSW."
        ),
        "is_enabled": True,
        "category": "governance",
    },
    {
        "feature_key": "privacy_dsar",
        "feature_name": "Privacy & Data Subject Access Requests",
        "description": (
            "Enable Privacy Act 1988 APP compliance tooling: data subject access "
            "requests (DSAR), consent management, and PII audit log. "
            "GAP-SEC-004. Required before national launch."
        ),
        "is_enabled": True,
        "category": "governance",
    },
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0043_seed_compliance_feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    conn = op.get_bind()
    for t in _TOGGLES:
        # routes/depends_on/allowed_roles are text[] — use SQL literal '{}' for empty arrays
        # rather than binding Python lists (asyncpg driver quirk in sync context).
        conn.execute(
            text(
                """
                INSERT INTO core.feature_toggles
                    (feature_key, feature_name, description, is_enabled, category,
                     icon, routes, depends_on, allowed_roles)
                VALUES
                    (:feature_key, :feature_name, :description, :is_enabled, :category,
                     'Shield', '{}'::text[], '{}'::text[], '{}'::text[])
                ON CONFLICT (feature_key) DO NOTHING
                """
            ),
            {
                "feature_key": t["feature_key"],
                "feature_name": t["feature_name"],
                "description": t["description"],
                "is_enabled": t["is_enabled"],
                "category": t["category"],
            },
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0043_seed_compliance_feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text
    conn = op.get_bind()
    keys = [t["feature_key"] for t in _TOGGLES]
    conn.execute(
        text("DELETE FROM core.feature_toggles WHERE feature_key = ANY(:keys)"),
        {"keys": keys},
    )
