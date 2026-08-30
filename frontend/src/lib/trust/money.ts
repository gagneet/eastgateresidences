import {formatMoneyFromCents} from '../currency';

/**
 * MONETARY UTILITIES — CENTS-BASED ARITHMETIC
 *
 * RULE: All monetary values in the system are integer cents (whole numbers).
 *       Dollars appear only at the UI boundary (formatting/parsing).
 *       Never use native JS arithmetic on dollar floats.
 */

/**
 * Convert dollars to integer cents.
 * dollarsToCents(866.53)      → 86653
 * dollarsToCents("866.53")    → 86653
 * dollarsToCents("$9,187.44") → 918744
 * dollarsToCents(0.005)       → 1  (rounds half-up)
 */
export function dollarsToCents(dollars: number | string): number {
    if (typeof dollars === 'string') {
        // Remove currency symbols, commas, spaces
        dollars = dollars.replace(/[$,\s]/g, '');
    }
    const num = typeof dollars === 'string' ? parseFloat(dollars) : dollars;
    if (isNaN(num)) return 0;
    return Math.round(num * 100);
}
/**
 * Convert integer cents to number of dollars.
 * centsToDollars(86653) → 866.53
 */
export function centsToDollars(cents: number): number {
    return cents / 100;
}
/**
 * Format cents as AUD display string.
 * formatAUD(918744) → "$9,187.44"
 * formatAUD(0)      → "$0.00"
 * formatAUD(-100)   → "-$1.00"
 */
export function formatAUD(cents: number): string {
    // Delegates to the canonical owner. This hand-rolled version built the string
    // itself, which hardcoded both the "$" and the en-AU grouping and so could not
    // follow a building's configured currency. Kept as a thin alias because this
    // module is the trust-money vocabulary; new code should call
    // formatMoneyFromCents directly.
    return formatMoneyFromCents(cents);
}
/**
 * Parse AUD display string to integer cents.
 * parseAUD("$9,187.44") → 918744
 * parseAUD("-$1.00")    → -100
 */
export function parseAUD(value: string): number {
    const negative = value.trim().startsWith('-');
    const cleaned = value.replace(/[-$,\s]/g, '');
    const num = parseFloat(cleaned);
    if (isNaN(num)) return 0;
    return (negative ? -1 : 1) * Math.round(num * 100);
}
/**
 * Safe integer addition of multiple cent values.
 * addCents(86653, 25295) → 111948
 */
export function addCents(...values: number[]): number {
    return values.reduce((sum, v) => sum + Math.round(v), 0);
}
/**
 * Proportional split: distribute totalCents across weights using largest-remainder method.
 * The result array sums EXACTLY to totalCents — no rounding drift.
 *
 * proportionalSplit(100, [1, 1, 1]) → [34, 33, 33]  (sums to 100)
 */
export function proportionalSplit(totalCents: number, weights: number[]): number[] {
    if (weights.length === 0) return [];
    const totalWeight = weights.reduce((s, w) => s + w, 0);
    if (totalWeight === 0) return weights.map(() => 0);

    // Compute exact shares
    const exactShares = weights.map(w => (totalCents * w) / totalWeight);
    // Floor each share
    const floored = exactShares.map(s => Math.floor(s));
    // Calculate remainders for largest-remainder method
    const remainders = exactShares.map((s, i) => s - floored[i]);
    // How many extra cents to distribute?
    const distributed = floored.reduce((s, v) => s + v, 0);
    const extra = totalCents - distributed;

    // Sort indices by remainder descending, give 1 extra cent to top `extra` items
    const indices = remainders
        .map((r, i) => ({r, i}))
        .sort((a, b) => b.r - a.r)
        .map(x => x.i);

    const result = [...floored];
    for (let k = 0; k < extra; k++) {
        result[indices[k]] += 1;
    }

    return result;
}
/**
 * Calculate the levy for one unit given quarterly budget, unit UOE, and total building UOE.
 * levyForUnit(8521750, 111, 10916) → 86653 cents ($866.53)
 */
export function levyForUnit(
    quarterlyBudgetCents: number,
    unitUoe: number,
    totalUoe: number,
): number {
    if (totalUoe === 0) return 0;
    return Math.floor((quarterlyBudgetCents * unitUoe) / totalUoe);
}
/**
 * Calculate interest accrued on overdue principal at the building's configured rate.
 * Rate is per-annum decimal (e.g. 0.10 = 10% pa).
 * interestAccrued(100000, 0.10, from, to) with 30 days → 822 cents
 */
export function interestAccrued(
    principalCents: number,
    annualRate: number,
    fromDate: Date,
    toDate?: Date,
): number {
    const to = toDate ?? new Date();
    const diffMs = to.getTime() - fromDate.getTime();
    const days = diffMs / (1000 * 60 * 60 * 24);
    if (days <= 0) return 0;
    // Day count convention: simple 365-day year (Act/365 Fixed).
    // The annual rate comes from building.trust_config — per-building, not a global constant.
    // State legislations vary; configure the correct rate per building.
    return Math.round(principalCents * annualRate * days / 365);
}
/**
 * Validate that a value is a safe non-negative integer (valid cents value).
 */
export function isValidCents(value: unknown): value is number {
    return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}
/**
 * Build an API response object with both cents and display variants.
 * toMoneyResponse(86653) → { cents: 86653, display: "$866.53" }
 */
export function toMoneyResponse(cents: number): { cents: number; display: string } {
    return {cents, display: formatAUD(cents)};
}
