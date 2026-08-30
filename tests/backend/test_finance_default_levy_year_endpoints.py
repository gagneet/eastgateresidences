#!/usr/bin/env python3
"""
Regression tests: endpoints that resolve a default levy year when the caller
omits `year` must never pick a not-yet-started year, even when annual_levies
already carries a row for it (e.g. a premature next-year budget import).

Covers the two highest-stakes confirmed-live bugs found in the 2026-07-02
cross-page levy audit (see docs/architecture/financial_data_flow_live_map.md
§0.8 for the full incident write-up):

  - GET /levy-status/{unit} with no `year` param — LevyPaymentPage.tsx (the
    actual levy PAYMENT page) is a confirmed live caller that omits it.
  - GET /arrears/detail — has no `year` param at all in its signature; the
    Arrears Recovery Board always resolves "the current year" internally.

Both previously used an unbounded `db.annual_levies.find_one(..., sort=[("year",
-1)])`, which happily returns a future, not-yet-started year if one exists.
"""

import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def _cursor(rows):
    c = MagicMock()
    c.sort = MagicMock(return_value=c)
    c.to_list = AsyncMock(return_value=rows)
    return c


@pytest.mark.asyncio
async def test_levy_status_no_year_param_resolves_current_not_future_year():
    """
    LevyPaymentPage.tsx's exact call shape: GET /levy-status/{unit} with no year
    param. annual_levies has both "2027" (premature next-year import) and "2026"
    (real current year) rows. Must resolve to 2026's rate, not 2027's.
    """
    from routers.finance import get_levy_status

    unit = {"entitlement": 100, "unit_number": "TH087"}
    settings = {
        "id": "main",
        "grace_period_days": 14,
        "levy_collection_frequency": "quarterly",
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "last",
        "financial_year_start_month": 1,
    }
    user = {"id": "admin-1", "role": "super_admin"}

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value=settings)),
        # The rate itself doesn't matter here — what matters is which YEAR the
        # rate lookup and the levy doc lookup both resolve to.
        patch("routers.finance.get_levy_rates",
              new=AsyncMock(return_value={"admin_annual": 34.08702, "sinking_annual": 9.95049})),
        patch("routers.finance.get_user_permissions") as mock_perms,
        patch("routers.finance.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = datetime(2026, 7, 2, tzinfo=timezone.utc)
        mock_perms.return_value = MagicMock(can_manage_finances=True)

        mock_db.units.find_one = AsyncMock(return_value=unit)
        # "2027" sorts higher than "2026" as a string — this is exactly the shape
        # that caused the bug when the fallback was a raw sort=[("year", -1)] query.
        mock_db.annual_levies.distinct = AsyncMock(return_value=["2027", "2026", "2025"])
        mock_db.annual_levies.find_one = AsyncMock(
            side_effect=lambda query, *a, **kw: (
                {"year": "2026", "payment_schedule": []} if query.get("year") == "2026"
                else {"year": "2027", "payment_schedule": []} if query.get("year") == "2027"
                else None
            )
        )
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
        # lifetime_paid_task (cumulative "Total Paid" tile) added to get_levy_status's
        # asyncio.gather() after this fixture was written -- an unconfigured
        # MagicMock().aggregate(...).to_list(1) is not awaitable, so it broke this
        # gather() with a generic TypeError unrelated to the year-resolution logic
        # this test actually covers.
        mock_db.unit_levy_ledger.aggregate = MagicMock(return_value=_cursor([]))
        mock_db.levy_payments.find = MagicMock(return_value=_cursor([]))
        mock_db.strata_owners.find_one = AsyncMock(return_value=None)
        mock_db._db.journal_entries.find.return_value = _cursor([])

        result = await get_levy_status("TH087", year=None, current_user=user, building_id="13195")

    assert "error" not in result, f"Expected levy data, got error: {result}"
    assert result["year"] == "2026", (
        f"get_levy_status with no year param resolved to {result.get('year')!r}, "
        f"expected '2026' (not the premature '2027' row)"
    )


class _FrozenDate(date):
    """date subclass with a fixed .today() — .fromisoformat() etc. behave normally,
    unlike patching `date` with a bare MagicMock (which breaks date.fromisoformat())."""

    @classmethod
    def today(cls):
        return date(2026, 7, 2)


@pytest.mark.asyncio
async def test_arrears_board_resolves_current_not_future_year():
    """GET /arrears/detail has no year param at all — must not default to 2027."""
    from routers.finance import get_arrears_board

    with (
        patch("routers.finance.db") as mock_db,
        patch("routers.finance._get_general_settings", new=AsyncMock(return_value={"financial_year_start_month": 1})),
        patch("routers.finance.datetime") as mock_dt,
        patch("routers.finance.date", _FrozenDate),
        patch("routers.finance.get_user_permissions") as mock_perms,
        # This test verifies year-resolution logic specifically reaches
        # db.unit_levy_ledger.find with the right year -- not PG cutover routing.
        # building_id="13195" is real East Gate, genuinely PG-eligible for
        # finance.arrears_detail since 2026-08-09; without this pin the ledger
        # fetch would go to Postgres instead of the mocked Mongo call this test
        # asserts on.
        patch("routers.finance.get_finance_route_runtime_state", new=AsyncMock(return_value={
            "route_key": "finance.arrears_detail", "source": "mongo", "run_shadow": False,
            "eligible_for_postgres_read": False, "blocked_reason": "forced mongo for unit test",
            "domain_mode": "mongo_primary", "route_readiness": {"status": "not_started"},
        })),
    ):
        mock_dt.now.return_value = datetime(2026, 7, 2, tzinfo=timezone.utc)
        mock_perms.return_value = MagicMock(can_view_finances=True)

        mock_db.annual_levies.distinct = AsyncMock(return_value=["2027", "2026", "2025"])
        mock_db.units.find.return_value = _cursor([])
        mock_db.user_units.find.return_value = _cursor([])
        mock_db.users.find.return_value = _cursor([])
        mock_db.user_units.aggregate.return_value = _cursor([])
        mock_db.levy_payments.aggregate.return_value = _cursor([])
        mock_db.buildings.find_one = AsyncMock(return_value={"arrears_interest_rate_pct": 10.0})
        mock_db.committee_resolutions.find_one = AsyncMock(return_value=None)
        mock_db.settings.find_one = AsyncMock(return_value={})

        captured_years = []

        def _ledger_find(query, *args, **kwargs):
            captured_years.append(query.get("year"))
            return _cursor([])

        mock_db.unit_levy_ledger.find = MagicMock(side_effect=_ledger_find)

        await get_arrears_board(current_user={"role": "super_admin"}, building_id="13195")

    assert captured_years, "unit_levy_ledger.find was never called"
    assert captured_years[0] == "2026", (
        f"Arrears board resolved year {captured_years[0]!r}, expected '2026' (not the "
        f"premature 2027 row)"
    )
