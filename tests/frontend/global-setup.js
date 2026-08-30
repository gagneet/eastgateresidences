/**
 * Global setup for Playwright tests
 * Creates necessary directories for test artifacts
 */

const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const {MongoClient} = require('mongodb');

const API = process.env.TEST_API || 'http://localhost:8003/api';
const BACKEND_ENV_PATH = path.join(__dirname, '..', '..', 'backend', '.env');

// ---------------------------------------------------------------------------
// Rate limit helpers
// ---------------------------------------------------------------------------

function apiRequest(method, urlStr, body, headers) {
    return new Promise((resolve, reject) => {
        const url = new URL(urlStr);
        const lib = url.protocol === 'https:' ? https : http;
        const options = {
            hostname: url.hostname,
            port: url.port,
            path: url.pathname + url.search,
            method,
            headers: { 'Content-Type': 'application/json', ...headers },
        };
        const req = lib.request(options, (res) => {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => {
                try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
                catch { resolve({ status: res.statusCode, body: data }); }
            });
        });
        req.on('error', reject);
        if (body) req.write(JSON.stringify(body));
        req.end();
    });
}

function parseEnv(contents) {
    const env = {};
    contents.split('\n').forEach((line) => {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) return;
        const idx = trimmed.indexOf('=');
        if (idx === -1) return;
        env[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
    });
    return env;
}

async function cleanupStaleTestData() {
    if (!fs.existsSync(BACKEND_ENV_PATH)) return;
    const env = parseEnv(fs.readFileSync(BACKEND_ENV_PATH, 'utf8'));
    if (!env.MONGO_URL || !env.DB_NAME) return;

    const client = new MongoClient(env.MONGO_URL);
    try {
        await client.connect();
        const db = client.db(env.DB_NAME);
        const workflowCleanup = await db.collection('workflow_requests').deleteMany({
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {title: {$regex: /^Perf test request perf/}},
                {subject: {$regex: /^Perf test request perf/}},
                {description: 'Automated performance test — safe to delete'},
                {body: 'Automated performance test — safe to delete'},
            ],
        });
        const maintenanceQuery = {
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {title: {$regex: /^Perf test request perf/}},
                {description: 'Automated performance test — safe to delete'},
            ],
        };
        const maintenanceIds = await db.collection('maintenance_requests')
            .find(maintenanceQuery, {projection: {_id: 0, id: 1}})
            .map((doc) => doc.id)
            .toArray();
        const auditCleanup = maintenanceIds.length > 0
            ? await db.collection('audit_logs').deleteMany({
                resource_type: 'maintenance_request',
                resource_id: {$in: maintenanceIds},
            })
            : {deletedCount: 0};
        const maintenanceCleanup = await db.collection('maintenance_requests').deleteMany(maintenanceQuery);
        const activityCleanup = await db.collection('activities').deleteMany({
            building_id: '13195',
            $or: [
                {title: 'New Resident Joined: Test Registrant'},
                {title: {$regex: /^New Maintenance Request: Perf test request perf/}},
            ],
        });
        const emailCleanup = await db.collection('email_sent_log').deleteMany({
            $or: [
                {subject: {$regex: /Perf test request perf/}},
                {subject: {$regex: /Test Registrant/}},
            ],
        });
        const notificationCleanup = await db.collection('user_notifications').deleteMany({
            building_id: '13195',
            $or: [
                {title: 'New Resident Joined: Test Registrant'},
                {message: {$regex: /Test Registrant/}},
                {title: {$regex: /Perf test request perf/}},
                {message: {$regex: /Perf test request perf/}},
            ],
        });
        const loginAuditCleanup = await db.collection('login_audit_logs').deleteMany({
            is_test_data: true,
        });
        const documentIds = await db.collection('documents')
            .find({
                building_id: '13195',
                $or: [
                    {is_test_data: true},
                    {title: {$regex: /^Perf test doc perf-/}},
                ],
            }, {projection: {_id: 0, id: 1}})
            .map((doc) => doc.id)
            .toArray();
        const annotationCleanup = documentIds.length > 0
            ? await db.collection('document_annotations').deleteMany({
                building_id: '13195',
                document_id: {$in: documentIds},
            })
            : {deletedCount: 0};
        const documentCleanup = await db.collection('documents').deleteMany({
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {title: {$regex: /^Perf test doc perf-/}},
            ],
        });
        const decisionCleanup = await db.collection('decisions').deleteMany({
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {motion_title: {$regex: /^Perf decision register perf-/}},
            ],
        });
        const trustBatchCleanup = await db.collection('trust_ledger_batches').deleteMany({
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {description: {$regex: /^Perf dual-approve batch /}},
            ],
        });
        if (workflowCleanup.deletedCount > 0 || maintenanceCleanup.deletedCount > 0 || activityCleanup.deletedCount > 0 || emailCleanup.deletedCount > 0 || auditCleanup.deletedCount > 0 || notificationCleanup.deletedCount > 0 || loginAuditCleanup.deletedCount > 0 || annotationCleanup.deletedCount > 0 || documentCleanup.deletedCount > 0 || decisionCleanup.deletedCount > 0 || trustBatchCleanup.deletedCount > 0) {
            console.log(`[cleanup] Pre-run stale cleanup workflow_requests=${workflowCleanup.deletedCount} maintenance_requests=${maintenanceCleanup.deletedCount} activities=${activityCleanup.deletedCount} email_sent_log=${emailCleanup.deletedCount} audit_logs=${auditCleanup.deletedCount} user_notifications=${notificationCleanup.deletedCount} login_audit_logs=${loginAuditCleanup.deletedCount} document_annotations=${annotationCleanup.deletedCount} documents=${documentCleanup.deletedCount} decisions=${decisionCleanup.deletedCount} trust_ledger_batches=${trustBatchCleanup.deletedCount}`);
        }
    } catch (err) {
        console.warn(`[cleanup] Pre-run stale cleanup skipped: ${err.message}`);
    } finally {
        await client.close();
    }
}

