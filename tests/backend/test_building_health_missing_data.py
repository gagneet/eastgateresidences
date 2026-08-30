"""A building with no data must not receive a health score.

## The reported bug

The management dashboard showed, on a platform whose data and owners had all been
removed:

    Building health: 75/100 (Grade )
    Your building is in  condition. Tap to see the breakdown.

The number looked hardcoded. It was not — it was worse. **75/100 Grade B is
exactly what the old formula returned for a completely empty building**, because
absence of data was scored as excellence:

* no compliance items tracked  → ``max(0, 1 - 0 * 0.2)`` → compliance **100**
* no disputes recorded         → ``max(0, 1 - 0)``       → dispute **100**
* no work orders               → overdue ratio 0         → half of maintenance perfect

and because seven of the eleven inputs `analytics_worker` fed it were literal
constants that never queried anything (`sinking_fund_balance=0`,
`capital_works_10yr_forecast=1`, `arrears_lots=0`, `avg_work_order_age_days=7`,
`vote_participation_rate=0.5`, `volunteer_events_ytd=0`, `open_disputes=0`), with
`total_lots` and `open_work_orders` floored to 1 by ``max()``.

The empty grade was a third, separate bug: the writer stored ``health_grade``
while the reader asked for ``building_health_grade``.

``test_the_exact_reported_score_is_reproducible`` locks the diagnosis down so
nobody has to re-derive it, and the rest of the file stops each cause returning.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from services.health_score_service import (  # noqa: E402
    COMPONENT_WEIGHTS,
    MIN_COVERAGE,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    compute_building_health_score,
    grade_for,
    _engagement,
)

#: Exactly what analytics_worker built when every collection was empty, before
#: the fix. Kept verbatim as the regression fixture.
LEGACY_EMPTY_BUILDING_INPUTS = {
    "sinking_fund_balance": 0,
    "capital_works_10yr_forecast": 1,
    "arrears_lots": 0,
    "total_lots": 1,           # max(0, 1)
    "overdue_work_orders": 0,
    "open_work_orders": 1,     # max(0, 1)
    "avg_work_order_age_days": 7,
    "compliance_items_overdue": 0,
    "vote_participation_rate": 0.5,
    "volunteer_events_ytd": 0,
    "open_disputes": 0,
}

#: What the fixed analytics_worker builds for the same empty platform: real
#: zeroes where a count was genuinely taken, None where nothing is measurable.
FIXED_EMPTY_BUILDING_INPUTS = {
    "total_lots": 0,
    "work_orders_total": 0,
    "open_work_orders": 0,
    "overdue_work_orders": 0,
    "avg_work_order_age_days": None,
    "compliance_items_total": 0,
    "compliance_items_overdue": 0,
    "volunteer_events_ytd": 0,
    "arrears_lots": None,
    "sinking_fund_balance": None,
    "capital_works_10yr_forecast": None,
    "vote_participation_rate": None,
    "open_disputes": None,
}


def _code_of(*relative_parts: str) -> str:
    """Return a source file with comment lines and docstrings stripped.

    A naive substring scan matches the comment that EXPLAINS the removed code as
    readily as the code itself, so the test passes or fails on prose. Stripping
    comments first means these assertions are about what executes.
    """
    path = Path(__file__).resolve().parents[2].joinpath(*relative_parts)
    lines = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Drop trailing comments, but not a '#' inside a string literal.
        if "#" in line and line.count('"') % 2 == 0 and line.count("'") % 2 == 0:
            line = line.split("#", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def _healthy_building(**overrides) -> dict:
    """A real, fully-measured building. Every input present and genuine."""
    base = {
        "total_lots": 87,
        "sinking_fund_balance": 500_000,
        "capital_works_10yr_forecast": 400_000,
        "arrears_lots": 2,
        "work_orders_total": 140,
        "open_work_orders": 6,
        "overdue_work_orders": 0,
        "avg_work_order_age_days": 5,
        "compliance_items_total": 14,
        "compliance_items_overdue": 0,
        "vote_participation_rate": 0.8,
        "volunteer_events_ytd": 6,
        "open_disputes": 0,
    }
    base.update(overrides)
    return base


# ── The diagnosis, locked down ───────────────────────────────────────────────

def test_the_exact_reported_score_is_reproducible():
    """75/100 Grade B was the score of an EMPTY building, not stale data.

    Feeding the old inputs through the current scorer must no longer reach 75 on
    full coverage. If this ever produces a confident high score again from
    nothing, the regression is back.
    """
    result = compute_building_health_score(LEGACY_EMPTY_BUILDING_INPUTS)

    # Compliance can no longer score 100 off an empty register.
    assert result["components"]["compliance"] is None
    assert "compliance" in result["unavailable_components"]
    assert result["coverage"] < 1.0


def test_an_empty_platform_gets_no_score_at_all():
    """The user-facing fix: no data in, no number out."""
    result = compute_building_health_score(FIXED_EMPTY_BUILDING_INPUTS)

    assert result["score"] is None
    assert result["grade"] is None
    assert result["status"] == STATUS_INSUFFICIENT

    # Every component that could flatter an empty building is excluded. Engagement
    # survives — volunteer_events_ytd=0 is a genuine count of a real collection —
    # but it scores 0, which is the safe direction, and 0.10 coverage is far below
    # MIN_COVERAGE so nothing is published anyway.
    for component in ("financial", "maintenance", "compliance", "dispute"):
        assert component in result["unavailable_components"], (
            f"{component} must not be scored for a building with no data"
        )
    assert result["coverage"] < MIN_COVERAGE


def test_no_input_at_all_is_handled():
    """Generated function header.

    Function: test_no_input_at_all_is_handled
    Path: tests/backend/test_building_health_missing_data.py
    """
    for payload in ({}, None):
        result = compute_building_health_score(payload)
        assert result["score"] is None
        assert result["status"] == STATUS_INSUFFICIENT


# ── Absence must never score as excellence ───────────────────────────────────

def test_an_empty_compliance_register_is_not_full_marks():
    """"0 of 0 overdue" is an empty register, not a clean bill of health."""
    result = compute_building_health_score(
        _healthy_building(compliance_items_total=0, compliance_items_overdue=0)
    )
    assert result["components"]["compliance"] is None


def test_a_populated_compliance_register_with_nothing_overdue_IS_full_marks():
    """The counterpart. A real register with no overdue items is genuinely 100."""
    result = compute_building_health_score(
        _healthy_building(compliance_items_total=14, compliance_items_overdue=0)
    )
    assert result["components"]["compliance"] == 100


def test_untracked_work_orders_are_not_perfect_maintenance():
    """Generated function header.

    Function: test_untracked_work_orders_are_not_perfect_maintenance
    Path: tests/backend/test_building_health_missing_data.py
    """
    result = compute_building_health_score(
        _healthy_building(work_orders_total=0, open_work_orders=0, avg_work_order_age_days=None)
    )
    assert result["components"]["maintenance"] is None


def test_untracked_disputes_are_not_a_perfect_dispute_score():
    """Generated function header.

    Function: test_untracked_disputes_are_not_a_perfect_dispute_score
    Path: tests/backend/test_building_health_missing_data.py
    """
    result = compute_building_health_score(_healthy_building(open_disputes=None))
    assert result["components"]["dispute"] is None


def test_a_measured_zero_still_scores_full_marks():
    """Missing is excluded; measured-and-none is rewarded. The distinction is the point."""
    result = compute_building_health_score(_healthy_building(open_disputes=0))
    assert result["components"]["dispute"] == 100


# ── Renormalisation ──────────────────────────────────────────────────────────

def test_partial_coverage_still_reads_out_of_100():
    """Excluding a component must not silently cap the score at the missing weight.

    A building perfect on everything it CAN measure should read 100, not
    100 minus the weight of what it cannot measure — otherwise "we have no
    disputes register" would look like "this building has problems".
    """
    result = compute_building_health_score(
        _healthy_building(
            arrears_lots=0,
            open_disputes=None,       # -0.10
            vote_participation_rate=None,
            volunteer_events_ytd=None,  # -0.10
            overdue_work_orders=0,
            avg_work_order_age_days=0,
        )
    )

    assert result["status"] == STATUS_OK
    assert result["score"] == 100
    assert result["coverage"] == pytest.approx(0.80)


def test_coverage_below_the_minimum_publishes_nothing():
    """Generated function header.

    Function: test_coverage_below_the_minimum_publishes_nothing
    Path: tests/backend/test_building_health_missing_data.py
    """
    # Only engagement (0.10) measurable — well under MIN_COVERAGE.
    result = compute_building_health_score(
        {"vote_participation_rate": 1.0, "volunteer_events_ytd": 10}
    )
    assert result["coverage"] < MIN_COVERAGE
    assert result["score"] is None
    assert result["status"] == STATUS_INSUFFICIENT


# ── Engagement: an empty volunteer register is not a disengaged community ────


def test_engagement_unavailable_when_no_volunteer_events_are_tracked():
    """The East Gate defect: engagement scored 0/100 off an empty collection.

    volunteer_events_ytd=0 with volunteer_events_total=0 means the building has
    never used volunteer events, not that its community failed to turn up. It
    rendered as the only populated axis on the pulse card — a lone full-red
    "Engagement 0" beside four blank axes — asserting a measurement that had no
    data behind it. This is the mirror of the PPM health_score=100 defect: the
    same absence-is-a-measurement bug, pointing the other way.
    """
    assert _engagement({"volunteer_events_ytd": 0, "volunteer_events_total": 0}) is None


def test_engagement_scores_a_genuine_zero_when_events_exist():
    """Events tracked but none completed IS a real zero and must still score."""
    assert _engagement({"volunteer_events_ytd": 0, "volunteer_events_total": 12}) == 0.0


def test_engagement_scores_real_volunteer_activity():
    assert _engagement({"volunteer_events_ytd": 10, "volunteer_events_total": 12}) == 1.0


def test_engagement_falls_back_when_total_is_absent():
    """A payload predating volunteer_events_total keeps its previous behaviour."""
    assert _engagement({"volunteer_events_ytd": 5}) == pytest.approx(0.5)


def test_engagement_vote_rate_still_carries_the_axis_alone():
    """An empty volunteer register must not suppress a real voting signal."""
    assert _engagement(
        {"vote_participation_rate": 0.8, "volunteer_events_ytd": 0, "volunteer_events_total": 0}
    ) == pytest.approx(0.8)


def test_building_with_no_community_data_at_all_publishes_no_axis():
    """East Gate's live state: every axis unavailable, nothing fabricated."""
    result = compute_building_health_score(
        {"vote_participation_rate": None, "volunteer_events_ytd": 0, "volunteer_events_total": 0}
    )
    assert result["components"]["engagement"] is None
    assert "engagement" in result["unavailable_components"]
    assert result["score"] is None
    assert result["status"] == STATUS_INSUFFICIENT


