# @featuretrace:cutover-control-plane — P0.3 safety backfill for protected feature toggles.
# Layer: migration
# Data flow: alembic upgrade head → core.feature_toggles (allowed_roles/depends_on backfill + bi_pg_primary_enabled=FALSE) (global)
# Related: backend/core/toggle_classification.py
#          backend/seeds/feature_toggles.py
#          docs/deployment/bi_pg_primary_toggle_verification_2026-06-11.md
# Table: core.feature_toggles
# Tests: tests/backend/test_toggle_classification_safety.py
"""P0.3 toggle safety hardening: backfill protected-toggle metadata, re-disable bi_pg_primary_enabled.

Production verification on 2026-06-11 (post PR #453) found bi_pg_primary_enabled
enabled with empty allowed_roles/depends_on. Two compounding causes:

  1. Migration 0054 inserted toggle rows with '{}'::text[] for allowed_roles and
     depends_on — the safety metadata in backend/seeds/feature_toggles.py only
     ever reached the MongoDB mirror, never core.feature_toggles.
  2. A blanket enable-all pass on 2026-06-09 flipped every row to TRUE,
     including cutover-class toggles that feature-toggle-governance.md §5
     requires to stay globally FALSE.

This migration:
  - Unions the classification-required allowed_roles/depends_on into every
    protected toggle row (idempotent; preserves any extra entries already set).
  - Forces bi_pg_primary_enabled to is_enabled=FALSE, replaying the manual
    production correction of 2026-06-11 so every environment converges. Other
    protected keys are NOT force-disabled: the development database
    intentionally runs with cutover toggles enabled (see
    scripts/audits/toggle_drift.py _KNOWN_INTENTIONAL_DRIFT).

Application-side enforcement (core/toggle_classification.py via config_repo)
prevents new drift; this migration repairs existing rows.

Downgrade is a no-op: removing safety metadata or re-enabling a PG-primary
read toggle is never a state worth restoring.

Revision ID: 0058_toggle_safety_backfill
Revises: 0057_scheme_manager_appointments
Create Date: 2026-06-11
"""
import sys
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0058_toggle_safety_backfill"
down_revision = "0057_scheme_manager_appointments"
branch_labels = None
depends_on = None

_BACKEND_DIR = str(Path(__file__).resolve().parents[2])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from core.toggle_classification import PROTECTED_TOGGLE_SAFETY_METADATA  # noqa: E402

_FORCE_DISABLE_KEYS = ["bi_pg_primary_enabled"]

# Phase D per-router PG read gates: consumed by cutover_config_service but the
# rows were never created (callers fall back to default=False). Create them
# disabled with full safety metadata so the catalogue is complete.
_MISSING_GATE_ROWS = [
    {
        "feature_key": "settings_pg_reads_enabled",
        "feature_name": "Settings PostgreSQL Reads",
        "description": (
            "Enable PostgreSQL-first reads for the settings router "
            "(Phase D per-router PG read gate). Safe default False."
        ),
        "category": "system",
    },
    {
        "feature_key": "users_pg_reads_enabled",
        "feature_name": "Users PostgreSQL Reads",
        "description": (
            "Enable PostgreSQL-first reads for the users router "
            "(Phase D per-router PG read gate). Safe default False."
        ),
        "category": "system",
    },
]


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0058_toggle_safety_backfill.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    conn = op.get_bind()

    for row in _MISSING_GATE_ROWS:
        required = PROTECTED_TOGGLE_SAFETY_METADATA[row["feature_key"]]
        conn.execute(
            text(
                """
                INSERT INTO core.feature_toggles
                    (feature_key, feature_name, description, category, is_enabled,
                     icon, routes, allowed_roles, depends_on, created_at, last_modified_at)
                VALUES
                    (:feature_key, :feature_name, :description, :category, FALSE,
                     'Database', '{}'::text[], CAST(:allowed_roles AS text[]),
                     CAST(:depends_on AS text[]), NOW(), NOW())
                ON CONFLICT (feature_key) DO NOTHING
                """
            ),
            {**row, "allowed_roles": required["allowed_roles"], "depends_on": required["depends_on"]},
        )

    for feature_key, required in PROTECTED_TOGGLE_SAFETY_METADATA.items():
        conn.execute(
            text(
                """
                UPDATE core.feature_toggles
                SET allowed_roles = (
                        SELECT COALESCE(array_agg(DISTINCT e), '{}'::text[])
                        FROM unnest(allowed_roles || CAST(:required_roles AS text[])) AS e
                    ),
                    depends_on = (
                        SELECT COALESCE(array_agg(DISTINCT e), '{}'::text[])
                        FROM unnest(depends_on || CAST(:required_deps AS text[])) AS e
                    ),
                    last_modified_at = NOW()
                WHERE feature_key = :feature_key
                  AND (
                        NOT (allowed_roles @> CAST(:required_roles AS text[]))
                     OR NOT (depends_on @> CAST(:required_deps AS text[]))
                  )
                """
            ),
            {
                "feature_key": feature_key,
                "required_roles": required["allowed_roles"],
                "required_deps": required["depends_on"],
            },
        )

    conn.execute(
        text(
            """
            UPDATE core.feature_toggles
            SET is_enabled = FALSE,
                last_modified_at = NOW()
            WHERE feature_key = ANY(:keys)
              AND is_enabled = TRUE
            """
        ),
        {"keys": _FORCE_DISABLE_KEYS},
    )


def downgrade() -> None:
    # Intentional no-op: never restore weaker safety metadata or re-enable
    # a PG-primary read toggle as part of a schema rollback.
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0058_toggle_safety_backfill.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    pass
