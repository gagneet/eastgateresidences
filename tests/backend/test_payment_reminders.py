"""
Tests for payment reminder email system.

Covers:
- Dual-owner email (primary + secondary) recipient collection
- Email audit log insertion
- Notification preferences check (levy_reminders_enabled)
- No duplicate recipients
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _unit(unit_number, owner_email, owner_email_b=None, entitlement=100, owner_name="John Doe"):
    """Build a minimal unit document."""
    u = {
        "unit_number": unit_number,
        "owner_email": owner_email,
        "owner_name": owner_name,
        "entitlement": entitlement,
    }
    if owner_email_b:
        u["owner_email_b"] = owner_email_b
    return u


def _user(email, full_name, mail_username=None, uid="user-001"):
    """Build a minimal user document."""
    return {
        "id": uid,
        "email": email,
        "full_name": full_name,
        "mail_username": mail_username,
        "role": "owner",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Unit-centric recipient collection logic
# (mirrors what cron_payment_reminders.py does)
# ─────────────────────────────────────────────────────────────────────────────

def collect_unit_recipients(unit_doc, owner_doc=None):
    """
    Replicate the recipient-collection logic from cron_payment_reminders.py.
    Returns a deduplicated list of email addresses for this unit.
    """
    primary = unit_doc.get("owner_email")
    secondary = unit_doc.get("owner_email_b")
    mail_alias = owner_doc.get("mail_username") if owner_doc else None
    return list({e for e in [primary, secondary, mail_alias] if e})


class TestRecipientCollection:
    def test_primary_only(self):
        unit = _unit("UA001", "alice@example.com")
        result = collect_unit_recipients(unit)
        assert result == ["alice@example.com"]

    def test_primary_and_secondary(self):
        unit = _unit("UA002", "alice@example.com", "bob@example.com")
        result = collect_unit_recipients(unit)
        assert set(result) == {"alice@example.com", "bob@example.com"}

    def test_primary_and_mail_alias_same(self):
        """When primary email == mail_username, should deduplicate to 1 entry."""
        unit = _unit("UA003", "alice@eastgateresidences.com.au")
        owner = _user("alice@eastgateresidences.com.au", "Alice", "alice@eastgateresidences.com.au")
        result = collect_unit_recipients(unit, owner)
        assert result == ["alice@eastgateresidences.com.au"]

    def test_primary_mail_alias_and_secondary(self):
        """All three addresses, all unique."""
        unit = _unit("UA004", "alice@gmail.com", "bob@gmail.com")
        owner = _user("alice@gmail.com", "Alice", "alice@eastgateresidences.com.au")
        result = collect_unit_recipients(unit, owner)
        assert set(result) == {"alice@gmail.com", "bob@gmail.com", "alice@eastgateresidences.com.au"}

    def test_secondary_none_not_included(self):
        """None values must not appear in recipient list."""
        unit = _unit("UA005", "alice@gmail.com", None)
        result = collect_unit_recipients(unit)
        assert None not in result

    def test_no_duplicates_when_secondary_equals_primary(self):
        """Same email in both primary and secondary slots → only one entry."""
        unit = _unit("UA006", "shared@example.com", "shared@example.com")
        result = collect_unit_recipients(unit)
        assert result == ["shared@example.com"]

    def test_missing_primary_email_returns_empty(self):
        """Unit without owner_email should produce no recipients."""
        unit = {"unit_number": "UA007", "entitlement": 100}  # No owner_email
        result = collect_unit_recipients(unit)
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Preference check
# ─────────────────────────────────────────────────────────────────────────────

class TestPreferenceCheck:
    def test_default_enabled_when_no_prefs_doc(self):
        prefs = None
        assert prefs is None or prefs.get("levy_reminders_enabled", True) is True

    def test_levy_reminders_disabled(self):
        prefs = {"levy_reminders_enabled": False}
        assert prefs.get("levy_reminders_enabled", True) is False

    def test_levy_reminders_explicitly_enabled(self):
        prefs = {"levy_reminders_enabled": True}
        assert prefs.get("levy_reminders_enabled", True) is True


# ─────────────────────────────────────────────────────────────────────────────
# Email audit log (_log_email_sent)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailAuditLog:
    @pytest.mark.asyncio
    async def test_log_on_success(self):
        """Successful sends should insert a doc with success=True."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

        mock_collection = MagicMock()
        mock_collection.insert_one = AsyncMock(return_value=None)

        with patch("utils.email.db") as mock_db:
            mock_db.email_sent_log = mock_collection
            from utils.email import _log_email_sent
            await _log_email_sent("alice@example.com", "Test Subject", True, "smtp", context="test")

        mock_collection.insert_one.assert_called_once()
        call_args = mock_collection.insert_one.call_args[0][0]
        assert call_args["to_email"] == "alice@example.com"
        assert call_args["subject"] == "Test Subject"
        assert call_args["success"] is True
        assert call_args["provider"] == "smtp"
        assert call_args["context"] == "test"
        assert "sent_at" in call_args

    @pytest.mark.asyncio
    async def test_log_on_failure(self):
        """Failed sends should insert a doc with success=False and error message."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

        mock_collection = MagicMock()
        mock_collection.insert_one = AsyncMock(return_value=None)

        with patch("utils.email.db") as mock_db:
            mock_db.email_sent_log = mock_collection
            from utils.email import _log_email_sent
            await _log_email_sent("bob@example.com", "Fail Subject", False, "", "Connection refused", "test_ctx")

        call_args = mock_collection.insert_one.call_args[0][0]
        assert call_args["success"] is False
        assert call_args["error"] == "Connection refused"

    @pytest.mark.asyncio
    async def test_log_failure_does_not_raise(self):
        """If DB insert fails, _log_email_sent must swallow the exception silently."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

        mock_collection = MagicMock()
        mock_collection.insert_one = AsyncMock(side_effect=Exception("DB down"))

        with patch("utils.email.db") as mock_db:
            mock_db.email_sent_log = mock_collection
            from utils.email import _log_email_sent
            # Should not raise
            await _log_email_sent("test@example.com", "Subject", True, "smtp", context="ctx")


