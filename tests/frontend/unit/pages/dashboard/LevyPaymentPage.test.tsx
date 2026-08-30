/**
 * LevyPaymentPage test suite.
 *
 * All tests are fully idempotent:
 * - All API calls are mocked via jest.fn() — no real server state is touched.
 * - jest.clearAllMocks() runs in each beforeEach — guaranteed clean slate.
 *
 * Coverage:
 *   1. EC Member — role-context banner rendered when unit_number present
 *   2. EC Member — no banner when unit_number absent
 *   3. EC Member — levy status auto-loaded on mount (Bug #1 fix)
 *   4. EC Member — no-unit-number shows correct helper message
 *   5. Owner — levy status auto-loaded on mount (regression guard)
 *   6. Owner — no EC banner rendered
 *   7. Payment History — quarters table rendered when levyStatus.quarters present (Bug #2 fix)
 *   8. Payment History — actionable message when no quarters configured
 *   9. Non-owner (no unit) — no auto-load, no banner
 *  10. Multi-tenant isolation — EC member (building 16244) only fetches their building
 */

import React, {act} from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import LevyPaymentPage from '@/pages/dashboard/LevyPaymentPage';

// ── Navigation mock ──────────────────────────────────────────────────────────

jest.mock('next/navigation', () => ({
    usePathname: () => '/financials/levy-payments',
    useRouter: () => ({push: jest.fn()}),
    useSearchParams: () => ({get: jest.fn().mockReturnValue(null)}),
}));

// ── API mock ─────────────────────────────────────────────────────────────────

const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
};

// ── Auth fixtures ─────────────────────────────────────────────────────────────

/** Standard owner — building 13195, unit UA001 */
const mockOwnerAuth = {
    api: mockApi,
    user: {id: 'user-owner', role: 'owner', unit_number: 'UA001', building_id: '13195', full_name: 'Jane Smith'},
    hasPermission: (_p: string) => false,
    isOwner: () => true,
    isECMember: () => false,
};

/** EC Member who owns unit UA015, building 13195 */
const mockECWithUnit = {
    api: mockApi,
    user: {id: 'user-ec', role: 'ec_member', unit_number: 'UA015', building_id: '13195', full_name: 'Michael Chen'},
    hasPermission: (p: string) => p === 'can_manage_finances',
    isOwner: () => true,   // AuthContext isOwner() returns true for ec_member + unit_number
    isECMember: () => true,
};

/** EC Member with no unit linked (edge case) */
const mockECNoUnit = {
    api: mockApi,
    user: {
        id: 'user-ec-nounit',
        role: 'ec_member',
        unit_number: undefined,
        building_id: '13195',
        full_name: 'Chris Lee'
    },
    hasPermission: (p: string) => p === 'can_manage_finances',
    isOwner: () => false,  // no unit_number → isOwner returns false
    isECMember: () => true,
};

/** EC Member in building 16244 (multi-tenant isolation) */
const mockECSierra = {
    api: mockApi,
    user: {id: 'user-ec-sierra', role: 'ec_member', unit_number: 'S001', building_id: '16244', full_name: 'Sara Patel'},
    hasPermission: (p: string) => p === 'can_manage_finances',
    isOwner: () => true,
    isECMember: () => true,
};

/** Strata manager — can search all, no personal unit */
const mockManagerAuth = {
    api: mockApi,
    user: {id: 'user-mgr', role: 'strata_manager', unit_number: undefined, building_id: '13195', full_name: 'Amy Wong'},
    hasPermission: (_p: string) => true,
    isOwner: () => false,
    isECMember: () => false,
};

// eslint-disable-next-line prefer-const
let _currentAuth: any = mockOwnerAuth;

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => _currentAuth,
}));

// ── Levy status fixtures ──────────────────────────────────────────────────────

const mockLevyStatusWithQuarters = {
    unit_number: 'UA001',
    year: 2026,
    annual_levy: 6120,
    admin_annual: 4730,
    sinking_annual: 1390,
    quarterly_levy: 1530,
    period_levy: 1530,
    balance_due: 1530,
    total_paid: 4590,
    in_credit: false,
    credit_balance: 0,
    prev_year_balance: 0,
    prev_year: 2025,
    grace_period_days: 14,
    quarters: [
        {quarter: 'Q1', due_date: '2026-03-31', amount_due: 1530, amount_paid: 1530, status: 'paid', carry_forward: 0},
        {quarter: 'Q2', due_date: '2026-06-01', amount_due: 1530, amount_paid: 1530, status: 'paid', carry_forward: 0},
        {quarter: 'Q3', due_date: '2026-09-01', amount_due: 1530, amount_paid: 1530, status: 'paid', carry_forward: 0},
        {quarter: 'Q4', due_date: '2026-12-01', amount_due: 1530, amount_paid: 0, status: 'upcoming', carry_forward: 0},
    ],
};

