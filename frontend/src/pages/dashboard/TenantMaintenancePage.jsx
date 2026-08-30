// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import {
    ChevronDown,
    ChevronUp,
    ImageIcon,
    Loader2,
    MessageSquare,
    Plus,
    RefreshCw,
    Send,
    User,
    Wrench
} from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { formatDate } from '../../lib/utils';

const STATUS_CONFIG = {
    submitted: {label: 'Submitted', color: 'bg-blue-100 text-blue-800', step: 0},
    acknowledged: {label: 'Acknowledged', color: 'bg-yellow-100 text-yellow-800', step: 1},
    assigned: {label: 'Assigned', color: 'bg-purple-100 text-purple-800', step: 2},
    in_progress: {label: 'In Progress', color: 'bg-orange-100 text-orange-800', step: 3},
    completed: {label: 'Completed', color: 'bg-green-100 text-green-800', step: 4},
    rejected: {label: 'Rejected', color: 'bg-red-100 text-red-800', step: -1},
};

const STATUS_STEPS = ['submitted', 'acknowledged', 'assigned', 'in_progress', 'completed'];

const URGENCY_CONFIG = {
    routine: {label: 'Routine', color: 'bg-gray-100 text-gray-800'},
    urgent: {label: 'Urgent', color: 'bg-orange-100 text-orange-800'},
    emergency: {label: 'Emergency', color: 'bg-red-100 text-red-800'},
};

