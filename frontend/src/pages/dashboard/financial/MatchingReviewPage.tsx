// @featuretrace:financial_matching — Payment matching review queue for manager triage of bank-tx-to-levy candidates.
// Layer: frontend
// Data flow: MatchingReviewPage → GET /financial/matching/queue → matching_queue (building-scoped)
//                               → GET /financial/matching/stats → matching_queue aggregate
//                               → POST /financial/matching/queue/{item_id}/decide → matching_queue + levy_payments
// Related: backend/routers/financial_matching.py
//           backend/integrations/matching/
//           frontend/src/app/(dashboard)/financials/matching/page.tsx

"use client";

import React, {useCallback, useEffect, useRef, useState} from "react";
import {useRouter} from "next/navigation";
import {
    AlertTriangle,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    Clock,
    HelpCircle,
    Layers,
    Loader2,
    RefreshCw,
    Search,
    Shield,
    XCircle,
} from "lucide-react";
import {useAuth} from "../../../contexts/AuthContext";
import {Badge} from "../../../components/ui/badge";
import {Button} from "../../../components/ui/button";
import {Card, CardContent, CardHeader, CardTitle} from "../../../components/ui/card";
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "../../../components/ui/dialog";
import {Input} from "../../../components/ui/input";
import {Label} from "../../../components/ui/label";
import {Textarea} from "../../../components/ui/textarea";
import {toast} from "sonner";

import {formatMoneyFromCents} from '@/lib/currency';
// ─── Type definitions ───────────────────────────────────────────────────────

type MatchType = "auto" | "review" | "agency_sweep" | "unidentified";
type ItemStatus = "pending" | "allocated" | "rejected" | "unidentified";
type DecisionAction = "allocate" | "reject" | "unidentified";

interface BankTx {
    amount_cents: number;
    description: string;
    // The unit/lot the transaction itself names, extracted at ingestion by
    // integrations.demo_bank.ingestion._extract_signals(). The backend has always
    // sent this on the queue item; this type simply never declared it, so the
    // reviewer had to retype a unit number that was sitting in the payload —
    // exactly the manual re-entry that mis-allocates payments.
    lot_ref_raw?: string | null;
}

interface ScoreEntry {
    lot_id: string;
    score: number;
    layer: string;
}

interface MatchingQueueItem {
    item_id: string;
    tenant_id: string;
    status: ItemStatus;
    match_type: MatchType;
    best_score: number;
    best_layer: string;
    best_lot_id: string | null;
    sla_due_at: string | null;
    created_at: string;
    tx: BankTx;
    candidates: ScoreEntry[];
    all_scores: ScoreEntry[];
}

interface MatchingStats {
    queue_depth: number;
    sla_breach_count: number;
    auto_match_rate: number;
    total_last_7_days: number;
    auto_allocated_last_7_days: number;
}

interface DecidePayload {
    action: DecisionAction;
    lot_id?: string;
    amount_cents?: number;
    notes?: string;
}
// ─── Helpers ────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: fmtScore
 * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtScore = (score: number): string =>
    `${(score * 100).toFixed(0)}%`;
