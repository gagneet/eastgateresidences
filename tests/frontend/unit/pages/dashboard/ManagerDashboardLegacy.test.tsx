import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

const mockPush = jest.fn();
const mockApiGet = jest.fn();
const mockApiClient = {get: mockApiGet};

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: mockPush}),
}));

jest.mock('next/link', () =>
    function MockLink({children, href}: any) {
        return <a href={href}>{children}</a>;
    }
);

jest.mock('framer-motion', () => ({
    motion: new Proxy({}, {get: () => ({children, ...props}: any) => <div {...props}>{children}</div>}),
}));

jest.mock('lucide-react', () =>
    new Proxy({}, {get: (_target, name) => () => <span data-testid={`icon-${String(name)}`}/>})
);

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        user: {id: 'mgr-1', role: 'strata_manager', full_name: 'Test Manager'},
        token: 'jwt-token',
        selectedYear: '2026',
        setSelectedYear: jest.fn(),
        hasPermission: jest.fn(() => true),
        isAdmin: jest.fn(() => false),
        api: mockApiClient,
    }),
}));

jest.mock('@/components/widgets/YearSelector', () => () => <div>Year selector</div>);
jest.mock('@/components/ui/card', () => ({
    Card: ({children}: any) => <section>{children}</section>,
    CardContent: ({children}: any) => <div>{children}</div>,
    CardHeader: ({children}: any) => <div>{children}</div>,
    CardTitle: ({children}: any) => <h3>{children}</h3>,
}));
jest.mock('@/components/ui/button', () => ({Button: ({children, ...props}: any) => <button {...props}>{children}</button>}));
jest.mock('@/components/ui/badge', () => ({Badge: ({children}: any) => <span>{children}</span>}));

jest.mock('@/components/dashboard/PulseScoreCard', () => ({
    __esModule: true,
    default: ({score, breakdown}: any) => {
        // Axes come from the BACKEND health score now (financial/maintenance/…),
        // not from a locally-derived 'Cash' axis fed by collectionRate. This mock
        // used to assert that coupling, which is the behaviour that let the two
        // dashboards disagree (26/100 vs 31/100 on the same data).
        const financial = Array.isArray(breakdown)
            ? breakdown.find((axis: any) => axis.k === 'Financial')
            : null;
        return <div>Building pulse score {String(score)} financial {String(financial?.v)}</div>;
    },
}));
jest.mock('@/components/dashboard/SinceLastVisit', () => ({
    __esModule: true,
    default: () => <div>Since last visit</div>,
}));
jest.mock('@/components/dashboard/TriageQueue', () => ({
    __esModule: true,
    default: () => <div>Today's triage</div>,
}));
jest.mock('@/components/dashboard/LevyFairnessCard', () => ({
    __esModule: true,
    default: () => <div>Levy fairness</div>,
}));
jest.mock('@/components/dashboard/CompactCalendar', () => ({
    __esModule: true,
    default: () => <div>Calendar</div>,
}));
jest.mock('@/components/dashboard/ReserveRunwayChart', () => ({
    __esModule: true,
    default: () => <div>Reserve runway</div>,
}));
jest.mock('@/components/dashboard/VendorScorecardCard', () => ({
    __esModule: true,
    default: () => <div>Vendor scorecard</div>,
}));
jest.mock('@/components/dashboard/ActivityFeed', () => ({
    __esModule: true,
    default: () => <div>Activity feed</div>,
}));
jest.mock('@/components/dashboard/KPIStatCard', () => ({
    __esModule: true,
    default: ({title, value}: any) => <div>{title}: {value}</div>,
}));
jest.mock('@/components/dashboard/ChartCard', () => ({
    __esModule: true,
    default: ({title}: any) => <div>{title}</div>,
}));
jest.mock('@/components/dashboard/DashboardFooterCue', () => ({
    __esModule: true,
    default: ({children}: any) => <footer>{children}</footer>,
}));
jest.mock('@/components/dashboard/DashboardDetailModal', () => ({
    __esModule: true,
    default: ({isOpen, title, actionLabel, onAction, children}: any) => isOpen ? (
        <div role="dialog" aria-label={title}>
            {children}
            {actionLabel && <button onClick={onAction}>{actionLabel}</button>}
        </div>
    ) : null,
}));
jest.mock('@/components/dashboard/CashPositionCard', () => ({
    __esModule: true,
    default: ({admin, sinking, arrears, collectionRatePct, onOpenArrears}: any) => (
        <section aria-label="Cash position">
            <div>Admin balance {admin.balance}</div>
            <div>Sinking balance {sinking.balance}</div>
            <div>Arrears total {arrears.total}</div>
            <div>Arrears units {arrears.units}</div>
            <div>Collection rate {collectionRatePct}</div>
            <button onClick={onOpenArrears}>Open arrears detail</button>
        </section>
    ),
}));

