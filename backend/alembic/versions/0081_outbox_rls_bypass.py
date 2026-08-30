"""Add RLS bypass-sentinel clause to core.outbox.tenant_isolation.

Revision ID: 0081_outbox_rls_bypass
Revises: 0080_seed_pg_reads_bypass
Create Date: 2026-08-09

# @featuretrace:financial_core — Fix the transactional outbox relay's 100%-silent-failure
#   RLS bug (found live 2026-08-09).
# Layer: migration
# Data flow: workers/outbox_relay.py's run_relay_loop() -> core.outbox (cross-tenant poll)
# Related: backend/workers/outbox_relay.py (the fix's other half — sets the bypass
#          sentinel before polling)
#          alembic/versions/0026 (core.schemes bypass — the pattern this mirrors)
#          alembic/versions/0027_role_assignments_rls_bypass.py

core.outbox's RLS policy (`tenant_isolation_core_outbox`) has always been
`tenant_id = core.current_tenant_id()` with NO bypass clause, and
`relforcerowsecurity = true`. workers/outbox_relay.py's run_relay_loop() is a
deliberately cross-tenant background poller -- it processes every tenant's
outbox events in one loop, by design, and its session never sets
app.tenant_id. Every poll since this table existed has therefore matched
zero rows, silently -- not an error, exactly the "any raw DB connection that
never runs SET app.tenant_id gets silent count(*) = 0" footgun already
documented in rules/post-compact-critical.md item 8, just not yet caught for
this table. Confirmed live: 15,000+ outbox rows across 11 event types
(payment.allocated, payment.received, ledger.historical_posting_authorisation_used,
etc.), spanning back to 2026-05-04, every single one still `attempts = 0,
published_at = NULL` -- the relay has never successfully processed a single
event since inception. Downstream effect: MongoDB's financial_events archive
and the Redis event streams that drive live financial notifications have
never received a real event from this pipeline.

Same bypass-sentinel pattern as core.schemes (migration 0026) and
core.user_role_assignments (migration 0027). Application code must continue
to set the bypass sentinel only inside the relay's own dedicated session
(workers/outbox_relay.py) -- never in a request-scoped/tenant-scoped session.
"""
from alembic import op

revision = "0081_outbox_rls_bypass"
down_revision = "0080_seed_pg_reads_bypass"
branch_labels = None
depends_on = None

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_outbox ON core.outbox")
    op.execute(f"""
        CREATE POLICY tenant_isolation_core_outbox ON core.outbox
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{_BYPASS_UUID}'
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_core_outbox ON core.outbox")
    op.execute("""
        CREATE POLICY tenant_isolation_core_outbox ON core.outbox
        USING (tenant_id = core.current_tenant_id())
    """)
