// @featuretrace:multi-unit-ownership — guards that My Finances re-queries on a unit switch.
// Layer: test
// Data flow: mocked AuthContext + api → MyFinancesPage → /owner-finance/*?unit_number= (building-scoped).
// Related: frontend/src/pages/dashboard/MyFinancesPage.jsx
//          backend/routers/owner_finance.py
/**
 * MyFinancesUnitScope.test.tsx — GAP-IDENTITY-UNIT-SWITCH-001, Tier 1
 *
 * The reported gap: an owner with two units in the same building had NO way,
 * from My Finances, to see any unit's finances but their account's default. The
 * page read `user.unit_number`, sent no unit to the backend, and its loader did
 * not depend on the active unit — so switching in the sidebar relabelled the
 * header at best and changed nothing below it.
 *
 * These assertions are about the fetch, not the render: a page that changes its
 * heading without re-querying is the exact failure being guarded against.
 */
import React from 'react';
import {render, screen, waitFor, act} from '@testing-library/react';
import '@testing-library/jest-dom';
import MyFinancesPage from '@/pages/dashboard/MyFinancesPage';

const get = jest.fn();
const switchUnit = jest.fn();

// Stable object identity per render — see useActiveUnit.test.tsx for why.
let mockAuth: any = {};
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuth,
}));

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const unitParamsOf = (calls: any[][]): string[] =>
    calls
        .map(([url]) => String(url))
        .filter((url) => url.includes('/owner-finance/levy-breakdown'))
        .map((url) => new URL(url, 'http://x').searchParams.get('unit_number') ?? '');

const hasFeatureAccess = jest.fn(() => true);

// Hoisted so every mockAuth carries the SAME `api` reference. The real AuthContext
// memoises `api` with useMemo(..., []); a fresh `{get}` literal per call would give
// it a new identity on each render and re-fire every loader keyed on it — which is
// not a production behaviour, and is the harness bug that made two dashboard suites
// hang rather than fail (2026-08-24). It also silently invalidates the
// "building-wide data is not re-fetched" assertion below, since that fetch is keyed
// on `api` alone.
const api = {get};

const setAuth = (selectedUnit: string | null) => {
    mockAuth = {
        api,
        user: {unit_number: 'UA013', owned_units: ['UA013', 'TH087'], role: 'owner'},
        selectedUnit,
        availableUnits: ['UA013', 'TH087'],
        switchUnit,
        hasFeatureAccess,
    };
};

const callsTo = (fragment: string): string[] =>
    get.mock.calls.map(([url]) => String(url)).filter((url) => url.includes(fragment));

beforeEach(() => {
    get.mockReset();
    switchUnit.mockClear();
    hasFeatureAccess.mockClear();
    hasFeatureAccess.mockReturnValue(true);
    get.mockResolvedValue({data: {}});
    setAuth('UA013');
});

describe('My Finances follows the active unit', () => {
    test('scopes its per-unit fetches to the active unit', async () => {
        render(<MyFinancesPage/>);
        await waitFor(() => expect(get).toHaveBeenCalled());

        expect(unitParamsOf(get.mock.calls)).toEqual(['UA013']);

        // health-explanation is building-wide and must NOT carry a unit.
        expect(callsTo('/owner-finance/health-explanation')).toEqual([
            '/owner-finance/health-explanation',
        ]);
    });

    test('re-queries when the sidebar switches units — not just relabels', async () => {
        const {rerender} = render(<MyFinancesPage/>);
        await waitFor(() => expect(unitParamsOf(get.mock.calls)).toEqual(['UA013']));

        act(() => {
            setAuth('TH087');
        });
        rerender(<MyFinancesPage/>);

        await waitFor(() =>
            expect(unitParamsOf(get.mock.calls)).toEqual(['UA013', 'TH087']),
        );
    });

    test('does NOT re-fetch the building-wide data on a unit switch', async () => {
        // Switching units cannot move a building's health score. Re-requesting it
        // would contradict the page's own contract and blank that card behind the
        // loading state for data that provably did not change — which is why the
        // per-unit and building-wide fetches have separate lifecycles.
        const {rerender} = render(<MyFinancesPage/>);
        await waitFor(() => expect(callsTo('/owner-finance/health-explanation')).toHaveLength(1));

        act(() => {
            setAuth('TH087');
        });
        rerender(<MyFinancesPage/>);

        await waitFor(() => expect(unitParamsOf(get.mock.calls)).toEqual(['UA013', 'TH087']));
        expect(callsTo('/owner-finance/health-explanation')).toHaveLength(1);
        expect(callsTo('/settings')).toHaveLength(1);
    });

    test('offers an in-page unit picker to multi-unit owners', async () => {
        render(<MyFinancesPage/>);
        await waitFor(() =>
            expect(screen.getByTestId('my-finances-unit-select')).toBeInTheDocument(),
        );
        expect(hasFeatureAccess).toHaveBeenCalledWith('multi_unit_ownership');
    });

    test('hides the picker when multi_unit_ownership is off for the building', async () => {
        // The picker calls switchUnit → POST /auth/switch-unit, which is gated by
        // require_feature("multi_unit_ownership"). Rendering it for a building with
        // the toggle off would give every selection a 403 and an error toast.
        hasFeatureAccess.mockReturnValue(false);
        render(<MyFinancesPage/>);
        await waitFor(() => expect(get).toHaveBeenCalled());
        expect(screen.queryByTestId('my-finances-unit-select')).not.toBeInTheDocument();
    });

    test('single-unit owners see no picker and send their own unit', async () => {
        mockAuth = {
            api,
            user: {unit_number: 'UA013', owned_units: ['UA013'], role: 'owner'},
            selectedUnit: null,
            availableUnits: ['UA013'],
            switchUnit,
            hasFeatureAccess,
        };
        render(<MyFinancesPage/>);
        await waitFor(() => expect(unitParamsOf(get.mock.calls)).toEqual(['UA013']));
        expect(screen.queryByTestId('my-finances-unit-select')).not.toBeInTheDocument();
    });

    test('an account with no unit still loads — no unit parameter, no crash', async () => {
        mockAuth = {
            api,
            user: {unit_number: null, owned_units: [], role: 'owner'},
            selectedUnit: null,
            availableUnits: [],
            switchUnit,
            hasFeatureAccess,
        };
        render(<MyFinancesPage/>);
        await waitFor(() => expect(get).toHaveBeenCalled());
        expect(callsTo('/owner-finance/levy-breakdown')).toEqual([
            '/owner-finance/levy-breakdown',
        ]);
    });
});
