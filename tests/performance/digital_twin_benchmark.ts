// @featuretrace:digital-twin — K6 benchmark for the digital-twin building-model read surface.
// Data flow: k6 -> /api/digital-twin/* read contracts -> backend/routers/digital_twin.py
//            -> digital_twin_repository (zones/facilities/assets/benefit-groups).
// Related: frontend/src/app/(app)/intelligence/assets/page.tsx
//          backend/routers/digital_twin.py
//
// Zero-coverage gap closed: digital_twin.py had 16 routes with no performance
// benchmark. The building-model graph reads (zones -> facilities -> assets)
// back the asset-intelligence and levy-fairness facility maps.
//
// Read-only: only GET probes. Zone/facility/asset creation + linking POSTs are
// excluded.
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {executor: 'constant-vus', vus: 1, duration: '10s', tags: {scenario: 'smoke'}},
        twin_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 5},
                {duration: '30s', target: 5},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'twin_load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:zones}': ['p(95)<600'],
        'http_req_duration{endpoint:facilities}': ['p(95)<600'],
        'http_req_duration{endpoint:assets}': ['p(95)<800'],
        'http_req_duration{endpoint:benefit_groups}': ['p(95)<600'],
        digital_twin_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';

const requiredErrors = new Counter('digital_twin_required_errors');
const endpointDuration = new Trend('digital_twin_endpoint_ms');

function headers() {
    const h: Record<string, string> = {Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json'};
    if (BUILDING_ID) {
        h['X-Building-ID'] = BUILDING_ID;
    }
    return {headers: h};
}

function probe(path: string, name: string, required: boolean) {
    const res = http.get(`${BASE_URL}${path}`, {...headers(), tags: {endpoint: name}});
    endpointDuration.add(res.timings.duration, {endpoint: name});
    if (required) {
        const ok = check(res, {
            [`${name} returns 200`]: (r) => r.status === 200,
            [`${name} returns array`]: (r) => {
                try {
                    return Array.isArray(JSON.parse(r.body as string));
                } catch {
                    return false;
                }
            },
        });
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
        throw new Error('AUTH_TOKEN env var is required for digital-twin benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for digital-twin probes.');
    }
}

export default function () {
    group('digital_twin_model', () => {
        probe('/digital-twin/zones', 'zones', true);
        probe('/digital-twin/facilities', 'facilities', true);
        probe('/digital-twin/assets', 'assets', true);
        probe('/digital-twin/benefit-groups', 'benefit_groups', true);
    });

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No digital-twin records are created.
}
