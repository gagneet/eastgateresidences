# @featuretrace:owner-activation — Login must refuse an unclaimed account, and a reset must reach Postgres.
# Layer: test
# Data flow: /auth/login -> identity_repo.activation_state -> 403 activation_required (building-scoped).
# Related: backend/routers/auth.py
#          backend/db_postgres/repos/identity_repo.py
#          backend/alembic/versions/0096_user_activation_state.py
"""The two halves of owner activation, and why each is load-bearing.

Restoring East Gate's owners exposed a split between the stores. MongoDB held 106
accounts as is_active=False with no password_hash — unusable, which was the intent.
PostgreSQL held the SAME accounts as is_active=TRUE, is_approved=TRUE, carrying their
pre-purge password hashes. Login resolves Postgres first, so the Mongo state was
decorative: an owner's old password opened an account nobody had claimed.

The gate closes that. The password sync is what makes the gate escapable by the
legitimate route — without it, setting a password writes to a store login never reads,
so the owner would be locked out permanently by the very step meant to let them in.
"""

import re
from pathlib import Path

import pytest

AUTH_SRC = (Path(__file__).resolve().parents[2] / "backend" / "routers" / "auth.py").read_text()
REPO_SRC = (Path(__file__).resolve().parents[2] / "backend" / "db_postgres" / "repos"
            / "identity_repo.py").read_text()


class TestActivationGateExists:
    def test_login_consults_the_activation_state(self):
        assert "activation_state" in AUTH_SRC, "login must check whether the account is claimed"

    def test_the_gate_returns_a_machine_readable_code(self):
        """The frontend routes on the code, not on the prose."""
        assert '"activation_required"' in AUTH_SRC
        assert '"next_step": "reset_password"' in AUTH_SRC

    def test_the_gate_runs_after_the_password_verify(self):
        """Answering before the verify would let anyone probe which accounts exist."""
        verify = AUTH_SRC.index("is_valid = verify_password(")
        gate = AUTH_SRC.index("activation_state(credentials.email)")
        assert gate > verify, "the activation gate must not precede the password check"

    def test_a_lookup_failure_does_not_lock_everyone_out(self):
        """A broken gate must fail open — it is a claim check, not an authorisation."""
        window = AUTH_SRC[AUTH_SRC.index("activation gate lookup failed") - 400:
                          AUTH_SRC.index("activation gate lookup failed") + 200]
        assert "except Exception" in window


class TestPasswordReachesPostgres:
    """Login reads core.users first, so a Mongo-only write is invisible to auth."""

    def test_the_repo_exposes_a_postgres_password_write(self):
        assert "async def set_password_hash" in REPO_SRC

    @pytest.mark.parametrize("endpoint", [
        "reset_password", "change_password", "admin_reset_user_passwords",
    ])
    def test_every_password_path_syncs_to_postgres(self, endpoint):
        """All three wrote to MongoDB alone, so a new password simply did not work."""
        start = AUTH_SRC.index(f"async def {endpoint}(")
        nxt = AUTH_SRC.find("\n@router.", start)
        body = AUTH_SRC[start: nxt if nxt != -1 else len(AUTH_SRC)]
        assert "set_password_hash" in body, (
            f"{endpoint} updates MongoDB only; login resolves Postgres first, so the "
            f"user's new password would be rejected while the old one kept working"
        )

    def test_setting_a_password_is_what_clears_the_activation_gate(self):
        start = AUTH_SRC.index("async def reset_password(")
        nxt = AUTH_SRC.find("\n@router.", start)
        body = AUTH_SRC[start:nxt]
        assert "mark_activated" in body, "the reset is the activation step"


class TestMigration:
    def test_revision_id_is_within_the_32_char_limit(self):
        """core.alembic_version.version_num is VARCHAR(32) — a longer id rolls back."""
        mig = (Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions"
               / "0096_user_activation_state.py").read_text()
        rev = re.search(r'^revision = "([^"]+)"', mig, re.M).group(1)
        assert len(rev) <= 32, f"{rev} is {len(rev)} chars"

    def test_the_column_defaults_to_false(self):
        """Existing accounts must be unaffected; marking restored owners is separate."""
        mig = (Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions"
               / "0096_user_activation_state.py").read_text()
        assert "requires_activation BOOLEAN NOT NULL DEFAULT FALSE" in mig
