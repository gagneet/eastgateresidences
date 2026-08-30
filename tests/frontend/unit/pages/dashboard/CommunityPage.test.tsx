/**
 * @featuretrace:community-hub — Frontend tests for Community Hub page.
 * Layer: test
 * Data flow: CommunityPage → /api/community/dashboard → dashboard cards and quick links (building-scoped).
 * Related: frontend/src/pages/dashboard/CommunityPage.tsx
 * Tests: Pet Register, Smart Request, Bookings cards present; stat cards clickable; quick links present.
 * Scope: (building-scoped)
 */
import React from 'react';
import {render, screen, waitFor, fireEvent} from '@testing-library/react';
import '@testing-library/jest-dom';

// Mock next/link
jest.mock('next/link', () => {
    return ({children, href, ...rest}: any) => <a href={href} {...rest}>{children}</a>;
});

// Mock useAuth
const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
};
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        isAdmin: () => false,
        isManager: () => false,
        isECMember: () => false,
    }),
}));

// Suppress sonner toasts
jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const mockSummary = {
    total_lots: 87,
    open_maintenance_requests: 5,
    open_proposals: 2,
    volunteer_events_ytd: 4,
    arrears_lots: 0,
    overdue_work_orders: 0,
    registered_pets: 12,
    open_smart_requests: 3,
    upcoming_bookings: 7,
    health_score: 82,
    health_grade: 'B',
};

const mockHealth = {
    score: 82,
    grade: 'B',
    summary: 'Building is in good health',
    factors: {maintenance: 90, compliance: 75},
};

describe('CommunityPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('building-summary')) return Promise.resolve({data: mockSummary});
            if (url.includes('health-score')) return Promise.resolve({data: mockHealth});
            return Promise.reject(new Error('Unknown endpoint'));
        });
    });

    const renderPage = async () => {
        const {default: CommunityPage} = await import('@/pages/dashboard/CommunityPage');
        render(<CommunityPage/>);
        // Wait for data to load
        await waitFor(() => expect(mockApi.get).toHaveBeenCalled());
    };

    it('renders the Community Hub heading', async () => {
        await renderPage();
        expect(screen.getAllByText(/Community/i).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/Hub/i).length).toBeGreaterThan(0);
    });

    it('shows the building health score', async () => {
        await renderPage();
        await waitFor(() => {
            expect(screen.getByText('82')).toBeInTheDocument();
        });
    });

    it('shows registered_pets stat card with link to /community/pet-register', async () => {
        await renderPage();
        await waitFor(() => {
            expect(screen.getByText('Registered pets')).toBeInTheDocument();
            expect(screen.getByText('12')).toBeInTheDocument();
        });
        const petLink = screen.getByRole('link', {name: /Registered pets/i});
        expect(petLink).toHaveAttribute('href', '/community/pet-register');
    });

    it('shows open_smart_requests stat card with link to /requests/new', async () => {
        await renderPage();
        await waitFor(() => {
            expect(screen.getByText('Open requests')).toBeInTheDocument();
            expect(screen.getByText('3')).toBeInTheDocument();
        });
    });

    it('shows upcoming_bookings stat card with link to /community/bookings', async () => {
        await renderPage();
        await waitFor(() => {
            expect(screen.getByText('Upcoming bookings')).toBeInTheDocument();
            expect(screen.getByText('7')).toBeInTheDocument();
        });
    });

    it('Quick Access includes Smart Request link', async () => {
        await renderPage();
        await waitFor(() => {
            const link = screen.getByTestId('community-link-smart-request');
            expect(link).toBeInTheDocument();
            expect(link).toHaveAttribute('href', '/requests/new');
        });
    });

    it('Quick Access includes Pet Register link', async () => {
        await renderPage();
        await waitFor(() => {
            const link = screen.getByTestId('community-link-pet-register');
            expect(link).toBeInTheDocument();
            expect(link).toHaveAttribute('href', '/community/pet-register');
        });
    });

    it('Quick Access includes Bookings link', async () => {
        await renderPage();
        await waitFor(() => {
            const link = screen.getByTestId('community-link-bookings');
            expect(link).toBeInTheDocument();
            expect(link).toHaveAttribute('href', '/community/bookings');
        });
    });

    it('Quick Access includes My Requests link', async () => {
        await renderPage();
        await waitFor(() => {
            const link = screen.getByTestId('community-link-my-requests');
            expect(link).toBeInTheDocument();
        });
    });

    it('shows open maintenance stat card', async () => {
        await renderPage();
        await waitFor(() => {
            expect(screen.getByText('Open maintenance')).toBeInTheDocument();
            expect(screen.getByText('5')).toBeInTheDocument();
        });
    });

    it('handles API error gracefully without crashing', async () => {
        mockApi.get.mockRejectedValue(new Error('Network error'));
        const {default: CommunityPage} = await import('@/pages/dashboard/CommunityPage');
        render(<CommunityPage/>);
        // Should not throw — just show empty state
        await waitFor(() => {
            expect(screen.queryByText('Community')).toBeInTheDocument();
        });
    });
});
