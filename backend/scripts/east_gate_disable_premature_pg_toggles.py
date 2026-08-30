#!/usr/bin/env python3
# @featuretrace:cutover-toggle-safety — Disables premature East Gate PostgreSQL finance writes.
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from db_postgres.repos.config_repo import upsert_feature_toggle_override  # noqa: E402

REASON = "East Gate finance PG writes remain disabled until P0 gates pass and 7-day zero-divergence shadow is proven."


async def run(building_id: str, apply: bool) -> dict[str, object]:
    """Generated function header.

    Function: run
    Path: backend/scripts/east_gate_disable_premature_pg_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    target = {"building_id": building_id, "feature_key": "financial_pg_writes_enabled", "is_enabled": False, "reason": REASON}
    if not apply:
        return {"dry_run": True, "would_upsert_override": target}
    row = await upsert_feature_toggle_override(building_id, "financial_pg_writes_enabled", False, reason=REASON)
    return {"dry_run": False, "override": row}


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/east_gate_disable_premature_pg_toggles.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.building_id, args.apply)), default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
