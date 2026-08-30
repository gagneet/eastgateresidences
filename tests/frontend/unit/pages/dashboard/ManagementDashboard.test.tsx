import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import {ManagementDashboard} from '@/app/(dashboard)/dashboard/ManagementDashboard';

const mockApiGet = jest.fn(() => new Promise(() => {}));
const mockRouterPush = jest.fn();

// Built ONCE and returned by reference — see ManagementDashboardTooltips.test.tsx
// for the same fix. A fresh object literal per call gives `api` a new identity on
// every render; ManagementDashboard's loader lists `api` in its dependency array
// and calls setDaysSinceLastVisit(), so the effect re-fired on every render and
// looped ("Maximum update depth exceeded"), hanging the worker for ever.
//
// A mock must reproduce the real value's referential stability, not just its
// shape. The real AuthContext memoises `api` with an empty dependency array, so
// this only ever bit under the harness.
const mockAuthValue = {
    user: {id: 'u1', role: 'strata_manager', full_name: 'Test Manager'},
    api: {get: mockApiGet},
    selectedBuilding: {id: '13195', name: 'Test Building'},
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuthValue,
}));

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: mockRouterPush}),
}));

jest.mock('@/components/dashboard/PulseScoreCard', () => ({
    __esModule: true,
    default: () => <div>Building Pulse · live</div>,
}));
jest.mock('@/components/dashboard/SinceLastVisit', () => ({
    __esModule: true,
    default: ({onItemSelect}: any) => (
        <div>
            Since your last visit
            <button type="button" onClick={() => onItemSelect?.({kind: 'ARREARS', label: '$1,235 outstanding', value: '3 units'})}>
                Open arrears update
            </button>
            <button type="button" onClick={() => onItemSelect?.({kind: 'SLA', label: '1 request past SLA', value: '1'})}>
                Open SLA update
            </button>
        </div>
    ),
}));
jest.mock('@/components/dashboard/DashboardDetailModal', () => ({
    __esModule: true,
    default: ({isOpen, actionLabel, onAction, children}: any) => isOpen ? (
        <div>
            {children}
            {actionLabel && <button type="button" onClick={onAction}>{actionLabel}</button>}
        </div>
    ) : null,
}));
jest.mock('@/components/dashboard/TriageQueue', () => ({
    __esModule: true,
    default: ({onAction, queueHref}: any) => (
        <div>
            Today's Triage
            <button type="button" onClick={() => onAction?.({id: 'req-1', status: 'overdue'})}>
                Handle now
            </button>
            <button type="button" onClick={() => onAction?.({status: 'overdue'})}>
                Handle unidentified
            </button>
            <a href={queueHref}>View request inbox</a>
        </div>
    ),
}));
// Serialise the mapped projection so the field-name mapping is assertable. The chart
// itself is Recharts-heavy and not what these tests are about; the mapping is.
jest.mock('@/components/dashboard/ReserveRunwayChart', () => ({
    __esModule: true,
    default: ({projection}: any) => (
        <div>
            Reserve forecast
            <span data-testid="reserve-projection">{JSON.stringify(projection ?? [])}</span>
        </div>
    ),
}));
jest.mock('@/components/dashboard/CompactCalendar', () => ({
    __esModule: true,
    default: () => <div>Compact calendar</div>,
}));
jest.mock('@/components/dashboard/premium', () => ({
    ActivityFeedPremium: () => <div>Activity feed</div>,
}));

const VENDOR_DATA = {
    stats: {},
    maintenance: {},
    compliance: {},
    activities: [],
    maintenance_spend_trend: [{vendor: 'BluePoint Plumbing', jobs: 12, spend: 14200}],
};

