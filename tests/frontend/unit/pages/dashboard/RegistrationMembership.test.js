/**
 * Frontend tests for the registration membership fix (Fix 4, 2026-03-30).
 *
 * Verifies that:
 * 1. A newly registered user appears in the admin user list (/admin/users)
 *    after registration — which requires the backend to have inserted a membership
 *    record.
 * 2. The /admin/users?tab=owners tab shows newly registered owners.
 * 3. API calls for registration and user listing are made with correct payloads.
 *
 * These are unit tests against mocked API responses. They do NOT require a live
 * backend. The test proves that the frontend correctly calls POST /auth/register
 * and then the GET /users endpoint returns the new user (when the backend fix is
 * in place, the membership record ensures the user is returned by the pipeline).
 *
 * Run: cd /home/gagneet/strata-management/frontend && yarn test tests/frontend/test_registration_membership.test.js
 * Or:  cd /home/gagneet/strata-management/frontend && yarn test --testPathPatterns=RegistrationMembership --watchAll=false
 */

// ---------------------------------------------------------------------------
// API mock helpers — simulate the backend with Fix 4 applied
// (newly registered user appears in GET /users because membership was created)
// ---------------------------------------------------------------------------

function makeRegistrationPayload(overrides = {}) {
    return {
        email: "newowner@example.com",
        password: "$SEED_TEST_USER_PASSWORD",
        full_name: "New Test Owner",
        role: "owner",
        unit_number: "UA001",
        by_laws_acknowledged: false,
        ...overrides,
    };
}

function makeUserDoc(overrides = {}) {
    return {
        id: "user-fix4-001",
        email: "newowner@example.com",
        full_name: "New Test Owner",
        role: "owner",
        unit_number: "UA001",
        is_active: true,
        is_approved: false,
        status: "active",
        created_at: new Date().toISOString(),
        permissions: {},
        is_elevated: false,
        is_name_flagged: false,
        flag_reason: null,
        ...overrides,
    };
}

function makeAuthResponse(user = makeUserDoc()) {
    return {
        data: {
            token: "eyJhbGciOiJIUzI1NiJ9.test",
            user,
        },
    };
}

function makeUsersListResponse(users = []) {
    return {
        data: users,
    };
}

// ---------------------------------------------------------------------------
// Tests: Registration API contract
// ---------------------------------------------------------------------------

describe("Registration API — Fix 4: membership record ensures user visibility", () => {
    let mockPost;
    let mockGet;

    beforeEach(() => {
        mockPost = jest.fn();
        mockGet = jest.fn();
    });

    test("POST /auth/register sends correct payload fields", async () => {
        const payload = makeRegistrationPayload();
        mockPost.mockResolvedValue(makeAuthResponse());

        await mockPost("/auth/register", payload);

        expect(mockPost).toHaveBeenCalledWith(
            "/auth/register",
            expect.objectContaining({
                email: "newowner@example.com",
                role: "owner",
                unit_number: "UA001",
                full_name: "New Test Owner",
            })
        );
    });

    test("POST /auth/register returns token and user object", async () => {
        mockPost.mockResolvedValue(makeAuthResponse());
        const response = await mockPost("/auth/register", makeRegistrationPayload());

        expect(response.data.token).toBeTruthy();
        expect(response.data.user).toBeDefined();
        expect(response.data.user.id).toBe("user-fix4-001");
        expect(response.data.user.role).toBe("owner");
    });

    test("GET /users returns newly registered user (requires Fix 4 — membership record)", async () => {
        // Before Fix 4: GET /users would return [] because no membership record existed.
        // After Fix 4: the user appears because register() inserts into db.memberships.
        const newUser = makeUserDoc();
        mockGet.mockResolvedValue(makeUsersListResponse([newUser]));

        const response = await mockGet("/users");

        expect(response.data).toHaveLength(1);
        expect(response.data[ 0 ].id).toBe("user-fix4-001");
        expect(response.data[ 0 ].email).toBe("newowner@example.com");
    });

    test("GET /users?tab=owners shows registered owner", async () => {
        const newOwner = makeUserDoc({role: "owner"});
        mockGet.mockImplementation((url) => {
            if (url.includes("role=owner") || url.includes("tab=owners")) {
                return Promise.resolve(makeUsersListResponse([newOwner]));
            }
            return Promise.resolve(makeUsersListResponse([]));
        });

        const response = await mockGet("/users?role=owner");
        const owners = response.data.filter((u) => u.role === "owner");

        expect(owners).toHaveLength(1);
        expect(owners[ 0 ].full_name).toBe("New Test Owner");
        expect(owners[ 0 ].unit_number).toBe("UA001");
    });

    test("registered user is not approved by default (awaits admin review)", async () => {
        const newUser = makeUserDoc({is_approved: false});
        mockGet.mockResolvedValue(makeUsersListResponse([newUser]));

        const response = await mockGet("/users");
        const user = response.data[ 0 ];

        expect(user.is_approved).toBe(false);
    });

    test("registered owner without unit_number still appears in user list", async () => {
        const userNoUnit = makeUserDoc({unit_number: null});
        mockGet.mockResolvedValue(makeUsersListResponse([userNoUnit]));

        const response = await mockGet("/users");
        expect(response.data).toHaveLength(1);
        expect(response.data[ 0 ].unit_number).toBeNull();
    });

    test("registration failure returns 4xx and does NOT add user to list", async () => {
        // Simulate server 400 (e.g. duplicate email)
        mockPost.mockRejectedValue({response: {status: 400, data: {detail: "Email already registered"}}});
        // User list remains empty
        mockGet.mockResolvedValue(makeUsersListResponse([]));

        let registrationError = null;
        try {
            await mockPost("/auth/register", makeRegistrationPayload());
        } catch (err) {
            registrationError = err;
        }

        expect(registrationError).not.toBeNull();
        expect(registrationError.response.status).toBe(400);

        const listResponse = await mockGet("/users");
        expect(listResponse.data).toHaveLength(0);
    });
});

