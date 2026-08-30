// @featuretrace:by-law-breach-register — Guards the register page's honest empty/unknown states.
// Layer: test
// Data flow: mocked /by-law-breach/reports -> ByLawBreachPage (building-scoped).
// Related: frontend/src/pages/dashboard/ByLawBreachPage.tsx
/**
 * The by-law breach register page.
 *
 * The register's API has existed since GAP-OPS-005 but had no UI, so nothing could ever
 * be recorded and Building Pulse reported the dispute signal as permanently unavailable.
 * These tests pin the behaviours that keep it honest once data exists.
 */
import React from 'react';
import {render, screen, fireEvent, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

import ByLawBreachPage from '@/pages/dashboard/ByLawBreachPage';

const mockGet = jest.fn();
const mockPost = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: {get: (...a: any[]) => mockGet(...a), post: (...a: any[]) => mockPost(...a)},
        user: {role: 'strata_manager'},
    }),
}));

const HELP = {
    summary: 'Terms are combined with AND.',
    examples: [{query: 'status:escalated', means: 'escalated matters only'}],
    operators: [{op: ':', means: 'equals'}],
    fields: ['status', 'unit', 'severity'],
};

const REPORTS = [
    {id: 'r1', alleged_unit: 'TH074', status: 'escalated', severity: 'major',
     description: 'Parking in visitor bay', created_at: '2026-08-01T00:00:00Z', by_law_section: '12'},
    {id: 'r2', alleged_unit: 'UA042', status: 'resolved', severity: 'minor',
     description: 'Noise after hours', created_at: '2026-07-01T00:00:00Z'},
    {id: 'r3', alleged_unit: 'UA050', status: 'tribunal_referred', severity: 'major',
     description: 'Unauthorised works', created_at: '2026-06-01T00:00:00Z'},
];

function mockApi({reports = REPORTS, unknown = ''} = {}) {
    mockGet.mockImplementation((url: string) => {
        if (url.includes('search-help')) return Promise.resolve({data: HELP});
        return Promise.resolve({
            data: reports,
            headers: unknown ? {'x-search-unknown-fields': unknown} : {},
        });
    });
}

beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockApi();
});

