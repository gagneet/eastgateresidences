"""0020 — Seed financial_core feature toggles.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-02

Seeds two new feature toggles in core.feature_toggles for the Phase F cutover:

1. financial_core.read_from_postgres
   - Enables Postgres (finance.*) as the primary read source for financial queries
   - Default: FALSE (MongoDB remains primary until cutover)
   - When TRUE, routes read from Postgres; when FALSE, routes read from MongoDB
   - Per-building override: allows gradual rollout

2. financial_core.shadow_read_postgres
   - Enables shadow-read mode (parallel MongoDB + Postgres reads)
   - Default: FALSE
   - When TRUE, every read is done twice; divergences logged to core.shadow_read_divergences
   - Used during pre-cutover validation period

Both toggles start as FALSE and disabled globally. Per-building overrides will be
set by the onboarding wizard as part of the clean-slate cutover process.
"""
from alembic import op
import sqlalchemy as sa

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Insert feature toggles
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0020_seed_financial_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        sa.text("""
                INSERT INTO core.feature_toggles (feature_key,
                                                  feature_name,
                                                  description,
                                                  category,
                                                  is_enabled,
                                                  routes,
                                                  allowed_roles,
                                                  depends_on,
                                                  seeded_version,
                                                  created_at)
                VALUES ('financial_core.read_from_postgres',
                        'Read Financial Data from Postgres',
                        'Enables Postgres (finance.*) as the primary source for financial reads. '
                            'When disabled, MongoDB remains primary. Toggle is per-building; cutover wizard sets building override to TRUE.',
                        'finance',
                        false,
                        ARRAY['/api/finance/trial-balance', '/api/finance/fund-balance', '/api/finance/ledger',
                        '/api/finance/receipts'],
                        ARRAY['super_admin', 'strata_manager'],
                        ARRAY[]::text[],
                        1,
                        now())
                """)
    )

    op.execute(
        sa.text("""
                INSERT INTO core.feature_toggles (feature_key,
                                                  feature_name,
                                                  description,
                                                  category,
                                                  is_enabled,
                                                  routes,
                                                  allowed_roles,
                                                  depends_on,
                                                  seeded_version,
                                                  created_at)
                VALUES ('financial_core.shadow_read_postgres',
                        'Shadow Read Mode (Postgres)',
                        'Enables parallel MongoDB + Postgres reads with divergence detection. '
                            'Used during pre-cutover validation. Set by observability team when ready to monitor Postgres parity.',
                        'finance',
                        false,
                        ARRAY[]::text[],
                        ARRAY['super_admin'],
                        ARRAY[]::text[],
                        1,
                        now())
                """)
    )


def downgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Remove feature toggles
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0020_seed_financial_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute(
        sa.text(
            "DELETE FROM core.feature_toggles WHERE feature_key IN ("
            "'financial_core.read_from_postgres', 'financial_core.shadow_read_postgres'"
            ")"
        )
    )
