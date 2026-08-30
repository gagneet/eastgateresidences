import http from 'k6/http';
import {check, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

// Phase E of the PostgreSQL shadow-read expansion (docs/migration/shadow-correction-updates-plan-p1.md,
// -p2.md, -p3.md) — proves the shared Postgres connection pool (backend/db_postgres/engine.py,
// pool_size=5/max_overflow=10 per worker x4 workers, right-sized by commit d4f5cde6 after a prior
// finance-only k6 burst caused 711 pg_unavailable events) can absorb concurrent shadow-comparison
// traffic across finance's 5 contract routes PLUS identity's new routes (GET /users' composition
// instrumentation, wired 2026-07-14) BEFORE any domain is promoted to postgres_shadow (Phase F/G).
//
// Cloned from finance_task17_burst_benchmark.ts (the exact script that reproduced the prior pool
// incident) — read-only GET requests only, creates no records, teardown() is a no-op.
//
// What this script CAN measure directly (k6 built-ins, asserted via thresholds below):
//   - primary-path error rate / latency percentiles per endpoint
//   - absolute request counts and failure counts
//
// What this script CANNOT measure directly (server-side telemetry, not observable from the HTTP
// client) — check these SEPARATELY after the run via:
//   cd backend && source venv/bin/activate
//   python3 scripts/phase_d_shadow_status.py --domain finance_ledger --json
//   python3 scripts/phase_d_shadow_status.py --domain identity_core --json
// and a direct query against core.shadow_read_coverage_daily for the run's coverage_date:
//   pg_unavailable_count, compare_error_count, timeout_count, skipped_capacity_count — all must
//   be 0 (or explained) per Phase E's acceptance criteria. This script's own thresholds only cover
//   what k6 can see from the outside.
//
// Acceptance criteria (recorded here BEFORE running, per the plan — do not judge retrospectively):
//   primary path error rate                 = 0%     (http_req_failed threshold below)
//   primary path p95 regression              <= 10%  (compare http_req_duration p95 against your
//                                                       own pre-change baseline manually — k6
//                                                       thresholds below are absolute, not relative)
//   primary path p99 regression              <= 15%  (same — compare manually against baseline)
//   absolute added p95 latency               <= 50ms (same — compare manually against baseline)
//   pg_unavailable / compare_error / timeout / skipped_capacity = 0  (check via phase_d_shadow_status.py,
//                                                                      see above — not a k6 threshold)

export const options: Options = {
    scenarios: {
        // Steady realistic traffic — sustained moderate load.
        steady: {
            executor: 'constant-vus',
            vus: 10,
            duration: '3m',
            tags: {scenario: 'steady'},
        },
        // Short burst above expected peak — the shape that caused the prior pool incident.
        burst: {
            executor: 'ramping-vus',
            startVUs: 0,
            startTime: '3m', // runs after the steady scenario completes
            stages: [
                {duration: '20s', target: 40},
                {duration: '1m', target: 40},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'burst'},
        },
    },
    thresholds: {
        http_req_failed: ['rate<0.01'], // stricter than task17's 0.05 — this is a capacity proof, not a bug repro
        'http_req_duration{endpoint:finance_summary}': ['p(95)<2000'],
        'http_req_duration{endpoint:finance_building_overview}': ['p(95)<2000'],
        'http_req_duration{endpoint:finance_unit_dashboard}': ['p(95)<2000'],
        'http_req_duration{endpoint:finance_levy_kpi}': ['p(95)<2000'],
        'http_req_duration{endpoint:finance_arrears_detail}': ['p(95)<2000'],
        'http_req_duration{endpoint:users_list}': ['p(95)<2000'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const BUILDING_ID = __ENV.BUILDING_ID || '13195';
const UNIT_NUMBER = __ENV.UNIT_NUMBER || 'TH001'; // override with a real unit for the target building

const httpErrors = new Counter('capacity_http_errors');
const responseLatency = new Trend('capacity_response_latency_ms', true);

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
            // Required for a super_admin token with no building_id JWT claim — matches
            // utils/auth.py's documented fallback chain (X-Building-ID checked before
            // any other resolution for super_admin). Confirmed live 2026-07-14: every
            // request 403'd with "No building context" until this header was added.
            'X-Building-ID': BUILDING_ID,
        },
    };
}

function hit(path: string, endpointTag: string): void {
    const h = headers();
    const res = http.get(`${BASE_URL}${path}`, {...h, tags: {endpoint: endpointTag}});
    responseLatency.add(res.timings.duration, {endpoint: endpointTag});
    if (res.status !== 200) {
        httpErrors.add(1, {endpoint: endpointTag});
    }
    check(res, {[`${endpointTag} 200`]: (r) => r.status === 200});
}

export default function () {
    group('finance contract routes', () => {
        hit('/finance/summary', 'finance_summary');
        hit('/finance/building-overview', 'finance_building_overview');
        hit(`/finance/unit-dashboard-overview/${UNIT_NUMBER}`, 'finance_unit_dashboard');
        hit('/finance/levy-kpi', 'finance_levy_kpi');
        hit('/arrears/detail', 'finance_arrears_detail'); // no /finance prefix — confirmed at routers/finance.py:6521
    });

    group('identity routes', () => {
        hit('/users', 'users_list');
    });
}

export function teardown(_data: unknown): void {
    // No-op: this script only issues GET requests and creates no records, so there is
    // nothing to clean up. Server-side shadow-comparison/coverage telemetry produced as a
    // *side effect* of these GETs is real data in core.shadow_diffs /
    // core.shadow_read_coverage_daily — check it via phase_d_shadow_status.py (see header
    // comment) rather than deleting it here; it's the evidence this test exists to produce.
}
