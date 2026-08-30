// @featuretrace:financial-ui-calculation-register — Frontend tests for registry-to-label alias mapping.
// Data flow: Jest -> levyDisplay registry helpers -> finance display labels (scope param: building|global).
// Related: frontend/src/lib/finance/levyDisplay.ts, backend/services/finance_calculation_registry.py
import {
    findFinanceCalculationGroup,
    GST_RATE,
    labelsFromCalculationRegistry,
    levyBreakdownExGST,
    levyBreakdownInclGST,
} from '@/lib/finance/levyDisplay';

describe('levyBreakdownExGST', () => {
    it('rounds ex-gst inputs before calculating gst and total', () => {
        expect(levyBreakdownExGST(100.005, 200.005)).toEqual({
            adminExGST: 100.01,
            sinkingExGST: 200.01,
            gst: 30,
            totalInclGST: 330.02,
            mode: 'ex-gst',
        });
    });

    it('suppresses gst when not gst registered', () => {
        expect(levyBreakdownExGST(100.005, 200.005, GST_RATE, false)).toEqual({
            adminExGST: 100.01,
            sinkingExGST: 200.01,
            gst: 0,
            totalInclGST: 300.02,
            mode: 'ex-gst',
        });
    });
});

describe('levyBreakdownInclGST', () => {
    it('keeps the gst line reconciled to the rounded payable total', () => {
        expect(levyBreakdownInclGST(0.001, 0.014)).toEqual({
            adminInclGST: 0,
            sinkingInclGST: 0.02,
            gst: 0.01,
            totalInclGST: 0.02,
            mode: 'incl-gst',
        });
    });

    it('suppresses gst when not gst registered', () => {
        expect(levyBreakdownInclGST(100.005, 200.005, GST_RATE, false)).toEqual({
            adminInclGST: 100.01,
            sinkingInclGST: 200.01,
            gst: 0,
            totalInclGST: 300.02,
            mode: 'incl-gst',
        });
    });
});

describe('finance calculation registry labels', () => {
    const registry = {
        version: '2026-07-15.v1',
        status: 'contract_cached_values_not_materialised',
        implementation_scope: {
            completed: ['Canonical label/formula/source registry is cached in-process for 300 seconds.'],
            not_completed: ['Heavy financial values are not materialised or shared from a backend value cache yet.'],
        },
        common_label_groups: [
            {
                canonical_key: 'levy.ledger_paid',
                canonical_label: 'Ledger Payments Recorded',
                similar_labels: ['Total Paid', 'Paid to Date'],
                sources: {
                    mongodb: ['unit_levy_ledger.total_paid'],
                    postgres: ['finance.levy_items.paid_cents'],
                },
            },
            {
                canonical_key: 'levy.outstanding_balance',
                canonical_label: 'Amount Owing',
                similar_labels: ['Outstanding', 'Arrears'],
            },
        ],
    };

    it('maps backend registry labels onto existing frontend aliases', () => {
        const labels = labelsFromCalculationRegistry(registry);

        expect(labels.ledgerPaid).toBe('Ledger Payments Recorded');
        expect(labels.outstandingBalance).toBe('Amount Owing');
        expect(labels['levy.ledger_paid']).toBe('Ledger Payments Recorded');
    });

    it('finds a registry group by canonical key', () => {
        const group = findFinanceCalculationGroup(registry, 'levy.ledger_paid');

        expect(group?.similar_labels).toContain('Total Paid');
        expect(group?.sources?.mongodb).toContain('unit_levy_ledger.total_paid');
        expect(group?.sources?.postgres).toContain('finance.levy_items.paid_cents');
    });
});
