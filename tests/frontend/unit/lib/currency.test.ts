/**
 * Money formatting owner — regression coverage.
 *
 * There were ten `fmtAUD` functions disagreeing about the unit of their own
 * argument, so the same call rendered money 100x apart depending on the file. These
 * tests pin the two properties that made that possible: the unit is in the NAME,
 * and a missing value is never rendered as zero.
 */
import {
    DEFAULT_CURRENCY,
    MISSING_MONEY,
    currencySymbol,
    formatMoneyCompact,
    formatMoneyFromCents,
    formatMoneyFromDollars,
    getActiveCurrency,
    setActiveCurrency,
} from '@/lib/currency';

// Non-breaking spaces appear in some locale outputs; normalise for comparison.
const norm = (s: string) => s.replace(/ /g, ' ');

describe('currency owner', () => {
    beforeEach(() => setActiveCurrency(DEFAULT_CURRENCY));

    it('states the unit in the function name — the 100x bug', () => {
        // The exact ambiguity that shipped: one value, two legitimate readings.
        expect(norm(formatMoneyFromCents(176150))).toBe('$1,761.50');
        expect(norm(formatMoneyFromDollars(176150))).toBe('$176,150.00');
    });

    it('renders a missing value as "—", never as zero', () => {
        for (const empty of [null, undefined, '', NaN]) {
            expect(formatMoneyFromCents(empty as never)).toBe(MISSING_MONEY);
            expect(formatMoneyFromDollars(empty as never)).toBe(MISSING_MONEY);
        }
        // A real zero is still a real value and must be shown.
        expect(norm(formatMoneyFromCents(0))).toBe('$0.00');
        expect(norm(formatMoneyFromDollars(0))).toBe('$0.00');
    });

    it('accepts numeric strings, since several APIs return them', () => {
        expect(norm(formatMoneyFromCents('176150'))).toBe('$1,761.50');
        expect(norm(formatMoneyFromDollars('1761.5'))).toBe('$1,761.50');
        expect(formatMoneyFromDollars('not-a-number')).toBe(MISSING_MONEY);
    });

    it('formats negatives (credits) rather than dropping the sign', () => {
        expect(norm(formatMoneyFromCents(-19000))).toContain('190.00');
        expect(norm(formatMoneyFromCents(-19000))).toMatch(/^-?\$|\(/);
    });

    describe('per-building currency', () => {
        it('uses the building\'s configured currency and locale', () => {
            setActiveCurrency({code: 'NZD', locale: 'en-NZ'});
            expect(getActiveCurrency().code).toBe('NZD');
            expect(norm(formatMoneyFromCents(176150))).toContain('1,761.50');

            setActiveCurrency({code: 'GBP', locale: 'en-GB'});
            expect(norm(formatMoneyFromCents(176150))).toBe('£1,761.50');
        });

        it('derives the symbol instead of hardcoding "$"', () => {
            setActiveCurrency(DEFAULT_CURRENCY);
            expect(currencySymbol()).toBe('$');
            setActiveCurrency({code: 'GBP', locale: 'en-GB'});
            expect(currencySymbol()).toBe('£');
            setActiveCurrency({code: 'EUR', locale: 'en-IE'});
            expect(currencySymbol()).toBe('€');
        });

        it('falls back to the default on an invalid currency instead of throwing', () => {
            // Intl.NumberFormat throws RangeError on an unknown currency, and an
            // exception in a table cell renderer blanks the whole page.
            setActiveCurrency({code: 'NOPE', locale: 'en-AU'});
            expect(getActiveCurrency()).toEqual(DEFAULT_CURRENCY);
            expect(norm(formatMoneyFromCents(176150))).toBe('$1,761.50');
        });

        it('treats a missing config as the default rather than clearing currency', () => {
            setActiveCurrency({code: 'GBP', locale: 'en-GB'});
            setActiveCurrency(null);
            expect(getActiveCurrency()).toEqual(DEFAULT_CURRENCY);
        });

        it('supports a per-call override for cross-currency views', () => {
            setActiveCurrency(DEFAULT_CURRENCY);
            expect(norm(formatMoneyFromCents(176150, {currency: {code: 'GBP', locale: 'en-GB'}})))
                .toBe('£1,761.50');
            // The override must not leak into subsequent calls.
            expect(norm(formatMoneyFromCents(176150))).toBe('$1,761.50');
        });
    });

    it('compact form is for axes and still respects the currency', () => {
        setActiveCurrency(DEFAULT_CURRENCY);
        expect(norm(formatMoneyCompact(164254))).toBe('$164.3K');
        expect(formatMoneyCompact(null)).toBe(MISSING_MONEY);
    });
});
