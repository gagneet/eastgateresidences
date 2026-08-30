"""Seed financial_pg_reads_bypass_shadow into PostgreSQL core.feature_toggles.

Revision ID: 0080_seed_pg_reads_bypass
Revises: 0079_opening_balance_evidence
Create Date: 2026-08-06

# @featuretrace:cutover-toggle-safety — Register the shadow-soak-waiver toggle in the
#   PostgreSQL source of truth, matching its existing Mongo seed entry.
# Layer: migration
# Data flow: alembic upgrade head -> core.feature_toggles -> finance_route_cutover_service.py
#            (readiness resolver) / require_feature() gates.
# Related: backend/seeds/feature_toggles.py (Mongo seed — already had this key)
#          backend/core/toggle_classification.py (DATA_SOURCE_PRIMARY — never bulk-enable)
#          backend/services/finance_route_cutover_service.py
#          backend/services/cutover_config_service.py

`financial_pg_reads_bypass_shadow` was added to seeds/feature_toggles.py's DEFAULT_FEATURES
(Mongo) but, per 0062's own documented rule ("every new feature key that appears in
DEFAULT_FEATURES must also be inserted into core.feature_toggles by a migration"), no
matching Postgres migration was ever written — found live 2026-08-06 via AUDIT-13
(scripts/audits/toggle_drift.py --strict-protected) reporting it "in seed but NOT in
live" for both stores, which fails the deploy gate. DATA_SOURCE_PRIMARY (protected):
is_enabled is hardcoded FALSE here, matching the Mongo seed's declared default and the
"never bulk-enable" rule — never conditional, never a parameter.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0080_seed_pg_reads_bypass"
down_revision = "0079_opening_balance_evidence"
branch_labels = None
depends_on = None


_TOGGLE = {
    "feature_key": "financial_pg_reads_bypass_shadow",
    "feature_name": "Financial PG Reads — Shadow-Soak Waiver",
    "description": (
        "Serve finance PostgreSQL reads once PG↔source parity is verified out-of-band, "
        "WITHOUT waiting out the 7-day shadow soak. Skips only the soak-duration gate — a "
        "route with any critical shadow diff is still refused. Enable per-building only after "
        "reconciliation confirms the building's data is clean."
    ),
    "category": "financial",
    "icon": "Database",
    "routes": [],
    "allowed_roles": ["super_admin"],
    "depends_on": ["financial_pg_reads_enabled"],
}


def upgrade() -> None:
    conn = op.get_bind()
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
        _TOGGLE,
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text("DELETE FROM core.feature_toggles WHERE feature_key = :feature_key"),
        {"feature_key": _TOGGLE["feature_key"]},
    )
