# @featuretrace:outbound-message-queue — RBAC and honesty of the admin console endpoints.
# Layer: test
# Data flow: /outbound-messages/* -> outbound_queue_service -> outbound_messages (building-scoped).
# Related: backend/routers/outbound_messages.py
"""Console endpoints: who may use them, and whether they tell the truth.

Two classes of assertion here. The RBAC ones guard a real trust boundary — deciding
what mail leaves a building. The "honesty" ones guard against the console reporting an
action as done when the underlying gate refused it, which is the failure mode that
would make an operator trust a queue they should not.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from models.outbound_message import MessageStatus
from routers import outbound_messages as mod

BID = "13195"


def _user(role, effective=None):
    u = {"id": "u-1", "role": role}
    if effective:
        u["effective_role"] = effective
    return u


class TestRBAC:
    """Managing outgoing mail is operational, not committee governance."""

    @pytest.mark.parametrize("role", ["super_admin", "strata_admin", "strata_manager"])
    def test_operational_roles_are_allowed(self, role):
        assert mod._require_queue_admin(_user(role)) == role

    @pytest.mark.parametrize("role", ["owner", "tenant", "guest", "real_estate_agent"])
    def test_resident_roles_are_refused(self, role):
        with pytest.raises(HTTPException) as e:
            mod._require_queue_admin(_user(role))
        assert e.value.status_code == 403

    def test_ec_member_is_refused_deliberately(self):
        """EC members hold governance duties; releasing mail is an operational one.

        The same boundary is already drawn for work-order approvals in CLAUDE.md. If
        this is ever widened it should be a deliberate decision, not a silent drift.
        """
        with pytest.raises(HTTPException):
            mod._require_queue_admin(_user("ec_member"))

    def test_an_elevated_user_is_judged_on_their_effective_role(self):
        """An elevated user keeps role='owner'; a raw check would 403 them wrongly."""
        assert mod._require_queue_admin(
            _user("owner", effective="strata_manager")) == "strata_manager"


class TestCancelHonesty:
    @pytest.mark.asyncio
    async def test_cancelling_an_already_sent_message_is_a_409_not_a_fake_success(self):
        find_one = AsyncMock(return_value={"status": MessageStatus.SENT.value})
        with patch.object(mod, "cancel_message", new=AsyncMock(return_value=False)), \
             patch.object(mod, "db") as fake_db:
            fake_db.__getitem__.return_value.find_one = find_one
            with pytest.raises(HTTPException) as e:
                await mod.cancel_outbound_message(
                    "m-1", mod.CancelRequest(reason=""), _user("strata_manager"), BID)
        assert e.value.status_code == 409
        assert e.value.detail["code"] == "not_cancellable"
        assert e.value.detail["status_now"] == "sent"

    @pytest.mark.asyncio
    async def test_cancelling_a_missing_message_is_a_404(self):
        with patch.object(mod, "cancel_message", new=AsyncMock(return_value=False)), \
             patch.object(mod, "db") as fake_db:
            fake_db.__getitem__.return_value.find_one = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as e:
                await mod.cancel_outbound_message(
                    "nope", mod.CancelRequest(reason=""), _user("super_admin"), BID)
        assert e.value.status_code == 404


class TestReleaseDoesNotOverrideTheQueueSwitch:
    @pytest.mark.asyncio
    async def test_releasing_while_the_queue_is_disabled_reports_it_will_not_send(self):
        """Release waives the undo delay ONLY.

        If this ever returned an unqualified success it would read as "sent" to an
        operator, turning the release button into a way around the disable switch it
        sits next to.
        """
        row = {"id": "m-2", "status": MessageStatus.HELD.value, "category": "automated",
               "hold_until": "2020-01-01T00:00:00+00:00",
               "expires_at": "2999-01-01T00:00:00+00:00"}
        with patch.object(mod, "release_now", new=AsyncMock(return_value=True)), \
             patch.object(mod, "create_audit_log", new=AsyncMock()), \
             patch.object(mod, "get_queue_settings",
                          new=AsyncMock(return_value={"enabled": False, "hold_seconds": 30,
                                                      "expiry_hours": 48,
                                                      "disabled_categories": []})), \
             patch.object(mod, "db") as fake_db:
            fake_db.__getitem__.return_value.find_one = AsyncMock(return_value=row)
            out = await mod.release_outbound_message("m-2", _user("strata_manager"), BID)

        assert out["success"] is True
        assert out["will_send_next_tick"] is False, "a disabled queue still holds it"
        assert "disabled" in out["hold_reason"]


class TestSettings:
    @pytest.mark.asyncio
    async def test_an_empty_update_is_rejected_rather_than_silently_doing_nothing(self):
        with pytest.raises(HTTPException) as e:
            await mod.update_queue_controls(
                mod.QueueSettingsUpdate(), _user("strata_manager"), BID)
        assert e.value.status_code == 400

    def test_hold_and_expiry_are_bounded(self):
        """Guards against a typo disabling the review window or holding mail for a year."""
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            mod.QueueSettingsUpdate(hold_seconds=99999)
        with pytest.raises(pydantic.ValidationError):
            mod.QueueSettingsUpdate(expiry_hours=0)
        assert mod.QueueSettingsUpdate(hold_seconds=0).hold_seconds == 0


class TestSearchGrammar:
    """The console reuses the audit-log grammar with its own vocabulary.

    Sharing the parser is deliberate: two parsers would let `field:value` mean
    different things on two pages, and the help panel is served from the same
    module so documented syntax cannot drift from what is accepted.
    """

    def _parse(self, q):
        from utils.audit_search import parse_audit_query
        return parse_audit_query(
            q, field_map=mod._SEARCH_FIELD_MAP,
            free_text_fields=mod._SEARCH_FREE_TEXT,
            numeric_fields=mod._SEARCH_NUMERIC, boolean_fields=set())

    def test_equality_targets_the_mapped_field(self):
        f, unknown = self._parse("status:held")
        assert unknown == []
        assert "$and" in f or "status" in str(f)
        assert "held" in str(f)

    def test_exclusion_is_supported_both_ways(self):
        for q in ("-status:sent", "status!=sent"):
            f, unknown = self._parse(q)
            assert unknown == [], q
            assert "sent" in str(f), q

    def test_a_mistyped_field_is_reported_not_silently_ignored(self):
        """The worst outcome is a typo returning everything as if unfiltered."""
        _, unknown = self._parse("statuss:held")
        assert unknown == ["statuss"]

    def test_aliases_resolve_to_the_stored_field(self):
        f, _ = self._parse("recipient:owner@example.com")
        assert "to_email" in str(f)

    def test_numeric_comparison_on_attempts(self):
        f, unknown = self._parse("attempts:>=2")
        assert unknown == []
        assert "$gte" in str(f)

    def test_every_documented_example_parses_without_unknown_fields(self):
        """The help panel is served from the parser; this stops the two drifting."""
        for entry in mod.SEARCH_HELP["syntax"]:
            _, unknown = self._parse(entry["example"])
            assert unknown == [], f"documented example does not parse: {entry['example']}"

    def test_a_bare_term_is_rehomed_under_and_before_it_reaches_the_db(self):
        """TenantScopedDatabase rejects a TOP-LEVEL $or — it cannot inject building_id.

        The parser legitimately produces one for a bare word; the router must re-home
        it. Asserted on the merge helper, because the endpoint is usually exercised
        with field filters, which never take this path.
        """
        parsed, _ = self._parse("levy")
        assert "$or" in parsed, "precondition: a bare term produces a top-level $or"

        merged = mod.merge_search_filter({"status": "held"}, parsed)
        assert "$or" not in merged, "a top-level $or would raise inside the DB wrapper"
        assert merged["status"] == "held", "the base query must survive the merge"
        assert merged["$and"] == [{"$or": parsed["$or"]}], "same clause, nested"

    def test_merging_preserves_both_an_existing_and_and_a_new_one(self):
        merged = mod.merge_search_filter(
            {"$and": [{"a": 1}]}, {"$and": [{"b": 2}], "status": "sent"})
        assert merged["$and"] == [{"a": 1}, {"b": 2}]
        assert merged["status"] == "sent"

    def test_merging_does_not_mutate_the_caller_query(self):
        base = {"status": "held"}
        mod.merge_search_filter(base, {"category": "manual"})
        assert base == {"status": "held"}


class TestAuditCallSignature:
    """The audit call must satisfy create_audit_log's real signature.

    resource_type, resource_id and user_name are required parameters. The original call
    sites passed only action/user_id/building_id/details, which raises TypeError — and
    because the audit write happens AFTER the state change, every cancel and release
    would have mutated the message and then returned 500.

    Binding the signature is the check that catches this; mocking create_audit_log (as
    the release test above does, legitimately) cannot.
    """

    def test_audit_helper_binds_against_the_real_create_audit_log(self):
        import inspect

        from utils.helpers import create_audit_log

        sig = inspect.signature(create_audit_log)
        # Exactly what mod._audit passes through.
        sig.bind(
            action="outbound_message_cancelled",
            resource_type="outbound_message",
            resource_id="m-1",
            user_id="u-1",
            user_name="A Manager",
            details={"reason": "x"},
            building_id="13195",
        )

    def test_the_old_incomplete_call_shape_would_be_rejected(self):
        """Pins WHY the helper exists, so it is not "simplified" back later."""
        import inspect

        import pytest as _pytest

        from utils.helpers import create_audit_log

        sig = inspect.signature(create_audit_log)
        with _pytest.raises(TypeError):
            sig.bind(action="x", user_id="u", building_id="13195", details={})

    def test_every_audit_write_goes_through_the_helper(self):
        """One call is expected — the one inside _audit. Any other is a regression."""
        src = open(mod.__file__).read()
        # _audit's own body legitimately calls it; everything after that function must not.
        helper_start = src.index("async def _audit(")
        helper_end = src.index("def _require_queue_admin(")
        outside = src[:helper_start] + src[helper_end:]
        assert "await create_audit_log(" not in outside, (
            "call _audit() instead; a direct call re-introduces the incomplete signature "
            "that raised TypeError after the state change had already been applied"
        )
