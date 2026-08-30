"""backend/services/finance_metrics/lot_true_balance.py — canonical per-lot TRUE balance.

# @featuretrace:financial-onboarding — per-lot true balance incl. unapplied credit (GAP-FIN-036).
# Layer: service
# Data flow: routers/finance.py (unit-dashboard / building-overview PG branch) ->
#            compute_lot_true_balances(session, scheme_id, tenant_id, fy) ->
#            finance.levy_items (outstanding) + finance.receipts (unapplied credit) ->
#            LotTrueBalance{outstanding_cents, unapplied_credit_cents, true_balance_cents}.
# Related: backend/routers/finance.py (building-overview credit_result SQL this generalises)
#          tasks/GAP-FIN-036-postgres-credit-allocation-structural-gap.md
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (A1)

Why this exists
---------------
`FinancialCoreService.allocate_payment()` allocates a receipt against a lot's *open*
levy items (`outstanding_cents > 0`), in order, until the receipt is exhausted. It has
**no mechanism** for a receipt that exceeds everything ever charged to a lot: when a lot
has zero open items, the receipt (and its journal entry) are real and posted, but the
`finance.levy_items` sub-ledger — which every per-lot "balance" query in this codebase
sums — has **no row** to represent the residual credit. So a genuinely-in-credit unit
reads as ``$0.00`` at the PG layer, silently dropping the credit instead of showing it
as a negative balance (GAP-FIN-036, live-confirmed for 14 East Gate units, $9,084.23).

This module is the ONE canonical per-lot true-balance computation. It is:

- **additive and read-side only** — it creates no rows and changes no posting path, so it
  cannot regress the arrears reconciliation that already ties out to the portal
  (GAP-FIN-046: 0/87 East Gate units diverge). Arrears (``outstanding_cents``) is computed
  exactly as the building-overview PG branch already computes it; this module only *adds*
  the credit dimension the sub-ledger cannot express.
- **strictly per-lot, never netted** — one lot's unapplied credit can never reduce another
  lot's arrears (CLAUDE.md rule 10). Credit is derived per lot from *its own* active
  (non-reversed) receipts vs *its own* total levied — the exact ``GREATEST(0, received -
  levied)`` shape already proven in ``routers/finance.py``'s ``credit_result`` query.
- **integer cents throughout** (CLAUDE.md ledger-money rule).

``true_balance_cents`` is signed: ``> 0`` == arrears owed, ``< 0`` == credit held, ``0`` ==
square. Missing is not zero — a lot absent from the returned mapping has no PG ledger rows
for the year and its balance is *unknown*, which callers must surface as null, never $0.00.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class LotTrueBalance:
    """One lot's true balance for a financial year, in integer cents.

    Attributes
    ----------
    lot_id:
        ``core.lots.lot_id`` as a string UUID.
    outstanding_cents:
        Amount still owed on this lot's *open* levy items (``principal + gst + interest +
        recovery - paid``), floored at 0. This is the canonical arrears figure — identical
        to the building-overview PG branch — and is never reduced by another lot's credit.
    unapplied_credit_cents:
        Amount this lot has paid (via active, non-reversed receipts for the year) **beyond**
        everything ever levied to it — the residual that ``allocate_payment()`` had no open
        item to absorb. ``>= 0`` by construction (``GREATEST(0, received - levied)``).
    true_balance_cents:
        ``outstanding_cents - unapplied_credit_cents``. Signed: positive = arrears,
        negative = credit held by the owner, zero = square.
    """

    lot_id: str
    outstanding_cents: int
    unapplied_credit_cents: int

    @property
    def true_balance_cents(self) -> int:
        return self.outstanding_cents - self.unapplied_credit_cents

    @property
    def is_in_credit(self) -> bool:
        return self.true_balance_cents < 0


# Per-lot arrears (outstanding) — same expression as routers/finance.py's arrears_result,
# but WITHOUT the ``HAVING ... > 0`` filter, because a fully-paid or in-credit lot (0 or
# negative outstanding) is exactly the case this module must still return, not drop.
_OUTSTANDING_SQL = text(
    """
    SELECT
        l.lot_id::text AS lot_id,
        COALESCE(SUM(
            li.principal_cents + li.gst_cents + li.interest_cents
            + li.recovery_costs_cents - li.paid_cents
        ), 0) AS outstanding_cents
    FROM finance.levy_items li
    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
    JOIN core.lots l ON l.lot_id = li.lot_id
    -- GAP-FIN-062: posted-only gate, kept identical to routers/finance.py's
    -- arrears_result per this module's own must-match-exactly invariant.
    JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
      AND je.status = 'posted'
    WHERE li.scheme_id = CAST(:sid AS UUID)
      AND lr.financial_year = :fy
    GROUP BY l.lot_id
    """
)

# Per-lot unapplied credit — the exact ``GREATEST(0, received - levied)`` semantics already
# live in routers/finance.py's credit_result, but keyed per lot (that query only returns the
# building-wide SUM). Active receipts only: a receipt whose journal entry was reversed is
# excluded via the anti-join, so a reversed duplicate-credit never counts as credit.
_CREDIT_SQL = text(
    """
    SELECT
        lot_receipts.lot_id::text AS lot_id,
        GREATEST(0, lot_receipts.received_cents - COALESCE(lot_levied.levied_cents, 0)) AS credit_cents
    FROM (
        SELECT r.lot_id, SUM(r.amount_cents) AS received_cents
        FROM finance.receipts r
        -- GAP-FIN-062: only count the receipt's own posted journal entry.
        JOIN finance.journal_entries je ON je.journal_entry_id = r.journal_entry_id
          AND je.status = 'posted'
        -- A reversal may be linked by reversal_of_id OR only by source_reference.
        -- 127 of East Gate's 155 reversal entries carried a NULL reversal_of_id and
        -- named their target in source_reference alone, so a reversal_of_id-only
        -- anti-join silently counted $1,769,655.36 of REVERSED back-solve receipts as
        -- owner credit (2026-08-28). Migration 0101 backfills the column; this clause
        -- also matches source_reference so the module is correct on any row the
        -- backfill has not reached, and on any future writer that sets only one.
        LEFT JOIN finance.journal_entries rev
               ON rev.source_type = 'reversal'
              AND rev.tenant_id = r.tenant_id
              AND (rev.reversal_of_id = r.journal_entry_id
                   OR rev.source_reference = r.journal_entry_id::text)
              -- ...and that reversal has NOT itself been reversed. Journal entries are
              -- immutable, so undoing a reversal means posting a second reversal on top
              -- of it. A receipt reversed and then un-reversed is LIVE again, but an
              -- "does any reversal exist?" test excludes it forever.
              -- Found on UA005 (2026-08-28): entry 9167 reversed by 9808, then 9808
              -- reversed by 9822 during a rollback. The receipt is live; the naive test
              -- dropped $467.51 of real owner credit.
              AND NOT EXISTS (
                  SELECT 1 FROM finance.journal_entries unrev
                  WHERE unrev.source_type = 'reversal'
                    AND unrev.tenant_id = rev.tenant_id
                    AND (unrev.reversal_of_id = rev.journal_entry_id
                         OR unrev.source_reference = rev.journal_entry_id::text)
              )
        WHERE r.tenant_id = CAST(:tid AS UUID) AND rev.journal_entry_id IS NULL
          -- A RETIRED receipt is not money the owner holds. This filter was absent, so
          -- the 70 back-solve receipts retired under GAP-FIN-073 kept counting as credit.
          AND r.retired_at IS NULL
          AND r.received_on >= CAST(:fy || '-01-01' AS date)
          AND r.received_on < CAST((CAST(:fy AS int) + 1) || '-01-01' AS date)
        GROUP BY r.lot_id
    ) lot_receipts
    -- LEFT JOIN (not INNER) so a lot with receipts but NO levy_items for the year (levied
    -- nothing, paid something -- the extreme GAP-FIN-036 case) still surfaces its full receipt
    -- as credit instead of being silently dropped. COALESCE(levied, 0) in the GREATEST above is
    -- required with the LEFT JOIN: without it, received - NULL = NULL and GREATEST(0, NULL) = NULL,
    -- which would null out the credit. This is strictly MORE correct than the building-overview
    -- inline credit_result (which INNER-JOINs); when that route is consolidated onto this helper,
    -- adopt this LEFT JOIN form.
    LEFT JOIN (
        SELECT li.lot_id, SUM(
            li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents
        ) AS levied_cents
        FROM finance.levy_items li
        JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
        -- GAP-FIN-062: same posted-only gate as _OUTSTANDING_SQL above.
        JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
          AND je.status = 'posted'
        WHERE li.scheme_id = CAST(:sid AS UUID) AND lr.financial_year = :fy
        GROUP BY li.lot_id
    ) lot_levied ON lot_levied.lot_id = lot_receipts.lot_id
    """
)


def _is_plain_calendar_year(fy: str) -> bool:
    """The credit SQL casts ``:fy`` to int for the received_on window, so only a plain
    4-digit calendar year is valid. A FY *label* like ``"2025-2026"`` would raise in
    Postgres — callers must pass the resolved calendar year (mirrors ``_compute_owner_paid_split``).
    """
    fy = str(fy).strip()
    return len(fy) == 4 and fy.isdigit()


async def compute_lot_true_balances(
    session: AsyncSession,
    *,
    scheme_id: str,
    tenant_id: str,
    financial_year: str,
) -> dict[str, LotTrueBalance]:
    """Return ``{lot_id: LotTrueBalance}`` for every lot with PG ledger rows in the year.

    Runs on an **already-open, tenant-scoped** ``session`` (the caller must have run
    ``set_tenant(session, tenant_id)`` — the receipts table has no RLS bypass clause, so an
    un-scoped connection silently returns 0 rows; see CLAUDE.md's RLS footgun). Read-only:
    executes two ``SELECT``s and combines them in Python. Never writes.

    A lot present in the outstanding set but absent from the credit set simply has no
    unapplied credit (``0``). A lot absent from *both* is not in the mapping at all — its
    balance is unknown for the year (missing ≠ zero), which the caller surfaces as null.

    Raises
    ------
    ValueError
        If ``financial_year`` is not a plain 4-digit calendar year (the credit window needs
        an int-castable year). Callers that resolve a FY label must convert it first.
    """
    if not _is_plain_calendar_year(financial_year):
        raise ValueError(
            f"compute_lot_true_balances requires a 4-digit calendar year, got {financial_year!r}"
        )

    fy = str(financial_year).strip()

    outstanding_rows = (
        await session.execute(_OUTSTANDING_SQL, {"sid": scheme_id, "fy": fy})
    ).fetchall()
    credit_rows = (
        await session.execute(_CREDIT_SQL, {"sid": scheme_id, "tid": tenant_id, "fy": fy})
    ).fetchall()

    credit_by_lot: dict[str, int] = {
        row.lot_id: int(row.credit_cents or 0) for row in credit_rows
    }

    balances: dict[str, LotTrueBalance] = {}
    for row in outstanding_rows:
        lot_id = row.lot_id
        balances[lot_id] = LotTrueBalance(
            lot_id=lot_id,
            outstanding_cents=int(row.outstanding_cents or 0),
            unapplied_credit_cents=credit_by_lot.pop(lot_id, 0),
        )

    # A lot with unapplied credit but no levy_items outstanding row (fully paid, then
    # overpaid) still must appear — outstanding is 0, credit is real. This is the exact
    # GAP-FIN-036 case: without this branch the credited lot would vanish from the mapping.
    for lot_id, credit_cents in credit_by_lot.items():
        balances[lot_id] = LotTrueBalance(
            lot_id=lot_id,
            outstanding_cents=0,
            unapplied_credit_cents=credit_cents,
        )

    return balances


_UNIT_OUTSTANDING_SQL = text(
    """
    SELECT COALESCE(SUM(
        li.principal_cents + li.gst_cents + li.interest_cents
        + li.recovery_costs_cents - li.paid_cents
    ), 0) AS outstanding_cents
    FROM finance.levy_items li
    JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
    JOIN core.lots l ON l.lot_id = li.lot_id
    -- GAP-FIN-062: posted-only gate, same as _OUTSTANDING_SQL.
    JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
      AND je.status = 'posted'
    WHERE li.scheme_id = CAST(:sid AS UUID)
      AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
      AND lr.financial_year = :fy
    """
)

_UNIT_CREDIT_SQL = text(
    """
    SELECT GREATEST(0, COALESCE(received.received_cents, 0) - COALESCE(levied.levied_cents, 0)) AS credit_cents
    FROM (SELECT 1) _one
    LEFT JOIN (
        SELECT SUM(r.amount_cents) AS received_cents
        FROM finance.receipts r
        JOIN core.lots l ON l.lot_id = r.lot_id
        -- GAP-FIN-062: only count the receipt's own posted journal entry.
        JOIN finance.journal_entries je ON je.journal_entry_id = r.journal_entry_id
          AND je.status = 'posted'
        -- Same two corrections as _CREDIT_SQL (2026-08-28): a reversal may be linked by
        -- reversal_of_id OR by source_reference alone (127 of 155 East Gate reversals
        -- were the latter), and a RETIRED receipt is not credit the owner holds.
        -- These two queries MUST agree — this is the single-unit path for the same
        -- number the building-wide path returns.
        LEFT JOIN finance.journal_entries rev
               ON rev.source_type = 'reversal'
              AND rev.tenant_id = r.tenant_id
              AND (rev.reversal_of_id = r.journal_entry_id
                   OR rev.source_reference = r.journal_entry_id::text)
              -- ...and that reversal has NOT itself been reversed. Journal entries are
              -- immutable, so undoing a reversal means posting a second reversal on top
              -- of it. A receipt reversed and then un-reversed is LIVE again, but an
              -- "does any reversal exist?" test excludes it forever.
              -- Found on UA005 (2026-08-28): entry 9167 reversed by 9808, then 9808
              -- reversed by 9822 during a rollback. The receipt is live; the naive test
              -- dropped $467.51 of real owner credit.
              AND NOT EXISTS (
                  SELECT 1 FROM finance.journal_entries unrev
                  WHERE unrev.source_type = 'reversal'
                    AND unrev.tenant_id = rev.tenant_id
                    AND (unrev.reversal_of_id = rev.journal_entry_id
                         OR unrev.source_reference = rev.journal_entry_id::text)
              )
        WHERE r.tenant_id = CAST(:tid AS UUID) AND rev.journal_entry_id IS NULL
          AND r.retired_at IS NULL
          AND l.scheme_id = CAST(:sid AS UUID)
          AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
          AND r.received_on >= CAST(:fy || '-01-01' AS date)
          AND r.received_on < CAST((CAST(:fy AS int) + 1) || '-01-01' AS date)
    ) received ON TRUE
    LEFT JOIN (
        SELECT SUM(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents) AS levied_cents
        FROM finance.levy_items li
        JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
        JOIN core.lots l ON l.lot_id = li.lot_id
        -- GAP-FIN-062: same posted-only gate as _UNIT_OUTSTANDING_SQL.
        JOIN finance.journal_entries je ON je.journal_entry_id = li.journal_entry_id
          AND je.status = 'posted'
        WHERE li.scheme_id = CAST(:sid AS UUID)
          AND (l.unit_number = :unit_number OR l.lot_number = :unit_number)
          AND lr.financial_year = :fy
    ) levied ON TRUE
    """
)


async def compute_unit_true_balance(
    session: AsyncSession,
    *,
    scheme_id: str,
    tenant_id: str,
    unit_number: str,
    financial_year: str,
) -> LotTrueBalance | None:
    """True balance for a SINGLE unit — the efficient path for a unit dashboard/detail view.

    Same semantics as :func:`compute_lot_true_balances` scoped to one unit (matched on
    ``unit_number`` OR ``lot_number``, exactly like the unit-dashboard PG queries). Returns
    ``None`` (unknown) when the unit has no levy_items AND no active receipts for the year —
    missing ≠ zero, so the caller surfaces null rather than a $0.00 credit.

    Runs on an already-open, tenant-scoped ``session``. Raises ``ValueError`` on a
    non-4-digit ``financial_year`` (same constraint as the building-wide helper).
    """
    if not _is_plain_calendar_year(financial_year):
        raise ValueError(
            f"compute_unit_true_balance requires a 4-digit calendar year, got {financial_year!r}"
        )
    fy = str(financial_year).strip()

    outstanding_row = (
        await session.execute(
            _UNIT_OUTSTANDING_SQL,
            {"sid": scheme_id, "unit_number": unit_number, "fy": fy},
        )
    ).first()
    credit_row = (
        await session.execute(
            _UNIT_CREDIT_SQL,
            {"sid": scheme_id, "tid": tenant_id, "unit_number": unit_number, "fy": fy},
        )
    ).first()

    outstanding_cents = int((outstanding_row.outstanding_cents if outstanding_row else 0) or 0)
    credit_cents = int((credit_row.credit_cents if credit_row else 0) or 0)

    # No ledger presence at all for the year -> unknown, not zero.
    if outstanding_cents == 0 and credit_cents == 0:
        return None

    # Resolve this unit's lot_id for the returned record (best-effort; the balance is valid
    # regardless). Keep it cheap — a single scalar lookup.
    lot_row = (
        await session.execute(
            text(
                "SELECT l.lot_id::text AS lot_id FROM core.lots l "
                "WHERE l.scheme_id = CAST(:sid AS UUID) "
                "AND (l.unit_number = :unit_number OR l.lot_number = :unit_number) LIMIT 1"
            ),
            {"sid": scheme_id, "unit_number": unit_number},
        )
    ).first()
    lot_id = lot_row.lot_id if lot_row else unit_number

    return LotTrueBalance(
        lot_id=lot_id,
        outstanding_cents=outstanding_cents,
        unapplied_credit_cents=credit_cents,
    )


def building_unapplied_credit_cents(balances: dict[str, LotTrueBalance]) -> int:
    """Building-wide unapplied credit = Σ per-lot credit — identical, by construction, to
    the building-overview PG branch's ``credit_result`` scalar. Provided so that branch can
    route through this one canonical helper instead of duplicating the SQL.
    """
    return sum(b.unapplied_credit_cents for b in balances.values())


async def get_lot_true_balances(
    building_id: str,
    financial_year: str,
) -> dict[str, LotTrueBalance]:
    """Convenience wrapper: resolve the scheme, open a tenant-scoped session, and compute.

    For callers (a unit-detail read path, a future ``/finance/reconciliation`` view) that do
    not already hold an open session. Returns ``{}`` when the building has no PG scheme
    context yet (not-yet-onboarded) — an empty, valid result, not an error.
    """
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant

    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        return {}

    scheme_id = str(scheme["scheme_id"])
    tenant_id = str(scheme["tenant_id"])
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        return await compute_lot_true_balances(
            session,
            scheme_id=scheme_id,
            tenant_id=tenant_id,
            financial_year=financial_year,
        )
