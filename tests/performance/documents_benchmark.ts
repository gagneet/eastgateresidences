/**
 * k6 performance benchmark — Documents, PDF Preview & Annotations
 *
 * Scenarios:
 *   smoke  — 1 VU, 10s   (baseline sanity)
 *   burst  — ramp 0→10→10→0 over 40s (concurrent annotation writers)
 *
 * Groups:
 *   read_documents      — GET /documents list
 *   preview_document    — GET /documents/{id} with file_data (base64 payload)
 *   annotations_crud    — POST annotation → GET list → DELETE (inline cleanup)
 *
 * Teardown: deletes all test documents (title prefix "Perf test doc perf-") and
 *           any surviving annotations for those documents (MongoDB has no cascade).
 *
 * Required env vars:
 *   AUTH_TOKEN — bearer token for a manager-role user (validated at startup)
 *   BASE_URL   — optional, defaults to http://localhost:8003/api
 */

import http from 'k6/http';
import encoding from 'k6/encoding';
import {check, group, sleep} from 'k6';
import {Options} from 'k6/options';
import {Counter, Trend} from 'k6/metrics';

export const options: Options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '10s',
            tags: {scenario: 'smoke'},
        },
        burst: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                {duration: '10s', target: 10},
                {duration: '20s', target: 10},
                {duration: '10s', target: 0},
            ],
            tags: {scenario: 'burst'},
        },
    },
    thresholds: {
        'http_req_duration{endpoint:list_documents}': ['p(95)<500'],
        'http_req_duration{endpoint:preview_document}': ['p(95)<1200'],
        'http_req_duration{endpoint:create_annotation}': ['p(95)<600'],
        'http_req_duration{endpoint:list_annotations}': ['p(95)<400'],
        'http_req_duration{endpoint:delete_annotation}': ['p(95)<400'],
        http_req_failed: ['rate<0.01'],
        checks: ['rate>0.99'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8003/api';
const TOKEN = __ENV.AUTH_TOKEN;

// Fail fast — an unset token produces "Bearer undefined" and silent 401s throughout
if (!TOKEN) {
    throw new Error(
        'AUTH_TOKEN env var is required. Pass it with: k6 run -e AUTH_TOKEN=<token> ...'
    );
}

const annotationErrors = new Counter('annotation_create_errors');
const previewDuration = new Trend('preview_document_ms');
const annotationRTT = new Trend('annotation_roundtrip_ms');

function headers() {
    return {
        headers: {
            Authorization: `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
        },
    };
}

function uniqueId(): string {
    return `perf-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

// ── Setup: create a test document to annotate ─────────────────────────────────

export function setup(): { docId: string } {
    const vuId = uniqueId();

    // Minimal PDF bytes — decoded from base64; real enough for storage tests
    const pdfBytes = encoding.b64decode('JVBERi0xLjQKJeLjz9MKCjEgMCBvYmoKPDwvVHlwZSAvQ2F0YWxvZz4+CmVuZG9iagp4cmVmCjAgMgowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMDkgMDAwMDAgbiAKdHJhaWxlcgo8PC9TaXplIDI+PgpzdGFydHhyZWYKOQolJUVPRgo=', 'rawstd', 'b');

    // Upload endpoint uses multipart/form-data (Form + UploadFile)
    // Do NOT set Content-Type — k6 sets it automatically with the multipart boundary
    const formData = {
        title: `Perf test doc ${vuId}`,
        category: 'financial',
        is_public: 'false',
        allowed_roles: '[]',
        is_test_data: 'true',
        file: http.file(pdfBytes, 'perf-test.pdf', 'application/pdf'),
    };

    const resp = http.post(`${BASE_URL}/documents`, formData, {
        headers: {Authorization: `Bearer ${TOKEN}`},
    });
    let docId = '';
    if (resp.status === 200 || resp.status === 201) {
        try {
            docId = (JSON.parse(resp.body as string) as any).id ?? '';
        } catch { /* no-op */
        }
    }
    if (!docId) {
        throw new Error(
            `Benchmark setup failed: POST /documents returned status ${resp.status}. ` +
            'Preview and annotation thresholds would pass with zero samples. Aborting.'
        );
    }
    return {docId};
}

// ── Teardown: delete all perf test data ───────────────────────────────────────

export function teardown(_data: unknown): void {
    const h = headers();

    // Delete test documents and any surviving annotations (MongoDB has no cascade)
    const docList = http.get(`${BASE_URL}/documents?include_test_data=true`, h);
    if (docList.status === 200) {
        let docs: Array<{ id: string; title: string }> = [];
        try {
            docs = JSON.parse(docList.body as string);
        } catch { /* no-op */
        }
        for (const doc of docs.filter((d) => d.title?.startsWith('Perf test doc perf-'))) {
            // Clean up any annotations that survived (e.g. from aborted VUs)
            const annList = http.get(`${BASE_URL}/documents/${doc.id}/annotations?include_test_data=true`, h);
            if (annList.status === 200) {
                let anns: Array<{ id: string }> = [];
                try {
                    anns = JSON.parse(annList.body as string);
                } catch { /* no-op */
                }
                for (const ann of anns) {
                    http.del(`${BASE_URL}/documents/${doc.id}/annotations/${ann.id}`, null, h);
                }
            }
            http.del(`${BASE_URL}/documents/${doc.id}`, null, h);
        }
    }
}

// ── Default function ──────────────────────────────────────────────────────────

export default function (data: { docId: string }) {
    const h = headers();
    const {docId} = data;

    // ── 1. Read documents list ──────────────────────────────────────────────────
    group('read_documents', () => {
        const list = http.get(`${BASE_URL}/documents`, {
            ...h,
            tags: {endpoint: 'list_documents'},
        });
        check(list, {
            'GET /documents 200': (r) => r.status === 200,
            'returns array': (r) => {
                try {
                    return Array.isArray(JSON.parse(r.body as string));
                } catch {
                    return false;
                }
            },
        });
    });

    sleep(0.3);

    if (!docId) {
        sleep(1);
        return;
    }

    // ── 2. Preview document (file_data fetch) ───────────────────────────────────
    group('preview_document', () => {
        const preview = http.get(`${BASE_URL}/documents/${docId}?include_test_data=true`, {
            ...h,
            tags: {endpoint: 'preview_document'},
        });
        check(preview, {
            'GET /documents/{id} 200': (r) => r.status === 200,
            'has file_data field': (r) => {
                try {
                    return typeof (JSON.parse(r.body as string) as any).file_data !== 'undefined';
                } catch {
                    return false;
                }
            },
        });
        previewDuration.add(preview.timings.duration);
    });

    sleep(0.3);

    // ── 3. Annotation CRUD ──────────────────────────────────────────────────────
    group('annotations_crud', () => {
        const highlightId = uniqueId();
        const annotPayload = JSON.stringify({
            highlight_id: highlightId,
            position: {
                boundingRect: {x1: 10, y1: 20, x2: 200, y2: 40, width: 800, height: 1000, pageNumber: 1},
                rects: [],
                pageNumber: 1,
            },
            content: {text: 'perf test highlight', image: null},
            comment: {text: 'k6 perf test annotation', emoji: ''},
        });

        const t0 = Date.now();
        const create = http.post(
            `${BASE_URL}/documents/${docId}/annotations?include_test_data=true`,
            annotPayload,
            {...h, tags: {endpoint: 'create_annotation'}},
        );
        const created = check(create, {
            'POST /annotations 201': (r) => r.status === 201,
        });
        if (!created) {
            annotationErrors.add(1);
            return;
        }

        let annId = '';
        try {
            annId = (JSON.parse(create.body as string) as any).id ?? '';
        } catch { /* no-op */
        }

        const list = http.get(
            `${BASE_URL}/documents/${docId}/annotations`,
            {...h, tags: {endpoint: 'list_annotations'}},
        );
        check(list, {
            'GET /annotations 200': (r) => r.status === 200,
            'annotation in list': (r) => {
                try {
                    const items = JSON.parse(r.body as string) as any[];
                    return items.some((a) => a.id === annId);
                } catch {
                    return false;
                }
            },
        });

        if (annId) {
            const del = http.del(
                `${BASE_URL}/documents/${docId}/annotations/${annId}`,
                null,
                {...h, tags: {endpoint: 'delete_annotation'}},
            );
            check(del, {'DELETE /annotations 200': (r) => r.status === 200});
        }

        annotationRTT.add(Date.now() - t0);
    });

    sleep(1);
}
