from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from models.cutover_status import CutoverMode, DataSource, DomainCutoverStatus, ReadinessStatus
from services.domain_source_guard import DomainSourceDecision
from services.cutover_config_service import FINANCIAL_PG_READS_ENABLED
from services.finance_route_cutover_service import (
    _SUBSTANTIVE_COMPARATOR_ROUTE_KEYS,
    get_finance_route_readiness_table,
    get_finance_route_runtime_state,
    reset_finance_route_runtime_state_cache,
    has_any_promotable_finance_route,
    list_finance_route_policies,
)


def _status(mode: CutoverMode = CutoverMode.mongo_primary) -> DomainCutoverStatus:
    now = datetime.now(UTC)
    return DomainCutoverStatus(
        id="s1",
        building_id="13195",
        domain="finance_ledger",
        read_source=DataSource.postgres if mode in {CutoverMode.postgres_read, CutoverMode.postgres_write} else DataSource.mongo,
        write_source=DataSource.mongo,
        mode=mode,
        readiness_status=ReadinessStatus.shadow_active,
        rollback_available=True,
        created_at=now,
        updated_at=now,
        p0_snapshot={"financial_onboarding": {"status": "pass"}},
    )


def _decision(
    *,
    operation: str,
    source: DataSource,
    postgres_allowed: bool = False,
    shadow_enabled: bool = False,
    blocked_reason: str | None = None,
    mode: CutoverMode = CutoverMode.mongo_primary,
    readiness_status: ReadinessStatus = ReadinessStatus.shadow_active,
) -> DomainSourceDecision:
    return DomainSourceDecision(
        building_id="13195",
        requested_domain="finance",
        domain="finance_ledger",
        operation=operation,  # type: ignore[arg-type]
        source=source,
        postgres_allowed=postgres_allowed,
        shadow_enabled=shadow_enabled,
        blocked_reason=blocked_reason,
        mode=mode,
        readiness_status=readiness_status,
    )


def _guard_side_effect(read: DomainSourceDecision, shadow: DomainSourceDecision):
    async def _guard(**kwargs):
        return read if kwargs["operation"] == "read" else shadow

    return _guard


def test_route_inventory_has_prompt6_routes():
    keys = {p.route_key for p in list_finance_route_policies()}
    assert "finance.summary" in keys
    assert "finance.building_overview" in keys
    assert "finance.unit_dashboard_overview" in keys
    assert "finance.levy_kpi" in keys
    assert "finance.arrears_detail" in keys


def test_quarterly_budget_route_registered_but_not_yet_promotable():
    """finance.quarterly_budget (added 2026-08-01, fixing get_quarterly_budget's
    dependency on the dead quarters_charged field) must be visible in the route
    inventory -- CLAUDE.md's Data-Source Precedence rule forbids a route with a
    future Postgres path hardcoding "always Mongo" with no dispatch at all -- but
    honestly marked not-yet-promotable since no Postgres query or shadow
    comparator exists for it yet."""
    policies = {p.route_key: p for p in list_finance_route_policies()}
    assert "finance.quarterly_budget" in policies
    policy = policies["finance.quarterly_budget"]
    assert policy.postgres_read_supported is False


