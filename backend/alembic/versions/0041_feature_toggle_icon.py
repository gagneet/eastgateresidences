"""0041 — add icon column to core.feature_toggles.

Revision: 0041
Previous: 0040

The Mongo feature-toggle catalog already carries an ``icon`` field and the
feature-toggle API response exposes it. The PostgreSQL config registry created
in 0011 did not include that column, which blocked a full router cutover.
"""
from __future__ import annotations

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0041_feature_toggle_icon.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("ALTER TABLE core.feature_toggles ADD COLUMN icon TEXT")


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0041_feature_toggle_icon.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("ALTER TABLE core.feature_toggles DROP COLUMN IF EXISTS icon")
