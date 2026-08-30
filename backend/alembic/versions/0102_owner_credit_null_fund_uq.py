# @featuretrace:financial_core — partial unique index for fund-less owner credit.
# Layer: migration
# Data flow: finance.owner_credit_balances -> unique (scheme_id, lot_id, owner_party_id)
#            WHERE fund_id IS NULL, so surplus credit accumulates on one row per owner/lot.
# Related: backend/services/financial_core/adapters/db_postgres/ledger_repo.py
#            (upsert_owner_credit's ON CONFLICT target must match this index exactly)
"""Partial unique index so fund-less owner credit accumulates instead of duplicating.

`owner_credit_balances` already has UNIQUE (scheme_id, lot_id, owner_party_id, fund_id).
That constraint does NOT do the job for the case this table exists to serve.

Unapplied credit from an over-payment is credit against the LOT, not against a
particular fund — the owner paid more than was charged, and which fund a future levy
draws it down into is decided when that levy is raised. So `fund_id` is NULL.

Postgres treats NULLs as DISTINCT in a unique constraint, so two fund-less credit rows
for the same owner and lot do not violate the existing constraint, and an
`ON CONFLICT (scheme_id, lot_id, owner_party_id, fund_id)` clause never matches one.
Without this index, every surplus receipt would insert a NEW row and the owner's credit
would be scattered across rows instead of accumulating on one.

The index predicate must stay identical to the `WHERE fund_id IS NULL` in
`upsert_owner_credit`'s ON CONFLICT clause; Postgres matches an arbiter index by its
predicate, and a mismatch fails at runtime rather than at deploy.
"""
from alembic import op

revision = "0102_owner_credit_null_fund_uq"
# Rebased onto 0101_session_revocation: a concurrent branch added that off the
# same parent, and two heads make `alembic upgrade head` ambiguous.
down_revision = "0101_session_revocation"
branch_labels = None
depends_on = None

_INDEX = "owner_credit_balances_null_fund_uq"


def upgrade() -> None:
    # CONCURRENTLY is deliberately NOT used: the table is empty on every deployment
    # reached by this migration, and CONCURRENTLY cannot run inside alembic's
    # transaction.
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX}
            ON finance.owner_credit_balances (scheme_id, lot_id, owner_party_id)
            WHERE fund_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS finance.{_INDEX}")
