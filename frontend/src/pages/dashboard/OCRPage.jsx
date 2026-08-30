// @featuretrace:document_ocr — Frontend OCR workspace for building-scoped document extraction.
// Layer: frontend
// Data flow: OCRPage.jsx → /ocr/providers, /ocr/jobs, /ocr/jobs/{id}
//            → backend/routers/ocr.py → ocr_jobs / ocr_results.
// Related: backend/services/ocr/unlimited_ocr_client.py
//           frontend/src/components/layout/DashboardLayout.tsx
// Scope: (building-scoped)

import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {useAuth} from '../../contexts/AuthContext';
import {Alert, AlertDescription} from '../../components/ui/alert';
import {Badge} from '../../components/ui/badge';
import {Button} from '../../components/ui/button';
import {Card, CardContent, CardHeader, CardTitle} from '../../components/ui/card';
import {Input} from '../../components/ui/input';
import {Label} from '../../components/ui/label';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../../components/ui/select';
import {ScrollArea} from '../../components/ui/scroll-area';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../../components/ui/table';
import {AlertCircle, CheckCircle2, FileText, Loader2, RefreshCw, ScanText, Upload} from 'lucide-react';
import {toast} from 'sonner';

// Job statuses that mean polling should stop — module scope so pollJob's useCallback doesn't
// need it in its dependency array (a literal defined inside the component would be a new array
// reference every render).
const TERMINAL_STATUSES = ['completed', 'needs_review', 'failed'];

const SOURCE_TYPES = [
    {id: 'general_document', label: 'General document'},
    {id: 'invoice', label: 'Invoice'},
    {id: 'quote', label: 'Quote'},
    {id: 'bank_statement', label: 'Bank statement'},
    {id: 'agm_minutes', label: 'AGM minutes'},
    {id: 'rates_notice', label: 'Rates notice'},
    {id: 'insurance', label: 'Insurance schedule'},
    {id: 'contract', label: 'Contract'},
    {id: 'maintenance_evidence', label: 'Maintenance evidence'},
];

function formatBytes(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso) {
    if (!iso) return '-';
    return new Date(iso).toLocaleString(undefined, {dateStyle: 'short', timeStyle: 'short'});
}

function statusBadge(status) {
    const classes = {
        completed: 'border-green-300 bg-green-50 text-green-700',
        needs_review: 'border-amber-300 bg-amber-50 text-amber-700',
        processing: 'border-blue-300 bg-blue-50 text-blue-700',
        failed: 'border-red-300 bg-red-50 text-red-700',
    };
    return <Badge variant="outline" className={classes[status] || ''}>{status}</Badge>;
}

