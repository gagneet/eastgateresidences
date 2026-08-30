# @featuretrace:pg-migration-shadow — Trust ledger + reconciliation shadow-read comparators.
# Layer: service
# Data flow: routers/trust_accounting.py & routers/trust_reconciliation.py → compare_trust_*() →
#            shadow_read_service.run_shadow_compare() → core.shadow_diffs (building-scoped).
#            Mongo remains the response source at all times, for both trust_ledger and
#            trust_reconciliation domains independently.
# Related: backend/services/shadow_read_service.py
#          backend/services/trust_read_service.py
#          backend/routers/trust_accounting.py
#          backend/routers/trust_reconciliation.py
#          docs/migration/shadow-correction-updates-plan-p1.md (Phase G)
"""Trust ledger + trust reconciliation shadow-read comparators — Phase G of the rollout.

Two independent domains (trust_ledger, trust_reconciliation) despite living in one file —
this module never conflates them; every function takes an explicit domain-appropriate
route_key and dispatches through the generic engine with the correct canonical domain.

Per this repo's standing invariant (CLAUDE.md "Ledger Money Precision"), all comparisons
use integer cents, converting Mongo's dollar-float fields at the boundary here — never
comparing formatted currency strings. No PII redaction is needed for these two domains
(account codes, balances, counts — no owner names/emails appear in trust ledger data).
"""
from __future__ import annotations

import logging
from typing import Any

from services.shadow_read_service import (
    FieldDiff,
    ShadowCompareResult,
    compare_count_fields,
    compare_money_fields,
    run_shadow_compare,
)

logger = logging.getLogger(__name__)

_TRUST_LEDGER_DOMAIN = "trust_ledger"
_TRUST_RECONCILIATION_DOMAIN = "trust_reconciliation"


def _compare_trial_balance_payloads(
        *, mongo_payload: dict[str, Any], pg_payload: dict[str, Any], tolerance_cents: int = 0,
) -> list[FieldDiff]:
    """Generated function header.

    Function: _compare_trial_balance_payloads
    Path: backend/services/trust_shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    diffs: list[FieldDiff] = []

    _d = compare_money_fields(
        field_path="total_debits",
        mongo_aud=mongo_payload.get("total_debits"),
        pg_cents=_aud_to_cents(pg_payload.get("total_debits")),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    _d = compare_money_fields(
        field_path="total_credits",
        mongo_aud=mongo_payload.get("total_credits"),
        pg_cents=_aud_to_cents(pg_payload.get("total_credits")),
        tolerance_cents=tolerance_cents,
    )
    if _d:
        diffs.append(_d)

    _d = compare_count_fields(
        field_path="account_count",
        mongo_count=len(mongo_payload.get("accounts") or []),
        pg_count=len(pg_payload.get("accounts") or []),
    )
    if _d:
        diffs.append(_d)

    return diffs


def _compare_reconciliation_summary_payloads(
        *, mongo_payload: dict[str, Any], pg_payload: dict[str, Any], tolerance_cents: int = 0,
) -> list[FieldDiff]:
    """Generated function header.

    Function: _compare_reconciliation_summary_payloads
    Path: backend/services/trust_shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    diffs: list[FieldDiff] = []

    _d = compare_count_fields(
        field_path="matched_count",
        mongo_count=mongo_payload.get("matched_count"),
        pg_count=pg_payload.get("matched_count"),
    )
    if _d:
        diffs.append(_d)

    _d = compare_count_fields(
        field_path="unmatched_bank_count",
        mongo_count=mongo_payload.get("unmatched_bank_count"),
        pg_count=pg_payload.get("unmatched_bank_count"),
    )
    if _d:
        diffs.append(_d)

    # Both sides already store this in cents (unlike trial balance's dollar-float Mongo
    # shape) — compare directly, no boundary conversion needed here.
    mongo_unreconciled = int(mongo_payload.get("total_unreconciled_amount_cents") or 0)
    pg_unreconciled = int(pg_payload.get("total_unreconciled_amount_cents") or 0)
    diff_abs = abs(mongo_unreconciled - pg_unreconciled)
    if diff_abs > tolerance_cents:
        diffs.append(FieldDiff(
            field_path="total_unreconciled_amount_cents",
            mongo_value=mongo_unreconciled, pg_value=pg_unreconciled,
            tolerance_cents=tolerance_cents,
            severity="critical" if diff_abs > 100 else "warn",
            diff_cents=mongo_unreconciled - pg_unreconciled,
        ))

    return diffs


