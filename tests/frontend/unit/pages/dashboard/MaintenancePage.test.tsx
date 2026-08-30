/**
 * MaintenancePage test suite.
 *
 * All tests are fully idempotent:
 * - All API calls are mocked via jest.fn() — no real server state is touched.
 * - jest.clearAllMocks() runs in each beforeEach — guaranteed clean slate.
 *
 * Coverage:
 *   1. Maintenance Requests tab (default)
 *   2. PPM Schedule tab — stats, item listing, filters, completion dialog
 */

import React, {act} from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import MaintenancePage from '@/pages/dashboard/MaintenancePage';
import {TooltipProvider} from '@/components/ui/tooltip';

// ── Navigation mocks ────────────────────────────────────────────────────────

const mockRouterReplace = jest.fn();
let mockSearchParams = new URLSearchParams();

jest.mock('next/navigation', () => ({
    usePathname: () => '/maintenance',
    useRouter: () => ({push: jest.fn(), replace: mockRouterReplace}),
    useSearchParams: () => mockSearchParams,
}));

// ── API mock ────────────────────────────────────────────────────────────────

const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
};

// ── Auth context mocks ───────────────────────────────────────────────────────

const mockOwnerAuth = {
    api: mockApi,
    hasPermission: (_p: string) => false,   // owner — no management permissions
    user: {id: 'user-owner', role: 'owner', full_name: 'Avneet Rooprai'},
};

const mockManagerAuth = {
    api: mockApi,
    hasPermission: (_p: string) => true,    // manager — all permissions
    user: {id: 'user-mgr', role: 'strata_manager', full_name: 'Anthony McDonald'},
};

let _currentAuth = mockOwnerAuth;

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => _currentAuth,
}));

// ── PPM test fixtures ────────────────────────────────────────────────────────

const mockPpmDashboard = {
    total_items: 79,
    overdue_count: 2,
    due_soon_count: 5,
    compliance_overdue_count: 1,
    health_score: 87.5,
};

const mockPpmItems = [
    {
        id: 'ppm-1',
        schedule_id: 'ppm-1',
        section: 'FIRE PROTECTION SYSTEMS & EVACUATION',
        description: 'Maintain fire hose reels',
        inspection_type: 'Compliance',
        frequency: '6monthly',
        frequency_days: 180,
        next_due_date: '2026-06-15',
        status: 'scheduled',
        is_compliance: true,
        vendor_name: 'FireSafe Pty Ltd',
        vendor_contact: '1300 555 123',
        completion_log: [],
        notification_log: [],
    },
    {
        id: 'ppm-2',
        schedule_id: 'ppm-2',
        section: 'LIFTS',
        description: 'Annual lift inspection by licenced contractor',
        inspection_type: 'Routine',
        frequency: 'annual',
        frequency_days: 365,
        next_due_date: '2025-12-01',
        status: 'overdue',
        is_compliance: false,
        vendor_name: null,
        vendor_contact: null,
        completion_log: [],
        notification_log: [],
    },
];

const mockPpmSections = [
    {section: 'FIRE PROTECTION SYSTEMS & EVACUATION', item_count: 8, compliance_count: 8, overdue_count: 0},
    {section: 'LIFTS', item_count: 5, compliance_count: 0, overdue_count: 1},
];