def test_gap_fin_052_extended_route_coverage_is_additive_only():
    """GAP-FIN-052 (2026-08-05) registered 23 additional routes (trust cluster,
    intelligence/capital-funding cluster, BI/portfolio/investor cluster, owner-hub
    + adjacent-cost pages) so every finance-adjacent route named in
    docs/finances/financial-data-consolidation-map.md is visible in the cutover
    readiness table. This must be purely additive: no duplicate route_keys, the
    original 8 Prompt-6 routes untouched, and every new entry honestly marked
    not-yet-promotable (no PG query or shadow comparator exists for any of them
    yet -- registering the policy alone does not change what any router serves)."""
    policies = list_finance_route_policies()
    keys = [p.route_key for p in policies]
    assert len(keys) == len(set(keys)), "duplicate route_key in _ROUTE_POLICIES"
    assert len(policies) >= 36

    original_eight = {
        "finance.summary",
        "finance.building_overview",
        "finance.unit_dashboard_overview",
        "finance.levy_kpi",
        "finance.arrears_detail",
        "finance.quarterly_budget",
        "finance.unit_levy_ledger",
        "finance.transactions",
    }
    by_key = {p.route_key: p for p in policies}
    assert original_eight <= set(by_key)

    new_keys = {
        "trust.v2_accounts",
        "trust.v1_accounts",
        "trust.reconciliation_runs",
        "trust.period_locks",
        "financial.matching_queue",
        "financial.ap_invoices",
        "intelligence.levy_fairness",
        "intelligence.capital_shock",
        "intelligence.levy_stability",
        "intelligence.special_levy_forecast",
        "levy_scenarios.list",
        "finance_intelligence.forecast",
        "finance_intelligence.anomalies",
        "finance_intelligence.health",
        "finance_intelligence.lot_summary",
        "savings.list",
        "building_stress.snapshot",
        "investor.building_health_report",
        "portfolio.dashboard",
        "bi.building_financial_summary",
        "owner_hub.properties",
        "owner_hub.property_ledger",
        "council_rates.get",
        "water_bills.get",
        "analytics.budget_variance",
    }
    assert new_keys <= set(by_key)
    for key in new_keys:
        policy = by_key[key]
        assert policy.postgres_read_supported is False, f"{key} must not claim PG parity yet"
        assert policy.shadow_supported is False, f"{key} has no shadow comparator yet"
        assert policy.read_only is True
    assert policy.shadow_supported is False

    # These analytics routes have graduated past "inert": each now has a shadow
    # comparator (GAP-FIN-055) feeding core.shadow_diffs so it can accrue shadow_pass
    # toward promotion. Still Mongo-primary (postgres_read_supported=False).
    # budget_variance is deliberately NOT here — its PG read model always raises
    # (Phase G not built), so a shadow would record pg_unavailable forever.
    shadow_ready = [
        "analytics.levy_allocation_breakdown",
        "analytics.sinking_fund_forecast",
        "analytics.levy_benchmarks",
        "analytics.expense_breakdown",
    ]
    for key in shadow_ready:
        p = by_key[key]
        assert p.shadow_supported is True, f"{key} should have a shadow comparator"
        assert p.postgres_read_supported is False, f"{key} must stay Mongo-primary until soaked"
        assert p.read_only is True
    assert by_key["analytics.budget_variance"].shadow_supported is False


@pytest.mark.asyncio
async def test_mongo_primary_forces_mongo_source():
    read = _decision(
        operation="read",
        source=DataSource.mongo,
        blocked_reason="mode/source do not allow postgres read: mongo_primary/mongo",
    )
    shadow = _decision(operation="shadow_read", source=DataSource.mongo, shadow_enabled=False)
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.mongo_primary)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ):
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.building_overview",
        )
    assert state["source"] == "mongo"
    assert state["eligible_for_postgres_read"] is False
    assert "mongo_primary" in (state["blocked_reason"] or "")


@pytest.mark.asyncio
async def test_postgres_read_requires_toggle_shadow_pass_and_domain_state():
    read = _decision(
        operation="read",
        source=DataSource.postgres,
        postgres_allowed=True,
        mode=CutoverMode.postgres_read,
    )
    shadow = _decision(operation="shadow_read", source=DataSource.dual, shadow_enabled=True, mode=CutoverMode.postgres_read)
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_read)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ):
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.unit_dashboard_overview",
        )
    assert state["source"] == "postgres"
    assert state["eligible_for_postgres_read"] is True


