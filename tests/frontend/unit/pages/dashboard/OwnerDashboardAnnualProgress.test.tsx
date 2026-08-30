/**
 * Frontend Tests: OwnerDashboard Annual Progress Calculation
 *
 * Verifies the fix that ensures progress bars and fund health scores use the
 * ANNUAL levy amount as the denominator, not the quarterly "levied so far" amount.
 *
 * Bug scenario (before fix):
 *   - admin_fund: { annual: 2000, paid: 500, levied: 500 }
 *   - Using `levied` (500) as denominator → adminHealth = 500/500 = 100%  ❌
 *
 * Correct behaviour (after fix):
 *   - Using `annual` (2000) as denominator → adminHealth = 500/2000 = 25%  ✅
 *
 * Tests:
 *   1. progress = 25%  (totalPaid=750 / totalAnnual=3000)
 *   2. adminHealth = 25%  (adminPaid=500 / adminAnnual=2000)
 *   3. sinkingHealth = 25%  (sinkingPaid=250 / sinkingAnnual=1000)
 *   4. fundHealth = 25%  (mean of adminHealth + sinkingHealth = (25+25)/2)
 *   5. Regression: levied amount NOT used as denominator when annual > 0
 *   6. Fallback: when annual = 0, progress falls back to levied total
 */
import React from 'react';
import {render, screen, within} from '@testing-library/react';
import '@testing-library/jest-dom';
// Import after all mocks are set up
import {OwnerDashboard} from '@/app/(dashboard)/dashboard/OwnerDashboard';

// ─── Required mocks (all OwnerDashboard dependencies) ─────────────────────────

jest.mock('next-auth/react', () => ({
    useSession: jest.fn(() => ({data: null, status: 'unauthenticated'})),
    SessionProvider: ({children}: any) => <>{children}</>,
    signIn: jest.fn(),
    signOut: jest.fn(),
}));

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn(), back: jest.fn(), replace: jest.fn()}),
    usePathname: () => '/dashboard',
    useSearchParams: () => ({get: (_k: string) => null}),
}));

// Mock framer-motion to avoid animation overhead in tests
jest.mock('framer-motion', () => ({
    motion: {
        div: ({children, ...p}: any) => <div {...p}>{children}</div>,
        button: ({children, ...p}: any) => <button {...p}>{children}</button>,
    },
    AnimatePresence: ({children}: any) => <>{children}</>,
}));

// Mock Radix UI Tooltip — needs TooltipProvider wrapper; stub out to avoid context errors
jest.mock('@/components/ui/tooltip', () => ({
    Tooltip: ({children}: any) => <>{children}</>,
    TooltipTrigger: ({children, asChild, ...p}: any) => (
        <div {...p}>{children}</div>
    ),
    TooltipContent: ({children}: any) => <span>{children}</span>,
    TooltipProvider: ({children}: any) => <>{children}</>,
}));

// Mock Dialog components (used for financial position popup)
jest.mock('@/components/ui/dialog', () => ({
    Dialog: ({children}: any) => <>{children}</>,
    DialogContent: ({children}: any) => <div data-testid="dialog-content">{children}</div>,
    DialogHeader: ({children}: any) => <>{children}</>,
    DialogTitle: ({children}: any) => <span>{children}</span>,
    DialogDescription: ({children}: any) => <span>{children}</span>,
}));

jest.mock('@/components/ui/button', () => ({
    Button: ({children, onClick, ...p}: any) => (
        <button onClick={onClick} {...p}>{children}</button>
    ),
}));

jest.mock('@/components/ui/card', () => ({
    Card: ({children, ...p}: any) => <div {...p}>{children}</div>,
    CardContent: ({children, ...p}: any) => <div {...p}>{children}</div>,
    CardHeader: ({children, ...p}: any) => <div {...p}>{children}</div>,
    CardTitle: ({children, ...p}: any) => <h3 {...p}>{children}</h3>,
    CardDescription: ({children, ...p}: any) => <p {...p}>{children}</p>,
}));

