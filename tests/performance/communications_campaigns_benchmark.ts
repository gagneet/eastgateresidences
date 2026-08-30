// @featuretrace:email-delivery — K6 benchmark for the communications campaigns read surface.
// Data flow: k6 -> /api/communications/* read contracts -> backend/routers/communications_campaigns.py
//            -> Mongo campaign/newsletter/delivery collections.
// Related: frontend/src/app/(app)/admin/communications/page.tsx
//          backend/routers/communications_campaigns.py
//
// Zero-coverage gap closed: communications_campaigns.py had 26 routes with no
// performance benchmark. Delivery-event and acknowledgement lists grow per
// recipient per send, so these list reads are the latency to guard.
//
// Read-only: only GET probes. Draft/campaign/newsletter creation, send, and
// scheduling POSTs are excluded (they dispatch real email).
import http from 'k6/http';
import {check, group, sleep} from 'k6';
import {Counter, Trend} from 'k6/metrics';
import {Options} from 'k6/options';

export const options: Options = {
    scenarios: {
        smoke: {executor: 'constant-vus', vus: 1, duration: '10s', tags: {scenario: 'smoke'}},
        campaigns_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '15s', target: 4},
                {duration: '30s', target: 4},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'campaigns_load'},
        },
    },
    thresholds: {
        'http_req_duration{surface:campaigns_list}': ['p(95)<800'],
        'http_req_duration{surface:campaigns_detail}': ['p(95)<1000'],
        communications_campaigns_required_errors: ['count==0'],
        http_req_failed: ['rate<0.02'],
        checks: ['rate>0.98'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN || '';
const BUILDING_ID = __ENV.BUILDING_ID || '';

const requiredErrors = new Counter('communications_campaigns_required_errors');
const endpointDuration = new Trend('communications_campaigns_endpoint_ms');

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
        throw new Error('AUTH_TOKEN env var is required for communications campaigns benchmarks.');
    }
    if (!BUILDING_ID) {
        throw new Error('BUILDING_ID env var is required for communications campaigns probes.');
    }
    const drafts = http.get(`${BASE_URL}/communications/drafts`, headers());
    const newsletters = http.get(`${BASE_URL}/communications/newsletters`, headers());
    const campaigns = http.get(`${BASE_URL}/communications/campaigns`, headers());
    return {
        draftId: drafts.status === 200 ? firstId(drafts.body as string) : '',
        newsletterId: newsletters.status === 200 ? firstId(newsletters.body as string) : '',
        campaignId: campaigns.status === 200 ? firstId(campaigns.body as string) : '',
    };
}

export default function (data: {draftId: string; newsletterId: string; campaignId: string}) {
    group('campaigns_lists', () => {
        probe('/communications/drafts', 'drafts_list', 'campaigns_list', true);
        probe('/communications/newsletters', 'newsletters_list', 'campaigns_list', true);
        probe('/communications/campaigns', 'campaigns_list', 'campaigns_list', true);
    });

    sleep(0.5);

    if (data.draftId) {
        probe(`/communications/drafts/${encodeURIComponent(data.draftId)}`, 'draft_detail', 'campaigns_detail', false);
    }
    if (data.newsletterId) {
        const n = encodeURIComponent(data.newsletterId);
        group('newsletter_detail', () => {
            probe(`/communications/newsletters/${n}`, 'newsletter_detail', 'campaigns_detail', false);
            probe(`/communications/newsletters/${n}/delivery-events`, 'newsletter_delivery_events', 'campaigns_detail', false);
            probe(`/communications/newsletters/${n}/acknowledgements`, 'newsletter_acks', 'campaigns_detail', false);
        });
    }
    if (data.campaignId) {
        const c = encodeURIComponent(data.campaignId);
        group('campaign_detail', () => {
            probe(`/communications/campaigns/${c}`, 'campaign_detail', 'campaigns_detail', false);
            probe(`/communications/campaigns/${c}/delivery`, 'campaign_delivery', 'campaigns_detail', false);
            probe(`/communications/campaigns/${c}/acknowledgements`, 'campaign_acks', 'campaigns_detail', false);
        });
    }

    sleep(1);
}

export function teardown(_data: unknown): void {
    // Read-only benchmark. No drafts, newsletters, or campaigns are created or sent.
}
