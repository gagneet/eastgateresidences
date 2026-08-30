// @featuretrace:financial-ui-calculation-register — Frontend label helpers for registry-backed finance display labels.
// Data flow: finance UI pages -> labelsFromCalculationRegistry/findFinanceCalculationGroup -> GET /finance/calculation-registry (scope param: building|global).
// Related: backend/services/finance_calculation_registry.py, tests/frontend/unit/unit/levyDisplay.test.ts
import {formatCurrency} from '../utils';

export const GST_RATE = 0.10;

export type LevyAmountMode = 'ex-gst' | 'incl-gst';

export interface LevyBreakdownExGST {
    adminExGST: number;
    sinkingExGST: number;
    gst: number;
    totalInclGST: number;
    mode: 'ex-gst';
}

export interface LevyBreakdownInclGST {
    adminInclGST: number;
    sinkingInclGST: number;
    gst: number;
    totalInclGST: number;
    mode: 'incl-gst';
}
/** Round a dollar amount to 2 decimal places, coercing non-numeric/null input to 0. */
function roundMoney(value: number): number {
    return Math.round((Number(value) || 0) * 100) / 100;
}
/**
 * Return the GST rate to apply for display purposes: the building's actual
 * rate when GST-registered (per `settings.gst_registered`), or 0 when not
 * registered — so un-registered buildings never show a phantom GST line.
 */
export function gstRateForDisplay(gstRate: number = GST_RATE, gstRegistered: boolean = true): number {
    return gstRegistered ? gstRate : 0;
}
/** Apply GST to a GST-exclusive amount using {@link gstRateForDisplay}, rounded to cents. */
export function withGST(
    amountExGST: number | null | undefined,
    gstRate: number = GST_RATE,
    gstRegistered: boolean = true,
): number {
    return roundMoney((amountExGST ?? 0) * (1 + gstRateForDisplay(gstRate, gstRegistered)));
}
/** {@link withGST} formatted as a currency string via `formatCurrency`. */
export function formatLevyInclGST(
    amountExGST: number | null | undefined,
    gstRate: number = GST_RATE,
    gstRegistered: boolean = true,
): string {
    return formatCurrency(withGST(amountExGST, gstRate, gstRegistered));
}
/**
 * Treasurer/admin finance screens generally display fund split amounts as GST-exclusive,
 * with GST as its own line and the payable owner total as GST-inclusive.
 */
export function levyBreakdownExGST(
    adminExGST: number,
    sinkingExGST: number,
    gstRate: number = GST_RATE,
    gstRegistered: boolean = true,
): LevyBreakdownExGST {
    const effectiveRate = gstRateForDisplay(gstRate, gstRegistered);
    const admin = roundMoney(adminExGST);
    const sinking = roundMoney(sinkingExGST);
    const gst = roundMoney((admin + sinking) * effectiveRate);
    return {
        adminExGST: admin,
        sinkingExGST: sinking,
        gst,
        totalInclGST: roundMoney(admin + sinking + gst),
        mode: 'ex-gst',
    };
}
/**
 * Owner-facing levy notices/payment pages can display both fund split lines as inclusive.
 */
export function levyBreakdownInclGST(
    adminExGST: number,
    sinkingExGST: number,
    gstRate: number = GST_RATE,
    gstRegistered: boolean = true,
): LevyBreakdownInclGST {
    const admin = roundMoney(adminExGST);
    const sinking = roundMoney(sinkingExGST);
    const adminInclGST = withGST(adminExGST, gstRate, gstRegistered);
    const sinkingInclGST = withGST(sinkingExGST, gstRate, gstRegistered);
    const totalInclGST = roundMoney(adminInclGST + sinkingInclGST);
    const gst = roundMoney(totalInclGST - admin - sinking);
    return {
        adminInclGST,
        sinkingInclGST,
        gst,
        totalInclGST,
        mode: 'incl-gst',
    };
}

/**
 * Clear semantic labels for finance dashboard cards.
 * These avoid mixing ledger payments, portal-confirmed payments and net-balance-derived collection.
 */
export const FINANCE_DISPLAY_LABELS = {
    ledgerPaid: 'Ledger Payments Recorded',
    portalConfirmed: 'Portal/Stripe Confirmed Only',
    effectiveCollection: 'Effective Collection vs Obligations',
    outstandingBalance: 'Current Positive Ledger Balance',
    annualLevyProposed: 'Annual Levy Proposed',
    ytdActual: 'YTD Actual',
} as const;

export type FinanceCalculationRegistryGroup = {
    canonical_key: string;
    canonical_label: string;
    similar_labels?: string[];
    calculation_id?: string;
    surfaces?: string[];
    backend_routes?: string[];
    sources?: {
        mongodb?: string[];
        postgres?: string[];
    };
    cache_strategy?: string;
    display_guidance?: string;
};

export type FinanceCalculationRegistry = {
    version: string;
    status: string;
    implementation_scope?: {
        completed?: string[];
        not_completed?: string[];
    };
    verification?: Record<string, string>;
    common_label_groups: FinanceCalculationRegistryGroup[];
    calculation_definitions?: Array<Record<string, unknown>>;
    cache_invalidation_events_for_future_value_cache?: string[];
    merge_path?: Array<Record<string, unknown>>;
    metadata?: Record<string, unknown>;
};

export const FINANCE_REGISTRY_LABEL_ALIASES: Record<string, keyof typeof FINANCE_DISPLAY_LABELS> = {
    'levy.ledger_paid': 'ledgerPaid',
    'levy.collection_rate': 'effectiveCollection',
    'levy.outstanding_balance': 'outstandingBalance',
    'levy.annual_commitment': 'annualLevyProposed',
};
/**
 * Merge the local {@link FINANCE_DISPLAY_LABELS} defaults with labels
 * pulled live from `GET /finance/calculation-registry`
 * (backend/services/finance_calculation_registry.py), so pages that adopt
 * the registry show the canonical label wording without a redeploy.
 * Falls back entirely to the local defaults if the registry hasn't loaded
 * (`registry` is null/undefined) — callers do not need a loading guard.
 */
export function labelsFromCalculationRegistry(
    registry: FinanceCalculationRegistry | null | undefined,
): Record<string, string> {
    const labels: Record<string, string> = {...FINANCE_DISPLAY_LABELS};
    for (const group of registry?.common_label_groups || []) {
        const alias = FINANCE_REGISTRY_LABEL_ALIASES[group.canonical_key];
        if (alias && group.canonical_label) {
            labels[alias] = group.canonical_label;
        }
        if (group.canonical_key && group.canonical_label) {
            labels[group.canonical_key] = group.canonical_label;
        }
    }
    return labels;
}
/**
 * Look up one canonical label group (formula, surfaces, Mongo/Postgres
 * source fields) from a loaded {@link FinanceCalculationRegistry} by its
 * `canonical_key` (e.g. `'levy.outstanding_balance'`). Returns `undefined`
 * if the registry hasn't loaded or the key doesn't exist — callers should
 * treat a miss as "no registry guidance available", not as an error.
 */
export function findFinanceCalculationGroup(
    registry: FinanceCalculationRegistry | null | undefined,
    canonicalKey: string,
): FinanceCalculationRegistryGroup | undefined {
    return registry?.common_label_groups?.find((group) => group.canonical_key === canonicalKey);
}
