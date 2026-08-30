// @ts-nocheck
// @featuretrace:trust-reconciliation — Trust bank reconciliation and period lock management UI.
// Layer: frontend
// Data flow: this page → GET/POST/DELETE /trust/reconciliation/runs, /trust/period-locks,
//            GET /trust/reconciliation/runs/{id}/suggestions, POST .../close
//            → trust_reconciliation.py → trust_period_locks + trust_reconciliation_runs (building-scoped).
// Related: backend/routers/trust_reconciliation.py
//          tests/frontend/unit/pages/dashboard/TrustReconciliationPage.test.jsx
// Toggle: trust_reconciliation
// Collection: trust_period_locks, trust_reconciliation_runs, bank_statement_lines
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
import { AlertCircle, CheckCircle2, Clock, FileText, Loader2, Lock, Plus, RefreshCw, Upload, } from 'lucide-react';
import { toast } from 'sonner';
import { formatCurrency } from '../../lib/utils';
import { classifyApiError } from '../../lib/api-error';

const ALLOWED_ROLES = ['strata_manager', 'super_admin', 'treasurer'];
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const statusBadge = (status) => {
    // Real backend enum (models/trust_accounting.py ReconciliationStatus): open, in_progress, closed, cancelled.
    switch (status) {
        case 'closed':
            return <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200">Closed</Badge>;
        case 'in_progress':
            return <Badge className="bg-blue-100 text-blue-700 border-blue-200">In Progress</Badge>;
        case 'open':
            return <Badge className="bg-amber-100 text-amber-700 border-amber-200">Open</Badge>;
        case 'cancelled':
            return <Badge className="bg-red-100 text-red-700 border-red-200">Cancelled</Badge>;
        default:
            return <Badge variant="outline">{status}</Badge>;
    }
};
/**
 * @generated FunctionHeader
 * Function: lockStatusBadge
 * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const lockStatusBadge = (active) =>
    active
        ? <Badge className="bg-red-100 text-red-700 border-red-200"><Lock size={10} className="mr-1"/>Locked</Badge>
        : <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200">Unlocked</Badge>;
/**
 * @generated FunctionHeader
 * Function: normalizeRun
 * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const normalizeRun = (run) => ({
    ...run,
    period_start: run?.period_start || run?.statement_start_date || '',
    period_end: run?.period_end || run?.statement_end_date || '',
    account_id: run?.account_id || run?.bank_account_id || '',
});
/**
 * @generated FunctionHeader
 * Function: normalizeStatementLine
 * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const normalizeStatementLine = (line) => {
    const matchedLedgerIds = Array.isArray(line?.matched_ledger_entry_ids)
        ? line.matched_ledger_entry_ids.filter(Boolean)
        : [];
    const amountCents = typeof line?.amount_cents === 'number'
        ? line.amount_cents
        : Math.round(Number(line?.amount || 0) * 100);

    return {
        ...line,
        date: line?.date || line?.statement_date || '—',
        amount_cents: amountCents,
        amount_display: line?.amount_display || null,
        is_matched: typeof line?.is_matched === 'boolean'
            ? line.is_matched
            : (line?.matched_status === 'matched' || Boolean(line?.matched)),
        matched_to: line?.matched_to || matchedLedgerIds.join(', ') || '—',
    };
};
/**
 * @generated FunctionHeader
 * Function: extractList
 * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const extractList = (payload, keys = []) => {
    if (Array.isArray(payload)) return payload;
    for (const key of keys) {
        if (Array.isArray(payload?.[key])) return payload[key];
    }
    return [];
};
/**
 * @generated FunctionHeader
 * Function: TrustReconciliationPage
 * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TrustReconciliationPage() {
    const {user, api} = useAuth();

    const [runs, setRuns] = useState([]);
    const [locks, setLocks] = useState([]);
    const [loadingRuns, setLoadingRuns] = useState(true);
    const [loadingLocks, setLoadingLocks] = useState(true);
    const [activeTab, setActiveTab] = useState('runs');

    // New run modal
    const [showNewRun, setShowNewRun] = useState(false);
    const [newRun, setNewRun] = useState({
        statement_start_date: '',
        statement_end_date: '',
        bank_account_id: '',
        notes: '',
    });
    const [savingRun, setSavingRun] = useState(false);

    // Per-run view
    const [selectedRun, setSelectedRun] = useState(null);
    const [statementLines, setStatementLines] = useState([]);
    const [loadingLines, setLoadingLines] = useState(false);
    const [uploadingCSV, setUploadingCSV] = useState(false);
    const [suggestionMap, setSuggestionMap] = useState({});
    const [closingRun, setClosingRun] = useState(false);

    // New lock modal
    const [showNewLock, setShowNewLock] = useState(false);
    const [newLock, setNewLock] = useState({period_start: '', period_end: '', reason: ''});
    const [savingLock, setSavingLock] = useState(false);

    const userRole = user?.role || '';
    const hasAccess = ALLOWED_ROLES.includes(userRole);

    const fetchRuns = useCallback(async () => {
        setLoadingRuns(true);
        try {
            const res = await api.get('/trust/reconciliation/runs');
            const rawRuns = extractList(res.data, ['runs', 'items']);
            setRuns(rawRuns.map(normalizeRun));
        } catch (err) {
            const classified = classifyApiError(err);
            toast.error(classified.message);
        } finally {
            setLoadingRuns(false);
        }
    }, [api]);

    const fetchLocks = useCallback(async () => {
        setLoadingLocks(true);
        try {
            const res = await api.get('/trust/period-locks');
            setLocks(extractList(res.data, ['locks', 'items']));
        } catch (err) {
            const classified = classifyApiError(err);
            toast.error(classified.message);
        } finally {
            setLoadingLocks(false);
        }
    }, [api]);

    const fetchStatementLines = useCallback(async (runId) => {
        setLoadingLines(true);
        try {
            const res = await api.get(`/trust/reconciliation/runs/${runId}/statement-lines`);
            const rawLines = extractList(res.data, ['lines', 'items']);
            setStatementLines(rawLines.map(normalizeStatementLine));
        } catch {
            toast.error('Failed to load statement lines');
        } finally {
            setLoadingLines(false);
        }
    }, [api]);

    const fetchSuggestions = useCallback(async (runId) => {
        try {
            const res = await api.get(`/trust/reconciliation/runs/${runId}/suggestions`);
            const rawSuggestions = extractList(res.data, ['suggestions']);
            const map = {};
            for (const s of rawSuggestions) {
                // Keep the highest-confidence suggestion per bank line (backend already
                // returns at most one best match per line, but guard anyway).
                const existing = map[s.bank_line_id];
                if (!existing || (s.confidence_score ?? 0) > (existing.confidence_score ?? 0)) {
                    map[s.bank_line_id] = {
                        internal_transaction_id: s.internal_transaction_id,
                        confidence_score: s.confidence_score,
                        match_type: s.match_type,
                    };
                }
            }
            setSuggestionMap(map);
        } catch {
            // Suggestions are an enhancement, not required for the page to function.
            setSuggestionMap({});
        }
    }, [api]);

    useEffect(() => {
        if (!hasAccess) return;
        fetchRuns();
        fetchLocks();
    }, [hasAccess, fetchRuns, fetchLocks]);

    useEffect(() => {
        if (!hasAccess) return;
        if (selectedRun) {
            fetchStatementLines(selectedRun.id);
            fetchSuggestions(selectedRun.id);
        } else {
            setSuggestionMap({});
        }
    }, [hasAccess, selectedRun, fetchStatementLines, fetchSuggestions]);

    if (!hasAccess) {
        return (
            <div className="p-8 text-center text-muted-foreground">
                <AlertCircle className="mx-auto mb-2 h-8 w-8 text-amber-500"/>
                <p>You do not have permission to view this page.</p>
            </div>
        );
    }
    /**
     * @generated FunctionHeader
     * Function: handleCreateRun
     * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreateRun = async () => {
        if (!newRun.statement_start_date || !newRun.statement_end_date) {
            toast.error('Statement start and end dates are required');
            return;
        }
        if (!newRun.bank_account_id) {
            toast.error('Bank account ID is required');
            return;
        }
        setSavingRun(true);
        try {
            await api.post('/trust/reconciliation/runs', newRun);
            toast.success('Reconciliation run created');
            setShowNewRun(false);
            setNewRun({
                statement_start_date: '',
                statement_end_date: '',
                bank_account_id: '',
                notes: '',
            });
            fetchRuns();
        } catch (e) {
            toast.error(e?.response?.data?.detail || 'Failed to create run');
        } finally {
            setSavingRun(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCSVUpload
     * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCSVUpload = async (e, runId) => {
        const file = e.target.files?.[ 0 ];
        if (!file) return;
        setUploadingCSV(true);
        const formData = new FormData();
        formData.append('file', file);
        try {
            await api.post(`/trust/reconciliation/runs/${runId}/import-statement`, formData, {
                headers: {'Content-Type': 'multipart/form-data'},
            });
            toast.success('Bank statement imported');
            fetchStatementLines(runId);
        } catch (e) {
            toast.error(e?.response?.data?.detail || 'Failed to import CSV');
        } finally {
            setUploadingCSV(false);
            e.target.value = '';
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleMatchLine
     * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleMatchLine = async (lineId, ledgerEntryId) => {
        try {
            await api.post(`/trust/reconciliation/runs/${selectedRun.id}/match`, {
                bank_line_id: lineId,
                internal_transaction_id: ledgerEntryId,
            });
            toast.success('Line matched');
            fetchStatementLines(selectedRun.id);
            fetchSuggestions(selectedRun.id);
        } catch (e) {
            toast.error(e?.response?.data?.detail || 'Failed to match line');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCloseRun
     * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCloseRun = async () => {
        setClosingRun(true);
        try {
            const res = await api.post(`/trust/reconciliation/runs/${selectedRun.id}/close`);
            const {discrepancy_display, unmatched_remaining} = res.data || {};
            if (unmatched_remaining > 0) {
                toast.warning(
                    `Run closed with ${unmatched_remaining} unmatched line(s) remaining. Discrepancy: ${discrepancy_display ?? '—'}`
                );
            } else {
                toast.success(`Run closed. Discrepancy: ${discrepancy_display ?? '—'}`);
            }
            setSelectedRun(null);
            fetchRuns();
        } catch (e) {
            toast.error(e?.response?.data?.detail || 'Failed to close run');
        } finally {
            setClosingRun(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCreateLock
     * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreateLock = async () => {
        if (!newLock.period_start || !newLock.period_end) {
            toast.error('Period start and end are required');
            return;
        }
        setSavingLock(true);
        try {
            await api.post('/trust/period-locks', newLock);
            toast.success('Period lock created');
            setShowNewLock(false);
            setNewLock({period_start: '', period_end: '', reason: ''});
            fetchLocks();
        } catch (e) {
            toast.error(e?.response?.data?.detail || 'Failed to create lock');
        } finally {
            setSavingLock(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleUnlock
     * Path: frontend/src/pages/dashboard/TrustReconciliationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUnlock = async (lockId) => {
        try {
            await api.delete(`/trust/period-locks/${lockId}`);
            toast.success('Period unlocked');
            fetchLocks();
        } catch (e) {
            toast.error(e?.response?.data?.detail || 'Failed to unlock period');
        }
    };

    return (
        <div className="p-6 space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight">Trust Reconciliation</h1>
                    <p className="text-muted-foreground text-sm">Bank reconciliation and period lock management</p>
                </div>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="runs">Reconciliation Runs</TabsTrigger>
                    <TabsTrigger value="locks">Period Locks</TabsTrigger>
                </TabsList>

                {/* ── Reconciliation Runs ── */}
                <TabsContent value="runs" className="space-y-4">
                    {selectedRun ? (
                        <Card>
                            <CardHeader>
                                <div className="flex items-center justify-between">
                                    <CardTitle className="text-base">
                                        Run: {selectedRun.period_start} → {selectedRun.period_end}
                                    </CardTitle>
                                    <div className="flex gap-2">
                                        <Button variant="outline" size="sm" onClick={() => setSelectedRun(null)}>
                                            ← Back
                                        </Button>
                                        <label className="cursor-pointer">
                                            <Button variant="outline" size="sm" asChild disabled={uploadingCSV}>
                        <span>
                          {uploadingCSV ? <Loader2 className="h-4 w-4 animate-spin mr-1"/> :
                              <Upload size={14} className="mr-1"/>}
                            Import CSV
                        </span>
                                            </Button>
                                            <input type="file" accept=".csv" className="hidden"
                                                   onChange={(e) => handleCSVUpload(e, selectedRun.id)}/>
                                        </label>
                                        {selectedRun.status !== 'closed' && (
                                            <Button size="sm" onClick={handleCloseRun} disabled={closingRun}>
                                                {closingRun ? <Loader2 className="h-4 w-4 animate-spin mr-1"/> :
                                                    <CheckCircle2 size={14} className="mr-1"/>}
                                                Close Run
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            </CardHeader>
                            <CardContent>
                                {loadingLines ? (
                                    <div className="flex justify-center py-8"><Loader2
                                        className="h-6 w-6 animate-spin"/></div>
                                ) : statementLines.length === 0 ? (
                                    <div className="text-center py-8 text-muted-foreground">
                                        <FileText className="mx-auto mb-2 h-8 w-8"/>
                                        <p>No statement lines. Import a bank statement CSV to begin.</p>
                                    </div>
                                ) : (
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Date</TableHead>
                                                <TableHead>Description</TableHead>
                                                <TableHead className="text-right">Amount</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead>Matched To</TableHead>
                                                <TableHead/>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {statementLines.map((line) => (
                                                <TableRow key={line.id}>
                                                    <TableCell className="text-sm">{line.date}</TableCell>
                                                    <TableCell className="text-sm">{line.description}</TableCell>
                                                    <TableCell
                                                        className={`text-right text-sm font-mono ${line.amount_cents < 0 ? 'text-red-600' : 'text-green-600'}`}>
                                                        {line.amount_display || formatCurrency(line.amount_cents / 100)}
                                                    </TableCell>
                                                    <TableCell>
                                                        {line.is_matched
                                                            ? <Badge
                                                                className="bg-emerald-100 text-emerald-700 border-emerald-200"><CheckCircle2
                                                                size={10} className="mr-1"/>Matched</Badge>
                                                            :
                                                            <Badge variant="outline"><Clock size={10} className="mr-1"/>Unmatched</Badge>
                                                        }
                                                    </TableCell>
                                                    <TableCell
                                                        className="text-sm text-muted-foreground">{line.matched_to || '—'}</TableCell>
                                                    <TableCell>
                                                        {!line.is_matched && suggestionMap[line.id] && (
                                                            <Button size="sm" variant="outline"
                                                                    title={`Suggested match (${Math.round((suggestionMap[line.id].confidence_score ?? 0) * 100)}% confidence, ${suggestionMap[line.id].match_type})`}
                                                                    onClick={() => handleMatchLine(line.id, suggestionMap[line.id].internal_transaction_id)}>
                                                                Match
                                                            </Button>
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                )}
                            </CardContent>
                        </Card>
                    ) : (
                        <Card>
                            <CardHeader>
                                <div className="flex items-center justify-between">
                                    <CardTitle className="text-base">Reconciliation Runs</CardTitle>
                                    <div className="flex gap-2">
                                        <Button variant="outline" size="sm" onClick={fetchRuns}>
                                            <RefreshCw size={14} className="mr-1"/> Refresh
                                        </Button>
                                        <Button size="sm" onClick={() => setShowNewRun(true)}>
                                            <Plus size={14} className="mr-1"/> New Run
                                        </Button>
                                    </div>
                                </div>
                            </CardHeader>
                            <CardContent>
                                {loadingRuns ? (
                                    <div className="flex justify-center py-8"><Loader2
                                        className="h-6 w-6 animate-spin"/></div>
                                ) : runs.length === 0 ? (
                                    <div className="text-center py-8 text-muted-foreground">No reconciliation runs
                                        found.</div>
                                ) : (
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Period</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead className="text-right">Discrepancy</TableHead>
                                                <TableHead>Created</TableHead>
                                                <TableHead/>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {runs.map((run) => (
                                                <TableRow key={run.id} className="cursor-pointer hover:bg-muted/50">
                                                    <TableCell
                                                        className="text-sm font-medium">{run.period_start} → {run.period_end}</TableCell>
                                                    <TableCell>{statusBadge(run.status)}</TableCell>
                                                    <TableCell
                                                        className={`text-right text-sm font-mono ${run.discrepancy_cents !== 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                                                        {run.discrepancy_display || (run.discrepancy_cents != null ? formatCurrency(run.discrepancy_cents / 100) : '—')}
                                                    </TableCell>
                                                    <TableCell className="text-sm text-muted-foreground">
                                                        {run.created_at ? new Date(run.created_at).toLocaleDateString('en-AU') : '—'}
                                                    </TableCell>
                                                    <TableCell>
                                                        <Button size="sm" variant="outline"
                                                                onClick={() => setSelectedRun(run)}>
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
                </TabsContent>

                {/* ── Period Locks ── */}
                <TabsContent value="locks" className="space-y-4">
                    <Card>
                        <CardHeader>
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-base">Period Locks</CardTitle>
                                <div className="flex gap-2">
                                    <Button variant="outline" size="sm" onClick={fetchLocks}>
                                        <RefreshCw size={14} className="mr-1"/> Refresh
                                    </Button>
                                    <Button size="sm" onClick={() => setShowNewLock(true)}>
                                        <Lock size={14} className="mr-1"/> New Lock
                                    </Button>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            {loadingLocks ? (
                                <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin"/>
                                </div>
                            ) : locks.length === 0 ? (
                                <div className="text-center py-8 text-muted-foreground">No period locks found.</div>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Period</TableHead>
                                            <TableHead>Status</TableHead>
                                            <TableHead>Reason</TableHead>
                                            <TableHead>Created By</TableHead>
                                            <TableHead>Created</TableHead>
                                            <TableHead/>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {locks.map((lock) => (
                                            <TableRow key={lock.id}>
                                                <TableCell
                                                    className="text-sm font-medium">{lock.period_start} → {lock.period_end}</TableCell>
                                                <TableCell>{lockStatusBadge(lock.is_active !== false)}</TableCell>
                                                <TableCell
                                                    className="text-sm text-muted-foreground">{lock.reason || '—'}</TableCell>
                                                <TableCell
                                                    className="text-sm text-muted-foreground">{lock.created_by || '—'}</TableCell>
                                                <TableCell className="text-sm text-muted-foreground">
                                                    {lock.created_at ? new Date(lock.created_at).toLocaleDateString('en-AU') : '—'}
                                                </TableCell>
                                                <TableCell>
                                                    {lock.is_active !== false && (
                                                        <Button size="sm" variant="outline"
                                                                onClick={() => handleUnlock(lock.id)}>
                                                            Unlock
                                                        </Button>
                                                    )}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>

            {/* New Run Dialog */}
            <Dialog open={showNewRun} onOpenChange={setShowNewRun}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>New Reconciliation Run</DialogTitle>
                        <DialogDescription>Create a new bank reconciliation run for a specific
                            period.</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>Period Start</Label>
                                <Input type="date" value={newRun.statement_start_date}
                                       onChange={e => setNewRun(p => ({...p, statement_start_date: e.target.value}))}/>
                            </div>
                            <div className="space-y-2">
                                <Label>Period End</Label>
                                <Input type="date" value={newRun.statement_end_date}
                                       onChange={e => setNewRun(p => ({...p, statement_end_date: e.target.value}))}/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label>Bank Account ID</Label>
                            <Input placeholder="Trust account ID" value={newRun.bank_account_id}
                                   onChange={e => setNewRun(p => ({...p, bank_account_id: e.target.value}))}/>
                        </div>
                        <div className="space-y-2">
                            <Label>Notes</Label>
                            <Input placeholder="Optional notes" value={newRun.notes}
                                   onChange={e => setNewRun(p => ( {...p, notes: e.target.value} ))}/>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowNewRun(false)}>Cancel</Button>
                        <Button onClick={handleCreateRun} disabled={savingRun}>
                            {savingRun ? <Loader2 className="h-4 w-4 animate-spin mr-1"/> : null}
                            Create Run
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* New Lock Dialog */}
            <Dialog open={showNewLock} onOpenChange={setShowNewLock}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>New Period Lock</DialogTitle>
                        <DialogDescription>Lock a financial period to prevent modifications.</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>Period Start</Label>
                                <Input type="date" value={newLock.period_start}
                                       onChange={e => setNewLock(p => ( {...p, period_start: e.target.value} ))}/>
                            </div>
                            <div className="space-y-2">
                                <Label>Period End</Label>
                                <Input type="date" value={newLock.period_end}
                                       onChange={e => setNewLock(p => ( {...p, period_end: e.target.value} ))}/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label>Reason</Label>
                            <Input placeholder="Reason for locking this period" value={newLock.reason}
                                   onChange={e => setNewLock(p => ( {...p, reason: e.target.value} ))}/>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowNewLock(false)}>Cancel</Button>
                        <Button onClick={handleCreateLock} disabled={savingLock}>
                            {savingLock ? <Loader2 className="h-4 w-4 animate-spin mr-1"/> : null}
                            Create Lock
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
