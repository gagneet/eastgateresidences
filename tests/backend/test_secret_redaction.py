"""
test_secret_redaction.py — guards the redaction applied to subprocess output
===========================================================================
The scraper endpoints (`POST /blog/scrape`, `POST /listings/scrape`) capture a
child process's stdout/stderr and then push it to three sinks:

  1. `logs/{news,property}_scraper.log` on disk, and
  2. Mongo — `scraper_run_logs.error_message` and `scraper_settings.*.error_message`,
     both of which the Scraper Settings admin UI reads back.

(The HTTP body is already covered: `_normalise_detail()` discards the detail of
any response with status >= 500, so the 500 these endpoints raise never echoed
stderr to the client. Redaction is defence in depth there, and the actual fix for
the two sinks above.)

The child re-reads `backend/.env` itself via `load_dotenv()`, so restricting the
environment the parent hands it achieves nothing — the child legitimately holds
the full secret set. The control that does work is redacting what comes back.

The concrete case: `MONGO_URL` and `DATABASE_URL` both embed a password, and
pymongo/asyncpg connection failures echo the whole URI in their exception text.
Without redaction a transient DB outage would publish the database password to
an HTTP client, a log file, and a database row in one go.

Run:
  backend/venv/bin/python3 -m pytest tests/backend/test_secret_redaction.py -q
"""

import os
import sys
from unittest.mock import patch

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, os.path.abspath(_BACKEND))

from utils.error_response import redact_secrets  # noqa: E402


class TestConnectionUriRedaction:
    """Pattern-based: catches credentials even for secrets this process never holds."""

    def test_mongo_connection_error_does_not_leak_the_password(self):
        raw = (
            "pymongo.errors.ServerSelectionTimeoutError: "
            "mongodb://demo_db_admin:SuperSecret123@localhost:27018/?authSource=admin: [Errno 111]"
        )
        out = redact_secrets(raw)

        assert "SuperSecret123" not in out
        # The username and host survive — they are what makes the error diagnosable.
        assert "demo_db_admin" in out
        assert "localhost:27018" in out

    def test_postgres_dsn_password_is_redacted(self):
        raw = "could not connect: postgresql+asyncpg://demo_db_user:pgpass1234@127.0.0.1:5432/demo_database"
        out = redact_secrets(raw)

        assert "pgpass1234" not in out
        assert "demo_db_user" in out

    def test_redacts_a_credential_this_process_does_not_hold(self):
        """The URI pattern must not depend on the value being in os.environ."""
        raw = "amqp://svc_user:neverSeenBefore99@broker.internal:5672/"
        out = redact_secrets(raw)

        assert "neverSeenBefore99" not in out

    def test_multiple_uris_in_one_blob_are_all_redacted(self):
        raw = (
            "mongodb://a_user:mongoPass123@h1:27017/ then "
            "postgresql://b_user:pgPass456@h2:5432/db"
        )
        out = redact_secrets(raw)

        assert "mongoPass123" not in out
        assert "pgPass456" not in out


class TestEnvironmentValueRedaction:
    """Value-based: catches a library dumping its own config."""

    def test_api_key_printed_bare_is_redacted_by_name(self):
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_live_ABCDEFGHIJKLMNOP"}):
            out = redact_secrets("AuthenticationError: key sk_live_ABCDEFGHIJKLMNOP is invalid")

        assert "sk_live_ABCDEFGHIJKLMNOP" not in out
        # Names the variable so the error stays actionable.
        assert "STRIPE_SECRET_KEY" in out

    def test_a_newly_added_secret_is_covered_without_code_changes(self):
        """Redaction keys off the NAME, so house-convention names are automatic."""
        with patch.dict(os.environ, {"SOME_NEW_PROVIDER_API_KEY": "brand-new-value-12345"}):
            out = redact_secrets("provider rejected brand-new-value-12345")

        assert "brand-new-value-12345" not in out

    def test_short_values_are_not_redacted(self):
        """Blanket-replacing a tiny value would mangle unrelated text."""
        with patch.dict(os.environ, {"TINY_SECRET": "ab"}):
            out = redact_secrets("the abbreviation ab appears in ordinary prose")

        assert out == "the abbreviation ab appears in ordinary prose"

    def test_non_secret_env_vars_are_left_alone(self):
        with patch.dict(os.environ, {"BUILDING_ID": "13195"}):
            out = redact_secrets("scraping for building 13195 completed")

        assert "13195" in out


