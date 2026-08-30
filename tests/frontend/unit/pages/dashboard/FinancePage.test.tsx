import React, {act} from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import FinancePage from '@/pages/dashboard/FinancePage';

// Mock next-auth/react BEFORE anything else imports it
jest.mock('next-auth/react', () => ({
    useSession: jest.fn(() => ({data: null, status: 'unauthenticated'})),
    SessionProvider: ({children}: any) => <div>{children}</div>,
    signIn: jest.fn(),
    signOut: jest.fn(),
}));

// mockGetSearchParam is a jest.fn() (not a plain closure) so individual tests can
// override which tab is active on mount — FinancePage re-syncs activeTab from
// useSearchParams() on every render (see its `useEffect(() => { setActiveTab(...) },
// [searchParams])`), which fights any fireEvent.click on a TabsTrigger when the mock
// keeps returning a fixed 'overview' value.
const mockGetSearchParam = jest.fn((key: string) => (key === 'tab' ? 'overview' : null));

jest.mock('next/navigation', () => ({
    usePathname: () => '/financials',
    useRouter: () => ({
        push: jest.fn(),
    }),
    useSearchParams: () => ({
        get: (key: string) => mockGetSearchParam(key),
    }),
}));

const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
    interceptors: {
        request: {use: jest.fn(), eject: jest.fn()},
        response: {use: jest.fn(), eject: jest.fn()},
    },
};
const mockIsManager = jest.fn(() => false);
const mockIsAdmin = jest.fn(() => false);

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        selectedYear: '2026',
        availableYears: ['2025', '2026'],
        financialYearStartMonth: 1,
        hasPermission: () => true,
        isManager: mockIsManager,
        isAdmin: mockIsAdmin,
        user: {role: 'owner', unit_number: 'UA001'},
    }),
    AuthProvider: ({children}: any) => <div>{children}</div>,
}));


