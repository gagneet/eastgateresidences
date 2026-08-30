// @featuretrace:demo_bank — k6 read-path benchmark for Demo Bank, Financial Import, and Strata Sync.
// Layer: test
// Data flow: this script -> GET /demo-bank/*, /financial-import/history, /strata/sync/* -> asserts
//            status + p(95) latency thresholds. Read-only: creates no records, so teardown() is a
//            documented no-op rather than a cleanup call (see its own comment for why).
// Related: backend/routers/demo_bank.py
//          backend/routers/financial_import.py
//          backend/routers/strata_sync.py
// Tests: tests/performance/maintenance_benchmark.ts (canonical k6 reference pattern)
//
// Run against local backend:
//   k6 run tests/performance/demo_integrations_benchmark.ts -e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<token>
// Smoke only:
//   k6 run --env BASE_URL=... --env AUTH_TOKEN=... -e K6_SCENARIO=smoke tests/performance/demo_integrations_benchmark.ts
// Optional: set DEMO_BANK_ACCOUNT_REF to also benchmark the account-scoped balance/transactions
// endpoints (skipped if unset, since a valid account_ref is building/environment-specific).

import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Options} from 'k6/options';

const SCENARIO = __ENV.K6_SCENARIO;

export const options: Options = {
    scenarios: SCENARIO === 'smoke'
        ? {
            smoke: {
                executor: 'constant-vus',
                vus: 1,
                duration: '10s',
                tags: {scenario: 'smoke'},
            },
        }
        : {
            smoke: {
                executor: 'constant-vus',
                vus: 1,
                duration: '10s',
                tags: {scenario: 'smoke'},
            },
            // Simulate an admin dashboard being open by several managers/EC members at once
            // while a scrape or CSV import is in flight.
            burst: {
                executor: 'ramping-vus',
                startVUs: 0,
                stages: [
                    {duration: '10s', target: 8},
                    {duration: '20s', target: 8},
                    {duration: '10s', target: 0},
                ],
                tags: {scenario: 'burst'},
            },
        },
    thresholds: {
        'http_req_duration{endpoint:demo_bank_accounts}': ['p(95)<800'],
        'http_req_duration{endpoint:demo_bank_import_batches}': ['p(95)<800'],
        'http_req_duration{endpoint:demo_bank_supported_banks}': ['p(95)<300'],
        'http_req_duration{endpoint:financial_import_history}': ['p(95)<800'],
        'http_req_duration{endpoint:strata_sync_latest}': ['p(95)<500'],
        'http_req_duration{endpoint:strata_sync_financials}': ['p(95)<1000'],
        'http_req_duration{endpoint:strata_sync_owners}': ['p(95)<1000'],
        'http_req_duration{endpoint:strata_sync_summary}': ['p(95)<1000'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const ACCOUNT_REF = __ENV.DEMO_BANK_ACCOUNT_REF;

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

// Read-only benchmark — no records are created by this script, so there is nothing for
// teardown() to delete. Kept as an explicit no-op (rather than omitted) per this repo's
// "every k6 script MUST include a teardown()" convention (CLAUDE.md, tests/README.md).
export function teardown(_data: unknown): void {
    // Intentionally empty.
}

export default function () {
    const h = headers();

    group('Demo Bank — read paths', () => {
        const accounts = http.get(`${BASE_URL}/demo-bank/accounts`, {
            ...h,
            tags: {endpoint: 'demo_bank_accounts'},
        });
        check(accounts, {'GET /demo-bank/accounts 200': (r) => r.status === 200});

        const batches = http.get(`${BASE_URL}/demo-bank/import-batches`, {
            ...h,
            tags: {endpoint: 'demo_bank_import_batches'},
        });
        check(batches, {'GET /demo-bank/import-batches 200': (r) => r.status === 200});

        const banks = http.get(`${BASE_URL}/demo-bank/supported-banks`, {
            ...h,
            tags: {endpoint: 'demo_bank_supported_banks'},
        });
        check(banks, {'GET /demo-bank/supported-banks 200': (r) => r.status === 200});

        if (ACCOUNT_REF) {
            const balance = http.get(`${BASE_URL}/demo-bank/accounts/${ACCOUNT_REF}/balance`, {
                ...h,
                tags: {endpoint: 'demo_bank_account_balance'},
            });
            check(balance, {'GET /demo-bank/accounts/{ref}/balance 200': (r) => r.status === 200});

            const txns = http.get(`${BASE_URL}/demo-bank/accounts/${ACCOUNT_REF}/transactions`, {
                ...h,
                tags: {endpoint: 'demo_bank_account_transactions'},
            });
            check(txns, {'GET /demo-bank/accounts/{ref}/transactions 200': (r) => r.status === 200});
        }
    });

    sleep(0.3);

    group('Financial Import — read paths', () => {
        const history = http.get(`${BASE_URL}/financial-import/history`, {
            ...h,
            tags: {endpoint: 'financial_import_history'},
        });
        check(history, {'GET /financial-import/history 200': (r) => r.status === 200});
    });

    sleep(0.3);

    group('Strata Sync — read paths', () => {
        const latest = http.get(`${BASE_URL}/strata/sync/latest`, {
            ...h,
            tags: {endpoint: 'strata_sync_latest'},
        });
        check(latest, {'GET /strata/sync/latest 200 or 404': (r) => r.status === 200 || r.status === 404});

        const financials = http.get(`${BASE_URL}/strata/financials`, {
            ...h,
            tags: {endpoint: 'strata_sync_financials'},
        });
        check(financials, {'GET /strata/financials 200': (r) => r.status === 200});

        const owners = http.get(`${BASE_URL}/strata/owners`, {
            ...h,
            tags: {endpoint: 'strata_sync_owners'},
        });
        check(owners, {'GET /strata/owners 200': (r) => r.status === 200});

        const summary = http.get(`${BASE_URL}/strata/summary`, {
            ...h,
            tags: {endpoint: 'strata_sync_summary'},
        });
        check(summary, {'GET /strata/summary 200': (r) => r.status === 200});
    });

    sleep(1);
}
