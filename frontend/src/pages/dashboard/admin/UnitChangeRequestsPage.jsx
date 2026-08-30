// @featuretrace:unit-change-requests — Admin/EC review queue for unit assignment/status change requests.
// Layer: frontend
// Data flow: this page -> GET /change-requests, PUT /change-requests/{id}/review -> change_requests (building-scoped).
// Related: backend routers handling /change-requests (see server.py's router table)
import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Badge } from '../../../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../../components/ui/table';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../../components/ui/dialog';
import { Textarea } from '../../../components/ui/textarea';
import { ArrowRight, CheckCircle, Clock, RefreshCw, Search, XCircle } from 'lucide-react';
import { formatDate } from '../../../lib/utils';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: UnitChangeRequestsPage
 * Path: frontend/src/pages/dashboard/admin/UnitChangeRequestsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const UnitChangeRequestsPage = () => {
    const {api} = useAuth();
    const [requests, setRequests] = useState([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [reviewDialogOpen, setReviewDialogOpen] = useState(false);
    const [selectedRequest, setSelectedRequest] = useState(null);
    const [adminNotes, setAdminNotes] = useState('');
    const [processing, setProcessing] = useState(false);

    const fetchRequests = useCallback(async () => {
        setLoading(true);
        try {
            const response = await api.get('/change-requests');
            setRequests(response.data);
        } catch (error) {
            console.error('Failed to fetch unit change requests:', error);
            toast.error('Failed to load requests');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchRequests();
    }, [fetchRequests]);
    /**
     * @generated FunctionHeader
     * Function: handleReview
     * Path: frontend/src/pages/dashboard/admin/UnitChangeRequestsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReview = (request) => {
        setSelectedRequest(request);
        setAdminNotes('');
        setReviewDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: submitReview
     * Path: frontend/src/pages/dashboard/admin/UnitChangeRequestsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const submitReview = async (action) => {
        if (!selectedRequest) return;

        setProcessing(true);
        try {
            await api.put(`/change-requests/${selectedRequest.id}/review`, {
                action,
                admin_notes: adminNotes
            });
            toast.success(`Request ${action === 'approve' ? 'approved' : 'rejected'} successfully`);
            setReviewDialogOpen(false);
            fetchRequests();
        } catch (error) {
            console.error('Failed to review request:', error);
            toast.error(error.response?.data?.detail?.message || 'Failed to process request');
        } finally {
            setProcessing(false);
        }
    };

    const filteredRequests = requests.filter(req =>
        req.user_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        req.user_email.toLowerCase().includes(searchTerm.toLowerCase()) ||
        req.requested_unit.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const statusColors = {
        pending: 'bg-yellow-100 text-yellow-800',
        approved: 'bg-green-100 text-green-800',
        rejected: 'bg-red-100 text-red-800'
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Change Requests</h1>
                    <p className="text-muted-foreground">Review and approve unit or profile status changes</p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchRequests} disabled={loading}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`}/>
                    Refresh
                </Button>
            </div>

            {/* Context panel: distinguish from Smart Requests */}
            <div className="rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-800">
                <p className="font-semibold mb-1">What are Change Requests?</p>
                <p>These are requests from residents or owners to update their <strong>unit assignment or property
                    status</strong> (e.g. marking a unit as tenanted, changing managing agent status). They are reviewed
                    and approved or rejected by the Strata Manager or Chairman.</p>
                <p className="mt-1 text-xs text-blue-600">📌 <strong>Not the same as Smart Requests</strong> — Smart
                    Requests (Maintenance, Levy Queries, Noise Complaints, etc.) are found at <Link
                        href="/requests/new" className="underline">Smart Request</Link>.</p>
            </div>

            <Card className="card-dashboard">
                <CardHeader>
                    <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                        <Input
                            placeholder="Search by name, email or unit..."
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                            className="pl-10"
                        />
                    </div>
                </CardHeader>
                <CardContent>
                    {loading && requests.length === 0 ? (
                        <div className="space-y-4">
                            {[1, 2, 3].map((i) => (
                                <div key={i} className="skeleton h-16 w-full"/>
                            ))}
                        </div>
                    ) : filteredRequests.length === 0 ? (
                        <div className="py-12 text-center">
                            <Clock className="h-12 w-12 text-muted-foreground/50 mx-auto mb-4"/>
                            <p className="text-muted-foreground">No unit change requests found</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>User</TableHead>
                                        <TableHead>Requested Changes</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Requested On</TableHead>
                                        <TableHead className="text-right">Action</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filteredRequests.map((request) => (
                                        <TableRow key={request.id}>
                                            <TableCell>
                                                <div>
                                                    <p className="font-medium">{request.user_name}</p>
                                                    <p className="text-xs text-muted-foreground">{request.user_email}</p>
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <div className="space-y-1">
                                                    {request.current_unit !== request.requested_unit && (
                                                        <div className="flex items-center gap-2 text-sm">
                                                            <span className="text-muted-foreground">Unit:</span>
                                                            <span>{request.current_unit || 'None'}</span>
                                                            <ArrowRight className="h-3 w-3"/>
                                                            <span
                                                                className="font-bold text-primary">{request.requested_unit}</span>
                                                        </div>
                                                    )}
                                                    {request.current_is_managing_agent !== request.requested_is_managing_agent && (
                                                        <div className="flex items-center gap-2 text-xs">
                                                            <Badge variant="outline">Agent</Badge>
                                                            <span>{String(request.current_is_managing_agent)}</span>
                                                            <ArrowRight className="h-3 w-3"/>
                                                            <span
                                                                className="font-semibold text-primary">{String(request.requested_is_managing_agent)}</span>
                                                        </div>
                                                    )}
                                                    {request.current_is_tenanted !== request.requested_is_tenanted && (
                                                        <div className="flex items-center gap-2 text-xs">
                                                            <Badge variant="outline">Tenanted</Badge>
                                                            <span>{String(request.current_is_tenanted)}</span>
                                                            <ArrowRight className="h-3 w-3"/>
                                                            <span
                                                                className="font-semibold text-primary">{String(request.requested_is_tenanted)}</span>
                                                        </div>
                                                    )}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <Badge className={statusColors[ request.status ]}>
                                                    {request.status.charAt(0).toUpperCase() + request.status.slice(1)}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-muted-foreground text-sm">
                                                {formatDate(request.created_at)}
                                            </TableCell>
                                            <TableCell className="text-right">
                                                {request.status === 'pending' ? (
                                                    <Button size="sm" onClick={() => handleReview(request)}>
                                                        Review
                                                    </Button>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">
                            Reviewed by {request.reviewer_name}
                          </span>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Dialog open={reviewDialogOpen} onOpenChange={setReviewDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Review Change Request</DialogTitle>
                        <DialogDescription>
                            User <strong>{selectedRequest?.user_name}</strong> is requesting the following changes:
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-3 py-4">
                        {selectedRequest?.current_unit !== selectedRequest?.requested_unit && (
                            <div className="flex items-center justify-between p-2 bg-muted rounded">
                                <span className="text-sm font-medium">Unit Number</span>
                                <div className="flex items-center gap-2 text-sm">
                                    <span>{selectedRequest?.current_unit || 'None'}</span>
                                    <ArrowRight className="h-4 w-4"/>
                                    <span className="font-bold text-primary">{selectedRequest?.requested_unit}</span>
                                </div>
                            </div>
                        )}
                        {selectedRequest?.current_is_managing_agent !== selectedRequest?.requested_is_managing_agent && (
                            <div className="flex items-center justify-between p-2 bg-muted rounded">
                                <span className="text-sm font-medium">Managing Agent</span>
                                <div className="flex items-center gap-2 text-sm">
                                    <span>{String(selectedRequest?.current_is_managing_agent)}</span>
                                    <ArrowRight className="h-4 w-4"/>
                                    <span
                                        className="font-bold text-primary">{String(selectedRequest?.requested_is_managing_agent)}</span>
                                </div>
                            </div>
                        )}
                        {selectedRequest?.current_is_tenanted !== selectedRequest?.requested_is_tenanted && (
                            <div className="flex items-center justify-between p-2 bg-muted rounded">
                                <span className="text-sm font-medium">Is Tenanted</span>
                                <div className="flex items-center gap-2 text-sm">
                                    <span>{String(selectedRequest?.current_is_tenanted)}</span>
                                    <ArrowRight className="h-4 w-4"/>
                                    <span
                                        className="font-bold text-primary">{String(selectedRequest?.requested_is_tenanted)}</span>
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Admin Notes / Reason</label>
                            <Textarea
                                placeholder="Optional notes for the user..."
                                value={adminNotes}
                                onChange={(e) => setAdminNotes(e.target.value)}
                                rows={3}
                            />
                        </div>
                    </div>

                    <DialogFooter className="flex-col sm:flex-row gap-2">
                        <Button
                            variant="outline"
                            className="sm:flex-1"
                            onClick={() => submitReview('reject')}
                            disabled={processing}
                        >
                            <XCircle className="mr-2 h-4 w-4"/>
                            Reject
                        </Button>
                        <Button
                            className="sm:flex-1 bg-green-600 hover:bg-green-700"
                            onClick={() => submitReview('approve')}
                            disabled={processing}
                        >
                            <CheckCircle className="mr-2 h-4 w-4"/>
                            Approve
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default UnitChangeRequestsPage;
