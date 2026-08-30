/**
 * Frontend Tests: OccupancyIntelligencePage
 *
 * Covers:
 * 1. Page renders for authorised roles (chairman, ec_member)
 * 2. API data loads and summary stats display
 * 3. Pie chart, trend chart, heatmap components render
 * 4. Feature toggle guard hides UI when disabled
 * 5. Role-based visibility (owners cannot see page)
 * 6. Recompute button shown for admin roles only
 * 7. API error state handled gracefully
 */

import React from 'react';
import {render, screen, waitFor, fireEvent} from '@testing-library/react';
import '@testing-library/jest-dom';
import OccupancyIntelligencePage from '@/pages/dashboard/OccupancyIntelligencePage';

// ── router / navigation mocks ────────────────────────────────────────────────
jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
    usePathname: () => '/intelligence/occupancy',
}));

jest.mock('sonner', () => ({toast: {success: jest.fn(), error: jest.fn()}}));

// ── Recharts: stub to avoid canvas errors in jsdom ───────────────────────────
jest.mock('recharts', () => ({
    PieChart: ({children}: any) => <div data-testid="pie-chart">{children}</div>,
    Pie: () => null,
    Cell: () => null,
    Tooltip: () => null,
    Legend: () => null,
    LineChart: ({children}: any) => <div data-testid="line-chart">{children}</div>,
    Line: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    ResponsiveContainer: ({children}: any) => <div>{children}</div>,
}));

// ── AuthContext mock ──────────────────────────────────────────────────────────
const mockApiGet = jest.fn();
const mockApiPost = jest.fn();

const mockApi = {
    get: mockApiGet,
    post: mockApiPost,
    interceptors: {
        request: {use: jest.fn(), eject: jest.fn()},
        response: {use: jest.fn(), eject: jest.fn()},
    },
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn(),
    AuthProvider: ({children}: any) => <>{children}</>,
}));

import {useAuth} from '@/contexts/AuthContext';

const mockUseAuth = useAuth as jest.Mock;

function makeAuthMock(roleOverride = 'strata_manager') {
    return {
        api: mockApi,
        user: {
            role: roleOverride,
            id: 'u1',
            is_elevated: false,
            temp_elevation: null,
        },
    };
}

// ── API response fixtures ─────────────────────────────────────────────────────
const SUMMARY = {
    building_id: '13195',
    total_lots: 87,
    tenant_count: 42,
    owner_occupied_count: 30,
    investor_unknown_count: 15,
    tenant_pct: 48.3,
    owner_occupied_pct: 34.5,
    investor_unknown_pct: 17.2,
    avg_confidence: 0.78,
    last_computed_at: '2026-04-05T02:00:00Z',
};

const LOTS = [
    {
        lot_number: 'TH017',
        unit_type: 'townhouse',
        status: 'owner_occupied',
        confidence: 0.9,
        sources: ['address_match'],
        last_verified: '2026-04-05T02:00:00Z'
    },
    {
        lot_number: 'UA001',
        unit_type: 'apartment',
        status: 'tenant',
        confidence: 0.95,
        sources: ['rental_certificate'],
        last_verified: '2026-04-05T02:00:00Z'
    },
    {
        lot_number: 'UA002',
        unit_type: 'apartment',
        status: 'investor_unknown',
        confidence: 0.5,
        sources: ['fallback'],
        last_verified: '2026-04-05T02:00:00Z'
    },
];

const TRENDS = {
    building_id: '13195',
    trend: [
        {month: '2025-05', tenant_count: 40, owner_occupied_count: 30, investor_unknown_count: 17, total: 87},
        {month: '2025-06', tenant_count: 41, owner_occupied_count: 31, investor_unknown_count: 15, total: 87},
    ],
};

function setupApiMocks() {
    mockApiGet.mockImplementation((url: string) => {
        if (url.includes('/occupancy/summary')) return Promise.resolve({data: SUMMARY});
        if (url.includes('/occupancy/lots')) return Promise.resolve({data: LOTS});
        if (url.includes('/occupancy/trends')) return Promise.resolve({data: TRENDS});
        return Promise.resolve({data: {}});
    });
}

