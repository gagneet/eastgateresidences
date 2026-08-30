export type FinanceChartData = {
    expense_by_category: any[];
    income_by_category: any[];
    monthly_trend: any[];
};

export type BudgetVsActualData = {
    administrative: any[];
    sinking: any[];
};

export type LedgerAvailability = 'ledger' | 'unit-list-placeholder' | 'empty';

export type LedgerRowsResult = {
    rows: any[];
    availability: LedgerAvailability;
    warning?: string;
};

export type CollectionRateSummary = {
    annualLevyTotal: number;
    totalCollectedYtd: number;
    collectionRate: number;
    inGraceAmount: number;
    trueArrearsAmount: number;
    inGracePct: number;
    arrearsPct: number;
    notYetDueAmount: number;
    notYetDuePct: number;
    // GAP-FIN-035 (2026-08-03): unapplied credit + receipts allocated to
    // charges whose due date is after today. A DISTINCT metric from
    // collectionRate above — never fold this into it or into notYetDueAmount.
    // See docs/architecture/financial-summary-analysis-of-issues.md Rule 53.
    collectedInAdvance: number;
    source: 'kpi-contract' | 'legacy-summary';
};

function numeric(value: unknown): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
}

export function collectionPercentage(numerator: unknown, denominator: unknown): number {
    const denom = numeric(denominator);
    if (denom <= 0) return 0;
    return (numeric(numerator) / denom) * 100;
}

// Keep this separate from collectionPercentage(): collection-rate displays can
// legitimately exceed 100% for overpaid/prepaid units, while progress bars and
// health widgets need bounded percentages.
export function clampedCollectionPercentage(numerator: unknown, denominator: unknown): number {
    return Math.max(0, Math.min(100, collectionPercentage(numerator, denominator)));
}

export function buildCollectionRateSummary(stats: any, kpiContract: any): CollectionRateSummary {
    const ledgerSummary = stats?.unit_ledger_summary || {};
    const inGraceSummary = stats?.in_grace_summary || {};
    const collectionMix = kpiContract?.collection_mix || null;

    if (collectionMix) {
        const annualLevyTotal = numeric(collectionMix.ledger_levied_ytd);
        const totalCollectedYtd = numeric(collectionMix.ledger_collected_ytd ?? collectionMix.ledger_paid_ytd);
        const inGraceAmount = numeric(collectionMix.in_grace_amount);
        const trueArrearsAmount = numeric(collectionMix.true_arrears_amount);
        const notYetDueAmount = numeric(collectionMix.not_yet_due_amount);

        return {
            annualLevyTotal,
            totalCollectedYtd,
            collectionRate: collectionPercentage(totalCollectedYtd, annualLevyTotal),
            inGraceAmount,
            trueArrearsAmount,
            inGracePct: collectionPercentage(inGraceAmount, annualLevyTotal),
            arrearsPct: collectionPercentage(trueArrearsAmount, annualLevyTotal),
            notYetDueAmount,
            notYetDuePct: collectionPercentage(notYetDueAmount, annualLevyTotal),
            collectedInAdvance: numeric(collectionMix.collected_in_advance),
            source: 'kpi-contract',
        };
    }

    const annualLevyTotal = numeric(ledgerSummary.annual_levy_total || ledgerSummary.total_levied);
    const totalCollectedYtd = numeric(
        ledgerSummary.total_collected_ytd ??
        (numeric(ledgerSummary.total_levied) - numeric(ledgerSummary.net_balance)),
    );
    const inGraceAmount = numeric(inGraceSummary.in_grace_amount);
    const trueArrearsAmount = numeric(
        inGraceSummary.true_arrears_amount ?? ledgerSummary.total_outstanding,
    );
    const notYetDueAmount = Math.max(
        0,
        annualLevyTotal - totalCollectedYtd - inGraceAmount - trueArrearsAmount,
    );

    return {
        annualLevyTotal,
        totalCollectedYtd,
        collectionRate: collectionPercentage(totalCollectedYtd, annualLevyTotal),
        inGraceAmount,
        trueArrearsAmount,
        inGracePct: collectionPercentage(inGraceAmount, annualLevyTotal),
        arrearsPct: collectionPercentage(trueArrearsAmount, annualLevyTotal),
        notYetDueAmount,
        notYetDuePct: collectionPercentage(notYetDueAmount, annualLevyTotal),
        collectedInAdvance: numeric(ledgerSummary.collected_in_advance),
        source: 'legacy-summary',
    };
}
/**
 * Normalize the payload from `GET /finance/charts` into a flat
 * {@link FinanceChartData} shape for chart components. Tolerates two legacy
 * response shapes for `expense_by_category` (a flat array, or an
 * `{administrative, sinking}` split object) and falls back from
 * `monthly_trend` to `quarterly_trend` when the backend hasn't computed a
 * monthly series. Always returns arrays (never undefined) so chart
 * components can render without null checks.
 */
