"""Extend domain_cutover_status readiness_status constraint.

Revision ID: 0060_extend_readiness_constraint
Revises: 0059_cutover_guard_audit_actions
Create Date: 2026-06-29 00:00:00.000000

Adds identity_ready, evidence_ready, genesis_ready, ready_for_shadow to the
ck_cutover_status_readiness check constraint.  These four values were added to the
Python ReadinessStatus enum in models/cutover_status.py after migration 0049 was
written, so the DB constraint was left behind.
"""
from __future__ import annotations

from alembic import op

revision = "0060_extend_readiness_constraint"
down_revision = "0059_cutover_guard_audit_actions"
branch_labels = None
depends_on = None

_OLD_READINESS = (
    "unknown",
    "not_started",
    "shadow_active",
    "shadow_passing",
    "shadow_clean",
    "promoted",
    "rolled_back",
    "blocked",
)

_NEW_READINESS = _OLD_READINESS + (
    "identity_ready",
    "evidence_ready",
    "genesis_ready",
    "ready_for_shadow",
)


def _readiness_in_clause(values: tuple[str, ...]) -> str:
    """Generated function header.

    Function: _readiness_in_clause
    Path: backend/alembic/versions/0060_extend_readiness_constraint.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"readiness_status IN ({quoted})"


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0060_extend_readiness_constraint.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_constraint(
        "ck_cutover_status_readiness",
        "domain_cutover_status",
        schema="core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_cutover_status_readiness",
        "domain_cutover_status",
        _readiness_in_clause(_NEW_READINESS),
        schema="core",
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0060_extend_readiness_constraint.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_constraint(
        "ck_cutover_status_readiness",
        "domain_cutover_status",
        schema="core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_cutover_status_readiness",
        "domain_cutover_status",
        _readiness_in_clause(_OLD_READINESS),
        schema="core",
    )
