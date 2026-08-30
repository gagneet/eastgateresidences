/**
 * GAP-FT-005 / GAP-FT-006: FeatureGuard coverage for requests and pet-register pages
 *
 * Before the fix, both pages were bare wrappers with zero protection:
 *   - dashboard/requests/page.tsx  → GAP-FT-005
 *   - dashboard/pet-register/page.tsx → GAP-FT-006
 *
 * Any authenticated user who navigated directly to the URL could access all
 * content regardless of role or feature toggle state.
 *
 * After the fix both pages are wrapped in <FeatureGuard featureKey="...">
 * Tests verify:
 *   1. When the feature toggle is OFF → renders unavailable message, NOT page content
 *   2. When the feature toggle is ON  → renders page content
 *   3. While auth is loading          → renders spinner, NOT page content
 *   4. featureKey passed to FeatureGuard is the correct toggle key
 *
 * These tests mock out the heavy page components (they have their own tests)
 * and focus solely on the guard wrapper behaviour.
 */
import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';

// ── Shared auth mock state ─────────────────────────────────────────────────
const authState = {
    hasFeatureAccess: jest.fn(() => false),
    loading: false,
    selectedBuilding: {id: '13195', name: 'East Gate'},
};

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
}));

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => authState,
}));

// Stub UI primitives used inside FeatureGuard
jest.mock('@/components/ui/card', () => ({
    Card: ({children}: {children: React.ReactNode}) => <div>{children}</div>,
    CardContent: ({children}: {children: React.ReactNode}) => <div>{children}</div>,
}));

jest.mock('@/components/ui/button', () => ({
    Button: ({children, ...props}: {children: React.ReactNode}) => (
        <button {...props}>{children}</button>
    ),
}));

// Stub the heavy page components — they have their own tests
jest.mock('@/pages/dashboard/RequestsPage', () => () => (
    <div data-testid="requests-page-content">Requests Page Content</div>
));

jest.mock('@/pages/dashboard/PetRegisterPage', () => () => (
    <div data-testid="pet-register-page-content">Pet Register Page Content</div>
));

// Import FeatureGuard directly so we can also test the key passed
import FeatureGuard from '@/components/layout/FeatureGuard';

// ── Helper: render a page with FeatureGuard ────────────────────────────────
function renderWithGuard(featureKey: string, pageContent: React.ReactNode) {
    return render(
        <FeatureGuard featureKey={featureKey}>
            {pageContent}
        </FeatureGuard>
    );
}

// ══════════════════════════════════════════════════════════════════════════════
// GAP-FT-005: dashboard/requests/page.tsx
// ══════════════════════════════════════════════════════════════════════════════

describe('GAP-FT-005: requests page — FeatureGuard wraps with featureKey="requests"', () => {
    beforeEach(() => {
        authState.hasFeatureAccess = jest.fn(() => false);
        authState.loading = false;
        authState.selectedBuilding = {id: '13195', name: 'East Gate'};
    });

    it('hides requests page content when "requests" toggle is disabled', () => {
        authState.hasFeatureAccess = jest.fn(() => false);

        renderWithGuard(
            'requests',
            <div data-testid="requests-page-content">Requests Page Content</div>,
        );

        expect(screen.queryByTestId('requests-page-content')).not.toBeInTheDocument();
        expect(screen.getByText(/Feature not available for East Gate/i)).toBeInTheDocument();
    });

    it('shows requests page content when "requests" toggle is enabled', () => {
        authState.hasFeatureAccess = jest.fn(() => true);

        renderWithGuard(
            'requests',
            <div data-testid="requests-page-content">Requests Page Content</div>,
        );

        expect(screen.getByTestId('requests-page-content')).toBeInTheDocument();
        expect(screen.queryByText(/Feature not available/i)).not.toBeInTheDocument();
    });

    it('shows spinner while auth is loading', () => {
        authState.loading = true;

        renderWithGuard(
            'requests',
            <div data-testid="requests-page-content">Requests Page Content</div>,
        );

        expect(document.querySelector('.animate-spin')).not.toBeNull();
        expect(screen.queryByTestId('requests-page-content')).not.toBeInTheDocument();
    });

    it('uses the correct feature key "requests" (not another key)', () => {
        // hasFeatureAccess is called with the featureKey from the guard.
        // We verify it is called with "requests", not e.g. "smart_requests".
        authState.hasFeatureAccess = jest.fn(() => true);

        renderWithGuard(
            'requests',
            <div data-testid="requests-page-content">Content</div>,
        );

        expect(authState.hasFeatureAccess).toHaveBeenCalledWith('requests');
    });

    it('uses a custom fallback when provided', () => {
        authState.hasFeatureAccess = jest.fn(() => false);

        render(
            <FeatureGuard
                featureKey="requests"
                fallback={<div data-testid="custom-fallback">Requests not enabled</div>}
            >
                <div data-testid="requests-page-content">Content</div>
            </FeatureGuard>
        );

        expect(screen.getByTestId('custom-fallback')).toBeInTheDocument();
        expect(screen.queryByTestId('requests-page-content')).not.toBeInTheDocument();
    });

    it('shows building name in unavailable message', () => {
        authState.hasFeatureAccess = jest.fn(() => false);
        authState.selectedBuilding = {id: '16244', name: 'Sierra Residences'};

        renderWithGuard(
            'requests',
            <div>Content</div>,
        );

        expect(screen.getByText(/Feature not available for Sierra Residences/i)).toBeInTheDocument();
    });
});

