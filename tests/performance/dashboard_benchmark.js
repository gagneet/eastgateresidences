import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    vus: 1,
    duration: '10s',
    thresholds: {
        http_req_duration: ['p(95)<500'], // 95% of requests should be below 500ms
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;
const BUILDING_ID = __ENV.BUILDING_ID || '13195';

export function setup() {
    if (!TOKEN) {
        throw new Error('AUTH_TOKEN env var is required for dashboard benchmarks');
    }
}

export default function () {
    const params = {
        headers: {
            'Authorization': `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
            'X-Building-ID': BUILDING_ID,
        },
    };

    const endpoints = [
        '/community-dashboard/building-summary',
        '/finance/summary',
        '/work-orders',
        '/maintenance',
        '/notices',
        '/documents',
        '/meetings?upcoming=true',
    ];

    for (const endpoint of endpoints) {
        const res = http.get(`${BASE_URL}${endpoint}`, params);
        check(res, {
            [ `${endpoint} status is 200` ]: (r) => r.status === 200,
        });
    }

    sleep(1);
}

export function teardown() {
    // Read-only benchmark; no test records are created.
}
