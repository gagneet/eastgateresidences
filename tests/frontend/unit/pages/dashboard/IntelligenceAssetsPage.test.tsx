import React, {act} from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import IntelligenceAssetsPage from '@/pages/dashboard/IntelligenceAssetsPage';

jest.mock('next-auth/react', () => ({
    useSession: jest.fn(() => ({data: null, status: 'unauthenticated'})),
    SessionProvider: ({children}: any) => <div>{children}</div>,
}));

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
    usePathname: () => '/intelligence/assets',
}));

// Mock Radix UI Tooltip to avoid missing TooltipProvider context in tests
jest.mock('@/components/ui/tooltip', () => ({
    Tooltip: ({children}: any) => <>{children}</>,
    TooltipTrigger: ({children}: any) => <>{children}</>,
    TooltipContent: ({children}: any) => <span>{children}</span>,
    TooltipProvider: ({children}: any) => <>{children}</>,
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

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        user: {role: 'strata_admin'},
    }),
    AuthProvider: ({children}: any) => <div>{children}</div>,
}));

const mockRisks = [
    {
        asset_id: 'asset-001',
        asset_name: 'Lift Motor A',
        repairs_last_12m: 4,
        repair_cost_last_12m: 8500.50,
        recommendation: 'Consider replacement — repair cost exceeds 40% of replacement value.',
        replacement_cost_estimate: 22000,
        risk_score: 82,
        anomaly_type: 'high_frequency',
    },
];

const mockAssets = [
    {
        id: 'asset-001',
        name: 'Lift Motor A',
        category: 'elevator',
        zone_id: 'zone-lobby',
        health_score: 38,
        risk_score: 82,
        install_year: 2012,
        expected_life_years: 20,
        replacement_cost: 22000,
        last_serviced: '2025-11-15T00:00:00Z',
        description: 'Main lobby lift motor, passenger lift.',
    },
    {
        id: 'asset-002',
        name: 'Rooftop Air Handler',
        category: 'hvac',
        zone_id: 'zone-roof',
        health_score: 76,
        risk_score: 22,
        install_year: 2018,
        expected_life_years: 15,
        replacement_cost: 18000,
        last_serviced: '2025-08-10T00:00:00Z',
    },
];

describe('IntelligenceAssetsPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('maintenance-risks')) return Promise.resolve({data: mockRisks});
            if (url.includes('digital-twin/assets')) return Promise.resolve({data: mockAssets});
            return Promise.resolve({data: []});
        });
    });

    it('renders loading skeleton initially', () => {
        mockApi.get.mockReturnValue(new Promise(() => {
        }));
        render(<IntelligenceAssetsPage/>);
        expect(document.querySelector('.animate-pulse')).toBeInTheDocument();
    });

    it('renders back navigation button', async () => {
        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText(/Back to Intelligence Hub/i)).toBeInTheDocument();
        });
    });

    it('renders page heading and description', async () => {
        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('Asset Intelligence')).toBeInTheDocument();
        });
        expect(screen.getByText(/Risk, health, and recurring repair signals/)).toBeInTheDocument();
    });

    it('renders maintenance anomaly table with risk data', async () => {
        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            expect(screen.getAllByText('Lift Motor A').length).toBeGreaterThan(0);
        });
        expect(screen.getByText('4')).toBeInTheDocument();
        expect(screen.getByText(/Consider replacement/)).toBeInTheDocument();
    });

    it('formats repair cost with exactly 2 decimal places', async () => {
        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            // repair_cost_last_12m = 8500.50, should display as $8,500.50
            const matches = screen.getAllByText(/8,500\.50/);
            expect(matches.length).toBeGreaterThanOrEqual(1);
        });
    });

    it('renders asset health snapshot table', async () => {
        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('Rooftop Air Handler')).toBeInTheDocument();
        });
        expect(screen.getByText('Asset Health Snapshot')).toBeInTheDocument();
    });

    it('shows "Alert" status badge for assets with maintenance risks', async () => {
        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('Alert')).toBeInTheDocument();
            expect(screen.getByText('OK')).toBeInTheDocument();
        });
    });

    it('shows empty state when no anomalies detected', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('maintenance-risks')) return Promise.resolve({data: []});
            if (url.includes('digital-twin/assets')) return Promise.resolve({data: []});
            return Promise.resolve({data: []});
        });

        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('No maintenance anomalies detected.')).toBeInTheDocument();
        });
    });

    it('handles API failure gracefully — shows empty tables not crash', async () => {
        mockApi.get.mockRejectedValue(new Error('Network Error'));
        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            expect(screen.getByText('Asset Intelligence')).toBeInTheDocument();
        });
    });

    it('opens maintenance risk dialog on row click', async () => {
        await act(async () => {
            render(<IntelligenceAssetsPage/>);
        });
        await waitFor(() => {
            expect(screen.getAllByText('Lift Motor A').length).toBeGreaterThan(0);
        });

        // Click the Attention Needed row
        const rows = screen.getAllByText('Lift Motor A');
        fireEvent.click(rows[0]);

        await waitFor(() => {
            expect(screen.getByText('Maintenance anomaly details')).toBeInTheDocument();
        });
    });
});
