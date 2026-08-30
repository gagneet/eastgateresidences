const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const {chromium} = require('@playwright/test');

const API = process.env.TEST_API || 'http://localhost:8003/api';
const BASE_URL = process.env.BASE_URL || 'http://localhost:3020';
const AUTH_DIR = path.join(__dirname, '.auth', 'request-catalogue');
const METADATA_PATH = path.join(AUTH_DIR, 'metadata.json');
const ALLOWED_BUILDINGS = new Set(['TEST-REQUEST-A', 'TEST-REQUEST-B']);
const PRIMARY_BUILDING_ID = process.env.REQUEST_CATALOGUE_E2E_BUILDING_A || 'TEST-REQUEST-A';
const SECONDARY_BUILDING_ID = process.env.REQUEST_CATALOGUE_E2E_BUILDING_B || 'TEST-REQUEST-B';

const ROLE_CONFIG = [
    {role: 'owner', env: 'OWNER'},
    {role: 'tenant', env: 'TENANT'},
    {role: 'real_estate_agent', env: 'AGENT'},
    {role: 'ec_member', env: 'EC_MEMBER'},
    {role: 'strata_manager', env: 'MANAGER', needsSecondaryBuilding: true},
];

function request(method, urlStr, body, headers = {}) {
    return new Promise((resolve, reject) => {
        const url = new URL(urlStr);
        const lib = url.protocol === 'https:' ? https : http;
        const req = lib.request({
            hostname: url.hostname,
            port: url.port,
            path: url.pathname + url.search,
            method,
            headers: {'Content-Type': 'application/json', ...headers},
        }, (res) => {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => {
                try {
                    resolve({status: res.statusCode, body: JSON.parse(data || '{}')});
                } catch {
                    resolve({status: res.statusCode, body: data});
                }
            });
        });
        req.on('error', reject);
        if (body) req.write(JSON.stringify(body));
        req.end();
    });
}

function credentialsFor(config) {
    const email = process.env[`REQUEST_CATALOGUE_E2E_${config.env}_EMAIL`];
    const password = process.env[`REQUEST_CATALOGUE_E2E_${config.env}_PASSWORD`];
    if (!email || !password) {
        throw new Error(
            `Missing REQUEST_CATALOGUE_E2E_${config.env}_EMAIL/PASSWORD for ${config.role}. ` +
            'Request-catalogue CI must provide synthetic TEST-REQUEST credentials.'
        );
    }
    return {email, password};
}

function assertAllowedBuilding(buildingId, label) {
    if (!ALLOWED_BUILDINGS.has(buildingId)) {
        throw new Error(`${label} resolved to ${buildingId}; only TEST-REQUEST-A and TEST-REQUEST-B are allowed.`);
    }
}

async function backendLogin(config, creds) {
    const login = await request('POST', `${API}/auth/login`, creds);
    if (login.status !== 200 || !login.body?.token) {
        throw new Error(`${config.role} backend login failed with HTTP ${login.status}.`);
    }
    const headers = {Authorization: `Bearer ${login.body.token}`, 'X-Building-ID': PRIMARY_BUILDING_ID};
    const me = await request('GET', `${API}/auth/me`, null, headers);
    if (me.status !== 200) throw new Error(`${config.role} /auth/me failed with HTTP ${me.status}.`);
    if (me.body?.role !== config.role) {
        throw new Error(`${config.role} credential returned role ${me.body?.role || 'unknown'}.`);
    }
    const buildings = await request('GET', `${API}/buildings/me`, null, headers);
    if (buildings.status !== 200 || !Array.isArray(buildings.body)) {
        throw new Error(`${config.role} /buildings/me failed with HTTP ${buildings.status}.`);
    }
    const primary = buildings.body.find((building) => building.building_id === PRIMARY_BUILDING_ID);
    if (!primary) throw new Error(`${config.role} cannot access ${PRIMARY_BUILDING_ID}.`);
    assertAllowedBuilding(primary.building_id, `${config.role} primary building`);

    let secondary = null;
    if (config.needsSecondaryBuilding) {
        secondary = buildings.body.find((building) => building.building_id === SECONDARY_BUILDING_ID);
        if (!secondary) throw new Error(`${config.role} cannot access ${SECONDARY_BUILDING_ID} for switch coverage.`);
        assertAllowedBuilding(secondary.building_id, `${config.role} secondary building`);
    }

    return {primary, secondary};
}

async function browserLogin(config, creds, building) {
    const statePath = path.join(AUTH_DIR, `${config.role}.json`);
    const browser = await chromium.launch();
    const context = await browser.newContext({baseURL: BASE_URL});
    await context.addInitScript((selectedBuilding) => {
        localStorage.setItem('selectedBuilding', JSON.stringify(selectedBuilding));
    }, building);
    const page = await context.newPage();
    await page.goto('/login');
    await page.getByTestId('email-input').fill(creds.email);
    await page.getByTestId('password-input').fill(creds.password);
    await page.getByTestId('login-submit').click();
    await page.waitForURL((url) => (
        url.pathname.startsWith('/dashboard') ||
        url.pathname === '/select-building' ||
        url.pathname === '/login/totp-challenge'
    ), {timeout: 30000});
    if (page.url().includes('/login/totp-challenge')) {
        throw new Error(`${config.role} requires TOTP; use a synthetic account without interactive MFA for CI.`);
    }
    await page.goto('/requests');
    await page.getByRole('heading', {name: 'Requests & Applications'}).waitFor({timeout: 30000});
    await context.storageState({path: statePath});
    await browser.close();
    return path.relative(process.cwd(), statePath);
}

module.exports = async () => {
    fs.mkdirSync(AUTH_DIR, {recursive: true});
    const metadata = {
        generated_at: new Date().toISOString(),
        api: API,
        base_url: BASE_URL,
        primary_building_id: PRIMARY_BUILDING_ID,
        secondary_building_id: SECONDARY_BUILDING_ID,
        roles: {},
    };

    assertAllowedBuilding(PRIMARY_BUILDING_ID, 'Primary test building');
    assertAllowedBuilding(SECONDARY_BUILDING_ID, 'Secondary test building');

    for (const config of ROLE_CONFIG) {
        const creds = credentialsFor(config);
        const {primary, secondary} = await backendLogin(config, creds);
        const storageState = await browserLogin(config, creds, primary);
        metadata.roles[config.role] = {
            storage_state: storageState,
            primary_building_id: primary.building_id,
            secondary_building_id: secondary?.building_id || null,
        };
    }

    fs.writeFileSync(METADATA_PATH, JSON.stringify(metadata, null, 2), 'utf8');
    console.log(`[request-catalogue] Generated runtime storage states in ${path.relative(process.cwd(), AUTH_DIR)}`);
};
