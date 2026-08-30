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
    },
    thresholds: {
        'http_req_duration{endpoint:tech_docs_index}': ['p(95)<500'],
        'http_req_duration{endpoint:canonical_owners}': ['p(95)<500'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const FRONTEND_URL = __ENV.FRONTEND_URL || 'http://127.0.0.1:3020';

export function teardown(_data: unknown): void {
    // Read-only static-doc benchmark. No test data is created.
}

export default function () {
    group('Canonical owner tech docs', () => {
        const index = http.get(`${FRONTEND_URL}/tech-docs/index.html`, {
            tags: {endpoint: 'tech_docs_index'},
        });
        check(index, {
            'tech-docs index responds': (r) => r.status === 200,
            'index links canonical owners': (r) =>
                String(r.body).includes('href="canonical-owners.html"'),
        });

        const registry = http.get(`${FRONTEND_URL}/tech-docs/canonical-owners.html`, {
            tags: {endpoint: 'canonical_owners'},
        });
        check(registry, {
            'canonical owners responds': (r) => r.status === 200,
            'registry names frontend concept': (r) => String(r.body).includes('api-error-detail'),
            'registry includes python entries': (r) => String(r.body).includes('data-language="python"'),
            'registry includes javascript entries': (r) => String(r.body).includes('data-language="javascript"'),
        });
    });

    sleep(1);
}
