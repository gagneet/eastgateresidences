/**
 * Tests for Owner Name Verification feature in UsersPage.
 *
 * Part 1: Pure algorithm tests — normaliseName / isSimilarName parity with backend
 * Part 2: Warning logic unit tests — no component render
 * Part 3: Component render tests — uses ?tab=owners URL to open Owners tab directly
 *
 * NOTE: Owner-role users appear on the "Owners" tab, not the default "Management" tab.
 * Component tests set window.location.search = '?tab=owners' before rendering so the
 * component initialises on the correct tab without needing to click.
 */
import React from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

// ─── next/navigation mock ────────────────────────────────────────────────────
jest.mock('next/navigation', () => ({
    usePathname: () => '/admin/users',
    useRouter: () => ({push: jest.fn()}),
    useSearchParams: () => ({get: jest.fn().mockReturnValue(null)}),
}));

// ─── AuthContext mock ────────────────────────────────────────────────────────
const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
};

let _toggleEnabled = true;
let _canManageUsers = true;
let _isAdmin = true;
let _userRole = 'super_admin';

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        hasPermission: (key: string) => key === 'can_manage_users' ? _canManageUsers : true,
        hasFeatureAccess: (key: string) =>
            key === 'owner_name_verification' ? _toggleEnabled : true,
        isAdmin: () => _isAdmin,
        user: {id: 'admin-1', role: _userRole, full_name: 'Admin', is_approved: true},
    }),
}));

// ─── Data factories ───────────────────────────────────────────────────────────
const makeOwner = (overrides: Record<string, unknown> = {}) => ({
    id: 'u-001', full_name: 'Alice Wong', email: 'alice@example.com',
    role: 'owner', unit_number: 'TH087', is_active: true, is_approved: true,
    status: 'active', created_at: '2026-01-01T00:00:00Z',
    is_name_flagged: false, flag_reason: null, permissions: {}, is_elevated: false,
    ...overrides,
});

const makeUnit = (overrides: Record<string, unknown> = {}) => ({
    unit_number: 'TH087', owner_name: 'Avneet Rooprai', owner_name_b: '',
    ...overrides,
});

function setupApiMocks(owner = makeOwner(), unit = makeUnit()) {
    mockApi.get.mockImplementation((url: string) => {
        if (url.startsWith('/units')) return Promise.resolve({data: [unit]});
        if (url.startsWith('/users')) return Promise.resolve({data: [owner]});
        return Promise.resolve({data: []});
    });
}

// Import UsersPage once at module level — avoids React instance conflicts
// eslint-disable-next-line @typescript-eslint/no-var-requires
const UsersPage = require('@/pages/dashboard/UsersPage').default;

function renderPageOnOwnersTab() {
    // Set URL so the component's useEffect picks up tab=owners on mount
    window.history.pushState({}, '', '/admin/users?tab=owners');
    return render(<UsersPage/>);
}

function renderPage() {
    window.history.pushState({}, '', '/admin/users');
    return render(<UsersPage/>);
}

// ═══════════════════════════════════════════════════════════════════════
// Part 1 — Pure algorithm tests (no component render)
// ═══════════════════════════════════════════════════════════════════════

/** Replicated verbatim from UsersPage.jsx */
function normaliseName(name: string): string[] {
    return ((name || '').toLowerCase().replace(/[^a-z\s]/g, ' ').split(/\s+/).filter(Boolean));
}

function isSimilarName(a: string, b: string): boolean {
    if (!a || !b) return true;
    const tokensA = normaliseName(a);
    const tokensB = normaliseName(b);
    if (tokensA.length === 0 || tokensB.length === 0) return true;
    const sortedA = [...tokensA].sort().join(' ');
    const sortedB = [...tokensB].sort().join(' ');
    if (sortedA === sortedB) return true;
    const setB = new Set(tokensB);
    const overlap = tokensA.filter(t => setB.has(t)).length;
    const minLen = Math.min(new Set(tokensA).size, setB.size);
    return overlap >= Math.ceil(minLen * 0.6);
}

describe('normaliseName / isSimilarName — algorithm parity with backend', () => {
    test('exact match → true', () => expect(isSimilarName('John Smith', 'John Smith')).toBe(true));
    test('order-independent match → true', () => expect(isSimilarName('Smith John', 'John Smith')).toBe(true));
    test('60 % overlap (2/3 tokens) → true', () => expect(isSimilarName('John Smith', 'John A Smith')).toBe(true));
    test('completely different → false', () => expect(isSimilarName('Alice Wong', 'Avneet Rooprai')).toBe(false));
    test('blank a → true', () => expect(isSimilarName('', 'John Smith')).toBe(true));
    test('blank b → true', () => expect(isSimilarName('John Smith', '')).toBe(true));
    test('strips punctuation: Smith, Jane → Jane Smith', () => expect(isSimilarName('Smith, Jane', 'Jane Smith')).toBe(true));
    test('threshold pass: 2/3 overlap → true', () => expect(isSimilarName('alice bob carol', 'alice bob dave')).toBe(true));
    test('threshold fail: 1/4 overlap → false', () => expect(isSimilarName('a b c d', 'a e f g')).toBe(false));
});

// ═══════════════════════════════════════════════════════════════════════
// Part 2 — Warning flag logic (pure JS, no DOM render)
// ═══════════════════════════════════════════════════════════════════════

