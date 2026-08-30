// @featuretrace:multi-unit-ownership — guards the active-unit resolution order and picker sync.
// Layer: test
// Data flow: mocked AuthContext → useActiveUnit / useActiveUnitSync (building-scoped).
// Related: frontend/src/hooks/useActiveUnit.ts
//          frontend/src/components/layout/UnitSwitcher.tsx
/**
 * useActiveUnit.test.tsx — GAP-IDENTITY-UNIT-SWITCH-001
 *
 * The hook is the single answer to "which unit is this page showing". Before it,
 * four pages hand-rolled the fallback chain and a dozen more read
 * `user.unit_number` directly, which pinned a multi-unit owner to their account's
 * default unit no matter what the sidebar switcher said.
 *
 * `useActiveUnitSync` is the companion for pages that keep their own picker: it
 * must re-point on a genuine switch and stay out of the way otherwise, so a
 * manager browsing another unit does not have their choice yanked back on every
 * render.
 */
import React from 'react';
import {render, screen, act} from '@testing-library/react';
import '@testing-library/jest-dom';
import {useActiveUnit, useActiveUnitSync} from '@/hooks/useActiveUnit';

// Returned BY REFERENCE, never as a fresh object literal per call: an unstable
// auth mock gives `api` a new identity every render, which turns any loader
// effect that depends on it into an infinite re-render (see the two suites that
// hung for ever before 2026-08-24).
let mockAuth: any = {};
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuth,
}));

const Probe: React.FC = () => {
    const {activeUnit, availableUnits, isMultiUnit} = useActiveUnit();
    return (
        <div>
            <span data-testid="active">{activeUnit ?? 'none'}</span>
            <span data-testid="units">{availableUnits.join(',')}</span>
            <span data-testid="multi">{String(isMultiUnit)}</span>
        </div>
    );
};

const switchUnit = jest.fn();

beforeEach(() => {
    switchUnit.mockClear();
    mockAuth = {user: null, selectedUnit: null, availableUnits: [], switchUnit};
});

describe('useActiveUnit — resolution order', () => {
    test('the sidebar switcher wins over the account default', () => {
        mockAuth = {
            user: {unit_number: 'UA013', owned_units: ['UA013', 'TH087']},
            selectedUnit: 'TH087',
            availableUnits: ['UA013', 'TH087'],
            switchUnit,
        };
        render(<Probe/>);
        expect(screen.getByTestId('active')).toHaveTextContent('TH087');
        expect(screen.getByTestId('multi')).toHaveTextContent('true');
    });

    test('falls back to the account unit when nothing has been switched', () => {
        mockAuth = {
            user: {unit_number: 'UA013', owned_units: ['UA013']},
            selectedUnit: null,
            availableUnits: [],
            switchUnit,
        };
        render(<Probe/>);
        expect(screen.getByTestId('active')).toHaveTextContent('UA013');
        expect(screen.getByTestId('multi')).toHaveTextContent('false');
    });

    test('falls back to the first owned unit for accounts with links but no unit_number', () => {
        // Co-owner and imported accounts carry `owned_units` while the account row
        // itself has no unit; without this leg they resolve to null and every
        // per-unit page renders empty.
        mockAuth = {
            user: {unit_number: null, owned_units: ['UA045']},
            selectedUnit: null,
            availableUnits: [],
            switchUnit,
        };
        render(<Probe/>);
        expect(screen.getByTestId('active')).toHaveTextContent('UA045');
    });

    test('resolves to null — not undefined or a crash — for an account with no unit', () => {
        mockAuth = {user: {}, selectedUnit: null, availableUnits: [], switchUnit};
        render(<Probe/>);
        expect(screen.getByTestId('active')).toHaveTextContent('none');
        expect(screen.getByTestId('multi')).toHaveTextContent('false');
    });

    test('availableUnits prefers the context list and falls back to owned_units', () => {
        mockAuth = {
            user: {unit_number: 'UA013', owned_units: ['UA013', 'TH087']},
            selectedUnit: null,
            availableUnits: [],
            switchUnit,
        };
        const {rerender} = render(<Probe/>);
        expect(screen.getByTestId('units')).toHaveTextContent('UA013,TH087');

        mockAuth = {...mockAuth, availableUnits: ['UA013', 'TH087', 'UA045']};
        rerender(<Probe/>);
        expect(screen.getByTestId('units')).toHaveTextContent('UA013,TH087,UA045');
        expect(screen.getByTestId('multi')).toHaveTextContent('true');
    });
});

