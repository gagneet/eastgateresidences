"""
Tests for services/ownership_shadow_read_service.py — replaces the legacy
ShadowReadValidator/core.shadow_read_divergences path for owner_service.py's 4 lookup
methods (get_owner_info, get_all_unit_owners, resolve_agm_voters,
is_user_current_owner_of_unit), added 2026-07-14 (PostgreSQL shadow-read expansion,
Phase D2 — a genuine Mongo-primary/Postgres-shadow pair, unlike identity.users.list).

Covers:
  1.  redaction: name/email fields are hashed, never stored raw
  2.  matching payloads produce no diff
  3.  divergent payloads produce exactly one aggregate diff (hashes only)
  4.  owner_service._run_owner_shadow routes to the new engine, not the legacy validator
  5.  multi-tenant isolation
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.ownership_shadow_read_service import (
    _hash_value,
    _redact,
    compare_owner_payloads,
)

BUILDING_A = "13195"
BUILDING_B = "16244"


class TestRedaction:
    def test_pii_field_names_are_hashed(self):
        redacted = _redact({"owner_name": "Jane Smith", "owner_email": "jane@example.com"})
        assert redacted["owner_name"] != "Jane Smith"
        assert redacted["owner_email"] != "jane@example.com"
        assert len(redacted["owner_name"]) == 16  # short sha256 prefix

    def test_non_pii_fields_pass_through_unredacted(self):
        redacted = _redact({"unit_number": "TH087"})
        assert redacted == {"unit_number": "TH087"}

    def test_source_field_is_stripped_not_compared(self):
        """Found 2026-07-20 tracing live ownership.owner.all_units mismatches: 'source'
        names which store answered ("user_units" vs "postgres_owner_read") and can never
        match across systems by construction — it was previously compared as literal
        equality, guaranteeing a reported mismatch on every call regardless of whether the
        actual owner data agreed."""
        redacted = _redact({"owner_name": "Jane Smith", "source": "user_units"})
        assert "source" not in redacted

    def test_owner_id_is_stripped_from_owner_presentation_parity(self):
        """Found 2026-07-20: Mongo's users.id and Postgres's core.users.user_id are
        independently generated UUIDs for the same real person (confirmed live: East
        Gate unit TH086, same name/email on both sides, different owner_id). Owner
        presentation parity excludes this field; access parity is checked separately."""
        assert _redact({"owner_id": "uuid-mongo-123"}) == {}
        assert _redact({"owner_id": "uuid-pg-456"}) == {}
        assert _redact({"owner_id": None}) == {}

    def test_case_and_whitespace_insensitive_hash(self):
        """Jane Smith / jane smith / JANE SMITH must hash identically (matches the legacy
        validator's normalization behavior, which this replaces)."""
        assert _hash_value("Jane Smith") == _hash_value("jane smith") == _hash_value("  JANE SMITH  ")

    def test_none_and_empty_string_both_hash_to_none(self):
        assert _hash_value(None) is None
        assert _hash_value("") is None
        assert _hash_value("   ") is None

    def test_redact_handles_nested_lists_of_dicts(self):
        """resolve_agm_voters returns a list[dict] — redaction must recurse into it."""
        redacted = _redact([
            {"full_name": "Alpha Owner", "email": "a@example.com", "unit_number": "7"},
            {"full_name": "Bravo Owner", "email": "b@example.com", "unit_number": "8"},
        ])
        assert redacted[0]["unit_number"] == "7"
        assert redacted[0]["full_name"] != "Alpha Owner"

    def test_redact_handles_dict_of_dicts(self):
        """get_all_unit_owners returns dict[unit_number, dict] — redaction must recurse."""
        redacted = _redact({"TH087": {"owner_name": "Jane Smith", "owner_email": "jane@example.com"}})
        assert redacted["TH087"]["owner_name"] != "Jane Smith"

    def test_redact_handles_plain_bool(self):
        """is_user_current_owner_of_unit returns a bare bool — must pass through unchanged."""
        assert _redact(True) is True
        assert _redact(False) is False


class TestCompareOwnerPayloads:
    @pytest.mark.asyncio
    async def test_matching_payloads_produce_no_diff(self):
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()), \
             patch("services.shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result = await compare_owner_payloads(
                building_id=BUILDING_A, route_key="ownership.owner.current",
                mongo_value={"owner_name": "Jane Smith", "owner_email": "jane@example.com"},
                pg_value={"owner_name": "Jane Smith", "owner_email": "jane@example.com"},
            )

        assert result.matched is True
        assert recorded == []

    @pytest.mark.asyncio
    async def test_case_insensitive_match_treated_as_matching(self):
        """Jane Smith (Mongo) vs jane smith (PG) must NOT be flagged as a divergence —
        matches the legacy validator's normalization behavior."""
        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_diff, \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()), \
             patch("services.shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result = await compare_owner_payloads(
                building_id=BUILDING_A, route_key="ownership.owner.current",
                mongo_value={"owner_name": "Jane Smith"},
                pg_value={"owner_name": "jane smith"},
            )

        assert result.matched is True
        mock_diff.assert_not_called()

    @pytest.mark.asyncio
    async def test_divergent_payloads_record_one_aggregate_diff_with_hashes_only(self):
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_owner_payloads(
                building_id=BUILDING_A, route_key="ownership.owner.current",
                mongo_value={"owner_name": "Jane Smith", "owner_email": "jane@example.com"},
                pg_value={"owner_name": "Different Person", "owner_email": "different@example.com"},
            )

        assert result.matched is False
        assert len(recorded) == 1
        payload = recorded[0]["mongo_value"]["fields"]["value"]
        assert "Jane Smith" not in str(payload)
        assert "jane@example.com" not in str(payload)

    @pytest.mark.asyncio
    async def test_same_owner_different_system_ids_and_source_does_not_diverge(self):
        """Regression for the live TH086 case (2026-07-20): both sides agree on the real
        owner (name/email), but Mongo's owner_id/source and Postgres's owner_id/source
        differ by construction. This must now match — before the fix, this diverged on
        every single call."""
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()), \
             patch("services.shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result = await compare_owner_payloads(
                building_id=BUILDING_A, route_key="ownership.owner.all_units",
                mongo_value={"TH086": {
                    "owner_name": "Riyu Kurian Abraham", "owner_email": "riyuroy@gmail.com",
                    "owner_id": "7700d07c-8761-4064-95a1-9ed4df8bfcd7", "source": "user_units",
                }},
                pg_value={"TH086": {
                    "owner_name": "Riyu Kurian Abraham", "owner_email": "riyuroy@gmail.com",
                    "owner_id": "9a0cc45c-3748-48eb-bb8b-e22e62b041d9", "source": "postgres_owner_read",
                }},
            )

        assert result.matched is True
        assert recorded == []

    @pytest.mark.asyncio
    async def test_one_side_missing_owner_still_diverges(self):
        """Regression for the live UA044 case (2026-07-20): Postgres has a real owner via
        core.ownership_periods, Mongo has zero user_units linkage at all (owner_id=None).
        This is a real data gap, not comparator noise, and must still be reported."""
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_owner_payloads(
                building_id=BUILDING_A, route_key="ownership.owner.all_units",
                mongo_value={"UA044": {
                    "owner_name": "Legacy Fallback Name", "owner_email": None,
                    "owner_id": None, "source": "units_legacy",
                }},
                pg_value={"UA044": {
                    "owner_name": "Lamisa Ahmad", "owner_email": "lamisa.ahmad@eastgateresidences.com.au",
                    "owner_id": "42baa37b-02cf-5834-b088-757917a6e7a1", "source": "postgres_owner_read",
                }},
            )

        assert result.matched is False
        assert len(recorded) == 1

    @pytest.mark.asyncio
    async def test_pg_none_records_pg_unavailable(self):
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_owner_payloads(
                building_id=BUILDING_A, route_key="ownership.owner.current",
                mongo_value={"owner_name": "Jane Smith"}, pg_value=None,
            )

        assert result.pg_available is False
        assert recorded[0]["diff_type"] == "pg_unavailable"

    @pytest.mark.asyncio
    async def test_building_ids_never_cross_contaminate(self):
        calls = []

        async def fake_record_coverage(**kwargs):
            calls.append(kwargs["building_id"])

        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()), \
             patch("services.shadow_read_service._safe_record_coverage", new=fake_record_coverage):
            await compare_owner_payloads(
                building_id=BUILDING_A, route_key="ownership.owner.current",
                mongo_value={"owner_name": "A"}, pg_value=None,
            )
            await compare_owner_payloads(
                building_id=BUILDING_B, route_key="ownership.owner.current",
                mongo_value={"owner_name": "B"}, pg_value=None,
            )

        assert calls == [BUILDING_A, BUILDING_B]


