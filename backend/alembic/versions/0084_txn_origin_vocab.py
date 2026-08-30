"""Controlled vocabulary for finance.bank_transactions.transaction_origin (provenance, B1).

Revision ID: 0084_txn_origin_vocab
Revises: 0083_portal_snapshot_pg
Create Date: 2026-08-10 00:00:00.000000
"""

from __future__ import annotations

# @featuretrace:financial-onboarding — provenance controlled vocabulary (B1), Bank/Trust-ready.
# Layer: migration
# Data flow: (writers) reconstruction_generators/* + bank_feeds.py + reconcile_portal_ledger.py
#            set finance.bank_transactions.transaction_origin -> this CHECK constrains the value.
# Related: backend/alembic/versions/0064_bank_transactions_provenance_fields.py (added the column)
#          docs/finances/onboarding-reconstruction-workflow-canonical-spec.md (Workstream D)
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (B1)
# Table: finance.bank_transactions
#
# Why NOT VALID (and why this is only step 1)
# -------------------------------------------
# `transaction_origin` was added as free-text TEXT (0064). The Bank/Trust-supersession design
# (G6) needs a CONTROLLED vocabulary so a real feed can `WHERE transaction_origin =
# 'reconstructed_historical'` supersede-by-tag without a forensic exercise, and so provenance is
# an enforced fact, not a typo-prone string. But existing rows may hold NULL or a legacy value
# this session cannot enumerate against the live DB. Adding a hard CHECK would risk failing the
# migration on an unseen existing value.
#
# So this adds the CHECK as `NOT VALID`: PostgreSQL enforces it on every INSERT/UPDATE from now
# on (all current writers already use a value in the set below), but does NOT scan/validate
# existing rows. NULL is still allowed here (a not-yet-tagged row is legitimate today). The
# tightening to a fully-validated, NOT-NULL constraint is B1b: backfill null/legacy rows in a
# real env, then `ALTER TABLE ... VALIDATE CONSTRAINT ...` + a `SET NOT NULL`. Keep that
# follow-up migration's revision string <= 32 chars (repo footgun #9).

from alembic import op

revision = "0084_txn_origin_vocab"
down_revision = "0083_portal_snapshot_pg"
branch_labels = None
depends_on = None

_CONSTRAINT = "bank_transactions_txn_origin_vocab_chk"

# The complete set every current writer uses (verified by grep across backend/), plus the
# canonical provenance tags the supersession design depends on. NULL is permitted (not-yet-tagged).
_ALLOWED = (
    "'reconstructed_historical'",  # derived, assumed — the default onboarding tag
    "'bank_reconciled'",           # confirmed against a real Bank/Trust feed (supersedes derived)
    "'portal_reconciliation'",     # bounded per-unit reconciliation adjustment (Workstream C)
    "'external_import'",           # real observed cash from a DEFT/BPAY/bank CSV import
    "'manual_adjustment'",         # dated, dual-control correction with a known cause
    "'opening_balance'",           # onboarding genesis opening position
    "'ledger_rechain'",            # re-chaining correction (reconcile_portal_ledger.py)
    "'consecutive_snapshot_delta'",  # inferred movement between two portal snapshots
    "'strata_web_portal_scrape'",  # legacy provenance value still present on some rows
    "'levy_payments_sum'",         # legacy reconciliation_source value seen on migrated rows
    "'trust_ledger_dual_write'",   # legacy dual-write provenance value
)


def upgrade() -> None:
    allowed = ", ".join(_ALLOWED)
    op.execute(
        f"ALTER TABLE finance.bank_transactions "
        f"ADD CONSTRAINT {_CONSTRAINT} "
        f"CHECK (transaction_origin IS NULL OR transaction_origin IN ({allowed})) "
        f"NOT VALID"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE finance.bank_transactions DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"
    )
