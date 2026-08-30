/**
 * @featuretrace:financial-mock-boundary — Frontend tests for FinancialMockModeCard.
 * Layer: test
 * Tests: role gating (incl. effective_role elevation); axios path has no /api/ double prefix;
 *        returning to mock applies immediately; going live is confirmation-gated and needs a
 *        reason; env override locks both switches; 403 renders the unavailable state.
 * Data flow: FinancialMockModeCard → GET/PUT /buildings/{building_id}/integrations/mock-mode
 *            → routers/building_integrations.py → core.feature_toggle_overrides (building-scoped).
 * Related: backend/routers/building_integrations.py
 *          backend/services/financial_mock_mode.py
 *          tests/backend/test_financial_mock_boundary.py
 * Scope: (building-scoped)
 */
import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';

import FinancialMockModeCard from '@/components/settings/FinancialMockModeCard';

// Jest hoists jest.mock above declarations; only names starting with "mock" may be
// referenced from inside the factory.
let mockAuth: any;

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuth,
}));

const mockToast = {success: jest.fn(), error: jest.fn()};
jest.mock('sonner', () => ({toast: {success: (...a: any[]) => mockToast.success(...a),
                                    error: (...a: any[]) => mockToast.error(...a)}}));

const BUILDING_ID = '13195';
const FINANCIAL_KEY = 'financial_services_mock';
const DIRECT_DEBIT_KEY = 'bank_direct_debit_mock';

/** Server response shape from GET /buildings/{id}/integrations/mock-mode. */
const stateResponse = (overrides: Partial<any> = {}) => ({
    building_id: BUILDING_ID,
    forced_by_environment: false,
    switches: [
        {
            feature_key: FINANCIAL_KEY,
            label: 'Mock financial services',
            detail: 'DEFT/BPAY, Stripe, the provider protocols and outbound payment (ABA) execution run against mocks.',
            is_mocked: true,
            forced_by_environment: false,
        },
        {
            feature_key: DIRECT_DEBIT_KEY,
            label: 'Mock bank direct debit & transaction history',
            detail: 'Direct debit and real transaction-history retrieval are mocked.',
            is_mocked: true,
            forced_by_environment: false,
        },
    ],
    ...overrides,
});

const setAuth = ({role = 'strata_manager', effectiveRole, get, put}: any = {}) => {
    mockAuth = {
        api: {
            get: get ?? jest.fn().mockResolvedValue({data: stateResponse()}),
            put: put ?? jest.fn().mockResolvedValue({data: stateResponse()}),
        },
        user: {id: 'u-1', role, ...(effectiveRole ? {effective_role: effectiveRole} : {})},
        selectedBuilding: {id: 'uuid-1', building_id: BUILDING_ID, name: 'East Gate', address: ''},
    };
    return mockAuth;
};

beforeEach(() => {
    jest.clearAllMocks();
    setAuth();
});

// ── Role gating ─────────────────────────────────────────────────────────────

describe('role gating', () => {
    it.each(['super_admin', 'strata_admin', 'strata_manager'])(
        'renders for %s — the roles the backend capability admits',
        async (role) => {
            setAuth({role});
            render(<FinancialMockModeCard/>);
            expect(await screen.findByTestId('financial-mock-mode-card')).toBeInTheDocument();
        },
    );

    it.each(['ec_member', 'owner', 'tenant', 'guest', 'service_provider'])(
        'renders nothing for %s',
        (role) => {
            setAuth({role});
            const {container} = render(<FinancialMockModeCard/>);
            expect(container).toBeEmptyDOMElement();
            // And must not have probed the endpoint on their behalf.
            expect(mockAuth.api.get).not.toHaveBeenCalled();
        },
    );

    it('honours effective_role, so an elevated user is not locked out', async () => {
        // Mirrors the backend rule: a temporarily elevated user keeps role="owner"
        // and carries effective_role. Reading the raw role would reject exactly the
        // users elevation exists to admit.
        setAuth({role: 'owner', effectiveRole: 'strata_manager'});
        render(<FinancialMockModeCard/>);
        expect(await screen.findByTestId('financial-mock-mode-card')).toBeInTheDocument();
    });

    it('ec_member is excluded even when elevated to it', () => {
        setAuth({role: 'owner', effectiveRole: 'ec_member'});
        const {container} = render(<FinancialMockModeCard/>);
        expect(container).toBeEmptyDOMElement();
    });
});

