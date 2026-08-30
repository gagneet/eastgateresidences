// @featuretrace:finance-reserve-forecast — Canonical client-side normaliser for the
// sinking fund / reserve projection.
// Layer: frontend
// Data flow: GET /analytics/sinking-fund-forecast -> normaliseReserveProjection()
//            -> ReserveRunwayChart / reserve detail modals (building-scoped).
// Related: backend/routers/analytics.py::get_sinking_fund_forecast
//          backend/services/forecast_service.py::get_capital_replacement_projection
//          backend/services/analytics_pg_service.py::get_sinking_fund_forecast_pg

/**
 * One row of the reserve projection, after normalisation.
 *
 * `undefined` means UNKNOWN — the backend had no source for the figure. It must render as
 * "—". A numeric `0` means a genuine zero (e.g. a year the capital schedule lists no asset
 * for) and must render as "$0.00". Collapsing the two is the bug this module exists to
 * prevent; see `capital_works` below.
 */
export type ReserveProjectionRow = {
  year?: number | string;
  opening_balance?: number;
  closing_balance?: number;
  contributions?: number;
  capital_works?: number;
  events?: Array<{ item: string; cost: number }>;
  shock_label?: string;
  [key: string]: any;
};

/**
 * Normalise one projection row from whichever read path served it.
 *
 * Two backends feed this shape and they do not agree on field names:
 *
 *   Mongo path (routers/analytics.py)      -> year, opening_balance, contributions,
 *                                             expenses, closing_balance, events
 *   Postgres path (analytics_pg_service.py) -> year, balance, contributions, is_actual
 *                                             (no capital-works figure at all)
 *
 * The alias chains below exist to absorb that difference in ONE place. They were
 * previously copy-pasted, byte-identical, into ManagementDashboard.tsx and
 * ManagerDashboard.jsx — and both copies were wrong the same way: neither listed
 * `expenses`, which is the only key the Mongo path has ever emitted for capital works. So
 * the chain always fell through to a trailing `?? 0` and the "Capital works" figure read
 * $0.00 on every year, for every building, no matter what the capital schedule held.
 *
 * Two rules encoded here:
 *   1. `expenses` leads the capital-works chain. The remaining aliases are kept for the
 *      Postgres path and older cached payloads, not because anything emits them today.
 *   2. The chain ends in `undefined`, never `0`. `??` (not `||`) throughout, so a real 0
 *      survives instead of being mistaken for absence.
 */
export function normaliseReserveProjectionRow(row: any): ReserveProjectionRow {
  return {
    ...row,
    closing_balance:
      row.closing_balance ??
      row.balance ??
      row.projected_balance ??
      (row.projected_balance_cents != null ? Number(row.projected_balance_cents) / 100 : undefined),
    contributions:
      row.contributions ?? row.contribution ?? row.annual_contribution ?? row.levy_income ?? undefined,
    capital_works:
      row.expenses ?? row.capital_works ?? row.capital_spend ?? row.projected_expenses ?? undefined,
  };
}

/** Normalise a whole projection. Accepts a missing/!Array payload and yields []. */
export function normaliseReserveProjection(projection: any): ReserveProjectionRow[] {
  if (!Array.isArray(projection)) return [];
  return projection.map(normaliseReserveProjectionRow);
}

/**
 * True when the reserve stays solvent across every projected year.
 *
 * A row whose closing balance is UNKNOWN cannot support a solvency claim in either
 * direction, so it is excluded rather than coerced. Reading an unknown balance as 0 (the
 * previous `?? 0` in OwnerDashboard) silently reported a building as not resilient purely
 * because its forecast had no data — a fabricated negative verdict.
 *
 * Returns `null` when nothing is known, so callers can render "—" instead of a verdict.
 */
export function isReserveResilient(projection: any): boolean | null {
  const known = normaliseReserveProjection(projection)
    .map((r) => r.closing_balance)
    .filter((v): v is number => typeof v === "number");
  if (!known.length) return null;
  return known.every((v) => v > 0);
}
