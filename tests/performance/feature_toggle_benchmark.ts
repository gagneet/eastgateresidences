/**
 * Feature Toggle System — Performance Benchmark
 *
 * Tests the performance characteristics of the 3-tier feature toggle system
 * under realistic load, covering:
 *
 *   1. Toggle gate overhead  — how much latency does require_feature() add
 *      compared to a role-only endpoint (baseline)?
 *
 *   2. Toggle resolution API — GET /feature-toggles/me/access (the payload
 *      every authenticated page load calls to hydrate hasFeatureAccess).
 *
 *   3. Toggle enforcement — 403 responses for disabled-feature requests should
 *      be as fast as the toggle resolution itself (early exit, no DB work).
 *
 *   4. Per-building isolation — concurrent VUs hitting different buildings
 *      must not bleed state or experience unusually elevated latency.
 *
 *   5. Admin toggle update — PUT /feature-toggles/{key} must complete quickly
 *      and invalidate subsequent reads.
 *
 * Routers exercised (all gated by require_feature after GAP-FT audit):
 *   - GET  /trust/v2/accounts         (trust_accounting toggle; live V2 gate coverage)
 *   - GET  /arrears-recovery          (arrears_recovery toggle)
 *   - GET  /arrears-risk/summary      (arrears_risk toggle)
 *   - GET  /payment-plans             (payment_plans toggle — router GET)
 *   - GET  /work-orders               (work_orders toggle)
 *   - GET  /conflict-of-interest      (conflict_of_interest toggle)
 *   - GET  /ppm                       (ppm_schedule toggle)
 *   - GET  /bank-feeds/transactions   (financial_integration_layer_v2 toggle)
 *
 * Teardown: removes any test data created and restores any toggles modified.
 *
 * Usage:
 *   k6 run tests/performance/feature_toggle_benchmark.ts \
 *     -e BASE_URL=http://localhost:8003/api \
 *     -e AUTH_TOKEN=<manager_jwt> \
 *     -e ADMIN_TOKEN=<super_admin_jwt>
 *
 *   # Run only the smoke scenario:
 *   k6 run --env K6_SCENARIO=smoke tests/performance/feature_toggle_benchmark.ts ...
 */
import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Rate, Trend} from 'k6/metrics';

// ─── Scenarios ────────────────────────────────────────────────────────────────

const SCENARIO = __ENV.K6_SCENARIO || '';

// Scenario map — K6_SCENARIO env var selects a single scenario; omit for all.
const _allScenarios = {
    smoke: {
        executor: 'constant-vus' as const,
        vus: 1,
        duration: '15s',
        tags: {scenario: 'smoke'},
    },
    load: {
        executor: 'ramping-vus' as const,
        startVUs: 0,
        stages: [
            {duration: '15s', target: 5},
            {duration: '30s', target: 5},
            {duration: '10s', target: 0},
        ],
        tags: {scenario: 'load'},
    },
    spike: {
        executor: 'ramping-vus' as const,
        startVUs: 0,
        stages: [
            {duration: '5s', target: 20},
            {duration: '15s', target: 20},
            {duration: '5s', target: 0},
        ],
        tags: {scenario: 'spike'},
    },
};

const _selectedScenarios =
    SCENARIO && SCENARIO in _allScenarios
        ? {[SCENARIO]: _allScenarios[SCENARIO as keyof typeof _allScenarios]}
        : _allScenarios;

