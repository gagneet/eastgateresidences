"""0031 — Seed canonical financial cutover toggles.

Introduces the umbrella finance cutover toggle plus the explicit child toggles
used by the PostgreSQL migration. Legacy toggle names remain in the database
for backward compatibility, but new code resolves through these canonical keys.
"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


_TOGGLES = [
    (
        "financial_integration_layer_v2",
        "Financial Integration Layer v2",
        "Umbrella gate for PostgreSQL financial reads/writes and provider-agnostic banking plugins.",
        "financial",
        ["/dashboard/financial/matching"],
        ["super_admin"],
        [],
    ),
    (
        "financial_pg_writes_enabled",
        "Financial PostgreSQL Writes",
        "Route financial write operations through the PostgreSQL financial core instead of legacy MongoDB paths.",
        "financial",
        [],
        ["super_admin", "strata_manager", "strata_admin"],
        ["financial_integration_layer_v2"],
    ),
    (
        "financial_pg_reads_enabled",
        "Financial PostgreSQL Reads",
        "Enable PostgreSQL read models for migrated financial APIs and dashboards.",
        "financial",
        [],
        ["super_admin", "strata_manager", "strata_admin"],
        ["financial_integration_layer_v2"],
    ),
    (
        "financial_shadow_reads_enabled",
        "Financial Shadow Reads",
        "Read MongoDB and PostgreSQL in parallel and log divergences during the finance cutover soak period.",
        "financial",
        [],
        ["super_admin"],
        ["financial_integration_layer_v2"],
    ),
    (
        "bank_integration_abstraction_enabled",
        "Bank Integration Abstraction",
        "Resolve bank providers from Postgres-backed building settings and the provider registry instead of hard-coded mocks.",
        "financial",
        ["/dashboard/financial/matching"],
        ["super_admin"],
        ["financial_integration_layer_v2"],
    ),
    (
        "trust_pg_ledger_enabled",
        "Trust PostgreSQL Ledger",
        "Enable PostgreSQL trust ledger reads and writes for trust-accounting workflows.",
        "financial",
        ["/dashboard/trust-accounting"],
        ["super_admin", "strata_manager", "strata_admin"],
        ["financial_integration_layer_v2"],
    ),
    (
        "trust_reconciliation_pg_enabled",
        "Trust PostgreSQL Reconciliation",
        "Enable PostgreSQL-backed trust reconciliation summaries and statement workflows.",
        "financial",
        ["/dashboard/trust-reconciliation"],
        ["super_admin", "strata_manager", "strata_admin"],
        ["financial_integration_layer_v2", "trust_pg_ledger_enabled"],
    ),
    (
        "external_api_finance_pg_enabled",
        "External API PostgreSQL Finance",
        "Serve migrated external finance API responses from PostgreSQL read models.",
        "financial",
        ["/api/v1/finance/summary", "/api/v1/oc/levies"],
        ["super_admin"],
        ["financial_integration_layer_v2", "financial_pg_reads_enabled"],
    ),
    (
        "onboarding_current_balance_adapters_enabled",
        "Onboarding Current-Balance Adapters",
        "Enable the current-balance onboarding adapters that feed genesis and financial cutover validation.",
        "financial",
        ["/dashboard/admin/financial-year-import"],
        ["super_admin"],
        ["financial_integration_layer_v2"],
    ),
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0031_seed_financial_cutover_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bind = op.get_bind()
    for (
            feature_key,
            feature_name,
            description,
            category,
            routes,
            allowed_roles,
            depends_on,
    ) in _TOGGLES:
        bind.execute(
            sa.text(
                """
                INSERT INTO core.feature_toggles
                    (feature_key, feature_name, description, category,
                     is_enabled, routes, allowed_roles, depends_on,
                     seeded_version, created_at, last_modified_at)
                VALUES
                    (:feature_key, :feature_name, :description, :category,
                     false, :routes, :allowed_roles, :depends_on,
                     1, now(), now())
                ON CONFLICT (feature_key) DO UPDATE
                    SET feature_name = EXCLUDED.feature_name,
                        description = EXCLUDED.description,
                        category = EXCLUDED.category,
                        routes = EXCLUDED.routes,
                        allowed_roles = EXCLUDED.allowed_roles,
                        depends_on = EXCLUDED.depends_on,
                        last_modified_at = now()
                """
            ),
            {
                "feature_key": feature_key,
                "feature_name": feature_name,
                "description": description,
                "category": category,
                "routes": routes,
                "allowed_roles": allowed_roles,
                "depends_on": depends_on,
            },
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0031_seed_financial_cutover_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM core.feature_toggles
            WHERE feature_key = ANY(:feature_keys)
            """
        ),
        {"feature_keys": [toggle[0] for toggle in _TOGGLES]},
    )
