import {firstMoneyValue, fundDisplayBalance, parseMoneyValue} from '@/lib/moneyNormalization';

describe('moneyNormalization', () => {
    it('parses currency-formatted strings', () => {
        expect(parseMoneyValue('$216,299.00')).toBe(216299);
    });

    it('returns first parseable value', () => {
        expect(firstMoneyValue(undefined, null, '$12.34', 99)).toBe(12.34);
    });

    it('prefers explicit current balance before projected closing balance', () => {
        expect(fundDisplayBalance({current_balance: 9187.44, closing_balance: 180000})).toBe(9187.44);
    });

    it('falls back to closing balance when current balance is unavailable', () => {
        expect(fundDisplayBalance({closing_balance: '$1,234.56', closing_balance_cents: 10})).toBe(1234.56);
    });

    it('falls back to current cents before closing cents', () => {
        expect(fundDisplayBalance({current_balance_cents: 918744, closing_balance_cents: 18000000})).toBe(9187.44);
    });

    it('falls back to closing cents when no current cents exist', () => {
        expect(fundDisplayBalance({closing_balance_cents: 41561400})).toBe(415614);
    });

    it('returns zero when no valid fund balance exists', () => {
        expect(fundDisplayBalance({closing_balance: 'bad', closing_balance_cents: 'nan'})).toBe(0);
    });
});
