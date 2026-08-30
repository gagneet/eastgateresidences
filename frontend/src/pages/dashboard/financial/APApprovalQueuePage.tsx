// @featuretrace:ap_automation — AP invoice approval queue for strata managers.
// Layer: frontend
// Data flow: APApprovalQueuePage → GET /ap/invoices → invoice_documents (building-scoped)
//                                → POST /ap/invoices/{id}/approve → invoice_documents + jurisdictional gate
//                                → POST /ap/invoices/{id}/reject  → invoice_documents
// Related: backend/routers/ap_approval.py
//           backend/domain/invoice_lifecycle.py
//           frontend/src/app/(dashboard)/financials/ap-approval/page.tsx

"use client";

import React, {useCallback, useEffect, useState} from "react";
import {useRouter} from "next/navigation";
import {
    AlertTriangle,
    CheckCircle2,
    ChevronDown,
    ChevronRight,
    Clock,
    FileText,
    Loader2,
    RefreshCw,
    Shield,
    XCircle,
} from "lucide-react";
import {useAuth} from "../../../contexts/AuthContext";
import {Badge} from "../../../components/ui/badge";
import {Button} from "../../../components/ui/button";
import {Card, CardContent, CardHeader, CardTitle} from "../../../components/ui/card";

import {formatMoneyFromCents} from '@/lib/currency';
const STATE_TABS = [
    {key: "validated", label: "Awaiting Approval"},
    {key: "approved", label: "Approved"},
    {key: "scheduled", label: "Scheduled"},
    {key: "paid", label: "Paid"},
    {key: "rejected", label: "Rejected"},
    {key: "draft", label: "Drafts"},
];

interface InvoiceSummary {
    invoice_id: string;
    state: string;
    vendor_name?: string;
    vendor_abn?: string;
    invoice_number?: string;
    issue_date?: string;
    due_date?: string;
    total_cents?: number;
    abn_valid?: boolean;
    withholding_required: boolean;
    created_at: string;
}

