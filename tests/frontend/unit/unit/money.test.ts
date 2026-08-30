import {
    addCents,
    centsToDollars,
    dollarsToCents,
    formatAUD,
    interestAccrued,
    isValidCents,
    levyForUnit,
    parseAUD,
    proportionalSplit,
    toMoneyResponse,
} from '@/lib/trust/money';

describe('dollarsToCents', () => {
    it('converts number', () => expect(dollarsToCents(866.53)).toBe(86653));
    it('converts string number', () => expect(dollarsToCents('866.53')).toBe(86653));
    it('converts dollar-formatted string', () => expect(dollarsToCents('$9,187.44')).toBe(918744));
    it('converts large formatted string', () => expect(dollarsToCents('$193,337.03')).toBe(19333703));
    it('rounds half-up', () => expect(dollarsToCents(0.005)).toBe(1));
    it('handles zero', () => expect(dollarsToCents(0)).toBe(0));
});

describe('centsToDollars', () => {
    it('converts cents to dollars', () => expect(centsToDollars(86653)).toBeCloseTo(866.53, 2));
    it('converts large cents', () => expect(centsToDollars(918744)).toBeCloseTo(9187.44, 2));
});

describe('formatAUD', () => {
    it('formats zero', () => expect(formatAUD(0)).toBe('$0.00'));
    it('formats positive', () => expect(formatAUD(918744)).toBe('$9,187.44'));
    it('formats negative', () => expect(formatAUD(-100)).toBe('-$1.00'));
    it('formats small amount', () => expect(formatAUD(86653)).toBe('$866.53'));
});

describe('parseAUD', () => {
    it('parses formatted string', () => expect(parseAUD('$9,187.44')).toBe(918744));
    it('parses plain string', () => expect(parseAUD('9187.44')).toBe(918744));
    it('parses negative', () => expect(parseAUD('-$1.00')).toBe(-100));
});

describe('addCents', () => {
    it('adds without float drift', () => expect(addCents(86653, 25295)).toBe(111948));
    it('adds multiple', () => expect(addCents(100, 200, 300)).toBe(600));
});

describe('isValidCents', () => {
    it('rejects negative', () => expect(isValidCents(-1)).toBe(false));
    it('rejects float', () => expect(isValidCents(1.5)).toBe(false));
    it('accepts zero', () => expect(isValidCents(0)).toBe(true));
    it('accepts positive integer', () => expect(isValidCents(100)).toBe(true));
    it('rejects string', () => expect(isValidCents('100')).toBe(false));
});

describe('proportionalSplit', () => {
    it('sums exactly to totalCents with equal weights', () => {
        const result = proportionalSplit(100, [1, 1, 1]);
        expect(result.reduce((s, v) => s + v, 0)).toBe(100);
    });
    it('three equal parts sum to 100', () => {
        const result = proportionalSplit(100, [1, 1, 1]);
        expect(result.reduce((a, b) => a + b, 0)).toBe(100);
    });
    it('sums exactly to totalCents with unequal weights', () => {
        const weights = [82, 111, 111, 139, 149];
        const total = 8521750;
        const result = proportionalSplit(total, weights);
        expect(result.reduce((s, v) => s + v, 0)).toBe(total);
    });
    it('handles empty weights', () => {
        expect(proportionalSplit(100, [])).toEqual([]);
    });
    it('big building sum is exact', () => {
        // 87-unit East Gate scenario: 4×82 + 48×111 + 15×139 + 3×149 + 9×160 + 8×161 = 87 units
        const uoes = [82, 82, 82, 82, ...Array(48).fill(111), ...Array(15).fill(139), 149, 149, 149, ...Array(9).fill(160), ...Array(8).fill(161)];
        const total = 8521750;
        const result = proportionalSplit(total, uoes);
        expect(result.reduce((s, v) => s + v, 0)).toBe(total);
    });
});

describe('levyForUnit', () => {
    it('calculates correctly for equal UOEs', () => {
        // 3 units each with UOE 10, total 30, budget 400000
        expect(levyForUnit(400000, 10, 30)).toBe(133333);
    });
    it('returns 0 if totalUoe is 0', () => {
        expect(levyForUnit(400000, 10, 0)).toBe(0);
    });
});

describe('interestAccrued', () => {
    it('calculates 30 days at 10% on $1000', () => {
        const from = new Date('2026-01-01');
        const to = new Date('2026-01-31'); // 30 days
        expect(interestAccrued(100000, 0.10, from, to)).toBe(822);
    });
    it('calculates 30 days at 8% on $1000', () => {
        const from = new Date('2026-01-01');
        const to = new Date('2026-01-31');
        expect(interestAccrued(100000, 0.08, from, to)).toBe(658);
    });
    it('uses different rates — different results', () => {
        const from = new Date('2026-01-01');
        const to = new Date('2026-01-31');
        const r1 = interestAccrued(100000, 0.10, from, to);
        const r2 = interestAccrued(100000, 0.08, from, to);
        expect(r1).not.toBe(r2);
    });
    it('returns 0 for zero days', () => {
        const now = new Date();
        expect(interestAccrued(100000, 0.10, now, now)).toBe(0);
    });
});

describe('toMoneyResponse', () => {
    it('returns correct shape', () => {
        expect(toMoneyResponse(86653)).toEqual({cents: 86653, display: '$866.53'});
    });
});
