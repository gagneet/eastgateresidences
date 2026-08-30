/**
 * Security Performance Benchmark — StrataOS
 *
 * Three scenarios:
 *   brute_force     — hammers /auth/login with bad creds until 429 is observed
 *   jwt_attacks     — sends crafted JWT tokens; every probe MUST return 401/403
 *   cross_building  — authenticated user attempts BOLA via X-Building-ID override
 *
 * Custom metrics:
 *   rate_limit_triggered    — count of 429 responses from auth (want > 0)
 *   jwt_attacks_allowed     — count of 200 responses to crafted JWT (must be 0)
 *   cross_building_escalations — count of cross-tenant 200 responses (must be 0)
 *
 * Run:
 *   k6 run tests/performance/security_benchmark.ts \
 *       -e BASE_URL=http://localhost:8003/api \
 *       -e AUTH_TOKEN=<valid_strata_manager_token>
 *
 * To run a single scenario:
 *   k6 run -e K6_SCENARIO=brute_force \
 *       -e BASE_URL=http://localhost:8003/api \
 *       tests/performance/security_benchmark.ts
 *
 * Teardown: no data created — nothing to clean up.
 */

import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Options } from 'k6/options';
import { Counter, Rate } from 'k6/metrics';
import { b64encode } from 'k6/encoding';

// ─── Custom security metrics ──────────────────────────────────────────────────

const rateLimitTriggered     = new Counter('rate_limit_triggered');
const jwtAttacksAllowed      = new Counter('jwt_attacks_allowed');
const crossBuildingEscalations = new Counter('cross_building_escalations');
const authFailRate           = new Rate('auth_failure_rate');

// ─── Config ───────────────────────────────────────────────────────────────────

const BASE_URL   = (__ENV.BASE_URL  || 'http://localhost:8003/api').replace(/\/$/, '');
const AUTH_TOKEN = __ENV.AUTH_TOKEN || '';  // valid token needed for jwt_attacks + cross_building
const SCENARIO   = __ENV.K6_SCENARIO || '';

const DEMO_BUILDING  = 'UP-DEMO-001';
const OTHER_BUILDING = 'DEMO-0001';  // platform shell — should be invisible to demo manager
const TEST_HEADERS = {'Content-Type': 'application/json', 'X-Test-Data': 'true'};

// ─── Scenario selector ────────────────────────────────────────────────────────

function activeScenarios() {
    if (SCENARIO) return { [SCENARIO]: allScenarios()[SCENARIO] };
    return allScenarios();
}

function allScenarios() {
    return {
        brute_force: {
            executor: 'constant-vus' as const,
            vus: 3,
            duration: '30s',
            tags: { scenario: 'brute_force' },
        },
        jwt_attacks: {
            executor: 'constant-vus' as const,
            vus: 2,
            duration: '20s',
            startTime: '35s',
            tags: { scenario: 'jwt_attacks' },
        },
        cross_building: {
            executor: 'constant-vus' as const,
            vus: 2,
            duration: '20s',
            startTime: '60s',
            tags: { scenario: 'cross_building' },
        },
    };
}

