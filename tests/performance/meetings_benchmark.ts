// @featuretrace:governance-meetings — K6 benchmark for the meetings / AGM read surface.
// Data flow: k6 -> /api/{meetings,agm,todos} read contracts -> backend/routers/meetings.py
//            -> Mongo governance collections.
// Related: frontend/src/app/(app)/governance/meetings/page.tsx
//          frontend/src/app/(app)/governance/agm/page.tsx
//          backend/routers/meetings.py
//
// Zero-coverage gap closed: meetings.py had 17 routes with no performance
// benchmark (k6_performance_coverage_report_2026-08-10). AGM motion tally and
// attendance/results endpoints aggregate votes across the whole building — a
// governance-critical read that must stay fast during live meetings.
//
// Read-only: only GET probes. setup() resolves a live meeting_id and agm_id from
// the list endpoints so the detail/motions/attendance/results reads are exercised
// against real ids. No meeting, motion, vote, or attendance record is written.
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        meetings_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 4},
                {duration: '30s', target: 4},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'meetings_load'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:meetings_list}': ['p(95)<600'],
        'http_req_duration{endpoint:agm_list}': ['p(95)<600'],
        'http_req_duration{endpoint:todos_list}': ['p(95)<600'],
        'http_req_duration{endpoint:meeting_detail}': ['p(95)<500'],
        'http_req_duration{endpoint:agm_motions}': ['p(95)<800'],
        'http_req_duration{endpoint:agm_attendance}': ['p(95)<800'],
        'http_req_duration{endpoint:agm_results}': ['p(95)<1000'],
        meetings_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';

const requiredErrors = new Counter('meetings_required_errors');
const endpointDuration = new Trend('meetings_endpoint_ms');

function headers() {
    const h: Record<string, string> = {
        Authorization: `Bearer ${TOKEN}`,
        'Content-Type': 'application/json',
    };
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
            return String(rec.id || rec._id || rec.meeting_id || rec.agm_id || '');
        }
    } catch {
        // fall through
    }
    return '';
}

function probe(path: string, name: string, required: boolean) {
    const res = http.get(`${BASE_URL}${path}`, {
        ...headers(),
        tags: {endpoint: name},
    });
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
        throw new Error('AUTH_TOKEN env var is required for meetings benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for meetings probes.');
    }
    // Resolve one live meeting and one live AGM so detail probes hit real data.
    const meetingsRes = http.get(`${BASE_URL}/meetings?limit=1`, headers());
    const agmRes = http.get(`${BASE_URL}/agm`, headers());
    return {
        meetingId: meetingsRes.status === 200 ? firstId(meetingsRes.body as string) : '',
        agmId: agmRes.status === 200 ? firstId(agmRes.body as string) : '',
    };
}

export default function (data: {meetingId: string; agmId: string}) {
    group('meetings_lists', () => {
        probe('/meetings?limit=25', 'meetings_list', true);
        probe('/agm', 'agm_list', true);
        probe('/todos?limit=25', 'todos_list', true);
    });

    sleep(0.5);

    if (data.meetingId) {
        group('meetings_detail', () => {
            const m = encodeURIComponent(data.meetingId);
            probe(`/meetings/${m}`, 'meeting_detail', false);
        });
    }

    if (data.agmId) {
        group('agm_detail', () => {
            const a = encodeURIComponent(data.agmId);
            probe(`/agm/${a}/motions`, 'agm_motions', false);
            probe(`/agm/${a}/attendance`, 'agm_attendance', false);
            probe(`/agm/${a}/results`, 'agm_results', false);
        });
    }

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No governance records are created.
}