def test_gap_fin_058_levy_kpi_and_arrears_detail_reenabled():
    """GAP-FIN-058 (2026-08-07): finance.levy_kpi and finance.arrears_detail were briefly
    flipped to postgres_read_supported=True on a "shadow_pass 0/0 = clean soak" claim,
    then reverted before deploy: their comparators (_compare_levy_kpi_payloads /
    _compare_arrears_payloads) unconditionally returned `[]` — 0/0 was guaranteed by
    construction, not evidence of parity — and GAP-FIN-057 independently overstated PG
    paid_cents building-wide.

    RE-ENABLED 2026-08-09: both are now fixed. The comparators are real, same-concept
    comparisons (0d566ae, GAP-FIN-058 B1 — arrears via get_arrears_summary(grace_aware=
    True), levy_kpi via quarter_billed_total_display <-> levy_budgeted_cents), verified
    live returning a genuine substantive diff rather than []. GAP-FIN-057 is fixed and
    verified for building 13195 (zero remaining stale receipt_allocations building-wide).
    postgres_read_supported=True only re-enables live shadow-soak evaluation for these
    routes — it does not itself promote them; get_route_shadow_readiness and the
    critical_count hard gate (see test_promoted_route_reverts_to_mongo_on_critical_diff)
    still gate actual promotion on real soak data."""
    by_key = {p.route_key: p for p in list_finance_route_policies()}
    for key in ("finance.levy_kpi", "finance.arrears_detail"):
        assert by_key[key].postgres_read_supported is True, f"{key}: comparator + GAP-FIN-057 fixed 2026-08-09"
        assert by_key[key].shadow_supported is True
        assert by_key[key].read_only is True
        assert key in _SUBSTANTIVE_COMPARATOR_ROUTE_KEYS, f"{key} must be on the allowlist to actually gain eligibility"


@pytest.mark.asyncio
async def test_gap_fin_058_substantive_comparator_gate_blocks_even_a_perfect_shadow_pass():
    """The durable fix for the whole GAP-FIN-058 failure class: a route not on the
    verified-substantive-comparator allowlist must stay on Mongo even if every other gate
    would let it through — a perfectly clean, real (not mocked-away) shadow_pass with 0
    critical diffs, postgres_read_supported flipped True, PG reads enabled, and no domain
    block. This is what would have caught the original GAP-FIN-057-labelled promotion
    mechanically, instead of relying on a session noticing the no-op comparator by hand.

    Patches `_SUBSTANTIVE_COMPARATOR_ROUTE_KEYS` directly to exclude
    finance.building_overview for this test rather than relying on it staying off the
    real allowlist — as of GAP-FIN-064 (2026-08-18) it IS on the real allowlist (a real,
    substantive comparator; the flag/gate contradiction that ticket reported is fixed),
    so a future route-policy change genuinely removing every real gap would otherwise
    silently break this test's premise instead of testing the actual mechanism."""
    read = _decision(
        operation="read", source=DataSource.postgres, postgres_allowed=True,
        mode=CutoverMode.postgres_write,
    )
    shadow = _decision(
        operation="shadow_read", source=DataSource.dual, shadow_enabled=True,
        mode=CutoverMode.postgres_write,
    )
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={
            "status": "shadow_pass", "critical_count": 0, "diff_count": 0,
            "last_compared_at": datetime.now(UTC).isoformat(),
        }),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ), patch(
        "services.finance_route_cutover_service._SUBSTANTIVE_COMPARATOR_ROUTE_KEYS",
        frozenset(_SUBSTANTIVE_COMPARATOR_ROUTE_KEYS - {"finance.building_overview"}),
    ):
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.building_overview",
        )
    assert state["source"] == "mongo"
    assert state["eligible_for_postgres_read"] is False
    assert "verified-substantive allowlist" in state["blocked_reason"]


