// @featuretrace:demo_bank — Reconciliation view: portal/input/bank/match/ledger states, side by side.
// Layer: frontend
// Data flow: ReconciliationPage → GET /finance/reconciliation → staging_strata_web_snapshots +
//                                 demo_bank_transactions + match_review_queue + unit_levy_ledger
//                               → POST /financial/matching/queue/{id}/decide (allocate/reject)
//                               → POST /financial/matching/queue/{id}/promote-high-confidence
//                               (building-scoped)
// Related: backend/routers/finance_reconciliation.py
//           backend/routers/financial_matching.py
//           frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
//           frontend/src/app/(dashboard)/financials/reconciliation/page.tsx

"use client";

import React, {useCallback, useEffect, useState} from "react";
import {useRouter} from "next/navigation";
import {
    AlertTriangle,
    ChevronDown,
    ChevronUp,
    Loader2,
    RefreshCw,
    ShieldCheck,
} from "lucide-react";
import {useAuth} from "../../../contexts/AuthContext";
import {Badge} from "../../../components/ui/badge";
import {Button} from "../../../components/ui/button";
import {Card, CardContent} from "../../../components/ui/card";
import {toast} from "sonner";

import {formatMoneyFromCents} from '@/lib/currency';
// ─── Type definitions ───────────────────────────────────────────────────────

interface Candidate {
    queue_item_id: string | null;
    source_type: string | null;
    confidence: string | null;
    provenance_class: string | null;
    evidence_type: string | null;
    amount_cents: number | null;
    requires_review: boolean;
    match_status: string | null;
    receipt_id: string | null;
}

interface UnitReconciliationRow {
    unit_number: string;
    portal_balance_cents: number | null;
    portal_snapshot_date: string | null;
    ledger_balance_cents: number;
    delta_cents: number | null;
    requires_reconciliation: boolean;
    candidates: Candidate[];
    ledger_result: { receipt_id: string; status: string } | null;
}

interface ReconciliationResponse {
    building_id: string;
    financial_year: string;
    portal_snapshot_date: string | null;
    units: UnitReconciliationRow[];
}
// ─── Helpers ────────────────────────────────────────────────────────────────


const MATCH_STATUS_CLASS: Record<string, string> = {
    pending: "bg-amber-100 text-amber-800 border-amber-200",
    unidentified: "bg-red-100 text-red-800 border-red-200",
    review: "bg-amber-100 text-amber-800 border-amber-200",
    posting: "bg-blue-100 text-blue-800 border-blue-200",
    allocated: "bg-emerald-100 text-emerald-800 border-emerald-200",
    rejected: "bg-stone-100 text-stone-600 border-stone-200",
};

// ─── Candidate row ───────────────────────────────────────────────────────────

