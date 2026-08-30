import os

import asyncio

from database import db
from request_context import set_ctx_building_id


async def main():
    set_ctx_building_id("13195")
    # UA001
    uoe = 82
    # 2025
    levy_2025 = await db.annual_levies.find_one({"year": "2025"})
    r2025_a = levy_2025.get("admin_levy_per_uoe_annual", 0)
    r2025_s = levy_2025.get("sinking_levy_per_uoe_annual", 0)
    print(f"2025 Rates: Admin={r2025_a}, Sinking={r2025_s}")
    t2025 = (r2025_a + r2025_s) * uoe
    print(f"UA001 2025: {t2025}")


if __name__ == "__main__":
    os.environ["MONGO_URL"] = "mongodb://localhost:27018"
    os.environ["DB_NAME"] = "strata_production"
    asyncio.run(main())