/**
 * @generated FunctionHeader
 * Function: fmtDate
 * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtDate = (iso: string | null): string => {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("en-AU", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
    });
};
/**
 * @generated FunctionHeader
 * Function: isOverdueSla
 * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const isOverdueSla = (slaDate: string | null): boolean => {
    if (!slaDate) return false;
    return new Date(slaDate) < new Date();
};

// ─── Match type badge config ─────────────────────────────────────────────────

const MATCH_TYPE_CONFIG: Record<
    MatchType,
    { label: string; className: string; Icon: React.ComponentType<{ className?: string }> }
> = {
    auto: {
        label: "Auto",
        className: "bg-emerald-100 text-emerald-800 border-emerald-200",
        Icon: CheckCircle2,
    },
    review: {
        label: "Review",
        className: "bg-amber-100 text-amber-800 border-amber-200",
        Icon: Shield,
    },
    agency_sweep: {
        label: "Agency Sweep",
        className: "bg-blue-100 text-blue-800 border-blue-200",
        Icon: Layers,
    },
    unidentified: {
        label: "Unidentified",
        className: "bg-red-100 text-red-800 border-red-200",
        Icon: HelpCircle,
    },
};

// ─── Stat card ───────────────────────────────────────────────────────────────

interface StatCardProps {
    label: string;
    value: string | number;
    sub?: string;
    alert?: boolean;
    icon: React.ReactNode;
}
/**
 * @generated FunctionHeader
 * Function: StatCard
 * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StatCard: React.FC<StatCardProps> = ({label, value, sub, alert, icon}) => (
    <Card
        className={`border ${alert ? "border-red-300 bg-red-50" : "border-stone-200 bg-white"} shadow-sm`}
    >
        <CardContent className="p-4 flex items-center gap-4">
            <div
                className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${
                    alert ? "bg-red-100 text-red-600" : "bg-[#2F4F4F]/10 text-[#2F4F4F]"
                }`}
            >
                {icon}
            </div>
            <div className="min-w-0">
                <p className="text-xs font-medium text-stone-500 uppercase tracking-wide">{label}</p>
                <p
                    className={`text-2xl font-bold font-['Manrope'] ${
                        alert ? "text-red-700" : "text-[#2F4F4F]"
                    }`}
                >
                    {value}
                </p>
                {sub && <p className="text-xs text-stone-400 mt-0.5">{sub}</p>}
            </div>
        </CardContent>
    </Card>
);

// ─── Allocate modal ───────────────────────────────────────────────────────────

interface AllocateModalProps {
    item: MatchingQueueItem | null;
    open: boolean;
    onClose: () => void;
    onConfirm: (itemId: string, payload: DecidePayload) => Promise<void>;
    submitting: boolean;
}
/**
 * @generated FunctionHeader
 * Function: AllocateModal
 * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AllocateModal: React.FC<AllocateModalProps> = ({
                                                         item,
                                                         open,
                                                         onClose,
                                                         onConfirm,
                                                         submitting,
                                                     }) => {
    const [lotId, setLotId] = useState("");
    const [amountCents, setAmountCents] = useState<number>(0);
    const [notes, setNotes] = useState("");

    // The unit the bank narration itself names, when scoring produced no candidate.
    //
    // `best_lot_id` is null whenever no matching layer scored above zero, and for an
    // inferred/portal-delta payment that is the NORMAL outcome rather than a failure:
    // L4 (`l4_unit_ref_amount_timing`) and L7 (`l7_exact_amount_unique`) both require
    // the amount to equal the lot's OPEN BALANCE as well as the unit to match, and a
    // part payment never satisfies that. The engine is right to withhold an automatic
    // match — but the UI then rendered "no auto-match" and "we have no idea which unit
    // this is" identically, leaving the reviewer to retype a unit number that was
    // sitting in the payload. Live on East Gate 2026-08-28: all 39 pending items carry
    // `tx.lot_ref_raw`, and every one had to be keyed in by hand.
    //
    // Prefilling is a data-entry aid, never an assertion of correctness — the hint
    // below states explicitly that no layer scored it and asks for confirmation.
    const namedLot = (item?.tx?.lot_ref_raw ?? "").trim();
    const prefillFromNarration = !item?.best_lot_id && Boolean(namedLot);

    useEffect(() => {
        if (!item) return;
        // Derived from `item` inside the effect so `item` remains the only dependency.
        setLotId(item.best_lot_id ?? (item.tx?.lot_ref_raw ?? "").trim());
        setAmountCents(item.tx.amount_cents);
        setNotes("");
    }, [item]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async () => {
        if (!item) return;
        if (!lotId.trim()) {
            toast.error("Lot ID is required");
            return;
        }
        await onConfirm(item.item_id, {
            action: "allocate",
            lot_id: lotId.trim(),
            amount_cents: amountCents,
            notes: notes.trim() || undefined,
        });
    };

    return (
        <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
            <DialogContent className="sm:max-w-md" data-testid="allocate-modal">
                <DialogHeader>
                    <DialogTitle className="font-['Manrope'] text-[#2F4F4F]">
                        Allocate Payment
                    </DialogTitle>
                </DialogHeader>

                {item && (
                    <div className="space-y-4">
                        {/* Transaction summary */}
                        <div className="rounded-lg bg-stone-50 border border-stone-200 p-3 space-y-1">
                            <p className="text-xs font-medium text-stone-500 uppercase tracking-wide">
                                Transaction
                            </p>
                            <p className="text-sm text-stone-800 font-medium leading-snug">
                                {item.tx.description}
                            </p>
                            <p className="text-lg font-bold text-[#2F4F4F] font-['Manrope']">
                                {formatMoneyFromCents(item.tx.amount_cents)}
                            </p>
                        </div>

                        {/* Lot ID */}
                        <div className="space-y-1.5">
                            <Label htmlFor="lot-id-input" className="text-sm font-medium text-stone-700">
                                Lot ID <span className="text-red-500">*</span>
                            </Label>
                            <Input
                                id="lot-id-input"
                                data-testid="allocate-lot-id-input"
                                value={lotId}
                                onChange={(e) => setLotId(e.target.value)}
                                placeholder="e.g. 42"
                                className="border-stone-300 focus:border-[#2F4F4F] focus:ring-[#2F4F4F]"
                            />
                            {item.best_lot_id ? (
                                <p className="text-xs text-muted-foreground">
                                    Best candidate:{" "}
                                    <button
                                        type="button"
                                        className="text-[#2F4F4F] underline underline-offset-2 hover:no-underline"
                                        onClick={() => setLotId(item.best_lot_id ?? "")}
                                        data-testid="allocate-use-best-candidate"
                                    >
                                        Lot {item.best_lot_id} ({fmtScore(item.best_score)})
                                    </button>
                                </p>
                            ) : prefillFromNarration ? (
                                <p className="text-xs text-muted-foreground" data-testid="allocate-named-lot-hint">
                                    Prefilled from the unit named in the transaction:{" "}
                                    <button
                                        type="button"
                                        className="text-[#2F4F4F] underline underline-offset-2 hover:no-underline"
                                        onClick={() => setLotId(namedLot)}
                                        data-testid="allocate-use-named-lot"
                                    >
                                        {namedLot}
                                    </button>
                                    . No matching layer scored it, so the amount does not equal that
                                    unit&apos;s open balance — confirm before allocating.
                                </p>
                            ) : (
                                <p className="text-xs text-muted-foreground">
                                    No candidate scored and the transaction names no unit — identify the
                                    lot from the narration before allocating.
                                </p>
                            )}
                        </div>

                        {/* Amount */}
                        <div className="space-y-1.5">
                            <Label htmlFor="amount-input" className="text-sm font-medium text-stone-700">
                                Amount (AUD)
                            </Label>
                            <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-sm">
                  $
                </span>
                                <Input
                                    id="amount-input"
                                    data-testid="allocate-amount-input"
                                    type="number"
                                    step="0.01"
                                    value={(amountCents / 100).toFixed(2)}
                                    onChange={(e) =>
                                        setAmountCents(Math.round(parseFloat(e.target.value || "0") * 100))
                                    }
                                    className="pl-6 border-stone-300 focus:border-[#2F4F4F] focus:ring-[#2F4F4F]"
                                />
                            </div>
                        </div>

                        {/* Notes */}
                        <div className="space-y-1.5">
                            <Label htmlFor="notes-input" className="text-sm font-medium text-stone-700">
                                Notes (optional)
                            </Label>
                            <Textarea
                                id="notes-input"
                                data-testid="allocate-notes-input"
                                value={notes}
                                onChange={(e) => setNotes(e.target.value)}
                                placeholder="Reason for manual allocation…"
                                rows={2}
                                className="border-stone-300 focus:border-[#2F4F4F] focus:ring-[#2F4F4F] resize-none"
                            />
                        </div>
                    </div>
                )}

                <DialogFooter className="gap-2">
                    <Button
                        variant="outline"
                        onClick={onClose}
                        disabled={submitting}
                        data-testid="allocate-cancel-btn"
                        className="rounded-full border-stone-300 text-stone-700 hover:bg-stone-50 active:scale-95"
                    >
                        Cancel
                    </Button>
                    <Button
                        onClick={handleSubmit}
                        disabled={submitting || !lotId.trim()}
                        data-testid="allocate-confirm-btn"
                        className="rounded-full bg-[#2F4F4F] hover:bg-[#243d3d] text-white active:scale-95"
                    >
                        {submitting ? (
                            <Loader2 className="w-4 h-4 animate-spin mr-1.5"/>
                        ) : (
                            <CheckCircle2 className="w-4 h-4 mr-1.5"/>
                        )}
                        Confirm Allocation
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

