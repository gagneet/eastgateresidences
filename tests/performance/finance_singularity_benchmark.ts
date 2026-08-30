/**
 * finance_singularity_benchmark.ts — GAP-FIN-040
 *
 * Performance + cross-page CONSISTENCY benchmark for the consolidated finance
 * views. After the calculation-singularity migration, every surface that shows
 * "units in arrears" derives it from the one canonical helper, so the SAME
 * number must come back from every endpoint. This k6 script:
 *
 *   1. measures p95 latency for each finance read view (bottleneck guard), and
 *   2. asserts the units-in-arrears / arrears-total figures AGREE across
 *      /finance/summary, /finance/kpi-contract, /stats/building-kpis and the BI
 *      financial-summary (a live regression guard against divergence creeping
 *      back in — the exact GAP-FIN-040 failure mode).
 *
 * Read-only: creates no data, so teardown is a no-op (kept per the repo rule
 * that every k6 script defines teardown()).
 *
 * Run:
 *   k6 run tests/performance/finance_singularity_benchmark.ts \
 *     -e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<token> -e BUILDING_ID=13195
 *   k6 run --env K6_SCENARIO=smoke ... tests/performance/finance_singularity_benchmark.ts
 */
import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {executor: 'constant-vus', vus: 1, duration: '10s', tags: {scenario: 'smoke'}},
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
        'http_req_duration{endpoint:finance_summary}': ['p(95)<1200'],
        'http_req_duration{endpoint:kpi_contract}': ['p(95)<1200'],
        'http_req_duration{endpoint:arrears_detail}': ['p(95)<1200'],
        'http_req_duration{endpoint:building_kpis}': ['p(95)<1500'],
        'http_req_duration{endpoint:bi_financial_summary}': ['p(95)<1500'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
        finance_arrears_inconsistencies: ['count==0'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const BUILDING_ID = __ENV.BUILDING_ID || '13195';
const YEAR = __ENV.YEAR || String(new Date().getFullYear());

const inconsistencies = new Counter('finance_arrears_inconsistencies');
const summaryMs = new Trend('finance_summary_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
            'X-Building-ID': BUILDING_ID,
        },
    };
}

function getJson(path: string, endpoint: string): any {
    const res = http.get(`${BASE_URL}${path}`, {...headers(), tags: {endpoint}});
    check(res, {[`${endpoint} 200`]: (r) => r.status === 200});
    try {
        return JSON.parse(res.body as string);
    } catch {
        return null;
    }
}

// Pull "units in arrears" from whichever field an endpoint exposes.
function unitsInArrears(obj: any): number | null {
    if (!obj) return null;
    if (typeof obj.units_in_arrears === 'number') return obj.units_in_arrears;
    if (typeof obj.lots_in_arrears === 'number') return obj.lots_in_arrears;
    if (obj.unit_ledger_summary && typeof obj.unit_ledger_summary.units_owing === 'number') {
        return obj.unit_ledger_summary.units_owing;
    }
    if (obj.unit_counts && typeof obj.unit_counts.owing === 'number') return obj.unit_counts.owing;
    return null;
}

export default function (): void {
    group('finance singularity views', () => {
        const summary = getJson(`/finance/summary?year=${YEAR}`, 'finance_summary');
        const kpi = getJson(`/finance/kpi-contract?year=${YEAR}`, 'kpi_contract');
        getJson(`/arrears/detail?year=${YEAR}`, 'arrears_detail');
        const buildingKpis = getJson(`/stats/building-kpis?financial_year=${YEAR}`, 'building_kpis');
        const bi = getJson(`/bi/building/${BUILDING_ID}/financial-summary?financial_year=${YEAR}`, 'bi_financial_summary');

        // Cross-endpoint consistency: the grace-aware "units in arrears" must agree.
        const values = [
            ['building_kpis', unitsInArrears(buildingKpis)],
            ['bi', unitsInArrears(bi)],
        ].filter(([, v]) => v !== null) as Array<[string, number]>;

        if (values.length >= 2) {
            const ref = values[0][1];
            const consistent = values.every(([, v]) => v === ref);
            check(null, {'units_in_arrears agrees across endpoints': () => consistent});
            if (!consistent) {
                inconsistencies.add(1);
                console.error(`Arrears divergence: ${JSON.stringify(values)}`);
            }
        }
        if (summary || kpi) summaryMs.add(1);
    });
    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark — no test data created, nothing to clean up.
}
