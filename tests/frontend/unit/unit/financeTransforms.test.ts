import {
    buildCollectionRateSummary,
    clampedCollectionPercentage,
    collectionPercentage,
    normalizeLevyLedgerRows,
} from '@/lib/finance/financeTransforms';

describe('normalizeLevyLedgerRows', () => {
    it('fills missing owner details from the matching unit metadata', () => {
        const result = normalizeLevyLedgerRows(
            [
                {unit_number: '101', total_paid: 10},
                {unit_number: '102', owner_name: 'Existing Owner', total_paid: 20},
            ],
            [
                {unit_number: '101', owner_name: 'Owner A', owner_name_b: 'Owner B'},
                {unit_number: '102', owner_name: 'Fallback Owner', owner_name_b: 'Fallback Co-owner'},
            ],
        );

        expect(result).toEqual({
            availability: 'ledger',
            rows: [
                {
                    unit_number: '101',
                    total_paid: 10,
                    owner_name: 'Owner A',
                    owner_name_b: 'Owner B',
                    __ledger_placeholder: false,
                },
                {
                    unit_number: '102',
                    owner_name: 'Existing Owner',
                    total_paid: 20,
                    owner_name_b: 'Fallback Co-owner',
                    __ledger_placeholder: false,
                },
            ],
        });
    });

    it('returns zero-value placeholder rows when ledger entries are missing', () => {
        const result = normalizeLevyLedgerRows([], [
            {unit_number: '201', owner: 'Owner Placeholder'},
        ]);

        expect(result).toEqual({
            availability: 'unit-list-placeholder',
            warning: 'No unit levy ledger records exist for the selected year. Unit list is displayed for reference only; financial values are zero placeholders.',
            rows: [
                {
                    unit_number: '201',
                    owner: 'Owner Placeholder',
                    total_levied: 0,
                    total_paid: 0,
                    net_balance: 0,
                    opening_arrears: 0,
                    admin_levied: 0,
                    admin_paid: 0,
                    sinking_levied: 0,
                    sinking_paid: 0,
                    owner_name: 'Owner Placeholder',
                    owner_name_b: '',
                    __ledger_placeholder: true,
                },
            ],
        });
    });
});

describe('collection-rate transform helpers', () => {
    it('builds aggregate collection figures from the kpi-contract mix before legacy summary fields', () => {
        const result = buildCollectionRateSummary(
            {
                unit_ledger_summary: {
                    annual_levy_total: 200000,
                    total_levied: 50000,
                    total_paid: 25000,
                    net_balance: 25000,
                    total_outstanding: 25000,
                },
                in_grace_summary: {
                    in_grace_amount: 100,
                    true_arrears_amount: 25000,
                },
            },
            {
                collection_mix: {
                    ledger_levied_ytd: 100000,
                    ledger_paid_ytd: 92000,
                    ledger_collected_ytd: 92000,
                    in_grace_amount: 1000,
                    true_arrears_amount: 4000,
                    not_yet_due_amount: 3000,
                    collected_in_advance: 500,
                },
            },
        );

        expect(result).toEqual({
            annualLevyTotal: 100000,
            totalCollectedYtd: 92000,
            collectionRate: 92,
            inGraceAmount: 1000,
            trueArrearsAmount: 4000,
            inGracePct: 1,
            arrearsPct: 4,
            notYetDueAmount: 3000,
            notYetDuePct: 3,
            collectedInAdvance: 500,
            source: 'kpi-contract',
        });
    });

    it('keeps the legacy summary calculation as an explicit unavailable-contract fallback', () => {
        const result = buildCollectionRateSummary(
            {
                unit_ledger_summary: {
                    annual_levy_total: 100000,
                    total_levied: 25000,
                    net_balance: 5000,
                    total_outstanding: 4000,
                },
                in_grace_summary: {
                    in_grace_amount: 1000,
                },
            },
            null,
        );

        expect(result).toMatchObject({
            annualLevyTotal: 100000,
            totalCollectedYtd: 20000,
            collectionRate: 20,
            inGraceAmount: 1000,
            trueArrearsAmount: 4000,
            inGracePct: 1,
            arrearsPct: 4,
            notYetDueAmount: 75000,
            notYetDuePct: 75,
            source: 'legacy-summary',
        });
    });

    it('preserves raw collection percentages for collection-rate displays', () => {
        expect(collectionPercentage(120, 100)).toBe(120);
        expect(collectionPercentage(-10, 100)).toBe(-10);
        expect(collectionPercentage(10, 0)).toBe(0);
    });

    it('clamps bounded collection percentages for progress and health widgets', () => {
        expect(clampedCollectionPercentage(120, 100)).toBe(100);
        expect(clampedCollectionPercentage(-10, 100)).toBe(0);
        expect(clampedCollectionPercentage(10, 0)).toBe(0);
    });
});
