/**
 * Tests for SavingsLedgerPage — YearSelector integration
 *
 * Verifies that:
 * 1. The page renders using the shared YearSelector (not hardcoded FISCAL_YEARS)
 * 2. The global selectedYear from AuthContext drives API calls
 * 3. Year "2026" is converted to "FY2026-27" when sent to the savings API
 * 4. The API is called with `financial_year` param (correct backend param name)
 * 5. Summary stats are read from the correct API response fields (ytd_saved_cents, all_time_saved_cents)
 * 6. by_category values are correctly extracted from {total_saved_cents, event_count} objects
 * 7. Event list reads saved_cents (not amount_cents) from API
 * 8. Admin-only "Record Saving" button visibility by role
 */

import React from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import SavingsLedgerPage from '@/pages/dashboard/SavingsLedgerPage';

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
    usePathname: () => '/financials/savings',
}));

jest.mock('sonner', () => ({toast: {success: jest.fn(), error: jest.fn()}}));

// Mock YearSelector — renders a testid so we can assert it's present
jest.mock('@/components/widgets/YearSelector', () => ({
    __esModule: true,
    default: () => <div data-testid="year-selector">YearSelector</div>,
}));

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

function makeAuthMock(overrides: Record<string, unknown> = {}) {
    return {
        api: mockApi,
        user: {role: 'owner', id: 'u1'},
        selectedYear: '2026',
        availableYears: ['2026', '2025'],
        ...overrides,
    };
}

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn(),
    AuthProvider: ({children}: any) => <>{children}</>,
}));

import {useAuth} from '@/contexts/AuthContext';

const mockUseAuth = useAuth as jest.Mock;

// ── Fixtures — use ACTUAL API response field names from savings_engine.py ────
// The backend returns: ytd_saved_cents, all_time_saved_cents, by_category (objects)
const SUMMARY_RESPONSE = {
    data: {
        financial_year: 'FY2026-27',
        ytd_saved_cents: 125000,           // NOT savings_ytd_cents
        all_time_saved_cents: 250000,      // NOT all_time_cents
        ytd_event_count: 3,
        all_time_event_count: 10,
        by_category: {
            // Values are objects, not bare numbers
            energy: {total_saved_cents: 80000, event_count: 2},
            water: {total_saved_cents: 45000, event_count: 1},
        },
    },
};

const LIST_RESPONSE = {
    data: [
        {
            id: 'sv1',
            category: 'energy',
            resident_summary: 'LED upgrade saved electricity',
            saved_cents: 80000,            // API field is saved_cents (not amount_cents)
            event_date: '2026-03-01T00:00:00Z',
            verified: true,
            financial_year: 'FY2026-27',
        },
    ],
};

beforeEach(() => {
    jest.clearAllMocks();
    mockUseAuth.mockReturnValue(makeAuthMock());
    mockApiGet.mockImplementation((url: string) => {
        if (url.includes('/savings/summary')) return Promise.resolve(SUMMARY_RESPONSE);
        if (url.includes('/savings/')) return Promise.resolve(LIST_RESPONSE);
        return Promise.resolve({data: []});
    });
});

// ── 1. YearSelector component is rendered (not a hardcoded Select) ───────────
test('renders shared YearSelector component', async () => {
    render(<SavingsLedgerPage/>);
    await waitFor(() => expect(screen.getByTestId('year-selector')).toBeInTheDocument());
});

// ── 2. API called with `financial_year` param (correct backend param name) ───
test('calls savings API with financial_year param (not fiscal_year)', async () => {
    render(<SavingsLedgerPage/>);
    await waitFor(() => {
        expect(mockApiGet).toHaveBeenCalledWith(
            expect.stringContaining('/savings/'),
            expect.objectContaining({params: expect.objectContaining({financial_year: 'FY2026-27'})})
        );
    });
});

