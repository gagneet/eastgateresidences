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
        load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 5},
                {duration: '30s', target: 5},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'load'},
        },
    },
    thresholds: {
        // Aggregation endpoints are heavier — allow up to 1s
        'http_req_duration{endpoint:summary}': ['p(95)<1000'],
        'http_req_duration{endpoint:building_overview}': ['p(95)<1500'],
        'http_req_duration{endpoint:levy_kpi}': ['p(95)<1000'],
        'http_req_duration{endpoint:unit_levy_ledger}': ['p(95)<1000'],
        'http_req_duration{endpoint:levy_calculator}': ['p(95)<1500'],
        'http_req_duration{endpoint:charts}': ['p(95)<1500'],
        'http_req_duration{endpoint:arrears}': ['p(95)<1000'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const BUILDING_ID = __ENV.BUILDING_ID || '13195';

const aggregationErrors = new Counter('finance_aggregation_errors');
const ledgerDuration = new Trend('ledger_response_ms');

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
        throw new Error('AUTH_TOKEN env var is required for finance benchmarks');
    }
}

export default function () {
    const h = headers();

    group('Finance Summary Endpoints', () => {
        const summary = http.get(`${BASE_URL}/finance/summary`, {
            ...h,
            tags: {endpoint: 'summary'},
        });
        check(summary, {'finance/summary 200': (r) => r.status === 200}) ||
        aggregationErrors.add(1);

        const overview = http.get(`${BASE_URL}/finance/building-overview`, {
            ...h,
            tags: {endpoint: 'building_overview'},
        });
        check(overview, {'building-overview 200': (r) => r.status === 200}) ||
        aggregationErrors.add(1);

        const kpi = http.get(`${BASE_URL}/finance/levy-kpi`, {
            ...h,
            tags: {endpoint: 'levy_kpi'},
        });
        check(kpi, {'levy-kpi 200': (r) => r.status === 200}) ||
        aggregationErrors.add(1);
    });

    sleep(0.5);

    group('Levy Ledger & Calculator', () => {
        const ledger = http.get(`${BASE_URL}/unit-levy-ledger`, {
            ...h,
            tags: {endpoint: 'unit_levy_ledger'},
        });
        check(ledger, {
            'unit-levy-ledger 200': (r) => r.status === 200,
            'ledger has data': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    return Array.isArray(body) && body.length > 0;
                } catch {
                    return false;
                }
            },
        }) || aggregationErrors.add(1);
        ledgerDuration.add(ledger.timings.duration);

        const calc = http.get(`${BASE_URL}/levy-calculator`, {
            ...h,
            tags: {endpoint: 'levy_calculator'},
        });
        check(calc, {'levy-calculator 200': (r) => r.status === 200}) ||
        aggregationErrors.add(1);
    });

    sleep(0.5);

    group('Charts & Arrears', () => {
        const charts = http.get(`${BASE_URL}/finance/charts`, {
            ...h,
            tags: {endpoint: 'charts'},
        });
        check(charts, {'finance/charts 200': (r) => r.status === 200}) ||
        aggregationErrors.add(1);

        const arrears = http.get(`${BASE_URL}/stats/top-arrears`, {
            ...h,
            tags: {endpoint: 'arrears'},
        });
        check(arrears, {'top-arrears 200': (r) => r.status === 200}) ||
        aggregationErrors.add(1);
    });

    sleep(1);
}

export function teardown() {
    // Read-only benchmark; no test records are created.
}