describe('ManagementDashboard', () => {
    beforeEach(() => {
        mockApiGet.mockClear();
        mockRouterPush.mockClear();
    });

    it('renders the new management cockpit sections', () => {
        render(
            <ManagementDashboard
                selectedYear="2026"
                data={{
                    stats: {
                        total_arrears: 17649.7,
                        units_in_arrears: 18,
                    },
                    building_overview: {
                        admin_fund: {current_balance: 9187.44, closing_balance: 180000},
                        sinking_fund: {current_balance_cents: 19333703, closing_balance_cents: 29839800},
                    },
                    maintenance: {open_requests: 4},
                    compliance: {items: [{label: 'Fire Annual Cert', due_date: '2026-07-24', days_left: 60}]},
                    activities: [],
                    maintenance_spend_trend: [{vendor: 'BluePoint Plumbing', jobs: 12, spend: 14200}],
                }}
            />
        );

        expect(screen.getByText(/Building Pulse/i)).toBeInTheDocument();
        expect(screen.getByText(/Today's Triage/i)).toBeInTheDocument();
        expect(screen.getByText(/Cash Position/i)).toBeInTheDocument();
        expect(screen.getByText(/9,187\.44/)).toBeInTheDocument();
        expect(screen.getByText(/193,337\.03/)).toBeInTheDocument();
        expect(screen.queryByText(/180,000\.00/)).not.toBeInTheDocument();
        expect(screen.queryByText(/298,398\.00/)).not.toBeInTheDocument();
        expect(screen.getByText(/Levy Fairness Index/i)).toBeInTheDocument();
        expect(screen.getByText(/Compliance Watchlist/i)).toBeInTheDocument();
        expect(screen.getByText(/Vendor performance/i)).toBeInTheDocument();
        expect(screen.getByText(/BluePoint Plumbing/i)).toBeInTheDocument();
    });

    it('renders an unknown collection rate as a dash, never as 0.00%', () => {
        // `stats: {}` is the "no data yet" case. This test previously asserted 0.00%,
        // pinning the old fallback — collectionRate defaulted to 0 when neither
        // collection_rate nor the levied/collected pair was present.
        //
        // "0% collected" is the worst possible direction for a fabricated number on this
        // particular metric: it reads as a building collecting nothing, on the strength
        // of an API response that simply had not arrived. The rate is now null and the
        // card renders "—", which is CashPositionCard's existing contract — it has always
        // been typed number | null and handled the dash; the dashboard just never passed
        // null. The dollar figures below legitimately stay $0.00: those are accumulators
        // over an empty set, not an absent measurement.
        render(<ManagementDashboard selectedYear="2026" data={{stats: {}, maintenance: {}, compliance: {}, activities: []}}/>);

        expect(screen.getByText('Admin Fund')).toBeInTheDocument();
        expect(screen.getByText('Sinking Fund')).toBeInTheDocument();
        expect(screen.getByText('Arrears')).toBeInTheDocument();
        expect(screen.getAllByText('$0.00').length).toBeGreaterThanOrEqual(2);
        expect(screen.getByText('0 units outstanding')).toBeInTheDocument();
        expect(screen.getByText('Collection')).toBeInTheDocument();
        expect(screen.queryByText('0.00%')).not.toBeInTheDocument();
        // Several tiles legitimately show a dash for absent data (vendor on-time, for
        // one), so assert at least one rather than exactly one — the load-bearing
        // assertion is the ABSENCE of the fabricated 0.00% above.
        expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1);
    });

    it('still shows a real collection rate when one is supplied', () => {
        render(<ManagementDashboard selectedYear="2026" data={{
            stats: {collection_rate: 96.4}, maintenance: {}, compliance: {}, activities: [],
        }}/>);
        expect(screen.getByText('96.40%')).toBeInTheDocument();
    });

    describe('reserve projection mapping', () => {
        const readProjection = () =>
            JSON.parse(screen.getByTestId('reserve-projection').textContent || '[]');

        const renderWithForecast = (projection: any[]) =>
            render(
                <ManagementDashboard
                    selectedYear="2026"
                    data={{
                        stats: {},
                        maintenance: {},
                        compliance: {},
                        activities: [],
                        sinking_fund_forecast: {projection},
                    }}
                />
            );

        // The regression this whole block exists for: /analytics/sinking-fund-forecast
        // returns `expenses`, but the mapping only looked for capital_works / capital_spend
        // / projected_expenses. None matched, so every year fell through to `?? 0` and the
        // "Capital works" figure read $0.00 no matter what the capital schedule held.
        it('maps capital works from the backend "expenses" field', () => {
            renderWithForecast([
                {year: 2029, opening_balance: 302052.06, contributions: 90459, expenses: 456317.33, closing_balance: -63806.27},
            ]);
            expect(readProjection()[0].capital_works).toBe(456317.33);
        });

        it('keeps a real $0 of capital works as 0, not as missing', () => {
            // A year the schedule lists no asset for is a known zero and must stay a number.
            renderWithForecast([{year: 2028, contributions: 90459, expenses: 0, closing_balance: 302052.06}]);
            expect(readProjection()[0].capital_works).toBe(0);
        });

        it('leaves capital works undefined when the backend reports it unknown', () => {
            // null = "no capital plan or replacement schedule on record". Coercing that to 0
            // is what rendered a fabricated $0.00 for buildings with no data at all.
            renderWithForecast([{year: 2030, contributions: null, expenses: null, closing_balance: 166969.06}]);
            const row = readProjection()[0];
            expect(row.capital_works).toBeUndefined();
            expect(row.contributions).toBeUndefined();
        });

        it('still honours the legacy Postgres-path aliases', () => {
            renderWithForecast([{year: 2031, capital_spend: 171345.34, closing_balance: 1}]);
            expect(readProjection()[0].capital_works).toBe(171345.34);
        });

        it('prefers "expenses" over a stale alias when both are present', () => {
            renderWithForecast([{year: 2032, expenses: 500, capital_works: 999, closing_balance: 1}]);
            expect(readProjection()[0].capital_works).toBe(500);
        });

        it('keeps a real $0 contribution rather than dropping it', () => {
            renderWithForecast([{year: 2033, contributions: 0, expenses: 10, closing_balance: 1}]);
            expect(readProjection()[0].contributions).toBe(0);
        });
    });

    it('does not render legacy metric card sections', () => {
        render(<ManagementDashboard data={{stats: {}, maintenance: {}, compliance: {}, activities: []}}/>);
        expect(screen.queryByText(/Active Residents/i)).toBeNull();
        expect(screen.queryByText(/Open Works/i)).toBeNull();
        expect(screen.queryByText(/Arrears Total/i)).toBeNull();
    });

    it('loads since-last-visit arrears for the selected financial year', async () => {
        render(<ManagementDashboard selectedYear="2026" data={{stats: {}, maintenance: {}, compliance: {}, activities: []}}/>);

        await waitFor(() => {
            expect(mockApiGet).toHaveBeenCalledWith(expect.stringMatching(/^\/analytics\/diff-since\?since=.*&year=2026$/));
        });
    });

    it('opens the arrears page from an arrears since-last-visit card', async () => {
        render(<ManagementDashboard selectedYear="2026" data={{stats: {}, maintenance: {}, compliance: {}, activities: []}}/>);

        fireEvent.click(screen.getByText('Open arrears update'));
        fireEvent.click(await screen.findByText('Open source'));

        expect(mockRouterPush).toHaveBeenCalledWith('/intelligence/debt-recovery');
    });

    it('opens the specific request from the triage action', () => {
        render(<ManagementDashboard selectedYear="2026" data={{stats: {}, maintenance: {}, compliance: {}, activities: []}}/>);

        fireEvent.click(screen.getByText('Handle now'));

        expect(mockRouterPush).toHaveBeenCalledWith('/requests/req-1');
    });

    it('falls back to the overdue request queue when a triage item has no id', () => {
        render(<ManagementDashboard selectedYear="2026" data={{stats: {}, maintenance: {}, compliance: {}, activities: []}}/>);

        fireEvent.click(screen.getByText('Handle unidentified'));

        // ?tab=my-requests is required: /requests on its own renders the request
        // FORM CATALOGUE and silently drops ?status=.
        expect(mockRouterPush).toHaveBeenCalledWith('/requests?tab=my-requests&status=overdue');
    });

    it('points the triage queue footer at the overdue queue, not the form catalogue', () => {
        render(<ManagementDashboard selectedYear="2026" data={{stats: {}, maintenance: {}, compliance: {}, activities: []}}/>);

        expect(screen.getByText('View request inbox')).toHaveAttribute(
            'href', '/requests?tab=my-requests&status=overdue',
        );
    });

    it('opens the SLA since-last-visit card on the overdue request queue', async () => {
        render(<ManagementDashboard selectedYear="2026" data={{stats: {}, maintenance: {}, compliance: {}, activities: []}}/>);

        fireEvent.click(screen.getByText('Open SLA update'));
        fireEvent.click(await screen.findByText('Open source'));

        expect(mockRouterPush).toHaveBeenCalledWith('/requests?tab=my-requests&status=overdue');
    });

    it('sends the vendor scorecard to the contractors tab that actually exists', async () => {
        render(<ManagementDashboard selectedYear="2026" data={VENDOR_DATA}/>);

        fireEvent.click(screen.getByLabelText(/Open BluePoint Plumbing details/i));
        fireEvent.click(await screen.findByText('Open vendors'));

        // MaintenancePage only knows the tabs in MAINTENANCE_TABS; 'vendors' is not
        // one of them and was silently normalised back to the default 'requests' tab.
        expect(mockRouterPush).toHaveBeenCalledWith('/maintenance?tab=contractors');
    });

    it('sends "view all vendors" to the contractors tab too', () => {
        render(<ManagementDashboard selectedYear="2026" data={VENDOR_DATA}/>);

        fireEvent.click(screen.getByLabelText(/View all vendors/i));

        expect(mockRouterPush).toHaveBeenCalledWith('/maintenance?tab=contractors');
    });
});
