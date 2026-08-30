"""Pure capital-funding calculations for special levy and strata-loan scenarios."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from domain import Cents


FundingFrequency = Literal["monthly", "quarterly", "annual"]


@dataclass(frozen=True)
class LotFundingInput:
    unit_number: str
    entitlement: int
    owner_choice: Literal["loan", "upfront"] = "loan"


@dataclass(frozen=True)
class LotAllocation:
    unit_number: str
    entitlement: int
    allocation_cents: Cents
    residual_rank: int


@dataclass(frozen=True)
class LotFundingImpact:
    unit_number: str
    entitlement: int
    owner_choice: str
    project_share_cents: Cents
    upfront_cents: Cents
    financed_principal_cents: Cents
    interest_cents: Cents
    fees_cents: Cents
    periodic_payment_cents: Cents
    payment_count: int
    total_cost_cents: Cents


@dataclass(frozen=True)
class FundingRequirement:
    project_cost_cents: Cents
    gst_cents: Cents = 0
    professional_fees_cents: Cents = 0
    contingency_cents: Cents = 0
    insurance_recoveries_cents: Cents = 0
    grants_cents: Cents = 0
    existing_fund_contribution_cents: Cents = 0


def funding_requirement_cents(requirement: FundingRequirement) -> Cents:
    gross = (
        requirement.project_cost_cents
        + requirement.gst_cents
        + requirement.professional_fees_cents
        + requirement.contingency_cents
    )
    offsets = (
        requirement.insurance_recoveries_cents
        + requirement.grants_cents
        + requirement.existing_fund_contribution_cents
    )
    return max(0, gross - offsets)


def allocate_cents_by_entitlement(total_cents: Cents, lots: Iterable[LotFundingInput]) -> list[LotAllocation]:
    lot_list = list(lots)
    if total_cents < 0:
        raise ValueError("total_cents must be non-negative")
    if not lot_list:
        return []
    denominator = sum(lot.entitlement for lot in lot_list)
    if denominator <= 0:
        raise ValueError("total entitlement must be positive")

    rows: list[tuple[int, LotFundingInput, int, int]] = []
    allocated = 0
    for index, lot in enumerate(lot_list):
        if lot.entitlement < 0:
            raise ValueError("lot entitlement must be non-negative")
        numerator = total_cents * lot.entitlement
        base = numerator // denominator
        remainder = numerator % denominator
        rows.append((index, lot, base, remainder))
        allocated += base

    residual = total_cents - allocated
    ranked = sorted(rows, key=lambda row: (-row[3], row[0]))
    residual_winners = {row[0]: idx + 1 for idx, row in enumerate(ranked[:residual])}

    allocations: list[LotAllocation] = []
    for index, lot, base, _remainder in rows:
        extra = 1 if index in residual_winners else 0
        allocations.append(
            LotAllocation(
                unit_number=lot.unit_number,
                entitlement=lot.entitlement,
                allocation_cents=base + extra,
                residual_rank=residual_winners.get(index, 0),
            )
        )
    return allocations


def _round_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if remainder * 2 >= denominator else 0)


def payment_count_for_frequency(term_months: int, frequency: FundingFrequency) -> int:
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    if frequency == "monthly":
        return term_months
    if frequency == "quarterly":
        return _round_div(term_months, 3)
    if frequency == "annual":
        return _round_div(term_months, 12)
    raise ValueError(f"unsupported frequency: {frequency}")


def simple_interest_cents(principal_cents: Cents, annual_interest_bps: int, term_months: int) -> Cents:
    if principal_cents < 0:
        raise ValueError("principal_cents must be non-negative")
    if annual_interest_bps < 0:
        raise ValueError("annual_interest_bps must be non-negative")
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    return _round_div(principal_cents * annual_interest_bps * term_months, 10000 * 12)


def model_lot_impacts(
    *,
    requirement_cents: Cents,
    lots: Iterable[LotFundingInput],
    annual_interest_bps: int,
    term_months: int,
    repayment_frequency: FundingFrequency,
    establishment_fee_cents: Cents = 0,
    annual_fee_cents: Cents = 0,
) -> list[LotFundingImpact]:
    lot_list = list(lots)
    invalid_choices = sorted({lot.owner_choice for lot in lot_list if lot.owner_choice not in {"loan", "upfront"}})
    if invalid_choices:
        raise ValueError(f"unsupported owner_choice: {', '.join(invalid_choices)}")
    allocations = allocate_cents_by_entitlement(requirement_cents, lot_list)
    choice_by_unit = {lot.unit_number: lot.owner_choice for lot in lot_list}
    financed_total = sum(a.allocation_cents for a in allocations if choice_by_unit[a.unit_number] == "loan")
    interest_total = simple_interest_cents(financed_total, annual_interest_bps, term_months) if financed_total else 0
    annual_fee_periods = (term_months + 11) // 12
    fee_total = establishment_fee_cents + (annual_fee_cents * annual_fee_periods if financed_total else 0)
    interest_allocations = allocate_cents_by_entitlement(
        interest_total,
        [lot for lot in lot_list if lot.owner_choice == "loan"],
    )
    fee_allocations = allocate_cents_by_entitlement(
        fee_total,
        [lot for lot in lot_list if lot.owner_choice == "loan"],
    )
    interest_by_unit = {row.unit_number: row.allocation_cents for row in interest_allocations}
    fees_by_unit = {row.unit_number: row.allocation_cents for row in fee_allocations}
    # Only compute a payment schedule when at least one lot is actually financing —
    # an all-upfront special-levy scenario has no loan term to validate, and
    # forcing term_months > 0 for it would reject an otherwise-valid preview.
    payment_count = payment_count_for_frequency(term_months, repayment_frequency) if financed_total else 0

    impacts: list[LotFundingImpact] = []
    for allocation in allocations:
        choice = choice_by_unit[allocation.unit_number]
        upfront = allocation.allocation_cents if choice == "upfront" else 0
        principal = allocation.allocation_cents if choice == "loan" else 0
        interest = interest_by_unit.get(allocation.unit_number, 0)
        fees = fees_by_unit.get(allocation.unit_number, 0)
        periodic = _round_div(principal + interest + fees, payment_count) if principal and payment_count > 0 else 0
        impacts.append(
            LotFundingImpact(
                unit_number=allocation.unit_number,
                entitlement=allocation.entitlement,
                owner_choice=choice,
                project_share_cents=allocation.allocation_cents,
                upfront_cents=upfront,
                financed_principal_cents=principal,
                interest_cents=interest,
                fees_cents=fees,
                periodic_payment_cents=periodic,
                payment_count=payment_count if principal else 0,
                total_cost_cents=upfront + principal + interest + fees,
            )
        )
    return impacts
