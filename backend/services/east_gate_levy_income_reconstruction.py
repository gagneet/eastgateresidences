# @featuretrace:demo_bank — Corrected levy-income historical reconstruction generator (Step 2).
# Layer: service
# Data flow: financial_source_fact_versions (Mongo, versioned, inactive) + units (Mongo)
#            -> ReconstructionManifest -> services.reconstruction_batch_service.submit_for_review()
#            (stops at needs_review; approval/generation/sync are separate, later, human-gated steps).
# Related: docs/research/financial-db-issues_plan04.md ("Change the implementation sequence")
#          backend/services/reconstruction_batch_service.py (workflow orchestration, reused as-is)
#          backend/services/historical_levy_reconstruction_service.py (the RETIRED predecessor —
#          synthetic_from_budget, no longer eligible for promotion; kept for other buildings'
#          non-East-Gate-specific onboarding, not reused here since it reads unversioned
#          annual_levies directly and lacks combined payment-header + real-evidence handling)
# Collection: financial_source_fact_versions, units, demo_bank_reconstruction_batches,
#             demo_bank_reconstruction_manifests
"""
Corrected levy-income reconstruction generator — East Gate 13195, Step 2 of the
financial-db-issues_plan04.md 12-step sequence.

Supersedes the old `historical_levy_reconstruction_service.synthesize_historical_levy_payments()`
approach for this building. Differences, all per plan04's corrections:

  1. Reads from `financial_source_fact_versions` (versioned, inactive, EC-confirmed facts —
     see docs/fixes/east_gate_13195_historical_reconstruction_step1_source_facts_2026-07-22.md),
     never `annual_levies` directly.
  2. Uses REAL levy-instalment dates/amounts wherever the source facts record them (2021's
     two ordinary half-yearly + one additional urgent instalment; 2022's four real quarterly
     admin dates and three real sinking dates, deliberately NOT aligned with admin — real
     evidence that admin/sinking were separate payments that year, so they are generated as
     separate payment groups, not combined). Only falls back to the provisional
     Q1-Q4/grace-period/payment-behaviour policy for years with no real per-instalment
     evidence (2023-2026).
  3. Combines same-date admin+sinking amounts into ONE payment_group_id (one Demo Bank
     transaction, GST-split allocation lines) wherever they legitimately occur together —
     never emits separate "admin credit" + "sinking credit" Demo Bank rows for what was one
     real payment event. See ReconstructedTransactionRow.payment_group_id.
  4. GST is split net/gst per fund from the GST-inclusive confirmed totals (East Gate's
     confirmed gst_basis="inclusive"), never re-derived downstream.
  5. Money never leaves integer cents.
  6. Stops at ReconstructionBatch.status="needs_review" — approval is a separate, later,
     human (EC) action. This module never calls approve_batch(), never populates Demo Bank,
     never syncs, never posts. See reconstruction_batch_service.submit_for_review()'s own
     docstring for why that boundary is load-bearing, not incidental.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import uuid4

GST_RATE = 0.10  # East Gate's confirmed rate (financial_source_fact_versions years.*.gst_rate context)


# ─────────────────────────────────────────────────────────────────────────────
# Pure math — no DB, no I/O. Unit-tested directly against the TH087 worked
# example and against full-building reconciliation totals.
# ─────────────────────────────────────────────────────────────────────────────

def largest_remainder_allocation(total_cents: int, weights: dict[str, int]) -> dict[str, int]:
    """Split `total_cents` across `weights` (e.g. {unit_number: uoe}) so the shares sum to
    EXACTLY total_cents, using the largest-remainder (Hare-Niemeyer) apportionment method.

    Naive independent per-unit rounding (round(total * uoe/total_uoe)) can drift the sum
    away from total_cents by a few cents in either direction — unacceptable here, since Step
    4's shadow reconciliation requires an EXACT tie-out to the confirmed year/fund totals.
    Every key gets floor(exact_share); the remaining pennies (total_cents - sum(floors)) go
    one each to the keys with the largest fractional remainder, breaking ties by key name for
    determinism (reproducible across re-runs of the same manifest).
    """
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError(f"total_weight must be positive, got {total_weight}")
    if total_cents == 0:
        return {k: 0 for k in weights}

    exact = {k: (total_cents * w) / total_weight for k, w in weights.items()}
    floors = {k: int(v) for k, v in exact.items()}  # truncates toward zero; total_cents/weights are non-negative here
    remainder = total_cents - sum(floors.values())
    if remainder < 0 or remainder > len(weights):
        raise AssertionError(f"largest_remainder_allocation: unexpected remainder {remainder} for {len(weights)} keys")

    ranked = sorted(weights.keys(), key=lambda k: (-(exact[k] - floors[k]), k))
    result = dict(floors)
    for k in ranked[:remainder]:
        result[k] += 1

    assert sum(result.values()) == total_cents, "largest_remainder_allocation must sum exactly"
    return result


def split_gst_inclusive(gross_cents: int, gst_rate: float = GST_RATE) -> tuple[int, int]:
    """Split a GST-inclusive gross amount into (net_cents, gst_cents), net+gst == gross exactly."""
    if gross_cents == 0:
        return 0, 0
    net_cents = round(gross_cents / (1 + gst_rate))
    gst_cents = gross_cents - net_cents
    return net_cents, gst_cents


# ─────────────────────────────────────────────────────────────────────────────
# Levy event model — one row per (fund or funds, date, building-wide amount).
# Events with more than one fund key are combined into one payment_group_id per
# unit (one Demo Bank transaction, allocation lines per fund). Events with a
# single fund key are naturally single-line per unit.
# ─────────────────────────────────────────────────────────────────────────────

class LevyEvent:
    __slots__ = ("label", "applied_date", "due_date", "fund_amounts_cents",
                 "assumption_code", "confidence", "combine_funds", "reconciliation_groups")

    def __init__(self, *, label: str, applied_date: date, due_date: Optional[date],
                 fund_amounts_cents: dict[str, int], assumption_code: str, confidence: str,
                 combine_funds: bool, reconciliation_groups: dict[str, str]):
        self.label = label
        self.applied_date = applied_date
        self.due_date = due_date
        self.fund_amounts_cents = fund_amounts_cents  # {"admin": cents} and/or {"sinking": cents}
        self.assumption_code = assumption_code
        self.confidence = confidence
        self.combine_funds = combine_funds  # True -> one payment_group_id across the funds present
        # {fund_type: reconciliation_group_key} — which confirmed-total bucket this event's
        # per-fund amount belongs to. Deliberately NOT derived from financial_year/fund_type
        # alone: e.g. the Dec-2021 urgent levy applies (ledger-posts) in Jan 2022 but must
        # reconcile against its OWN confirmed $69,230.14 figure, not get silently absorbed
        # into (or subtracted from) either year's ordinary admin total.
        self.reconciliation_groups = reconciliation_groups


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def _provisional_quarter_split(year: str, source_facts: dict) -> dict[str, dict]:
    """The exact largest-remainder-allocated {admin, sinking} split of `year`'s confirmed
    annual totals across all 4 provisional quarters, plus each quarter's due date.

    Returns ALL 4 quarters regardless of whether their due date has arrived — callers
    filter by due_date themselves. Shared by build_levy_events() (generates a payment
    event only for quarters whose due date has passed) and reconciliation_targets()
    (sums only those same included quarters) so the two can never independently drift
    on what each quarter is worth — a single source of truth for the split, not two
    copies of the same largest_remainder_allocation() call that could diverge if only
    one were ever updated.
    """
    due_dates = source_facts["levy_schedule"]["due_dates"]
    quarters = [("Q1", due_dates["Q1"]), ("Q2", due_dates["Q2"]), ("Q3", due_dates["Q3"]), ("Q4", due_dates["Q4"])]
    y = source_facts["years"][year]
    admin_total = y["admin_fund"]["amount_cents"]
    sinking_total = y["sinking_fund"]["amount_cents"]
    admin_per_q = largest_remainder_allocation(admin_total, {q: 1 for q, _ in quarters}) if admin_total else {q: 0 for q, _ in quarters}
    sinking_per_q = largest_remainder_allocation(sinking_total, {q: 1 for q, _ in quarters}) if sinking_total else {q: 0 for q, _ in quarters}
    return {
        q: {"admin": admin_per_q[q], "sinking": sinking_per_q[q], "due_date": _d(f"{year}-{mmdd}")}
        for q, mmdd in quarters
    }


def build_levy_events(source_facts: dict, as_of_date: date) -> list[LevyEvent]:
    """Derive the list of building-wide levy events from a financial_source_fact_versions
    document (expects the v5-shaped document — years.2021/2022 real-instalment detail,
    additional_levy_instalments, levy_schedule.due_dates for years without real detail).

    `as_of_date` is the real-world date this manifest is being generated as of — REQUIRED,
    not defaulted here, so callers (and tests) are always explicit about it. A provisional
    quarter whose due date is after `as_of_date` is never generated as a "payment received"
    event: no owner could plausibly have paid a levy that isn't due yet (barring genuine
    early/advance payment by a specific owner, which this generator does not model — see
    generate_levy_income_manifest's docstring). Found live 2026-07-22/23: the pre-fix
    generator produced 174 transactions ($220,187.44) dated 2026-09-01 and 2026-12-01 —
    both after that generation run's real-world date — as if every unit had already paid
    Q3/Q4 2026 in full months before either due date arrived.
    """
    events: list[LevyEvent] = []
    years = source_facts["years"]

    # --- 2021: two ordinary half-yearly instalments (admin only, sinking=$0) ---
    y2021 = years["2021"]
    for inst in y2021["levy_instalments"]["instalments"]:
        if inst["type"] in ("ordinary_half_year_1", "ordinary_half_year_2"):
            events.append(LevyEvent(
                label=f"2021 ordinary half-year ({inst['date']})",
                applied_date=_d(inst["date"]), due_date=_d(inst["date"]),
                fund_amounts_cents={"admin": inst["amount_cents"]},
                assumption_code="confirmed_real_instalment", confidence="confirmed",
                combine_funds=False,
                reconciliation_groups={"admin": "2021_admin_ordinary"},
            ))
    # The Dec-2021/Jan-2022 additional urgent levy — its own event, real date, admin only,
    # its OWN reconciliation target ($69,230.14) — never absorbed into either year's
    # ordinary admin comparison, since it was confirmed as an ADDITIONAL charge beyond
    # both years' ordinary levy determinations.
    # applied_date = ledger_posting_date (when it hit owner accounts, per date_resolution);
    # due_date is not separately recorded for this event, so we use the same date — there is
    # no evidence of a distinct earlier "due" date independent of the posting date itself.
    urgent = source_facts["additional_levy_instalments"]["dec_2021_urgent_levy"]
    urgent_date = _d(urgent["ledger_posting_date"])
    events.append(LevyEvent(
        label="2021/2022 additional urgent Administrative levy",
        applied_date=urgent_date,
        due_date=urgent_date,
        fund_amounts_cents={"admin": urgent["amount_cents"]},
        assumption_code="confirmed_additional_urgent_levy", confidence="confirmed",
        combine_funds=False,
        reconciliation_groups={"admin": "urgent_levy_dec2021"},
    ))

    # --- 2022: four real admin instalment DATES, three real sinking instalments (dates
    # AND amounts) — NOT the same dates (real evidence they were separate payments this
    # year) -> not combined. The admin instalments' source facts record the date of each
    # (classification="confirmed") but not a per-instalment amount — only the year total
    # is confirmed (years.2022.admin_fund.amount_cents) — so each instalment's amount is
    # an equal split of that total, not itself a confirmed figure. Tagged with a distinct
    # assumption_code from the sinking loop below (which DOES have real per-instalment
    # amounts) so a reviewer reading assumption_code alone isn't told the exact admin
    # instalment amount was confirmed when only its date and the annual total were. ---
    y2022 = years["2022"]
    for inst in y2022["levy_instalments"]["instalments"]:
        events.append(LevyEvent(
            label=f"2022 admin instalment {inst['quarter']} ({inst['date']})",
            applied_date=_d(inst["date"]), due_date=_d(inst["date"]),
            fund_amounts_cents={"admin": None},  # filled in below (equal split of the year total)
            assumption_code="confirmed_date_equal_split_amount", confidence="confirmed",
            combine_funds=False,
            reconciliation_groups={"admin": "2022_admin_ordinary"},
        ))
    admin_2022_per_instalment = y2022["admin_fund"]["amount_cents"] // len(y2022["levy_instalments"]["instalments"])
    admin_2022_remainder = y2022["admin_fund"]["amount_cents"] - admin_2022_per_instalment * len(y2022["levy_instalments"]["instalments"])
    admin_events_2022 = [e for e in events if e.label.startswith("2022 admin instalment")]
    for i, e in enumerate(admin_events_2022):
        e.fund_amounts_cents["admin"] = admin_2022_per_instalment + (1 if i < admin_2022_remainder else 0)

    sinking_2022 = source_facts["additional_levy_instalments"]["dec_2022_sinking_fund_contribution"]
    ordinary_sinking_insts = sinking_2022["distinct_from_ordinary_levy_evidence"]["ordinary_2022_sinking_instalments"]
    for inst in ordinary_sinking_insts:
        events.append(LevyEvent(
            label=f"2022 sinking instalment ({inst['date']})",
            applied_date=_d(inst["date"]), due_date=_d(inst["date"]),
            fund_amounts_cents={"sinking": inst["amount_cents"]},
            assumption_code="confirmed_real_instalment", confidence="confirmed",
            combine_funds=False,
            reconciliation_groups={"sinking": "2022_sinking_ordinary"},
        ))
    # The Dec-2022 $30,000 additional Sinking Fund contribution — its own event, own
    # reconciliation target, lower confidence (per-unit allocation is inferred, not
    # from a real levy roll).
    events.append(LevyEvent(
        label="2022 additional Sinking Fund contribution",
        applied_date=_d(sinking_2022["transfer_date"]), due_date=_d(sinking_2022["transfer_date"]),
        fund_amounts_cents={"sinking": sinking_2022["amount_cents"]},
        assumption_code="inferred_uoe_allocation_pending_levy_roll", confidence="inferred",
        combine_funds=False,
        reconciliation_groups={"sinking": "additional_sinking_dec2022"},
    ))

    # --- Every other year present in source_facts (2023 onward for East Gate today, but
    # deliberately not hardcoded to that specific range): no real per-instalment evidence,
    # use the provisional Q1-Q4 due-date schedule + combine admin/sinking (no evidence
    # against combining these years). 2021/2022 are excluded here since they're handled
    # above from real per-instalment evidence. A quarter is only generated once its due
    # date has arrived (<= as_of_date) — see build_levy_events()'s own docstring. ---
    provisional_years = sorted(y for y in years.keys() if y not in ("2021", "2022"))
    for year in provisional_years:
        split = _provisional_quarter_split(year, source_facts)
        for q, amounts in split.items():
            if amounts["due_date"] > as_of_date:
                continue
            events.append(LevyEvent(
                label=f"{year} provisional {q} ({amounts['due_date'].isoformat()})",
                applied_date=amounts["due_date"], due_date=amounts["due_date"],
                fund_amounts_cents={"admin": amounts["admin"], "sinking": amounts["sinking"]},
                assumption_code="provisional_schedule_quarterly_regular", confidence="provisional",
                combine_funds=True,
                reconciliation_groups={"admin": f"{year}_admin_ordinary", "sinking": f"{year}_sinking_ordinary"},
            ))

    return events


def reconciliation_targets(source_facts: dict, as_of_date: date) -> dict[str, int]:
    """The confirmed cents figure each reconciliation_group key ties out to.

    `as_of_date` is REQUIRED and must match the value passed to build_levy_events() for
    the same manifest — for provisional (no-real-evidence) years, the target for a year
    with quarters excluded by the as_of_date cutoff is the sum of only the INCLUDED
    quarters' allocated share (via the same _provisional_quarter_split() both functions
    call), not the full annual confirmed total. Reconciling a partial year against its
    full-year total would report a permanent, misleading "under-collection" variance for
    every quarter that legitimately hasn't come due yet.
    """
    years = source_facts["years"]
    urgent = source_facts["additional_levy_instalments"]["dec_2021_urgent_levy"]
    sinking_2022 = source_facts["additional_levy_instalments"]["dec_2022_sinking_fund_contribution"]
    targets = {
        "2021_admin_ordinary": years["2021"]["admin_fund"]["amount_cents"],
        "urgent_levy_dec2021": urgent["amount_cents"],
        "2022_admin_ordinary": years["2022"]["admin_fund"]["amount_cents"],
        # Reconciles against the exact sum of the 3 real dated sinking instalments
        # ($25,000.01), not the nominal years.2022.sinking_fund.amount_cents
        # ($25,000.00) — v5 itself documents this 1-cent difference as expected,
        # not an error (source_facts.additional_levy_instalments.
        # dec_2022_sinking_fund_contribution.distinct_from_ordinary_levy_evidence.
        # note_vs_years_2022_sinking_fund).
        "2022_sinking_ordinary": sinking_2022["distinct_from_ordinary_levy_evidence"]["ordinary_2022_sinking_instalments_total_cents"],
        "additional_sinking_dec2022": sinking_2022["amount_cents"],
    }
    for year in sorted(y for y in years.keys() if y not in ("2021", "2022")):
        split = _provisional_quarter_split(year, source_facts)
        included = [v for v in split.values() if v["due_date"] <= as_of_date]
        targets[f"{year}_admin_ordinary"] = sum(v["admin"] for v in included)
        targets[f"{year}_sinking_ordinary"] = sum(v["sinking"] for v in included)
    return targets


# ─────────────────────────────────────────────────────────────────────────────
# Per-unit allocation — turns one building-wide LevyEvent into per-unit
# ReconstructedTransactionRow lines, GST-split, largest-remainder-exact.
# ─────────────────────────────────────────────────────────────────────────────

def allocate_event_to_units(
        *, event: LevyEvent, event_index: int, unit_uoes: dict[str, int],
        account_refs: dict[str, str], financial_year: str,
        arrears_overrides: Optional[dict[str, dict]] = None,
):
    """Split one LevyEvent's building-wide fund total(s) across every unit by UOE
    share (largest-remainder exact), GST-split each unit's share, and return the
    ReconstructedTransactionRow list. Rows for the same unit within this one
    event share a payment_group_id when event.combine_funds is True (e.g. a
    provisional quarter with both admin and sinking present) — collapsing to
    ONE Demo Bank transaction at materialisation time, per
    financial-db-issues_plan04.md point 5.

    IMPORTANT: a single real bank transaction can only land in ONE bank
    account. East Gate has two physically separate trust accounts (Admin,
    Capital Works/Sinking — confirmed via finance.trust_accounts, two
    "Macquire Bank" rows) and the whole existing Demo Bank sync/matching
    pipeline (routers/bank_feeds.py's _fund_type_for_account()) assumes a
    1:1 account_ref->fund_type mapping throughout. So when combine_funds is
    True, every row in the group is posted against the SAME account_ref —
    the admin account, treated as the building's primary levy-collection
    account (matching real Australian strata practice: owners pay one
    combined DEFT/BPAY reference, the managing agent's system allocates it
    across funds afterwards — this is exactly what
    finance.receipts(ONE trust_account_id) + finance.receipt_allocations
    (per-fund split) already models). The fund_type on each allocation LINE
    still correctly records the accounting split; only the transaction
    HEADER's account_ref is singular. Getting this wrong is not cosmetic —
    ingestion.import_historical_reconstruction()'s own consistency guard
    correctly refuses to materialise a group whose rows disagree on
    account_ref (found live, 2026-07-22: every provisional-year combined
    group was rejected until this was fixed).

    `arrears_overrides` (optional): {unit_number: {"skip": True, "reason": ...}}
    or {unit_number: {"posted_date": date}} — real evidence (e.g. the confirmed
    31-Dec-2021 and 13-Jan-2022 arrears reports) overriding the default
    "assume fully paid on schedule" policy for specific units/events. Not
    threaded through for every event yet (see generate_levy_income_manifest's
    docstring for exactly which events this applies to) — the hook exists so a
    future event can use real per-unit evidence without changing this function's
    contract.
    """
    from integrations.demo_bank.reconstruction_batch_schemas import ReconstructedTransactionRow

    rows = []
    seq_by_unit: dict[str, int] = {}
    header_account_ref = account_refs["admin"] if event.combine_funds else None
    for fund_type, gross_total in event.fund_amounts_cents.items():
        if not gross_total:
            continue
        per_unit_gross = largest_remainder_allocation(gross_total, unit_uoes)
        for unit_number, gross in per_unit_gross.items():
            if gross == 0:
                continue
            override = (arrears_overrides or {}).get(unit_number, {})
            if override.get("skip"):
                continue
            net_cents, gst_cents = split_gst_inclusive(gross)
            seq_by_unit[unit_number] = seq_by_unit.get(unit_number, 0) + 1
            posted_date = override.get("posted_date", event.applied_date)
            rows.append(ReconstructedTransactionRow(
                account_ref=header_account_ref or account_refs[fund_type],
                unit_number=unit_number,
                financial_year=financial_year,
                quarter=None,
                fund_type=fund_type,
                levy_component="ordinary",
                posted_date=posted_date,
                amount_cents=gross,
                amount_ex_gst_cents=net_cents,
                gst_cents=gst_cents,
                direction="credit",
                assumption_code=event.assumption_code,
                description=f"{event.label} — Unit {unit_number}",
                transaction_sequence=seq_by_unit[unit_number],
                payment_group_id=(f"evt{event_index}:{unit_number}" if event.combine_funds else None),
            ))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Manifest assembly + shadow reconciliation (plan04 steps 2-4: build, generate
# shadow manifest, reconcile against confirmed totals — stops there).
# ─────────────────────────────────────────────────────────────────────────────

def _financial_year_for(d: date) -> str:
    """East Gate's levy year is calendar-year-aligned except 2021 (Dec 2020 start,
    see source_facts.years.2021.levy_year_start) — any date in Dec 2020 belongs
    to financial_year "2021", matching how years.2021 is keyed in source facts."""
    if d.year == 2020 and d.month == 12:
        return "2021"
    return str(d.year)


async def generate_levy_income_manifest(
        db, *, building_id: str, source_facts: dict, batch_id: str, generator_version: str,
        as_of_date: Optional[date] = None,
):
    """Build a ReconstructionManifest for East Gate 13195's levy-income history,
    from a `financial_source_fact_versions` document (pass the v5-or-later doc).

    `as_of_date` defaults to the real current date if not given — the date this
    manifest is being generated as of. Provisional quarters (2023 onward, no real
    per-instalment evidence) whose due date is after this are never generated as
    "payment received" events; see build_levy_events()'s docstring for the live
    incident (174 transactions, $220,187.44, dated months into the future) this
    guards against. Pass it explicitly for a reproducible/testable manifest, or to
    regenerate "as of" a specific past date.

    Arrears evidence applied: the confirmed 31-Dec-2021 debtors (Units 55, 86 —
    source_facts.arrears_evidence_points.as_at_2021_12_31) do NOT override any
    generated event here, because they describe PRIOR-year balances outstanding
    at that date, not this generator's own events (its first event is the
    2020-12-23 instalment, before that evidence point). The 13-Jan-2022
    aggregate arrears report (most owners hadn't yet paid the new $69,230.14
    urgent levy) is evidence about payment TIMING, not an exclusion — already
    reflected by using ledger_posting_date (2022-01-01) as that event's date
    rather than an earlier "issue" date, deliberately not modelling individual
    late-payment dates beyond that single real anchor point (no per-unit
    evidence exists to do so more precisely than that).

    Returns (manifest, year_fund_reconciliation) — the manifest is NOT submitted
    to reconstruction_batch_service here; the caller decides when to call
    submit_for_review() (stops the workflow at needs_review, per that
    function's own documented invariant — no approval/generation/sync happens
    from this module).
    """
    from integrations.demo_bank.reconstruction_batch_schemas import (
        ReconstructionManifest, YearFundReconciliationLine,
    )

    if as_of_date is None:
        as_of_date = datetime.now(timezone.utc).date()

    units = await db.units.find(
        {"building_id": building_id}, {"_id": 0, "unit_number": 1, "entitlement": 1}
    ).to_list(500)
    unit_uoes = {u["unit_number"]: int(u["entitlement"]) for u in units if u.get("unit_number")}
    total_uoe = sum(unit_uoes.values())
    if total_uoe <= 0:
        raise ValueError(f"building {building_id}: total UOE across units is {total_uoe}, cannot allocate")

    account_refs = {"admin": f"ADMIN-{building_id}", "sinking": f"SINKING-{building_id}"}

    events = build_levy_events(source_facts, as_of_date)
    all_rows = []
    for i, event in enumerate(events):
        fy = _financial_year_for(event.applied_date)
        all_rows.extend(allocate_event_to_units(
            event=event, event_index=i, unit_uoes=unit_uoes, account_refs=account_refs,
            financial_year=fy,
        ))

    # Shadow reconciliation: each EVENT's building-wide fund amount reconciles against its
    # own tagged reconciliation_group target (reconciliation_targets()) — never a generic
    # (financial_year, fund_type) bucket. This is deliberate: events can apply/post in a
    # different calendar year than the levy year they belong to (the urgent levy postmarks
    # 2022-01-01 but is confirmed as its own $69,230.14 figure, not part of either year's
    # ordinary admin total), and "additional" events (urgent levy, extra sinking
    # contribution) have their own confirmed totals entirely separate from the ordinary
    # levy figures. Bucketing by (financial_year, fund_type) alone conflates all of this
    # and produces false mismatches — confirmed by the first version of this function
    # actually doing that and reporting three incorrect variances that had nothing to do
    # with a real generator bug.
    generated_by_group: dict[str, int] = {}
    group_fund_type: dict[str, str] = {}  # each group is single-fund by construction
    for event in events:
        for fund_type, amount in event.fund_amounts_cents.items():
            group = event.reconciliation_groups[fund_type]
            generated_by_group[group] = generated_by_group.get(group, 0) + amount
            if group in group_fund_type and group_fund_type[group] != fund_type:
                raise AssertionError(
                    f"reconciliation group {group!r} tagged with both "
                    f"{group_fund_type[group]!r} and {fund_type!r} — every group must be "
                    f"single-fund by construction; fix the tagging in build_levy_events()"
                )
            group_fund_type[group] = fund_type

    targets = reconciliation_targets(source_facts, as_of_date)
    year_fund_reconciliation = []
    for group, expected_cents in targets.items():
        generated_cents = generated_by_group.get(group, 0)
        variance = generated_cents - expected_cents
        # group keys are "{year}_{fund}_..." or a standalone label (urgent_levy_dec2021,
        # additional_sinking_dec2022) — year is parsed where present, "n/a" otherwise;
        # fund_type comes from group_fund_type (built above from the actual event data),
        # never string-matched against the group key.
        parts = group.split("_")
        year = parts[0] if parts[0].isdigit() else "n/a"
        fund_type = group_fund_type.get(group, "admin")  # targets with 0 generated txns still need a valid Literal
        year_fund_reconciliation.append(YearFundReconciliationLine(
            year=year, fund_type=fund_type,
            expected_levy_total_cents=expected_cents,
            generated_credit_cents=generated_cents,
            ending_arrears_cents=0,  # arrears/credit tracking is out of scope for this manifest pass
            owner_credit_cents=0,
            variance_cents=variance,
            within_tolerance=(variance == 0),
        ))

    total_credit_cents = sum(row.amount_cents for row in all_rows)
    input_fact_hash = hashlib.sha256(
        json.dumps(source_facts, sort_keys=True, default=str).encode()
    ).hexdigest()
    manifest_payload_for_hash = {
        "batch_id": batch_id, "building_id": building_id,
        "generator_version": generator_version, "input_fact_hash": input_fact_hash,
        "transaction_count": len(all_rows), "total_credit_cents": total_credit_cents,
    }
    manifest_hash = hashlib.sha256(
        json.dumps(manifest_payload_for_hash, sort_keys=True, default=str).encode()
    ).hexdigest()

    manifest = ReconstructionManifest(
        manifest_id=str(uuid4()),
        batch_id=batch_id,
        building_id=building_id,
        version=1,
        input_document_hashes=[input_fact_hash],
        input_fact_hash=input_fact_hash,
        calculation_configuration={
            "gst_rate": GST_RATE,
            "source_fact_version": source_facts.get("version"),
            "combine_admin_sinking_years": ["2023", "2024", "2025", "2026"],
            "separate_admin_sinking_years_real_evidence": ["2021", "2022"],
            "as_of_date": as_of_date.isoformat(),
        },
        generator_version=generator_version,
        expected_transaction_count=len(all_rows),
        expected_credit_cents=total_credit_cents,
        expected_debit_cents=0,
        transactions=all_rows,
        year_fund_reconciliation=year_fund_reconciliation,
        lot_reconciliation=[],
        fund_balance_reconciliation=[],
        warnings=[],
        manifest_hash=manifest_hash,
    )
    return manifest, year_fund_reconciliation
