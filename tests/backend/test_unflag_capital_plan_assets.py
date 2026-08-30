"""An asset a production capital plan depends on cannot be test data.

# @featuretrace:levy-fairness — capital plan asset reference repair (building-scoped)
# Layer: test
# Data flow: capital_replacement_schedule -> building_assets.is_test_data
# Related: backend/scripts/data_repair/unflag_capital_plan_assets_20260830.py,
#          backend/services/levy_fairness_service.py
# Tests: this file

The criterion under test is the general one, not East Gate's two rows: when a production
plan row and an asset's test flag disagree, the plan row is the record with money attached
and a year it falls due, so the flag is what gives way.

The cases that must NOT fire are the point of the suite. Unflagging on a test plan row,
or across a building boundary, is a cross-tenant or a laundering write.
"""

import importlib.util
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "backend", "scripts", "data_repair", "unflag_capital_plan_assets_20260830.py",
)
_spec = importlib.util.spec_from_file_location("unflag_capital_plan_assets", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
find_candidates = _mod.find_candidates


def _db(plan_rows, assets):
    """Mock DB where building_assets is keyed by (building_id, id)."""
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=plan_rows)
    db.capital_replacement_schedule.find = MagicMock(return_value=cursor)

    async def find_one(flt, _proj=None):
        return assets.get((flt["building_id"], flt["id"]))

    db.building_assets.find_one = AsyncMock(side_effect=find_one)
    return db


PLAN = {"asset_id": "asset-lift", "asset_name": "Lift Motor",
        "estimated_cost": 247611.94, "replacement_year": 2030, "building_id": "13195"}


class TestFindCandidates:
    @pytest.mark.asyncio
    async def test_a_test_flagged_asset_behind_a_production_plan_row_is_a_candidate(self):
        got = await find_candidates(
            _db([PLAN], {("13195", "asset-lift"): {"id": "asset-lift", "name": "Lift Motor",
                                                   "is_test_data": True}}),
            None,
        )
        assert [c["asset_id"] for c in got] == ["asset-lift"]
        assert got[0]["total_cost"] == pytest.approx(247611.94)
        assert got[0]["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_an_unflagged_asset_is_left_alone(self):
        got = await find_candidates(
            _db([PLAN], {("13195", "asset-lift"): {"id": "asset-lift", "is_test_data": False}}),
            None,
        )
        assert got == []

    @pytest.mark.asyncio
    async def test_an_asset_with_no_flag_at_all_is_left_alone(self):
        got = await find_candidates(
            _db([PLAN], {("13195", "asset-lift"): {"id": "asset-lift"}}), None,
        )
        assert got == []

    @pytest.mark.asyncio
    async def test_a_truthy_non_true_flag_is_not_treated_as_flagged(self):
        # `is True` rather than truthiness: a stray string would otherwise clear a flag
        # nobody set, and this script's whole justification is that it never guesses.
        got = await find_candidates(
            _db([PLAN], {("13195", "asset-lift"): {"id": "asset-lift", "is_test_data": "yes"}}),
            None,
        )
        assert got == []

    @pytest.mark.asyncio
    async def test_a_missing_asset_is_not_invented(self):
        got = await find_candidates(_db([PLAN], {}), None)
        assert got == []

    @pytest.mark.asyncio
    async def test_a_plan_row_with_no_asset_id_is_skipped(self):
        got = await find_candidates(
            _db([{**PLAN, "asset_id": None}],
                {("13195", "asset-lift"): {"id": "asset-lift", "is_test_data": True}}),
            None,
        )
        assert got == []

    @pytest.mark.asyncio
    async def test_the_same_asset_id_in_another_building_is_not_unflagged(self):
        # The id space is not global. Unflagging building B's asset because building A's
        # plan references the same id is a cross-tenant write.
        got = await find_candidates(
            _db([PLAN], {("16244", "asset-lift"): {"id": "asset-lift", "is_test_data": True}}),
            None,
        )
        assert got == []

    @pytest.mark.asyncio
    async def test_multiple_plan_rows_for_one_asset_sum_their_cost(self):
        rows = [PLAN, {**PLAN, "estimated_cost": 1000.0, "replacement_year": 2036}]
        got = await find_candidates(
            _db(rows, {("13195", "asset-lift"): {"id": "asset-lift", "is_test_data": True}}),
            None,
        )
        assert len(got) == 1
        assert got[0]["total_cost"] == pytest.approx(248611.94)
        assert len(got[0]["plan_rows"]) == 2


class TestPlanRowFilter:
    @pytest.mark.asyncio
    async def test_test_flagged_plan_rows_are_excluded_at_the_query(self):
        # The inverse case is deliberately NOT handled: if the PLAN row is test data, the
        # fix is to the plan row, which is a different decision with a different blast
        # radius. Assert the filter reaches the query rather than trusting the comment.
        db = _db([], {})
        await find_candidates(db, None)
        flt = db.capital_replacement_schedule.find.call_args[0][0]
        assert flt["is_test_data"] == {"$ne": True}
        assert "building_id" not in flt

    @pytest.mark.asyncio
    async def test_building_id_scopes_the_query_when_given(self):
        db = _db([], {})
        await find_candidates(db, "13195")
        assert db.capital_replacement_schedule.find.call_args[0][0]["building_id"] == "13195"
