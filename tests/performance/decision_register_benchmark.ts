/**
 * k6 performance benchmark — Decision Register (GAP-GOV-005)
 *
 * Decision register is low-write (EC records decisions after meetings) but
 * high-read (all owners view the register). Benchmark covers:
 *   - Paginated list (all-owner read path, most frequent)
 *   - Full-text search (regex across title/text/tags)
 *   - Single get
 *   - Create decision (EC-only write path)
 *
 * Usage:
 *   k6 run tests/performance/decision_register_benchmark.ts \
 *     -e BASE_URL=http://localhost:8003/api \
 *     -e AUTH_TOKEN=<owner_jwt> \
 *     -e EC_TOKEN=<ec_member_jwt>
 */
import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '15s',
            tags: {scenario: 'smoke'},
        },
        // Simulate owners browsing decisions after AGM minutes are published
        burst: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 15},
                {duration: '30s', target: 15},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'burst'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:list}': ['p(95)<600'],
        'http_req_duration{endpoint:search}': ['p(95)<800'],
        'http_req_duration{endpoint:get_one}': ['p(95)<400'],
        'http_req_duration{endpoint:create}': ['p(95)<1000'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const EC_TOKEN = __ENV.EC_TOKEN || __ENV.AUTH_TOKEN;

const listErrors = new Counter('decision_list_errors');
const searchDuration = new Trend('decision_search_ms');

const PERF_PREFIX = 'Perf decision register perf-';

function headers(token = TOKEN) {
    return {
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
    };
}

function uniqueId(): string {
    return `${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

export default function () {
    let singleId: string | null = null;

    group('List decisions (owner read)', () => {
        const res = http.get(`${BASE_URL}/decisions?page=1&page_size=20`, {
            ...headers(),
            tags: {endpoint: 'list'},
        });
        const ok = check(res, {
            'list 200': (r) => r.status === 200,
            'returns data array': (r) => {
                try {
                    return Array.isArray(JSON.parse(r.body as string).data);
                } catch {
                    return false;
                }
            },
        });
        if (!ok) listErrors.add(1);

        // Capture a real decision ID for the get-one test
        try {
            const body = JSON.parse(res.body as string);
            if (body.data?.length > 0) {
                singleId = body.data[0].id;
            }
        } catch { /* ignore */
        }
    });

    sleep(0.2);

    group('List with filter (meeting_type=ec_meeting)', () => {
        const res = http.get(
            `${BASE_URL}/decisions?meeting_type=ec_meeting&page_size=20`,
            {...headers(), tags: {endpoint: 'list'}},
        );
        check(res, {'filtered list 200': (r) => r.status === 200});
    });

    sleep(0.2);

    group('Full-text search', () => {
        const res = http.get(
            `${BASE_URL}/decisions/search?q=maintenance&page_size=10`,
            {...headers(), tags: {endpoint: 'search'}},
        );
        check(res, {
            'search 200': (r) => r.status === 200,
            'search has query field': (r) => {
                try {
                    return JSON.parse(r.body as string).query === 'maintenance';
                } catch {
                    return false;
                }
            },
        });
        searchDuration.add(res.timings.duration);
    });

    sleep(0.2);

    if (singleId) {
        group('Get single decision', () => {
            const res = http.get(`${BASE_URL}/decisions/${singleId}`, {
                ...headers(),
                tags: {endpoint: 'get_one'},
            });
            check(res, {
                'get one 200': (r) => r.status === 200,
                'has decision_number': (r) => {
                    try {
                        return !!JSON.parse(r.body as string).decision_number;
                    } catch {
                        return false;
                    }
                },
            });
        });
        sleep(0.2);
    }

    // Write path — EC token only, low rate (1 in 5 iterations)
    if (__ITER % 5 === 0) {
        group('Create decision (EC write path)', () => {
            const payload = JSON.stringify({
                building_id: 'auto', // overridden server-side
                meeting_type: 'ec_meeting',
                meeting_date: '2026-04-15',
                motion_title: `${PERF_PREFIX}${uniqueId()}`,
                motion_text: 'Performance test motion — to be deleted in teardown. No operational significance.',
                resolution_type: 'ordinary',
                result: 'passed',
                vote_for: 3,
                vote_against: 0,
                vote_abstain: 0,
                tags: ['perf-test'],
                is_test_data: true,
            });

            const res = http.post(`${BASE_URL}/decisions`, payload, {
                ...headers(EC_TOKEN),
                tags: {endpoint: 'create'},
            });
            check(res, {
                'create 201': (r) => r.status === 201,
                'has decision_number': (r) => {
                    try {
                        return !!JSON.parse(r.body as string).decision_number;
                    } catch {
                        return false;
                    }
                },
            });
        });
    }

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Search for perf-created decisions and delete them
    const search = http.get(
        `${BASE_URL}/decisions/search?q=${encodeURIComponent(PERF_PREFIX)}&page_size=100&include_test_data=true`,
        {
            headers: {
                Authorization: `Bearer ${EC_TOKEN}`,
                'Content-Type': 'application/json',
            },
        },
    );

    if (search.status !== 200) return;

    let body: { data?: Array<{ id: string; motion_title?: string }> };
    try {
        body = JSON.parse(search.body as string);
    } catch {
        return;
    }

    for (const decision of body.data || []) {
        if (decision.motion_title?.startsWith(PERF_PREFIX)) {
            http.del(`${BASE_URL}/decisions/${decision.id}`, null, {
                headers: {Authorization: `Bearer ${EC_TOKEN}`},
            });
        }
    }
}
