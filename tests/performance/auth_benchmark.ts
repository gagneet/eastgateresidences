import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Counter, Rate} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        // Login smoke: 1 VU only — endpoint is rate-limited at 10/min by design.
        // Purpose: verify login works and bcrypt latency is acceptable.
        login_smoke: {
            executor: 'constant-vus',
            exec: 'loginScenario',
            vus: 1,
            duration: '15s',
            tags: {scenario: 'login_smoke'},
        },
        // Session validation load: simulates concurrent authenticated users hitting
        // the hot path on every page load (/auth/me, /auth/memberships).
        session_load: {
            executor: 'constant-vus',
            exec: 'sessionScenario',
            vus: 5,
            duration: '30s',
            tags: {scenario: 'session_load'},
        },
    },
    thresholds: {
        // bcrypt hashing makes login inherently slower than read endpoints
        'http_req_duration{endpoint:login}': ['p(95)<1500'],
        'http_req_duration{endpoint:me}': ['p(95)<200'],
        'http_req_duration{endpoint:memberships}': ['p(95)<300'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.95'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const ADMIN_EMAIL = __ENV.ADMIN_EMAIL || 'administrator@eastgateresidences.com.au';
const ADMIN_PASSWORD = __ENV.ADMIN_PASSWORD || process.env.E2E_ADMIN_PASSWORD || '';
const TOKEN = __ENV.AUTH_TOKEN;

const loginSuccessRate = new Rate('login_success_rate');
const rateLimitHits = new Counter('rate_limit_hits');

const TEST_HEADERS = {
    'Content-Type': 'application/json',
    'X-Test-Data': 'true',
};

function authHeaders(token: string) {
    return {
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
    };
}

// Single-VU login scenario — respects 10/min rate limit
export function loginScenario() {
    group('Login Flow', () => {
        const loginRes = http.post(
            `${BASE_URL}/auth/login`,
            JSON.stringify({email: ADMIN_EMAIL, password: ADMIN_PASSWORD}),
            {
                headers: TEST_HEADERS,
                tags: {endpoint: 'login'},
            }
        );

        if (loginRes.status === 429) {
            rateLimitHits.add(1);
            sleep(6); // back off to respect 10/min rate limit
            return;
        }

        const loginOk = check(loginRes, {
            'login 200': (r) => r.status === 200,
            'login returns token': (r) => {
                try {
                    const body = JSON.parse(r.body as string);
                    return typeof body.token === 'string' && body.token.length > 0;
                } catch {
                    return false;
                }
            },
        });
        loginSuccessRate.add(loginOk ? 1 : 0);
    });

    sleep(1);
}

// Multi-VU session scenario — tests the per-page-load hot path
export function sessionScenario() {
    if (!TOKEN) {
        // AUTH_TOKEN env var not provided — skip HTTP work but still pace iterations
        sleep(1);
        return;
    }

    group('Session Validation', () => {
        const me = http.get(`${BASE_URL}/auth/me`, {
            ...authHeaders(TOKEN),
            tags: {endpoint: 'me'},
        });
        check(me, {'/auth/me 200': (r) => r.status === 200});

        const memberships = http.get(`${BASE_URL}/auth/memberships`, {
            ...authHeaders(TOKEN),
            tags: {endpoint: 'memberships'},
        });
        check(memberships, {'/auth/memberships 200': (r) => r.status === 200});
    });

    sleep(1);
}

// Required by K6 even when using named scenario exec functions
export default function () {
}

export function teardown() {
    let cleanupToken = TOKEN;
    if (!cleanupToken) {
        const loginRes = http.post(
            `${BASE_URL}/auth/login`,
            JSON.stringify({email: ADMIN_EMAIL, password: ADMIN_PASSWORD}),
            {headers: TEST_HEADERS},
        );
        if (loginRes.status === 200) {
            try {
                cleanupToken = JSON.parse(loginRes.body as string).token;
            } catch {
                cleanupToken = '';
            }
        }
    }
    if (!cleanupToken) {
        console.warn('Auth benchmark teardown skipped: no token available for /security/test-login-audits');
        return;
    }
    http.del(`${BASE_URL}/security/test-login-audits`, null, {
        headers: {Authorization: `Bearer ${cleanupToken}`},
    });
}
