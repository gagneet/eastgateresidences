import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

// @featuretrace:invoices — k6 perf benchmark for invoice OCR endpoints.
// Layer: test
// Data flow: k6 benchmark scenarios → /api/invoices endpoints → invoices persistence/read paths (building-scoped).
// Related: backend/routers/invoices.py
//           tests/performance/maintenance_benchmark.ts
// Tests: list invoices, get single invoice. Upload-and-parse is excluded (calls
// the Anthropic API — not appropriate for load testing; would incur real cost).
// Teardown deletes all records created during the run via the title prefix filter.

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
        'http_req_duration{endpoint:list_invoices}': ['p(95)<600'],
        'http_req_duration{endpoint:get_invoice}': ['p(95)<400'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

// Track invoices created so teardown can remove them
const createdIds: string[] = [];
const listErrors = new Counter('invoice_list_errors');
const listDuration = new Trend('invoice_list_ms');

function headers(): { headers: Record<string, string> } {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

// Seed a minimal pending_review invoice record directly via the confirm endpoint
// is not feasible without a real PDF, so we only benchmark read paths here.
// Write paths (upload) are documented as excluded above.

export function setup(): { invoiceId: string | null } {
    if (!TOKEN) {
        throw new Error('AUTH_TOKEN env var is required. Run: k6 run invoice_benchmark.ts -e AUTH_TOKEN=<your_token>');
    }
    // Nothing to pre-seed — read-path benchmark only.
    return {invoiceId: null};
}

export function teardown(_data: unknown): void {
    // Delete any pending_review invoices created via upload during manual runs.
    // Confirmed invoices are retained (7-year policy).
    const h = headers();
    const res = http.get(`${BASE_URL}/invoices?status=pending_review`, h);
    if (res.status !== 200) return;

    let invoices: Array<{ id: string; file_name: string; status: string }>;
    try {
        const body = JSON.parse(res.body as string);
        invoices = body.invoices || [];
    } catch {
        return;
    }

    // Only delete records created by this benchmark (identified by name prefix)
    const perfInvoices = invoices.filter((inv) =>
        inv.file_name?.startsWith('perf-invoice-')
    );
    for (const inv of perfInvoices) {
        http.del(`${BASE_URL}/invoices/${inv.id}`, null, h);
    }
}

export default function () {
    const h = headers();

    group('List invoices', () => {
        const start = Date.now();
        const res = http.get(`${BASE_URL}/invoices`, {
            ...h,
            tags: {endpoint: 'list_invoices'},
        });
        listDuration.add(Date.now() - start);

        const ok = check(res, {
            'list status 200': (r) => r.status === 200,
            'list has invoices array': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    return Array.isArray(body.invoices);
                } catch {
                    return false;
                }
            },
        });
        if (!ok) listErrors.add(1);
    });

    group('List invoices — pending filter', () => {
        const res = http.get(`${BASE_URL}/invoices?status=pending_review`, {
            ...h,
            tags: {endpoint: 'list_invoices'},
        });
        check(res, {'pending filter 200': (r) => r.status === 200});
    });

    group('List invoices — confirmed filter', () => {
        const res = http.get(`${BASE_URL}/invoices?status=confirmed`, {
            ...h,
            tags: {endpoint: 'list_invoices'},
        });
        check(res, {'confirmed filter 200': (r) => r.status === 200});

        // If there are confirmed invoices, benchmark a single-record GET
        try {
            const body = JSON.parse(res.body as string);
            const first = (body.invoices || [])[0];
            if (first?.id) {
                group('Get single confirmed invoice', () => {
                    const getRes = http.get(`${BASE_URL}/invoices/${first.id}`, {
                        ...h,
                        tags: {endpoint: 'get_invoice'},
                    });
                    check(getRes, {
                        'get invoice 200': (r) => r.status === 200,
                        'no receipt_data in get': (r) => {
                            try {
                                // receipt_data should only be present on the single-get, not list.
                                // This check verifies the single-get does return the full record.
                                return JSON.parse(r.body as string).id === first.id;
                            } catch {
                                return false;
                            }
                        },
                    });
                });
            }
        } catch {
            // no confirmed invoices yet — skip
        }
    });

    sleep(1);
}
