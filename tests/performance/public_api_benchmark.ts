import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        browse: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 5},
                {duration: '20s', target: 5},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'browse'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:buildings}': ['p(95)<400'],
        'http_req_duration{endpoint:units}': ['p(95)<700'],
        'http_req_duration{endpoint:bylaws}': ['p(95)<700'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const BUILDING_ID = __ENV.BUILDING_ID || '13195';

export function teardown(_data: unknown): void {}

export default function () {
    group('public_auth_and_registration_paths', () => {
        const buildings = http.get(`${BASE_URL}/auth/buildings`, {
            tags: {endpoint: 'buildings'},
        });
        check(buildings, {
            'GET /auth/buildings 200': (r) => r.status === 200,
            'buildings returns list': (r) => {
                try {
                    return Array.isArray(JSON.parse(r.body as string));
                } catch {
                    return false;
                }
            },
        });

        const units = http.get(`${BASE_URL}/units/all?building_id=${encodeURIComponent(BUILDING_ID)}`, {
            tags: {endpoint: 'units'},
            headers: {'X-Building-ID': BUILDING_ID},
        });
        check(units, {
            'GET /units/all 200': (r) => r.status === 200,
            'units returns list': (r) => {
                try {
                    return Array.isArray(JSON.parse(r.body as string));
                } catch {
                    return false;
                }
            },
        });

        const bylaws = http.get(`${BASE_URL}/by-laws/current?building_id=${encodeURIComponent(BUILDING_ID)}`, {
            tags: {endpoint: 'bylaws'},
            headers: {'X-Building-ID': BUILDING_ID},
        });
        check(bylaws, {
            'GET /by-laws/current success or not-found': (r) => r.status === 200 || r.status === 404,
        });
    });

    sleep(1);
}
