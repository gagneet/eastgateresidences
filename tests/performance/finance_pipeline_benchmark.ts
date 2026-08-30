// @featuretrace:demo_bank - k6 benchmark for finance UI reads and opt-in pipeline readiness.
// Layer: test
// Data flow: k6 -> finance UI read endpoints by default; INCLUDE_PIPELINE_PROBES=1
//            also probes bank-feed/matching/onboarding endpoints -> Mongo staging,
//            Postgres finance read paths, and matching queue read paths.
// Related: backend/routers/bank_feeds.py
//          backend/routers/financial_matching.py
//          backend/routers/financial_onboarding.py
//          docs/architecture/live_db_app_audit_2026-07-26.md
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

type ScenarioName = 'smoke' | 'finance_pipeline_load' | 'finance_pipeline_burst';

const SCENARIO = (__ENV.K6_SCENARIO || 'smoke') as ScenarioName;

function scenarioOptions(name: ScenarioName): Options {
    if (name === 'finance_pipeline_burst') {
        return {
            scenarios: {
                finance_pipeline_burst: {
                    executor: 'ramping-vus',
                    startVUs: 0,
                    stages: [
                        {duration: '10s', target: 15},
                        {duration: '30s', target: 15},
                        {duration: '10s', target: 0},
                    ],
                    tags: {scenario: 'finance_pipeline_burst'},
                },
            },
            thresholds: commonThresholds(),
        };
    }
    if (name === 'finance_pipeline_load') {
        return {
            scenarios: {
                finance_pipeline_load: {
                    executor: 'ramping-vus',
                    startVUs: 0,
                    stages: [
                        {duration: '15s', target: 5},
                        {duration: '45s', target: 5},
                        {duration: '10s', target: 0},
                    ],
                    tags: {scenario: 'finance_pipeline_load'},
                },
            },
            thresholds: commonThresholds(),
        };
    }
    return {
        scenarios: {
            smoke: {
                executor: 'constant-vus',
                vus: 1,
                duration: '10s',
                tags: {scenario: 'smoke'},
            },
        },
        thresholds: commonThresholds(),
    };
}

function commonThresholds() {
    return {
        'http_req_duration{surface:finance_pipeline_core}': ['p(95)<1200'],
        'http_req_duration{surface:finance_pipeline_secondary}': ['p(95)<2000'],
        'http_req_duration{surface:finance_pipeline_dry_run}': ['p(95)<2500'],
        finance_pipeline_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    };
}

export const options: Options = scenarioOptions(SCENARIO);

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '13195';
const UNIT_NUMBER = __ENV.UNIT_NUMBER || 'TH001';
const FINANCIAL_YEAR = __ENV.FINANCIAL_YEAR || String(new Date().getFullYear());
const INCLUDE_PIPELINE_PROBES = __ENV.INCLUDE_PIPELINE_PROBES === '1' || __ENV.INCLUDE_OPTIONAL_PROBES === '1';

const requiredErrors = new Counter('finance_pipeline_required_errors');
const endpointLatency = new Trend('finance_pipeline_endpoint_ms', true);
const optionalUnavailable = new Counter('finance_pipeline_optional_unavailable');

function requireAuthToken(): void {
    if (!TOKEN) {
        throw new Error(
            'AUTH_TOKEN env var is required. Example: k6 run tests/performance/finance_pipeline_benchmark.ts ' +
            '-e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<jwt> -e BUILDING_ID=<scheme_number>',
        );
    }
}

function apiHeaders() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
            'X-Building-ID': BUILDING_ID,
        },
    };
}

function hitGet(path: string, endpoint: string, surface = 'finance_pipeline_core'): void {
    const res = http.get(`${BASE_URL}${path}`, {
        ...apiHeaders(),
        tags: {endpoint, surface},
    });
    endpointLatency.add(res.timings.duration, {endpoint, surface});

    const ok = check(res, {
        [`${endpoint} returns 200`]: (r) => r.status === 200,
    });
    if (!ok) {
        requiredErrors.add(1, {endpoint});
    }
}

