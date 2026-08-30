# Onboarding Financial Reconstruction — G3.2: per-lot opening balance (arrears/credit) generator.
#
# Data flow: portal net snapshot (per-unit balance_cents) + lot/owner refs -> OpeningBalancePlan.
#   For a building migrating onto StrataOS (old trust account closed, no transaction history), the
#   ONLY per-owner anchor we have is the portal's CURRENT net position per unit. This builds the
#   genesis "opening" for each lot from that net: a lot that OWES at migration gets an opening
#   arrears charge; a lot in CREDIT gets an opening credit receipt — so its net_balance starts at
#   the true migration position and flows through the SAME get_arrears_metrics/net_balance machinery
#   as every other charge (confirmed design option A + admin-fund default).
#
# Scope of THIS module (G3.2 increment 1): build + preview ONLY. Pure and synchronous — no DB, no
# writes — so it is fully unit-testable. The apply/posting step (increment 2) sources the lots +
# portal snapshot and posts each line via the canonical financial_core primitives (opening arrears
# via a levy-charge posting; opening credit via a payment/receipt posting), idempotently and
# reverse-then-replace.
#
# @featuretrace layer=service feature=onboarding-financial-reconstruction
# Related: backend/services/portal_ledger_reconciliation_service.py (the portal net snapshot source),
#          backend/services/reconstruction_generators/historical_levy_payment_generator.py (the pure
#          build/apply split this mirrors),
#          docs/architecture/onboarding/04_onboarding_financial_data_flow.md (G3)

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

# The portal net is a single per-unit number, not an admin/sinking split. Per the confirmed design,
# the per-lot opening defaults to the ADMIN fund and is tagged as an assumption; the building-level
# fund opening (Q1) keeps the true admin/sinking split separately.
OPENING_FUND_DEFAULT = "admin"

# Opening balances are an assumption derived from the portal net, never observed bank evidence — so
# they carry the reconstruction provenance tag, never "bank_reconciled", until a real feed supersedes.
OPENING_SOURCE_TAG = "reconstructed_historical"
OPENING_ASSUMPTION_CODE = "portal_net_opening_admin_default"
OPENING_DERIVATION_TYPE = "document_derived_opening_balance"


class OpeningBalanceGenerationError(ValueError):
    """Raised when an opening plan cannot be built. Plain ValueError subclass, matching the other
    reconstruction generators' convention, so this module has no dependency on the batch workflow."""


@dataclass(frozen=True)
class LotOwnerRef:
    """The minimal per-lot identity the opening plan needs. The apply step resolves these from
    core.lots + date-scoped ownership; this pure module just consumes them."""
    unit_number: str
    lot_id: UUID
    owner_party_id: UUID


def opening_idempotency_key(building_id: str, as_of_year: int, fund_type: str, lot_id: UUID) -> str:
    """Deterministic key for one lot's opening line. Distinct 'opening' namespace so it can never
    collide with a levy charge or a derived payment for the same lot/year/fund."""
    return f"opening:{building_id}:{as_of_year}:{fund_type}:{lot_id}"


def opening_dedup_key(lot_id: UUID, as_of_year: int, fund_type: str) -> tuple:
    """The (lot, year, fund) tuple the apply step uses to detect an already-posted opening. Passed
    in via `existing_opening_keys` so this module stays DB-free; the apply wrapper populates it."""
    return (lot_id, as_of_year, fund_type)


@dataclass(frozen=True)
class LotOpeningLine:
    """One lot's genesis opening. `kind` decides how the apply step posts it:
      - "arrears": the lot OWED `amount_cents` at migration -> an opening levy-charge.
      - "credit":  the lot was `amount_cents` in CREDIT     -> an opening receipt.
    `amount_cents` is always a positive integer-cents magnitude."""
    unit_number: str
    lot_id: UUID
    owner_party_id: UUID
    fund_type: str  # OPENING_FUND_DEFAULT unless a split is ever provided
    kind: str  # "arrears" | "credit"
    amount_cents: int  # positive magnitude, integer cents
    as_of_date: date
    source_tag: str
    assumption_code: str
    derivation_type: str
    idempotency_key: str


