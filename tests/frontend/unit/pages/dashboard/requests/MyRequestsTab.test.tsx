import React, {type ChangeEvent, type ReactNode} from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

const mockApiGet = jest.fn<Promise<{data: unknown[]}>, [string]>();
const mockRouterReplace = jest.fn();
let mockSearchParams = new URLSearchParams();

let mockUser: {role?: string; effective_role?: string} = {role: 'owner'};

jest.mock('@/contexts/AuthContext', () => ({
    // No isManager stub on purpose: the component must derive manager scope from
    // effective_role/role itself, exactly as the API does.
    useAuth: () => ({
        api: {get: mockApiGet},
        user: mockUser,
    }),
}));

jest.mock('next/link', () => ({
    __esModule: true,
    default: ({href, children, ...props}: {href: string; children: ReactNode; [key: string]: unknown}) => <a href={href} {...props}>{children}</a>,
}));

jest.mock('next/navigation', () => ({
    useRouter: () => ({replace: mockRouterReplace}),
    useSearchParams: () => mockSearchParams,
}));

jest.mock('@/components/ui/select', () => {
    const React = require('react');
    return {
        Select: ({value, onValueChange, children}: {value: string; onValueChange: (value: string) => void; children: ReactNode}) => (
            <select
                aria-label="Filter requests by status"
                value={value}
                onChange={(event: ChangeEvent<HTMLSelectElement>) => onValueChange(event.target.value)}
            >
                {children}
            </select>
        ),
        SelectContent: ({children}: {children: ReactNode}) => <>{children}</>,
        SelectItem: ({value, children}: {value: string; children: ReactNode}) => <option value={value}>{children}</option>,
        SelectTrigger: () => null,
        SelectValue: () => null,
    };
});

const request = {
    id: 'req-1',
    request_number: 'WR-1',
    request_type: 'maintenance',
    subject: 'Leaking tap',
    body: 'Water leak in bathroom',
    status: 'in_progress',
    created_at: '2026-07-01T00:00:00Z',
    sla_due_at: '2026-07-20T00:00:00Z',
};

describe('MyRequestsTab', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockSearchParams = new URLSearchParams();
        mockUser = {role: 'owner'};
        mockApiGet.mockResolvedValue({data: [request]});
    });

    it('loads all requests by default', async () => {
        const {default: MyRequestsTab} = await import('@/pages/dashboard/requests/MyRequestsTab');

        render(<MyRequestsTab/>);

        await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/workflow-requests'));
        expect(await screen.findByText('Leaking tap')).toBeInTheDocument();
        expect(screen.getByText('All requests (1)')).toBeInTheDocument();
        expect(screen.getByText('In progress (1)')).toBeInTheDocument();
    });

    it('reloads requests with the selected status filter', async () => {
        mockApiGet.mockImplementation((url: string) => Promise.resolve({
            data: url.includes('status=closed') ? [] : [request],
        }));
        const {default: MyRequestsTab} = await import('@/pages/dashboard/requests/MyRequestsTab');

        render(<MyRequestsTab/>);
        await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/workflow-requests'));

        fireEvent.change(screen.getByLabelText('Filter requests by status'), {target: {value: 'closed'}});

        expect(mockRouterReplace).toHaveBeenCalledWith('/requests?status=closed', {scroll: false});
        await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/workflow-requests?status=closed'));
        expect(await screen.findByText('No closed requests found')).toBeInTheDocument();
    });

    it('loads the status filter from the URL and counts overdue requests', async () => {
        mockSearchParams = new URLSearchParams('status=overdue');
        const overdueRequest = {...request, id: 'req-2', subject: 'Overdue leak', status: 'awaiting_review', sla_breached: true};
        mockApiGet.mockImplementation((url: string) => Promise.resolve({
            data: url.includes('status=overdue') ? [overdueRequest] : [request, overdueRequest],
        }));
        const {default: MyRequestsTab} = await import('@/pages/dashboard/requests/MyRequestsTab');

        render(<MyRequestsTab/>);

        await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/workflow-requests?status=overdue'));
        expect(await screen.findByText('Overdue leak')).toBeInTheDocument();
        expect(screen.getByLabelText('Filter requests by status')).toHaveValue('overdue');
        expect(screen.getByText('Overdue (1)')).toBeInTheDocument();
        expect(screen.getByText('All requests (2)')).toBeInTheDocument();
    });

    it('presents the list as a building queue with attribution for a manager', async () => {
        // GET /workflow-requests is role-scoped server-side: a manager gets every
        // request raised for the building, so the copy must not say "my requests"
        // and each row needs to say whose request it is.
        mockUser = {role: 'strata_manager'};
        mockApiGet.mockResolvedValue({
            data: [{...request, unit_number: '42', submitted_by: 'Dana Owner'}],
        });

        const {default: MyRequestsTab} = await import('@/pages/dashboard/requests/MyRequestsTab');

        render(<MyRequestsTab/>);

        expect(await screen.findByText('Building request queue')).toBeInTheDocument();
        expect(screen.getByText(/Unit 42 · Dana Owner/)).toBeInTheDocument();
    });

    it('keeps resident-facing copy and hides attribution for a non-manager', async () => {
        mockApiGet.mockResolvedValue({
            data: [{...request, unit_number: '42', submitted_by: 'Dana Owner'}],
        });

        const {default: MyRequestsTab} = await import('@/pages/dashboard/requests/MyRequestsTab');

        render(<MyRequestsTab/>);

        expect(await screen.findByText('Track requests')).toBeInTheDocument();
        expect(screen.queryByText(/Unit 42/)).not.toBeInTheDocument();
    });

    it('tells a manager the building queue is empty rather than prompting a Smart Request', async () => {
        mockUser = {role: 'strata_manager'};
        mockApiGet.mockResolvedValue({data: []});

        const {default: MyRequestsTab} = await import('@/pages/dashboard/requests/MyRequestsTab');

        render(<MyRequestsTab/>);

        expect(await screen.findByText('No requests found')).toBeInTheDocument();
        expect(screen.getByText(/No residents or owners have raised a request/)).toBeInTheDocument();
    });

    it('treats a temporarily-elevated user as a manager', async () => {
        // effective_role wins over the raw role, matching `_is_manager` in
        // backend/routers/workflow_requests.py. AuthContext.isManager() reads the
        // raw role only and would have shown resident copy over a building queue.
        mockUser = {role: 'owner', effective_role: 'ec_member'};
        mockApiGet.mockResolvedValue({
            data: [{...request, unit_number: '42', submitted_by: 'Dana Owner'}],
        });

        const {default: MyRequestsTab} = await import('@/pages/dashboard/requests/MyRequestsTab');

        render(<MyRequestsTab/>);

        expect(await screen.findByText('Building request queue')).toBeInTheDocument();
    });

    it('shows the unit alone when the read path carries no submitter name', async () => {
        // The Postgres (ops.cases) branch returns submitted_by_user_id only, never a
        // display name — attribution must degrade rather than render a raw UUID.
        mockUser = {role: 'strata_manager'};
        mockApiGet.mockResolvedValue({
            data: [{...request, unit_number: '42', submitted_by_user_id: 'a4f1-uuid'}],
        });

        const {default: MyRequestsTab} = await import('@/pages/dashboard/requests/MyRequestsTab');

        render(<MyRequestsTab/>);

        expect(await screen.findByText('Unit 42')).toBeInTheDocument();
        expect(screen.queryByText(/a4f1-uuid/)).not.toBeInTheDocument();
    });
});
