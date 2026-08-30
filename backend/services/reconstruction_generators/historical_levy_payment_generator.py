# Onboarding Financial Reconstruction — Workstream B: derived payment (receipt) generator.
#
# Data flow: LevyChargePlan (from historical_levy_charge_generator) -> LevyPaymentPlan.
#   The charge generator produces the receivable OBLIGATIONS (finance.levy_items). This module
#   produces the matching derived RECEIPTS ("paid == charged, on time" — canonical spec §0), one
#   per posted charge line, at EXACTLY that charge line's total_cents (GST-inclusive). Because the
#   amount is copied from the charge line rather than independently re-derived, paid == charged by
#   construction — this is the fix for the ~10% GST-mismatch class of bug (spec §4a #4), which only
#   ever entered via bespoke paid-side backfill scripts that re-derived the amount on their own.
#
# Scope of THIS module (Workstream B, increment 1): build + preview ONLY. It deliberately never
# writes finance.receipts / demo_bank_transactions — mirroring the charge generator's own
# "produce a plan, apply separately" discipline. The apply/posting step (increment 2) goes through
# the canonical record_payment() path and, per spec §4a #10, only after the target building's
# non-canonical paid residue is reversed (BUILD is separate from APPLY, to avoid double-posting).
#
# Table: (reads nothing directly; consumes a LevyChargePlan) — apply step will write finance.receipts
# @featuretrace layer=service feature=onboarding-financial-reconstruction

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:  # avoid a runtime dependency on the charge generator's DB stack (sqlalchemy etc.)
    # This module only READS duck-typed attributes off the passed-in plan; it never constructs a
    # LevyChargeLine, so the charge generator is a type-only dependency.
    from services.reconstruction_generators.historical_levy_charge_generator import (
        LevyChargeLine,
        LevyChargePlan,
    )

# The derived receipt is dated a few days BEFORE the charge's due date — the "paid on time"
# assumption from spec §0 / step 3 ("a date ... before that period's due day"). Clamped to never
# precede the charge's own issue_date.
DEFAULT_PAYMENT_LEAD_DAYS = 3

# Provenance tag carried on every derived receipt. Never "bank_reconciled" — these are assumptions,
# not evidence, until a real Bank/Trust feed supersedes them (spec §0, §4a #6).
RECONSTRUCTED_SOURCE_TAG = "reconstructed_historical"
PAYMENT_DERIVATION_TYPE = "document_derived_levy_payment"


class LevyPaymentGenerationError(ValueError):
    """Raised when a payment plan cannot be built. Plain ValueError subclass, matching the charge
    generator's convention, so this module has no dependency on the reconstruction-batch workflow."""


def payment_idempotency_key(
    building_id: str, year: int, fund_type: str, levy_type: str, period: str, lot_id: UUID
) -> str:
    """Deterministic key for one derived receipt. Distinct namespace ("payment") from the charge
    generator's key so a receipt and its charge never collide, but 1:1 with the charge otherwise."""
    return f"payment:{building_id}:{year}:{fund_type}:{levy_type}:{period}:{lot_id}"


def receipt_dedup_key(lot_id: UUID, year: int, fund_type: str, quarter_no: int) -> tuple:
    """The (lot, year, fund, period-index) tuple the apply step uses to detect an existing receipt.
    Passed in via `existing_receipt_keys` so this module stays pure/DB-free and unit-testable; the
    async apply wrapper (increment 2) populates it from finance.receipts."""
    return (lot_id, year, fund_type, quarter_no)


@dataclass(frozen=True)
class LevyPaymentLine:
    """One derived receipt — the paid side mirror of a LevyChargeLine. amount_cents == the charge's
    total_cents (GST-inclusive) by construction."""
    unit_number: str
    lot_id: UUID
    owner_party_id: UUID
    year: int
    fund_type: str  # "admin" | "sinking"
    levy_type: str
    period: str  # "Q1".."Q4" / "H1"/"H2" / "M1".."M12" / "Y1"
    quarter_no: int
    payment_date: date
    amount_cents: int  # == source charge line total_cents (integer cents; never a float)
    source_tag: str
    derivation_type: str
    idempotency_key: str


@dataclass
class LevyPaymentPlan:
    building_id: str
    from_year: int
    to_year: int
    as_of_date: date
    lines: list[LevyPaymentLine] = field(default_factory=list)
    skipped_existing_receipt: int = 0
    total_amount_cents: int = 0
    warnings: list[str] = field(default_factory=list)


def _payment_date_for(line: LevyChargeLine, lead_days: int) -> date:
    """A few days before due_date, never before the charge's issue_date."""
    candidate = line.due_date - timedelta(days=lead_days)
    return candidate if candidate >= line.issue_date else line.issue_date


def build_payment_plan_from_charge_plan(
    charge_plan: LevyChargePlan,
    *,
    payment_lead_days: int = DEFAULT_PAYMENT_LEAD_DAYS,
    existing_receipt_keys: frozenset[tuple] = frozenset(),
) -> LevyPaymentPlan:
    """Emit one derived receipt per POSTABLE charge line (charge_plan.lines — NOT planned_not_posted,
    which are future/not-yet-due periods we don't fabricate a payment for), at that line's exact
    total_cents. `existing_receipt_keys` (spec §4a #10) causes any (lot, year, fund, period) that
    already has a receipt to be skipped, so re-running or applying to a building with real payments
    never double-posts the paid side.

    Pure and synchronous — no DB, no writes — so it is fully unit-testable and safe to preview."""
    if payment_lead_days < 0:
        raise LevyPaymentGenerationError("payment_lead_days must be >= 0")

    plan = LevyPaymentPlan(
        building_id=charge_plan.building_id,
        from_year=charge_plan.from_year,
        to_year=charge_plan.to_year,
        as_of_date=charge_plan.as_of_date,
    )

    for line in charge_plan.lines:
        if line.total_cents <= 0:
            continue
        key = receipt_dedup_key(line.lot_id, line.year, line.fund_type, line.quarter_no)
        if key in existing_receipt_keys:
            plan.skipped_existing_receipt += 1
            continue
        plan.lines.append(
            LevyPaymentLine(
                unit_number=line.unit_number,
                lot_id=line.lot_id,
                owner_party_id=line.owner_party_id,
                year=line.year,
                fund_type=line.fund_type,
                levy_type=line.levy_type,
                period=line.period,
                quarter_no=line.quarter_no,
                payment_date=_payment_date_for(line, payment_lead_days),
                amount_cents=line.total_cents,  # paid == charged, by construction
                source_tag=RECONSTRUCTED_SOURCE_TAG,
                derivation_type=PAYMENT_DERIVATION_TYPE,
                idempotency_key=payment_idempotency_key(
                    charge_plan.building_id, line.year, line.fund_type,
                    line.levy_type, line.period, line.lot_id,
                ),
            )
        )
        plan.total_amount_cents += line.total_cents

    return plan


def summarize_by_year_fund_period(plan: LevyPaymentPlan) -> list[dict]:
    """Group receipts by (year, fund_type, period) with count + total cents — the preview/dry-run
    surface (mirror of the charge generator's summarize_by_year_fund_period)."""
    groups: dict[tuple, dict] = {}
    for line in plan.lines:
        gk = (line.year, line.fund_type, line.period)
        g = groups.setdefault(
            gk, {"year": line.year, "fund_type": line.fund_type, "period": line.period,
                 "receipt_count": 0, "total_cents": 0}
        )
        g["receipt_count"] += 1
        g["total_cents"] += line.amount_cents
    return sorted(groups.values(), key=lambda g: (g["year"], g["fund_type"], g["period"]))
