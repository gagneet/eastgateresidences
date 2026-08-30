// @featuretrace:smart-request — k6 performance benchmark for the request queue read path.
// Layer: test
// Data flow: k6 → GET /workflow-requests[?status=overdue] + /workflow-requests/stats/triage
//            + /engagement/morning-card → workflow_requests (building-scoped).
// Related: backend/routers/workflow_requests.py
//          backend/services/morning_card_service.py
//          backend/scripts/cleanup_perf_test_workflow_requests.py
//
// Covers the endpoints behind the Management dashboard's "N requests past SLA" card and
// the request tracking list it links to. The card's count and the queue's contents are
// produced by two different queries that must agree, so both are exercised together and
// their agreement is asserted, not just their latency.
//
// Usage:
//   k6 run tests/performance/workflow_requests_benchmark.ts \
//     -e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<super_admin JWT>
//
//   # single scenario
//   k6 run -e K6_SCENARIO=smoke ... tests/performance/workflow_requests_benchmark.ts
//
// AUTH_TOKEN must belong to a super_admin: setup() seeds via
// POST /workflow-requests/smart with is_test_data=true, and the router's
// _can_mark_test_data() grants that flag to super_admin ONLY. With any other role the
// flag is silently dropped and the run would write unflagged rows into a real building's
// live queue — so the token role is asserted before a single record is created.

import http from 'k6/http';
import { check, fail } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const BASE_URL: string = (__ENV.BASE_URL || 'http://localhost:8003/api').replace(/\/$/, '');
const AUTH_TOKEN: string = __ENV.AUTH_TOKEN || '';
const BUILDING_ID: string = __ENV.BUILDING_ID || '';
const SEED_COUNT: number = Number(__ENV.SEED_COUNT || 15);

// Deterministic prefix so cleanup can filter precisely and can never match a real
// resident's request. Mirrors the "Perf test request perf-" convention already used by
// backend/scripts/cleanup_perf_test_maintenance.py.
const PERF_SUBJECT_PREFIX = 'Perf test workflow-request perf-';

const queueLatency = new Trend('wr_queue_latency', true);
const overdueLatency = new Trend('wr_overdue_latency', true);
const triageLatency = new Trend('wr_triage_latency', true);
const morningCardLatency = new Trend('wr_morning_card_latency', true);
// A card that promises "N past SLA" while the queue it links to returns a different
// count is the exact defect this feature's fixes addressed; a perf run is a cheap place
// to keep watching for it under concurrency.
const cardQueueAgreement = new Rate('wr_card_queue_agreement');

function headers() {
    const h: Record<string, string> = {
        Authorization: `Bearer ${AUTH_TOKEN}`,
        'Content-Type': 'application/json',
    };
    // Super admins select a building with X-Building-ID; without it the JWT/user
    // fallback chain decides, which is fine for a single-building token.
    if (BUILDING_ID) h['X-Building-ID'] = BUILDING_ID;
    return h;
}

const ALL_SCENARIOS = {
    smoke: {
        executor: 'shared-iterations',
        vus: 1,
        iterations: 5,
        maxDuration: '1m',
        tags: { scenario: 'smoke' },
    },
    read_load: {
        executor: 'ramping-vus',
        startVUs: 1,
        stages: [
            { duration: '30s', target: 10 },
            { duration: '1m', target: 10 },
            { duration: '15s', target: 0 },
        ],
        tags: { scenario: 'read_load' },
    },
};

const selected = __ENV.K6_SCENARIO;
if (selected && !(selected in ALL_SCENARIOS)) {
    fail(`Unknown K6_SCENARIO "${selected}". Valid: ${Object.keys(ALL_SCENARIOS).join(', ')}`);
}

export const options = {
    scenarios: selected
        ? { [selected]: (ALL_SCENARIOS as Record<string, unknown>)[selected] }
        : ALL_SCENARIOS,
    thresholds: {
        // The queue is a manager's first screen of the day; anything slower than this
        // reads as a broken page rather than a slow one.
        wr_queue_latency: ['p(95)<800'],
        wr_overdue_latency: ['p(95)<800'],
        wr_triage_latency: ['p(95)<1000'],
        wr_morning_card_latency: ['p(95)<1000'],
        wr_card_queue_agreement: ['rate>0.99'],
        http_req_failed: ['rate<0.01'],
    },
};

interface SeedState {
    ids: string[];
    seeded: number;
}

