"""finance.levy_runs gains fund_id + a partial unique natural-key index; finance.levy_items
gains owner_resolution_status for durable provenance.

Revision ID: 0076_levy_charge_uniqueness
Revises: 0075_user_email_aliases
Create Date: 2026-07-31 00:00:00.000000

# @featuretrace:financial-onboarding — DB-level uniqueness for the historical levy-charge
#   posting pipeline's levy_run natural key, and durable per-item ownership-resolution
#   provenance.
# Layer: migration
# Data flow: finance.levy_runs.fund_id / finance.levy_items.owner_resolution_status ->
#            FinancialCoreService.create_historical_levy() ->
#            backend/services/unit_levy_ledger_service.py (projection rebuild).
# Related: backend/services/financial_core/service.py (create_historical_levy)
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#          docs/finances/reconcilation-2021-2022-finances-plan01.md
#          docs/finances/reconcilation-2021-2022-finances-plan02.md
# Table: finance.levy_runs, finance.levy_items

Context
-------
`finance.levy_runs` has no fund dimension today — fund only lives on `finance.levy_items`.
`create_levy()` (the live quarterly-billing path) already enforces "one run = one fund" at the
application layer (`create_levy()` rejects a call whose `lot_levies` span more than one
`fund_id`), but nothing at the database level encodes or protects that invariant. The historical
levy-charge posting pipeline (`create_historical_levy()`) needs to look up an existing run by
natural key (scheme, financial_year, quarter_no, levy_run_type, fund) to stay idempotent/safe to
retry — without a fund column, two calls for the same period but different funds (e.g. admin Q1
2022 and sinking Q1 2022) would be indistinguishable by natural key. A bare application-level
"lookup then insert" is also not concurrency-safe by itself (two concurrent callers can both
lookup-miss then both insert) — a real DB-level constraint is required, matching the precedent
already set by `finance.levy_items` (`UNIQUE(levy_run_id, lot_id, fund_id)`, migration 0004) and
`finance.journal_entries` (`je_idempotency_key_idx`, migration 0008).

`fund_id` is nullable and the new unique index is PARTIAL (`WHERE fund_id IS NOT NULL`) —
mirroring `je_idempotency_key_idx`'s own `WHERE idempotency_key IS NOT NULL` pattern — so every
pre-existing `finance.levy_runs` row (created by the live `create_levy()` path, which never sets
this column) is completely unaffected: no backfill, no behaviour change on the live path. Only
new rows inserted by `create_historical_levy()` populate `fund_id` and participate in the
uniqueness guarantee.

`finance.levy_items.owner_resolution_status` records whether a historical charge's
`owner_party_id` resolved to a real, date-scoped owner or to the per-tenant sentinel placeholder
party (`core.parties.party_type='unresolved_placeholder'`) — durable, DB-level provenance, not
solely journal metadata, so it survives a `unit_levy_ledger` projection rebuild. Defaults to
'resolved' for every existing row, which is accurate: every levy_item posted before this
migration was posted with a real resolved owner (the sentinel-placeholder fallback is new,
introduced by this same feature).
"""
from __future__ import annotations

from alembic import op

revision = "0076_levy_charge_uniqueness"
down_revision = "0075_user_email_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE finance.levy_runs "
        "ADD COLUMN fund_id UUID REFERENCES finance.funds (fund_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX levy_runs_natural_key_idx "
        "ON finance.levy_runs (scheme_id, financial_year, quarter_no, levy_run_type, fund_id) "
        "WHERE fund_id IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE finance.levy_items "
        "ADD COLUMN owner_resolution_status TEXT NOT NULL DEFAULT 'resolved'"
    )
    op.execute(
        "ALTER TABLE finance.levy_items "
        "ADD CONSTRAINT levy_items_owner_resolution_status_chk "
        "CHECK (owner_resolution_status IN ('resolved', 'unresolved'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE finance.levy_items DROP CONSTRAINT IF EXISTS levy_items_owner_resolution_status_chk"
    )
    op.execute("ALTER TABLE finance.levy_items DROP COLUMN IF EXISTS owner_resolution_status")
    op.execute("DROP INDEX IF EXISTS finance.levy_runs_natural_key_idx")
    op.execute("ALTER TABLE finance.levy_runs DROP COLUMN IF EXISTS fund_id")
