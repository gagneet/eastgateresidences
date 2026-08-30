import os

import asyncio

from utils.finance_helpers import get_latest_levy_year, get_latest_ledger_year


async def main():
    bid = "13195"
    ly = await get_latest_levy_year(bid)
    gy = await get_latest_ledger_year(bid)
    print(f"Latest Levy Year: {ly}")
    print(f"Latest Ledger Year: {gy}")


if __name__ == "__main__":
    os.environ["MONGO_URL"] = "mongodb://localhost:27018"
    os.environ["DB_NAME"] = "strata_production"
    asyncio.run(main())
