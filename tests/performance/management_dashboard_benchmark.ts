// @featuretrace:dashboard-v2 — k6 benchmark for the Management dashboard read fan-out.
// Layer: test
// Data flow: k6 → GET /analytics/diff-since + /community-dashboard/health-score
//            + /finance/building-overview + /stats/building-kpis (building-scoped).
// Related: backend/routers/analytics.py (get_diff_since)
//          backend/alembic/versions/0099_diff_since_indexes.py
//          backend/routers/community_dashboard.py
//
// Every endpoint here runs on a single dashboard load, so their latencies ADD UP for the
// user. They are measured together for that reason, and the composite is asserted
// separately from the individual thresholds — four endpoints each comfortably inside
// their own budget can still make a dashboard feel slow.
//
// diff-since is the reason this exists. It issues one COUNT(*) per domain table over a
// caller-supplied window, and before migration 0099 only ops.cases had a covering
// (scheme_id, created_at) index — finance.receipts did a Seq Scan whose cost grows with
// every levy payment. The window is swept here precisely because the cost is a function
// of it: a regression that reintroduces a scan shows up as latency rising with the
// window, which a single fixed-window probe would miss entirely.
//
// READ-ONLY. Every request is a GET, nothing is seeded and nothing is written, so there
// is no teardown to perform and no is_test_data residue to clean. That is a deliberate
// property of this script rather than an omission — see the teardown rule in CLAUDE.md,
// which applies to scripts that CREATE records.
//
// Usage:
//   k6 run tests/performance/management_dashboard_benchmark.ts \
//     -e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<manager JWT> -e BUILDING_ID=13195
//
//   k6 run -e K6_SCENARIO=smoke ... tests/performance/management_dashboard_benchmark.ts

import http from 'k6/http';
import { check, fail } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const BASE_URL: string = (__ENV.BASE_URL || 'http://localhost:8003/api').replace(/\/$/, '');
const AUTH_TOKEN: string = __ENV.AUTH_TOKEN || '';
const BUILDING_ID: string = __ENV.BUILDING_ID || '';

const diffSinceLatency = new Trend('mgmt_diff_since_latency', true);
const pulseLatency = new Trend('mgmt_pulse_latency', true);
const overviewLatency = new Trend('mgmt_overview_latency', true);
const kpiLatency = new Trend('mgmt_kpi_latency', true);
// What the user actually waits for: the sum of one full fan-out.
const dashboardLatency = new Trend('mgmt_dashboard_total_latency', true);
const errorRate = new Rate('mgmt_errors');
// A widening window must not cost materially more once the indexes are in place. This
// is the regression signal for migration 0099 being dropped or the predicate changing.
const windowScaling = new Trend('mgmt_diff_since_window_scaling', true);
const forecastLatency = new Trend('mgmt_reserve_forecast_latency', true);
// The sinking fund forecast reads the whole capital_replacement_schedule once and then
// walks the horizon in memory, so cost is a function of ASSET COUNT, not of `years`.
// A ratio that climbs with the horizon means someone reintroduced a per-year query.
const forecastHorizonScaling = new Trend('mgmt_reserve_forecast_horizon_scaling', true);

function headers() {
  const h: Record<string, string> = {
    Authorization: `Bearer ${AUTH_TOKEN}`,
    'Content-Type': 'application/json',
  };
  if (BUILDING_ID) h['X-Building-ID'] = BUILDING_ID;
  return h;
}