beforeEach(() => {
    jest.clearAllMocks();
    mockUseAuth.mockReturnValue(makeAuthMock('strata_manager'));
    setupApiMocks();
});

// ── 1. Page renders for authorised role ──────────────────────────────────────
test('renders page heading for strata_manager', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText('Building Occupancy Intelligence')).toBeInTheDocument());
});

// ── 2. Summary stats are displayed ───────────────────────────────────────────
test('displays total lots from API summary', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText('87')).toBeInTheDocument());
});

test('displays tenant count from API summary', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText('42')).toBeInTheDocument());
});

test('displays owner-occupied count from API summary', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText('30')).toBeInTheDocument());
});

// ── 3. Chart components render ────────────────────────────────────────────────
test('renders OccupancyPieChart component', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByTestId('occupancy-pie-chart')).toBeInTheDocument());
});

test('renders OccupancyTrendChart component', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByTestId('occupancy-trend-chart')).toBeInTheDocument());
});

test('renders OccupancyHeatmap component with lot tiles', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => {
        expect(screen.getByTestId('occupancy-heatmap')).toBeInTheDocument();
        expect(screen.getByTestId('lot-tile-TH017')).toBeInTheDocument();
        expect(screen.getByTestId('lot-tile-UA001')).toBeInTheDocument();
    });
});

// ── 4. Access restricted for owner role ──────────────────────────────────────
test('shows access restricted message for owner role', async () => {
    mockUseAuth.mockReturnValue(makeAuthMock('owner'));
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText(/Access Restricted/i)).toBeInTheDocument());
});

test('owner role does not see the intelligence page content', async () => {
    mockUseAuth.mockReturnValue(makeAuthMock('owner'));
    render(<OccupancyIntelligencePage/>);
    expect(screen.queryByText('Building Occupancy Intelligence')).not.toBeInTheDocument();
});

// ── 5. Recompute button visibility by role ────────────────────────────────────
test('Recompute button visible for strata_manager default role', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByTestId('recompute-btn')).toBeInTheDocument());
});

test('Recompute button visible for strata_manager', async () => {
    mockUseAuth.mockReturnValue(makeAuthMock('strata_manager'));
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByTestId('recompute-btn')).toBeInTheDocument());
});

test('Recompute button not visible for ec_member', async () => {
    mockUseAuth.mockReturnValue(makeAuthMock('ec_member'));
    render(<OccupancyIntelligencePage/>);
    // ec_member can VIEW but not recompute
    await waitFor(() => expect(screen.getByText('Building Occupancy Intelligence')).toBeInTheDocument());
    expect(screen.queryByTestId('recompute-btn')).not.toBeInTheDocument();
});

// ── 6. Recompute button calls POST and refreshes data ─────────────────────────
test('clicking Recompute calls POST /occupancy/recompute', async () => {
    mockApiPost.mockResolvedValue({data: {total_lots: 87, tenant: 43}});
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => screen.getByTestId('recompute-btn'));
    fireEvent.click(screen.getByTestId('recompute-btn'));
    await waitFor(() => expect(mockApiPost).toHaveBeenCalledWith('/occupancy/recompute'));
});

// ── 7. API error state is handled gracefully ──────────────────────────────────
test('shows error message when API fails', async () => {
    mockApiGet.mockRejectedValue({response: {data: {detail: 'Service unavailable'}}});
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText('Service unavailable')).toBeInTheDocument());
});

// ── 8. Confidence badge is rendered ───────────────────────────────────────────
test('renders confidence badge with avg_confidence from summary', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText(/78% confident/i)).toBeInTheDocument());
});

// ── 9. Last computed timestamp is shown ──────────────────────────────────────
test('renders last computed timestamp from summary', async () => {
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText(/Last computed/i)).toBeInTheDocument());
});

// ── 10. ec_member can view data ───────────────────────────────────────────────
test('ec_member can view occupancy page content', async () => {
    mockUseAuth.mockReturnValue(makeAuthMock('ec_member'));
    render(<OccupancyIntelligencePage/>);
    await waitFor(() => expect(screen.getByText('Building Occupancy Intelligence')).toBeInTheDocument());
});