/** Same structure but with empty quarters — simulates levy periods not yet configured */
const mockLevyStatusNoQuarters = {
    ...mockLevyStatusWithQuarters,
    quarters: [],
};

/** Levy status for Sierra building (16244) — must not appear for 13195 users */
const mockLevyStatusSierra = {
    ...mockLevyStatusWithQuarters,
    unit_number: 'S001',
    building_id: '16244',
};

/** Default API responses for the levy payment page */
function setupDefaultMocks(levyStatus = mockLevyStatusWithQuarters) {
    mockApi.get.mockImplementation((url: string) => {
        if (url === '/payment-methods') return Promise.resolve({data: {methods: []}});
        if (url === '/levy-payments') return Promise.resolve({data: []});
        if (url.startsWith('/levy-status/')) return Promise.resolve({data: levyStatus});
        return Promise.resolve({data: []});
    });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderPage() {
    return render(
            <LevyPaymentPage/>
    );
}

// ═════════════════════════════════════════════════════════════════════════════
// 1. EC Member banner
// ═════════════════════════════════════════════════════════════════════════════

describe('LevyPaymentPage — EC Member role banner', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        setupDefaultMocks();
    });

    it('shows the EC member banner when user is ec_member with a unit', async () => {
        _currentAuth = mockECWithUnit;
        await act(async () => {
            renderPage();
        });
        const banner = await screen.findByTestId('ec-member-levy-banner');
        expect(banner).toBeInTheDocument();
        expect(banner).toHaveAttribute('role', 'note');
        expect(banner).toHaveTextContent('Executive Committee Member');
        expect(banner).toHaveTextContent('Unit UA015 (Owner)');
    });

    it('banner text mentions Levy Status tab and building management ability', async () => {
        _currentAuth = mockECWithUnit;
        await act(async () => {
            renderPage();
        });
        const banner = await screen.findByTestId('ec-member-levy-banner');
        expect(banner).toHaveTextContent(/Levy Status tab/i);
        expect(banner).toHaveTextContent(/record and verify payments/i);
    });

    it('shows no-unit helper message in banner when ec_member has no unit linked', async () => {
        _currentAuth = mockECNoUnit;
        await act(async () => {
            renderPage();
        });
        const banner = await screen.findByTestId('ec-member-levy-banner');
        expect(banner).toHaveTextContent(/No unit is linked/i);
        expect(banner).toHaveTextContent(/strata manager/i);
    });

    it('does NOT show the banner for a plain owner', async () => {
        _currentAuth = mockOwnerAuth;
        await act(async () => {
            renderPage();
        });
        await waitFor(() => {
            expect(screen.queryByTestId('ec-member-levy-banner')).not.toBeInTheDocument();
        });
    });

    it('does NOT show the banner for a strata manager', async () => {
        _currentAuth = mockManagerAuth;
        await act(async () => {
            renderPage();
        });
        await waitFor(() => {
            expect(screen.queryByTestId('ec-member-levy-banner')).not.toBeInTheDocument();
        });
    });
});

// ═════════════════════════════════════════════════════════════════════════════
// 2. EC Member — levy status auto-load (Bug #1)
// ═════════════════════════════════════════════════════════════════════════════

describe('LevyPaymentPage — EC Member levy status auto-load (Bug #1)', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        setupDefaultMocks();
    });

    it('fetches levy status on mount for an EC member with a unit number', async () => {
        _currentAuth = mockECWithUnit;
        await act(async () => {
            renderPage();
        });
        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith('/levy-status/UA015');
        });
    });

    it('does NOT fetch levy status for an EC member with no unit', async () => {
        _currentAuth = mockECNoUnit;
        await act(async () => {
            renderPage();
        });
        await waitFor(() => {
            // Only payment-methods and levy-payments should be called, not levy-status
            expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/levy-status/'));
        });
    });

    it('fetches levy status on mount for a plain owner (regression guard)', async () => {
        _currentAuth = mockOwnerAuth;
        await act(async () => {
            renderPage();
        });
        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith('/levy-status/UA001');
        });
    });

    it('does NOT auto-fetch levy status for a strata manager (no personal unit)', async () => {
        _currentAuth = mockManagerAuth;
        await act(async () => {
            renderPage();
        });
        await waitFor(() => {
            expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining('/levy-status/'));
        });
    });
});

// ═════════════════════════════════════════════════════════════════════════════
// 3. Payment History tab — quarters fix (Bug #2)
// ═════════════════════════════════════════════════════════════════════════════

