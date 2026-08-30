// @featuretrace:request-catalogue — Searchable, role-aware entry point for request discovery and tracking.
// Data flow: RequestsPage → requestCatalogue → existing request form component → existing domain API (building-scoped).
// Related: frontend/src/config/requestCatalogue.js
//          frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
import React, {Suspense, useEffect, useMemo, useRef, useState} from 'react';
import {useRouter, useSearchParams} from 'next/navigation';
import {Activity, ArrowLeft, Clock, FileCheck2, Search} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '../../components/ui/card';
import {useAuth} from '../../contexts/AuthContext';
import {managesBuildingRequests} from '../../lib/requests/requestScope';
import MyRequestsTab from './requests/MyRequestsTab';
import {
    findRequestDefinition,
    hydrateRequestCatalogue,
    REQUEST_CATALOGUE,
    REQUEST_CATEGORIES,
    searchRequestCatalogue,
    trackRequestCatalogueEvent,
} from '../../config/requestCatalogue';

const viewFromQuery = (queryString) => {
    const params = new URLSearchParams(queryString);
    const requestedTab = params.get('tab');
    // A `?status=` deep link (e.g. the Management dashboard's "1 request past SLA"
    // card linking to ?status=overdue) is only meaningful on the tracking list, so
    // it implies that view even when ?tab= was omitted. Without this, such links
    // silently landed on the form catalogue with the filter ignored.
    if (!requestedTab) {
        return params.get('status')
            ? {view: 'my-requests', definition: null}
            : {view: 'catalogue', definition: null};
    }
    if (requestedTab === 'my-requests') return {view: 'my-requests', definition: null};

    const definition = findRequestDefinition(requestedTab);
    return definition
        ? {view: 'form', definition}
        : {view: 'catalogue', definition: null};
};

// A nested App Router page can force a specific request form via `initialTab`
// (e.g. /requests/access-control → the "Keys, Fobs & Access" form) without a
// ?tab= query string. The legacy ?tab= path still works when no prop is given.
/**
 * @param {{initialTab?: string}} [props]
 */
