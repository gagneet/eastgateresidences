/**
 * Frontend tests for Session 82 bug fixes.
 *
 * Tests:
 *   §1  RequestStatusPage — resident sees "What happens next?" message
 *   §2  RequestStatusPage — strata manager sees staff action card, not resident message
 *   §3  RequestStatusPage — super admin sees staff action card
 *   §4  RequestStatusPage — SLA-breached requests show breach message to managers
 *   §5  RequestStatusPage — closed/auto-resolved requests show no action card
 *   §6  CreateStaffUserPage — buildings fetch uses /buildings/me (no double /api/)
 *
 * Confidence ratings:
 *   §1–5 RequestStatusPage role rendering: 9/10 — directly mocks AuthContext helpers,
 *        minor risk if helper names change.
 *   §6 CreateStaffUserPage API path: 9/10 — intercepts the axios mock; confirms exact
 *      URL string. Does not exercise real network.
 *
 * All tests are mock-based, create no data, require no teardown.
 */

import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

// ─── Global navigation mock ────────────────────────────────────────────────

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn(), replace: jest.fn(), back: jest.fn()}),
    usePathname: () => '/requests/req-status-001',
    useSearchParams: () => ({get: () => null}),
    useParams: () => ({id: 'req-status-001'}),
}));

jest.mock('next/link', () =>
    function MockLink({children, href}: any) {
        return <a href={href}>{children}</a>;
    }
);

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

// ─── Mutable auth state (pattern: named `mock*` so Jest allows in factory) ─

const mockAuthState = {
    isManager: jest.fn(() => false),
    isAdmin: jest.fn(() => false),
    api: {
        get: jest.fn(),
        put: jest.fn(),
    },
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuthState,
    AuthProvider: ({children}: any) => <>{children}</>,
}));

// ─── Mutable API response ──────────────────────────────────────────────────

const baseRequest = {
    id: 'req-status-001',
    request_number: 'REQ-2026-0042',
    request_type: 'noise_complaint',
    subject: 'Noise from upstairs unit',
    status: 'awaiting_review',
    sla_breached: false,
    sla_due_at: new Date(Date.now() + 86_400_000).toISOString(),
    submitted_at: '2026-04-20T10:00:00+00:00',
    auto_resolved: false,
    timeline: [],
};

// ─── Stub Radix / shadcn UI components ────────────────────────────────────

jest.mock('@/components/ui/card', () => ({
    Card: ({children, className}: any) => <div className={className ?? ''}>{children}</div>,
    CardContent: ({children}: any) => <div>{children}</div>,
    CardDescription: ({children}: any) => <p>{children}</p>,
    CardHeader: ({children}: any) => <div>{children}</div>,
    CardTitle: ({children}: any) => <h3>{children}</h3>,
}));

jest.mock('lucide-react', () =>
    new Proxy({}, {get: (_t, name) => () => <span data-testid={`icon-${String(name)}`}/>})
);

jest.mock('@/components/ui/badge', () => ({
    Badge: ({children}: any) => <span data-testid="badge">{children}</span>,
}));

jest.mock('@/components/ui/button', () => ({
    Button: ({children, ...props}: any) => <button {...props}>{children}</button>,
}));

jest.mock('@/components/ui/select', () => ({
    Select: ({children}: any) => <div>{children}</div>,
    SelectContent: ({children}: any) => <div>{children}</div>,
    SelectItem: ({value, children}: any) => <option value={value}>{children}</option>,
    SelectTrigger: ({children}: any) => <div>{children}</div>,
    SelectValue: () => null,
}));

jest.mock('@/components/ui/switch', () => ({
    Switch: ({checked, onCheckedChange}: any) => (
        <input
            type="checkbox"
            checked={!!checked}
            onChange={(e) => onCheckedChange?.(e.target.checked)}
        />
    ),
}));

jest.mock('@/components/ui/alert', () => ({
    Alert: ({children}: any) => <div>{children}</div>,
    AlertDescription: ({children}: any) => <div>{children}</div>,
    AlertTitle: ({children}: any) => <div>{children}</div>,
}));

jest.mock('@/components/ui/input', () => ({
    Input: (props: any) => <input {...props} />,
}));

jest.mock('@/components/ui/label', () => ({
    Label: ({children}: any) => <label>{children}</label>,
}));

// ─── Helper: render RequestStatusPage with a given request state ───────────

function renderRequestPage(requestOverride: Partial<typeof baseRequest> = {}) {
    const request = {...baseRequest, ...requestOverride};
    mockAuthState.api.get.mockResolvedValue({data: request});
    mockAuthState.api.put.mockResolvedValue({data: {...request, status: 'closed'}});

    // lazy require so mock state is already set
    const RequestStatusPage =
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        require('@/pages/dashboard/RequestStatusPage').default;

    return render(<RequestStatusPage/>);
}

// ─── §1–5  RequestStatusPage — role-aware action card ─────────────────────

