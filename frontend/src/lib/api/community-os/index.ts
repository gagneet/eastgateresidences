
// Community OS API client — pass the axios instance from useAuth()

/**
 * @generated FunctionHeader
 * Function: proposalsApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const proposalsApi = (api: any) => ({

    list: (params?: any) => api.get('/proposals/', {params}),

    get: (id: string) => api.get(`/proposals/${id}`),

    create: (data: any) => api.post('/proposals/', data),

    vote: (id: string, vote: any) => api.post(`/proposals/${id}/vote`, vote),

    open: (id: string) => api.post(`/proposals/${id}/open`),

    close: (id: string) => api.post(`/proposals/${id}/close`),

    updateStatus: (id: string, data: any) => api.put(`/proposals/${id}/status`, data),
});
/**
 * @generated FunctionHeader
 * Function: savingsApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const savingsApi = (api: any) => ({

    list: (params?: any) => api.get('/savings/', {params}),

    create: (data: any) => api.post('/savings/', data),

    summary: (params?: any) => api.get('/savings/summary', {params}),
});
/**
 * @generated FunctionHeader
 * Function: volunteerApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const volunteerApi = (api: any) => ({

    list: (params?: any) => api.get('/volunteer/', {params}),

    get: (id: string) => api.get(`/volunteer/${id}`),

    create: (data: any) => api.post('/volunteer/', data),

    register: (id: string) => api.post(`/volunteer/${id}/register`),

    complete: (id: string, data: any) => api.put(`/volunteer/${id}/complete`, data),
});
/**
 * @generated FunctionHeader
 * Function: healthApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const healthApi = (api: any) => ({

    score: () => api.get('/community-dashboard/health-score'),

    summary: () => api.get('/community-dashboard/building-summary'),
});
/**
 * @generated FunctionHeader
 * Function: workflowRequestsApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const workflowRequestsApi = (api: any) => ({

    list: (params?: any) => api.get('/workflow-requests/', {params}),

    get: (id: string) => api.get(`/workflow-requests/${id}`),

    getStatus: (id: string) => api.get(`/workflow-requests/${id}/status`),

    create: (data: any) => api.post('/workflow-requests/', data),

    createSmart: (data: any) => api.post('/workflow-requests/smart', data),

    updateStatus: (id: string, data: any) => api.put(`/workflow-requests/${id}/status`, data),
});
/**
 * @generated FunctionHeader
 * Function: engagementApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const engagementApi = (api: any) => ({

    // Submit a smart (auto-classified) request
    createSmartRequest: (data: {
        subject: string;
        body: string;
        unit_number?: string;
        source_channel?: string;
    }) => api.post('/engagement/requests/smart', data),


    // List requests (scoped by role)
    listRequests: (params?: any) => api.get('/engagement/requests', {params}),


    // Get request status + timeline
    getRequestStatus: (id: string) => api.get(`/engagement/requests/${id}/status`),


    // Manager triage queue
    getTriageQueue: () => api.get('/engagement/triage'),


    // Deflection rate stats
    getStats: (days?: number) => api.get('/engagement/stats', {params: {days}}),
});
/**
 * @generated FunctionHeader
 * Function: workflowGovernanceApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const workflowGovernanceApi = (api: any) => ({

    // Catalogue enriched with last run data
    getStatus: (category?: string) =>
        api.get('/workflows/status', {params: category ? {category} : undefined}),


    // Recent runs for a specific workflow
    getRuns: (workflowId: string, limit?: number) =>
        api.get(`/workflows/${workflowId}/runs`, {params: {limit}}),


    // Raw catalogue (super_admin only)
    getCatalogue: () => api.get('/workflows/catalogue'),
});
/**
 * @generated FunctionHeader
 * Function: morningCardApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const morningCardApi = (api: any) => ({

    getCard: () => api.get('/engagement/morning-card'),

    getBuildingPulse: () => api.get('/engagement/building-pulse'),
});
/**
 * @generated FunctionHeader
 * Function: navBadgesApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const navBadgesApi = (api: any) => ({

    getBadges: () => api.get('/nav/badges'),
});
/**
 * @generated FunctionHeader
 * Function: residencyApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const residencyApi = (api: any) => ({

    getMyPassport: () => api.get('/residency/my-passport'),

    downloadPassport: () => api.get('/residency/my-passport/download', {responseType: 'blob'}),

    verifyPassport: (token: string) => api.get(`/residency/verify/${token}`),
});
/**
 * @generated FunctionHeader
 * Function: safetyApi
 * Path: frontend/src/lib/api/community-os/index.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const safetyApi = (api: any) => ({

    listEvents: () => api.get('/safety/events'),

    createEvent: (data: any) => api.post('/safety/events', data),

    resolveEvent: (id: string) => api.patch(`/safety/events/${id}/resolve`),
});
