import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import { AlertTriangle, ArrowRight, CheckCircle2, ChevronLeft, Circle, CircleDot, Clock, Loader2, } from 'lucide-react';
import { toast } from 'sonner';

// ─── Status helpers ────────────────────────────────────────────────────────

const STATUS_CONFIG = {
    auto_resolved: {
        label: 'Auto-Resolved',
        badgeClass: 'bg-teal-100 text-teal-800',
        icon: CheckCircle2,
    },
    in_progress: {
        label: 'In Progress',
        badgeClass: 'bg-blue-100 text-blue-800',
        icon: CircleDot,
    },
    awaiting_review: {
        label: 'Awaiting Review',
        badgeClass: 'bg-amber-100 text-amber-800',
        icon: Clock,
    },
    awaiting_resident: {
        label: 'Awaiting Resident',
        badgeClass: 'bg-yellow-100 text-yellow-800',
        icon: Clock,
    },
    closed: {
        label: 'Closed',
        badgeClass: 'bg-gray-100 text-gray-700',
        icon: CheckCircle2,
    },
};
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboard/RequestStatusPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
// Every "back to requests" link from a single request goes to the QUEUE, not to bare
// /requests. RequestsPage treats a missing ?tab= as the request-definitions view — the
// "create a request" catalogue — so a user who opened a request from their queue was
// returned to a page for starting a new one, with no obvious way back to the list they
// had been looking at.
const REQUESTS_QUEUE_HREF = "/requests?tab=my-requests";

