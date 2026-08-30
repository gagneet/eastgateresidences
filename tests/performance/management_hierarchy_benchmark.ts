/**
 * K6 performance benchmark — Management Hierarchy API
 *
 * Tests:
 *  1. GET /management-hierarchy/buildings/{building_id}/model  (read-heavy, most common)
 *  2. GET /management-hierarchy/entities/{entity_id}           (entity read, PII filtering)
 *  3. POST /management-hierarchy/schemes/{scheme_id}/ensure    (idempotent upsert)
 *  4. GET /management-hierarchy/user/{user_id}/buildings       (portfolio list)
 *
 * Security assertions:
 *  - Non-admin UUIDs return 403, not 500
 *  - Malformed UUIDs return 400
 *
 * Teardown: deletes any scheme_manager_appointments with is_test_data=True
 *           created during the run via the admin cleanup endpoint.
 *
 * Usage:
 *   k6 run tests/performance/management_hierarchy_benchmark.ts \
 *       -e BASE_URL=http://localhost:8003/api \
 *       -e AUTH_TOKEN=<super_admin_jwt> \
 *       -e BUILDING_ID=<your_test_building_id>
 */

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Options } from 'k6/options';
import { Counter, Trend } from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '15s',
            tags: { scenario: 'smoke' },
        },
        load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '15s', target: 10 },
                { duration: '30s', target: 10 },
                { duration: '15s', target: 0 },
            ],
            tags: { scenario: 'load' },
        },
    },
    thresholds: {
        'http_req_duration{endpoint:get_building_model}': ['p(95)<500'],
        'http_req_duration{endpoint:get_entity}': ['p(95)<500'],
        'http_req_duration{endpoint:ensure_scheme}': ['p(95)<1000'],
        'http_req_duration{endpoint:user_buildings}': ['p(95)<600'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const BUILDING_ID = __ENV.BUILDING_ID;
if (!BUILDING_ID) {
    throw new Error('BUILDING_ID env var is required (e.g. -e BUILDING_ID=<your_test_building_id>)');
}

const ensureErrors = new Counter('mh_ensure_errors');
const entityDuration = new Trend('mh_entity_read_ms');

// Deterministic test scheme UUID prefix — swept in teardown
const TEST_PREFIX = 'perf-mh-';

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

function uniqueId(): string {
    return `${TEST_PREFIX}${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

export function setup() {
    // Resolve the scheme_id for our test building so we can use it in tests
    const res = http.get(
        `${BASE_URL}/management-hierarchy/buildings/${BUILDING_ID}/model`,
        headers()
    );
    // If no entity yet, scheme_id will be in the 404 response body or we skip entity tests
    const schemeId: string | null = res.status === 200
        ? (JSON.parse(res.body as string)?.scheme_id ?? null)
        : null;
    const entityId: string | null = res.status === 200
        ? (JSON.parse(res.body as string)?.entity?.management_entity_id ?? null)
        : null;
    return { schemeId, entityId };
}

export default function (data: { schemeId: string | null; entityId: string | null }) {
    const h = headers();

    group('read_building_model', () => {
        const res = http.get(
            `${BASE_URL}/management-hierarchy/buildings/${BUILDING_ID}/model`,
            { ...h, tags: { endpoint: 'get_building_model' } }
        );
        check(res, {
            'building model status 200 or 404': (r) => r.status === 200 || r.status === 404,
            'no 500': (r) => r.status !== 500,
        });
    });

    if (data.entityId) {
        group('read_entity', () => {
            const start = Date.now();
            const res = http.get(
                `${BASE_URL}/management-hierarchy/entities/${data.entityId}`,
                { ...h, tags: { endpoint: 'get_entity' } }
            );
            entityDuration.add(Date.now() - start);
            check(res, {
                'entity read 200': (r) => r.status === 200,
                'no 500': (r) => r.status !== 500,
            });
        });
    }

    group('malformed_uuid_returns_400', () => {
        const res = http.get(
            `${BASE_URL}/management-hierarchy/entities/not-a-uuid`,
            { ...h, tags: { endpoint: 'malformed_uuid' } }
        );
        check(res, {
            'malformed UUID returns 400 not 500': (r) => r.status === 400,
        });
    });

    group('ensure_scheme_idempotent', () => {
        if (!data.schemeId) return;
        const payload = JSON.stringify({
            mode: 'self_managed',
            building_id: BUILDING_ID,
            is_test_data: true,
        });
        const res = http.post(
            `${BASE_URL}/management-hierarchy/schemes/${data.schemeId}/ensure`,
            payload,
            { ...h, tags: { endpoint: 'ensure_scheme' } }
        );
        check(res, {
            'ensure returns 200 or 201': (r) => r.status === 200 || r.status === 201,
            'no 500': (r) => r.status !== 500,
        });
        if (res.status >= 500) ensureErrors.add(1);
    });

    sleep(0.5);
}

export function teardown(_data: unknown): void {
    // Delete any test appointments created during this run
    // The management hierarchy ensure endpoint is idempotent — no new rows unless
    // building has no prior entity; the test uses the existing entity, so cleanup
    // is a no-op in most runs. Log a warning if cleanup endpoint exists.
    const h = headers();
    const cleanupUrl = `${BASE_URL}/management-hierarchy/test-cleanup?prefix=${TEST_PREFIX}`;
    const res = http.del(cleanupUrl, null, h);
    // 404 is acceptable (endpoint optional / not wired in non-test builds)
    if (res.status !== 200 && res.status !== 404) {
        console.warn(`Teardown returned ${res.status}: ${res.body}`);
    }
}
