"""Seed per-building unit display prefix rules (idempotent).

# @featuretrace:finance-owner-dashboard — seeds db.settings unit_display rules per building.
# Layer: seed
# Data flow: this script → db.settings {type: "unit_display"} → utils/unit_number.py resolution + UI display (building-scoped).
# Related: backend/services/settings_service.py
#           backend/routers/settings.py
# Collection: settings

Unit numbers are conceptually numeric lots; the display prefix (UA/TH/A/…)
is per-building presentation configuration set during onboarding, not data.
This seed records the known production/demo conventions:

- East Gate (13195): lots 1-70 apartments "UA###", lots 71-87 townhouses "TH###".
- StrataOS Demo Residences (UPDEMO5): lots 1-14 "A#" (no zero padding).

Run:  cd backend && python3 seeds/unit_display_config.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

UNIT_DISPLAY_SEED = {
    "13195": [
        {"prefix": "UA", "min": 1, "max": 70, "pad": 3},
        {"prefix": "TH", "min": 71, "max": 87, "pad": 3},
    ],
    "UPDEMO5": [
        {"prefix": "A", "min": 1, "max": 14, "pad": 1},
    ],
}


def _backend_env() -> dict[str, str]:
    """Generated function header.

    Function: _backend_env
    Path: backend/seeds/unit_display_config.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/seeds/unit_display_config.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    env = _backend_env()
    uri = os.environ.get("MONGO_URL") or env.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    db_name = os.environ.get("DB_NAME") or env.get("DB_NAME") or "strataos_production"
    client = AsyncIOMotorClient(uri)
    db = client[db_name]
    try:
        for building_id, rules in UNIT_DISPLAY_SEED.items():
            await db.settings.update_one(
                {"building_id": building_id, "type": "unit_display"},
                {"$set": {"building_id": building_id, "type": "unit_display", "rules": rules}},
                upsert=True,
            )
            print(f"unit_display rules upserted for {building_id}: {rules}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
