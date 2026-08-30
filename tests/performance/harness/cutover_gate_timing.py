"""Times the finance route cutover gate, which every governed analytics/finance
read route calls before it does any work of its own.

Read-only: resolves control-plane state, creates no records.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv

load_dotenv(".env")

BID = os.environ.get("PERF_BUILDING", "13195")
ROUTE = os.environ.get("PERF_ROUTE_KEY", "analytics.levy_benchmarks")


async def t(label, coro_factory, n=5):
    # one warm-up, then n timed runs
    try:
        await coro_factory()
    except Exception as exc:  # noqa: BLE001
        print(f"{label:52s}  ERROR {type(exc).__name__}: {exc}")
        return
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        await coro_factory()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    print(f"{label:52s}  med {times[n // 2]:7.1f} ms   min {times[0]:7.1f}  max {times[-1]:7.1f}")


async def main():
    from request_context import set_ctx_building_id

    set_ctx_building_id(BID)

    from services.finance_route_cutover_service import get_finance_route_runtime_state
    from services.domain_source_guard import require_domain_source, DomainSourceAuditContext
    from services.cutover_status_service import get_or_default_cutover_status
    from services.finance_route_cutover_service import (
        get_route_shadow_readiness,
        is_cutover_feature_enabled,
        FINANCIAL_PG_READS_ENABLED,
    )

    ctx = DomainSourceAuditContext(
        route="/analytics/levy-benchmarks",
        source_service="perf_harness",
        feature_toggle_key=FINANCIAL_PG_READS_ENABLED,
        metadata={"route_key": ROUTE},
    )

    print(f"building={BID} route_key={ROUTE}\n")
    await t("WHOLE get_finance_route_runtime_state()",
            lambda: get_finance_route_runtime_state(building_id=BID, route_key=ROUTE))
    print("  components:")
    await t("  get_or_default_cutover_status(finance_ledger)",
            lambda: get_or_default_cutover_status(BID, "finance_ledger"))
    await t("  require_domain_source(read)",
            lambda: require_domain_source(domain="finance_ledger", building_id=BID,
                                          operation="read", requested_source="postgres",
                                          audit_context=ctx))
    await t("  require_domain_source(shadow_read)",
            lambda: require_domain_source(domain="finance_ledger", building_id=BID,
                                          operation="shadow_read", audit_context=ctx))
    await t("  get_route_shadow_readiness()",
            lambda: get_route_shadow_readiness(building_id=BID, route_key=ROUTE))
    await t("  is_cutover_feature_enabled(pg_reads)",
            lambda: is_cutover_feature_enabled(BID, FINANCIAL_PG_READS_ENABLED))


asyncio.run(main())
