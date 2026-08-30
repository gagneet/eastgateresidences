# @featuretrace:finance-postgres-read-cutover — shadow-diff-is-non-blocking resolver tests.
# Layer: test
# Data flow: pytest -> get_finance_route_runtime_state (mocked deps) -> source/eligible assertions
# Related: backend/services/finance_route_cutover_service.py
#          backend/services/cutover_config_service.py (FINANCIAL_PG_READS_BYPASS_SHADOW —
#          now inert; see the module docstring below)
# Tests: this file
"""Shadow-diff status no longer gates Postgres read eligibility (removed 2026-08-09,
explicit direction).

History: this file originally tested `financial_pg_reads_bypass_shadow`, an operator
waiver that let a route serve PG before its 7-day shadow soak completed, PROVIDED the
latest shadow comparison was still clean (zero diffs, no live critical diff). That
design assumed PostgreSQL disagreeing with MongoDB was evidence PostgreSQL was wrong,
and blocked promotion on that basis with no way to distinguish a real PG defect from
Mongo simply being stale. This session repeatedly proved that assumption backwards for
the finance domain: PostgreSQL is the system of record (CLAUDE.md), Mongo is the store
being retired, and PG's arrears figures were independently verified correct against the
live portal balance (exact to the cent, multiple units) while Mongo held pre-correction
figures nobody was updating.

Current behaviour: shadow-diff status (critical, warn, clean, or never-run) has ZERO
effect on eligibility. A route serves Postgres once it passes the gates that actually
reflect implementation readiness: read-only, postgres_read_supported, a verified-
substantive comparator (GAP-FIN-058 allowlist — this is "does real PG code exist for
this route", not a Mongo-agreement check, so it stays), domain mode, the reads feature
toggle, and the domain-source guard. route_readiness is still computed and returned for
observability; a genuinely new, unexplained PG defect is still investigable via
core.shadow_diffs directly, it just doesn't auto-block.

FINANCIAL_PG_READS_BYPASS_SHADOW itself is left defined (toggle_classification.py,
cutover_config_service.py, seeds/feature_toggles.py) but is no longer read by
get_finance_route_runtime_state at all -- test_waiver_toggle_is_now_inert below pins
that explicitly, so it can't silently start blocking again by accident.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.cutover_status import CutoverMode
from services import finance_route_cutover_service as svc

# A real promotable route (read_only=True, postgres_read_supported=True) from the policy
# table, also on the GAP-FIN-058 verified-substantive-comparator allowlist
# (finance_route_cutover_service._SUBSTANTIVE_COMPARATOR_ROUTE_KEYS) -- a route not on
# that allowlist is blocked before ever reaching the logic these tests exercise.
PROMOTABLE_ROUTE = "finance.unit_dashboard_overview"
BID = "13195"


def _readiness(status, *, diff_count=0, critical_count=0, ran=True):
    return {
        "status": status,
        "diff_count": diff_count,
        "critical_count": critical_count,
        "last_compared_at": "2026-08-06T00:00:00Z" if ran else None,
    }


def _domain_decision():
    d = MagicMock()
    d.blocked_reason = None
    d.postgres_allowed = True
    d.shadow_enabled = True
    return d


async def _run(*, readiness, reads_enabled=True, waiver=False,
               mode=CutoverMode.postgres_read):
    """Drive the resolver with all external deps mocked."""
    toggles = {
        "financial_pg_reads_enabled": reads_enabled,
        "financial_pg_reads_bypass_shadow": waiver,
    }

    async def fake_toggle(building_id, feature_key):
        return toggles.get(feature_key, False)

    status = MagicMock()
    status.mode = mode

    with patch.object(svc, "get_or_default_cutover_status",
                      AsyncMock(return_value=status)), \
         patch.object(svc, "require_domain_source",
                      AsyncMock(return_value=_domain_decision())), \
         patch.object(svc, "get_route_shadow_readiness",
                      AsyncMock(return_value=readiness)), \
         patch.object(svc, "is_cutover_feature_enabled",
                      AsyncMock(side_effect=fake_toggle)):
        return await svc.get_finance_route_runtime_state(
            building_id=BID, route_key=PROMOTABLE_ROUTE,
        )


class TestShadowDiffNonBlocking:
    @pytest.mark.asyncio
    async def test_not_shadow_pass_still_serves_postgres(self):
        state = await _run(readiness=_readiness("shadow_soaking"), waiver=False)
        assert state["source"] == "postgres"
        assert state["eligible_for_postgres_read"] is True
        assert state["blocked_reason"] is None

    @pytest.mark.asyncio
    async def test_critical_diff_no_longer_blocks(self):
        # Was a HARD, never-waivable gate before 2026-08-09; removed entirely now.
        state = await _run(
            readiness=_readiness("shadow_soaking", critical_count=1), waiver=False,
        )
        assert state["source"] == "postgres"
        assert state["eligible_for_postgres_read"] is True
        assert state["blocked_reason"] is None
        # Diagnostic data survives even though it no longer gates anything.
        assert state["route_readiness"]["critical_count"] == 1

    @pytest.mark.asyncio
    async def test_no_shadow_run_ever_no_longer_blocks(self):
        state = await _run(readiness=_readiness("not_started", ran=False), waiver=False)
        assert state["source"] == "postgres"
        assert state["eligible_for_postgres_read"] is True
        assert state["blocked_reason"] is None

    @pytest.mark.asyncio
    async def test_nonzero_noncritical_diffs_no_longer_block(self):
        state = await _run(
            readiness=_readiness("shadow_soaking", diff_count=3), waiver=False,
        )
        assert state["source"] == "postgres"
        assert state["eligible_for_postgres_read"] is True
        assert state["blocked_reason"] is None

    @pytest.mark.asyncio
    async def test_shadow_pass_still_serves_postgres(self):
        # Baseline unchanged: a genuinely clean soak also serves PG (same outcome as
        # every other shadow status now -- shadow_pass was never special-cased on its
        # own, it's just one more status that no longer matters for eligibility).
        state = await _run(readiness=_readiness("shadow_pass"), waiver=False)
        assert state["source"] == "postgres"
        assert state["eligible_for_postgres_read"] is True

    @pytest.mark.asyncio
    async def test_reads_toggle_still_gates_postgres(self):
        # Unlike shadow status, the reads feature toggle is a legitimate,
        # implementation-readiness gate and still blocks when off.
        state = await _run(
            readiness=_readiness("shadow_pass"), waiver=False, reads_enabled=False,
        )
        assert state["source"] == "mongo"
        assert state["eligible_for_postgres_read"] is False
        assert "financial_pg_reads_enabled is disabled" in state["blocked_reason"]

    @pytest.mark.asyncio
    async def test_waiver_toggle_is_now_inert(self):
        """FINANCIAL_PG_READS_BYPASS_SHADOW is still defined but no longer read by
        get_finance_route_runtime_state at all -- flipping it on or off must produce
        an identical result, since there is nothing left for it to waive."""
        state_off = await _run(readiness=_readiness("shadow_soaking", critical_count=2), waiver=False)
        state_on = await _run(readiness=_readiness("shadow_soaking", critical_count=2), waiver=True)
        assert state_off["source"] == state_on["source"] == "postgres"
        assert state_off["eligible_for_postgres_read"] == state_on["eligible_for_postgres_read"] is True
        assert state_off["shadow_waiver_applied"] is False
        assert state_on["shadow_waiver_applied"] is False
