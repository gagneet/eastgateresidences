# @featuretrace:financial-onboarding — generic (building-agnostic) manifest generator for
#   financial-only onboarding: reconstructs owner levy payments from the proposed budget.
#   Data flow: annual_levies + units (Mongo) -> ReconstructedTransactionRow (credit)
#   (building-scoped). Related: expense_category_generator.py, financial_onboarding.py.
# @featuretrace:demo_bank — produces ReconstructedTransactionRow/ReconstructionManifest for
#   the existing reconstruction-batch pipeline (Mongo staging → Demo Bank → matching review).
# Layer: service
# Data flow: annual_levies + units + settings (Mongo) + finance.levy_items (Postgres, dedup) →
#            ReconstructionManifest (one combined Admin+Sinking(+GST) ReconstructedTransactionRow
#            group per lot per quarter) → reconstruction_batch_service.submit_for_review() →
#            [human approval] → integrations.demo_bank.ingestion.import_historical_reconstruction().
#            (building-scoped)
# Related: backend/services/reconstruction_batch_service.py (link_existing_rows_as_manifest — the
#          East-Gate-only retroactive-linking sibling this module does NOT replace)
#          backend/services/levy_generation_service.py (same apportionment core, different output)
#          backend/scripts/migrations/migration_027_randomize_east_gate_demo_bank_levies.py
#          docs/migration/historical_ledger_reconciliation_plan01.md
#          docs/migration/historical_ledger_reconciliation_plan02.md
# Toggle: historical_financial_reconstruction, historical_reconstruction_posting
# Collection: annual_levies, units, settings, demo_bank_accounts
# Table: core.lots, finance.levy_items, finance.levy_runs, finance.funds
"""Generic "generate from proposed budget" manifest builder.

Unlike `reconstruction_batch_service.link_existing_rows_as_manifest()` (which only reads
pre-existing `demo_bank_transactions` rows that happen to exist for East Gate), this module
GENERATES those rows for any building from its proposed Admin/Sinking budget, unit
entitlements, and GST settings — the same building-agnostic apportionment core
`levy_generation_service.py` already reuses from `migration_027_randomize_east_gate_demo_bank_levies.py`,
imported a third time here rather than re-derived.

One combined Admin+Sinking(+GST) transaction per lot per quarter
------------------------------------------------------------------
Owners make one bank payment per period, not separate admin/sinking/GST payments (see
`integrations.demo_bank.ingestion.import_historical_reconstruction()`'s own
docstring, financial-db-issues_plan04.md point 5). Rows sharing a `payment_group_id` collapse
into one Demo Bank transaction there. That grouping requires every row in a group to share the
SAME `(account_ref, unit_number, posted_date, direction)` — it does NOT require agreement on
`fund_type` (that lives only in the per-allocation-line breakdown). Concretely this means the
admin and sinking rows for one lot/quarter must NOT use their own per-fund account_ref (the
`ADMIN-{building_id}` / `SINKING-{building_id}` convention `_fund_models()` returns is only used
here to size each fund's allocation, never as the row's `account_ref`) — they must share one
common `account_ref`, `COMBINED_ACCOUNT_REF_TEMPLATE` below. Using each fund's own account_ref
would silently drop every generated group (the ingestion module logs a warning and skips
mismatched groups rather than guessing which value is authoritative).

Dedup — one mechanism, covers both onboarding scenarios
--------------------------------------------------------
`finance.levy_items.paid_cents` (already resolved by `levy_generation_service._resolve_current_levy_items()`,
keyed exactly `(financial_year, fund_type, unit_number)`) is the dedup source. Any lot/year/fund
already showing `paid_cents > 0` is skipped entirely (not partially generated — `finance.levy_items`
here is annual, not quarterly, so a precise shortfall isn't computable without inventing new
math, and full-skip is the safer choice against "no duplicates"). This single check is what makes
the generator behave correctly whether a building has SOME financial history already (imported at
onboarding, or synced from a connected trust account) or NONE at all — no separate code path for
the two scenarios, just a set of already-covered periods to skip.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import (
    LEVY_FREQUENCIES,
    QUARTERS,
    _allocate_cents,
    _due_dates,
    _due_dates_for_periods,
    _fund_models,
    _load_gst_settings,
    _load_levies,
    _load_units,
    _payment_date,
    _payment_date_for_period,
    _payment_pattern,
    _periods_for_frequency,
)
from services.levy_generation_service import _resolve_current_levy_items
from integrations.demo_bank.reconstruction_batch_schemas import (
    ReconstructedTransactionRow,
    ReconstructionBatch,
    ReconstructionManifest,
)

logger = logging.getLogger(__name__)

GENERATION_METHOD = "generate_from_budget_v1"
COMBINED_ACCOUNT_REF_TEMPLATE = "OPERATING-{building_id}"


class ReconstructionGenerationError(ValueError):
    """Raised when the proposed-budget generator cannot build a manifest.

    Deliberately a plain ValueError subclass (not importing
    reconstruction_batch_service.ReconstructionWorkflowError) so this module has no dependency
    on the workflow-orchestration module — the router catches both via a shared except clause.
    """


def combined_account_ref(building_id: str) -> str:
    return COMBINED_ACCOUNT_REF_TEMPLATE.format(building_id=building_id)


async def ensure_generation_account(building_id: str, *, is_test_data: bool = False) -> str:
    """Idempotently register the combined-payment Demo Bank account for this building.

    Called at actual generate-time (not preview/submit-review, which must stay pure dry-runs)
    so `_recompute_balance()` in `import_historical_reconstruction()` has a real
    `demo_bank_accounts` document to update rather than silently no-op'ing against a missing one.
    `ensure_account()` wants the outer TenantScopedDatabase-wrapped `db` (it reads `db._db`
    internally), not the raw motor `db._db` the rest of this module's Mongo reads use.
    """
    from database import db as wrapped_db
    from integrations.demo_bank.ingestion import ensure_account

    account_ref = combined_account_ref(building_id)
    await ensure_account(
        wrapped_db,
        building_id,
        account_ref,
        account_name="Levy Receipts (Generated)",
        account_type="transaction",
        is_test_data=is_test_data,
    )
    return account_ref


def _paid_periods(existing_items: dict[tuple[str, str, str], dict[str, Any]]) -> set[tuple[str, str, str]]:
    """(financial_year, fund_type, unit_number) keys where finance.levy_items.paid_cents > 0."""
    return {key for key, row in existing_items.items() if int(row.get("paid_cents") or 0) > 0}


async def generate_manifest_from_proposed_budget(
    *, mongo_db, pg_session, scheme_ref, building_id: str, batch: ReconstructionBatch,
) -> ReconstructionManifest:
    """Build a ReconstructionManifest by generating owner levy payments from the proposed budget.

    Pure computation — no writes. Safe to call repeatedly for both preview (dry-run) and
    submit-review (persisted by the caller). Raises ReconstructionGenerationError if there's
    nothing to generate (no units, no annual_levies for the requested range, or every
    lot/year/fund is already covered by a real paid levy item).
    """
    gap_warnings: list[str] = []
    units = await _load_units(mongo_db, building_id, warnings=gap_warnings)
    if not units:
        raise ReconstructionGenerationError(
            f"No units with positive unit-of-entitlement found for building_id={building_id!r} "
            "— cannot generate levy payments without a unit entitlement schedule."
        )

    # strict=False: a building onboarded with partial history is the expected case, not an
    # error — skip years with no annual_levies doc and surface it as a warning instead of
    # failing the whole requested range. See migration_027's _load_levies docstring.
    levies = await _load_levies(
        mongo_db, building_id, batch.financial_year_start, batch.financial_year_end, strict=False,
    )
    missing_years = [
        year for year in range(batch.financial_year_start, batch.financial_year_end + 1)
        if year not in levies
    ]
    if missing_years:
        gap_warnings.append(
            f"No annual_levies (proposed budget) found for year(s) {missing_years} — "
            "skipped, no owner-payment rows generated for those years."
        )

    gst = await _load_gst_settings(mongo_db, building_id)
    gst_multiplier = float(gst["gst_multiplier"])
    gst_rate = float(gst.get("effective_gst_rate") or 0.0)

    existing_items = await _resolve_current_levy_items(pg_session, scheme_ref.scheme_id, mongo_db, building_id)
    paid_periods = _paid_periods(existing_items)

    account_ref = combined_account_ref(building_id)
    today = datetime.now(timezone.utc).date()

    transactions: list[ReconstructedTransactionRow] = []
    total_credit_cents = 0
    skipped_unit_year_fund_count = 0

    for year in range(batch.financial_year_start, batch.financial_year_end + 1):
        if year not in levies:
            continue
        levy_doc = levies[year]
        # Building-agnostic levy frequency: a building may bill monthly, quarterly,
        # half-yearly, or annually, and the cadence can change between financial years.
        # Missing or unrecognised values preserve the legacy quarterly path; recognised
        # non-quarterly values use the period-generic helpers from migration_027.
        levy_frequency = str(levy_doc.get("levy_frequency") or "quarterly").strip().lower()
        is_quarterly = levy_frequency not in LEVY_FREQUENCIES or levy_frequency == "quarterly"
        periods = QUARTERS if is_quarterly else _periods_for_frequency(levy_frequency)
        due_dates = _due_dates(levy_doc, year) if is_quarterly else _due_dates_for_periods(year, periods)
        fund_models = _fund_models(building_id, levy_doc, gst_multiplier)
        if not fund_models:
            continue

        weighted_keys = [((unit.unit_number, period), unit.uoe) for unit in units for period in periods]

        for fund_model in fund_models:
            ex_allocation = _allocate_cents(fund_model.annual_ex_gst_cents, weighted_keys)
            inc_allocation = _allocate_cents(fund_model.annual_inc_gst_cents, weighted_keys)

            for unit in units:
                dedup_key = (str(year), fund_model.fund, unit.unit_number)
                if dedup_key in paid_periods:
                    skipped_unit_year_fund_count += 1
                    continue

                pattern = _payment_pattern(building_id, unit.unit_number, year) if is_quarterly else None
                for period_index, period in enumerate(periods, start=1):
                    alloc_key = (unit.unit_number, period)
                    inc_cents = inc_allocation[alloc_key]
                    ex_cents = ex_allocation[alloc_key]
                    if inc_cents <= 0:
                        continue
                    gst_cents = inc_cents - ex_cents

                    if is_quarterly:
                        paid_date, _lag_days = _payment_date(
                            building_id=building_id, unit_number=unit.unit_number, levy_year=year,
                            quarter=period, due_dates=due_dates, pattern=pattern, max_date=today,
                        )
                        assumption_code = pattern
                    else:
                        paid_date, _lag_days = _payment_date_for_period(
                            building_id=building_id, unit_number=unit.unit_number, levy_year=year,
                            period=period, due_dates=due_dates, max_date=today,
                        )
                        assumption_code = f"{levy_frequency}_regular"

                    transactions.append(ReconstructedTransactionRow(
                        account_ref=account_ref,
                        unit_number=unit.unit_number,
                        financial_year=str(year),
                        quarter=period_index,
                        fund_type=fund_model.fund,
                        levy_component="ordinary",
                        posted_date=paid_date,
                        amount_cents=inc_cents,
                        amount_ex_gst_cents=ex_cents,
                        gst_cents=gst_cents,
                        direction="credit",
                        assumption_code=assumption_code,
                        description=(
                            f"Estimated levy receipt: {period} {year} {fund_model.label} - "
                            f"Unit {unit.unit_number}; NOT a confirmed payment — generated from the "
                            f"proposed levy assuming full on-time payment; annual proposed "
                            f"{fund_model.label} ${fund_model.annual_ex_gst_cents / 100:.2f} ex-GST "
                            f"({fund_model.basis}) + {gst_rate:.0%} GST = "
                            f"${fund_model.annual_inc_gst_cents / 100:.2f}; allocated by UOE across "
                            f"{len(units)} units and {len(periods)} {levy_frequency} period(s)"
                        ),
                        transaction_sequence=1,
                        payment_group_id=f"{building_id}:{year}:{period}:{unit.unit_number}",
                    ))
                    total_credit_cents += inc_cents

    warnings: list[str] = list(gap_warnings)
    if skipped_unit_year_fund_count:
        warnings.append(
            f"Skipped {skipped_unit_year_fund_count} lot/year/fund combination(s) because "
            "finance.levy_items already shows a real payment (paid_cents > 0) for that period — "
            "no synthetic duplicate was generated. Requires manual reconciliation if a shortfall "
            "remains."
        )

    if not transactions:
        raise ReconstructionGenerationError(
            f"Nothing to generate for building_id={building_id!r} years "
            f"{batch.financial_year_start}-{batch.financial_year_end}: the proposed budget "
            "resolves to zero across all funds, or every lot/year/fund combination is already "
            "covered by a real paid levy item."
        )

    input_hashes = sorted(
        f"{t.unit_number}:{t.financial_year}:{t.quarter}:{t.fund_type}:{t.amount_cents}"
        for t in transactions
    )
    input_fact_hash = hashlib.sha256("|".join(input_hashes).encode()).hexdigest()
    manifest_hash = hashlib.sha256(
        f"{building_id}:{GENERATION_METHOD}:{len(transactions)}:{total_credit_cents}:{input_fact_hash}".encode()
    ).hexdigest()

    return ReconstructionManifest(
        manifest_id=str(uuid4()),
        batch_id=batch.batch_id,
        building_id=building_id,
        version=1,
        input_document_hashes=[],
        input_fact_hash=input_fact_hash,
        calculation_configuration={
            "generation_method": GENERATION_METHOD,
            "account_ref": account_ref,
            "skipped_paid_unit_year_fund_count": skipped_unit_year_fund_count,
        },
        generator_version=GENERATION_METHOD,
        expected_transaction_count=len(transactions),
        expected_credit_cents=total_credit_cents,
        expected_debit_cents=0,
        transactions=transactions,
        warnings=warnings,
        manifest_hash=manifest_hash,
        generated_at=datetime.now(timezone.utc),
        generated_by=batch.created_by,
        is_test_data=batch.is_test_data,
    )
