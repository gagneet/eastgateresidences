// @featuretrace:insurance — K6 benchmark for the insurance management read surface.
// Data flow: k6 -> /api/insurance/* read contracts -> backend/routers/insurance.py
//            -> Mongo insurance collections.
// Related: frontend/src/app/(app)/insurance/page.tsx
//          backend/routers/insurance.py
//
// Zero-coverage gap closed: insurance.py had 12 routes with no performance
// benchmark (k6_performance_coverage_report_2026-08-10). The compliance and
// agm-disclosure endpoints derive statutory disclosure state across every active
// policy, so they are the read latency to guard.
//
// Read-only: only GET probes. setup() resolves a live policy_id from the list
// endpoint so the policy-detail read is exercised. No policy or broker is written.
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
        insurance_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 4},
                {duration: '30s', target: 4},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'insurance_load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:policies_list}': ['p(95)<600'],
        'http_req_duration{endpoint:policy_detail}': ['p(95)<500'],
        'http_req_duration{endpoint:compliance}': ['p(95)<1000'],
        'http_req_duration{endpoint:agm_disclosure}': ['p(95)<1000'],
        'http_req_duration{endpoint:expiring}': ['p(95)<800'],
        'http_req_duration{endpoint:brokers}': ['p(95)<500'],
        insurance_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';

const requiredErrors = new Counter('insurance_required_errors');
const endpointDuration = new Trend('insurance_endpoint_ms');

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

function firstPolicyId(body: string): string {
    try {
        const parsed = JSON.parse(body);
        const arr = Array.isArray(parsed) ? parsed : parsed.policies || parsed.items || [];
        if (Array.isArray(arr) && arr.length > 0) {
            const rec = arr[0];
            return String(rec.id || rec._id || rec.policy_id || '');
        }
    } catch {
        // fall through
    }
    return '';
}

function probe(path: string, name: string, required: boolean) {
    const res = http.get(`${BASE_URL}${path}`, {
        ...headers(),
        tags: {endpoint: name},
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
        throw new Error('AUTH_TOKEN env var is required for insurance benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for insurance probes.');
    }
    const policiesRes = http.get(`${BASE_URL}/insurance/policies`, headers());
    return {
        policyId: policiesRes.status === 200 ? firstPolicyId(policiesRes.body as string) : '',
    };
}

export default function (data: {policyId: string}) {
    group('insurance_core', () => {
        probe('/insurance/policies', 'policies_list', true);
        probe('/insurance/compliance', 'compliance', true);
        probe('/insurance/agm-disclosure', 'agm_disclosure', true);
        probe('/insurance/expiring', 'expiring', true);
        probe('/insurance/brokers', 'brokers', false);
    });

    if (data.policyId) {
        group('insurance_detail', () => {
            const p = encodeURIComponent(data.policyId);
            probe(`/insurance/policies/${p}`, 'policy_detail', false);
        });
    }

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No policies or brokers are created.
}