describe('useActiveUnit — reference stability', () => {
    test('returns the SAME availableUnits array across renders when contents match', () => {
        // `user` is replaced wholesale on every AuthContext update, so
        // `user.owned_units` is a new array each time even when the units are
        // identical. If that identity leaked through, any consumer putting
        // `availableUnits` in a dependency array would re-fetch for ever — the same
        // unstable-reference class that made two dashboard suites hang (2026-08-24).
        const seen: string[][] = [];
        const Capture: React.FC = () => {
            const {availableUnits} = useActiveUnit();
            seen.push(availableUnits);
            return <div/>;
        };

        mockAuth = {
            user: {unit_number: 'UA013', owned_units: ['UA013', 'TH087']},
            selectedUnit: 'UA013',
            availableUnits: [],
            switchUnit,
        };
        const {rerender} = render(<Capture/>);

        // A new user object with an equal-but-distinct owned_units array.
        mockAuth = {
            ...mockAuth,
            user: {unit_number: 'UA013', owned_units: ['UA013', 'TH087']},
        };
        rerender(<Capture/>);

        expect(seen.length).toBeGreaterThan(1);
        expect(seen[seen.length - 1]).toBe(seen[0]);
    });

    test('returns a NEW array once the units genuinely change', () => {
        const seen: string[][] = [];
        const Capture: React.FC = () => {
            const {availableUnits} = useActiveUnit();
            seen.push(availableUnits);
            return <div/>;
        };

        mockAuth = {
            user: {unit_number: 'UA013', owned_units: ['UA013']},
            selectedUnit: 'UA013',
            availableUnits: [],
            switchUnit,
        };
        const {rerender} = render(<Capture/>);

        mockAuth = {
            ...mockAuth,
            user: {unit_number: 'UA013', owned_units: ['UA013', 'UA045']},
        };
        rerender(<Capture/>);

        expect(seen[seen.length - 1]).not.toBe(seen[0]);
        expect(seen[seen.length - 1]).toEqual(['UA013', 'UA045']);
    });
});

describe('useActiveUnitSync — pages with their own picker', () => {
    const applied: string[] = [];
    const Syncing: React.FC = () => {
        useActiveUnitSync((unit) => {
            applied.push(unit);
        });
        return <div/>;
    };

    beforeEach(() => {
        applied.length = 0;
    });

    test('seeds the page on mount from the active unit', () => {
        mockAuth = {user: {unit_number: 'UA013'}, selectedUnit: null, availableUnits: [], switchUnit};
        render(<Syncing/>);
        expect(applied).toEqual(['UA013']);
    });

    test('re-points the page when the sidebar switches units', () => {
        mockAuth = {
            user: {unit_number: 'UA013', owned_units: ['UA013', 'TH087']},
            selectedUnit: 'UA013',
            availableUnits: ['UA013', 'TH087'],
            switchUnit,
        };
        const {rerender} = render(<Syncing/>);
        expect(applied).toEqual(['UA013']);

        act(() => {
            mockAuth = {...mockAuth, selectedUnit: 'TH087'};
        });
        rerender(<Syncing/>);
        expect(applied).toEqual(['UA013', 'TH087']);
    });

    test('does NOT re-apply on an unrelated re-render — a manual pick survives', () => {
        // This is what protects a manager who has selected another owner's unit in
        // the page's own picker: the active unit has not changed, so nothing is
        // re-applied and their selection stands.
        mockAuth = {user: {unit_number: 'UA013'}, selectedUnit: 'UA013', availableUnits: ['UA013'], switchUnit};
        const {rerender} = render(<Syncing/>);
        expect(applied).toEqual(['UA013']);

        rerender(<Syncing/>);
        rerender(<Syncing/>);
        expect(applied).toEqual(['UA013']);
    });

    test('does nothing at all while the active unit is still unresolved', () => {
        mockAuth = {user: null, selectedUnit: null, availableUnits: [], switchUnit};
        const {rerender} = render(<Syncing/>);
        expect(applied).toEqual([]);

        // …and applies once the session hydrates.
        act(() => {
            mockAuth = {...mockAuth, user: {unit_number: 'UA013'}};
        });
        rerender(<Syncing/>);
        expect(applied).toEqual(['UA013']);
    });
});
