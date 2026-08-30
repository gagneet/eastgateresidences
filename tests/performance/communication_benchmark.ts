// @featuretrace:communication — K6 benchmark for the resident communication read surface.
// Data flow: k6 -> /api/{messages,announcements,notices,conversations} read contracts
//            -> backend/routers/communication.py -> Mongo communication collections.
// Related: frontend/src/app/(app)/community/notices/page.tsx
//          frontend/src/app/(app)/community/messages/page.tsx
//          backend/routers/communication.py
//
// Zero-coverage gap closed: communication.py had 24 routes with no performance
// benchmark (k6_performance_coverage_report_2026-08-10). Announcement/notice
// list endpoints paginate over building-scoped feeds that grow unbounded, so
// list latency is the metric to guard here.
//
// Read-only: only GET probes. Every send/broadcast/acknowledge POST is excluded
// because it would emit real messages/notifications to residents.
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
        communication_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 5},
                {duration: '30s', target: 5},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'communication_load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:announcements}': ['p(95)<800'],
        'http_req_duration{endpoint:notices}': ['p(95)<800'],
        'http_req_duration{endpoint:messages}': ['p(95)<600'],
        'http_req_duration{endpoint:conversations}': ['p(95)<600'],
        'http_req_duration{endpoint:recipients}': ['p(95)<1000'],
        'http_req_duration{endpoint:notification_preferences}': ['p(95)<400'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';

const requiredErrors = new Counter('communication_required_errors');
const endpointDuration = new Trend('communication_endpoint_ms');

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

function probe(path: string, name: string, required: boolean) {
    const res = http.get(`${BASE_URL}${path}`, {
        ...headers(),
        tags: {endpoint: name},
    });
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
        throw new Error('AUTH_TOKEN env var is required for communication benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for communication probes.');
    }
}

export default function () {
    group('communication_feeds', () => {
        probe('/announcements?limit=25', 'announcements', true);
        probe('/notices?limit=25', 'notices', true);
        probe('/messages?limit=25', 'messages', true);
        probe('/conversations', 'conversations', true);
    });

    sleep(0.5);

    group('communication_settings', () => {
        probe('/notifications/preferences', 'notification_preferences', false);
        // Recipient resolution fans out across the building's user roll — the
        // heaviest read on this router.
        probe('/communication/recipients', 'recipients', false);
    });

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No messages, notices, or announcements are sent.
}
