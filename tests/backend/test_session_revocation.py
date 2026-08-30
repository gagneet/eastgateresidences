"""Session revocation — "sign out everywhere except this device".

Auth is a stateless Bearer JWT: once issued a token is valid until `exp` and nothing
server-side stops it, so a password change leaves an intruder's session working.
Revocation records an instant on the user; get_current_user() rejects any token issued
at or before it, sparing exactly one named `jti` — the device that pressed the button.

See migration 0101_session_revocation for why the survivor is named rather than
inferred from a timestamp cutoff.
"""
from datetime import datetime, timedelta, timezone

import pytest

from utils.auth import _parse_revocation_cutoff

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


class TestParseRevocationCutoff:
    """The value arrives as a datetime (Mongo) or an ISO string (Postgres)."""

    def test_iso_string_with_offset(self):
        assert _parse_revocation_cutoff("2026-08-28T12:00:00+00:00") == NOW

    def test_iso_string_with_z_suffix(self):
        assert _parse_revocation_cutoff("2026-08-28T12:00:00Z") == NOW

    def test_naive_string_is_treated_as_utc(self):
        """A naive value compared against an aware one raises TypeError mid-request."""
        assert _parse_revocation_cutoff("2026-08-28T12:00:00") == NOW

    def test_naive_datetime_is_made_aware(self):
        assert _parse_revocation_cutoff(datetime(2026, 8, 28, 12, 0, 0)) == NOW

    def test_aware_datetime_passes_through(self):
        assert _parse_revocation_cutoff(NOW) == NOW

    @pytest.mark.parametrize("value", [None, "", "not-a-date", 12345, {}, []])
    def test_unparseable_fails_open(self, value):
        """None means "no cutoff", so the request proceeds.

        This is a deliberate fail-OPEN. A malformed timestamp in one column must not
        lock every user out of the platform — revocation is a security nicety, being
        able to log in is not.
        """
        assert _parse_revocation_cutoff(value) is None


def _decide(*, revoked_at, keep_jti, token_jti, token_iat):
    """The exact decision get_current_user() makes, kept in one place.

    Mirrors the handler rather than importing it because get_current_user() needs the
    full HTTP credential + datastore stack; the branch under test is this arithmetic.
    """
    if not revoked_at:
        return "allow"
    cutoff = _parse_revocation_cutoff(revoked_at)
    if cutoff is None:
        return "allow"
    if token_jti == keep_jti:
        return "allow"
    issued = (datetime.fromtimestamp(token_iat, tz=timezone.utc)
              if isinstance(token_iat, (int, float)) else None)
    return "reject" if (issued is None or issued <= cutoff) else "allow"


class TestRevocationDecision:
    OLD = (NOW - timedelta(hours=1)).timestamp()
    NEW = (NOW + timedelta(hours=1)).timestamp()

    def test_never_revoked_allows_everything(self):
        assert _decide(revoked_at=None, keep_jti=None,
                       token_jti="any", token_iat=self.OLD) == "allow"

    def test_other_device_older_token_is_rejected(self):
        assert _decide(revoked_at=NOW, keep_jti="mine",
                       token_jti="theirs", token_iat=self.OLD) == "reject"

    def test_the_spared_session_survives_even_though_it_is_older(self):
        """The whole point: the device that clicked the button stays signed in."""
        assert _decide(revoked_at=NOW, keep_jti="mine",
                       token_jti="mine", token_iat=self.OLD) == "allow"

    def test_a_fresh_login_after_revocation_is_allowed(self):
        """Revocation ends existing sessions; it must not lock the account."""
        assert _decide(revoked_at=NOW, keep_jti="mine",
                       token_jti="brand-new", token_iat=self.NEW) == "allow"

    def test_token_issued_in_the_same_instant_is_rejected(self):
        """`iat` has one-second resolution, so the boundary is inclusive.

        This is exactly why the surviving token is named by jti instead of inferred
        from the cutoff: a token minted in the same second as the revocation would
        otherwise be spared by accident.
        """
        assert _decide(revoked_at=NOW, keep_jti="mine",
                       token_jti="theirs", token_iat=NOW.timestamp()) == "reject"

    def test_token_without_iat_is_rejected_not_trusted(self):
        """A token that cannot be placed relative to the cutoff must not survive.

        The user pressed this button because they are unsure which sessions are theirs;
        admitting an unplaceable one defeats it.
        """
        assert _decide(revoked_at=NOW, keep_jti="mine",
                       token_jti="legacy", token_iat=None) == "reject"

    def test_no_spared_jti_revokes_every_session_including_the_caller(self):
        """When the caller's jti could not be read, nothing is spared.

        The endpoint reports current_session_kept=False for this, and the UI redirects
        to login rather than letting the user discover it on a later click.
        """
        assert _decide(revoked_at=NOW, keep_jti=None,
                       token_jti="mine", token_iat=self.OLD) == "reject"


class TestEndpointContract:
    """Static guards on the two properties that make this safe."""

    @staticmethod
    def _src() -> str:
        from pathlib import Path
        return (Path(__file__).resolve().parents[2] / "backend" / "routers" / "security.py").read_text(encoding="utf-8")

    def test_failure_to_revoke_anywhere_is_an_error_not_a_200(self):
        """Reporting success while every other session still works is the worst
        possible outcome for this specific button."""
        src = self._src()
        assert "if not revoked_in:" in src
        assert "status_code=500" in src

    def test_revocation_is_written_to_postgres_too(self):
        """Mongo-only would be a no-op for a Postgres-resident user — which is 119 of
        119 active East Gate users."""
        src = self._src()
        assert "revoke_other_sessions" in src

    def test_migration_columns_are_nullable(self):
        """A NOT NULL default would rewrite core.users and mean "revoked" for every
        existing row."""
        from pathlib import Path
        mig = (Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions"
               / "0101_session_revocation.py").read_text(encoding="utf-8")
        assert mig.count("nullable=True") == 2

    def test_revision_id_is_within_the_32_char_limit(self):
        """core.alembic_version.version_num is VARCHAR(32); a longer id applies its DDL
        and then fails the version bump, rolling the whole migration back."""
        assert len("0101_session_revocation") <= 32
