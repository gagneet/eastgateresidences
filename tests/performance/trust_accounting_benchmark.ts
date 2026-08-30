import http from 'k6/http';
import {check, group, sleep} from 'k6';
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
        // Trust accounting is strata-manager-facing; realistic concurrent users is low.
        load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 3},
                {duration: '20s', target: 3},
                {duration: '5s', target: 0},
            ],
            tags: {scenario: 'load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:v2_accounts}': ['p(95)<1000'],
        'http_req_duration{endpoint:v2_account_balance}': ['p(95)<1000'],
        'http_req_duration{endpoint:v2_transactions}': ['p(95)<1200'],
        'http_req_duration{endpoint:v2_financial_summary}': ['p(95)<1500'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

const trustErrors = new Counter('trust_v2_errors');
const transactionsDuration = new Trend('trust_v2_transactions_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

function firstAccountId(body: string): string | null {
    try {
        const parsed = JSON.parse(body);
        const accounts = parsed?.data;
        if (!Array.isArray(accounts) || accounts.length === 0) return null;
        return accounts[0]?.id || null;
    } catch {
        return null;
    }
}

export default function () {
    const h = headers();
    let accountId: string | null = null;

    group('Trust V2 Accounts', () => {
        const accounts = http.get(`${BASE_URL}/trust/v2/accounts`, {
            ...h,
            tags: {endpoint: 'v2_accounts'},
        });
        check(accounts, {
            'trust/v2/accounts 200': (r) => r.status === 200,
            'trust/v2/accounts has account list': (r) => {
                accountId = firstAccountId(r.body as string);
                return accountId !== null;
            },
        }) || trustErrors.add(1);

        if (!accountId) return;

        const balance = http.get(`${BASE_URL}/trust/v2/accounts/${accountId}/balance`, {
            ...h,
            tags: {endpoint: 'v2_account_balance'},
        });
        check(balance, {
            'trust/v2 account balance 200': (r) => r.status === 200,
            'trust/v2 account balance has computed balance': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    return typeof body?.data?.computed_balance?.cents === 'number';
                } catch {
                    return false;
                }
            },
        }) || trustErrors.add(1);
    });

    sleep(0.5);

    group('Trust V2 Transactions', () => {
        if (!accountId) return;
        const transactions = http.get(`${BASE_URL}/trust/v2/transactions?trust_account_id=${accountId}&limit=25`, {
            ...h,
            tags: {endpoint: 'v2_transactions'},
        });
        check(transactions, {
            'trust/v2 transactions 200': (r) => r.status === 200,
            'trust/v2 transactions has data array': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    return Array.isArray(body?.data);
                } catch {
                    return false;
                }
            },
        }) || trustErrors.add(1);
        transactionsDuration.add(transactions.timings.duration);
    });

    sleep(0.5);

    group('Trust V2 Summary', () => {
        const summary = http.get(`${BASE_URL}/trust/v2/financial-summary`, {
            ...h,
            tags: {endpoint: 'v2_financial_summary'},
        });
        check(summary, {'trust/v2 financial-summary 200': (r) => r.status === 200}) || trustErrors.add(1);
    });

    sleep(1);
}

export function teardown() {
    // Read-only benchmark; no test data is created.
}
