import React from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
const mockDownloadTaxSummary = jest.fn();
const mockApiClient = {get: mockApiGet, put: mockApiPut};
let mockAuthState: any;
let mockFinanceOverview: any;

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
}));

jest.mock('framer-motion', () => ({
    motion: new Proxy({}, {get: () => ({children, ...props}: any) => <div {...props}>{children}</div>}),
    AnimatePresence: ({children}: any) => <>{children}</>,
}));

jest.mock('lucide-react', () =>
    new Proxy({}, {get: (_target, name) => () => <span data-testid={`icon-${String(name)}`}/>})
);

jest.mock('sonner', () => ({toast: {error: jest.fn(), info: jest.fn(), success: jest.fn()}}));

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuthState || ({
        user: {
            id: 'owner-1',
            role: 'owner',
            full_name: 'Legacy Owner',
            unit_number: '',
            owned_units: ['TH087'],
        },
        token: 'jwt-token',
        selectedYear: '2026',
        setSelectedYear: jest.fn(),
        selectedBuilding: {id: '13195', name: 'East Gate', lot_count: 87},
        selectedUnit: null,
        isManager: jest.fn(() => false),
        isECMember: jest.fn(() => false),
        api: mockApiClient,
    }),
}));

jest.mock('@/hooks/useTaxSummary', () => ({
    useTaxSummary: () => ({loading: false, downloadTaxSummary: mockDownloadTaxSummary}),
}));

jest.mock('@/components/widgets/YearSelector', () => () => <div>Year selector</div>);
jest.mock('@/components/widgets/FinancialSummaryCard', () => ({financialData}: any) => (
    <div>Levy summary {financialData?.unit_number}</div>
));
jest.mock('@/components/payments/PaymentModal', () => () => null);
jest.mock('@/components/ui/card', () => ({
    Card: ({children}: any) => <section>{children}</section>,
    CardContent: ({children}: any) => <div>{children}</div>,
    CardHeader: ({children}: any) => <div>{children}</div>,
    CardTitle: ({children}: any) => <h3>{children}</h3>,
    CardDescription: ({children}: any) => <p>{children}</p>,
}));
jest.mock('@/components/ui/button', () => ({
    Button: ({children, ...props}: any) => <button {...props}>{children}</button>,
}));
jest.mock('@/components/ui/badge', () => ({Badge: ({children}: any) => <span>{children}</span>}));
jest.mock('@/components/ui/dialog', () => ({
    Dialog: ({children}: any) => <>{children}</>,
    DialogContent: ({children}: any) => <div>{children}</div>,
    DialogHeader: ({children}: any) => <div>{children}</div>,
    DialogTitle: ({children}: any) => <h3>{children}</h3>,
    DialogDescription: ({children}: any) => <p>{children}</p>,
}));
jest.mock('@/components/ui/input', () => ({Input: (props: any) => <input {...props}/>}));

jest.mock('@/components/dashboard/MarketSnapshotCard', () => ({
    __esModule: true,
    default: ({data}: any) => <div>Market {data?.suburb || 'none'}</div>,
}));
jest.mock('@/components/dashboard/DashboardDetailModal', () => ({
    __esModule: true,
    default: ({children}: any) => <div>{children}</div>,
}));
jest.mock('@/components/dashboard/PropertyServicesActionCards', () => ({
    CouncilRateActionCard: ({unitNumber}: any) => <div>Council {unitNumber}</div>,
    LandTaxActionCard: ({unitNumber}: any) => <div>Land tax {unitNumber}</div>,
    WaterBillActionCard: ({unitNumber}: any) => <div>Water {unitNumber}</div>,
    ElectricityActionCard: ({unitNumber}: any) => <div>Electricity {unitNumber}</div>,
    GasActionCard: ({unitNumber}: any) => <div>Gas {unitNumber}</div>,
    NBNActionCard: ({unitNumber}: any) => <div>NBN {unitNumber}</div>,
}));
jest.mock('@/components/dashboard/LevyAllocationDonut', () => ({
    __esModule: true,
    default: ({totalAnnual}: any) => <div>Levy allocation {totalAnnual}</div>,
}));
jest.mock('@/components/dashboard/PaymentStreakCard', () => ({
    __esModule: true,
    default: ({data}: any) => <div>Payment streak {data?.streak}</div>,
}));
jest.mock('@/components/dashboard/CommunityPulseFeed', () => ({
    __esModule: true,
    default: ({items}: any) => <div>Community {items?.length || 0}</div>,
}));
jest.mock('@/components/dashboard/CapitalForMeCard', () => ({
    __esModule: true,
    default: ({entitlementPct}: any) => <div>Capital share {entitlementPct}</div>,
}));
jest.mock('@/components/dashboard/BuildingStrengthCard', () => ({
    __esModule: true,
    default: ({score}: any) => <div>Building strength {score}</div>,
}));
jest.mock('@/components/dashboard/YourRequestsCard', () => ({
    __esModule: true,
    default: ({requests}: any) => <div>Your requests {requests?.length || 0}</div>,
}));