@pytest.mark.asyncio
async def test_gap_fin_058_allowlisted_route_still_serves_pg_on_clean_soak():
    """Sanity check the gate isn't overly broad: finance.unit_dashboard_overview IS on the
    verified-substantive-comparator allowlist (its arrears/closing_balance comparator was
    read and confirmed real, 2026-08-07), so it must still promote normally under the exact
    same clean-soak conditions the previous test used to prove a non-allowlisted route
    can't."""
    read = _decision(
        operation="read", source=DataSource.postgres, postgres_allowed=True,
        mode=CutoverMode.postgres_write,
    )
    shadow = _decision(
        operation="shadow_read", source=DataSource.dual, shadow_enabled=True,
        mode=CutoverMode.postgres_write,
    )
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={
            "status": "shadow_pass", "critical_count": 0, "diff_count": 0,
            "last_compared_at": datetime.now(UTC).isoformat(),
        }),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ):
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.unit_dashboard_overview",
        )
    assert state["source"] == "postgres"
    assert state["eligible_for_postgres_read"] is True
    assert state["blocked_reason"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("route_key", ["finance.levy_kpi", "finance.arrears_detail"])
async def test_gap_fin_058_reenabled_routes_go_postgres_on_clean_soak(route_key):
    """Regression guard, updated 2026-08-09: with the real comparators + GAP-FIN-057 fix
    now landed, postgres_read_supported=True, and both routes on
    _SUBSTANTIVE_COMPARATOR_ROUTE_KEYS, a mocked clean shadow soak (shadow_pass, 0
    critical) in postgres_write mode with reads enabled DOES now make these routes
    eligible for Postgres — this is the intended, unlocked behavior, not a bypass. The
    live soak is NOT mocked-clean right now (finance.levy_kpi has a real, unresolved
    quarter_billed_total_display 2x divergence found 2026-08-09 — see the critical_count
    hard gate test below for what keeps a route on Mongo when the soak is genuinely
    dirty); this test only proves the wiring responds correctly once soak data really is
    clean, so promotion isn't silently blocked by a stale flag once the underlying
    data issue is actually fixed."""
    read = _decision(
        operation="read",
        source=DataSource.postgres,
        postgres_allowed=True,
        mode=CutoverMode.postgres_write,
    )
    shadow = _decision(
        operation="shadow_read",
        source=DataSource.dual,
        shadow_enabled=True,
        mode=CutoverMode.postgres_write,
    )
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(
            return_value={
                "status": "shadow_pass",
                "critical_count": 0,
                "diff_count": 0,
                "last_compared_at": datetime.now(UTC).isoformat(),
            }
        ),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ):
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key=route_key,
        )
    assert state["source"] == "postgres"
    assert state["eligible_for_postgres_read"] is True
    assert state["blocked_reason"] is None


@pytest.mark.asyncio
async def test_route_serves_postgres_even_with_critical_shadow_diff():
    """REMOVED 2026-08-09 (explicit direction): a critical PG-vs-Mongo shadow diff no
    longer reverts an otherwise-eligible route to Mongo. That gate assumed disagreement
    with Mongo meant PG was wrong; this session repeatedly proved the opposite (PG
    verified correct against the live portal while Mongo held stale pre-correction
    figures). A route that passes every implementation-readiness gate (read-only,
    postgres_read_supported, verified-substantive comparator, domain mode, feature
    toggle, domain-source guard) now serves Postgres regardless of shadow-diff status.
    route_readiness is still returned for observability -- shadow_diffs remain
    investigable, they just don't block."""
    read = _decision(
        operation="read",
        source=DataSource.postgres,
        postgres_allowed=True,
        mode=CutoverMode.postgres_write,
    )
    shadow = _decision(
        operation="shadow_read",
        source=DataSource.dual,
        shadow_enabled=True,
        mode=CutoverMode.postgres_write,
    )
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_write)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(
            return_value={
                "status": "shadow_fail",
                "critical_count": 3,
                "diff_count": 3,
                "last_compared_at": datetime.now(UTC).isoformat(),
            }
        ),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ):
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.unit_dashboard_overview",
        )
    assert state["source"] == "postgres"
    assert state["eligible_for_postgres_read"] is True
    assert state["blocked_reason"] is None
    # Diagnostic data survives even though it no longer gates anything.
    assert state["route_readiness"]["critical_count"] == 3


