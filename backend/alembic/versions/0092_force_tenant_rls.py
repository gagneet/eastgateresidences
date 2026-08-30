"""0092 — FORCE row level security on 21 tables that had it enabled but not forced.

## Why this is a real gap, not a tidy-up

These 21 tables already had RLS *enabled* and a `tenant_isolation_*` policy, so
every previous audit that asked "does this table have RLS?" answered yes. They
were missing `FORCE ROW LEVEL SECURITY`.

PostgreSQL exempts a table's OWNER from its own RLS policies unless FORCE is
set. The application connects as ``strataos_user``, which owns every table in
this database. So on these 21 tables the policy was inert for the application:
a query issued without ``app.tenant_id`` returned **every tenant's rows**, not
zero. That is the cross-tenant direction of the failure — worse than the
0-rows symptom RLS normally produces.

Found by the rewritten ``tests/backend/test_rls_coverage.py`` sweep, which
asserts enabled AND forced AND policed. The previous allowlist-style test could
not have found it.

## Safety

All 21 tables were verified EMPTY at the time of writing, so this changes no
current behaviour. Every caller was checked for tenant context:

  - ``services/powerhouse_conversation_service.py`` and
    ``routers/powerhouse_conversations.py`` call ``set_tenant()`` directly.
  - ``powerhouse_workflow_command_service.py`` and
    ``powerhouse_communications_command_service.py`` do not call it themselves,
    but both run through ``services/powerhouse_command_foundation.py``, which
    calls ``set_tenant(session, context.tenant_id)`` before dispatching the
    command.

Forcing now — while the tables are empty — means a caller that forgets tenant
context fails loudly in development instead of leaking across tenants in
production once this data lands.

Note ``core.tenancy_periods`` and the 18 tables of 0091 are already forced;
this migration is only the enabled-but-unforced remainder.
"""
from __future__ import annotations

from alembic import op

revision = "0092_force_tenant_rls"
down_revision = "0091_restore_tenant_rls"
branch_labels = None
depends_on = None


_TABLES: list[tuple[str, str]] = [
    ("communications", "conversation_links"),
    ("communications", "conversation_messages"),
    ("communications", "conversation_participants"),
    ("communications", "conversation_threads"),
    ("communications", "conversation_watchers"),
    ("communications", "inboxes"),
    ("communications", "message_ai_suggestions"),
    ("communications", "message_ai_summaries"),
    ("communications", "message_attachments"),
    ("communications", "message_delivery_events"),
    ("workflow", "automation_rule_actions"),
    ("workflow", "automation_rule_conditions"),
    ("workflow", "automation_rule_runs"),
    ("workflow", "automation_rules"),
    ("workflow", "workflow_actions"),
    ("workflow", "workflow_assignments"),
    ("workflow", "workflow_events"),
    ("workflow", "workflow_instances"),
    ("workflow", "workflow_status_history"),
    ("workflow", "workflow_steps"),
    ("workflow", "workflow_templates"),
]


def upgrade() -> None:
    """Force RLS so the owning application role is subject to its own policies.

    Idempotent: FORCE is a no-op when already set. RLS is re-ENABLEd defensively
    so a table that was disabled out-of-band converges too, matching 0091.
    """
    for schema, table in _TABLES:
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Return to enabled-but-not-forced.

    This re-exempts the owning application role from every policy on these
    tables, i.e. restores the cross-tenant read. Provided for alembic symmetry;
    do not run it to fix a query that returns 0 rows — that means the caller
    never set ``app.tenant_id``.
    """
    for schema, table in reversed(_TABLES):
        op.execute(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY")
