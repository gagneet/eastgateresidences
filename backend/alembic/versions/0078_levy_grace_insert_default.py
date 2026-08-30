"""0078 — Compute grace_deadline_date at INSERT time.

Revision ID: 0078_levy_grace_insert_default
Previous: 0077_arrears_grace_meta
Create Date: 2026-08-03

# @featuretrace:arrears-grace-deadline-metadata
# Layer: migration
# Data flow: finance.levy_runs / finance.levy_items INSERT → BEFORE INSERT trigger computes
#            grace_deadline_date when the caller didn't supply one → existing 0077 trigger
#            derives is_past_grace/days_overdue from it.
# Note: revision id must stay <= 32 characters (see rules/post-compact-critical.md footgun #9).
# Related: backend/alembic/versions/0077_arrears_grace_meta.py
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py (create_levy() /
#          find_or_create_levy_run() / save_levy_run() / save_levy_items() — the live levy-issuance
#          path; none of these supply grace_deadline_date)
# Table: finance.levy_runs, finance.levy_items

Bug this closes
----------------
Migration 0077 added `grace_deadline_date` to `finance.levy_runs`/`finance.levy_items` and made it
`NOT NULL` with **no database default**. The live levy-issuance code path
(`ledger_repo.py::find_or_create_levy_run()`, `save_levy_run()`, `save_levy_items()`) never sets
this field — it wasn't wired to grace-deadline logic (by design; see
`docs/architecture/arrears_calculation_spec_2026_08_03.md` and GAP-FIN-030's "existing router calls
still preserve the old formula path" note). Once 0077 is applied, every new levy run/item insert
through that path fails with a NOT NULL violation, breaking real levy generation.

Fix: BEFORE INSERT triggers compute `grace_deadline_date` server-side whenever the inserted row
doesn't already supply one, so no application code needs to change:

- `finance.levy_runs`: `due_date + core.schemes.grace_period_days` (falls back to 14 days if the
  scheme row or its grace_period_days is somehow unavailable — matches 0077's own backfill default).
- `finance.levy_items`: looks up its parent `finance.levy_runs.grace_deadline_date` (already resolved
  by the trigger above, since a run row is always inserted+flushed before its items in the same
  transaction — confirmed against `ledger_repo.py`'s call order) and falls back to that run's
  `due_date + 14 days` if the run's own grace_deadline_date is unexpectedly still null.

The existing `levy_items_grace_deadline_trigger` (0077) already computes `is_past_grace`/
`days_overdue` from `NEW.grace_deadline_date` on BEFORE INSERT OR UPDATE — extending that same
function to resolve `grace_deadline_date` first keeps both derivations in one place instead of
stacking a second trigger with an ordering dependency.
"""
from __future__ import annotations

from alembic import op

revision = "0078_levy_grace_insert_default"
down_revision = "0077_arrears_grace_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. finance.levy_runs — compute grace_deadline_date from due_date + the scheme's
    # grace_period_days when the caller didn't supply one.
    op.execute("""
        CREATE OR REPLACE FUNCTION finance.levy_runs_default_grace_deadline()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.grace_deadline_date IS NULL THEN
                NEW.grace_deadline_date := (NEW.due_date + (
                    COALESCE(
                        (SELECT grace_period_days FROM core.schemes WHERE scheme_id = NEW.scheme_id),
                        14
                    ) * INTERVAL '1 day'
                ))::DATE;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER levy_runs_default_grace_deadline_trigger
        BEFORE INSERT ON finance.levy_runs
        FOR EACH ROW
        EXECUTE FUNCTION finance.levy_runs_default_grace_deadline();
    """)

    # 2. finance.levy_items — extend the 0077 trigger function to resolve
    # grace_deadline_date (from the parent run) before deriving is_past_grace/days_overdue.
    op.execute("""
        CREATE OR REPLACE FUNCTION finance.levy_items_update_grace_deadline()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.grace_deadline_date IS NULL THEN
                SELECT COALESCE(lr.grace_deadline_date, lr.due_date + INTERVAL '14 days')::DATE
                INTO NEW.grace_deadline_date
                FROM finance.levy_runs lr
                WHERE lr.levy_run_id = NEW.levy_run_id;
            END IF;

            NEW.is_past_grace := (CURRENT_DATE > NEW.grace_deadline_date);
            NEW.days_overdue := CASE
                WHEN CURRENT_DATE > NEW.grace_deadline_date THEN (CURRENT_DATE - NEW.grace_deadline_date)
                ELSE 0
            END;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS levy_runs_default_grace_deadline_trigger ON finance.levy_runs")
    op.execute("DROP FUNCTION IF EXISTS finance.levy_runs_default_grace_deadline()")

    # Restore the 0077 version of the levy_items trigger function (no grace_deadline_date resolution).
    op.execute("""
        CREATE OR REPLACE FUNCTION finance.levy_items_update_grace_deadline()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.is_past_grace := (CURRENT_DATE > NEW.grace_deadline_date);
            NEW.days_overdue := CASE
                WHEN CURRENT_DATE > NEW.grace_deadline_date THEN (CURRENT_DATE - NEW.grace_deadline_date)
                ELSE 0
            END;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
