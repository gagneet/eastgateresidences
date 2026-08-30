// @featuretrace:owner-transfers — Frontend tests for imported owner drift review behavior.
// Layer: test
// Data flow: Jest -> OwnerTransfersPage -> mocked /owner-transfers API calls (building-scoped).
// Related: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx

import React from 'react';
import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import '@testing-library/jest-dom';
import OwnerTransfersPage from '@/pages/dashboard/admin/OwnerTransfersPage';

const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    put: jest.fn(),
};

const mockAuth = {
    api: mockApi,
    user: {id: 'manager-1', role: 'strata_manager', full_name: 'Strata Manager'},
    hasPermission: jest.fn(() => true),
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuth,
}));

jest.mock('sonner', () => ({
    toast: {
        success: jest.fn(),
        error: jest.fn(),
    },
}));

const importedTransfer = {
    id: 'transfer-th078',
    building_id: '13195',
    unit_number: 'TH078',
    status: 'pending',
    requested_date: '2026-07-02T00:00:00+00:00',
    submitted_by_id: 'system:imported_owner_snapshot_backfill',
    submitted_by_name: 'Imported ledger data',
    old_owners: [
        {user_id: 'olivia', full_name: 'Olivia Rollings', email: 'olivia@example.com'},
        {user_id: 'mark', full_name: 'Mark Raets', email: 'mark.raets@example.com'},
    ],
    new_owner: {
        user_id: 'contact-tavis',
        full_name: 'Tavis Christian Hamer',
        email: 'owner-transfer+contact-tavis@strataos.local',
        is_internal_contact_email: true,
    },
    current_approvals: 0,
    required_approvals: 1,
    approval_history: [],
    suggested_remove_owner_ids: ['olivia'],
    request_notes: 'Automatically created from imported owner-name drift.',
    ownership_documents: [],
};

function mockInitialApi({role = 'strata_manager'} = {}) {
    mockAuth.user = {id: 'manager-1', role, full_name: 'Reviewer'};
    mockAuth.hasPermission.mockReturnValue(true);
    mockApi.get.mockImplementation((url) => {
        if (url === '/owner-transfers/form-options') {
            return Promise.resolve({
                data: {
                    can_create: true,
                    can_review: true,
                    can_view_history: true,
                    accessible_units: [
                        {
                            unit_number: 'TH078',
                            display_name: 'Unit TH078',
                            owner_names: ['Tavis Christian Hamer', 'Mark Raets'],
                        },
                    ],
                },
            });
        }
        if (url === '/owner-transfers') {
            return Promise.resolve({data: [importedTransfer]});
        }
        if (url === '/owner-transfers/history') {
            return Promise.resolve({data: []});
        }
        return Promise.resolve({data: []});
    });
}

describe('OwnerTransfersPage imported owner drift', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockInitialApi();
        mockApi.put.mockResolvedValue({data: {status: 'approved'}});
    });

    it('renders imported owner drift without exposing generated internal contact emails', async () => {
        render(<OwnerTransfersPage/>);

        expect(await screen.findByText('Unit TH078')).toBeInTheDocument();
        expect(screen.getByText('Tavis Christian Hamer')).toBeInTheDocument();
        expect(screen.getByText('Contact email pending')).toBeInTheDocument();
        expect(screen.queryByText(/owner-transfer\+contact-tavis@strataos\.local/i)).not.toBeInTheDocument();
    });

    it('preselects the imported drift owner suggested for removal', async () => {
        render(<OwnerTransfersPage/>);

        fireEvent.click(await screen.findByRole('button', {name: /approve and remove existing owners/i}));

        const dialog = await screen.findByRole('dialog');
        const oliviaRow = within(dialog).getByText('Olivia Rollings').closest('div.rounded');
        const markRow = within(dialog).getByText('Mark Raets').closest('div.rounded');

        expect(within(oliviaRow).getByRole('checkbox')).toBeChecked();
        expect(within(markRow).getByRole('checkbox')).not.toBeChecked();

        fireEvent.click(within(dialog).getByRole('button', {name: /submit approval/i}));

        await waitFor(() => {
            expect(mockApi.put).toHaveBeenCalledWith('/owner-transfers/transfer-th078', {
                action: 'approve_remove_old',
                review_notes: null,
                remove_owner_ids: ['olivia'],
            });
        });
    });

    it('allows chairman reviewers (role=ec_member, ec_position=CHAIRMAN) to process imported owner transfers', async () => {
        // 'chairman' is never a top-level user.role value in this codebase — a chairman is a user
        // with role 'ec_member' and ec_position 'CHAIRMAN' (see rules/post-compact-critical.md).
        mockInitialApi({role: 'ec_member'});
        mockAuth.user = {...mockAuth.user, ec_position: 'CHAIRMAN'};
        render(<OwnerTransfersPage/>);

        expect(await screen.findByRole('button', {name: /approve and keep existing owners/i})).toBeInTheDocument();
        expect(screen.getByRole('button', {name: /approve and remove existing owners/i})).toBeInTheDocument();
    });

    it('never grants reviewer access via a literal "chairman" role string (regression guard)', async () => {
        // Backend never issues role: 'chairman'. If REVIEWER_ROLES/EC_ROLES in OwnerTransfersPage.jsx
        // ever regain the literal string 'chairman', this test still passes vacuously for that array
        // membership, but the assertion below on formOptions.can_review=false forces the fallback
        // array path (REVIEWER_ROLES.includes(user?.role)) to run — with a real, impossible-in-prod
        // role value, reviewer actions must NOT be granted.
        mockAuth.user = {id: 'imposter-1', role: 'chairman', full_name: 'Impossible Role'};
        mockAuth.hasPermission.mockReturnValue(true);
        mockApi.get.mockImplementation((url) => {
            if (url === '/owner-transfers/form-options') {
                return Promise.resolve({
                    data: {can_create: false, can_review: false, can_view_history: false, accessible_units: []},
                });
            }
            if (url === '/owner-transfers') {
                return Promise.resolve({data: [importedTransfer]});
            }
            if (url === '/owner-transfers/history') {
                return Promise.resolve({data: []});
            }
            return Promise.resolve({data: []});
        });

        render(<OwnerTransfersPage/>);

        expect(await screen.findByText('Unit TH078')).toBeInTheDocument();
        expect(screen.queryByRole('button', {name: /approve and keep existing owners/i})).not.toBeInTheDocument();
        expect(screen.queryByRole('button', {name: /approve and remove existing owners/i})).not.toBeInTheDocument();
    });
});
