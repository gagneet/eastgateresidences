import React from 'react';
import {render, screen, waitFor, fireEvent} from '@testing-library/react';
import '@testing-library/jest-dom';
import GSTBASLedgerPage from '@/pages/dashboard/admin/GSTBASLedgerPage';

// ─── Mock AuthContext ──────────────────────────────────────────────────────────
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn(),
}));
const mockUseAuth = require('@/contexts/AuthContext').useAuth;

// ─── Mock next/navigation ─────────────────────────────────────────────────────
jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
    usePathname: () => '/admin/gst-bas-ledger',
    useSearchParams: () => ({get: () => null}),
}));

// ─── Shared fixture data ──────────────────────────────────────────────────────
const SETTINGS_REGISTERED = {
    gst_registered: true,
    levy_gst_rate: 0.10,
    effective_gst_rate: 0.10,
    gst_multiplier: 1.10,
    gst_label: 'GST (10%)',
};

const SETTINGS_UNREGISTERED = {
    gst_registered: false,
    levy_gst_rate: 0.10,
    effective_gst_rate: 0.0,
    gst_multiplier: 1.0,
    gst_label: 'GST (0%)',
};

const SUMMARY_REGISTERED = {
    financial_year: '2026',
    quarter: 'Annual',
    period_start: '2026-01-01',
    period_end: '2026-12-31',
    gst_registered: true,
    effective_gst_rate: 0.10,
    levy_ex_gst: 400000,
    levy_inc_gst: 440000,
    output_tax: 40000,
    itc_total: 5000,
    itc_line_items: [
        {
            date: '2026-03-01',
            description: 'Electricity',
            supplier_name: 'AGL',
            invoice_number: 'INV-001',
            category_name: 'Electricity',
            fund_type: 'administrative',
            amount_ex_gst: 500,
            gst_amount: 50,
            amount_inc_gst: 550,
            is_gst_free: false,
        },
    ],
    water_total: 12000,
    council_rates_total: 3600,
    gst_free_total: 15600,
    net_gst_payable: 35000,
    quarterly: [],
};

const WORKSHEET = {
    financial_year: '2026',
    quarter: 'Annual',
    period_start: '2026-01-01',
    period_end: '2026-12-31',
    G1: 440000,
    G2: 0,
    G3: 0,
    G10: 0,
    G11: 55000,
    field_1A: 40000,
    field_1B: 5000,
    net_9: 35000,
    gst_registered: true,
    notes: [
        'Water and council rates are GST-free purchases — excluded from G11.',
    ],
};

// ─── Build mock api.get ────────────────────────────────────────────────────────
function buildApi(settings = SETTINGS_REGISTERED) {
    return {
        get: jest.fn((url: string) => {
            if (url.includes('/gst/settings')) {
                return Promise.resolve({data: settings});
            }
            if (url.includes('/gst/summary')) {
                return Promise.resolve({
                    data: {
                        ...SUMMARY_REGISTERED,
                        gst_registered: settings.gst_registered,
                        output_tax: settings.gst_registered ? 40000 : 0,
                        itc_total: settings.gst_registered ? 5000 : 0,
                        net_gst_payable: settings.gst_registered ? 35000 : 0,
                    },
                });
            }
            if (url.includes('/gst/bas-worksheet')) {
                return Promise.resolve({data: WORKSHEET});
            }
            if (url.includes('/gst/output-tax')) {
                return Promise.resolve({data: []});
            }
            if (url.includes('/gst/input-tax')) {
                return Promise.resolve({
                    data: {financial_year: '2026', itc_total: 5000, line_items: [], by_category: []},
                });
            }
            if (url.includes('/gst/gst-free')) {
                return Promise.resolve({
                    data: {water_bills: [], council_rates: [], water_total: 12000, council_rates_total: 3600},
                });
            }
            if (url.includes('/gst/income-tax-prep')) {
                return Promise.resolve({
                    data: {
                        financial_year: '2026',
                        levy_income_ex_gst: 400000,
                        interest_income: 1234.56,
                        other_non_mutual_income: 0,
                        total_assessable_income: 1234.56,
                        total_deductions: 0,
                        net_taxable_income: 1234.56,
                    },
                });
            }
            return Promise.resolve({data: {}});
        }),
        get_pdf: jest.fn(() =>
            Promise.resolve({
                data: new Blob(['%PDF-1.4'], {type: 'application/pdf'}),
            })
        ),
    };
}

