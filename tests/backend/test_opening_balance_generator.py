"""Unit tests for services/reconstruction_generators/opening_balance_generator.py (G3.2 increment 1).

Pure module (stdlib only), so these run without a DB or the backend venv's heavy deps.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from services.reconstruction_generators.opening_balance_generator import (
    OPENING_ASSUMPTION_CODE,
    OPENING_FUND_DEFAULT,
    OPENING_SOURCE_TAG,
    LotOwnerRef,
    OpeningBalanceGenerationError,
    build_opening_plan_from_portal_snapshot,
    opening_dedup_key,
    opening_idempotency_key,
    summarize_opening_plan,
)

BUILDING_ID = "16244"
AS_OF = date(2026, 7, 1)


def _lot(unit_number: str) -> LotOwnerRef:
    return LotOwnerRef(unit_number=unit_number, lot_id=uuid4(), owner_party_id=uuid4())


class TestBuildOpeningPlan:
    def test_positive_net_becomes_arrears_at_exact_magnitude(self):
        lot = _lot("TH001")
        plan = build_opening_plan_from_portal_snapshot(
            BUILDING_ID, AS_OF, [lot], {"TH001": 123_45}
        )
        assert len(plan.lines) == 1
        line = plan.lines[0]
        assert line.kind == "arrears"
        assert line.amount_cents == 123_45
        assert line.fund_type == OPENING_FUND_DEFAULT == "admin"
        assert line.source_tag == OPENING_SOURCE_TAG
        assert line.assumption_code == OPENING_ASSUMPTION_CODE
        assert plan.total_arrears_cents == 123_45
        assert plan.total_credit_cents == 0
        assert plan.net_owing_cents == 123_45

    def test_negative_net_becomes_credit_at_positive_magnitude(self):
        lot = _lot("TH002")
        plan = build_opening_plan_from_portal_snapshot(
            BUILDING_ID, AS_OF, [lot], {"TH002": -254_98}
        )
        assert len(plan.lines) == 1
        line = plan.lines[0]
        assert line.kind == "credit"
        assert line.amount_cents == 254_98  # positive magnitude
        assert plan.total_credit_cents == 254_98
        assert plan.total_arrears_cents == 0
        assert plan.net_owing_cents == -254_98

    def test_zero_net_generates_no_line(self):
        lot = _lot("TH003")
        plan = build_opening_plan_from_portal_snapshot(BUILDING_ID, AS_OF, [lot], {"TH003": 0})
        assert plan.lines == []
        assert plan.skipped_zero == 1

    def test_missing_portal_data_skips_with_warning_never_assumes(self):
        lot = _lot("TH004")
        plan = build_opening_plan_from_portal_snapshot(BUILDING_ID, AS_OF, [lot], {})
        assert plan.lines == []
        assert plan.lots_without_portal_data == 1
        assert any("no portal net balance" in w for w in plan.warnings)

    def test_existing_opening_key_is_skipped_no_double_post(self):
        lot = _lot("TH005")
        key = opening_dedup_key(lot.lot_id, AS_OF.year, OPENING_FUND_DEFAULT)
        plan = build_opening_plan_from_portal_snapshot(
            BUILDING_ID, AS_OF, [lot], {"TH005": 500_00},
            existing_opening_keys=frozenset({key}),
        )
        assert plan.lines == []
        assert plan.skipped_existing == 1

    def test_mixed_building_totals_and_net(self):
        lots = [_lot("A"), _lot("B"), _lot("C"), _lot("D")]
        net = {"A": 100_00, "B": 300_00, "C": -50_00, "D": 0}
        plan = build_opening_plan_from_portal_snapshot(BUILDING_ID, AS_OF, lots, net)
        assert len(plan.lines) == 3  # D is zero
        assert plan.total_arrears_cents == 400_00
        assert plan.total_credit_cents == 50_00
        assert plan.net_owing_cents == 350_00
        assert plan.skipped_zero == 1

    def test_empty_lots_raises(self):
        with pytest.raises(OpeningBalanceGenerationError, match="No lots"):
            build_opening_plan_from_portal_snapshot(BUILDING_ID, AS_OF, [], {"X": 1})

    def test_idempotency_key_is_deterministic_and_opening_namespaced(self):
        lot = _lot("TH006")
        k = opening_idempotency_key(BUILDING_ID, AS_OF.year, "admin", lot.lot_id)
        assert k == f"opening:{BUILDING_ID}:{AS_OF.year}:admin:{lot.lot_id}"
        assert k.startswith("opening:")  # never collides with a levy/payment key


class TestSummarize:
    def test_summary_shape(self):
        lots = [_lot("A"), _lot("B")]
        plan = build_opening_plan_from_portal_snapshot(
            BUILDING_ID, AS_OF, lots, {"A": 100_00, "B": -40_00}
        )
        s = summarize_opening_plan(plan)
        assert s["arrears_lot_count"] == 1
        assert s["credit_lot_count"] == 1
        assert s["total_arrears_cents"] == 100_00
        assert s["total_credit_cents"] == 40_00
        assert s["net_owing_cents"] == 60_00
        assert s["fund_type"] == "admin"