async function getAdminToken() {
    // Pre-generated token takes priority (no password needed).
    if (process.env.PLAYWRIGHT_ADMIN_TOKEN) return process.env.PLAYWRIGHT_ADMIN_TOKEN;

    const email = process.env.PLAYWRIGHT_ADMIN_EMAIL;
    const password = process.env.PLAYWRIGHT_ADMIN_PASSWORD;
    if (!email || !password) return null;

    try {
        const res = await apiRequest('POST', `${API}/auth/login`, { email, password }, {});
        return res.status === 200 ? (res.body.token ?? null) : null;
    } catch {
        return null;
    }
}

// X-Building-ID is required by get_current_building() for super_admin tokens
// that have no building_id claim in the JWT. Rate limit settings live in
// db.site_settings (global, not per-building) so any valid ID works here.
const ADMIN_BUILDING_ID = '13195';

async function getRateLimitSettings(token) {
    const res = await apiRequest('GET', `${API}/settings`, null, {
        Authorization: `Bearer ${token}`,
        'X-Building-ID': ADMIN_BUILDING_ID,
    });
    if (res.status !== 200) return null;
    const body = res.body;
    return {
        rate_limit_multiplier: body.rate_limit_multiplier ?? 1.0,
        rate_limit_login: body.rate_limit_login ?? 10,
    };
}

async function putRateLimitSettings(token, settings) {
    return apiRequest('PUT', `${API}/settings`, settings, {
        Authorization: `Bearer ${token}`,
        'X-Building-ID': ADMIN_BUILDING_ID,
    });
}

// ---------------------------------------------------------------------------

module.exports = async () => {
    await cleanupStaleTestData();

    const runId = process.env.TEST_RUN_ID || `pw_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
    process.env.TEST_RUN_ID = runId;
    const runIdPath = path.join(__dirname, '.test_run_id');
    fs.writeFileSync(runIdPath, runId, 'utf8');

    const registryPath = path.join(__dirname, '.cleanup_registry.ndjson');
    fs.writeFileSync(registryPath, '', 'utf8');

    // Create directories if they don't exist
    const dirs = [
        'tests/screenshots',
        'tests/artifacts',
        'tests/reports',
        'tests/reports/html'
    ];

    dirs.forEach(dir => {
        const dirPath = path.join(__dirname, '..', dir);
        if (!fs.existsSync(dirPath)) {
            fs.mkdirSync(dirPath, {recursive: true});
            console.log(`Created directory: ${dir}`);
        }
    });

    // ---------------------------------------------------------------------------
    // Optional: raise rate limits for the test run to prevent 429 cross-test
    // contamination from the Layer 1 rate-limit exhaustion test.
    //
    // How to enable:
    //   PLAYWRIGHT_ADMIN_TOKEN=<super_admin_jwt>     (preferred — no password)
    //   PLAYWRIGHT_ADMIN_EMAIL + PLAYWRIGHT_ADMIN_PASSWORD   (auto-login)
    //
    // Teardown ALWAYS restores the pre-run values (not hardcoded defaults), so
    // the production DB config is preserved exactly. NEVER assume defaults.
    //
    // Alternative: start backend with DISABLE_RATE_LIMIT=1 — bypasses slowapi
    // entirely, no API calls needed.
    // ---------------------------------------------------------------------------
    const adminToken = await getAdminToken();
    if (adminToken) {
        try {
            // Capture CURRENT values first — teardown will restore these exactly.
            const original = await getRateLimitSettings(adminToken);
            if (!original) {
                console.warn('[rate-limit] Could not read current settings — skipping raise.');
            } else {
                const res = await putRateLimitSettings(adminToken, {
                    rate_limit_multiplier: 10000.0,
                    rate_limit_login: 100000,
                });
                if (res.status === 200) {
                    console.log('[rate-limit] Raised limits for test run (was:', JSON.stringify(original), ')');
                    // Store token + original values so teardown restores the right thing.
                    fs.writeFileSync(
                        path.join(__dirname, '.admin_token'),
                        JSON.stringify({ token: adminToken, original }),
                        'utf8',
                    );
                } else {
                    console.warn(`[rate-limit] PUT /settings returned ${res.status} — limits unchanged.`);
                }
            }
        } catch (err) {
            console.warn(`[rate-limit] Could not raise rate limits: ${err.message}`);
        }
    } else if (!process.env.DISABLE_RATE_LIMIT) {
        console.log(
            '[rate-limit] No admin token set — rate limits are ACTIVE.\n' +
            '  To disable: set PLAYWRIGHT_ADMIN_TOKEN, or start backend with DISABLE_RATE_LIMIT=1.'
        );
    }

    console.log(`Global setup complete (TEST_RUN_ID=${runId})`);
};
