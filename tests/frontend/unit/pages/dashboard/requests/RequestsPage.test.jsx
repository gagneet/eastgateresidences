import React from 'react';
import {render, screen, waitFor, fireEvent} from '@testing-library/react';
import '@testing-library/jest-dom';

import RequestsPage from '@/pages/dashboard/RequestsPage';

const mockReplace = jest.fn();
let queryString = '';
let authState;

jest.mock('next/navigation', () => ({
    useRouter: () => ({replace: mockReplace}),
    useSearchParams: () => new URLSearchParams(queryString),
}));

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => authState,
}));

jest.mock('@/pages/dashboard/requests/PetRequestTab', () => () => <div data-testid="pet-form">Pet form</div>);
jest.mock('@/pages/dashboard/requests/InsuranceClaimTab', () => () => <div>Insurance claim form</div>);
jest.mock('@/pages/dashboard/requests/InsuranceEnquiryTab', () => () => <div>Insurance enquiry form</div>);
jest.mock('@/pages/dashboard/requests/AccessControlTab', () => () => <div>Access form</div>);
jest.mock('@/pages/dashboard/requests/AlterationsTab', () => () => <div>Alterations form</div>);
jest.mock('@/pages/dashboard/requests/ReimbursementTab', () => () => <div>Reimbursement form</div>);
jest.mock('@/pages/dashboard/requests/WorkOrderTab', () => () => <div>Work orders</div>);
jest.mock('@/pages/dashboard/requests/MaintenanceRequestTab', () => () => <div>Maintenance redirect</div>);
jest.mock('@/pages/dashboard/requests/MyRequestsTab', () => () => <div>My Requests list</div>);

const catalogue = [
    {
        key: 'pet',
        version: 1,
        title: 'Pet application',
        description: 'Apply for approval to keep a pet at your unit.',
        category: 'Pets & Residents',
        search_terms: ['pet', 'dog'],
        expected_response: 'Decision timeframe depends on building rules',
        documents: 'Pet photo and supporting records may be required',
        legacy_tab_aliases: ['pet'],
        recommended: true,
    },
    {
        key: 'access-control',
        version: 1,
        title: 'Access device request',
        description: 'Request review for a building access device or replacement.',
        category: 'Keys, Fobs & Access',
        search_terms: ['fob', 'garage'],
        expected_response: 'Eligibility review before any issue or replacement',
        documents: 'Reason for request is required',
        legacy_tab_aliases: ['access-control', 'remote'],
        recommended: false,
    },
];

function setAuth({buildingId = 'TEST-REQUEST-A', role = 'owner', effectiveRole, data = catalogue} = {}) {
    authState = {
        // No isManager stub on purpose: the page must derive manager scope from
        // effective_role/role itself, exactly as the API does.
        user: {building_id: buildingId, role, effective_role: effectiveRole},
        api: {get: jest.fn().mockResolvedValue({data})},
    };
}

describe('RequestsPage catalogue behaviour', () => {
    beforeEach(() => {
        queryString = '';
        mockReplace.mockClear();
        setAuth();
    });

    it('loads a searchable, category-filtered catalogue', async () => {
        render(<RequestsPage/>);

        expect(await screen.findByText('Pet application')).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Search requests and applications'), {
            target: {value: 'garage'},
        });

        expect(screen.queryByText('Pet application')).not.toBeInTheDocument();
        expect(screen.getByText('Access device request')).toBeInTheDocument();

        fireEvent.click(screen.getByTestId('request-category-pets-&-residents'));
        expect(screen.getByText('No matching requests found')).toBeInTheDocument();
    });

    it('opens the tracking list for a bare ?status= deep link', async () => {
        // The Management dashboard's "N requests past SLA" card links here. Without
        // this, ?status= landed on the form catalogue and the filter was dropped.
        queryString = 'status=overdue';
        render(<RequestsPage/>);

        expect(await screen.findByText('My Requests list')).toBeInTheDocument();
        expect(screen.queryByTestId('request-catalogue-grid')).not.toBeInTheDocument();
    });

    it('labels the tracking view as a building queue for a manager', async () => {
        queryString = 'tab=my-requests';
        setAuth({role: 'strata_manager'});
        render(<RequestsPage/>);

        // GET /workflow-requests returns every request in the building to a manager,
        // so calling the view "My Requests" misdescribes what is on screen.
        expect(await screen.findByText('My Requests list')).toBeInTheDocument();
        expect(screen.getByTestId('my-requests-button')).toHaveTextContent('Request Queue');
        expect(screen.queryByText('My Requests')).not.toBeInTheDocument();
    });

    it('labels the tracking view as a queue for a temporarily-elevated user', async () => {
        // The API hands a raw-role "owner" with effective_role "ec_member" the
        // building-wide queue. AuthContext.isManager() reads the raw role only and
        // would have mislabelled this screen "My Requests".
        queryString = 'tab=my-requests';
        setAuth({role: 'owner', effectiveRole: 'ec_member'});
        render(<RequestsPage/>);

        expect(await screen.findByText('My Requests list')).toBeInTheDocument();
        expect(screen.getByTestId('my-requests-button')).toHaveTextContent('Request Queue');
    });

    it('keeps the "My Requests" label for a resident', async () => {
        queryString = 'tab=my-requests';
        render(<RequestsPage/>);

        expect(await screen.findByText('My Requests list')).toBeInTheDocument();
        expect(screen.getByTestId('my-requests-button')).toHaveTextContent('My Requests');
    });

    it('retains legacy tab links for authorised forms', async () => {
        queryString = 'tab=pet';
        render(<RequestsPage/>);

        expect(await screen.findByTestId('pet-form')).toBeInTheDocument();
    });

    it('prevents unauthorised direct form access', async () => {
        queryString = 'tab=remote';
        setAuth({data: [catalogue[0]]});
        render(<RequestsPage/>);

        await waitFor(() => {
            expect(mockReplace).toHaveBeenCalledWith('/requests', {scroll: false});
        });
    });

    it('refetches when building context changes', async () => {
        const {rerender} = render(<RequestsPage/>);
        await screen.findByText('Pet application');
        expect(authState.api.get).toHaveBeenCalledTimes(1);

        setAuth({buildingId: 'TEST-REQUEST-B'});
        rerender(<RequestsPage/>);

        await waitFor(() => expect(authState.api.get).toHaveBeenCalledTimes(1));
    });
});