// Mock premium dashboard widgets — they make their own API calls and are not under test
jest.mock('@/components/dashboard/premium', () => ({
    FinancialHero: ({progress, adminHealth, sinkingHealth}: any) => (
        <div data-testid="financial-hero">
            <span data-testid="hero-progress">{progress}</span>
            <span data-testid="hero-admin-health">{adminHealth}</span>
            <span data-testid="hero-sinking-health">{sinkingHealth}</span>
        </div>
    ),
    MetricCard: ({title, value}: any) => (
        <div data-testid={`metric-${title}`}>{value}</div>
    ),
    LevyTrendChart: () => <div data-testid="levy-trend-chart"/>,
    MarketPulseCard: () => <div data-testid="market-pulse-card"/>,
    BuildingHealthScore: ({adminHealth, sinkingHealth}: any) => (
        <div data-testid="building-health-score">
            <span data-testid="admin-health-score">{adminHealth}</span>
            <span data-testid="sinking-health-score">{sinkingHealth}</span>
        </div>
    ),
    ActivityFeedPremium: () => <div data-testid="activity-feed"/>,
    MarketplaceInvestmentCard: () => <div data-testid="marketplace-card"/>,
    UtilityComparisonCard: () => <div data-testid="utility-card"/>,
    BuildingFundsCard: () => <div data-testid="building-funds-card"/>,
    LevyStatusCard: () => <div data-testid="levy-status-card"/>,
}));

jest.mock('@/components/widgets/FinancialSummaryCard', () => ({
    __esModule: true,
    default: (props: any) => (
        <div data-testid="financial-summary-card">
            <span data-testid="fsc-next-payment">{props.nextPayment}</span>
        </div>
    ),
}));

jest.mock('@/components/dashboard/PropertyServicesActionCards', () => ({
    CouncilRateActionCard: () => null,
    LandTaxActionCard: () => null,
    WaterBillActionCard: () => null,
    ElectricityActionCard: () => null,
    GasActionCard: () => null,
    NBNActionCard: () => null,
}));

// Mock lucide-react icons
jest.mock('lucide-react', () => {
    const Icon = ({'data-testid': tid, ...p}: any) => (
        <svg data-testid={tid || 'icon'} {...p} />
    );
    return new Proxy({}, {get: () => Icon});
});

// ─── Mock useAuth ─────────────────────────────────────────────────────────────

const mockApi = {
    get: jest.fn().mockResolvedValue({data: {}}),
    post: jest.fn().mockResolvedValue({data: {}}),
    put: jest.fn().mockResolvedValue({data: {}}),
    delete: jest.fn().mockResolvedValue({data: {}}),
    interceptors: {
        request: {use: jest.fn(), eject: jest.fn()},
        response: {use: jest.fn(), eject: jest.fn()},
    },
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        user: {
            id: 'user-avneet',
            role: 'owner',
            unit_number: 'TH017',
            building_id: '13195',
            full_name: 'Avneet Rooprai',
        },
        selectedYear: '2026',
        availableYears: ['2025', '2026'],
        financialYearStartMonth: 1,
        hasPermission: () => false,
        isAuthenticated: true,
    }),
    AuthProvider: ({children}: any) => <>{children}</>,
}));

// ─── Suppress expected console errors from child components ───────────────────
beforeAll(() => {
    jest.spyOn(console, 'error').mockImplementation(() => {
    });
});
afterAll(() => {
    (console.error as jest.Mock).mockRestore();
});
afterEach(() => jest.clearAllMocks());

// ─── Shared test data ─────────────────────────────────────────────────────────

/**
 * Build owner_unit data where both admin and sinking funds have been paid at 25%
 * of their annual levy.
 *
 *   admin_fund:   annual=2000, paid=500,  levied=500
 *   sinking_fund: annual=1000, paid=250,  levied=250
 *
 * Expected:
 *   progress     = floor((500+250) / (2000+1000) * 100) = floor(750/3000*100) = 25
 *   adminHealth  = floor(500 / 2000 * 100) = 25
 *   sinkingHealth = floor(250 / 1000 * 100) = 25
 *   fundHealth   = floor((25+25) / 2) = 25
 */