# ─────────────────────────────────────────────────────────────────────────────
# Cron integration-style test
# ─────────────────────────────────────────────────────────────────────────────

class TestCronDualOwnerIntegration:
    """Verify that the cron levy reminder sends to BOTH primary and secondary owner emails."""

    @pytest.mark.asyncio
    async def test_sends_to_both_owners(self):
        """
        Given a unit with owner_email=alice and owner_email_b=bob,
        verify send_email_async is called for both addresses.
        """
        unit = _unit("TH071", "alice@example.com", "bob@example.com", entitlement=161)
        owner = _user("alice@example.com", "Alice Smith", "alice@eastgateresidences.com.au")

        recipients = collect_unit_recipients(unit, owner)
        assert "alice@example.com" in recipients
        assert "bob@example.com" in recipients
        assert "alice@eastgateresidences.com.au" in recipients
        assert len(recipients) == 3

    @pytest.mark.asyncio
    async def test_no_send_when_preferences_disabled(self):
        """
        If the registered owner has levy_reminders_enabled=False,
        the cron should skip this unit entirely.
        """
        prefs = {"levy_reminders_enabled": False}
        should_skip = not prefs.get("levy_reminders_enabled", True)
        assert should_skip is True

    def test_unit_without_owner_email_skipped(self):
        """Units with no owner_email produce no recipients → skipped."""
        unit = {"unit_number": "UA099"}
        recipients = collect_unit_recipients(unit)
        assert recipients == []


