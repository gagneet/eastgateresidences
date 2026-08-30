// @featuretrace:smart-request — Smart Request page: resident-facing triage and request submission UI.
// Layer: frontend
// Data flow: SmartRequestPage.jsx → POST /workflow-requests/smart → workflow_requests collection (building-scoped).
// Related: backend/routers/workflow_requests.py
//           backend/services/comms_intake_service.py
//           frontend/src/pages/dashboard/RequestsPage.jsx  (request detail + all request types)

import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    MessageSquare, Wrench, DollarSign, FileText, Volume2, HelpCircle,
    Loader2, AlertCircle, CheckCircle2, Clock, ChevronRight, ChevronLeft,
    Info, UserCheck, Timer, Mail, Users, BarChart3, RefreshCw, UserCog
} from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { toast } from 'sonner';
import { workflowRequestsApi } from '../../lib/api/community-os';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../../components/ui/dialog';

const CATEGORIES = [
    {
        id: 'maintenance',
        label: 'Maintenance',
        icon: Wrench,
        description: 'Repairs, work orders, building issues',
        color: 'text-orange-500',
        bg: 'bg-orange-50 hover:bg-orange-100'
    },
    {
        id: 'levy_query',
        label: 'Levy Query',
        icon: DollarSign,
        description: 'Questions about levies or payments',
        color: 'text-green-600',
        bg: 'bg-green-50 hover:bg-green-100'
    },
    {
        id: 'bylaw_query',
        label: 'By-Law Query',
        icon: FileText,
        description: 'Questions about strata by-laws',
        color: 'text-blue-500',
        bg: 'bg-blue-50 hover:bg-blue-100'
    },
    {
        id: 'document_request',
        label: 'Document Request',
        icon: FileText,
        description: 'Request strata documents or records',
        color: 'text-purple-500',
        bg: 'bg-purple-50 hover:bg-purple-100'
    },
    {
        id: 'noise_complaint',
        label: 'Noise Complaint',
        icon: Volume2,
        description: 'Report noise disturbances',
        color: 'text-red-500',
        bg: 'bg-red-50 hover:bg-red-100'
    },
    {
        id: 'other',
        label: 'Other',
        icon: HelpCircle,
        description: 'Any other request or query',
        color: 'text-gray-500',
        bg: 'bg-gray-50 hover:bg-gray-100'
    },
];

