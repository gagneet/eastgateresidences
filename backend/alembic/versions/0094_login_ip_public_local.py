"""0094 — Record the public AND local IP of a login, not one conflated value.

## Why

``core.users.last_login_ip`` holds a single address, written from
``geo.get_real_ip()``. When that resolved to something internal — ``10.0.0.7``,
``172.18.0.1``, ``127.0.0.1`` — the dashboard showed an internal address and
there was no way to tell which of three different situations had occurred:

1. the proxy forwarded no ``X-Real-IP``/``X-Forwarded-For`` at all;
2. the proxy's own address is missing from ``TRUSTED_PROXY_CIDRS``, so the
   headers were present but deliberately ignored as untrustworthy; or
3. the caller genuinely is on the local network.

All three look identical in one column, and the remedy for each is different.
Splitting the value makes them distinguishable at a glance, which is what an
operator actually needs from a security log.

## What this adds

``last_login_public_ip``  the globally-routable client address, when one could
                          be established from a TRUSTED proxy header
``last_login_local_ip``   the address the TCP connection actually came from —
                          usually the proxy or container bridge

Either may be NULL, and NULL is meaningful: "no public address was
established" is exactly the diagnosis worth surfacing. They are deliberately
NOT backfilled from each other.

``last_login_ip`` is retained and keeps its existing meaning (best-known single
value, public preferred) so every existing reader, index and export continues to
work untouched. See ``backend/utils/client_ip.py``.

## Type choice

``inet`` rather than ``text``. Postgres validates the literal on write, so a
malformed address fails loudly at insert rather than sitting in the security log
looking plausible. ``last_login_ip`` is left as-is — changing its type would
require rewriting existing rows and is not what this change is for.

## Backfill

None. Historical rows keep ``last_login_ip`` and get NULL for both new columns,
which is honest: we cannot retroactively know whether an old value was the
client or the proxy. That is the ambiguity being fixed going forward.
"""

from alembic import op

revision = "0094_login_ip_public_local"
down_revision = "0093_authorisation_acl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the public/local login IP columns to core.users."""
    # IF NOT EXISTS so a re-run is a no-op — deploys apply migrations
    # unconditionally and a half-applied environment must not block.
    op.execute(
        """
        ALTER TABLE core.users
            ADD COLUMN IF NOT EXISTS last_login_public_ip inet,
            ADD COLUMN IF NOT EXISTS last_login_local_ip  inet
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN core.users.last_login_public_ip IS
            'Globally-routable client address from a TRUSTED proxy header. '
            'NULL means no public address could be established — see '
            'backend/utils/client_ip.py'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN core.users.last_login_local_ip IS
            'Address the TCP connection arrived from (proxy or container bridge). '
            'Never spoofable, so useful as corroboration for the public address.'
        """
    )


def downgrade() -> None:
    """Drop the columns. last_login_ip is untouched, so readers keep working."""
    op.execute(
        """
        ALTER TABLE core.users
            DROP COLUMN IF EXISTS last_login_public_ip,
            DROP COLUMN IF EXISTS last_login_local_ip
        """
    )
