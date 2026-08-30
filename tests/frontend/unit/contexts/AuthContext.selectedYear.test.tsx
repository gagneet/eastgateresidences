// @featuretrace:levy — Regression test: selectedYear defaults to the current
// calendar year, not blindly to years[0] from GET /years.
// Layer: test
// Data flow: AuthProvider render → GET /years → selectedYear (building-scoped).
// Related: frontend/src/contexts/AuthContext.tsx
//           backend/routers/finance.py (GET /years)
// Tests: this file

/**
 * Regression test: Owner Dashboard levy underreporting bug.
 *
 * Root cause: GET /years returns `sorted(annual_levies.distinct("year"), reverse=True)`.
 * A still-forming next-FY `annual_levies` doc (status "partial_actual") sorts above
 * the real current year, so the old code (`setSelectedYearState(years[0])`) silently
 * defaulted every dashboard widget — including the Owner Hub "12-month true cost"
 * card — to next year's partial rates instead of the actual current FY.
 *
 * Fix: prefer the current calendar year when it's present in the years list; only
 * fall back to years[0] when the current year isn't listed.
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

// ─── Axios mock ───────────────────────────────────────────────────────────────
const mockGet = jest.fn();
const mockPost = jest.fn();

jest.mock('axios', () => ({
    __esModule: true,
    default: {
        create: jest.fn(() => ({
            get: (...args: unknown[]) => mockGet(...args),
            post: (...args: unknown[]) => mockPost(...args),
            put: jest.fn().mockResolvedValue({ data: {} }),
            delete: jest.fn().mockResolvedValue({ data: {} }),
            interceptors: {
                request: { use: jest.fn(), eject: jest.fn() },
                response: { use: jest.fn(), eject: jest.fn() },
            },
        })),
    },
}));

// ─── next-auth ────────────────────────────────────────────────────────────────
const mockUseSession = jest.fn();
jest.mock('next-auth/react', () => ({
    __esModule: true,
    useSession: () => mockUseSession(),
    SessionProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    signIn: jest.fn(),
    signOut: jest.fn(),
}));

// ─── Misc peer deps ───────────────────────────────────────────────────────────
jest.mock('sonner', () => ({
    toast: { error: jest.fn(), success: jest.fn(), info: jest.fn(), warning: jest.fn() },
}));
jest.mock('@/lib/api-error', () => ({ classifyApiError: jest.fn() }));
jest.mock('next/navigation', () => ({
    useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
    usePathname: () => '/dashboard',
    useSearchParams: () => ({ get: jest.fn(() => null) }),
}));

// ─── Import under test ────────────────────────────────────────────────────────
import { AuthProvider, useAuth } from '@/contexts/AuthContext';

// ─── Fixtures ─────────────────────────────────────────────────────────────────
const BUILDING = {
    id: 'abc-123-scheme-uuid-east-gate',
    building_id: '13195',
    name: 'East Gate Residences',
    address: '1 Demo St, Denman Prospect',
};

const USER = {
    id: 'user-001',
    email: 'owner@test.com',
    full_name: 'Test Owner',
    role: 'owner',
    is_approved: true,
    created_at: '2025-01-01T00:00:00Z',
};

function fakeJwt(payload: object): string {
    const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const body = btoa(JSON.stringify(payload));
    return `${header}.${body}.fakesig`;
}

function setupGetMocks(years: string[]) {
    mockGet.mockImplementation((url: string) => {
        if (url === '/auth/me') return Promise.resolve({ data: USER });
        if (url === '/buildings/me') return Promise.resolve({ data: [BUILDING] });
        if (url === '/feature-toggles/access-summary/me') return Promise.resolve({ data: [] });
        if (url === '/notifications/unread-count') return Promise.resolve({ data: { unread_count: 0 } });
        if (url.startsWith('/notifications/history')) return Promise.resolve({ data: [] });
        if (url === '/years') return Promise.resolve({ data: years });
        if (url === '/settings') return Promise.resolve({ data: {} });
        return Promise.resolve({ data: null });
    });
}

// Small consumer that surfaces selectedYear into the DOM for assertions.
function SelectedYearProbe() {
    const { selectedYear } = useAuth() as any;
    return <div data-testid="selected-year">{selectedYear ?? ''}</div>;
}

describe('AuthContext — selectedYear default', () => {
    let dateSpy: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        localStorage.setItem('selectedBuilding', JSON.stringify(BUILDING));

        const scopedToken = fakeJwt({ user_id: USER.id, building_id: '13195', exp: 9999999999 });
        mockUseSession.mockReturnValue({
            data: { accessToken: scopedToken, user: { data: USER } },
            status: 'authenticated',
        });

        // Pin "today" inside FY2026 regardless of when the suite actually runs.
        dateSpy = jest.spyOn(Date.prototype, 'getFullYear').mockReturnValue(2026);
    });

    afterEach(() => {
        localStorage.clear();
        dateSpy.mockRestore();
    });

    it('defaults to the current calendar year, not years[0], when a newer partial-FY year sorts first', async () => {
        // "2027" is a still-forming next-FY budget doc that sorts above "2026" —
        // this reproduces the exact production bug (annual_levies partial_actual row).
        setupGetMocks(['2027', '2026', '2025']);

        render(
            <AuthProvider>
                <SelectedYearProbe />
            </AuthProvider>,
        );

        await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/years'));

        await waitFor(
            () => expect(screen.getByTestId('selected-year').textContent).toBe('2026'),
            { timeout: 3000 },
        );
    });

    it('falls back to years[0] when the current calendar year is not in the list', async () => {
        setupGetMocks(['2027', '2025']);

        render(
            <AuthProvider>
                <SelectedYearProbe />
            </AuthProvider>,
        );

        await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/years'));

        await waitFor(
            () => expect(screen.getByTestId('selected-year').textContent).toBe('2027'),
            { timeout: 3000 },
        );
    });

    it('ignores a stale saved future year until /years confirms it is selectable', async () => {
        localStorage.setItem('selectedYear', '2027');
        setupGetMocks(['2026', '2025']);

        render(
            <AuthProvider>
                <SelectedYearProbe />
            </AuthProvider>,
        );

        await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/years'));

        await waitFor(
            () => expect(screen.getByTestId('selected-year').textContent).toBe('2026'),
            { timeout: 3000 },
        );
    });
});
