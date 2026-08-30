import React from 'react';
import {render, screen, waitFor, fireEvent} from '@testing-library/react';
import '@testing-library/jest-dom';
import ReconciliationPage from '@/pages/dashboard/financial/ReconciliationPage';

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn(),
}));

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const mockUseAuth = require('@/contexts/AuthContext').useAuth;
const {toast} = require('sonner');

const RECONCILIATION_RESPONSE = {
    building_id: '13195',
    financial_year: '2026',
    portal_snapshot_date: '2026-04-15',
    units: [
        {
            unit_number: 'TH078',
            portal_balance_cents: 55000,
            portal_snapshot_date: '2026-04-15',
            ledger_balance_cents: 0,
            delta_cents: 55000,
            requires_reconciliation: true,
            candidates: [
                {
                    queue_item_id: 'queue-1',
                    source_type: 'synthetic_from_budget',
                    confidence: 'high',
                    provenance_class: 'reconstruction',
                    evidence_type: 'budget_uoe_reconstruction',
                    amount_cents: 55000,
                    requires_review: false,
                    match_status: 'pending',
                    receipt_id: null,
                },
            ],
            ledger_result: null,
        },
        {
            unit_number: '1',
            portal_balance_cents: 0,
            portal_snapshot_date: '2026-04-15',
            ledger_balance_cents: 0,
            delta_cents: 0,
            requires_reconciliation: false,
            candidates: [],
            ledger_result: null,
        },
    ],
};

function mockAuth(overrides: Partial<Record<string, any>> = {}) {
    const api = {
        get: jest.fn().mockResolvedValue({data: RECONCILIATION_RESPONSE}),
        post: jest.fn().mockResolvedValue({data: {}}),
    };
    mockUseAuth.mockReturnValue({
        api,
        user: {role: 'strata_manager'},
        loading: false,
        ...overrides,
    });
    return api;
}

describe('ReconciliationPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('renders ledger and portal balance columns per unit, distinct from operational balances', async () => {
        mockAuth();
        render(<ReconciliationPage/>);

        await waitFor(() => expect(screen.getByTestId('unit-row-TH078')).toBeInTheDocument());

        expect(screen.getByTestId('portal-balance-TH078')).toHaveTextContent('$550.00');
        expect(screen.getByTestId('ledger-balance-TH078')).toHaveTextContent('$0.00');
    });

    it('shows a pending candidate as not yet affecting the ledger result', async () => {
        mockAuth();
        render(<ReconciliationPage/>);

        await waitFor(() => expect(screen.getByTestId('unit-row-TH078')).toBeInTheDocument());

        // Requires-reconciliation units auto-expand.
        expect(screen.getByTestId(/candidate-row-TH078/)).toBeInTheDocument();
        expect(screen.getByText('pending')).toBeInTheDocument();
    });

    it('shows a "Reconstructed / Estimated" badge for provenance_class=reconstruction candidates', async () => {
        // GAP-FIN-024: this candidate came from the historical-reconstruction pipeline
        // (synthetic, not a bank-verified transaction) — must be visibly flagged, not shown
        // as plain muted text indistinguishable from a real match.
        mockAuth();
        render(<ReconciliationPage/>);

        await waitFor(() => expect(screen.getByTestId('unit-row-TH078')).toBeInTheDocument());
        expect(screen.getByText('Reconstructed / Estimated')).toBeInTheDocument();
    });

    it('calls the promote-high-confidence endpoint when Promote is clicked', async () => {
        const api = mockAuth();
        render(<ReconciliationPage/>);

        await waitFor(() => expect(screen.getByTestId('btn-promote-queue-1')).toBeInTheDocument());
        fireEvent.click(screen.getByTestId('btn-promote-queue-1'));

        await waitFor(() =>
            expect(api.post).toHaveBeenCalledWith(
                '/financial/matching/queue/queue-1/promote-high-confidence'
            )
        );
        expect(toast.success).toHaveBeenCalled();
    });

    it('calls the decide reject endpoint when Reject is clicked', async () => {
        const api = mockAuth();
        render(<ReconciliationPage/>);

        await waitFor(() => expect(screen.getByTestId('btn-reject-queue-1')).toBeInTheDocument());
        fireEvent.click(screen.getByTestId('btn-reject-queue-1'));

        await waitFor(() =>
            expect(api.post).toHaveBeenCalledWith(
                '/financial/matching/queue/queue-1/decide',
                {action: 'reject'}
            )
        );
    });

    it('redirects non-managers away from the page', async () => {
        const {mockReplace} = require('next/navigation');
        mockAuth({user: {role: 'owner'}});

        const {container} = render(<ReconciliationPage/>);
        // Non-managers see nothing rendered (guard returns null before data fetch).
        expect(container.textContent).not.toContain('Finance Reconciliation');
        await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/dashboard'));
    });

    it('allows a temporarily-elevated user via effective_role even when raw role is owner', async () => {
        // Regression: AuthContext.isManager() only checks the raw `role` field, which
        // would blank-screen a temp-elevated user even though the backend's
        // _DECIDE_ROLES check (effective_role || role) would authorise them.
        mockAuth({user: {role: 'owner', effective_role: 'ec_member'}});

        render(<ReconciliationPage/>);

        await waitFor(() => expect(screen.getByText('Finance Reconciliation')).toBeInTheDocument());
    });
});