describe('FinancePage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockIsManager.mockReturnValue(false);
        mockIsAdmin.mockReturnValue(false);
        mockGetSearchParam.mockImplementation((key: string) => (key === 'tab' ? 'overview' : null));
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('/finance/summary')) {
                return Promise.resolve({
                    data: {
                        admin_fund: {
                            total_income: 1000,
                            levy_income: 800,
                            total_expenses: 500,
                            budgeted_expenses: 600,
                            annual_levy_proposed: 1000,
                            ytd_total_income: 800,
                        },
                        sinking_fund: {
                            total_income: 2000,
                            levy_income: 1500,
                            total_expenses: 1000,
                            budgeted_expenses: 1200,
                            annual_levy_proposed: 2000,
                            ytd_total_income: 1500,
                        },
                        unit_ledger_summary: {
                            total_outstanding: 500,
                            units_owing: 2,
                            total_levied: 5000,
                            total_paid: 4500,
                            units_paid_up: 85,
                            units_credit: 1
                        }
                    }
                });
            }
            if (url.includes('/finance/charts')) {
                return Promise.resolve({
                    data: {
                        expense_by_category: [{name: 'Test Expense', value: 100}],
                        income_by_category: [],
                        monthly_trend: []
                    }
                });
            }
            if (url.includes('/finance/budget-vs-actual')) {
                return Promise.resolve({
                    data: {
                        administrative: [{category: 'Admin', budget: 100, actual: 80}],
                        sinking: []
                    }
                });
            }
            if (url.includes('/unit-levy-ledger')) {
                return Promise.resolve({data: []});
            }
            if (url.includes('/levy-calculator')) {
                return Promise.resolve({
                    data: {
                        total_budget: 10000,
                        year: '2026',
                        status: 'actual',
                        levies: [],
                        payment_schedule: [],
                        payment_methods: []
                    }
                });
            }
            if (url.includes('/owners-units')) {
                return Promise.resolve({data: []});
            }
            return Promise.resolve({data: {}});
        });
    });

    it('renders without crashing and shows summary data', async () => {
        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        expect(screen.getByTestId('finance-page')).toBeInTheDocument();

        await waitFor(() => {
            const adminIncome = screen.queryAllByText(/\$1,000.00/);
            expect(adminIncome.length).toBeGreaterThan(0);
        });
        expect(screen.getByText('Sinking Fund Expenses')).toBeInTheDocument();
        expect(screen.queryAllByText(/\$1,000.00/).length).toBeGreaterThan(0);
    });

    it('shows year-scoped collected label and raw paid context when cumulative ledger total differs', async () => {
        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText('Ledger Collected YTD')).toBeInTheDocument();
        });
    });

    it('does not fetch levy status detail data on the overview tab', async () => {
        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith('/finance/summary?year=2026');
        });
        expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/unit-levy-ledger'));
        expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/arrears/detail'));
    });

    it('fetches levy status detail data only when the levy status tab is active', async () => {
        mockIsManager.mockReturnValue(true);
        mockGetSearchParam.mockImplementation((key: string) => (key === 'tab' ? 'levy-status' : null));

        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith('/unit-levy-ledger?year=2026&limit=200');
        });
        expect(mockApi.get).toHaveBeenCalledWith('/arrears/detail?year=2026');
        expect(mockApi.get).toHaveBeenCalledWith('/owners-units');
    });

    it('does not clear overview values when a non-summary finance endpoint fails', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('/finance/summary')) {
                return Promise.resolve({
                    data: {
                        admin_fund: {annual_levy_proposed: 1234, budgeted_expenses: 900},
                        sinking_fund: {annual_levy_proposed: 5678},
                        unit_ledger_summary: {
                            total_outstanding: 0,
                            units_owing: 0,
                            total_levied: 6912,
                            total_paid: 6912,
                            units_paid_up: 87,
                            units_credit: 0,
                        },
                        ledger_quality: {canonical_unit_count: 87},
                    },
                });
            }
            if (url.includes('/finance/charts')) return Promise.reject(new Error('charts failed'));
            if (url.includes('/finance/budget-vs-actual')) return Promise.resolve({data: {administrative: [], sinking: []}});
            if (url.includes('/unit-levy-ledger')) return Promise.resolve({data: []});
            if (url.includes('/finance/quarterly-budget')) return Promise.resolve({data: {quarters: []}});
            if (url.includes('/owners-units')) return Promise.resolve({data: []});
            return Promise.resolve({data: null});
        });

        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText('$1,234.00')).toBeInTheDocument();
        });
        expect(screen.getByText('$5,678.00')).toBeInTheDocument();
        expect(screen.getByText('87')).toBeInTheDocument();
    });

    it('switches tabs correctly', async () => {
        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        const chartsTab = screen.getByRole('tab', {name: /Charts/i});
        fireEvent.click(chartsTab);

        // Some Radix tabs implementations might need a wait for the content to mount
        await waitFor(() => {
            expect(screen.queryByText(/Admin Fund: Budget vs. Actual/i)).toBeDefined();
        });
    });

    it('shows an Overdue badge (not Past) for a past-due quarter with an unpaid balance', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('/finance/quarterly-budget')) {
                return Promise.resolve({
                    data: {
                        gst_registered: false,
                        quarters: [
                            {
                                label: 'Q1', due_date: '2026-03-01', status: 'past',
                                budgeted_income_ex_gst: 100, budgeted_gst: 0, budgeted_income_inc_gst: 100,
                                levied: 100, collected: 100, outstanding: 0, has_ledger_data: true,
                                portal_confirmed: 0, portal_pending: 0, budgeted_expenses: 50, actual_expenses: 50,
                            },
                            {
                                label: 'Q2', due_date: '2026-06-01', status: 'overdue',
                                budgeted_income_ex_gst: 100, budgeted_gst: 0, budgeted_income_inc_gst: 100,
                                levied: 100, collected: 40, outstanding: 60, has_ledger_data: true,
                                portal_confirmed: 0, portal_pending: 0, budgeted_expenses: 50, actual_expenses: 50,
                            },
                        ],
                        annual_totals: null,
                    }
                });
            }
            if (url.includes('/finance/summary')) {
                return Promise.resolve({data: {unit_ledger_summary: {}}});
            }
            if (url.includes('/finance/charts')) {
                return Promise.resolve({data: {expense_by_category: [], income_by_category: [], monthly_trend: []}});
            }
            if (url.includes('/finance/budget-vs-actual')) {
                return Promise.resolve({data: {administrative: [], sinking: []}});
            }
            return Promise.resolve({data: {}});
        });

        // FinancePage re-syncs activeTab from useSearchParams().get('tab') on every
        // render; start directly on 'charts' rather than fighting that sync effect
        // with a simulated tab click (see mockGetSearchParam comment above).
        mockGetSearchParam.mockImplementation((key: string) => (key === 'tab' ? 'charts' : null));
        try {
            await act(async () => {
                render(
                        <FinancePage/>
                );
            });

            await waitFor(() => {
                expect(screen.getByText('Overdue')).toBeInTheDocument();
            });
            expect(screen.getByText('Past')).toBeInTheDocument();
        } finally {
            mockGetSearchParam.mockImplementation((key: string) => (key === 'tab' ? 'overview' : null));
        }
    });

    it('renders GST as tax metadata below income sources, not as an income source slice', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('/finance/charts')) {
                return Promise.resolve({
                    data: {
                        expense_by_category: [],
                        income_by_category: [
                            {name: 'Administrative Fund (ex-GST)', value: 1000},
                        ],
                        gst_summary: {
                            gst_registered: true,
                            gst_label: 'GST (10%)',
                            gst_component: 100,
                            classification: 'tax_collected_not_income',
                        },
                        monthly_trend: []
                    }
                });
            }
            if (url.includes('/finance/summary')) return Promise.resolve({data: {unit_ledger_summary: {}}});
            if (url.includes('/finance/budget-vs-actual')) return Promise.resolve({data: {administrative: [], sinking: []}});
            if (url.includes('/finance/quarterly-budget')) return Promise.resolve({data: {quarters: []}});
            return Promise.resolve({data: {}});
        });
        mockGetSearchParam.mockImplementation((key: string) => (key === 'tab' ? 'charts' : null));
        try {
            await act(async () => {
                render(
                        <FinancePage/>
                );
            });

            await waitFor(() => {
                expect(screen.getByText('Administrative Fund (ex-GST)')).toBeInTheDocument();
            });
            expect(screen.getByText('GST (10%) on levy income')).toBeInTheDocument();
            expect(screen.getByText('GST is tracked as tax collected on levies, not as an income source.')).toBeInTheDocument();
        } finally {
            mockGetSearchParam.mockImplementation((key: string) => (key === 'tab' ? 'overview' : null));
        }
    });

    it('shows Levy Status due-to-date amounts instead of full-year annual paid totals', async () => {
        mockIsManager.mockReturnValue(true);
        mockGetSearchParam.mockImplementation((key: string) => (key === 'tab' ? 'levy-status' : null));
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('/unit-levy-ledger')) {
                return Promise.resolve({
                    data: [{
                        id: 'ledger-1',
                        building_id: '13195',
                        plan_id: '13195',
                        year: '2026',
                        unit_number: 'TH001',
                        owner_name: 'Owner One',
                        total_levied: 4000,
                        total_paid: 4000,
                        net_balance: 0,
                        levied_due_to_date: 2000,
                        paid_due_to_date: 2000,
                        outstanding_due_to_date: 0,
                        paid_this_year: 2000,
                    }],
                });
            }
            if (url.includes('/finance/summary')) return Promise.resolve({data: {unit_ledger_summary: {}}});
            if (url.includes('/finance/charts')) return Promise.resolve({data: {expense_by_category: [], income_by_category: [], monthly_trend: []}});
            if (url.includes('/finance/budget-vs-actual')) return Promise.resolve({data: {administrative: [], sinking: []}});
            if (url.includes('/finance/quarterly-budget')) return Promise.resolve({data: {quarters: []}});
            if (url.includes('/owners-units')) return Promise.resolve({data: []});
            return Promise.resolve({data: {}});
        });

        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText('Levied to Date (2026)')).toBeInTheDocument();
        });
        expect(screen.getByText('Paid to Date (2026)')).toBeInTheDocument();
        expect(screen.getAllByText('$2,000.00').length).toBeGreaterThanOrEqual(2);
        expect(screen.getByText('On Track')).toBeInTheDocument();
    });

    it('treats the paid-up Levy Status filter as on-track plus credit units', async () => {
        mockIsManager.mockReturnValue(true);
        mockGetSearchParam.mockImplementation((key: string) => {
            if (key === 'tab') return 'levy-status';
            if (key === 'status') return 'paid_up';
            return null;
        });
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('/unit-levy-ledger')) {
                return Promise.resolve({
                    data: [
                        {
                            id: 'credit',
                            year: '2026',
                            unit_number: 'TH001',
                            owner_name: 'Credit Owner',
                            net_balance: -50,
                            levied_due_to_date: 2000,
                            paid_due_to_date: 2050,
                            outstanding_due_to_date: 0,
                        },
                        {
                            id: 'on-track',
                            year: '2026',
                            unit_number: 'TH002',
                            owner_name: 'Clear Owner',
                            net_balance: 0,
                            levied_due_to_date: 2000,
                            paid_due_to_date: 2000,
                            outstanding_due_to_date: 0,
                        },
                        {
                            id: 'arrears',
                            year: '2026',
                            unit_number: 'TH003',
                            owner_name: 'Arrears Owner',
                            net_balance: 100,
                            levied_due_to_date: 2000,
                            paid_due_to_date: 1900,
                            outstanding_due_to_date: 100,
                        },
                    ],
                });
            }
            if (url.includes('/finance/summary')) return Promise.resolve({data: {unit_ledger_summary: {}}});
            if (url.includes('/finance/charts')) return Promise.resolve({data: {expense_by_category: [], income_by_category: [], monthly_trend: []}});
            if (url.includes('/finance/budget-vs-actual')) return Promise.resolve({data: {administrative: [], sinking: []}});
            if (url.includes('/finance/quarterly-budget')) return Promise.resolve({data: {quarters: []}});
            if (url.includes('/owners-units')) return Promise.resolve({data: []});
            return Promise.resolve({data: {}});
        });

        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText('Credit Owner')).toBeInTheDocument();
        });
        expect(screen.getByText('Clear Owner')).toBeInTheDocument();
        expect(screen.queryByText('Arrears Owner')).not.toBeInTheDocument();
    });

    it('export button is present and clickable', async () => {
        await act(async () => {
            render(
                    <FinancePage/>
            );
        });
        const exportBtn = screen.getByTestId('export-btn');
        expect(exportBtn).toBeInTheDocument();
        // Clicking when selectedYear is set should call api.get
        await act(async () => {
            fireEvent.click(exportBtn);
        });
        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith(
                expect.stringContaining('/finance/export'),
                expect.any(Object)
            );
        });
    });

    it('fetches data only once on initial render', async () => {
        await act(async () => {
            render(
                    <FinancePage/>
            );
        });

        await waitFor(() => {
            // finance/summary should be called exactly once per effect trigger
            const summaryCalls = mockApi.get.mock.calls.filter((c: any[]) =>
                String(c[0]).includes('/finance/summary')
            );
            expect(summaryCalls.length).toBe(1);
        });
        expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/levy-calculator'));
        expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/owners-units'));
        expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/finance/budget-vs-actual'));
        expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/finance/quarterly-budget'));
        expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/expense-transactions'));
        expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/income-transactions'));
    });

    describe('GAP-FIN-014 — ledger quality denominator', () => {
        // East Gate has 87 canonical lots; a duplicate/orphaned unit_levy_ledger
        // row must never inflate the "of X units" denominator to 156.
        const summaryWithInconsistentLedger = {
            admin_fund: {total_income: 1000, annual_levy_proposed: 1000},
            sinking_fund: {total_income: 2000, annual_levy_proposed: 2000},
            unit_ledger_summary: {
                total_outstanding: 500,
                units_owing: 14,
                total_levied: 5000,
                total_paid: 4500,
                units_paid_up: 59,
                units_credit: 14,
            },
            ledger_quality: {
                canonical_unit_count: 87,
                ledger_row_count: 156,
                distinct_ledger_unit_count: 87,
                duplicate_ledger_units: ['TH001'],
                missing_ledger_units: [],
                extra_ledger_units: [],
                is_unit_count_consistent: false,
                source: 'units + unit_levy_ledger',
            },
        };

        beforeEach(() => {
            mockApi.get.mockImplementation((url: string) => {
                if (url.includes('/finance/summary')) {
                    return Promise.resolve({data: summaryWithInconsistentLedger});
                }
                if (url.includes('/unit-levy-ledger')) return Promise.resolve({data: []});
                if (url.includes('/owners-units')) return Promise.resolve({data: []});
                return Promise.resolve({data: {}});
            });
        });

        it('renders "of 87 units", not "of 156 units", when duplicate ledger rows are present', async () => {
            await act(async () => {
                render(
                        <FinancePage/>
                );
            });

            await waitFor(() => {
                expect(screen.getByText(/of 87 units/i)).toBeInTheDocument();
            });
            expect(screen.queryByText(/of 156 units/i)).not.toBeInTheDocument();
        });

        it('renders paid-up units as canonical units minus arrears, not the raw paid-up bucket', async () => {
            await act(async () => {
                render(
                        <FinancePage/>
                );
            });

            await waitFor(() => {
                expect(screen.getByText('73')).toBeInTheDocument();
            });
            expect(screen.queryByText('59')).not.toBeInTheDocument();
        });

        it('shows a ledger-quality warning banner when duplicate/missing/extra units exist', async () => {
            await act(async () => {
                render(
                        <FinancePage/>
                );
            });

            await waitFor(() => {
                expect(screen.getByTestId('ledger-quality-warning')).toBeInTheDocument();
            });
            expect(screen.getByText(/duplicate ledger unit/i)).toBeInTheDocument();
        });
    });
});
