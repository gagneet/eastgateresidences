"""Tests for Reconciliation Matching Engine — Phase 2."""
from __future__ import annotations

import os
import sys

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from services.reconciliation_matching_service import (
    MatchingEngine,
    ReconciliationMatch,
    detect_duplicate_bank_lines,
)

BUILDING_ID = "13195"
OTHER_BUILDING_ID = "16244"


def _bank_line(amount_cents=10000, date_str="2026-03-01", reference="REF001", description="LEVY PAYMENT"):
    return {
        "_id": "BL001",
        "bank_line_id": "BL001",
        "amount_cents": amount_cents,
        "date": date_str,
        "reference": reference,
        "description": description,
        "building_id": BUILDING_ID,
    }


def _internal_tx(amount_cents=10000, date_str="2026-03-01", reference="REF001", description="LEVY PAYMENT"):
    return {
        "_id": "TX001",
        "internal_transaction_id": "TX001",
        "amount_cents": amount_cents,
        "date": date_str,
        "reference": reference,
        "description": description,
        "building_id": BUILDING_ID,
    }


class TestMatchingEngineScoring:
    def test_exact_match_same_day_high_score(self):
        """Exact amount + same day + matching ref + description → score >= 0.80 (exact).

        Max achievable score = 0.40 (amount) + 0.20 (date) + 0.15 (ref) + 0.05 (desc) = 0.80.
        THRESHOLD_EXACT = 0.80 so a perfect 4-signal match → "exact".
        """
        engine = MatchingEngine()
        bank = _bank_line(amount_cents=10000, date_str="2026-03-01", reference="REF001")
        internal = _internal_tx(amount_cents=10000, date_str="2026-03-01", reference="REF001")
        match = engine.score_pair(bank, internal)
        assert match.confidence_score >= 0.80
        assert match.match_type == "exact"
        assert match.suggested_action == "auto_match"

    def test_near_match_date_off_by_2_days(self):
        """Amount matches, date off by 2 days → score >= 0.70 (likely)."""
        engine = MatchingEngine()
        bank = _bank_line(amount_cents=10000, date_str="2026-03-01", reference="REF001")
        internal = _internal_tx(amount_cents=10000, date_str="2026-03-03", reference="REF001")
        match = engine.score_pair(bank, internal)
        assert match.confidence_score >= 0.70
        assert match.match_type in ("likely", "exact")

    def test_false_positive_different_description(self):
        """Amount matches but description completely different → score < 0.70."""
        engine = MatchingEngine()
        bank = _bank_line(amount_cents=10000, date_str="2026-03-01", reference="ABC123",
                          description="BANK FEE SERVICE CHARGE MAINTENANCE")
        internal = _internal_tx(amount_cents=10000, date_str="2026-03-15", reference="XYZ999",
                                description="QUARTERLY LEVY UNIT 42 RESIDENT")
        match = engine.score_pair(bank, internal)
        # Date is 14 days apart (date_proximity = 0), different reference, different description
        assert match.confidence_score < 0.70

    def test_ambiguous_match_type_for_low_score(self):
        """Very different records → match_type in (weak, ambiguous, none)."""
        engine = MatchingEngine()
        bank = _bank_line(amount_cents=50000, date_str="2026-01-01", reference="AAAA", description="ALPHA")
        internal = _internal_tx(amount_cents=10000, date_str="2026-03-15", reference="ZZZZ", description="OMEGA")
        match = engine.score_pair(bank, internal)
        assert match.match_type in ("weak", "ambiguous", "none")

    def test_score_bounded_0_to_1(self):
        """Confidence score must be in [0, 1]."""
        engine = MatchingEngine()
        bank = _bank_line()
        internal = _internal_tx()
        match = engine.score_pair(bank, internal)
        assert 0.0 <= match.confidence_score <= 1.0


class TestDuplicateDetection:
    def test_duplicate_bank_lines_detected(self):
        """Two bank lines with same (amount, date, reference) → at least 1 duplicate warning."""
        engine = MatchingEngine()
        lines = [
            _bank_line(amount_cents=10000, date_str="2026-03-01", reference="REF001"),
            {**_bank_line(amount_cents=10000, date_str="2026-03-01", reference="REF001"), "_id": "BL002",
             "bank_line_id": "BL002"},
        ]
        duplicates = detect_duplicate_bank_lines(lines)
        assert len(duplicates) >= 1

    def test_no_duplicate_different_amounts(self):
        """Different amounts → no duplicates."""
        engine = MatchingEngine()
        lines = [
            _bank_line(amount_cents=10000, date_str="2026-03-01", reference="REF001"),
            _bank_line(amount_cents=20000, date_str="2026-03-01", reference="REF001"),
        ]
        # Override ids
        lines[1]["_id"] = "BL002"
        lines[1]["bank_line_id"] = "BL002"
        duplicates = detect_duplicate_bank_lines(lines)
        assert len(duplicates) == 0

    def test_building_isolation_in_candidates(self):
        """Candidates from other building should not score against this building's bank lines."""
        engine = MatchingEngine()
        bank = _bank_line(amount_cents=10000, date_str="2026-03-01", reference="REF001")
        # Internal tx from different building
        internal = {**_internal_tx(amount_cents=10000, date_str="2026-03-01", reference="REF001"),
                    "building_id": OTHER_BUILDING_ID}
        # The matching engine should either refuse or return low score for cross-building
        # Most important: no auto-match suggestion for cross-building
        match = engine.score_pair(bank, internal)
        # If engine filters by building_id, score should be 0 or match_type should reflect mismatch
        assert match.confidence_score == 0.0 or match.match_type in ("none", "ambiguous")


class TestMatchModel:
    def test_reconciliation_match_model_fields(self):
        """ReconciliationMatch must have all required fields."""
        match = ReconciliationMatch(
            bank_line_id="BL001",
            internal_transaction_id="TX001",
            confidence_score=0.95,
            match_type="exact",
            match_reasons=["exact_amount", "same_day"],
            suggested_action="auto_match",
        )
        assert match.bank_line_id == "BL001"
        assert match.match_type == "exact"
        assert match.confidence_score == 0.95
