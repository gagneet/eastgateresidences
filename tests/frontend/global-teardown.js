const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const {MongoClient} = require('mongodb');

const RUN_ID_PATH = path.join(__dirname, '.test_run_id');
const REGISTRY_PATH = path.join(__dirname, '.cleanup_registry.ndjson');
const BACKEND_ENV_PATH = path.join(__dirname, '..', '..', 'backend', '.env');
const ADMIN_TOKEN_PATH = path.join(__dirname, '.admin_token');

const API = process.env.TEST_API || 'http://localhost:8003/api';

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
        const key = trimmed.slice(0, idx).trim();
        const value = trimmed.slice(idx + 1).trim();
        env[ key ] = value;
    });
    return env;
}

async function cleanupKnownTestData(db, runId) {
    const escapedRunId = runId ? runId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : null;
    const runIdPattern = escapedRunId ? new RegExp(escapedRunId) : null;
    const perfTitlePattern = /^Perf test request perf/;

    const workflowClauses = [
        {is_test_data: true},
        {title: {$regex: perfTitlePattern}},
        {subject: {$regex: perfTitlePattern}},
        {description: 'Automated performance test — safe to delete'},
        {body: 'Automated performance test — safe to delete'},
    ];
    if (runIdPattern) {
        workflowClauses.push(
            {title: {$regex: runIdPattern}},
            {subject: {$regex: runIdPattern}},
            {description: {$regex: runIdPattern}},
            {body: {$regex: runIdPattern}},
        );
    }

    try {
        const r = await db.collection('workflow_requests').deleteMany({
            building_id: '13195',
            $or: workflowClauses,
        });
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test workflow_requests`);
    } catch (err) {
        console.warn(`[cleanup] workflow_requests safety cleanup failed: ${err.message}`);
    }

    try {
        const maintenanceQuery = {
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {title: {$regex: perfTitlePattern}},
                {description: 'Automated performance test — safe to delete'},
            ],
        };
        const maintenanceIds = await db.collection('maintenance_requests')
            .find(maintenanceQuery, {projection: {_id: 0, id: 1}})
            .map((doc) => doc.id)
            .toArray();
        if (maintenanceIds.length > 0) {
            const audit = await db.collection('audit_logs').deleteMany({
                resource_type: 'maintenance_request',
                resource_id: {$in: maintenanceIds},
            });
            if (audit.deletedCount > 0) console.log(`[cleanup] Removed ${audit.deletedCount} test audit_logs records`);
        }
        const r = await db.collection('maintenance_requests').deleteMany(maintenanceQuery);
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test maintenance_requests`);
    } catch (err) {
        console.warn(`[cleanup] maintenance_requests safety cleanup failed: ${err.message}`);
    }

    try {
        const r = await db.collection('activities').deleteMany({
            building_id: '13195',
            $or: [
                {title: 'New Resident Joined: Test Registrant'},
                {title: {$regex: /^New Maintenance Request: Perf test request perf/}},
            ],
        });
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test activities`);
    } catch (err) {
        console.warn(`[cleanup] activities safety cleanup failed: ${err.message}`);
    }

    try {
        const documentQuery = {
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {title: {$regex: /^Perf test doc perf-/}},
                ...(runIdPattern ? [{title: {$regex: runIdPattern}}] : []),
            ],
        };
        const documentIds = await db.collection('documents')
            .find(documentQuery, {projection: {_id: 0, id: 1}})
            .map((doc) => doc.id)
            .toArray();
        if (documentIds.length > 0) {
            const ann = await db.collection('document_annotations').deleteMany({
                building_id: '13195',
                document_id: {$in: documentIds},
            });
            if (ann.deletedCount > 0) console.log(`[cleanup] Removed ${ann.deletedCount} test document_annotations`);
        }
        const r = await db.collection('documents').deleteMany(documentQuery);
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test documents`);
    } catch (err) {
        console.warn(`[cleanup] documents safety cleanup failed: ${err.message}`);
    }

    try {
        const r = await db.collection('decisions').deleteMany({
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {motion_title: {$regex: /^Perf decision register perf-/}},
                ...(runIdPattern ? [{motion_title: {$regex: runIdPattern}}] : []),
            ],
        });
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test decisions`);
    } catch (err) {
        console.warn(`[cleanup] decisions safety cleanup failed: ${err.message}`);
    }

    try {
        const r = await db.collection('trust_ledger_batches').deleteMany({
            building_id: '13195',
            $or: [
                {is_test_data: true},
                {description: {$regex: /^Perf dual-approve batch /}},
                ...(runIdPattern ? [{description: {$regex: runIdPattern}}] : []),
            ],
        });
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test trust_ledger_batches`);
    } catch (err) {
        console.warn(`[cleanup] trust_ledger_batches safety cleanup failed: ${err.message}`);
    }

    try {
        const r = await db.collection('email_sent_log').deleteMany({
            $or: [
                {subject: {$regex: /Perf test request perf/}},
                {subject: {$regex: /Test Registrant/}},
            ],
        });
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test email_sent_log records`);
    } catch (err) {
        console.warn(`[cleanup] email_sent_log safety cleanup failed: ${err.message}`);
    }

    try {
        const r = await db.collection('login_audit_logs').deleteMany({is_test_data: true});
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test login_audit_logs`);
    } catch (err) {
        console.warn(`[cleanup] login_audit_logs test-data cleanup failed: ${err.message}`);
    }

    const notificationClauses = [
        {title: 'New Resident Joined: Test Registrant'},
        {message: {$regex: /Test Registrant/}},
        {title: {$regex: /Perf test request perf/}},
        {message: {$regex: /Perf test request perf/}},
    ];
    if (runIdPattern) {
        notificationClauses.push(
            {title: {$regex: runIdPattern}},
            {message: {$regex: runIdPattern}},
        );
    }

    try {
        const r = await db.collection('user_notifications').deleteMany({
            building_id: '13195',
            $or: notificationClauses,
        });
        if (r.deletedCount > 0) console.log(`[cleanup] Removed ${r.deletedCount} test user_notifications`);
    } catch (err) {
        console.warn(`[cleanup] user_notifications safety cleanup failed: ${err.message}`);
    }
}

