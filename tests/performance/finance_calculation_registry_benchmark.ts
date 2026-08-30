// @featuretrace:financial-ui-calculation-register — K6 cache-load contract test for finance calculation registry.
// Data flow: k6 -> GET /finance/calculation-registry -> backend registry cache (scope param: building|global).
// Related: backend/routers/finance.py, backend/services/finance_calculation_registry.py
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Options} from 'k6/options';
import {Counter} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        cache_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 10},
                {duration: '20s', target: 10},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'cache_load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:calculation_registry}': ['p(95)<250'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const BUILDING_ID = __ENV.BUILDING_ID;

const registryErrors = new Counter('finance_calculation_registry_errors');

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

export default function () {
    group('Finance calculation registry cache contract', () => {
        const res = http.get(`${BASE_URL}/finance/calculation-registry`, {
            ...headers(),
            tags: {endpoint: 'calculation_registry'},
        });
        const ok = check(res, {
            'registry endpoint returns 200': (r) => r.status === 200,
            'registry version header present': (r) => !!r.headers['X-Finance-Calculation-Registry-Version'],
            'registry has common label groups': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    return body.status === 'contract_cached_values_not_materialised' &&
                        Array.isArray(body.common_label_groups) &&
                        body.common_label_groups.length >= 8;
                } catch {
                    return false;
                }
            },
            'registry states value cache is a future merge path': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    return body.implementation_scope.not_completed.includes(
                        'Heavy financial values are not materialised or shared from a backend value cache yet.',
                    );
                } catch {
                    return false;
                }
            },
            'registry includes Mongo and Postgres source fields': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    const paid = body.common_label_groups.find((g: any) => g.canonical_key === 'levy.ledger_paid');
                    return paid.sources.mongodb.includes('unit_levy_ledger.total_paid') &&
                        paid.sources.postgres.includes('finance.levy_items.paid_cents');
                } catch {
                    return false;
                }
            },
        });
        if (!ok) {
            registryErrors.add(1);
        }
    });
    sleep(0.25);
}

export function teardown() {
    // Read-only benchmark. No test data is created.
}
