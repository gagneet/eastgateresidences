/**
 * k6 performance benchmark — WeasyPrint Letter Generator
 *
 * Scenarios:
 *   smoke — 1 VU, 30s   (WeasyPrint is slow; single-VU ensures baseline)
 *   load  — ramp 0→5→5→0 over 60s (concurrent letter generation)
 *
 * Groups:
 *   list_templates  — GET /letters/templates
 *   generate_letter — POST /letters/generate for each template
 *
 * Teardown: no-op. This benchmark calls /letters/generate only, which streams a
 * preview PDF and does not create letters_log records. Do not switch this file
 * to /letters/send unless that route first gains a guarded is_test_data path
 * and the benchmark deletes the resulting tagged letters_log records.
 *
 * Note: WeasyPrint PDF generation takes 1–5s CPU. p95 threshold is 5000ms.
 */

import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '30s',
            tags: {scenario: 'smoke'},
        },
        load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 5},
                {duration: '30s', target: 5},
                {duration: '15s', target: 0},
            ],
            tags: {scenario: 'load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:list_templates}': ['p(95)<200'],
        'http_req_duration{endpoint:generate_levy_reminder}': ['p(95)<5000'],
        'http_req_duration{endpoint:generate_agm_invitation}': ['p(95)<5000'],
        'http_req_duration{endpoint:generate_general_notice}': ['p(95)<5000'],
        http_req_failed: ['rate<0.05'],  // WeasyPrint may be unavailable in CI → lenient
        checks: ['rate>0.90'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

const generateErrors = new Counter('letter_generate_errors');
const generateDuration = new Trend('letter_generate_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

const LEVY_DATA = {
    owner_name: 'Perf Test Owner',
    unit_number: '99',
    amount_due: '1530.50',
    due_date: '2026-06-01',
    quarter: 'Q2 2026',
    payment_ref: 'PERF-99-Q2',
};

const AGM_DATA = {
    owner_name: 'Perf Test Owner',
    agm_date: '2026-07-15',
    agm_time: '6:30 PM',
    agm_location: 'Common Room, Level 2',
    agenda_items: '1. Welcome\n2. Financials\n3. Election of EC',
};

const NOTICE_DATA = {
    owner_name: 'Perf Test Owner',
    subject: 'k6 Perf Test Notice',
    body: 'This is an automated performance test letter. Safe to delete.',
    closing: 'Regards',
};

// ── Teardown ──────────────────────────────────────────────────────────────────

export function teardown(_data: unknown): void {
    // /letters/generate is read/preview-only from a persistence perspective.
    // It does not write letters_log, so there is nothing to delete.
}

// ── Default function ──────────────────────────────────────────────────────────

export default function () {
    const h = headers();

    // ── 1. List templates ───────────────────────────────────────────────────────
    group('list_templates', () => {
        const resp = http.get(`${BASE_URL}/letters/templates`, {
            ...h,
            tags: {endpoint: 'list_templates'},
        });
        check(resp, {
            'GET /letters/templates 200': (r) => r.status === 200,
            'returns 3 templates': (r) => {
                try {
                    return (JSON.parse(r.body as string) as any[]).length >= 3;
                } catch {
                    return false;
                }
            },
        });
    });

    sleep(0.5);

    // ── 2. Generate levy reminder ───────────────────────────────────────────────
    group('generate_levy_reminder', () => {
        const t0 = Date.now();
        const resp = http.post(
            `${BASE_URL}/letters/generate`,
            JSON.stringify({template: 'levy_reminder', data: LEVY_DATA}),
            {...h, tags: {endpoint: 'generate_levy_reminder'}},
        );
        const ok = check(resp, {
            'POST /letters/generate (levy) 200': (r) => r.status === 200,
            'returns PDF content-type': (r) => (r.headers['Content-Type'] ?? '').includes('application/pdf'),
            'PDF not empty': (r) => (r.body as string)?.length > 0,
        });
        if (!ok) generateErrors.add(1);
        generateDuration.add(Date.now() - t0);
    });

    sleep(1);

    // ── 3. Generate AGM invitation ──────────────────────────────────────────────
    group('generate_agm_invitation', () => {
        const t0 = Date.now();
        const resp = http.post(
            `${BASE_URL}/letters/generate`,
            JSON.stringify({template: 'agm_invitation', data: AGM_DATA}),
            {...h, tags: {endpoint: 'generate_agm_invitation'}},
        );
        check(resp, {
            'POST /letters/generate (agm) 200': (r) => r.status === 200,
            'returns PDF': (r) => (r.headers['Content-Type'] ?? '').includes('application/pdf'),
        });
        generateDuration.add(Date.now() - t0);
    });

    sleep(1);

    // ── 4. Generate general notice ──────────────────────────────────────────────
    group('generate_general_notice', () => {
        const t0 = Date.now();
        const resp = http.post(
            `${BASE_URL}/letters/generate`,
            JSON.stringify({template: 'general_notice', data: NOTICE_DATA}),
            {...h, tags: {endpoint: 'generate_general_notice'}},
        );
        check(resp, {
            'POST /letters/generate (notice) 200': (r) => r.status === 200,
            'returns PDF': (r) => (r.headers['Content-Type'] ?? '').includes('application/pdf'),
        });
        generateDuration.add(Date.now() - t0);
    });

    sleep(2);
}
