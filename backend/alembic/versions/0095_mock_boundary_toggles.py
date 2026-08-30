"""Seed the mock-boundary financial toggles into PostgreSQL.

Revision ID: 0095_mock_boundary_toggles
Revises: 0094_login_ip_public_local
Create Date: 2026-08-26

# @featuretrace:financial-mock-boundary — register the per-building mock/live switches.
# Layer: migration
# Data flow: alembic upgrade head → core.feature_toggles → resolve_feature_toggle()
#            → services/financial_mock_mode.py → DEFT / Stripe / ProviderRegistry / ABA.
# Related: backend/seeds/feature_toggles.py
#          backend/core/toggle_classification.py
#          backend/services/financial_mock_mode.py
#          backend/routers/building_integrations.py

These two rows are the INVERSE of every other protected financial toggle in this
table, and the difference matters when reading the seeded state:

  * the cutover toggles seed is_enabled = FALSE, because for them ON is the
    dangerous direction and per-building promotion turns them on;
  * these seed is_enabled = TRUE, because for them OFF is the dangerous direction
    — off is what points a building at a live financial institution — and
    per-building promotion turns them OFF.

So a blanket "everything false" pass over this table, of the kind that caused
P0.3 on 2026-06-09, would silently connect every building to real money. That is
why they are classified mock_boundary and guarded by
assert_global_disable_allowed rather than assert_global_enable_allowed.

allowed_roles is wider than super_admin by design: returning a building to mock
is the safe direction and is the manager's call. Building scoping is enforced by
the building.integrations.manage capability on the route.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0095_mock_boundary_toggles"
down_revision = "0094_login_ip_public_local"
branch_labels = None
depends_on = None


_MANAGER_ROLES = ["super_admin", "strata_admin", "strata_manager"]

_TOGGLES = [
    {
        "feature_key": "financial_services_mock",
        "feature_name": "Mock Financial Services",
        "description": (
            "While enabled, this building's DEFT/BPAY, Stripe, provider-protocol and "
            "outbound payment (ABA) integrations run against their mock implementations "
            "instead of a live financial institution. Disable per-building only, once that "
            "building is ready to transact for real. Demo Bank is NOT affected — it is a "
            "first-party emulator with its own toggles."
        ),
        "category": "financial",
        "icon": "FlaskConical",
        "routes": [],
        "allowed_roles": _MANAGER_ROLES,
        "depends_on": [],
    },
    {
        "feature_key": "bank_direct_debit_mock",
        "feature_name": "Mock Bank Direct Debit & Transaction History",
        "description": (
            "While enabled, bank direct debit and real transaction-history retrieval are "
            "mocked for this building. Held separately from Mock Financial Services because "
            "these pull customer bank data and can debit an owner directly. No live code "
            "path consumes this yet — the switch exists ahead of the implementation."
        ),
        "category": "financial",
        "icon": "Landmark",
        "routes": [],
        "allowed_roles": _MANAGER_ROLES,
        "depends_on": [],
    },
]


def upgrade() -> None:
    """Insert both mock-boundary toggles, enabled (the safe state)."""
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
            row,
        )
    # Deliberately NOT re-asserting is_enabled on conflict. The cutover migrations
    # force their rows back to FALSE because false is their safe state; forcing TRUE
    # here would undo a deliberate, gated per-building go-live on every re-run.
    # A fresh insert is TRUE; an existing row keeps whatever it was promoted to.


def downgrade() -> None:
    """Remove both rows. Per-building overrides cascade with the toggle."""
    conn = op.get_bind()
    conn.execute(
        text("DELETE FROM core.feature_toggles WHERE feature_key = ANY(:feature_keys)"),
        {"feature_keys": [row["feature_key"] for row in _TOGGLES]},
    )
