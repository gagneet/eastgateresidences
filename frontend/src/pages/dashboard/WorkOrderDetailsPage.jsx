import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger, } from '../../components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import {
    ArrowLeft,
    CheckCircle2,
    FileText,
    Loader2,
    Mail,
    MessageSquare,
    Plus,
    Star,
    User,
    Wrench,
    XCircle
} from 'lucide-react';
import { formatCurrency, formatDate } from '../../lib/utils';
import { toast } from 'sonner';
import Timeline from '../../components/shared/Timeline';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuTrigger
} from '../../components/ui/dropdown-menu';
/**
 * @generated FunctionHeader
 * Function: WorkOrderDetailsPage
 * Path: frontend/src/pages/dashboard/WorkOrderDetailsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const WorkOrderDetailsPage = () => {
    const params = useParams();
    const wo_id = params?.wo_id;
    const router = useRouter();
    const {api, hasPermission, user} = useAuth();
    const [wo, setWo] = useState(null);
    const [quotes, setQuotes] = useState([]);
    const [communications, setCommunications] = useState([]);
    const [attachments, setAttachments] = useState([]);
    const [invoices, setInvoices] = useState([]);
    const [timeline, setTimeline] = useState([]);
    const [loading, setLoading] = useState(true);
    const [quoteDialogOpen, setQuoteDialogOpen] = useState(false);
    const [completionDialogOpen, setCompletionDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [contractors, setContractors] = useState([]);
    const [showOverride, setShowOverride] = useState(false);
    const [isECMember, setIsECMember] = useState(false);

    const [quoteForm, setQuoteForm] = useState({
        vendor_id: '', amount: '', description: ''
    });

    const [completionForm, setCompletionForm] = useState({
        invoice_number: '', amount: '', gst: '', notes: '', completion_photos: [], compliance_certificates: []
    });

    const canManage = hasPermission('can_manage_meetings');

    useEffect(() => {
        if (user?.role === 'ec_member' || user?.role === 'strata_admin' || user?.role === 'super_admin' || user?.role === 'strata_manager') {
            setIsECMember(true);
        }
    }, [user]);

    const fetchData = useCallback(async () => {
        try {
            const [woRes, quotesRes, commRes, attachRes, timelineRes] = await Promise.all([
                api.get(`/work-orders/${wo_id}`),
                api.get(`/work-orders/${wo_id}/quotes`),
                api.get(`/work-orders/${wo_id}/communications`),
                api.get(`/work-orders/${wo_id}/attachments`),
                api.get(`/work-orders/${wo_id}/timeline`)
            ]);
            setWo(woRes.data);
            setQuotes(quotesRes.data);
            setCommunications(commRes.data);
            setAttachments(attachRes.data);
            setTimeline(timelineRes.data);

            if (canManage) {
                const [contRes, invRes] = await Promise.all([
                    api.get('/contractors'),
                    api.get(`/work-orders/${wo_id}/invoices`)
                ]);
                setContractors(contRes.data);
                setInvoices(invRes.data);
            }
        } catch (error) {
            console.error('Failed to fetch WO data:', error);
            toast.error('Failed to load work order details');
        } finally {
            setLoading(false);
        }
    }, [api, wo_id, canManage]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);
    /**
     * @generated FunctionHeader
     * Function: handleSelectQuote
     * Path: frontend/src/pages/dashboard/WorkOrderDetailsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSelectQuote = async (quoteId) => {
        try {
            await api.put(`/work-orders/quotes/${quoteId}/select`);
            toast.success('Quote selected');
            fetchData();
        } catch (error) {
            toast.error('Failed to select quote');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCompletionSubmit
     * Path: frontend/src/pages/dashboard/WorkOrderDetailsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCompletionSubmit = async (e) => {
        e.preventDefault();
        if (!wo?.assigned_vendor) {
            toast.error('No vendor assigned to this work order');
            return;
        }
        setSubmitting(true);
        try {
            await api.post(`/work-orders/${wo_id}/invoices`, {
                ...completionForm,
                vendor_id: wo.assigned_vendor,
                amount: parseFloat(completionForm.amount),
                gst: parseFloat(completionForm.gst || 0)
            });
            toast.success('Completion report and invoice submitted');
            setCompletionDialogOpen(false);
            fetchData();
        } catch (error) {
            toast.error('Failed to submit completion report');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleQuoteSubmit
     * Path: frontend/src/pages/dashboard/WorkOrderDetailsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleQuoteSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post(`/work-orders/${wo_id}/quotes`, {
                ...quoteForm,
                amount: parseFloat(quoteForm.amount)
            });
            toast.success('Quote added');
            setQuoteDialogOpen(false);
            setQuoteForm({vendor_id: '', amount: '', description: ''});
            fetchData();
        } catch (error) {
            toast.error('Failed to add quote');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleApprove
     * Path: frontend/src/pages/dashboard/WorkOrderDetailsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleApprove = async (decision) => {
        try {
            await api.post(`/work-orders/${wo_id}/approvals`, {
                work_order_id: wo_id,
                decision,
                comment: ''
            });
            toast.success(`Work order ${decision}d`);
            fetchData();
        } catch (error) {
            toast.error('Failed to submit approval');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleManualStatus
     * Path: frontend/src/pages/dashboard/WorkOrderDetailsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleManualStatus = async (status) => {
        try {
            await api.put(`/work-orders/${wo_id}`, {status});
            toast.success(`Status updated to ${status}`);
            fetchData();
        } catch (error) {
            toast.error('Failed to update status');
        }
    };

    if (loading) return <div className="flex items-center justify-center min-h-[400px]"><Loader2
        className="h-8 w-8 animate-spin"/></div>;
    if (!wo) return <div className="text-center py-12">Work Order not found</div>;

    return (
        <div className="space-y-6 pb-24 lg:pb-0">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <Link href="/maintenance" data-testid="btn-back-to-maintenance"
                          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors mb-2">
                        <ArrowLeft className="h-4 w-4"/>
                        Back to Maintenance Tracker
                    </Link>
                    <div className="flex items-center gap-2 text-muted-foreground mb-1">
                        <Wrench className="h-4 w-4"/>
                        <span className="text-sm font-medium">Work Order #{wo.id.slice(0, 8)}</span>
                    </div>
                    <h1 className="text-2xl font-bold">{wo.title}</h1>
                </div>
                <div className="flex gap-2">
                    {isECMember && (
                        <>
                            {wo.status === 'pending_approval' && (
                                <>
                                    <Button size="sm" variant="outline"
                                            className="text-green-600 hover:text-green-700 border-green-200"
                                            onClick={() => handleApprove('approve')}>
                                        <CheckCircle2 className="h-4 w-4 mr-2"/> Approve
                                    </Button>
                                    <Button size="sm" variant="outline"
                                            className="text-red-600 hover:text-red-700 border-red-200"
                                            onClick={() => handleApprove('reject')}>
                                        <XCircle className="h-4 w-4 mr-2"/> Reject
                                    </Button>
                                </>
                            )}
                            {wo.priority === 'urgent' && !wo.is_emergency && (
                                <Button size="sm" variant="destructive" onClick={() => setShowOverride(true)}>
                                    Emergency Override
                                </Button>
                            )}
                        </>
                    )}
                    <Badge variant="outline" className="capitalize">{wo.status.replace('_', ' ')}</Badge>
                    {wo.is_emergency && <Badge variant="destructive">Emergency</Badge>}
                    <Badge variant={wo.priority === 'urgent' ? 'destructive' : 'secondary'}>{wo.priority}</Badge>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Left Column: Details & Tabs */}
                <div className="lg:col-span-2 space-y-6">
                    <Card>
                        <CardHeader>
                            <CardTitle>Description</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <p className="whitespace-pre-wrap text-muted-foreground">{wo.description}</p>
                            <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
                                <div>
                                    <Label className="text-xs uppercase text-muted-foreground">Supplier Type</Label>
                                    <p className="capitalize">{wo.supplier_type.replace('_', ' ')}</p>
                                </div>
                                <div>
                                    <Label className="text-xs uppercase text-muted-foreground">Lot / Unit</Label>
                                    <p>{wo.lot_number || 'Common Area'}</p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Tabs defaultValue="quotes">
                        <TabsList className="w-full justify-start border-b rounded-none bg-transparent h-auto p-0">
                            <TabsTrigger value="quotes"
                                         className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent px-4 py-2">Quotes</TabsTrigger>
                            <TabsTrigger value="communications"
                                         className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent px-4 py-2">Communications</TabsTrigger>
                            <TabsTrigger value="attachments"
                                         className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent px-4 py-2">Attachments</TabsTrigger>
                            {canManage && <TabsTrigger value="invoices"
                                                       className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent px-4 py-2">Invoices</TabsTrigger>}
                        </TabsList>

                        <TabsContent value="quotes" className="pt-4">
                            <Card>
                                <CardHeader className="flex flex-row items-center justify-between">
                                    <div>
                                        <CardTitle>Vendor Quotes</CardTitle>
                                        <CardDescription>Comparison of submitted quotes</CardDescription>
                                    </div>
                                    {canManage && (
                                        <Dialog open={quoteDialogOpen} onOpenChange={setQuoteDialogOpen}>
                                            <DialogTrigger asChild>
                                                <Button size="sm"><Plus className="h-4 w-4 mr-2"/>Add Quote</Button>
                                            </DialogTrigger>
                                            <DialogContent>
                                                <DialogHeader>
                                                    <DialogTitle>Add Vendor Quote</DialogTitle>
                                                </DialogHeader>
                                                <form onSubmit={handleQuoteSubmit} className="space-y-4">
                                                    <div className="space-y-2">
                                                        <Label>Contractor</Label>
                                                        <select
                                                            className="w-full p-2 border rounded-md"
                                                            value={quoteForm.vendor_id}
                                                            onChange={(e) => setQuoteForm(prev => ( {
                                                                ...prev,
                                                                vendor_id: e.target.value
                                                            } ))}
                                                            required
                                                        >
                                                            <option value="">Select a contractor</option>
                                                            {contractors.map(c => <option key={c.id}
                                                                                          value={c.id}>{c.name}</option>)}
                                                        </select>
                                                    </div>
                                                    <div className="space-y-2">
                                                        <Label>Amount (AUD)</Label>
                                                        <Input type="number" step="0.01" value={quoteForm.amount}
                                                               onChange={(e) => setQuoteForm(prev => ( {
                                                                   ...prev,
                                                                   amount: e.target.value
                                                               } ))} required/>
                                                    </div>
                                                    <div className="space-y-2">
                                                        <Label>Description</Label>
                                                        <Textarea value={quoteForm.description}
                                                                  onChange={(e) => setQuoteForm(prev => ( {
                                                                      ...prev,
                                                                      description: e.target.value
                                                                  } ))} required/>
                                                    </div>
                                                    <Button type="submit" className="w-full" disabled={submitting}>Submit
                                                        Quote</Button>
                                                </form>
                                            </DialogContent>
                                        </Dialog>
                                    )}
                                </CardHeader>
                                <CardContent>
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Vendor</TableHead>
                                                <TableHead>Amount</TableHead>
                                                <TableHead>Rating</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead></TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {quotes.map(q => (
                                                <TableRow key={q.id} className={q.selected ? 'bg-green-50/50' : ''}>
                                                    <TableCell className="font-medium">{q.vendor_name}</TableCell>
                                                    <TableCell>{formatCurrency(q.amount)}</TableCell>
                                                    <TableCell>
                                                        <div className="flex items-center gap-1">
                                                            <Star
                                                                className={`h-3 w-3 ${q.vendor_rating ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground'}`}/>
                                                            <span>{q.vendor_rating || 'N/A'}</span>
                                                        </div>
                                                    </TableCell>
                                                    <TableCell>
                                                        {q.selected ? <Badge
                                                                className="bg-green-100 text-green-800 hover:bg-green-100">Selected</Badge> :
                                                            <Badge variant="outline"
                                                                   className="capitalize">{q.status}</Badge>}
                                                    </TableCell>
                                                    <TableCell>
                                                        {canManage && !q.selected && ( wo.status === 'awaiting_quotes' || wo.status === 'new' ) && (
                                                            <Button size="sm"
                                                                    onClick={() => handleSelectQuote(q.id)}>Select</Button>
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                            {quotes.length === 0 && (
                                                <TableRow>
                                                    <TableCell colSpan={5}
                                                               className="text-center py-8 text-muted-foreground">
                                                        No quotes submitted yet
                                                    </TableCell>
                                                </TableRow>
                                            )}
                                        </TableBody>
                                    </Table>
                                </CardContent>
                            </Card>
                        </TabsContent>

                        <TabsContent value="communications" className="pt-4">
                            <Card>
                                <CardHeader>
                                    <CardTitle>Communication Logs</CardTitle>
                                    <CardDescription>Email and internal message history</CardDescription>
                                </CardHeader>
                                <CardContent>
                                    <div className="space-y-4">
                                        {communications.map(comm => (
                                            <div key={comm.id} className="p-3 border rounded-lg bg-muted/30">
                                                <div className="flex items-center justify-between mb-2">
                                                    <div className="flex items-center gap-2">
                                                        {comm.source_type === 'email' ?
                                                            <Mail className="h-4 w-4 text-blue-500"/> :
                                                            <MessageSquare className="h-4 w-4 text-green-500"/>}
                                                        <span
                                                            className="text-sm font-semibold">{comm.sender_name}</span>
                                                    </div>
                                                    <span
                                                        className="text-xs text-muted-foreground">{formatDate(comm.timestamp)}</span>
                                                </div>
                                                <p className="text-sm font-medium mb-1">{comm.subject}</p>
                                                <p className="text-sm text-muted-foreground whitespace-pre-wrap">{comm.message}</p>
                                            </div>
                                        ))}
                                        {communications.length === 0 && (
                                            <div className="text-center py-8 text-muted-foreground">No communication
                                                logs found</div>
                                        )}
                                    </div>
                                </CardContent>
                            </Card>
                        </TabsContent>

                        <TabsContent value="attachments" className="pt-4">
                            <Card>
                                <CardHeader>
                                    <CardTitle>Attachments</CardTitle>
                                    <CardDescription>Photos, reports, and compliance documents</CardDescription>
                                </CardHeader>
                                <CardContent>
                                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                                        {attachments.map(at => (
                                            <div key={at.id}
                                                 className="p-3 border rounded-lg hover:bg-muted/30 transition-colors group relative">
                                                <div
                                                    className="aspect-square bg-muted rounded-md flex items-center justify-center mb-2 overflow-hidden">
                                                    {at.file_type.startsWith('image') ? (
                                                        <img src={at.file_url} alt={at.attachment_type}
                                                             className="w-full h-full object-cover"/>
                                                    ) : (
                                                        <FileText className="h-8 w-8 text-muted-foreground"/>
                                                    )}
                                                </div>
                                                <p className="text-xs font-medium truncate capitalize">{at.attachment_type.replace('_', ' ')}</p>
                                                <p className="text-[10px] text-muted-foreground">{formatDate(at.created_at)}</p>
                                                <a href={at.file_url} target="_blank" rel="noreferrer"
                                                   className="absolute inset-0 opacity-0 group-hover:opacity-100 flex items-center justify-center bg-black/10 rounded-lg transition-opacity">
                                                    <Button size="sm" variant="secondary">View</Button>
                                                </a>
                                            </div>
                                        ))}
                                        {attachments.length === 0 && (
                                            <div
                                                className="col-span-full text-center py-8 text-muted-foreground font-medium border-2 border-dashed rounded-lg">
                                                No attachments uploaded
                                            </div>
                                        )}
                                    </div>
                                </CardContent>
                            </Card>
                        </TabsContent>

                        <TabsContent value="invoices" className="pt-4">
                            <Card>
                                <CardHeader className="flex flex-row items-center justify-between">
                                    <div>
                                        <CardTitle>Invoices & Completion Reports</CardTitle>
                                        <CardDescription>Submitted invoices for this work order</CardDescription>
                                    </div>
                                    {canManage && ( wo.status === 'assigned' || wo.status === 'in_progress' || wo.status === 'awaiting_invoice' ) && (
                                        <Dialog open={completionDialogOpen} onOpenChange={setCompletionDialogOpen}>
                                            <DialogTrigger asChild>
                                                <Button size="sm"><CheckCircle2 className="h-4 w-4 mr-2"/>Submit
                                                    Completion</Button>
                                            </DialogTrigger>
                                            <DialogContent className="max-w-md">
                                                <DialogHeader>
                                                    <DialogTitle>Submit Job Completion Report</DialogTitle>
                                                </DialogHeader>
                                                <form onSubmit={handleCompletionSubmit} className="space-y-4">
                                                    <div className="grid grid-cols-2 gap-4">
                                                        <div className="space-y-2">
                                                            <Label>Invoice Number</Label>
                                                            <Input value={completionForm.invoice_number}
                                                                   onChange={(e) => setCompletionForm(prev => ( {
                                                                       ...prev,
                                                                       invoice_number: e.target.value
                                                                   } ))} required/>
                                                        </div>
                                                        <div className="space-y-2">
                                                            <Label>Total Amount (AUD)</Label>
                                                            <Input type="number" step="0.01"
                                                                   value={completionForm.amount}
                                                                   onChange={(e) => setCompletionForm(prev => ( {
                                                                       ...prev,
                                                                       amount: e.target.value
                                                                   } ))} required/>
                                                        </div>
                                                    </div>
                                                    <div className="space-y-2">
                                                        <Label>Notes / Description of Work</Label>
                                                        <Textarea value={completionForm.notes}
                                                                  onChange={(e) => setCompletionForm(prev => ( {
                                                                      ...prev,
                                                                      notes: e.target.value
                                                                  } ))} rows={3}/>
                                                    </div>
                                                    <div className="space-y-2">
                                                        <Label>Completion Photos (URLs)</Label>
                                                        <Input placeholder="Add photo URL..." onKeyDown={(e) => {
                                                            if (e.key === 'Enter') {
                                                                e.preventDefault();
                                                                setCompletionForm(prev => ( {
                                                                    ...prev,
                                                                    completion_photos: [...prev.completion_photos, e.target.value]
                                                                } ));
                                                                e.target.value = '';
                                                            }
                                                        }}/>
                                                        <div className="flex gap-1 flex-wrap mt-1">
                                                            {completionForm.completion_photos.map((p, i) => <Badge
                                                                key={i} variant="secondary">Photo {i + 1}</Badge>)}
                                                        </div>
                                                    </div>
                                                    <Button type="submit" className="w-full" disabled={submitting}>Submit
                                                        Invoice & Report</Button>
                                                </form>
                                            </DialogContent>
                                        </Dialog>
                                    )}
                                </CardHeader>
                                <CardContent>
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Invoice #</TableHead>
                                                <TableHead>Amount</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead>Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {invoices.map(inv => (
                                                <TableRow key={inv.id}>
                                                    <TableCell className="font-medium">{inv.invoice_number}</TableCell>
                                                    <TableCell>{formatCurrency(inv.amount)}</TableCell>
                                                    <TableCell>
                                                        <Badge
                                                            variant={inv.payment_status === 'paid' ? 'default' : 'outline'}>{inv.payment_status}</Badge>
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex items-center gap-2">
                                                            {inv.approval_status === 'submitted' && (
                                                                <Button size="sm" asChild>
                                                                    <Link href="/requests/my-approvals">Review</Link>
                                                                </Button>
                                                            )}
                                                            <DropdownMenu>
                                                                <DropdownMenuTrigger asChild><Button variant="ghost"
                                                                                                     size="sm">Evidence</Button></DropdownMenuTrigger>
                                                                <DropdownMenuContent>
                                                                    <DropdownMenuLabel>Completion
                                                                        Evidence</DropdownMenuLabel>
                                                                    {inv.completion_photos?.map((p, i) => (
                                                                        <DropdownMenuItem key={i}
                                                                                          onClick={() => window.open(p, '_blank')}>View
                                                                            Photo {i + 1}</DropdownMenuItem>
                                                                    ))}
                                                                    {( !inv.completion_photos || inv.completion_photos.length === 0 ) &&
                                                                        <DropdownMenuItem disabled>No photos
                                                                            uploaded</DropdownMenuItem>}
                                                                </DropdownMenuContent>
                                                            </DropdownMenu>
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                            {invoices.length === 0 && (
                                                <TableRow>
                                                    <TableCell colSpan={4}
                                                               className="text-center py-8 text-muted-foreground">
                                                        No invoices submitted yet
                                                    </TableCell>
                                                </TableRow>
                                            )}
                                        </TableBody>
                                    </Table>
                                </CardContent>
                            </Card>
                        </TabsContent>
                    </Tabs>
                </div>

                {/* Emergency Override Dialog */}
                <Dialog open={showOverride} onOpenChange={setShowOverride}>
                    <DialogContent>
                        <DialogHeader>
                            <DialogTitle>Emergency Override</DialogTitle>
                            <DialogDescription>
                                Activating emergency override will bypass normal approval thresholds for this work order
                                and associated invoices. This action will be audited.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="flex justify-end gap-2 mt-4">
                            <Button variant="outline" onClick={() => setShowOverride(false)}>Cancel</Button>
                            <Button variant="destructive" onClick={async () => {
                                await api.put(`/work-orders/${wo_id}`, {emergency_override: true});
                                toast.success('Emergency override activated');
                                setShowOverride(false);
                                fetchData();
                            }}>Activate Override</Button>
                        </div>
                    </DialogContent>
                </Dialog>

                {/* Mobile Sticky Approval Footer */}
                {isECMember && wo.status === 'pending_approval' && (
                    <div
                        className="lg:hidden fixed bottom-0 left-0 right-0 bg-background/80 backdrop-blur-md border-t p-4 z-50 flex gap-3 shadow-2xl">
                        <Button className="flex-1 bg-green-600 hover:bg-green-700 h-12"
                                onClick={() => handleApprove('approve')}>
                            <CheckCircle2 className="mr-2 h-5 w-5"/> Approve
                        </Button>
                        <Button variant="destructive" className="flex-1 h-12" onClick={() => handleApprove('reject')}>
                            <XCircle className="mr-2 h-5 w-5"/> Reject
                        </Button>
                    </div>
                )}

                {/* Right Column: Timeline & Sidebar Info */}
                <div className="space-y-6">
                    <Card>
                        <CardHeader>
                            <CardTitle>Timeline</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <Timeline events={timeline}/>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle>Vendor Information</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {wo.assigned_vendor ? (
                                <div>
                                    <p className="font-semibold">{wo.vendor_name}</p>
                                    <p className="text-sm text-muted-foreground mt-1">Status: Assigned</p>
                                </div>
                            ) : (
                                <div className="text-center py-4 text-muted-foreground flex flex-col items-center">
                                    <User className="h-8 w-8 mb-2 opacity-20"/>
                                    <p className="text-sm">No vendor assigned</p>
                                </div>
                            )}
                            {canManage && wo.status === 'approved' && wo.assigned_vendor && (
                                <Button className="w-full mt-2" onClick={() => handleManualStatus('assigned')}>
                                    Issue Work Order to Vendor
                                </Button>
                            )}
                            {canManage && wo.status === 'assigned' && (
                                <Button className="w-full mt-2" onClick={() => handleManualStatus('in_progress')}>
                                    Confirm Work Commenced
                                </Button>
                            )}
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
};

export default WorkOrderDetailsPage;
