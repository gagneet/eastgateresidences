"""Add a salutation field to core.users and core.parties.

Revision ID: 0097_party_user_salutation
Revises: 0096_user_activation_state
Create Date: 2026-08-27

# @featuretrace:user-management — separate the honorific from the name it was embedded in.
# Layer: migration
# Data flow: alembic upgrade head → core.users.salutation / core.parties.salutation
#            → owner name formatting and correspondence (building-scoped).
# Related: backend/scripts/data_repair/eastgate_backfill_salutations.py
#          backend/utils/name_utils.py

Titles were being stored inside the name itself — `legal_name = 'Ms Rachel Clarke'`,
`full_name = 'Mr Yushan Han'`. 43 parties and 34 users at East Gate carry one.

Nothing is currently corrupted by this: `first_name`/`last_name` are NULL rather than
holding "Ms", so no structured field is polluted. It is a modelling problem, and it bites
at the edges — anything deriving a value from the name has to strip the title first, and
every place that does so is a separate chance to forget. The mailbox derivation added on
2026-08-27 needed exactly that strip to avoid producing `ua001.mr.han@…`.

Nullable with no default and no backfill in the DDL. Separating "Ms Rachel Clarke" into
its parts is a data judgement — some legal names legitimately begin with a word that
looks like a title, and a person may have no salutation at all — so it belongs in a
reviewable script with a dry-run, not in a migration that runs unattended during deploy.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0097_party_user_salutation"
down_revision = "0096_user_activation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # VARCHAR(20) rather than TEXT: a salutation is a short closed-ish set ("Mr", "Ms",
    # "Dr", "Prof"), and the cap makes a mis-parsed full name fail loudly on insert
    # instead of silently storing a whole name in the honorific column.
    op.execute(text("ALTER TABLE core.users ADD COLUMN IF NOT EXISTS salutation VARCHAR(20)"))
    op.execute(text("ALTER TABLE core.parties ADD COLUMN IF NOT EXISTS salutation VARCHAR(20)"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE core.parties DROP COLUMN IF EXISTS salutation"))
    op.execute(text("ALTER TABLE core.users DROP COLUMN IF EXISTS salutation"))
