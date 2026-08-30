/**
 * UnitFinanceDetailPage test suite (GAP-FIN-014).
 *
 * Coverage:
 *   1. Operational balance fields (Total Paid / Annual Levy / Balance Due /
 *      Opening Arrears) come from /levy-status ledger fields only.
 *   2. When the selected year has no ledger row, the page shows an explicit
 *      data-quality warning instead of silently falling back to stale
 *      /owners-units (unitInfo) values.
 */
import React, {act} from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import UnitFinanceDetailPage from '@/pages/dashboard/UnitFinanceDetailPage';

jest.mock('next/navigation', () => ({
    useParams: () => ({unit_number: 'TH001'}),
    useRouter: () => ({push: jest.fn(), back: jest.fn()}),
}));

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        user: {role: 'strata_manager'},
        hasPermission: () => true,
        availableYears: ['2024', '2025', '2026'],
        selectedYear: '2026',
        setSelectedYear: jest.fn(),
        financialYearStartMonth: 1,
    }),
}));

// A stale /owners-units mirror — must NEVER be used for operational figures
// once GAP-FIN-014's ledger-only rule is in effect.
const staleUnitInfo = {
    unit_number: 'TH001',
    owner_name: 'Jane Doe',
    total_paid: 999999,
    total_levied: 999999,
    balance_owing: 999999,
    net_balance: 999999,
    balance_credit: 0,
    opening_arrears: 999999,
};

describe('UnitFinanceDetailPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('renders operational balances from /levy-status ledger fields, not /owners-units', async () => {
        mockApi.get.mockImplementation((url) => {
            if (url.includes('/owners-units/')) return Promise.resolve({data: staleUnitInfo});
            if (url.includes('/levy-status/')) {
                return Promise.resolve({
                    data: {
                        unit_number: 'TH001',
                        annual_levy: 1200,
                        total_paid: 800,
                        balance_due: 400,
                        net_balance: 400,
                        credit_balance: 0,
                        opening_arrears: 15,
                        paid_this_year: 805,
                        ledger: {admin_opening: 10, sinking_opening: 5},
                    },
                });
            }
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(
                    <UnitFinanceDetailPage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText(/\$1,200\.00/)).toBeInTheDocument();
        });
        expect(screen.getByText(/\$800\.00/)).toBeInTheDocument();
        expect(screen.getByText(/\$805\.00/)).toBeInTheDocument();
        expect(screen.getByText('Prior levy-year debt carried into this year')).toBeInTheDocument();
        expect(screen.getByText('Applied to FY 2026 levy, excluding credit')).toBeInTheDocument();
        expect(screen.getByText(/\$400\.00/)).toBeInTheDocument();
        // The stale /owners-units figures must never render.
        expect(screen.queryByText(/999,999/)).not.toBeInTheDocument();
    });

    it('shows lifetime cumulative Total Paid (total_paid_all_years), not the current-year figure', async () => {
        mockApi.get.mockImplementation((url) => {
            if (url.includes('/owners-units/')) return Promise.resolve({data: staleUnitInfo});
            if (url.includes('/levy-status/')) {
                return Promise.resolve({
                    data: {
                        unit_number: 'TH001',
                        annual_levy: 1200,
                        total_paid: 800,            // current year only
                        total_paid_all_years: 4300, // cumulative across all years
                        paid_this_year: 805,
                        balance_due: 400,
                        net_balance: 400,
                        credit_balance: 0,
                        opening_arrears: 15,
                        ledger: {admin_opening: 10, sinking_opening: 5},
                    },
                });
            }
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(
                    <UnitFinanceDetailPage/>
            );
        });

        await waitFor(() => {
            // Total Paid tile shows the cumulative lifetime figure...
            expect(screen.getByText(/\$4,300\.00/)).toBeInTheDocument();
        });
        // ...and the label reflects the lifetime semantics.
        expect(screen.getByText('Cumulative paid across all levy years')).toBeInTheDocument();
        // Paid This Year remains the current-year figure.
        expect(screen.getByText(/\$805\.00/)).toBeInTheDocument();
    });

    it('shows a data-quality warning instead of a stale fallback when the selected-year ledger is missing', async () => {
        mockApi.get.mockImplementation((url) => {
            if (url.includes('/owners-units/')) return Promise.resolve({data: staleUnitInfo});
            if (url.includes('/levy-status/')) {
                // No ledger row for this year — backend returns ledger: null.
                return Promise.resolve({
                    data: {
                        unit_number: 'TH001',
                        ledger: null,
                    },
                });
            }
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(
                    <UnitFinanceDetailPage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByTestId('ledger-missing-warning')).toBeInTheDocument();
        });
        // Must not silently render the stale unitInfo balance as if it were current.
        expect(screen.queryByText(/999,999/)).not.toBeInTheDocument();
    });
});
