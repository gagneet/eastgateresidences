# @featuretrace:cutover-control-plane — Allows persisted domain source guard audit events.
# Layer: migration
# Data flow: domain_source_guard → cutover_status_service → core.cutover_audit_log.action (building-scoped).
# Related: backend/services/domain_source_guard.py
#          backend/services/cutover_status_service.py
#          backend/models/cutover_status.py
# Tests: tests/backend/test_domain_source_guard.py
"""Allow cutover guard audit action values.

Revision ID: 0059_cutover_guard_audit_actions
Revises: 0058_toggle_safety_backfill
Create Date: 2026-06-13
"""

from __future__ import annotations

from alembic import op

revision = "0059_cutover_guard_audit_actions"
down_revision = "0058_toggle_safety_backfill"
branch_labels = None
depends_on = None

_OLD_AUDIT_ACTIONS = (
    "entered_shadow",
    "promoted_read",
    "promoted_write",
    "archived_mongo",
    "rolled_back",
    "readiness_checked",
    "shadow_diff_recorded",
)

_NEW_GUARD_ACTIONS = (
    "domain_source.blocked",
    "domain_source.downgraded",
    "domain_source.shadow_blocked",
    "domain_source.missing_state_defaulted",
    "domain_source.readiness_failed",
    "domain_source.operation_denied",
)


def _action_check(actions: tuple[str, ...]) -> str:
    """Generated function header.

    Function: _action_check
    Path: backend/alembic/versions/0059_cutover_guard_audit_actions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    quoted = ", ".join(f"'{action}'" for action in actions)
    return f"action IN ({quoted})"


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0059_cutover_guard_audit_actions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_constraint(
        "ck_cutover_audit_action",
        "cutover_audit_log",
        schema="core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_cutover_audit_action",
        "cutover_audit_log",
        _action_check(_OLD_AUDIT_ACTIONS + _NEW_GUARD_ACTIONS),
        schema="core",
    )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0059_cutover_guard_audit_actions.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_constraint(
        "ck_cutover_audit_action",
        "cutover_audit_log",
        schema="core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_cutover_audit_action",
        "cutover_audit_log",
        _action_check(_OLD_AUDIT_ACTIONS),
        schema="core",
    )