export const options: Options = {
    scenarios: activeScenarios(),
    thresholds: {
        // No JWT attack probe should ever succeed
        'jwt_attacks_allowed':          ['count==0'],
        // No cross-building escalation should succeed
        'cross_building_escalations':   ['count==0'],
        // Auth endpoint must stay below 500ms even under brute force
        'http_req_duration{endpoint:login}': ['p(95)<500'],
        // Overall failure rate (non-401/403/429) must be near zero
        http_req_failed: ['rate<0.05'],
    },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function jsonHeaders(extra: Record<string, string> = {}) {
    return { headers: { 'Content-Type': 'application/json', ...extra } };
}

function authHeaders(extra: Record<string, string> = {}) {
    return {
        headers: {
            Authorization: `Bearer ${AUTH_TOKEN}`,
            'Content-Type': 'application/json',
            ...extra,
        },
    };
}

function b64url(obj: Record<string, unknown>): string {
    // k6's b64encode produces standard base64; convert to URL-safe, strip padding
    return b64encode(JSON.stringify(obj), 'rawurl');
}

function craftToken(header: Record<string, unknown>, payload: Record<string, unknown>, sig = ''): string {
    return `${b64url(header)}.${b64url(payload)}.${sig}`;
}

function demoPayload(role = 'strata_manager') {
    const now = Math.floor(Date.now() / 1000);
    return {
        user_id:     'perf-security-probe',
        email:       'probe@security.test',
        role,
        building_id: DEMO_BUILDING,
        exp:         now + 3600,
        iat:         now,
    };
}

// ─────────────────────────────────────────────────────────────────────────────
// Scenario 1: Brute Force / Rate Limit
// ─────────────────────────────────────────────────────────────────────────────

function runBruteForce() {
    group('brute_force', () => {
        // Use unique email per iteration to avoid persistent lockout state
        const email = `attacker_${Date.now()}_${Math.random().toString(36).slice(2)}@evil.test`;
        const res = http.post(
            `${BASE_URL}/auth/login`,
            JSON.stringify({ email, password: 'wrongpassword123!' }),
            { headers: TEST_HEADERS, tags: { endpoint: 'login' } },
        );

        if (res.status === 429) {
            rateLimitTriggered.add(1);
            // Back off after rate limit — don't thrash
            sleep(1);
        }

        authFailRate.add(![200, 401, 403, 429].includes(res.status));

        check(res, {
            'login rejects unknown creds (401/403/429)': (r) =>
                [401, 403, 429].includes(r.status),
            'no 5xx from login endpoint': (r) => r.status < 500,
        });

        sleep(0.1);
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Scenario 2: JWT Attack Probes
// ─────────────────────────────────────────────────────────────────────────────

type JWTProbe = { name: string; token: string };

function buildJWTProbes(): JWTProbe[] {
    const now = Math.floor(Date.now() / 1000);
    const payload = demoPayload('super_admin');

    // Split a real token if provided (we only need the valid header/payload parts)
    const realParts = AUTH_TOKEN ? AUTH_TOKEN.split('.') : [];
    const realPayloadDecoded = realParts.length === 3
        ? (() => {
            try {
                return JSON.parse(Buffer.from(realParts[1], 'base64url').toString());
            } catch { return {}; }
          })()
        : {};

    return [
        // alg:none variants
        {
            name: 'alg_none_lowercase',
            token: craftToken({ alg: 'none', typ: 'JWT' }, payload),
        },
        {
            name: 'alg_NONE_uppercase',
            token: craftToken({ alg: 'NONE', typ: 'JWT' }, payload),
        },
        {
            name: 'alg_empty_string',
            token: craftToken({ alg: '', typ: 'JWT' }, payload),
        },
        // Algorithm confusion
        {
            name: 'rs256_algorithm_confusion',
            token: craftToken({ alg: 'RS256', typ: 'JWT' }, payload, 'fakesig'),
        },
        {
            name: 'hs512_algorithm_substitution',
            token: craftToken({ alg: 'HS512', typ: 'JWT' }, payload, 'fakesig'),
        },
        // Expired token
        {
            name: 'expired_1_hour_ago',
            token: craftToken(
                { alg: 'HS256', typ: 'JWT' },
                { ...payload, exp: now - 3600 },
                'invalidsignatureabc',
            ),
        },
        // Role escalation on a real token (swap payload, keep original sig)
        ...(realParts.length === 3 ? [{
            name: 'tampered_role_escalation',
            token: `${realParts[0]}.${b64url({ ...realPayloadDecoded, role: 'super_admin' })}.${realParts[2]}`,
        }, {
            name: 'tampered_building_to_production',
            token: `${realParts[0]}.${b64url({ ...realPayloadDecoded, building_id: '13195' })}.${realParts[2]}`,
        }] : []),
        // Completely invalid
        {
            name: 'garbage_token',
            token: 'not.a.real.jwt.whatsoever',
        },
        {
            name: 'empty_bearer',
            token: '',
        },
    ];
}

function runJWTAttacks() {
    const probes = buildJWTProbes();

    group('jwt_attacks', () => {
        for (const probe of probes) {
            const res = http.get(`${BASE_URL}/units/`, {
                headers: {
                    Authorization: probe.token ? `Bearer ${probe.token}` : '',
                    'Content-Type': 'application/json',
                },
                tags: { endpoint: 'units', probe: probe.name },
            });

            const allowed = res.status === 200;
            if (allowed) {
                jwtAttacksAllowed.add(1);
                console.error(`🔴 JWT ATTACK SUCCEEDED: probe=${probe.name} status=${res.status}`);
            }

            check(res, {
                [`JWT probe '${probe.name}' must return 401/403`]: (r) =>
                    [401, 403, 422].includes(r.status),
            });
        }

        sleep(0.5);
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Scenario 3: Cross-Building BOLA via Header Override
// ─────────────────────────────────────────────────────────────────────────────

function runCrossBuilding() {
    if (!AUTH_TOKEN) {
        check(null, { 'AUTH_TOKEN required for cross_building scenario': () => false });
        return;
    }

    group('cross_building', () => {
        // Attempt 1: X-Building-ID header to platform shell
        const r1 = http.get(`${BASE_URL}/units/`, {
            ...authHeaders({ 'X-Building-ID': OTHER_BUILDING }),
            tags: { endpoint: 'units_cross_building', override: 'x-building-id' },
        });

        // Attempt 2: query param override
        const r2 = http.get(`${BASE_URL}/units/?building_id=${OTHER_BUILDING}`, {
            ...authHeaders(),
            tags: { endpoint: 'units_query_override' },
        });

        // Attempt 3: finance endpoint with other building
        const r3 = http.get(`${BASE_URL}/finance/levies/?building_id=${OTHER_BUILDING}`, {
            ...authHeaders(),
            tags: { endpoint: 'finance_query_override' },
        });

        // Check baseline count (own building)
        const baseline = http.get(`${BASE_URL}/units/`, {
            ...authHeaders(),
            tags: { endpoint: 'units_baseline' },
        });

        // If r1 succeeded, verify it only returned data for the user's real building
        if (r1.status === 200) {
            try {
                const body = r1.json() as Record<string, unknown>;
                const items = (body.data ?? body.units ?? (Array.isArray(body) ? body : [])) as Record<string, unknown>[];
                const leaked = items.some(u => u.building_id && u.building_id !== DEMO_BUILDING);
                if (leaked) {
                    crossBuildingEscalations.add(1);
                    console.error(`🔴 BOLA: X-Building-ID header returned data from another building`);
                }
            } catch { /* non-JSON or empty */ }
        }

        // Query param override must not change the result set (should return same building's data or 403)
        if (r2.status === 200 && baseline.status === 200) {
            try {
                const overrideBody = r2.json() as Record<string, unknown>;
                const baseBody = baseline.json() as Record<string, unknown>;
                const overrideItems = (overrideBody.data ?? overrideBody.units ?? (Array.isArray(overrideBody) ? overrideBody : [])) as unknown[];
                const baseItems = (baseBody.data ?? baseBody.units ?? (Array.isArray(baseBody) ? baseBody : [])) as unknown[];

                const leaked = overrideItems.length !== baseItems.length;
                if (leaked) {
                    crossBuildingEscalations.add(1);
                    console.error(`🔴 BOLA: building_id query param changed result count (${baseItems.length} → ${overrideItems.length})`);
                }
            } catch { /* non-JSON */ }
        }

        check(r1, {
            'X-Building-ID override: non-admin gets own building data (not 5xx)': (r) => r.status < 500,
        });
        check(r2, {
            'building_id param override: not 5xx': (r) => r.status < 500,
        });
        check(r3, {
            'finance cross-building: not 5xx': (r) => r.status < 500,
        });

        sleep(0.2);
    });
}

// ─── Entry point ──────────────────────────────────────────────────────────────

export default function () {
    const scenario = __ENV.K6_SCENARIO || '';

    if (!scenario || scenario === 'brute_force')  runBruteForce();
    if (!scenario || scenario === 'jwt_attacks')  runJWTAttacks();
    if (!scenario || scenario === 'cross_building') runCrossBuilding();
}

// ─── Teardown ────────────────────────────────────────────────────────────────

export function teardown(_data: unknown): void {
    if (!AUTH_TOKEN) {
        console.warn('Security benchmark teardown skipped: AUTH_TOKEN required for /security/test-login-audits');
        return;
    }
    http.del(`${BASE_URL}/security/test-login-audits`, null, {
        headers: {Authorization: `Bearer ${AUTH_TOKEN}`},
    });
}
