"""Seed Demo Bank cutover feature toggles into PostgreSQL.

Revision ID: 0062_seed_demo_bank_feature_toggles
Revises: 0061_bank_tx_provider_name
Create Date: 2026-07-01

# @featuretrace:demo_bank — Register Demo Bank provider/cutover toggles in the PostgreSQL source of truth.
# Layer: migration
# Data flow: alembic upgrade head → core.feature_toggles → require_feature() gates.
# Related: backend/seeds/feature_toggles.py
#          backend/core/toggle_classification.py
#          backend/routers/demo_bank.py
#          backend/routers/bank_feeds.py

Normal deploys do not require --seed-toggles. The protected drift gate runs
after Alembic, so every new feature key that appears in DEFAULT_FEATURES must
also be inserted into core.feature_toggles by a migration. These three keys are
data-path/cutover toggles and intentionally default globally false; per-building
promotion must use core.feature_toggle_overrides after readiness review.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0062_seed_demo_bank_toggles"
down_revision = "0061_bank_tx_provider_name"
branch_labels = None
depends_on = None


_TOGGLES = [
    {
        "feature_key": "demo_bank_feed_enabled",
        "feature_name": "Demo Bank Feed",
        "description": (
            "Stateful bank-feed emulator: routes CSV uploads and Strata Web scrape data "
            "through demo_bank_transactions staging instead of direct ledger writes. "
            "Enable per building only after Demo Bank ingestion readiness is reviewed."
        ),
        "category": "financial",
        "icon": "Database",
        "routes": ["/dashboard/financial/demo-bank"],
        "allowed_roles": ["super_admin"],
        "depends_on": ["financial_integration_layer_v2"],
    },
    {
        "feature_key": "bank_feeds_sync_enabled",
        "feature_name": "Bank Feeds Sync to Postgres",
        "description": (
            "Enables POST /bank-feeds/sync to promote Demo Bank transactions into "
            "finance.bank_transactions. Keep disabled globally; enable per building "
            "only after staged transaction history is complete."
        ),
        "category": "financial",
        "icon": "RefreshCw",
        "routes": [],
        "allowed_roles": ["super_admin"],
        "depends_on": ["demo_bank_feed_enabled"],
    },
    {
        "feature_key": "disable_strata_sync_direct_write",
        "feature_name": "Disable strata_sync Direct Ledger Write",
        "description": (
            "Stops legacy scraper/import direct writes to unit_levy_ledger and "
            "levy_payments. Enable per building only after bank_feeds_sync_enabled "
            "is active and Demo Bank coverage has been verified."
        ),
        "category": "financial",
        "icon": "ShieldOff",
        "routes": [],
        "allowed_roles": ["super_admin"],
        "depends_on": ["bank_feeds_sync_enabled"],
    },
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0062_seed_demo_bank_feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    conn = op.get_bind()
    for row in _TOGGLES:
        conn.execute(
            text(
                """
                INSERT INTO core.feature_toggles
                    (feature_key, feature_name, description, category, is_enabled,
                     icon, routes, allowed_roles, depends_on,
                     seeded_version, created_at, last_modified_at)
                VALUES
                    (:feature_key, :feature_name, :description, :category, FALSE,
                     :icon, CAST(:routes AS text[]), CAST(:allowed_roles AS text[]),
                     CAST(:depends_on AS text[]), 1, NOW(), NOW())
                ON CONFLICT (feature_key) DO UPDATE
                    SET feature_name = EXCLUDED.feature_name,
                        description = EXCLUDED.description,
                        category = EXCLUDED.category,
                        is_enabled = FALSE,
                        icon = EXCLUDED.icon,
                        routes = EXCLUDED.routes,
                        allowed_roles = EXCLUDED.allowed_roles,
                        depends_on = EXCLUDED.depends_on,
                        last_modified_at = NOW()
                """
            ),
            row,
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0062_seed_demo_bank_feature_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    conn = op.get_bind()
    conn.execute(
        text("DELETE FROM core.feature_toggles WHERE feature_key = ANY(:feature_keys)"),
        {"feature_keys": [row["feature_key"] for row in _TOGGLES]},
    )