function responseFor(url: string) {
    if (url === '/finance/unit-dashboard-overview/TH087?year=2026') {
        return mockFinanceOverview || {
            unit_number: 'TH087',
            unit_entitlement: 134,
            admin_fund: {annual: 1000, paid: 500},
            sinking_fund: {annual: 400, paid: 400},
            total_levied: 1400,
            total_paid: 900,
            yearly_forecast: {paid_so_far: 900, total_levies: 1400},
            quarters: [],
            source: 'postgres_ledger',
        };
    }
    if (url === '/analytics/my-streak?unit_number=TH087') {
        return {streak: 3, on_time_pct: 75, recent_quarters: [], total_quarters: 4, on_time_count: 3, entitlement_pct: 1.54};
    }
    if (url === '/units/TH087/market-valuation') return {estimated_market_price: 750000};
    if (url.startsWith('/analytics/sinking-fund-forecast')) return {projection: [{year: 2026, closing_balance: 1}]};
    if (url === '/analytics/maintenance-stats') return {open_requests: 1, avg_resolution_days: 4};
    if (url.startsWith('/analytics/activities')) return [];
    if (url === '/analytics/market-snapshot') return {suburb: 'Denman Prospect', median_price: 800000};
    if (url === '/annual-levies') return [{year: '2026', admin_levy_per_uoe_annual: 1, sinking_levy_per_uoe_annual: 1}];
    if (url === '/agm') return [];
    if (url === '/analytics/compliance-summary') return {completed: 1, total: 1, overdue: 0, status: 'healthy'};
    if (url === '/intelligence/summary') return {asset_health_score: 90, high_risk_assets_count: 0};
    if (url === '/intelligence/capital-shock') return {capital_shock_index: {rows: []}};
    if (url === '/analytics/levy-allocation-breakdown?year=2026') return {total_annual: 1400, categories: []};
    if (url === '/workflow-requests?limit=5') return [];
    if (url === '/security/my-activity') return null;
    if (url === '/documents/important') return [];
    return {};
}

describe('OwnerDashboard legacy page', () => {
    beforeEach(() => {
        mockApiGet.mockClear();
        mockApiPut.mockClear();
        mockApiGet.mockImplementation((url: string) => Promise.resolve({data: responseFor(url)}));
        mockAuthState = null;
        mockFinanceOverview = null;
    });

    it('uses the active owned unit for PostgreSQL-first owner dashboard contracts', async () => {
        const OwnerDashboard = require('@/pages/dashboard/OwnerDashboard').default;

        render(<OwnerDashboard/>);

        await waitFor(() => {
            expect(mockApiGet).toHaveBeenCalledWith('/finance/unit-dashboard-overview/TH087?year=2026');
        });

        expect(mockApiGet).toHaveBeenCalledWith('/analytics/my-streak?unit_number=TH087');
        expect(mockApiGet).toHaveBeenCalledWith('/units/TH087/market-valuation');
        expect(mockApiGet).not.toHaveBeenCalledWith('/finance/summary?year=2026');
        expect((await screen.findAllByText(/Unit TH087/)).length).toBeGreaterThan(0);
        expect(screen.getByText('Council TH087')).toBeInTheDocument();
        expect(screen.getByText('Levy summary TH087')).toBeInTheDocument();
    });

    it('uses shared clamped display rates for owner levy progress and admin fund health', async () => {
        mockFinanceOverview = {
            unit_number: 'TH087',
            unit_entitlement: 134,
            admin_fund: {annual: 1000, paid: 1200},
            sinking_fund: {annual: 400, paid: 400},
            total_levied: 1400,
            total_paid: 1800,
            yearly_forecast: {paid_so_far: 1800, total_levies: 1400},
            quarters: [],
            source: 'postgres_ledger',
        };
        const OwnerDashboard = require('@/pages/dashboard/OwnerDashboard').default;

        render(<OwnerDashboard/>);

        await waitFor(() => {
            expect(screen.getByText('100% of 2026')).toBeInTheDocument();
        });
        expect(screen.getByLabelText(/Fund Health: 100%/)).toBeInTheDocument();
        expect(screen.queryByText('129% of 2026')).not.toBeInTheDocument();
    });

    it('uses a display fallback when no active unit is resolved', async () => {
        mockAuthState = {
            user: {
                id: 'owner-1',
                role: 'owner',
                full_name: 'Legacy Owner',
                unit_number: '',
                owned_units: [],
            },
            token: 'jwt-token',
            selectedYear: '2026',
            setSelectedYear: jest.fn(),
            selectedBuilding: {id: '13195', name: 'East Gate', lot_count: 87},
            selectedUnit: null,
            isManager: jest.fn(() => true),
            isECMember: jest.fn(() => false),
            api: mockApiClient,
        };
        const OwnerDashboard = require('@/pages/dashboard/OwnerDashboard').default;

        render(<OwnerDashboard/>);

        expect(await screen.findByText('My Property View — Unit not selected')).toBeInTheDocument();
        expect(screen.getByText('Unit not selected · East Gate')).toBeInTheDocument();
        expect(screen.getByText('Unit not selected — set your personal market value estimate')).toBeInTheDocument();
        expect(screen.queryByText(/Unit null/)).not.toBeInTheDocument();
        expect(mockApiGet).not.toHaveBeenCalledWith(expect.stringContaining('/finance/unit-dashboard-overview/null'));
    });
});