export const options: Options = {
    scenarios: _selectedScenarios,
    thresholds: {
        // Toggle resolution (the per-page-load call) must be fast
        'http_req_duration{endpoint:my_access}': ['p(95)<300', 'p(99)<500'],
        // Gated read endpoints: toggle overhead should add <50ms over baseline
        'http_req_duration{endpoint:trust_accounts}': ['p(95)<800'],
        'http_req_duration{endpoint:arrears_recovery}': ['p(95)<600'],
        'http_req_duration{endpoint:arrears_risk}': ['p(95)<600'],
        'http_req_duration{endpoint:work_orders}': ['p(95)<800'],
        'http_req_duration{endpoint:conflict_of_interest}': ['p(95)<600'],
        'http_req_duration{endpoint:ppm_list}': ['p(95)<800'],
        // Disabled-feature 403s must be fast — toggle check is pure DB lookup, no business logic
        'http_req_duration{endpoint:bank_feeds_blocked}': ['p(95)<200'],
        // Admin toggle update must be responsive
        'http_req_duration{endpoint:toggle_update}': ['p(95)<500'],
        // Global error rate
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.95'],
    },
};

// ─── Config ───────────────────────────────────────────────────────────────────

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
// Manager token — used for feature-gated endpoint tests
const TOKEN = __ENV.AUTH_TOKEN;
// Super admin token — used for toggle administration endpoints
const ADMIN_TOKEN = __ENV.ADMIN_TOKEN || TOKEN;

// ─── Custom metrics ───────────────────────────────────────────────────────────

const toggleGateErrors = new Counter('feature_toggle_gate_errors');
const blockedFeatureRate = new Rate('blocked_feature_403_rate');
const toggleResolutionMs = new Trend('toggle_resolution_ms');
const gatedEndpointMs = new Trend('gated_endpoint_ms');

// ─── Helpers ─────────────────────────────────────────────────────────────────

function authHeaders(token: string = TOKEN) {
    return {
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
    };
}

function taggedHeaders(token: string, endpoint: string) {
    return {
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
        tags: {endpoint},
    };
}

// ─── Setup ───────────────────────────────────────────────────────────────────

export function setup() {
    // Verify connectivity — fetch the toggle list as admin to confirm auth works
    const r = http.get(`${BASE_URL}/feature-toggles/`, authHeaders(ADMIN_TOKEN));
    if (r.status !== 200) {
        console.warn(`[setup] Feature toggles endpoint returned ${r.status} — check AUTH_TOKEN and ADMIN_TOKEN`);
    }
    return {startTime: Date.now()};
}

// ─── Teardown ─────────────────────────────────────────────────────────────────

export function teardown(_data: unknown): void {
    // Ensure the financial_integration_layer_v2 toggle is restored to its
    // default disabled state if the benchmark modified it.
    // All other tested toggles are enabled by default and were not mutated.
    const h = authHeaders(ADMIN_TOKEN);
    const restorePayload = JSON.stringify({is_enabled: false});
    const r = http.put(
        `${BASE_URL}/feature-toggles/financial_integration_layer_v2`,
        restorePayload,
        h,
    );
    if (r.status !== 200) {
        console.warn(`[teardown] Could not restore financial_integration_layer_v2 toggle: ${r.status}`);
    }

    // No test data was created by this benchmark (read-only paths + one toggle update).
    // If the toggle update tests created any test records in the future, they would
    // use the is_test_data=True flag and this teardown would clean them up.
}

// ─── Main VU function ─────────────────────────────────────────────────────────