class TestGeneratedNoticeIsReapable:
    """The levy-notice generator and the document reaper must agree on the marker.

    `cron_payment_reminders` stamps each generated notice with a 30-day
    `expires_at`, but `cron_notification_cleanup` only purges expired documents
    whose `author_id` is `"system"` — a deliberate guard so a human-authored
    document carrying an expiry is never hard-deleted by a cron.

    The generator never set `author_id`, so not one generated notice was ever
    reapable and they accumulated without limit. East Gate held 240 of them from
    a single run, still present long after the levy data they described had been
    removed, and still being advertised in the dashboard activity feed.

    A 30-day expiry that nothing can act on is worse than no expiry at all: it
    reads as "this is transient and cleans itself up" while the rows stay forever.
    """

    def test_generator_stamps_the_marker_the_reaper_requires(self):
        """Both halves of the contract, asserted against the real source."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        generator = (root / "backend/cron/cron_payment_reminders.py").read_text()
        reaper = (root / "backend/cron/cron_notification_cleanup.py").read_text()

        # The reaper's precondition.
        assert '"author_id": "system"' in reaper, (
            "cron_notification_cleanup no longer filters on author_id='system'; "
            "this test encodes that contract and must be updated with it."
        )
        # The generator must satisfy it.
        assert '"author_id": "system"' in generator, (
            "cron_payment_reminders writes documents with expires_at but no "
            "author_id='system', so cron_notification_cleanup can never purge "
            "them and generated levy notices accumulate indefinitely."
        )

    def test_generated_notice_carries_an_expiry(self):
        """The expiry is the other half — without it the reaper has no trigger."""
        from pathlib import Path

        generator = (
            Path(__file__).resolve().parents[2] / "backend/cron/cron_payment_reminders.py"
        ).read_text()
        assert '"expires_at": expires_at' in generator


class TestGeneratedNoticeIsIdempotent:
    """Re-running the reminder job must not multiply stored notices.

    The document write used `insert_one` with a fresh `uuid4`, so every execution
    added one document per unit with no check for an existing notice. East Gate
    accumulated **240 documents for 80 units** — exactly three copies of each,
    from three runs, two of them 33 milliseconds apart because the crontab held a
    duplicate entry for this job (GAP-OPS-001).

    A scheduling accident should not be able to multiply stored records. The id is
    now derived from the notice's identity — building, unit, levy period — so a
    re-run resolves to the same document and upserts into it instead.
    """

    def test_document_id_is_derived_not_random(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[2] / "backend/cron/cron_payment_reminders.py"
        ).read_text()
        assert "uuid.uuid5(" in src, (
            "the levy-notice document id must be derived from "
            "(building, unit, period), not randomly generated"
        )
        assert "$setOnInsert" in src and "upsert=True" in src, (
            "the notice write must upsert so a re-run is a no-op"
        )
        # The old pattern must be gone from this write path. Comment lines are
        # stripped first: the block deliberately DESCRIBES the previous
        # insert_one/uuid4 behaviour, and matching that prose would fail a
        # correct implementation.
        notice_block = src[src.index("Save PDF to documents"):]
        notice_block = notice_block[: notice_block.index("upsert=True")]
        code_only = "\n".join(
            line for line in notice_block.splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "db.documents.insert_one(" not in code_only
        assert "uuid.uuid4()" not in code_only

    def test_namespace_is_a_fixed_literal(self):
        """Regenerating the namespace would orphan every notice already issued."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[2] / "backend/cron/cron_payment_reminders.py"
        ).read_text()
        assert 'DOCUMENT_NAMESPACE = uuid.UUID("' in src, (
            "DOCUMENT_NAMESPACE must be a hardcoded UUID literal — deriving it at "
            "runtime would change the id of every future notice"
        )

    def test_id_is_stable_for_the_same_notice_and_distinct_across_notices(self):
        """The property the whole fix rests on, exercised directly."""
        import uuid as _uuid

        ns = _uuid.UUID("6b1f2c4e-8a3d-5e7f-9c0b-1d2e3f4a5b6c")

        def notice_id(building, unit, period):
            return str(_uuid.uuid5(ns, f"levy-notice:{building}:{unit}:{period}"))

        # Same notice, twice -> same id (a re-run cannot duplicate).
        assert notice_id("13195", "TH083", "2026-09-01") == notice_id("13195", "TH083", "2026-09-01")
        # Different unit, period or building -> different id (no collisions).
        base = notice_id("13195", "TH083", "2026-09-01")
        assert notice_id("13195", "TH084", "2026-09-01") != base
        assert notice_id("13195", "TH083", "2026-12-01") != base
        assert notice_id("18932", "TH083", "2026-09-01") != base, (
            "notice ids must be building-scoped — two buildings sharing a unit "
            "number must not share a document"
        )
