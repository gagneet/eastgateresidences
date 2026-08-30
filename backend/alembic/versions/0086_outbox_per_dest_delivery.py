"""core.outbox: independent Redis/Mongo delivery tracking (GAP-FIN-061 correctness fix).

Revision ID: 0086_outbox_per_dest_delivery
Revises: 0085_bank_txn_origin_nn
Create Date: 2026-08-11 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial_core — per-destination outbox delivery tracking (GAP-FIN-061).
# Layer: migration
# Data flow: core.outbox.{redis_published_at,mongo_published_at,redis_error,mongo_error}
#            -> workers/outbox_relay.py (reads/writes these columns per attempt)
# Related: backend/workers/outbox_relay.py (the fix's other half)
#          alembic/versions/0081_outbox_rls_bypass.py (the RLS fix this correctness fix follows)
#          tasks/GAP-FIN-061-outbox-relay-mass-dead-letter-mongo-dr-mirror-stale.md
#
# Found live 2026-08-11 while investigating GAP-FIN-061: _publish_to_mongo() swallows its own
# exception and never re-raises ("MongoDB archive is a best-effort projection"), but the relay
# set a single shared `published_at` once _dispatch_event() returned without error. Redis
# succeeding while Mongo silently failed was therefore recorded as fully delivered forever, with
# no retry ever firing for the missed Mongo write. This migration adds independent per-destination
# delivery columns so a partial failure stays retryable for exactly the destination that failed.
# `published_at` (pre-existing) is repurposed by the relay to mean "both destinations confirmed" —
# no schema change needed for it, only the write discipline in outbox_relay.py changes.

from alembic import op
import sqlalchemy as sa

revision = "0086_outbox_per_dest_delivery"
down_revision = "0085_bank_txn_origin_nn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("redis_published_at", sa.DateTime(timezone=True), nullable=True), schema="core")
    op.add_column("outbox", sa.Column("mongo_published_at", sa.DateTime(timezone=True), nullable=True), schema="core")
    op.add_column("outbox", sa.Column("redis_error", sa.Text(), nullable=True), schema="core")
    op.add_column("outbox", sa.Column("mongo_error", sa.Text(), nullable=True), schema="core")
    # Backfill: every row already marked published_at (fully delivered under the old,
    # combined-success semantics) is treated as delivered to both destinations as of that
    # timestamp — the old semantics never distinguished which destination succeeded, but both
    # must have succeeded together for published_at to have been set at all under the old code.
    op.execute(
        "UPDATE core.outbox "
        "SET redis_published_at = published_at, mongo_published_at = published_at "
        "WHERE published_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("outbox", "mongo_error", schema="core")
    op.drop_column("outbox", "redis_error", schema="core")
    op.drop_column("outbox", "mongo_published_at", schema="core")
    op.drop_column("outbox", "redis_published_at", schema="core")
