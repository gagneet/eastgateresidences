import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '../../components/ui/tooltip';
import { Alert, AlertDescription, AlertTitle, } from '../../components/ui/alert';
import {
    AlertTriangle,
    Building2,
    CheckCircle,
    Clock,
    ExternalLink,
    FileText,
    Loader2,
    User,
    Wrench,
    XCircle
} from 'lucide-react';
import { formatCurrency, formatDate } from '../../lib/utils';
import { toast } from 'sonner';
import Link from 'next/link';
/**
 * @generated FunctionHeader
 * Function: MyApprovalsPage
 * Path: frontend/src/pages/dashboard/MyApprovalsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MyApprovalsPage = () => {
    const {api, hasPermission, user, fetchPendingApprovalsCount, selectedBuilding} = useAuth();
    const [invoices, setInvoices] = useState([]);
    const [workOrders, setWorkOrders] = useState([]);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [selectedInvoice, setSelectedInvoice] = useState(null);
    const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
    const [approveDialogOpen, setApproveDialogOpen] = useState(false);
    const [rejectionReason, setRejectionReason] = useState('');
    const [buildingSettings, setBuildingSettings] = useState(null);

    const canApprove = hasPermission('can_manage_finances');

    useEffect(() => {
        api.get('/settings').then(res => setBuildingSettings(res.data)).catch(() => {});
    }, [api, selectedBuilding?.id]);

    const fetchPendingInvoices = useCallback(async () => {
        try {
            setLoading(true);
            const data = await fetchPendingApprovalsCount(true);
            if (data) {
                setInvoices(data);
            }

            // Fetch pending work orders
            const woRes = await api.get('/work-orders?status=pending_approval');
            setWorkOrders(woRes.data);
        } catch (error) {
            console.error('Failed to fetch pending invoices:', error);
            toast.error('Failed to load pending approvals');
        } finally {
            setLoading(false);
        }
    }, [fetchPendingApprovalsCount]);

    useEffect(() => {
        if (canApprove) {
            fetchPendingInvoices();
        }
    }, [fetchPendingInvoices, canApprove]);
    /**
     * @generated FunctionHeader
     * Function: handleApprove
     * Path: frontend/src/pages/dashboard/MyApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleApprove = async () => {
        if (!selectedInvoice) return;

        try {
            setActionLoading(true);
            await api.put(`/work-orders/invoices/${selectedInvoice.id}/approve`);
            toast.success(`Invoice ${selectedInvoice.invoice_number} approved`);
            setApproveDialogOpen(false);
            setSelectedInvoice(null);
            await fetchPendingInvoices();
        } catch (error) {
            console.error('Failed to approve invoice:', error);
            toast.error(error.response?.data?.detail || 'Failed to approve invoice');
        } finally {
            setActionLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleReject
     * Path: frontend/src/pages/dashboard/MyApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReject = async () => {
        if (!selectedInvoice || !rejectionReason.trim()) {
            toast.error('Please provide a rejection reason');
            return;
        }

        if (rejectionReason.trim().length < 5) {
            toast.error('Rejection reason must be at least 5 characters');
            return;
        }

        try {
            setActionLoading(true);
            await api.put(`/work-orders/invoices/${selectedInvoice.id}/reject?reason=${encodeURIComponent(rejectionReason)}`);
            toast.success(`Invoice ${selectedInvoice.invoice_number} rejected`);
            setRejectDialogOpen(false);
            setSelectedInvoice(null);
            setRejectionReason('');
            await fetchPendingInvoices();
        } catch (error) {
            console.error('Failed to reject invoice:', error);
            toast.error(error.response?.data?.detail || 'Failed to reject invoice');
        } finally {
            setActionLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openApproveDialog
     * Path: frontend/src/pages/dashboard/MyApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openApproveDialog = (invoice) => {
        setSelectedInvoice(invoice);
        setApproveDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openRejectDialog
     * Path: frontend/src/pages/dashboard/MyApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openRejectDialog = (invoice) => {
        setSelectedInvoice(invoice);
        setRejectDialogOpen(true);
    };

    if (!canApprove) {
        return (
            <div className="max-w-6xl mx-auto space-y-6">
                <Card className="card-dashboard">
                    <CardContent className="p-12 text-center">
                        <AlertTriangle className="h-16 w-16 text-muted-foreground mx-auto mb-4"/>
                        <h2 className="text-xl font-semibold mb-2">Access Restricted</h2>
                        <p className="text-muted-foreground">
                            You do not have permission to approve invoices. This feature is restricted to EC members and
                            administrators.
                        </p>
                    </CardContent>
                </Card>
            </div>
        );
    }

    return (
        <div className="max-w-7xl mx-auto space-y-6" data-testid="my-approvals-page">
            {/* Header */}
            <div>
                <h1 className="text-2xl font-bold">My Approvals</h1>
                <p className="text-muted-foreground">Review and approve pending invoices</p>
            </div>

            {/* Property Overview Widget */}
            <Card className="card-dashboard border-l-4 border-l-primary">
                <CardContent className="p-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                        <div className="flex items-start gap-3">
                            <Building2 className="h-5 w-5 text-primary mt-0.5"/>
                            <div>
                                <p className="text-xs text-muted-foreground">Plan</p>
                                <p className="font-semibold">{selectedBuilding?.building_id || '—'}</p>
                            </div>
                        </div>
                        <div className="flex items-start gap-3">
                            <Building2 className="h-5 w-5 text-primary mt-0.5"/>
                            <div>
                                <p className="text-xs text-muted-foreground">Complex</p>
                                <p className="font-semibold">{selectedBuilding?.name || buildingSettings?.building_name || '—'}</p>
                            </div>
                        </div>
                        <div className="flex items-start gap-3">
                            <User className="h-5 w-5 text-primary mt-0.5"/>
                            <div>
                                <p className="text-xs text-muted-foreground">Contact</p>
                                <p className="font-semibold">{buildingSettings?.contact_email || buildingSettings?.contact_phone || '—'}</p>
                            </div>
                        </div>
                        <div className="flex items-start gap-3">
                            <FileText className="h-5 w-5 text-primary mt-0.5"/>
                            <div>
                                <p className="text-xs text-muted-foreground">Address</p>
                                <p className="font-semibold text-sm">{buildingSettings?.building_address || selectedBuilding?.address || '—'}</p>
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Pending Work Orders Card */}
            <Card className="card-dashboard">
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <Wrench className="h-5 w-5"/>
                                Work Orders Awaiting Approval
                            </CardTitle>
                            <CardDescription>
                                {loading ? 'Loading...' : `${workOrders.length} work order${workOrders.length !== 1 ? 's' : ''} found`}
                            </CardDescription>
                        </div>
                        <Badge variant={workOrders.length > 0 ? "default" : "secondary"} className="text-lg px-4 py-2">
                            {workOrders.length}
                        </Badge>
                    </div>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex items-center justify-center py-6">
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/>
                        </div>
                    ) : workOrders.length === 0 ? (
                        <div className="text-center py-6 text-muted-foreground">No pending work orders</div>
                    ) : (
                        <div className="space-y-4">
                            {workOrders.map(wo => (
                                <div key={wo.id}
                                     className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors">
                                    <div>
                                        <p className="font-semibold">{wo.title}</p>
                                        <p className="text-sm text-muted-foreground">Vendor: {wo.vendor_name} •
                                            Est: {formatCurrency(wo.estimated_cost)}</p>
                                    </div>
                                    <Button size="sm" asChild>
                                        <Link href={`/maintenance/work-order/${wo.id}`}>Review</Link>
                                    </Button>
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Approval Status Card */}
            <Card className="card-dashboard">
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <Clock className="h-5 w-5"/>
                                Pending Invoice Approvals
                            </CardTitle>
                            <CardDescription>
                                {loading ? 'Loading...' : `${invoices.length} invoice${invoices.length !== 1 ? 's' : ''} found`}
                            </CardDescription>
                        </div>
                        <Badge variant={invoices.length > 0 ? "default" : "secondary"} className="text-lg px-4 py-2">
                            {invoices.length}
                        </Badge>
                    </div>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex items-center justify-center py-12">
                            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
                            <span className="ml-2 text-muted-foreground">Loading pending invoices...</span>
                        </div>
                    ) : invoices.length === 0 ? (
                        <div className="text-center py-12">
                            <CheckCircle className="h-16 w-16 text-green-500 mx-auto mb-4"/>
                            <h3 className="text-lg font-semibold mb-2">No Pending Approvals</h3>
                            <p className="text-muted-foreground">
                                All invoices have been reviewed. Great work!
                            </p>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {/* Desktop Table View */}
                            <div className="hidden md:block rounded-md border">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Invoice #</TableHead>
                                            <TableHead>Contractor</TableHead>
                                            <TableHead>PO Number</TableHead>
                                            <TableHead>Amount</TableHead>
                                            <TableHead>GST</TableHead>
                                            <TableHead>Total</TableHead>
                                            <TableHead>Date</TableHead>
                                            <TableHead className="text-right">Actions</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {invoices.map((invoice) => (
                                            <TableRow key={invoice.id}>
                                                <TableCell className="font-medium">{invoice.invoice_number}</TableCell>
                                                <TableCell>{invoice.contractor_name}</TableCell>
                                                <TableCell>{invoice.po_number}</TableCell>
                                                <TableCell>{formatCurrency(invoice.amount)}</TableCell>
                                                <TableCell>{formatCurrency(invoice.gst_amount)}</TableCell>
                                                <TableCell className="font-semibold">
                                                    {formatCurrency(invoice.total_amount)}
                                                    {invoice.warnings?.length > 0 && (
                                                        <TooltipProvider>
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <AlertTriangle
                                                                        className="h-4 w-4 text-orange-500 inline ml-2 cursor-help"/>
                                                                </TooltipTrigger>
                                                                <TooltipContent>
                                                                    <ul className="list-disc list-inside text-xs">
                                                                        {invoice.warnings.map((w, i) => <li
                                                                            key={i}>{w}</li>)}
                                                                    </ul>
                                                                </TooltipContent>
                                                            </Tooltip>
                                                        </TooltipProvider>
                                                    )}
                                                </TableCell>
                                                <TableCell>{formatDate(invoice.created_at)}</TableCell>
                                                <TableCell className="text-right">
                                                    <div className="flex items-center justify-end gap-2">
                                                        <Button
                                                            size="sm"
                                                            variant="default"
                                                            onClick={() => openApproveDialog(invoice)}
                                                            className="bg-green-600 hover:bg-green-700"
                                                        >
                                                            <CheckCircle className="h-4 w-4 mr-1"/>
                                                            Approve
                                                        </Button>
                                                        <Button
                                                            size="sm"
                                                            variant="destructive"
                                                            onClick={() => openRejectDialog(invoice)}
                                                        >
                                                            <XCircle className="h-4 w-4 mr-1"/>
                                                            Reject
                                                        </Button>
                                                    </div>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>

                            {/* Mobile Card View */}
                            <div className="md:hidden space-y-4">
                                {invoices.map((invoice) => (
                                    <Card key={invoice.id} className="border-l-4 border-l-yellow-500">
                                        <CardContent className="p-4 space-y-3">
                                            <div className="flex items-start justify-between">
                                                <div>
                                                    <p className="font-semibold">{invoice.invoice_number}</p>
                                                    <p className="text-sm text-muted-foreground">{invoice.contractor_name}</p>
                                                </div>
                                                <Badge variant="outline"
                                                       className="bg-yellow-50 text-yellow-800 border-yellow-200">
                                                    Pending
                                                </Badge>
                                            </div>
                                            <div className="grid grid-cols-2 gap-2 text-sm">
                                                <div>
                                                    <p className="text-muted-foreground">PO Number</p>
                                                    <p className="font-medium">{invoice.po_number}</p>
                                                </div>
                                                <div>
                                                    <p className="text-muted-foreground">Total Amount</p>
                                                    <p className="font-semibold text-green-600">{formatCurrency(invoice.total_amount)}</p>
                                                </div>
                                                <div>
                                                    <p className="text-muted-foreground">Amount</p>
                                                    <p>{formatCurrency(invoice.amount)}</p>
                                                </div>
                                                <div>
                                                    <p className="text-muted-foreground">GST</p>
                                                    <p>{formatCurrency(invoice.gst_amount)}</p>
                                                </div>
                                                <div className="col-span-2">
                                                    <p className="text-muted-foreground">Date</p>
                                                    <p>{formatDate(invoice.created_at)}</p>
                                                </div>
                                            </div>
                                            <div className="flex gap-2 pt-2">
                                                <Button
                                                    size="sm"
                                                    variant="default"
                                                    onClick={() => openApproveDialog(invoice)}
                                                    className="flex-1 bg-green-600 hover:bg-green-700"
                                                >
                                                    <CheckCircle className="h-4 w-4 mr-1"/>
                                                    Approve
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="destructive"
                                                    onClick={() => openRejectDialog(invoice)}
                                                    className="flex-1"
                                                >
                                                    <XCircle className="h-4 w-4 mr-1"/>
                                                    Reject
                                                </Button>
                                            </div>
                                        </CardContent>
                                    </Card>
                                ))}
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Help & Guidance Section */}
            <Card className="card-dashboard bg-blue-50 dark:bg-blue-950 border-blue-200">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-blue-900 dark:text-blue-100">
                        <FileText className="h-5 w-5"/>
                        How to Approve Invoices
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-blue-900 dark:text-blue-100">
                    <p className="text-sm">
                        Review each invoice carefully before approving or rejecting. Here are some tips:
                    </p>
                    <ul className="text-sm space-y-2 list-disc list-inside">
                        <li>Verify the invoice amount matches the purchase order</li>
                        <li>Check that the contractor name is correct</li>
                        <li>Ensure the work was completed as specified</li>
                        <li>If rejecting, provide a clear reason for audit purposes</li>
                        <li>Approved invoices will be queued for payment</li>
                    </ul>
                    <div className="flex flex-col sm:flex-row gap-2 pt-2">
                        <a
                            href="https://www.youtube.com/watch?v=example-desktop"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-2 text-sm font-medium text-blue-700 dark:text-blue-300 hover:underline"
                        >
                            <ExternalLink className="h-4 w-4"/>
                            Watch Desktop Guide
                        </a>
                        <span className="hidden sm:inline text-blue-400">•</span>
                        <a
                            href="https://www.youtube.com/watch?v=example-mobile"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-2 text-sm font-medium text-blue-700 dark:text-blue-300 hover:underline"
                        >
                            <ExternalLink className="h-4 w-4"/>
                            Watch Mobile Guide
                        </a>
                    </div>
                </CardContent>
            </Card>

            {/* Approve Confirmation Dialog */}
            <Dialog open={approveDialogOpen} onOpenChange={setApproveDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <CheckCircle className="h-5 w-5 text-green-600"/>
                            Approve Invoice
                        </DialogTitle>
                        <DialogDescription>
                            Are you sure you want to approve this invoice? This action cannot be undone.
                        </DialogDescription>
                    </DialogHeader>

                    {selectedInvoice && (
                        <div className="space-y-3 py-4">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pb-4 border-b">
                                <div className="space-y-2">
                                    <Label className="text-xs uppercase text-muted-foreground">Invoice Details</Label>
                                    <div className="grid grid-cols-2 gap-2 text-sm">
                                        <p className="text-muted-foreground">Number:</p>
                                        <p className="font-semibold">{selectedInvoice.invoice_number}</p>
                                        <p className="text-muted-foreground">Vendor:</p>
                                        <p className="font-semibold">{selectedInvoice.contractor_name}</p>
                                        <p className="text-muted-foreground text-lg font-bold">Total:</p>
                                        <p className="text-lg font-bold text-green-600">{formatCurrency(selectedInvoice.total_amount)}</p>
                                    </div>
                                </div>
                                {selectedInvoice.warnings?.some(w => w.includes('quote')) && (
                                    <div className="space-y-2 p-3 bg-muted rounded-lg">
                                        <Label className="text-xs uppercase text-muted-foreground">Quote
                                            Comparison</Label>
                                        <div className="text-sm space-y-1">
                                            <p className="flex justify-between"><span>Approved Quote:</span> <span
                                                className="font-medium text-blue-600">See Work Order</span></p>
                                            <p className="flex justify-between text-xs text-orange-600 font-bold">
                                                <span>Variance:</span>
                                                <span>{selectedInvoice.warnings.find(w => w.includes('exceeds'))?.split('by ')[ 1 ] || 'Unknown'}</span>
                                            </p>
                                        </div>
                                    </div>
                                )}
                            </div>
                            {selectedInvoice.notes && (
                                <div>
                                    <p className="text-sm text-muted-foreground mb-1">Notes</p>
                                    <p className="text-sm bg-muted p-3 rounded">{selectedInvoice.notes}</p>
                                </div>
                            )}
                            {selectedInvoice.warnings?.length > 0 && (
                                <Alert variant="warning" className="bg-orange-50 border-orange-200 mt-4">
                                    <AlertTriangle className="h-4 w-4 text-orange-600"/>
                                    <AlertTitle className="text-orange-800 font-bold">Financial Integrity
                                        Warning</AlertTitle>
                                    <AlertDescription className="text-orange-700">
                                        <ul className="list-disc list-inside mt-1">
                                            {selectedInvoice.warnings.map((w, i) => <li key={i}>{w}</li>)}
                                        </ul>
                                    </AlertDescription>
                                </Alert>
                            )}
                        </div>
                    )}

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setApproveDialogOpen(false)} disabled={actionLoading}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleApprove}
                            disabled={actionLoading}
                            className="bg-green-600 hover:bg-green-700"
                        >
                            {actionLoading ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                    Approving...
                                </>
                            ) : (
                                <>
                                    <CheckCircle className="mr-2 h-4 w-4"/>
                                    Confirm Approval
                                </>
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Reject Dialog with Reason */}
            <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <XCircle className="h-5 w-5 text-red-600"/>
                            Reject Invoice
                        </DialogTitle>
                        <DialogDescription>
                            Please provide a reason for rejecting this invoice. The reason will be sent to the creator.
                        </DialogDescription>
                    </DialogHeader>

                    {selectedInvoice && (
                        <div className="space-y-4 py-4">
                            <div className="grid grid-cols-2 gap-3 text-sm">
                                <div>
                                    <p className="text-muted-foreground">Invoice Number</p>
                                    <p className="font-semibold">{selectedInvoice.invoice_number}</p>
                                </div>
                                <div>
                                    <p className="text-muted-foreground">Total Amount</p>
                                    <p className="font-semibold">{formatCurrency(selectedInvoice.total_amount)}</p>
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="rejection-reason">
                                    Rejection Reason <span className="text-red-500">*</span>
                                </Label>
                                <Textarea
                                    id="rejection-reason"
                                    placeholder="E.g., Invoice amount does not match PO, work not completed as specified, incorrect contractor information..."
                                    value={rejectionReason}
                                    onChange={(e) => setRejectionReason(e.target.value)}
                                    rows={4}
                                    required
                                />
                                <p className="text-xs text-muted-foreground">Minimum 5 characters required</p>
                            </div>
                        </div>
                    )}

                    <DialogFooter>
                        <Button variant="outline" onClick={() => {
                            setRejectDialogOpen(false);
                            setRejectionReason('');
                        }} disabled={actionLoading}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleReject}
                            disabled={actionLoading || rejectionReason.trim().length < 5}
                        >
                            {actionLoading ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                    Rejecting...
                                </>
                            ) : (
                                <>
                                    <XCircle className="mr-2 h-4 w-4"/>
                                    Confirm Rejection
                                </>
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default MyApprovalsPage;