class TestCallerSafety:
    """Callers use the result unconditionally, so it must never return None."""

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_input_returns_empty_string(self, value):
        assert redact_secrets(value) == ""

    def test_clean_text_passes_through_unchanged(self):
        raw = "Exit code 1: Traceback (most recent call last): ValueError: bad row"
        assert redact_secrets(raw) == raw


class TestDisabledPdfImporter:
    """`POST /owners-units/import-from-pdf` must fail closed, and say why.

    The importer it drove was archived; its write path is
    `db.owners_units.delete_many({})` with no building filter anywhere in the
    script, so running it from any building would wipe every building's owner
    records. See tasks/GAP-SEC-001.
    """

    @pytest.mark.asyncio
    async def test_returns_410_with_an_actionable_reason(self):
        import server
        from fastapi import HTTPException
        from models.user import UserRole

        with pytest.raises(HTTPException) as exc:
            await server.import_owners_from_pdf(
                current_user={"id": "sa-1", "role": UserRole.SUPER_ADMIN},
                building_id="13195",
            )

        # 410, NOT 501: utils/error_response._normalise_detail() discards the detail
        # of any response with status >= 500, so a 501 would reach the admin as a
        # bare "Something went wrong on our side." and explain nothing.
        assert exc.value.status_code == 410
        assert exc.value.detail["code"] == "IMPORTER_DISABLED"
        assert "GAP-SEC-001" in exc.value.detail["message"]

    @pytest.mark.asyncio
    async def test_non_super_admin_is_rejected_before_the_disabled_notice(self):
        """Authorisation still comes first — the 403 must not become a 410."""
        import server
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await server.import_owners_from_pdf(
                current_user={"id": "mgr-1", "role": "strata_manager"},
                building_id="13195",
            )

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_a_user_document_without_a_role_is_denied_not_500(self):
        """Was `current_user["role"]` — a KeyError surfacing as a 500, not a 403."""
        import server
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await server.import_owners_from_pdf(current_user={"id": "x"}, building_id="13195")

        assert exc.value.status_code == 403


