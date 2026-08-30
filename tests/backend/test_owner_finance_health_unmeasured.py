# @featuretrace:finance-owner-dashboard — Unmeasured building health must not render as a measurement.
# Layer: test
# Data flow: building_summaries.health_score=None -> /owner-finance/health-explanation -> grade N/A (building-scoped).
# Related: backend/routers/owner_finance.py
"""A building_summaries row whose health_score is None must report "unmeasured", not 500.

Regression for a live HTTP 500 on GET /owner-finance/health-explanation, found on
2026-08-27 once East Gate's owner records were restored and the suite that covers this
route stopped being skipped for missing identities.

The summary writer stores None for a score it could not compute (East Gate sat at
health_coverage 0.1 with no financial inputs restored). The handler read it with
``summary.get("health_score", 0)`` — which does not default, because the key is present
and holds None — and then evaluated ``None >= 85``.

Defaulting the score to 0 would swap a crash for a worse answer: an unmeasured building
would be graded "D" on the strength of data nobody has. This is the same
missing-vs-measurement rule applied to PPM health and the engagement pulse on
2026-08-24, so the assertions below pin the "unmeasured" contract, not merely "no crash".
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routers.owner_finance import building_health_explanation

BID = "13195"
OWNER = {"id": "u1", "role": "owner", "effective_role": "owner"}


def _db(summary):
    db = MagicMock()
    db.building_summaries.find_one = AsyncMock(return_value=summary)
    return db


@pytest.mark.asyncio
async def test_none_health_score_reports_unmeasured_not_a_crash():
    db = _db({
        "building_id": BID,
        "health_score": None,          # the writer's "not computed" marker
        "arrears_rate_pct": None,
        "sinking_fund_pct": None,
        "compliance_overdue_count": None,
        "open_work_orders": 0,
        "health_coverage": 0.1,
        "computed_at": "2026-08-26T23:22:47.703389+00:00",
    })
    with patch("routers.owner_finance.db", db):
        out = await building_health_explanation(current_user=OWNER, building_id=BID)

    assert out["health_score"] is None, "an uncomputed score must stay None, never 0"
    assert out["grade"] == "N/A", "an unmeasured building must not be graded D"
    assert out["components"] == []
    assert out["is_authoritative_finance_metric"] is False
    assert out["last_computed_at"] == "2026-08-26T23:22:47.703389+00:00"


@pytest.mark.asyncio
async def test_real_score_still_grades_normally():
    """The fix must not swallow a genuine score — including a real, measured zero."""
    db = _db({
        "building_id": BID, "health_score": 88, "arrears_rate_pct": 1.0,
        "sinking_fund_pct": 95, "open_work_orders": 0, "compliance_overdue_count": 0,
    })
    with patch("routers.owner_finance.db", db):
        out = await building_health_explanation(current_user=OWNER, building_id=BID)
    assert out["health_score"] == 88
    assert out["grade"] == "A"
    assert [c["key"] for c in out["components"]] == [
        "sinking_fund", "arrears", "maintenance", "compliance"]


@pytest.mark.asyncio
async def test_measured_zero_is_not_treated_as_missing():
    """0 is a real score and must grade D — the inverse of the bug above."""
    db = _db({
        "building_id": BID, "health_score": 0, "arrears_rate_pct": 40.0,
        "sinking_fund_pct": 0, "open_work_orders": 9, "compliance_overdue_count": 3,
    })
    with patch("routers.owner_finance.db", db):
        out = await building_health_explanation(current_user=OWNER, building_id=BID)
    assert out["health_score"] == 0
    assert out["grade"] == "D"
    assert out["components"], "a measured zero still explains itself"


@pytest.mark.asyncio
async def test_no_summary_at_all_is_still_unmeasured():
    with patch("routers.owner_finance.db", _db(None)):
        out = await building_health_explanation(current_user=OWNER, building_id=BID)
    assert out["health_score"] is None
    assert out["grade"] == "N/A"