module.exports = async () => {
    // Rate limit restore must run unconditionally — it is independent of the
    // MongoDB cleanup below, which has several early-exit paths that would
    // leave the limits raised if Mongo config is missing.
    //
    // Restores the EXACT values captured before setup raised them, not hardcoded
    // defaults. This preserves production-configured limits (e.g. 1000/min).
    if (fs.existsSync(ADMIN_TOKEN_PATH)) {
        let stored;
        try {
            stored = JSON.parse(fs.readFileSync(ADMIN_TOKEN_PATH, 'utf8').trim());
        } catch {
            stored = null;
        }
        fs.unlinkSync(ADMIN_TOKEN_PATH);

        if (stored && stored.token && stored.original) {
            try {
                const res = await apiRequest(
                    'PUT',
                    `${API}/settings`,
                    stored.original,
                    { Authorization: `Bearer ${stored.token}`, 'X-Building-ID': '13195' },
                );
                if (res.status === 200) {
                    console.log('[rate-limit] Restored original settings:', JSON.stringify(stored.original));
                } else {
                    console.warn(`[rate-limit] Restore returned ${res.status} — manual check needed.`);
                }
            } catch (err) {
                console.warn(`[rate-limit] Could not restore rate limits: ${err.message}`);
            }
        } else {
            console.warn('[rate-limit] .admin_token was malformed — rate limits may still be raised.');
        }
    }

    if (!fs.existsSync(RUN_ID_PATH) && !fs.existsSync(REGISTRY_PATH)) {
        return;
    }

    const registryLines = fs.existsSync(REGISTRY_PATH)
        ? fs.readFileSync(REGISTRY_PATH, 'utf8').split('\n').filter(Boolean)
        : [];

    if (!fs.existsSync(BACKEND_ENV_PATH)) {
        console.warn('Backend .env not found; skipping DB cleanup');
        return;
    }

    const env = parseEnv(fs.readFileSync(BACKEND_ENV_PATH, 'utf8'));
    const mongoUrl = env.MONGO_URL;
    const dbName = env.DB_NAME;
    if (!mongoUrl || !dbName) {
        console.warn('Mongo config missing; skipping DB cleanup');
        return;
    }

    const client = new MongoClient(mongoUrl);
    await client.connect();
    const db = client.db(dbName);

    const entries = registryLines.map((line) => {
        try {
            return JSON.parse(line);
        } catch (e) {
            return null;
        }
    }).filter(Boolean);

    const runId = fs.existsSync(RUN_ID_PATH) ? fs.readFileSync(RUN_ID_PATH, 'utf8').trim() : null;
    await cleanupKnownTestData(db, runId);

    for (const entry of entries) {
        const {collection, field, value} = entry;
        if (!collection || !field) continue;
        try {
            const deleted = await db.collection(collection).deleteMany({[ field ]: value});
            if (deleted.deletedCount > 0) {
                console.log(`[cleanup] Deleted ${deleted.deletedCount} docs from ${collection} where ${field}=${value}`);
            }
        } catch (err) {
            console.warn(`[cleanup] Error cleaning ${collection}: ${err.message}`);
        }
    }

    // Login audits are created by nearly every Playwright auth helper.
    // Remove entries for the fixed test accounts so security screens stay clean
    // after the suite and repeated runs remain idempotent.
    const testEmails = [
        'administrator@strataos.live',
        'buildingadmin@eastgateresidences.com.au',
        'anthony@eastgateresidences.com.au',
        'marcelo.dasilva@eastgateresidences.com.au',
        'avneet@eastgateresidences.com.au',
        'tenant@eastgateresidences.com.au',
        'admin@eastgate.com',
        'owner@eastgate.com',
        'chairman@eastgate.com',
        'tenant@eastgate.com',
    ];
    try {
        const loginCleanup = await db.collection('login_audit_logs').deleteMany({
            email: {$in: testEmails},
        });
        console.log(`[cleanup] login_audit_logs removed=${loginCleanup.deletedCount}`);
        if (loginCleanup.deletedCount > 0) {
            console.log(`[cleanup] Removed ${loginCleanup.deletedCount} login audit records for test accounts`);
        }
    } catch (err) {
        console.warn(`[cleanup] Login audit cleanup failed: ${err.message}`);
    }

    // Auto-cleanup: remove any user_notifications whose title or message contains
    // the test run ID pattern (safety net in case registerCleanup wasn't called).
    if (runId) {
        try {
            const testNotifPattern = new RegExp(runId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
            const r1 = await db.collection('user_notifications').deleteMany({
                $or: [
                    {title: {$regex: testNotifPattern}},
                    {message: {$regex: testNotifPattern}},
                ]
            });
            const r2 = await db.collection('announcements').deleteMany({
                $or: [
                    {title: {$regex: testNotifPattern}},
                    {content: {$regex: testNotifPattern}},
                ]
            });
            if (r1.deletedCount > 0) console.log(`[cleanup] Removed ${r1.deletedCount} test user_notifications (runId=${runId})`);
            if (r2.deletedCount > 0) console.log(`[cleanup] Removed ${r2.deletedCount} test announcements (runId=${runId})`);
        } catch (err) {
            console.warn(`[cleanup] Test-run ID cleanup failed: ${err.message}`);
        }
    }

    // Always clean up user_notifications whose related_id matches any cleaned announcement
    // (belt-and-suspenders: in case user_notifications weren't explicitly registered)
    const announcementEntries = entries.filter(e => e.collection === 'announcements' && e.field === 'id');
    for (const entry of announcementEntries) {
        try {
            const r = await db.collection('user_notifications').deleteMany({related_id: entry.value});
            if (r.deletedCount > 0) {
                console.log(`[cleanup] Removed ${r.deletedCount} user_notifications for announcement ${entry.value}`);
            }
        } catch (err) {
            // Ignore
        }
    }

    await client.close();
    console.log('[cleanup] Global teardown complete.');
};