export function setup(): SeedState {
    if (!AUTH_TOKEN) fail('AUTH_TOKEN is required. Pass -e AUTH_TOKEN=<super_admin JWT>.');

    // Role gate before any write: see the header note on _can_mark_test_data.
    const me = http.get(`${BASE_URL}/auth/me`, { headers: headers() });
    if (me.status !== 200) fail(`AUTH_TOKEN rejected by /auth/me (HTTP ${me.status}).`);
    const role = (me.json('effective_role') as string) || (me.json('role') as string) || '';
    if (role !== 'super_admin') {
        fail(
            `AUTH_TOKEN role is "${role}", but is_test_data can only be set by super_admin. ` +
            'Running as any other role would write unflagged perf records into a live queue.',
        );
    }

    const ids: string[] = [];
    for (let i = 0; i < SEED_COUNT; i++) {
        const res = http.post(
            `${BASE_URL}/workflow-requests/smart`,
            JSON.stringify({
                subject: `${PERF_SUBJECT_PREFIX}${i}`,
                body: 'Synthetic load-test request. Safe to remove.',
                is_test_data: true,
            }),
            { headers: headers() },
        );
        if (res.status !== 200) {
            fail(`Seeding failed at record ${i} (HTTP ${res.status}): ${res.body}`);
        }
        const id = res.json('id') as string;
        // A record that came back without is_test_data set means the flag was dropped —
        // stop immediately rather than accumulate unremovable live rows.
        if (res.json('is_test_data') === false) {
            fail(`Record ${id} was created WITHOUT is_test_data. Aborting before more are written.`);
        }
        ids.push(id);
    }
    return { ids, seeded: ids.length };
}

export default function () {
    const h = headers();

    const queue = http.get(`${BASE_URL}/workflow-requests`, { headers: h, tags: { ep: 'queue' } });
    queueLatency.add(queue.timings.duration);
    check(queue, { 'queue 200': (r) => r.status === 200 });

    const overdue = http.get(`${BASE_URL}/workflow-requests?status=overdue`, {
        headers: h,
        tags: { ep: 'overdue' },
    });
    overdueLatency.add(overdue.timings.duration);
    check(overdue, { 'overdue 200': (r) => r.status === 200 });

    const triage = http.get(`${BASE_URL}/workflow-requests/stats/triage`, {
        headers: h,
        tags: { ep: 'triage' },
    });
    triageLatency.add(triage.timings.duration);
    check(triage, { 'triage 200': (r) => r.status === 200 });

    const card = http.get(`${BASE_URL}/engagement/morning-card`, {
        headers: h,
        tags: { ep: 'morning_card' },
    });
    morningCardLatency.add(card.timings.duration);
    check(card, { 'morning-card 200': (r) => r.status === 200 });

    // Cross-endpoint invariant: when the morning card reports an SLA breach count, the
    // overdue queue it links to must be able to show that many rows. The card counts
    // Mongo directly while the queue goes through the router's own filter, so this is a
    // genuine two-source comparison rather than a tautology.
    if (card.status === 200 && overdue.status === 200) {
        const cardType = card.json('card_type') as string;
        if (cardType === 'sla_breach_manager') {
            const title = String(card.json('title') || '');
            const claimed = parseInt(title, 10);
            const overdueBody = overdue.json();
            const actual = Array.isArray(overdueBody) ? overdueBody.length : -1;
            // The card counts every breach; the queue response may be capped by ?limit=,
            // which this script never sets — so an exact match is the correct assertion.
            cardQueueAgreement.add(Number.isNaN(claimed) ? false : claimed === actual);
        } else {
            // No SLA card means no claim to contradict — neutral, not a failure.
            cardQueueAgreement.add(true);
        }
    }
}

export function teardown(data: SeedState): void {
    // Deliberately NOT an API delete, and deliberately not a status close either:
    //
    //  * workflow_requests has NO delete endpoint. Records are retained for 7 years
    //    under ACT/NSW rules (see CLAUDE.md "Building Soft-Archive — Never Hard Delete"),
    //    so one is not going to be added for a benchmark.
    //  * PUT /workflow-requests/{id}/status looks the record up with
    //    {"is_test_data": {"$ne": True}} and therefore returns 404 for every record this
    //    script creates. Closing them through the API is impossible by construction.
    //
    // What actually keeps these rows out of every user-facing surface is the
    // is_test_data flag itself, which production queries filter on. So teardown verifies
    // that invariant rather than pretending to delete, and prints the exact command that
    // does remove the rows. Silence here would be the real failure mode: a teardown that
    // 404s on every call and reports success looks identical to one that worked.
    if (!data || !data.ids || data.ids.length === 0) return;

    const res = http.get(`${BASE_URL}/workflow-requests`, { headers: headers() });
    let leaked = 0;
    if (res.status === 200) {
        const body = res.json();
        if (Array.isArray(body)) {
            leaked = body.filter(
                (r: Record<string, unknown>) =>
                    typeof r.subject === 'string' && r.subject.startsWith(PERF_SUBJECT_PREFIX),
            ).length;
        }
    }

    console.log(
        `[teardown] seeded ${data.seeded} request(s) with is_test_data=true, prefix "${PERF_SUBJECT_PREFIX}".`,
    );
    if (leaked > 0) {
        console.error(
            `[teardown] FAILED INVARIANT: ${leaked} perf record(s) are visible in the live queue. ` +
            'is_test_data filtering is not working — investigate before running this again.',
        );
    } else {
        console.log('[teardown] verified: none are visible in the live request queue.');
    }
    console.log(
        '[teardown] to remove the rows from the database:\n' +
        '           cd backend && python3 scripts/cleanup_perf_test_workflow_requests.py --dry-run\n' +
        '           cd backend && python3 scripts/cleanup_perf_test_workflow_requests.py',
    );
}
