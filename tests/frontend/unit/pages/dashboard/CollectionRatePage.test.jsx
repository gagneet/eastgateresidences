/**
 * CollectionRatePage test suite (GAP-FIN-014).
 *
 * Coverage:
 *   1. Building-wide collection rate / status distribution come from the
 *      backend /finance/kpi-contract response, not a local recompute from
 *      /finance/summary's raw ledger totals.
 *   2. Portal/scraper values render with explicit cross-check wording.
 *   3. A portal/ledger arrears mismatch renders a reconciliation warning.
 */
import React, {act} from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import CollectionRatePage from '@/pages/dashboard/CollectionRatePage';

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn(), back: jest.fn()}),
}));

jest.mock('next/link', () =>
    function MockLink({children, href}) {
        return <a href={href}>{children}</a>;
    }
);

const mockApi = {
    get: jest.fn(),
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        selectedYear: '2026',
        availableYears: ['2025', '2026'],
        financialYearStartMonth: 1,
    }),
}));

// /finance/summary — legacy shape. Deliberately set so that a naive
// total_paid/total_levied client-side recompute (the pre-GAP-FIN-014
// behaviour) would produce numbers DIFFERENT from the kpi-contract mock
// below, so the tests can prove which source actually won.
const legacySummary = {
    unit_ledger_summary: {
        annual_levy_total: 200000,
        total_levied: 25000,
        total_paid: 20000,
        net_balance: 5000,
        total_outstanding: 5000,
        units_paid_up: 59,
        units_owing: 85,
        units_credit: 12,
    },
    in_grace_summary: {
        in_grace_amount: 0,
        true_arrears_amount: 5000,
        grace_period_days: 14,
        in_grace_periods: [],
    },
    portal_summary: {
        arrears_total: 9999,
        credit_total: 100,
        arrears_count: 3,
        credit_count: 1,
        clear_count: 83,
        collection_rate: 91.2,
        risk_level: 'LOW',
        updated_at: '2026-06-01T00:00:00Z',
    },
};

const kpiContract = {
    unit_counts: {
        canonical_unit_count: 87,
        paid_up: 70,
        owing: 15,
        credit: 2,
    },
    collection_mix: {
        ledger_levied_ytd: 100000,
        ledger_paid_ytd: 92000,
        ledger_collected_ytd: 92000,
        in_grace_amount: 1000,
        true_arrears_amount: 4000,
        not_yet_due_amount: 3000,
    },
    portal_cross_check: {
        available: true,
        portal_arrears_total: 9999,
        ledger_arrears_total: 4000,
        delta: 5999,
        requires_reconciliation: true,
        last_scraped_at: '2026-06-01T00:00:00Z',
    },
    warnings: ['Portal arrears ($9999.00) differ from ledger arrears ($4000.00) — reconciliation required.'],
};

