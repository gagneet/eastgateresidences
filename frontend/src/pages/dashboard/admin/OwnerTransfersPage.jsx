// @featuretrace:owner-transfers — Owner transfer request lodging, approval workflow, and History tab.
// Layer: frontend
// Data flow: OwnerTransfersPage → GET/POST/PATCH/PUT /owner-transfers* → owner_transfer_requests, ownership_transfer_log, user_units, users (building-scoped + global).
// Related: backend/server.py #owner-transfers-section (lines ~9011-9980)
// Scope: (building-scoped)
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useAuth } from '@/contexts/AuthContext';
import { toast } from 'sonner';
import {
    AlertCircle,
    ArrowRight,
    CheckCircle,
    Clock,
    Database,
    FileText,
    Home,
    Pencil,
    Plus,
    RefreshCw,
    Search,
    ShieldCheck,
    User,
    XCircle
} from 'lucide-react';

// 'chairman' is NOT a top-level user.role value (see rules/post-compact-critical.md) — a chairman
// is a user with role 'ec_member' and ec_position 'CHAIRMAN', so 'ec_member' alone already covers them.
const REVIEWER_ROLES = ['super_admin', 'strata_manager', 'real_estate_agent', 'ec_member'];
const EC_ROLES = ['ec_member'];
const PENDING_STATUSES = ['pending', 'pending_second_approval'];
/**
 * @generated FunctionHeader
 * Function: createEmptyForm
 * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const createEmptyForm = (unitNumber = '') => ( {
    unit_number: unitNumber,
    new_owner_email: '',
    settlement_date: '',
    request_notes: '',
    ownership_documents_text: ''
} );
/**
 * @generated FunctionHeader
 * Function: parseOwnershipDocuments
 * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const parseOwnershipDocuments = (text) => {
    if (!text.trim()) return [];
    return text
        .split(/\n|,/)
        .map(item => item.trim())
        .filter(Boolean);
};
/**
 * @generated FunctionHeader
 * Function: displayOwnerEmail
 * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const displayOwnerEmail = (owner) => {
    if (!owner?.email) return 'Email not recorded';
    if (owner.is_internal_contact_email || owner.email.endsWith('@strataos.local')) {
        return 'Contact email pending';
    }
    return owner.email;
};
/**
 * @generated FunctionHeader
 * Function: OwnerTransfersPage
 * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function OwnerTransfersPage() {
    const {api, user, hasPermission} = useAuth();
    const [transfers, setTransfers] = useState([]);
    const [history, setHistory] = useState([]);
    const [loading, setLoading] = useState(true);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [selectedTransfer, setSelectedTransfer] = useState(null);
    const [reviewDialog, setReviewDialog] = useState(false);
    const [reviewAction, setReviewAction] = useState('');
    const [reviewNotes, setReviewNotes] = useState('');
    const [selectedOwnersToRemove, setSelectedOwnersToRemove] = useState([]);
    const [processing, setProcessing] = useState(false);
    const [historySearch, setHistorySearch] = useState('');
    const [formDialogOpen, setFormDialogOpen] = useState(false);
    const [editingTransfer, setEditingTransfer] = useState(null);
    const [formState, setFormState] = useState(createEmptyForm());
    const [formSubmitting, setFormSubmitting] = useState(false);
    const [formOptions, setFormOptions] = useState({
        can_create: false,
        can_review: false,
        can_view_history: false,
        accessible_units: []
    });

    const canReview = useMemo(() => {
        return formOptions.can_review || (
            hasPermission('can_manage_requests') &&
            REVIEWER_ROLES.includes(user?.role || '')
        );
    }, [formOptions.can_review, hasPermission, user?.role]);

    const canViewHistory = formOptions.can_view_history || canReview;
    const canCreate = formOptions.can_create;
    const isEcReviewer = EC_ROLES.includes(user?.role || '');

    const fetchFormOptions = useCallback(async () => {
        try {
            const response = await api.get('/owner-transfers/form-options');
            setFormOptions(response.data || {
                can_create: false,
                can_review: false,
                can_view_history: false,
                accessible_units: []
            });
        } catch (error) {
            console.error('Failed to fetch owner transfer options:', error);
            toast.error('Failed to load owner transfer options');
        }
    }, [api]);

    const fetchTransfers = useCallback(async (status = null) => {
        try {
            setLoading(true);
            const endpoint = status ? `/owner-transfers?status=${status}` : '/owner-transfers';
            const response = await api.get(endpoint);
            setTransfers(Array.isArray(response.data) ? response.data : []);
        } catch (error) {
            console.error('Failed to fetch transfers:', error);
            toast.error('Failed to load transfer requests');
        } finally {
            setLoading(false);
        }
    }, [api]);

    const fetchHistory = useCallback(async () => {
        if (!canViewHistory) {
            setHistory([]);
            return;
        }

        try {
            setHistoryLoading(true);
            const response = await api.get('/owner-transfers/history');
            setHistory(Array.isArray(response.data) ? response.data : []);
        } catch (error) {
            console.error('Failed to fetch transfer history:', error);
            toast.error('Failed to load transfer history');
        } finally {
            setHistoryLoading(false);
        }
    }, [api, canViewHistory]);

    useEffect(() => {
        fetchFormOptions();
        fetchTransfers();
    }, [fetchFormOptions, fetchTransfers]);

    useEffect(() => {
        fetchHistory();
    }, [fetchHistory]);

    const resetFormState = useCallback((unitNumber = '') => {
        setFormState(createEmptyForm(unitNumber));
        setEditingTransfer(null);
    }, []);

    const openCreateDialog = useCallback(() => {
        const defaultUnit = formOptions.accessible_units?.[ 0 ]?.unit_number || '';
        resetFormState(defaultUnit);
        setFormDialogOpen(true);
    }, [formOptions.accessible_units, resetFormState]);

    const openEditDialog = useCallback((transfer) => {
        setEditingTransfer(transfer);
        setFormState({
            unit_number: transfer.unit_number || '',
            new_owner_email: transfer.new_owner?.email || '',
            settlement_date: transfer.settlement_date || '',
            request_notes: transfer.request_notes || '',
            ownership_documents_text: ( transfer.ownership_documents || [] ).join('\n')
        });
        setFormDialogOpen(true);
    }, []);
    /**
     * @generated FunctionHeader
     * Function: openReviewDialog
     * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openReviewDialog = (transfer, action) => {
        setSelectedTransfer(transfer);
        setReviewAction(action);
        setReviewNotes('');
        setSelectedOwnersToRemove(
            action === 'approve_remove_old' && Array.isArray(transfer.suggested_remove_owner_ids)
                ? transfer.suggested_remove_owner_ids
                : []
        );
        setReviewDialog(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleReview
     * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReview = async () => {
        if (!selectedTransfer || !reviewAction) return;

        if (reviewAction === 'reject' && !reviewNotes.trim()) {
            toast.error('Please provide a reason for rejection');
            return;
        }

        try {
            setProcessing(true);

            const payload = {
                action: reviewAction,
                review_notes: reviewNotes.trim() || null,
                remove_owner_ids: selectedOwnersToRemove
            };

            const response = await api.put(`/owner-transfers/${selectedTransfer.id}`, payload);
            const status = response.data?.status;

            if (status === 'pending_second_approval') {
                toast.success('First EC approval recorded. A second EC approval is still required.');
            } else if (reviewAction === 'reject') {
                toast.success('Transfer request rejected');
            } else {
                toast.success('Transfer approved and processed');
            }

            setReviewDialog(false);
            fetchTransfers();
            fetchHistory();
        } catch (error) {
            console.error('Failed to process transfer:', error);
            toast.error(error.response?.data?.detail || 'Failed to process transfer');
        } finally {
            setProcessing(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmitTransfer
     * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmitTransfer = async () => {
        if (!formState.unit_number) {
            toast.error('Please select a unit');
            return;
        }

        if (!formState.new_owner_email.trim()) {
            toast.error('Please enter the incoming owner email address');
            return;
        }

        const payload = {
            unit_number: formState.unit_number,
            new_owner_email: formState.new_owner_email.trim(),
            settlement_date: formState.settlement_date || null,
            request_notes: formState.request_notes.trim() || null,
            ownership_documents: parseOwnershipDocuments(formState.ownership_documents_text)
        };

        try {
            setFormSubmitting(true);

            if (editingTransfer) {
                await api.patch(`/owner-transfers/${editingTransfer.id}`, payload);
                toast.success('Owner transfer request updated');
            } else {
                await api.post('/owner-transfers', payload);
                toast.success('Owner transfer request created');
            }

            setFormDialogOpen(false);
            resetFormState(formOptions.accessible_units?.[ 0 ]?.unit_number || '');
            fetchTransfers();
        } catch (error) {
            console.error('Failed to save transfer request:', error);
            toast.error(error.response?.data?.detail || 'Failed to save transfer request');
        } finally {
            setFormSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: toggleOwnerRemoval
     * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleOwnerRemoval = (ownerId) => {
        setSelectedOwnersToRemove(prev =>
            prev.includes(ownerId)
                ? prev.filter(id => id !== ownerId)
                : [...prev, ownerId]
        );
    };
    /**
     * @generated FunctionHeader
     * Function: getStatusBadge
     * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusBadge = (status) => {
        const labels = {
            pending: {className: 'bg-yellow-100 text-yellow-800', label: 'PENDING'},
            pending_second_approval: {className: 'bg-blue-100 text-blue-800', label: 'IN REVIEW'},
            approved: {className: 'bg-green-100 text-green-800', label: 'APPROVED'},
            rejected: {className: 'bg-red-100 text-red-800', label: 'REJECTED'},
            // Raised in error and retracted without a human decision — e.g. a joint-owner
            // addition that was mis-detected as a transfer. Retained (never deleted) for
            // the 7-year record, but out of the pending review queue.
            withdrawn: {className: 'bg-slate-100 text-slate-700', label: 'WITHDRAWN'}
        };
        const config = labels[ status ] || {
            className: 'bg-gray-100 text-gray-800',
            label: status?.toUpperCase() || 'UNKNOWN'
        };
        return <Badge className={config.className}>{config.label}</Badge>;
    };

    const canEditTransfer = useCallback((transfer) => {
        return transfer.status === 'pending' &&
            ( transfer.current_approvals || 0 ) === 0 &&
            ( transfer.submitted_by_id === user?.id || canReview );
    }, [canReview, user?.id]);

    const canReviewTransfer = useCallback((transfer) => {
        return canReview &&
            transfer.submitted_by_id !== user?.id &&
            PENDING_STATUSES.includes(transfer.status);
    }, [canReview, user?.id]);

    const filteredHistory = useMemo(() => {
        return history.filter(h => {
            if (!historySearch) return true;
            const q = historySearch.toLowerCase();
            return (
                h.unit_number?.toLowerCase().includes(q) ||
                h.previous_owner_name?.toLowerCase().includes(q) ||
                h.new_owner_name?.toLowerCase().includes(q)
            );
        });
    }, [history, historySearch]);

    const pendingTransfers = useMemo(
        () => transfers.filter(t => PENDING_STATUSES.includes(t.status)),
        [transfers]
    );
    /**
     * @generated FunctionHeader
     * Function: formatPrice
     * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const formatPrice = (price) =>
        price ? `$${Number(price).toLocaleString('en-AU')}` : ' - ';
    /**
     * @generated FunctionHeader
     * Function: TransferCard
     * Path: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const TransferCard = ({transfer}) => {
        const approvalProgress = transfer.required_approvals > 1
            ? `${transfer.current_approvals || 0} / ${transfer.required_approvals} approvals`
            : null;
        const approvalHistory = Array.isArray(transfer.approval_history) ? transfer.approval_history : [];
        const restrictEcAction = isEcReviewer &&
            transfer.status === 'pending_second_approval' &&
            transfer.approval_mode === 'ec_dual';
        const allowKeepOld = !restrictEcAction || transfer.pending_approval_action === 'approve_keep_old';
        const allowRemoveOld = !restrictEcAction || transfer.pending_approval_action === 'approve_remove_old';

        return (
            <Card className="mb-4">
                <CardHeader>
                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <Home className="h-5 w-5"/>
                                Unit {transfer.unit_number}
                            </CardTitle>
                            <CardDescription>
                                Requested {new Date(transfer.requested_date).toLocaleDateString()}
                                {transfer.submitted_by_name ? ` by ${transfer.submitted_by_name}` : ''}
                            </CardDescription>
                        </div>
                        <div className="flex flex-col items-start gap-2 md:items-end">
                            {getStatusBadge(transfer.status)}
                            {approvalProgress && (
                                <Badge variant="outline" className="text-xs">
                                    <ShieldCheck className="mr-1 h-3.5 w-3.5"/>
                                    {approvalProgress}
                                </Badge>
                            )}
                        </div>
                    </div>
                </CardHeader>

                <CardContent>
                    <div className="space-y-4">
                        {transfer.settlement_date && (
                            <div className="rounded-lg border bg-muted/20 p-3 text-sm">
                                <span className="font-semibold">Settlement date:</span>{' '}
                                {new Date(transfer.settlement_date).toLocaleDateString()}
                            </div>
                        )}

                        {transfer.request_notes && (
                            <div className="rounded-lg border bg-muted/20 p-3 text-sm">
                                <span className="font-semibold">Request notes:</span>{' '}
                                {transfer.request_notes}
                            </div>
                        )}

                        {transfer.status === 'pending_second_approval' && (
                            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
                                A first EC approval has been recorded. One more EC approval is required before this
                                transfer can be processed.
                            </div>
                        )}

                        <div>
                            <Label className="text-sm font-semibold text-gray-700">Current Owners</Label>
                            <div className="mt-2 space-y-2">
                                {transfer.old_owners.map((owner) => (
                                    <div key={owner.user_id} className="flex items-center gap-2 rounded bg-gray-50 p-2">
                                        <User className="h-4 w-4 text-gray-500"/>
                                        <div>
                                            <div className="font-medium">{owner.full_name}</div>
                                            <div className="text-sm text-gray-500">{displayOwnerEmail(owner)}</div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        <div className="flex justify-center">
                            <ArrowRight className="h-6 w-6 text-gray-400"/>
                        </div>

                        <div>
                            <Label className="text-sm font-semibold text-gray-700">Incoming Owner</Label>
                            <div className="mt-2 rounded bg-blue-50 p-3">
                                <div className="flex items-center gap-2">
                                    <User className="h-4 w-4 text-blue-500"/>
                                    <div>
                                        <div className="font-medium text-blue-900">{transfer.new_owner?.full_name}</div>
                                        <div className="text-sm text-blue-700">{displayOwnerEmail(transfer.new_owner)}</div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {transfer.ownership_documents?.length > 0 && (
                            <div>
                                <Label className="text-sm font-semibold text-gray-700">Supporting Documents</Label>
                                <div className="mt-2 space-y-2">
                                    {transfer.ownership_documents.map((documentRef) => (
                                        <div key={documentRef}
                                             className="flex items-center gap-2 text-sm text-gray-600">
                                            <FileText className="h-4 w-4"/>
                                            <span>{documentRef}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {approvalHistory.length > 0 && (
                            <div className="pt-4 border-t">
                                <Label className="text-sm font-semibold text-gray-700">Review Activity</Label>
                                <div className="mt-2 space-y-2">
                                    {approvalHistory.map((entry) => (
                                        <div key={`${entry.user_id}-${entry.decided_at}`}
                                             className="rounded border bg-muted/20 p-3 text-sm">
                                            <div className="font-medium">
                                                {entry.full_name} ({entry.role?.replaceAll('_', ' ')})
                                            </div>
                                            <div className="text-gray-500">
                                                {new Date(entry.decided_at).toLocaleString()}
                                            </div>
                                            <div className="mt-1 capitalize">
                                                {entry.action?.replaceAll('_', ' ')}
                                            </div>
                                            {entry.review_notes && (
                                                <div className="mt-1 text-gray-600">{entry.review_notes}</div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {transfer.status !== 'pending' && transfer.status !== 'pending_second_approval' && (
                            <div className="pt-4 border-t text-sm space-y-1">
                                {transfer.reviewed_date && (
                                    <div><strong>Reviewed
                                        on:</strong> {new Date(transfer.reviewed_date).toLocaleDateString()}</div>
                                )}
                                {transfer.action_taken && (
                                    <div><strong>Final action:</strong> {transfer.action_taken.replaceAll('_', ' ')}
                                    </div>
                                )}
                                {transfer.review_notes && (
                                    <div><strong>Review notes:</strong> {transfer.review_notes}</div>
                                )}
                            </div>
                        )}

                        <div className="flex flex-wrap gap-2 pt-4 border-t">
                            {canEditTransfer(transfer) && (
                                <Button
                                    variant="outline"
                                    onClick={() => openEditDialog(transfer)}
                                >
                                    <Pencil className="mr-2 h-4 w-4"/>
                                    Edit Request
                                </Button>
                            )}

                            {canReviewTransfer(transfer) && allowKeepOld && (
                                <Button
                                    onClick={() => openReviewDialog(transfer, 'approve_keep_old')}
                                    className="flex-1 min-w-[220px]"
                                >
                                    <CheckCircle className="mr-2 h-4 w-4"/>
                                    Approve and Keep Existing Owners
                                </Button>
                            )}

                            {canReviewTransfer(transfer) && allowRemoveOld && (
                                <Button
                                    onClick={() => openReviewDialog(transfer, 'approve_remove_old')}
                                    className="flex-1 min-w-[220px]"
                                >
                                    <CheckCircle className="mr-2 h-4 w-4"/>
                                    Approve and Remove Existing Owners
                                </Button>
                            )}

                            {canReviewTransfer(transfer) && (
                                <Button
                                    onClick={() => openReviewDialog(transfer, 'reject')}
                                    variant="destructive"
                                >
                                    <XCircle className="mr-2 h-4 w-4"/>
                                    Reject
                                </Button>
                            )}
                        </div>
                    </div>
                </CardContent>
            </Card>
        );
    };

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <h1 className="text-3xl font-bold">Owner Transfers</h1>
                    <p className="mt-2 text-gray-600">
                        Lodge, track, and approve ownership transfer requests. Strata managers and real estate agents
                        can finalise a request, while EC approvals require two distinct EC approvers.
                    </p>
                </div>

                <div className="flex flex-wrap gap-2">
                    <Button variant="outline" onClick={() => {
                        fetchFormOptions();
                        fetchTransfers();
                        fetchHistory();
                    }}>
                        <RefreshCw className="mr-2 h-4 w-4"/>
                        Refresh
                    </Button>
                    {canCreate && (
                        <Button onClick={openCreateDialog}>
                            <Plus className="mr-2 h-4 w-4"/>
                            New Transfer Request
                        </Button>
                    )}
                </div>
            </div>

            <Card className="border-blue-200 bg-blue-50">
                <CardContent className="pt-6 text-sm text-blue-950 space-y-2">
                    <p><strong>Who can lodge a transfer:</strong> current owners for their own units, plus reviewer
                        roles acting on behalf of the building.</p>
                    <p><strong>Who can approve:</strong> super admin, strata manager, real estate agent, and
                        EC member (including the chairman, who holds the EC member role).</p>
                    <p><strong>Approval rule:</strong> strata manager / real estate / super admin approvals can complete
                        the transfer immediately; EC-driven approvals need two distinct EC approvals.</p>
                </CardContent>
            </Card>

            <Tabs defaultValue="pending">
                <TabsList className="flex flex-wrap h-auto">
                    <TabsTrigger value="pending">
                        <Clock className="mr-2 h-4 w-4"/>
                        Pending ({pendingTransfers.length})
                    </TabsTrigger>
                    <TabsTrigger value="all">All Requests ({transfers.length})</TabsTrigger>
                    <TabsTrigger value="approved">
                        <CheckCircle className="mr-2 h-4 w-4"/>
                        Approved
                    </TabsTrigger>
                    <TabsTrigger value="rejected">
                        <XCircle className="mr-2 h-4 w-4"/>
                        Rejected
                    </TabsTrigger>
                    {canViewHistory && (
                        <TabsTrigger value="history">
                            <Database className="mr-2 h-4 w-4"/>
                            History ({history.length})
                        </TabsTrigger>
                    )}
                </TabsList>

                <TabsContent value="all" className="mt-6">
                    {loading ? (
                        <div className="py-12 text-center">Loading...</div>
                    ) : transfers.length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <AlertCircle className="mx-auto mb-4 h-12 w-12 text-gray-400"/>
                                <p className="text-gray-600">No transfer requests found</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div>
                            {transfers.map(transfer => (
                                <TransferCard key={transfer.id} transfer={transfer}/>
                            ))}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="pending" className="mt-6">
                    {loading ? (
                        <div className="py-12 text-center">Loading...</div>
                    ) : pendingTransfers.length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <CheckCircle className="mx-auto mb-4 h-12 w-12 text-green-400"/>
                                <p className="text-gray-600">No pending transfer requests</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div>
                            {pendingTransfers.map(transfer => (
                                <TransferCard key={transfer.id} transfer={transfer}/>
                            ))}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="approved" className="mt-6">
                    {loading ? (
                        <div className="py-12 text-center">Loading...</div>
                    ) : transfers.filter(t => t.status === 'approved').length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <p className="text-gray-600">No approved transfers</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div>
                            {transfers.filter(t => t.status === 'approved').map(transfer => (
                                <TransferCard key={transfer.id} transfer={transfer}/>
                            ))}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="rejected" className="mt-6">
                    {loading ? (
                        <div className="py-12 text-center">Loading...</div>
                    ) : transfers.filter(t => t.status === 'rejected').length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <p className="text-gray-600">No rejected transfers</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div>
                            {transfers.filter(t => t.status === 'rejected').map(transfer => (
                                <TransferCard key={transfer.id} transfer={transfer}/>
                            ))}
                        </div>
                    )}
                </TabsContent>

                {canViewHistory && (
                    <TabsContent value="history" className="mt-6">
                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <Database className="h-5 w-5 text-blue-500"/>
                                    Suburb Transfer and Sales History
                                </CardTitle>
                                <CardDescription>
                                    Verified ownership transfer records with public sale prices.
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="mb-4 flex items-center gap-2">
                                    <Search className="h-4 w-4 text-gray-400"/>
                                    <Input
                                        placeholder="Search by unit or owner..."
                                        value={historySearch}
                                        onChange={e => setHistorySearch(e.target.value)}
                                        className="max-w-sm"
                                    />
                                </div>

                                {historyLoading ? (
                                    <div className="py-8 text-center">Loading history...</div>
                                ) : filteredHistory.length === 0 ? (
                                    <div className="py-8 text-center text-gray-500">No records found</div>
                                ) : (
                                    <div className="overflow-x-auto">
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Transfer Date</TableHead>
                                                    <TableHead>Unit</TableHead>
                                                    <TableHead>Previous Owner</TableHead>
                                                    <TableHead>New Owner</TableHead>
                                                    <TableHead className="text-right">Sale Price</TableHead>
                                                    <TableHead>Source</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {filteredHistory
                                                    .sort((a, b) => b.transfer_date?.localeCompare(a.transfer_date))
                                                    .map((record) => (
                                                        <TableRow key={record.id}>
                                                            <TableCell className="whitespace-nowrap">
                                                                {record.transfer_date
                                                                    ? new Date(record.transfer_date).toLocaleDateString('en-AU', {
                                                                        day: '2-digit',
                                                                        month: 'short',
                                                                        year: 'numeric'
                                                                    })
                                                                    : ' - '}
                                                            </TableCell>
                                                            <TableCell>
                                                                <span
                                                                    className="font-mono font-medium">{record.unit_number}</span>
                                                            </TableCell>
                                                            <TableCell>{record.previous_owner_name || ' - '}</TableCell>
                                                            <TableCell>{record.new_owner_name || ' - '}</TableCell>
                                                            <TableCell
                                                                className="text-right font-semibold text-green-700">
                                                                {formatPrice(record.sold_price_aud)}
                                                            </TableCell>
                                                            <TableCell>{record.source || ' - '}</TableCell>
                                                        </TableRow>
                                                    ))}
                                            </TableBody>
                                        </Table>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    </TabsContent>
                )}
            </Tabs>

            <Dialog open={formDialogOpen} onOpenChange={setFormDialogOpen}>
                <DialogContent className="max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>{editingTransfer ? 'Edit Transfer Request' : 'New Transfer Request'}</DialogTitle>
                        <DialogDescription>
                            Lodge an ownership change for a unit in your building. If the incoming owner isn't
                            registered yet, they'll receive an email invitation to set up their account once the
                            transfer is approved.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4">
                        <div>
                            <Label htmlFor="transfer-unit">Unit</Label>
                            <Select
                                value={formState.unit_number}
                                onValueChange={value => setFormState(prev => ( {...prev, unit_number: value} ))}
                            >
                                <SelectTrigger id="transfer-unit" className="mt-2">
                                    <SelectValue placeholder="Select a unit"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {( formOptions.accessible_units || [] ).map((unit) => (
                                        <SelectItem key={unit.unit_number} value={unit.unit_number}>
                                            {unit.display_name}
                                            {unit.owner_names?.length ? ` - ${unit.owner_names.join(' / ')}` : ''}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div>
                            <Label htmlFor="new-owner-email">Incoming Owner Email</Label>
                            <Input
                                id="new-owner-email"
                                type="email"
                                value={formState.new_owner_email}
                                onChange={e => setFormState(prev => ( {...prev, new_owner_email: e.target.value} ))}
                                placeholder="new.owner@example.com"
                                className="mt-2"
                            />
                        </div>

                        <div>
                            <Label htmlFor="settlement-date">Settlement Date</Label>
                            <Input
                                id="settlement-date"
                                type="date"
                                value={formState.settlement_date}
                                onChange={e => setFormState(prev => ( {...prev, settlement_date: e.target.value} ))}
                                className="mt-2"
                            />
                        </div>

                        <div>
                            <Label htmlFor="request-notes">Request Notes</Label>
                            <Textarea
                                id="request-notes"
                                value={formState.request_notes}
                                onChange={e => setFormState(prev => ( {...prev, request_notes: e.target.value} ))}
                                placeholder="Optional context for reviewers..."
                                rows={3}
                                className="mt-2"
                            />
                        </div>

                        <div>
                            <Label htmlFor="ownership-documents">
                                Supporting Documents
                            </Label>
                            <Textarea
                                id="ownership-documents"
                                value={formState.ownership_documents_text}
                                onChange={e => setFormState(prev => ( {
                                    ...prev,
                                    ownership_documents_text: e.target.value
                                } ))}
                                placeholder="Enter one document reference per line (optional)"
                                rows={4}
                                className="mt-2"
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setFormDialogOpen(false)}
                            disabled={formSubmitting}
                        >
                            Cancel
                        </Button>
                        <Button onClick={handleSubmitTransfer} disabled={formSubmitting}>
                            {formSubmitting
                                ? ( editingTransfer ? 'Saving...' : 'Creating...' )
                                : ( editingTransfer ? 'Save Changes' : 'Create Request' )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={reviewDialog} onOpenChange={setReviewDialog}>
                <DialogContent className="max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>
                            {reviewAction === 'reject' ? 'Reject Transfer Request' : 'Approve Transfer Request'}
                        </DialogTitle>
                        <DialogDescription>
                            {selectedTransfer && (
                                <>Unit {selectedTransfer.unit_number} - transfer
                                    to {selectedTransfer.new_owner?.full_name}</>
                            )}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4">
                        {reviewAction === 'approve_remove_old' && selectedTransfer && (
                            <div>
                                <Label className="text-sm font-semibold">Select owners to remove</Label>
                                <div className="mt-2 space-y-2">
                                    {selectedTransfer.old_owners.map((owner) => (
                                        <div key={owner.user_id} className="flex items-center gap-2 rounded border p-2">
                                            <Checkbox
                                                checked={selectedOwnersToRemove.includes(owner.user_id)}
                                                onCheckedChange={() => toggleOwnerRemoval(owner.user_id)}
                                            />
                                            <div>
                                                <div className="font-medium">{owner.full_name}</div>
                                                <div className="text-sm text-gray-500">{displayOwnerEmail(owner)}</div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                                {selectedOwnersToRemove.length === 0 && (
                                    <p className="mt-2 text-sm text-amber-600">
                                        No owners selected. All current owners will be removed if you continue.
                                    </p>
                                )}
                            </div>
                        )}

                        <div>
                            <Label htmlFor="review-notes">
                                Review Notes
                                {reviewAction === 'reject' && <span className="text-red-500"> *</span>}
                            </Label>
                            <Textarea
                                id="review-notes"
                                value={reviewNotes}
                                onChange={(e) => setReviewNotes(e.target.value)}
                                placeholder={
                                    reviewAction === 'reject'
                                        ? 'Please provide a reason for rejection...'
                                        : 'Optional notes about this approval...'
                                }
                                rows={4}
                                className="mt-2"
                            />
                        </div>

                        <div className="rounded bg-gray-50 p-4">
                            <Label className="text-sm font-semibold">Action Summary</Label>
                            <ul className="mt-2 space-y-1 text-sm">
                                {reviewAction === 'reject' && (
                                    <>
                                        <li>- The transfer request will be marked as rejected.</li>
                                        <li>- No ownership links will change.</li>
                                    </>
                                )}
                                {reviewAction === 'approve_keep_old' && (
                                    <>
                                        <li>- The incoming owner will be added to the unit.</li>
                                        <li>- Existing owners will remain active on the unit.</li>
                                    </>
                                )}
                                {reviewAction === 'approve_remove_old' && (
                                    <>
                                        <li>- The incoming owner will be added to the unit.</li>
                                        <li>- Selected owners will be removed from the unit.</li>
                                    </>
                                )}
                            </ul>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setReviewDialog(false)} disabled={processing}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleReview}
                            disabled={processing || ( reviewAction === 'reject' && !reviewNotes.trim() )}
                            variant={reviewAction === 'reject' ? 'destructive' : 'default'}
                        >
                            {processing ? 'Processing...' : reviewAction === 'reject' ? 'Reject Transfer' : 'Submit Approval'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
