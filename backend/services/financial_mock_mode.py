# @featuretrace:financial-mock-boundary — per-building mock/live boundary for external financial integrations.
# Layer: service
# Data flow: core.feature_toggles + core.feature_toggle_overrides (per building)
#            → resolve_feature_toggle() → financial_services_mocked()/bank_direct_debit_mocked()
#            → routers/trust_phase1.py (DEFT), server.py (Stripe), integrations/registry.py,
#              routers/trust_accounting.py (ABA).
# Toggle: financial_services_mock, bank_direct_debit_mock
# Table: core.feature_toggles, core.feature_toggle_overrides
# Tests: tests/backend/test_financial_mock_mode.py
"""Which external financial integrations run against mocks, per building.

The platform deliberately talks to no live financial institution today
(``docs/architecture/transactions_accounting.md`` RULE 2: "no real external API
keys required"). That used to be expressed by a single process-wide env var,
``MOCK_EXTERNAL_SERVICES``, read in exactly one place — the DEFT webhook — which
made it impossible to connect one building to a real bank without connecting all
of them, and left every other financial integration ungoverned.

Two building-scoped toggles replace it:

``financial_services_mock``
    The umbrella. While ON, DEFT/BPAY, Stripe, the ProviderRegistry protocols and
    outbound payment execution (ABA) run against their mock implementations for
    this building.

``bank_direct_debit_mock``
    Bank direct debit and real transaction-history retrieval, held separately
    because they pull customer bank data and can debit an owner directly — a
    different risk profile from initiating a payment the OC has already approved,
    and worth promoting on its own schedule.

Both default to ON and are ``mock_boundary``-classified, so turning one OFF — the
direction that reaches real money — is the guarded action. See
``core/toggle_classification.py``.

**Demo Bank is out of scope on purpose.** It is a first-party emulator with its
own gates (``demo_bank_feed_enabled`` / ``historical_financial_reconstruction``)
and is mock by construction, so it is unaffected by either toggle. Folding it in
would give one switch two unrelated meanings, and a building that turned this
toggle off would silently lose its reconstruction staging.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

FINANCIAL_SERVICES_MOCK_KEY = "financial_services_mock"
BANK_DIRECT_DEBIT_MOCK_KEY = "bank_direct_debit_mock"

#: Process-wide kill switch. When true, everything is mocked regardless of any
#: per-building toggle — it can only ever force mock ON, never off, so it stays a
#: safe operational override (a deploy that wants to guarantee no outbound
#: financial traffic sets it and needs no database access to do so).
MOCK_EXTERNAL_SERVICES_ENV = "MOCK_EXTERNAL_SERVICES"


def global_mock_override_active() -> bool:
    """True when the env kill switch forces mock for every building."""
    return os.environ.get(MOCK_EXTERNAL_SERVICES_ENV, "false").lower() == "true"


async def _mocked(building_id: str | None, feature_key: str) -> bool:
    """Resolve one mock-boundary toggle, failing safe.

    Fail-safe means returning True (mocked) on any failure — an unresolvable
    building, an unreachable config store, a missing row. The alternative would be
    to treat "I could not tell" as permission to contact a real bank, which is the
    one outcome that must never happen by accident. This is the opposite default
    from the protected cutover toggles, where absent means "not promoted"; here
    absent means "not yet connected", and both resolve to the safe state.
    """
    if global_mock_override_active():
        return True
    if not building_id:
        logger.warning(
            "financial mock boundary: no building context for %s; defaulting to mock", feature_key
        )
        return True
    try:
        from db_postgres.repos.config_repo import (
            get_global_feature_toggle,
            resolve_feature_toggle,
        )

        resolved = await resolve_feature_toggle(str(building_id), feature_key, default=True)

        # "Mocked" is the safe answer and needs no corroboration. "Live" is the
        # dangerous one, and there is exactly one way it can be produced without
        # anybody having chosen it: resolve_feature_toggle delegates to the SQL
        # function core.feature_toggle_resolved, which returns FALSE — not NULL — for
        # a key it has never heard of, so its `default=True` is unreachable and an
        # UNSEEDED key reads as live. For every other toggle in this table FALSE is
        # the safe answer; for these two it would mean a database missing migration
        # 0095 silently ran live providers.
        #
        # So only a False is corroborated against the catalogue, which also keeps the
        # common path to a single round trip — this resolver sits in
        # ProviderRegistry._get_preference and runs on every provider lookup.
        if resolved is False and await get_global_feature_toggle(feature_key) is None:
            logger.warning(
                "financial mock boundary: '%s' is not in core.feature_toggles "
                "(migration 0095 not applied?); defaulting to mock.",
                feature_key,
            )
            return True
    except Exception:
        logger.exception(
            "financial mock boundary: could not resolve %s for building %s; defaulting to mock",
            feature_key, building_id,
        )
        return True
    return True if resolved is None else bool(resolved)


async def financial_services_mocked(building_id: str | None) -> bool:
    """True when DEFT/BPAY, Stripe, provider protocols and ABA are mocked here."""
    return await _mocked(building_id, FINANCIAL_SERVICES_MOCK_KEY)


async def bank_direct_debit_mocked(building_id: str | None) -> bool:
    """True when bank direct debit and transaction-history retrieval are mocked here.

    NOTE: no live code path consumes this yet — direct debit and real
    transaction-history retrieval are not implemented (the only occurrences of
    "direct debit" in the backend today are notification copy). The toggle, its
    classification and this resolver exist ahead of that work so the switch is in
    place, governed and visible before anything can call a bank. Wire the
    implementation to this function; do not add a second flag.
    """
    return await _mocked(building_id, BANK_DIRECT_DEBIT_MOCK_KEY)


async def assert_live_financial_call_allowed(building_id: str | None, operation: str) -> None:
    """Raise 409 when `operation` would reach a live institution under mock mode.

    Call this at the boundary of any code path that hands an instruction to a real
    financial provider. It is deliberately an exception rather than a silent
    fallback: silently doing nothing where the caller expected a payment to leave
    is worse than refusing loudly.
    """
    if await financial_services_mocked(building_id):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail=(
                f"'{operation}' would contact a live financial provider, but "
                f"'{FINANCIAL_SERVICES_MOCK_KEY}' is enabled for this building. "
                f"Disable it for this building once its readiness gates pass."
            ),
        )