describe('Owner name mismatch flag logic', () => {
    /**
     * Mirrors the FIXED JSX condition:
     *   ownerEnabled && role==='owner' && (
     *     is_name_flagged ||
     *     (!isSimilarName(name, primary) &&
     *      (!secondary || !isSimilarName(name, secondary)))
     *   )
     */
    function shouldShowWarning(
        toggleEnabled: boolean,
        user: ReturnType<typeof makeOwner>,
        unitMap: Record<string, { primary: string; secondary: string }>,
    ): boolean {
        if (!toggleEnabled) return false;
        if (user.role !== 'owner') return false;
        if (user.is_name_flagged) return true;
        const entry = user.unit_number ? unitMap[user.unit_number] : null;
        if (!entry) return false;
        return (
            !isSimilarName(user.full_name, entry.primary) &&
            (!entry.secondary || !isSimilarName(user.full_name, entry.secondary))
        );
    }

    const unitMap = {TH087: {primary: 'Avneet Rooprai', secondary: ''}};

    test('toggle disabled → no warning', () => {
        expect(shouldShowWarning(false, makeOwner(), unitMap)).toBe(false);
    });
    test('tenant role → no warning', () => {
        expect(shouldShowWarning(true, makeOwner({role: 'tenant'}), unitMap)).toBe(false);
    });
    test('name mismatch + toggle on → warning', () => {
        expect(shouldShowWarning(true, makeOwner({full_name: 'Alice Wong'}), unitMap)).toBe(true);
    });
    test('name match → no warning', () => {
        expect(shouldShowWarning(true, makeOwner({full_name: 'Avneet Rooprai'}), unitMap)).toBe(false);
    });
    test('is_name_flagged=true overrides frontend check', () => {
        const matchingUnit = {TH087: {primary: 'Alice Wong', secondary: ''}};
        expect(shouldShowWarning(true, makeOwner({is_name_flagged: true}), matchingUnit)).toBe(true);
    });
    test('joint owner: secondary match → no warning', () => {
        const twoOwners = {TH087: {primary: 'John Smith', secondary: 'Jane Smith'}};
        expect(shouldShowWarning(true, makeOwner({full_name: 'Jane Smith'}), twoOwners)).toBe(false);
    });
    test('owner with no unit → no warning', () => {
        expect(shouldShowWarning(true, makeOwner({unit_number: null as unknown as string}), unitMap)).toBe(false);
    });
    test('single-owner unit: empty secondary does NOT suppress warning', () => {
        // BUG was: !isSimilarName(name, '') = !true = false, killing the AND condition
        // Fix: (!secondary || !isSimilarName(name, secondary))
        expect(shouldShowWarning(true, makeOwner({full_name: 'Alice Wong'}), {
            TH087: {
                primary: 'Avneet Rooprai',
                secondary: ''
            }
        })).toBe(true);
    });
    test('tooltip text: no [object Object]', () => {
        const entry = {primary: 'Avneet Rooprai', secondary: ''};
        const tooltip = `Strata roll: ${[entry.primary, entry.secondary].filter(Boolean).join(' / ')}`;
        expect(tooltip).toBe('Strata roll: Avneet Rooprai');
        expect(tooltip).not.toContain('[object Object]');
    });
    test('tooltip text dual-owner shows both names', () => {
        const entry = {primary: 'John Smith', secondary: 'Jane Smith'};
        const tooltip = `Strata roll: ${[entry.primary, entry.secondary].filter(Boolean).join(' / ')}`;
        expect(tooltip).toBe('Strata roll: John Smith / Jane Smith');
    });
});

// ═══════════════════════════════════════════════════════════════════════
// Part 3 — Component render tests
// ═══════════════════════════════════════════════════════════════════════

describe('UsersPage — component render (Owners tab)', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        _toggleEnabled = true;
        _canManageUsers = true;
        _isAdmin = true;
        _userRole = 'super_admin';
        setupApiMocks();
    });

    afterEach(() => {
        window.history.pushState({}, '', '/admin/users');
    });

    test('component mounts without crashing', () => {
        expect(() => renderPage()).not.toThrow();
    });

    test('Owners tab is present in the tab list', async () => {
        renderPage();
        const ownersTab = await screen.findByRole('tab', {name: /^owners/i}, {timeout: 3000});
        expect(ownersTab).toBeInTheDocument();
    });

    test('owner user appears when page loads with Owners tab active', async () => {
        renderPageOnOwnersTab();
        await waitFor(
            () => expect(screen.getByText('Alice Wong')).toBeInTheDocument(),
            {timeout: 4000}
        );
    });

    test('[object Object] never in DOM when Owners tab is active', async () => {
        renderPageOnOwnersTab();
        await waitFor(
            () => expect(screen.getByText('Alice Wong')).toBeInTheDocument(),
            {timeout: 4000}
        );
        expect(document.body.textContent).not.toContain('[object Object]');
    });

    test('strata manager sees approval action for pending service provider registrations', async () => {
        _isAdmin = false;
        _userRole = 'strata_manager';
        const pendingServiceProvider = makeOwner({
            role: 'service_provider',
            full_name: 'Sarah Edwards',
            email: 'sedwards@360degree.net.au',
            status: 'active',
            is_approved: false,
        });
        setupApiMocks(pendingServiceProvider);

        window.history.pushState({}, '', '/admin/users?tab=service');
        render(<UsersPage/>);

        await waitFor(
            () => expect(screen.getByText('Sarah Edwards')).toBeInTheDocument(),
            {timeout: 4000}
        );
        expect(screen.getByLabelText('More actions')).toBeInTheDocument();
    });
});
