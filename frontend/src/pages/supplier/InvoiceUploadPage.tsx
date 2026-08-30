// @featuretrace:ap_automation — Supplier-facing invoice upload with OCR confidence feedback.
// Layer: frontend
// Data flow: InvoiceUploadPage → POST /ap/supplier-upload/ → invoice_documents (draft)
//                              → OCR extraction → ABN validation → duplicate check.
// Related: backend/routers/ap_supplier_upload.py
//           backend/integrations/abn_validator.py
//           frontend/src/app/supplier/invoice-upload/page.tsx
// Scope: (building-scoped)

"use client";

import React, {useCallback, useRef, useState} from "react";
import {
    AlertTriangle,
    CheckCircle2,
    FileText,
    Loader2,
    Shield,
    Upload,
    XCircle,
} from "lucide-react";
import {useAuth} from "../../contexts/AuthContext";
import {Badge} from "../../components/ui/badge";
import {Button} from "../../components/ui/button";
import {Card, CardContent, CardHeader, CardTitle} from "../../components/ui/card";

import {formatMoneyFromCents} from '@/lib/currency';
interface UploadResult {
    invoice_id: string;
    state: string;
    vendor_name?: string;
    vendor_abn?: string;
    invoice_number?: string;
    issue_date?: string;
    due_date?: string;
    total_cents?: number;
    gst_cents?: number;
    is_tax_invoice: boolean;
    abn_valid?: boolean;
    abn_entity_name?: string;
    withholding_required: boolean;
    overall_ocr_confidence: number;
    low_confidence_fields: string[];
}
/**
 * @generated FunctionHeader
 * Function: CONFIDENCE_COLOUR
 * Path: frontend/src/pages/supplier/InvoiceUploadPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CONFIDENCE_COLOUR = (conf: number) =>
    conf >= 0.80 ? "text-green-700" : conf >= 0.60 ? "text-amber-600" : "text-red-600";
/**
 * @generated FunctionHeader
 * Function: InvoiceUploadPage
 * Path: frontend/src/pages/supplier/InvoiceUploadPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function InvoiceUploadPage() {
    const {api} = useAuth() as any;

    const [dragOver, setDragOver] = useState(false);
    const [file, setFile] = useState<File | null>(null);
    const [uploading, setUploading] = useState(false);
    const [result, setResult] = useState<UploadResult | null>(null);
    const [error, setError] = useState<string | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    const handleFile = useCallback((f: File) => {
        setFile(f);
        setResult(null);
        setError(null);
    }, []);

    const onDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragOver(false);
        const f = e.dataTransfer.files[0];
        if (f) handleFile(f);
    }, [handleFile]);

    const onInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const f = e.target.files?.[0];
        if (f) handleFile(f);
    }, [handleFile]);

    const onSubmit = useCallback(async () => {
        if (!file) return;
        setUploading(true);
        setError(null);
        setResult(null);
        try {
            const form = new FormData();
            form.append("file", file);
            const res = await api.post("/ap/supplier-upload/", form, {
                headers: {"Content-Type": "multipart/form-data"},
            });
            setResult(res.data);
            setFile(null);
            if (inputRef.current) inputRef.current.value = "";
        } catch (err: any) {
            const detail = err?.response?.data?.detail;
            if (typeof detail === "object" && detail?.message) {
                setError(`${detail.message} (Invoice ID: ${detail.existing_invoice_id ?? "unknown"})`);
            } else {
                setError(typeof detail === "string" ? detail : "Upload failed. Please try again.");
            }
        } finally {
            setUploading(false);
        }
    }, [api, file]);

    const reset = useCallback(() => {
        setFile(null);
        setResult(null);
        setError(null);
        if (inputRef.current) inputRef.current.value = "";
    }, []);

    return (
        <div className="min-h-screen bg-gray-50 flex items-start justify-center pt-16 px-4">
            <div className="w-full max-w-xl space-y-6" data-testid="invoice-upload-page">
                <div className="text-center">
                    <h1 className="text-2xl font-bold text-gray-900">Submit a Supplier Invoice</h1>
                    <p className="text-sm text-gray-500 mt-1">
                        Upload your invoice as a PDF. Our system will extract the details automatically.
                    </p>
                </div>

                {/* Success result */}
                {result && (
                    <Card className="border-green-200 bg-green-50" data-testid="upload-result">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-base text-green-800 flex items-center gap-2">
                                <CheckCircle2 className="w-5 h-5"/>
                                Invoice submitted successfully
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4 text-sm">
                            {/* Extracted fields */}
                            <div className="grid grid-cols-2 gap-3">
                                {[
                                    {label: "Vendor", value: result.vendor_name || "—"},
                                    {label: "ABN", value: result.vendor_abn || "—"},
                                    {label: "Invoice #", value: result.invoice_number || "—"},
                                    {label: "Issue Date", value: result.issue_date || "—"},
                                    {label: "Due Date", value: result.due_date || "—"},
                                    {label: "Total (inc. GST)", value: formatMoneyFromCents(result.total_cents)},
                                    {label: "GST", value: formatMoneyFromCents(result.gst_cents)},
                                    {label: "Tax Invoice", value: result.is_tax_invoice ? "Yes" : "No"},
                                ].map(({label, value}) => (
                                    <div key={label}>
                                        <p className="text-xs text-gray-500">{label}</p>
                                        <p className="font-medium text-gray-800"
                                           data-testid={`field-${label.toLowerCase().replace(/\s+/g, "-")}`}>{value}</p>
                                    </div>
                                ))}
                            </div>

                            {/* ABN status */}
                            {result.abn_valid != null && (
                                <div
                                    className={`flex items-center gap-2 text-xs ${result.abn_valid ? "text-green-700" : "text-amber-700"}`}
                                    data-testid="abn-status">
                                    {result.abn_valid
                                        ? <><Shield className="w-3 h-3"/> ABN verified — {result.abn_entity_name}</>
                                        : <><AlertTriangle className="w-3 h-3"/> ABN could not be verified</>
                                    }
                                </div>
                            )}

                            {/* 47% withholding */}
                            {result.withholding_required && (
                                <div
                                    className="flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800"
                                    data-testid="withholding-warning">
                                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5"/>
                                    <span>
                    <strong>47% ATO withholding may apply.</strong> The ABN provided is invalid and the supply
                    exceeds $75 ex-GST. Please supply a valid ABN to avoid withholding.
                  </span>
                                </div>
                            )}

                            {/* OCR confidence */}
                            <div className="flex items-center justify-between text-xs" data-testid="ocr-confidence-row">
                                <span className="text-gray-500">Extraction confidence</span>
                                <span className={`font-semibold ${CONFIDENCE_COLOUR(result.overall_ocr_confidence)}`}>
                  {Math.round(result.overall_ocr_confidence * 100)}%
                </span>
                            </div>
                            {result.low_confidence_fields.length > 0 && (
                                <div className="text-xs text-amber-700 flex items-center gap-1"
                                     data-testid="low-confidence-fields">
                                    <AlertTriangle className="w-3 h-3 shrink-0"/>
                                    Low confidence: {result.low_confidence_fields.join(", ")} — the strata manager will
                                    review these fields.
                                </div>
                            )}

                            <div className="text-xs text-gray-500">
                                Invoice ID: <code className="font-mono">{result.invoice_id}</code> · Status: <Badge
                                variant="outline" className="text-xs">{result.state}</Badge>
                            </div>
                            <Button variant="outline" size="sm" onClick={reset} className="w-full"
                                    data-testid="upload-another-btn">
                                Upload another invoice
                            </Button>
                        </CardContent>
                    </Card>
                )}

                {/* Upload area */}
                {!result && (
                    <Card>
                        <CardContent className="p-6 space-y-4">
                            {/* Drop zone */}
                            <div
                                data-testid="drop-zone"
                                onDragOver={e => {
                                    e.preventDefault();
                                    setDragOver(true);
                                }}
                                onDragLeave={() => setDragOver(false)}
                                onDrop={onDrop}
                                onClick={() => inputRef.current?.click()}
                                className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors ${
                                    dragOver
                                        ? "border-teal-500 bg-teal-50"
                                        : file
                                            ? "border-green-400 bg-green-50"
                                            : "border-gray-300 hover:border-gray-400 bg-gray-50"
                                }`}
                            >
                                <input
                                    ref={inputRef}
                                    type="file"
                                    accept="application/pdf,text/plain"
                                    className="hidden"
                                    onChange={onInputChange}
                                    data-testid="file-input"
                                    aria-label="Upload invoice PDF"
                                />
                                {file ? (
                                    <div className="space-y-2">
                                        <FileText className="w-10 h-10 mx-auto text-green-600"/>
                                        <p className="text-sm font-medium text-gray-800"
                                           data-testid="selected-filename">{file.name}</p>
                                        <p className="text-xs text-gray-500">{(file.size / 1024).toFixed(1)} KB — click
                                            to change</p>
                                    </div>
                                ) : (
                                    <div className="space-y-2">
                                        <Upload className="w-10 h-10 mx-auto text-gray-400"/>
                                        <p className="text-sm font-medium text-gray-700">Drag & drop or click to
                                            upload</p>
                                        <p className="text-xs text-gray-400">PDF or plain text · max 20 MB</p>
                                    </div>
                                )}
                            </div>

                            {/* Error */}
                            {error && (
                                <div
                                    className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700"
                                    data-testid="upload-error">
                                    <XCircle className="w-4 h-4 shrink-0 mt-0.5"/>
                                    {error}
                                </div>
                            )}

                            {/* Submit */}
                            <Button
                                className="w-full bg-teal-700 hover:bg-teal-800 text-white"
                                disabled={!file || uploading}
                                onClick={onSubmit}
                                data-testid="submit-btn"
                            >
                                {uploading ? (
                                    <><Loader2 className="w-4 h-4 mr-2 animate-spin"/> Processing…</>
                                ) : (
                                    <><Upload className="w-4 h-4 mr-2"/> Submit Invoice</>
                                )}
                            </Button>

                            <p className="text-xs text-center text-gray-400">
                                Your invoice will be reviewed by the strata manager before payment is processed.
                            </p>
                        </CardContent>
                    </Card>
                )}
            </div>
        </div>
    );
}