// ── 3. FY format conversion: "2026" → "FY2026-27" ────────────────────────────
test('converts selectedYear 2026 to FY2026-27 for API call', async () => {
    render(<SavingsLedgerPage/>);
    await waitFor(() => {
        const call = mockApiGet.mock.calls.find(c =>
            c[1]?.params?.financial_year === 'FY2026-27'
        );
        expect(call).toBeTruthy();
    });
});

// ── 4. YTD stat reads ytd_saved_cents (correct field name) ───────────────────
test('displays YTD savings from ytd_saved_cents field', async () => {
    render(<SavingsLedgerPage/>);
    // 125000 cents = $1,250.00
    await waitFor(() => expect(screen.getByText(/\$1,250\.00/)).toBeInTheDocument());
});

// ── 5. All Time stat reads all_time_saved_cents ───────────────────────────────
test('displays All Time savings from all_time_saved_cents field', async () => {
    render(<SavingsLedgerPage/>);
    // 250000 cents = $2,500.00
    await waitFor(() => expect(screen.getByText(/\$2,500\.00/)).toBeInTheDocument());
});

// ── 6. Category bar renders amounts from nested total_saved_cents ─────────────
test('renders category breakdown with amounts from total_saved_cents', async () => {
    render(<SavingsLedgerPage/>);
    // energy: 80000 cents = $800.00
    await waitFor(() => expect(screen.getAllByText(/\$800\.00/).length).toBeGreaterThan(0));
});

// ── 7. Event list renders using saved_cents field ─────────────────────────────
test('renders savings event amount from saved_cents', async () => {
    render(<SavingsLedgerPage/>);
    await waitFor(() => expect(screen.getByText('LED upgrade saved electricity')).toBeInTheDocument());
    // $800.00 from saved_cents: 80000
    await waitFor(() => expect(screen.getAllByText(/\$800\.00/).length).toBeGreaterThan(0));
});

// ── 8. Empty state shows fiscal year label ────────────────────────────────────
test('empty state message shows FY2026-27 when no data', async () => {
    mockApiGet.mockImplementation(() => Promise.resolve({data: []}));
    render(<SavingsLedgerPage/>);
    await waitFor(() => expect(screen.getByText(/FY2026-27/)).toBeInTheDocument());
});

// ── 9. "Record Saving" button hidden for non-admin role ──────────────────────
test('Record Saving button is hidden for owner role', async () => {
    render(<SavingsLedgerPage/>);
    await waitFor(() => expect(screen.queryByText('Record Saving')).not.toBeInTheDocument());
});

// ── 10. "Record Saving" button visible for strata_manager ────────────────────
// (Post-migration 0025: chairman is no longer a top-level role.
// The page's ADMIN_ROLES = ['super_admin', 'strata_manager'].)
test('Record Saving button is visible for strata_manager role', async () => {
    mockUseAuth.mockReturnValue(makeAuthMock({user: {role: 'strata_manager', id: 'u2'}}));
    render(<SavingsLedgerPage/>);
    await waitFor(() => expect(screen.getByText('Record Saving')).toBeInTheDocument());
});

// ── 11. API not called if selectedYear is null (graceful no-op) ───────────────
test('does not call API when selectedYear is null', () => {
    mockUseAuth.mockReturnValue(makeAuthMock({selectedYear: null}));
    render(<SavingsLedgerPage/>);
    expect(mockApiGet).not.toHaveBeenCalled();
});

// ── 12. Year "2025" converts to FY2025-26 ────────────────────────────────────
test('converts selectedYear 2025 to FY2025-26 for API call', async () => {
    mockUseAuth.mockReturnValue(makeAuthMock({selectedYear: '2025'}));
    render(<SavingsLedgerPage/>);
    await waitFor(() => {
        expect(mockApiGet).toHaveBeenCalledWith(
            expect.stringContaining('/savings/'),
            expect.objectContaining({params: expect.objectContaining({financial_year: 'FY2025-26'})})
        );
    });
});
