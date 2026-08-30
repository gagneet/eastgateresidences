// @featuretrace:financial_core — K6 benchmark for the partner-facing External API v1.
// Data flow: k6 -> X-API-Key -> /api/v1/* -> backend/routers/external_api.py
//            -> building/finance/maintenance/work-order read + write contracts.
// Related: backend/routers/external_api.py
//          backend/models/external_api_models.py
//          frontend/public/tech-docs/external-api.html
//
// Coverage gap closed: external_api.py (the versioned, API-key-authenticated
// partner surface) had no dedicated performance benchmark. This exercises every
// GET read contract and — opt-in — the three write contracts partners actually
// call (report defect, submit quote, submit invoice).
//
// AUTH: External API v1 uses an `X-API-Key` header, NOT a JWT bearer. Supply the
// key via API_KEY. Create keys through the JWT-authenticated management endpoints
// (POST /api/v1/api-keys) out of band — this script does not mint keys.
//
// WRITES ARE OPT-IN AND NOT AUTO-DELETABLE VIA THIS API. The External API is
// intentionally append-only (no DELETE for defects/quotes/invoices), so writes
// are gated behind INCLUDE_WRITES=1 and MUST target a demo/staging building.
// Records use the deterministic title/description prefix `perf-ext-` so the
// backend cleanup script (backend/scripts/cleanup_perf_test_maintenance.py) can
// remove them. If CLEANUP_TOKEN (a JWT admin token) is provided, teardown also
// best-effort deletes perf defects via the internal maintenance API.
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        partner_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 5},
                {duration: '30s', target: 5},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'partner_load'},
        },
    },
    thresholds: {
        'http_req_duration{surface:ext_discovery}': ['p(95)<300'],
        'http_req_duration{surface:ext_building}': ['p(95)<800'],
        'http_req_duration{surface:ext_finance}': ['p(95)<1500'],
        'http_req_duration{surface:ext_maintenance}': ['p(95)<800'],
        'http_req_duration{surface:ext_write}': ['p(95)<1500'],
        external_api_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const API_KEY = __ENV.API_KEY || '';
const UNIT_NUMBER = __ENV.UNIT_NUMBER || '';
const INCLUDE_WRITES = (__ENV.INCLUDE_WRITES || '') === '1';
const CLEANUP_TOKEN = __ENV.CLEANUP_TOKEN || '';

const requiredErrors = new Counter('external_api_required_errors');
const endpointDuration = new Trend('external_api_endpoint_ms');

function apiHeaders() {
    return {headers: {'X-API-Key': API_KEY, 'Content-Type': 'application/json'}};
}

function probe(path: string, name: string, surface: string, required: boolean) {
    const res = http.get(`${BASE_URL}/v1${path}`, {
        ...apiHeaders(),
        tags: {endpoint: name, surface},
    });
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

function firstId(body: string, ...keys: string[]): string {
    try {
        const parsed = JSON.parse(body);
        const arr = Array.isArray(parsed)
            ? parsed
            : parsed.items || parsed.results || parsed.work_orders || parsed.units || [];
        if (Array.isArray(arr) && arr.length > 0) {
            const rec = arr[0];
            for (const k of keys) {
                if (rec[k]) {
                    return String(rec[k]);
                }
            }
        }
    } catch {
        // fall through
    }
    return '';
}

export function setup() {
    if (!API_KEY) {
        throw new Error('API_KEY env var is required for External API v1 benchmarks.');
    }
    // Resolve a live unit + work order so path-param reads/writes hit real data.
    const unitsRes = http.get(`${BASE_URL}/v1/units`, apiHeaders());
    const woRes = http.get(`${BASE_URL}/v1/work-orders`, apiHeaders());
    const maintRes = http.get(`${BASE_URL}/v1/maintenance`, apiHeaders());
    return {
        unitNumber: UNIT_NUMBER || (unitsRes.status === 200 ? firstId(unitsRes.body as string, 'unit_number', 'id') : ''),
        workOrderId: woRes.status === 200 ? firstId(woRes.body as string, 'id', 'work_order_id') : '',
        maintenanceId: maintRes.status === 200 ? firstId(maintRes.body as string, 'id', 'request_id') : '',
    };
}

export default function (data: {unitNumber: string; workOrderId: string; maintenanceId: string}) {
    group('ext_discovery', () => {
        // /v1/health is unauthenticated by contract; /v1/scopes needs the key.
        probe('/health', 'health', 'ext_discovery', true);
        probe('/scopes', 'scopes', 'ext_discovery', false);
        probe('/webhooks/events', 'webhook_events', 'ext_discovery', false);
    });

    sleep(0.3);

    group('ext_building', () => {
        probe('/building', 'building_info', 'ext_building', true);
        probe('/units', 'units_list', 'ext_building', true);
        probe('/building/defects', 'defects_list', 'ext_building', false);
        if (data.unitNumber) {
            const u = encodeURIComponent(data.unitNumber);
            probe(`/units/${u}`, 'unit_detail', 'ext_building', false);
            probe(`/units/${u}/levy-balance`, 'unit_levy_balance', 'ext_building', false);
        }
    });

    sleep(0.3);

    group('ext_finance', () => {
        probe('/oc/summary', 'oc_summary', 'ext_finance', false);
        probe('/oc/levies', 'oc_levies', 'ext_finance', false);
        probe('/finance/summary', 'finance_summary', 'ext_finance', true);
        probe('/finance/budget', 'finance_budget', 'ext_finance', false);
        probe('/finance/transactions', 'finance_transactions', 'ext_finance', false);
    });

    sleep(0.3);

    group('ext_maintenance', () => {
        probe('/maintenance', 'maintenance_list', 'ext_maintenance', true);
        probe('/work-orders', 'work_orders_list', 'ext_maintenance', false);
        probe('/service/work-orders', 'service_work_orders', 'ext_maintenance', false);
        if (data.maintenanceId) {
            probe(`/maintenance/${encodeURIComponent(data.maintenanceId)}`, 'maintenance_detail', 'ext_maintenance', false);
        }
        if (data.workOrderId) {
            probe(`/work-orders/${encodeURIComponent(data.workOrderId)}`, 'work_order_detail', 'ext_maintenance', false);
        }
    });

    if (INCLUDE_WRITES) {
        group('ext_write', () => {
            // Report a defect (needs write:maintenance scope). Append-only.
            const defect = http.post(
                `${BASE_URL}/v1/building/defects`,
                JSON.stringify({
                    title: `perf-ext-${__VU}-${__ITER} defect`,
                    description: 'perf-ext automated External API benchmark defect — safe to delete',
                    location: 'Common Area',
                    priority: 'low',
                }),
                {...apiHeaders(), tags: {endpoint: 'create_defect', surface: 'ext_write'}},
            );
            check(defect, {'POST /v1/building/defects 200/201': (r) => r.status === 200 || r.status === 201})
                || requiredErrors.add(1, {endpoint: 'create_defect'});

            if (data.workOrderId) {
                // Submit a quote (needs write:service scope).
                const quote = http.post(
                    `${BASE_URL}/v1/service/quotes`,
                    JSON.stringify({
                        work_order_id: data.workOrderId,
                        amount: 100.0,
                        description: 'perf-ext automated benchmark quote — safe to delete',
                        notes: 'perf-ext',
                    }),
                    {...apiHeaders(), tags: {endpoint: 'submit_quote', surface: 'ext_write'}},
                );
                check(quote, {'POST /v1/service/quotes <500': (r) => r.status < 500});

                // Submit an invoice (needs write:service scope).
                const invoice = http.post(
                    `${BASE_URL}/v1/service/invoices`,
                    JSON.stringify({
                        work_order_id: data.workOrderId,
                        amount: 100.0,
                        gst_amount: 10.0,
                        description: 'perf-ext automated benchmark invoice',
                        invoice_number: `perf-ext-${__VU}-${__ITER}`,
                        invoice_date: '2026-06-28',
                        notes: 'perf-ext',
                    }),
                    {...apiHeaders(), tags: {endpoint: 'submit_invoice', surface: 'ext_write'}},
                );
                check(invoice, {'POST /v1/service/invoices <500': (r) => r.status < 500});
            }
        });
    }

    sleep(1);
}

export function teardown(_data: unknown): void {
    // The External API v1 is append-only (no DELETE for defects/quotes/invoices),
    // so writes cannot be removed through the partner surface itself.
    // If an internal admin JWT is supplied, best-effort delete perf-ext defects
    // via the internal maintenance API; otherwise this is a documented no-op and
    // cleanup must run backend/scripts/cleanup_perf_test_maintenance.py.
    if (!CLEANUP_TOKEN) {
        return;
    }
    const jwt = {headers: {Authorization: `Bearer ${CLEANUP_TOKEN}`, 'Content-Type': 'application/json'}};
    const list = http.get(`${BASE_URL}/maintenance?include_test_data=true`, jwt);
    if (list.status !== 200) {
        return;
    }
    let requests: Array<{id: string; title?: string}>;
    try {
        requests = JSON.parse(list.body as string);
    } catch {
        return;
    }
    for (const r of requests.filter((x) => x.title?.startsWith('perf-ext-'))) {
        http.del(`${BASE_URL}/maintenance/${r.id}`, null, jwt);
    }
}