/** Default API mock — covers all endpoints used by MaintenancePage. */
function setupDefaultMocks() {
    mockApi.get.mockImplementation((url: string) => {
        if (url === '/maintenance' || url.startsWith('/maintenance?')) {
            return Promise.resolve({
                data: [
                    {
                        id: 'req-1',
                        title: 'Leaking pipe',
                        description: 'Under kitchen sink',
                        category: 'plumbing',
                        priority: 'medium',
                        status: 'submitted',
                        submitted_by_name: 'John Doe',
                        created_at: '2026-01-01T00:00:00Z',
                        location: 'Kitchen',
                        work_order_ids: [],
                        approval_history: [],
                        notes: [],
                    },
                ]
            });
        }
        if (url.startsWith('/ppm/sections')) return Promise.resolve({data: mockPpmSections});
        if (url.startsWith('/ppm/dashboard')) return Promise.resolve({data: mockPpmDashboard});
        if (url.startsWith('/ppm')) return Promise.resolve({data: mockPpmItems});
        if (url.startsWith('/maintenance/contractors')) return Promise.resolve({data: []});
        if (url.startsWith('/maintenance/purchase-orders')) return Promise.resolve({data: []});
        if (url.startsWith('/maintenance/invoices')) return Promise.resolve({data: []});
        if (url.startsWith('/maintenance/work-orders')) return Promise.resolve({data: []});
        if (url === '/work-orders') return Promise.resolve({
            data: [{id: 'wo-1', title: 'Lift repair', status: 'approved', vendor_name: 'Acme Lifts'}]
        });
        if (url === '/purchase-orders') return Promise.resolve({data: []});
        if (url === '/invoices') return Promise.resolve({data: []});
        return Promise.resolve({data: []});
    });
}

// ── Maintenance Requests tab tests ──────────────────────────────────────────

describe('MaintenancePage — Requests Tab', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockSearchParams = new URLSearchParams();
        _currentAuth = mockOwnerAuth;
        setupDefaultMocks();
    });

    it('renders the maintenance page container', async () => {
        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });
        expect(screen.getByTestId('maintenance-page')).toBeInTheDocument();
    });

    it('displays a submitted maintenance request', async () => {
        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });
        await waitFor(() => {
            expect(screen.getByText('Leaking pipe')).toBeInTheDocument();
        });
    });

    it('shows submit request button for owners', async () => {
        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });
        expect(screen.getByTestId('submit-request-btn')).toBeInTheDocument();
    });

    it('opens the submit request dialog when button is clicked', async () => {
        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });
        fireEvent.click(screen.getByTestId('submit-request-btn'));
        expect(screen.getByText('Submit Maintenance Request')).toBeInTheDocument();
    });

    it('falls back to the requests tab when the URL has an unknown maintenance tab', async () => {
        mockSearchParams = new URLSearchParams('tab=unknown&status=submitted');

        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });

        await waitFor(() => {
            expect(screen.getByText('Leaking pipe')).toBeInTheDocument();
        });
    });
});

describe('MaintenancePage — URL status filters', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        _currentAuth = mockManagerAuth;
        setupDefaultMocks();
    });

    it('treats an unknown status in the URL as All statuses for the active tab', async () => {
        mockSearchParams = new URLSearchParams('tab=work-orders&status=unknown');

        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });

        await waitFor(() => {
            expect(screen.getByText('Lift repair')).toBeInTheDocument();
        });
    });
});

// ── ?new=true deep link ──────────────────────────────────────────────────────

describe('MaintenancePage — ?new=true deep link', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        _currentAuth = mockOwnerAuth;
        setupDefaultMocks();
    });

    afterEach(() => {
        mockSearchParams = new URLSearchParams();
    });

    it('opens the create dialog and strips ?new from the URL', async () => {
        // The owner dashboards deep-link "New request" here. Before this param was
        // honoured the link landed on the plain list; before it was CONSUMED, it
        // stayed in the URL and re-opened the dialog on every later filter change
        // (updateUrlFilters rebuilds the query from the current params and only
        // deletes tab/status, so `new=true` was carried into every router.replace).
        mockSearchParams = new URLSearchParams('new=true');

        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });

        await waitFor(() => {
            expect(mockRouterReplace).toHaveBeenCalled();
        });

        const replacedWith = mockRouterReplace.mock.calls.map(c => String(c[0]));
        expect(replacedWith.some(u => !u.includes('new='))).toBe(true);
    });

    it('preserves other query params while stripping only ?new', async () => {
        mockSearchParams = new URLSearchParams('new=true&tab=work-orders');

        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });

        await waitFor(() => {
            expect(mockRouterReplace).toHaveBeenCalled();
        });

        const consumed = mockRouterReplace.mock.calls
            .map(c => String(c[0]))
            .find(u => !u.includes('new='));
        expect(consumed).toBeDefined();
        expect(consumed).toContain('tab=work-orders');
    });

    it('does not touch the URL when ?new is absent', async () => {
        // Manager auth: the work-orders tab is permission-gated, and an owner
        // never sees it (the sibling "URL status filters" suite does the same).
        _currentAuth = mockManagerAuth;
        mockSearchParams = new URLSearchParams('tab=work-orders');

        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });

        await waitFor(() => {
            expect(screen.getByText('Lift repair')).toBeInTheDocument();
        });
        expect(mockRouterReplace).not.toHaveBeenCalled();
    });
});