class TestOwnerServiceRoutesToNewEngine:
    @pytest.mark.asyncio
    async def test_run_owner_shadow_calls_new_comparator_not_legacy_validator(self):
        import services.owner_service as owner_service

        with patch.object(owner_service, "compare_owner_payloads", new=AsyncMock()) as mock_compare:
            await owner_service._run_owner_shadow(
                building_id=BUILDING_A,
                method_name="get_owner_info",
                query_params={"unit_number": "7"},
                mongodb_value={"owner_name": "A"},
                postgres_value={"owner_name": "A"},
            )

        mock_compare.assert_awaited_once()
        assert mock_compare.await_args.kwargs["route_key"] == "ownership.owner.current"
        assert mock_compare.await_args.kwargs["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_all_four_methods_have_a_mapped_route_key(self):
        import services.owner_service as owner_service

        for method_name in (
            "get_owner_info", "get_all_unit_owners",
            "resolve_agm_voters", "is_user_current_owner_of_unit",
        ):
            assert method_name in owner_service._OWNER_METHOD_ROUTE_KEYS

    @pytest.mark.asyncio
    async def test_unmapped_method_name_logs_and_does_not_call_comparator(self):
        import services.owner_service as owner_service

        with patch.object(owner_service, "compare_owner_payloads", new=AsyncMock()) as mock_compare:
            await owner_service._run_owner_shadow(
                building_id=BUILDING_A, method_name="not_a_real_method",
                query_params={}, mongodb_value={}, postgres_value={},
            )

        mock_compare.assert_not_called()