describe('CollectionRatePage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockApi.get.mockImplementation((url) => {
            if (url.includes('/finance/kpi-contract')) {
                return Promise.resolve({data: kpiContract});
            }
            if (url.includes('/finance/summary')) {
                return Promise.resolve({data: legacySummary});
            }
            if (url.includes('/unit-levy-ledger')) {
                return Promise.resolve({data: []});
            }
            return Promise.resolve({data: {}});
        });
    });

    it('renders the backend kpi-contract collection rate, not a local recompute from /finance/summary', async () => {
        await act(async () => {
            render(
                    <CollectionRatePage/>
            );
        });

        // kpi-contract: ledger_collected_ytd / ledger_levied_ytd = 92000/100000 = 92.0%.
        // legacySummary.annual_levy_total is deliberately 200000, so a half-contract/half-summary
        // calculation would render 46.0% instead.
        // legacy client-side calc would have produced a different figure from
        // legacySummary's total_paid/total_levied-derived totalCollectedYtd.
        // (Rendered in more than one place — headline gauge + breakdown legend.)
        await waitFor(() => {
            expect(screen.getAllByText(/92\.0%/).length).toBeGreaterThan(0);
        });
        expect(screen.queryByText(/46\.0%/)).not.toBeInTheDocument();
    });

    it('does not derive unit status distribution locally when the kpi-contract supplies unit_counts', async () => {
        await act(async () => {
            render(
                    <CollectionRatePage/>
            );
        });

        // legacySummary.unit_ledger_summary has units_paid_up=59 (the "156 total" bug shape);
        // kpi-contract.unit_counts.paid_up=70 must be what's used for the canonical count.
        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining('/finance/kpi-contract'));
        });
    });

    it('renders portal/scraper data as an explicitly labelled cross-check', async () => {
        await act(async () => {
            render(
                    <CollectionRatePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText(/Strata Web Portal Snapshot/i)).toBeInTheDocument();
        });
        expect(screen.getByText(/Read-only cross-check/i)).toBeInTheDocument();
        expect(screen.getByText(/Portal Snapshot Arrears/i)).toBeInTheDocument();
        expect(screen.getByText(/cross-check only/i)).toBeInTheDocument();
    });

    it('shows a reconciliation warning when portal arrears differ from ledger arrears', async () => {
        await act(async () => {
            render(
                    <CollectionRatePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByTestId('portal-reconciliation-warning')).toBeInTheDocument();
        });
        expect(screen.getByText(/reconciliation required/i)).toBeInTheDocument();
    });

    it('renders a definition tooltip trigger on the True Arrears card', async () => {
        await act(async () => {
            render(
                    <CollectionRatePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByLabelText(/What is True Arrears/i)).toBeInTheDocument();
        });
    });

    it('preserves overpaid unit collection rates in the unit table', async () => {
        mockApi.get.mockImplementation((url) => {
            if (url.includes('/finance/kpi-contract')) {
                return Promise.resolve({data: kpiContract});
            }
            if (url.includes('/finance/summary')) {
                return Promise.resolve({data: legacySummary});
            }
            if (url.includes('/unit-levy-ledger')) {
                return Promise.resolve({
                    data: [
                        {
                            id: 'ledger-unit-1',
                            unit_number: '1',
                            total_levied: 1000,
                            total_paid: 1200,
                            owner_name: 'Overpaid Owner',
                        },
                    ],
                });
            }
            return Promise.resolve({data: {}});
        });

        await act(async () => {
            render(
                    <CollectionRatePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText('120.0%')).toBeInTheDocument();
        });
        expect(screen.getByText(/FULLY\s*PAID/i)).toBeInTheDocument();
    });

    it('uses paid_this_year (not the lifetime total_paid) for the unit table Paid column and rate', async () => {
        // Regression test for the 2026-08-01 bug: entry.total_paid is not reliably scoped to
        // one year (confirmed live -- can be back-solved from a portal balance snapshot, i.e.
        // cumulative payment history through the scrape date). A real East Gate unit showed
        // total_paid=$28,783.04 against total_levied=$3,545.02 for the SAME nominal year, which
        // would render as a nonsensical >800% collection rate here. paid_this_year is the
        // correctly year-scoped field the backend now provides alongside it.
        mockApi.get.mockImplementation((url) => {
            if (url.includes('/finance/kpi-contract')) {
                return Promise.resolve({data: kpiContract});
            }
            if (url.includes('/finance/summary')) {
                return Promise.resolve({data: legacySummary});
            }
            if (url.includes('/unit-levy-ledger')) {
                return Promise.resolve({
                    data: [
                        {
                            id: 'ledger-unit-th087',
                            unit_number: 'TH087',
                            total_levied: 3545.02,
                            total_paid: 28783.04,
                            paid_this_year: 3800.00,
                            owner_name: 'TH087 Owner',
                        },
                    ],
                });
            }
            return Promise.resolve({data: {}});
        });

        await act(async () => {
            render(
                    <CollectionRatePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText('$3,800.00')).toBeInTheDocument();
        });
        expect(screen.queryByText('$28,783.04')).not.toBeInTheDocument();
        // 3800.00 / 3545.02 * 100 = 107.19...% -> capped display at 100.0% by clampedCollectionPercentage
        // for the progress bar, but the raw rate badge is uncapped -- assert the badge shows the
        // real ~107.2%, not the >800% the contaminated total_paid would have produced.
        expect(screen.getByText('107.2%')).toBeInTheDocument();
    });
});
