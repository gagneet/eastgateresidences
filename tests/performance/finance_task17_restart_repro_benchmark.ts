import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Options} from 'k6/options';
import {Counter} from 'k6/metrics';

// Task #17 restart-correlation test (docs/fixes/finance_task17_summary_shadow_divergence_2026-07-13.md):
// 76% of historical empty-ledger occurrences (107/141) fell within 20 minutes of a
// backend restart; 24% (34) happened hours into stable uptime. This script applies
// LIGHT, realistic concurrent load (matching the "2-3 real users" scale, not a heavy
// burst) sustained across a backend restart triggered mid-run by the operator, to
// test whether the restart itself — not raw concurrency — is the dominant trigger.
//
// Read-only: GET requests only, no records created, teardown() is a no-op.

export const options: Options = {
    scenarios: {
        light_sustained: {
            executor: 'constant-vus',
            vus: 3,
            duration: '25m',
            tags: {scenario: 'task17_restart_repro'},
        },
    },
    thresholds: {
        http_req_failed: ['rate<0.5'], // expect some failures right at the restart moment
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

const emptyLedgerHits = new Counter('task17_empty_ledger_hits');
const httpErrors = new Counter('task17_http_errors');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

export default function () {
    const h = headers();

    group('finance.summary light load across restart', () => {
        const res = http.get(`${BASE_URL}/finance/summary`, {
            ...h,
            tags: {endpoint: 'summary_restart_repro'},
        });

        if (res.status !== 200) {
            httpErrors.add(1);
            console.error(`HTTP ${res.status} at ${new Date().toISOString()}`);
            return;
        }

        let body: any;
        try {
            body = JSON.parse(res.body as string);
        } catch (e) {
            httpErrors.add(1);
            return;
        }

        const summary = body.unit_ledger_summary || {};
        const totalLevied = summary.total_levied;
        const hasAnnualLevyData = body.year && body.year !== 'N/A';
        const isEmptyLedgerBug =
            hasAnnualLevyData &&
            (totalLevied === 0 || totalLevied === undefined || totalLevied === null);

        check(res, {
            'finance/summary: total_levied is non-zero': () => !isEmptyLedgerBug,
        });

        if (isEmptyLedgerBug) {
            emptyLedgerHits.add(1);
            console.error(
                `TASK17 REPRO HIT: year=${body.year} total_levied=${totalLevied} ` +
                `at=${new Date().toISOString()}`
            );
        }
    });

    // ~2-6s between requests per VU — mimics real dashboard usage, not a burst.
    sleep(2 + Math.random() * 4);
}

export function teardown(_data: unknown): void {
    // No-op: read-only script, creates no records.
}
