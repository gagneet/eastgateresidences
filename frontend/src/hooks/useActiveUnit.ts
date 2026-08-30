// @featuretrace:multi-unit-ownership — canonical "which unit am I looking at" resolver for owner-facing pages.
// Layer: frontend
// Data flow: AuthContext (selectedUnit ← POST /auth/switch-unit) → useActiveUnit() → owner-facing page
//            data fetches (?unit_number=…) (building-scoped).
// Related: frontend/src/contexts/AuthContext.tsx
//           frontend/src/components/layout/UnitSwitcher.tsx
//           frontend/src/pages/dashboard/MyFinancesPage.jsx
//           backend/routers/auth.py (POST /auth/switch-unit, GET /auth/my-units)
//           backend/utils/unit_number.py (authorise_owner_unit)
// Tests: tests/frontend/unit/hooks/useActiveUnit.test.tsx
"use client";

import {useEffect, useMemo, useRef} from 'react';
import {useAuth} from '../contexts/AuthContext';

export interface ActiveUnitContext {
    /**
     * The unit every owner-facing page on screen should be scoped to.
     *
     * Resolution order — first non-empty wins:
     *   1. `selectedUnit`      — the sidebar UnitSwitcher's choice for this session.
     *   2. `user.unit_number`  — the account's own unit (also what `POST /auth/switch-unit`
     *                            rewrites into the re-issued JWT, so it usually agrees with 1).
     *   3. `user.owned_units[0]` — co-owner / imported accounts that carry links but no
     *                            `unit_number` on the account row itself.
     */
    activeUnit: string | null;
    /** Every unit this user is actively linked to in the current building. */
    availableUnits: string[];
    /** Switch the active unit (re-issues the JWT server-side). */
    switchUnit: (unitNumber: string) => Promise<void>;
    /** True when the account holds more than one unit in this building. */
    isMultiUnit: boolean;
}

/**
 * Single source of truth for the active unit on owner-facing pages.
 *
 * Before this hook, four pages hand-rolled the fallback chain and a dozen more
 * read `user.unit_number` directly — the latter pinning a multi-unit owner to
 * their account's default unit no matter what the sidebar switcher said
 * (GAP-IDENTITY-UNIT-SWITCH-001). Pages must scope fetches to `activeUnit` and
 * list it in their loader's dependency array so switching re-queries rather
 * than only relabelling.
 */
export function useActiveUnit(): ActiveUnitContext {
    const {user, selectedUnit, availableUnits, switchUnit} = useAuth() as any;

    const owned: string[] = Array.isArray(user?.owned_units) ? user.owned_units : [];
    const units: string[] = availableUnits?.length ? availableUnits : owned;

    // Memoise on CONTENT, not on array identity. `user` is replaced wholesale on
    // every AuthContext update, so `user.owned_units` is a new array each time even
    // when the units are identical — keying the memo on it would hand back a fresh
    // `availableUnits` array on every render. Any consumer putting that array in a
    // dependency array would then re-fetch forever: the same unstable-reference
    // class that made two dashboard test files hang rather than fail (2026-08-24).
    const unitsKey = units.join('\u0000');
    const activeUnit: string | null = selectedUnit || user?.unit_number || owned[0] || null;

    return useMemo(
        () => ({
            activeUnit,
            // Split back out of the key so the returned array is stable while the
            // contents are: same units in, same array reference out.
            availableUnits: unitsKey ? unitsKey.split('\u0000') : [],
            switchUnit,
            isMultiUnit: unitsKey ? unitsKey.split('\u0000').length > 1 : false,
        }),
        [activeUnit, unitsKey, switchUnit],
    );
}

/**
 * Keep a page's own unit picker in step with the sidebar switcher.
 *
 * Calls `apply(unit)` on mount and again every time the active unit actually
 * changes — never on an unrelated re-render. That distinction is the whole
 * point: pages that let a manager browse another unit (water bills, TCO) must
 * not have that choice yanked back on every render, but a genuine switch in the
 * sidebar should re-point the page rather than leave it on the old unit.
 *
 *     const [viewed, setViewed] = useState('');
 *     useActiveUnitSync(setViewed);
 */
export function useActiveUnitSync(apply: (unitNumber: string) => void): string | null {
    const {activeUnit} = useActiveUnit();
    const lastApplied = useRef<string | null>(null);
    const applyRef = useRef(apply);
    // Assigned in an effect, never during render: a ref written during render can
    // be stale for the render that reads it, and React's lint rule rejects it.
    // Declared before the effect below so it always holds the latest callback.
    useEffect(() => {
        applyRef.current = apply;
    });

    useEffect(() => {
        if (!activeUnit || activeUnit === lastApplied.current) return;
        lastApplied.current = activeUnit;
        applyRef.current(activeUnit);
    }, [activeUnit]);

    return activeUnit;
}

export default useActiveUnit;