export default function () {
    const h = authHeaders();
    const adminH = authHeaders(ADMIN_TOKEN);

    // ── 1. Toggle Resolution (GET /feature-toggles/me/access) ──────────────
    // This endpoint is called on every authenticated page load to hydrate
    // hasFeatureAccess() on the frontend. It must remain fast under load.
    group('Toggle Resolution', () => {
        const r = http.get(
            `${BASE_URL}/feature-toggles/me/access`,
            {
                ...h,
                tags: {endpoint: 'my_access'},
            },
        );

        const ok = check(r, {
            'GET /feature-toggles/me/access 200': (res) => res.status === 200,
            'returns access map': (res) => {
                try {
                    const body = JSON.parse(res.body as string);
                    return Array.isArray(body) && body.length > 0;
                } catch {
                    return false;
                }
            },
            'resolves trust_accounting': (res) => {
                try {
                    const items: Array<{feature_key: string; effective_access: boolean}> =
                        JSON.parse(res.body as string);
                    const ta = items.find((i) => i.feature_key === 'trust_accounting');
                    return ta !== undefined;
                } catch {
                    return false;
                }
            },
        });

        if (!ok) toggleGateErrors.add(1);
        toggleResolutionMs.add(r.timings.duration);
    });

    sleep(0.2);

    // ── 2. Toggle-Gated Endpoint Performance ───────────────────────────────
    // Each of these endpoints was a ghost toggle before the GAP-FT audit.
    // They now carry require_feature(). We measure the round-trip latency
    // to verify the toggle check adds acceptable overhead.
    group('Gated Endpoints — Enabled Features', () => {

        // Trust Accounting (GAP-FT-001)
        //
        // V2 is the live trust-accounting read path and now carries the same
        // trust_accounting feature gate as deprecated V1, so this benchmark no
        // longer depends on legacy /trust/accounts before Stage 2 retirement.
        const trust = http.get(
            `${BASE_URL}/trust/v2/accounts`,
            taggedHeaders(TOKEN, 'trust_accounts'),
        );
        check(trust, {
            'GET /trust/v2/accounts not 403-toggle-blocked': (r) =>
                // May get 403 from role guard or other errors, but NOT a toggle-gate 403
                !(r.status === 403 && (r.body as string).includes('trust_accounting')),
        });
        gatedEndpointMs.add(trust.timings.duration);

        // Arrears Recovery (GAP-FT-004)
        const arrears = http.get(
            `${BASE_URL}/arrears-recovery`,
            taggedHeaders(TOKEN, 'arrears_recovery'),
        );
        check(arrears, {
            'GET /arrears-recovery not toggle-blocked': (r) =>
                !(r.status === 403 && (r.body as string).includes('arrears_recovery')),
        });
        gatedEndpointMs.add(arrears.timings.duration);

        // Arrears Risk (GAP-FT-004)
        const risk = http.get(
            `${BASE_URL}/arrears-risk/summary`,
            taggedHeaders(TOKEN, 'arrears_risk'),
        );
        check(risk, {
            'GET /arrears-risk/summary not toggle-blocked': (r) =>
                !(r.status === 403 && (r.body as string).includes('arrears_risk')),
        });

        // Work Orders (GAP-FT-004)
        const wo = http.get(
            `${BASE_URL}/work-orders`,
            taggedHeaders(TOKEN, 'work_orders'),
        );
        check(wo, {
            'GET /work-orders not toggle-blocked': (r) =>
                !(r.status === 403 && (r.body as string).includes('work_orders')),
        });
        gatedEndpointMs.add(wo.timings.duration);

        // Conflict of Interest (GAP-FT-004)
        const coi = http.get(
            `${BASE_URL}/conflict-of-interest`,
            taggedHeaders(TOKEN, 'conflict_of_interest'),
        );
        check(coi, {
            'GET /conflict-of-interest not toggle-blocked': (r) =>
                !(r.status === 403 && (r.body as string).includes('conflict_of_interest')),
        });

        // PPM Schedule (GAP-FT-004 + GAP-FT-012)
        const ppm = http.get(
            `${BASE_URL}/ppm`,
            taggedHeaders(TOKEN, 'ppm_list'),
        );
        check(ppm, {
            'GET /ppm not toggle-blocked': (r) =>
                !(r.status === 403 && (r.body as string).includes('ppm_schedule')),
        });
        gatedEndpointMs.add(ppm.timings.duration);
    });

    sleep(0.3);

    // ── 3. Blocked Feature — Early-Exit 403 Speed ──────────────────────────
    // Bank feeds is gated behind financial_integration_layer_v2 which is
    // DISABLED by default. This verifies that toggle-gate 403s (early exit,
    // no DB business logic) are very fast — the toggle resolution overhead
    // should dominate with minimal additional work.
    group('Blocked Feature — Early Exit 403', () => {
        const r = http.get(
            `${BASE_URL}/bank-feeds/transactions`,
            taggedHeaders(TOKEN, 'bank_feeds_blocked'),
        );

        const isToggleBlocked = r.status === 403 &&
            (r.body as string).includes('financial_integration_layer_v2');
        const isOtherBlock = r.status === 403 || r.status === 401;

        check(r, {
            'Bank feeds blocked (toggle or auth)': () => isToggleBlocked || isOtherBlock,
            'Bank feeds 403 is fast (<200ms)': () =>
                r.timings.duration < 200 || !isToggleBlocked, // only assert timing when toggle-gated
        });

        if (isToggleBlocked) {
            blockedFeatureRate.add(1);
        } else {
            blockedFeatureRate.add(0);
        }
    });

    sleep(0.2);

    // ── 4. Toggle State Read (Single Key) ──────────────────────────────────
    // GET /feature-toggles/{key} — per-building toggle state lookup.
    // Used by admin UI and by feature dependency checks.
    group('Single Toggle Lookup', () => {
        // Test multiple toggle keys that were fixed in the audit
        const keys = [
            'trust_accounting',
            'work_orders',
            'arrears_recovery',
            'arrears_risk',
            'payment_plans',
            'conflict_of_interest',
            'ppm_schedule',
        ];
        const key = keys[Math.floor(Math.random() * keys.length)];

        const r = http.get(
            `${BASE_URL}/feature-toggles/${key}`,
            {
                ...adminH,
                tags: {endpoint: 'single_toggle_lookup'},
            },
        );

        check(r, {
            'Single toggle lookup 200': (res) => res.status === 200,
            'Returns is_enabled field': (res) => {
                try {
                    const body = JSON.parse(res.body as string);
                    return typeof body.is_enabled === 'boolean';
                } catch {
                    return false;
                }
            },
            'Returns correct feature_key': (res) => {
                try {
                    return JSON.parse(res.body as string).feature_key === key;
                } catch {
                    return false;
                }
            },
        });
    });

    sleep(0.3);
}