describe('RequestStatusPage — role-aware action card', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockAuthState.isManager.mockReturnValue(false);
        mockAuthState.isAdmin.mockReturnValue(false);
    });

    it('§1 shows resident "What happens next?" message for owners', async () => {
        renderRequestPage();
        await waitFor(() => {
            expect(screen.getByText(/What happens next\?/i)).toBeInTheDocument();
        });
    });

    it('§2 does NOT show "What happens next?" to strata managers', async () => {
        mockAuthState.isManager.mockReturnValue(true);
        renderRequestPage();
        await waitFor(() => {
            expect(screen.queryByText(/What happens next\?/i)).not.toBeInTheDocument();
        });
    });

    it('§2 shows staff action card text to managers for open requests', async () => {
        mockAuthState.isManager.mockReturnValue(true);
        renderRequestPage();
        await waitFor(() => {
            expect(screen.getByText(/awaiting staff action/i)).toBeInTheDocument();
        });
    });

    it('§3 does NOT show "What happens next?" to super admins', async () => {
        mockAuthState.isAdmin.mockReturnValue(true);
        renderRequestPage();
        await waitFor(() => {
            expect(screen.queryByText(/What happens next\?/i)).not.toBeInTheDocument();
        });
    });

    it('§4 shows SLA breach message to managers when request is overdue', async () => {
        mockAuthState.isManager.mockReturnValue(true);
        renderRequestPage({sla_breached: true});
        await waitFor(() => {
            expect(screen.getByText(/breached its SLA/i)).toBeInTheDocument();
        });
    });

    it('§5a shows no action card for closed requests (manager)', async () => {
        mockAuthState.isManager.mockReturnValue(true);
        renderRequestPage({status: 'closed'});
        await waitFor(() => {
            expect(screen.queryByText(/What happens next\?/i)).not.toBeInTheDocument();
            expect(screen.queryByText(/awaiting staff action/i)).not.toBeInTheDocument();
        });
    });

    it('§5a regression: closed stale SLA-breached requests are not shown as overdue', async () => {
        mockAuthState.isManager.mockReturnValue(true);
        renderRequestPage({status: 'closed', sla_breached: true});
        await waitFor(() => {
            expect(screen.getByText(/Closed/i)).toBeInTheDocument();
            expect(screen.queryByText(/SLA Breached/i)).not.toBeInTheDocument();
            expect(screen.queryByText(/breached its SLA/i)).not.toBeInTheDocument();
        });
    });

    it('§5b shows no action card for auto_resolved requests (resident)', async () => {
        renderRequestPage({status: 'auto_resolved'});
        await waitFor(() => {
            expect(screen.queryByText(/What happens next\?/i)).not.toBeInTheDocument();
        });
    });

    it('§5c lets managers close a request with resolution notes', async () => {
        mockAuthState.isManager.mockReturnValue(true);
        renderRequestPage({sla_breached: true});

        await waitFor(() => {
            expect(screen.getByRole('button', {name: /close request/i})).toBeInTheDocument();
        });

        fireEvent.change(screen.getByLabelText(/resolution notes/i), {
            target: {value: 'Called resident and arranged contractor attendance.'},
        });
        fireEvent.click(screen.getByRole('button', {name: /close request/i}));

        await waitFor(() => {
            expect(mockAuthState.api.put).toHaveBeenCalledWith('/workflow-requests/req-status-001/status', {
                status: 'closed',
                resolution_notes: 'Called resident and arranged contractor attendance.',
            });
        });
    });
});

describe('MyRequestsTab — terminal requests with stale SLA flags', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockAuthState.api.get.mockResolvedValue({
            data: [{
                id: 'req-closed-overdue',
                request_number: 'REQ-2026-0099',
                request_type: 'maintenance',
                subject: 'Closed old request',
                status: 'closed',
                sla_breached: true,
                sla_due_at: '2026-01-01T00:00:00+00:00',
                created_at: '2026-01-01T00:00:00+00:00',
            }],
        });
    });

    it('renders Closed instead of Overdue when a terminal row still has sla_breached=true', async () => {
        const MyRequestsTab =
            // eslint-disable-next-line @typescript-eslint/no-var-requires
            require('@/pages/dashboard/requests/MyRequestsTab').default;

        render(<MyRequestsTab/>);

        await waitFor(() => {
            expect(screen.getByText(/Closed old request/i)).toBeInTheDocument();
        });
        expect(screen.getByTestId('badge')).toHaveTextContent(/^closed$/i);
        expect(screen.getByTestId('badge')).not.toHaveTextContent(/^overdue$/i);
    });
});

// ─── §6  CreateStaffUserPage — /buildings/me path has no double /api/ ──────

describe('CreateStaffUserPage — API path correctness', () => {
    const mockGet = jest.fn().mockResolvedValue({
        data: [{id: '13195', name: 'East Gate Residences'}],
    });

    beforeEach(() => {
        mockGet.mockClear();
        // Override api.get for this suite
        mockAuthState.api.get = mockGet;
        mockAuthState.isAdmin.mockReturnValue(true);
        // Provide permissions helper used by CreateStaffUserPage
        (mockAuthState as any).user = {role: 'super_admin', id: 'admin-001'};
        (mockAuthState as any).hasPermission = (p: string) => p === 'can_manage_users';
    });

    it('§6 fetches buildings via /buildings/me — not /api/buildings/me', async () => {
        const CreateStaffUserPage =
            // eslint-disable-next-line @typescript-eslint/no-var-requires
            require('@/pages/dashboard/admin/CreateStaffUserPage').default;

        render(<CreateStaffUserPage/>);

        await waitFor(() => {
            expect(mockGet).toHaveBeenCalled();
        });

        const firstCallPath: string = mockGet.mock.calls[0][0];
        // Must be /buildings/me, never /api/buildings/me
        expect(firstCallPath).toBe('/buildings/me');
        expect(firstCallPath).not.toMatch(/^\/api\//);
    });
});
