// @featuretrace:settings — K6 benchmark for the building settings / config read surface.
// Data flow: k6 -> /api/{settings,schedule,stats/dashboard,email-settings} read
//            contracts -> settings endpoints served inline from backend/server.py
//            -> services/settings_service + db.settings.
// Related: frontend/src/app/(app)/admin/console/page.tsx
//          backend/server.py (inline settings routes)
//          backend/routers/settings.py (mirror; currently unwired)
//
// Zero-coverage gap closed: the settings surface (~17 routes) had no performance
// benchmark. NOTE: routers/settings.py is not wired into server.py — these
// endpoints are served by inline @api_router routes in server.py, which is what
// this benchmark targets (same paths). /stats/dashboard fans out across the
// building and is the heaviest read here.
//
// Read-only: only GET probes. Settings/schedule writes are excluded.
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {executor: 'constant-vus', vus: 1, duration: '10s', tags: {scenario: 'smoke'}},
        settings_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 5},
                {duration: '30s', target: 5},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'settings_load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:settings}': ['p(95)<400'],
        'http_req_duration{endpoint:unit_display}': ['p(95)<400'],
        'http_req_duration{endpoint:schedule}': ['p(95)<600'],
        'http_req_duration{endpoint:dashboard_stats}': ['p(95)<1200'],
        'http_req_duration{endpoint:email_settings}': ['p(95)<400'],
        settings_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';
const LEGAL_PAGE = __ENV.LEGAL_PAGE || 'terms';

const requiredErrors = new Counter('settings_required_errors');
const endpointDuration = new Trend('settings_endpoint_ms');

function headers() {
    const h: Record<string, string> = {Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json'};
    if (BUILDING_ID) {
        h['X-Building-ID'] = BUILDING_ID;
    }
    return {headers: h};
}

function probe(path: string, name: string, required: boolean) {
    const res = http.get(`${BASE_URL}${path}`, {...headers(), tags: {endpoint: name}});
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
        throw new Error('AUTH_TOKEN env var is required for settings benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for settings probes.');
    }
}

export default function () {
    group('settings_config', () => {
        probe('/settings', 'settings', true);
        probe('/settings/unit-display', 'unit_display', false);
        probe('/schedule', 'schedule', true);
        probe('/email-settings', 'email_settings', false);
    });

    sleep(0.5);

    group('settings_dashboard', () => {
        // Aggregates building-wide counters — the heaviest read on this surface.
        probe('/stats/dashboard', 'dashboard_stats', true);
        probe(`/legal-pages/${encodeURIComponent(LEGAL_PAGE)}`, 'legal_page', false);
    });

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No settings or schedules are modified.
}
