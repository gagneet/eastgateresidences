"""backend/services/reconstruction_generators/bounded_portal_reconciliation_engine.py

# @featuretrace:financial-onboarding — Workstream C: bounded, interest-aware per-unit
#   portal_reconciliation adjustment classification (GAP-ONBOARD-004 A2).
# Layer: service
# Data flow: PG per-unit true balance (services/finance_metrics/lot_true_balance.py) +
#            Mongo portal snapshot (services/portal_ledger_reconciliation_service.py::
#            _latest_portal_by_unit) + PG one-period-levy bound ->
#            classify_bounded_adjustment() -> BoundedAdjustmentDecision -> (NOT built here)
#            a posting orchestrator that calls FinancialCoreService.record_adjustment() /
#            record_historical_payment()+allocate_payment() for units the classifier approves.
# Related: scripts/data_repair/gap_fin_046_portal_evidenced_adjustment_20260806.py (the
#              existing, unbounded, East-Gate/2026-hardcoded precedent for the debit/
#              over-recorded direction this module generalises)
#          services/finance_metrics/lot_true_balance.py (A1 — the PG-side figure this reads)
#          docs/finances/onboarding-reconstruction-workflow-canonical-spec.md §4b Stage 2 (C)
#          tasks/GAP-FIN-047-interest-charged-on-credit-units.md (interest-awareness)
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (A2)
# Toggle: none (pure classification; no toggle gates a read-only decision function)

Why this exists
----------------
Two real, working, human-approved posting mechanisms for a portal-evidenced correction already
exist in this codebase:

- **Debit direction** (portal implies MORE owed than PG shows -> PG overstated `paid_cents`):
  `FinancialCoreService.record_adjustment()` (`RecordAdjustmentCommand`), used live for East Gate
  2026-08-06 by `scripts/data_repair/gap_fin_046_portal_evidenced_adjustment_20260806.py`.
- **Credit direction** (portal implies LESS owed / more credit than PG shows -> a real payment PG
  never captured): a new `finance.receipts` row via `record_historical_payment()` +
  `allocate_payment()`, tagged `transaction_origin=portal_confirmed_manual_entry` /
  `portal_scrape_reconciliation_delta` — live-verified for 15 East Gate units in this session's
  own A1 evidence pack (`docs/finances/GAP-FIN-036-A1-reconciliation-evidence-pack-2026-08-10.md`).

Both existing callers apply to **every** discovered gap, with no bound and no explicit
interest-netting statement — exactly the two guardrails canonical-spec §4a finding #2 requires
before this becomes a generic, reusable, any-building engine:

  (a) **Bounded** — a residual larger than ~one period's levy is a signal of a remaining
      systematic bug, not real payment behaviour: halt and flag it, never auto-post.
  (b) **Interest-aware** — the portal balance includes interest; the comparison basis must not
      mislabel interest as a `portal_reconciliation` principal residual.

This module is the classification step ONLY — it decides, per unit, whether a residual is
`reconciled` (within tolerance, nothing to do), `post_debit_adjustment` /
`post_credit_receipt` (within the bound — safe to post via the two existing mechanisms above),
or `halt_flag` (exceeds the bound — a human must investigate, never auto-posted). It posts
nothing itself; wiring its `post_*` decisions to the two existing FinancialCoreService calls is a
separate, explicitly scoped follow-up (see the module's own docstring note at the bottom).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

_TOLERANCE_CENTS = 100  # $1 — matches every other reconciliation tolerance in this codebase
_DEFAULT_BOUND_PERIODS = 1.0  # "~one period's levy" per canonical-spec finding #2

AdjustmentDecisionKind = Literal[
    "reconciled", "post_debit_adjustment", "post_credit_receipt", "halt_flag", "no_portal_data",
]


@dataclass(frozen=True)
class BoundedAdjustmentDecision:
    unit_number: str
    pg_true_balance_cents: Optional[int]  # signed: >0 arrears, <0 credit (A1's true_balance_cents)
    portal_balance_cents: Optional[int]  # signed, same convention, interest-inclusive per the portal
    period_bound_cents: Optional[int]  # "one period's levy" for this unit -- the halt threshold
    gap_cents: Optional[int]  # portal - pg, positive = PG understates arrears (debit direction)
    decision: AdjustmentDecisionKind
    reason: str

    @property
    def is_postable(self) -> bool:
        """True only for the two within-bound decisions a poster may act on."""
        return self.decision in ("post_debit_adjustment", "post_credit_receipt")


def classify_bounded_adjustment(
    *,
    unit_number: str,
    pg_true_balance_cents: Optional[int],
    portal_balance_cents: Optional[int],
    period_bound_cents: Optional[int],
    bound_periods: float = _DEFAULT_BOUND_PERIODS,
    tolerance_cents: int = _TOLERANCE_CENTS,
) -> BoundedAdjustmentDecision:
    """Classify ONE unit's portal-vs-ledger residual. Pure function, no I/O, no side effects.

    Sign convention (matches ``LotTrueBalance.true_balance_cents`` and the portal's own
    per-unit net figure): positive = arrears owed, negative = credit held, zero = square.

    Interest-awareness (canonical-spec finding #2(b)): both ``pg_true_balance_cents`` (derived
    from ``finance.levy_items`` + ``finance.receipts``, which already include any posted
    ``interest_cents`` component per CLAUDE.md rule 10's arrears formula) and
    ``portal_balance_cents`` (the portal's own net figure, which the codebase's own
    documentation confirms includes accrued interest -- see GAP-FIN-047's Lot 50 example,
    ``$918.84 = $902.77 principal + $16.07 interest``) are ALREADY interest-inclusive on both
    sides of the comparison -- so ``gap_cents`` is not mislabelling interest as a principal
    residual; it is a like-for-like comparison of two already-interest-inclusive figures. This
    function does not separately subtract an interest component because doing so would
    introduce an artificial mismatch, not remove one.

    Missing data (``pg_true_balance_cents`` or ``portal_balance_cents`` is ``None``) always
    yields ``no_portal_data`` -- missing != zero, never treated as "reconciled" or auto-posted.
    """
    if pg_true_balance_cents is None or portal_balance_cents is None:
        return BoundedAdjustmentDecision(
            unit_number=unit_number, pg_true_balance_cents=pg_true_balance_cents,
            portal_balance_cents=portal_balance_cents, period_bound_cents=period_bound_cents,
            gap_cents=None, decision="no_portal_data",
            reason="Missing PG true-balance or portal snapshot data for this unit -- unknown, not zero.",
        )

    gap_cents = portal_balance_cents - pg_true_balance_cents

    if abs(gap_cents) <= tolerance_cents:
        return BoundedAdjustmentDecision(
            unit_number=unit_number, pg_true_balance_cents=pg_true_balance_cents,
            portal_balance_cents=portal_balance_cents, period_bound_cents=period_bound_cents,
            gap_cents=gap_cents, decision="reconciled",
            reason=f"Within ${tolerance_cents / 100:.2f} tolerance -- no adjustment needed.",
        )

    if period_bound_cents is None or period_bound_cents <= 0:
        return BoundedAdjustmentDecision(
            unit_number=unit_number, pg_true_balance_cents=pg_true_balance_cents,
            portal_balance_cents=portal_balance_cents, period_bound_cents=period_bound_cents,
            gap_cents=gap_cents, decision="halt_flag",
            reason="No usable one-period-levy bound for this unit -- cannot safely bound the "
            "residual, so it is flagged rather than assumed safe.",
        )

    bound_cents = int(round(period_bound_cents * bound_periods))
    if abs(gap_cents) > bound_cents:
        return BoundedAdjustmentDecision(
            unit_number=unit_number, pg_true_balance_cents=pg_true_balance_cents,
            portal_balance_cents=portal_balance_cents, period_bound_cents=period_bound_cents,
            gap_cents=gap_cents, decision="halt_flag",
            reason=(
                f"${abs(gap_cents) / 100:,.2f} residual exceeds the {bound_periods:g}x "
                f"one-period-levy bound (${bound_cents / 100:,.2f}) -- probable systematic bug, "
                "not real payment behaviour. Halted, not auto-posted; needs human investigation."
            ),
        )

    if gap_cents > 0:
        # Portal implies MORE owed than PG shows -> PG's paid_cents is overstated ->
        # RecordAdjustmentCommand decrements it (the existing debit-direction mechanism).
        return BoundedAdjustmentDecision(
            unit_number=unit_number, pg_true_balance_cents=pg_true_balance_cents,
            portal_balance_cents=portal_balance_cents, period_bound_cents=period_bound_cents,
            gap_cents=gap_cents, decision="post_debit_adjustment",
            reason=(
                f"${gap_cents / 100:,.2f} within bound -- PG understates this unit's arrears "
                "(paid_cents overstated); post via record_adjustment() to decrement it."
            ),
        )

    # gap_cents < 0: portal implies LESS owed / more credit than PG shows -> a real payment PG
    # never captured -> post a new tagged receipt (the existing credit-direction mechanism).
    return BoundedAdjustmentDecision(
        unit_number=unit_number, pg_true_balance_cents=pg_true_balance_cents,
        portal_balance_cents=portal_balance_cents, period_bound_cents=period_bound_cents,
        gap_cents=gap_cents, decision="post_credit_receipt",
        reason=(
            f"${abs(gap_cents) / 100:,.2f} within bound -- PG is missing a real payment the "
            "portal confirms; post a new receipt via record_historical_payment()+allocate_payment()."
        ),
    )
