import React from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import FinancePage from '@/pages/dashboard/FinancePage';

// Mock dependencies
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn()
}));

const mockUseAuth = require('@/contexts/AuthContext').useAuth;

// Mock next/navigation
jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: jest.fn(),
    }),
    usePathname: () => '/financials',
    useSearchParams: () => ({
        get: (key: string) => null,
    }),
}));

// Mock Tremor

describe('FinancePage', () => {
    beforeEach(() => {
        mockUseAuth.mockReturnValue({
            user: {role: 'owner', unit_number: '101'},
            token: 'fake-token',
            selectedYear: '2024',
            availableYears: ['2023', '2024'],
            api: {
                get: (url: string) => {
                    if (url.includes('/finance/summary')) {
                        return Promise.resolve({
                            data: {
                                admin_fund: {total_income: 50000, levy_income: 45000, total_expenses: 30000, annual_levy_proposed: 50000},
                                sinking_fund: {total_income: 20000, levy_income: 18000, annual_levy_proposed: 20000},
                                unit_ledger_summary: {
                                    total_outstanding: 5000,
                                    units_owing: 2,
                                    total_levied: 100000,
                                    total_paid: 95000,
                                    units_paid_up: 10
                                }
                            }
                        });
                    }
                    if (url.includes('/finance/charts')) {
                        return Promise.resolve({
                            data: {
                                expense_by_category: [],
                                income_by_category: [],
                                monthly_trend: []
                            }
                        });
                    }
                    if (url.includes('/finance/budget-vs-actual')) {
                        return Promise.resolve({data: {administrative: [], sinking: []}});
                    }
                    if (url.includes('/unit-levy-ledger')) {
                        return Promise.resolve({data: []});
                    }
                    if (url.includes('/levy-calculator')) {
                        return Promise.resolve({data: {total_budget: 120000, year: '2024', status: 'approved'}});
                    }
                    if (url.includes('/owners-units')) {
                        return Promise.resolve({data: []});
                    }
                    return Promise.resolve({data: {}});
                },
            },
            hasPermission: () => true,
            isAdmin: () => false,
            isManager: () => false,
            isECMember: () => false,
        });
    });

    it('renders correctly and displays financial data', async () => {
        render(
                <FinancePage/>
        );

        // Wait for data to load
        await waitFor(() => {
            expect(screen.getByTestId('finance-page')).toBeInTheDocument();
        }, {timeout: 3000});

        // Check headings
        expect(screen.getByRole('heading', {name: /Finance/i})).toBeInTheDocument();

        // Check fund incomes (wait for text to appear as it might be async after initial render).
        // Admin fund's total AND its "Levy: $X" breakdown line both read annual_levy_proposed, so
        // when a fund's income is 100% levy-derived (as in this mock) the figure legitimately
        // renders twice — use getAllByText rather than the single-match getByText.
        await waitFor(() => {
            expect(screen.getAllByText(/\$50,000.00/i).length).toBeGreaterThanOrEqual(1);
        });
        expect(screen.getAllByText(/\$20,000.00/i).length).toBeGreaterThanOrEqual(1);

        // Check tabs
        expect(screen.getByText('Overview')).toBeInTheDocument();
        expect(screen.getByText('Charts')).toBeInTheDocument();
    });
});
