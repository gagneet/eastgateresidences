"""Local dashboard endpoint timing sweep.

Times every API call made by the four dashboard surfaces (Management new/classic,
Owner new/classic) against the locally running backend, so we can see which
endpoints dominate page load. Read-only: issues GETs only, creates no records.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv

load_dotenv(".env")

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from utils.auth import create_token

BASE = os.environ.get("PERF_BASE", "http://localhost:8003/api")
BID = os.environ.get("PERF_BUILDING", "13195")
UNIT = os.environ.get("PERF_UNIT", "TH087")
YEAR = os.environ.get("PERF_YEAR", "2026")

MGMT_NEW = [
    "/analytics/compliance-summary",
    "/workflow-requests/stats/triage",
    f"/analytics/levy-benchmarks?financial_year={YEAR}",
    "/analytics/activities?limit=10&offset=0",
    f"/finance/building-overview?year={YEAR}",
    "/finance/portal-bank-balances",
    f"/analytics/levy-allocation-breakdown?year={YEAR}",
    "/analytics/sinking-fund-forecast?years=10",
    "/analytics/maintenance/spend-trend",
    "/intelligence/levy-fairness",
    "/intelligence/capital-shock",
    f"/analytics/dashboard-v2-extras?unit_number={UNIT}",
    "/meetings?status=scheduled&limit=1",
    "/analytics/market-snapshot",
]

MGMT_CLASSIC = [
    f"/stats/building-kpis?financial_year={YEAR}",
    f"/finance/building-overview?year={YEAR}",
    "/finance/portal-bank-balances",
    "/arrears/detail",
    "/analytics/maintenance-stats",
    "/analytics/activities?limit=15",
    "/workflow-requests?status=awaiting_review",
    "/workflow-requests?status=overdue",
    "/workflow-requests/stats/triage",
    f"/analytics/levy-benchmarks?financial_year={YEAR}",
    "/admin/stats",
    "/analytics/sinking-fund-forecast?years=10",
    "/analytics/compliance-summary",
    "/analytics/maintenance/spend-trend",
    "/analytics/expenses-by-supplier?months=12",
    "/ppm/dashboard",
    "/ppm/upcoming?days=60",
    f"/analytics/diff-since?since=2026-08-20T00:00:00Z&year={YEAR}",
    "/intelligence/levy-fairness",
    "/intelligence/capital-shock",
]

OWNER_NEW = [
    f"/finance/unit-dashboard-overview/{UNIT}?year={YEAR}",
    f"/owner-hub/unit-tco?unit_number={UNIT}&year={YEAR}",
    f"/analytics/my-streak?unit_number={UNIT}",
    "/workflow-requests?limit=5",
    "/workflow-requests?limit=100",
]

OWNER_CLASSIC = [
    f"/finance/unit-dashboard-overview/{UNIT}?year={YEAR}",
    "/analytics/sinking-fund-forecast?years=10",
    "/analytics/maintenance-stats",
    "/analytics/activities?limit=15",
    "/analytics/market-snapshot",
    "/annual-levies",
    "/agm",
    "/analytics/compliance-summary",
    "/intelligence/summary",
    "/intelligence/capital-shock",
    f"/analytics/levy-allocation-breakdown?year={YEAR}",
    f"/analytics/my-streak?unit_number={UNIT}",
    "/workflow-requests?limit=5",
    f"/analytics/dashboard-v2-extras?unit_number={UNIT}",
    f"/owner-hub/unit-tco?unit_number={UNIT}&year={YEAR}",
    "/meetings?status=scheduled&limit=1",
    "/security/my-activity",
    "/documents/important",
    f"/units/{UNIT}/market-valuation",
]

SURFACES = [
    ("MANAGEMENT (new /dashboard)", MGMT_NEW),
    ("MANAGEMENT (classic /management/classic)", MGMT_CLASSIC),
    ("OWNER (new /dashboard)", OWNER_NEW),
    ("OWNER (classic /owner-hub/classic)", OWNER_CLASSIC),
]


ADMIN_EMAIL = os.environ.get("PERF_ADMIN_EMAIL", "administrator@strataos.live")
BYPASS = "00000000-0000-0000-0000-000000000000"


async def mint() -> str:
    """Mint a short-lived JWT for the existing East Gate administrator account.

    Read-only benchmarking: no user, session, or record is created, so there is
    nothing to tear down. The account is looked up, never modified.
    """
    import asyncpg

    dsn = (os.environ["DATABASE_URL"]).replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    await conn.execute(f"SET app.tenant_id = '{BYPASS}'")
    row = await conn.fetchrow(
        "SELECT user_id, email, role::text AS role, tenant_id FROM core.users WHERE email = $1",
        ADMIN_EMAIL,
    )
    await conn.close()
    if not row:
        raise SystemExit(f"admin account {ADMIN_EMAIL} not found in core.users")
    return create_token(
        user_id=str(row["user_id"]),
        email=row["email"],
        role=row["role"],
        building_id=BID,
        unit_number=UNIT,
        tenant_id=str(row["tenant_id"]),
    )


async def timed(client, path):
    t0 = time.perf_counter()
    try:
        r = await client.get(BASE + path)
        ms = (time.perf_counter() - t0) * 1000
        return ms, r.status_code, len(r.content)
    except Exception as exc:  # noqa: BLE001
        return (time.perf_counter() - t0) * 1000, f"ERR {type(exc).__name__}", 0


async def main():
    token = await mint()
    headers = {"Authorization": f"Bearer {token}", "X-Building-ID": BID}
    results = {}
    async with httpx.AsyncClient(headers=headers, timeout=120.0) as client:
        # warm the process once so we measure steady state, not first-import cost
        await timed(client, "/analytics/compliance-summary")

        seen = {}
        for name, eps in SURFACES:
            rows = []
            for ep in eps:
                if ep in seen:
                    rows.append((ep, *seen[ep]))
                    continue
                ms, code, size = await timed(client, ep)
                seen[ep] = (ms, code, size)
                rows.append((ep, ms, code, size))
            results[name] = rows

    for name, rows in results.items():
        total = sum(r[1] for r in rows)
        slowest = max(r[1] for r in rows)
        print(f"\n=== {name} — {len(rows)} calls")
        print(f"    sequential total {total:8.0f} ms   |   parallel floor {slowest:8.0f} ms")
        for ep, ms, code, size in sorted(rows, key=lambda r: -r[1]):
            flag = "  <<<" if ms > 300 else ""
            print(f"    {ms:8.0f} ms  {str(code):>5}  {size:>8}B  {ep}{flag}")


async def print_token():
    print(await mint())


if __name__ == "__main__":
    if "--print-token" in sys.argv:
        asyncio.run(print_token())
    else:
        asyncio.run(main())
