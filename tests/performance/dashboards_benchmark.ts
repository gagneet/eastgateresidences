// @featuretrace:dashboard-v2 — K6 benchmark for all four dashboard surfaces.
// Layer: test
// Data flow: k6 -> the exact API fan-out each dashboard issues on mount
//            -> analytics / finance / intelligence / workflow / ppm routers.
// Related: frontend/src/app/(dashboard)/dashboard/page.tsx          (Management + Owner, new)
//          frontend/src/pages/dashboard/ManagerDashboard.jsx        (Management, classic)
//          frontend/src/pages/dashboard/OwnerDashboard.tsx          (Owner, classic)
//          backend/routers/analytics.py
//          backend/services/finance_route_cutover_service.py
//
// WHY THIS EXISTS
// ---------------
// dashboard_benchmark.js and owner_dashboard_benchmark.ts each cover part of one
// surface. Neither models what a dashboard actually costs to open, which is the
// number users feel: the whole fan-out, and specifically how much of it blocks
// first paint.
//
// Each surface below is measured two ways:
//   * sequential_total_ms — the cost when calls are chained (what the classic
//     dashboards did before 2026-08-24: N awaits back to back).
//   * critical_path_ms    — the cost of the wave that actually gates first paint,
//     measured with all of a wave's calls in flight at once.
// The gap between them is the win from parallelising, and it is the thing to
// watch for regressions: a single new `await` added to a fetch chain shows up
// here immediately.
//
// Read-only: every request is a GET. No records are created, so there is nothing
// to tear down — see teardown() at the bottom.
import http from 'k6/http';
import {check, group} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';
const UNIT = __ENV.UNIT_NUMBER || 'TH087';
const YEAR = __ENV.FINANCIAL_YEAR || String(new Date().getFullYear());
const MEASURE_SEQUENTIAL = String(__ENV.MEASURE_SEQUENTIAL || '').toLowerCase() === 'true';

export const options: Options = {
    scenarios: {
        smoke: {executor: 'constant-vus', vus: 1, duration: '20s', tags: {scenario: 'smoke'}},
        load: {
            executor: 'ramping-vus',
            startVUs: 0,
            startTime: '20s',
            stages: [
                {duration: '15s', target: 5},
                {duration: '30s', target: 5},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'load'},
        },
    },
    thresholds: {
        // Page-open budget, scoped to the SMOKE scenario (1 VU) on purpose.
        //
        // This is the number the user actually feels: how long one person waits for
        // one dashboard to become usable. Scoping to 1 VU keeps it a measure of the
        // work a page costs, not of how much backend concurrency the box running the
        // test happens to have — under the ramping `load` scenario a single-worker
        // dev backend queues 5 VUs and the p95 reflects that queuing, which tells you
        // nothing about whether the page itself got faster.
        //
        // Watch the `load` scenario for saturation and errors instead; that is what
        // dash_server_errors and the reported trends are for.
        'dash_critical_path_ms{scenario:smoke,surface:management_new}': ['p(95)<800'],
        'dash_critical_path_ms{scenario:smoke,surface:management_classic}': ['p(95)<800'],
        'dash_critical_path_ms{scenario:smoke,surface:owner_new}': ['p(95)<800'],
        'dash_critical_path_ms{scenario:smoke,surface:owner_classic}': ['p(95)<800'],

        // Only server faults are failures here. `http_req_failed` deliberately has no
        // threshold: it counts any non-2xx, and 404 is a CORRECT answer from the
        // unit-scoped finance routes when the benchmarked unit has no ledger row for
        // the selected year — which is exactly the state East Gate is in for units
        // with no reconstructed history. Gating on it would make the benchmark fail
        // for a data condition rather than a performance regression.
        dash_server_errors: ['count==0'],
    },
};

const criticalPath = new Trend('dash_critical_path_ms', true);
const sequentialTotal = new Trend('dash_sequential_total_ms', true);
const endpointMs = new Trend('dash_endpoint_ms', true);
const serverErrors = new Counter('dash_server_errors');

function params(surface: string) {
    const headers: Record<string, string> = {
        Authorization: `Bearer ${TOKEN}`,
        'Content-Type': 'application/json',
    };
    if (BUILDING_ID) {
        headers['X-Building-ID'] = BUILDING_ID;
    }
    return {headers, tags: {surface}};
}

const u = encodeURIComponent(UNIT);

