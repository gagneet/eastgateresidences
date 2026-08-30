// @featuretrace:request-catalogue — Catalogue metadata and lazy adapters for resident request workflows.
// Data flow: RequestsPage → catalogue definition → existing request form component → existing domain API (building-scoped).
// Related: frontend/src/pages/dashboard/RequestsPage.jsx
//          docs/features/request_forms_feature.md
import {lazy} from 'react';
import {
    Dog,
    DollarSign,
    FileText,
    Hammer,
    HelpCircle,
    Key,
    Shield,
    Wrench,
} from 'lucide-react';

const ALL_AUTHENTICATED_ROLES = [
    'super_admin',
    'strata_admin',
    'strata_manager',
    'ec_member',
    'admin_staff',
    'owner',
    'tenant',
    'real_estate_agent',
];

export const REQUEST_CATEGORIES = [
    'All',
    'Repairs & Maintenance',
    'Insurance',
    'Pets & Residents',
    'Keys, Fobs & Access',
    'Renovations & Moving',
    'Money & Levies',
];

export const REQUEST_CATALOGUE = [
    {
        id: 'maintenance',
        title: 'Maintenance request',
        description: 'Open the dedicated Maintenance page to report and track repairs or hazards.',
        category: 'Repairs & Maintenance',
        icon: Wrench,
        component: lazy(() => import('../pages/dashboard/requests/MaintenanceRequestTab')),
        searchTerms: ['repair', 'broken', 'leak', 'damage', 'hazard', 'maintenance'],
        allowedRoles: ALL_AUTHENTICATED_ROLES,
        expectedResponse: 'Handled in the Maintenance workspace',
        documents: 'Photos can be added from the Maintenance page',
        legacyTabAliases: ['maintenance'],
        recommended: true,
    },
    {
        id: 'insurance-claim',
        title: 'Insurance claim',
        description: 'Report property damage and provide information for an insurance claim.',
        category: 'Insurance',
        icon: Shield,
        component: lazy(() => import('../pages/dashboard/requests/InsuranceClaimTab')),
        searchTerms: ['insurance', 'claim', 'damage', 'incident'],
        allowedRoles: ALL_AUTHENTICATED_ROLES,
        expectedResponse: 'Initial review within 2 business days',
        documents: 'Photos, invoices or reports may be required',
        legacyTabAliases: ['insurance-claim'],
    },
    {
        id: 'insurance-enquiry',
        title: 'Insurance question',
        description: 'Ask about building insurance, coverage or an existing claim.',
        category: 'Insurance',
        icon: HelpCircle,
        component: lazy(() => import('../pages/dashboard/requests/InsuranceEnquiryTab')),
        searchTerms: ['insurance', 'coverage', 'policy', 'question'],
        allowedRoles: ALL_AUTHENTICATED_ROLES,
        expectedResponse: 'Response timeframe is set by the building team',
        documents: 'No documents required to start',
        legacyTabAliases: ['insurance-enquiry'],
    },
    {
        id: 'pet',
        title: 'Pet application',
        description: 'Apply for approval to keep a pet at your unit.',
        category: 'Pets & Residents',
        icon: Dog,
        component: lazy(() => import('../pages/dashboard/requests/PetRequestTab')),
        searchTerms: ['pet', 'dog', 'cat', 'animal', 'approval'],
        allowedRoles: ALL_AUTHENTICATED_ROLES,
        expectedResponse: 'Decision timeframe depends on building rules',
        documents: 'Pet photo and supporting records may be required',
        legacyTabAliases: ['pet'],
        recommended: true,
    },
    {
        id: 'access-control',
        title: 'Access device request',
        description: 'Request review for a building access device or replacement.',
        category: 'Keys, Fobs & Access',
        icon: Key,
        component: lazy(() => import('../pages/dashboard/requests/AccessControlTab')),
        searchTerms: ['key', 'fob', 'swipe', 'remote', 'garage', 'access'],
        allowedRoles: ALL_AUTHENTICATED_ROLES,
        expectedResponse: 'Eligibility review before any issue or replacement',
        documents: 'Reason for request is required',
        legacyTabAliases: ['access-control', 'remote', 'key'],
        recommended: true,
    },
    {
        id: 'alterations',
        title: 'Alterations application',
        description: 'Request approval for renovations, installations or changes to your lot.',
        category: 'Renovations & Moving',
        icon: Hammer,
        component: lazy(() => import('../pages/dashboard/requests/AlterationsTab')),
        searchTerms: ['alteration', 'renovation', 'flooring', 'air conditioning', 'solar', 'works'],
        allowedRoles: ['super_admin', 'strata_admin', 'strata_manager', 'ec_member', 'admin_staff', 'owner', 'real_estate_agent'],
        expectedResponse: 'Timeframe depends on the approval pathway',
        documents: 'Plans and contractor details may be required',
        legacyTabAliases: ['alterations'],
    },
    {
        id: 'reimbursement',
        title: 'Reimbursement request',
        description: 'Request review of an out-of-pocket building expense with receipts.',
        category: 'Money & Levies',
        icon: DollarSign,
        component: lazy(() => import('../pages/dashboard/requests/ReimbursementTab')),
        searchTerms: ['reimbursement', 'refund', 'expense', 'receipt', 'payment'],
        allowedRoles: ['super_admin', 'strata_admin', 'strata_manager', 'ec_member', 'admin_staff', 'owner'],
        expectedResponse: 'Finance review within 5 business days',
        documents: 'Receipt or tax invoice required',
        legacyTabAliases: ['reimbursement'],
    },
    {
        id: 'work-order',
        title: 'Work orders',
        description: 'Review work orders, assigned work and vendor quotations.',
        category: 'Repairs & Maintenance',
        icon: FileText,
        component: lazy(() => import('../pages/dashboard/requests/WorkOrderTab')),
        searchTerms: ['work order', 'vendor', 'quote', 'contractor', 'job'],
        allowedRoles: ['super_admin', 'strata_admin', 'strata_manager', 'ec_member', 'admin_staff'],
        expectedResponse: 'Operational workspace',
        documents: 'Varies by work order',
        legacyTabAliases: ['work-order'],
    },
];