// ─── Queue item row ───────────────────────────────────────────────────────────

interface QueueItemRowProps {
    item: MatchingQueueItem;
    onAllocate: (item: MatchingQueueItem) => void;
    onReject: (item: MatchingQueueItem) => void;
    onMarkUnidentified: (item: MatchingQueueItem) => void;
    actionLoading: string | null;
}
/**
 * @generated FunctionHeader
 * Function: QueueItemRow
 * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const QueueItemRow: React.FC<QueueItemRowProps> = ({
                                                       item,
                                                       onAllocate,
                                                       onReject,
                                                       onMarkUnidentified,
                                                       actionLoading,
                                                   }) => {
    const [expanded, setExpanded] = useState(false);
    const cfg = MATCH_TYPE_CONFIG[item.match_type] ?? MATCH_TYPE_CONFIG.unidentified;
    const overdue = isOverdueSla(item.sla_due_at);
    const isPending = item.status === "pending";
    const isLoading = actionLoading === item.item_id;

    return (
        <div
            className={`border rounded-xl overflow-hidden transition-shadow ${
                overdue && isPending
                    ? "border-red-300 shadow-md shadow-red-50"
                    : "border-stone-200 shadow-sm"
            } bg-white`}
            data-testid={`queue-item-${item.item_id}`}
        >
            {/* Main row */}
            <div className="px-5 py-4 flex flex-col sm:flex-row sm:items-center gap-3">
                {/* Description + amount */}
                <div className="flex-1 min-w-0">
                    <div className="flex items-start gap-2 flex-wrap">
                        <p
                            className="text-sm font-semibold text-stone-800 leading-snug break-words"
                            data-testid={`item-description-${item.item_id}`}
                        >
                            {item.tx.description}
                        </p>
                        {overdue && isPending && (
                            <span
                                className="inline-flex items-center gap-1 text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded-full px-2 py-0.5">
                <AlertTriangle className="w-3 h-3"/>
                SLA Overdue
              </span>
                        )}
                    </div>
                    <div className="flex items-center gap-3 mt-1.5 flex-wrap">
            <span
                className="text-lg font-bold text-[#2F4F4F] font-['Manrope']"
                data-testid={`item-amount-${item.item_id}`}
            >
              {formatMoneyFromCents(item.tx.amount_cents)}
            </span>

                        {/* Match type badge */}
                        <span
                            className={`inline-flex items-center gap-1 text-xs font-semibold border rounded-full px-2 py-0.5 ${cfg.className}`}
                            data-testid={`item-match-type-${item.item_id}`}
                        >
              <cfg.Icon className="w-3 h-3"/>
                            {cfg.label}
            </span>

                        {/* Score */}
                        <span className="text-xs text-stone-500" data-testid={`item-score-${item.item_id}`}>
              Score:{" "}
                            <span
                                className={`font-semibold ${
                                    item.best_score >= 0.8
                                        ? "text-emerald-700"
                                        : item.best_score >= 0.5
                                            ? "text-amber-700"
                                            : "text-red-700"
                                }`}
                            >
                {fmtScore(item.best_score)}
              </span>
            </span>

                        {/* Layer */}
                        <span className="text-xs text-stone-400" data-testid={`item-layer-${item.item_id}`}>
              via{" "}
                            <span className="font-medium text-stone-600">
                {item.best_layer || "—"}
              </span>
            </span>
                    </div>

                    {/* SLA */}
                    <div className="flex items-center gap-1.5 mt-1">
                        <Clock
                            className={`w-3.5 h-3.5 ${overdue && isPending ? "text-red-500" : "text-stone-400"}`}
                        />
                        <span
                            className={`text-xs ${
                                overdue && isPending ? "text-red-600 font-semibold" : "text-stone-400"
                            }`}
                            data-testid={`item-sla-${item.item_id}`}
                        >
              SLA due: {fmtDate(item.sla_due_at)}
            </span>
                    </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 flex-shrink-0 flex-wrap">
                    {isPending ? (
                        <>
                            <Button
                                size="sm"
                                onClick={() => onAllocate(item)}
                                disabled={isLoading}
                                data-testid={`btn-allocate-${item.item_id}`}
                                className="rounded-full bg-[#2F4F4F] hover:bg-[#243d3d] text-white text-xs px-4 active:scale-95 h-8"
                            >
                                {isLoading ? (
                                    <Loader2 className="w-3.5 h-3.5 animate-spin"/>
                                ) : (
                                    "Allocate"
                                )}
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => onMarkUnidentified(item)}
                                disabled={isLoading}
                                data-testid={`btn-unidentified-${item.item_id}`}
                                className="rounded-full border-stone-300 text-stone-600 hover:bg-stone-50 text-xs px-4 active:scale-95 h-8"
                            >
                                Unidentified
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => onReject(item)}
                                disabled={isLoading}
                                data-testid={`btn-reject-${item.item_id}`}
                                className="rounded-full border-red-200 text-red-600 hover:bg-red-50 text-xs px-4 active:scale-95 h-8"
                            >
                                Reject
                            </Button>
                        </>
                    ) : (
                        <span
                            className={`text-xs font-semibold px-3 py-1.5 rounded-full border ${
                                item.status === "allocated"
                                    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                                    : item.status === "rejected"
                                        ? "bg-red-50 text-red-700 border-red-200"
                                        : "bg-stone-100 text-stone-600 border-stone-200"
                            }`}
                            data-testid={`item-status-badge-${item.item_id}`}
                        >
              {item.status.charAt(0).toUpperCase() + item.status.slice(1)}
            </span>
                    )}

                    {/* Expand toggle */}
                    {item.all_scores.length > 0 && (
                        <button
                            type="button"
                            onClick={() => setExpanded((x) => !x)}
                            data-testid={`btn-expand-${item.item_id}`}
                            className="p-1.5 rounded-full text-stone-400 hover:text-stone-700 hover:bg-stone-100 transition-colors"
                            aria-label={expanded ? "Collapse candidates" : "Expand candidates"}
                        >
                            {expanded ? (
                                <ChevronUp className="w-4 h-4"/>
                            ) : (
                                <ChevronDown className="w-4 h-4"/>
                            )}
                        </button>
                    )}
                </div>
            </div>

            {/* Expanded candidate scores */}
            {expanded && item.all_scores.length > 0 && (
                <div
                    className="border-t border-stone-100 bg-stone-50 px-5 py-3"
                    data-testid={`item-scores-panel-${item.item_id}`}
                >
                    <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-2">
                        All Candidate Scores
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {item.all_scores
                            .slice()
                            .sort((a, b) => b.score - a.score)
                            .map((s) => (
                                <div
                                    key={`${s.lot_id}-${s.layer}`}
                                    className="flex items-center gap-1.5 bg-white border border-stone-200 rounded-lg px-2.5 py-1.5 text-xs"
                                    data-testid={`score-entry-${item.item_id}-${s.lot_id}`}
                                >
                                    <span className="font-medium text-stone-700">Lot {s.lot_id}</span>
                                    <span className="text-stone-400">·</span>
                                    <span
                                        className={`font-bold ${
                                            s.score >= 0.8
                                                ? "text-emerald-700"
                                                : s.score >= 0.5
                                                    ? "text-amber-700"
                                                    : "text-red-600"
                                        }`}
                                    >
                    {fmtScore(s.score)}
                  </span>
                                    <span className="text-stone-400 italic">{s.layer}</span>
                                </div>
                            ))}
                    </div>
                </div>
            )}
        </div>
    );
};
// ─── Main page ────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: MatchingReviewPage
 * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MatchingReviewPage: React.FC = () => {
    const {api, isManager, loading: authLoading} = useAuth() as any;
    const router = useRouter();

    const [queueItems, setQueueItems] = useState<MatchingQueueItem[]>([]);
    const [stats, setStats] = useState<MatchingStats | null>(null);
    const [pageLoading, setPageLoading] = useState(true);
    const [statsLoading, setStatsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState<"all" | "pending" | "allocated" | "rejected" | "unidentified">("pending");
    const [refreshing, setRefreshing] = useState(false);

    // Allocate modal state
    const [allocateItem, setAllocateItem] = useState<MatchingQueueItem | null>(null);
    const [allocateOpen, setAllocateOpen] = useState(false);
    const [actionLoading, setActionLoading] = useState<string | null>(null);

    // Guard: redirect non-managers
    useEffect(() => {
        if (authLoading) return;
        if (!isManager()) {
            router.replace("/dashboard");
        }
    }, [authLoading, isManager, router]);

    // ── Data fetching ──────────────────────────────────────────────────────────

    const fetchQueue = useCallback(async () => {
        try {
            const res = await api.get("/financial/matching/queue");
            setQueueItems(res.data ?? []);
            setError(null);
        } catch (err: any) {
            setError(
                err?.response?.data?.detail ?? "Failed to load matching queue. Please try again."
            );
        }
    }, [api]);

    const fetchStats = useCallback(async () => {
        try {
            const res = await api.get("/financial/matching/stats");
            setStats(res.data ?? null);
        } catch {
            // Stats failure is non-critical; silently ignore
        }
    }, [api]);

    const loadAll = useCallback(async (showRefreshing = false) => {
        if (showRefreshing) setRefreshing(true);
        else setPageLoading(true);
        setStatsLoading(true);

        await Promise.all([fetchQueue(), fetchStats()]);

        setPageLoading(false);
        setStatsLoading(false);
        setRefreshing(false);
    }, [fetchQueue, fetchStats]);

    useEffect(() => {
        if (!authLoading && isManager()) {
            loadAll(false);
        }
    }, [authLoading]); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Actions ────────────────────────────────────────────────────────────────

    const handleDecide = useCallback(
        async (itemId: string, payload: DecidePayload) => {
            setActionLoading(itemId);
            try {
                await api.post(`/financial/matching/queue/${itemId}/decide`, payload);
                toast.success(
                    payload.action === "allocate"
                        ? "Payment allocated successfully"
                        : payload.action === "reject"
                            ? "Item rejected"
                            : "Marked as unidentified"
                );
                setAllocateOpen(false);
                setAllocateItem(null);
                // Refresh queue + stats
                await loadAll(true);
            } catch (err: any) {
                toast.error(
                    err?.response?.data?.detail ?? "Action failed. Please try again."
                );
            } finally {
                setActionLoading(null);
            }
        },
        [api, loadAll]
    );
    /**
     * @generated FunctionHeader
     * Function: handleAllocateClick
     * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAllocateClick = (item: MatchingQueueItem) => {
        setAllocateItem(item);
        setAllocateOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleRejectClick
     * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRejectClick = (item: MatchingQueueItem) => {
        handleDecide(item.item_id, {action: "reject"});
    };
    /**
     * @generated FunctionHeader
     * Function: handleMarkUnidentified
     * Path: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleMarkUnidentified = (item: MatchingQueueItem) => {
        handleDecide(item.item_id, {action: "unidentified"});
    };

    // ── Filtering ──────────────────────────────────────────────────────────────

    const filteredItems = queueItems.filter((item) => {
        const matchesStatus =
            statusFilter === "all" || item.status === statusFilter;
        const q = search.toLowerCase();
        const matchesSearch =
            !q ||
            item.tx.description.toLowerCase().includes(q) ||
            (item.best_lot_id ?? "").toLowerCase().includes(q) ||
            item.item_id.toLowerCase().includes(q);
        return matchesStatus && matchesSearch;
    });

    const pendingCount = queueItems.filter((i) => i.status === "pending").length;

    // ── Render guards ──────────────────────────────────────────────────────────

    if (authLoading || (!authLoading && !isManager())) return null;

    if (pageLoading) {
        return (
            <div className="flex items-center justify-center min-h-[60vh]" data-testid="page-loading">
                <Loader2 className="w-8 h-8 animate-spin text-[#2F4F4F]"/>
            </div>
        );
    }

    // ── Render ─────────────────────────────────────────────────────────────────

    return (
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-8 font-['Inter']">
            {/* Header */}
            <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                    <h1 className="text-2xl font-bold font-['Manrope'] text-[#2F4F4F]">
                        Payment Matching Queue
                    </h1>
                    <p className="text-sm text-stone-500 mt-1">
                        Review and allocate incoming bank transactions to levy accounts.
                    </p>
                </div>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => loadAll(true)}
                    disabled={refreshing}
                    data-testid="btn-refresh"
                    className="rounded-full border-stone-300 text-stone-600 hover:bg-stone-50 active:scale-95 gap-1.5"
                >
                    <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`}/>
                    Refresh
                </Button>
            </div>

            {/* Stats bar */}
            {!statsLoading && stats && (
                <div
                    className="grid grid-cols-2 sm:grid-cols-4 gap-3"
                    data-testid="stats-bar"
                >
                    <StatCard
                        label="Queue Depth"
                        value={stats.queue_depth}
                        sub={`${pendingCount} pending`}
                        icon={<Layers className="w-5 h-5"/>}
                    />
                    <StatCard
                        label="SLA Breaches"
                        value={stats.sla_breach_count}
                        alert={stats.sla_breach_count > 0}
                        sub="Items past due"
                        icon={<AlertTriangle className="w-5 h-5"/>}
                    />
                    <StatCard
                        label="Auto-Match Rate"
                        value={`${(stats.auto_match_rate * 100).toFixed(0)}%`}
                        sub="Last 7 days"
                        icon={<CheckCircle2 className="w-5 h-5"/>}
                    />
                    <StatCard
                        label="Processed (7d)"
                        value={stats.total_last_7_days}
                        sub={`${stats.auto_allocated_last_7_days} auto-allocated`}
                        icon={<RefreshCw className="w-5 h-5"/>}
                    />
                </div>
            )}

            {statsLoading && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="stats-loading">
                    {[...Array(4)].map((_, i) => (
                        <div
                            key={i}
                            className="h-20 rounded-xl bg-stone-100 animate-pulse"
                        />
                    ))}
                </div>
            )}

            {/* Error banner */}
            {error && (
                <div
                    className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl p-4"
                    data-testid="error-banner"
                >
                    <XCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5"/>
                    <div>
                        <p className="text-sm font-semibold text-red-800">Unable to load queue</p>
                        <p className="text-xs text-red-600 mt-0.5">{error}</p>
                    </div>
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => loadAll(false)}
                        className="ml-auto text-red-600 hover:bg-red-100 rounded-full active:scale-95"
                        data-testid="btn-retry"
                    >
                        Retry
                    </Button>
                </div>
            )}

            {/* Filter bar */}
            <Card className="border border-stone-200 shadow-sm">
                <CardContent className="p-4 flex flex-col sm:flex-row gap-3 items-stretch sm:items-center">
                    {/* Search */}
                    <div className="relative flex-1">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-stone-400"/>
                        <Input
                            type="search"
                            placeholder="Search by description, lot ID…"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            data-testid="search-input"
                            className="pl-9 border-stone-300 focus:border-[#2F4F4F] focus:ring-[#2F4F4F] text-sm"
                        />
                    </div>

                    {/* Status filter pills */}
                    <div
                        className="flex gap-1.5 flex-wrap"
                        data-testid="status-filter-group"
                        role="group"
                        aria-label="Filter by status"
                    >
                        {(["pending", "all", "allocated", "rejected", "unidentified"] as const).map(
                            (s) => (
                                <button
                                    key={s}
                                    type="button"
                                    onClick={() => setStatusFilter(s)}
                                    data-testid={`filter-${s}`}
                                    className={`text-xs font-medium px-3 py-1.5 rounded-full border transition-colors active:scale-95 ${
                                        statusFilter === s
                                            ? "bg-[#2F4F4F] text-white border-[#2F4F4F]"
                                            : "bg-white text-stone-600 border-stone-300 hover:bg-stone-50"
                                    }`}
                                >
                                    {s === "all"
                                        ? "All"
                                        : s.charAt(0).toUpperCase() + s.slice(1)}
                                    {s === "pending" && pendingCount > 0 && (
                                        <span
                                            className={`ml-1.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-bold ${
                                                statusFilter === "pending"
                                                    ? "bg-white text-[#2F4F4F]"
                                                    : "bg-[#E07A5F] text-white"
                                            }`}
                                        >
                      {pendingCount}
                    </span>
                                    )}
                                </button>
                            )
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Queue list */}
            <div className="space-y-3" data-testid="queue-list">
                {filteredItems.length === 0 ? (
                    <div
                        className="flex flex-col items-center justify-center py-16 text-stone-400"
                        data-testid="empty-state"
                    >
                        <CheckCircle2 className="w-12 h-12 mb-3 text-stone-300"/>
                        <p className="text-sm font-semibold text-stone-500">
                            {search || statusFilter !== "pending"
                                ? "No items match your filters"
                                : "All caught up — no pending items"}
                        </p>
                        {!search && statusFilter === "pending" && (
                            <p className="text-xs text-stone-400 mt-1">
                                New items appear here when bank transactions arrive.
                            </p>
                        )}
                    </div>
                ) : (
                    filteredItems.map((item) => (
                        <QueueItemRow
                            key={item.item_id}
                            item={item}
                            onAllocate={handleAllocateClick}
                            onReject={handleRejectClick}
                            onMarkUnidentified={handleMarkUnidentified}
                            actionLoading={actionLoading}
                        />
                    ))
                )}
            </div>

            {/* Result count */}
            {filteredItems.length > 0 && (
                <p
                    className="text-xs text-stone-400 text-right"
                    data-testid="result-count"
                >
                    Showing {filteredItems.length} of {queueItems.length} items
                </p>
            )}

            {/* Allocate modal */}
            <AllocateModal
                item={allocateItem}
                open={allocateOpen}
                onClose={() => {
                    setAllocateOpen(false);
                    setAllocateItem(null);
                }}
                onConfirm={handleDecide}
                submitting={actionLoading === allocateItem?.item_id}
            />
        </div>
    );
};

export default MatchingReviewPage;