export function normalizeFinanceChartData(raw: any): FinanceChartData {
    const expenseByCategory = raw?.expense_by_category;
    const flatExpenses = Array.isArray(expenseByCategory)
        ? expenseByCategory
        : [
            ...(expenseByCategory?.administrative || []),
            ...(expenseByCategory?.sinking || []),
        ];

    return {
        expense_by_category: flatExpenses,
        income_by_category: Array.isArray(raw?.income_by_category) ? raw.income_by_category : [],
        monthly_trend: Array.isArray(raw?.monthly_trend)
            ? raw.monthly_trend
            : (Array.isArray(raw?.quarterly_trend) ? raw.quarterly_trend : []),
    };
}
/**
 * Normalize the payload from `GET /finance/budget-vs-actual` into the
 * `{administrative, sinking}` array shape the Budget vs Actual chart
 * expects, defaulting missing/malformed fund arrays to `[]`.
 */
export function normalizeBudgetVsActualData(raw: any): BudgetVsActualData {
    return {
        administrative: Array.isArray(raw?.administrative) ? raw.administrative : [],
        sinking: Array.isArray(raw?.sinking) ? raw.sinking : [],
    };
}
/**
 * Prefer explicit annual proposed fields. Raw annual_levies total_income/levy_income can be YTD actuals
 * for partial years, so dashboards should not silently show them as the annual target.
 */
export function getAnnualProposedFundIncome(fund: any): number {
    return Number(
        fund?.annual_levy_proposed ??
        fund?.proposed_income ??
        0
    ) || 0;
}
/**
 * Read a fund's year-to-date *actual* income (as opposed to
 * {@link getAnnualProposedFundIncome}'s annual target), preferring the
 * explicit `ytd_total_income` field and falling back to the legacy
 * `total_income` field used by older `annual_levies.*_fund` documents.
 */
export function getYtdFundIncome(fund: any): number {
    return Number(
        fund?.ytd_total_income ??
        fund?.total_income ??
        0
    ) || 0;
}
/**
 * Merge `unit_levy_ledger` entries with unit/owner metadata for the ledger
 * table, with a three-tier fallback so the table never silently renders
 * nothing:
 *
 * 1. `'ledger'` — real ledger rows exist; owner name/owner_name_b are
 *    backfilled from the matching unit record when the ledger row itself
 *    doesn't carry them.
 * 2. `'unit-list-placeholder'` — no ledger rows for the selected year, but
 *    units exist; renders the unit list with all money fields zeroed and
 *    `__ledger_placeholder: true` so callers can visually distinguish
 *    "genuinely zero" from "no ledger built yet".
 * 3. `'empty'` — neither ledger rows nor units were returned; likely an
 *    upstream data/API failure rather than a legitimately empty building.
 *
 * @param entries - raw rows from `GET /unit-levy-ledger`.
 * @param allUnits - raw rows from the units list endpoint, used for name backfill and the placeholder tier.
 */
export function normalizeLevyLedgerRows(entries: any[], allUnits: any[]): LedgerRowsResult {
    if (Array.isArray(entries) && entries.length > 0) {
        const unitsByNumber = new Map(
            (Array.isArray(allUnits) ? allUnits : []).map((unit: any) => [unit.unit_number, unit]),
        );
        return {
            availability: 'ledger',
            rows: entries.map((entry: any) => {
                const unit = unitsByNumber.get(entry.unit_number);
                return {
                    ...entry,
                    owner_name: entry.owner_name || unit?.owner_name || unit?.owner || '',
                    owner_name_b: entry.owner_name_b || unit?.owner_name_b || '',
                    __ledger_placeholder: false,
                };
            }),
        };
    }

    if (Array.isArray(allUnits) && allUnits.length > 0) {
        return {
            availability: 'unit-list-placeholder',
            warning: 'No unit levy ledger records exist for the selected year. Unit list is displayed for reference only; financial values are zero placeholders.',
            rows: allUnits.map((unit: any) => ({
                ...unit,
                total_levied: 0,
                total_paid: 0,
                net_balance: 0,
                opening_arrears: 0,
                admin_levied: 0,
                admin_paid: 0,
                sinking_levied: 0,
                sinking_paid: 0,
                owner_name: unit.owner_name || unit.owner || '',
                owner_name_b: unit.owner_name_b || '',
                __ledger_placeholder: true,
            })),
        };
    }

    return {
        availability: 'empty',
        rows: [],
        warning: 'No unit levy ledger or unit metadata records were returned for the selected year.',
    };
}
