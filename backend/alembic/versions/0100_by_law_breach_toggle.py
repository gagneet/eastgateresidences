"""Seed the by-law breach / disputes register toggle into PostgreSQL.

Revision ID: 0100_by_law_breach_toggle
Revises: 0099_diff_since_indexes
Create Date: 2026-08-27

# @featuretrace:by-law-breach — register the visibility toggle the page and nav gate on.
# Layer: migration
# Data flow: alembic upgrade head -> core.feature_toggles -> resolve_feature_toggle()
#            -> hasFeatureAccess("by_law_breach") -> /community/by-law-breach + nav.
# Related: backend/seeds/feature_toggles.py
#          backend/routers/by_law_breach.py
#          scripts/audits/toggle_drift.py

GAP-OPS-005 shipped the register (router, page, Building Pulse dispute signal) and
added the toggle to backend/seeds/feature_toggles.py — but that seed writes only to
MongoDB. PostgreSQL is the source of truth the admin page and every
require_feature()/hasFeatureAccess() gate read, so the key existed in the seed and in
Mongo while being absent from core.feature_toggles: the page stayed unreachable in
production and AUDIT-13's drift gate failed the deploy ("1 in seed but NOT in live").

Every other PG-only toggle row in this table arrived through a migration for exactly
this reason (see 0095_mock_boundary_toggles). This is that missing migration.

Class: visibility (unclassified in core/toggle_classification.py, which defaults to
VISIBILITY) — unprotected, so seeding it enabled is safe and needs no promotion gate.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0100_by_law_breach_toggle"
down_revision = "0099_diff_since_indexes"
branch_labels = None
depends_on = None


_TOGGLE = {
    "feature_key": "by_law_breach",
    "feature_name": "By-law Breaches & Disputes",
    "description": (
        "Record by-law breaches, issue notices, and keep a tribunal-ready evidence trail"
    ),
    "category": "community",
    "icon": "Gavel",
    "routes": ["/community/by-law-breach"],
    "allowed_roles": [],
    "depends_on": [],
}


def upgrade() -> None:
    """Insert the toggle, enabled — matching the seed's is_enabled=True."""
    conn = op.get_bind()
    conn.execute(
        text(
            """
            INSERT INTO core.feature_toggles
                (feature_key, feature_name, description, category, is_enabled,
                 icon, routes, allowed_roles, depends_on,
                 seeded_version, created_at, last_modified_at)
            VALUES
                (:feature_key, :feature_name, :description, :category, TRUE,
                 :icon, CAST(:routes AS text[]), CAST(:allowed_roles AS text[]),
                 CAST(:depends_on AS text[]), 1, NOW(), NOW())
            ON CONFLICT (feature_key) DO UPDATE
                SET feature_name = EXCLUDED.feature_name,
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    icon = EXCLUDED.icon,
                    routes = EXCLUDED.routes,
                    allowed_roles = EXCLUDED.allowed_roles,
                    depends_on = EXCLUDED.depends_on,
                    last_modified_at = NOW()
            """
        ),
        _TOGGLE,
    )
    # is_enabled is deliberately not re-asserted on conflict: a building that has
    # turned the register off through the admin page keeps that decision on re-run.


def downgrade() -> None:
    """Remove the row. Per-building overrides cascade with the toggle."""
    conn = op.get_bind()
    conn.execute(
        text("DELETE FROM core.feature_toggles WHERE feature_key = :feature_key"),
        {"feature_key": _TOGGLE["feature_key"]},
    )