@pytest.mark.asyncio
async def test_enabled_global_toggle_cannot_override_blocked_domain_state():
    read = _decision(
        operation="read",
        source=DataSource.mongo,
        postgres_allowed=False,
        blocked_reason="readiness is blocked",
        mode=CutoverMode.postgres_read,
        readiness_status=ReadinessStatus.blocked,
    )
    shadow = _decision(operation="shadow_read", source=DataSource.mongo, shadow_enabled=False)
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_read)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ):
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.unit_dashboard_overview",
        )
    assert state["source"] == "mongo"
    assert state["eligible_for_postgres_read"] is False
    assert state["blocked_reason"] == "readiness is blocked"


@pytest.mark.asyncio
async def test_shadow_warn_does_not_block_postgres_read():
    # REMOVED 2026-08-09: shadow_warn (or any non-clean shadow status) used to fall
    # through to a "requires shadow_pass" block. That branch no longer exists -- shadow
    # status is diagnostic only now. finance.unit_dashboard_overview is the route on the
    # verified-substantive-comparator allowlist, so it's still the one used to isolate
    # this specific behavior.
    read = _decision(
        operation="read",
        source=DataSource.postgres,
        postgres_allowed=True,
        mode=CutoverMode.postgres_read,
    )
    shadow = _decision(operation="shadow_read", source=DataSource.dual, shadow_enabled=True, mode=CutoverMode.postgres_read)
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_read)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_warn", "critical_count": 0}),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        # PG reads enabled, but the shadow-soak waiver is OFF — this must fall
        # into the final "requires shadow_pass" branch, not the waiver branch
        # (a blanket True for both toggles routes into the waiver branch
        # instead and produces a completely different blocked_reason).
        new=AsyncMock(side_effect=lambda building_id, feature_key: feature_key == FINANCIAL_PG_READS_ENABLED),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ):
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.unit_dashboard_overview",
        )
    assert state["source"] == "postgres"
    assert state["eligible_for_postgres_read"] is True
    assert state["blocked_reason"] is None
    assert state["route_readiness"]["status"] == "shadow_warn"


@pytest.mark.asyncio
async def test_non_promotable_route_never_uses_postgres_read():
    read = _decision(
        operation="read",
        source=DataSource.postgres,
        postgres_allowed=True,
        mode=CutoverMode.postgres_read,
    )
    shadow = _decision(operation="shadow_read", source=DataSource.dual, shadow_enabled=True, mode=CutoverMode.postgres_read)
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.postgres_read)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "shadow_pass", "critical_count": 0}),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=AsyncMock(side_effect=_guard_side_effect(read, shadow)),
    ):
        # finance.unit_levy_ledger, not finance.summary (promoted 2026-08-09) --
        # postgres_read_supported is still deliberately False here, gated on
        # GAP-FIN-031 (FY2026 bank-transaction-to-receipt matching).
        state = await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.unit_levy_ledger",
        )
    assert state["source"] == "mongo"
    assert state["eligible_for_postgres_read"] is False
    assert "GAP-FIN-031" in (state["blocked_reason"] or "")


