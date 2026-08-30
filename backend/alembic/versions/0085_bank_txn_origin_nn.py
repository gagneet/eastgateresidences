"""finance.bank_transactions.transaction_origin: backfill NULLs, tighten to NOT NULL (B1b).

Revision ID: 0085_bank_txn_origin_nn
Revises: 0084_txn_origin_vocab
Create Date: 2026-08-10 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial-onboarding — provenance vocabulary tightened to NOT NULL (B1b).
# Layer: migration
# Data flow: finance.bank_transactions.transaction_origin (nullable, NOT VALID check from 0084)
#            -> backfill every NULL row per-tenant -> re-add the CHECK (now covering the one
#            legacy value not in 0084's vocabulary) -> VALIDATE CONSTRAINT -> SET NOT NULL.
# Related: backend/alembic/versions/0084_txn_origin_vocab.py (added the NOT VALID CHECK)
#          docs/finances/onboarding-reconstruction-workflow-canonical-spec.md (Workstream D)
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (B1b)
# Table: finance.bank_transactions
#
# Live audit (2026-08-10, this migration's author) across all three schemes in
# strataos_production, iterated per-tenant (this table has no RLS bypass clause — see the
# footgun this migration's own tenant-loop pattern avoids, same as 0072):
#   - 13195 (East Gate):    3952 rows 'reconstructed_historical', 1 row
#                            'portal_confirmed_manual_entry' (a real, distinct provenance value
#                            NOT in 0084's allowed set -- a manually-entered, portal-confirmed-
#                            but-not-bank-verified UA038 payment, posted 2026-08-05; see
#                            docs/finances/GAP-FIN-046-reconciliation-analysis.md). Remapping it
#                            to an existing tag (e.g. manual_adjustment, which implies dual-
#                            control correction with a known cause -- not what this is) would
#                            silently lose real provenance information on an already-posted row,
#                            which the controlling contract forbids. Adding it to the controlled
#                            vocabulary instead preserves the original, honest provenance.
#   - DEMO-0001 (Tower):    0 rows (table empty for this scheme).
#   - UP-DEMO-001 (Acme):   123 rows with NULL transaction_origin (source_type IN ('seed',
#                            'csv_upload'), provider_name='demo_bank_feed' for all of them --
#                            the Acme sales-demo seed's synthetic two-year ledger, predates the
#                            transaction_origin column being populated by that seed script).
#                            Backfilled to 'reconstructed_historical' -- the correct tag for
#                            derived/seeded historical data, matching the vocabulary's own
#                            definition ("derived, assumed -- the default onboarding tag").
#
# Sequence: backfill (per-tenant, RLS-scoped) -> drop+re-add the CHECK (now covering
# 'portal_confirmed_manual_entry', and no longer permitting NULL) as NOT VALID -> VALIDATE
# CONSTRAINT (scans every row across all tenants -- DDL validation is not RLS-filtered, unlike
# the DML backfill step) -> SET NOT NULL. Keeping the revision string <=32 chars (footgun #9).

from alembic import op
from sqlalchemy import text as sa_text

revision = "0085_bank_txn_origin_nn"
down_revision = "0084_txn_origin_vocab"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
_CONSTRAINT = "bank_transactions_txn_origin_vocab_chk"

# 0084's set, plus the one live legacy value it missed.
_ALLOWED = (
    "'reconstructed_historical'",
    "'bank_reconciled'",
    "'portal_reconciliation'",
    "'external_import'",
    "'manual_adjustment'",
    "'opening_balance'",
    "'ledger_rechain'",
    "'consecutive_snapshot_delta'",
    "'strata_web_portal_scrape'",
    "'levy_payments_sum'",
    "'trust_ledger_dual_write'",
    "'portal_confirmed_manual_entry'",  # added this migration -- see audit note above
)


def upgrade() -> None:
    """Backfill NULL transaction_origin per-tenant, expand the CHECK vocabulary, validate it
    against every row, then SET NOT NULL (see module docstring for the full 4-step contract)."""
    bind = op.get_bind()

    # Step 1 — backfill every NULL row, per-tenant (RLS has no bypass clause on this table).
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))
    scheme_rows = bind.execute(
        sa_text("SELECT tenant_id::text FROM core.schemes")
    ).fetchall()
    for (tenant_id,) in scheme_rows:
        bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        bind.execute(
            sa_text(
                "UPDATE finance.bank_transactions "
                "SET transaction_origin = 'reconstructed_historical' "
                "WHERE transaction_origin IS NULL"
            )
        )
    bind.execute(sa_text(f"SET LOCAL app.tenant_id = '{_TENANT_BYPASS_ID}'"))

    # Step 2 — re-create the CHECK (DDL; not RLS-scoped) with the expanded vocabulary and no
    # NULL allowance, as NOT VALID so this ALTER itself doesn't block on a table scan.
    allowed = ", ".join(_ALLOWED)
    op.execute(f"ALTER TABLE finance.bank_transactions DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE finance.bank_transactions "
        f"ADD CONSTRAINT {_CONSTRAINT} "
        f"CHECK (transaction_origin IN ({allowed})) "
        f"NOT VALID"
    )

    # Step 3 — validate against every existing row (all tenants; DDL, not RLS-filtered).
    op.execute(f"ALTER TABLE finance.bank_transactions VALIDATE CONSTRAINT {_CONSTRAINT}")

    # Step 4 — tighten to NOT NULL now that no row holds NULL.
    op.execute("ALTER TABLE finance.bank_transactions ALTER COLUMN transaction_origin SET NOT NULL")


def downgrade() -> None:
    """Revert the DDL only (drop NOT NULL, restore the pre-0085 CHECK). The NULL backfill from
    upgrade()'s step 1 is NOT reversed -- there is no record of which rows were originally NULL,
    so "undoing" it would mean guessing, not reverting."""
    op.execute("ALTER TABLE finance.bank_transactions ALTER COLUMN transaction_origin DROP NOT NULL")
    op.execute(f"ALTER TABLE finance.bank_transactions DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    allowed_0084 = ", ".join(_ALLOWED[:-1])  # drop the value added this migration
    op.execute(
        f"ALTER TABLE finance.bank_transactions "
        f"ADD CONSTRAINT {_CONSTRAINT} "
        f"CHECK (transaction_origin IS NULL OR transaction_origin IN ({allowed_0084})) "
        f"NOT VALID"
    )
