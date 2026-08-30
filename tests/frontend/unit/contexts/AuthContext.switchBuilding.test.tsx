// @featuretrace:user-management — Regression test: switch-building infinite loop guard (JWT plan-number vs UUID comparison).
// Layer: test
// Data flow: AuthProvider render → GET /auth/me, /buildings/me → POST /auth/switch-building (building-scoped).
// Related: frontend/src/contexts/AuthContext.tsx
//           backend/routers/auth.py
// Tests: this file

/**
 * Regression test: switch-building infinite loop guard.
 *
 * Fixed in commit b9bc1a0c. Root cause: JWT carries building_id = plan number
 * (e.g. "13195") but the old code compared it against selectedBuilding.id
 * (a UUID). jwtBuildingId !== currentId was always true, so switch-building
 * was called on every fetchUser, which rotated the token, which re-triggered
 * fetchUser, looping indefinitely.
 *
 * These tests exercise AuthProvider directly (not via mock) to verify the
 * real guard logic inside fetchUser.
 */

import React from 'react';
import { render, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

// ─── Axios mock ───────────────────────────────────────────────────────────────
// Must be defined before imports so the closure captures live jest.fn references.
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
import { AuthProvider } from '@/contexts/AuthContext';

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

// Builds a fake JWT with the given payload. Only the base64-encoded middle
// segment is read by fetchUser (via atob), so no real signature is needed.
function fakeJwt(payload: object): string {
    const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const body = btoa(JSON.stringify(payload));
    return `${header}.${body}.fakesig`;
}

function setupGetMocks() {
    mockGet.mockImplementation((url: string) => {
        if (url === '/auth/me') return Promise.resolve({ data: USER });
        if (url === '/buildings/me') return Promise.resolve({ data: [BUILDING] });
        // Must be an array — code calls .forEach() on the response body.
        if (url === '/feature-toggles/access-summary/me') return Promise.resolve({ data: [] });
        // Field name is unread_count, not count — code reads countRes.data.unread_count.
        if (url === '/notifications/unread-count') return Promise.resolve({ data: { unread_count: 0 } });
        // Actual call includes ?limit=10 query string — use startsWith to match.
        if (url.startsWith('/notifications/history')) return Promise.resolve({ data: [] });
        if (url === '/years') return Promise.resolve({ data: ['2026', '2025'] });
        if (url === '/settings') return Promise.resolve({ data: {} });
        return Promise.resolve({ data: null });
    });
}

// ─── Tests ────────────────────────────────────────────────────────────────────
describe('AuthContext — switch-building loop guard', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        localStorage.setItem('selectedBuilding', JSON.stringify(BUILDING));
        setupGetMocks();
    });

    afterEach(() => {
        localStorage.clear();
    });

    it('does NOT call switch-building when JWT building_id (plan number) already matches localStorage', async () => {
        // JWT already carries building_id: "13195" — the plan number, not the UUID.
        const scopedToken = fakeJwt({ user_id: USER.id, building_id: '13195', exp: 9999999999 });

        mockUseSession.mockReturnValue({
            data: { accessToken: scopedToken, user: { data: USER } },
            status: 'authenticated',
        });

        render(
            <AuthProvider>
                <div data-testid="app">loaded</div>
            </AuthProvider>,
        );

        // Wait for fetchUser's first API call to confirm it ran.
        await waitFor(
            () => expect(mockGet).toHaveBeenCalledWith('/auth/me', expect.anything()),
            { timeout: 3000 },
        );

        // Allow all async effects (buildings fetch, year fetch, etc.) to settle.
        await new Promise(r => setTimeout(r, 300));

        const switchCalls = mockPost.mock.calls.filter(([url]) => url === '/auth/switch-building');
        expect(switchCalls).toHaveLength(0);
    });

    it('calls switch-building exactly once when JWT is unscoped, then stops on re-fetch', async () => {
        // Unscoped JWT has no building_id claim.
        const unscopedToken = fakeJwt({ user_id: USER.id, exp: 9999999999 });
        // Scoped JWT returned by the switch-building endpoint.
        const scopedToken = fakeJwt({ user_id: USER.id, building_id: '13195', exp: 9999999999 });

        mockUseSession.mockReturnValue({
            data: { accessToken: unscopedToken, user: { data: USER } },
            status: 'authenticated',
        });

        mockPost.mockResolvedValue({
            data: { token: scopedToken, building: BUILDING, user: USER },
        });

        render(
            <AuthProvider>
                <div data-testid="app">loaded</div>
            </AuthProvider>,
        );

        await waitFor(
            () => expect(mockGet).toHaveBeenCalledWith('/auth/me', expect.anything()),
            { timeout: 3000 },
        );

        // Allow time for: first fetchUser → switch → setToken → second fetchUser (no switch).
        await new Promise(r => setTimeout(r, 600));

        const switchCalls = mockPost.mock.calls.filter(([url]) => url === '/auth/switch-building');

        // Must be called exactly once — the second fetchUser sees jwtBuildingId === currentBuildingId.
        expect(switchCalls).toHaveLength(1);

        // Must pass the UUID (currentId), not the plan number — backend accepts both.
        expect(switchCalls[0][1]).toEqual({ building_id: BUILDING.id });
    });
});