function hitPipelineProbeGet(path: string, endpoint: string, surface = 'finance_pipeline_secondary'): void {
    if (!INCLUDE_PIPELINE_PROBES) {
        return;
    }
    const res = http.get(`${BASE_URL}${path}`, {
        ...apiHeaders(),
        tags: {endpoint, surface, optional: 'true'},
    });
    endpointLatency.add(res.timings.duration, {endpoint, surface});
    const ok = check(res, {
        [`${endpoint} optional probe returns 200`]: (r) => r.status === 200,
    });
    if (!ok) {
        optionalUnavailable.add(1, {endpoint, status: String(res.status)});
    }
}

function hitPipelineDryRunReconstruction(): void {
    if (!INCLUDE_PIPELINE_PROBES) {
        return;
    }
    const payload = JSON.stringify({
        from_year: Number(__ENV.RECON_FROM_YEAR || 2021),
        to_year: Number(__ENV.RECON_TO_YEAR || 2021),
        dry_run: true,
    });
    const res = http.post(
        `${BASE_URL}/financial/matching/historical-reconstruction/run`,
        payload,
        {...apiHeaders(), tags: {endpoint: 'historical_reconstruction_dry_run', surface: 'finance_pipeline_dry_run'}},
    );
    endpointLatency.add(res.timings.duration, {
        endpoint: 'historical_reconstruction_dry_run',
        surface: 'finance_pipeline_dry_run',
    });
    const ok = check(res, {
        'historical reconstruction dry-run returns 200 or 409': (r) => r.status === 200 || r.status === 409,
        'dry-run response is well-formed on success': (r) => {
            if (r.status !== 200) return true;
            try {
                const body = JSON.parse(r.body as string);
                return body.dry_run === true && typeof body.inserted === 'number';
            } catch {
                return false;
            }
        },
    });
    if (!ok) {
        requiredErrors.add(1, {endpoint: 'historical_reconstruction_dry_run'});
    }
}

export function setup() {
    requireAuthToken();
    return {
        buildingId: BUILDING_ID,
        unitNumber: UNIT_NUMBER,
        financialYear: FINANCIAL_YEAR,
    };
}

export default function (data: {buildingId: string; unitNumber: string; financialYear: string}) {
    const unit = encodeURIComponent(data.unitNumber);
    const year = encodeURIComponent(data.financialYear);
    const building = encodeURIComponent(data.buildingId);

    group('finance_contract_reads', () => {
        hitGet(`/finance/summary?year=${year}`, 'finance_summary');
        hitGet(`/finance/building-overview?year=${year}`, 'finance_building_overview');
        hitGet(`/finance/unit-dashboard-overview/${unit}?year=${year}`, 'finance_unit_dashboard_overview');
        hitGet(`/finance/levy-kpi?year=${year}`, 'finance_levy_kpi');
        hitGet(`/unit-levy-ledger?year=${year}`, 'unit_levy_ledger');
    });

    // These probes are opt-in because bank-feeds and matching are guarded by
    // financial_integration_layer_v2, and onboarding reads are guarded by
    // historical_financial_reconstruction. A generic finance UI performance run
    // should not fail only because those separate readiness features are off.
    group('bank_feed_readiness_probes', () => {
        hitPipelineProbeGet('/bank-feeds/transactions?page=1&page_size=20', 'bank_feed_transactions');
        hitPipelineProbeGet('/bank-feeds/runs?page=1&page_size=20', 'bank_feed_runs');
        hitPipelineProbeGet('/bank-feeds/strata-years/summary', 'bank_feed_strata_year_summary');
        hitPipelineProbeGet('/bank-feeds/strata-years/summary/pg', 'bank_feed_pg_strata_year_summary');
    });

    group('matching_queue_probes', () => {
        hitPipelineProbeGet('/financial/matching/queue?status=pending&page=1&page_size=20', 'matching_queue_pending');
        hitPipelineProbeGet('/financial/matching/stats', 'matching_queue_stats');
    });

    group('finance_onboarding_readiness_probes', () => {
        hitPipelineProbeGet(
            `/financials/onboarding/building/${building}/reconstruction-batches?page=1&page_size=20`,
            'reconstruction_batches',
            'finance_pipeline_secondary',
        );
        hitPipelineProbeGet(
            `/financials/cutover/${building}`,
            'financial_cutover_config',
            'finance_pipeline_secondary',
        );
    });

    group('dry_run_reconstruction_probe', () => {
        hitPipelineDryRunReconstruction();
    });

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Default coverage is GET-only. INCLUDE_PIPELINE_PROBES=1 adds one POST, but
    // that call sends dry_run=true and creates no test records to delete.
}
