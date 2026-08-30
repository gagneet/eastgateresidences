# @featuretrace:pg-migration — Canonical feature-toggle contract for the Mongo→Postgres cutover.
# Layer: config
# Data flow: every PG-gated router → is_cutover_feature_enabled(building_id, FLAG) → core.feature_toggles (Postgres).
# Related: backend/db_postgres/repos/config_repo.py
#           backend/routers/finance.py
#           backend/routers/settings.py
#           backend/routers/users.py
# Toggle: financial_integration_layer_v2  (umbrella — all child flags gated behind this)

"""Central cutover toggle and provider-setting resolution.

This service normalizes the finance migration's fragmented historical toggle
names into one canonical contract rooted in ``financial_integration_layer_v2``.
Every new finance cutover read/write gate should go through this module rather
than querying feature toggles or building settings directly.
"""
from __future__ import annotations

import asyncio
import logging

from db_postgres.repos import config_repo

logger = logging.getLogger(__name__)

UMBRELLA_FEATURE_KEY = "financial_integration_layer_v2"
FINANCIAL_PG_WRITES_ENABLED = "financial_pg_writes_enabled"
FINANCIAL_PG_READS_ENABLED = "financial_pg_reads_enabled"
FINANCIAL_SHADOW_READS_ENABLED = "financial_shadow_reads_enabled"
OWNER_READ_PG_ENABLED = "owner_read_pg_enabled"
BANK_INTEGRATION_ABSTRACTION_ENABLED = "bank_integration_abstraction_enabled"
TRUST_PG_LEDGER_ENABLED = "trust_pg_ledger_enabled"
TRUST_RECONCILIATION_PG_ENABLED = "trust_reconciliation_pg_enabled"
EXTERNAL_API_FINANCE_PG_ENABLED = "external_api_finance_pg_enabled"
ONBOARDING_CURRENT_BALANCE_ADAPTERS_ENABLED = "onboarding_current_balance_adapters_enabled"
GOVERNANCE_READ_PG_ENABLED = "governance_read_pg_enabled"
# P0 router migration: per-router PG read gates (Phase D of Mongo→Postgres cutover plan)
SETTINGS_PG_READS_ENABLED = "settings_pg_reads_enabled"
USERS_PG_READS_ENABLED = "users_pg_reads_enabled"
# Operator waiver: serve finance PG reads once parity is verified out-of-band, WITHOUT
# waiting out the 7-day shadow soak. It only skips the soak-duration gate in
# finance_route_cutover_service (never the critical-diff gate). Protected/off by default.
FINANCIAL_PG_READS_BYPASS_SHADOW = "financial_pg_reads_bypass_shadow"

BANKING_PROVIDER_SETTING_KEY = "banking.provider"
BANKING_MODE_SETTING_KEY = "banking.mode"

LEGACY_FEATURE_KEY_ALIASES = {
    # Historical fragmentation: these keys were used interchangeably across
    # docs and code even though they described different phases of the cutover.
    "financial_core_enabled": FINANCIAL_PG_WRITES_ENABLED,
    "financial_core.read_from_postgres": FINANCIAL_PG_WRITES_ENABLED,
    "financial_core.shadow_read_postgres": FINANCIAL_SHADOW_READS_ENABLED,
    "read_source_v2": FINANCIAL_PG_READS_ENABLED,
}

_CHILD_FEATURE_KEYS = {
    FINANCIAL_PG_WRITES_ENABLED,
    FINANCIAL_PG_READS_ENABLED,
    FINANCIAL_SHADOW_READS_ENABLED,
    OWNER_READ_PG_ENABLED,
    BANK_INTEGRATION_ABSTRACTION_ENABLED,
    TRUST_PG_LEDGER_ENABLED,
    TRUST_RECONCILIATION_PG_ENABLED,
    EXTERNAL_API_FINANCE_PG_ENABLED,
    ONBOARDING_CURRENT_BALANCE_ADAPTERS_ENABLED,
    GOVERNANCE_READ_PG_ENABLED,
    SETTINGS_PG_READS_ENABLED,
    USERS_PG_READS_ENABLED,
    FINANCIAL_PG_READS_BYPASS_SHADOW,
}

