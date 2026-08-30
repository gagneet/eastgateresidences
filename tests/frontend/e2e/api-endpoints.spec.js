/**
 * API Endpoints Comprehensive Tests
 *
 * Tests all major API endpoints for:
 * - Correct response codes
 * - Data structure validation
 * - Authentication requirements
 * - Permission checks
 * - Error handling
 * - CRUD operations
 */

const {test, expect} = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'https://eastgateresidences.com.au';

const TEST_USERS = {
    admin: {email: 'admin@eastgate.com', password: '$SEED_TEST_USER_PASSWORD'},
    owner: {email: 'owner@eastgate.com', password: '$SEED_TEST_USER_PASSWORD'},
    tenant: {email: 'tenant@eastgate.com', password: process.env.E2E_TENANT_PASSWORD || ''}
};

// Helper to get auth token
async function getAuthToken(request, email, password) {
    const response = await request.post(`${BASE_URL}/api/auth/login`, {
        data: {email, password}
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    return data.token;
}

test.describe('Authentication API', () => {

    test('POST /api/auth/login - Should login successfully', async ({request}) => {
        const response = await request.post(`${BASE_URL}/api/auth/login`, {
            data: {
                email: TEST_USERS.admin.email,
                password: TEST_USERS.admin.password
            }
        });

        expect(response.ok()).toBeTruthy();
        const data = await response.json();

        expect(data).toHaveProperty('token');
        expect(data).toHaveProperty('user');
        expect(data.user.email).toBe(TEST_USERS.admin.email);
    });

    test('POST /api/auth/login - Should reject invalid credentials', async ({request}) => {
        const response = await request.post(`${BASE_URL}/api/auth/login`, {
            data: {
                email: 'wrong@email.com',
                password: 'WrongPassword123!'
            }
        });

        expect(response.status()).toBeGreaterThanOrEqual(400);
    });

    test('GET /api/auth/me - Should return current user', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/auth/me`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const user = await response.json();

        expect(user.email).toBe(TEST_USERS.admin.email);
        expect(user).toHaveProperty('role');
    });

    test('GET /api/auth/me - Should reject without token', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/auth/me`);
        expect(response.status()).toBe(401);
    });
});

test.describe('Units API', () => {

    test('GET /api/units - Should return list of units', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/units`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const units = await response.json();

        expect(Array.isArray(units)).toBe(true);
        expect(units.length).toBeGreaterThan(0);

        // Verify unit structure
        const unit = units[ 0 ];
        expect(unit).toHaveProperty('unit_number');
        expect(unit).toHaveProperty('unit_type');
        expect(unit).toHaveProperty('lot_number');
    });

    test('GET /api/units - Should support pagination', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/units`, {
            headers: {'Authorization': `Bearer ${token}`},
            params: {limit: 5, offset: 0}
        });

        expect(response.ok()).toBeTruthy();
        const units = await response.json();

        expect(units.length).toBeLessThanOrEqual(5);
    });

    test('GET /api/units/available - Should return unassigned units', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/units/available`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const units = await response.json();

        expect(Array.isArray(units)).toBe(true);
    });
});

test.describe('Owners & Units API', () => {

    test('GET /api/owners-units - Should return units with owner info', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/owners-units`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const data = await response.json();

        expect(Array.isArray(data)).toBe(true);
    });

    test('GET /api/owners-units/UA001 - Should return specific apartment', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/owners-units/UA001`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const unit = await response.json();

        expect(unit.unit_number).toBe('UA001');
        expect(unit.unit_type).toBe('apartment');
    });

    test('GET /api/owners-units/TH001 - Should return specific townhouse', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/owners-units/TH001`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const unit = await response.json();

        expect(unit.unit_number).toBe('TH001');
        expect(unit.unit_type).toBe('townhouse');
    });
});

test.describe('Users API', () => {

    test('GET /api/users - Should return user list (admin only)', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/users`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const users = await response.json();

        expect(Array.isArray(users)).toBe(true);
        expect(users.length).toBeGreaterThan(0);
    });

    test('GET /api/users - Should deny access to regular users', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.owner.email, TEST_USERS.owner.password);

        const response = await request.get(`${BASE_URL}/api/users`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        // Should be forbidden or not allowed
        expect(response.status()).toBeGreaterThanOrEqual(403);
    });
});

test.describe('Documents API', () => {

    test('GET /api/documents - Should return document list', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/documents`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const docs = await response.json();

        expect(Array.isArray(docs)).toBe(true);
    });

    test('GET /api/documents/folders - Should return folder structure', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/documents/folders`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const folders = await response.json();

        expect(Array.isArray(folders)).toBe(true);
    });
});

test.describe('Finance API', () => {

    test('GET /api/finance - Should return financial entries', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/finance`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const entries = await response.json();

        expect(Array.isArray(entries)).toBe(true);
    });

    test('GET /api/finance/summary - Should return financial summary', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/finance/summary`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const summary = await response.json();

        expect(summary).toHaveProperty('admin_fund');
        expect(summary).toHaveProperty('sinking_fund');
    });

    test('GET /api/levy-status - Should return levy status', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.owner.email, TEST_USERS.owner.password);

        const response = await request.get(`${BASE_URL}/api/levy-status`, {
            headers: {'Authorization': `Bearer ${token}`},
            params: {unit_number: 'UA003'}
        });

        if (response.ok()) {
            const status = await response.json();
            expect(status).toHaveProperty('unit_number');
        }
    });
});

test.describe('Announcements API', () => {

    test('GET /api/announcements - Should return announcements', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/announcements`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const announcements = await response.json();

        expect(Array.isArray(announcements)).toBe(true);
    });

    test('GET /api/announcements - Should be accessible to all authenticated users', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.tenant.email, TEST_USERS.tenant.password);

        const response = await request.get(`${BASE_URL}/api/announcements`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
    });
});

