// @featuretrace:invoices — Invoice & Quote list page: stats, search/filter, delete, receipt download.
// Layer: frontend
// Data flow: InvoicesPage → GET /invoices → invoice_documents (building-scoped).
// Related: frontend/src/components/invoices/InvoiceUploadModal.jsx
//           frontend/src/app/(dashboard)/financials/invoices/page.tsx
//           backend/routers/invoices.py

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useRouter } from 'next/navigation';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Input } from '../../components/ui/input';
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../components/ui/select';
import { toast } from 'sonner';
import {
    Upload, Receipt, CheckCircle, Clock, FileText, Trash2, Download, Search,
} from 'lucide-react';
import InvoiceUploadModal from '../../components/invoices/InvoiceUploadModal';

const STATUS_LABELS = {
    pending_review: {label: 'Pending Review', colour: 'bg-yellow-100 text-yellow-800'},
    confirmed: {label: 'Confirmed', colour: 'bg-green-100 text-green-800'},
    rejected: {label: 'Rejected', colour: 'bg-red-100 text-red-800'},
};
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/dashboard/InvoicesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatusBadge({status}) {
    const {label, colour} = STATUS_LABELS[ status ] || {label: status, colour: 'bg-gray-100 text-gray-600'};
    return <span
        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${colour}`}>{label}</span>;
}
/**
 * @generated FunctionHeader
 * Function: OcrBadge
 * Path: frontend/src/pages/dashboard/InvoicesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function OcrBadge({source}) {
    if (!source) return null;
    const labels = {claude_vision: 'Claude Vision', mindee: 'Mindee', manual: 'Manual'};
    return (
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-blue-50 text-blue-700">
            {labels[ source ] || source}
        </span>
    );
}
/**
 * @generated FunctionHeader
 * Function: InvoicesPage
 * Path: frontend/src/pages/dashboard/InvoicesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function InvoicesPage() {
    const {api, isManager, loading} = useAuth();
    const router = useRouter();

    const [invoices, setInvoices] = useState([]);
    const [fetching, setFetching] = useState(true);
    const [modalOpen, setModalOpen] = useState(false);
    const [docType, setDocType] = useState('invoice');
    const [statusFilter, setStatusFilter] = useState('');
    const [search, setSearch] = useState('');
    const [deleting, setDeleting] = useState(null);

    useEffect(() => {
        if (loading) return;
        if (!isManager()) {
            router.replace('/dashboard');
        }
    }, [loading, isManager, router]);

    const fetchInvoices = useCallback(async () => {
        setFetching(true);
        try {
            const params = {};
            if (statusFilter) params.status = statusFilter;
            const {data} = await api.get('/invoices', {params});
            setInvoices(data.invoices || []);
        } catch {
            toast.error('Failed to load invoices');
        } finally {
            setFetching(false);
        }
    }, [api, statusFilter]);

    useEffect(() => {
        if (!loading) fetchInvoices();
    }, [fetchInvoices, loading]);
    /**
     * @generated FunctionHeader
     * Function: downloadReceipt
     * Path: frontend/src/pages/dashboard/InvoicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const downloadReceipt = async (id) => {
        try {
            const response = await api.get(`/invoices/${id}/receipt`, {responseType: 'blob'});
            const url = URL.createObjectURL(new Blob([response.data], {type: 'application/pdf'}));
            const link = document.createElement('a');
            link.href = url;
            link.download = `receipt_${id}.pdf`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            // Defer revocation so the browser has time to initiate the download
            setTimeout(() => URL.revokeObjectURL(url), 100);
        } catch {
            toast.error('Failed to download receipt');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/InvoicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (id) => {
        if (!window.confirm('Delete this pending invoice?')) return;
        setDeleting(id);
        try {
            await api.delete(`/invoices/${id}`);
            setInvoices(prev => prev.filter(inv => inv.id !== id));
            toast.success('Invoice deleted');
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Delete failed');
        } finally {
            setDeleting(null);
        }
    };

    const filtered = useMemo(() => {
        if (!search) return invoices;
        const q = search.toLowerCase();
        return invoices.filter(inv => {
            const vendor = ( ( inv.confirmed_data || inv.parsed_data || {} ).vendor_name || '' ).toLowerCase();
            const invNo = ( ( inv.confirmed_data || inv.parsed_data || {} ).invoice_number || '' ).toLowerCase();
            return vendor.includes(q) || invNo.includes(q) || inv.file_name.toLowerCase().includes(q);
        });
    }, [invoices, search]);

    const stats = useMemo(() => {
        let pending = 0;
        let confirmed = 0;
        let total_amount = 0;
        for (const i of invoices) {
            if (i.status === 'pending_review') pending++;
            else if (i.status === 'confirmed') {
                confirmed++;
                total_amount += ( i.confirmed_data || {} ).total || 0;
            }
        }
        return {total: invoices.length, pending, confirmed, total_amount};
    }, [invoices]);

    if (loading || !isManager()) return null;

    return (
        <div className="p-6 space-y-6 max-w-7xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-[#2F4F4F]">Invoices &amp; Quotes</h1>
                    <p className="text-sm text-gray-500 mt-0.5">Upload PDFs — Claude Vision extracts details and posts
                        expenses to the ledger.</p>
                </div>
                <div className="flex gap-2">
                    <Button
                        data-testid="upload-invoice-btn"
                        onClick={() => {
                            setDocType('invoice');
                            setModalOpen(true);
                        }}
                        className="bg-[#2F4F4F] hover:bg-[#2F4F4F]/90 text-white rounded-full"
                    >
                        <Upload className="h-4 w-4 mr-2"/> Upload Invoice
                    </Button>
                    <Button
                        variant="outline"
                        data-testid="upload-quote-btn"
                        onClick={() => {
                            setDocType('quote');
                            setModalOpen(true);
                        }}
                        className="rounded-full"
                    >
                        <FileText className="h-4 w-4 mr-2"/> Upload Quote
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-4 gap-4">
                {[
                    {label: 'Total Uploaded', value: stats.total, icon: Receipt},
                    {label: 'Pending Review', value: stats.pending, icon: Clock, warn: stats.pending > 0},
                    {label: 'Confirmed', value: stats.confirmed, icon: CheckCircle},
                    {
                        label: 'Total Confirmed',
                        value: `$${stats.total_amount.toLocaleString('en-AU', {minimumFractionDigits: 2})}`,
                        icon: Receipt
                    },
                ].map(({label, value, icon: Icon, warn}) => (
                    <div key={label}
                         className={`rounded-xl border p-4 flex items-center gap-3 shadow-sm ${warn ? 'border-yellow-300 bg-yellow-50' : 'bg-white'}`}>
                        <Icon className={`h-8 w-8 ${warn ? 'text-yellow-500' : 'text-[#2F4F4F]'}`}/>
                        <div>
                            <p className="text-xs text-gray-500">{label}</p>
                            <p className="text-xl font-bold text-gray-800">{value}</p>
                        </div>
                    </div>
                ))}
            </div>

            {/* Filters */}
            <div className="flex gap-3 items-center">
                <div className="relative flex-1 max-w-xs">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400"/>
                    <Input
                        data-testid="search-input"
                        className="pl-9"
                        placeholder="Search vendor, invoice #…"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                    />
                </div>
                <Select value={statusFilter || 'all'} onValueChange={v => setStatusFilter(v === 'all' ? '' : v)}>
                    <SelectTrigger className="w-44" data-testid="status-filter">
                        <SelectValue placeholder="All statuses"/>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All statuses</SelectItem>
                        <SelectItem value="pending_review">Pending Review</SelectItem>
                        <SelectItem value="confirmed">Confirmed</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {/* Table */}
            <div className="bg-white rounded-xl border shadow-sm overflow-hidden">
                {fetching ? (
                    <div className="p-12 text-center text-gray-400" data-testid="loading-state">
                        <Receipt className="h-10 w-10 mx-auto mb-3 animate-pulse"/>
                        <p>Loading invoices…</p>
                    </div>
                ) : filtered.length === 0 ? (
                    <div className="p-12 text-center text-gray-400" data-testid="empty-state">
                        <Receipt className="h-10 w-10 mx-auto mb-3 opacity-40"/>
                        <p className="font-medium">No invoices yet</p>
                        <p className="text-sm mt-1">Upload an invoice or quote to get started.</p>
                    </div>
                ) : (
                    <table className="w-full text-sm" data-testid="invoices-table">
                        <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                        <tr>
                            <th className="text-left p-3">File</th>
                            <th className="text-left p-3">Vendor</th>
                            <th className="text-left p-3">Invoice #</th>
                            <th className="text-right p-3">Total</th>
                            <th className="text-left p-3">Date</th>
                            <th className="text-left p-3">Status</th>
                            <th className="text-left p-3">OCR</th>
                            <th className="text-right p-3">Actions</th>
                        </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                        {filtered.map(inv => {
                            const d = inv.confirmed_data || inv.parsed_data || {};
                            return (
                                <tr key={inv.id} className="hover:bg-gray-50 transition-colors">
                                    <td className="p-3">
                                        <div className="flex items-center gap-2">
                                            <FileText className="h-4 w-4 text-gray-400 shrink-0"/>
                                            <span
                                                className="truncate max-w-[120px] text-gray-700">{inv.file_name}</span>
                                        </div>
                                    </td>
                                    <td className="p-3 font-medium text-gray-800 max-w-[140px] truncate">{d.vendor_name || '—'}</td>
                                    <td className="p-3 text-gray-500">{d.invoice_number || '—'}</td>
                                    <td className="p-3 text-right font-medium">
                                        {d.total != null ? `$${parseFloat(d.total).toLocaleString('en-AU', {minimumFractionDigits: 2})}` : '—'}
                                    </td>
                                    <td className="p-3 text-gray-500">{d.invoice_date || '—'}</td>
                                    <td className="p-3"><StatusBadge status={inv.status}/></td>
                                    <td className="p-3"><OcrBadge source={inv.ocr_source}/></td>
                                    <td className="p-3">
                                        <div className="flex items-center justify-end gap-1">
                                            {inv.receipt_generated && (
                                                <Button
                                                    variant="ghost" size="icon"
                                                    title="Download receipt"
                                                    aria-label={`Download receipt for ${d.vendor_name || inv.file_name}`}
                                                    onClick={() => downloadReceipt(inv.id)}
                                                >
                                                    <Download className="h-4 w-4 text-[#2F4F4F]"/>
                                                </Button>
                                            )}
                                            {inv.status === 'pending_review' && (
                                                <Button
                                                    variant="ghost" size="icon"
                                                    title="Delete"
                                                    aria-label={`Delete
                                                    invoice from
                                                    ${d.vendor_name || inv.file_name}`}
                                                    disabled={deleting === inv.id}
                                                    onClick={() => handleDelete(inv.id)}
                                                    data-testid={`delete-${inv.id}`}
                                                >
                                                    <Trash2 className="h-4 w-4 text-red-400"/>
                                                </Button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                        </tbody>
                    </table>
                )}
            </div>

            <InvoiceUploadModal
                open={modalOpen}
                onClose={() => setModalOpen(false)}
                onUploaded={fetchInvoices}
                onConfirmed={fetchInvoices}
                documentType={docType}
            />
        </div>
    );
}