// ══════════════════════════════════════════════════════════════════════════════
// GAP-FT-006: dashboard/pet-register/page.tsx
// ══════════════════════════════════════════════════════════════════════════════

describe('GAP-FT-006: pet-register page — FeatureGuard wraps with featureKey="pet_register"', () => {
    beforeEach(() => {
        authState.hasFeatureAccess = jest.fn(() => false);
        authState.loading = false;
        authState.selectedBuilding = {id: '13195', name: 'East Gate'};
    });

    it('hides pet register content when "pet_register" toggle is disabled', () => {
        authState.hasFeatureAccess = jest.fn(() => false);

        renderWithGuard(
            'pet_register',
            <div data-testid="pet-register-page-content">Pet Register Page Content</div>,
        );

        expect(screen.queryByTestId('pet-register-page-content')).not.toBeInTheDocument();
        expect(screen.getByText(/Feature not available for East Gate/i)).toBeInTheDocument();
    });

    it('shows pet register content when "pet_register" toggle is enabled', () => {
        authState.hasFeatureAccess = jest.fn(() => true);

        renderWithGuard(
            'pet_register',
            <div data-testid="pet-register-page-content">Pet Register Page Content</div>,
        );

        expect(screen.getByTestId('pet-register-page-content')).toBeInTheDocument();
        expect(screen.queryByText(/Feature not available/i)).not.toBeInTheDocument();
    });

    it('shows spinner while auth is loading for pet register', () => {
        authState.loading = true;

        renderWithGuard(
            'pet_register',
            <div data-testid="pet-register-page-content">Content</div>,
        );

        expect(document.querySelector('.animate-spin')).not.toBeNull();
        expect(screen.queryByTestId('pet-register-page-content')).not.toBeInTheDocument();
    });

    it('uses the correct feature key "pet_register" (not "pets" or "pet-register")', () => {
        authState.hasFeatureAccess = jest.fn(() => true);

        renderWithGuard(
            'pet_register',
            <div data-testid="pet-register-page-content">Content</div>,
        );

        expect(authState.hasFeatureAccess).toHaveBeenCalledWith('pet_register');
    });

    it('different buildings can have different pet_register toggle states', () => {
        // Building A: toggle off
        authState.hasFeatureAccess = jest.fn(() => false);
        authState.selectedBuilding = {id: '13195', name: 'East Gate'};

        const {unmount} = renderWithGuard(
            'pet_register',
            <div data-testid="pet-register-page-content">Content</div>,
        );
        expect(screen.queryByTestId('pet-register-page-content')).not.toBeInTheDocument();
        unmount();

        // Building B: toggle on
        authState.hasFeatureAccess = jest.fn(() => true);
        authState.selectedBuilding = {id: '16244', name: 'Sierra'};

        renderWithGuard(
            'pet_register',
            <div data-testid="pet-register-page-content">Content</div>,
        );
        expect(screen.getByTestId('pet-register-page-content')).toBeInTheDocument();
    });
});

// ══════════════════════════════════════════════════════════════════════════════
// Regression guard: both pages must use FeatureGuard (import verification)
// ══════════════════════════════════════════════════════════════════════════════

describe('Page wrapper source — FeatureGuard import present', () => {
    it('requests/page.tsx contains FeatureGuard with featureKey="requests"', () => {
        // Read the actual page file and verify it uses FeatureGuard with the right key.
        // This is a smoke test that catches accidental removal of the guard.
        const fs = require('fs');
        const path = require('path');
        // __dirname = tests/frontend/unit/pages/
        // page is at frontend/src/app/(app)/requests/page.tsx
        const pageFile = path.resolve(
            __dirname,
            '../../../../frontend/src/app/(app)/requests/page.tsx',
        );
        const content = fs.readFileSync(pageFile, 'utf-8');

        expect(content).toContain('FeatureGuard');
        expect(content).toContain('featureKey="requests"');
    });

    it('pet-register/page.tsx contains FeatureGuard with featureKey="pet_register"', () => {
        const fs = require('fs');
        const path = require('path');
        const pageFile = path.resolve(
            __dirname,
            '../../../../frontend/src/app/(app)/community/pet-register/page.tsx',
        );
        const content = fs.readFileSync(pageFile, 'utf-8');

        expect(content).toContain('FeatureGuard');
        expect(content).toContain('featureKey="pet_register"');
    });
});