export const findRequestDefinition = (tabId) =>
    REQUEST_CATALOGUE.find((definition) =>
        definition.id === tabId || definition.legacyTabAliases.includes(tabId)
    );

export const isRequestEligible = (definition, role) =>
    Boolean(role && definition.allowedRoles.includes(role));

export const searchRequestCatalogue = (definitions, query) => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return definitions;

    return definitions.filter((definition) =>
        [
            definition.title,
            definition.description,
            definition.category,
            ...definition.searchTerms,
        ].some((value) => value.toLowerCase().includes(normalized))
    );
};


/**
 * Join server-authorised metadata to client-only icons and lazy form adapters.
 * Unknown server keys are ignored so the backend cannot request an arbitrary import.
 */
export const hydrateRequestCatalogue = (authorisedDefinitions) => {
    const adapters = new Map(REQUEST_CATALOGUE.map((definition) => [definition.id, definition]));

    return authorisedDefinitions.flatMap((definition) => {
        const adapter = adapters.get(definition.key);
        if (!adapter) return [];

        return [{
            ...adapter,
            ...definition,
            id: definition.key,
            searchTerms: definition.search_terms,
            expectedResponse: definition.expected_response,
            legacyTabAliases: definition.legacy_tab_aliases,
        }];
    });
};

const REQUEST_ANALYTICS_EVENTS = new Set([
    'request_catalogue_viewed',
    'request_catalogue_searched',
    'request_category_selected',
    'request_form_started',
    'request_form_submitted',
]);

const allowedAnalyticsKeys = new Set([
    'definition_key',
    'definition_version',
    'role',
    'result',
    'status',
    'category',
    'elapsed_time_bucket',
]);

export const trackRequestCatalogueEvent = (eventName, properties = {}) => {
    if (!REQUEST_ANALYTICS_EVENTS.has(eventName) || typeof window === 'undefined') return;

    // Keep this list intentionally narrower than the product spec allows:
    // no building identifier is emitted until the approved analytics format is
    // wired, and free-form request/search data is never accepted.
    const safeProperties = Object.fromEntries(
        Object.entries(properties).filter(([key, value]) =>
            allowedAnalyticsKeys.has(key)
            && ['string', 'number', 'boolean'].includes(typeof value)
            && String(value).length <= 120
        )
    );
    const event = {event: eventName, ...safeProperties};

    window.dispatchEvent(new CustomEvent('strataos:analytics', {detail: event}));
    if (Array.isArray(window.dataLayer)) {
        window.dataLayer.push(event);
    }
};
