import React, {act} from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import CapitalRiskPage from '@/pages/dashboard/CapitalRiskPage';

jest.mock('next-auth/react', () => ({
    useSession: jest.fn(() => ({data: null, status: 'unauthenticated'})),
    SessionProvider: ({children}: any) => <div>{children}</div>,
}));

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
    usePathname: () => '/intelligence/capital-risk',
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

const mockCapitalShockData = {
    risk_level: 'MODERATE',
    reserve_balance: 250000,
    recommendation: 'Consider increasing sinking fund levies by 5%',
    capital_shock_index: {
        baseline_spend: 45000,
        rows: [
            {year: 2025, capital_spend: 50000, capital_shock_index: 1.1, risk_level: 'LOW'},
            {year: 2026, capital_spend: 90000, capital_shock_index: 2.0, risk_level: 'MAJOR'},
        ],
    },
    funding_adequacy: {
        sfar: 0.92,
        status_label: 'At Risk',
        funding_gap: 15000,
    },
    reserve_collapse: {
        collapse_year: null,
        risk_label: 'No collapse predicted',
    },
    risks: [],
    reserve_projection: [
        {year: 2025, reserve_before_capital: 300000, capital_cost: 50000, reserve_after_capital: 250000},
        {year: 2026, reserve_before_capital: 265000, capital_cost: 90000, reserve_after_capital: 175000},
    ],
};

describe('CapitalRiskPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockApi.get.mockResolvedValue({data: mockCapitalShockData});
    });

    it('renders loading state initially', () => {
        mockApi.get.mockReturnValue(new Promise(() => {
        })); // never resolves
        render(<CapitalRiskPage/>);
        // skeleton loading divs should be present
        expect(document.querySelector('.animate-pulse')).toBeInTheDocument();
    });

    it('renders capital risk data after loading', async () => {
        await act(async () => {
            render(<CapitalRiskPage/>);
        });

        await waitFor(() => {
            expect(screen.getByText('Capital Shock Risk')).toBeInTheDocument();
        });
        expect(screen.getByText('MODERATE')).toBeInTheDocument();
        // 'Capital Shock Index (CSI)' and 'Funding Adequacy' appear as card titles.
        // Tooltip portals may render additional copies — getAllByText handles both cases.
        expect(screen.getAllByText('Capital Shock Index (CSI)').length).toBeGreaterThanOrEqual(1);
        expect(screen.getAllByText('Funding Adequacy').length).toBeGreaterThanOrEqual(1);
    });

    it('displays reserve balance correctly formatted', async () => {
        await act(async () => {
            render(<CapitalRiskPage/>);
        });

        await waitFor(() => {
            // Reserve balance 250,000 displays as $250,000.00 — verify 2dp formatting
            // May appear multiple times (summary card + projection table rows)
            const matches = screen.getAllByText(/250,000\.00/);
            expect(matches.length).toBeGreaterThanOrEqual(1);
        });
    });

    it('shows recommendation text', async () => {
        await act(async () => {
            render(<CapitalRiskPage/>);
        });

        await waitFor(() => {
            expect(screen.getByText(/Consider increasing sinking fund levies/)).toBeInTheDocument();
        });
    });

    it('shows error state when API fails', async () => {
        mockApi.get.mockRejectedValue(new Error('Network Error'));
        await act(async () => {
            render(<CapitalRiskPage/>);
        });

        await waitFor(() => {
            expect(screen.getByText(/Failed to load data/)).toBeInTheDocument();
        });
    });
});