@pytest.mark.asyncio
async def test_any_promotable_route_true_when_one_passes():
    with patch(
        "services.finance_route_cutover_service.get_finance_route_readiness_table",
        new=AsyncMock(
            return_value=[
                {
                    "route_key": "finance.building_overview",
                    "postgres_read_supported": True,
                    "eligible_for_postgres_read": True,
                }
            ]
        ),
    ):
        ok, rows = await has_any_promotable_finance_route(building_id="13195")
    assert ok is True
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_finance_route_guard_receives_audit_context():
    read = _decision(
        operation="read",
        source=DataSource.mongo,
        blocked_reason="mode/source do not allow postgres read: mongo_primary/mongo",
    )
    shadow = _decision(operation="shadow_read", source=DataSource.mongo, shadow_enabled=False)
    guard = AsyncMock(side_effect=_guard_side_effect(read, shadow))
    with patch(
        "services.finance_route_cutover_service.get_or_default_cutover_status",
        new=AsyncMock(return_value=_status(CutoverMode.mongo_primary)),
    ), patch(
        "services.finance_route_cutover_service.get_route_shadow_readiness",
        new=AsyncMock(return_value={"status": "not_started", "critical_count": 0}),
    ), patch(
        "services.finance_route_cutover_service.is_cutover_feature_enabled",
        new=AsyncMock(return_value=False),
    ), patch(
        "services.finance_route_cutover_service.require_domain_source",
        new=guard,
    ):
        await get_finance_route_runtime_state(
            building_id="13195",
            route_key="finance.building_overview",
        )

    read_context = guard.await_args_list[0].kwargs["audit_context"]
    shadow_context = guard.await_args_list[1].kwargs["audit_context"]
    assert read_context.route == "/finance/building-overview"
    assert read_context.source_service == "finance_route_cutover_service"
    assert read_context.feature_toggle_key == "financial_pg_reads_enabled"
    assert read_context.metadata == {"route_key": "finance.building_overview"}
    assert shadow_context == read_context


@pytest.mark.asyncio
async def test_readiness_table_flags_stopped_monitoring_for_promoted_routes():
    """2026-08-09 (GAP: stale shadow-diff panel): once a route is promoted to
    postgres, run_shadow permanently goes False for it, so diff_count/
    critical_count/last_compared_at stop being refreshed -- a real, previously
    undiscovered UX risk (finance.levy_kpi's admin panel kept showing its
    pre-fix critical diff for hours after the underlying bug was fixed, because
    nothing re-ran the comparison). shadow_monitoring_active must mirror
    run_shadow exactly so /admin/cutover can flag frozen rows instead of
    rendering them as live."""
    promoted_state = {
        "route_key": "finance.arrears_detail", "source": "postgres", "run_shadow": False,
        "eligible_for_postgres_read": True, "blocked_reason": None,
        "domain_mode": "postgres_write",
        "route_readiness": {
            "status": "shadow_fail", "critical_count": 16, "diff_count": 16,
            "last_compared_at": "2026-08-09T10:31:01+00:00",
        },
    }
    monitored_state = {
        "route_key": "finance.building_overview", "source": "mongo", "run_shadow": True,
        "eligible_for_postgres_read": False, "blocked_reason": "no PG implementation",
        "domain_mode": "mongo_primary",
        "route_readiness": {"status": "shadow_soaking", "critical_count": 0, "diff_count": 0, "last_compared_at": None},
    }

    def _fake_state(*, building_id, route_key):
        return promoted_state if route_key == "finance.arrears_detail" else monitored_state

    with patch(
        "services.finance_route_cutover_service.get_finance_route_runtime_state",
        new=AsyncMock(side_effect=_fake_state),
    ):
        rows = await get_finance_route_readiness_table(building_id="13195")

    by_key = {r["route_key"]: r for r in rows}
    assert by_key["finance.arrears_detail"]["current_source"] == "postgres"
    assert by_key["finance.arrears_detail"]["shadow_monitoring_active"] is False
    # Still on Mongo, shadow reads actively running -- the contrasting live case.
    assert by_key["finance.building_overview"]["shadow_monitoring_active"] is True


# ---------------------------------------------------------------------------
# Per-request memoisation of get_finance_route_runtime_state (perf, 2026-08-24)
#
# Resolving one route costs ~50 ms of control-plane work (two require_domain_source
# calls that each persist a hash-chained audit row, plus cutover-status,
# shadow-readiness and feature-toggle reads). routers/analytics.py asks the same
# question twice per request (_governed_read_source + _fire_analytics_shadow), so
# every governed dashboard endpoint paid that twice. The result is now memoised for
# the life of one request. These tests pin the two properties that make that safe:
# it must not resolve twice within a request, and it must never leak across them.
# ---------------------------------------------------------------------------