def test_component_weights_sum_to_one():
    """Renormalisation arithmetic depends on this."""
    assert sum(COMPONENT_WEIGHTS.values()) == pytest.approx(1.0)


# ── The grade thresholds ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "score,expected", [(100, "A"), (80, "A"), (79, "B"), (65, "B"), (64, "C"), (50, "C"), (49, "D"), (0, "D")]
)
def test_grade_boundaries(score, expected):
    """Generated function header.

    Function: test_grade_boundaries
    Path: tests/backend/test_building_health_missing_data.py
    """
    assert grade_for(score) == expected


def test_a_published_score_always_has_a_grade():
    """The blank "(Grade )" in the reported message must be unreachable from here."""
    result = compute_building_health_score(_healthy_building())
    assert result["status"] == STATUS_OK
    assert result["score"] is not None
    assert result["grade"] in {"A", "B", "C", "D"}


# ── The field-name mismatch that blanked the grade ───────────────────────────

def test_the_worker_writes_both_grade_field_names():
    """`health_grade` was written; `building_health_grade` was read. Hence "(Grade )".

    Asserted against the source because the writer is a worker with a live-DB
    dependency, and the contract that matters is which KEYS it emits.
    """
    source = _code_of("backend", "workers", "analytics_worker.py")

    for key in (
        '"building_health_score"',
        '"building_health_grade"',
        '"health_score"',
        '"health_grade"',
        '"health_status"',
    ):
        assert key in source, f"analytics_worker no longer writes {key}"


