"""
backend/domain/reconciliation.py — Three-way trust account reconciliation checker.

# @featuretrace:financial_integration_v2 — reconciliation gate for period CLOSED transition.
# Layer: domain
# Data flow: period-close route → three_way_reconcile() → finance reconciliation state (building-scoped).
# Related: backend/domain/period_lifecycle.py
#           backend/domain/__init__.py (Cents type)

The three-way rule (from the spec §7):

  adjusted_bank == ledger_control == sub_ledger_sum   (within 0 cents)

  where:
    adjusted_bank = cleared_bank_balance
                  + deposits_in_transit
                  - unpresented_withdrawals

All amounts are integer cents. Float arithmetic is banned in this module.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReconciliationResult:
    """Immutable result of a three-way reconciliation check."""

    adjusted_bank_cents: int
    ledger_balance_cents: int
    subledger_sum_cents: int
    agrees: bool
    max_discrepancy_cents: int

    def assert_agrees(self) -> None:
        """Raise ValueError with a detailed breakdown if reconciliation fails.

        Called by the period-close routine; the router converts this to HTTP 409.
        """
        if not self.agrees:
            raise ValueError(
                f"Three-way reconciliation failed — period cannot be closed.\n"
                f"  Adjusted bank:   {self.adjusted_bank_cents:>14,} cents\n"
                f"  Ledger control:  {self.ledger_balance_cents:>14,} cents\n"
                f"  Sub-ledger sum:  {self.subledger_sum_cents:>14,} cents\n"
                f"  Max discrepancy: {self.max_discrepancy_cents:>14,} cents\n"
                "Post reconciliation items (deposits in transit, unpresented withdrawals, "
                "bank errors) to the reconciliation_items collection and retry."
            )


def three_way_reconcile(
        cleared_bank_cents: int,
        deposits_in_transit_cents: int,
        unpresented_withdrawals_cents: int,
        ledger_balance_cents: int,
        subledger_sum_cents: int,
) -> ReconciliationResult:
    """Compute and return the three-way reconciliation result.

    All arguments must be integer cents. The caller is responsible for converting
    any provider floats to cents before calling this function.

    Args:
        cleared_bank_cents: Balance confirmed cleared by the bank statement.
        deposits_in_transit_cents: Receipts recorded in ledger, not yet on statement.
        unpresented_withdrawals_cents: Payments recorded in ledger, not yet on statement.
        ledger_balance_cents: The trust ledger's own computed control balance.
        subledger_sum_cents: Sum of all per-scheme (per-building/per-fund) sub-ledger balances.

    Returns:
        ReconciliationResult with agrees=True only if all three figures match exactly.
    """
    adjusted_bank = (
            cleared_bank_cents
            + deposits_in_transit_cents
            - unpresented_withdrawals_cents
    )

    agrees = (adjusted_bank == ledger_balance_cents == subledger_sum_cents)

    max_discrepancy = max(
        abs(adjusted_bank - ledger_balance_cents),
        abs(ledger_balance_cents - subledger_sum_cents),
        abs(adjusted_bank - subledger_sum_cents),
    )

    return ReconciliationResult(
        adjusted_bank_cents=adjusted_bank,
        ledger_balance_cents=ledger_balance_cents,
        subledger_sum_cents=subledger_sum_cents,
        agrees=agrees,
        max_discrepancy_cents=max_discrepancy,
    )
