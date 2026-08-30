#!/usr/bin/env python3
# @featuretrace:cutover-control-plane — Sets explicit East Gate finance ledger source mode.
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.cutover_status_service import record_financial_foundation_readiness  # noqa: E402
from models.cutover_status import ReadinessStatus  # noqa: E402

NOTES = (
    "East Gate finance - Strata Web portal scrape source. PG shadow only after "
    "genesis onboarding and carry-forward journals complete."
)


async def run(building_id: str, apply: bool) -> dict[str, object]:
    """Generated function header.

    Function: run
    Path: backend/scripts/east_gate_set_finance_source_mode.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    summary = {"source_mode": "mongo_primary", "source_system": "strata_web_portal_scrape", "explicit_state": True}
    if not apply:
        return {"dry_run": True, "would_set": {"building_id": building_id, "domain": "finance_ledger", "mode": "mongo_primary", "notes": NOTES}}
    status = await record_financial_foundation_readiness(
        building_id=building_id,
        validation_passed=False,
        summary=summary,
        reason=NOTES,
        actor_role="system",
        is_test_data=False,
        readiness_status=ReadinessStatus.not_started,
    )
    return {"dry_run": False, "status": status.model_dump() if hasattr(status, "model_dump") else status.__dict__}


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/east_gate_set_finance_source_mode.py

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
