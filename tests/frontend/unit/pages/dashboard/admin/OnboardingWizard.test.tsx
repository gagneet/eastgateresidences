/**
 * Tests: buildCurrentBalancesFunds (tasks/GAP-ONBOARD-001, Fix 1)
 *
 * Pure-function unit tests — no rendering of the 32-step wizard required.
 * This is the function that turns the "Current Balances" (Track A) manual-entry
 * step's form fields into the `funds` array finalize_onboarding's clean-state
 * genesis-posting path reads. Before this fix, nothing ever built this array,
 * so manually-entered balances were silently discarded (never reached the ledger).
 */
import {
    buildCurrentBalancesFunds,
    buildSchemeStartPayload,
    extractOnboardingErrorDetail,
} from '@/pages/dashboard/admin/OnboardingWizard';

jest.mock('sonner', () => ({toast: {warning: jest.fn(), success: jest.fn(), error: jest.fn()}}));
const {toast} = require('sonner');

describe('buildCurrentBalancesFunds', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('returns null when balance_date is missing (Track B / unfilled Track A)', () => {
        expect(buildCurrentBalancesFunds({admin_fund_balance: '1000'})).toBeNull();
    });

    it('returns null when admin_fund_balance is not a number', () => {
        expect(buildCurrentBalancesFunds({balance_date: '2026-01-01', admin_fund_balance: ''})).toBeNull();
    });

    it('builds a single admin fund entry when sinking balance is absent', () => {
        const result = buildCurrentBalancesFunds({balance_date: '2026-01-01', admin_fund_balance: '2500.00'});
        expect(result).toEqual([
            {fund_type: 'admin', opening_balance_cents: 250000, as_at_date: '2026-01-01'},
        ]);
    });

    it('builds both admin and sinking fund entries when both are present', () => {
        const result = buildCurrentBalancesFunds({
            balance_date: '2026-01-01',
            admin_fund_balance: '2500.00',
            sinking_fund_balance: '1000.00',
        });
        expect(result).toEqual([
            {fund_type: 'admin', opening_balance_cents: 250000, as_at_date: '2026-01-01'},
            {fund_type: 'sinking', opening_balance_cents: 100000, as_at_date: '2026-01-01'},
        ]);
    });

    it('converts dollars to cents via round(), not truncation (avoids float drift)', () => {
        const result = buildCurrentBalancesFunds({balance_date: '2026-01-01', admin_fund_balance: '10.005'});
        // 10.005 * 100 = 1000.4999999999999 in floating point -> must round to 1000, not truncate to 999
        expect(result[0].opening_balance_cents).toBe(1001);
    });

    it('does not warn when trust_balance matches admin + sinking totals', () => {
        buildCurrentBalancesFunds({
            balance_date: '2026-01-01',
            admin_fund_balance: '2500.00',
            sinking_fund_balance: '1000.00',
            trust_balance: '3500.00',
        });
        expect(toast.warning).not.toHaveBeenCalled();
    });

    it('warns (but still returns funds) when trust_balance does not match admin + sinking totals', () => {
        const result = buildCurrentBalancesFunds({
            balance_date: '2026-01-01',
            admin_fund_balance: '2500.00',
            sinking_fund_balance: '1000.00',
            trust_balance: '9999.00',
        });
        expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining("doesn't match"));
        // Reconciliation mismatch is a warning, not a block — funds are still built.
        expect(result).toHaveLength(2);
    });

    it('does not warn over a sub-cent rounding difference (tolerance is 1 cent)', () => {
        buildCurrentBalancesFunds({
            balance_date: '2026-01-01',
            admin_fund_balance: '2500.00',
            trust_balance: '2500.004', // rounds to the same 250000 cents
        });
        expect(toast.warning).not.toHaveBeenCalled();
    });

    it('trust_balance never appears as a third fund row (computed check only, not stored)', () => {
        const result = buildCurrentBalancesFunds({
            balance_date: '2026-01-01',
            admin_fund_balance: '2500.00',
            trust_balance: '2500.00',
        });
        expect(result.map((f: any) => f.fund_type)).toEqual(['admin']);
    });
});

describe('buildSchemeStartPayload', () => {
    it('uses ACT when the jurisdiction select is still on its visual default', () => {
        expect(buildSchemeStartPayload({
            scheme_number: ' 13195 ',
            scheme_name: ' East Gate Residences ',
        })).toEqual({
            scheme_number: '13195',
            scheme_name: 'East Gate Residences',
            jurisdiction: 'ACT',
            tenant_id: undefined,
            abn: undefined,
        });
    });

    it('normalises jurisdiction casing and keeps optional tenant/ABN fields', () => {
        expect(buildSchemeStartPayload({
            scheme_number: 'UP13195',
            scheme_name: 'East Gate Residences',
            jurisdiction: 'nsw',
            tenant_id: 'tenant-1',
            abn: ' 12 345 678 901 ',
        })).toEqual({
            scheme_number: 'UP13195',
            scheme_name: 'East Gate Residences',
            jurisdiction: 'NSW',
            tenant_id: 'tenant-1',
            abn: '12 345 678 901',
        });
    });
});

describe('extractOnboardingErrorDetail', () => {
    it('unwraps backend duplicate-conflict error objects', () => {
        const err = {
            response: {
                data: {
                    detail: {
                        code: 'scheme_number_taken_in_jurisdiction',
                        detail: 'Scheme number already exists.',
                    },
                },
            },
        };
        expect(extractOnboardingErrorDetail(err)).toBe('Scheme number already exists.');
    });

    it('falls back instead of returning an object to React', () => {
        expect(extractOnboardingErrorDetail({response: {data: {detail: {code: 'x'}}}})).toBe(
            'Failed to start onboarding session.',
        );
    });
});
