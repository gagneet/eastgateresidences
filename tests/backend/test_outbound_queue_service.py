# @featuretrace:outbound-message-queue — State machine, hold window and 48h expiry.
# Layer: test
# Data flow: enqueue -> held -> sendable_reason gates -> claim -> sent | cancelled | expired (building-scoped).
# Related: backend/services/outbound_queue_service.py
#          backend/models/outbound_message.py
"""The queue's decision logic, tested without a database.

The gates are pure functions on (message, settings, now) precisely so they can be
tested at arbitrary points in time. The 48-hour expiry and the 30-second hold are the
two behaviours a clock-dependent test would otherwise have to sleep for.
"""

from datetime import datetime, timedelta, timezone

import pytest

from models.outbound_message import (
    DEFAULT_EXPIRY_HOURS,
    DEFAULT_HOLD_SECONDS,
    MessageStatus,
    expires_at_iso,
    hold_until_iso,
    is_due,
    is_expired,
)
from services.outbound_queue_service import resolve_hold_seconds, sendable_reason

T0 = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
ENABLED = {"enabled": True, "hold_seconds": 30, "expiry_hours": 48, "disabled_categories": []}


def msg(**over):
    base = {
        "status": MessageStatus.HELD.value,
        "category": "automated",
        "hold_until": hold_until_iso(30, now=T0),
        "expires_at": expires_at_iso(48, now=T0),
    }
    base.update(over)
    return base


class TestHoldWindow:
    def test_not_due_inside_the_window(self):
        assert is_due(msg(), now=T0 + timedelta(seconds=29)) is False

    def test_due_once_the_window_elapses(self):
        assert is_due(msg(), now=T0 + timedelta(seconds=31)) is True

    def test_default_hold_is_gmails_maximum_of_30_seconds(self):
        assert DEFAULT_HOLD_SECONDS == 30

    def test_a_caller_may_lengthen_the_hold_but_never_shorten_it(self):
        assert resolve_hold_seconds(5, 30) == 30, "the building's hold is a floor"
        assert resolve_hold_seconds(120, 30) == 120
        assert resolve_hold_seconds(None, 30) == 30
        assert resolve_hold_seconds(-10, 30) == 30


class TestSendGates:
    def test_a_due_message_on_an_enabled_queue_may_send(self):
        ok, why = sendable_reason(msg(), ENABLED, now=T0 + timedelta(minutes=1))
        assert ok is True and why == ""

    def test_a_disabled_queue_holds_the_message_rather_than_dropping_it(self):
        ok, why = sendable_reason(msg(), {**ENABLED, "enabled": False},
                                  now=T0 + timedelta(minutes=1))
        assert ok is False
        assert "disabled" in why

    def test_a_disabled_category_holds_only_that_category(self):
        settings = {**ENABLED, "disabled_categories": ["automated"]}
        ok, _ = sendable_reason(msg(category="automated"), settings, now=T0 + timedelta(minutes=1))
        assert ok is False
        ok2, _ = sendable_reason(msg(category="manual"), settings, now=T0 + timedelta(minutes=1))
        assert ok2 is True

    def test_the_reason_is_specific_enough_to_act_on(self):
        _, why = sendable_reason(msg(), ENABLED, now=T0 + timedelta(seconds=5))
        assert "hold window" in why, "an operator must see WHICH gate is holding it"

    def test_a_non_held_message_is_never_resent(self):
        for status in (MessageStatus.SENT, MessageStatus.CANCELLED, MessageStatus.EXPIRED):
            ok, _ = sendable_reason(msg(status=status.value), ENABLED,
                                    now=T0 + timedelta(minutes=1))
            assert ok is False, f"{status} must not be sendable"


class TestExpiry:
    def test_still_releasable_at_47_hours(self):
        m = msg()
        assert is_expired(m, now=T0 + timedelta(hours=47)) is False
        ok, _ = sendable_reason(m, ENABLED, now=T0 + timedelta(hours=47))
        assert ok is True, "re-enabling email inside the window must release held mail"

    def test_expired_at_49_hours(self):
        assert is_expired(msg(), now=T0 + timedelta(hours=49)) is True

    def test_expiry_beats_an_enabled_queue(self):
        """A message that timed out must not go out just because email came back on."""
        ok, why = sendable_reason(msg(), ENABLED, now=T0 + timedelta(hours=49))
        assert ok is False and why == "expired"

    def test_default_window_is_48_hours(self):
        assert DEFAULT_EXPIRY_HOURS == 48

    def test_a_sent_message_is_not_retroactively_expired(self):
        assert is_expired(msg(status=MessageStatus.SENT.value),
                          now=T0 + timedelta(hours=99)) is False


class TestTimestampOrdering:
    def test_iso_strings_compare_correctly_across_the_window(self):
        """The gates compare ISO strings, which is only safe for UTC-aware values."""
        assert hold_until_iso(30, now=T0) < expires_at_iso(48, now=T0)
        # A zero hold is immediately due: the string must not sort after "now".
        assert hold_until_iso(0, now=T0) <= T0.isoformat()
        # An hour later must sort after, not before — catches a naive-datetime regression.
        assert hold_until_iso(30, now=T0) < hold_until_iso(30, now=T0 + timedelta(hours=1))


class TestSuppressedIsNotFailed:
    """A policy refusal must not consume the retry budget.

    The kill switch and the recipient-domain allowlist both refuse messages, and both
    are policies that can change — lifting EMAIL_SEND_DISABLED_ALL, or adding a domain,
    should release everything still inside its window.

    Counting a refusal as a delivery attempt burns all three attempts in ninety seconds
    on a 30-second tick and lands the message in FAILED: untrue, and unrecoverable. An
    operator who queued 83 activation invitations under an active kill switch would have
    opened the console to 83 failures for messages that were correctly held.

    Verified against the live worker on 2026-08-27: three consecutive ticks left the
    message at status=held, attempts=0.
    """

    def test_the_service_exposes_a_no_attempt_return_path(self):
        from services import outbound_queue_service as svc

        assert hasattr(svc, "return_to_held"), (
            "the worker needs a way to release a claimed message without counting an "
            "attempt against it"
        )

    def test_return_to_held_does_not_touch_attempts(self):
        """The distinguishing property. If this ever sets attempts, FAILED returns."""
        import inspect

        from services import outbound_queue_service as svc

        body = inspect.getsource(svc.return_to_held)
        assert "attempts" not in body.split('"""')[-1], (
            "return_to_held must leave attempts unchanged — incrementing it re-creates "
            "the bug where a suppressed message is marked FAILED after three ticks"
        )

    def test_the_worker_routes_suppression_away_from_the_failure_path(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2] / "backend" / "cron"
               / "cron_outbound_queue.py").read_text()
        suppressed_at = src.index('result.get("suppressed")')
        failed_at = src.index("await mark_attempt_failed(", suppressed_at)
        return_at = src.index("await return_to_held(", suppressed_at)
        assert return_at < failed_at, (
            "the suppressed branch must return the message to HELD, not fall through to "
            "mark_attempt_failed"
        )
