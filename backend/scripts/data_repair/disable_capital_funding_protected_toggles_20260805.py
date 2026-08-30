"""
Disable capital-funding protected toggles left globally enabled (2026-08-05)
===============================================================================
`eb955f21` correctly reclassified `capital_funding_ledger_posting` and
`capital_funding_notice_issuance` as `ToggleClass.FINANCE_WRITE` (protected) and
reverted the SEED file to `is_enabled: False` -- but the live `core.feature_toggles`
row for both was never touched, so both are STILL globally enabled in production
right now (they were flipped on before the reclassification landed). Confirmed via
`scripts/audits/toggle_drift.py` after fixing a separate KeyError in
`_protected_metadata_violations()` (missing `PROTECTED_TOGGLE_SAFETY_METADATA`
entries for these two keys, added alongside this script).

This is the fail-safe direction -- disabling a protected toggle never needs the
`_allow_protected_global_enable` escape hatch; `assert_global_enable_allowed()`
only blocks `is_enabled=True`. Uses the sanctioned `update_global_feature_toggle()`
repo function, never raw SQL.

Usage (from backend/):
    venv/bin/python3 scripts/data_repair/disable_capital_funding_protected_toggles_20260805.py             # dry-run
    venv/bin/python3 scripts/data_repair/disable_capital_funding_protected_toggles_20260805.py --apply      # apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

KEYS = ("capital_funding_ledger_posting", "capital_funding_notice_issuance")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", default=False)
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    from db_postgres.repos.config_repo import get_global_feature_toggle, update_global_feature_toggle

    for key in KEYS:
        current = await get_global_feature_toggle(key)
        if current is None:
            print(f"{key}: NOT FOUND -- skipping")
            continue
        print(f"{key}: currently is_enabled={current['is_enabled']}")
        if not current["is_enabled"]:
            print(f"  already False, nothing to do")
            continue
        if not args.apply:
            print(f"  DRY-RUN: would set is_enabled=False")
            continue
        result = await update_global_feature_toggle(key, {"is_enabled": False}, actor_email="manager@eastgate.com")
        print(f"  -> is_enabled={result['is_enabled']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