_BANK_PROVIDER_TO_PROTOCOL_PREFS = {
    "mock": {
        "bank_feed": "csv_upload_bank_feed",
        "payment_initiation": "mock_aba_writer",
    },
    "csv_replay": {
        "bank_feed": "csv_upload_bank_feed",
        "payment_initiation": "mock_aba_writer",
    },
    "basiq": {
        "bank_feed": "basiq",
        "payment_initiation": "mock_aba_writer",
    },
    "macquarie_deft": {
        "bank_feed": "macquarie_deft",
        "payment_initiation": "macquarie_deft",
    },
}


def canonical_feature_key(feature_key: str) -> str:
    """Return the canonical finance-cutover feature key."""
    return LEGACY_FEATURE_KEY_ALIASES.get(feature_key, feature_key)


async def is_cutover_feature_enabled(building_id: str, feature_key: str) -> bool:
    """Resolve a cutover feature toggle, enforcing the umbrella gate for children.

    Returns False on any Postgres failure (fail-closed: stay on Mongo when the
    toggle store is unreachable, consistent with get_cutover_status behaviour).
    """
    try:
        return await _is_cutover_feature_enabled_inner(building_id, feature_key)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "is_cutover_feature_enabled(%s, %s) failed, defaulting to False: %s",
            building_id, feature_key, exc,
            exc_info=True,
        )
        return False


async def _is_cutover_feature_enabled_inner(building_id: str, feature_key: str) -> bool:
    """Generated function header.

    Function: _is_cutover_feature_enabled_inner
    Path: backend/services/cutover_config_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    canonical_key = canonical_feature_key(feature_key)

    if canonical_key == UMBRELLA_FEATURE_KEY:
        return bool(
            await config_repo.resolve_feature_toggle(
                building_id,
                UMBRELLA_FEATURE_KEY,
                default=False,
            )
        )

    if canonical_key in _CHILD_FEATURE_KEYS:
        umbrella_enabled = bool(
            await config_repo.resolve_feature_toggle(
                building_id,
                UMBRELLA_FEATURE_KEY,
                default=False,
            )
        )
        if not umbrella_enabled:
            return False

    resolved = await config_repo.resolve_feature_toggle(
        building_id,
        canonical_key,
        default=None,
    )
    if resolved is None and canonical_key != feature_key:
        resolved = await config_repo.resolve_feature_toggle(
            building_id,
            feature_key,
            default=False,
        )

    return bool(resolved)


async def are_cutover_features_enabled(building_id: str, *feature_keys: str) -> bool:
    """Return True only when every requested feature toggle resolves enabled."""
    for feature_key in feature_keys:
        if not await is_cutover_feature_enabled(building_id, feature_key):
            return False
    return True


async def get_banking_provider(building_id: str) -> str:
    """Return the configured banking provider slug."""
    provider = await config_repo.get_building_setting(
        building_id,
        BANKING_PROVIDER_SETTING_KEY,
        default="mock",
    )
    provider_str = str(provider or "mock").strip().lower()
    return provider_str if provider_str in _BANK_PROVIDER_TO_PROTOCOL_PREFS else "mock"


async def get_banking_mode(building_id: str) -> str:
    """Return the configured banking runtime mode."""
    mode = await config_repo.get_building_setting(
        building_id,
        BANKING_MODE_SETTING_KEY,
        default="mock",
    )
    mode_str = str(mode or "mock").strip().lower()
    return mode_str if mode_str in {"mock", "sandbox", "live"} else "mock"


async def get_bank_provider_preferences(building_id: str) -> dict[str, str]:
    """Map the canonical bank provider selection to per-protocol registry names."""
    provider = await get_banking_provider(building_id)
    return dict(_BANK_PROVIDER_TO_PROTOCOL_PREFS.get(provider, _BANK_PROVIDER_TO_PROTOCOL_PREFS["mock"]))
