// @featuretrace:levy-fairness — K6 benchmark for the Intelligence / levy-fairness read surface.
// Data flow: k6 -> /api/intelligence/* read contracts -> backend/routers/intelligence.py
//            -> levy_fairness_service / financial forecasts.
// Related: frontend/src/app/(app)/intelligence/levy-fairness/page.tsx
//          frontend/src/app/(app)/intelligence/levy-stability/page.tsx
//          backend/routers/intelligence.py
//
// Zero-coverage gap closed: intelligence.py had 47 routes with no performance
// benchmark (k6_performance_coverage_report_2026-08-10). Levy-fairness and
// forecast endpoints run per-unit entitlement maths + multi-year projections,
// the kind of read that silently degrades as a building's ledger grows.
//
// Read-only: only GET probes. The recompute / simulation / snapshot-write POSTs
// are deliberately excluded — they mutate cached intelligence state.
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        intelligence_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 3},
                {duration: '30s', target: 3},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'intelligence_load'},
        },
    },
    thresholds: {
        // Fairness/subsidy maths fan out per unit.
        'http_req_duration{surface:intel_fairness}': ['p(95)<2000'],
        // Multi-year forecast/stability projections are the heaviest.
        'http_req_duration{surface:intel_forecast}': ['p(95)<2500'],
        'http_req_duration{surface:intel_summary}': ['p(95)<1500'],
        intelligence_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';
const UNIT_NUMBER = __ENV.UNIT_NUMBER || '';

const requiredErrors = new Counter('intelligence_required_errors');
const endpointDuration = new Trend('intelligence_endpoint_ms');

function headers() {
    const h: Record<string, string> = {
        Authorization: `Bearer ${TOKEN}`,
        'Content-Type': 'application/json',
    };
    if (BUILDING_ID) {
        h['X-Building-ID'] = BUILDING_ID;
    }
    return {headers: h};
}

function probe(path: string, name: string, surface: string, required: boolean,
               tolerate404 = false) {
    const res = http.get(`${BASE_URL}${path}`, {
        ...headers(),
        tags: {endpoint: name, surface},
        // A 404 on an OPTIONAL per-entity probe is a property of the fixture data, not
        // of the server. Marking it an expected response keeps it out of
        // http_req_failed, which is threshold-gated at rate<0.02 — otherwise the run
        // reports a performance failure that is really "the row does not exist".
        //
        // Real incident (2026-08-25): run with UNIT_NUMBER=1 against East Gate, whose
        // lots had been wiped (core.lots = 0). The two per-unit probes 404'd, which is
        // CORRECT behaviour, and http_req_failed came out at 11.11% (2 of 18) —
        // failing the threshold while every latency threshold passed by 10x and every
        // check was green. A perf gate that fails on absent fixture data trains people
        // to ignore it.
        responseCallback: tolerate404
            ? http.expectedStatuses({min: 200, max: 399}, 404)
            : undefined,
    });
    endpointDuration.add(res.timings.duration, {endpoint: name});
    if (required) {
        const ok = check(res, {[`${name} returns 200`]: (r) => r.status === 200});
        if (!ok) {
            requiredErrors.add(1, {endpoint: name});
        }
    } else {
        check(res, {[`${name} avoids server error`]: (r) => r.status < 500});
    }
    return res;
}

export function setup() {
    if (!TOKEN) {
        throw new Error('AUTH_TOKEN env var is required for intelligence benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for intelligence probes.');
    }
}

export default function () {
    group('intelligence_summary', () => {
        probe('/intelligence/summary', 'intelligence_summary', 'intel_summary', true);
        probe('/intelligence/maintenance-forecast', 'maintenance_forecast', 'intel_summary', true);
        probe('/intelligence/maintenance-risks', 'maintenance_risks', 'intel_summary', false);
        probe('/intelligence/capital-works', 'capital_works', 'intel_summary', false);
    });

    sleep(0.5);

    group('intelligence_levy_fairness', () => {
        probe('/intelligence/levy-fairness', 'levy_fairness', 'intel_fairness', true);
        probe('/intelligence/levy-model', 'levy_model', 'intel_fairness', true);
        probe('/intelligence/levy-facilities', 'levy_facilities', 'intel_fairness', false);
        probe('/intelligence/levy-groups', 'levy_groups', 'intel_fairness', false);
        probe('/intelligence/levy-fairness/subsidy-map', 'subsidy_map', 'intel_fairness', false);
        probe('/intelligence/levy-fairness/audit', 'fairness_audit', 'intel_fairness', false);
        probe('/intelligence/levy-fairness/snapshots', 'fairness_snapshots', 'intel_fairness', false);
        if (UNIT_NUMBER) {
            const u = encodeURIComponent(UNIT_NUMBER);
            // tolerate404: UNIT_NUMBER may not exist in the target building's data.
            probe(`/intelligence/levy-fairness/unit/${u}/explain`, 'fairness_unit_explain', 'intel_fairness', false, true);
            probe(`/intelligence/levy-fairness/subsidy-map/unit/${u}`, 'subsidy_map_unit', 'intel_fairness', false, true);
        }
    });

    sleep(0.5);

    group('intelligence_forecasts', () => {
        probe('/intelligence/capital-shock', 'capital_shock', 'intel_forecast', true);
        probe('/intelligence/special-levy-forecast', 'special_levy_forecast', 'intel_forecast', true);
        probe('/intelligence/special-levy-report', 'special_levy_report', 'intel_forecast', false);
        probe('/intelligence/levy-stability', 'levy_stability', 'intel_forecast', true);
        probe('/intelligence/levy-stabilization', 'levy_stabilization', 'intel_forecast', false);
    });

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No test data is created.
}
