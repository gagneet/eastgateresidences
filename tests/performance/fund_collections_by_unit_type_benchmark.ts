import http from 'k6/http';
import {check} from 'k6';
import {Options} from 'k6/options';

// GAP-FIN-035 Item 3 — GET /finance/fund-collections-by-unit-type.
// Read-only report endpoint (no writes), so teardown() is a required but
// intentional no-op per tests/README.md's k6 convention — nothing is created.

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:fund_collections_by_unit_type}': ['p(95)<800'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
        tags: {endpoint: 'fund_collections_by_unit_type'},
    };
}

export default function () {
    const res = http.get(`${BASE_URL}/finance/fund-collections-by-unit-type`, headers());
    check(res, {
        'status is 200': (r) => r.status === 200,
        'has admin_fund': (r) => {
            try {
                return typeof JSON.parse(r.body as string).admin_fund === 'object';
            } catch {
                return false;
            }
        },
    });
}

export function teardown(_data: unknown): void {
    // No-op: this endpoint is read-only and creates no records to clean up.
}
