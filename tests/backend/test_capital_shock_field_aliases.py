"""
Tests for the description/estimated_cost/severity field aliases added to
_compute_capital_shock_index() in services/capital_shock_service.py.

Regression coverage: CapitalForMeCard (owner dashboards) and ReserveRunwayChart
(manager dashboards) read `description`/`estimated_cost`/`severity` off capital-shock
rows. The MongoDB path previously only returned `capital_spend`/`risk_level` with no
description at all, so "your share" always computed to $0 and shock labels on the
reserve chart rendered "$—". The Postgres path already had `estimated_cost`/
`description` but only `risk_level`, never `severity`.

_compute_capital_shock_index is a pure function — no DB mocking required.
"""

from services.capital_shock_service import _compute_capital_shock_index


def test_estimated_cost_and_severity_alias_capital_spend_and_risk_level():
    plan_rows = [
        {"year": 2027, "expenditure": 10000.0},
        {"year": 2028, "expenditure": 90000.0},
    ]
    result = _compute_capital_shock_index(plan_rows)

    for row in result["rows"]:
        assert row["estimated_cost"] == row["capital_spend"]
        assert row["severity"] == row["risk_level"]


def test_description_built_from_sinking_fund_plan_line_items():
    plan_rows = [{
        "year": 2027,
        "expenditure": 45000.0,
        "line_items": [{"name": "Roof replacement", "cost": 30000.0}, {"name": "Facade repaint", "cost": 15000.0}],
    }]
    result = _compute_capital_shock_index(plan_rows)
    assert result["rows"][0]["description"] == "Roof replacement, Facade repaint"


def test_description_built_from_schedule_year_map_when_no_line_items():
    plan_rows = [{"year": 2029, "expenditure": 60000.0}]
    year_map = {2029: [{"asset_name": "Lift modernisation", "estimated_cost": 60000.0}]}
    result = _compute_capital_shock_index(plan_rows, year_map)
    assert result["rows"][0]["description"] == "Lift modernisation"


def test_description_is_none_when_no_named_items_available():
    plan_rows = [{"year": 2030, "expenditure": 5000.0}]
    result = _compute_capital_shock_index(plan_rows)
    assert result["rows"][0]["description"] is None
    # estimated_cost/severity are still populated even without a description.
    assert result["rows"][0]["estimated_cost"] == 5000.0


def test_next_shock_carries_the_same_aliased_fields():
    plan_rows = [
        {"year": 2026, "expenditure": 1000.0},
        # A large spike relative to the 1000/1000 baseline triggers "major"/"severe".
        {"year": 2027, "expenditure": 1000.0},
        {"year": 2028, "expenditure": 200000.0, "line_items": [{"name": "Pool resurfacing"}]},
    ]
    result = _compute_capital_shock_index(plan_rows)
    next_shock = result["next_shock"]
    assert next_shock is not None
    assert next_shock["estimated_cost"] == next_shock["capital_spend"]
    assert next_shock["severity"] == next_shock["risk_level"]
    assert next_shock["description"] == "Pool resurfacing"