class TestRedosResistance:
    """The URI pattern runs over scraper stdout, which carries externally-scraped text.

    The original unbounded pattern was super-linear: `re.sub` restarts at each
    offset and an unbounded scheme quantifier rescans the tail every time. It took
    ~14.8s on a 100 KB run of one repeated character and could not finish 1 MB.
    Every quantifier is now explicitly bounded. These are wall-clock guards, so the
    thresholds are deliberately loose — they exist to catch a return to quadratic
    behaviour, not to benchmark the machine.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "mongodb://" + "a" * 100_000,        # long user, no terminating '@'
            "mongodb://" + "a:" * 50_000,        # many colons
            "mongodb://" + "a:b@" * 25_000,      # many at-signs
            "x" * 500_000,                       # no scheme at all
            "://" * 50_000,                      # scheme punctuation only
        ],
        ids=["long-user", "many-colons", "many-ats", "no-scheme", "punctuation"],
    )
    def test_pathological_input_completes_quickly(self, payload):
        import time

        start = time.perf_counter()
        redact_secrets(payload)
        elapsed = time.perf_counter() - start

        assert elapsed < 5.0, (
            f"redact_secrets took {elapsed:.1f}s on a {len(payload)}-char input — "
            "the URI pattern has likely lost its bounded quantifiers and gone quadratic"
        )

    def test_a_credential_longer_than_the_bound_is_not_silently_truncated(self):
        """Over-long values fall outside the bound: not redacted, but not mangled either."""
        over_long = "b" * 300
        raw = f"mongodb://user:{over_long}@host:27017/"
        out = redact_secrets(raw)

        # Unmatched by the bounded pattern, so it passes through untouched — the
        # string is never partially rewritten in a way that would corrupt the log.
        assert out == raw


class TestNonSecretValuesAreNotRedacted:
    """Over-redaction destroys the diagnostic value of the very logs this protects."""

    def test_public_portal_url_survives(self):
        """FRONTEND_URL/API_URL/APP_URL are public endpoints, not secrets.

        Matching them turned ordinary log lines into '***FRONTEND_URL***/dashboard'.
        Credential-bearing URLs are still covered by the URI pattern, which redacts
        only the password and keeps the host.
        """
        with patch.dict(os.environ, {"FRONTEND_URL": "https://www.example-strata.test"}):
            out = redact_secrets("portal link https://www.example-strata.test/dashboard ok")

        assert "https://www.example-strata.test/dashboard" in out
        assert "***FRONTEND_URL***" not in out

    def test_stripe_publishable_key_survives(self):
        """A publishable key is designed to ship to browsers."""
        with patch.dict(os.environ, {"STRIPE_PUBLISHABLE_KEY": "pk_live_VISIBLE12345"}):
            out = redact_secrets("stripe init with pk_live_VISIBLE12345")

        assert "pk_live_VISIBLE12345" in out

    def test_the_secret_stripe_key_is_still_redacted(self):
        """Guards the PUBLIC/PUBLISHABLE carve-out from being too broad."""
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_live_HIDDEN12345"}):
            out = redact_secrets("stripe rejected sk_live_HIDDEN12345")

        assert "sk_live_HIDDEN12345" not in out

    def test_mongo_url_password_still_redacted_but_host_kept(self):
        """Dropping URL$ from the name pass must not lose credential redaction."""
        with patch.dict(os.environ, {"MONGO_URL": "mongodb://u:pw123456@db.internal:27017/x"}):
            out = redact_secrets("connect failed: mongodb://u:pw123456@db.internal:27017/x")

        assert "pw123456" not in out
        assert "db.internal:27017" in out, "the host must survive — it is what makes the error useful"

    def test_uuids_and_hashes_are_untouched(self):
        raw = (
            "request 550e8400-e29b-41d4-a716-446655440000 "
            "file hash d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2"
        )
        assert redact_secrets(raw) == raw


class TestFailsClosed:
    """A redactor that returns its input on error is worse than none at all.

    Callers trust the result enough to write it to a log file and a Mongo document,
    so an internal failure must withhold the text rather than pass it through.
    """

    def test_bytes_input_is_decoded_and_still_redacted(self):
        """A future `text=False` on subprocess.run would hand this bytes."""
        out = redact_secrets(b"mongodb://u:byteSecret99@host:27017/")

        assert "byteSecret99" not in out
        assert "***@" in out

    def test_non_string_input_does_not_raise(self):
        for value in (12345, {"a": 1}, ["x"], object()):
            assert isinstance(redact_secrets(value), str)

    def test_internal_failure_withholds_the_text_rather_than_returning_it(self):
        """The whole point: on error, never emit the unredacted original."""
        secret_bearing = "mongodb://u:MustNotEscape123@host:27017/"

        class _ExplodingPattern:
            def sub(self, *_a, **_kw):
                raise RuntimeError("boom")

        # Patch the module-level name: a compiled re.Pattern's attributes are read-only.
        with patch("utils.error_response._URI_CREDENTIALS_RE", _ExplodingPattern()):
            out = redact_secrets(secret_bearing)

        assert "MustNotEscape123" not in out
        assert out == "[redaction failed — output withheld]"
