"""0025 — Rename core.user_role enum value building_admin → strata_admin.

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-02

The ``building_admin`` role is being renamed to ``strata_admin`` to match
the org model: a Strata Admin runs a Strata Management Company (the core.tenants
row), distinct from a Strata Manager who works for that company managing
buildings, and distinct from the volunteer Chairman who heads an Executive
Committee. The previous name ``building_admin`` conflated "admin of the
strata management company" with "admin of a single building"; in our model
the latter is just the Strata Manager.

Postgres ALTER TYPE … RENAME VALUE updates the enum atomically and is
free at the data layer — every existing row keeps the same internal
identity, just appears under the new label going forward.

Companion code changes:
- ``models/user.py`` renames ``UserRole.BUILDING_ADMIN`` → ``UserRole.STRATA_ADMIN``
- ``normalize_user_role`` accepts the legacy ``"building_admin"`` slug for
  in-flight JWT tokens and maps it to ``STRATA_ADMIN``
- ``normalize_user_role`` no longer maps ``"chairman" → "building_admin"``;
  ``chairman`` now normalizes to ``ec_member`` (Chairman is a volunteer EC
  position, never an employee of a strata management company)
"""
from alembic import op

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0025_rename_building_admin_to_strata_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("ALTER TYPE core.user_role RENAME VALUE 'building_admin' TO 'strata_admin'")


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0025_rename_building_admin_to_strata_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("ALTER TYPE core.user_role RENAME VALUE 'strata_admin' TO 'building_admin'")
