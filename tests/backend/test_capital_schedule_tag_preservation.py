# @featuretrace:levy-fairness — regression cover for benefit-group attribution on
#   capital works rows, which decides which lots fund each capital line item.
# Layer: test
# Related: backend/repositories/digital_twin_repository.py,
#          backend/routers/intelligence.py,
#          backend/services/levy_fairness_service.py
# Collections: capital_replacement_schedule
"""Capital schedule benefit-group tag preservation.

Unit tests with a mocked DB — deliberately NOT in test_capital_works_planner.py,
which conftest gates behind RUN_INTEGRATION_TESTS=1 as a live-backend file.
These must run in the default suite, because the regression they cover is
silent: it produces a valid-looking schedule with the attribution stripped.

Regression: CapitalWorkItemUpdate declared no benefit_group_id/facility_id, so
Pydantic dropped them from every PUT payload, and update_capital_schedule's
delete_many + insert_many then replaced the whole collection with untagged
rows. Every Capital Works Planner save silently flattened the plan to ALL_LOTS,
which is the state East Gate (13195) was found in on 2026-08-19: 18 rows,
$1,158,834.99, none tagged. services/maintenance_intelligence_service.py's
forecast regeneration writes through the same repository function and had the
same effect, which is why the fix lives in the repository, not the router.

Run:
    cd backend
    venv/bin/pytest ../tests/backend/test_capital_schedule_tag_preservation.py -v
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _cursor(rows):
    c = MagicMock()
    c.sort.return_value = c
    c.to_list = AsyncMock(return_value=rows)
    return c


class TestCapitalScheduleTagPreservation:

    @staticmethod
    def _mock_db(existing):
        mock_db = MagicMock()
        mock_db.capital_replacement_schedule.find.return_value = _cursor(existing)
        mock_db.capital_replacement_schedule.delete_many = AsyncMock()
        mock_db.capital_replacement_schedule.insert_many = AsyncMock()
        return mock_db

    @pytest.mark.asyncio
    async def test_tags_survive_a_replace_that_omits_them(self):
        """An untagged incoming row inherits the tags of the row it replaces."""
        from repositories import digital_twin_repository as repo

        existing = [{"asset_name": "Lifts", "replacement_year": 2035, "estimated_cost": 205652.0,
                     "benefit_group_id": "bg-tower", "facility_id": "fac-lift"}]
        mock_db = self._mock_db(existing)
        with patch.object(repo, "db", mock_db):
            await repo.update_capital_schedule("13195", [
                {"asset_name": "Lifts", "replacement_year": 2035, "estimated_cost": 210000.0},
            ])

        written = mock_db.capital_replacement_schedule.insert_many.call_args[0][0]
        assert written[0]["benefit_group_id"] == "bg-tower"
        assert written[0]["facility_id"] == "fac-lift"
        # the caller's own edit still lands
        assert written[0]["estimated_cost"] == 210000.0

    @pytest.mark.asyncio
    async def test_matches_on_asset_id_when_the_name_changed(self):
        """asset_id identifies the row across a rename."""
        from repositories import digital_twin_repository as repo

        existing = [{"asset_id": "asset-lifts", "asset_name": "Lifts", "replacement_year": 2035,
                     "benefit_group_id": "bg-tower"}]
        mock_db = self._mock_db(existing)
        with patch.object(repo, "db", mock_db):
            await repo.update_capital_schedule("13195", [
                {"asset_id": "asset-lifts", "asset_name": "Lift replacement",
                 "replacement_year": 2035, "estimated_cost": 205652.0},
            ])

        written = mock_db.capital_replacement_schedule.insert_many.call_args[0][0]
        assert written[0]["benefit_group_id"] == "bg-tower"

    @pytest.mark.asyncio
    async def test_explicit_value_is_not_overwritten_by_the_prior_tag(self):
        """A caller retagging a row wins over the inherited value."""
        from repositories import digital_twin_repository as repo

        existing = [{"asset_name": "Roof", "replacement_year": 2030, "benefit_group_id": "bg-tower"}]
        mock_db = self._mock_db(existing)
        with patch.object(repo, "db", mock_db):
            await repo.update_capital_schedule("13195", [
                {"asset_name": "Roof", "replacement_year": 2030, "estimated_cost": 11826.0,
                 "benefit_group_id": "bg-all"},
            ])

        written = mock_db.capital_replacement_schedule.insert_many.call_args[0][0]
        assert written[0]["benefit_group_id"] == "bg-all"

    @pytest.mark.asyncio
    async def test_empty_string_clears_a_tag(self):
        """Sending "" is the documented way to deliberately untag a row."""
        from repositories import digital_twin_repository as repo

        existing = [{"asset_name": "Roof", "replacement_year": 2030, "benefit_group_id": "bg-tower"}]
        mock_db = self._mock_db(existing)
        with patch.object(repo, "db", mock_db):
            await repo.update_capital_schedule("13195", [
                {"asset_name": "Roof", "replacement_year": 2030, "estimated_cost": 11826.0,
                 "benefit_group_id": ""},
            ])

        written = mock_db.capital_replacement_schedule.insert_many.call_args[0][0]
        assert written[0]["benefit_group_id"] == ""

    @pytest.mark.asyncio
    async def test_a_genuinely_new_row_stays_untagged(self):
        """No prior row to inherit from means no tag is invented."""
        from repositories import digital_twin_repository as repo

        mock_db = self._mock_db([{"asset_name": "Roof", "replacement_year": 2030,
                                  "benefit_group_id": "bg-tower"}])
        with patch.object(repo, "db", mock_db):
            await repo.update_capital_schedule("13195", [
                {"asset_name": "Solar array", "replacement_year": 2033, "estimated_cost": 90000.0},
            ])

        written = mock_db.capital_replacement_schedule.insert_many.call_args[0][0]
        assert written[0].get("benefit_group_id") is None

    @pytest.mark.asyncio
    async def test_the_router_now_forwards_the_tag_fields(self):
        """The PUT model must declare the fields, or Pydantic drops them."""
        from routers.intelligence import (
            update_capital_works_schedule, CapitalWorksUpdateRequest, CapitalWorkItemUpdate,
        )

        with patch("routers.intelligence.db") as mock_db, \
                patch("repositories.digital_twin_repository.update_capital_schedule",
                      new_callable=AsyncMock) as mock_update:
            mock_db.capital_shock_risks.delete_many = AsyncMock()
            payload = CapitalWorksUpdateRequest(items=[
                CapitalWorkItemUpdate(asset_name="Lifts", replacement_year=2035,
                                      estimated_cost=205652.0,
                                      benefit_group_id="bg-tower", facility_id="fac-lift"),
            ])
            await update_capital_works_schedule(payload, {"role": "super_admin"}, "13195")

        items_written = mock_update.call_args[0][1]
        assert items_written[0]["benefit_group_id"] == "bg-tower"
        assert items_written[0]["facility_id"] == "fac-lift"