def test_the_morning_card_reads_both_grade_field_names():
    """Generated function header.

    Function: test_the_morning_card_reads_both_grade_field_names
    Path: tests/backend/test_building_health_missing_data.py
    """
    source = _code_of("backend", "services", "morning_card_service.py")

    assert '"building_health_grade"' in source
    assert '"health_grade"' in source
    assert 'health_status") == "ok"' in source, (
        "the morning card must publish only on an explicit health_status of 'ok'. "
        "Absence of the marker means the row predates the 2026-08-24 rewrite and "
        "its score came from the formula that graded empty buildings at 75."
    )


def test_the_workers_hardcoded_health_inputs_are_gone():
    """Seven literal constants were being passed off as measurements."""
    source = _code_of("backend", "workers", "analytics_worker.py")

    for banned in (
        '"vote_participation_rate": 0.5',
        '"avg_work_order_age_days": 7',
        '"capital_works_10yr_forecast": 1,',
        '"open_work_orders": max(open_wos, 1)',
        '"total_lots": max(total_lots, 1)',
    ):
        assert banned not in source, f"analytics_worker still fabricates {banned}"


def test_the_community_dashboard_no_longer_derives_the_forecast_from_the_balance():
    """`capital_works_10yr_forecast = sinking_balance * 1.2` pinned adequacy at 0.833.

    The target moved with the balance, so the metric could never detect an
    underfunded reserve.
    """
    source = _code_of("backend", "routers", "community_dashboard.py")

    assert "sinking_balance * 1.2" not in source