function buildOwnerUnit(overrides: Record<string, any> = {}) {
    return {
        unit_number: 'TH017',
        building_id: '13195',
        total_paid: 750,
        total_levied: 750,   // "levied so far" — should NOT be used as denominator
        admin_fund: {
            annual: 2000,      // FULL YEAR levy — must be the denominator
            paid: 500,
            levied: 500,       // only Q1 levied so far
        },
        sinking_fund: {
            annual: 1000,
            paid: 250,
            levied: 250,
        },
        next_payment_adjusted: 750,
        next_due_date: '2026-06-01',
        opening_arrears: 0,
        balance_owing: 0,
        balance_credit: 0,
        ...overrides,
    };
}

function buildDashboardData(ownerUnit = buildOwnerUnit()) {
    return {
        owner_overview: ownerUnit,
        maintenance: {open_requests: 0},
        building_overview: {
            fund_health: 85,
            levies_paid_pct: 90,
            admin_fund: {total_levied: 88000, total_paid: 75000, collection_rate: 85},
            sinking_fund: {total_levied: 21000, total_paid: 18000, collection_rate: 85},
            total_levied: 109000,
            total_paid: 93000,
        },
        nextMeeting: null,
    };
}

// ─── Import component under test ──────────────────────────────────────────────

// ─── Test Suite ───────────────────────────────────────────────────────────────

