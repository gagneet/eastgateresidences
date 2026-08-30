// @featuretrace:financial-onboarding — k6 benchmark for the gap-tolerant CSV/JSON historical
//   financial import + generate-preview path (GAP-FIN-024).
// Layer: test
// Data flow: k6 -> POST /financials/onboarding/building/{id}/import-financial-data (CSV upload)
//            -> annual_levies/levy_categories (Mongo, is_test_data=true) -> POST
//            .../reconstruction-batches (draft, is_test_data=true) -> .../extract ->
//            .../generate-preview (dry-run, merged income+expense manifest, no persistence).
// Related: backend/routers/financial_onboarding.py
//          backend/services/onboarding/gap_tolerant_financial_import.py
//          backend/services/reconstruction_generators/budget_levy_generator.py
//          backend/services/reconstruction_generators/expense_category_generator.py
//
// Building-agnostic by design — BUILDING_ID has NO default (must be passed explicitly). Every
// write in this script is is_test_data=true and targets a reserved, deliberately implausible
// financial-year range (2099) that no real building's reconstruction would request. Teardown
// calls the route-level test cleanup endpoint so the benchmark is idempotent and does not leave
// annual_levies, levy_categories, reconstruction batch, or manifest rows behind.
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
    },
    thresholds: {
        'http_req_duration{endpoint:import_financial_data}': ['p(95)<2000'],
        'http_req_duration{endpoint:create_batch}': ['p(95)<800'],
        'http_req_duration{endpoint:extract_batch}': ['p(95)<800'],
        'http_req_duration{endpoint:generate_preview}': ['p(95)<3000'],
        financial_onboarding_import_required_errors: ['count==0'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';

// Reserved, implausible financial year used only to make cleanup exact.
const TEST_YEAR = 2099;

const requiredErrors = new Counter('financial_onboarding_import_required_errors');
const importDuration = new Trend('financial_onboarding_import_ms', true);
const previewDuration = new Trend('financial_onboarding_generate_preview_ms', true);

function requireConfig(): void {
    if (!TOKEN) {
        throw new Error(
            'AUTH_TOKEN env var is required. Example: k6 run tests/performance/financial_onboarding_import_benchmark.ts ' +
            '-e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<jwt> -e BUILDING_ID=<scheme_number>',
        );
    }
    if (!BUILDING_ID) {
        throw new Error(
            'BUILDING_ID env var is required — this script is building-agnostic by design and has no ' +
            'default (never assume East Gate/13195). Pass -e BUILDING_ID=<scheme_number> for the building ' +
            'to run against; use a dedicated non-production test building where possible.',
        );
    }
}

function authHeaders() {
    return {Authorization: `Bearer ${TOKEN}`};
}

function csvPayload(): string {
    return (
        'year,fund_type,proposed_amount,closing_balance,category_name,category_actual_amount\n' +
        `${TEST_YEAR},admin,50000.00,10000.00,Insurance,8000.00\n` +
        `${TEST_YEAR},sinking,20000.00,5000.00,Lift Maintenance,4500.00\n`
    );
}

export function teardown(_data: unknown): void {
    if (!TOKEN || !BUILDING_ID) return;
    http.del(
        `${BASE_URL}/financials/onboarding/building/${BUILDING_ID}/test-data?year=${TEST_YEAR}`,
        null,
        {headers: authHeaders(), tags: {endpoint: 'cleanup_test_data'}},
    );
}

export default function () {
    requireConfig();
    const h = authHeaders();
    let batchId: string | null = null;

    group('Import financial data (CSV, is_test_data)', () => {
        const res = http.post(
            `${BASE_URL}/financials/onboarding/building/${BUILDING_ID}/import-financial-data`,
            {
                file: http.file(csvPayload(), 'perf-budget.csv', 'text/csv'),
                is_test_data: 'true',
            },
            {headers: h, tags: {endpoint: 'import_financial_data'}},
        );
        importDuration.add(res.timings.duration);
        const ok = check(res, {
            'POST import-financial-data 200': (r) => r.status === 200,
            'response has coverage summary': (r) => {
                if (r.status !== 200) return false;
                try {
                    const body = JSON.parse(r.body as string);
                    return Array.isArray(body.coverage) && body.fund_rows_written >= 1;
                } catch {
                    return false;
                }
            },
        });
        if (!ok) requiredErrors.add(1);
    });

    sleep(0.5);

    group('Create + extract + generate-preview (dry-run, no ledger writes)', () => {
        const createRes = http.post(
            `${BASE_URL}/financials/onboarding/building/${BUILDING_ID}/reconstruction-batches`,
            JSON.stringify({
                financial_year_start: TEST_YEAR,
                financial_year_end: TEST_YEAR,
                reconstruction_method: 'generate_from_budget_v1',
                is_test_data: true,
            }),
            {headers: {...h, 'Content-Type': 'application/json'}, tags: {endpoint: 'create_batch'}},
        );
        const createOk = check(createRes, {'POST reconstruction-batches 200': (r) => r.status === 200});
        if (!createOk) {
            requiredErrors.add(1);
            return;
        }
        try {
            batchId = JSON.parse(createRes.body as string).batch_id;
        } catch {
            requiredErrors.add(1);
            return;
        }
        if (!batchId) {
            requiredErrors.add(1);
            return;
        }

        const extractRes = http.post(
            `${BASE_URL}/financials/onboarding/building/${BUILDING_ID}/reconstruction-batches/${batchId}/extract`,
            null,
            {headers: h, tags: {endpoint: 'extract_batch'}},
        );
        check(extractRes, {'POST extract 200': (r) => r.status === 200}) || requiredErrors.add(1);

        const previewRes = http.post(
            `${BASE_URL}/financials/onboarding/building/${BUILDING_ID}/reconstruction-batches/${batchId}/generate-preview`,
            null,
            {headers: h, tags: {endpoint: 'generate_preview'}},
        );
        previewDuration.add(previewRes.timings.duration);
        // 200 = income and/or expense data resolved; 422 is legitimate — the test data intentionally
        // has no `units` UOE schedule fixture, so the income side may correctly report nothing to
        // generate while still exercising the merge/expense/reconciliation code path under load.
        check(previewRes, {
            'POST generate-preview 200 or 422': (r) => r.status === 200 || r.status === 422,
        }) || requiredErrors.add(1);
    });

    sleep(1);
}