@dataclass
class OpeningBalancePlan:
    building_id: str
    as_of_date: date
    fund_type: str
    lines: list[LotOpeningLine] = field(default_factory=list)
    # Aggregate owner position at migration — DISTINCT from the fund cash opening (Q1). This is the
    # sum of what owners owe / are owed, surfaced as its own figure, never force-equated to the fund
    # balance; the apply step reports both and flags an unexplained gap as a reconciliation exception.
    total_arrears_cents: int = 0
    total_credit_cents: int = 0
    skipped_zero: int = 0
    skipped_existing: int = 0
    lots_without_portal_data: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def net_owing_cents(self) -> int:
        """Building-wide owners' net at migration (arrears minus credit). Positive = owners owe the
        OC on balance; negative = owners are in credit on balance."""
        return self.total_arrears_cents - self.total_credit_cents


def build_opening_plan_from_portal_snapshot(
    building_id: str,
    as_of_date: date,
    lots: list[LotOwnerRef],
    portal_net_by_unit: dict[str, int],
    *,
    fund_type: str = OPENING_FUND_DEFAULT,
    existing_opening_keys: frozenset[tuple] = frozenset(),
) -> OpeningBalancePlan:
    """Build a per-lot opening plan from the portal net snapshot.

    `portal_net_by_unit` maps unit_number -> net balance in integer cents, using the portal's own
    sign convention: > 0 = the lot OWES (arrears), < 0 = the lot is in CREDIT, 0 / absent = nothing
    to open. `existing_opening_keys` (opening_dedup_key tuples) skips any lot whose opening for this
    (year, fund) is already posted, so a re-run never double-posts.

    Pure and synchronous — no DB, no writes."""
    if not lots:
        raise OpeningBalanceGenerationError(
            f"No lots supplied for building_id={building_id!r} — cannot build an opening plan."
        )
    as_of_year = as_of_date.year
    plan = OpeningBalancePlan(building_id=building_id, as_of_date=as_of_date, fund_type=fund_type)

    for lot in lots:
        net = portal_net_by_unit.get(lot.unit_number)
        if net is None:
            plan.lots_without_portal_data += 1
            plan.warnings.append(
                f"Unit {lot.unit_number}: no portal net balance in the snapshot — no opening "
                "generated (cannot assume a position we have no evidence for)."
            )
            continue
        net = int(net)
        if net == 0:
            plan.skipped_zero += 1
            continue

        key = opening_dedup_key(lot.lot_id, as_of_year, fund_type)
        if key in existing_opening_keys:
            plan.skipped_existing += 1
            continue

        if net > 0:
            kind, amount = "arrears", net
            plan.total_arrears_cents += amount
        else:
            kind, amount = "credit", -net
            plan.total_credit_cents += amount

        plan.lines.append(
            LotOpeningLine(
                unit_number=lot.unit_number,
                lot_id=lot.lot_id,
                owner_party_id=lot.owner_party_id,
                fund_type=fund_type,
                kind=kind,
                amount_cents=amount,
                as_of_date=as_of_date,
                source_tag=OPENING_SOURCE_TAG,
                assumption_code=OPENING_ASSUMPTION_CODE,
                derivation_type=OPENING_DERIVATION_TYPE,
                idempotency_key=opening_idempotency_key(building_id, as_of_year, fund_type, lot.lot_id),
            )
        )

    return plan


def summarize_opening_plan(plan: OpeningBalancePlan) -> dict:
    """A compact preview/dry-run surface: counts + integer-cents totals for the plan."""
    return {
        "building_id": plan.building_id,
        "as_of_date": plan.as_of_date.isoformat(),
        "fund_type": plan.fund_type,
        "lot_count": len(plan.lines),
        "arrears_lot_count": sum(1 for ln in plan.lines if ln.kind == "arrears"),
        "credit_lot_count": sum(1 for ln in plan.lines if ln.kind == "credit"),
        "total_arrears_cents": plan.total_arrears_cents,
        "total_credit_cents": plan.total_credit_cents,
        "net_owing_cents": plan.net_owing_cents,
        "skipped_zero": plan.skipped_zero,
        "skipped_existing": plan.skipped_existing,
        "lots_without_portal_data": plan.lots_without_portal_data,
    }
