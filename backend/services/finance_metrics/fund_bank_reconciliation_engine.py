"""backend/services/finance_metrics/fund_bank_reconciliation_engine.py

# @featuretrace:financial-onboarding — fund-level Admin+Sinking bank-reconciliation engine (A3).
# Layer: service
# Data flow: finance.funds (genesis opening) + finance.levy_items/levy_runs (levied) +
#            finance.receipts/receipt_allocations (received) + finance.expense_transactions
#            (spent) + finance.journal_entries/journal_lines (reconstruction_adjustment) ->
#            compute_fund_bank_reconciliation() -> FundBankReconciliationLine ->
#            persist_fund_bank_reconciliation() -> finance.fund_bank_reconciliations (UPSERT).
# Related: backend/alembic/versions/0082_fund_bank_recon.py (schema)
#          backend/services/reconstruction_generators/annual_fund_reconciliation.py (the
#              Mongo-side sibling this PG engine mirrors the opening/closing bridge shape of)
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (A3)
# Table: finance.fund_bank_reconciliations (writes), finance.funds/levy_items/levy_runs/
#        receipts/receipt_allocations/expense_transactions/journal_entries/journal_lines (reads)

Why this exists
---------------
`finance.fund_bank_reconciliations` (migration 0082) is the schema for a per-fund
opening -> movements -> closing -> external-balance bridge. This module is the engine that
populates the GL-computed side of that bridge (``opening_balance_cents`` through
``computed_closing_cents``). It never touches ``external_balance_cents`` — that is a real
Bank/Trust statement or a portal snapshot, wired separately (B2b); this engine leaves it
``NULL`` (missing != zero, status stays ``'unreconciled'``) until that anchor exists.

It is a reconciliation CONTROL, never a journal source (contract §0, rule 43): every figure
here is *derived by reading* already-posted GL data. Nothing in this module calls
``financial_core`` or posts a journal entry.

Cash bridge (per fund, per financial year, ``period_label='FY'``)
-------------------------------------------------------------------
``computed_closing_cents = opening_balance_cents + received_cents - expense_cents
                            + interest_cents + adjustment_cents``

- ``opening_balance_cents`` rolls forward from the PRIOR year's own
  ``computed_closing_cents`` row in this same table when one exists; otherwise it falls back
  to ``finance.funds.opening_balance_cents`` (the genesis figure — correct only for a
  building's first onboarded year, mirroring ``annual_fund_reconciliation.py``'s
  ``own_declared`` / ``carried_forward_from_prior_closing`` distinction).
- ``levied_cents`` is informational only (AR-side, not cash) — included so a reader can see
  billed-vs-collected at a glance; it never enters the cash arithmetic above.
- ``received_cents`` sums ``receipt_allocations.allocated_cents`` for active (non-reversed)
  receipts *received* within the calendar year, joined through ``levy_items.fund_id`` — the
  only reliable per-fund attribution for cash actually banked, since
  ``finance.gl_accounts`` for Bank (1000) / Undeposited Funds (1010) are scheme-wide control
  accounts with no ``fund_id`` (verified live 2026-08-10: both have ``fund_id IS NULL``) —
  the fund split lives in the sub-ledger (levy_items/expense_transactions), not the GL
  account itself, so this engine reads the sub-ledger, never nets a GL account balance.
- ``expense_cents`` sums ``expense_transactions.amount_cents + gst_cents`` directly
  (that table already carries ``fund_id`` + ``financial_year``), excluding any transaction
  whose posting journal entry was reversed.
- ``interest_cents`` is always ``0`` in this engine, with an explicit ``notes`` flag —
  **not a confirmed zero**. Trust-account interest posting (`cron/cron_trust_interest.py`) is
  Mongo-only today with no PostgreSQL journal path (confirmed live via the file's own
  featuretrace header), so there is nothing in PG to sum yet. Do not read ``interest_cents``
  as "no interest was earned"; read it as "not yet available in PG."
- ``adjustment_cents`` sums ``reconstruction_adjustment``-sourced journal lines hitting the
  cash-side accounts (1000/1010), **only when the owning journal_entry carries a fund_id**.
  Live-checked 2026-08-10: East Gate's 13 `reconstruction_adjustment` entries (the GAP-FIN-046
  portal-evidenced corrections) all have ``journal_entries.fund_id IS NULL`` — each is scoped
  to one lot, not resolved to a single fund by this engine (a lot can carry both admin and
  sinking levy_items in the same year, so guessing would misattribute real money). Their
  scheme-wide total is surfaced in every fund row's ``notes``/``metadata`` for that
  (scheme, year) as ``unattributed_adjustment_cents`` — never silently dropped, never guessed
  into a specific fund. Resolving this precisely (e.g. backfilling ``journal_entries.fund_id``
  at adjustment-posting time) is a follow-up, tracked in the master ticket.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RECONCILIATION_TOLERANCE_CENTS = 100  # $1 — matches annual_fund_reconciliation.py's tolerance
_FUND_TYPES = ("admin", "sinking")


@dataclass(frozen=True)
class FundBankReconciliationLine:
    scheme_id: str
    fund_id: str
    fund_type: str
    financial_year: str
    period_label: str
    as_of: date
    opening_balance_cents: int
    opening_provenance: str  # "genesis" | "carried_forward"
    levied_cents: int
    received_cents: int
    expense_cents: int
    interest_cents: int
    adjustment_cents: int
    computed_closing_cents: int
    unattributed_adjustment_cents: int
    receipt_allocation_gap_cents: int
    notes: Optional[str]


def _calendar_year_bounds(fy: str) -> tuple[date, date]:
    fy = str(fy).strip()
    if not (len(fy) == 4 and fy.isdigit()):
        raise ValueError(f"compute_fund_bank_reconciliation requires a 4-digit calendar year, got {fy!r}")
    year = int(fy)
    return date(year, 1, 1), date(year + 1, 1, 1)


_OPENING_PRIOR_SQL = text(
    """
    SELECT computed_closing_cents FROM finance.fund_bank_reconciliations
    WHERE scheme_id = CAST(:sid AS UUID) AND fund_id = CAST(:fid AS UUID)
      AND financial_year = :prior_fy AND period_label = :period_label
    """
)

_LEVIED_SQL = text(
    """
    SELECT COALESCE(SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents), 0)
        AS levied_cents
    FROM finance.levy_items li
    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
    JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
    WHERE li.scheme_id = CAST(:sid AS UUID) AND li.fund_id = CAST(:fid AS UUID)
      AND lr.financial_year = :fy
      AND je.status = 'posted'
    """
)

_RECEIVED_SQL = text(
    """
    SELECT COALESCE(SUM(ra.allocated_cents), 0) AS received_cents
    FROM finance.receipt_allocations ra
    JOIN finance.receipts r ON r.receipt_id = ra.receipt_id
    JOIN finance.levy_items li ON li.levy_item_id = ra.levy_item_id
    JOIN finance.journal_entries je ON je.journal_entry_id = r.journal_entry_id
    LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
    WHERE li.scheme_id = CAST(:sid AS UUID) AND li.fund_id = CAST(:fid AS UUID)
      AND ra.tenant_id = CAST(:tid AS UUID)
      AND je.status = 'posted'
      AND rev.journal_entry_id IS NULL
      AND r.received_on >= CAST(:fy_start AS date) AND r.received_on < CAST(:fy_end AS date)
    """
)

_EXPENSE_SQL = text(
    """
    SELECT COALESCE(SUM(et.amount_cents + et.gst_cents), 0) AS expense_cents
    FROM finance.expense_transactions et
    JOIN finance.journal_entries je ON je.journal_entry_id = et.journal_entry_id
    LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = et.journal_entry_id
    WHERE et.scheme_id = CAST(:sid AS UUID) AND et.fund_id = CAST(:fid AS UUID)
      AND et.financial_year = :fy
      AND je.status = 'posted'
      AND rev.journal_entry_id IS NULL
    """
)

# Cash-side (asset) accounts: debit increases cash, credit decreases it. Only entries whose
# journal_entry already carries this fund's fund_id are attributed here — see module docstring.
_ADJUSTMENT_SQL = text(
    """
    SELECT COALESCE(SUM(
        CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE -jl.amount_cents END
    ), 0) AS adjustment_cents
    FROM finance.journal_lines jl
    JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
    JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
    LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = je.journal_entry_id
    WHERE je.scheme_id = CAST(:sid AS UUID) AND je.fund_id = CAST(:fid AS UUID)
      AND je.source_type = 'reconstruction_adjustment'
      AND je.status = 'posted'
      AND ga.account_code IN ('1000', '1010')
      AND je.effective_on >= CAST(:fy_start AS date) AND je.effective_on < CAST(:fy_end AS date)
      AND rev.journal_entry_id IS NULL
    """
)

# Scheme-wide (not fund-attributable) reconstruction_adjustment cash-side total — entries
# whose journal_entry.fund_id IS NULL. Surfaced, never guessed into a specific fund.
_UNATTRIBUTED_ADJUSTMENT_SQL = text(
    """
    SELECT COALESCE(SUM(
        CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE -jl.amount_cents END
    ), 0) AS unattributed_cents
    FROM finance.journal_lines jl
    JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
    JOIN finance.gl_accounts ga ON ga.gl_account_id = jl.gl_account_id
    LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = je.journal_entry_id
    WHERE je.scheme_id = CAST(:sid AS UUID) AND je.fund_id IS NULL
      AND je.source_type = 'reconstruction_adjustment'
      AND je.status = 'posted'
      AND ga.account_code IN ('1000', '1010')
      AND je.effective_on >= CAST(:fy_start AS date) AND je.effective_on < CAST(:fy_end AS date)
      AND rev.journal_entry_id IS NULL
    """
)

# Scheme-wide, fund-unattributed gap between what a receipt records as received
# (finance.receipts.amount_cents) and what has actually been allocated to a specific levy_item
# (finance.receipt_allocations.allocated_cents) -- receipts carry no fund_id of their own (only
# their allocations do, via the levy_item they're applied to), so a partially- or un-allocated
# receipt cannot be attributed to admin vs sinking without guessing. Live-checked 2026-08-10:
# East Gate 2021 alone has a real ~$172.83/period-consistent gap this way (receipts total
# $138,460.00 vs allocated $121,176.55) -- residue from the multiple historical posting code
# paths this codebase's own docs describe (GAP-FIN-046 §3), not something this engine invented
# or should silently absorb. Surfaced per (scheme, year), never netted into a specific fund's
# received_cents.
_RECEIPT_ALLOCATION_GAP_SQL = text(
    """
    SELECT COALESCE(SUM(r.amount_cents), 0) - COALESCE(SUM(alloc.allocated), 0) AS gap_cents
    FROM finance.receipts r
    JOIN finance.journal_entries je ON je.journal_entry_id = r.journal_entry_id
    LEFT JOIN finance.journal_entries rev ON rev.reversal_of_id = r.journal_entry_id
    LEFT JOIN (
        SELECT receipt_id, SUM(allocated_cents) AS allocated
        FROM finance.receipt_allocations WHERE tenant_id = CAST(:tid AS UUID)
        GROUP BY receipt_id
    ) alloc ON alloc.receipt_id = r.receipt_id
    WHERE r.tenant_id = CAST(:tid AS UUID)
      AND je.status = 'posted'
      AND rev.journal_entry_id IS NULL
      AND r.received_on >= CAST(:fy_start AS date) AND r.received_on < CAST(:fy_end AS date)
    """
)

_FUNDS_SQL = text(
    """
    SELECT fund_id::text AS fund_id, fund_type::text AS fund_type,
           COALESCE(opening_balance_cents, 0) AS opening_balance_cents
    FROM finance.funds
    WHERE scheme_id = CAST(:sid AS UUID) AND fund_type::text = ANY(:fund_types)
    """
)


async def compute_fund_bank_reconciliation(
    session: AsyncSession,
    *,
    scheme_id: str,
    tenant_id: str,
    financial_year: str,
    period_label: str = "FY",
    as_of: Optional[date] = None,
    prior_closing_override: Optional[dict[str, int]] = None,
) -> list[FundBankReconciliationLine]:
    """One line per (admin, sinking) fund that exists for this scheme. Pure read — never writes.

    ``as_of`` defaults to the last day of ``financial_year`` (a stable, deterministic value —
    this module does not call ``date.today()``, matching the workflow-script no-nondeterminism
    convention this codebase follows elsewhere).

    ``prior_closing_override`` (``{fund_id: computed_closing_cents}``) lets a multi-year caller
    (e.g. a dry-run preview that processes years ascending without persisting between them) chain
    each year's opening off the PREVIOUS call's in-memory result instead of a DB row that dry-run
    never wrote. Takes precedence over both the persisted prior-year row and the funds-table
    genesis value when a fund_id key is present. Absent by default (falls back to the DB read).
    """
    fy_start, fy_end = _calendar_year_bounds(financial_year)
    prior_fy = str(int(financial_year) - 1)
    resolved_as_of = as_of or date(int(financial_year), 12, 31)

    fund_rows = (
        await session.execute(_FUNDS_SQL, {"sid": scheme_id, "fund_types": list(_FUND_TYPES)})
    ).fetchall()

    unattributed_cents = int(
        (
            await session.execute(
                _UNATTRIBUTED_ADJUSTMENT_SQL,
                {"sid": scheme_id, "fy_start": fy_start, "fy_end": fy_end},
            )
        ).scalar()
        or 0
    )
    receipt_allocation_gap_cents = int(
        (
            await session.execute(
                _RECEIPT_ALLOCATION_GAP_SQL,
                {"tid": tenant_id, "fy_start": fy_start, "fy_end": fy_end},
            )
        ).scalar()
        or 0
    )

    lines: list[FundBankReconciliationLine] = []
    for row in fund_rows:
        fund_id = row.fund_id
        fund_type = row.fund_type

        if prior_closing_override and fund_id in prior_closing_override:
            opening_cents = int(prior_closing_override[fund_id])
            opening_provenance = "carried_forward"
        else:
            prior_row = (
                await session.execute(
                    _OPENING_PRIOR_SQL,
                    {"sid": scheme_id, "fid": fund_id, "prior_fy": prior_fy, "period_label": period_label},
                )
            ).first()
            if prior_row is not None:
                opening_cents = int(prior_row.computed_closing_cents)
                opening_provenance = "carried_forward"
            else:
                opening_cents = int(row.opening_balance_cents)
                opening_provenance = "genesis"

        levied_cents = int(
            (await session.execute(_LEVIED_SQL, {"sid": scheme_id, "fid": fund_id, "fy": financial_year})).scalar()
            or 0
        )
        received_cents = int(
            (
                await session.execute(
                    _RECEIVED_SQL,
                    {"sid": scheme_id, "fid": fund_id, "tid": tenant_id, "fy_start": fy_start, "fy_end": fy_end},
                )
            ).scalar()
            or 0
        )
        expense_cents = int(
            (
                await session.execute(
                    _EXPENSE_SQL, {"sid": scheme_id, "fid": fund_id, "fy": financial_year}
                )
            ).scalar()
            or 0
        )
        adjustment_cents = int(
            (
                await session.execute(
                    _ADJUSTMENT_SQL,
                    {"sid": scheme_id, "fid": fund_id, "fy_start": fy_start, "fy_end": fy_end},
                )
            ).scalar()
            or 0
        )
        interest_cents = 0  # not yet available in PG — see module docstring

        computed_closing_cents = (
            opening_cents + received_cents - expense_cents + interest_cents + adjustment_cents
        )

        notes_parts = [
            "interest_cents=0 is NOT a confirmed zero -- trust-account interest posting is "
            "Mongo-only today (cron_trust_interest.py), no PostgreSQL journal path exists yet."
        ]
        if unattributed_cents:
            notes_parts.append(
                f"${unattributed_cents / 100:.2f} in scheme-wide reconstruction_adjustment "
                "entries could not be attributed to a specific fund (journal_entries.fund_id "
                "is NULL on those rows) and is excluded from this fund's adjustment_cents -- "
                "see unattributed_adjustment_cents."
            )
        if abs(receipt_allocation_gap_cents) > _RECONCILIATION_TOLERANCE_CENTS:
            notes_parts.append(
                f"${receipt_allocation_gap_cents / 100:.2f} scheme-wide gap between "
                "finance.receipts.amount_cents and finance.receipt_allocations.allocated_cents "
                "this year (receipts recorded as received but not, or only partly, allocated to "
                "a specific levy_item) -- excluded from received_cents (which counts only "
                "allocated cash), fund-unattributable, and NOT fixed by this engine -- see "
                "receipt_allocation_gap_cents. Likely residue from a prior non-canonical "
                "posting path (see docs/finances/GAP-FIN-046-reconciliation-analysis.md)."
            )

        lines.append(
            FundBankReconciliationLine(
                scheme_id=scheme_id,
                fund_id=fund_id,
                fund_type=fund_type,
                financial_year=str(financial_year),
                period_label=period_label,
                as_of=resolved_as_of,
                opening_balance_cents=opening_cents,
                opening_provenance=opening_provenance,
                levied_cents=levied_cents,
                received_cents=received_cents,
                expense_cents=expense_cents,
                interest_cents=interest_cents,
                adjustment_cents=adjustment_cents,
                computed_closing_cents=computed_closing_cents,
                unattributed_adjustment_cents=unattributed_cents,
                receipt_allocation_gap_cents=receipt_allocation_gap_cents,
                notes=" ".join(notes_parts),
            )
        )

    return lines


_UPSERT_SQL = text(
    """
    INSERT INTO finance.fund_bank_reconciliations
        (reconciliation_id, tenant_id, scheme_id, fund_id, financial_year, period_label, as_of,
         opening_balance_cents, levied_cents, received_cents, expense_cents, interest_cents,
         adjustment_cents, computed_closing_cents, external_balance_cents, external_source,
         variance_cents, status, notes, metadata, is_test_data)
    VALUES
        (gen_random_uuid(), CAST(:tid AS UUID), CAST(:sid AS UUID), CAST(:fid AS UUID), :fy,
         :period_label, :as_of, :opening_cents, :levied_cents, :received_cents, :expense_cents,
         :interest_cents, :adjustment_cents, :computed_closing_cents, NULL, 'unreconciled',
         NULL, 'unreconciled', :notes, CAST(:metadata AS jsonb), :is_test_data)
    ON CONFLICT (scheme_id, fund_id, financial_year, period_label) DO UPDATE SET
        as_of = EXCLUDED.as_of,
        opening_balance_cents = EXCLUDED.opening_balance_cents,
        levied_cents = EXCLUDED.levied_cents,
        received_cents = EXCLUDED.received_cents,
        expense_cents = EXCLUDED.expense_cents,
        interest_cents = EXCLUDED.interest_cents,
        adjustment_cents = EXCLUDED.adjustment_cents,
        computed_closing_cents = EXCLUDED.computed_closing_cents,
        notes = EXCLUDED.notes,
        metadata = EXCLUDED.metadata,
        updated_at = now()
    RETURNING reconciliation_id::text
    """
)


async def persist_fund_bank_reconciliation(
    session: AsyncSession,
    lines: list[FundBankReconciliationLine],
    *,
    tenant_id: str,
    is_test_data: bool = False,
) -> list[str]:
    """UPSERT each computed line into finance.fund_bank_reconciliations.

    Never touches ``external_balance_cents``/``variance_cents`` on an existing row that already
    has one set from a separate, real reconciliation pass — this function always writes them
    back to NULL/'unreconciled' because it has no external evidence of its own (see the module
    docstring: this engine populates only the GL-computed side of the bridge). Callers that
    later attach a real external anchor (B2b) must do so via a separate, explicit UPDATE that
    this function does not overwrite implicitly.

    Does NOT post to the GL and does NOT call financial_core — this is a reconciliation control
    row, not a journal (contract rule 43). Idempotent: re-running with the same inputs UPSERTs
    to the same values via the (scheme_id, fund_id, financial_year, period_label) unique key.
    """
    ids: list[str] = []
    for line in lines:
        result = await session.execute(
            _UPSERT_SQL,
            {
                "tid": tenant_id,
                "sid": line.scheme_id,
                "fid": line.fund_id,
                "fy": line.financial_year,
                "period_label": line.period_label,
                "as_of": line.as_of,
                "opening_cents": line.opening_balance_cents,
                "levied_cents": line.levied_cents,
                "received_cents": line.received_cents,
                "expense_cents": line.expense_cents,
                "interest_cents": line.interest_cents,
                "adjustment_cents": line.adjustment_cents,
                "computed_closing_cents": line.computed_closing_cents,
                "notes": line.notes,
                # json.dumps(), not manual f-string interpolation: opening_provenance is
                # code-controlled today (only "genesis"/"carried_forward"), but hand-built
                # JSON is a fragile pattern -- any future value containing a quote or
                # backslash would produce invalid JSON and fail the jsonb cast at the DB
                # layer instead of failing loudly in Python. Self-audit fix, 2026-08-10.
                "metadata": json.dumps({
                    "unattributed_adjustment_cents": line.unattributed_adjustment_cents,
                    "receipt_allocation_gap_cents": line.receipt_allocation_gap_cents,
                    "opening_provenance": line.opening_provenance,
                }),
                "is_test_data": is_test_data,
            },
        )
        ids.append(result.scalar())
    return ids
