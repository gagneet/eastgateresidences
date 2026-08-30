// @featuretrace:smart-request — Request tracking list: resident's own requests, or the
// whole building's queue for a manager (role-scoped server-side).
// Layer: frontend
// Data flow: RequestsPage (?tab=my-requests[&status=]) → GET /workflow-requests[?status=]
//            → workflow_requests (building-scoped) → row link to /requests/{id}.
// Related: frontend/src/pages/dashboard/RequestsPage.jsx
//          backend/routers/workflow_requests.py
//          backend/services/morning_card_service.py (SLA card links straight into ?status=overdue)

import React, { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent } from '../../../components/ui/card';
import { Badge } from '../../../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { cn, formatDate } from '../../../lib/utils';
import { Activity, ChevronRight, Clock, Filter, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { isTerminalRequestStatus, managesBuildingRequests, TERMINAL_REQUEST_STATUSES } from '../../../lib/requests/requestScope';

type StatusFilter = 'all' | 'overdue' | 'awaiting_review' | 'in_progress' | 'awaiting_resident' | 'closed' | 'auto_resolved';

type WorkflowRequest = {
    id?: string;
    _id?: string;
    request_number?: string;
    request_type: string;
    subject?: string;
    title?: string;
    body?: string;
    description?: string;
    status: string;
    created_at: string;
    sla_due_at?: string | null;
    sla_breached?: boolean;
    unit_number?: string | null;
    submitted_by?: string | null;
    submitted_by_email?: string | null;
    assigned_to_name?: string | null;
};

type WorkflowRequestsResponse = WorkflowRequest[] | { requests?: WorkflowRequest[]; items?: WorkflowRequest[] };

const STATUS_FILTERS: Array<{ value: StatusFilter; label: string }> = [
    {value: 'all', label: 'All requests'},
    {value: 'overdue', label: 'Overdue'},
    {value: 'awaiting_review', label: 'Awaiting review'},
    {value: 'in_progress', label: 'In progress'},
    {value: 'awaiting_resident', label: 'Awaiting resident'},
    {value: 'closed', label: 'Closed'},
    {value: 'auto_resolved', label: 'Auto-resolved'},
];

// Re-exported from the shared contract so this file and the owner dashboards
// cannot drift apart on what counts as terminal.
const TERMINAL_STATUSES = TERMINAL_REQUEST_STATUSES;
/**
 * @generated FunctionHeader
 * Function: validStatusFilter
 * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const validStatusFilter = (value: string | null): StatusFilter => {
    return STATUS_FILTERS.some(filter => filter.value === value) ? value as StatusFilter : 'all';
};
/**
 * @generated FunctionHeader
 * Function: extractRequests
 * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const extractRequests = (data: WorkflowRequestsResponse): WorkflowRequest[] => {
    if (Array.isArray(data)) return data;
    return data.requests || data.items || [];
};
/**
 * @generated FunctionHeader
 * Function: isTerminalStatus
 * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const isTerminalStatus = (status: string) => isTerminalRequestStatus(status);
/**
 * @generated FunctionHeader
 * Function: isRequestOverdue
 * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const isRequestOverdue = (req: WorkflowRequest) => Boolean(req.sla_breached || req.status === 'overdue') && !isTerminalStatus(req.status);
/**
 * @generated FunctionHeader
 * Function: MyRequestsTab
 * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MyRequestsTab = () => {
    // GET /workflow-requests is role-scoped server-side: managers receive every
    // request raised for the building, everyone else only their own. The copy and
    // the per-row attribution follow that, so a manager arriving from the "past
    // SLA" dashboard card sees a real building queue rather than a list
    // mislabelled "My Requests".
    //
    // managesBuildingRequests() reads effective_role first, mirroring the router's
    // `_is_manager`. AuthContext.isManager() checks the raw role only and would
    // give a temporarily-elevated user resident copy over a building-wide list.
    const {api, user} = useAuth() as {
        api: { get: (url: string) => Promise<{ data: WorkflowRequestsResponse }> },
        user?: { effective_role?: string; role?: string } | null,
    };
    const managesRequests = managesBuildingRequests(user);
    const router = useRouter();
    const searchParams = useSearchParams();
    const searchParamString = searchParams?.toString?.() || '';
    const [requests, setRequests] = useState<WorkflowRequest[]>([]);
    const [allRequests, setAllRequests] = useState<WorkflowRequest[]>([]);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState<StatusFilter>(() => validStatusFilter(new URLSearchParams(searchParamString).get('status')));

    useEffect(() => {
        setStatusFilter(validStatusFilter(new URLSearchParams(searchParamString).get('status')));
    }, [searchParamString]);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchRequests
         * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchRequests = async () => {
            setLoading(true);
            try {
                const statusQuery = statusFilter === 'all' ? '' : '?status=' + encodeURIComponent(statusFilter);
                // Fetch the full list for dropdown counts; fetch the filtered list to preserve backend semantics
                // such as status=overdue mapping to SLA-breached non-terminal workflow requests.
                const [allResponse, filteredResponse] = await Promise.all([
                    api.get('/workflow-requests'),
                    statusFilter === 'all'
                        ? Promise.resolve(null)
                        : api.get('/workflow-requests' + statusQuery),
                ]);
                const allData = extractRequests(allResponse.data);
                setAllRequests(allData);
                setRequests(filteredResponse ? extractRequests(filteredResponse.data) : allData);
            } catch (error) {
                console.error('Failed to fetch requests:', error);
                setRequests([]);
                setAllRequests([]);
            } finally {
                setLoading(false);
            }
        };
        fetchRequests();
    }, [api, statusFilter]);
    /**
     * @generated FunctionHeader
     * Function: getStatusStyle
     * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusStyle = (status: string, breached: boolean) => {
        if (breached) return "bg-red-100 text-red-800 border-red-200";
        switch (status) {
            case 'auto_resolved':
                return "bg-green-100 text-green-800 border-green-200";
            case 'in_progress':
                return "bg-blue-100 text-blue-800 border-blue-200";
            case 'awaiting_review':
                return "bg-amber-100 text-amber-800 border-amber-200";
            case 'closed':
                return "bg-slate-100 text-slate-800 border-slate-200";
            default:
                return "bg-slate-50 text-slate-600 border-slate-100";
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getTimeRemaining
     * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getTimeRemaining = (deadline?: string | null) => {
        if (!deadline) return null;
        const now = new Date();
        const due = new Date(deadline);
        const diff = due.getTime() - now.getTime();

        if (diff < 0) return "Overdue";

        const hours = Math.floor(diff / ( 1000 * 60 * 60 ));
        if (hours > 24) return `Due in ${Math.floor(hours / 24)}d`;
        return `Due in ${hours}h`;
    };

    const selectedFilterLabel = STATUS_FILTERS.find(filter => filter.value === statusFilter)?.label || 'Selected status';
    const statusCounts = useMemo(() => {
        const counts = STATUS_FILTERS.reduce((acc, filter) => ({...acc, [filter.value]: 0}), {} as Record<StatusFilter, number>);
        allRequests.forEach(req => {
            counts.all += 1;
            if (isRequestOverdue(req)) {
                counts.overdue += 1;
                return;
            }
            if (req.status === 'auto_resolved') {
                counts.auto_resolved += 1;
                return;
            }
            if (TERMINAL_STATUSES.includes(req.status)) {
                counts.closed += 1;
                return;
            }
            if (STATUS_FILTERS.some(filter => filter.value === req.status)) {
                counts[req.status as StatusFilter] += 1;
            }
        });
        return counts;
    }, [allRequests]);
    /**
     * @generated FunctionHeader
     * Function: handleStatusFilterChange
     * Path: frontend/src/pages/dashboard/requests/MyRequestsTab.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleStatusFilterChange = (value: string) => {
        const nextFilter = validStatusFilter(value);
        setStatusFilter(nextFilter);
        const params = new URLSearchParams(searchParamString);
        if (nextFilter === 'all') {
            params.delete('status');
        } else {
            params.set('status', nextFilter);
        }
        const query = params.toString();
        router.replace(query ? `/requests?${query}` : '/requests', {scroll: false});
    };

    if (loading) return <div className="flex justify-center p-8"><Loader2
        className="h-6 w-6 animate-spin text-muted-foreground"/></div>;

    return (
        <div className="space-y-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <h3 className="text-sm font-semibold">
                        {managesRequests ? 'Building request queue' : 'Track requests'}
                    </h3>
                    <p className="text-xs text-muted-foreground">
                        {managesRequests
                            ? 'Every request raised by residents and owners for this building. Narrow the list by status.'
                            : 'View all requests or narrow the list by status.'}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Filter className="h-4 w-4 text-muted-foreground" aria-hidden="true"/>
                    <Select value={statusFilter} onValueChange={handleStatusFilterChange}>
                        <SelectTrigger className="w-full sm:w-52" aria-label="Filter requests by status">
                            <SelectValue placeholder="Filter status"/>
                        </SelectTrigger>
                        <SelectContent>
                            {STATUS_FILTERS.map(filter => (
                                <SelectItem key={filter.value} value={filter.value}>
                                    {filter.label} ({statusCounts[filter.value]})
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            </div>
            {requests.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground border-2 border-dashed rounded-lg">
                    <Activity className="h-12 w-12 mx-auto mb-4 opacity-20"/>
                    <p>{statusFilter === 'all' ? 'No requests found' : 'No ' + selectedFilterLabel.toLowerCase() + ' requests found'}</p>
                    <p className="text-xs mt-1">
                        {statusFilter !== 'all'
                            ? 'Switch to All requests to view the full request history'
                            : managesRequests
                                ? 'No residents or owners have raised a request for this building yet'
                                : 'Submit a new request using the Smart Request tool'}
                    </p>
                </div>
            ) : (
                requests.map(req => {
                    const requestOverdue = isRequestOverdue(req);
                    return <Link key={req.id || req._id} href={`/requests/${req.id || req._id}`}
                          className="block group">
                        <Card className="hover:border-primary transition-colors overflow-hidden">
                            <CardContent className="p-0 flex items-stretch">
                                <div className={cn(
                                    "w-1",
                                    requestOverdue ? "bg-red-500" :
                                        req.status === 'auto_resolved' ? "bg-green-500" :
                                            req.status === 'in_progress' ? "bg-blue-500" : "bg-slate-300"
                                )}/>
                                <div className="p-4 flex-1 flex items-center justify-between">
                                    <div className="space-y-1">
                                        <div className="flex items-center gap-2">
                                            <span
                                                className="font-mono text-[10px] text-muted-foreground">{req.request_number}</span>
                                            <h4 className="font-semibold text-sm group-hover:text-primary transition-colors">
                                                {req.subject || req.title || req.request_type.replace('_', ' ').toUpperCase()}
                                            </h4>
                                            <Badge variant="outline"
                                                   className={cn("text-[10px] uppercase", getStatusStyle(req.status, requestOverdue))}>
                                                {requestOverdue ? 'Overdue' : req.status.replace('_', ' ')}
                                            </Badge>
                                        </div>
                                        <p className="text-xs text-muted-foreground line-clamp-1">
                                            {req.body || req.description || 'No description provided'}
                                        </p>
                                        <div
                                            className="flex items-center gap-3 mt-2 text-[10px] text-muted-foreground font-medium uppercase tracking-tight">
                                            <span>{formatDate(req.created_at)}</span>
                                            <span>•</span>
                                            <span className="capitalize">{req.request_type.replace('_', ' ')}</span>
                                            {managesRequests && (
                                                <>
                                                    <span>•</span>
                                                    {/* The Postgres (ops.cases) read path returns only a
                                                        submitted_by_user_id UUID — never a display name — so
                                                        attribution degrades to the unit rather than rendering
                                                        a raw id at the user. */}
                                                    <span className="normal-case">
                                                        {req.unit_number ? `Unit ${req.unit_number}` : 'Unit —'}
                                                        {(req.submitted_by || req.submitted_by_email)
                                                            ? ` · ${req.submitted_by || req.submitted_by_email}`
                                                            : ''}
                                                    </span>
                                                    {req.assigned_to_name && (
                                                        <>
                                                            <span>•</span>
                                                            <span className="normal-case">Assigned to {req.assigned_to_name}</span>
                                                        </>
                                                    )}
                                                </>
                                            )}
                                            {!isTerminalStatus(req.status) && (
                                                <>
                                                    <span>•</span>
                                                    <span
                                                        className={cn("flex items-center gap-1", requestOverdue ? "text-red-600 font-bold" : "")}>
                            <Clock size={10}/>
                                                        {getTimeRemaining(req.sla_due_at)}
                          </span>
                                                </>
                                            )}
                                        </div>
                                    </div>
                                    <ChevronRight
                                        className="h-4 w-4 text-muted-foreground group-hover:translate-x-1 transition-transform"/>
                                </div>
                            </CardContent>
                        </Card>
                    </Link>
                })
            )}
        </div>
    );
};

export default MyRequestsTab;