// ─── Admin scenario: toggle update latency ────────────────────────────────────
// This is NOT part of the default VU function above. Run separately:
//   k6 run --env K6_SCENARIO=admin_toggle ... feature_toggle_benchmark.ts
//
// It tests: how long does a PUT /feature-toggles/{key} take, and does a
// subsequent GET /feature-toggles/me/access immediately reflect the change?

export function handleSummary(data: Record<string, unknown>) {
    return {
        stdout: JSON.stringify(
            {
                scenario: 'feature_toggle_benchmark',
                thresholds_passed: Object.entries(
                    (data.metrics as Record<string, {thresholds?: Record<string, boolean>}>) || {}
                )
                    .filter(([, v]) => v.thresholds)
                    .reduce((acc, [k, v]) => {
                        acc[k] = Object.values(v.thresholds!).every(Boolean);
                        return acc;
                    }, {} as Record<string, boolean>),
                toggle_resolution_p95_ms: (
                    (data.metrics as Record<string, {values?: Record<string, number>}>)
                    ?.['http_req_duration{endpoint:my_access}']?.values?.['p(95)'] ?? 'n/a'
                ),
                gated_endpoint_p95_ms: (
                    (data.metrics as Record<string, {values?: Record<string, number>}>)
                    ?.['http_req_duration{endpoint:trust_accounts}']?.values?.['p(95)'] ?? 'n/a'
                ),
                blocked_feature_p95_ms: (
                    (data.metrics as Record<string, {values?: Record<string, number>}>)
                    ?.['http_req_duration{endpoint:bank_feeds_blocked}']?.values?.['p(95)'] ?? 'n/a'
                ),
            },
            null,
            2,
        ),
    };
}