describe('LevyPaymentPage — Payment History tab (Bug #2)', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('renders the quarters breakdown table when levyStatus.quarters is populated', async () => {
        _currentAuth = mockOwnerAuth;
        setupDefaultMocks(mockLevyStatusWithQuarters);
        await act(async () => {
            renderPage();
        });

        // Navigate to Payment History tab
        const historyTab = screen.getByRole('tab', {name: /Payment History/i});
        await act(async () => {
            historyTab.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
        });

        await waitFor(() => {
            // The quarters table should be visible — Q1 through Q4
            expect(screen.getByText('Q1')).toBeInTheDocument();
            expect(screen.getByText('Q4')).toBeInTheDocument();
        });
    });

    it('does NOT show the misleading "no levy periods configured" message when quarters exist', async () => {
        _currentAuth = mockOwnerAuth;
        setupDefaultMocks(mockLevyStatusWithQuarters);
        await act(async () => {
            renderPage();
        });

        const historyTab = screen.getByRole('tab', {name: /Payment History/i});
        await act(async () => {
            historyTab.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
        });

        await waitFor(() => {
            expect(screen.queryByText(/no levy periods have been configured yet/i)).not.toBeInTheDocument();
        });
    });

    it('shows an actionable message (pointing to Building Settings) when quarters is empty', async () => {
        _currentAuth = mockOwnerAuth;
        setupDefaultMocks(mockLevyStatusNoQuarters);
        await act(async () => {
            renderPage();
        });

        const historyTab = screen.getByRole('tab', {name: /Payment History/i});
        await act(async () => {
            historyTab.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
        });

        await waitFor(() => {
            expect(screen.getByText(/Building Settings.*Financial/i)).toBeInTheDocument();
        });
    });

    it('formats due dates correctly in en-AU locale in the quarters table', async () => {
        _currentAuth = mockOwnerAuth;
        setupDefaultMocks(mockLevyStatusWithQuarters);
        await act(async () => {
            renderPage();
        });

        const historyTab = screen.getByRole('tab', {name: /Payment History/i});
        await act(async () => {
            historyTab.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
        });

        await waitFor(() => {
            // 2026-03-31 formatted as en-AU: "31 Mar 2026"
            expect(screen.getByText('31 Mar 2026')).toBeInTheDocument();
        });
    });

    it('shows accessible status badge labels in the quarters table', async () => {
        _currentAuth = mockOwnerAuth;
        setupDefaultMocks(mockLevyStatusWithQuarters);
        await act(async () => {
            renderPage();
        });

        const historyTab = screen.getByRole('tab', {name: /Payment History/i});
        await act(async () => {
            historyTab.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
        });

        await waitFor(() => {
            // Badges render human-readable labels, not raw DB values
            expect(screen.getAllByText('Paid').length).toBeGreaterThan(0);
            expect(screen.getByText('Upcoming')).toBeInTheDocument();
        });
    });
});

// ═════════════════════════════════════════════════════════════════════════════
// 4. Multi-tenant isolation
// ═════════════════════════════════════════════════════════════════════════════

describe('LevyPaymentPage — multi-tenant isolation', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('EC member in building 16244 fetches levy status for their unit, not 13195 units', async () => {
        _currentAuth = mockECSierra;
        mockApi.get.mockImplementation((url: string) => {
            if (url === '/payment-methods') return Promise.resolve({data: {methods: []}});
            if (url === '/levy-payments') return Promise.resolve({data: []});
            if (url === '/levy-status/S001') return Promise.resolve({data: mockLevyStatusSierra});
            // Reject any attempt to access 13195-specific data
            if (url.includes('UA')) return Promise.reject(new Error('Cross-building access denied'));
            return Promise.resolve({data: []});
        });

        await act(async () => {
            renderPage();
        });

        await waitFor(() => {
            expect(mockApi.get).toHaveBeenCalledWith('/levy-status/S001');
            expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringMatching(/levy-status\/UA/));
        });
    });
});

// ═════════════════════════════════════════════════════════════════════════════
// 5. Page structural / accessibility baseline
// ═════════════════════════════════════════════════════════════════════════════

describe('LevyPaymentPage — structural and accessibility baseline', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        _currentAuth = mockECWithUnit;
        setupDefaultMocks();
    });

    it('renders the page container', async () => {
        await act(async () => {
            renderPage();
        });
        expect(screen.getByTestId('levy-payment-page')).toBeInTheDocument();
    });

    it('renders all three navigation tabs', async () => {
        await act(async () => {
            renderPage();
        });
        expect(screen.getByRole('tab', {name: /Payment Methods/i})).toBeInTheDocument();
        expect(screen.getByRole('tab', {name: /Levy Status/i})).toBeInTheDocument();
        expect(screen.getByRole('tab', {name: /Payment History/i})).toBeInTheDocument();
    });

    it('EC member banner has role="note" for screen readers', async () => {
        await act(async () => {
            renderPage();
        });
        const banner = await screen.findByTestId('ec-member-levy-banner');
        expect(banner).toHaveAttribute('role', 'note');
        expect(banner).toHaveAttribute('aria-label');
    });

    it('the record-payment button appears for EC members (can_manage_finances)', async () => {
        await act(async () => {
            renderPage();
        });
        // Multiple "Record Payment" buttons exist (header + per-quarter), check at least one is present
        const buttons = screen.getAllByRole('button', {name: /Record Payment/i});
        expect(buttons.length).toBeGreaterThan(0);
    });
});
