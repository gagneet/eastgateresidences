import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        // Simulate concurrent residents submitting requests (e.g. after a storm event)
        burst: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 10},
                {duration: '20s', target: 10},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'burst'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:list_requests}': ['p(95)<800'],
        'http_req_duration{endpoint:create_request}': ['p(95)<1000'],
        'http_req_duration{endpoint:list_purchase_orders}': ['p(95)<800'],
        'http_req_duration{endpoint:contractors}': ['p(95)<500'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

const createErrors = new Counter('maintenance_create_errors');
const listDuration = new Trend('maintenance_list_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

function uniqueId(): string {
    return `perf-${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

export function teardown(_data: unknown): void {
    const h = headers();

    // Fetch all maintenance requests and delete any left by this perf run
    const list = http.get(`${BASE_URL}/maintenance?include_test_data=true`, h);
    if (list.status !== 200) return;

    let requests: Array<{ id: string; title: string }>;
    try {
        requests = JSON.parse(list.body as string);
    } catch {
        return;
    }

    const perfEntries = requests.filter((r) => r.title?.startsWith('Perf test request perf-'));

    for (const entry of perfEntries) {
        http.del(`${BASE_URL}/maintenance/${entry.id}`, null, h);
    }
}

export default function () {
    const h = headers();
    const vuId = uniqueId();

    group('Read Paths', () => {
        const list = http.get(`${BASE_URL}/maintenance`, {
            ...h,
            tags: {endpoint: 'list_requests'},
        });
        check(list, {
            'GET /maintenance 200': (r) => r.status === 200,
            'returns array': (r) => {
                try {
                    return Array.isArray(JSON.parse(r.body as string));
                } catch {
                    return false;
                }
            },
        });
        listDuration.add(list.timings.duration);

        const pos = http.get(`${BASE_URL}/purchase-orders`, {
            ...h,
            tags: {endpoint: 'list_purchase_orders'},
        });
        check(pos, {'GET /purchase-orders 200': (r) => r.status === 200});

        const contractors = http.get(`${BASE_URL}/contractors`, {
            ...h,
            tags: {endpoint: 'contractors'},
        });
        check(contractors, {'GET /contractors 200': (r) => r.status === 200});
    });

    sleep(0.5);

    group('Write Path', () => {
        const payload = JSON.stringify({
            title: `Perf test request ${vuId}`,
            description: 'Automated performance test — safe to delete',
            category: 'common_area',
            location: 'Level 1 - Common Area',
            priority: 'low',
            images: [],
            is_test_data: true,
        });

        const create = http.post(`${BASE_URL}/maintenance`, payload, {
            ...h,
            tags: {endpoint: 'create_request'},
        });
        check(create, {
            'POST /maintenance 200 or 201': (r) =>
                r.status === 200 || r.status === 201,
        }) || createErrors.add(1);
    });

    sleep(1);
}
