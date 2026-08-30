// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import {
    AlertCircle,
    CheckCircle2,
    ChevronLeft,
    Database,
    Loader2,
    Play,
    Plus,
    RefreshCw,
    RotateCcw,
} from 'lucide-react';
import { toast } from 'sonner';

const ALLOWED_ROLES = ['strata_manager', 'super_admin'];

const SOURCE_SYSTEMS = ['MRI', 'Strata Master', 'Palace', 'Console Cloud', 'Other'];

const statusConfig = {
    pending: {label: 'Pending', className: 'bg-gray-100 text-gray-700 border-gray-200'},
    validating: {label: 'Validating', className: 'bg-blue-100 text-blue-700 border-blue-200'},
    validated: {label: 'Validated', className: 'bg-emerald-100 text-emerald-700 border-emerald-200'},
    dry_run: {label: 'Dry Run', className: 'bg-amber-100 text-amber-700 border-amber-200'},
    committing: {label: 'Committing', className: 'bg-violet-100 text-violet-700 border-violet-200'},
    committed: {label: 'Committed', className: 'bg-emerald-100 text-emerald-800 border-emerald-300'},
    failed: {label: 'Failed', className: 'bg-red-100 text-red-700 border-red-200'},
    rolled_back: {label: 'Rolled Back', className: 'bg-slate-100 text-slate-700 border-slate-200'},
};
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/dashboard/MriMigrationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StatusBadge = ({status}) => {
    const cfg = statusConfig[ status ] || {label: status, className: ''};
    return <Badge className={cfg.className}>{cfg.label}</Badge>;
};
/**
 * @generated FunctionHeader
 * Function: severityClass
 * Path: frontend/src/pages/dashboard/MriMigrationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const severityClass = (sev) =>
    sev === 'error' ? 'text-red-600' : sev === 'warning' ? 'text-amber-600' : 'text-blue-600';
/**
 * @generated FunctionHeader
 * Function: MriMigrationPage
 * Path: frontend/src/pages/dashboard/MriMigrationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function MriMigrationPage() {
    const {user, api} = useAuth();

    const [batches, setBatches] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selected, setSelected] = useState(null);
    const [batchDetail, setBatchDetail] = useState(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [activeDetailTab, setActiveDetailTab] = useState('overview');

    const [showNew, setShowNew] = useState(false);
    const [newBatch, setNewBatch] = useState({description: '', source_system: 'MRI'});
    const [saving, setSaving] = useState(false);

    const [actionLoading, setActionLoading] = useState('');

    // All hooks must be declared before any conditional return (Rules of Hooks).
    const fetchBatches = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.get('/migration/mri/batches');
            setBatches(res.data?.items || res.data || []);
        } catch {
            toast.error('Failed to load migration batches');
        } finally {
            setLoading(false);
        }
    }, [api]);

    const fetchBatchDetail = useCallback(async (id) => {
        setDetailLoading(true);
        try {
            const res = await api.get(`/migration/mri/batches/${id}`);
            setBatchDetail(res.data);
        } catch {
            toast.error('Failed to load batch details');
        } finally {
            setDetailLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchBatches();
    }, [fetchBatches]);

    useEffect(() => {
        if (selected) fetchBatchDetail(selected.id);
    }, [selected, fetchBatchDetail]);

    const userRole = user?.role || '';

    if (!ALLOWED_ROLES.includes(userRole)) {
        return (
            <div className="p-8 text-center text-muted-foreground">
                <AlertCircle className="mx-auto mb-2 h-8 w-8 text-amber-500"/>
                <p>You do not have permission to view this page.</p>
            </div>
        );
    }
    /**
     * @generated FunctionHeader
     * Function: handleCreate
     * Path: frontend/src/pages/dashboard/MriMigrationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreate = async () => {
        if (!newBatch.description) {
            toast.error('Description is required');
            return;
        }
        setSaving(true);
        try {
            await api.post('/migration/mri/batches', newBatch);
            toast.success('Migration batch created');
            setShowNew(false);
            setNewBatch({description: '', source_system: 'MRI'});
            fetchBatches();
        } catch (e) {
            toast.error(e?.response?.data?.detail || 'Failed to create batch');
        } finally {
            setSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleAction
     * Path: frontend/src/pages/dashboard/MriMigrationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAction = async (batchId, action) => {
        setActionLoading(action);
        try {
            await api.post(`/migration/mri/batches/${batchId}/${action}`);
            toast.success(`${action.replace('_', ' ')} initiated`);
            fetchBatchDetail(batchId);
            fetchBatches();
        } catch (e) {
            toast.error(e?.response?.data?.detail || `Failed to ${action}`);
        } finally {
            setActionLoading('');
        }
    };

    const detail = batchDetail || selected;

    return (
        <div className="p-6 space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight">MRI Migration</h1>
                    <p className="text-muted-foreground text-sm">Manage data migration batches from external systems</p>
                </div>
            </div>

            {selected ? (
                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <Button variant="outline" size="sm" onClick={() => {
                                    setSelected(null);
                                    setBatchDetail(null);
                                }}>
                                    <ChevronLeft size={14} className="mr-1"/> Back
                                </Button>
                                <CardTitle className="text-base">{selected.description}</CardTitle>
                                <StatusBadge status={selected.status}/>
                            </div>
                            <Button variant="outline" size="sm" onClick={() => fetchBatchDetail(selected.id)}>
                                <RefreshCw size={14} className="mr-1"/> Refresh
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {detailLoading ? (
                            <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin"/></div>
                        ) : (
                            <Tabs value={activeDetailTab} onValueChange={setActiveDetailTab}>
                                <TabsList>
                                    <TabsTrigger value="overview">Overview</TabsTrigger>
                                    <TabsTrigger value="validation">Validation Report</TabsTrigger>
                                    <TabsTrigger value="dry_run">Dry Run</TabsTrigger>
                                    <TabsTrigger value="actions">Commit / Rollback</TabsTrigger>
                                </TabsList>

                                <TabsContent value="overview" className="space-y-4 pt-4">
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                        {[
                                            {label: 'Source System', value: detail?.source_system},
                                            {label: 'Status', value: <StatusBadge status={detail?.status}/>},
                                            {label: 'Records Total', value: detail?.records_total ?? '—'},
                                            {label: 'Records Processed', value: detail?.records_processed ?? '—'},
                                            {label: 'Records Failed', value: detail?.records_failed ?? '—'},
                                            {
                                                label: 'Created',
                                                value: detail?.created_at ? new Date(detail.created_at).toLocaleDateString('en-AU') : '—'
                                            },
                                            {
                                                label: 'Updated',
                                                value: detail?.updated_at ? new Date(detail.updated_at).toLocaleDateString('en-AU') : '—'
                                            },
                                            {label: 'Created By', value: detail?.created_by ?? '—'},
                                        ].map(({label, value}) => (
                                            <div key={label} className="rounded-lg border p-3 space-y-1">
                                                <p className="text-xs text-muted-foreground">{label}</p>
                                                <p className="text-sm font-medium">{value}</p>
                                            </div>
                                        ))}
                                    </div>
                                    {detail?.notes && (
                                        <div className="rounded-lg border p-3">
                                            <p className="text-xs text-muted-foreground mb-1">Notes</p>
                                            <p className="text-sm">{detail.notes}</p>
                                        </div>
                                    )}
                                </TabsContent>

                                <TabsContent value="validation" className="pt-4">
                                    {!detail?.validation_report || detail.validation_report.length === 0 ? (
                                        <div className="text-center py-8 text-muted-foreground">
                                            <CheckCircle2 className="mx-auto mb-2 h-8 w-8 text-emerald-500"/>
                                            <p>No validation issues found.</p>
                                        </div>
                                    ) : (
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Severity</TableHead>
                                                    <TableHead>Field</TableHead>
                                                    <TableHead>Message</TableHead>
                                                    <TableHead>Row</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {detail.validation_report.map((issue, idx) => (
                                                    <TableRow key={idx}>
                                                        <TableCell>
                              <span className={`text-xs font-medium uppercase ${severityClass(issue.severity)}`}>
                                {issue.severity}
                              </span>
                                                        </TableCell>
                                                        <TableCell
                                                            className="text-sm font-mono">{issue.field || '—'}</TableCell>
                                                        <TableCell className="text-sm">{issue.message}</TableCell>
                                                        <TableCell
                                                            className="text-sm text-muted-foreground">{issue.row ?? '—'}</TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                    )}
                                </TabsContent>

                                <TabsContent value="dry_run" className="pt-4 space-y-4">
                                    <div className="flex items-center justify-between">
                                        <p className="text-sm text-muted-foreground">Preview what will be imported
                                            without making changes.</p>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={actionLoading === 'dry-run'}
                                            onClick={() => handleAction(selected.id, 'dry-run')}
                                        >
                                            {actionLoading === 'dry-run' ?
                                                <Loader2 className="h-4 w-4 animate-spin mr-1"/> :
                                                <Play size={14} className="mr-1"/>}
                                            Run Dry Run
                                        </Button>
                                    </div>
                                    {detail?.dry_run_summary ? (
                                        <div className="rounded-lg border p-4 space-y-2">
                                            <p className="text-sm font-medium">Dry Run Summary</p>
                                            <pre className="text-xs text-muted-foreground whitespace-pre-wrap">
                        {JSON.stringify(detail.dry_run_summary, null, 2)}
                      </pre>
                                        </div>
                                    ) : (
                                        <div className="text-center py-8 text-muted-foreground">No dry run results
                                            yet.</div>
                                    )}
                                </TabsContent>

                                <TabsContent value="actions" className="pt-4 space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <Card className="border-emerald-200">
                                            <CardHeader className="pb-2">
                                                <CardTitle className="text-sm text-emerald-700">Commit
                                                    Migration</CardTitle>
                                            </CardHeader>
                                            <CardContent className="space-y-3">
                                                <p className="text-xs text-muted-foreground">
                                                    Permanently apply all validated records to the database. This action
                                                    cannot be undone.
                                                </p>
                                                <Button
                                                    className="w-full"
                                                    disabled={actionLoading === 'commit' || !['validated', 'dry_run'].includes(detail?.status)}
                                                    onClick={() => handleAction(selected.id, 'commit')}
                                                >
                                                    {actionLoading === 'commit' ?
                                                        <Loader2 className="h-4 w-4 animate-spin mr-1"/> :
                                                        <Database size={14} className="mr-1"/>}
                                                    Commit
                                                </Button>
                                            </CardContent>
                                        </Card>
                                        <Card className="border-amber-200">
                                            <CardHeader className="pb-2">
                                                <CardTitle className="text-sm text-amber-700">Rollback
                                                    Migration</CardTitle>
                                            </CardHeader>
                                            <CardContent className="space-y-3">
                                                <p className="text-xs text-muted-foreground">
                                                    Revert all changes made by this batch. Only available for committed
                                                    batches.
                                                </p>
                                                <Button
                                                    variant="outline"
                                                    className="w-full border-amber-300 text-amber-700 hover:bg-amber-50"
                                                    disabled={actionLoading === 'rollback' || detail?.status !== 'committed'}
                                                    onClick={() => handleAction(selected.id, 'rollback')}
                                                >
                                                    {actionLoading === 'rollback' ?
                                                        <Loader2 className="h-4 w-4 animate-spin mr-1"/> :
                                                        <RotateCcw size={14} className="mr-1"/>}
                                                    Rollback
                                                </Button>
                                            </CardContent>
                                        </Card>
                                    </div>
                                </TabsContent>
                            </Tabs>
                        )}
                    </CardContent>
                </Card>
            ) : (
                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-base">Migration Batches</CardTitle>
                            <div className="flex gap-2">
                                <Button variant="outline" size="sm" onClick={fetchBatches}>
                                    <RefreshCw size={14} className="mr-1"/> Refresh
                                </Button>
                                <Button size="sm" onClick={() => setShowNew(true)}>
                                    <Plus size={14} className="mr-1"/> New Batch
                                </Button>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {loading ? (
                            <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin"/></div>
                        ) : batches.length === 0 ? (
                            <div className="text-center py-8 text-muted-foreground">No migration batches found.</div>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Description</TableHead>
                                        <TableHead>Source</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead className="text-right">Total</TableHead>
                                        <TableHead className="text-right">Processed</TableHead>
                                        <TableHead className="text-right">Failed</TableHead>
                                        <TableHead>Created</TableHead>
                                        <TableHead/>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {batches.map((batch) => (
                                        <TableRow key={batch.id} className="cursor-pointer hover:bg-muted/50">
                                            <TableCell className="text-sm font-medium">{batch.description}</TableCell>
                                            <TableCell className="text-sm">{batch.source_system}</TableCell>
                                            <TableCell><StatusBadge status={batch.status}/></TableCell>
                                            <TableCell
                                                className="text-right text-sm">{batch.records_total ?? '—'}</TableCell>
                                            <TableCell
                                                className="text-right text-sm text-emerald-600">{batch.records_processed ?? '—'}</TableCell>
                                            <TableCell
                                                className="text-right text-sm text-red-600">{batch.records_failed ?? '—'}</TableCell>
                                            <TableCell className="text-sm text-muted-foreground">
                                                {batch.created_at ? new Date(batch.created_at).toLocaleDateString('en-AU') : '—'}
                                            </TableCell>
                                            <TableCell>
                                                <Button size="sm" variant="outline" onClick={() => setSelected(batch)}>
                                                    View
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            )}

            <Dialog open={showNew} onOpenChange={setShowNew}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>New Migration Batch</DialogTitle>
                        <DialogDescription>Create a new data migration batch from an external
                            system.</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label>Description</Label>
                            <Input
                                placeholder="e.g. MRI Q1 2026 owner data import"
                                value={newBatch.description}
                                onChange={e => setNewBatch(p => ( {...p, description: e.target.value} ))}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>Source System</Label>
                            <select
                                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={newBatch.source_system}
                                onChange={e => setNewBatch(p => ( {...p, source_system: e.target.value} ))}
                            >
                                {SOURCE_SYSTEMS.map(s => <option key={s} value={s}>{s}</option>)}
                            </select>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowNew(false)}>Cancel</Button>
                        <Button onClick={handleCreate} disabled={saving}>
                            {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1"/> : null}
                            Create Batch
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
