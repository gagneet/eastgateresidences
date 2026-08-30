// @featuretrace:financial-forecast — K6 benchmark for the finance intelligence read surface.
// Data flow: k6 -> /api/finance/{forecast,anomalies,health,cashflow,...} read contracts
//            -> backend/routers/finance_intelligence.py -> forecast/anomaly services.
// Related: frontend/src/app/(app)/intelligence/financial/page.tsx
//          backend/routers/finance_intelligence.py
//
// Zero-coverage gap closed: finance_intelligence.py had 19 routes with no
// performance benchmark. Anomaly scans, cashflow, and sinking-fund projections
// run multi-year aggregations — the reads most sensitive to ledger growth.
//
// Read-only: only GET probes. Forecast/anomaly-scan/lot-summary-compute POSTs
// (which recompute and persist cached intelligence) are excluded.
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {executor: 'constant-vus', vus: 1, duration: '10s', tags: {scenario: 'smoke'}},
        finance_intel_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 3},
                {duration: '30s', target: 3},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'finance_intel_load'},
        },
    },
    thresholds: {
        'http_req_duration{surface:fi_core}': ['p(95)<1500'],
        'http_req_duration{surface:fi_projection}': ['p(95)<2500'],
        finance_intelligence_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';
const LOT_NUMBER = __ENV.LOT_NUMBER || '';
// Most /finance/* reads REQUIRE a `year` query param and return 422 without one.
// Defaulting keeps the benchmark meaningful when the caller does not pass FY.
const YEAR = __ENV.YEAR || String(new Date().getFullYear());

const requiredErrors = new Counter('finance_intelligence_required_errors');
const endpointDuration = new Trend('finance_intelligence_endpoint_ms');

function headers() {
    const h: Record<string, string> = {Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json'};
    if (BUILDING_ID) {
        h['X-Building-ID'] = BUILDING_ID;
    }
    return {headers: h};
}

function probe(path: string, name: string, surface: string, required: boolean) {
    // Append `year` unless the caller already encoded one.
    //
    // WHY: measured 2026-08-25, this benchmark was sending no `year` at all, so SEVEN
    // of its ten probes returned 422 VALIDATION_ERROR ("Field required: year") — a 70%
    // http_req_failed rate, checks at 40%, and 468 required-errors, while every latency
    // threshold passed comfortably. It had therefore never exercised the endpoints it
    // claims to cover: it was timing the framework's own validation rejection, not the
    // multi-year aggregations described in this file's header. Confirmed by hand:
    // all seven return 200 once `year` is supplied.
    const sep = path.includes('?') ? '&' : '?';
    const url = /[?&]year=/.test(path) ? `${BASE_URL}${path}` : `${BASE_URL}${path}${sep}year=${encodeURIComponent(YEAR)}`;
    const res = http.get(url, {...headers(), tags: {endpoint: name, surface}});
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
        throw new Error('AUTH_TOKEN env var is required for finance intelligence benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for finance intelligence probes.');
    }
}

export default function () {
    group('fi_core', () => {
        probe('/finance/forecast', 'forecast', 'fi_core', true);
        probe('/finance/anomalies', 'anomalies', 'fi_core', true);
        probe('/finance/health', 'financial_health', 'fi_core', true);
        probe('/finance/lot-summary', 'lot_summary', 'fi_core', true);
        probe('/finance/documents', 'documents', 'fi_core', false);
        if (LOT_NUMBER) {
            probe(`/finance/lot-summary/${encodeURIComponent(LOT_NUMBER)}`, 'lot_summary_one', 'fi_core', false);
        }
    });

    sleep(0.5);

    group('fi_projection', () => {
        probe('/finance/levy-projection', 'levy_projection', 'fi_projection', true);
        probe('/finance/cashflow', 'cashflow', 'fi_projection', true);
        probe('/finance/sinking-fund-projection', 'sinking_fund_projection', 'fi_projection', true);
        probe('/finance/sinking-fund-plan', 'sinking_fund_plan', 'fi_projection', true);
        probe('/finance/levy-simulator', 'levy_simulator', 'fi_projection', false);
    });

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No forecasts or anomaly scans are persisted.
}