const RequestsPage = ({initialTab} = {}) => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const {api, user} = useAuth();
    // GET /workflow-requests returns the whole building's requests to a manager and
    // only the caller's own to everyone else, so the view is labelled to match what
    // it actually contains. managesBuildingRequests() mirrors the router's own
    // `_is_manager` (effective_role first) rather than AuthContext.isManager(),
    // which reads the raw role and so mislabels a temporarily-elevated user.
    const trackingLabel = managesBuildingRequests(user) ? 'Request Queue' : 'My Requests';
    const searchParamString = searchParams?.toString?.() || '';
    const routeState = useMemo(() => {
        if (initialTab) {
            const forced = viewFromQuery(`tab=${initialTab}`);
            if (forced.view !== 'catalogue') return forced;
        }
        return viewFromQuery(searchParamString);
    }, [searchParamString, initialTab]);
    const [query, setQuery] = useState('');
    const [category, setCategory] = useState('All');
    const [eligibleDefinitions, setEligibleDefinitions] = useState([]);
    const [catalogueLoading, setCatalogueLoading] = useState(true);
    const [catalogueError, setCatalogueError] = useState('');
    const viewStartedAt = useRef(Date.now());

    useEffect(() => {
        let active = true;

        const loadCatalogue = async () => {
            setCatalogueLoading(true);
            setCatalogueError('');
            try {
                const response = await api.get('/request-catalogue');
                if (active) {
                    setEligibleDefinitions(hydrateRequestCatalogue(response.data));
                    trackRequestCatalogueEvent('request_catalogue_viewed', {
                        role: user?.effective_role || user?.role,
                        result: 'loaded',
                    });
                }
            } catch (error) {
                if (active) {
                    setEligibleDefinitions([]);
                    setCatalogueError(
                        error.response?.data?.detail?.message
                        || error.response?.data?.detail
                        || 'The request catalogue could not be loaded.'
                    );
                    trackRequestCatalogueEvent('request_catalogue_viewed', {
                        role: user?.effective_role || user?.role,
                        result: 'error',
                    });
                }
            } finally {
                if (active) setCatalogueLoading(false);
            }
        };

        loadCatalogue();
        return () => {
            active = false;
        };
    }, [api, user?.building_id, user?.effective_role, user?.role]);
    const visibleDefinitions = useMemo(() => {
        const categoryMatches = category === 'All'
            ? eligibleDefinitions
            : eligibleDefinitions.filter((definition) => definition.category === category);
        return searchRequestCatalogue(categoryMatches, query);
    }, [category, eligibleDefinitions, query]);

    useEffect(() => {
        if (catalogueLoading || routeState.view !== 'form') return;
        const authorised = eligibleDefinitions.some(
            (definition) => definition.id === routeState.definition?.id
        );
        if (!authorised) router.replace('/requests', {scroll: false});
    }, [catalogueLoading, eligibleDefinitions, routeState, router]);

    const navigate = (tabId) => {
        if (tabId && tabId !== 'my-requests') {
            const definition = eligibleDefinitions.find((item) => item.id === tabId);
            trackRequestCatalogueEvent('request_form_started', {
                definition_key: definition?.id,
                definition_version: definition?.version,
                role: user?.effective_role || user?.role,
                category: definition?.category,
                elapsed_time_bucket: Date.now() - viewStartedAt.current < 30000 ? 'under_30s' : '30s_plus',
            });
        }
        const params = new URLSearchParams(searchParamString);
        if (tabId) params.set('tab', tabId);
        else params.delete('tab');
        params.delete('status');
        const nextQuery = params.toString();
        router.replace(nextQuery ? `/requests?${nextQuery}` : '/requests', {scroll: false});
    };

    const ActiveForm = routeState.definition?.component;

    return (
        <div className="max-w-7xl mx-auto space-y-6" data-testid="requests-page">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Requests & Applications</h1>
                    <p className="text-muted-foreground">
                        Find the right form, submit it and track progress in one place.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={() => navigate('my-requests')}
                    className="inline-flex items-center justify-center gap-2 rounded-full border bg-background px-4 py-2 text-sm font-medium shadow-sm transition hover:bg-muted active:scale-95"
                    data-testid="my-requests-button"
                >
                    <Activity className="h-4 w-4"/>
                    {trackingLabel}
                </button>
            </div>

            {routeState.view === 'catalogue' && (
                <>
                    <Card className="card-dashboard border-blue-200 bg-blue-50 dark:bg-blue-950">
                        <CardContent className="p-4">
                            <p className="text-sm text-blue-900 dark:text-blue-100">
                                For an immediate threat to life or property, contact emergency services first.
                                Routine requests can be started below and tracked from My Requests.
                            </p>
                        </CardContent>
                    </Card>

                    <div className="relative">
                        <Search className="absolute left-3 top-3 h-5 w-5 text-muted-foreground" aria-hidden="true"/>
                        <input
                            type="search"
                            value={query}
                            onChange={(event) => {
                                const nextValue = event.target.value;
                                setQuery(nextValue);
                                if (nextValue.trim()) {
                                    trackRequestCatalogueEvent('request_catalogue_searched', {
                                        role: user?.effective_role || user?.role,
                                        result: 'changed',
                                    });
                                }
                            }}
                            placeholder="Search requests — try pet, leak, fob or insurance"
                            aria-label="Search requests and applications"
                            className="h-11 w-full rounded-lg border bg-background pl-10 pr-4 text-sm outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring"
                            data-testid="request-catalogue-search"
                        />
                    </div>

                    <div className="flex gap-2 overflow-x-auto pb-1" aria-label="Request categories">
                        {REQUEST_CATEGORIES.map((item) => (
                            <button
                                type="button"
                                key={item}
                                onClick={() => {
                                    setCategory(item);
                                    trackRequestCatalogueEvent('request_category_selected', {
                                        role: user?.effective_role || user?.role,
                                        category: item,
                                    });
                                }}
                                aria-pressed={category === item}
                                className={`shrink-0 rounded-full border px-4 py-2 text-sm font-medium transition active:scale-95 ${
                                    category === item
                                        ? 'border-primary bg-primary text-primary-foreground'
                                        : 'bg-background hover:bg-muted'
                                }`}
                                data-testid={`request-category-${item.toLowerCase().replaceAll(' ', '-')}`}
                            >
                                {item}
                            </button>
                        ))}
                    </div>

                    {catalogueLoading ? (
                        <Card className="card-dashboard">
                            <CardContent className="p-8 text-center text-sm text-muted-foreground">
                                Loading available requests…
                            </CardContent>
                        </Card>
                    ) : catalogueError ? (
                        <Card className="card-dashboard border-destructive/40">
                            <CardContent className="p-8 text-center">
                                <p className="font-medium">Requests are temporarily unavailable</p>
                                <p className="mt-1 text-sm text-muted-foreground">{catalogueError}</p>
                            </CardContent>
                        </Card>
                    ) : visibleDefinitions.length > 0 ? (
                        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="request-catalogue-grid">
                            {visibleDefinitions.map((definition) => {
                                const Icon = definition.icon;
                                return (
                                    <Card key={definition.id} className="card-dashboard flex h-full flex-col">
                                        <CardHeader className="pb-3">
                                            <div className="flex items-start justify-between gap-3">
                                                <div className="rounded-lg bg-primary/10 p-2 text-primary">
                                                    <Icon className="h-5 w-5" aria-hidden="true"/>
                                                </div>
                                                {definition.recommended && (
                                                    <span className="rounded-full bg-muted px-2 py-1 text-xs font-medium">
                                                        Common
                                                    </span>
                                                )}
                                            </div>
                                            <CardTitle className="text-lg">{definition.title}</CardTitle>
                                        </CardHeader>
                                        <CardContent className="flex flex-1 flex-col gap-4">
                                            <p className="text-sm text-muted-foreground">{definition.description}</p>
                                            <div className="space-y-2 text-xs text-muted-foreground">
                                                <p className="flex gap-2">
                                                    <Clock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true"/>
                                                    {definition.expectedResponse}
                                                </p>
                                                <p className="flex gap-2">
                                                    <FileCheck2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true"/>
                                                    {definition.documents}
                                                </p>
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => navigate(definition.id)}
                                                className="mt-auto inline-flex items-center justify-center rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 active:scale-95"
                                                data-testid={`start-request-${definition.id}`}
                                            >
                                                Start request
                                            </button>
                                        </CardContent>
                                    </Card>
                                );
                            })}
                        </div>
                    ) : (
                        <Card className="card-dashboard">
                            <CardContent className="p-8 text-center">
                                <p className="font-medium">No matching requests found</p>
                                <p className="mt-1 text-sm text-muted-foreground">
                                    Try a broader search or choose another category.
                                </p>
                            </CardContent>
                        </Card>
                    )}
                </>
            )}

            {routeState.view !== 'catalogue' && (
                <Card className="card-dashboard">
                    <CardHeader className="space-y-3">
                        <button
                            type="button"
                            onClick={() => navigate(null)}
                            className="inline-flex w-fit items-center gap-2 rounded-full px-3 py-2 text-sm font-medium hover:bg-muted active:scale-95"
                            data-testid="back-to-request-catalogue"
                        >
                            <ArrowLeft className="h-4 w-4"/>
                            All requests
                        </button>
                        <CardTitle>
                            {routeState.view === 'my-requests' ? trackingLabel : routeState.definition?.title}
                        </CardTitle>
                        {routeState.definition && (
                            <p className="text-sm font-normal text-muted-foreground">
                                {routeState.definition.description}
                            </p>
                        )}
                    </CardHeader>
                    <CardContent>
                        {routeState.view === 'my-requests' && <MyRequestsTab/>}
                        {routeState.view === 'form' && catalogueLoading && (
                            <p className="py-8 text-center text-sm text-muted-foreground">Checking request access…</p>
                        )}
                        {routeState.view === 'form' && !catalogueLoading
                            && !eligibleDefinitions.some((definition) => definition.id === routeState.definition?.id) && (
                            <p className="py-8 text-center text-sm text-muted-foreground">
                                This request is not available for your current building or role.
                            </p>
                        )}
                        {routeState.view === 'form' && !catalogueLoading
                            && eligibleDefinitions.some((definition) => definition.id === routeState.definition?.id)
                            && ActiveForm && (
                                <Suspense fallback={<p className="py-8 text-center text-sm text-muted-foreground">Loading form…</p>}>
                                    <ActiveForm/>
                                </Suspense>
                            )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
};

export default RequestsPage;
