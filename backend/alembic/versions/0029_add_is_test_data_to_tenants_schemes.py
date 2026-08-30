"""0029 — Add ``is_test_data`` flag to core.tenants and core.schemes.

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-03

Lets the platform structurally distinguish *test* data (rows produced by
backend integration tests against the live Postgres) from *real* tenants
and schemes (production + demo). Without this flag the building selector
shown to super-admins after login surfaces leftover test rows whenever
a test crashes mid-run, gets sigkilled, or hits an FK that the per-test
cleanup fixture didn't anticipate (the `try: ... except Exception: pass`
swallows the failure).

Two columns are added:

- ``core.tenants.is_test_data``  — TRUE for rows inserted by automated
  test fixtures only.
- ``core.schemes.is_test_data``  — TRUE for rows inserted by automated
  test fixtures only.

Both default FALSE; existing real rows are unaffected.

The repo helpers (``list_all_active_schemes``, ``list_schemes_for_user``,
``get_scheme_by_id``, ``get_scheme_by_number``) filter ``is_test_data``
on read so production callers never see them. The ``conftest`` sweeper
hook truncates them at the end of every pytest session.

Existing leaked rows are flagged in this migration based on their name
prefix so the SA selector goes clean immediately after deploy:

- core.schemes  WHERE scheme_name ILIKE 'E2E %' OR scheme_name ILIKE 'Test %'
- core.tenants  WHERE tenant_name ILIKE 'E2E %' OR tenant_name ILIKE 'Test %'
                  OR tenant_name = 'E2E SA Tenant'

Real production tenants/schemes never start with those prefixes — see
``backend/seeds/buildings.py`` and ``seed_demo_building.py`` for the
real naming convention.
"""
from alembic import op

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────
    # 1. Add columns
    # ──────────────────────────────────────────────────────────────────
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0029_add_is_test_data_to_tenants_schemes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               ALTER TABLE core.tenants
                   ADD COLUMN IF NOT EXISTS is_test_data BOOLEAN NOT NULL DEFAULT FALSE
               """)
    op.execute("""
               ALTER TABLE core.schemes
                   ADD COLUMN IF NOT EXISTS is_test_data BOOLEAN NOT NULL DEFAULT FALSE
               """)

    # ──────────────────────────────────────────────────────────────────
    # 2. Partial indexes — speed up the "real data only" common case
    # ──────────────────────────────────────────────────────────────────
    op.execute("""
               CREATE INDEX IF NOT EXISTS schemes_real_active_idx
                   ON core.schemes (status)
                   WHERE is_test_data = FALSE
               """)
    op.execute("""
               CREATE INDEX IF NOT EXISTS tenants_real_active_idx
                   ON core.tenants (status)
                   WHERE is_test_data = FALSE
               """)

    # ──────────────────────────────────────────────────────────────────
    # 3. Backfill — flag known leftover test rows
    # ──────────────────────────────────────────────────────────────────
    # Schemes seeded by tests/backend/test_onboarding_e2e.py and
    # tests/backend/test_sm_organisations.py. Both files use ``E2E``
    # or ``Test`` prefixes deliberately. Real tenants/schemes never use
    # those prefixes (see seeds/buildings.py + seed_demo_building.py).
    op.execute("""
               UPDATE core.schemes
               SET is_test_data = TRUE
               WHERE scheme_name ILIKE 'E2E %'
            OR scheme_name ILIKE 'Test %'
            OR scheme_number LIKE 'E2E-%'
            OR scheme_number LIKE 'TEST-%'
            OR scheme_number IN ('X', 'X-403', 'E2E-EXTRA')
               """)
    op.execute("""
               UPDATE core.tenants
               SET is_test_data = TRUE
               WHERE tenant_name ILIKE 'E2E %'
            OR tenant_name ILIKE 'Test %'
            OR tenant_name = 'E2E SA Tenant'
               """)
    # Belt-and-braces: any tenant whose only schemes are all test data
    # is itself a test tenant. Skips tenants that have at least one real
    # scheme (a defensive no-op once the test suite stops reusing real
    # tenants — but harmless and cheap).
    op.execute("""
               UPDATE core.tenants t
               SET is_test_data = TRUE
               WHERE t.is_test_data = FALSE
                 AND EXISTS (SELECT 1
                             FROM core.schemes s
                             WHERE s.tenant_id = t.tenant_id
                               AND s.is_test_data = TRUE)
                 AND NOT EXISTS (SELECT 1
                                 FROM core.schemes s
                                 WHERE s.tenant_id = t.tenant_id
                                   AND s.is_test_data = FALSE)
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0029_add_is_test_data_to_tenants_schemes.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP INDEX IF EXISTS core.tenants_real_active_idx")
    op.execute("DROP INDEX IF EXISTS core.schemes_real_active_idx")
    op.execute("ALTER TABLE core.schemes DROP COLUMN IF EXISTS is_test_data")
    op.execute("ALTER TABLE core.tenants DROP COLUMN IF EXISTS is_test_data")
