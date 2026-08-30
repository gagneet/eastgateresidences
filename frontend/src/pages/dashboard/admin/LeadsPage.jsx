import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Badge } from '../../../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../../components/ui/table';
import {
    Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Textarea } from '../../../components/ui/textarea';
import {
    Building2, CheckCircle2, ChevronLeft, ChevronRight,
    Mail, RefreshCw, Search, TrendingUp, UserPlus, XCircle,
} from 'lucide-react';
import { toast } from 'sonner';

// ── Constants ─────────────────────────────────────────────────────────────────

const STATUS_CONFIG = {
    new: {label: 'New', classes: 'bg-blue-100 text-blue-800'},
    contacted: {label: 'Contacted', classes: 'bg-amber-100 text-amber-800'},
    demo_scheduled: {label: 'Demo Scheduled', classes: 'bg-purple-100 text-purple-800'},
    converted: {label: 'Converted', classes: 'bg-emerald-100 text-emerald-800'},
    disqualified: {label: 'Disqualified', classes: 'bg-gray-100 text-gray-500'},
};

const STATUS_OPTIONS = Object.entries(STATUS_CONFIG).map(([value, {label}]) => ( {value, label} ));
/**
 * @generated FunctionHeader
 * Function: fmtDate
 * Path: frontend/src/pages/dashboard/admin/LeadsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function fmtDate(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString('en-AU', {
        day: 'numeric', month: 'short', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
}
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/dashboard/admin/LeadsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatusBadge({status}) {
    const cfg = STATUS_CONFIG[ status ] || {label: status, classes: 'bg-gray-100 text-gray-600'};
    return <span
        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${cfg.classes}`}>{cfg.label}</span>;
}
/**
 * @generated FunctionHeader
 * Function: StatCard
 * Path: frontend/src/pages/dashboard/admin/LeadsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatCard({icon: Icon, label, value, color}) {
    return (
        <Card className="border shadow-sm">
            <CardContent className="p-4 flex items-center gap-3">
                <div className={`p-2 rounded-lg ${color}`}>
                    <Icon className="h-4 w-4"/>
                </div>
                <div>
                    <p className="text-xs text-muted-foreground">{label}</p>
                    <p className="text-xl font-bold">{value ?? '—'}</p>
                </div>
            </CardContent>
        </Card>
    );
}
// ── Main component ────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LeadsPage
 * Path: frontend/src/pages/dashboard/admin/LeadsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function LeadsPage() {
    const {api, isAdmin, loading: authLoading} = useAuth();

    const [leads, setLeads] = useState([]);
    const [loading, setLoading] = useState(true);
    const [counts, setCounts] = useState({});
    const [pagination, setPagination] = useState({page: 1, total_pages: 1, total: 0});
    const [page, setPage] = useState(1);
    const [statusFilter, setStatusFilter] = useState('all');
    const [searchTerm, setSearchTerm] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [fromDate, setFromDate] = useState('');
    const [toDate, setToDate] = useState('');

    // edit dialog
    const [editingLead, setEditingLead] = useState(null);
    const [editStatus, setEditStatus] = useState('');
    const [editNotes, setEditNotes] = useState('');
    const [patchLoading, setPatchLoading] = useState(false);

    // Debounce search input
    useEffect(() => {
        const t = setTimeout(() => setDebouncedSearch(searchTerm), 300);
        return () => clearTimeout(t);
    }, [searchTerm]);

    // Reset to page 1 when filters change
    useEffect(() => {
        setPage(1);
    }, [statusFilter, debouncedSearch, fromDate, toDate]);

    const fetchLeads = useCallback(async () => {
        setLoading(true);
        try {
            const params = new URLSearchParams({page, limit: 25});
            if (statusFilter !== 'all') params.set('status', statusFilter);
            if (debouncedSearch) params.set('search', debouncedSearch);
            if (fromDate) params.set('from_date', fromDate);
            if (toDate) params.set('to_date', toDate);

            const res = await api.get(`/admin/trial-requests?${params}`);
            setLeads(res.data.data);
            setCounts(res.data.counts || {});
            setPagination(res.data.pagination);
        } catch {
            toast.error('Failed to load leads');
        } finally {
            setLoading(false);
        }
    }, [api, page, statusFilter, debouncedSearch, fromDate, toDate]);

    useEffect(() => {
        fetchLeads();
    }, [fetchLeads]);

    // Guard after all hooks — Rules of Hooks require unconditional hook calls above.
    // Auth must resolve first: isAdmin() is false while the session loads, so
    // guarding on it directly hides the page from real admins on first paint.
    if (authLoading) return null;
    if (!isAdmin()) return null;
    /**
     * @generated FunctionHeader
     * Function: openEdit
     * Path: frontend/src/pages/dashboard/admin/LeadsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function openEdit(lead) {
        setEditingLead(lead);
        setEditStatus(lead.status);
        setEditNotes(lead.notes || '');
    }
    /**
     * @generated FunctionHeader
     * Function: handlePatch
     * Path: frontend/src/pages/dashboard/admin/LeadsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    async function handlePatch(e) {
        e.preventDefault();
        if (!editingLead) return;
        setPatchLoading(true);
        try {
            await api.patch(`/admin/trial-requests/${editingLead.id}`, {
                status: editStatus,
                notes: editNotes,
            });
            toast.success('Lead updated');
            setEditingLead(null);
            fetchLeads();
        } catch {
            toast.error('Failed to update lead');
        } finally {
            setPatchLoading(false);
        }
    }

    const thisMonth = ( () => {
        const now = new Date();
        return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    } )();

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold" style={{color: '#2F4F4F'}}>Trial Leads</h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        All walkthrough requests submitted via the StrataOS marketing page
                    </p>
                </div>
                <Button
                    variant="outline"
                    size="sm"
                    className="rounded-full gap-2"
                    onClick={fetchLeads}
                    data-testid="leads-refresh"
                >
                    <RefreshCw className="h-4 w-4"/>
                    Refresh
                </Button>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard icon={UserPlus} label="Total Leads" value={counts.total} color="bg-slate-100 text-slate-600"/>
                <StatCard icon={Mail} label="New" value={counts.new} color="bg-blue-100 text-blue-600"/>
                <StatCard icon={TrendingUp} label="Converted" value={counts.converted}
                          color="bg-emerald-100 text-emerald-600"/>
                <StatCard icon={Building2} label="Demo Scheduled" value={counts.demo_scheduled}
                          color="bg-purple-100 text-purple-600"/>
            </div>

            {/* Filters */}
            <Card className="border shadow-sm">
                <CardContent className="p-4">
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                            <Input
                                placeholder="Search name, company, email…"
                                value={searchTerm}
                                onChange={e => setSearchTerm(e.target.value)}
                                className="pl-9"
                                data-testid="leads-search"
                            />
                        </div>
                        <Select value={statusFilter} onValueChange={setStatusFilter}>
                            <SelectTrigger data-testid="leads-status-filter">
                                <SelectValue placeholder="All statuses"/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All statuses</SelectItem>
                                {STATUS_OPTIONS.map(o => (
                                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Input
                            type="date"
                            value={fromDate}
                            onChange={e => setFromDate(e.target.value)}
                            placeholder="From date"
                            data-testid="leads-from-date"
                        />
                        <Input
                            type="date"
                            value={toDate}
                            onChange={e => setToDate(e.target.value)}
                            placeholder="To date"
                            data-testid="leads-to-date"
                        />
                    </div>
                </CardContent>
            </Card>

            {/* Table */}
            <Card className="border shadow-sm">
                <CardHeader className="pb-2">
                    <CardTitle className="text-base font-semibold">
                        {loading ? 'Loading…' : `${pagination.total} lead${pagination.total !== 1 ? 's' : ''}`}
                    </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead className="pl-6">Submitted</TableHead>
                                <TableHead>Name / Company</TableHead>
                                <TableHead>Email</TableHead>
                                <TableHead>Phone</TableHead>
                                <TableHead>Lots / State</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead>Email Sent</TableHead>
                                <TableHead className="pr-6 text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading && (
                                <TableRow>
                                    <TableCell colSpan={8} className="text-center py-12 text-muted-foreground">
                                        Loading leads…
                                    </TableCell>
                                </TableRow>
                            )}
                            {!loading && leads.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={8} className="text-center py-12 text-muted-foreground">
                                        No leads found
                                    </TableCell>
                                </TableRow>
                            )}
                            {!loading && leads.map(lead => (
                                <TableRow key={lead.id}>
                                    <TableCell className="pl-6 text-sm whitespace-nowrap">
                                        {fmtDate(lead.submitted_at)}
                                    </TableCell>
                                    <TableCell>
                                        <div className="font-medium text-sm">{lead.name}</div>
                                        <div className="text-xs text-muted-foreground">{lead.company}</div>
                                    </TableCell>
                                    <TableCell>
                                        <a
                                            href={`mailto:${lead.email}`}
                                            className="text-sm hover:underline"
                                            style={{color: '#2F4F4F'}}
                                        >
                                            {lead.email}
                                        </a>
                                    </TableCell>
                                    <TableCell className="text-sm">{lead.phone || '—'}</TableCell>
                                    <TableCell>
                                        <div className="text-sm">{lead.lots || '—'} lots</div>
                                        <div className="text-xs text-muted-foreground">{lead.state || '—'}</div>
                                    </TableCell>
                                    <TableCell>
                                        <StatusBadge status={lead.status}/>
                                    </TableCell>
                                    <TableCell>
                                        {lead.email_sent
                                            ? <CheckCircle2 className="h-4 w-4 text-emerald-500"/>
                                            : <XCircle className="h-4 w-4 text-red-400"/>
                                        }
                                    </TableCell>
                                    <TableCell className="pr-6 text-right">
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="rounded-full"
                                            onClick={() => openEdit(lead)}
                                            data-testid={`leads-edit-${lead.id}`}
                                        >
                                            Edit
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>

                {/* Pagination */}
                {pagination.total_pages > 1 && (
                    <div className="flex items-center justify-between px-6 py-3 border-t">
                        <p className="text-sm text-muted-foreground">
                            Page {pagination.page} of {pagination.total_pages}
                        </p>
                        <div className="flex gap-2">
                            <Button
                                variant="outline"
                                size="sm"
                                className="rounded-full"
                                disabled={page <= 1}
                                onClick={() => setPage(p => p - 1)}
                            >
                                <ChevronLeft className="h-4 w-4"/>
                            </Button>
                            <Button
                                variant="outline"
                                size="sm"
                                className="rounded-full"
                                disabled={page >= pagination.total_pages}
                                onClick={() => setPage(p => p + 1)}
                            >
                                <ChevronRight className="h-4 w-4"/>
                            </Button>
                        </div>
                    </div>
                )}
            </Card>

            {/* Edit dialog */}
            <Dialog open={!!editingLead} onOpenChange={open => {
                if (!open) setEditingLead(null);
            }}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Update Lead</DialogTitle>
                    </DialogHeader>

                    {editingLead && (
                        <form onSubmit={handlePatch} className="space-y-4">
                            {/* Read-only summary */}
                            <div className="rounded-lg border p-4 space-y-1 text-sm bg-muted/30">
                                <div className="font-semibold text-base">{editingLead.name}</div>
                                <div className="text-muted-foreground">{editingLead.company}</div>
                                <a href={`mailto:${editingLead.email}`} className="block hover:underline"
                                   style={{color: '#2F4F4F'}}>
                                    {editingLead.email}
                                </a>
                                {editingLead.phone && <div>{editingLead.phone}</div>}
                                <div className="text-muted-foreground pt-1">
                                    {editingLead.lots ? `${editingLead.lots} lots` : ''}
                                    {editingLead.lots && editingLead.state ? ' · ' : ''}
                                    {editingLead.state || ''}
                                </div>
                                {editingLead.message && (
                                    <div className="pt-2 border-t mt-2 text-muted-foreground italic">
                                        "{editingLead.message}"
                                    </div>
                                )}
                                <div className="pt-2 text-xs text-muted-foreground">
                                    Submitted {fmtDate(editingLead.submitted_at)}
                                </div>
                            </div>

                            {/* Status */}
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium">Status</label>
                                <Select value={editStatus} onValueChange={setEditStatus}>
                                    <SelectTrigger data-testid="leads-patch-status">
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {STATUS_OPTIONS.map(o => (
                                            <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            {/* Notes */}
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium">Notes</label>
                                <Textarea
                                    value={editNotes}
                                    onChange={e => setEditNotes(e.target.value)}
                                    placeholder="Internal notes about this lead…"
                                    rows={4}
                                    data-testid="leads-patch-notes"
                                />
                            </div>

                            <DialogFooter>
                                <Button
                                    type="button"
                                    variant="outline"
                                    className="rounded-full"
                                    onClick={() => setEditingLead(null)}
                                >
                                    Cancel
                                </Button>
                                <Button
                                    type="submit"
                                    className="rounded-full"
                                    style={{background: '#2F4F4F'}}
                                    disabled={patchLoading}
                                    data-testid="leads-patch-submit"
                                >
                                    {patchLoading ? 'Saving…' : 'Save Changes'}
                                </Button>
                            </DialogFooter>
                        </form>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}
