import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Trend} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        // Simulate multiple residents checking notifications concurrently
        concurrent_reads: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 10},
                {duration: '20s', target: 10},
                {duration: '5s', target: 0},
            ],
            tags: {scenario: 'concurrent_reads'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:unread_count}': ['p(95)<200'],
        'http_req_duration{endpoint:unread_list}': ['p(95)<400'],
        'http_req_duration{endpoint:history}': ['p(95)<500'],
        'http_req_duration{endpoint:preferences}': ['p(95)<300'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const BUILDING_ID = __ENV.BUILDING_ID || '13195';

const unreadLatency = new Trend('unread_count_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
            'X-Building-ID': BUILDING_ID,
        },
    };
}

export function setup() {
    if (!TOKEN) {
        throw new Error('AUTH_TOKEN env var is required for notification benchmarks');
    }
}

export default function () {
    const h = headers();

    // Hot path: residents poll unread count on every page load
    group('Resident Notification Poll', () => {
        const count = http.get(`${BASE_URL}/notifications/unread-count`, {
            ...h,
            tags: {endpoint: 'unread_count'},
        });
        check(count, {
            'unread-count 200': (r) => r.status === 200,
            'returns unread_count field': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    return typeof body.unread_count === 'number';
                } catch {
                    return false;
                }
            },
        });
        unreadLatency.add(count.timings.duration);

        const unread = http.get(`${BASE_URL}/notifications/unread`, {
            ...h,
            tags: {endpoint: 'unread_list'},
        });
        check(unread, {'notifications/unread 200': (r) => r.status === 200});

        const history = http.get(`${BASE_URL}/notifications/history`, {
            ...h,
            tags: {endpoint: 'history'},
        });
        check(history, {'notifications/history 200': (r) => r.status === 200});

        const prefs = http.get(`${BASE_URL}/notifications/preferences`, {
            ...h,
            tags: {endpoint: 'preferences'},
        });
        check(prefs, {'notifications/preferences 200': (r) => r.status === 200});
    });

    sleep(1);
}

export function teardown() {
    // Read-only benchmark; no notification records are created.
}
