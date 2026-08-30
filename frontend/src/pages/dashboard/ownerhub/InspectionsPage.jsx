// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { AlertTriangle, CheckCircle2, ClipboardList, Clock, Home, Loader2, Plus, RefreshCw } from 'lucide-react';
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
import { formatDate } from '../../../lib/utils';

const ALLOWED_ROLES = ['owner', 'real_estate_agent', 'super_admin', 'strata_manager', 'admin_staff', 'ec_member'];

const INSPECTION_TYPES = [
    {value: 'routine', label: 'Routine'},
    {value: 'ingoing', label: 'Ingoing'},
    {value: 'outgoing', label: 'Outgoing'},
    {value: 'maintenance', label: 'Maintenance'},
    {value: 'special', label: 'Special'},
];

const INSPECTION_STATUSES = [
    {value: 'scheduled', label: 'Scheduled'},
    {value: 'completed', label: 'Completed'},
    {value: 'cancelled', label: 'Cancelled'},
    {value: 'overdue', label: 'Overdue'},
];
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboardhub/InspectionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function statusBadge(status) {
    const map = {
        scheduled: 'bg-blue-100 text-blue-800',
        completed: 'bg-green-100 text-green-800',
        cancelled: 'bg-slate-100 text-slate-600',
        overdue: 'bg-red-100 text-red-800',
    };
    return (
        <Badge className={`${map[ status ] || 'bg-slate-100 text-slate-600'} border-0 text-xs`}>
            {status.charAt(0).toUpperCase() + status.slice(1)}
        </Badge>
    );
}
/**
 * @generated FunctionHeader
 * Function: typeBadge
 * Path: frontend/src/pages/dashboardhub/InspectionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function typeBadge(type) {
    const map = {
        routine: 'bg-violet-100 text-violet-800',
        ingoing: 'bg-teal-100 text-teal-800',
        outgoing: 'bg-amber-100 text-amber-800',
        maintenance: 'bg-orange-100 text-orange-800',
        special: 'bg-rose-100 text-rose-800',
    };
    return (
        <Badge className={`${map[ type ] || 'bg-slate-100 text-slate-600'} border-0 text-xs`}>
            {INSPECTION_TYPES.find(t => t.value === type)?.label || type}
        </Badge>
    );
}

const EMPTY_FORM = {
    property_id: '',
    inspection_type: 'routine',
    scheduled_date: '',
    inspector_name: '',
    notes: '',
};
/**
 * @generated FunctionHeader
 * Function: InspectionsPage
 * Path: frontend/src/pages/dashboardhub/InspectionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InspectionsPage = () => {
    const {user, api} = useAuth();
    const router = useRouter();

    const [properties, setProperties] = useState([]);
    const [inspections, setInspections] = useState([]);
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [addOpen, setAddOpen] = useState(false);
    const [updateOpen, setUpdateOpen] = useState(false);
    const [selected, setSelected] = useState(null);
    const [form, setForm] = useState(EMPTY_FORM);
    const [filterStatus, setFilterStatus] = useState('all');
    const [filterProp, setFilterProp] = useState('all');

    useEffect(() => {
        if (!user) return;
        if (!ALLOWED_ROLES.includes(user.role)) {
            router.push('/dashboard');
            return;
        }
        loadData();
    }, [user]);

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [propsRes, inspsRes] = await Promise.allSettled([
                api.get('/owner-hub/properties'),
                api.get('/owner-hub/inspections'),
            ]);
            if (propsRes.status === 'fulfilled') setProperties(propsRes.value.data || []);
            if (inspsRes.status === 'fulfilled') {
                setInspections(inspsRes.value.data || []);
            } else {
                // Fallback: collect inspections per property
                const props = propsRes.status === 'fulfilled' ? ( propsRes.value.data || [] ) : [];
                const results = await Promise.allSettled(
                    props.map(p => api.get(`/owner-hub/properties/${p.id}/inspections`))
                );
                const all = results.flatMap(r => r.status === 'fulfilled' ? ( r.value.data || [] ) : []);
                setInspections(all);
            }
        } catch {
            toast.error('Failed to load inspections');
        } finally {
            setLoading(false);
        }
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: handleAdd
     * Path: frontend/src/pages/dashboardhub/InspectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAdd = async () => {
        if (!form.property_id || !form.scheduled_date) {
            toast.error('Property and scheduled date are required');
            return;
        }
        setSubmitting(true);
        try {
            await api.post(`/owner-hub/properties/${form.property_id}/inspections`, form);
            toast.success('Inspection scheduled');
            setAddOpen(false);
            setForm(EMPTY_FORM);
            loadData();
        } catch {
            toast.error('Failed to schedule inspection');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleUpdate
     * Path: frontend/src/pages/dashboardhub/InspectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpdate = async () => {
        if (!selected) return;
        setSubmitting(true);
        try {
            await api.put(`/owner-hub/inspections/${selected.id}`, form);
            toast.success('Inspection updated');
            setUpdateOpen(false);
            setSelected(null);
            loadData();
        } catch {
            toast.error('Failed to update inspection');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openUpdate
     * Path: frontend/src/pages/dashboardhub/InspectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openUpdate = (insp) => {
        setSelected(insp);
        setForm({
            property_id: insp.property_id || '',
            inspection_type: insp.inspection_type || 'routine',
            scheduled_date: insp.scheduled_date?.slice(0, 10) || '',
            inspector_name: insp.inspector_name || '',
            notes: insp.notes || '',
            status: insp.status || 'scheduled',
            outcome: insp.outcome || '',
        });
        setUpdateOpen(true);
    };

    const filtered = inspections.filter(i => {
        if (filterStatus !== 'all' && i.status !== filterStatus) return false;
        if (filterProp !== 'all' && i.property_id !== filterProp) return false;
        return true;
    });
    /**
     * @generated FunctionHeader
     * Function: propName
     * Path: frontend/src/pages/dashboardhub/InspectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const propName = (id) => {
        const p = properties.find(p => p.id === id);
        return p ? `${p.address}, ${p.suburb}` : id;
    };

    const stats = {
        total: inspections.length,
        scheduled: inspections.filter(i => i.status === 'scheduled').length,
        completed: inspections.filter(i => i.status === 'completed').length,
        overdue: inspections.filter(i => i.status === 'overdue').length,
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[60vh]">
                <Loader2 className="h-8 w-8 animate-spin text-teal-600"/>
            </div>
        );
    }

    return (
        <div className="container max-w-6xl py-8 space-y-8">
            {/* Header */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <ClipboardList className="h-8 w-8 text-teal-600"/>
                        Property Inspections
                    </h1>
                    <p className="text-slate-500 mt-1">Schedule and track routine, ingoing and outgoing inspections</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={loadData}>
                        <RefreshCw className="h-4 w-4 mr-1"/> Refresh
                    </Button>
                    <Button size="sm" className="bg-teal-600 hover:bg-teal-700" onClick={() => {
                        setForm(EMPTY_FORM);
                        setAddOpen(true);
                    }}>
                        <Plus className="h-4 w-4 mr-1"/> Schedule Inspection
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                    {label: 'Total', value: stats.total, icon: ClipboardList, color: 'text-slate-600'},
                    {label: 'Scheduled', value: stats.scheduled, icon: Clock, color: 'text-blue-600'},
                    {label: 'Completed', value: stats.completed, icon: CheckCircle2, color: 'text-green-600'},
                    {label: 'Overdue', value: stats.overdue, icon: AlertTriangle, color: 'text-red-600'},
                ].map(s => (
                    <Card key={s.label} className="rounded-2xl border-slate-100 shadow-sm">
                        <CardContent className="pt-5 pb-4 flex items-center gap-3">
                            <s.icon className={`h-8 w-8 ${s.color}`}/>
                            <div>
                                <p className="text-2xl font-black">{s.value}</p>
                                <p className="text-xs text-slate-500">{s.label}</p>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>

            {/* Filters */}
            <div className="flex flex-wrap gap-3">
                <Select value={filterStatus} onValueChange={setFilterStatus}>
                    <SelectTrigger className="w-40"><SelectValue placeholder="All statuses"/></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All statuses</SelectItem>
                        {INSPECTION_STATUSES.map(s => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
                    </SelectContent>
                </Select>
                <Select value={filterProp} onValueChange={setFilterProp}>
                    <SelectTrigger className="w-56"><SelectValue placeholder="All properties"/></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All properties</SelectItem>
                        {properties.map(p => (
                            <SelectItem key={p.id} value={p.id}>{p.address}, {p.suburb}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>

            {/* Table */}
            <Card className="rounded-2xl border-slate-100 shadow-sm">
                <CardHeader className="pb-2">
                    <CardTitle className="text-base font-semibold">Inspections ({filtered.length})</CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    {filtered.length === 0 ? (
                        <div className="text-center py-16 text-slate-400">
                            <ClipboardList className="h-10 w-10 mx-auto mb-3 opacity-30"/>
                            <p>No inspections found. Schedule your first one.</p>
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Property</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Scheduled</TableHead>
                                    <TableHead>Inspector</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Outcome</TableHead>
                                    <TableHead></TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.map(insp => (
                                    <TableRow key={insp.id} className="hover:bg-slate-50">
                                        <TableCell className="text-sm font-medium">
                                            <div className="flex items-center gap-2">
                                                <Home className="h-4 w-4 text-slate-400"/>
                                                <span
                                                    className="truncate max-w-[180px]">{propName(insp.property_id)}</span>
                                            </div>
                                        </TableCell>
                                        <TableCell>{typeBadge(insp.inspection_type)}</TableCell>
                                        <TableCell className="text-sm">{formatDate(insp.scheduled_date)}</TableCell>
                                        <TableCell
                                            className="text-sm text-slate-600">{insp.inspector_name || '—'}</TableCell>
                                        <TableCell>{statusBadge(insp.status)}</TableCell>
                                        <TableCell
                                            className="text-sm text-slate-500 max-w-[150px] truncate">{insp.outcome || '—'}</TableCell>
                                        <TableCell>
                                            <Button variant="ghost" size="sm"
                                                    onClick={() => openUpdate(insp)}>Edit</Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {/* Add Dialog */}
            <Dialog open={addOpen} onOpenChange={setAddOpen}>
                <DialogContent className="max-w-lg rounded-2xl">
                    <DialogHeader>
                        <DialogTitle>Schedule Inspection</DialogTitle>
                        <DialogDescription>Add a new property inspection to your schedule.</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        <div className="space-y-1">
                            <Label>Property *</Label>
                            <Select value={form.property_id}
                                    onValueChange={v => setForm(f => ( {...f, property_id: v} ))}>
                                <SelectTrigger><SelectValue placeholder="Select property"/></SelectTrigger>
                                <SelectContent>
                                    {properties.map(p => (
                                        <SelectItem key={p.id} value={p.id}>{p.address}, {p.suburb}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label>Type</Label>
                                <Select value={form.inspection_type}
                                        onValueChange={v => setForm(f => ( {...f, inspection_type: v} ))}>
                                    <SelectTrigger><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        {INSPECTION_TYPES.map(t => <SelectItem key={t.value}
                                                                               value={t.value}>{t.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1">
                                <Label>Scheduled Date *</Label>
                                <Input type="date" value={form.scheduled_date}
                                       onChange={e => setForm(f => ( {...f, scheduled_date: e.target.value} ))}/>
                            </div>
                        </div>
                        <div className="space-y-1">
                            <Label>Inspector Name</Label>
                            <Input value={form.inspector_name}
                                   onChange={e => setForm(f => ( {...f, inspector_name: e.target.value} ))}
                                   placeholder="Property manager or inspector"/>
                        </div>
                        <div className="space-y-1">
                            <Label>Notes</Label>
                            <Textarea value={form.notes} onChange={e => setForm(f => ( {...f, notes: e.target.value} ))}
                                      rows={3} placeholder="Any pre-inspection notes…"/>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setAddOpen(false)}>Cancel</Button>
                        <Button className="bg-teal-600 hover:bg-teal-700" onClick={handleAdd} disabled={submitting}>
                            {submitting && <Loader2 className="h-4 w-4 mr-1 animate-spin"/>} Schedule
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Update Dialog */}
            <Dialog open={updateOpen} onOpenChange={setUpdateOpen}>
                <DialogContent className="max-w-lg rounded-2xl">
                    <DialogHeader>
                        <DialogTitle>Update Inspection</DialogTitle>
                        <DialogDescription>Update status, outcome or notes for this inspection.</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        <div className="space-y-1">
                            <Label>Status</Label>
                            <Select value={form.status || 'scheduled'}
                                    onValueChange={v => setForm(f => ( {...f, status: v} ))}>
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    {INSPECTION_STATUSES.map(s => <SelectItem key={s.value}
                                                                              value={s.value}>{s.label}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label>Type</Label>
                                <Select value={form.inspection_type}
                                        onValueChange={v => setForm(f => ( {...f, inspection_type: v} ))}>
                                    <SelectTrigger><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        {INSPECTION_TYPES.map(t => <SelectItem key={t.value}
                                                                               value={t.value}>{t.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1">
                                <Label>Scheduled Date</Label>
                                <Input type="date" value={form.scheduled_date}
                                       onChange={e => setForm(f => ( {...f, scheduled_date: e.target.value} ))}/>
                            </div>
                        </div>
                        <div className="space-y-1">
                            <Label>Inspector Name</Label>
                            <Input value={form.inspector_name}
                                   onChange={e => setForm(f => ( {...f, inspector_name: e.target.value} ))}/>
                        </div>
                        <div className="space-y-1">
                            <Label>Outcome / Report Summary</Label>
                            <Textarea value={form.outcome || ''}
                                      onChange={e => setForm(f => ( {...f, outcome: e.target.value} ))} rows={3}
                                      placeholder="Summary of findings…"/>
                        </div>
                        <div className="space-y-1">
                            <Label>Notes</Label>
                            <Textarea value={form.notes} onChange={e => setForm(f => ( {...f, notes: e.target.value} ))}
                                      rows={2}/>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setUpdateOpen(false)}>Cancel</Button>
                        <Button className="bg-teal-600 hover:bg-teal-700" onClick={handleUpdate} disabled={submitting}>
                            {submitting && <Loader2 className="h-4 w-4 mr-1 animate-spin"/>} Save Changes
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default InspectionsPage;