jest.mock('recharts', () => ({
    Area: () => null,
    AreaChart: ({children}: any) => <div>{children}</div>,
    Bar: () => null,
    BarChart: ({children}: any) => <div>{children}</div>,
    CartesianGrid: () => null,
    Legend: () => null,
    ResponsiveContainer: ({children}: any) => <div>{children}</div>,
    Tooltip: () => null,
    XAxis: () => null,
    YAxis: () => null,
}));

function responseFor(url: string) {
    if (url === '/finance/building-overview?year=2026') {
        return {
            admin_fund: {closing_balance_cents: 21629900},
            sinking_fund: {closing_balance: '$415,614.00'},
            total_outstanding: 17649.7,
            units_in_arrears: 18,
            levies_paid_pct: 84.48,
            arrears_delta_pct: -3,
            collection_delta_pct: 2,
        };
    }
    if (url.startsWith('/stats/building-kpis')) return {collection_rate: 12, total_arrears: 999, units_in_arrears: 99};
    if (url === '/arrears/detail') return [
        {unit_number: 'TH001', total_arrears: 10000},
        {unit_number: 'TH002', total_arrears: 5284.25},
    ];
    if (url === '/analytics/maintenance-stats') return {open_requests: 4, avg_resolution_days: 3};
    // The Building Pulse score is served by the canonical backend health-score
    // endpoint; the dashboard no longer derives it.
    if (url === '/community-dashboard/health-score') {
        return {
            score: 72,
            grade: 'B',
            status: 'ok',
            components: {financial: 65, maintenance: 90, compliance: 100, engagement: 40, dispute: 100},
            unavailable_components: [],
            coverage: 1.0,
        };
    }
    if (url.startsWith('/analytics/activities')) return [];
    if (url.startsWith('/workflow-requests?status=')) return [];
    if (url === '/workflow-requests/stats/triage') return {pending_total: 0, sla_breaches: 0};
    if (url.startsWith('/analytics/levy-benchmarks')) return [];
    if (url === '/admin/stats') return {pending_users: 0, pending_invoices_count: 0};
    if (url.startsWith('/analytics/sinking-fund-forecast')) return {projection: []};
    if (url === '/analytics/compliance-summary') return {percentage: 100, upcoming: []};
    if (url === '/analytics/maintenance/spend-trend') return [];
    if (url === '/ppm/dashboard') return {health_score: 100, overdue_count: 0};
    if (url.startsWith('/ppm/upcoming')) return [];
    if (url.startsWith('/analytics/diff-since')) return {days_since: 1, items: []};
    if (url === '/intelligence/levy-fairness') return {score: 90};
    if (url === '/intelligence/capital-shock') return {capital_shock_index: {rows: []}};
    return {};
}

describe('ManagerDashboard legacy page', () => {
    beforeEach(() => {
        mockPush.mockClear();
        mockApiGet.mockImplementation((url: string) => Promise.resolve({data: responseFor(url)}));
    });

    it('uses building-overview for legacy management cash metrics and does not call finance summary', async () => {
        const ManagerDashboard = require('@/pages/dashboard/ManagerDashboard').default;

        render(<ManagerDashboard/>);

        await waitFor(() => {
            expect(screen.getByLabelText('Cash position')).toBeInTheDocument();
        });

        expect(mockApiGet).toHaveBeenCalledWith('/finance/building-overview?year=2026');
        expect(mockApiGet).toHaveBeenCalledWith('/arrears/detail');
        expect(mockApiGet).not.toHaveBeenCalledWith('/finance/summary?year=2026');
        expect(mockApiGet).toHaveBeenCalledWith('/analytics/levy-benchmarks?financial_year=2026');
        expect(screen.getByText('Admin balance 216299')).toBeInTheDocument();
        expect(screen.getByText('Sinking balance 415614')).toBeInTheDocument();
        expect(screen.getByText('Arrears total 15284.25')).toBeInTheDocument();
        expect(screen.getByText('Arrears units 2')).toBeInTheDocument();
        expect(screen.getByText('Collection rate 84.48')).toBeInTheDocument();
        // The pulse now reflects the backend health score, not a browser-side
        // formula over collectionRate.
        expect(screen.getByText('Building pulse score 72 financial 65')).toBeInTheDocument();
    });

    it('adds the current dashboard arrears detail interaction to the legacy cash card', async () => {
        const ManagerDashboard = require('@/pages/dashboard/ManagerDashboard').default;

        render(<ManagerDashboard/>);
        fireEvent.click(await screen.findByText('Open arrears detail'));

        expect(screen.getByRole('dialog', {name: 'Arrears and collection'})).toBeInTheDocument();
        fireEvent.click(screen.getByText('Open arrears'));

        expect(mockPush).toHaveBeenCalledWith('/intelligence/debt-recovery');
    });
});