const ALLOWED_ROLES_ALL = ['admin_staff', 'strata_manager', 'super_admin', 'ec_member'];
/**
 * @generated FunctionHeader
 * Function: StatusStepper
 * Path: frontend/src/pages/dashboard/TenantMaintenancePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatusStepper({status}) {
    const currentStep = STATUS_CONFIG[ status ]?.step ?? 0;
    return (
        <div className="flex items-center gap-0.5 flex-wrap">
            {STATUS_STEPS.map((step, i) => {
                const conf = STATUS_CONFIG[ step ];
                const done = currentStep > i;
                const active = currentStep === i;
                return (
                    <React.Fragment key={step}>
                        <div className={`flex flex-col items-center`}>
                            <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold border-2 transition-colors
                ${done ? 'bg-green-500 border-green-500 text-white'
                                : active ? 'bg-blue-500 border-blue-500 text-white'
                                    : 'bg-white border-gray-300 text-gray-400'}`}>
                                {done ? '✓' : i + 1}
                            </div>
                            <span className={`text-[10px] mt-0.5 text-center leading-tight
                ${active ? 'text-blue-600 font-medium' : done ? 'text-green-600' : 'text-gray-400'}`}>
                {conf?.label.split(' ')[ 0 ]}
              </span>
                        </div>
                        {i < STATUS_STEPS.length - 1 && (
                            <div
                                className={`h-0.5 flex-1 min-w-[12px] transition-colors ${done ? 'bg-green-400' : 'bg-gray-200'}`}/>
                        )}
                    </React.Fragment>
                );
            })}
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: TenantMaintenancePage
 * Path: frontend/src/pages/dashboard/TenantMaintenancePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const TenantMaintenancePage = () => {
    const {user, api} = useAuth();
    const router = useRouter();
    const [requests, setRequests] = useState([]);
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [newOpen, setNewOpen] = useState(false);
    const [activeTab, setActiveTab] = useState('all');
    const [expandedId, setExpandedId] = useState(null);
    const [messages, setMessages] = useState({});
    const [replyText, setReplyText] = useState({});
    const [sendingReply, setSendingReply] = useState({});
    const fileRef = useRef(null);

    const [form, setForm] = useState({
        issue_title: '', issue_description: '', urgency: 'routine',
        preferred_access_times: '', photos: []
    });

    const fetchRequests = useCallback(async () => {
        try {
            const res = await api.get('/tenant/maintenance');
            setRequests(res.data || []);
        } catch {
            toast.error('Failed to load maintenance requests');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchRequests();
    }, [fetchRequests]);
    /**
     * @generated FunctionHeader
     * Function: fetchMessages
     * Path: frontend/src/pages/dashboard/TenantMaintenancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchMessages = async (requestId) => {
        try {
            const res = await api.get(`/tenant/maintenance/${requestId}/messages`);
            setMessages(prev => ( {...prev, [ requestId ]: res.data || []} ));
        } catch {
            // silent
        }
    };
    /**
     * @generated FunctionHeader
     * Function: toggleExpand
     * Path: frontend/src/pages/dashboard/TenantMaintenancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleExpand = (id) => {
        if (expandedId === id) {
            setExpandedId(null);
        } else {
            setExpandedId(id);
            fetchMessages(id);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/TenantMaintenancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            const formData = new FormData();
            Object.entries(form).forEach(([k, v]) => {
                if (k === 'photos') {
                    v.forEach(f => formData.append('photos', f));
                } else {
                    formData.append(k, v);
                }
            });
            await api.post('/tenant/maintenance', formData, {
                headers: {'Content-Type': 'multipart/form-data'}
            });
            toast.success('Maintenance request submitted');
            setNewOpen(false);
            setForm({
                issue_title: '',
                issue_description: '',
                urgency: 'routine',
                preferred_access_times: '',
                photos: []
            });
            fetchRequests();
        } catch {
            toast.error('Failed to submit request');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSendReply
     * Path: frontend/src/pages/dashboard/TenantMaintenancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSendReply = async (requestId) => {
        const text = replyText[ requestId ]?.trim();
        if (!text) return;
        setSendingReply(prev => ( {...prev, [ requestId ]: true} ));
        try {
            await api.post(`/tenant/maintenance/${requestId}/messages`, {content: text});
            setReplyText(prev => ( {...prev, [ requestId ]: ''} ));
            fetchMessages(requestId);
        } catch {
            toast.error('Failed to send message');
        } finally {
            setSendingReply(prev => ( {...prev, [ requestId ]: false} ));
        }
    };

    const filteredRequests = activeTab === 'all'
        ? requests
        : requests.filter(r => r.status === activeTab);

    const counts = STATUS_STEPS.reduce((acc, s) => {
        acc[ s ] = requests.filter(r => r.status === s).length;
        return acc;
    }, {});

    return (
        <div className="space-y-6 p-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Maintenance Requests</h1>
                    <p className="text-sm text-gray-500">Submit and track maintenance issues</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchRequests}>
                        <RefreshCw className="h-4 w-4 mr-2"/>Refresh
                    </Button>
                    <Button size="sm" onClick={() => setNewOpen(true)}>
                        <Plus className="h-4 w-4 mr-2"/>Submit Request
                    </Button>
                </div>
            </div>

            {/* Status filter tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList className="flex-wrap h-auto gap-1">
                    <TabsTrigger value="all">All ({requests.length})</TabsTrigger>
                    {STATUS_STEPS.map(s => (
                        <TabsTrigger key={s} value={s} className="capitalize">
                            {STATUS_CONFIG[ s ]?.label} {counts[ s ] > 0 ? `(${counts[ s ]})` : ''}
                        </TabsTrigger>
                    ))}
                </TabsList>

                <TabsContent value={activeTab} className="mt-4">
                    {loading ? (
                        <div className="flex justify-center py-16"><Loader2
                            className="h-8 w-8 animate-spin text-gray-400"/></div>
                    ) : filteredRequests.length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center text-gray-400">
                                <Wrench className="mx-auto h-12 w-12 text-gray-200 mb-3"/>
                                No maintenance requests found.
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="space-y-3">
                            {filteredRequests.map(req => {
                                const sc = STATUS_CONFIG[ req.status ] || STATUS_CONFIG.submitted;
                                const uc = URGENCY_CONFIG[ req.urgency ] || URGENCY_CONFIG.routine;
                                const isExpanded = expandedId === req.id;
                                const msgs = messages[ req.id ] || [];
                                return (
                                    <Card key={req.id}
                                          className={`transition-shadow ${req.urgency === 'emergency' ? 'border-red-300' : ''}`}>
                                        <CardContent className="pt-4 pb-3">
                                            {/* Header row */}
                                            <div className="flex items-start gap-3">
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center gap-2 flex-wrap">
                                                        <span
                                                            className="font-semibold text-gray-900 truncate">{req.issue_title}</span>
                                                        <span
                                                            className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${sc.color}`}>{sc.label}</span>
                                                        <span
                                                            className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${uc.color}`}>{uc.label}</span>
                                                    </div>
                                                    <p className="text-sm text-gray-500 mt-0.5">{req.property_address || req.unit_number} · {formatDate(req.created_at)}</p>
                                                    {/* Stepper */}
                                                    <div className="mt-3 max-w-sm">
                                                        <StatusStepper status={req.status}/>
                                                    </div>
                                                </div>
                                                <Button variant="ghost" size="sm" onClick={() => toggleExpand(req.id)}
                                                        className="shrink-0">
                                                    {isExpanded ? <ChevronUp className="h-4 w-4"/> :
                                                        <ChevronDown className="h-4 w-4"/>}
                                                </Button>
                                            </div>

                                            {/* Expanded detail panel */}
                                            {isExpanded && (
                                                <div className="mt-4 border-t pt-4 space-y-4">
                                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                                                        <div>
                                                            <p className="text-gray-400 text-xs">Description</p>
                                                            <p className="text-gray-700">{req.issue_description || '—'}</p>
                                                        </div>
                                                        {req.assignee_name && (
                                                            <div>
                                                                <p className="text-gray-400 text-xs">Assigned To</p>
                                                                <p className="text-gray-700 font-medium">{req.assignee_name}</p>
                                                                {req.assignee_phone &&
                                                                    <p className="text-xs text-gray-500">{req.assignee_phone}</p>}
                                                            </div>
                                                        )}
                                                        {req.scheduled_date && (
                                                            <div>
                                                                <p className="text-gray-400 text-xs">Scheduled</p>
                                                                <p className="text-gray-700">{formatDate(req.scheduled_date)}</p>
                                                            </div>
                                                        )}
                                                        {req.cost_estimate != null && (
                                                            <div>
                                                                <p className="text-gray-400 text-xs">Cost Estimate</p>
                                                                <p className="text-gray-700">${req.cost_estimate}</p>
                                                            </div>
                                                        )}
                                                    </div>

                                                    {/* Messages */}
                                                    <div>
                                                        <p className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1">
                                                            <MessageSquare className="h-3.5 w-3.5"/>Messages
                                                            ({msgs.length})
                                                        </p>
                                                        <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                                                            {msgs.length === 0 ? (
                                                                <p className="text-xs text-gray-400">No messages
                                                                    yet.</p>
                                                            ) : msgs.map((msg, i) => (
                                                                <div key={i}
                                                                     className={`flex gap-2 ${msg.sender_id === user?.id ? 'flex-row-reverse' : ''}`}>
                                                                    <div
                                                                        className="w-6 h-6 rounded-full bg-gray-200 flex items-center justify-center shrink-0">
                                                                        <User className="h-3 w-3 text-gray-500"/>
                                                                    </div>
                                                                    <div
                                                                        className={`max-w-xs rounded-lg px-3 py-2 text-sm ${msg.sender_id === user?.id ? 'bg-blue-100 text-blue-900' : 'bg-gray-100 text-gray-800'}`}>
                                                                        <p className="font-medium text-xs opacity-70 mb-0.5">{msg.sender_name} · {formatDate(msg.created_at)}</p>
                                                                        <p>{msg.content}</p>
                                                                    </div>
                                                                </div>
                                                            ))}
                                                        </div>
                                                        {/* Reply box */}
                                                        <div className="flex gap-2 mt-3">
                                                            <Input
                                                                placeholder="Type a message…"
                                                                value={replyText[ req.id ] || ''}
                                                                onChange={e => setReplyText(prev => ( {
                                                                    ...prev,
                                                                    [ req.id ]: e.target.value
                                                                } ))}
                                                                onKeyDown={e => e.key === 'Enter' && handleSendReply(req.id)}
                                                                className="h-8 text-sm"
                                                            />
                                                            <Button size="sm" className="h-8 px-3"
                                                                    onClick={() => handleSendReply(req.id)}
                                                                    disabled={sendingReply[ req.id ]}>
                                                                {sendingReply[ req.id ] ?
                                                                    <Loader2 className="h-3 w-3 animate-spin"/> :
                                                                    <Send className="h-3 w-3"/>}
                                                            </Button>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </CardContent>
                                    </Card>
                                );
                            })}
                        </div>
                    )}
                </TabsContent>
            </Tabs>

            {/* Submit Request Dialog */}
            <Dialog open={newOpen} onOpenChange={setNewOpen}>
                <DialogContent className="max-w-md max-h-[90vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Submit Maintenance Request</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div className="space-y-2">
                            <Label>Issue Title *</Label>
                            <Input value={form.issue_title}
                                   onChange={e => setForm(p => ( {...p, issue_title: e.target.value} ))} required
                                   placeholder="e.g. Leaking tap in kitchen"/>
                        </div>
                        <div className="space-y-2">
                            <Label>Description</Label>
                            <Textarea value={form.issue_description}
                                      onChange={e => setForm(p => ( {...p, issue_description: e.target.value} ))}
                                      rows={4} placeholder="Describe the issue in detail…"/>
                        </div>
                        <div className="space-y-2">
                            <Label>Urgency</Label>
                            <div className="flex gap-2">
                                {Object.entries(URGENCY_CONFIG).map(([key, cfg]) => (
                                    <button
                                        type="button" key={key}
                                        onClick={() => setForm(p => ( {...p, urgency: key} ))}
                                        className={`flex-1 rounded-lg border px-3 py-2 text-sm font-medium transition-all
                      ${form.urgency === key ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-200 text-gray-600 hover:border-gray-300'}`}>
                                        {cfg.label}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label>Preferred Access Times</Label>
                            <Input value={form.preferred_access_times}
                                   onChange={e => setForm(p => ( {...p, preferred_access_times: e.target.value} ))}
                                   placeholder="e.g. Weekday mornings 9am–12pm"/>
                        </div>
                        <div className="space-y-2">
                            <Label className="flex items-center gap-2">
                                <ImageIcon className="h-4 w-4"/>Photos (optional)
                            </Label>
                            <Input ref={fileRef} type="file" accept="image/*" multiple
                                   onChange={e => setForm(p => ( {...p, photos: Array.from(e.target.files || [])} ))}/>
                            {form.photos.length > 0 &&
                                <p className="text-xs text-gray-500">{form.photos.length} file(s) selected</p>}
                        </div>
                        <DialogFooter>
                            <Button type="submit" className="w-full" disabled={submitting}>
                                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}Submit Request
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default TenantMaintenancePage;