function statusBadge(status, isOverdue) {
    if (isOverdue && !['closed', 'auto_resolved'].includes(status)) {
        return (
            <Badge className="bg-red-100 text-red-800">
                <AlertTriangle className="w-3 h-3 mr-1"/>
                Overdue
            </Badge>
        );
    }
    const cfg = STATUS_CONFIG[ status ] || {
        label: status?.replace(/_/g, ' ') || 'Unknown',
        badgeClass: 'bg-gray-100 text-gray-700',
        icon: Circle,
    };
    const Icon = cfg.icon;
    return (
        <Badge className={cfg.badgeClass}>
            <Icon className="w-3 h-3 mr-1"/>
            {cfg.label}
        </Badge>
    );
}
/**
 * @generated FunctionHeader
 * Function: formatDateTime
 * Path: frontend/src/pages/dashboard/RequestStatusPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatDateTime(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString('en-AU', {
            weekday: 'short',
            day: 'numeric',
            month: 'short',
            year: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        });
    } catch {
        return iso;
    }
}
/**
 * @generated FunctionHeader
 * Function: slaCountdown
 * Path: frontend/src/pages/dashboard/RequestStatusPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function slaCountdown(sla_due_at) {
    if (!sla_due_at) return null;
    const due = new Date(sla_due_at);
    const now = new Date();
    const diffMs = due - now;
    if (diffMs <= 0) return null;
    const hours = Math.floor(diffMs / 3600000);
    const minutes = Math.floor(( diffMs % 3600000 ) / 60000);
    if (hours >= 24) {
        const days = Math.floor(hours / 24);
        return `Due in ${days} day${days !== 1 ? 's' : ''}`;
    }
    return `Due in ${hours}h ${minutes}m`;
}
/**
 * @generated FunctionHeader
 * Function: isTerminalStatus
 * Path: frontend/src/pages/dashboard/RequestStatusPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function isTerminalStatus(status) {
    return ['closed', 'auto_resolved', 'completed', 'cancelled'].includes(status);
}
// ─── Timeline step indicator ───────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: TimelineStep
 * Path: frontend/src/pages/dashboard/RequestStatusPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function TimelineStep({entry, isLast}) {
    const actionLabel = entry.action?.replace(/_/g, ' ') || 'Updated';

    return (
        <div className="flex gap-3">
            <div className="flex flex-col items-center">
                <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
                    <CheckCircle2 className="w-4 h-4 text-blue-600"/>
                </div>
                {!isLast && <div className="w-0.5 flex-1 bg-gray-200 mt-1 mb-1"/>}
            </div>
            <div className="pb-4 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-sm capitalize">{actionLabel}</span>
                    {entry.actor && entry.actor !== 'system' && (
                        <span className="text-xs text-muted-foreground">by {entry.actor}</span>
                    )}
                </div>
                {entry.note && entry.note !== '{}' && (
                    <p className="text-xs text-muted-foreground mt-0.5">{entry.note}</p>
                )}
                <p className="text-xs text-muted-foreground mt-0.5">
                    {formatDateTime(entry.timestamp)}
                </p>
            </div>
        </div>
    );
}
// ─── Main page ─────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: RequestStatusPage
 * Path: frontend/src/pages/dashboard/RequestStatusPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function RequestStatusPage() {
    const params = useParams();
    const requestId = params?.id;
    const {api, isManager, isAdmin} = useAuth();
    const [request, setRequest] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [resolutionNotes, setResolutionNotes] = useState('');
    const [updatingStatus, setUpdatingStatus] = useState(null);

    const fetchRequest = useCallback(async () => {
        if (!requestId) return;
        setLoading(true);
        setError(null);
        try {
            const res = await api.get(`/engagement/requests/${requestId}/status`);
            setRequest(res.data);
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to load request';
            setError(msg);
            toast.error(msg);
        } finally {
            setLoading(false);
        }
    }, [requestId, api]);

    useEffect(() => {
        fetchRequest();
    }, [fetchRequest]);
    /**
     * @generated FunctionHeader
     * Function: updateRequestStatus
     * Path: frontend/src/pages/dashboard/RequestStatusPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateRequestStatus = async (status) => {
        if (!requestId || updatingStatus) return;
        setUpdatingStatus(status);
        try {
            await api.put(`/workflow-requests/${requestId}/status`, {
                status,
                resolution_notes: resolutionNotes.trim() || null,
            });
            toast.success(status === 'closed' ? 'Request closed' : 'Request updated');
            setResolutionNotes('');
            await fetchRequest();
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to update request';
            toast.error(msg);
        } finally {
            setUpdatingStatus(null);
        }
    };

    // ── Loading / error states ──────────────────────────────────────────────

    if (loading) {
        return (
            <div className="flex items-center justify-center py-16">
                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground"/>
            </div>
        );
    }

    if (error || !request) {
        return (
            <div className="max-w-2xl mx-auto py-10 space-y-4">
                <div className="flex items-center gap-2 text-destructive">
                    <AlertTriangle className="w-5 h-5"/>
                    <span>{error || 'Request not found'}</span>
                </div>
                <Link href={REQUESTS_QUEUE_HREF}>
                    <Button variant="outline" size="sm">
                        <ChevronLeft className="w-4 h-4 mr-1"/>
                        Back to Requests
                    </Button>
                </Link>
            </div>
        );
    }

    const isClosed = isTerminalStatus(request.status);
    const isOverdue = Boolean(request.sla_breached) && !isClosed;
    const countdown = !isOverdue ? slaCountdown(request.sla_due_at) : null;
    const isStaff = isManager() || isAdmin();

    // ── Page render ────────────────────────────────────────────────────────

    return (
        <div className="max-w-2xl mx-auto space-y-6">
            {/* Back link */}
            <Link href={REQUESTS_QUEUE_HREF}>
                <Button variant="ghost" size="sm" className="pl-0">
                    <ChevronLeft className="w-4 h-4 mr-1"/>
                    All Requests
                </Button>
            </Link>

            {/* Header card */}
            <Card className="card-dashboard">
                <CardHeader className="pb-2">
                    <div className="flex items-start justify-between gap-2 flex-wrap">
                        <div>
                            <p className="text-xs text-muted-foreground font-mono">
                                {request.request_number || request.id}
                            </p>
                            <CardTitle className="text-lg mt-1">
                                {request.subject || request.request_type?.replace(/_/g, ' ')}
                            </CardTitle>
                        </div>
                        <div className="flex items-center gap-2 flex-wrap">
                            {statusBadge(request.status, isOverdue)}
                            {isOverdue && (
                                <Badge className="bg-red-50 text-red-700 border border-red-200">
                                    SLA Breached
                                </Badge>
                            )}
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="space-y-3">
                    <div className="grid grid-cols-2 gap-3 text-sm">
                        <div>
                            <p className="text-xs text-muted-foreground">Request type</p>
                            <p className="font-medium capitalize">
                                {request.request_type?.replace(/_/g, ' ') || '—'}
                            </p>
                        </div>
                        <div>
                            <p className="text-xs text-muted-foreground">Submitted</p>
                            <p className="font-medium">{formatDateTime(request.submitted_at)}</p>
                        </div>
                        {request.assigned_to_name && (
                            <div>
                                <p className="text-xs text-muted-foreground">Assigned to</p>
                                <p className="font-medium">{request.assigned_to_name}</p>
                            </div>
                        )}
                        <div>
                            <p className="text-xs text-muted-foreground">SLA deadline</p>
                            <p className={`font-medium ${isOverdue ? 'text-red-600' : ''}`}>
                                {formatDateTime(request.sla_due_at)}
                                {countdown && (
                                    <span className="text-xs text-muted-foreground ml-1">({countdown})</span>
                                )}
                            </p>
                        </div>
                    </div>

                    {/* Auto-resolved response */}
                    {request.auto_resolved && request.auto_resolution_response && (
                        <div className="rounded-md bg-teal-50 border border-teal-200 p-3">
                            <p className="text-xs font-semibold text-teal-800 mb-1">Auto-resolved response</p>
                            <p className="text-sm text-teal-900">{request.auto_resolution_response}</p>
                        </div>
                    )}

                    {/* Resolution summary (manager notes) */}
                    {!request.auto_resolved && request.resolution_summary && (
                        <div className="rounded-md bg-blue-50 border border-blue-200 p-3">
                            <p className="text-xs font-semibold text-blue-800 mb-1">Resolution notes</p>
                            <p className="text-sm text-blue-900">{request.resolution_summary}</p>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Timeline */}
            <Card className="card-dashboard">
                <CardHeader className="pb-2">
                    <CardTitle className="text-base">Activity Timeline</CardTitle>
                </CardHeader>
                <CardContent>
                    {request.timeline && request.timeline.length > 0 ? (
                        <div className="pt-1">
                            {request.timeline.map((entry, idx) => (
                                <TimelineStep
                                    key={idx}
                                    entry={entry}
                                    isLast={idx === request.timeline.length - 1}
                                />
                            ))}
                        </div>
                    ) : (
                        <p className="text-sm text-muted-foreground py-2">No timeline entries yet.</p>
                    )}
                </CardContent>
            </Card>

            {/* What to expect next / Manager actions */}
            {!isClosed && (
                isStaff ? (
                    <Card className="card-dashboard bg-amber-50 dark:bg-amber-950 border-amber-200">
                        <CardContent className="p-4 space-y-3">
                            <div className="flex items-start gap-2">
                                <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0"/>
                                <p className="text-sm text-amber-900 dark:text-amber-100 font-medium">
                                    {isOverdue ? 'This request has breached its SLA — action required.' : 'This request is awaiting staff action.'}
                                </p>
                            </div>
                            <Textarea
                                value={resolutionNotes}
                                onChange={(event) => setResolutionNotes(event.target.value)}
                                placeholder="Add resolution notes or next action..."
                                className="min-h-[88px] bg-white/80"
                                aria-label="Resolution notes"
                            />
                            <div className="flex flex-wrap gap-2 items-center">
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={!!updatingStatus}
                                    onClick={() => updateRequestStatus('in_progress')}
                                >
                                    {updatingStatus === 'in_progress' && (
                                        <Loader2 className="w-3 h-3 mr-1 animate-spin"/>
                                    )}
                                    Mark in progress
                                </Button>
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={!!updatingStatus}
                                    onClick={() => updateRequestStatus('awaiting_resident')}
                                >
                                    {updatingStatus === 'awaiting_resident' && (
                                        <Loader2 className="w-3 h-3 mr-1 animate-spin"/>
                                    )}
                                    Awaiting resident
                                </Button>
                                <Button
                                    type="button"
                                    size="sm"
                                    disabled={!!updatingStatus}
                                    onClick={() => updateRequestStatus('closed')}
                                >
                                    {updatingStatus === 'closed' && (
                                        <Loader2 className="w-3 h-3 mr-1 animate-spin"/>
                                    )}
                                    Close request
                                </Button>
                                <Link href={REQUESTS_QUEUE_HREF}>
                                    <Button type="button" size="sm" variant="ghost">
                                        View all requests
                                    </Button>
                                </Link>
                            </div>
                        </CardContent>
                    </Card>
                ) : (
                    <Card className="card-dashboard bg-blue-50 dark:bg-blue-950 border-blue-200">
                        <CardContent className="p-4">
                            <div className="flex items-start gap-2">
                                <ArrowRight className="w-4 h-4 text-blue-600 mt-0.5 flex-shrink-0"/>
                                <p className="text-sm text-blue-900 dark:text-blue-100">
                                    <strong>What happens next?</strong> Your request has been received and will be
                                    reviewed by the strata management team. You will receive a notification when
                                    there is an update. No need to follow up — this page shows the latest status.
                                </p>
                            </div>
                        </CardContent>
                    </Card>
                )
            )}
        </div>
    );
}
