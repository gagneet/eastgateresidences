"""
Tests for services/identity_shadow_read_service.py — composition instrumentation for the
live mongo_pg_union GET /users route (server.py), added 2026-07-14 (PostgreSQL shadow-read
expansion, Phase D). Per docs/migration/phase-d-choices-mongodb-postgres.md, this is
deliberately NOT a field-diff comparator — the two sources are an intentional union, not a
primary/shadow pair.

Covers:
  1.  compute_composition_stats: pure function, correct set arithmetic
  2.  record_user_list_composition: persists via record_shadow_diff (diff_type=composition_stats,
      never field_mismatch) and increments coverage; never raises
  3.  redaction: no raw email/name/phone ever passed to record_shadow_diff
  4.  multi-tenant isolation: building_id flows through untouched
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.identity_shadow_read_service import (
    CompositionStats,
    ReadStrategy,
    ROUTE_READ_STRATEGY,
    compute_composition_stats,
    record_user_list_composition,
)

BUILDING_A = "13195"
BUILDING_B = "16244"


class TestComputeCompositionStats:
    def test_full_overlap_no_conflicts(self):
        stats = compute_composition_stats(
            mongo_ids={"u1", "u2", "u3"}, pg_ids={"u1", "u2", "u3"},
            merged_count=3, pg_available=True,
        )
        assert stats.mongo_count == 3
        assert stats.pg_count == 3
        assert stats.overlap_count == 3
        assert stats.pg_only_count == 0
        assert stats.mongo_only_count == 0
        assert stats.merged_count == 3

    def test_pg_only_users_counted(self):
        stats = compute_composition_stats(
            mongo_ids={"u1"}, pg_ids={"u1", "u2", "u3"},
            merged_count=3, pg_available=True,
        )
        assert stats.pg_only_count == 2
        assert stats.overlap_count == 1
        assert stats.mongo_only_count == 0

    def test_mongo_only_users_counted(self):
        stats = compute_composition_stats(
            mongo_ids={"u1", "u2"}, pg_ids={"u1"},
            merged_count=2, pg_available=True,
        )
        assert stats.mongo_only_count == 1
        assert stats.pg_only_count == 0

    def test_empty_sets(self):
        stats = compute_composition_stats(
            mongo_ids=set(), pg_ids=set(), merged_count=0, pg_available=True,
        )
        assert stats.mongo_count == 0
        assert stats.pg_count == 0
        assert stats.overlap_count == 0

    def test_pg_unavailable_flag_passthrough(self):
        stats = compute_composition_stats(
            mongo_ids={"u1"}, pg_ids=set(), merged_count=1, pg_available=False,
        )
        assert stats.pg_available is False
        # pg_ids empty because PG failed, not because there are genuinely 0 PG users —
        # distinguishing "PG returned empty" from "PG failed" is exactly what pg_available is for.
        assert stats.pg_only_count == 0

    def test_latency_and_conflict_count_passthrough(self):
        stats = compute_composition_stats(
            mongo_ids={"u1"}, pg_ids={"u1"}, merged_count=1, pg_available=True,
            latency_ms=42.5, merge_conflict_count=2,
        )
        assert stats.latency_ms == 42.5
        assert stats.merge_conflict_count == 2


class TestRecordUserListComposition:
    @pytest.mark.asyncio
    async def test_persists_as_composition_stats_not_field_mismatch(self):
        recorded = []

        async def fake_record_shadow_diff(**kwargs):
            recorded.append(kwargs)

        stats = CompositionStats(mongo_count=5, pg_count=6, pg_available=True, overlap_count=5, pg_only_count=1)
        with patch("services.identity_shadow_read_service.record_shadow_diff", new=fake_record_shadow_diff), \
             patch("services.identity_shadow_read_service._safe_record_coverage", new=AsyncMock()):
            await record_user_list_composition(building_id=BUILDING_A, stats=stats)

        assert recorded[0]["diff_type"] == "composition_stats"
        assert recorded[0]["domain"] == "identity_core"
        assert recorded[0]["route"] == "identity.users.list"

    @pytest.mark.asyncio
    async def test_pg_unavailable_produces_nonzero_divergence_score(self):
        recorded = []

        async def fake_record_shadow_diff(**kwargs):
            recorded.append(kwargs)

        stats = CompositionStats(mongo_count=5, pg_count=0, pg_available=False)
        with patch("services.identity_shadow_read_service.record_shadow_diff", new=fake_record_shadow_diff), \
             patch("services.identity_shadow_read_service._safe_record_coverage", new=AsyncMock()) as mock_cov:
            await record_user_list_composition(building_id=BUILDING_A, stats=stats)

        assert recorded[0]["divergence_score"] == 0.5
        assert mock_cov.call_args.kwargs["pg_unavailable"] == 1

    @pytest.mark.asyncio
    async def test_pg_available_produces_zero_divergence_score_and_matched_coverage(self):
        async def fake_record_shadow_diff(**kwargs):
            pass

        stats = CompositionStats(mongo_count=5, pg_count=5, pg_available=True)
        with patch("services.identity_shadow_read_service.record_shadow_diff", new=fake_record_shadow_diff), \
             patch("services.identity_shadow_read_service._safe_record_coverage", new=AsyncMock()) as mock_cov:
            await record_user_list_composition(building_id=BUILDING_A, stats=stats)

        assert mock_cov.call_args.kwargs["matched"] == 1
        assert mock_cov.call_args.kwargs["pg_unavailable"] == 0

    @pytest.mark.asyncio
    async def test_never_raises_even_if_record_shadow_diff_fails(self):
        with patch("services.identity_shadow_read_service.record_shadow_diff",
                   new=AsyncMock(side_effect=RuntimeError("db down"))), \
             patch("services.identity_shadow_read_service._safe_record_coverage", new=AsyncMock()):
            # Must not raise.
            await record_user_list_composition(
                building_id=BUILDING_A,
                stats=CompositionStats(mongo_count=1, pg_count=1, pg_available=True),
            )

    @pytest.mark.asyncio
    async def test_no_pii_in_recorded_payload(self):
        """Redaction contract: only counts/booleans/floats may appear in the composition dict."""
        recorded = []

        async def fake_record_shadow_diff(**kwargs):
            recorded.append(kwargs)

        stats = CompositionStats(mongo_count=5, pg_count=6, pg_available=True)
        with patch("services.identity_shadow_read_service.record_shadow_diff", new=fake_record_shadow_diff), \
             patch("services.identity_shadow_read_service._safe_record_coverage", new=AsyncMock()):
            await record_user_list_composition(building_id=BUILDING_A, stats=stats)

        payload = recorded[0]["mongo_value"]
        for value in payload.values():
            assert isinstance(value, (int, float, bool, type(None))), (
                f"Non-count value found in composition payload: {value!r} — possible PII leak"
            )

    @pytest.mark.asyncio
    async def test_building_ids_never_cross_contaminate(self):
        calls = []

        async def fake_record_shadow_diff(**kwargs):
            calls.append(kwargs["building_id"])

        stats = CompositionStats(mongo_count=1, pg_count=1, pg_available=True)
        with patch("services.identity_shadow_read_service.record_shadow_diff", new=fake_record_shadow_diff), \
             patch("services.identity_shadow_read_service._safe_record_coverage", new=AsyncMock()):
            await record_user_list_composition(building_id=BUILDING_A, stats=stats)
            await record_user_list_composition(building_id=BUILDING_B, stats=stats)

        assert calls == [BUILDING_A, BUILDING_B]


class TestRouteReadStrategyClassification:
    def test_users_list_is_union_strategy(self):
        assert ROUTE_READ_STRATEGY["identity.users.list"] == ReadStrategy.MONGO_PG_UNION

    def test_login_is_postgres_primary_mongo_fallback(self):
        assert ROUTE_READ_STRATEGY["identity.auth.login"] == ReadStrategy.POSTGRES_PRIMARY_MONGO_FALLBACK

    def test_ownership_routes_are_shadow_pairs(self):
        for route in (
            "ownership.owner.current",
            "ownership.owner.all_units",
            "ownership.owner.agm_voters",
            "ownership.owner.membership_check",
        ):
            assert ROUTE_READ_STRATEGY[route] == ReadStrategy.MONGO_PRIMARY_PG_SHADOW