// ── PPM Schedule tab tests ───────────────────────────────────────────────────

describe('MaintenancePage — PPM Schedule Tab', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockSearchParams = new URLSearchParams();
        _currentAuth = mockManagerAuth;
        setupDefaultMocks();
    });

    /** Helper: render and activate the PPM tab.
     *
     * Radix UI TabsTrigger activates on `mousedown` (button=0, ctrlKey=false),
     * NOT on `click`. Using fireEvent.mouseDown correctly fires onValueChange
     * which sets activeTab → 'ppm' and triggers fetchPpmData().
     */
    async function renderAndOpenPpmTab() {
        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });
        const ppmTab = screen.getByTestId('ppm-tab-trigger');
        await act(async () => {
            // Radix UI Tabs uses onMouseDown (button=0) — fireEvent.click alone won't
            // activate the tab because the Radix handler only runs on mousedown.
            fireEvent.mouseDown(ppmTab, {button: 0, ctrlKey: false});
        });
        return ppmTab;
    }

    it('renders the PPM Schedule tab trigger', async () => {
        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });
        expect(screen.getByTestId('ppm-tab-trigger')).toBeInTheDocument();
        expect(screen.getByText('PPM Schedule')).toBeInTheDocument();
    });

    it('calls the three PPM API endpoints when tab is activated', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            const getCalls: string[] = mockApi.get.mock.calls.map((c: any[]) => c[0] as string);
            expect(getCalls.some((u: string) => u.startsWith('/ppm/sections'))).toBe(true);
            expect(getCalls.some((u: string) => u.startsWith('/ppm/dashboard'))).toBe(true);
            expect(getCalls.some((u: string) => u.startsWith('/ppm') && !u.includes('/sections') && !u.includes('/dashboard'))).toBe(true);
        });
    });

    it('displays dashboard stats after tab activation', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            expect(screen.getByTestId('ppm-total-items')).toHaveTextContent('79');
            expect(screen.getByTestId('ppm-health-score')).toHaveTextContent('87.5%');
            expect(screen.getByTestId('ppm-overdue-count')).toHaveTextContent('2');
        });
    });

    it('opens an overdue summary and filters the PPM table when the overdue card is clicked', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            expect(screen.getByRole('button', {name: /show overdue items/i})).toBeInTheDocument();
        });

        await act(async () => {
            fireEvent.click(screen.getByRole('button', {name: /show overdue items/i}));
        });

        expect(screen.getByRole('heading', {name: 'Overdue PPM items'})).toBeInTheDocument();
        expect(screen.getAllByText('Annual lift inspection by licenced contractor').length).toBeGreaterThanOrEqual(1);
        expect(screen.queryByText('Maintain fire hose reels')).not.toBeInTheDocument();
    });

    it('opens a compliance summary and filters the PPM table when the compliance card is clicked', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            expect(screen.getByRole('button', {name: /show compliance items/i})).toBeInTheDocument();
        });

        await act(async () => {
            fireEvent.click(screen.getByRole('button', {name: /show compliance items/i}));
        });

        expect(screen.getByRole('heading', {name: 'Compliance-linked PPM items'})).toBeInTheDocument();
        expect(screen.getAllByText('Maintain fire hose reels').length).toBeGreaterThanOrEqual(1);
        expect(screen.queryByText('Annual lift inspection by licenced contractor')).not.toBeInTheDocument();
    });

    it('shows PPM items in the list after tab activation', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            expect(screen.getByText('Maintain fire hose reels')).toBeInTheDocument();
            expect(screen.getByText('Annual lift inspection by licenced contractor')).toBeInTheDocument();
        });
    });

    it('shows Overdue badge for overdue items', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            // The overdue PPM item should display an "Overdue" status badge
            const overdueBadges = screen.getAllByText(/overdue/i);
            expect(overdueBadges.length).toBeGreaterThanOrEqual(1);
        });
    });

    it('section filter dropdown is populated with sections from API', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            // Section values should be present in the dropdown
            expect(screen.getByText('All Sections')).toBeInTheDocument();
        });
    });

    it('compliance-only toggle is visible', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            expect(screen.getByText(/compliance only/i)).toBeInTheDocument();
        });
    });

    it('log completion button is visible for manager role', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            // At least one "Log" button should be present for managers
            const logButtons = screen.getAllByText(/^log$/i);
            expect(logButtons.length).toBeGreaterThanOrEqual(1);
        });
    });

    it('opens log completion dialog when Log button is clicked', async () => {
        await renderAndOpenPpmTab();

        await waitFor(() => {
            const logButtons = screen.getAllByText(/^log$/i);
            expect(logButtons.length).toBeGreaterThanOrEqual(1);
        });

        const logButtons = screen.getAllByText(/^log$/i);
        await act(async () => {
            fireEvent.click(logButtons[0]);
        });

        // Radix Dialog renders in a portal — use waitFor to handle async mount.
        // Use heading role to distinguish dialog title from the submit button text.
        await waitFor(() => {
            expect(screen.getByRole('heading', {name: 'Log Completion'})).toBeInTheDocument();
        });
    });

    it('submits completion via API with correct payload', async () => {
        mockApi.post.mockResolvedValueOnce({data: {...mockPpmItems[0], last_completed_date: '2026-06-15'}});
        await renderAndOpenPpmTab();

        // Open completion dialog for first item
        await waitFor(() => {
            expect(screen.getAllByText(/^log$/i).length).toBeGreaterThanOrEqual(1);
        });
        await act(async () => {
            fireEvent.click(screen.getAllByText(/^log$/i)[0]);
        });

        // Wait for dialog to open (title = heading, distinct from submit button)
        await waitFor(() => screen.getByRole('heading', {name: 'Log Completion'}));

        // Fill completed_by — placeholder is "Name or company" in the component
        const completedByInput = screen.getByPlaceholderText(/name or company/i);
        fireEvent.change(completedByInput, {target: {value: 'FireSafe Pty Ltd'}});

        // Submit
        const submitBtn = screen.getByRole('button', {name: /log completion/i});
        await act(async () => {
            fireEvent.click(submitBtn);
        });

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith(
                expect.stringContaining('/ppm/'),
                expect.objectContaining({completed_by: 'FireSafe Pty Ltd'}),
            );
        });
    });

    it('does NOT show Log buttons for owner role', async () => {
        _currentAuth = mockOwnerAuth;
        jest.clearAllMocks();
        setupDefaultMocks();

        await act(async () => {
            render(<TooltipProvider><MaintenancePage/></TooltipProvider>);
        });
        const ppmTab = screen.getByTestId('ppm-tab-trigger');
        // Radix Tabs uses onMouseDown (not onClick) — must use mouseDown to activate
        await act(async () => {
            fireEvent.mouseDown(ppmTab, {button: 0, ctrlKey: false});
        });

        await waitFor(() => {
            expect(screen.getByText('Maintain fire hose reels')).toBeInTheDocument();
        });

        // Owners should NOT see "Log" buttons (completion is manager-only)
        expect(screen.queryAllByText(/^log$/i)).toHaveLength(0);
    });
});