// The hero wave gates first paint; the secondary wave streams into its own cards.
// Keep these lists in step with the dashboards' `critical` / `secondary` arrays.
const SURFACES: Record<string, {hero: string[]; secondary: string[]}> = {
    management_new: {
        hero: [
            `/finance/building-overview?year=${YEAR}`,
            '/finance/portal-bank-balances',
            '/workflow-requests/stats/triage',
        ],
        secondary: [
            '/analytics/compliance-summary',
            `/analytics/levy-benchmarks?financial_year=${YEAR}`,
            '/analytics/activities?limit=10&offset=0',
            `/analytics/levy-allocation-breakdown?year=${YEAR}`,
            '/analytics/sinking-fund-forecast?years=10',
            '/analytics/maintenance/spend-trend',
            '/intelligence/levy-fairness',
            '/intelligence/capital-shock',
            `/analytics/dashboard-v2-extras?unit_number=${u}`,
            '/meetings?status=scheduled&limit=1',
            '/analytics/market-snapshot',
        ],
    },
    management_classic: {
        hero: [
            `/stats/building-kpis?financial_year=${YEAR}`,
            `/finance/building-overview?year=${YEAR}`,
            '/finance/portal-bank-balances',
            '/arrears/detail',
        ],
        secondary: [
            '/analytics/maintenance-stats',
            '/analytics/activities?limit=15',
            '/workflow-requests?status=awaiting_review',
            '/workflow-requests?status=overdue',
            '/workflow-requests/stats/triage',
            `/analytics/levy-benchmarks?financial_year=${YEAR}`,
            '/admin/stats',
            '/analytics/sinking-fund-forecast?years=10',
            '/analytics/compliance-summary',
            '/analytics/maintenance/spend-trend',
            '/analytics/expenses-by-supplier?months=12',
            '/ppm/dashboard',
            '/ppm/upcoming?days=60',
            '/intelligence/levy-fairness',
            '/intelligence/capital-shock',
        ],
    },
    owner_new: {
        hero: [
            `/finance/unit-dashboard-overview/${u}?year=${YEAR}`,
            `/owner-hub/unit-tco?unit_number=${u}&year=${YEAR}`,
            `/finance/building-overview?year=${YEAR}`,
        ],
        secondary: [
            `/analytics/my-streak?unit_number=${u}`,
            '/workflow-requests?limit=5',
            '/workflow-requests?limit=100',
            `/analytics/levy-allocation-breakdown?year=${YEAR}`,
            '/analytics/sinking-fund-forecast?years=10',
            '/intelligence/capital-shock',
        ],
    },
    owner_classic: {
        hero: [
            `/finance/unit-dashboard-overview/${u}?year=${YEAR}`,
            `/owner-hub/unit-tco?unit_number=${u}&year=${YEAR}`,
        ],
        secondary: [
            '/analytics/sinking-fund-forecast?years=10',
            '/analytics/maintenance-stats',
            '/analytics/activities?limit=15',
            '/analytics/market-snapshot',
            '/annual-levies',
            '/agm',
            '/analytics/compliance-summary',
            '/intelligence/summary',
            '/intelligence/capital-shock',
            `/analytics/levy-allocation-breakdown?year=${YEAR}`,
            `/analytics/my-streak?unit_number=${u}`,
            '/workflow-requests?limit=5',
            `/analytics/dashboard-v2-extras?unit_number=${u}`,
            '/meetings?status=scheduled&limit=1',
            '/security/my-activity',
            '/documents/important',
            `/units/${u}/market-valuation`,
        ],
    },
};

/** Fire a wave concurrently (as the browser does) and return its wall-clock cost. */
function wave(paths: string[], surface: string): number {
    if (paths.length === 0) return 0;
    const requests = paths.map((p) => ['GET', `${BASE_URL}${p}`, null, params(surface)]);
    const started = Date.now();
    const responses = http.batch(requests as any);
    const elapsed = Date.now() - started;

    responses.forEach((res: any, i: number) => {
        endpointMs.add(res.timings.duration, {surface, endpoint: paths[i]});
        // 404 is a legitimate answer for a unit with no ledger row in the selected
        // year — this benchmark is about latency, so only server faults are failures.
        if (res.status >= 500 || res.status === 0) {
            serverErrors.add(1, {surface, endpoint: paths[i]});
        }
        check(res, {[`${paths[i]} is not a server error`]: (r: any) => r.status < 500 && r.status !== 0});
    });
    return elapsed;
}

function requireToken() {
    if (!TOKEN) {
        throw new Error('AUTH_TOKEN env var is required. Mint one with: ' +
            'cd backend && ./venv/bin/python3 ../tests/performance/harness/' +
            'dashboard_endpoint_timing.py --print-token');
    }
}

export function setup() {
    requireToken();
}

export default function () {
    for (const [surface, spec] of Object.entries(SURFACES)) {
        group(surface, () => {
            // What gates first paint.
            const heroMs = wave(spec.hero, surface);
            criticalPath.add(heroMs, {surface});

            // The rest of the fan-out, also concurrent.
            const secondaryMs = wave(spec.secondary, surface);

            endpointMs.add(heroMs + secondaryMs, {surface, endpoint: '__parallel_total__'});

            // Optional diagnostic: what the same work costs chained one after another,
            // i.e. what these pages cost before the 2026-08-24 parallelisation. It is
            // off by default because it re-issues every endpoint a second time, which
            // roughly doubles the load a run puts on the backend and would distort the
            // critical-path numbers above. Turn it on deliberately when you want the
            // before/after contrast: -e MEASURE_SEQUENTIAL=true
            if (MEASURE_SEQUENTIAL) {
                let seq = 0;
                for (const p of [...spec.hero, ...spec.secondary]) {
                    seq += http.get(`${BASE_URL}${p}`, params(surface)).timings.duration;
                }
                sequentialTotal.add(seq, {surface});
            }
        });
    }
}

export function teardown() {
    // Read-only benchmark: every request is a GET and no record is created, so
    // there is nothing to delete and no is_test_data residue to verify.
}
