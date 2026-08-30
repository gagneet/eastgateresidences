import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Trend} from 'k6/metrics';

// Teardown archives (soft-deletes) buildings created during the run via
// DELETE /portfolio/buildings/{id}, which sets is_archived=True — data is
// retained for compliance, not permanently deleted.

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        concurrent_managers: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 5},
                {duration: '20s', target: 5},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'concurrent_managers'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:portfolio_summary}': ['p(95)<600'],
        'http_req_duration{endpoint:portfolio_dashboard}': ['p(95)<800'],
        'http_req_duration{endpoint:portfolio_buildings}': ['p(95)<600'],
        'http_req_duration{endpoint:users_search}': ['p(95)<400'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

const dashboardDuration = new Trend('portfolio_dashboard_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

export function teardown(_data: unknown): void {}

export default function () {
    const h = headers();

    group('Read paths — portfolio overview', () => {
        const summary = http.get(`${BASE_URL}/portfolio/summary`, {
            ...h,
            tags: {endpoint: 'portfolio_summary'},
        });
        check(summary, {
            'portfolio summary 200': (r) => r.status === 200,
            'has active_buildings': (r) => {
                try {
                    const d = JSON.parse(r.body as string);
                    return typeof d.active_buildings === 'number';
                } catch {
                    return false;
                }
            },
        });

        const dashboard = http.get(`${BASE_URL}/portfolio/dashboard`, {
            ...h,
            tags: {endpoint: 'portfolio_dashboard'},
        });
        dashboardDuration.add(dashboard.timings.duration);
        check(dashboard, {
            'portfolio dashboard 200': (r) => r.status === 200,
            'has buildings array': (r) => {
                try {
                    const d = JSON.parse(r.body as string);
                    return Array.isArray(d.buildings);
                } catch {
                    return false;
                }
            },
        });

        const buildings = http.get(`${BASE_URL}/portfolio/buildings`, {
            ...h,
            tags: {endpoint: 'portfolio_buildings'},
        });
        check(buildings, {'portfolio buildings 200': (r) => r.status === 200});
    });

    sleep(0.5);

    group('User search for assignment', () => {
        const smSearch = http.get(`${BASE_URL}/portfolio/users/search?role=strata_manager`, {
            ...h,
            tags: {endpoint: 'users_search'},
        });
        check(smSearch, {
            'users/search 200': (r) => r.status === 200,
            'returns array': (r) => {
                try {
                    const d = JSON.parse(r.body as string);
                    return Array.isArray(d);
                } catch {
                    return false;
                }
            },
            'no credential fields exposed': (r) => {
                const body = r.body as string;
                return (
                    !body.includes('password') &&
                    !body.includes('totp_secret') &&
                    !body.includes('hashed_password')
                );
            },
        });

        const querySearch = http.get(`${BASE_URL}/portfolio/users/search?query=test`, {
            ...h,
            tags: {endpoint: 'users_search'},
        });
        check(querySearch, {'users/search query 200': (r) => r.status === 200});
    });

    sleep(0.5);

    sleep(1);
}
