// @featuretrace:currency-config — The single owner for formatting money in the UI.
// Layer: frontend
// Data flow: GET /buildings/me {currency_code, currency_locale} -> AuthContext -> setActiveCurrency() -> formatMoneyFrom*() (building-scoped).
// Related: backend/utils/currency.py, frontend/src/contexts/AuthContext.tsx, docs/architecture/canonical_owners.yaml

/**
 * Money formatting — one owner, and the UNIT is always explicit.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * There were ten `fmtAUD` functions in this codebase and they disagreed about the
 * unit of their own argument:
 *
 *   lib/utils.ts (the nominal owner)  took DOLLARS, style 'decimal' -> "1,000.00" (no symbol)
 *   LevyScenariosPage, GSTBASLedger…  took DOLLARS                  -> "$1,000.00"
 *   MatchingReview, Reconciliation…   took CENTS                    -> "$10.00"
 *
 * The same call rendered money 100x apart depending on which file it sat in, with
 * nothing at the call site disclosing which one had been imported. That is the
 * duplicate-concept failure recorded in tasks/P0-CANONICAL-OWNER-REGISTRY.md.
 *
 * The fix is not "one function" — it is one function PER UNIT, named so the call
 * site states which it means. `formatMoneyFromCents(176150)` cannot be misread;
 * `fmtAUD(176150)` could mean $1,761.50 or $176,150.00 and did mean both.
 *
 * CURRENCY IS PER BUILDING, NOT PER APP
 * -------------------------------------
 * Amounts are stored in minor units and are currency-agnostic. The currency is a
 * display concern resolved from the building's settings and delivered on
 * /buildings/me as an ISO-4217 code + BCP-47 locale.
 *
 * We never ship a bare "$": it is ambiguous across AUD, NZD, USD, SGD and HKD.
 * `Intl.NumberFormat` derives the symbol, its PLACEMENT, and the grouping and
 * decimal separators from the code+locale pair — "1.234,56 €" is not "$1,234.56"
 * with a different glyph.
 */

export interface CurrencyConfig {
    /** ISO-4217, e.g. "AUD". */
    code: string;
    /** BCP-47, e.g. "en-AU". Drives separators and symbol placement. */
    locale: string;
}

/** Australian strata is the platform default; AUD renders as "$". */
export const DEFAULT_CURRENCY: CurrencyConfig = {code: 'AUD', locale: 'en-AU'};

/**
 * Module-level rather than React context, deliberately.
 *
 * Formatting is called from ~70 files, many of them plain helper functions and
 * table cell renderers that are not components and cannot consume a hook. Threading
 * a context through all of them would guarantee that some call sites keep a local
 * fallback — which is exactly the fragmentation this module exists to end.
 *
 * Exactly one building is in context at a time, so a module-level value is a
 * faithful model of the state. `setActiveCurrency` is called by AuthContext when
 * the building resolves or changes.
 */
let active: CurrencyConfig = {...DEFAULT_CURRENCY};

/** Set the currency for the building currently in context. */
export function setActiveCurrency(config: Partial<CurrencyConfig> | null | undefined): void {
    if (!config?.code) {
        active = {...DEFAULT_CURRENCY};
        return;
    }
    const code = String(config.code).toUpperCase();
    // Validate before storing. Intl.NumberFormat throws a RangeError on an unknown
    // currency, and an exception inside a table cell renderer blanks the whole
    // page — a bad setting must degrade to the default, never to a crash.
    const locale = config.locale || DEFAULT_CURRENCY.locale;
    try {
        new Intl.NumberFormat(locale, {style: 'currency', currency: code}).format(0);
    } catch {
        active = {...DEFAULT_CURRENCY};
        return;
    }
    active = {code, locale};
}

export function getActiveCurrency(): CurrencyConfig {
    return active;
}

/** What a missing value renders as. NEVER "0.00" — zero and missing are distinct
 *  states, and showing an unknown balance as zero is a documented finance rule
 *  violation, not a cosmetic choice. */
export const MISSING_MONEY = '—';

interface FormatOptions {
    /** Override the building's currency for one call (rare — cross-currency views). */
    currency?: Partial<CurrencyConfig>;
    /** What to render when the value is null/undefined/NaN. Defaults to "—". */
    missing?: string;
    /** Drop the decimal places, e.g. for axis ticks. */
    whole?: boolean;
}

function resolve(override?: Partial<CurrencyConfig>): CurrencyConfig {
    if (!override?.code) return active;
    return {code: String(override.code).toUpperCase(), locale: override.locale || active.locale};
}

function format(amount: number, opts?: FormatOptions): string {
    const {code, locale} = resolve(opts?.currency);
    const digits = opts?.whole ? 0 : 2;
    try {
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: code,
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
        }).format(amount);
    } catch {
        // Last-resort fallback: never throw out of a formatter.
        return new Intl.NumberFormat(DEFAULT_CURRENCY.locale, {
            style: 'currency',
            currency: DEFAULT_CURRENCY.code,
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
        }).format(amount);
    }
}

function toNumber(value: number | string | null | undefined): number | null {
    if (value === null || value === undefined || value === '') return null;
    const n = typeof value === 'string' ? Number(value) : value;
    return Number.isFinite(n) ? n : null;
}

/**
 * Format an amount held in MINOR UNITS (cents) — the storage form of every
 * ledger-adjacent value in this system.
 *
 *   formatMoneyFromCents(176150) -> "$1,761.50"
 */
export function formatMoneyFromCents(
    cents: number | string | null | undefined,
    opts?: FormatOptions,
): string {
    const n = toNumber(cents);
    if (n === null) return opts?.missing ?? MISSING_MONEY;
    return format(n / 100, opts);
}

/**
 * Format an amount already expressed in MAJOR UNITS (dollars).
 *
 * Needed because several stored fields are documented dollar floats rather than
 * cents (`unit_levy_ledger.admin_levied`, `annual_levies` fund totals), and
 * budget/scenario endpoints return dollars.
 *
 *   formatMoneyFromDollars(1761.5) -> "$1,761.50"
 */
export function formatMoneyFromDollars(
    dollars: number | string | null | undefined,
    opts?: FormatOptions,
): string {
    const n = toNumber(dollars);
    if (n === null) return opts?.missing ?? MISSING_MONEY;
    return format(n, opts);
}

/** The bare currency symbol, for axis labels and input adornments where a full
 *  formatted amount does not fit. Derived, never hardcoded. */
export function currencySymbol(override?: Partial<CurrencyConfig>): string {
    const {code, locale} = resolve(override);
    try {
        return (
            new Intl.NumberFormat(locale, {style: 'currency', currency: code})
                .formatToParts(0)
                .find((part) => part.type === 'currency')?.value ?? '$'
        );
    } catch {
        return '$';
    }
}

/**
 * Compact form for chart axes: "$164k", "$1.2M".
 *
 * Replaces the hand-rolled `` `$${(v / 1000).toFixed(0)}k` `` pattern repeated
 * across chart components, which hardcoded both the symbol and the scale.
 */
export function formatMoneyCompact(
    dollars: number | string | null | undefined,
    opts?: FormatOptions,
): string {
    const n = toNumber(dollars);
    if (n === null) return opts?.missing ?? MISSING_MONEY;
    const {code, locale} = resolve(opts?.currency);
    try {
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: code,
            notation: 'compact',
            maximumFractionDigits: 1,
        }).format(n);
    } catch {
        return format(n, opts);
    }
}