interface InvoiceDetail extends InvoiceSummary {
    gst_cents?: number;
    is_tax_invoice: boolean;
    description?: string;
    category?: string;
    ocr_confidence?: number;
    low_confidence_fields: string[];
    abn_entity_name?: string;
    abn_status?: string;
    transitions: Array<{
        from_state: string;
        to_state: string;
        transitioned_at: string;
        transitioned_by: string;
        notes?: string
    }>;
    submitted_by?: string;
}
/**
 * @generated FunctionHeader
 * Function: APApprovalQueuePage
 * Path: frontend/src/pages/dashboard/financial/APApprovalQueuePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function APApprovalQueuePage() {
    const {isManager, loading: authLoading, api} = useAuth() as any;
    const router = useRouter();

    const [activeTab, setActiveTab] = useState("validated");
    const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [detail, setDetail] = useState<InvoiceDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [actionLoading, setActionLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading) return;
        if (!isManager()) {
            router.replace("/dashboard");
            return;
        }
    }, [authLoading, isManager, router]);

    const loadInvoices = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.get("/ap/invoices", {params: {status: activeTab}});
            setInvoices(res.data);
            setSelectedId(null);
            setDetail(null);
        } catch {
            setError("Failed to load invoices.");
        } finally {
            setLoading(false);
        }
    }, [api, activeTab]);

    useEffect(() => {
        if (!authLoading && isManager()) loadInvoices();
    }, [loadInvoices, authLoading]);

    const loadDetail = useCallback(async (id: string) => {
        setSelectedId(id);
        setDetail(null);
        setActionError(null);
        try {
            const res = await api.get(`/ap/invoices/${id}`);
            setDetail(res.data);
        } catch {
            setActionError("Failed to load invoice detail.");
        }
    }, [api]);

    const doAction = useCallback(async (invoiceId: string, action: string, body: object = {}) => {
        setActionLoading(true);
        setActionError(null);
        try {
            await api.post(`/ap/invoices/${invoiceId}/${action}`, body);
            await loadInvoices();
        } catch (err: any) {
            const msg = err?.response?.data?.detail;
            setActionError(typeof msg === "string" ? msg : (msg?.message || "Action failed."));
        } finally {
            setActionLoading(false);
        }
    }, [api, loadInvoices]);

    if (authLoading || (!authLoading && !isManager())) return null;

    return (
        <div className="p-6 max-w-7xl mx-auto space-y-6" data-testid="ap-approval-queue-page">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Accounts Payable — Invoice Approval</h1>
                    <p className="text-sm text-gray-500 mt-1">Review, approve, and schedule supplier invoices</p>
                </div>
                <Button variant="outline" size="sm" onClick={loadInvoices} disabled={loading} data-testid="refresh-btn">
                    <RefreshCw className={`w-4 h-4 mr-1 ${loading ? "animate-spin" : ""}`}/>
                    Refresh
                </Button>
            </div>

            {error && (
                <div
                    className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700"
                    data-testid="error-banner">
                    <AlertTriangle className="w-4 h-4 shrink-0"/>
                    {error}
                </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 border-b border-gray-200" data-testid="state-tabs">
                {STATE_TABS.map(tab => (
                    <button
                        key={tab.key}
                        data-testid={`tab-${tab.key}`}
                        onClick={() => {
                            setActiveTab(tab.key);
                        }}
                        className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                            activeTab === tab.key
                                ? "border-teal-600 text-teal-700"
                                : "border-transparent text-gray-500 hover:text-gray-700"
                        }`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Invoice list */}
                <div className="space-y-3">
                    {loading && (
                        <div className="flex justify-center py-12 text-gray-400" data-testid="loading-spinner">
                            <Loader2 className="w-6 h-6 animate-spin"/>
                        </div>
                    )}
                    {!loading && invoices.length === 0 && (
                        <div className="text-center py-12 text-gray-400" data-testid="empty-state">
                            <FileText className="w-8 h-8 mx-auto mb-2 opacity-40"/>
                            <p className="text-sm">No invoices in this state</p>
                        </div>
                    )}
                    {invoices.map(inv => (
                        <Card
                            key={inv.invoice_id}
                            data-testid={`invoice-item-${inv.invoice_id}`}
                            className={`cursor-pointer border transition-all ${
                                selectedId === inv.invoice_id ? "border-teal-500 shadow-md" : "border-gray-200 hover:border-gray-300"
                            }`}
                            onClick={() => loadDetail(inv.invoice_id)}
                        >
                            <CardContent className="p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <p className="font-medium text-sm text-gray-900 truncate">
                                            {inv.vendor_name || "Unknown vendor"}
                                        </p>
                                        <p className="text-xs text-gray-500">
                                            {inv.invoice_number ? `#${inv.invoice_number}` : "No invoice number"} ·
                                            Due {inv.due_date || "—"}
                                        </p>
                                    </div>
                                    <div className="text-right shrink-0">
                                        <p className="font-semibold text-sm text-gray-900">{formatMoneyFromCents(inv.total_cents)}</p>
                                        {inv.withholding_required && (
                                            <Badge variant="destructive" className="text-xs mt-1"
                                                   data-testid="withholding-badge">
                                                47% withholding
                                            </Badge>
                                        )}
                                    </div>
                                </div>
                                <div className="flex items-center gap-2 mt-2">
                                    {inv.abn_valid === false && (
                                        <span className="text-xs text-amber-600 flex items-center gap-1"
                                              data-testid="abn-invalid-flag">
                      <AlertTriangle className="w-3 h-3"/> Invalid ABN
                    </span>
                                    )}
                                    {inv.abn_valid === true && (
                                        <span className="text-xs text-green-600 flex items-center gap-1"
                                              data-testid="abn-valid-flag">
                      <Shield className="w-3 h-3"/> ABN verified
                    </span>
                                    )}
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>

                {/* Detail panel */}
                {selectedId && (
                    <div data-testid="invoice-detail-panel">
                        {!detail ? (
                            <div className="flex justify-center py-12 text-gray-400">
                                <Loader2 className="w-5 h-5 animate-spin"/>
                            </div>
                        ) : (
                            <Card className="border border-gray-200">
                                <CardHeader className="pb-3">
                                    <CardTitle className="text-base flex items-center justify-between">
                                        <span>{detail.vendor_name || "Invoice Detail"}</span>
                                        <Badge variant="outline" className="text-xs" data-testid="invoice-state-badge">
                                            {detail.state}
                                        </Badge>
                                    </CardTitle>
                                </CardHeader>
                                <CardContent className="space-y-4">
                                    {/* Fields */}
                                    <div className="grid grid-cols-2 gap-3 text-sm">
                                        {[
                                            {label: "ABN", value: detail.vendor_abn || "—"},
                                            {label: "Entity", value: detail.abn_entity_name || "—"},
                                            {label: "Invoice #", value: detail.invoice_number || "—"},
                                            {label: "Issue Date", value: detail.issue_date || "—"},
                                            {label: "Due Date", value: detail.due_date || "—"},
                                            {label: "Total (inc. GST)", value: formatMoneyFromCents(detail.total_cents)},
                                            {label: "GST", value: formatMoneyFromCents(detail.gst_cents)},
                                            {label: "Tax Invoice", value: detail.is_tax_invoice ? "Yes" : "No"},
                                        ].map(({label, value}) => (
                                            <div key={label}>
                                                <p className="text-xs text-gray-500">{label}</p>
                                                <p className="font-medium text-gray-800">{value}</p>
                                            </div>
                                        ))}
                                    </div>

                                    {/* OCR confidence */}
                                    {detail.ocr_confidence != null && (
                                        <div className="text-xs text-gray-500" data-testid="ocr-confidence">
                                            OCR confidence: <span
                                            className={detail.ocr_confidence < 0.6 ? "text-red-600 font-semibold" : "text-green-600"}>
                        {Math.round(detail.ocr_confidence * 100)}%
                      </span>
                                            {detail.low_confidence_fields.length > 0 && (
                                                <span className="ml-2 text-amber-600">
                          Low confidence: {detail.low_confidence_fields.join(", ")}
                        </span>
                                            )}
                                        </div>
                                    )}

                                    {/* Withholding warning */}
                                    {detail.withholding_required && (
                                        <div
                                            className="flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800"
                                            data-testid="withholding-warning">
                                            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5"/>
                                            <span>
                        47% ATO withholding applies — supplier ABN is invalid and supply exceeds $75 ex-GST.
                        Seek a valid ABN before payment.
                      </span>
                                        </div>
                                    )}

                                    {/* Action error */}
                                    {actionError && (
                                        <div
                                            className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700"
                                            data-testid="action-error">
                                            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5"/>
                                            {actionError}
                                        </div>
                                    )}

                                    {/* Actions */}
                                    <div className="flex flex-wrap gap-2 pt-2">
                                        {detail.state === "validated" && (
                                            <>
                                                <Button
                                                    size="sm"
                                                    className="bg-green-600 hover:bg-green-700 text-white"
                                                    disabled={actionLoading}
                                                    onClick={() => doAction(detail.invoice_id, "approve")}
                                                    data-testid="approve-btn"
                                                >
                                                    <CheckCircle2 className="w-4 h-4 mr-1"/>
                                                    Approve
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    className="border-red-300 text-red-600 hover:bg-red-50"
                                                    disabled={actionLoading}
                                                    onClick={() => doAction(detail.invoice_id, "reject")}
                                                    data-testid="reject-btn"
                                                >
                                                    <XCircle className="w-4 h-4 mr-1"/>
                                                    Reject
                                                </Button>
                                            </>
                                        )}
                                        {detail.state === "approved" && (
                                            <Button
                                                size="sm"
                                                className="bg-teal-700 hover:bg-teal-800 text-white"
                                                disabled={actionLoading}
                                                onClick={() => doAction(detail.invoice_id, "schedule")}
                                                data-testid="schedule-btn"
                                            >
                                                <Clock className="w-4 h-4 mr-1"/>
                                                Schedule Payment
                                            </Button>
                                        )}
                                        {detail.state === "submitted" && (
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                disabled={actionLoading}
                                                onClick={() => doAction(detail.invoice_id, "validate")}
                                                data-testid="validate-btn"
                                            >
                                                <Shield className="w-4 h-4 mr-1"/>
                                                Validate
                                            </Button>
                                        )}
                                        {["draft", "submitted", "validated"].includes(detail.state) && (
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                className="text-gray-500"
                                                disabled={actionLoading || detail.state === "draft"}
                                                onClick={() => doAction(detail.invoice_id, "reject")}
                                                data-testid="reject-any-btn"
                                            >
                                                Reject
                                            </Button>
                                        )}
                                        {actionLoading && <Loader2 className="w-4 h-4 animate-spin text-gray-400"/>}
                                    </div>

                                    {/* Transition history */}
                                    {detail.transitions.length > 0 && (
                                        <details className="text-xs text-gray-500 mt-2">
                                            <summary
                                                className="cursor-pointer font-medium text-gray-700 flex items-center gap-1">
                                                <ChevronRight className="w-3 h-3"/> Transition history
                                                ({detail.transitions.length})
                                            </summary>
                                            <ul className="mt-2 space-y-1 pl-3 border-l border-gray-100">
                                                {detail.transitions.map((t, i) => (
                                                    <li key={i} data-testid={`transition-${i}`}>
                                                        <span
                                                            className="text-gray-700">{t.from_state || "—"} → {t.to_state}</span>
                                                        {" "}by <span className="font-medium">{t.transitioned_by}</span>
                                                        {" "}at {t.transitioned_at?.slice(0, 16).replace("T", " ")}
                                                        {t.notes && <span className="text-gray-400"> · {t.notes}</span>}
                                                    </li>
                                                ))}
                                            </ul>
                                        </details>
                                    )}
                                </CardContent>
                            </Card>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
