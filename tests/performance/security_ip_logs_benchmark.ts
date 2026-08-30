// @featuretrace:security-ip-logging — k6 benchmark for the Security & IP Logs read surface.
// Layer: test
// Data flow: k6 VU -> GET /security/stats + /security/login-attempts -> login_audit_logs (global).
// Related: backend/routers/security.py
//          backend/utils/audit_search.py
//          frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
//
// Usage:
//   k6 run tests/performance/security_ip_logs_benchmark.ts \
//       -e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<super_admin token>
//   k6 run --env K6_SCENARIO=smoke ...            # single-pass sanity check
//   k6 archive tests/performance/security_ip_logs_benchmark.ts --archive-out /tmp/c.tar
//
// WHY THIS EXISTS
// ---------------
// The 2026-08-24 change added three things to this page that each cost server
// work on every request:
//
//   1. a field-scoped search grammar, which turns one indexed equality into a
//      set of $regex / $nor clauses;
//   2. name resolution that now falls back to a SECOND store (Postgres) when
//      Mongo cannot answer;
//   3. per-row `signals` and dual IP fields, widening every document.
//
// Each is defensible on its own; together they could quietly turn a fast page
// into a slow one, and the regression would only show up on the largest tenant.
// These thresholds are the guard.
//
// READ-ONLY BY DESIGN
// -------------------
// Every request here is a GET. This script creates NO records, so the usual
// `is_test_data: true` tagging does not apply and `teardown()` has nothing to
// delete — it verifies that fact rather than pretending to clean up, per the
// CLAUDE.md rule that a teardown which cannot delete must say so.

import http from 'k6/http';
import { check, group } from 'k6';
import { Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const AUTH_TOKEN = __ENV.AUTH_TOKEN || '';
const SCENARIO = __ENV.K6_SCENARIO || 'default';

// Separated so a slow SEARCH does not hide behind a fast unfiltered list, and
// vice versa. The whole point is to know which of the two regressed.
const statsTrend = new Trend('security_stats_duration', true);
const plainListTrend = new Trend('security_list_plain_duration', true);
const searchTrend = new Trend('security_list_search_duration', true);
const excludeTrend = new Trend('security_list_exclude_duration', true);

const smoke = {
    executor: 'shared-iterations',
    vus: 1,
    iterations: 1,
    maxDuration: '1m',
};

const load = {
    executor: 'ramping-vus',
    startVUs: 1,
    stages: [
        { duration: '20s', target: 5 },
        { duration: '40s', target: 5 },
        { duration: '10s', target: 0 },
    ],
    gracefulRampDown: '10s',
};

export const options = {
    scenarios: { [SCENARIO === 'smoke' ? 'smoke' : 'load']: SCENARIO === 'smoke' ? smoke : load },
    thresholds: {
        // The stats card row is the first thing rendered; it is aggregate-only
        // and should stay comfortably sub-second.
        security_stats_duration: ['p(95)<1500'],

        // An unfiltered page of 25 rows. Includes the two-store name resolution.
        security_list_plain_duration: ['p(95)<1200'],

        // A field-scoped search. Allowed more headroom than the plain list
        // because $regex clauses cannot use an index the way an equality can —
        // but NOT unlimited, because "search is slow" is the regression this
        // file exists to catch.
        security_list_search_duration: ['p(95)<2000'],

        // Exclusion ($nor) is the most expensive shape the grammar can produce,
        // and the one the operator will reach for most often ("hide the probe").
        security_list_exclude_duration: ['p(95)<2500'],

        http_req_failed: ['rate<0.01'],
    },
};

function authHeaders() {
    return {
        headers: {
            Authorization: `Bearer ${AUTH_TOKEN}`,
            'Content-Type': 'application/json',
        },
        // Long enough that a genuinely slow response is measured rather than
        // aborted and recorded as a failure — a timeout would mask the number
        // this script is trying to produce.
        timeout: '30s',
    };
}

export function setup() {
    if (!AUTH_TOKEN) {
        throw new Error(
            'AUTH_TOKEN is required — these endpoints are super_admin only. ' +
            'Run with -e AUTH_TOKEN=<token>.',
        );
    }
    // Fail fast and clearly if the token is not a super admin, rather than
    // reporting a 403 rate as if it were a latency result.
    const probe = http.get(`${BASE_URL}/security/stats`, authHeaders());
    if (probe.status === 403) {
        throw new Error('AUTH_TOKEN is not a super_admin — /security/* requires it.');
    }
    if (probe.status !== 200) {
        throw new Error(`Backend not ready: /security/stats returned ${probe.status}`);
    }
    return { startedAt: new Date().toISOString() };
}

export default function () {
    group('overview stats', () => {
        const res = http.get(`${BASE_URL}/security/stats`, authHeaders());
        statsTrend.add(res.timings.duration);
        check(res, {
            'stats 200': (r) => r.status === 200,
            'stats has counts': (r) => r.json('total_logins_30d') !== undefined,
        });
    });

    group('login activity — unfiltered', () => {
        const res = http.get(`${BASE_URL}/security/login-attempts?page=1&per_page=25`, authHeaders());
        plainListTrend.add(res.timings.duration);
        check(res, {
            'list 200': (r) => r.status === 200,
            'list returns items array': (r) => Array.isArray(r.json('items')),
            // The help payload drives the UI's help panel; if it stops being
            // returned the panel silently empties.
            'list returns search_help': (r) => r.json('search_help.fields') !== undefined,
        });
    });

    group('login activity — field search', () => {
        const res = http.get(
            `${BASE_URL}/security/login-attempts?page=1&per_page=25&search=${encodeURIComponent('status:failed')}`,
            authHeaders(),
        );
        searchTrend.add(res.timings.duration);
        check(res, {
            'search 200': (r) => r.status === 200,
            'search reports no unknown fields': (r) =>
                (r.json('unknown_search_fields') || []).length === 0,
        });
    });

    group('login activity — exclusion', () => {
        // The shape an operator actually types: hide the monitoring probe.
        const res = http.get(
            `${BASE_URL}/security/login-attempts?page=1&per_page=25&search=${encodeURIComponent('-device:api')}`,
            authHeaders(),
        );
        excludeTrend.add(res.timings.duration);
        check(res, {
            'exclude 200': (r) => r.status === 200,
            'exclude returns items array': (r) => Array.isArray(r.json('items')),
        });
    });
}

export function teardown() {
    // Nothing to delete: every request in this script is a GET and no records
    // are created. Stated explicitly rather than left as an empty function, so a
    // reader does not have to diff the script to establish that there is no
    // residue to clean up.
    console.log(
        'teardown: read-only benchmark — no records created, no cleanup required. ' +
        'If this script ever gains a write, tag it is_test_data:true and add a ' +
        'backend/scripts/cleanup_perf_test_*.py companion.',
    );
}
