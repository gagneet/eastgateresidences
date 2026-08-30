# @featuretrace:levy-kpi — collection_rate must carry metric 1, never full-year coverage.
# Layer: test
# Data flow: get_collection_rate_metrics -> /stats/building-kpis.collection_rate (building-scoped).
# Related: backend/server.py (/stats/building-kpis)
#          backend/domain/finance/formulas/collection.py
"""The three collection metrics must stay three metrics.

CLAUDE.md is explicit: full-year coverage "must NEVER be labelled Collection Rate
anywhere in a UI or API response". `/stats/building-kpis` violated it — `collection_rate`
and `full_year_coverage_pct` were assigned the SAME variable, while five named frontend
components displayed the first under the label "Collection Rate".

It survived because at East Gate the two differ by 0.03pp (96.40 vs 96.43). They agree
until a building pays meaningfully ahead, and then coverage silently overstates
collection — which is the exact failure the separation exists to prevent.
"""

import re
from pathlib import Path

SERVER = (Path(__file__).resolve().parents[2] / "backend" / "server.py").read_text()

# The /stats/building-kpis response body.
_BLOCK = SERVER[SERVER.index("METRIC[collection_rate]: return"):][:2600]


def test_collection_rate_is_not_assigned_the_coverage_variable():
    """The original defect: both keys took `round(collection_rate, 2)`."""
    assert '"collection_rate": round(collection_rate, 2),' not in _BLOCK, (
        "collection_rate is once again the full-year coverage figure, which CLAUDE.md "
        "forbids from being labelled Collection Rate in any API response"
    )


def test_collection_rate_sources_the_due_date_metric():
    assert "due_date_collection_rate_pct" in _BLOCK.split('"due_date_collection_rate_pct"')[0], (
        "collection_rate must be derived from the due-date metric"
    )


def test_full_year_coverage_is_still_reported_separately():
    """Metric 2 is legitimate and must keep its own key — just not this name."""
    assert '"full_year_coverage_pct": round(collection_rate, 2),' in _BLOCK


def test_collected_in_advance_remains_its_own_figure():
    """Metric 3 is never folded into either of the other two."""
    assert '"collected_in_advance"' in _BLOCK


def test_the_three_metrics_are_distinct_keys():
    for key in ("collection_rate", "full_year_coverage_pct", "collected_in_advance"):
        assert f'"{key}"' in _BLOCK, f"{key} missing from the KPI contract"