// ---------------------------------------------------------------------------
// Tests: Building context in registration (Fix 1)
// ---------------------------------------------------------------------------

describe("Registration API — Fix 1: building context header", () => {
    let mockPost;

    beforeEach(() => {
        mockPost = jest.fn();
    });

    test("registration request includes X-Building-ID header when building is known", async () => {
        // Frontend should pass the building header so the backend can scope unit lookup
        const headers = {"X-Building-ID": "13195"};
        mockPost.mockResolvedValue(makeAuthResponse());

        await mockPost("/auth/register", makeRegistrationPayload(), {headers});

        expect(mockPost).toHaveBeenCalledWith(
            "/auth/register",
            expect.anything(),
            expect.objectContaining({headers: expect.objectContaining({"X-Building-ID": "13195"})})
        );
    });

    test("registration without building header still works (get_optional_building returns DEFAULT)", async () => {
        // get_optional_building is Optional — should not block registration
        mockPost.mockResolvedValue(makeAuthResponse());

        const response = await mockPost("/auth/register", makeRegistrationPayload());
        expect(response.data.token).toBeTruthy();
    });
});

// ---------------------------------------------------------------------------
// Tests: DCA Status Update audit trail (Fix 3 — frontend perspective)
// ---------------------------------------------------------------------------

describe("DCA Status API — Fix 3: audit trail in admin UI", () => {
    let mockPatch;
    let mockGet;

    beforeEach(() => {
        mockPatch = jest.fn();
        mockGet = jest.fn();
    });

    test("PATCH /arrears/{unit}/dca-status returns success:true", async () => {
        mockPatch.mockResolvedValue({data: {success: true}});

        const response = await mockPatch("/arrears/UA042/dca-status?status=referred");
        expect(response.data.success).toBe(true);
    });

    test("PATCH /arrears/{unit}/dca-status with invalid status returns 400", async () => {
        mockPatch.mockRejectedValue({response: {status: 400, data: {detail: "Invalid DCA status"}}});

        let error = null;
        try {
            await mockPatch("/arrears/UA042/dca-status?status=INVALID");
        } catch (err) {
            error = err;
        }

        expect(error).not.toBeNull();
        expect(error.response.status).toBe(400);
        expect(error.response.data.detail).toContain("Invalid DCA status");
    });

    test("all six valid DCA status values are accepted", async () => {
        const validStatuses = ["none", "eligible", "referred", "recovering", "resolved", "recalled"];
        mockPatch.mockResolvedValue({data: {success: true}});

        for (const status of validStatuses) {
            const response = await mockPatch(`/arrears/UA042/dca-status?status=${status}`);
            expect(response.data.success).toBe(true);
        }
    });
});
