// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { AlertTriangle, DollarSign, Loader2, Plus, RefreshCw, User } from 'lucide-react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Badge } from '../../../components/ui/badge';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Textarea } from '../../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '../../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../../components/ui/table';
import { formatCurrency, formatDate } from '../../../lib/utils';

const ALLOWED_ROLES = ['owner', 'real_estate_agent', 'super_admin', 'strata_manager', 'ec_member'];
const PAYMENT_METHODS = ['bank_transfer', 'cash', 'cheque', 'direct_debit', 'card', 'other'];

const ARREARS_STAGES = {
    1: {label: 'Reminder', color: 'bg-yellow-100 text-yellow-800'},
    2: {label: 'Warning', color: 'bg-orange-100 text-orange-800'},
    3: {label: 'Breach', color: 'bg-red-100 text-red-800'},
    4: {label: 'Tribunal', color: 'bg-red-900 text-white'},
};
/**
 * @generated FunctionHeader
 * Function: getArrearsStage
 * Path: frontend/src/pages/dashboardhub/TenancyLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getArrearsStage(days) {
    if (days >= 30) return 4;
    if (days >= 21) return 3;
    if (days >= 14) return 2;
    if (days >= 7) return 1;
    return 0;
}
/**
 * @generated FunctionHeader
 * Function: TenancyLedgerPage
 * Path: frontend/src/pages/dashboardhub/TenancyLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const TenancyLedgerPage = () => {
    const {user, api} = useAuth();
    const router = useRouter();
    const [properties, setProperties] = useState([]);
    const [selectedPropertyId, setSelectedPropertyId] = useState('');
    const [tenancy, setTenancy] = useState(null);
    const [ledger, setLedger] = useState([]);
    const [loading, setLoading] = useState(true);
    const [tenancyLoading, setTenancyLoading] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [paymentOpen, setPaymentOpen] = useState(false);
    const [addTenancyOpen, setAddTenancyOpen] = useState(false);
    const [sortOrder, setSortOrder] = useState('desc');
    const [page, setPage] = useState(1);
    const PAGE_SIZE = 10;

    const [paymentForm, setPaymentForm] = useState({
        amount: '', date: new Date().toISOString().split('T')[ 0 ],
        method: 'bank_transfer', reference: '', notes: ''
    });

    const [tenancyForm, setTenancyForm] = useState({
        tenant_name: '', tenant_email: '', tenant_phone: '',
        lease_start: '', lease_end: '', rent_amount: '', bond_amount: '', notes: ''
    });

    const canAccess = user && ALLOWED_ROLES.includes(user.role);

    const fetchProperties = useCallback(async () => {
        try {
            const res = await api.get('/owner-hub/properties');
            const props = res.data || [];
            setProperties(props);
            if (props.length > 0 && !selectedPropertyId) {
                setSelectedPropertyId(props[ 0 ].id);
            }
        } catch {
            toast.error('Failed to load properties');
        } finally {
            setLoading(false);
        }
    }, [api, selectedPropertyId]);

    const fetchTenancyData = useCallback(async (propId) => {
        if (!propId) return;
        setTenancyLoading(true);
        try {
            const [tenancyRes, ledgerRes] = await Promise.all([
                api.get(`/owner-hub/properties/${propId}/tenancy`),
                api.get(`/owner-hub/properties/${propId}/ledger`),
            ]);
            setTenancy(tenancyRes.data || null);
            setLedger(ledgerRes.data || []);
        } catch {
            setTenancy(null);
            setLedger([]);
        } finally {
            setTenancyLoading(false);
        }
    }, [api]);

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchProperties();
    }, [canAccess, fetchProperties, router]);

    useEffect(() => {
        if (selectedPropertyId) {
            setPage(1);
            fetchTenancyData(selectedPropertyId);
        }
    }, [selectedPropertyId, fetchTenancyData]);
    /**
     * @generated FunctionHeader
     * Function: handlePayment
     * Path: frontend/src/pages/dashboardhub/TenancyLedgerPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePayment = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post(`/owner-hub/tenancies/${tenancy.id}/payments`, paymentForm);
            toast.success('Payment recorded');
            setPaymentOpen(false);
            fetchTenancyData(selectedPropertyId);
        } catch {
            toast.error('Failed to record payment');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleAddTenancy
     * Path: frontend/src/pages/dashboardhub/TenancyLedgerPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAddTenancy = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post(`/owner-hub/properties/${selectedPropertyId}/tenancy`, tenancyForm);
            toast.success('Tenancy created');
            setAddTenancyOpen(false);
            fetchTenancyData(selectedPropertyId);
        } catch {
            toast.error('Failed to create tenancy');
        } finally {
            setSubmitting(false);
        }
    };

    const selectedProperty = properties.find(p => p.id === selectedPropertyId);
    const arrearsStage = tenancy?.arrears_days > 0 ? getArrearsStage(tenancy.arrears_days) : 0;
    const stageConfig = ARREARS_STAGES[ arrearsStage ];

    const sortedLedger = [...ledger].sort((a, b) => {
        const diff = new Date(b.date) - new Date(a.date);
        return sortOrder === 'desc' ? diff : -diff;
    });
    const pagedLedger = sortedLedger.slice(( page - 1 ) * PAGE_SIZE, page * PAGE_SIZE);
    const totalPages = Math.ceil(sortedLedger.length / PAGE_SIZE);

    if (!canAccess) return null;

    return (
        <div className="space-y-6 p-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Tenancy Ledger</h1>
                    <p className="text-sm text-gray-500">View rent history and manage tenancies</p>
                </div>
                <Button variant="outline" size="sm" onClick={() => fetchTenancyData(selectedPropertyId)}>
                    <RefreshCw className="h-4 w-4 mr-2"/>Refresh
                </Button>
            </div>

            {/* Property Selector */}
            <div className="flex items-center gap-3">
                <Label className="shrink-0 text-sm font-medium">Select Property:</Label>
                {loading ? (
                    <Loader2 className="h-4 w-4 animate-spin text-gray-400"/>
                ) : (
                    <Select value={selectedPropertyId} onValueChange={setSelectedPropertyId}>
                        <SelectTrigger className="w-72">
                            <SelectValue placeholder="Choose a property"/>
                        </SelectTrigger>
                        <SelectContent>
                            {properties.map(p => (
                                <SelectItem key={p.id} value={p.id}>
                                    {p.address} — {p.suburb}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                )}
            </div>

            {tenancyLoading ? (
                <div className="flex justify-center py-16"><Loader2 className="h-8 w-8 animate-spin text-gray-400"/>
                </div>
            ) : !tenancy && selectedProperty ? (
                <Card className="border-dashed border-2">
                    <CardContent className="pt-8 pb-8 text-center">
                        <User className="mx-auto h-12 w-12 text-gray-300 mb-3"/>
                        <p className="text-gray-500 mb-4">No active tenancy for this property.</p>
                        <Button onClick={() => setAddTenancyOpen(true)}><Plus className="mr-2 h-4 w-4"/>Add
                            Tenancy</Button>
                    </CardContent>
                </Card>
            ) : tenancy ? (
                <>
                    {/* Arrears Banner */}
                    {tenancy.arrears_days > 0 && stageConfig && (
                        <div className={`flex items-center gap-3 rounded-lg p-4 ${stageConfig.color}`}>
                            <AlertTriangle className="h-5 w-5 shrink-0"/>
                            <div>
                                <p className="font-semibold">
                                    Rent overdue by {tenancy.arrears_days} day{tenancy.arrears_days > 1 ? 's' : ''} —
                                    Stage: {stageConfig.label}
                                </p>
                                <p className="text-sm opacity-80">Outstanding
                                    amount: {formatCurrency(tenancy.arrears_amount)}</p>
                            </div>
                        </div>
                    )}

                    {/* Active Tenancy Card */}
                    <Card>
                        <CardHeader>
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-base">Active Tenancy</CardTitle>
                                <div className="flex gap-2">
                                    <Badge
                                        className={tenancy.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}>
                                        {tenancy.status || 'active'}
                                    </Badge>
                                    <Button size="sm" onClick={() => setPaymentOpen(true)}>
                                        <DollarSign className="mr-2 h-4 w-4"/>Record Payment
                                    </Button>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                <div>
                                    <p className="text-gray-500">Tenant</p>
                                    <p className="font-medium">{tenancy.tenant_name || '—'}</p>
                                </div>
                                <div>
                                    <p className="text-gray-500">Email</p>
                                    <p className="font-medium">{tenancy.tenant_email || '—'}</p>
                                </div>
                                <div>
                                    <p className="text-gray-500">Phone</p>
                                    <p className="font-medium">{tenancy.tenant_phone || '—'}</p>
                                </div>
                                <div>
                                    <p className="text-gray-500">Weekly Rent</p>
                                    <p className="font-medium">{formatCurrency(tenancy.rent_amount)}</p>
                                </div>
                                <div>
                                    <p className="text-gray-500">Lease Start</p>
                                    <p className="font-medium">{formatDate(tenancy.lease_start)}</p>
                                </div>
                                <div>
                                    <p className="text-gray-500">Lease End</p>
                                    <p className="font-medium">{formatDate(tenancy.lease_end)}</p>
                                </div>
                                <div>
                                    <p className="text-gray-500">Bond</p>
                                    <p className="font-medium">{formatCurrency(tenancy.bond_amount)}</p>
                                </div>
                                <div>
                                    <p className="text-gray-500">Bond Status</p>
                                    <p className="font-medium capitalize">{tenancy.bond_status || '—'}</p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Rent Ledger */}
                    <Card>
                        <CardHeader>
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-base">Rent Ledger</CardTitle>
                                <Button variant="outline" size="sm"
                                        onClick={() => setSortOrder(s => s === 'desc' ? 'asc' : 'desc')}>
                                    Date {sortOrder === 'desc' ? '↓' : '↑'}
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent className="p-0">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Date</TableHead>
                                        <TableHead>Type</TableHead>
                                        <TableHead>Amount</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Method</TableHead>
                                        <TableHead>Reference</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {pagedLedger.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={6} className="text-center text-gray-400 py-8">
                                                No ledger entries yet.
                                            </TableCell>
                                        </TableRow>
                                    ) : pagedLedger.map((entry, i) => (
                                        <TableRow key={i}>
                                            <TableCell>{formatDate(entry.date)}</TableCell>
                                            <TableCell
                                                className="capitalize">{entry.type?.replace('_', ' ') || '—'}</TableCell>
                                            <TableCell
                                                className={entry.type === 'payment' ? 'text-green-700 font-medium' : ''}>
                                                {formatCurrency(entry.amount)}
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant="outline"
                                                       className="capitalize text-xs">{entry.status}</Badge>
                                            </TableCell>
                                            <TableCell
                                                className="capitalize">{entry.method?.replace('_', ' ') || '—'}</TableCell>
                                            <TableCell
                                                className="text-gray-500 text-xs">{entry.reference || '—'}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                            {totalPages > 1 && (
                                <div className="flex items-center justify-between p-4 border-t">
                                    <p className="text-sm text-gray-500">
                                        Showing {( page - 1 ) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, sortedLedger.length)} of {sortedLedger.length}
                                    </p>
                                    <div className="flex gap-2">
                                        <Button variant="outline" size="sm" disabled={page === 1}
                                                onClick={() => setPage(p => p - 1)}>Previous</Button>
                                        <Button variant="outline" size="sm" disabled={page === totalPages}
                                                onClick={() => setPage(p => p + 1)}>Next</Button>
                                    </div>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </>
            ) : null}

            {/* Record Payment Dialog */}
            <Dialog open={paymentOpen} onOpenChange={setPaymentOpen}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Record Payment</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handlePayment} className="space-y-4">
                        <div className="space-y-2">
                            <Label>Amount ($) *</Label>
                            <Input type="number" min="0" step="0.01" value={paymentForm.amount}
                                   onChange={e => setPaymentForm(p => ( {...p, amount: e.target.value} ))} required/>
                        </div>
                        <div className="space-y-2">
                            <Label>Date *</Label>
                            <Input type="date" value={paymentForm.date}
                                   onChange={e => setPaymentForm(p => ( {...p, date: e.target.value} ))} required/>
                        </div>
                        <div className="space-y-2">
                            <Label>Payment Method</Label>
                            <Select value={paymentForm.method}
                                    onValueChange={v => setPaymentForm(p => ( {...p, method: v} ))}>
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    {PAYMENT_METHODS.map(m => <SelectItem key={m}
                                                                          value={m}>{m.replace('_', ' ')}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>Reference</Label>
                            <Input value={paymentForm.reference}
                                   onChange={e => setPaymentForm(p => ( {...p, reference: e.target.value} ))}/>
                        </div>
                        <div className="space-y-2">
                            <Label>Notes</Label>
                            <Textarea value={paymentForm.notes}
                                      onChange={e => setPaymentForm(p => ( {...p, notes: e.target.value} ))} rows={2}/>
                        </div>
                        <DialogFooter>
                            <Button type="submit" className="w-full" disabled={submitting}>
                                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}Record Payment
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            {/* Add Tenancy Dialog */}
            <Dialog open={addTenancyOpen} onOpenChange={setAddTenancyOpen}>
                <DialogContent className="max-w-md max-h-[90vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Add Tenancy</DialogTitle>
                        <DialogDescription>Create a tenancy for {selectedProperty?.address}</DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleAddTenancy} className="space-y-4">
                        <div className="space-y-2"><Label>Tenant Name *</Label>
                            <Input value={tenancyForm.tenant_name}
                                   onChange={e => setTenancyForm(p => ( {...p, tenant_name: e.target.value} ))}
                                   required/>
                        </div>
                        <div className="space-y-2"><Label>Email</Label>
                            <Input type="email" value={tenancyForm.tenant_email}
                                   onChange={e => setTenancyForm(p => ( {...p, tenant_email: e.target.value} ))}/>
                        </div>
                        <div className="space-y-2"><Label>Phone</Label>
                            <Input value={tenancyForm.tenant_phone}
                                   onChange={e => setTenancyForm(p => ( {...p, tenant_phone: e.target.value} ))}/>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-2"><Label>Lease Start</Label>
                                <Input type="date" value={tenancyForm.lease_start}
                                       onChange={e => setTenancyForm(p => ( {...p, lease_start: e.target.value} ))}/>
                            </div>
                            <div className="space-y-2"><Label>Lease End</Label>
                                <Input type="date" value={tenancyForm.lease_end}
                                       onChange={e => setTenancyForm(p => ( {...p, lease_end: e.target.value} ))}/>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-2"><Label>Weekly Rent ($)</Label>
                                <Input type="number" value={tenancyForm.rent_amount}
                                       onChange={e => setTenancyForm(p => ( {...p, rent_amount: e.target.value} ))}/>
                            </div>
                            <div className="space-y-2"><Label>Bond ($)</Label>
                                <Input type="number" value={tenancyForm.bond_amount}
                                       onChange={e => setTenancyForm(p => ( {...p, bond_amount: e.target.value} ))}/>
                            </div>
                        </div>
                        <DialogFooter>
                            <Button type="submit" className="w-full" disabled={submitting}>
                                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}Create Tenancy
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default TenancyLedgerPage;
