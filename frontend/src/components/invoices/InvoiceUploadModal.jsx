// @featuretrace:invoices — 3-step upload modal: drag-drop → OCR review/edit → confirm & receipt.
// Layer: frontend
// Data flow: InvoiceUploadModal → POST /invoices/upload-and-parse → pending_review record;
//            → POST /invoices/{id}/confirm → confirmed record + financial_transaction.
// Related: frontend/src/pages/dashboard/InvoicesPage.jsx
//           backend/routers/invoices.py
// Scope: (building-scoped)

import React, { useRef, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Badge } from '../ui/badge';
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '../ui/dialog';
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../ui/select';
import { toast } from 'sonner';
import { Upload, FileText, CheckCircle, Loader2, Plus, Trash2 } from 'lucide-react';

const ACCEPTED = '.pdf,.jpg,.jpeg,.png,.webp';

const FUND_OPTIONS = [
    {value: 'admin', label: 'Administrative Fund'},
    {value: 'sinking', label: 'Sinking Fund'},
];

const CATEGORY_OPTIONS = [
    'General Maintenance', 'Plumbing', 'Electrical', 'Gardening & Landscaping',
    'Cleaning', 'Insurance', 'Pest Control', 'Painting', 'Structural Repairs',
    'Capital Works', 'Legal & Professional', 'Management Fees', 'Other',
];
/**
 * @generated FunctionHeader
 * Function: ConfidenceBadge
 * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ConfidenceBadge({source, confidence}) {
    const pct = Math.round(( confidence || 0 ) * 100);
    const colour = pct >= 80 ? 'bg-green-100 text-green-800' : pct >= 50 ? 'bg-yellow-100 text-yellow-800' : 'bg-gray-100 text-gray-600';
    const label = source === 'claude_vision' ? 'Claude Vision' : source === 'mindee' ? 'Mindee' : source === 'unlimited_ocr' ? 'Unlimited OCR' : 'Manual entry';
    return (
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${colour}`}>
            {label}{pct > 0 ? ` — ${pct}% confidence` : ''}
        </span>
    );
}
/**
 * @generated FunctionHeader
 * Function: InvoiceUploadModal
 * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function InvoiceUploadModal({open, onClose, onConfirmed, onUploaded, documentType = 'invoice'}) {
    const {api} = useAuth();
    const fileInputRef = useRef(null);
    const [step, setStep] = useState('upload'); // upload | reviewing | done
    const [dragging, setDragging] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const [invoiceId, setInvoiceId] = useState(null);
    const [receiptUrl, setReceiptUrl] = useState(null);

    const [form, setForm] = useState({
        vendor_name: '', abn: '', invoice_number: '',
        invoice_date: '', due_date: '',
        subtotal: '', gst: '', total: '',
        fund: 'admin', category: 'General Maintenance',
        line_items: [],
    });
    const [ocrMeta, setOcrMeta] = useState({source: null, confidence: 0});
    /**
     * @generated FunctionHeader
     * Function: resetModal
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const resetModal = () => {
        setStep('upload');
        setUploading(false);
        setConfirming(false);
        setInvoiceId(null);
        setReceiptUrl(null);
        setForm({
            vendor_name: '', abn: '', invoice_number: '',
            invoice_date: '', due_date: '',
            subtotal: '', gst: '', total: '',
            fund: 'admin', category: 'General Maintenance',
            line_items: [],
        });
        setOcrMeta({source: null, confidence: 0});
    };
    /**
     * @generated FunctionHeader
     * Function: handleClose
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleClose = () => {
        resetModal();
        onClose();
    };
    /**
     * @generated FunctionHeader
     * Function: processFile
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const processFile = async (file) => {
        setUploading(true);
        const fd = new FormData();
        fd.append('file', file);
        fd.append('document_type', documentType);
        try {
            const {data} = await api.post('/invoices/upload-and-parse', fd, {
                headers: {'Content-Type': 'multipart/form-data'},
            });
            const p = data.parsed_data || {};
            setInvoiceId(data.id);
            setOcrMeta({source: data.ocr_source, confidence: data.ocr_confidence});
            setForm({
                vendor_name: p.vendor_name || '',
                abn: p.abn || '',
                invoice_number: p.invoice_number || '',
                invoice_date: p.invoice_date || '',
                due_date: p.due_date || '',
                subtotal: p.subtotal != null ? String(p.subtotal) : '',
                gst: p.gst != null ? String(p.gst) : '',
                total: p.total != null ? String(p.total) : '',
                fund: p.suggested_fund || 'admin',
                category: p.suggested_category || 'General Maintenance',
                line_items: ( p.line_items || [] ).map(i => ( {...i} )),
            });
            setStep('reviewing');
            if (onUploaded) onUploaded();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Upload failed');
        } finally {
            setUploading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: onFileChange
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const onFileChange = (e) => {
        if (e.target.files?.[ 0 ]) processFile(e.target.files[ 0 ]);
    };
    /**
     * @generated FunctionHeader
     * Function: onDrop
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const onDrop = (e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files?.[ 0 ]) processFile(e.dataTransfer.files[ 0 ]);
    };
    /**
     * @generated FunctionHeader
     * Function: setField
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const setField = (key, val) => setForm(f => ( {...f, [ key ]: val} ));
    /**
     * @generated FunctionHeader
     * Function: recalcTotals
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const recalcTotals = (items) => {
        const subtotal = items.reduce((s, i) => s + ( parseFloat(i.total) || 0 ), 0);
        // subtotal is ex-GST; Australian GST = 10% on exclusive amount
        const gst = Math.round(subtotal / 10 * 100) / 100;
        const total = Math.round(( subtotal + gst ) * 100) / 100;
        setForm(f => ( {
            ...f, line_items: items,
            subtotal: subtotal.toFixed(2),
            gst: gst.toFixed(2),
            total: total.toFixed(2),
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: updateLineItem
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateLineItem = (idx, key, val) => {
        const items = form.line_items.map((item, i) => {
            if (i !== idx) return item;
            const updated = {...item, [ key ]: val};
            if (key === 'qty' || key === 'unit_price') {
                updated.total = ( parseFloat(updated.qty || 0) * parseFloat(updated.unit_price || 0) ).toFixed(2);
            }
            return updated;
        });
        recalcTotals(items);
    };
    /**
     * @generated FunctionHeader
     * Function: addLineItem
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const addLineItem = () => recalcTotals([...form.line_items, {
        description: '',
        qty: 1,
        unit_price: '',
        total: '0.00'
    }]);
    /**
     * @generated FunctionHeader
     * Function: removeLineItem
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeLineItem = (idx) => recalcTotals(form.line_items.filter((_, i) => i !== idx));
    /**
     * @generated FunctionHeader
     * Function: downloadReceipt
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const downloadReceipt = async () => {
        try {
            const response = await api.get(receiptUrl, {responseType: 'blob'});
            const url = URL.createObjectURL(new Blob([response.data], {type: 'application/pdf'}));
            const link = document.createElement('a');
            link.href = url;
            link.download = 'receipt.pdf';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            setTimeout(() => URL.revokeObjectURL(url), 100);
        } catch {
            toast.error('Failed to download receipt');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleConfirm
     * Path: frontend/src/components/invoices/InvoiceUploadModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleConfirm = async () => {
        if (!form.vendor_name.trim()) {
            toast.error('Vendor name is required');
            return;
        }
        if (!form.invoice_date) {
            toast.error('Invoice date is required');
            return;
        }
        if (!form.total || parseFloat(form.total) <= 0) {
            toast.error('Total must be greater than zero');
            return;
        }

        setConfirming(true);
        try {
            const payload = {
                vendor_name: form.vendor_name,
                abn: form.abn || null,
                invoice_number: form.invoice_number || null,
                invoice_date: form.invoice_date,
                due_date: form.due_date || null,
                line_items: form.line_items.map(i => ( {
                    description: i.description,
                    qty: parseFloat(i.qty) || 1,
                    unit_price: parseFloat(i.unit_price) || 0,
                    total: parseFloat(i.total) || 0,
                } )),
                subtotal: parseFloat(form.subtotal) || 0,
                gst: parseFloat(form.gst) || 0,
                total: parseFloat(form.total),
                fund: form.fund,
                category: form.category,
                document_type: documentType,
            };
            await api.post(`/invoices/${invoiceId}/confirm`, payload);
            setReceiptUrl(`/invoices/${invoiceId}/receipt`);
            setStep('done');
            if (onConfirmed) onConfirmed();
            toast.success('Invoice confirmed and posted to the ledger');
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Confirmation failed');
        } finally {
            setConfirming(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(v) => {
            if (!v) handleClose();
        }}>
            <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>
                        {step === 'upload' && `Upload ${documentType === 'quote' ? 'Quote' : 'Invoice'}`}
                        {step === 'reviewing' && 'Review Extracted Details'}
                        {step === 'done' && 'Invoice Confirmed'}
                    </DialogTitle>
                </DialogHeader>

                {/* ── Step 1: Upload ── */}
                {step === 'upload' && (
                    <div
                        data-testid="upload-dropzone"
                        role="button"
                        tabIndex={uploading ? -1 : 0}
                        aria-label={`Upload ${documentType === 'quote' ? 'quote' : 'invoice'} — click or press Enter to browse files, or drag and drop`}
                        aria-busy={uploading}
                        aria-disabled={uploading}
                        className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2F4F4F] focus-visible:ring-offset-2 ${dragging ? 'border-[#2F4F4F] bg-[#2F4F4F]/5' : 'border-gray-300 hover:border-[#2F4F4F]'} ${uploading ? 'pointer-events-none opacity-75' : ''}`}
                        onDragOver={(e) => {
                            e.preventDefault();
                            setDragging(true);
                        }}
                        onDragLeave={() => setDragging(false)}
                        onDrop={onDrop}
                        onClick={() => !uploading && fileInputRef.current?.click()}
                        onKeyDown={(e) => {
                            if (( e.key === 'Enter' || e.key === ' ' ) && !uploading) {
                                e.preventDefault();
                                fileInputRef.current?.click();
                            }
                        }}
                    >
                        <input ref={fileInputRef} type="file" accept={ACCEPTED} className="sr-only"
                               onChange={onFileChange} data-testid="file-input" tabIndex={-1}/>
                        {uploading ? (
                            <div className="flex flex-col items-center gap-3" aria-live="polite" aria-atomic="true">
                                <Loader2 className="h-10 w-10 animate-spin text-[#2F4F4F]" aria-hidden="true"/>
                                <p className="text-sm text-gray-500">Parsing with Claude Vision…</p>
                            </div>
                        ) : (
                            <div className="flex flex-col items-center gap-3">
                                <Upload className="h-10 w-10 text-gray-400" aria-hidden="true"/>
                                <p className="font-medium text-gray-700">Drop PDF or image here</p>
                                <p className="text-sm text-gray-400">PDF, JPEG, PNG, WebP · max 20 MB</p>
                            </div>
                        )}
                    </div>
                )}

                {/* ── Step 2: Review ── */}
                {step === 'reviewing' && (
                    <div className="space-y-5" data-testid="review-form">
                        <div className="flex items-center justify-between">
                            <p className="text-sm text-gray-500">Review and correct the extracted details before posting
                                to the ledger.</p>
                            <ConfidenceBadge source={ocrMeta.source} confidence={ocrMeta.confidence}/>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div className="col-span-2">
                                <Label htmlFor="inv-vendor-name">Vendor / Supplier Name *</Label>
                                <Input id="inv-vendor-name" data-testid="vendor-name" value={form.vendor_name}
                                       onChange={e => setField('vendor_name', e.target.value)}
                                       placeholder="ABC Plumbing Pty Ltd"/>
                            </div>
                            <div>
                                <Label htmlFor="inv-abn">ABN</Label>
                                <Input id="inv-abn" value={form.abn} onChange={e => setField('abn', e.target.value)}
                                       placeholder="12 345 678 901"/>
                            </div>
                            <div>
                                <Label htmlFor="inv-invoice-number">Invoice Number</Label>
                                <Input id="inv-invoice-number" value={form.invoice_number}
                                       onChange={e => setField('invoice_number', e.target.value)}
                                       placeholder="INV-001"/>
                            </div>
                            <div>
                                <Label htmlFor="inv-invoice-date">Invoice Date *</Label>
                                <Input id="inv-invoice-date" type="date" data-testid="invoice-date"
                                       value={form.invoice_date}
                                       onChange={e => setField('invoice_date', e.target.value)}/>
                            </div>
                            <div>
                                <Label htmlFor="inv-due-date">Due Date</Label>
                                <Input id="inv-due-date" type="date" value={form.due_date}
                                       onChange={e => setField('due_date', e.target.value)}/>
                            </div>
                            <div>
                                <Label htmlFor="inv-fund">Fund *</Label>
                                <Select value={form.fund} onValueChange={v => setField('fund', v)}>
                                    <SelectTrigger id="inv-fund"
                                                   data-testid="fund-select"><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        {FUND_OPTIONS.map(o => <SelectItem key={o.value}
                                                                           value={o.value}>{o.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div>
                                <Label htmlFor="inv-category">Category</Label>
                                <Select value={form.category} onValueChange={v => setField('category', v)}>
                                    <SelectTrigger id="inv-category"><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        {CATEGORY_OPTIONS.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>

                        {/* Line items */}
                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <Label>Line Items</Label>
                                <Button type="button" variant="outline" size="sm" onClick={addLineItem}>
                                    <Plus className="h-3 w-3 mr-1"/> Add row
                                </Button>
                            </div>
                            {form.line_items.length > 0 && (
                                <div className="border rounded-lg overflow-hidden">
                                    <table className="w-full text-sm">
                                        <thead className="bg-gray-50">
                                        <tr>
                                            <th className="text-left p-2 font-medium">Description</th>
                                            <th className="text-right p-2 font-medium w-16">Qty</th>
                                            <th className="text-right p-2 font-medium w-24">Unit Price</th>
                                            <th className="text-right p-2 font-medium w-24">Total</th>
                                            <th className="w-8"></th>
                                        </tr>
                                        </thead>
                                        <tbody>
                                        {form.line_items.map((item, idx) => (
                                            <tr key={idx} className="border-t">
                                                <td className="p-1"><Input value={item.description}
                                                                           onChange={e => updateLineItem(idx, 'description', e.target.value)}
                                                                           className="h-7 text-xs"/></td>
                                                <td className="p-1"><Input type="number" value={item.qty}
                                                                           onChange={e => updateLineItem(idx, 'qty', e.target.value)}
                                                                           className="h-7 text-xs text-right"/></td>
                                                <td className="p-1"><Input type="number" value={item.unit_price}
                                                                           onChange={e => updateLineItem(idx, 'unit_price', e.target.value)}
                                                                           className="h-7 text-xs text-right"/></td>
                                                <td className="p-2 text-right text-xs font-medium">${parseFloat(item.total || 0).toFixed(2)}</td>
                                                <td className="p-1"><Button type="button" variant="ghost" size="icon"
                                                                            className="h-6 w-6"
                                                                            aria-label={`Remove line item: ${item.description || `row ${idx + 1}`}`}
                                                                            onClick={() => removeLineItem(idx)}><Trash2
                                                    className="h-3 w-3 text-red-400" aria-hidden="true"/></Button></td>
                                            </tr>
                                        ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>

                        {/* Totals */}
                        <div className="grid grid-cols-3 gap-4 border-t pt-4">
                            <div>
                                <Label htmlFor="inv-subtotal">Subtotal (excl. GST)</Label>
                                <Input id="inv-subtotal" type="number" value={form.subtotal}
                                       onChange={e => setField('subtotal', e.target.value)} placeholder="0.00"/>
                            </div>
                            <div>
                                <Label htmlFor="inv-gst">GST</Label>
                                <Input id="inv-gst" type="number" value={form.gst}
                                       onChange={e => setField('gst', e.target.value)} placeholder="0.00"/>
                            </div>
                            <div>
                                <Label htmlFor="inv-total">Total (incl. GST) *</Label>
                                <Input id="inv-total" data-testid="total-input" type="number" value={form.total}
                                       onChange={e => setField('total', e.target.value)} placeholder="0.00"
                                       className="font-semibold"/>
                            </div>
                        </div>
                    </div>
                )}

                {/* ── Step 3: Done ── */}
                {step === 'done' && (
                    <div className="text-center py-8 space-y-4" data-testid="done-screen">
                        <CheckCircle className="h-14 w-14 text-green-500 mx-auto"/>
                        <div>
                            <p className="text-lg font-semibold">Invoice posted to ledger</p>
                            <p className="text-sm text-gray-500 mt-1">An expense transaction has been recorded in the
                                financial ledger.</p>
                        </div>
                        {receiptUrl && (
                            <Button variant="outline" onClick={downloadReceipt}>
                                <FileText className="h-4 w-4 mr-2"/> Download Receipt PDF
                            </Button>
                        )}
                    </div>
                )}

                <DialogFooter className="gap-2">
                    {step === 'reviewing' && (
                        <>
                            <Button variant="outline" onClick={handleClose} disabled={confirming}>Cancel</Button>
                            <Button data-testid="confirm-button" onClick={handleConfirm} disabled={confirming}
                                    className="bg-[#2F4F4F] hover:bg-[#2F4F4F]/90 text-white rounded-full">
                                {confirming ? <><Loader2
                                    className="h-4 w-4 animate-spin mr-2"/>Posting…</> : 'Confirm & Post to Ledger'}
                            </Button>
                        </>
                    )}
                    {step === 'done' && (
                        <Button onClick={handleClose}
                                className="bg-[#2F4F4F] hover:bg-[#2F4F4F]/90 text-white rounded-full">Done</Button>
                    )}
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}