# ── Robustness of the inputs themselves ──────────────────────────────────────

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "twelve", True, False, object()])
def test_a_corrupt_stored_value_makes_the_component_unavailable(bad):
    """A bad value must never crash the scorer or leak into the arithmetic.

    NaN in particular was already neutralised before this was made explicit, but
    only by accident: ``max(0.0, nan)`` returns 0.0 because ``nan > 0.0`` is
    False. Rearranging an expression could have carried it through to ``round()``,
    which raises on NaN. Found during the post-implementation audit.
    """
    result = compute_building_health_score(_healthy_building(open_disputes=bad))

    assert result["components"]["dispute"] is None
    # The rest of the building is unaffected — one bad field is not a total loss.
    assert result["components"]["compliance"] == 100


def test_a_corrupt_value_never_produces_an_out_of_range_score():
    """Generated function header.

    Function: test_a_corrupt_value_never_produces_an_out_of_range_score
    Path: tests/backend/test_building_health_missing_data.py
    """
    for bad in (float("nan"), float("inf"), -1e9, 1e12):
        result = compute_building_health_score(
            _healthy_building(sinking_fund_balance=bad, arrears_lots=bad, vote_participation_rate=bad)
        )
        assert result["score"] is None or 0 <= result["score"] <= 100
        assert (result["score"] is None) == (result["grade"] is None)


def test_the_work_order_age_query_is_bounded():
    """An unbounded cursor here runs on every recompute-triggering event."""
    source = _code_of("backend", "workers", "analytics_worker.py")

    assert "WORK_ORDER_AGE_SAMPLE_LIMIT" in source
    assert "to_list(WORK_ORDER_AGE_SAMPLE_LIMIT)" in source
