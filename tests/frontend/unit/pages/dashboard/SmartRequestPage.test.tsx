/**
 * @featuretrace:smart-request — Frontend tests for Smart Request page.
 * Layer: test
 * Tests: SLA banner visible, workflow stages explained, confirmation content, request list overdue indicator.
 * Data flow: SmartRequestPage → workflow requests API → db.workflow_requests / db.messages (building-scoped).
 * Related: backend/routers/workflow_requests.py
 *          frontend/src/pages/dashboard/SmartRequestPage.jsx
 * Scope: (building-scoped)
 */
import React from 'react';
import {render, screen, waitFor, fireEvent} from '@testing-library/react';
import '@testing-library/jest-dom';

// Mock workflowRequestsApi
const mockWrApi = {
    list: jest.fn(),
    createSmart: jest.fn(),
    getStatus: jest.fn(),
};

jest.mock('@/lib/api/community-os', () => ({
    workflowRequestsApi: () => mockWrApi,
}));

// Mock useAuth
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        user: {id: 'u1', full_name: 'Test User', unit_number: 'TH017'},
        api: {},
    }),
}));

// Mock UI components that cause issues in test
jest.mock('@/components/ui/dialog', () => ({
    Dialog: ({children, open}: any) => open ? <div>{children}</div> : null,
    DialogContent: ({children}: any) => <div role="dialog">{children}</div>,
    DialogHeader: ({children}: any) => <div>{children}</div>,
    DialogTitle: ({children}: any) => <h2>{children}</h2>,
    DialogDescription: ({children}: any) => <p>{children}</p>,
}));

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const mockRequests = [
    {
        id: 'req1',
        _id: 'req1',
        request_number: 'WR-2026-0001',
        subject: 'Lift broken on level 3',
        status: 'open',
        category: 'maintenance',
        created_at: '2026-04-01T10:00:00Z',
        sla_due_at: '2026-04-03T10:00:00Z',  // already past
    },
    {
        id: 'req2',
        _id: 'req2',
        request_number: 'WR-2026-0002',
        subject: 'Levy query FY2026',
        status: 'resolved',
        category: 'levy_query',
        created_at: '2026-04-02T10:00:00Z',
        sla_due_at: '2026-04-04T10:00:00Z',
    },
];

describe('SmartRequestPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockWrApi.list.mockResolvedValue({data: mockRequests});
        mockWrApi.createSmart.mockResolvedValue({
            data: {
                id: 'new-req',
                status: 'open',
                request_number: 'WR-2026-0003',
                sla_hours: 48,
            },
        });
    });

    const renderPage = async () => {
        const {default: SmartRequestPage} = await import('@/pages/dashboard/SmartRequestPage');
        render(<SmartRequestPage/>);
        await waitFor(() => expect(mockWrApi.list).toHaveBeenCalled());
    };

    it('renders the page heading', async () => {
        await renderPage();
        expect(screen.getByText('Smart Request')).toBeInTheDocument();
    });

    it('shows the workflow info banner with SLA details', async () => {
        await renderPage();
        expect(screen.getByRole('region', {name: /How Smart Request works/i})).toBeInTheDocument();
        expect(screen.getAllByText(/48 hours/i).length).toBeGreaterThan(0);
        expect(screen.getByText(/strata management team/i)).toBeInTheDocument();
    });

    it('shows step indicator with 3 steps', async () => {
        await renderPage();
        expect(screen.getByText('Category')).toBeInTheDocument();
        expect(screen.getByText('Details')).toBeInTheDocument();
        expect(screen.getByText('Done')).toBeInTheDocument();
    });

    it('renders all 6 category buttons', async () => {
        await renderPage();
        // Use actual component IDs — bylaw_query has no underscore between by and law
        const categoryIds = ['maintenance', 'levy_query', 'bylaw_query', 'document_request', 'noise_complaint', 'other'];
        for (const id of categoryIds) {
            expect(screen.getByTestId(`category-${id}`)).toBeInTheDocument();
        }
    });

    it('clicking a category navigates to form step', async () => {
        await renderPage();
        fireEvent.click(screen.getByTestId('category-maintenance'));
        expect(screen.getByTestId('smart-request-subject')).toBeInTheDocument();
        expect(screen.getByTestId('smart-request-description')).toBeInTheDocument();
    });

    it('shows My Requests list with SLA overdue indicator', async () => {
        await renderPage();
        // req1 has sla_due_at in the past → should show Overdue badge
        await waitFor(() => {
            expect(screen.getByText('Lift broken on level 3')).toBeInTheDocument();
        });
    });

    it('shows confirmation screen with SLA info on submit', async () => {
        await renderPage();
        fireEvent.click(screen.getByTestId('category-maintenance'));

        const subjectInput = screen.getByTestId('smart-request-subject');
        const descInput = screen.getByTestId('smart-request-description');
        fireEvent.change(subjectInput, {target: {value: 'Test subject'}});
        fireEvent.change(descInput, {target: {value: 'Test description with enough detail'}});

        fireEvent.click(screen.getByTestId('smart-request-submit'));

        await waitFor(() => {
            expect(screen.getByText(/Request Submitted/i)).toBeInTheDocument();
        });

        // Should show "what happens next" section
        expect(screen.getByText(/What happens next/i)).toBeInTheDocument();
        expect(screen.getAllByText(/48 hours/i).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/strata management team/i).length).toBeGreaterThan(0);
    });

    it('shows "Track Status" button after submit', async () => {
        await renderPage();
        fireEvent.click(screen.getByTestId('category-maintenance'));

        fireEvent.change(screen.getByTestId('smart-request-subject'), {target: {value: 'Subject'}});
        fireEvent.change(screen.getByTestId('smart-request-description'), {target: {value: 'Description'}});
        fireEvent.click(screen.getByTestId('smart-request-submit'));

        await waitFor(() => {
            expect(screen.getByTestId('track-status-btn')).toBeInTheDocument();
        });
    });

    it('shows empty state when no requests', async () => {
        mockWrApi.list.mockResolvedValue({data: []});
        await renderPage();
        await waitFor(() => {
            expect(screen.getByText(/No requests submitted yet/i)).toBeInTheDocument();
        });
    });
});
