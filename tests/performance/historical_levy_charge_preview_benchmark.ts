// @featuretrace:financial-onboarding — k6 benchmark for the unpersisted, batch-independent
//   historical levy charge preview endpoint.
// Layer: test
// Data flow: k6 -> POST /financials/onboarding/building/{id}/levy-charges/preview ->
//            generate_historical_levy_charge_plan() (Mongo annual_levies/units reads + Postgres
//            finance.levy_items/core.ownership_periods reads) -> response. Read-only — no writes,
//            no teardown required.
// Related: backend/routers/financial_onboarding.py (preview_historical_levy_charges)
//          backend/services/reconstruction_generators/historical_levy_charge_generator.py
//
// Building-agnostic by design — BUILDING_ID has NO default (must be passed explicitly). This
// endpoint performs zero writes, so there is nothing to tear down; still runs as a smoke scenario
// to catch latency regressions on a real building's annual_levies/units/ownership-periods volume.
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
    },
    thresholds: {
        'http_req_duration{endpoint:levy_charge_preview}': ['p(95)<3000'],
        historical_levy_charge_preview_required_errors: ['count==0'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';
const FROM_YEAR = Number(__ENV.FROM_YEAR || '2021');
const TO_YEAR = Number(__ENV.TO_YEAR || '2026');

const requiredErrors = new Counter('historical_levy_charge_preview_required_errors');
const previewDuration = new Trend('historical_levy_charge_preview_ms', true);

function requireConfig(): void {
    if (!TOKEN) {
        throw new Error(
            'AUTH_TOKEN env var is required. Example: k6 run tests/performance/historical_levy_charge_preview_benchmark.ts ' +
            '-e BASE_URL=http://localhost:8003/api -e AUTH_TOKEN=<jwt> -e BUILDING_ID=<scheme_number>',
        );
    }
    if (!BUILDING_ID) {
        throw new Error(
            'BUILDING_ID env var is required — this script is building-agnostic by design and has no ' +
            'default (never assume East Gate/13195). Pass -e BUILDING_ID=<scheme_number> for the ' +
            'building to preview against.',
        );
    }
}

function authHeaders() {
    return {Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json'};
}

export function setup(): void {
    requireConfig();
}

export default function (): void {
    group('Unpersisted levy charge preview (read-only, no writes)', () => {
        const res = http.post(
            `${BASE_URL}/financials/onboarding/building/${BUILDING_ID}/levy-charges/preview`,
            JSON.stringify({from_year: FROM_YEAR, to_year: TO_YEAR}),
            {headers: authHeaders(), tags: {endpoint: 'levy_charge_preview'}},
        );
        previewDuration.add(res.timings.duration);
        // 200 = plan computed (postable and/or planned_not_posted lines); 422 is legitimate — a
        // building with no annual_levies/units data in the requested range correctly reports
        // "nothing to generate" rather than a 200 with an empty body.
        const ok = check(res, {
            'POST levy-charges/preview 200 or 422': (r) => r.status === 200 || r.status === 422,
            'response has expected shape when 200': (r) => {
                if (r.status !== 200) return true;
                try {
                    const body = JSON.parse(r.body as string);
                    return (
                        typeof body.postable_line_count === 'number' &&
                        typeof body.total_principal_cents === 'number' &&
                        Array.isArray(body.by_year_fund_period)
                    );
                } catch {
                    return false;
                }
            },
        });
        if (!ok) requiredErrors.add(1);
    });

    sleep(1);
}

export function teardown() {
    // Nothing to clean up, and that is verifiable rather than assumed: the only
    // write this benchmark issues is POST .../levy-charges/preview, whose handler
    // (backend/routers/financial_onboarding.py::preview_historical_levy_charges) is
    // documented and implemented as a "pure, unpersisted" projection — it computes
    // the levy-charge schedule and returns it with no inserts, updates or batch
    // manifest. No record is created, so there is no is_test_data residue to sweep.
    //
    // If that endpoint ever gains a persisting path, this teardown must change from
    // a no-op to a real cleanup (see the teardown rules in the project CLAUDE.md).
}
