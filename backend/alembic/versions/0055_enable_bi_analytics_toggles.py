"""Enable BI analytics feature toggles so the dashboard surfaces MongoDB fallback data.

Migration 0054 inserted the BI analytics rows with is_enabled=False (safe default
pending ETL parity confirmation).  The BI service already has a full MongoDB fallback
path, so enabling the toggles causes no data integrity risk and lets super_admins,
strata_managers and EC members see live BI charts sourced from MongoDB until ETL runs.

Keys enabled here:
  bi_analytics_platform_enabled  — Platform scope (super_admin)
  bi_analytics_building_enabled  — Building scope (ec_member, strata_manager, strata_admin)
  bi_analytics_enabled           — Legacy alias (backward compat, caught by _toggle_on())

bi_pg_primary_enabled is intentionally left False — it must only be turned on after
/api/bi/building/{id}/cutover-status passes all parity checks.

Revision ID: 0055_enable_bi_analytics_toggles
Revises: 0054_seed_feature_toggles
Create Date: 2026-06-09
"""

from alembic import op

revision = "0055_enable_bi_analytics_toggles"
down_revision = "0054_seed_feature_toggles"
branch_labels = None
depends_on = None

_ENABLE_KEYS = [
    "bi_analytics_enabled",
    "bi_analytics_platform_enabled",
    "bi_analytics_building_enabled",
    "bi_analytics_agency_enabled",
    "bi_portfolio_analytics_enabled",
]

_DISABLE_KEYS = [
    "bi_pg_primary_enabled",
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0055_enable_bi_analytics_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text

    conn = op.get_bind()
    if _ENABLE_KEYS:
        conn.execute(
            text(
                "UPDATE core.feature_toggles SET is_enabled = TRUE "
                "WHERE feature_key = ANY(:keys)"
            ),
            {"keys": _ENABLE_KEYS},
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0055_enable_bi_analytics_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from sqlalchemy import text

    conn = op.get_bind()
    if _ENABLE_KEYS:
        conn.execute(
            text(
                "UPDATE core.feature_toggles SET is_enabled = FALSE "
                "WHERE feature_key = ANY(:keys)"
            ),
            {"keys": _ENABLE_KEYS},
        )