// ── Fetching ────────────────────────────────────────────────────────────────

describe('loading state', () => {
    it('requests the building-scoped path with no /api/ prefix', async () => {
        // The api instance already has baseURL ".../api"; prefixing here yields
        // /api/api/... and a 404 (documented footgun in CLAUDE.md).
        render(<FinancialMockModeCard/>);
        await waitFor(() => expect(mockAuth.api.get).toHaveBeenCalledWith(
            `/buildings/${BUILDING_ID}/integrations/mock-mode`,
        ));
    });

    it('renders both switches with their current state', async () => {
        render(<FinancialMockModeCard/>);
        expect(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`)).toBeInTheDocument();
        expect(screen.getByTestId(`toggle-${DIRECT_DEBIT_KEY}`)).toBeInTheDocument();
        expect(screen.getAllByText('Mock')).toHaveLength(2);
    });

    it('shows a Live badge for a building already switched over', async () => {
        const live = stateResponse();
        live.switches[0].is_mocked = false;
        setAuth({get: jest.fn().mockResolvedValue({data: live})});
        render(<FinancialMockModeCard/>);
        expect(await screen.findByText('Live')).toBeInTheDocument();
        expect(screen.getAllByText('Mock')).toHaveLength(1);
    });

    it('renders an unavailable state when the backend refuses (403)', async () => {
        // A manager not assigned to the selected building. The backend decides this,
        // not the client, so the component must degrade rather than assume access.
        setAuth({get: jest.fn().mockRejectedValue({response: {status: 403}})});
        render(<FinancialMockModeCard/>);
        expect(await screen.findByText(/not available for the selected building/i)).toBeInTheDocument();
        expect(screen.queryByTestId(`toggle-${FINANCIAL_KEY}`)).not.toBeInTheDocument();
    });
});

// ── Returning to mock: the safe direction ───────────────────────────────────

describe('returning to mock', () => {
    it('applies immediately with no reason required', async () => {
        const live = stateResponse();
        live.switches[0].is_mocked = false;
        const put = jest.fn().mockResolvedValue({data: stateResponse()});
        setAuth({get: jest.fn().mockResolvedValue({data: live}), put});

        render(<FinancialMockModeCard/>);
        fireEvent.click(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`));

        await waitFor(() => expect(put).toHaveBeenCalledWith(
            `/buildings/${BUILDING_ID}/integrations/mock-mode/${FINANCIAL_KEY}`,
            {is_mocked: true, reason: null},
        ));
        // No confirmation gate on the safe direction.
        expect(screen.queryByTestId(`confirm-live-${FINANCIAL_KEY}`)).not.toBeInTheDocument();
    });
});

// ── Going live: the consequential direction ─────────────────────────────────

