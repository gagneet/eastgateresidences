import http from 'k6/http';
import {check, group} from 'k6';
import {Options} from 'k6/options';
import {Counter} from 'k6/metrics';

// Task #17 (docs/fixes/finance_task17_summary_shadow_divergence_2026-07-13.md):
// GET /finance/summary occasionally returns unit_ledger_summary.total_levied: 0
// for a building/year that genuinely has 87 populated unit_levy_ledger rows.
// 8 synthetic reproduction attempts at lower concurrency did not trigger it.
// This script forces higher, sustained concurrency against the live backend
// and checks the RESPONSE BODY itself for the empty-ledger signature — this
// works independent of whether the routers/finance.py diagnostic logging
// (overlapping_summary_calls_5s) has been deployed yet.
//
// Read-only: GET requests only, creates no records, so no teardown() cleanup
// is required — included below as a no-op to satisfy the project convention.

export const options: Options = {
    scenarios: {
        burst: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '20s', target: 40},
                {duration: '3m', target: 40},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'task17_burst'},
        },
    },
    thresholds: {
        http_req_failed: ['rate<0.05'],
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

    group('finance.summary burst (current year, no explicit ?year=)', () => {
        const res = http.get(`${BASE_URL}/finance/summary`, {
            ...h,
            tags: {endpoint: 'summary_burst'},
        });

        if (res.status !== 200) {
            httpErrors.add(1);
            check(res, {'finance/summary 200': () => false});
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

        // The bug signature: annual_levies WAS found (year resolved, admin_fund/
        // sinking_fund populated) but total_levied is exactly 0/undefined.
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
                `admin_fund=${JSON.stringify(body.admin_fund)} ` +
                `at=${new Date().toISOString()}`
            );
        }
    });
}

export function teardown(_data: unknown): void {
    // No-op: this script only issues GET /finance/summary requests and creates
    // no records, so there is nothing to clean up.
}
