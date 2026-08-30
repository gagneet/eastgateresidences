import http from 'k6/http';
import {check, group, sleep} from 'k6';

export const options = {
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
                {duration: '10s', target: 3},
                {duration: '20s', target: 3},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'browse'},
        },
    },
    thresholds: {
        'http_req_duration{page:home}': ['p(95)<900'],
        'http_req_duration{page:login}': ['p(95)<900'],
        'http_req_duration{page:register}': ['p(95)<900'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const UI_BASE_URL = __ENV.UI_BASE_URL || 'http://localhost:3000';

export function teardown(_data) {}

function isHtml(res) {
    return String(res.headers['Content-Type'] || '').includes('text/html');
}

export default function () {
    group('public_page_loads', () => {
        const home = http.get(`${UI_BASE_URL}/`, {tags: {page: 'home'}});
        check(home, {
            'GET / 200': (r) => r.status === 200,
            'home returns html': isHtml,
        });

        const login = http.get(`${UI_BASE_URL}/login`, {tags: {page: 'login'}});
        check(login, {
            'GET /login 200': (r) => r.status === 200,
            'login returns html': isHtml,
        });

        const register = http.get(`${UI_BASE_URL}/register`, {tags: {page: 'register'}});
        check(register, {
            'GET /register 200': (r) => r.status === 200,
            'register returns html': isHtml,
        });
    });

    sleep(1);
}