def _patched_gate(status, read_decision, shadow_decision):
    """Patch every control-plane dependency of the runtime-state resolver."""
    return (
        patch(
            "services.finance_route_cutover_service.get_or_default_cutover_status",
            new=AsyncMock(return_value=status),
        ),
        patch(
            "services.finance_route_cutover_service.require_domain_source",
            new=AsyncMock(side_effect=_guard_side_effect(read_decision, shadow_decision)),
        ),
        patch(
            "services.finance_route_cutover_service.get_route_shadow_readiness",
            new=AsyncMock(return_value={"status": "shadow_active"}),
        ),
        patch(
            "services.finance_route_cutover_service.is_cutover_feature_enabled",
            new=AsyncMock(return_value=False),
        ),
    )


@pytest.mark.asyncio
async def test_runtime_state_resolves_once_per_request():
    """Two calls for the same (building, route) inside one request hit the control
    plane once — the second is served from the memo."""
    reset_finance_route_runtime_state_cache()
    status = _status()
    read = _decision(operation="read", source=DataSource.mongo, blocked_reason=None)
    shadow = _decision(operation="shadow_read", source=DataSource.mongo, shadow_enabled=True)
    p1, p2, p3, p4 = _patched_gate(status, read, shadow)

    with p1 as m_status, p2, p3, p4:
        first = await get_finance_route_runtime_state(
            building_id="13195", route_key="analytics.levy_benchmarks"
        )
        second = await get_finance_route_runtime_state(
            building_id="13195", route_key="analytics.levy_benchmarks"
        )

    assert first == second
    assert m_status.await_count == 1, "second lookup must come from the per-request memo"


@pytest.mark.asyncio
async def test_runtime_state_memo_is_keyed_by_building_and_route():
    """The memo must never serve one building's or one route's answer to another —
    a shared answer would be a cross-tenant source-selection bug."""
    reset_finance_route_runtime_state_cache()
    status = _status()
    read = _decision(operation="read", source=DataSource.mongo)
    shadow = _decision(operation="shadow_read", source=DataSource.mongo)
    p1, p2, p3, p4 = _patched_gate(status, read, shadow)

    with p1 as m_status, p2, p3, p4:
        await get_finance_route_runtime_state(building_id="13195", route_key="finance.summary")
        await get_finance_route_runtime_state(building_id="16244", route_key="finance.summary")
        await get_finance_route_runtime_state(building_id="13195", route_key="finance.arrears_detail")
        # repeats of all three
        await get_finance_route_runtime_state(building_id="13195", route_key="finance.summary")
        await get_finance_route_runtime_state(building_id="16244", route_key="finance.summary")
        await get_finance_route_runtime_state(building_id="13195", route_key="finance.arrears_detail")

    assert m_status.await_count == 3, "each (building, route) pair resolves exactly once"


@pytest.mark.asyncio
async def test_runtime_state_memo_does_not_leak_between_requests():
    """The memo lives in a ContextVar. Each Starlette request runs in its own task,
    which copies the parent context, so a value set inside one request must not be
    visible to a sibling request — otherwise a promotion or toggle flip would be
    pinned for the process lifetime, and one tenant could be served another's
    resolved source."""
    reset_finance_route_runtime_state_cache()
    status = _status()
    read = _decision(operation="read", source=DataSource.mongo)
    shadow = _decision(operation="shadow_read", source=DataSource.mongo)
    p1, p2, p3, p4 = _patched_gate(status, read, shadow)

    async def one_request():
        # Mirrors a Starlette request: its own task, hence its own context copy.
        return await get_finance_route_runtime_state(
            building_id="13195", route_key="analytics.levy_benchmarks"
        )

    with p1 as m_status, p2, p3, p4:
        await asyncio.gather(
            asyncio.create_task(one_request()),
            asyncio.create_task(one_request()),
            asyncio.create_task(one_request()),
        )

    assert m_status.await_count == 3, (
        "each request must resolve independently; a leaked memo would show 1"
    )
