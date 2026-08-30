"""Tests for pure capital-funding calculations."""

import pytest

from domain.capital_funding import (
    FundingRequirement,
    LotFundingInput,
    allocate_cents_by_entitlement,
    funding_requirement_cents,
    model_lot_impacts,
    payment_count_for_frequency,
    simple_interest_cents,
)


def test_funding_requirement_applies_offsets():
    requirement = FundingRequirement(
        project_cost_cents=7_500_000_00,
        gst_cents=750_000_00,
        professional_fees_cents=120_000_00,
        contingency_cents=300_000_00,
        insurance_recoveries_cents=400_000_00,
        grants_cents=50_000_00,
        existing_fund_contribution_cents=500_000_00,
    )

    assert funding_requirement_cents(requirement) == 7_720_000_00


def test_allocate_cents_by_entitlement_is_deterministic_with_residual_cents():
    lots = [
        LotFundingInput(unit_number="2", entitlement=1),
        LotFundingInput(unit_number="1", entitlement=1),
        LotFundingInput(unit_number="3", entitlement=1),
    ]

    allocations = allocate_cents_by_entitlement(10, lots)

    assert sum(row.allocation_cents for row in allocations) == 10
    assert [(row.unit_number, row.allocation_cents, row.residual_rank) for row in allocations] == [
        ("2", 4, 1),
        ("1", 3, 0),
        ("3", 3, 0),
    ]


def test_allocate_cents_by_entitlement_uses_input_position_not_unit_label_for_residuals():
    lots = [
        LotFundingInput(unit_number="1", entitlement=1),
        LotFundingInput(unit_number="1", entitlement=1),
        LotFundingInput(unit_number="2", entitlement=1),
    ]

    allocations = allocate_cents_by_entitlement(10, lots)

    assert [row.allocation_cents for row in allocations] == [4, 3, 3]
    assert [row.residual_rank for row in allocations] == [1, 0, 0]


def test_model_lot_impacts_supports_upfront_and_loan_funded_owners():
    lots = [
        LotFundingInput(unit_number="A", entitlement=50, owner_choice="upfront"),
        LotFundingInput(unit_number="B", entitlement=30, owner_choice="loan"),
        LotFundingInput(unit_number="C", entitlement=20, owner_choice="loan"),
    ]

    impacts = model_lot_impacts(
        requirement_cents=100_000_00,
        lots=lots,
        annual_interest_bps=600,
        term_months=24,
        repayment_frequency="quarterly",
        establishment_fee_cents=1_000_00,
        annual_fee_cents=500_00,
    )

    upfront = next(row for row in impacts if row.unit_number == "A")
    loan_b = next(row for row in impacts if row.unit_number == "B")
    loan_c = next(row for row in impacts if row.unit_number == "C")

    assert upfront.upfront_cents == 50_000_00
    assert upfront.financed_principal_cents == 0
    assert upfront.total_cost_cents == 50_000_00
    assert loan_b.financed_principal_cents == 30_000_00
    assert loan_c.financed_principal_cents == 20_000_00
    assert loan_b.payment_count == 8
    assert loan_b.periodic_payment_cents > loan_c.periodic_payment_cents
    assert sum(row.interest_cents for row in impacts) == simple_interest_cents(50_000_00, 600, 24)
    assert sum(row.fees_cents for row in impacts) == 2_000_00


def test_invalid_frequency_raises_value_error():
    with pytest.raises(ValueError, match="unsupported frequency"):
        payment_count_for_frequency(12, "weekly")


def test_invalid_owner_choice_raises_value_error():
    with pytest.raises(ValueError, match="unsupported owner_choice"):
        model_lot_impacts(
            requirement_cents=100,
            lots=[LotFundingInput(unit_number="1", entitlement=1, owner_choice="defer")],
            annual_interest_bps=500,
            term_months=12,
            repayment_frequency="monthly",
        )


def test_zero_total_entitlement_raises_value_error():
    with pytest.raises(ValueError, match="total entitlement"):
        allocate_cents_by_entitlement(100, [LotFundingInput(unit_number="1", entitlement=0)])


def test_all_upfront_scenario_does_not_require_positive_term_months():
    # A pure special-levy scenario (every owner pays upfront) has no loan, so
    # term_months is meaningless — it must not be forced positive just to run
    # the calculator.
    lots = [
        LotFundingInput(unit_number="A", entitlement=50, owner_choice="upfront"),
        LotFundingInput(unit_number="B", entitlement=50, owner_choice="upfront"),
    ]

    impacts = model_lot_impacts(
        requirement_cents=100_000_00,
        lots=lots,
        annual_interest_bps=600,
        term_months=0,
        repayment_frequency="quarterly",
    )

    assert all(row.financed_principal_cents == 0 for row in impacts)
    assert all(row.payment_count == 0 for row in impacts)
    assert all(row.periodic_payment_cents == 0 for row in impacts)
    assert sum(row.upfront_cents for row in impacts) == 100_000_00
