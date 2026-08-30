// @featuretrace:nsw-compliance — K6 benchmark for the NSW compliance read surface.
// Data flow: k6 -> /api/nsw/* read contracts -> backend/routers/nsw_compliance.py
//            -> Mongo compliance/report collections + PDF generators.
// Related: frontend/src/app/(app)/compliance/page.tsx
//          backend/routers/nsw_compliance.py
//
// Zero-coverage gap closed: nsw_compliance.py had 17 routes with no performance
// benchmark. Strata-hub exports and the CWF/s184 PDF generators derive statutory
// state across the whole scheme — the compliance reads to guard.
//
// Read-only: only GET probes. The optional PDF probes (cwf-schedule.pdf,
// s184-certificate.pdf) are heavier (document generation) and gated behind
// INCLUDE_PDF=1. No report/return/disclosure is created.
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {executor: 'constant-vus', vus: 1, duration: '10s', tags: {scenario: 'smoke'}},
        nsw_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 4},
                {duration: '30s', target: 4},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'nsw_load'},
        },
    },
    thresholds: {
        'http_req_duration{surface:nsw_list}': ['p(95)<800'],
        'http_req_duration{surface:nsw_export}': ['p(95)<1500'],
        'http_req_duration{surface:nsw_pdf}': ['p(95)<5000'],
        nsw_compliance_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';
const INCLUDE_PDF = (__ENV.INCLUDE_PDF || '') === '1';

const requiredErrors = new Counter('nsw_compliance_required_errors');
const endpointDuration = new Trend('nsw_compliance_endpoint_ms');

function headers() {
    const h: Record<string, string> = {Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json'};
    if (BUILDING_ID) {
        h['X-Building-ID'] = BUILDING_ID;
    }
    return {headers: h};
}

function firstId(body: string): string {
    try {
        const parsed = JSON.parse(body);
        const arr = Array.isArray(parsed) ? parsed : parsed.items || parsed.results || [];
        if (Array.isArray(arr) && arr.length > 0) {
            const rec = arr[0];
            return String(rec.id || rec._id || '');
        }
    } catch {
        // fall through
    }
    return '';
}

function probe(path: string, name: string, surface: string, required: boolean) {
    const res = http.get(`${BASE_URL}${path}`, {...headers(), tags: {endpoint: name, surface}});
    endpointDuration.add(res.timings.duration, {endpoint: name});
    if (required) {
        const ok = check(res, {[`${name} returns 200`]: (r) => r.status === 200});
        if (!ok) {
            requiredErrors.add(1, {endpoint: name});
        }
    } else {
        check(res, {[`${name} avoids server error`]: (r) => r.status < 500});
    }
    return res;
}

export function setup() {
    if (!TOKEN) {
        throw new Error('AUTH_TOKEN env var is required for NSW compliance benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for NSW compliance probes.');
    }
    const reports = http.get(`${BASE_URL}/nsw/reports`, headers());
    const returns = http.get(`${BASE_URL}/nsw/strata-hub-returns`, headers());
    const disclosures = http.get(`${BASE_URL}/nsw/commission-disclosures`, headers());
    return {
        reportId: reports.status === 200 ? firstId(reports.body as string) : '',
        returnId: returns.status === 200 ? firstId(returns.body as string) : '',
        disclosureId: disclosures.status === 200 ? firstId(disclosures.body as string) : '',
    };
}

export default function (data: {reportId: string; returnId: string; disclosureId: string}) {
    group('nsw_lists', () => {
        probe('/nsw/reports', 'reports_list', 'nsw_list', true);
        probe('/nsw/strata-hub-returns', 'strata_hub_returns_list', 'nsw_list', true);
        probe('/nsw/commission-disclosures', 'commission_disclosures_list', 'nsw_list', true);
    });

    sleep(0.5);

    group('nsw_exports', () => {
        probe('/nsw/strata-hub-export', 'strata_hub_export', 'nsw_export', false);
        probe('/nsw/levy-notice-hardship', 'levy_notice_hardship', 'nsw_export', false);
        if (data.reportId) {
            probe(`/nsw/reports/${encodeURIComponent(data.reportId)}`, 'report_detail', 'nsw_list', false);
        }
        if (data.returnId) {
            probe(`/nsw/strata-hub-returns/${encodeURIComponent(data.returnId)}`, 'strata_hub_return_detail', 'nsw_list', false);
        }
        if (data.disclosureId) {
            probe(`/nsw/commission-disclosures/${encodeURIComponent(data.disclosureId)}`, 'commission_disclosure_detail', 'nsw_list', false);
        }
    });

    if (INCLUDE_PDF) {
        group('nsw_pdf', () => {
            probe('/nsw/cwf-schedule.pdf', 'cwf_schedule_pdf', 'nsw_pdf', false);
            probe('/nsw/s184-certificate.pdf', 's184_certificate_pdf', 'nsw_pdf', false);
        });
    }

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No compliance records are created.
}