def _compare_v2_account_summary_payloads(
        *, mongo_payload: dict[str, Any], pg_payload: dict[str, Any], tolerance_cents: int = 0,
) -> list[FieldDiff]:
    """Generated function header.

    Function: _compare_v2_account_summary_payloads
    Path: backend/services/trust_shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    diffs: list[FieldDiff] = []
    mongo_accounts = mongo_payload.get("accounts") or []
    pg_accounts = pg_payload.get("accounts") or []

    _d = compare_count_fields(
        field_path="account_count",
        mongo_count=len(mongo_accounts),
        pg_count=len(pg_accounts),
    )
    if _d:
        diffs.append(_d)

    mongo_balance_cents = sum(int(acc.get("computed_balance_cents") or 0) for acc in mongo_accounts)
    pg_balance_cents = sum(_aud_to_cents(acc.get("current_balance")) for acc in pg_accounts)
    diff_abs = abs(mongo_balance_cents - pg_balance_cents)
    if diff_abs > tolerance_cents:
        diffs.append(FieldDiff(
            field_path="total_current_balance_cents",
            mongo_value=mongo_balance_cents,
            pg_value=pg_balance_cents,
            tolerance_cents=tolerance_cents,
            severity="critical" if diff_abs > 100 else "warn",
            diff_cents=mongo_balance_cents - pg_balance_cents,
        ))

    return diffs


def _aud_to_cents(value: float | int | None) -> int:
    """Generated function header.

    Function: _aud_to_cents
    Path: backend/services/trust_shadow_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return int(round((value or 0) * 100))


async def compare_trust_trial_balance(
        *,
        building_id: str,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any] | None,
        is_test_data: bool = False,
) -> ShadowCompareResult:
    """Compare GET /trust/trial-balance's Mongo vs Postgres computation. Never raises."""
    return await run_shadow_compare(
        building_id=building_id,
        domain=_TRUST_LEDGER_DOMAIN,
        route_key="trust.summary",
        mongo_payload=mongo_payload,
        pg_payload=pg_payload,
        comparator=_compare_trial_balance_payloads,
        is_test_data=is_test_data,
    )


async def compare_trust_reconciliation_summary(
        *,
        building_id: str,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any] | None,
        is_test_data: bool = False,
) -> ShadowCompareResult:
    """Compare GET /trust/reconciliation/runs/{id}/summary's Mongo vs Postgres computation.
    Never raises."""
    return await run_shadow_compare(
        building_id=building_id,
        domain=_TRUST_RECONCILIATION_DOMAIN,
        route_key="trust_reconciliation.summary",
        mongo_payload=mongo_payload,
        pg_payload=pg_payload,
        comparator=_compare_reconciliation_summary_payloads,
        is_test_data=is_test_data,
    )


async def compare_trust_v2_account_summary(
        *,
        building_id: str,
        mongo_payload: dict[str, Any],
        pg_payload: dict[str, Any] | None,
        is_test_data: bool = False,
) -> ShadowCompareResult:
    """Compare live V2 trust account totals against the Postgres trust account read model."""
    return await run_shadow_compare(
        building_id=building_id,
        domain=_TRUST_LEDGER_DOMAIN,
        route_key="trust_v2.accounts.summary",
        mongo_payload=mongo_payload,
        pg_payload=pg_payload,
        comparator=_compare_v2_account_summary_payloads,
        is_test_data=is_test_data,
    )
