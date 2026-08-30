# @featuretrace:levy-kpi — An unrecorded fund split is unknown, never zero.
# Layer: test
# Data flow: unit_levy_ledger -> $group with *_known counts -> building-overview per-fund (building-scoped).
# Related: backend/routers/finance.py (_get_building_overview_mongo_fallback)
#          tasks/GAP-FIN-073-post-restore-finance-audit.md
"""$sum returns 0 for an all-null group, which hides a real distinction.

East Gate's 2026 ledger carries admin_levied and sinking_levied NULL on all 87 rows
while total_levied is populated ($220,187.56), because 2026 was back-solved from a portal
balance rather than built from itemised per-fund charges (GAP-FIN-035). 2021-2025 all
carry the split.

Summing alone cannot tell "every unit was charged nothing" from "no unit has a split
recorded", so the endpoint reported:

    admin_fund.total_levied  0.0        total_levied  220187.56
    admin_fund.collection_rate 0.0      levies_paid_pct     96.4

which states something false about the admin fund rather than declining to answer. The
pipeline now counts non-null contributors per fund, and the route returns None when that
count is zero — CLAUDE.md's missing-vs-measurement rule, the same class already fixed in
/owner-finance/health-explanation.
"""

import re
from pathlib import Path

FINANCE = (Path(__file__).resolve().parents[2] / "backend" / "routers" / "finance.py").read_text()


class TestPipelineDetectsUnknown:
    def test_the_group_stage_counts_known_contributors_per_fund(self):
        for field in ("admin_levied_known", "sinking_levied_known"):
            assert f'"{field}"' in FINANCE, (
                f"{field} is required: $sum alone returns 0 for an all-null group, so the "
                f"summed value cannot distinguish unknown from zero"
            )

    def test_the_counts_use_ifNull_rather_than_a_truthiness_test(self):
        """A truthy test would also discard a legitimate 0.00 charge."""
        assert '{"$ifNull": ["$admin_levied", None]}' in FINANCE


class TestRouteReportsUnknownAsNone:
    def test_per_fund_values_are_gated_on_the_known_count(self):
        for name in ("admin_levied", "admin_paid"):
            assert re.search(rf"{name} = .*if _admin_split_known else None", FINANCE), (
                f"{name} must be None when no unit carries an admin split"
            )
        for name in ("sinking_levied", "sinking_paid"):
            assert re.search(rf"{name} = .*if _sinking_split_known else None", FINANCE)

    def test_the_gate_is_a_count_greater_than_zero(self):
        assert '_admin_split_known = int(agg.get("admin_levied_known", 0) or 0) > 0' in FINANCE

    def test_building_wide_totals_are_not_gated(self):
        """total_levied is populated for 2026 and must keep reporting its real value.

        Only the per-FUND breakdown is unknown; nulling the total too would turn one
        false statement into a worse one.
        """
        assert 'total_levied = round(float(agg.get("total_levied", 0) or 0), 2)' in FINANCE
        assert "total_levied = ... if" not in FINANCE