describe('going live', () => {
    it('does not call the API until the change is confirmed', async () => {
        render(<FinancialMockModeCard/>);
        fireEvent.click(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`));

        expect(await screen.findByTestId(`confirm-live-${FINANCIAL_KEY}`)).toBeInTheDocument();
        expect(screen.getByText(/live financial providers/i)).toBeInTheDocument();
        expect(mockAuth.api.put).not.toHaveBeenCalled();
    });

    it('keeps the confirm button disabled until a reason is given', async () => {
        render(<FinancialMockModeCard/>);
        fireEvent.click(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`));

        const confirm = await screen.findByTestId(`confirm-live-${FINANCIAL_KEY}`);
        expect(confirm).toBeDisabled();

        fireEvent.change(screen.getByTestId(`reason-${FINANCIAL_KEY}`), {target: {value: '   '}});
        expect(confirm).toBeDisabled();  // whitespace is not a reason

        fireEvent.change(screen.getByTestId(`reason-${FINANCIAL_KEY}`), {target: {value: 'Bank signed off'}});
        expect(confirm).toBeEnabled();
    });

    it('sends the trimmed reason with the change', async () => {
        const put = jest.fn().mockResolvedValue({data: stateResponse()});
        setAuth({put});
        render(<FinancialMockModeCard/>);
        fireEvent.click(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`));
        fireEvent.change(screen.getByTestId(`reason-${FINANCIAL_KEY}`), {target: {value: '  Committee approved  '}});
        fireEvent.click(screen.getByTestId(`confirm-live-${FINANCIAL_KEY}`));

        await waitFor(() => expect(put).toHaveBeenCalledWith(
            `/buildings/${BUILDING_ID}/integrations/mock-mode/${FINANCIAL_KEY}`,
            {is_mocked: false, reason: 'Committee approved'},
        ));
    });

    it('cancelling closes the confirmation and calls nothing', async () => {
        render(<FinancialMockModeCard/>);
        fireEvent.click(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`));
        fireEvent.change(screen.getByTestId(`reason-${FINANCIAL_KEY}`), {target: {value: 'changed my mind'}});
        fireEvent.click(screen.getByRole('button', {name: /cancel/i}));

        await waitFor(() =>
            expect(screen.queryByTestId(`confirm-live-${FINANCIAL_KEY}`)).not.toBeInTheDocument());
        expect(mockAuth.api.put).not.toHaveBeenCalled();
    });

    it('opens the confirmation only for the switch that was clicked', async () => {
        // The two switches share one `reason` state, so a leaked confirmation would
        // let a reason typed for one be submitted against the other.
        render(<FinancialMockModeCard/>);
        fireEvent.click(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`));

        expect(screen.getByTestId(`confirm-live-${FINANCIAL_KEY}`)).toBeInTheDocument();
        expect(screen.queryByTestId(`confirm-live-${DIRECT_DEBIT_KEY}`)).not.toBeInTheDocument();
    });

    it('clears the typed reason after a successful change', async () => {
        render(<FinancialMockModeCard/>);
        fireEvent.click(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`));
        fireEvent.change(screen.getByTestId(`reason-${FINANCIAL_KEY}`), {target: {value: 'first reason'}});
        fireEvent.click(screen.getByTestId(`confirm-live-${FINANCIAL_KEY}`));
        await waitFor(() => expect(mockAuth.api.put).toHaveBeenCalled());

        // Re-opening on the other switch must not inherit the previous justification.
        fireEvent.click(screen.getByTestId(`toggle-${DIRECT_DEBIT_KEY}`));
        expect(await screen.findByTestId(`reason-${DIRECT_DEBIT_KEY}`)).toHaveValue('');
    });

    it('surfaces the backend refusal and leaves the switch untouched', async () => {
        const detail = 'MOCK_EXTERNAL_SERVICES is set process-wide, so this building cannot be switched to live providers.';
        const put = jest.fn().mockRejectedValue({response: {data: {detail}}});
        setAuth({put});

        render(<FinancialMockModeCard/>);
        fireEvent.click(await screen.findByTestId(`toggle-${FINANCIAL_KEY}`));
        fireEvent.change(screen.getByTestId(`reason-${FINANCIAL_KEY}`), {target: {value: 'try anyway'}});
        fireEvent.click(screen.getByTestId(`confirm-live-${FINANCIAL_KEY}`));

        await waitFor(() => expect(mockToast.error).toHaveBeenCalledWith(detail));
        // Still showing Mock for both — the server rejected the change.
        expect(screen.getAllByText('Mock')).toHaveLength(2);
    });
});

// ── Process-wide override ───────────────────────────────────────────────────

describe('environment override', () => {
    it('locks both switches and explains why', async () => {
        const forced = stateResponse({forced_by_environment: true});
        forced.switches.forEach((s: any) => {
            s.forced_by_environment = true;
        });
        setAuth({get: jest.fn().mockResolvedValue({data: forced})});

        render(<FinancialMockModeCard/>);
        expect(await screen.findByText(/enforced for every building/i)).toBeInTheDocument();
        expect(screen.getByTestId(`toggle-${FINANCIAL_KEY}`)).toBeDisabled();
        expect(screen.getByTestId(`toggle-${DIRECT_DEBIT_KEY}`)).toBeDisabled();
    });
});
