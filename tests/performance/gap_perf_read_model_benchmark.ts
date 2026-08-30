import http from 'k6/http';
import { check, fail, group, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import { Options } from 'k6/options';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8003/api').replace(/\/$/, '');
const AUTH_TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';
const UNIT_NUMBER = __ENV.UNIT_NUMBER || '';
const INCLUDE_OPTIONAL = (__ENV.INCLUDE_OPTIONAL || '').toLowerCase() === 'true';

const errors = new Rate('gap_perf_errors');
const dashboardReadMs = new Trend('gap_perf_dashboard_read_ms', true);
const financeReadMs = new Trend('gap_perf_finance_read_ms', true);
const operationsReadMs = new Trend('gap_perf_operations_read_ms', true);
const documentReadMs = new Trend('gap_perf_document_read_ms', true);
const identityReadMs = new Trend('gap_perf_identity_read_ms', true);
const optionalFutureReadMs = new Trend('gap_perf_optional_future_read_ms', true);

export const options: Options = {
  scenarios: {
    smoke: {
      executor: 'shared-iterations',
      vus: 1,
      iterations: 3,
      exec: 'currentStateReadPaths',
    },
    sustained_current: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: Number(__ENV.K6_TARGET_VUS || 25) },
        { duration: '1m', target: Number(__ENV.K6_TARGET_VUS || 25) },
        { duration: '20s', target: 0 },
      ],
      exec: 'currentStateReadPaths',
      startTime: '5s',
    },
    optional_future_surface: {
      executor: 'shared-iterations',
      vus: 1,
      iterations: 2,
      exec: 'optionalFutureReadPaths',
      startTime: '2m',
    },
  },
  thresholds: {
    gap_perf_errors: ['rate<0.01'],
    gap_perf_dashboard_read_ms: ['p(95)<2500'],
    gap_perf_finance_read_ms: ['p(95)<1500'],
    gap_perf_operations_read_ms: ['p(95)<1200'],
    gap_perf_document_read_ms: ['p(95)<1200'],
    gap_perf_identity_read_ms: ['p(95)<800'],
  },
};

function headers() {
  const result: Record<string, string> = {
    Authorization: `Bearer ${AUTH_TOKEN}`,
    'Content-Type': 'application/json',
  };
  if (BUILDING_ID) result['X-Building-ID'] = BUILDING_ID;
  return result;
}

function timedGet(path: string, trend: Trend, validStatuses: number[] = [200]): number {
  const res = http.get(`${BASE_URL}${path}`, { headers: headers(), tags: { endpoint: path.split('?')[0] } });
  const ok = check(res, {
    [`GET ${path} expected status`]: (r) => validStatuses.includes(r.status),
  });
  errors.add(!ok);
  trend.add(res.timings.duration);
  return res.timings.duration;
}

export function setup() {
  if (!AUTH_TOKEN) fail('AUTH_TOKEN is required for GAP-PERF read-model benchmarks.');
  const probe = http.get(`${BASE_URL}/auth/me`, { headers: headers() });
  if (probe.status === 401 || probe.status === 403) {
    fail(`AUTH_TOKEN rejected by /auth/me (${probe.status}).`);
  }
}

export function currentStateReadPaths() {
  group('identity and navigation', () => {
    timedGet('/auth/me', identityReadMs);
    timedGet('/buildings/me', identityReadMs);
    timedGet('/feature-toggles/access-summary/me', identityReadMs);
    timedGet('/navigation/config', identityReadMs);
    timedGet('/navigation/badges', identityReadMs);
  });

  group('dashboard and portfolio-adjacent reads', () => {
    timedGet('/workflow-requests/stats/triage', dashboardReadMs);
    timedGet('/workflow-requests?limit=25', dashboardReadMs);
    timedGet('/analytics/maintenance-stats', dashboardReadMs);
    timedGet('/analytics/compliance-summary', dashboardReadMs);
    timedGet('/analytics/activities?limit=15', dashboardReadMs);
  });

  group('finance and BI reads', () => {
    timedGet('/finance/building-overview', financeReadMs);
    timedGet('/finance/summary', financeReadMs);
    timedGet('/finance/levy-kpi', financeReadMs);
    timedGet('/stats/building-kpis', financeReadMs);
    if (UNIT_NUMBER) {
      timedGet(`/finance/unit-dashboard-overview/${encodeURIComponent(UNIT_NUMBER)}`, financeReadMs);
    }
  });

  group('operations and supplier-adjacent reads', () => {
    timedGet('/maintenance?limit=25', operationsReadMs);
    timedGet('/work-orders?limit=25', operationsReadMs, [200, 404]);
    timedGet('/contractors', operationsReadMs);
    timedGet('/ppm/upcoming?days=60', operationsReadMs);
    timedGet('/parcels', operationsReadMs, [200, 403, 404]);
  });

  group('documents and records reads', () => {
    timedGet('/documents', documentReadMs);
    timedGet('/documents/folders', documentReadMs);
    timedGet('/documents/important', documentReadMs);
    timedGet('/document-requests', documentReadMs, [200, 403, 404]);
  });

  sleep(1);
}

export function optionalFutureReadPaths() {
  if (!INCLUDE_OPTIONAL) return;

  group('optional future/read-model probes', () => {
    timedGet('/ops/cases?limit=25', optionalFutureReadMs, [200, 403, 404]);
    timedGet('/communications/campaigns?limit=25', optionalFutureReadMs, [200, 403, 404]);
    timedGet('/access/devices?limit=25', optionalFutureReadMs, [200, 403, 404]);
    timedGet('/portfolio/buildings/archived', optionalFutureReadMs, [200, 403, 404]);
  });
}

export function teardown() {
  // Read-only by design. If future scenarios create records, they must use is_test_data=true
  // and deterministic cleanup before this benchmark can be extended.
}

