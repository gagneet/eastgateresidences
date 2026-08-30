"""Per-user session revocation so "sign out everywhere" can invalidate live JWTs.

Revision ID: 0101_session_revocation
Revises: 0100_by_law_breach_toggle
Create Date: 2026-08-28

# @featuretrace:security-ip-logging — token revocation state read on every authenticated request.
# Layer: migration
# Data flow: POST /security/sign-out-everywhere -> core.users.sessions_invalidated_at
#            -> get_current_user() rejects older tokens -> only the calling session survives.
# Related: backend/utils/auth.py (get_current_user)
#          backend/routers/security.py (sign-out-everywhere)
#          backend/db_postgres/repos/identity_repo.py (revoke_other_sessions)

Auth is a stateless Bearer JWT: once issued, a token is valid until `exp` and nothing
server-side can stop it. That is fine until someone sees their account signed in from
a place they do not recognise, at which point "change your password" does nothing to
the session already holding a token.

Revocation needs one durable fact per user, so it lives on core.users rather than in a
side table: get_current_user() already loads that row on every authenticated request,
so the check costs no extra query.

  sessions_invalidated_at  every token issued at or before this instant is dead
  session_keep_jti         the ONE token spared, so the user is not logged out of the
                           device they clicked the button on

Why a jti exception and not simply a cutoff: `iat` has one-second resolution, so a
cutoff of "now" would also kill the caller's own token, and a cutoff of "the caller's
iat" would spare every other token minted in that same second. Naming the surviving
token is exact.

Both columns are nullable with no default, so this does not rewrite the table
(PostgreSQL 11+ only rewrites for a volatile default). NULL means "never revoked",
which is the correct state for every existing row.
"""
import sqlalchemy as sa
from alembic import op

revision = "0101_session_revocation"
down_revision = "0100_by_law_breach_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("sessions_invalidated_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )
    # TEXT, not UUID: jti is an opaque token identifier. It happens to be a uuid4 today,
    # but constraining the column would turn a future format change into a login outage.
    op.add_column(
        "users",
        sa.Column("session_keep_jti", sa.Text(), nullable=True),
        schema="core",
    )


def downgrade() -> None:
    op.drop_column("users", "session_keep_jti", schema="core")
    op.drop_column("users", "sessions_invalidated_at", schema="core")
