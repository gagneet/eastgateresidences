# @featuretrace:demo_bank — Seed the 3 feature toggles gating the historical reconstruction pipeline.
# Layer: seed
# Data flow: this script → core.feature_toggles (global, Postgres canonical) →
#            utils.permissions.require_feature() guards in routers/financial_onboarding.py,
#            routers/onboarding.py.
# Related: backend/db_postgres/repos/config_repo.py
#          backend/core/toggle_classification.py
#          docs/migration/historical_ledger_reconciliation_plan02.md (amendment 13)
# Table: core.feature_toggles
"""Seed the historical-reconstruction feature toggles.

All three default to is_enabled=False — the pipeline is built but not
live-enabled by default, matching this repo's cautious-rollout convention for
new capability (see CLAUDE.md Step 0 Feature Completion Gate). None of these
keys are cutover_sensitive/data_source_primary/shadow_read in
core/toggle_classification.py (absent keys default to `visibility`, the safest
class), so create_global_feature_toggle()'s assert_global_enable_allowed()
does not block them even if later flipped to True via the admin UI.

Run manually:
    cd backend && source venv/bin/activate && python3 seeds/seed_historical_reconstruction_toggles.py
Idempotent: re-running skips any key that already exists.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Matches the convention every other backend/seeds/*.py script uses (e.g.
# role_permissions.py, migrate_strata_sync_to_financial.py) — db_postgres,
# core, etc. are top-level packages under backend/, not under seeds/, so
# backend/ must be added to sys.path explicitly when this file is run
# directly (`python3 seeds/seed_historical_reconstruction_toggles.py`),
# which only puts seeds/ itself on sys.path[0].
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

logger = logging.getLogger(__name__)

TOGGLES = [
    {
        "feature_key": "historical_financial_reconstruction",
        "feature_name": "Historical Financial Reconstruction",
        "description": (
            "Enables the reconstruction-batch workflow that turns AGM/audited "
            "budget facts into a reviewable Demo Bank transaction manifest "
            "(create/extract/preview/submit-review + GST-aware levy regeneration "
            "dry-run planning). Read/plan-only capability — does not itself post "
            "anything to the ledger."
        ),
        "category": "finance",
        "is_enabled": False,
        "icon": "History",
        "routes": ["/financials/onboarding"],
        "allowed_roles": ["super_admin", "strata_admin", "strata_manager"],
        "depends_on": [],
    },
    {
        "feature_key": "historical_reconstruction_posting",
        "feature_name": "Historical Reconstruction Posting",
        "description": (
            "Enables the write-side of the reconstruction pipeline: approve, "
            "generate, sync, reverse a reconstruction batch, and apply a levy-item "
            "GST regeneration. Gated separately from "
            "historical_financial_reconstruction because this class of action "
            "mutates finance.levy_items / demo_bank_transactions and must remain "
            "off until a building's dry-run report has been reviewed."
        ),
        "category": "finance",
        "is_enabled": False,
        "icon": "History",
        "routes": ["/financials/onboarding"],
        "allowed_roles": ["super_admin", "strata_admin"],
        "depends_on": ["historical_financial_reconstruction"],
    },
    {
        "feature_key": "historical_expense_reconstruction",
        "feature_name": "Historical Expense Reconstruction",
        "description": (
            "Enables import-historical-expenses (CSV) and the expense-side "
            "ledger posting path (FinancialCoreService.record_expense with "
            "derivation_level tiers exact/source_derived/aggregate_balancing)."
        ),
        "category": "finance",
        "is_enabled": False,
        "icon": "Receipt",
        "routes": ["/financials/onboarding"],
        "allowed_roles": ["super_admin", "strata_admin", "strata_manager"],
        "depends_on": [],
    },
]


async def seed() -> None:
    from db_postgres.repos.config_repo import create_global_feature_toggle, resolve_feature_toggle

    for toggle in TOGGLES:
        existing = await resolve_feature_toggle(None, toggle["feature_key"], default=None)
        if existing is not None:
            logger.info("Toggle %s already exists — skipping.", toggle["feature_key"])
            continue
        await create_global_feature_toggle(toggle, actor_email="system@strataos.internal")
        logger.info("Seeded toggle %s (is_enabled=%s)", toggle["feature_key"], toggle["is_enabled"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
