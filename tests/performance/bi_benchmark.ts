// @featuretrace:bi-analytics — K6 benchmark for the Business Intelligence read surface.
// Data flow: k6 -> /api/bi/* read contracts -> backend/routers/bi.py -> Postgres/Mongo BI aggregates.
// Related: frontend/src/app/(app)/intelligence/bi/page.tsx
//          frontend/src/app/(app)/intelligence/bi/platform/page.tsx
//          backend/routers/bi.py
//
// Zero-coverage gap closed: bi.py had 58 routes with no performance benchmark
// (k6_performance_coverage_report_2026-08-10). BI endpoints run heavy cross-lot
// aggregations (arrears hotspots, health trends, budget-vs-actual, ETL health),
// so they are exactly the routes most at risk of read-latency regressions.
//
// Read-only: every probe is a GET. No ledger, no mutation, no test data created.
// The single POST route (/bi/admin/etl/run) is deliberately excluded — it kicks
// off an ETL job and is never appropriate for a load benchmark.
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
        bi_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 4},
                {duration: '30s', target: 4},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'bi_load'},
        },
    },
    thresholds: {
        // Building-level aggregates fan out across every lot for the tenant.
        'http_req_duration{surface:bi_building}': ['p(95)<2000'],
        // Platform/agency/manager rollups aggregate across many buildings.
        'http_req_duration{surface:bi_rollup}': ['p(95)<2500'],
        bi_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';
// Optional rollup scopes. When unset, the corresponding groups are skipped
// rather than probed with a guessed id (which would only ever 404).
const ORG_ID = __ENV.ORG_ID || '';
const AGENCY_ID = __ENV.AGENCY_ID || '';
const MANAGER_ID = __ENV.MANAGER_ID || '';
const OWNER_ID = __ENV.OWNER_ID || '';
// Platform-level probes require a super-admin token; opt-in only.
const INCLUDE_PLATFORM = (__ENV.INCLUDE_PLATFORM || '') === '1';

const requiredErrors = new Counter('bi_required_errors');
const endpointDuration = new Trend('bi_endpoint_ms');

function headers() {
    const h: Record<string, string> = {
        Authorization: `Bearer ${TOKEN}`,
        'Content-Type': 'application/json',
    };
    if (BUILDING_ID) {
        h['X-Building-ID'] = BUILDING_ID;
    }
    return {headers: h};
}

// required=true  -> must return 200 (a real read regression if not)
// required=false -> must merely avoid a 5xx (data-shape/permission dependent)
function probe(path: string, name: string, surface: string, required: boolean) {
    const res = http.get(`${BASE_URL}${path}`, {
        ...headers(),
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

export function setup() {
    if (!TOKEN) {
        throw new Error('AUTH_TOKEN env var is required for BI benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for BI building-scoped probes.');
    }
}

export default function () {
    const b = encodeURIComponent(BUILDING_ID);

    group('bi_building_financials', () => {
        probe(`/bi/building/${b}/financial-summary`, 'building_financial_summary', 'bi_building', true);
        probe(`/bi/building/${b}/levy-collection-trend`, 'levy_collection_trend', 'bi_building', true);
        probe(`/bi/building/${b}/arrears-hotspots`, 'building_arrears_hotspots', 'bi_building', true);
        probe(`/bi/building/${b}/capex-vs-plan`, 'capex_vs_plan', 'bi_building', true);
        probe(`/bi/building/${b}/sinking-fund-projection`, 'sinking_fund_projection', 'bi_building', true);
    });

    sleep(0.5);

    group('bi_building_operations', () => {
        probe(`/bi/building/${b}/health-trend`, 'health_trend', 'bi_building', true);
        probe(`/bi/building/${b}/maintenance-costs`, 'maintenance_costs', 'bi_building', true);
        probe(`/bi/building/${b}/compliance-status`, 'compliance_status', 'bi_building', true);
        probe(`/bi/building/${b}/utility-trend`, 'utility_trend', 'bi_building', true);
        probe(`/bi/building/${b}/occupancy-mix`, 'occupancy_mix', 'bi_building', true);
        probe(`/bi/building/${b}/request-heatmap`, 'request_heatmap', 'bi_building', true);
        probe(`/bi/building/${b}/asset-risk`, 'asset_risk', 'bi_building', true);
        probe(`/bi/building/${b}/sinking-fund-risk`, 'sinking_fund_risk', 'bi_building', true);
        probe(`/bi/building/${b}/alerts`, 'building_alerts', 'bi_building', true);
        probe(`/bi/building/${b}/cutover-status`, 'building_cutover_status', 'bi_building', false);
    });

    sleep(0.5);

    if (OWNER_ID) {
        group('bi_owner', () => {
            const o = encodeURIComponent(OWNER_ID);
            probe(`/bi/owner/${o}/levy-history`, 'owner_levy_history', 'bi_rollup', false);
            probe(`/bi/owner/${o}/arrears-status`, 'owner_arrears_status', 'bi_rollup', false);
            probe(`/bi/owner/${o}/total-cost-of-ownership`, 'owner_tco', 'bi_rollup', false);
            probe(`/bi/owner/${o}/building-health-context`, 'owner_health_context', 'bi_rollup', false);
        });
        sleep(0.5);
    }

    if (ORG_ID) {
        group('bi_portfolio', () => {
            const org = encodeURIComponent(ORG_ID);
            probe(`/bi/portfolio/${org}/arrears-hotspots`, 'portfolio_arrears_hotspots', 'bi_rollup', false);
            probe(`/bi/portfolio/${org}/compliance-overdue`, 'portfolio_compliance_overdue', 'bi_rollup', false);
            probe(`/bi/portfolio/${org}/health-ranking`, 'portfolio_health_ranking', 'bi_rollup', false);
            probe(`/bi/portfolio/${org}/budget-vs-actual`, 'portfolio_budget_vs_actual', 'bi_rollup', false);
            probe(`/bi/portfolio/${org}/maintenance-risk`, 'portfolio_maintenance_risk', 'bi_rollup', false);
            probe(`/bi/portfolio/${org}/comparison`, 'portfolio_comparison', 'bi_rollup', false);
        });
        sleep(0.5);
    }

    if (AGENCY_ID) {
        group('bi_agency', () => {
            const a = encodeURIComponent(AGENCY_ID);
            probe(`/bi/agency/${a}/summary`, 'agency_summary', 'bi_rollup', false);
            probe(`/bi/agency/${a}/health-ranking`, 'agency_health_ranking', 'bi_rollup', false);
            probe(`/bi/agency/${a}/arrears-hotspots`, 'agency_arrears_hotspots', 'bi_rollup', false);
            probe(`/bi/agency/${a}/maintenance-risk`, 'agency_maintenance_risk', 'bi_rollup', false);
        });
        sleep(0.5);
    }

    if (MANAGER_ID) {
        group('bi_manager', () => {
            const m = encodeURIComponent(MANAGER_ID);
            probe(`/bi/manager/${m}/summary`, 'manager_summary', 'bi_rollup', false);
            probe(`/bi/manager/${m}/workload`, 'manager_workload', 'bi_rollup', false);
            probe(`/bi/manager/${m}/upcoming-actions`, 'manager_upcoming_actions', 'bi_rollup', false);
        });
        sleep(0.5);
    }

    if (INCLUDE_PLATFORM) {
        group('bi_platform', () => {
            probe('/bi/platform/summary', 'platform_summary', 'bi_rollup', false);
            probe('/bi/platform/agencies', 'platform_agencies', 'bi_rollup', false);
            probe('/bi/platform/buildings', 'platform_buildings', 'bi_rollup', false);
            probe('/bi/platform/feature-adoption', 'platform_feature_adoption', 'bi_rollup', false);
            probe('/bi/platform/data-freshness', 'platform_data_freshness', 'bi_rollup', false);
            probe('/bi/platform/etl-health', 'platform_etl_health', 'bi_rollup', false);
            probe('/bi/admin/etl-status', 'admin_etl_status', 'bi_rollup', false);
        });
    }

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No test data is created.
}