test.describe('Marketplace API', () => {

    test('GET /api/listings - Should return marketplace listings', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/listings`);

        expect(response.ok()).toBeTruthy();
        const listings = await response.json();

        expect(Array.isArray(listings)).toBe(true);
    });

    test('GET /api/listings - Should not require authentication', async ({request}) => {
        // Public endpoint
        const response = await request.get(`${BASE_URL}/api/listings`);
        expect(response.ok()).toBeTruthy();
    });
});

test.describe('Blog API', () => {

    test('GET /api/blog - Should return blog posts', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/blog`);

        expect(response.ok()).toBeTruthy();
        const posts = await response.json();

        expect(Array.isArray(posts)).toBe(true);
    });

    test('GET /api/blog/{id} - Should return single post', async ({request}) => {
        // First get all posts
        const listResponse = await request.get(`${BASE_URL}/api/blog`);
        const posts = await listResponse.json();

        if (posts.length > 0) {
            const postId = posts[ 0 ].id;

            // Get single post
            const response = await request.get(`${BASE_URL}/api/blog/${postId}`);
            expect(response.ok()).toBeTruthy();

            const post = await response.json();
            expect(post.id).toBe(postId);
        }
    });
});

test.describe('EC Members API', () => {

    test('GET /api/ec-members - Should return EC members', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/ec-members`);

        expect(response.ok()).toBeTruthy();
        const members = await response.json();

        expect(Array.isArray(members)).toBe(true);
        expect(members.length).toBeGreaterThan(0);

        // Verify structure
        const member = members[ 0 ];
        expect(member).toHaveProperty('name');
        expect(member).toHaveProperty('position');
        expect(member).toHaveProperty('email');
    });
});

test.describe('Settings API', () => {

    test('GET /api/settings - Should return site settings', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/settings`);

        expect(response.ok()).toBeTruthy();
        const settings = await response.json();

        expect(settings).toHaveProperty('building_name');
    });

    test('PUT /api/settings - Should require admin permissions', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.owner.email, TEST_USERS.owner.password);

        const response = await request.put(`${BASE_URL}/api/settings`, {
            headers: {'Authorization': `Bearer ${token}`},
            data: {building_name: 'Test'}
        });

        // Should be denied
        expect(response.status()).toBeGreaterThanOrEqual(403);
    });
});

test.describe('Admin Stats API', () => {

    test('GET /api/admin/stats - Should return dashboard statistics', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.admin.email, TEST_USERS.admin.password);

        const response = await request.get(`${BASE_URL}/api/admin/stats`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.ok()).toBeTruthy();
        const stats = await response.json();

        expect(stats).toHaveProperty('total_users');
        expect(stats).toHaveProperty('total_documents');
    });

    test('GET /api/admin/stats - Should deny non-admin access', async ({request}) => {
        const token = await getAuthToken(request, TEST_USERS.tenant.email, TEST_USERS.tenant.password);

        const response = await request.get(`${BASE_URL}/api/admin/stats`, {
            headers: {'Authorization': `Bearer ${token}`}
        });

        expect(response.status()).toBeGreaterThanOrEqual(403);
    });
});

test.describe('Error Handling', () => {

    test('Should return 404 for non-existent endpoint', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/nonexistent`);
        expect(response.status()).toBe(404);
    });

    test('Should return proper error format', async ({request}) => {
        const response = await request.get(`${BASE_URL}/api/auth/me`);
        expect(response.status()).toBe(401);

        const error = await response.json();
        expect(error).toHaveProperty('detail');
    });

    test('Should handle malformed JSON', async ({request}) => {
        const response = await request.post(`${BASE_URL}/api/auth/login`, {
            headers: {'Content-Type': 'application/json'},
            data: 'invalid json'
        });

        expect(response.status()).toBeGreaterThanOrEqual(400);
    });
});
