import React, { useEffect, useState } from 'react';
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
import { Input } from '@/components/ui/input';
import { useAuth } from '@/contexts/AuthContext';
import { toast } from 'sonner';
import { AlertCircle, Calendar, CheckCircle, Clock, FileText, Home, RefreshCw, User, XCircle } from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: TenantRenewalsPage
 * Path: frontend/src/pages/dashboard/admin/TenantRenewalsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TenantRenewalsPage() {
    const {api} = useAuth();
    const [renewals, setRenewals] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedRenewal, setSelectedRenewal] = useState(null);
    const [reviewDialog, setReviewDialog] = useState(false);
    const [reviewAction, setReviewAction] = useState('');
    const [reviewNotes, setReviewNotes] = useState('');
    const [customExpirationDate, setCustomExpirationDate] = useState('');
    const [processing, setProcessing] = useState(false);

    const fetchRenewals = React.useCallback(async (status = null) => {
        try {
            setLoading(true);
            const endpoint = status ? `/tenant-renewals?status=${status}` : '/tenant-renewals';
            const response = await api.get(endpoint);
            setRenewals(response.data);
        } catch (error) {
            console.error('Failed to fetch renewals:', error);
            toast.error('Failed to load renewal requests');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchRenewals();
    }, [fetchRenewals]);
    /**
     * @generated FunctionHeader
     * Function: openReviewDialog
     * Path: frontend/src/pages/dashboard/admin/TenantRenewalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openReviewDialog = (renewal, action) => {
        setSelectedRenewal(renewal);
        setReviewAction(action);
        setReviewNotes('');
        setCustomExpirationDate('');
        setReviewDialog(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleReview
     * Path: frontend/src/pages/dashboard/admin/TenantRenewalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReview = async () => {
        if (!selectedRenewal || !reviewAction) return;

        if (reviewAction === 'reject' && !reviewNotes.trim()) {
            toast.error('Please provide a reason for rejection');
            return;
        }

        try {
            setProcessing(true);

            const payload = {
                action: reviewAction,
                notes: reviewNotes,
                ...( customExpirationDate && {new_expiration_date: customExpirationDate} )
            };

            await api.put(`/tenant-renewals/${selectedRenewal.id}`, payload);

            toast.success(
                reviewAction === 'reject'
                    ? 'Renewal request rejected'
                    : 'Renewal approved successfully'
            );

            setReviewDialog(false);
            fetchRenewals(); // Refresh list
        } catch (error) {
            console.error('Failed to process renewal:', error);
            toast.error(error.response?.data?.detail || 'Failed to process renewal');
        } finally {
            setProcessing(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: calculateDaysUntilExpiration
     * Path: frontend/src/pages/dashboard/admin/TenantRenewalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const calculateDaysUntilExpiration = (expirationDate) => {
        const today = new Date();
        const expDate = new Date(expirationDate);
        const diffTime = expDate - today;
        const diffDays = Math.ceil(diffTime / ( 1000 * 60 * 60 * 24 ));
        return diffDays;
    };
    /**
     * @generated FunctionHeader
     * Function: getStatusBadge
     * Path: frontend/src/pages/dashboard/admin/TenantRenewalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusBadge = (status) => {
        const variants = {
            pending: 'bg-yellow-100 text-yellow-800',
            approved: 'bg-green-100 text-green-800',
            rejected: 'bg-red-100 text-red-800',
            withdrawn: 'bg-gray-100 text-gray-800'
        };
        return <Badge className={variants[ status ] || 'bg-gray-100 text-gray-800'}>{status.toUpperCase()}</Badge>;
    };
    /**
     * @generated FunctionHeader
     * Function: getUrgencyIndicator
     * Path: frontend/src/pages/dashboard/admin/TenantRenewalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getUrgencyIndicator = (renewal) => {
        const daysLeft = calculateDaysUntilExpiration(renewal.original_expiration_date);
        if (daysLeft <= 7) {
            return <Badge className="bg-red-100 text-red-800">URGENT - {daysLeft} days left</Badge>;
        } else if (daysLeft <= 30) {
            return <Badge className="bg-amber-100 text-amber-800">{daysLeft} days until expiration</Badge>;
        }
        return <Badge className="bg-blue-100 text-blue-800">{daysLeft} days until expiration</Badge>;
    };
    /**
     * @generated FunctionHeader
     * Function: RenewalCard
     * Path: frontend/src/pages/dashboard/admin/TenantRenewalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const RenewalCard = ({renewal}) => {
        const daysLeft = calculateDaysUntilExpiration(renewal.original_expiration_date);
        const isUrgent = daysLeft <= 7;

        return (
            <Card className={`mb-4 ${isUrgent ? 'border-red-300 border-2' : ''}`}>
                <CardHeader>
                    <div className="flex justify-between items-start">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <Home className="h-5 w-5"/>
                                {renewal.unit_number}
                            </CardTitle>
                            <CardDescription>
                                Requested {new Date(renewal.requested_renewal_date).toLocaleDateString()}
                            </CardDescription>
                        </div>
                        <div className="space-y-2">
                            {getStatusBadge(renewal.status)}
                            {renewal.status === 'pending' && getUrgencyIndicator(renewal)}
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="space-y-4">
                        {/* Tenant Info */}
                        <div>
                            <Label className="text-sm font-semibold text-gray-700">Tenant</Label>
                            <div className="mt-2 flex items-center gap-2 p-2 bg-gray-50 rounded">
                                <User className="h-4 w-4 text-gray-500"/>
                                <div>
                                    <div className="font-medium">{renewal.tenant_name}</div>
                                    <div className="text-sm text-gray-500">{renewal.tenant_email}</div>
                                </div>
                            </div>
                        </div>

                        {/* Lease Timeline */}
                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <Label className="text-sm font-semibold text-gray-700">Current Lease</Label>
                                <div className="mt-2 p-3 bg-gray-50 rounded">
                                    <div className="flex items-center gap-2 text-sm">
                                        <Calendar className="h-4 w-4 text-gray-500"/>
                                        <div>
                                            <div
                                                className="font-medium">Start: {new Date(renewal.original_start_date).toLocaleDateString()}</div>
                                            <div className="font-medium text-red-600">
                                                Expires: {new Date(renewal.original_expiration_date).toLocaleDateString()}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div>
                                <Label className="text-sm font-semibold text-gray-700">Requested Renewal</Label>
                                <div className="mt-2 p-3 bg-green-50 rounded">
                                    <div className="flex items-center gap-2 text-sm">
                                        <RefreshCw className="h-4 w-4 text-green-600"/>
                                        <div>
                                            <div
                                                className="font-medium text-green-800">Duration: {renewal.requested_duration_days} days
                                            </div>
                                            <div className="font-medium text-green-800">
                                                New Expiry: {new Date(renewal.new_expiration_date).toLocaleDateString()}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Lease Documents */}
                        <div>
                            <Label className="text-sm font-semibold text-gray-700">Lease Documents</Label>
                            <div className="mt-2 space-y-2">
                                {renewal.current_lease_document_id && (
                                    <div className="flex items-center gap-2 text-sm text-gray-600">
                                        <FileText className="h-4 w-4"/>
                                        Current lease document on file
                                    </div>
                                )}
                                {renewal.new_lease_document_id && (
                                    <div className="flex items-center gap-2 text-sm text-green-600">
                                        <FileText className="h-4 w-4"/>
                                        New lease document provided
                                    </div>
                                )}
                                {!renewal.current_lease_document_id && !renewal.new_lease_document_id && (
                                    <div className="flex items-center gap-2 text-sm text-amber-600">
                                        <AlertCircle className="h-4 w-4"/>
                                        No lease documents
                                    </div>
                                )}
                            </div>
                        </div>

                        {/* Landlord Info (if available) */}
                        {renewal.landlord_user_id && (
                            <div className="p-3 bg-blue-50 rounded">
                                <Label className="text-sm font-semibold text-blue-800">Landlord Notification</Label>
                                <div className="text-sm text-blue-700 mt-1">
                                    {renewal.landlord_notified ? '✓ Landlord has been notified' : '○ Landlord notification pending'}
                                </div>
                            </div>
                        )}

                        {/* Review Info (if processed) */}
                        {renewal.status !== 'pending' && (
                            <div className="pt-4 border-t">
                                <Label className="text-sm font-semibold text-gray-700">Review Details</Label>
                                <div className="mt-2 text-sm space-y-1">
                                    {renewal.reviewed_date && (
                                        <div><strong>Reviewed
                                            on:</strong> {new Date(renewal.reviewed_date).toLocaleDateString()}</div>
                                    )}
                                    {renewal.status === 'approved' && renewal.approval_notes && (
                                        <div><strong>Approval Notes:</strong> {renewal.approval_notes}</div>
                                    )}
                                    {renewal.status === 'rejected' && renewal.rejection_reason && (
                                        <div className="text-red-600"><strong>Rejection
                                            Reason:</strong> {renewal.rejection_reason}</div>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Action Buttons (pending only) */}
                        {renewal.status === 'pending' && (
                            <div className="flex gap-2 pt-4 border-t">
                                <Button
                                    onClick={() => openReviewDialog(renewal, 'approve')}
                                    className="flex-1"
                                    variant="default"
                                >
                                    <CheckCircle className="h-4 w-4 mr-2"/>
                                    Approve Renewal
                                </Button>
                                <Button
                                    onClick={() => openReviewDialog(renewal, 'reject')}
                                    variant="destructive"
                                >
                                    <XCircle className="h-4 w-4 mr-2"/>
                                    Reject
                                </Button>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>
        );
    };

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold">Tenant Renewal Requests</h1>
                <p className="text-gray-600 mt-2">
                    Review and approve lease renewal requests from tenants
                </p>
            </div>

            <Tabs defaultValue="pending" onValueChange={(val) => fetchRenewals(val === 'all' ? null : val)}>
                <TabsList>
                    <TabsTrigger value="all">All Requests</TabsTrigger>
                    <TabsTrigger value="pending">
                        <Clock className="h-4 w-4 mr-2"/>
                        Pending
                    </TabsTrigger>
                    <TabsTrigger value="approved">
                        <CheckCircle className="h-4 w-4 mr-2"/>
                        Approved
                    </TabsTrigger>
                    <TabsTrigger value="rejected">
                        <XCircle className="h-4 w-4 mr-2"/>
                        Rejected
                    </TabsTrigger>
                </TabsList>

                <TabsContent value="all" className="mt-6">
                    {loading ? (
                        <div className="text-center py-12">Loading...</div>
                    ) : renewals.length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <AlertCircle className="h-12 w-12 mx-auto text-gray-400 mb-4"/>
                                <p className="text-gray-600">No renewal requests found</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div>
                            {renewals.map(renewal => (
                                <RenewalCard key={renewal.id} renewal={renewal}/>
                            ))}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="pending" className="mt-6">
                    {loading ? (
                        <div className="text-center py-12">Loading...</div>
                    ) : renewals.filter(r => r.status === 'pending').length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <CheckCircle className="h-12 w-12 mx-auto text-green-400 mb-4"/>
                                <p className="text-gray-600">No pending renewal requests</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div>
                            {renewals
                                .filter(r => r.status === 'pending')
                                .sort((a, b) => {
                                    // Sort by urgency (closest to expiration first)
                                    const daysA = calculateDaysUntilExpiration(a.original_expiration_date);
                                    const daysB = calculateDaysUntilExpiration(b.original_expiration_date);
                                    return daysA - daysB;
                                })
                                .map(renewal => (
                                    <RenewalCard key={renewal.id} renewal={renewal}/>
                                ))}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="approved" className="mt-6">
                    {loading ? (
                        <div className="text-center py-12">Loading...</div>
                    ) : renewals.filter(r => r.status === 'approved').length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <p className="text-gray-600">No approved renewals</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div>
                            {renewals.filter(r => r.status === 'approved').map(renewal => (
                                <RenewalCard key={renewal.id} renewal={renewal}/>
                            ))}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="rejected" className="mt-6">
                    {loading ? (
                        <div className="text-center py-12">Loading...</div>
                    ) : renewals.filter(r => r.status === 'rejected').length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <p className="text-gray-600">No rejected renewals</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div>
                            {renewals.filter(r => r.status === 'rejected').map(renewal => (
                                <RenewalCard key={renewal.id} renewal={renewal}/>
                            ))}
                        </div>
                    )}
                </TabsContent>
            </Tabs>

            {/* Review Dialog */}
            <Dialog open={reviewDialog} onOpenChange={setReviewDialog}>
                <DialogContent className="max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>
                            {reviewAction === 'reject' ? 'Reject Renewal Request' : 'Approve Renewal Request'}
                        </DialogTitle>
                        <DialogDescription>
                            {selectedRenewal && (
                                <>Unit {selectedRenewal.unit_number} - {selectedRenewal.tenant_name}</>
                            )}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4">
                        {/* Current vs New Expiration */}
                        {reviewAction === 'approve' && selectedRenewal && (
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <Label className="text-sm">Current Expiration</Label>
                                    <div className="mt-2 p-3 bg-gray-100 rounded text-sm font-medium">
                                        {new Date(selectedRenewal.original_expiration_date).toLocaleDateString()}
                                    </div>
                                </div>
                                <div>
                                    <Label className="text-sm">New Expiration</Label>
                                    <div className="mt-2 p-3 bg-green-100 rounded text-sm font-medium text-green-800">
                                        {new Date(selectedRenewal.new_expiration_date).toLocaleDateString()}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Custom Expiration Date (optional) */}
                        {reviewAction === 'approve' && (
                            <div>
                                <Label htmlFor="custom-expiration">Custom Expiration Date (Optional)</Label>
                                <Input
                                    id="custom-expiration"
                                    type="date"
                                    value={customExpirationDate}
                                    onChange={(e) => setCustomExpirationDate(e.target.value)}
                                    className="mt-2"
                                    min={new Date().toISOString().split('T')[ 0 ]}
                                />
                                <p className="text-sm text-gray-500 mt-1">
                                    Leave empty to use the requested expiration date
                                </p>
                            </div>
                        )}

                        {/* Review Notes */}
                        <div>
                            <Label htmlFor="review-notes">
                                {reviewAction === 'reject' ? 'Rejection Reason' : 'Approval Notes'}
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

                        {/* Action Summary */}
                        <div className="p-4 bg-gray-50 rounded">
                            <Label className="text-sm font-semibold">Action Summary:</Label>
                            <ul className="mt-2 text-sm space-y-1">
                                {reviewAction === 'reject' && (
                                    <>
                                        <li>• Renewal request will be marked as rejected</li>
                                        <li>• Tenant's lease will still expire on original date</li>
                                        <li>• Tenant will be notified of rejection</li>
                                    </>
                                )}
                                {reviewAction === 'approve' && (
                                    <>
                                        <li>• Lease expiration will be extended
                                            by {selectedRenewal?.requested_duration_days} days
                                        </li>
                                        <li>• New
                                            expiration: {customExpirationDate || new Date(selectedRenewal?.new_expiration_date).toLocaleDateString()}</li>
                                        <li>• Tenant account will remain active</li>
                                        <li>• Tenant will be notified of approval</li>
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
                            disabled={processing}
                            variant={reviewAction === 'reject' ? 'destructive' : 'default'}
                        >
                            {processing ? 'Processing...' : reviewAction === 'reject' ? 'Reject Renewal' : 'Approve Renewal'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