interface CandidateRowProps {
    unitNumber: string;
    candidate: Candidate;
    onPromote: (unitNumber: string, candidate: Candidate) => void;
    onReject: (unitNumber: string, candidate: Candidate) => void;
    actionLoading: string | null;
}
/**
 * @generated FunctionHeader
 * Function: CandidateRow
 * Path: frontend/src/pages/dashboard/financial/ReconciliationPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CandidateRow: React.FC<CandidateRowProps> = ({unitNumber, candidate, onPromote, onReject, actionLoading}) => {
    const isDecided = candidate.match_status === "allocated" || candidate.match_status === "rejected";
    const isLoading = actionLoading === candidate.queue_item_id;
    const statusClass = candidate.match_status
        ? MATCH_STATUS_CLASS[candidate.match_status] ?? "bg-stone-100 text-stone-600 border-stone-200"
        : "bg-stone-100 text-stone-500 border-stone-200";

    return (
        <div
            className="flex flex-wrap items-center gap-2 py-2 px-3 border border-stone-200 rounded-lg bg-white"
            data-testid={`candidate-row-${unitNumber}-${candidate.queue_item_id ?? "unlinked"}`}
        >
            <Badge variant="outline" className="text-xs">{candidate.source_type ?? "unknown"}</Badge>
            {candidate.provenance_class === "reconstruction" ? (
                <Badge
                    className="text-xs bg-amber-100 text-amber-800 border-amber-300"
                    title="Reconstructed from declared budget/spend data — not a bank-verified transaction"
                >
                    Reconstructed / Estimated
                </Badge>
            ) : (
                <span className="text-xs text-stone-500">{candidate.provenance_class ?? "—"}</span>
            )}
            <span className="text-sm font-semibold text-[#2F4F4F]">{formatMoneyFromCents(candidate.amount_cents)}</span>
            <span
                className={`text-xs font-medium px-2 py-0.5 rounded-full border ${
                    candidate.confidence === "high"
                        ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                        : candidate.confidence === "medium"
                            ? "bg-amber-50 text-amber-700 border-amber-200"
                            : "bg-stone-50 text-stone-500 border-stone-200"
                }`}
            >
                {candidate.confidence ?? "unknown"} confidence
            </span>
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${statusClass}`}>
                {candidate.match_status ?? "unlinked"}
            </span>
            {candidate.receipt_id && (
                <span className="text-xs text-stone-400">receipt {candidate.receipt_id}</span>
            )}

            {!isDecided && candidate.queue_item_id && (
                <div className="flex items-center gap-2 ml-auto">
                    <Button
                        size="sm"
                        onClick={() => onPromote(unitNumber, candidate)}
                        disabled={isLoading}
                        data-testid={`btn-promote-${candidate.queue_item_id}`}
                        className="rounded-full bg-[#2F4F4F] hover:bg-[#243d3d] text-white text-xs px-3 h-7 active:scale-95"
                    >
                        {isLoading ? <Loader2 className="w-3 h-3 animate-spin"/> : "Promote"}
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onReject(unitNumber, candidate)}
                        disabled={isLoading}
                        data-testid={`btn-reject-${candidate.queue_item_id}`}
                        className="rounded-full border-red-200 text-red-600 hover:bg-red-50 text-xs px-3 h-7 active:scale-95"
                    >
                        Reject
                    </Button>
                </div>
            )}
        </div>
    );
};

// ─── Unit row ────────────────────────────────────────────────────────────────

interface UnitRowProps {
    row: UnitReconciliationRow;
    onPromote: (unitNumber: string, candidate: Candidate) => void;
    onReject: (unitNumber: string, candidate: Candidate) => void;
    actionLoading: string | null;
}
/**
 * @generated FunctionHeader
 * Function: UnitRow
 * Path: frontend/src/pages/dashboard/financial/ReconciliationPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const UnitRow: React.FC<UnitRowProps> = ({row, onPromote, onReject, actionLoading}) => {
    const [expanded, setExpanded] = useState(row.requires_reconciliation);

    return (
        <div
            className={`border rounded-xl overflow-hidden bg-white ${
                row.requires_reconciliation ? "border-amber-300 shadow-md shadow-amber-50" : "border-stone-200 shadow-sm"
            }`}
            data-testid={`unit-row-${row.unit_number}`}
        >
            <div className="px-5 py-4 flex flex-wrap items-center gap-4">
                <div className="min-w-[80px]">
                    <p className="text-xs text-stone-400 uppercase tracking-wide">Unit</p>
                    <p className="text-sm font-bold text-[#2F4F4F]">{row.unit_number}</p>
                </div>
                <div className="min-w-[110px]">
                    <p className="text-xs text-stone-400 uppercase tracking-wide">Portal Balance</p>
                    <p className="text-sm font-semibold text-stone-700" data-testid={`portal-balance-${row.unit_number}`}>
                        {formatMoneyFromCents(row.portal_balance_cents)}
                    </p>
                </div>
                <div className="min-w-[110px]">
                    <p className="text-xs text-stone-400 uppercase tracking-wide">Ledger Balance</p>
                    <p className="text-sm font-semibold text-stone-700" data-testid={`ledger-balance-${row.unit_number}`}>
                        {formatMoneyFromCents(row.ledger_balance_cents)}
                    </p>
                </div>
                <div className="min-w-[100px]">
                    <p className="text-xs text-stone-400 uppercase tracking-wide">Delta</p>
                    <p className={`text-sm font-semibold ${row.requires_reconciliation ? "text-amber-700" : "text-stone-400"}`}>
                        {formatMoneyFromCents(row.delta_cents)}
                    </p>
                </div>
                <div className="min-w-[100px]">
                    <p className="text-xs text-stone-400 uppercase tracking-wide">Ledger Result</p>
                    {row.ledger_result ? (
                        <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-700">
                            <ShieldCheck className="w-3.5 h-3.5"/> {row.ledger_result.receipt_id}
                        </span>
                    ) : (
                        <span className="text-xs text-stone-400">—</span>
                    )}
                </div>

                {row.requires_reconciliation && (
                    <span className="inline-flex items-center gap-1 text-xs font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
                        <AlertTriangle className="w-3 h-3"/> Requires reconciliation
                    </span>
                )}

                {row.candidates.length > 0 && (
                    <button
                        type="button"
                        onClick={() => setExpanded((x) => !x)}
                        data-testid={`btn-expand-${row.unit_number}`}
                        className="ml-auto p-1.5 rounded-full text-stone-400 hover:text-stone-700 hover:bg-stone-100 transition-colors"
                        aria-label={expanded ? "Collapse candidates" : "Expand candidates"}
                    >
                        {expanded ? <ChevronUp className="w-4 h-4"/> : <ChevronDown className="w-4 h-4"/>}
                    </button>
                )}
            </div>

            {expanded && row.candidates.length > 0 && (
                <div className="border-t border-stone-100 bg-stone-50 px-5 py-3 space-y-2" data-testid={`candidates-panel-${row.unit_number}`}>
                    <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-1">
                        Candidate / Input Transactions
                    </p>
                    {row.candidates.map((c, idx) => (
                        <CandidateRow
                            key={`${row.unit_number}-${c.queue_item_id ?? idx}`}
                            unitNumber={row.unit_number}
                            candidate={c}
                            onPromote={onPromote}
                            onReject={onReject}
                            actionLoading={actionLoading}
                        />
                    ))}
                </div>
            )}
        </div>
    );
};

// ─── Main page ────────────────────────────────────────────────────────────────

// Mirrors backend _DECIDE_ROLES (routers/financial_matching.py) exactly. Deliberately
// reads effective_role first, like other manager-gated pages (e.g. LevyFairnessPage.jsx) —
// AuthContext.isManager() only checks the raw `role` field, which blank-screens a
// temporarily-elevated user (effective_role="ec_member", raw role still "owner") even
// though the backend would authorise them.
const _RECONCILIATION_ROLES = ["super_admin", "strata_admin", "strata_manager", "ec_member"];
/**
 * @generated FunctionHeader
 * Function: ReconciliationPage
 * Path: frontend/src/pages/dashboard/financial/ReconciliationPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ReconciliationPage: React.FC = () => {
    const {api, user, loading: authLoading} = useAuth() as any;
    const router = useRouter();
    const _role = user?.effective_role || user?.role || "";
    const canView = _RECONCILIATION_ROLES.includes(_role);

    const [data, setData] = useState<ReconciliationResponse | null>(null);
    const [pageLoading, setPageLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [actionLoading, setActionLoading] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading) return;
        if (!canView) {
            router.replace("/dashboard");
        }
    }, [authLoading, canView, router]);

    const fetchData = useCallback(async (showRefreshing = false) => {
        if (showRefreshing) setRefreshing(true);
        else setPageLoading(true);
        try {
            const res = await api.get("/finance/reconciliation");
            setData(res.data ?? null);
            setError(null);
        } catch (err: any) {
            setError(err?.response?.data?.detail ?? "Failed to load reconciliation data.");
        } finally {
            setPageLoading(false);
            setRefreshing(false);
        }
    }, [api]);

    useEffect(() => {
        if (!authLoading && canView) {
            fetchData(false);
        }
    }, [authLoading]); // eslint-disable-line react-hooks/exhaustive-deps

    const handlePromote = useCallback(async (unitNumber: string, candidate: Candidate) => {
        if (!candidate.queue_item_id) return;
        setActionLoading(candidate.queue_item_id);
        try {
            await api.post(`/financial/matching/queue/${candidate.queue_item_id}/promote-high-confidence`);
            toast.success(`Unit ${unitNumber}: promoted to ledger`);
            await fetchData(true);
        } catch (err: any) {
            toast.error(err?.response?.data?.detail ?? "Promotion failed.");
        } finally {
            setActionLoading(null);
        }
    }, [api, fetchData]);

    const handleReject = useCallback(async (unitNumber: string, candidate: Candidate) => {
        if (!candidate.queue_item_id) return;
        setActionLoading(candidate.queue_item_id);
        try {
            await api.post(`/financial/matching/queue/${candidate.queue_item_id}/decide`, {action: "reject"});
            toast.success(`Unit ${unitNumber}: candidate rejected`);
            await fetchData(true);
        } catch (err: any) {
            toast.error(err?.response?.data?.detail ?? "Reject failed.");
        } finally {
            setActionLoading(null);
        }
    }, [api, fetchData]);

    if (authLoading || (!authLoading && !canView)) return null;

    if (pageLoading) {
        return (
            <div className="flex items-center justify-center min-h-[60vh]" data-testid="page-loading">
                <Loader2 className="w-8 h-8 animate-spin text-[#2F4F4F]"/>
            </div>
        );
    }

    const requiringReconciliation = (data?.units ?? []).filter((u) => u.requires_reconciliation).length;

    return (
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-6 font-['Inter']">
            <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                    <h1 className="text-2xl font-bold font-['Manrope'] text-[#2F4F4F]">
                        Finance Reconciliation
                    </h1>
                    <p className="text-sm text-stone-500 mt-1">
                        Portal snapshot vs ledger balance, per unit — with candidate/input transactions and their
                        match status. Ledger amounts are the operational truth; portal/input rows are cross-check
                        or pending evidence only until promoted or allocated.
                    </p>
                </div>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => fetchData(true)}
                    disabled={refreshing}
                    data-testid="btn-refresh"
                    className="rounded-full border-stone-300 text-stone-600 hover:bg-stone-50 active:scale-95 gap-1.5"
                >
                    <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`}/>
                    Refresh
                </Button>
            </div>

            {error && (
                <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl p-4" data-testid="error-banner">
                    <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5"/>
                    <p className="text-sm text-red-700">{error}</p>
                </div>
            )}

            {data && (
                <Card className="border-stone-200 shadow-sm">
                    <CardContent className="p-4 flex flex-wrap items-center gap-6">
                        <div>
                            <p className="text-xs text-stone-400 uppercase tracking-wide">Financial Year</p>
                            <p className="text-lg font-bold text-[#2F4F4F] font-['Manrope']">{data.financial_year}</p>
                        </div>
                        <div>
                            <p className="text-xs text-stone-400 uppercase tracking-wide">Portal Snapshot</p>
                            <p className="text-sm font-semibold text-stone-700">{data.portal_snapshot_date ?? "No snapshot"}</p>
                        </div>
                        <div>
                            <p className="text-xs text-stone-400 uppercase tracking-wide">Units Requiring Reconciliation</p>
                            <p className={`text-lg font-bold font-['Manrope'] ${requiringReconciliation > 0 ? "text-amber-700" : "text-[#2F4F4F]"}`}>
                                {requiringReconciliation}
                            </p>
                        </div>
                    </CardContent>
                </Card>
            )}

            <div className="space-y-3" data-testid="unit-rows">
                {(data?.units ?? []).map((row) => (
                    <UnitRow
                        key={row.unit_number}
                        row={row}
                        onPromote={handlePromote}
                        onReject={handleReject}
                        actionLoading={actionLoading}
                    />
                ))}
                {data && data.units.length === 0 && (
                    <p className="text-sm text-stone-400 text-center py-8">No units found for this building.</p>
                )}
            </div>
        </div>
    );
};

export default ReconciliationPage;
