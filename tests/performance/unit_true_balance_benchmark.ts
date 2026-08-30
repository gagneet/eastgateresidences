import http from 'k6/http';
import {check} from 'k6';
import {Options} from 'k6/options';

// GAP-FIN-036 / GAP-ONBOARD-004 A1 — GET /finance/unit-dashboard-overview/{unit}, which now
// carries the additive receipts-aware true-balance enrichment (unapplied_credit / true_balance).
// The enrichment adds two SELECTs on an already-open, tenant-scoped session inside the PG branch,
// so this benchmark guards that the per-unit dashboard read stays fast under the extra queries.
// Read-only endpoint (no writes), so teardown() is a required but intentional no-op per
// tests/README.md's k6 convention — nothing is created.

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        load: {
            executor: 'ramping-vus',
            startVUs: 1,
            stages: [
                {duration: '15s', target: 10},
                {duration: '30s', target: 10},
                {duration: '10s', target: 0},
            ],
            startTime: '12s',
            tags: {scenario: 'load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:unit_dashboard_overview}': ['p(95)<900'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
// A unit number that exists for the test building; overridable per-env.
const UNIT = __ENV.UNIT_NUMBER || 'UA001';

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
        tags: {endpoint: 'unit_dashboard_overview'},
    };
}

export default function () {
    const res = http.get(
        `${BASE_URL}/finance/unit-dashboard-overview/${encodeURIComponent(UNIT)}`,
        headers(),
    );
    check(res, {
        'status is 200': (r) => r.status === 200,
        // The additive fields must be present (value may be null == unknown, never fabricated $0).
        'has true_balance key': (r) => {
            try {
                return 'true_balance' in JSON.parse(r.body as string);
            } catch {
                return false;
            }
        },
        'has unapplied_credit key': (r) => {
            try {
                return 'unapplied_credit' in JSON.parse(r.body as string);
            } catch {
                return false;
            }
        },
    });
}

export function teardown(_data: unknown): void {
    // No-op: this endpoint is read-only and creates no records to clean up.
}