export default function OCRPage() {
    const {api, user} = useAuth();
    const [providers, setProviders] = useState([]);
    const [jobs, setJobs] = useState([]);
    const [selectedJob, setSelectedJob] = useState(null);
    const [file, setFile] = useState(null);
    const [sourceType, setSourceType] = useState('general_document');
    const [provider, setProvider] = useState('auto');
    const [loading, setLoading] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [publishing, setPublishing] = useState(false);

    const unlimited = useMemo(() => providers.find(p => p.id === 'unlimited_ocr'), [providers]);

    const fetchData = useCallback(async () => {
        if (!api) return;
        setRefreshing(true);
        try {
            const [providersRes, jobsRes] = await Promise.all([
                api.get('/ocr/providers'),
                api.get('/ocr/jobs?limit=25'),
            ]);
            setProviders(providersRes.data || []);
            setJobs(jobsRes.data || []);
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Could not load OCR workspace');
        } finally {
            setRefreshing(false);
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    // ── Poll a processing job until it reaches a terminal status ────────────
    // create_ocr_job now dispatches extraction via FastAPI BackgroundTasks and returns
    // immediately with status="processing" (never blocks the request on a potentially
    // long-running provider call) — the UI must poll to see it actually finish.
    const pollTimeoutRef = useRef(null);

    const stopPolling = useCallback(() => {
        if (pollTimeoutRef.current) {
            clearTimeout(pollTimeoutRef.current);
            pollTimeoutRef.current = null;
        }
    }, []);

    useEffect(() => stopPolling, [stopPolling]);

    const pollJob = useCallback((jobId, attempt = 0) => {
        stopPolling();
        const maxAttempts = 30; // ~60s at 2s intervals
        pollTimeoutRef.current = setTimeout(async () => {
            try {
                const res = await api.get(`/ocr/jobs/${jobId}`);
                setSelectedJob(res.data);
                const jobStatus = res.data?.job?.status;
                if (TERMINAL_STATUSES.includes(jobStatus)) {
                    await fetchData();
                    return;
                }
                if (attempt + 1 >= maxAttempts) {
                    toast.error('OCR job is taking longer than expected — refresh to check on it later.');
                    return;
                }
                pollJob(jobId, attempt + 1);
            } catch {
                // Transient fetch error — stop polling rather than loop forever silently.
            }
        }, 2000);
    }, [api, stopPolling, fetchData]);

    const upload = async () => {
        if (!file) {
            toast.error('Choose a document first');
            return;
        }
        setLoading(true);
        const form = new FormData();
        form.append('file', file);
        form.append('source_type', sourceType);
        form.append('provider', provider);
        try {
            const res = await api.post('/ocr/jobs', form, {
                headers: {'Content-Type': 'multipart/form-data'},
            });
            setSelectedJob(null);
            setFile(null);
            toast.success('OCR job created — processing…');
            await fetchData();
            await openJob(res.data.id);
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'OCR upload failed');
        } finally {
            setLoading(false);
        }
    };

    const openJob = async (jobId) => {
        stopPolling();
        try {
            const res = await api.get(`/ocr/jobs/${jobId}`);
            setSelectedJob(res.data);
            if (!TERMINAL_STATUSES.includes(res.data?.job?.status)) {
                pollJob(jobId);
            }
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Could not load OCR result');
        }
    };

    const publishToDocuments = async () => {
        if (!selectedJob?.job?.id) return;
        setPublishing(true);
        const form = new FormData();
        form.append('title', `OCR extraction - ${selectedJob.job.file_name}`);
        try {
            await api.post(`/ocr/jobs/${selectedJob.job.id}/publish-document`, form, {
                headers: {'Content-Type': 'multipart/form-data'},
            });
            toast.success('OCR text published to the document library');
            await fetchData();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Could not publish OCR text');
        } finally {
            setPublishing(false);
        }
    };

    const extracted = selectedJob?.result?.result;

    return (
        <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-2xl font-semibold tracking-normal text-slate-950">Document OCR</h1>
                    <p className="text-sm text-slate-500">
                        {user?.role === 'owner' || user?.role === 'tenant'
                            ? 'Extract text from building documents for review.'
                            : 'Extract structured text for invoices, AGM minutes, rates notices, statements, and records.'}
                    </p>
                </div>
                <Button variant="outline" onClick={fetchData} disabled={refreshing}>
                    {refreshing ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <RefreshCw className="mr-2 h-4 w-4"/>}
                    Refresh
                </Button>
            </div>

            {unlimited && !unlimited.available && (
                <Alert className="border-amber-300 bg-amber-50">
                    <AlertCircle className="h-4 w-4 text-amber-600"/>
                    <AlertDescription className="text-amber-800">
                        Unlimited OCR is present in the product but not selectable in this environment: {unlimited.unavailable_reason}.
                    </AlertDescription>
                </Alert>
            )}

            <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_1fr]">
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                            <Upload className="h-4 w-4"/> Upload
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="ocr-file">Document</Label>
                            <Input
                                id="ocr-file"
                                type="file"
                                accept="application/pdf,image/png,image/jpeg,image/webp,text/plain"
                                onChange={(event) => setFile(event.target.files?.[0] || null)}
                            />
                            {file && <p className="text-xs text-slate-500">{file.name} · {formatBytes(file.size)}</p>}
                        </div>

                        <div className="space-y-2">
                            <Label>Document type</Label>
                            <Select value={sourceType} onValueChange={setSourceType}>
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    {SOURCE_TYPES.map(item => (
                                        <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="space-y-2">
                            <Label>OCR provider</Label>
                            <Select value={provider} onValueChange={setProvider}>
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="auto">Auto / building preference</SelectItem>
                                    {providers.filter(p => p.selectable).map(item => (
                                        <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <Button className="w-full" onClick={upload} disabled={loading || !file}>
                            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <ScanText className="mr-2 h-4 w-4"/>}
                            Run OCR
                        </Button>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                            <FileText className="h-4 w-4"/> Recent jobs
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Document</TableHead>
                                        <TableHead>Type</TableHead>
                                        <TableHead>Provider</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Confidence</TableHead>
                                        <TableHead>Created</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {jobs.map(job => (
                                        <TableRow key={job.id} className="cursor-pointer" onClick={() => openJob(job.id)}>
                                            <TableCell className="max-w-[260px] truncate font-medium">{job.file_name}</TableCell>
                                            <TableCell>{job.source_type}</TableCell>
                                            <TableCell>{job.provider}</TableCell>
                                            <TableCell>{statusBadge(job.status)}</TableCell>
                                            <TableCell>{Math.round((job.confidence || 0) * 100)}%</TableCell>
                                            <TableCell>{formatDate(job.created_at)}</TableCell>
                                        </TableRow>
                                    ))}
                                    {jobs.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={6} className="py-8 text-center text-sm text-slate-500">
                                                No OCR jobs yet.
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {selectedJob && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                            {selectedJob.job.file_name}
                            {statusBadge(selectedJob.job.status)}
                            {selectedJob.job.raw_text_sha256 && <Badge variant="outline">sha256 captured</Badge>}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {selectedJob.job.error_message && (
                            <Alert className="border-red-300 bg-red-50">
                                <AlertCircle className="h-4 w-4 text-red-600"/>
                                <AlertDescription className="text-red-700">{selectedJob.job.error_message}</AlertDescription>
                            </Alert>
                        )}
                        {extracted && (
                            <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
                                <div className="space-y-2 text-sm">
                                    <div className="flex justify-between gap-2"><span className="text-slate-500">Vendor</span><span className="font-medium">{extracted.vendor_name?.value || '-'}</span></div>
                                    <div className="flex justify-between gap-2"><span className="text-slate-500">ABN</span><span className="font-medium">{extracted.vendor_abn?.value || '-'}</span></div>
                                    <div className="flex justify-between gap-2"><span className="text-slate-500">Reference</span><span className="font-medium">{extracted.invoice_number?.value || '-'}</span></div>
                                    <div className="flex justify-between gap-2"><span className="text-slate-500">Total</span><span className="font-medium">{extracted.total_cents ? `$${(extracted.total_cents / 100).toFixed(2)}` : '-'}</span></div>
                                    <Button variant="outline" onClick={publishToDocuments} disabled={publishing || !extracted.raw_text}>
                                        {publishing ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <CheckCircle2 className="mr-2 h-4 w-4"/>}
                                        Publish text to Documents
                                    </Button>
                                </div>
                                <ScrollArea className="h-72 rounded-md border bg-slate-50">
                                    <pre className="whitespace-pre-wrap break-words p-4 text-xs leading-relaxed text-slate-700">
                                        {extracted.raw_text || 'No raw OCR text stored for this result.'}
                                    </pre>
                                </ScrollArea>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}

