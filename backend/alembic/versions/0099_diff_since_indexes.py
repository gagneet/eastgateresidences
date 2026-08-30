"""Index the columns /analytics/diff-since counts on.

Revision ID: 0099_diff_since_indexes
Revises: 0098_receipt_retirement
Create Date: 2026-08-27

# @featuretrace:dashboard-v2 — make the "since your last visit" counts index-only.
# Layer: migration
# Data flow: GET /analytics/diff-since → COUNT(*) per domain table → dashboard card (building-scoped).
# Related: backend/routers/analytics.py (get_diff_since)
#          frontend/src/components/dashboard/SinceLastVisit.tsx

`get_diff_since` issues one `SELECT COUNT(*) ... WHERE scheme_id = :sid AND created_at >
:since` per domain table, and the Management dashboard calls it on every load.

Measured before this migration (EXPLAIN, East Gate, 7-day window):

    ops.cases                     Seq Scan   (0 rows — trivial)
    finance.receipts              Seq Scan   (2,233 rows)
    compliance.compliance_items   Bitmap Heap Scan
    communications.announcements  Bitmap Heap Scan

Only `ops.cases` had a covering `(scheme_id, created_at)` index. `finance.receipts` is
the one that matters: it is the largest of the four and the only one that grows with
ordinary use — every levy payment adds a row — so the scan cost rises for as long as the
building keeps paying. It is also the table a dashboard read has least business scanning.

Plain CREATE INDEX rather than CONCURRENTLY: Alembic wraps each migration in a
transaction, and CONCURRENTLY cannot run inside one. These tables are small enough that
the brief lock is not worth splitting the migration to avoid — revisit if any of them
reaches a scale where a lock is felt.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0099_diff_since_indexes"
down_revision = "0098_receipt_retirement"
branch_labels = None
depends_on = None

# (schema, table) pairs get_diff_since counts over. ops.cases already has one.
_TARGETS = [
    ("finance", "receipts"),
    ("compliance", "compliance_items"),
    ("communications", "announcements"),
]


def upgrade() -> None:
    for schema, table in _TARGETS:
        # Column order matters: scheme_id is the equality predicate and created_at the
        # range, so scheme_id must lead for the range to be satisfied by the index.
        op.execute(text(f"""
            CREATE INDEX IF NOT EXISTS {table}_scheme_created_idx
                ON {schema}.{table} (scheme_id, created_at DESC)
        """))


def downgrade() -> None:
    for schema, table in _TARGETS:
        op.execute(text(f"DROP INDEX IF EXISTS {schema}.{table}_scheme_created_idx"))
