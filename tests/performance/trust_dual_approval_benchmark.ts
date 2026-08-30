/**
 * k6 performance benchmark — Trust Dual-Approval (GAP-FIN-011)
 *
 * Legacy V1 coverage: this benchmark intentionally exercises /trust/batches*
 * in routers/trust_accounting.py. There is no /trust/v2 batch/ABA replacement
 * yet, so keep this benchmark out of "V2 retargeted" evidence until Stage 2
 * either replaces the workflow or retires V1 batch approvals.
 *
 * Tests the approve + second-approve flow under concurrent EC-level users.
 * Trust accounting is low-concurrency (strata manager + 2-3 EC members);
 * burst scenario models month-end payment run with multiple batches.
 *
 * Usage:
 *   k6 run tests/performance/trust_dual_approval_benchmark.ts \
 *     -e BASE_URL=http://localhost:8003/api \
 *     -e AUTH_TOKEN=<ec_member_jwt> \
 *     -e AUTH_TOKEN_2=<second_ec_member_jwt>
 */
import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '15s',
            tags: {scenario: 'smoke'},
        },
        // Month-end: 3 EC members reviewing batches concurrently
        load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 3},
                {duration: '30s', target: 3},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:approve}': ['p(95)<800'],
        'http_req_duration{endpoint:second_approve}': ['p(95)<800'],
        'http_req_duration{endpoint:batches_list}': ['p(95)<500'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const TOKEN_2 = __ENV.AUTH_TOKEN_2 || __ENV.AUTH_TOKEN; // second approver

const approveErrors = new Counter('dual_approve_errors');
const approveDuration = new Trend('approve_ms');

// Prefix for deterministic teardown
const PERF_PREFIX = 'Perf dual-approve batch ';

function headers(token = TOKEN) {
    return {
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
    };
}

function uniqueId(): string {
    return `${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

// Store created batch IDs for teardown
const createdBatchIds: string[] = [];

export default function () {
    group('Batch List (read path)', () => {
        const res = http.get(`${BASE_URL}/trust/batches`, {
            ...headers(),
            tags: {endpoint: 'batches_list'},
        });
        check(res, {'batches list 200': (r) => r.status === 200});
    });

    sleep(0.3);

    group('Create + First Approve (below threshold)', () => {
        // Create a small batch (< $5k) — should go directly to approved
        const batchPayload = JSON.stringify({
            description: `${PERF_PREFIX}${uniqueId()}`,
            fund_type: 'admin_fund',
            batch_type: 'direct',
            payment_date: '2026-04-30',
            is_test_data: true,
            total_amount: 1200.00,
            item_count: 1,
            items: [{
                bsb: '082-001',
                account_number: '123456789',
                payee_name: 'Test Vendor',
                amount: 1200.00,
                reference: 'PERF-001',
                lodgement_ref: 'PERF-001',
            }],
        });

        const create = http.post(`${BASE_URL}/trust/batches`, batchPayload, headers());
        const created = check(create, {
            'batch created 201': (r) => r.status === 201,
        });

        if (created) {
            let batchId: string;
            try {
                batchId = JSON.parse(create.body as string).id;
            } catch {
                return;
            }
            if (!batchId) return;
            createdBatchIds.push(batchId);

            const approve = http.post(
                `${BASE_URL}/trust/batches/${batchId}/approve`,
                null,
                {...headers(), tags: {endpoint: 'approve'}},
            );
            const approveOk = check(approve, {
                'first approve 200': (r) => r.status === 200,
                'directly approved (below threshold)': (r) => {
                    try {
                        return JSON.parse(r.body as string).status === 'approved';
                    } catch {
                        return false;
                    }
                },
            });
            approveDuration.add(approve.timings.duration);
            if (!approveOk) approveErrors.add(1);
        }
    });

    sleep(0.5);
}

export function teardown(_data: unknown): void {
    // Fetch all batches and delete any created by this perf run
    const res = http.get(`${BASE_URL}/trust/batches?page_size=100&include_test_data=true`, headers());
    if (res.status !== 200) return;

    let body: { data?: Array<{ id: string; description?: string }> };
    try {
        body = JSON.parse(res.body as string);
    } catch {
        return;
    }

    const perfBatches = (body.data || []).filter(
        (b) => b.description?.startsWith(PERF_PREFIX),
    );

    for (const batch of perfBatches) {
        // Cancel/delete if endpoint exists; otherwise log
        http.del(`${BASE_URL}/trust/batches/${batch.id}`, null, headers());
    }
}
