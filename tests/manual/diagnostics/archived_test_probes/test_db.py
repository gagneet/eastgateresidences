import sys
from pathlib import Path

import asyncio

_BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKEND_DIR))

from database import db


async def test():
    try:
        doc = await db.annual_levies.find_one()
        print(doc)
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(test())