export const options = {
  scenarios: (() => {
    const only = __ENV.K6_SCENARIO;
    const all: Record<string, any> = {
      smoke: { executor: 'shared-iterations', vus: 1, iterations: 5, exec: 'dashboardLoad' },
      sustained: {
        executor: 'constant-vus', vus: 5, duration: '30s',
        exec: 'dashboardLoad', startTime: '10s',
      },
      window_sweep: {
        executor: 'shared-iterations', vus: 1, iterations: 4,
        exec: 'windowSweep', startTime: '45s',
      },
      horizon_sweep: {
        executor: 'shared-iterations', vus: 1, iterations: 4,
        exec: 'horizonSweep', startTime: '60s',
      },
    };
    return only ? { [only]: { ...all[only], startTime: '0s' } } : all;
  })(),
  thresholds: {
    // Individually generous; the composite below is the one that reflects the user's wait.
    mgmt_diff_since_latency: ['p(95)<400'],
    mgmt_pulse_latency: ['p(95)<600'],
    mgmt_overview_latency: ['p(95)<800'],
    mgmt_kpi_latency: ['p(95)<800'],
    mgmt_reserve_forecast_latency: ['p(95)<600'],
    // Flat within noise across a 20x horizon. 3 leaves room for jitter on a small sample
    // while still failing loudly if the work starts tracking the number of years.
    mgmt_reserve_forecast_horizon_scaling: ['p(95)<3'],
    mgmt_dashboard_total_latency: ['p(95)<2500'],
    mgmt_errors: ['rate<0.01'],
  },
};

export function setup() {
  if (!AUTH_TOKEN) fail('AUTH_TOKEN is required — pass a manager/super_admin JWT.');
  const probe = http.get(`${BASE_URL}/stats/building-kpis`, { headers: headers() });
  if (probe.status === 401 || probe.status === 403) {
    fail(`AUTH_TOKEN rejected (${probe.status}). The dashboard endpoints need a manager role.`);
  }
  return {};
}

function timedGet(path: string, trend: Trend): number {
  const res = http.get(`${BASE_URL}${path}`, { headers: headers() });
  trend.add(res.timings.duration);
  const ok = check(res, { [`${path} -> 200`]: (r) => r.status === 200 });
  errorRate.add(!ok);
  return res.timings.duration;
}

export function dashboardLoad() {
  // Mirrors what the page issues on mount. Sequential on purpose: the browser fans these
  // out, but measuring them in series gives the worst-case wall clock the user can see
  // on a cold connection, which is the number worth defending.
  const since = new Date(Date.now() - 7 * 86_400_000).toISOString();
  let total = 0;
  total += timedGet(`/analytics/diff-since?since=${encodeURIComponent(since)}`, diffSinceLatency);
  total += timedGet('/community-dashboard/health-score', pulseLatency);
  total += timedGet('/finance/building-overview', overviewLatency);
  total += timedGet('/stats/building-kpis', kpiLatency);
  total += timedGet('/analytics/sinking-fund-forecast?years=10', forecastLatency);
  dashboardLatency.add(total);
}

export function windowSweep() {
  // 1 day to 1 year. With (scheme_id, created_at) indexed the planner does an index
  // scan at every width and latency stays flat; without it the widest window degrades
  // toward a full scan of finance.receipts, which grows with every payment recorded.
  const widths = [1, 30, 180, 365];
  const baseline: number[] = [];
  for (const days of widths) {
    const since = new Date(Date.now() - days * 86_400_000).toISOString();
    const d = timedGet(`/analytics/diff-since?since=${encodeURIComponent(since)}`, diffSinceLatency);
    baseline.push(d);
  }
  // Ratio of widest to narrowest. Near 1 means the index is doing its job; a large
  // ratio means cost is tracking the window, i.e. a scan has come back.
  if (baseline[0] > 0) windowScaling.add(baseline[baseline.length - 1] / baseline[0]);
}

export function horizonSweep() {
  // 1 to 20 years — the full range the endpoint accepts. The projection is built from a
  // single capital_replacement_schedule fetch, so a 20-year horizon should cost what a
  // 1-year horizon costs. If this ratio grows, the per-year loop has started hitting the
  // database (or the CPI fallback is re-querying annual_levies per year).
  const horizons = [1, 5, 10, 20];
  const timings: number[] = [];
  for (const years of horizons) {
    timings.push(timedGet(`/analytics/sinking-fund-forecast?years=${years}`, forecastLatency));
  }
  if (timings[0] > 0) forecastHorizonScaling.add(timings[timings.length - 1] / timings[0]);
}

export function teardown() {
  // Intentionally empty. This script performs GETs only — it creates no records, so
  // there is nothing to delete and no residue to verify. Documented rather than omitted
  // so the absence reads as a decision, not an oversight.
}
