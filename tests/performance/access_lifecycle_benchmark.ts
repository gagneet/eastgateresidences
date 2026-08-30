// @featuretrace:access-lifecycle — K6 benchmark for the access-device lifecycle read surface.
// Data flow: k6 -> /api/access/* read contracts -> backend/routers/access_lifecycle.py
//            -> Mongo access device/request/audit collections.
// Related: backend/routers/access_lifecycle.py
//
// Zero-coverage gap closed: access_lifecycle.py had 16 routes with no performance
// benchmark. The device and request registers (and per-request audit trail) grow
// with every fob/key issued, so these list reads are the latency to guard.
//
// Read-only: only GET probes. Device/request creation, approval, issue, disable,
// return, and mark-lost POSTs are excluded (they mutate the access register).
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {executor: 'constant-vus', vus: 1, duration: '10s', tags: {scenario: 'smoke'}},
        access_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 4},
                {duration: '30s', target: 4},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'access_load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:device_types}': ['p(95)<400'],
        'http_req_duration{endpoint:devices}': ['p(95)<600'],
        'http_req_duration{endpoint:requests}': ['p(95)<600'],
        'http_req_duration{endpoint:device_detail}': ['p(95)<400'],
        'http_req_duration{endpoint:request_audit}': ['p(95)<600'],
        access_lifecycle_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';

const requiredErrors = new Counter('access_lifecycle_required_errors');
const endpointDuration = new Trend('access_lifecycle_endpoint_ms');

function headers() {
    const h: Record<string, string> = {Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json'};
    if (BUILDING_ID) {
        h['X-Building-ID'] = BUILDING_ID;
    }
    return {headers: h};
}

function firstId(body: string): string {
    try {
        const parsed = JSON.parse(body);
        const arr = Array.isArray(parsed) ? parsed : parsed.items || parsed.results || [];
        if (Array.isArray(arr) && arr.length > 0) {
            const rec = arr[0];
            return String(rec.id || rec._id || rec.device_id || rec.request_id || '');
        }
    } catch {
        // fall through
    }
    return '';
}

function probe(path: string, name: string, required: boolean) {
    const res = http.get(`${BASE_URL}${path}`, {...headers(), tags: {endpoint: name}});
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
        throw new Error('AUTH_TOKEN env var is required for access-lifecycle benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for access-lifecycle probes.');
    }
    const devices = http.get(`${BASE_URL}/access/devices`, headers());
    const requests = http.get(`${BASE_URL}/access/requests`, headers());
    return {
        deviceId: devices.status === 200 ? firstId(devices.body as string) : '',
        requestId: requests.status === 200 ? firstId(requests.body as string) : '',
    };
}

export default function (data: {deviceId: string; requestId: string}) {
    group('access_registers', () => {
        probe('/access/device-types', 'device_types', true);
        probe('/access/devices', 'devices', true);
        probe('/access/requests', 'requests', true);
    });

    sleep(0.5);

    if (data.deviceId) {
        probe(`/access/devices/${encodeURIComponent(data.deviceId)}`, 'device_detail', false);
    }
    if (data.requestId) {
        probe(`/access/requests/${encodeURIComponent(data.requestId)}/audit`, 'request_audit', false);
    }

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No access devices or requests are created.
}