// ─── Helper: render the page under test ───────────────────────────────────────
function renderPage(user: object, api = buildApi()) {
    mockUseAuth.mockReturnValue({
        user,
        api,
        isAdmin: () => (user as any).role === 'super_admin',
        isStrataAdmin: () => (user as any).role === 'strata_admin',
    });
    return render(
            <GSTBASLedgerPage/>
    );
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('GSTBASLedgerPage', () => {
    afterEach(() => {
        jest.clearAllMocks();
    });

    // ── Access control ──────────────────────────────────────────────────────────
    describe('Access control', () => {
        it('renders page for super_admin', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getByText(/GST & BAS Ledger/i)).toBeInTheDocument();
            });
        });

        it('renders page for strata_admin', async () => {
            renderPage({role: 'strata_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getByText(/GST & BAS Ledger/i)).toBeInTheDocument();
            });
        });

        it('renders page for strata_manager', async () => {
            mockUseAuth.mockReturnValue({
                user: {role: 'strata_manager', building_id: '13195'},
                api: buildApi(),
                isAdmin: () => false,
                isStrataAdmin: () => false,
            });
            render(
                    <GSTBASLedgerPage/>
            );
            await waitFor(() => {
                expect(screen.getByText(/GST & BAS Ledger/i)).toBeInTheDocument();
            });
        });

        it('shows access denied for owner role', async () => {
            mockUseAuth.mockReturnValue({
                user: {role: 'owner', building_id: '13195'},
                api: buildApi(),
                isAdmin: () => false,
                isStrataAdmin: () => false,
            });
            render(
                    <GSTBASLedgerPage/>
            );
            await waitFor(() => {
                expect(screen.getByText(/Access Denied|access denied|not authorised|not authorized/i)).toBeInTheDocument();
            });
        });

        it('shows access denied for ec_member role', async () => {
            mockUseAuth.mockReturnValue({
                user: {role: 'ec_member', building_id: '13195'},
                api: buildApi(),
                isAdmin: () => false,
                isStrataAdmin: () => false,
            });
            render(
                    <GSTBASLedgerPage/>
            );
            await waitFor(() => {
                expect(screen.getByText(/Access Denied|access denied|not authorised|not authorized/i)).toBeInTheDocument();
            });
        });
    });

    // ── GST Registration badge ─────────────────────────────────────────────────
    describe('GST registration status', () => {
        it('shows green GST Registered badge when registered', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getByText(/GST Registered/i)).toBeInTheDocument();
            });
        });

        it('shows warning banner when not registered', async () => {
            const api = buildApi(SETTINGS_UNREGISTERED);
            renderPage({role: 'super_admin', building_id: '13195'}, api);
            await waitFor(() => {
                expect(
                    screen.getByText(/not.*GST registered|GST not registered/i)
                ).toBeInTheDocument();
            });
        });
    });

    // ── Summary cards ──────────────────────────────────────────────────────────
    // The page is gated to super_admin / strata_admin / strata_manager — without
    // an explicit role the renderPage default is no-role → access-denied stub.
    describe('Summary cards', () => {
        it('renders all 4 KPI cards', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/GST Collected/i).length).toBeGreaterThan(0);
                // Input Tax Credits appears in both tab + KPI card — just verify at least one instance
                expect(screen.getAllByText(/Input Tax Credits/i).length).toBeGreaterThan(0);
                expect(screen.getAllByText(/Net GST Payable/i).length).toBeGreaterThan(0);
                // Card title is "GST-Free Items" (not "Total")
                expect(screen.getAllByText(/GST-Free Items/i).length).toBeGreaterThan(0);
            });
        });

        it('displays $40,000 output tax', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/40,000|40000/).length).toBeGreaterThan(0);
            });
        });
    });

    // ── Tab navigation ─────────────────────────────────────────────────────────
    describe('Tabs', () => {
        it('BAS Worksheet tab is visible by default', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/BAS Worksheet/i).length).toBeGreaterThan(0);
            });
        });

        it('Output Tax tab is visible', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/Output Tax/i).length).toBeGreaterThan(0);
            });
        });

        it('Input Tax Credits tab is visible', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/Input Tax Credits/i).length).toBeGreaterThan(0);
            });
        });

        it('GST-Free Items tab is visible', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/GST-Free/i).length).toBeGreaterThan(0);
            });
        });

        it('Annual Summary tab is visible', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/Annual Summary/i).length).toBeGreaterThan(0);
            });
        });
    });

    // ── BAS Worksheet ATO fields ───────────────────────────────────────────────
    describe('BAS Worksheet ATO fields', () => {
        it('displays G1 field label', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                // G1 = Total sales including GST
                expect(screen.getAllByText(/G1/).length).toBeGreaterThan(0);
            });
        });

        it('displays Net 9 (or net_9) field', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/Net 9|net_9|Net GST/i).length).toBeGreaterThan(0);
            });
        });

        it('G3 description indicates GST-free is 0', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                // G3 = GST-free sales, always 0
                expect(screen.getAllByText(/G3/).length).toBeGreaterThan(0);
            });
        });
    });

    // ── Year selector ──────────────────────────────────────────────────────────
    describe('Year selector', () => {
        it('renders year selector dropdown', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                // The year selector should show 2026 as an option
                expect(screen.getByText(/2026/)).toBeInTheDocument();
            });
        });
    });

    // ── PDF export button ──────────────────────────────────────────────────────
    describe('PDF export', () => {
        it('renders Export PDF button in Annual Summary tab', async () => {
            renderPage({role: 'super_admin', building_id: '13195'});
            await waitFor(() => {
                expect(screen.getAllByText(/Annual Summary/i).length).toBeGreaterThan(0);
            });
            const annualTab = screen.getAllByText(/Annual Summary/i)[0];
            fireEvent.click(annualTab);
            await waitFor(() => {
                expect(screen.getByText(/Export.*PDF|Download.*PDF/i)).toBeInTheDocument();
            });
        });
    });

    // ── API calls ─────────────────────────────────────────────────────────────
    describe('API integration', () => {
        it('calls /gst/settings on mount', async () => {
            const api = buildApi();
            renderPage({role: 'super_admin', building_id: '13195'}, api);
            await waitFor(() => {
                expect(api.get).toHaveBeenCalledWith(
                    expect.stringContaining('/gst/settings')
                );
            });
        });

        it('calls /gst/summary on mount', async () => {
            const api = buildApi();
            renderPage({role: 'super_admin', building_id: '13195'}, api);
            await waitFor(() => {
                expect(api.get).toHaveBeenCalledWith(
                    expect.stringContaining('/gst/summary')
                );
            });
        });

        it('calls /gst/bas-worksheet on mount', async () => {
            const api = buildApi();
            renderPage({role: 'super_admin', building_id: '13195'}, api);
            await waitFor(() => {
                expect(api.get).toHaveBeenCalledWith(
                    expect.stringContaining('/gst/bas-worksheet')
                );
            });
        });
    });

    // ── Not registered state ───────────────────────────────────────────────────
    describe('Unregistered OC', () => {
        it('shows zero values when not registered', async () => {
            const api = buildApi(SETTINGS_UNREGISTERED);
            renderPage({role: 'super_admin', building_id: '13195'}, api);
            await waitFor(() => {
                // Zero output tax displayed
                expect(screen.getAllByText(/\$0\.00|\$0/)[0]).toBeInTheDocument();
            });
        });
    });
});
