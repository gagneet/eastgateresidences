# @featuretrace:finance-postgres-read-cutover — scope guard + dimension handling for shadow diffs.
# Layer: test
# Data flow: finance_shadow_read_service.population_scope_conflict / _compare_transactions_payloads (building-scoped).
# Related: backend/services/finance_shadow_read_service.py
#          backend/scripts/data_repair/resolve_stale_shadow_diffs_20260829.py
"""Regression tests for the two defects that produced 260 false criticals.

Both were found live on building 13195 on 2026-08-29, while the two stores actually
agreed to the cent (Mongo FY2026: 87 units, $220,187.56 levied / $212,146.26 paid;
Postgres: 22018756 / 21214626 cents over 87 lots).

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_finance_shadow_scope_guard.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from services.finance_shadow_read_service import (  # noqa: E402
    _compare_transactions_payloads,
    population_scope_conflict,
)


class TestPopulationScopeGuard:
    def test_single_unit_payload_against_building_aggregate_is_refused(self):
        """The exact live shape: unit_count 1 vs 87 alongside a money field."""
        conflict = population_scope_conflict(
            route_key="finance.unit_levy_ledger",
            mongo_payload={"unit_count": 1, "total_paid": 3523.00},
            pg_payload={"unit_count": 87, "total_paid_cents": 21214626},
        )
        assert conflict is not None
        assert "mongo=1" in conflict and "pg=87" in conflict

    def test_matching_population_is_compared_normally(self):
        assert population_scope_conflict(
            route_key="finance.unit_levy_ledger",
            mongo_payload={"unit_count": 87, "total_paid": 212146.26},
            pg_payload={"unit_count": 87, "total_paid_cents": 21214626},
        ) is None

    def test_missing_population_marker_does_not_disable_the_route(self):
        """Refusing here would silently switch off the route's shadow coverage."""
        assert population_scope_conflict(
            route_key="finance.unit_levy_ledger",
            mongo_payload={"total_paid": 212146.26},
            pg_payload={"unit_count": 87},
        ) is None

    def test_arrears_is_deliberately_not_population_guarded(self):
        """units_in_arrears is a MEASURED value there, not a population.

        Guarding on it would suppress exactly the divergence the route exists to find.
        """
        assert population_scope_conflict(
            route_key="finance.arrears_detail",
            mongo_payload={"units_in_arrears": 2, "total_arrears": 5500.0},
            pg_payload={"units_in_arrears": 14, "total_arrears_cents": 804130},
        ) is None

    def test_unknown_route_is_not_guarded(self):
        assert population_scope_conflict(
            route_key="finance.summary",
            mongo_payload={"unit_count": 1},
            pg_payload={"unit_count": 87},
        ) is None


class TestTransactionsDimension:
    """The income endpoint passes expenses=[], so total_expense is a structural 0.0.

    Comparing that against PG's real building-wide expense total produced a guaranteed
    field_mismatch:critical on every single production call.
    """

    PG = {"total_expense_cents": 14565265, "total_income_cents": 201321215}

    def test_income_call_does_not_compare_the_empty_expense_side(self):
        diffs = _compare_transactions_payloads(
            mongo_payload={"_dimension": "income", "total_expense": 0.0, "total_income": 2013212.15},
            pg_payload=self.PG,
            tolerance_cents=100,
        )
        assert [d.field_path for d in diffs] == []

    def test_expense_call_does_not_compare_the_empty_income_side(self):
        diffs = _compare_transactions_payloads(
            mongo_payload={"_dimension": "expense", "total_expense": 145652.65, "total_income": 0.0},
            pg_payload=self.PG,
            tolerance_cents=100,
        )
        assert [d.field_path for d in diffs] == []

    def test_a_real_divergence_in_the_populated_dimension_is_still_reported(self):
        """The guard must not become a way to stop seeing genuine drift."""
        diffs = _compare_transactions_payloads(
            mongo_payload={"_dimension": "expense", "total_expense": 999.99, "total_income": 0.0},
            pg_payload=self.PG,
            tolerance_cents=100,
        )
        assert [d.field_path for d in diffs] == ["total_expense"]

    def test_omitted_dimension_preserves_the_old_both_sides_behaviour(self):
        diffs = _compare_transactions_payloads(
            mongo_payload={"total_expense": 0.0, "total_income": 0.0},
            pg_payload=self.PG,
            tolerance_cents=100,
        )
        assert {d.field_path for d in diffs} == {"total_expense", "total_income"}

    def test_an_unrecognised_dimension_compares_both_rather_than_nothing(self):
        """A false PASS is worse than the false FAIL this function was fixed to stop.

        With a naive branch pair, a typo'd or future dimension makes neither branch fire
        and the route reports a clean shadow forever.
        """
        diffs = _compare_transactions_payloads(
            mongo_payload={"_dimension": "both", "total_expense": 0.0, "total_income": 0.0},
            pg_payload=self.PG,
            tolerance_cents=100,
        )
        assert {d.field_path for d in diffs} == {"total_expense", "total_income"}
