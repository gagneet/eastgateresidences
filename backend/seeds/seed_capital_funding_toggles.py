# @featuretrace:capital-funding-levy-notices — Backfill the 6 capital-funding
#   feature toggles into PostgreSQL core.feature_toggles (the AUDIT-13 drift
#   gate's source of truth).
# Layer: seed
# Data flow: seeds.feature_toggles.DEFAULT_FEATURES (canonical definitions) →
#            this script → core.feature_toggles (global) → require_feature() guards.
# Related: backend/seeds/feature_toggles.py (canonical toggle list; MongoDB-only writer)
#          backend/db_postgres/repos/config_repo.py (assert_global_enable_allowed gate)
#          backend/core/toggle_classification.py
#          scripts/audits/toggle_drift.py (AUDIT-13)
# Table: core.feature_toggles
"""Backfill the capital-funding levy-notice toggles into PostgreSQL.

Merging the capital-funding levy-notice automation branch added 6 new keys
to seeds/feature_toggles.py's DEFAULT_FEATURES (capital_funding_workspace,
capital_funding_owner_elections, capital_funding_notice_preview,
capital_funding_notice_issuance, capital_funding_ledger_posting,
hybrid_funding_structures) — but that seed script's seed_feature_toggles()
only writes MongoDB. Nothing wrote these rows into PostgreSQL
core.feature_toggles, which scripts/audits/toggle_drift.py (AUDIT-13) treats
as the source of truth, so deploy.sh's protected-toggle drift gate correctly
blocked the deploy.

All 6 default is_enabled=False in DEFAULT_FEATURES and none appear in
core/toggle_classification.py's PROTECTED_TOGGLE_KEYS (absent keys default
to the safe `visibility` class), so create_global_feature_toggle()'s
assert_global_enable_allowed() does not block them.

Run manually:
    cd backend && source venv/bin/activate && python3 seeds/seed_capital_funding_toggles.py
Idempotent: re-running skips any key that already exists in Postgres.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

logger = logging.getLogger(__name__)

TOGGLE_KEYS = [
    "capital_funding_workspace",
    "capital_funding_owner_elections",
    "capital_funding_notice_preview",
    "capital_funding_notice_issuance",
    "capital_funding_ledger_posting",
    "hybrid_funding_structures",
]


async def seed() -> None:
    from db_postgres.repos.config_repo import create_global_feature_toggle, resolve_feature_toggle
    from seeds.feature_toggles import DEFAULT_FEATURES

    seed_by_key = {feature["feature_key"]: feature for feature in DEFAULT_FEATURES}
    missing_keys = [key for key in TOGGLE_KEYS if key not in seed_by_key]
    if missing_keys:
        raise RuntimeError(
            f"TOGGLE_KEYS references keys not present in DEFAULT_FEATURES: {missing_keys}"
        )

    for key in TOGGLE_KEYS:
        toggle = seed_by_key[key]
        existing = await resolve_feature_toggle(None, key, default=None)
        if existing is not None:
            logger.info("Toggle %s already exists — skipping.", key)
            continue
        await create_global_feature_toggle(toggle, actor_email="system@strataos.internal")
        logger.info("Seeded toggle %s (is_enabled=%s)", key, toggle["is_enabled"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