// Tooltip text explaining each workflow stage
const STAGE_INFO = {
    'Open': 'Your request has been submitted and is in the queue for review.',
    'In Review': 'The strata management team is reviewing your request.',
    'In Progress': 'Your request has been assigned and is being actively worked on.',
    'Resolved': 'Your request has been resolved. Please confirm if satisfactory.',
    'Closed': 'The request has been fully closed and archived.',
};
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboard/SmartRequestPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function statusBadge(status) {
    const map = {
        open: {label: 'Open', className: 'bg-blue-100 text-blue-800'},
        in_progress: {label: 'In Progress', className: 'bg-yellow-100 text-yellow-800'},
        resolved: {label: 'Resolved', className: 'bg-green-100 text-green-800'},
        auto_resolved: {label: 'Auto-Resolved', className: 'bg-teal-100 text-teal-800'},
        closed: {label: 'Closed', className: 'bg-gray-100 text-gray-700'},
        overdue: {label: 'Overdue', className: 'bg-red-100 text-red-800'},
    };
    const cfg = map[ status ] || {label: status, className: 'bg-gray-100 text-gray-700'};
    return <Badge className={cfg.className}>{cfg.label}</Badge>;
}
/**
 * @generated FunctionHeader
 * Function: isSlaOverdue
 * Path: frontend/src/pages/dashboard/SmartRequestPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function isSlaOverdue(req) {
    if (!req.sla_due_at) return false;
    const status = req.status;
    if (status === 'closed' || status === 'resolved' || status === 'auto_resolved') return false;
    return new Date(req.sla_due_at) < new Date();
}
/**
 * @generated FunctionHeader
 * Function: SmartRequestPage
 * Path: frontend/src/pages/dashboard/SmartRequestPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function SmartRequestPage() {
    const {user, api} = useAuth();
    const wrApi = workflowRequestsApi(api);

    const [step, setStep] = useState(1);
    const [selectedCategory, setSelectedCategory] = useState(null);
    const [form, setForm] = useState({subject: '', description: ''});
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState(null);

    const [myRequests, setMyRequests] = useState([]);
    const [loadingRequests, setLoadingRequests] = useState(true);
    const [selectedRequest, setSelectedRequest] = useState(null);
    const [requestDetailOpen, setRequestDetailOpen] = useState(false);
    const [requestTimeline, setRequestTimeline] = useState([]);
    const [loadingTimeline, setLoadingTimeline] = useState(false);

    // Manager-only state
    const MANAGER_ROLES = ['super_admin', 'ec_member', 'strata_manager'];
    const isManager = MANAGER_ROLES.includes(user?.role);
    const [managerView, setManagerView] = useState(false);
    const [allRequests, setAllRequests] = useState([]);
    const [loadingAll, setLoadingAll] = useState(false);
    const [queueFilter, setQueueFilter] = useState('open');
    const [triageStats, setTriageStats] = useState(null);
    const [updatingStatus, setUpdatingStatus] = useState(null);

    const loadMyRequests = useCallback(async () => {
        setLoadingRequests(true);
        try {
            const res = await wrApi.list({my_requests: true});
            setMyRequests(res.data?.requests || res.data || []);
        } catch {
            setMyRequests([]);
        } finally {
            setLoadingRequests(false);
        }
    }, []);

    const loadAllRequests = useCallback(async () => {
        setLoadingAll(true);
        try {
            const [reqRes, statsRes] = await Promise.allSettled([
                wrApi.list({status: queueFilter !== 'all' ? queueFilter : undefined}),
                api.get('/workflow-requests/stats/triage'),
            ]);
            setAllRequests(reqRes.status === 'fulfilled' ? ( reqRes.value.data?.requests || reqRes.value.data || [] ) : []);
            if (statsRes.status === 'fulfilled') setTriageStats(statsRes.value.data);
        } catch {
            setAllRequests([]);
        } finally {
            setLoadingAll(false);
        }
    }, [queueFilter]);
    /**
     * @generated FunctionHeader
     * Function: handleUpdateStatus
     * Path: frontend/src/pages/dashboard/SmartRequestPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpdateStatus = async (requestId, newStatus) => {
        setUpdatingStatus(requestId);
        try {
            await wrApi.updateStatus(requestId, {status: newStatus});
            await loadAllRequests();
        } catch {
            // silently ignore
        } finally {
            setUpdatingStatus(null);
        }
    };

    useEffect(() => {
        loadMyRequests();
    }, [loadMyRequests]);
    useEffect(() => {
        if (isManager && managerView) loadAllRequests();
    }, [managerView, loadAllRequests, isManager]);
    /**
     * @generated FunctionHeader
     * Function: handleSelectCategory
     * Path: frontend/src/pages/dashboard/SmartRequestPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSelectCategory = (cat) => {
        setSelectedCategory(cat);
        setForm({subject: `${cat.label} Request`, description: ''});
        setStep(2);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/SmartRequestPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            const res = await wrApi.createSmart({
                subject: form.subject,
                body: form.description,
            });
            const data = res.data;
            setResult({
                id: data.id,
                auto_resolved: data.auto_resolved || data.status === 'auto_resolved',
                reference: data.reference_number || data.request_number || data.id,
                message: data.resolution_message || data.auto_resolution_message || data.auto_resolution_response || null,
                sla_hours: data.sla_hours || 48,
            });
            setStep(3);
            loadMyRequests();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to submit request');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleReset
     * Path: frontend/src/pages/dashboard/SmartRequestPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReset = () => {
        setStep(1);
        setSelectedCategory(null);
        setForm({subject: '', description: ''});
        setResult(null);
    };
    /**
     * @generated FunctionHeader
     * Function: openRequestDetail
     * Path: frontend/src/pages/dashboard/SmartRequestPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openRequestDetail = async (req) => {
        setSelectedRequest(req);
        setRequestDetailOpen(true);
        setLoadingTimeline(true);
        try {
            const res = await wrApi.getStatus(req.id || req._id);
            setRequestTimeline(res.data?.timeline || []);
        } catch {
            setRequestTimeline([]);
        } finally {
            setLoadingTimeline(false);
        }
    };

    return (
        <div className="p-6 max-w-3xl mx-auto space-y-6" role="main">
            <div>
                <h1 className="text-2xl font-bold flex items-center gap-2">
                    <MessageSquare className="h-6 w-6 text-sky-500" aria-hidden="true"/>
                    Smart Request
                </h1>
                <p className="text-muted-foreground text-sm">Submit a request or query — our team aims to respond within
                    48 hours</p>
            </div>

            {/* Workflow info banner — always visible */}
            <div className="rounded-xl border border-sky-100 bg-sky-50 p-4" role="region"
                 aria-label="How Smart Request works">
                <div className="flex items-start gap-3">
                    <Info className="h-5 w-5 text-sky-500 shrink-0 mt-0.5" aria-hidden="true"/>
                    <div className="space-y-2 flex-1">
                        <p className="text-sm font-semibold text-sky-800">How your request is handled</p>
                        <ol className="text-xs text-sky-700 space-y-1 list-none">
                            <li className="flex items-center gap-2"><span
                                className="w-5 h-5 rounded-full bg-sky-200 text-sky-800 flex items-center justify-center font-bold text-[10px] shrink-0">1</span>You
                                submit → system checks for instant answers (auto-resolve)
                            </li>
                            <li className="flex items-center gap-2"><span
                                className="w-5 h-5 rounded-full bg-sky-200 text-sky-800 flex items-center justify-center font-bold text-[10px] shrink-0">2</span>If
                                not auto-resolved → strata management team reviews within <strong>48 hours</strong></li>
                            <li className="flex items-center gap-2"><span
                                className="w-5 h-5 rounded-full bg-sky-200 text-sky-800 flex items-center justify-center font-bold text-[10px] shrink-0">3</span>Team
                                acts and may contact you for more info
                            </li>
                            <li className="flex items-center gap-2"><span
                                className="w-5 h-5 rounded-full bg-sky-200 text-sky-800 flex items-center justify-center font-bold text-[10px] shrink-0">4</span>Request
                                is resolved → you receive a notification
                            </li>
                        </ol>
                        <div className="flex flex-wrap gap-3 pt-1 text-[11px] text-sky-600 font-medium">
                            <span className="flex items-center gap-1"><UserCheck className="h-3.5 w-3.5"
                                                                                 aria-hidden="true"/>Reviewed by strata team</span>
                            <span className="flex items-center gap-1"><Timer className="h-3.5 w-3.5"
                                                                             aria-hidden="true"/>48-hour SLA</span>
                            <span className="flex items-center gap-1"><Mail className="h-3.5 w-3.5" aria-hidden="true"/>Email notification on update</span>
                        </div>
                    </div>
                </div>
            </div>

            {/* Step indicator */}
            <div className="flex items-center gap-2 text-sm" role="navigation" aria-label="Request submission steps">
                {[1, 2, 3].map((s, i) => (
                    <React.Fragment key={s}>
                        <div
                            className={`flex items-center gap-1.5 ${step === s ? 'text-primary font-medium' : step > s ? 'text-green-600' : 'text-muted-foreground'}`}
                            aria-current={step === s ? 'step' : undefined}>
                            {step > s ? <CheckCircle2 className="h-4 w-4" aria-hidden="true"/> : <span
                                className={`w-5 h-5 rounded-full text-xs flex items-center justify-center ${step === s ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}
                                aria-hidden="true">{s}</span>}
                            {['Category', 'Details', 'Done'][ i ]}
                        </div>
                        {i < 2 && <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true"/>}
                    </React.Fragment>
                ))}
            </div>

            {/* Step 1: Category Grid */}
            {step === 1 && (
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3" role="list" aria-label="Request categories">
                    {CATEGORIES.map((cat) => {
                        const Icon = cat.icon;
                        return (
                            <button
                                key={cat.id}
                                role="listitem"
                                onClick={() => handleSelectCategory(cat)}
                                className={`p-4 rounded-lg border-2 border-transparent text-left space-y-2 transition-all cursor-pointer ${cat.bg} hover:border-primary/20 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-1`}
                                aria-label={`${cat.label}: ${cat.description}`}
                                data-testid={`category-${cat.id}`}
                            >
                                <Icon className={`h-6 w-6 ${cat.color}`} aria-hidden="true"/>
                                <p className="font-medium text-sm">{cat.label}</p>
                                <p className="text-xs text-muted-foreground leading-tight">{cat.description}</p>
                            </button>
                        );
                    })}
                </div>
            )}

            {/* Step 2: Form */}
            {step === 2 && selectedCategory && (
                <Card>
                    <CardHeader className="pb-2">
                        <div className="flex items-center gap-2">
                            <Button variant="ghost" size="sm" onClick={() => setStep(1)} className="h-7 px-2"
                                    aria-label="Go back to category selection">
                                <ChevronLeft className="h-4 w-4" aria-hidden="true"/>
                            </Button>
                            <CardTitle className="text-base">
                                {React.createElement(selectedCategory.icon, {
                                    className: `h-4 w-4 inline mr-1 ${selectedCategory.color}`,
                                    'aria-hidden': 'true'
                                })}
                                {selectedCategory.label}
                            </CardTitle>
                        </div>
                    </CardHeader>
                    <CardContent>
                        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
                            <div className="space-y-1">
                                <Label htmlFor="sr-subject">Subject <span aria-hidden="true">*</span><span
                                    className="sr-only">(required)</span></Label>
                                <Input
                                    id="sr-subject"
                                    required
                                    value={form.subject}
                                    onChange={e => setForm(f => ( {...f, subject: e.target.value} ))}
                                    placeholder="Brief summary of your request"
                                    aria-required="true"
                                    data-testid="smart-request-subject"
                                />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="sr-desc">Description <span aria-hidden="true">*</span><span
                                    className="sr-only">(required)</span></Label>
                                <Textarea
                                    id="sr-desc"
                                    required
                                    rows={5}
                                    value={form.description}
                                    onChange={e => setForm(f => ( {...f, description: e.target.value} ))}
                                    placeholder={
                                        selectedCategory.id === 'maintenance' ? 'Describe the issue, location, and any urgency...' :
                                            selectedCategory.id === 'levy_query' ? 'What would you like to know about your levies?' :
                                                selectedCategory.id === 'noise_complaint' ? 'Describe the noise, unit number if known, and times...' :
                                                    selectedCategory.id === 'bylaw_query' ? 'Which by-law are you asking about? Provide context...' :
                                                        selectedCategory.id === 'document_request' ? 'Which document(s) do you need and why?' :
                                                            'Please provide as much detail as possible...'
                                    }
                                    aria-required="true"
                                    data-testid="smart-request-description"
                                />
                            </div>
                            <div className="flex gap-2 justify-end">
                                <Button type="button" variant="outline" onClick={() => setStep(1)}>Back</Button>
                                <Button type="submit" disabled={submitting} data-testid="smart-request-submit">
                                    {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" aria-hidden="true"/>}
                                    {submitting ? 'Submitting…' : 'Submit Request'}
                                </Button>
                            </div>
                        </form>
                    </CardContent>
                </Card>
            )}

            {/* Step 3: Result — enriched confirmation */}
            {step === 3 && result && (
                <Card className={result.auto_resolved ? 'border-green-200' : 'border-blue-200'} role="status"
                      aria-live="polite">
                    <CardContent className="py-8 space-y-5">
                        <div className="text-center space-y-3">
                            {result.auto_resolved ? (
                                <>
                                    <CheckCircle2 className="h-12 w-12 text-green-500 mx-auto" aria-hidden="true"/>
                                    <div>
                                        <p className="font-bold text-lg text-green-700">Instantly Resolved!</p>
                                        <p className="text-sm text-muted-foreground mt-1">{result.message || 'Your query was answered automatically by our knowledge base.'}</p>
                                    </div>
                                </>
                            ) : (
                                <>
                                    <Clock className="h-12 w-12 text-blue-400 mx-auto" aria-hidden="true"/>
                                    <div>
                                        <p className="font-bold text-lg">Request Submitted</p>
                                        {result.reference && (
                                            <p className="text-sm text-muted-foreground mt-1">
                                                Reference: <span
                                                className="font-mono font-medium bg-slate-100 px-1.5 py-0.5 rounded">{result.reference}</span>
                                            </p>
                                        )}
                                    </div>
                                </>
                            )}
                        </div>

                        {/* What happens next — only for non-auto-resolved */}
                        {!result.auto_resolved && (
                            <div className="border border-blue-100 bg-blue-50 rounded-xl p-4 space-y-2">
                                <p className="text-xs font-bold uppercase tracking-wide text-blue-700">What happens
                                    next</p>
                                <ul className="text-xs text-blue-700 space-y-1.5 list-none">
                                    <li className="flex items-start gap-2">
                                        <UserCheck className="h-3.5 w-3.5 shrink-0 mt-0.5" aria-hidden="true"/>
                                        The <strong>strata management team</strong> will triage your request
                                    </li>
                                    <li className="flex items-start gap-2">
                                        <Timer className="h-3.5 w-3.5 shrink-0 mt-0.5" aria-hidden="true"/>
                                        You'll receive a response
                                        within <strong>{result.sla_hours || 48} hours</strong> (SLA)
                                    </li>
                                    <li className="flex items-start gap-2">
                                        <Mail className="h-3.5 w-3.5 shrink-0 mt-0.5" aria-hidden="true"/>
                                        Email notification will be sent when status changes
                                    </li>
                                </ul>
                            </div>
                        )}

                        <div className="flex flex-col sm:flex-row gap-3 justify-center">
                            <Button onClick={handleReset} variant="outline">Submit Another Request</Button>
                            {result.id && (
                                <Button asChild data-testid="track-status-btn">
                                    <a href={`/requests/${result.id}`}>Track Status</a>
                                </Button>
                            )}
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* My Requests */}
            <div className="space-y-3" role="region" aria-label="My submitted requests">
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">My Requests</h2>
                {loadingRequests ? (
                    <div className="flex justify-center py-6" aria-busy="true" aria-label="Loading requests">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden="true"/>
                    </div>
                ) : myRequests.length === 0 ? (
                    <div className="text-center py-8 border-2 border-dashed rounded-xl border-slate-100">
                        <MessageSquare className="h-8 w-8 text-slate-200 mx-auto mb-2" aria-hidden="true"/>
                        <p className="text-sm text-muted-foreground">No requests submitted yet.</p>
                        <p className="text-xs text-muted-foreground mt-1">Use a category above to get started.</p>
                    </div>
                ) : (
                    <div className="space-y-2">
                        {myRequests.map((req) => {
                            const overdue = isSlaOverdue(req);
                            return (
                                <Card
                                    key={req.id || req._id}
                                    className={`group hover:border-primary/30 transition-colors cursor-pointer ${overdue ? 'border-red-200 bg-red-50/30' : ''}`}
                                    onClick={() => openRequestDetail(req)}
                                    role="button"
                                    tabIndex={0}
                                    onKeyDown={e => {
                                        if (e.key === 'Enter' || e.key === ' ') openRequestDetail(req);
                                    }}
                                    aria-label={`Request: ${req.subject || req.title} — status: ${req.status}${overdue ? ' — SLA overdue' : ''}`}
                                    data-testid={`request-item-${req.id || req._id}`}
                                >
                                    <CardContent className="py-3 flex items-start justify-between gap-3">
                                        <div className="space-y-0.5 flex-1 min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span
                                                    className="font-mono text-[10px] text-muted-foreground">{req.request_number}</span>
                                                <p className="text-sm font-medium truncate">{req.subject || req.title}</p>
                                                {overdue && (
                                                    <Badge className="bg-red-100 text-red-700 text-[10px]"
                                                           aria-label="SLA overdue">
                                                        <AlertCircle className="h-2.5 w-2.5 mr-0.5" aria-hidden="true"/>
                                                        Overdue
                                                    </Badge>
                                                )}
                                            </div>
                                            <div className="flex items-center gap-2 flex-wrap">
                                                {( req.request_type || req.category ) && (
                                                    <Badge variant="outline"
                                                           className="text-xs capitalize">{( req.request_type || req.category ).replace(/_/g, ' ')}</Badge>
                                                )}
                                                {statusBadge(req.status)}
                                            </div>
                                        </div>
                                        <div className="flex flex-col items-end gap-1 shrink-0">
                                            {req.created_at && (
                                                <p className="text-xs text-muted-foreground whitespace-nowrap">
                                                    {new Date(req.created_at).toLocaleDateString('en-AU')}
                                                </p>
                                            )}
                                            {req.sla_due_at && !overdue && (
                                                <p className="text-[10px] text-muted-foreground whitespace-nowrap">
                                                    Due {new Date(req.sla_due_at).toLocaleDateString('en-AU')}
                                                </p>
                                            )}
                                        </div>
                                    </CardContent>
                                </Card>
                            );
                        })}
                    </div>
                )}
            </div>

            {/* Manager Queue Toggle */}
            {isManager && (
                <div className="border-t pt-4">
                    <button
                        className="flex items-center gap-2 text-sm font-semibold text-sky-700 hover:text-sky-900 transition-colors"
                        onClick={() => setManagerView(v => !v)}
                        aria-expanded={managerView}
                    >
                        <Users className="h-4 w-4" aria-hidden="true"/>
                        {managerView ? 'Hide' : 'Show'} Management Queue
                        <ChevronRight className={`h-4 w-4 transition-transform ${managerView ? 'rotate-90' : ''}`}
                                      aria-hidden="true"/>
                    </button>

                    {managerView && (
                        <div className="mt-4 space-y-4">
                            {/* Triage Stats */}
                            {triageStats && (
                                <div className="grid grid-cols-3 gap-3" role="region" aria-label="Triage statistics">
                                    <div className="rounded-lg bg-sky-50 border border-sky-100 p-3 text-center">
                                        <BarChart3 className="h-4 w-4 text-sky-500 mx-auto mb-1" aria-hidden="true"/>
                                        <p className="text-lg font-bold text-sky-800">{triageStats.auto_resolved_today}</p>
                                        <p className="text-[10px] text-sky-600">Auto-resolved today</p>
                                    </div>
                                    <div className="rounded-lg bg-green-50 border border-green-100 p-3 text-center">
                                        <CheckCircle2 className="h-4 w-4 text-green-500 mx-auto mb-1"
                                                      aria-hidden="true"/>
                                        <p className="text-lg font-bold text-green-800">{triageStats.deflection_rate_pct}%</p>
                                        <p className="text-[10px] text-green-600">Deflection rate (7 days)</p>
                                    </div>
                                    <div className="rounded-lg bg-slate-50 border border-slate-100 p-3 text-center">
                                        <MessageSquare className="h-4 w-4 text-slate-500 mx-auto mb-1"
                                                       aria-hidden="true"/>
                                        <p className="text-lg font-bold text-slate-800">{triageStats.total_received_week}</p>
                                        <p className="text-[10px] text-slate-600">Received this week</p>
                                    </div>
                                </div>
                            )}

                            {/* Queue Filters */}
                            <div className="flex items-center gap-2 flex-wrap">
                                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mr-1">Filter:</p>
                                {['open', 'in_progress', 'resolved', 'all'].map(f => (
                                    <button
                                        key={f}
                                        onClick={() => {
                                            setQueueFilter(f);
                                        }}
                                        className={`text-xs px-3 py-1 rounded-full border transition-colors ${queueFilter === f ? 'bg-primary text-primary-foreground border-primary' : 'bg-white border-slate-200 text-slate-600 hover:border-primary/40'}`}
                                    >
                                        {f === 'all' ? 'All' : f.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                                    </button>
                                ))}
                                <button
                                    onClick={loadAllRequests}
                                    className="ml-auto text-xs flex items-center gap-1 text-muted-foreground hover:text-primary transition-colors"
                                    disabled={loadingAll}
                                    aria-label="Refresh queue"
                                >
                                    <RefreshCw className={`h-3.5 w-3.5 ${loadingAll ? 'animate-spin' : ''}`}
                                               aria-hidden="true"/>
                                    Refresh
                                </button>
                            </div>

                            {/* Queue Table */}
                            {loadingAll ? (
                                <div className="flex justify-center py-6" aria-busy="true"><Loader2
                                    className="h-6 w-6 animate-spin text-muted-foreground"/></div>
                            ) : allRequests.length === 0 ? (
                                <div className="text-center py-8 border-2 border-dashed rounded-xl border-slate-100">
                                    <CheckCircle2 className="h-8 w-8 text-green-300 mx-auto mb-2"/>
                                    <p className="text-sm text-muted-foreground">No requests in this queue.</p>
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    {allRequests.map((req) => {
                                        const overdue = isSlaOverdue(req);
                                        return (
                                            <Card
                                                key={req.id || req._id}
                                                className={`transition-colors ${overdue ? 'border-red-200 bg-red-50/40' : ''}`}
                                            >
                                                <CardContent
                                                    className="py-3 flex flex-col sm:flex-row sm:items-center gap-3">
                                                    <div className="flex-1 min-w-0 space-y-0.5">
                                                        <div className="flex items-center gap-2 flex-wrap">
                                                            <span
                                                                className="font-mono text-[10px] text-muted-foreground">{req.request_number}</span>
                                                            <button
                                                                className="text-sm font-medium hover:text-primary transition-colors text-left truncate"
                                                                onClick={() => openRequestDetail(req)}
                                                            >
                                                                {req.subject || req.title}
                                                            </button>
                                                            {overdue && (
                                                                <Badge className="bg-red-100 text-red-700 text-[10px]">
                                                                    <AlertCircle className="h-2.5 w-2.5 mr-0.5"/>SLA
                                                                    Overdue
                                                                </Badge>
                                                            )}
                                                        </div>
                                                        <div className="flex items-center gap-2 flex-wrap">
                                                            {statusBadge(req.status)}
                                                            {( req.request_type || req.category ) && (
                                                                <Badge variant="outline"
                                                                       className="text-xs capitalize">{( req.request_type || req.category ).replace(/_/g, ' ')}</Badge>
                                                            )}
                                                            <span className="text-[10px] text-muted-foreground">
                                Unit {req.unit_number || '—'} · {req.created_at ? new Date(req.created_at).toLocaleDateString('en-AU') : '—'}
                              </span>
                                                            {req.assigned_to_name && (
                                                                <span
                                                                    className="text-[10px] text-sky-600 flex items-center gap-0.5">
                                  <UserCog className="h-3 w-3"/>
                                                                    {req.assigned_to_name}
                                </span>
                                                            )}
                                                        </div>
                                                    </div>
                                                    <div className="shrink-0">
                                                        <Select
                                                            value={req.status}
                                                            onValueChange={(v) => handleUpdateStatus(req.id || req._id, v)}
                                                            disabled={updatingStatus === ( req.id || req._id )}
                                                        >
                                                            <SelectTrigger className="w-32 h-7 text-xs">
                                                                <SelectValue/>
                                                            </SelectTrigger>
                                                            <SelectContent>
                                                                <SelectItem value="open">Open</SelectItem>
                                                                <SelectItem value="in_progress">In Progress</SelectItem>
                                                                <SelectItem value="resolved">Resolved</SelectItem>
                                                                <SelectItem value="closed">Closed</SelectItem>
                                                            </SelectContent>
                                                        </Select>
                                                    </div>
                                                </CardContent>
                                            </Card>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* Request Detail Modal */}
            {selectedRequest && (
                <Dialog open={requestDetailOpen} onOpenChange={setRequestDetailOpen}>
                    <DialogContent className="max-w-lg" aria-label="Request details">
                        <DialogHeader>
                            <DialogTitle className="flex items-center gap-2">
                                <MessageSquare className="h-5 w-5 text-sky-500" aria-hidden="true"/>
                                {selectedRequest.subject || selectedRequest.title}
                            </DialogTitle>
                            <DialogDescription>
                                {selectedRequest.request_number && (
                                    <span className="font-mono text-xs">Ref: {selectedRequest.request_number}</span>
                                )}
                            </DialogDescription>
                        </DialogHeader>
                        <div className="space-y-4">
                            {/* Status & Category */}
                            <div className="flex flex-wrap gap-2">
                                {statusBadge(selectedRequest.status)}
                                {( selectedRequest.request_type || selectedRequest.category ) && (
                                    <Badge variant="outline" className="capitalize">
                                        {( selectedRequest.request_type || selectedRequest.category ).replace(/_/g, ' ')}
                                    </Badge>
                                )}
                                {selectedRequest.sla_due_at && (
                                    <Badge variant="outline" className="text-[10px]">
                                        <Timer className="h-2.5 w-2.5 mr-0.5" aria-hidden="true"/>
                                        SLA: {new Date(selectedRequest.sla_due_at).toLocaleDateString('en-AU')}
                                    </Badge>
                                )}
                            </div>

                            {/* Description */}
                            {selectedRequest.body && (
                                <div>
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Description</p>
                                    <p className="text-sm text-slate-700 bg-slate-50 rounded-lg p-3">{selectedRequest.body}</p>
                                </div>
                            )}

                            {/* Auto-resolution response */}
                            {selectedRequest.auto_resolution_response && (
                                <div className="bg-teal-50 border border-teal-200 rounded-lg p-3">
                                    <p className="text-xs font-semibold text-teal-700 uppercase tracking-wide mb-1">Auto-Resolution</p>
                                    <p className="text-sm text-teal-800">{selectedRequest.auto_resolution_response}</p>
                                </div>
                            )}

                            {/* Workflow Timeline */}
                            <div>
                                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Workflow
                                    Timeline</p>
                                {loadingTimeline ? (
                                    <div className="flex justify-center py-4" aria-busy="true"><Loader2
                                        className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden="true"/>
                                    </div>
                                ) : requestTimeline.length > 0 ? (
                                    <div className="space-y-2" role="list" aria-label="Timeline events">
                                        {requestTimeline.map((entry, i) => (
                                            <div key={i} className="flex gap-3 items-start" role="listitem">
                                                <div className={`mt-1 w-2.5 h-2.5 rounded-full shrink-0 ${
                                                    entry.status === 'resolved' || entry.status === 'auto_resolved' ? 'bg-green-500' :
                                                        entry.status === 'in_progress' ? 'bg-yellow-500' :
                                                            entry.status === 'closed' ? 'bg-gray-400' : 'bg-blue-500'
                                                }`} aria-hidden="true"/>
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span
                                                            className="text-xs font-semibold capitalize">{( entry.status || entry.action || '' ).replace(/_/g, ' ')}</span>
                                                        {entry.timestamp && (
                                                            <span
                                                                className="text-[10px] text-muted-foreground whitespace-nowrap">
                                {new Date(entry.timestamp).toLocaleDateString('en-AU')}
                              </span>
                                                        )}
                                                    </div>
                                                    {entry.notes &&
                                                        <p className="text-xs text-muted-foreground mt-0.5">{entry.notes}</p>}
                                                    {entry.actor_name &&
                                                        <p className="text-[10px] text-muted-foreground">by {entry.actor_name}</p>}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="text-center py-4">
                                        <Clock className="h-8 w-8 text-muted-foreground/40 mx-auto mb-2"
                                               aria-hidden="true"/>
                                        <p className="text-xs text-muted-foreground">No updates yet</p>
                                        <p className="text-xs text-muted-foreground mt-1">
                                            Submitted {selectedRequest.created_at ? new Date(selectedRequest.created_at).toLocaleDateString('en-AU') : '—'}
                                        </p>
                                    </div>
                                )}
                            </div>

                            {/* Workflow stages guide — with tooltips */}
                            <div className="bg-slate-50 rounded-lg p-3">
                                <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-2">Workflow
                                    Stages</p>
                                <div className="flex items-center gap-1 flex-wrap" role="list"
                                     aria-label="Workflow stages">
                                    {['Open', 'In Review', 'In Progress', 'Resolved', 'Closed'].map((stage, i, arr) => {
                                        const isActive =
                                            selectedRequest.status === stage.toLowerCase().replace(' ', '_') ||
                                            ( selectedRequest.status === 'in_progress' && stage === 'In Progress' ) ||
                                            ( selectedRequest.status === 'open' && stage === 'Open' ) ||
                                            ( selectedRequest.status === 'resolved' && stage === 'Resolved' ) ||
                                            ( selectedRequest.status === 'closed' && stage === 'Closed' );
                                        return (
                                            <React.Fragment key={stage}>
                        <span
                            role="listitem"
                            className={`text-[10px] px-2 py-0.5 rounded-full font-medium cursor-help ${isActive ? 'bg-primary text-primary-foreground' : 'bg-white border text-slate-500'}`}
                            title={STAGE_INFO[ stage ]}
                            aria-label={`${stage}${isActive ? ' (current)' : ''}: ${STAGE_INFO[ stage ]}`}
                        >
                          {stage}
                        </span>
                                                {i < arr.length - 1 && <ChevronRight className="h-3 w-3 text-slate-300"
                                                                                     aria-hidden="true"/>}
                                            </React.Fragment>
                                        );
                                    })}
                                </div>
                                <p className="text-[10px] text-slate-400 mt-2 italic">Hover over a stage to see what it
                                    means.</p>
                            </div>
                        </div>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    );
}
