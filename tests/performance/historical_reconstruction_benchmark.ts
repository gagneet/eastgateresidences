import http from 'k6/http';
import {check, sleep, group} from 'k6';
import {Options} from 'k6/options';
import {Trend} from 'k6/metrics';

// GAP-FIN-015 criterion 6 — POST /financial/matching/historical-reconstruction/run.
//
// Deliberately dry_run-only. Unlike other benchmark scripts in this directory, this
// endpoint's real (dry_run=false) mode writes financial-ledger-adjacent
// demo_bank_transactions rows that are NOT is_test_data-tagged (they are meant to be
// genuine per-building historical reconstructions, not throwaway fixtures — the
// endpoint's request body has no is_test_data field to opt into safe-to-delete test
// rows). A k6 load test must never mutate real financial data, so this script only
// ever sends dry_run: true, which the endpoint guarantees performs zero database
// writes (see tests/backend/test_historical_levy_reconstruction_service.py::
// test_dry_run_does_not_write_to_demo_bank_transactions). teardown() is therefore a
// documented no-op, not an oversight — there is nothing to clean up.
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
        'http_req_duration{endpoint:historical_reconstruction_dry_run}': ['p(95)<2000'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

const dryRunDuration = new Trend('historical_reconstruction_dry_run_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

export function teardown(_data: unknown): void {
    // No-op by design — every request in this script uses dry_run: true, which the
    // endpoint guarantees writes zero rows. Nothing to delete.
}

export default function () {
    const h = headers();

    group('Historical Reconstruction — dry-run only', () => {
        const payload = JSON.stringify({
            from_year: 2021,
            to_year: 2021,
            dry_run: true,
        });

        const run = http.post(
            `${BASE_URL}/financial/matching/historical-reconstruction/run`,
            payload,
            {...h, tags: {endpoint: 'historical_reconstruction_dry_run'}},
        );
        check(run, {
            'POST historical-reconstruction/run 200 or 409': (r) =>
                // 409 is a legitimate response for a building with no annual_levies
                // for the requested year range — not every test building has 2021 data.
                r.status === 200 || r.status === 409,
            'response is well-formed on 200': (r) => {
                if (r.status !== 200) return true;
                try {
                    const body = JSON.parse(r.body as string);
                    return body.dry_run === true && typeof body.inserted === 'number';
                } catch {
                    return false;
                }
            },
        });
        dryRunDuration.add(run.timings.duration);
    });

    sleep(1);
}