describe('ByLawBreachPage', () => {
    it('lists the register', async () => {
        render(<ByLawBreachPage/>);
        expect(await screen.findByText('TH074')).toBeInTheDocument();
        expect(screen.getByText('UA042')).toBeInTheDocument();
    });

    it('counts a tribunal referral as unresolved', async () => {
        render(<ByLawBreachPage/>);
        await screen.findByText('TH074');
        // r1 escalated + r3 tribunal_referred = 2. Counting with BreachStatus.OPEN would
        // report 1, hiding the most serious matter on the register.
        expect(screen.getByTestId('breach-tile-unresolved')).toHaveTextContent('2');
        expect(screen.getByTestId('breach-tile-tribunal')).toHaveTextContent('1');
    });

    it('explains the empty state instead of implying a clean building', async () => {
        mockApi({reports: []});
        render(<ByLawBreachPage/>);
        const empty = await screen.findByTestId('breach-empty');
        expect(empty.textContent).toMatch(/unavailable rather than assuming there are none/i);
    });

    it('warns when a searched field is not recognised', async () => {
        mockApi({unknown: 'adress'});
        render(<ByLawBreachPage/>);
        const warn = await screen.findByTestId('breach-search-unknown');
        // A typo that silently matches everything reads as "no filter applied".
        expect(warn.textContent).toMatch(/adress/);
        expect(warn.textContent).toMatch(/wider than you asked for/i);
    });

    it('does not warn when every field is recognised', async () => {
        render(<ByLawBreachPage/>);
        await screen.findByText('TH074');
        expect(screen.queryByTestId('breach-search-unknown')).not.toBeInTheDocument();
    });

    it('sends the search to the backend rather than filtering locally', async () => {
        render(<ByLawBreachPage/>);
        await screen.findByText('TH074');
        fireEvent.change(screen.getByTestId('breach-search-input'), {target: {value: 'status:escalated'}});
        fireEvent.click(screen.getByTestId('breach-search-submit'));
        await waitFor(() => {
            expect(mockGet).toHaveBeenCalledWith('/by-law-breach/reports',
                {params: {search: 'status:escalated'}});
        });
    });

    it('renders the help panel from the API, not from a local copy', async () => {
        render(<ByLawBreachPage/>);
        await screen.findByText('TH074');
        fireEvent.click(screen.getByTestId('breach-search-help-toggle'));
        const help = await screen.findByTestId('breach-search-help');
        expect(help).toHaveTextContent('Terms are combined with AND.');
        expect(help).toHaveTextContent('escalated matters only');
    });

    it('applies a summary tile as a search so the count and the list agree', async () => {
        render(<ByLawBreachPage/>);
        await screen.findByText('TH074');
        fireEvent.click(screen.getByTestId('breach-tile-tribunal'));
        await waitFor(() => {
            expect(mockGet).toHaveBeenCalledWith('/by-law-breach/reports',
                {params: {search: 'status:tribunal_referred'}});
        });
    });

    it('opens a row for the full detail', async () => {
        render(<ByLawBreachPage/>);
        fireEvent.click(await screen.findByTestId('breach-row-r1'));
        expect(await screen.findByTestId('breach-detail')).toHaveTextContent('Parking in visitor bay');
    });

    it('requires a tribunal target only when escalating', async () => {
        // r2, not r1: the status select omits the report's CURRENT status, and r1 is
        // already escalated — so "escalated" is deliberately not offered for it.
        render(<ByLawBreachPage/>);
        fireEvent.click(await screen.findByTestId('breach-row-r2'));
        await screen.findByTestId('breach-detail');
        expect(screen.queryByTestId('breach-tribunal-select')).not.toBeInTheDocument();
        fireEvent.change(screen.getByTestId('breach-status-select'), {target: {value: 'escalated'}});
        expect(screen.getByTestId('breach-tribunal-select')).toBeInTheDocument();
    });

    it('submits a status change to the evidence trail', async () => {
        mockPost.mockResolvedValue({data: {}});
        render(<ByLawBreachPage/>);
        fireEvent.click(await screen.findByTestId('breach-row-r1'));
        await screen.findByTestId('breach-detail');
        fireEvent.change(screen.getByTestId('breach-status-select'), {target: {value: 'resolved'}});
        fireEvent.change(screen.getByTestId('breach-status-notes'), {target: {value: 'Owner complied'}});
        fireEvent.click(screen.getByTestId('breach-status-submit'));
        await waitFor(() => {
            expect(mockPost).toHaveBeenCalledWith('/by-law-breach/reports/r1/status',
                expect.objectContaining({new_status: 'resolved', notes: 'Owner complied'}));
        });
    });

    it('will not submit a new report without a unit and a description', async () => {
        render(<ByLawBreachPage/>);
        fireEvent.click(await screen.findByTestId('breach-report-new'));
        const submit = await screen.findByTestId('breach-create-submit');
        expect(submit).toBeDisabled();
        fireEvent.change(screen.getByTestId('breach-create-unit'), {target: {value: 'UA013'}});
        expect(submit).toBeDisabled();
        fireEvent.change(screen.getByTestId('breach-create-description'), {target: {value: 'Rubbish in corridor'}});
        expect(submit).toBeEnabled();
    });

    it('creates a report', async () => {
        mockPost.mockResolvedValue({data: {}});
        render(<ByLawBreachPage/>);
        fireEvent.click(await screen.findByTestId('breach-report-new'));
        fireEvent.change(await screen.findByTestId('breach-create-unit'), {target: {value: 'UA013'}});
        fireEvent.change(screen.getByTestId('breach-create-description'), {target: {value: 'Rubbish in corridor'}});
        fireEvent.click(screen.getByTestId('breach-create-submit'));
        await waitFor(() => {
            expect(mockPost).toHaveBeenCalledWith('/by-law-breach/reports',
                expect.objectContaining({alleged_unit: 'UA013', description: 'Rubbish in corridor'}));
        });
    });

    it('surfaces a load failure instead of showing an empty register', async () => {
        mockGet.mockImplementation((url: string) => url.includes('search-help')
            ? Promise.resolve({data: HELP})
            : Promise.reject({response: {data: {error: {code: 'x', message: 'Backend unavailable'}}}}));
        render(<ByLawBreachPage/>);
        expect(await screen.findByTestId('breach-error')).toHaveTextContent('Backend unavailable');
    });
});