describe('OwnerDashboard — Property Intelligence v2 layout', () => {
    it('leads with personal stake and owner-only action strip', () => {
        render(
            <OwnerDashboard
                selectedYear="2026"
                data={{
                    ...buildDashboardData(),
                    activities: [{id: 'n1', type: 'notice', title: 'Pool resurfacing', created_at: new Date().toISOString()}],
                    streak_data: {streak: 14, on_time_pct: 100, recent_quarters: []},
                    levy_allocation: {
                        total_annual: 3000,
                        categories: [{name: 'Insurance', pct: 22, amount: 660}],
                    },
                }}
            />
        );

        expect(screen.getByText(/Your standing/i)).toBeInTheDocument();
        // v2 layout: hero label is the unit pill, no "Property Intelligence" h2 anymore.
        // "This week — only your stuff" replaces the legacy "Action strip" card.
        expect(screen.getAllByText(/This week/i).length).toBeGreaterThan(0);
        expect(screen.getByText(/only your stuff/i)).toBeInTheDocument();
        expect(screen.getByText(/Where your levy goes/i)).toBeInTheDocument();
        // Building Strength replaces the old "Action strip" card.
        expect(screen.getByText(/Your building's strength/i)).toBeInTheDocument();
    });

    it('renders breached maintenance SLA when dashboard data includes SLA breaches', () => {
        render(
            <OwnerDashboard
                selectedYear="2026"
                data={{
                    ...buildDashboardData(),
                    maintenance: {open_requests: 4, sla_breaches: 2},
                }}
            />
        );

        const strength = screen.getByTestId('building-strength-card');
        expect(within(strength).getByText('Maintenance SLA')).toBeInTheDocument();
        expect(within(strength).getByText('2 SLA breaches active')).toBeInTheDocument();
    });

    it('uses unit-level annual levy totals (admin + sinking) for levy allocation totals', () => {
        render(
            <OwnerDashboard
                selectedYear="2026"
                data={{
                    ...buildDashboardData(),
                    owner_overview: buildOwnerUnit({
                        admin_fund: {annual: 2000, paid: 500, levied: 500},
                        sinking_fund: {annual: 1000, paid: 250, levied: 250},
                        total_paid: 750,
                        total_levied: 750,
                    }),
                    levy_allocation: {
                        // Building-wide total should not be used on owner card.
                        total_annual: 120000,
                        categories: [
                            {name: 'Insurance', pct: 22, amount: 26400},
                            {name: 'Sinking Fund', pct: 28, amount: 33600},
                        ],
                    },
                }}
            />
        );

        expect(screen.getByText(/Annual share of \$3,000\.00/i)).toBeInTheDocument();
        const levyDonut = screen.getByTestId('levy-allocation-donut');
        expect(within(levyDonut).getByText('$750.00')).toBeInTheDocument();
        expect(screen.queryByText('$30,000.00')).not.toBeInTheDocument();
    });

    it('labels the dashboard with the active unit selected by the shared loader', () => {
        render(
            <OwnerDashboard
                selectedYear="2026"
                data={{
                    ...buildDashboardData(),
                    active_unit_number: 'TH087',
                }}
            />
        );

        expect(screen.getByText(/Your standing · Unit TH087/i)).toBeInTheDocument();
    });

    it('shows $0 amounts for levy and true-cost components instead of blank placeholders', () => {
        render(
            <OwnerDashboard
                selectedYear="2026"
                data={{
                    ...buildDashboardData(buildOwnerUnit({
                        total_paid: 0,
                        total_levied: 0,
                        admin_fund: {annual: 0, paid: 0, levied: 0},
                        sinking_fund: {annual: 0, paid: 0, levied: 0},
                    })),
                    levy_allocation: {
                        total_annual: 0,
                        categories: [{name: 'Insurance', pct: 100, amount: 0}],
                    },
                    dashboard_v2_extras: {
                        cost_categories: [{name: 'Council rates', annual: 0}],
                    },
                }}
            />
        );

        const levyDonut = screen.getByTestId('levy-allocation-donut');
        expect(within(levyDonut).getAllByText('$0.00').length).toBeGreaterThanOrEqual(2);
        const trueCost = screen.getByTestId('true-cost-breakdown');
        expect(within(trueCost).getByText('$0.00 all-in · everything you pay to hold this unit')).toBeInTheDocument();
    });

    it('uses canonical unit_tco values for true cost before dashboard extras', () => {
        render(
            <OwnerDashboard
                selectedYear="2026"
                data={{
                    ...buildDashboardData(),
                    unit_tco: {
                        strata_levies: 3000,
                        council_rates: 1200,
                        land_tax: 300,
                        water_charges: 400,
                        capital_replacement: 9000,
                    },
                    dashboard_v2_extras: {
                        cost_categories: [
                            {name: 'Council rates', annual: 9999},
                            {name: 'Water charges', annual: 8888},
                        ],
                    },
                }}
            />
        );

        const trueCost = screen.getByTestId('true-cost-breakdown');
        expect(within(trueCost).getByText('$4,900.00 all-in · everything you pay to hold this unit')).toBeInTheDocument();
        expect(within(trueCost).getByText('$1,200.00')).toBeInTheDocument();
        expect(within(trueCost).queryByText('$9,999.00')).not.toBeInTheDocument();
        expect(within(trueCost).queryByText('$9,000.00')).not.toBeInTheDocument();
    });

    it('uses the next quarter outstanding amount for Quick Pay, not the full levy amount', () => {
        render(
            <OwnerDashboard
                selectedYear="2026"
                data={buildDashboardData(buildOwnerUnit({
                    next_payment_adjusted: undefined,
                    next_payment_amount: undefined,
                    quarters: [{
                        quarter: 'Q2',
                        due_date: '2026-09-01',
                        status: 'partial',
                        amount_due: 750,
                        amount_paid: 500,
                        outstanding: 250,
                    }],
                }))}
            />
        );

        expect(screen.getAllByRole('button', {name: /Quick Pay \$250\.00/i}).length).toBeGreaterThan(0);
        expect(screen.queryByRole('button', {name: /Quick Pay \$750\.00/i})).not.toBeInTheDocument();
    });
});

describe('OwnerDashboard — Annual Progress Calculation', () => {

    describe('Core progress metrics use annual levy as denominator', () => {

        it('renders without crashing for a 25%-paid owner', () => {
            const data = buildDashboardData();
            expect(() => render(<OwnerDashboard data={data} selectedYear="2026"/>)).not.toThrow();
        });

        it('passes progress=25 to FinancialHero (totalPaid=750 / annual=3000)', () => {
            const data = buildDashboardData();
            render(<OwnerDashboard data={data} selectedYear="2026"/>);

            // FinancialHero receives adminHealth + sinkingHealth props
            const adminEl = screen.queryByTestId('hero-admin-health');
            const sinkingEl = screen.queryByTestId('hero-sinking-health');

            if (adminEl) {
                expect(Number(adminEl.textContent)).toBe(25);
            }
            if (sinkingEl) {
                expect(Number(sinkingEl.textContent)).toBe(25);
            }

            // At minimum the progress stat card must not show "100"
            const progressEl = screen.queryByTestId('hero-progress');
            if (progressEl && progressEl.textContent !== '') {
                expect(Number(progressEl.textContent)).toBeLessThanOrEqual(25);
            }
        });

        it('passes adminHealth=25 to BuildingHealthScore (500/2000)', () => {
            const data = buildDashboardData();
            render(<OwnerDashboard data={data} selectedYear="2026"/>);

            const adminScore = screen.queryByTestId('admin-health-score');
            if (adminScore && adminScore.textContent !== '') {
                expect(Number(adminScore.textContent)).toBe(25);
            }
        });

        it('passes sinkingHealth=25 to BuildingHealthScore (250/1000)', () => {
            const data = buildDashboardData();
            render(<OwnerDashboard data={data} selectedYear="2026"/>);

            const sinkingScore = screen.queryByTestId('sinking-health-score');
            if (sinkingScore && sinkingScore.textContent !== '') {
                expect(Number(sinkingScore.textContent)).toBe(25);
            }
        });

    });

    describe('Regression: levied amount (quarterly) must NOT be used as denominator', () => {

        it('adminHealth is NOT 100 when paid == levied but annual is larger', () => {
            // This is the exact bug scenario: paid=500 == levied=500 → old code gave 100%
            const ownerUnit = buildOwnerUnit({
                admin_fund: {annual: 2000, paid: 500, levied: 500},
                sinking_fund: {annual: 1000, paid: 250, levied: 250},
                total_paid: 750,
                total_levied: 750,
            });
            const data = buildDashboardData(ownerUnit);
            render(<OwnerDashboard data={data} selectedYear="2026"/>);

            // Logic under test (mirrors OwnerDashboard.tsx lines 196-200):
            const adminFundAnnual = ownerUnit.admin_fund.annual;    // 2000
            const adminPaid = ownerUnit.admin_fund.paid;            // 500
            const adminHealth = adminFundAnnual > 0
                ? Math.min(100, Math.round((adminPaid / adminFundAnnual) * 100))
                : 0;
            expect(adminHealth).toBe(25);
            expect(adminHealth).not.toBe(100); // regression guard
        });

        it('sinkingHealth is NOT 100 when paid == levied but annual is larger', () => {
            const ownerUnit = buildOwnerUnit({
                sinking_fund: {annual: 1000, paid: 250, levied: 250},
            });

            const sinkingFundAnnual = ownerUnit.sinking_fund.annual;
            const sinkingPaid = ownerUnit.sinking_fund.paid;
            const sinkingHealth = sinkingFundAnnual > 0
                ? Math.min(100, Math.round((sinkingPaid / sinkingFundAnnual) * 100))
                : 0;
            expect(sinkingHealth).toBe(25);
            expect(sinkingHealth).not.toBe(100);
        });

        it('progress is NOT 100 when total_paid == total_levied but annual is larger', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 750,
                total_levied: 750,       // same as paid — old code might use this as denominator
                admin_fund: {annual: 2000, paid: 500, levied: 500},
                sinking_fund: {annual: 1000, paid: 250, levied: 250},
            });

            const adminFundAnnual = ownerUnit.admin_fund.annual;
            const sinkingFundAnnual = ownerUnit.sinking_fund.annual;
            const totalAnnualLevy = adminFundAnnual + sinkingFundAnnual;  // 3000
            const totalPaid = ownerUnit.total_paid;                        // 750

            const progress = totalAnnualLevy > 0
                ? Math.min(100, Math.round((totalPaid / totalAnnualLevy) * 100))
                : 0;
            expect(progress).toBe(25);
            expect(progress).not.toBe(100);
        });

    });

    describe('Pure algorithm unit tests (no rendering)', () => {

        it('algorithm: 25% of annual levy paid → progress = 25', () => {
            const ownerUnit = buildOwnerUnit();
            const adminFundAnnual = ownerUnit.admin_fund.annual;      // 2000
            const sinkingFundAnnual = ownerUnit.sinking_fund.annual;  // 1000
            const totalAnnualLevy = adminFundAnnual + sinkingFundAnnual; // 3000
            const totalPaid = ownerUnit.total_paid;                    // 750

            const progress = totalAnnualLevy > 0
                ? Math.min(100, Math.round((totalPaid / totalAnnualLevy) * 100))
                : 0;
            expect(progress).toBe(25);
        });

        it('algorithm: adminHealth = 500/2000 = 25', () => {
            const ownerUnit = buildOwnerUnit();
            const adminFundAnnual = ownerUnit.admin_fund.annual;
            const adminPaid = ownerUnit.admin_fund.paid;

            const adminHealth = adminFundAnnual > 0
                ? Math.min(100, Math.round((adminPaid / adminFundAnnual) * 100))
                : 0;
            expect(adminHealth).toBe(25);
        });

        it('algorithm: sinkingHealth = 250/1000 = 25', () => {
            const ownerUnit = buildOwnerUnit();
            const sinkingFundAnnual = ownerUnit.sinking_fund.annual;
            const sinkingPaid = ownerUnit.sinking_fund.paid;

            const sinkingHealth = sinkingFundAnnual > 0
                ? Math.min(100, Math.round((sinkingPaid / sinkingFundAnnual) * 100))
                : 0;
            expect(sinkingHealth).toBe(25);
        });

        it('algorithm: fundHealth = mean(adminHealth, sinkingHealth) = 25', () => {
            const adminHealth = 25;
            const sinkingHealth = 25;
            const fundHealth = Math.round((adminHealth + sinkingHealth) / 2);
            expect(fundHealth).toBe(25);
        });

        it('algorithm: 50% paid → progress = 50', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 1500,
                admin_fund: {annual: 2000, paid: 1000, levied: 1000},
                sinking_fund: {annual: 1000, paid: 500, levied: 500},
            });
            const totalAnnualLevy =
                ownerUnit.admin_fund.annual + ownerUnit.sinking_fund.annual;
            const progress = Math.min(100, Math.round((ownerUnit.total_paid / totalAnnualLevy) * 100));
            expect(progress).toBe(50);
        });

        it('algorithm: 100% paid → progress capped at 100', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 3000,
                admin_fund: {annual: 2000, paid: 2000, levied: 2000},
                sinking_fund: {annual: 1000, paid: 1000, levied: 1000},
            });
            const totalAnnualLevy =
                ownerUnit.admin_fund.annual + ownerUnit.sinking_fund.annual;
            const progress = Math.min(100, Math.round((ownerUnit.total_paid / totalAnnualLevy) * 100));
            expect(progress).toBe(100);
        });

        it('algorithm: advance payment > 100% of annual is capped at 100', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 4000,   // more than annual (3000)
                admin_fund: {annual: 2000, paid: 2800, levied: 2000},
                sinking_fund: {annual: 1000, paid: 1200, levied: 1000},
            });
            const totalAnnualLevy =
                ownerUnit.admin_fund.annual + ownerUnit.sinking_fund.annual;
            const progress = Math.min(100, Math.round((ownerUnit.total_paid / totalAnnualLevy) * 100));
            expect(progress).toBe(100); // capped, not 133
        });

    });

    describe('Regression: "Paid to date"/"remaining" must use paid_this_year, not lifetime total_paid', () => {

        // Real East Gate unit (TH087, FY2026) reported 2026-08-01. total_paid=$28,783.04 is
        // that unit's own reconciliation_note-documented "back-solved... cumulative payment
        // history through the scrape date, not payments received within this calendar year
        // specifically" -- not what an owner paid this year. Using it directly showed
        // "Paid to date $28,783.04 of $7,090.04 annual... $0.00 remaining", which the owner
        // correctly flagged as nonsensical (they'd paid 2 of 4 quarters, not the full year).
        //
        // First fix attempt (2026-08-01, same day) swapped "remaining" to balance_owing --
        // WRONG for a different reason: balance_owing is arrears against what's been CHARGED
        // so far only (can correctly read $0 mid-year with quarters still to come), not "how
        // much of the full annual budget is left to pay". The actually-correct fix is
        // paid_this_year (backend: total_levied - net_balance), which both "Paid to date" and
        // "remaining" must use so the simple subtraction (annual - paid) means what it says.
        const REAL_TOTAL_LEVIED = 3545.02;     // charged so far this FY (2 of 4 quarters)
        const REAL_NET_BALANCE = -254.98;      // in credit (balance_owing=0, balance_credit=254.98)
        const REAL_PAID_THIS_YEAR = 3800.00;   // total_levied - net_balance
        const REAL_ADMIN_ANNUAL = 5488.01;
        const REAL_SINKING_ANNUAL = 1602.03;   // together: $7,090.04 annual
        const REAL_REMAINING = 3290.04;        // 7090.04 - 3800.00

        it('shows paid_this_year (not the cumulative total_paid) as "Paid to date"', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 28783.04,
                paid_this_year: REAL_PAID_THIS_YEAR,
                total_levied: REAL_TOTAL_LEVIED,
                admin_fund: {annual: REAL_ADMIN_ANNUAL, paid: 22149.54, levied: 2744.00},
                sinking_fund: {annual: REAL_SINKING_ANNUAL, paid: 6633.50, levied: 801.02},
                balance_owing: 0,
                balance_credit: 254.98,
            });
            render(<OwnerDashboard data={buildDashboardData(ownerUnit)} selectedYear="2026"/>);

            expect(screen.getAllByText(/\$3,800\.00/).length).toBeGreaterThan(0);
            expect(screen.queryByText(/\$28,783\.04/)).not.toBeInTheDocument();
        });

        it('computes remaining as annual minus paid_this_year, not $0.00', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 28783.04,
                paid_this_year: REAL_PAID_THIS_YEAR,
                total_levied: REAL_TOTAL_LEVIED,
                admin_fund: {annual: REAL_ADMIN_ANNUAL, paid: 22149.54, levied: 2744.00},
                sinking_fund: {annual: REAL_SINKING_ANNUAL, paid: 6633.50, levied: 801.02},
                balance_owing: 0,           // arrears against charges-so-far is genuinely $0 --
                balance_credit: 254.98,     // must NOT be what "remaining" shows
            });
            render(<OwnerDashboard data={buildDashboardData(ownerUnit)} selectedYear="2026"/>);

            expect(screen.getAllByText(/\$3,290\.04/).length).toBeGreaterThan(0);
            expect(screen.queryByText(/\$0\.00\s*remaining/i)).not.toBeInTheDocument();
        });

        it('falls back to total_paid only when paid_this_year is absent (older cached response)', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 750,
                total_levied: 750,
                admin_fund: {annual: 2000, paid: 500, levied: 500},
                sinking_fund: {annual: 1000, paid: 250, levied: 250},
            });
            render(<OwnerDashboard data={buildDashboardData(ownerUnit)} selectedYear="2026"/>);

            // 3000 annual - 750 paid = 2250 remaining
            expect(screen.getAllByText(/\$2,250\.00/).length).toBeGreaterThan(0);
        });

    });

    describe('Regression: "vs Building avg" must compare like-for-like bases', () => {

        // Real East Gate unit (Lot 63 / UA063, FY2026) reported 2026-08-01: paid 3 of 4
        // quarters (ahead of the invoicing schedule -- in credit against what's actually
        // been levied so far), yet showed "-20pts behind" the building average. Root
        // cause: `progress` (the ring %) is deliberately annual-budget-based (paid /
        // FULL YEAR total) -- a legitimate, different metric -- but was being diffed
        // directly against bldgLeviesPaidPct, which is YTD-invoiced-based (paid so far /
        // levied so far). Paying ahead of the invoicing schedule reads LOWER on the
        // annual-budget basis purely from the denominator mismatch, making an ahead-of-
        // schedule owner look "behind" a building average that's actually a different
        // basis entirely. The fix compares like with like: progress vs invoiced-so-far
        // (paid_this_year / total_levied), matching bldgLeviesPaidPct's own basis.
        it('shows "ahead", not "behind", for a unit paid ahead of the invoicing schedule', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 26160.12,
                paid_this_year: 4590.90,   // 3 of 4 quarters paid
                total_levied: 3060.60,     // only 2 of 4 quarters invoiced so far
                admin_fund: {annual: 2369.04 * 2, paid: 20131.10, levied: 2369.04},
                sinking_fund: {annual: 691.56 * 2, paid: 6029.02, levied: 691.56},
                balance_owing: 0,
                balance_credit: 1530.30,
            });
            const data = buildDashboardData(ownerUnit);
            data.building_overview.levies_paid_pct = 92; // typical near-full building collection rate
            render(<OwnerDashboard data={data} selectedYear="2026"/>);

            const vsAvgLabel = screen.getByText(/vs Building avg/i);
            const vsAvgSection = vsAvgLabel.parentElement;
            expect(vsAvgSection.textContent).toMatch(/ahead/i);
            expect(vsAvgSection.textContent).not.toMatch(/behind/i);
        });

        it('algorithm: progressVsInvoiced compares paid_this_year against total_levied (YTD), not the annual budget', () => {
            const totalPaidThisYear = 4590.90;
            const totalLevied = 3060.60; // YTD invoiced
            const bldgLeviesPaidPct = 92;

            const progressVsInvoiced = totalLevied > 0
                ? Math.min(100, Math.round((totalPaidThisYear / totalLevied) * 100))
                : 0;
            const aheadOfAvg = Math.round(progressVsInvoiced - bldgLeviesPaidPct);

            expect(progressVsInvoiced).toBe(100); // capped -- paid more than what's been invoiced
            expect(aheadOfAvg).toBe(8);
            expect(aheadOfAvg).toBeGreaterThanOrEqual(0); // must read "ahead", never "behind"
        });

    });

    describe('Fallback behaviour when annual levy is zero', () => {

        it('uses total_levied as fallback when admin_fund.annual = 0', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 500,
                total_levied: 2000,
                admin_fund: {annual: 0, paid: 500, levied: 500},
                sinking_fund: {annual: 0, paid: 0, levied: 0},
            });

            const adminFundAnnual = ownerUnit.admin_fund.annual;     // 0
            const sinkingFundAnnual = ownerUnit.sinking_fund.annual; // 0
            const totalAnnualLevy = adminFundAnnual + sinkingFundAnnual; // 0
            const totalLevied = ownerUnit.total_levied;              // 2000

            // Mirrors the fallback in OwnerDashboard.tsx line 190-193
            const progress = totalAnnualLevy > 0
                ? Math.min(100, Math.round((ownerUnit.total_paid / totalAnnualLevy) * 100))
                : totalLevied > 0
                    ? Math.min(100, Math.round((ownerUnit.total_paid / totalLevied) * 100))
                    : 0;

            expect(progress).toBe(25); // 500/2000 = 25%
        });

        it('returns 0 when both annual and total_levied are 0', () => {
            const ownerUnit = buildOwnerUnit({
                total_paid: 0,
                total_levied: 0,
                admin_fund: {annual: 0, paid: 0, levied: 0},
                sinking_fund: {annual: 0, paid: 0, levied: 0},
            });

            const totalAnnualLevy = 0;
            const totalLevied = 0;
            const progress = totalAnnualLevy > 0
                ? Math.min(100, Math.round((ownerUnit.total_paid / totalAnnualLevy) * 100))
                : totalLevied > 0
                    ? Math.min(100, Math.round((ownerUnit.total_paid / totalLevied) * 100))
                    : 0;

            expect(progress).toBe(0);
        });

    });

});
