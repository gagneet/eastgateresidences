// @featuretrace:demo_bank — Historical reconstruction batch workflow + GST-aware levy regeneration UI (building-scoped).
// @featuretrace:financial-onboarding — Shared historical finance reconstruction UI used after building creation (building-scoped).
// Layer: frontend
// Data flow: HistoricalReconstructionPage → GET/POST /financials/onboarding/building/{id}/reconstruction-batches/*
//            → reconstruction_batch_service (Mongo demo_bank_reconstruction_batches/manifests)
//            → Generate → demo_bank_transactions → Sync → finance.bank_transactions/matching/GL posting path.
//            Levy regeneration remains gated separately and only uses approved Demo Bank reconstruction batches.
// Related: backend/routers/financial_onboarding.py
//           backend/services/reconstruction_batch_service.py
//           backend/services/levy_generation_service.py
//           frontend/src/app/(dashboard)/financials/historical-reconstruction/page.tsx
// Toggle: historical_financial_reconstruction, historical_reconstruction_posting

"use client";

import React, {useCallback, useEffect, useMemo, useState} from "react";
import {useRouter} from "next/navigation";
import {
    AlertTriangle,
    ChevronLeft,
    Database,
    Loader2,
    Plus,
    RefreshCw,
    RotateCcw,
    ShieldCheck,
} from "lucide-react";
import {useAuth} from "../../../contexts/AuthContext";
import HistoricalFinancialImportWizard from "./HistoricalFinancialImportWizard";
import {Badge} from "../../../components/ui/badge";
import {Button} from "../../../components/ui/button";
import {Card, CardContent, CardHeader, CardTitle} from "../../../components/ui/card";
import {Input} from "../../../components/ui/input";
import {Label} from "../../../components/ui/label";
import {Textarea} from "../../../components/ui/textarea";
import {Checkbox} from "../../../components/ui/checkbox";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "../../../components/ui/tabs";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from "../../../components/ui/select";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "../../../components/ui/table";
import {
    Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../../../components/ui/dialog";
import {toast} from "sonner";

import {formatMoneyFromCents} from '@/lib/currency';
// ─── Role / RBAC constants ──────────────────────────────────────────────────
// Mirrors backend/routers/financial_onboarding.py's per-action role sets
// exactly (_RECONSTRUCTION_UPLOAD_ROLES / _REVIEW_ROLES / _APPROVE_ROLES /
// _APPLY_ROLES / _SYNC_ROLES / _REVERSE_ROLES) — action-level, not a single
// page-wide gate, per docs/migration/build-demo-data-ui-system.md finding 3.1
// (the "operations model": strata_manager can prepare, only super_admin/
// strata_admin can approve/generate/sync, only super_admin can apply/reverse).
const PAGE_ROLES = ["super_admin", "strata_admin", "strata_manager"];

// Matches services/reconstruction_generators/budget_levy_generator.py's GENERATION_METHOD —
// the generic, building-agnostic default. Any other reconstruction_method value routes to the
// legacy East-Gate-only retroactive-linking path (link_existing_rows_as_manifest), unchanged.
const GENERATE_FROM_BUDGET_METHOD = "generate_from_budget_v1";
const LEGACY_LINK_METHOD_DEFAULT = "gst_uoe_largest_remainder_v5";
const PREP_ROLES = ["super_admin", "strata_admin", "strata_manager"];
const APPROVE_ROLES = ["super_admin", "strata_admin"];
const SYNC_ROLES = ["super_admin", "strata_admin"];
const APPLY_ROLES = ["super_admin"];
const REVERSE_ROLES = ["super_admin"];

const STATUS_LABEL: Record<string, string> = {
    draft: "Draft",
    extracted: "Extracted",
    validation_failed: "Validation Failed",
    needs_review: "Needs Review",
    approved: "Approved",
    generation_ready: "Generation Ready",
    generated: "Generated",
    syncing: "Syncing",
    synced: "Synced",
    matching: "Matching",
    partially_posted: "Partially Posted",
    posted: "Posted",
    failed: "Failed",
    superseded: "Superseded",
    reversed: "Reversed",
};

const STATUS_CLASS: Record<string, string> = {
    draft: "bg-gray-100 text-gray-700 border-gray-200",
    extracted: "bg-blue-100 text-blue-700 border-blue-200",
    validation_failed: "bg-red-100 text-red-700 border-red-200",
    needs_review: "bg-amber-100 text-amber-700 border-amber-200",
    approved: "bg-emerald-100 text-emerald-700 border-emerald-200",
    generation_ready: "bg-violet-100 text-violet-700 border-violet-200",
    generated: "bg-violet-100 text-violet-800 border-violet-300",
    syncing: "bg-blue-100 text-blue-700 border-blue-200",
    synced: "bg-emerald-100 text-emerald-800 border-emerald-300",
    matching: "bg-blue-100 text-blue-700 border-blue-200",
    partially_posted: "bg-amber-100 text-amber-700 border-amber-200",
    posted: "bg-emerald-100 text-emerald-900 border-emerald-400",
    failed: "bg-red-100 text-red-700 border-red-200",
    superseded: "bg-slate-100 text-slate-700 border-slate-200",
    reversed: "bg-slate-200 text-slate-800 border-slate-300",
};

const StatusBadge = ({status}: { status: string }) => (
    <Badge className={STATUS_CLASS[status] || ""}>{STATUS_LABEL[status] || status}</Badge>
);

// Ordered timeline of the "happy path" this router's own actions can drive
// end to end. Batches that hit validation_failed/failed/superseded/reversed
// branch off this line — those are shown via the status badge, not the
// timeline, since they are not a step on the forward path.
const HAPPY_PATH_STATUSES = [
    "draft", "extracted", "needs_review", "approved", "generation_ready", "generated", "syncing", "synced",
];

type BatchAction = {
    key: string;
    label: string;
    roles: string[];
    requiresPosting: boolean;
    danger?: boolean;
};

// One entry per status this router's endpoints can act on. Reverse is
// intentionally NOT listed here — per reconstruction_batch_service.py's
// _VALID_TRANSITIONS, "reversed" is only reachable from "posted", a status
// this router never itself sets (it's set by the separate bank-feed
// matching/posting pipeline once a synced batch's transactions are
// matched+posted). Showing a Reverse button that would always 409 for every
// status this page can otherwise reach would be actively misleading — it is
// offered only when status === "posted" (see renderReverseAction below).
// "needs_review" is NOT listed here — it needs the batch's own reviewed_by
// field and the current user's id to decide between "Review" and "Approve",
// which this static table can't express — see getAllowedBatchActions below.
const ACTIONS_BY_STATUS: Record<string, BatchAction[]> = {
    draft: [{key: "extract", label: "Extract", roles: PREP_ROLES, requiresPosting: false}],
    extracted: [
        {key: "generate-preview", label: "Preview", roles: PREP_ROLES, requiresPosting: false},
        {key: "submit-review", label: "Submit for Review", roles: PREP_ROLES, requiresPosting: false},
    ],
    approved: [{key: "generate", label: "Generate", roles: SYNC_ROLES, requiresPosting: true}],
    generation_ready: [{key: "generate", label: "Generate", roles: SYNC_ROLES, requiresPosting: true}],
    generated: [{key: "sync", label: "Sync", roles: SYNC_ROLES, requiresPosting: true}],
    synced: [{key: "sync", label: "Re-verify Sync", roles: SYNC_ROLES, requiresPosting: true}],
    failed: [
        {key: "extract", label: "Re-extract", roles: PREP_ROLES, requiresPosting: false},
        {key: "sync", label: "Retry Sync", roles: SYNC_ROLES, requiresPosting: true},
    ],
};

/** Centralizes status/role/toggle/reviewer-identity -> allowed-action
 * resolution so button enablement logic isn't scattered across JSX (per the
 * review's guidance — the backend remains authoritative; this is
 * presentation only, a 409 from an unexpected transition still surfaces as
 * a toast). needs_review is special-cased: the backend enforces genuine
 * dual control (POST .../review then POST .../approve, reviewer must differ
 * from approver) — this function mirrors that by choosing "Review" or
 * "Approve" based on the batch's own reviewed_by field, not a static table. */
export function getAllowedBatchActions(
    status: string, effectiveRole: string, prepEnabled: boolean, postingEnabled: boolean,
    detail: any, currentUserId: string,
): Array<BatchAction & { enabled: boolean; disabledReason: string | null }> {
    if (status === "needs_review") {
        const roleOk = APPROVE_ROLES.includes(effectiveRole); // review/reject share the same role set as approve
        const reviewedBy: string | null = detail?.reviewed_by ?? null;
        let baseDisabledReason: string | null = null;
        if (!roleOk) baseDisabledReason = `Requires one of: ${APPROVE_ROLES.join(", ")}`;
        else if (!postingEnabled) baseDisabledReason = "Posting is disabled for this building";

        // Reject has no reviewed_by precondition (backend requires only status=needs_review) —
        // any single reviewer can reject straight away, unlike approve's dual-control gate.
        const rejectAction = {
            key: "reject", label: "Reject", roles: APPROVE_ROLES, requiresPosting: true, danger: true,
            enabled: roleOk && postingEnabled, disabledReason: baseDisabledReason,
        };

        if (!reviewedBy) {
            return [
                {
                    key: "review", label: "Review", roles: APPROVE_ROLES, requiresPosting: true,
                    enabled: roleOk && postingEnabled, disabledReason: baseDisabledReason,
                },
                rejectAction,
            ];
        }
        const isOwnReview = reviewedBy === currentUserId;
        let approveDisabledReason = baseDisabledReason;
        if (!approveDisabledReason && isOwnReview) {
            approveDisabledReason = "You recorded this review — a different admin must approve (dual control)";
        }
        return [
            {
                key: "approve", label: "Approve", roles: APPROVE_ROLES, requiresPosting: true, danger: true,
                enabled: roleOk && postingEnabled && !isOwnReview, disabledReason: approveDisabledReason,
            },
            rejectAction,
        ];
    }
    const raw = ACTIONS_BY_STATUS[status] || [];
    return raw.map((a) => {
        const roleOk = a.roles.includes(effectiveRole);
        const toggleOk = a.requiresPosting ? postingEnabled : prepEnabled;
        let disabledReason: string | null = null;
        if (!roleOk) disabledReason = `Requires one of: ${a.roles.join(", ")}`;
        else if (!toggleOk && a.requiresPosting) disabledReason = "Posting is disabled for this building";
        else if (!toggleOk) disabledReason = "Reconstruction preparation is disabled for this building";
        return {...a, enabled: roleOk && toggleOk, disabledReason};
    });
}

interface BatchSummary {
    batch_id: string;
    building_id: string;
    financial_year_start: number;
    financial_year_end: number;
    reconstruction_method: string;
    status: string;
    unit_count: number | null;
    generated_credit_cents: number;
    reconciliation_variance_cents: number;
    created_by: string | null;
    reviewed_by: string | null;
    approved_by: string | null;
    created_at: string;
    reviewed_at: string | null;
    approved_at: string | null;
    is_test_data: boolean;
}


const fmtDate = (value: string | null | undefined) =>
    value ? new Date(value).toLocaleString("en-AU") : "—";
/**
 * @generated FunctionHeader
 * Function: HistoricalReconstructionPage
 * Path: frontend/src/pages/dashboard/financial/HistoricalReconstructionPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
interface HistoricalReconstructionPageProps {
    buildingIdOverride?: string;
    title?: string;
    description?: string;
}

export default function HistoricalReconstructionPage({
    buildingIdOverride,
    title = "Historical Reconstruction",
    description = "Reconstruct pre-cutover levy/expense history through a reviewed, approved batch workflow — never writes to the ledger without an explicit human approval.",
}: HistoricalReconstructionPageProps = {}) {
    const {api, user, loading: authLoading, hasFeatureAccess, selectedBuilding} = useAuth() as any;
    const router = useRouter();

    const effectiveRole: string = user?.effective_role || user?.role || "";
    const canView = PAGE_ROLES.includes(effectiveRole);
    const prepEnabled: boolean = !!hasFeatureAccess?.("historical_financial_reconstruction");
    const postingEnabled: boolean = !!hasFeatureAccess?.("historical_reconstruction_posting");
    // building_id is always the human-readable plan number (e.g. "13195") carried in
    // JWTs/path params — never selectedBuilding.id (the Postgres scheme UUID). See
    // CLAUDE.md's "GET /buildings/me returns {id, building_id}" pattern note.
    const buildingId: string = buildingIdOverride || selectedBuilding?.building_id || "";
    const batchesBase = `/financials/onboarding/building/${buildingId}/reconstruction-batches`;
    const levyBase = `/financials/onboarding/building/${buildingId}/levy-items`;

    useEffect(() => {
        if (authLoading) return;
        if (!canView) router.replace("/dashboard");
    }, [authLoading, canView, router]);

    const [activeTab, setActiveTab] = useState("batches");

    // ── Batch list state ────────────────────────────────────────────────────
    const [batches, setBatches] = useState<BatchSummary[]>([]);
    const [listLoading, setListLoading] = useState(true);
    const [listError, setListError] = useState<string | null>(null);
    const [nextCursor, setNextCursor] = useState<string | null>(null);
    const [loadingMore, setLoadingMore] = useState(false);

    const fetchBatches = useCallback(async () => {
        setListLoading(true);
        try {
            const res = await api.get(batchesBase, {
                params: {limit: 100},
            });
            setBatches(res.data?.items || []);
            setNextCursor(res.data?.next_cursor ?? null);
            setListError(null);
        } catch (err: any) {
            setListError(err?.response?.data?.detail ?? "Failed to load reconstruction batches.");
        } finally {
            setListLoading(false);
        }
    }, [api, batchesBase]);

    const loadMoreBatches = useCallback(async () => {
        if (!nextCursor) return;
        setLoadingMore(true);
        try {
            const res = await api.get(batchesBase, {
                params: {limit: 100, cursor: nextCursor},
            });
            setBatches((prev) => [...prev, ...(res.data?.items || [])]);
            setNextCursor(res.data?.next_cursor ?? null);
        } catch (err: any) {
            toast.error(err?.response?.data?.detail ?? "Failed to load more batches.");
        } finally {
            setLoadingMore(false);
        }
    }, [api, batchesBase, nextCursor]);

    // ── Historical-data-status badge — answers "does this building already have
    // financial history?" before an admin commits to creating a batch. ────────
    const [dataStatus, setDataStatus] = useState<any>(null);
    const [dataStatusLoading, setDataStatusLoading] = useState(false);
    const currentYear = new Date().getFullYear();

    const fetchDataStatus = useCallback(async () => {
        if (!buildingId) return;
        setDataStatusLoading(true);
        try {
            const res = await api.get(
                `/financials/onboarding/building/${buildingId}/historical-data-status`,
                {params: {from_year: currentYear - 3, to_year: currentYear}},
            );
            setDataStatus(res.data);
        } catch {
            setDataStatus(null);
        } finally {
            setDataStatusLoading(false);
        }
    }, [api, buildingId, currentYear]);

    useEffect(() => {
        fetchDataStatus();
    }, [fetchDataStatus]);

    useEffect(() => {
        if (!authLoading && canView && buildingId) fetchBatches();
    }, [authLoading, canView, buildingId]); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Batch detail state ──────────────────────────────────────────────────
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [detail, setDetail] = useState<any>(null);
    const [manifest, setManifest] = useState<any>(null);
    const [auditLog, setAuditLog] = useState<any[]>([]);
    const [detailLoading, setDetailLoading] = useState(false);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [previewResult, setPreviewResult] = useState<any>(null);
    // The decision-basis view: what does this batch's dollar total actually
    // reconcile against, and how material are the exceptions? Fetched
    // alongside detail/manifest/audit-log so it's visible the moment a
    // reviewer opens a batch, not hidden behind a separate button click.
    const [reconciliationSummary, setReconciliationSummary] = useState<any>(null);
    const [reconciliationError, setReconciliationError] = useState<string | null>(null);

    const fetchDetail = useCallback(async (batchId: string) => {
        setDetailLoading(true);
        try {
            const [detailRes, manifestRes, auditRes, reconciliationRes] = await Promise.allSettled([
                api.get(`${batchesBase}/${batchId}`),
                api.get(`${batchesBase}/${batchId}/manifest`),
                api.get(`${batchesBase}/${batchId}/audit-log`),
                api.get(`${batchesBase}/${batchId}/reconciliation-summary`),
            ]);
            if (detailRes.status === "fulfilled") setDetail(detailRes.value.data);
            setManifest(manifestRes.status === "fulfilled" ? manifestRes.value.data : null);
            setAuditLog(auditRes.status === "fulfilled" ? (auditRes.value.data?.entries || []) : []);
            if (reconciliationRes.status === "fulfilled") {
                setReconciliationSummary(reconciliationRes.value.data);
                setReconciliationError(null);
            } else {
                setReconciliationSummary(null);
                setReconciliationError(
                    reconciliationRes.reason?.response?.data?.detail ?? "Failed to load reconciliation summary.",
                );
            }
        } catch (err: any) {
            toast.error(err?.response?.data?.detail ?? "Failed to load batch detail.");
        } finally {
            setDetailLoading(false);
        }
    }, [api, batchesBase]);

    useEffect(() => {
        if (selectedId) {
            setPreviewResult(null);
            fetchDetail(selectedId);
        }
    }, [selectedId, fetchDetail]);

    const runBatchAction = useCallback(async (batchId: string, actionKey: string, body?: any) => {
        setActionLoading(actionKey);
        try {
            const res = await api.post(
                `${batchesBase}/${batchId}/${actionKey}`,
                body,
            );
            if (actionKey === "generate-preview") {
                setPreviewResult(res.data);
                toast.success(`Preview computed: ${res.data?.expected_transaction_count ?? 0} planned transactions.`);
            } else if (actionKey === "sync") {
                const sr = res.data?.sync_result;
                if (sr) {
                    toast[sr.failed > 0 ? "error" : "success"](
                        `Sync: ${sr.inserted} inserted, ${sr.duplicates} already present, ${sr.failed} failed.`,
                    );
                } else {
                    toast.success("Sync completed.");
                }
            } else {
                toast.success(`${actionKey.replace(/-/g, " ")} completed.`);
            }
            await fetchDetail(batchId);
            await fetchBatches();
        } catch (err: any) {
            toast.error(err?.response?.data?.detail?.message ?? err?.response?.data?.detail ?? `Failed to ${actionKey}.`);
        } finally {
            setActionLoading(null);
        }
    }, [api, batchesBase, fetchDetail, fetchBatches]);

    // ── Reverse confirmation dialog (typed confirmation — irreversible action) ──
    const [reverseOpen, setReverseOpen] = useState(false);
    const [reverseReason, setReverseReason] = useState("");
    const [reverseConfirmText, setReverseConfirmText] = useState("");

    const submitReverse = useCallback(async () => {
        if (!selectedId) return;
        if (reverseReason.trim().length < 10) {
            toast.error("Provide a reason (at least 10 characters) for reversing this batch.");
            return;
        }
        if (reverseConfirmText !== selectedId) {
            toast.error("Type the batch ID exactly to confirm reversal.");
            return;
        }
        await runBatchAction(selectedId, "reverse", {reason: reverseReason.trim()});
        setReverseOpen(false);
        setReverseReason("");
        setReverseConfirmText("");
    }, [selectedId, reverseReason, reverseConfirmText, runBatchAction]);

    // ── Review dialog (dual-control step 1 of 2) ────────────────────────────
    const [reviewOpen, setReviewOpen] = useState(false);
    const [reviewNotes, setReviewNotes] = useState("");

    const submitReview = useCallback(async () => {
        if (!selectedId) return;
        await runBatchAction(selectedId, "review", {notes: reviewNotes.trim() || null});
        setReviewOpen(false);
        setReviewNotes("");
    }, [selectedId, reviewNotes, runBatchAction]);

    // ── Reject dialog (the negative outcome of needs_review — no dual-control precondition,
    // any single reviewer can reject; a reason is mandatory so the rejection is auditable) ──
    const [rejectOpen, setRejectOpen] = useState(false);
    const [rejectReason, setRejectReason] = useState("");

    const submitReject = useCallback(async () => {
        if (!selectedId) return;
        if (rejectReason.trim().length < 10) {
            toast.error("Provide a reason (at least 10 characters) for rejecting this batch.");
            return;
        }
        await runBatchAction(selectedId, "reject", {reason: rejectReason.trim()});
        setRejectOpen(false);
        setRejectReason("");
    }, [selectedId, rejectReason, runBatchAction]);

    // ── Approve confirmation dialog (dual-control step 2 of 2 — typed confirmation) ──
    const [approveOpen, setApproveOpen] = useState(false);
    const [approveConfirmText, setApproveConfirmText] = useState("");

    const submitApprove = useCallback(async () => {
        if (!selectedId) return;
        if (approveConfirmText.trim().toUpperCase() !== "APPROVE") {
            toast.error('Type "APPROVE" exactly to confirm.');
            return;
        }
        await runBatchAction(selectedId, "approve", {});
        setApproveOpen(false);
        setApproveConfirmText("");
    }, [selectedId, approveConfirmText, runBatchAction]);

    // ── Create batch dialog ─────────────────────────────────────────────────
    const [createOpen, setCreateOpen] = useState(false);
    const [creating, setCreating] = useState(false);
    const [newBatch, setNewBatch] = useState({
        financial_year_start: new Date().getFullYear(),
        financial_year_end: new Date().getFullYear(),
        reconstruction_method: GENERATE_FROM_BUDGET_METHOD,
        source_document_ids: "",
        onboarding_session_id: "",
        is_test_data: false,
    });
    const isGenerateFromBudget = newBatch.reconstruction_method === GENERATE_FROM_BUDGET_METHOD;

    // Reset to defaults every time the dialog opens — otherwise a prior session's
    // "Link existing Demo Bank rows" mode selection (or any other field) leaks into
    // the next batch a user creates, since useState only initialises once per mount.
    const openCreateDialog = useCallback(() => {
        setNewBatch({
            financial_year_start: new Date().getFullYear(),
            financial_year_end: new Date().getFullYear(),
            reconstruction_method: GENERATE_FROM_BUDGET_METHOD,
            source_document_ids: "",
            onboarding_session_id: "",
            is_test_data: false,
        });
        setCreateOpen(true);
    }, []);

    const submitCreate = useCallback(async () => {
        setCreating(true);
        try {
            await api.post(batchesBase, {
                financial_year_start: Number(newBatch.financial_year_start),
                financial_year_end: Number(newBatch.financial_year_end),
                reconstruction_method: newBatch.reconstruction_method,
                source_document_ids: newBatch.source_document_ids
                    ? newBatch.source_document_ids.split(",").map((s) => s.trim()).filter(Boolean)
                    : [],
                onboarding_session_id: newBatch.onboarding_session_id || null,
                // is_test_data is only ever sent true when the create dialog exposed the
                // checkbox at all (super_admin only — see the dialog body below); every
                // other caller always sends false, never a client-toggleable production flag.
                is_test_data: effectiveRole === "super_admin" ? newBatch.is_test_data : false,
            });
            toast.success("Reconstruction batch created.");
            setCreateOpen(false);
            await fetchBatches();
        } catch (err: any) {
            toast.error(err?.response?.data?.detail ?? "Failed to create batch.");
        } finally {
            setCreating(false);
        }
    }, [api, batchesBase, newBatch, effectiveRole, fetchBatches]);

    // ── Levy regeneration tab state ─────────────────────────────────────────
    const approvedBatches = useMemo(() => batches.filter((b) => b.status === "approved"), [batches]);
    const [regenFromYear, setRegenFromYear] = useState(new Date().getFullYear());
    const [regenToYear, setRegenToYear] = useState(new Date().getFullYear());
    const [regenBatchId, setRegenBatchId] = useState("");
    const [regenPlan, setRegenPlan] = useState<any>(null);
    const [regenPlanLoading, setRegenPlanLoading] = useState(false);
    const [regenApplying, setRegenApplying] = useState(false);
    const [regenOverrideReason, setRegenOverrideReason] = useState("");
    const [regenShowAllLines, setRegenShowAllLines] = useState(false);

    const generateRegenPlan = useCallback(async () => {
        setRegenPlanLoading(true);
        setRegenPlan(null);
        try {
            const res = await api.post(
                `${levyBase}/regenerate-plan`,
                {},
                {params: {from_year: regenFromYear, to_year: regenToYear}},
            );
            setRegenPlan(res.data);
        } catch (err: any) {
            toast.error(err?.response?.data?.detail ?? "Failed to generate regeneration plan.");
        } finally {
            setRegenPlanLoading(false);
        }
    }, [api, levyBase, regenFromYear, regenToYear]);

    const applyRegenPlan = useCallback(async () => {
        if (!regenBatchId) {
            toast.error("Select an approved governing batch before applying.");
            return;
        }
        const mismatch = regenPlan && regenPlan.totals_reconcile === false;
        if (mismatch && regenOverrideReason.trim().length < 10) {
            toast.error("This plan does not reconcile — provide an override justification (at least 10 characters).");
            return;
        }
        setRegenApplying(true);
        try {
            const res = await api.post(
                `${levyBase}/regenerate-apply`,
                {
                    confirm: true,
                    override_reconciliation_mismatch: mismatch,
                    override_reason: mismatch ? regenOverrideReason.trim() : null,
                },
                {params: {from_year: regenFromYear, to_year: regenToYear, batch_id: regenBatchId}},
            );
            toast.success(
                `Applied ${res.data.applied_item_count} levy items ` +
                `(${res.data.manual_review_count} flagged for manual review).`,
            );
            setRegenPlan(null);
            setRegenOverrideReason("");
        } catch (err: any) {
            toast.error(err?.response?.data?.detail ?? "Failed to apply levy regeneration.");
        } finally {
            setRegenApplying(false);
        }
    }, [api, levyBase, regenBatchId, regenPlan, regenOverrideReason, regenFromYear, regenToYear]);

    if (authLoading || (!authLoading && !canView)) return null;

    const selected = batches.find((b) => b.batch_id === selectedId) || null;
    const timelineIndex = detail ? HAPPY_PATH_STATUSES.indexOf(detail.status) : -1;
    const isOffPath = detail && timelineIndex === -1;
    const allowedActions = detail
        ? getAllowedBatchActions(detail.status, effectiveRole, prepEnabled, postingEnabled, detail, String(user?.id ?? ""))
        : [];
    const canReverse = detail?.status === "posted" && REVERSE_ROLES.includes(effectiveRole);

    const regenLines: any[] = regenPlan?.lines || [];
    const visibleRegenLines = regenShowAllLines
        ? regenLines
        : regenLines.filter((l) => l.action !== "unchanged");
    const regenLinesPage = visibleRegenLines.slice(0, 200);

    return (
        <div className="p-6 space-y-6" data-testid="historical-reconstruction-page">
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
                    <p className="text-muted-foreground text-sm">
                        {description}
                    </p>
                </div>
                {!prepEnabled ? (
                    <Badge className="bg-red-100 text-red-700 border-red-200">Reconstruction disabled for this building</Badge>
                ) : !postingEnabled ? (
                    <Badge className="bg-amber-100 text-amber-700 border-amber-200">
                        Preparation enabled — posting disabled for this building
                    </Badge>
                ) : (
                    <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200">Preparation + posting enabled</Badge>
                )}
            </div>

            {!postingEnabled && prepEnabled && (
                <Card className="border-amber-200 bg-amber-50">
                    <CardContent className="py-3 text-sm text-amber-800 flex items-start gap-2">
                        <AlertTriangle size={16} className="mt-0.5 shrink-0"/>
                        <span>
              Reconstruction preparation is enabled. Posting and ledger-changing actions (Approve, Generate, Sync,
              Reverse, Apply) remain disabled for this building until <code>historical_reconstruction_posting</code>{" "}
                            is enabled.
            </span>
                    </CardContent>
                </Card>
            )}

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="batches">Reconstruction Batches</TabsTrigger>
                    <TabsTrigger value="import-generate">Import &amp; Generate</TabsTrigger>
                    <TabsTrigger value="levy-regen">Levy Regeneration</TabsTrigger>
                </TabsList>

                {/* ── Tab: Import & Generate — gap-tolerant CSV/JSON wizard ────────── */}
                <TabsContent value="import-generate" className="pt-4 space-y-4">
                    {!prepEnabled ? (
                        <Card>
                            <CardContent className="py-8 text-center text-sm text-muted-foreground">
                                Reconstruction preparation is disabled for this building.
                            </CardContent>
                        </Card>
                    ) : !PREP_ROLES.includes(effectiveRole) ? (
                        <Card>
                            <CardContent className="py-8 text-center text-sm text-muted-foreground">
                                Requires one of: {PREP_ROLES.join(", ")}.
                            </CardContent>
                        </Card>
                    ) : buildingId ? (
                        <HistoricalFinancialImportWizard
                            buildingId={buildingId}
                            onBatchSubmitted={() => {
                                fetchBatches();
                                setActiveTab("batches");
                            }}
                        />
                    ) : (
                        <Card>
                            <CardContent className="py-8 text-center text-sm text-muted-foreground">
                                Select a building first.
                            </CardContent>
                        </Card>
                    )}
                </TabsContent>

                {/* ── Tab 1: Batches ─────────────────────────────────────────────── */}
                <TabsContent value="batches" className="pt-4 space-y-4">
                    {selected ? (
                        <Card>
                            <CardHeader>
                                <div className="flex items-center justify-between flex-wrap gap-2">
                                    <div className="flex items-center gap-3">
                                        <Button variant="outline" size="sm" onClick={() => setSelectedId(null)}>
                                            <ChevronLeft size={14} className="mr-1"/> Back
                                        </Button>
                                        <CardTitle className="text-base font-mono">{selected.batch_id}</CardTitle>
                                        <StatusBadge status={detail?.status || selected.status}/>
                                        {selected.is_test_data && <Badge variant="outline">test data</Badge>}
                                    </div>
                                    <Button variant="outline" size="sm" onClick={() => fetchDetail(selected.batch_id)}>
                                        <RefreshCw size={14} className="mr-1"/> Refresh
                                    </Button>
                                </div>
                            </CardHeader>
                            <CardContent className="space-y-6">
                                {detailLoading ? (
                                    <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin"/></div>
                                ) : (
                                    <>
                                        {/* Action timeline */}
                                        <div className="flex flex-wrap items-center gap-1 text-xs">
                                            {HAPPY_PATH_STATUSES.map((s, idx) => (
                                                <React.Fragment key={s}>
                                                    {idx > 0 && <span className="text-muted-foreground">→</span>}
                                                    <span
                                                        className={`px-2 py-1 rounded-full border ${
                                                            idx <= timelineIndex && !isOffPath
                                                                ? STATUS_CLASS[s]
                                                                : "bg-gray-50 text-gray-400 border-gray-100"
                                                        }`}
                                                    >
                            {STATUS_LABEL[s]}
                          </span>
                                                </React.Fragment>
                                            ))}
                                            {isOffPath && (
                                                <span className="ml-2">
                              <StatusBadge status={detail.status}/> (off the standard forward path)
                            </span>
                                            )}
                                        </div>

                                        {/* Summary grid */}
                                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                            {[
                                                {label: "Financial Years", value: `${detail?.financial_year_start}–${detail?.financial_year_end}`},
                                                {label: "Method", value: detail?.reconstruction_method},
                                                {label: "Unit Count", value: detail?.unit_count ?? "—"},
                                                {label: "Created By", value: detail?.created_by ?? "—"},
                                                {label: "Created", value: fmtDate(detail?.created_at)},
                                                {label: "Reviewed By", value: detail?.reviewed_by ?? "—"},
                                                {label: "Approved By", value: detail?.approved_by ?? "—"},
                                                {label: "Approved", value: fmtDate(detail?.approved_at)},
                                            ].map(({label, value}) => (
                                                <div key={label} className="rounded-lg border p-3 space-y-1">
                                                    <p className="text-xs text-muted-foreground">{label}</p>
                                                    <p className="text-sm font-medium">{value as any}</p>
                                                </div>
                                            ))}
                                        </div>

                                        {detail?.warnings?.length > 0 && (
                                            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 space-y-1">
                                                <p className="text-xs font-medium text-amber-800">Warnings</p>
                                                {detail.warnings.map((w: string, i: number) => (
                                                    <p key={i} className="text-xs text-amber-700">{w}</p>
                                                ))}
                                            </div>
                                        )}

                                        {/* Source evidence */}
                                        <div className="space-y-2">
                                            <p className="text-sm font-medium">Source Evidence</p>
                                            {(detail?.source_document_ids?.length > 0 || detail?.source_organisation
                                                || detail?.source_application || detail?.source_account_reference) ? (
                                                <div className="rounded-lg border p-3 text-sm space-y-1">
                                                    {detail?.source_document_ids?.length > 0 && (
                                                        <p>Documents: {detail.source_document_ids.map((id: string) => (
                                                            <code key={id} className="text-xs bg-muted px-1 py-0.5 rounded mr-1">{id}</code>
                                                        ))}</p>
                                                    )}
                                                    {detail?.source_organisation && <p>Organisation: {detail.source_organisation}</p>}
                                                    {detail?.source_application && <p>Application: {detail.source_application}</p>}
                                                    {detail?.source_account_reference && <p>Account reference: {detail.source_account_reference}</p>}
                                                </div>
                                            ) : (
                                                <p className="text-sm text-muted-foreground">
                                                    No source documents recorded for this batch.
                                                </p>
                                            )}
                                        </div>

                                        {/* Manifest / preview */}
                                        <div className="space-y-2">
                                            <p className="text-sm font-medium">Manifest</p>
                                            {(() => {
                                                const active = previewResult || manifest;
                                                if (!active) {
                                                    return (
                                                        <p className="text-sm text-muted-foreground">
                                                            No manifest yet — run Preview or Submit for Review.
                                                        </p>
                                                    );
                                                }
                                                const fundReconciliation = active.fund_balance_reconciliation || [];
                                                return (
                                                    <div className="rounded-lg border p-3 text-sm space-y-3">
                                                        <p>
                                                            {previewResult
                                                                ? "Dry-run preview — not yet persisted (Submit for Review persists this)."
                                                                : `Persisted manifest v${manifest.version} — ${manifest.expected_transaction_count} transactions.`}
                                                        </p>
                                                        <div className="grid grid-cols-3 gap-3">
                                                            <div>
                                                                <p className="text-xs text-muted-foreground">Transactions</p>
                                                                <p className="font-medium">{active.expected_transaction_count}</p>
                                                            </div>
                                                            <div>
                                                                <p className="text-xs text-muted-foreground">Income (credit)</p>
                                                                <p className="font-medium text-emerald-700">{formatMoneyFromCents(active.expected_credit_cents)}</p>
                                                            </div>
                                                            <div>
                                                                <p className="text-xs text-muted-foreground">Expenses (debit)</p>
                                                                <p className="font-medium text-red-700">{formatMoneyFromCents(active.expected_debit_cents)}</p>
                                                            </div>
                                                        </div>
                                                        {fundReconciliation.length > 0 && (
                                                            <Table>
                                                                <TableHeader>
                                                                    <TableRow>
                                                                        <TableHead>Account</TableHead>
                                                                        <TableHead className="text-right">Opening</TableHead>
                                                                        <TableHead className="text-right">+ Income</TableHead>
                                                                        <TableHead className="text-right">− Expenses</TableHead>
                                                                        <TableHead className="text-right">Declared Closing</TableHead>
                                                                        <TableHead>Matches</TableHead>
                                                                    </TableRow>
                                                                </TableHeader>
                                                                <TableBody>
                                                                    {fundReconciliation.map((line: any, i: number) => (
                                                                        <TableRow key={i} className={!line.matches_expected_closing ? "bg-amber-50" : ""}>
                                                                            <TableCell className="text-sm">{line.account_ref}</TableCell>
                                                                            <TableCell className="text-right text-sm">{formatMoneyFromCents(line.opening_balance_cents)}</TableCell>
                                                                            <TableCell className="text-right text-sm text-emerald-700">{formatMoneyFromCents(line.reconstructed_credits_cents)}</TableCell>
                                                                            <TableCell className="text-right text-sm text-red-700">{formatMoneyFromCents(line.reconstructed_debits_cents)}</TableCell>
                                                                            <TableCell className="text-right text-sm">{formatMoneyFromCents(line.closing_balance_cents)}</TableCell>
                                                                            <TableCell>{line.matches_expected_closing ? "✓" : "⚠"}</TableCell>
                                                                        </TableRow>
                                                                    ))}
                                                                </TableBody>
                                                            </Table>
                                                        )}
                                                        {active.warnings?.length > 0 && (
                                                            <p className="text-amber-700 text-xs">{active.warnings.join("; ")}</p>
                                                        )}
                                                    </div>
                                                );
                                            })()}
                                        </div>

                                        {/* Reconciliation summary — the decision basis for approve/generate */}
                                        <div className="space-y-2">
                                            <p className="text-sm font-medium">Reconciliation Summary</p>
                                            <p className="text-xs text-muted-foreground">
                                                This is what the batch total actually reconciles against — not
                                                just its own reported number. Approving a batch means accepting
                                                that this breakdown is correct, not just that a total exists.
                                            </p>
                                            {reconciliationError ? (
                                                <p className="text-sm text-red-600">{reconciliationError}</p>
                                            ) : !reconciliationSummary ? (
                                                <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                                                    <Loader2 size={14} className="animate-spin"/> Loading reconciliation basis…
                                                </div>
                                            ) : (
                                                <div className="rounded-lg border p-3 space-y-3">
                                                    <div className="flex flex-wrap items-center gap-3">
                                                        {reconciliationSummary.totals_reconcile === true && (
                                                            <Badge className="bg-green-100 text-green-800 border-green-300">
                                                                <ShieldCheck size={12} className="mr-1"/> Reconciles against annual levies
                                                            </Badge>
                                                        )}
                                                        {reconciliationSummary.totals_reconcile === false && (
                                                            <Badge className="bg-red-100 text-red-800 border-red-300">
                                                                <AlertTriangle size={12} className="mr-1"/> Does not reconcile
                                                            </Badge>
                                                        )}
                                                        {reconciliationSummary.totals_reconcile === null && (
                                                            <Badge variant="outline" className="text-muted-foreground">
                                                                <AlertTriangle size={12} className="mr-1"/> Reconciliation could not be computed
                                                            </Badge>
                                                        )}
                                                        {reconciliationSummary.manual_review?.count > 0 && (
                                                            <span className="text-sm">
                                                                <strong>{reconciliationSummary.manual_review.count}</strong> unit(s) flagged
                                                                for manual review —{" "}
                                                                <strong>{formatMoneyFromCents(reconciliationSummary.manual_review.total_variance_cents)}</strong>
                                                                {reconciliationSummary.manual_review.percentage_of_batch != null && (
                                                                    <> ({reconciliationSummary.manual_review.percentage_of_batch}% of batch total)</>
                                                                )}
                                                            </span>
                                                        )}
                                                    </div>

                                                    {reconciliationSummary.by_year_fund?.length > 0 && (
                                                        <>
                                                            <Table>
                                                                <TableHeader>
                                                                    <TableRow>
                                                                        <TableHead>Year</TableHead>
                                                                        <TableHead>Fund</TableHead>
                                                                        <TableHead className="text-right">Proposed Levy</TableHead>
                                                                        <TableHead className="text-right">Currently Posted</TableHead>
                                                                        <TableHead className="text-right">Demo Bank Total</TableHead>
                                                                        <TableHead className="text-right">Regenerated (GST-aware)</TableHead>
                                                                        <TableHead className="text-right">Variance (Regen. − Proposed)</TableHead>
                                                                    </TableRow>
                                                                </TableHeader>
                                                                <TableBody>
                                                                    {reconciliationSummary.by_year_fund.map((yf: any, i: number) => (
                                                                        <TableRow key={i} className={!yf.within_tolerance ? "bg-amber-50" : ""}>
                                                                            <TableCell>{yf.year}</TableCell>
                                                                            <TableCell className="capitalize">{yf.fund_type}</TableCell>
                                                                            <TableCell className="text-right">{formatMoneyFromCents(yf.annual_levies_proposed_inc_gst_cents)}</TableCell>
                                                                            <TableCell className="text-right">{formatMoneyFromCents(yf.existing_levy_items_total_cents)}</TableCell>
                                                                            <TableCell className="text-right">{formatMoneyFromCents(yf.demo_bank_payment_total_cents)}</TableCell>
                                                                            <TableCell className="text-right">{formatMoneyFromCents(yf.regenerated_levy_items_total_cents)}</TableCell>
                                                                            <TableCell className={`text-right ${!yf.within_tolerance ? "text-amber-700 font-medium" : ""}`}>
                                                                                {formatMoneyFromCents(yf.variance_cents)}
                                                                            </TableCell>
                                                                        </TableRow>
                                                                    ))}
                                                                </TableBody>
                                                            </Table>
                                                            <p className="text-xs text-muted-foreground">
                                                                "Currently Posted" is what finance.levy_items holds today, before this
                                                                batch's regeneration is applied — a large gap from "Regenerated" shows how
                                                                much this correction will move existing figures.
                                                            </p>
                                                        </>
                                                    )}

                                                    {reconciliationSummary.warnings?.length > 0 && (
                                                        <div className="space-y-1">
                                                            {reconciliationSummary.warnings.map((w: string, i: number) => (
                                                                <p key={i} className="text-xs text-amber-700">⚠ {w}</p>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                        </div>

                                        {/* Audit history */}
                                        <div className="space-y-2">
                                            <p className="text-sm font-medium">Audit History</p>
                                            {auditLog.length === 0 ? (
                                                <p className="text-sm text-muted-foreground">No audit events recorded yet.</p>
                                            ) : (
                                                <Table>
                                                    <TableHeader>
                                                        <TableRow>
                                                            <TableHead>When</TableHead>
                                                            <TableHead>Action</TableHead>
                                                            <TableHead>By</TableHead>
                                                        </TableRow>
                                                    </TableHeader>
                                                    <TableBody>
                                                        {auditLog.map((entry: any, i: number) => (
                                                            <TableRow key={i}>
                                                                <TableCell className="text-sm text-muted-foreground">{fmtDate(entry.created_at)}</TableCell>
                                                                <TableCell className="text-sm">{String(entry.action || "").replace(/_/g, " ")}</TableCell>
                                                                <TableCell className="text-sm">{entry.user_name || entry.user_id || "—"}</TableCell>
                                                            </TableRow>
                                                        ))}
                                                    </TableBody>
                                                </Table>
                                            )}
                                        </div>

                                        {/* Actions */}
                                        <div className="flex flex-wrap gap-2">
                                            {allowedActions.map((a) => (
                                                <Button
                                                    key={a.key}
                                                    size="sm"
                                                    variant={a.danger ? "default" : "outline"}
                                                    disabled={!a.enabled || actionLoading === a.key}
                                                    title={a.disabledReason || undefined}
                                                    onClick={() => {
                                                        if (a.key === "approve") {
                                                            setApproveOpen(true);
                                                        } else if (a.key === "review") {
                                                            setReviewOpen(true);
                                                        } else if (a.key === "reject") {
                                                            setRejectOpen(true);
                                                        } else {
                                                            runBatchAction(selected.batch_id, a.key);
                                                        }
                                                    }}
                                                >
                                                    {actionLoading === a.key ? (
                                                        <Loader2 className="h-4 w-4 animate-spin mr-1"/>
                                                    ) : (
                                                        <Database size={14} className="mr-1"/>
                                                    )}
                                                    {a.label}
                                                </Button>
                                            ))}
                                            {canReverse && (
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    className="border-red-300 text-red-700 hover:bg-red-50"
                                                    disabled={actionLoading === "reverse"}
                                                    onClick={() => setReverseOpen(true)}
                                                >
                                                    <RotateCcw size={14} className="mr-1"/> Reverse
                                                </Button>
                                            )}
                                            {allowedActions.length === 0 && !canReverse && (
                                                <p className="text-sm text-muted-foreground">
                                                    No further actions available for status "{STATUS_LABEL[detail?.status] || detail?.status}".
                                                </p>
                                            )}
                                            {["synced", "matching", "partially_posted", "posted"].includes(detail?.status) && (
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => router.push("/financials/matching")}
                                                >
                                                    Go to Payment Matching Review
                                                </Button>
                                            )}
                                        </div>
                                    </>
                                )}
                            </CardContent>
                        </Card>
                    ) : (
                        <Card>
                            <CardHeader className="space-y-3">
                                <div className="flex items-center justify-between">
                                    <CardTitle className="text-base">Reconstruction Batches</CardTitle>
                                    <div className="flex gap-2">
                                        <Button variant="outline" size="sm" onClick={fetchBatches}>
                                            <RefreshCw size={14} className="mr-1"/> Refresh
                                        </Button>
                                        <Button
                                            size="sm"
                                            disabled={!prepEnabled || !PREP_ROLES.includes(effectiveRole)}
                                            onClick={openCreateDialog}
                                        >
                                            <Plus size={14} className="mr-1"/> Create Batch
                                        </Button>
                                    </div>
                                </div>
                                {!dataStatusLoading && dataStatus && (
                                    <div
                                        data-testid="historical-data-status"
                                        className={`rounded-lg border p-3 text-sm ${
                                            dataStatus.has_any_paid_periods
                                                ? "border-amber-200 bg-amber-50 text-amber-800"
                                                : "border-blue-200 bg-blue-50 text-blue-800"
                                        }`}
                                    >
                                        {dataStatus.has_any_paid_periods ? (
                                            <>
                                                Partial financial history found ({dataStatus.paid_period_count} of{" "}
                                                {dataStatus.total_period_count} lot/year/fund periods already paid) —
                                                generating from the proposed budget will skip those periods.
                                            </>
                                        ) : (
                                            <>No financial history found for the last 3 years — ready to generate.</>
                                        )}
                                    </div>
                                )}
                            </CardHeader>
                            <CardContent>
                                {listLoading ? (
                                    <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin"/></div>
                                ) : listError ? (
                                    <div className="text-center py-8 text-red-600 text-sm">{listError}</div>
                                ) : batches.length === 0 ? (
                                    <div className="text-center py-8 text-muted-foreground">No reconstruction batches found.</div>
                                ) : (
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Batch</TableHead>
                                                <TableHead>Years</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead className="text-right">Units</TableHead>
                                                <TableHead>Created</TableHead>
                                                <TableHead/>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {batches.map((b) => (
                                                <TableRow key={b.batch_id} className="cursor-pointer hover:bg-muted/50">
                                                    <TableCell className="text-sm font-mono">
                                                        {b.batch_id.slice(0, 8)}…{b.is_test_data && <Badge variant="outline" className="ml-2">test</Badge>}
                                                    </TableCell>
                                                    <TableCell className="text-sm">{b.financial_year_start}–{b.financial_year_end}</TableCell>
                                                    <TableCell><StatusBadge status={b.status}/></TableCell>
                                                    <TableCell className="text-right text-sm">{b.unit_count ?? "—"}</TableCell>
                                                    <TableCell className="text-sm text-muted-foreground">{fmtDate(b.created_at)}</TableCell>
                                                    <TableCell>
                                                        <Button size="sm" variant="outline" onClick={() => setSelectedId(b.batch_id)}>
                                                            View
                                                        </Button>
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                )}
                                {nextCursor && (
                                    <div className="flex justify-center pt-4">
                                        <Button variant="outline" size="sm" onClick={loadMoreBatches} disabled={loadingMore}>
                                            {loadingMore && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                                            Load More
                                        </Button>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    )}
                </TabsContent>

                {/* ── Tab 2: Levy Regeneration ───────────────────────────────────── */}
                <TabsContent value="levy-regen" className="pt-4 space-y-4">
                    <Card>
                        <CardHeader><CardTitle className="text-base">Regeneration Plan (dry-run)</CardTitle></CardHeader>
                        <CardContent className="space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div className="space-y-2">
                                    <Label htmlFor="regen-from-year">From financial year</Label>
                                    <Input id="regen-from-year" type="number" value={regenFromYear}
                                           onChange={(e) => setRegenFromYear(Number(e.target.value))}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="regen-to-year">To financial year</Label>
                                    <Input id="regen-to-year" type="number" value={regenToYear}
                                           onChange={(e) => setRegenToYear(Number(e.target.value))}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="regen-batch-id">Governing approved batch</Label>
                                    <select
                                        id="regen-batch-id"
                                        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                        value={regenBatchId}
                                        onChange={(e) => setRegenBatchId(e.target.value)}
                                    >
                                        <option value="">Select an approved batch…</option>
                                        {approvedBatches.map((b) => (
                                            <option key={b.batch_id} value={b.batch_id}>
                                                {b.batch_id.slice(0, 8)}… ({b.financial_year_start}–{b.financial_year_end})
                                            </option>
                                        ))}
                                    </select>
                                    {approvedBatches.length === 0 && (
                                        <p className="text-xs text-muted-foreground">
                                            No approved batches yet — approve a reconstruction batch first.
                                        </p>
                                    )}
                                </div>
                            </div>
                            <Button onClick={generateRegenPlan} disabled={regenPlanLoading || !prepEnabled}>
                                {regenPlanLoading && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                                Generate Plan
                            </Button>

                            {regenPlan && (
                                <div className="space-y-4 pt-2">
                                    <div className="flex items-center gap-2">
                                        {regenPlan.totals_reconcile ? (
                                            <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200">
                                                <ShieldCheck size={12} className="mr-1"/> Reconciles
                                            </Badge>
                                        ) : (
                                            <Badge className="bg-red-100 text-red-700 border-red-200">
                                                <AlertTriangle size={12} className="mr-1"/> Does not reconcile
                                            </Badge>
                                        )}
                                        <span className="text-sm text-muted-foreground">
                      {regenPlan.lines?.length ?? 0} lines total,{" "}
                                            {regenLines.filter((l: any) => l.action !== "unchanged").length} changed
                    </span>
                                    </div>

                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Year</TableHead>
                                                <TableHead>Fund</TableHead>
                                                <TableHead className="text-right">Proposed</TableHead>
                                                <TableHead className="text-right">Regenerated</TableHead>
                                                <TableHead className="text-right">Variance</TableHead>
                                                <TableHead>Within Tolerance</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {(regenPlan.by_year_fund || []).map((yf: any, i: number) => (
                                                <TableRow key={i}>
                                                    <TableCell className="text-sm">{yf.year}</TableCell>
                                                    <TableCell className="text-sm capitalize">{yf.fund_type}</TableCell>
                                                    <TableCell className="text-right text-sm">{formatMoneyFromCents(yf.annual_levies_proposed_inc_gst_cents)}</TableCell>
                                                    <TableCell className="text-right text-sm">{formatMoneyFromCents(yf.regenerated_levy_items_total_cents)}</TableCell>
                                                    <TableCell className={`text-right text-sm ${yf.within_tolerance ? "" : "text-red-600 font-medium"}`}>
                                                        {formatMoneyFromCents(yf.variance_cents)}
                                                    </TableCell>
                                                    <TableCell>{yf.within_tolerance ? "✓" : "✗"}</TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>

                                    <div className="flex items-center justify-between">
                                        <p className="text-sm font-medium">Per-lot line items</p>
                                        <label className="flex items-center gap-2 text-xs text-muted-foreground">
                                            <Checkbox checked={regenShowAllLines}
                                                      onCheckedChange={(v: boolean) => setRegenShowAllLines(!!v)}/>
                                            Show unchanged lines
                                        </label>
                                    </div>
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Unit</TableHead>
                                                <TableHead>Year</TableHead>
                                                <TableHead>Fund</TableHead>
                                                <TableHead className="text-right">New Principal</TableHead>
                                                <TableHead className="text-right">New GST</TableHead>
                                                <TableHead className="text-right">Existing Paid</TableHead>
                                                <TableHead>Action</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {regenLinesPage.map((l: any, i: number) => (
                                                <TableRow key={i}
                                                          className={l.action === "manual_review_overpaid" ? "bg-amber-50" : ""}>
                                                    <TableCell className="text-sm">{l.unit_number}</TableCell>
                                                    <TableCell className="text-sm">{l.year}</TableCell>
                                                    <TableCell className="text-sm capitalize">{l.fund_type}</TableCell>
                                                    <TableCell className="text-right text-sm">{formatMoneyFromCents(l.principal_cents)}</TableCell>
                                                    <TableCell className="text-right text-sm">{formatMoneyFromCents(l.gst_cents)}</TableCell>
                                                    <TableCell className="text-right text-sm">{formatMoneyFromCents(l.existing_paid_cents)}</TableCell>
                                                    <TableCell className="text-sm">
                                                        {l.action === "manual_review_overpaid" ? (
                                                            <Badge className="bg-amber-100 text-amber-700 border-amber-200">manual review</Badge>
                                                        ) : l.action}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                    {visibleRegenLines.length > 200 && (
                                        <p className="text-xs text-muted-foreground">
                                            Showing first 200 of {visibleRegenLines.length} lines.
                                        </p>
                                    )}

                                    {!regenPlan.totals_reconcile && (
                                        <div className="rounded-lg border border-red-200 bg-red-50 p-3 space-y-2">
                                            <p className="text-sm text-red-800">
                                                This plan does not reconcile against the proposed budget. Applying requires an
                                                explicit override with a recorded justification.
                                            </p>
                                            <Label htmlFor="regen-override-reason" className="text-xs">Override justification (required, min 10 characters)</Label>
                                            <Textarea
                                                id="regen-override-reason"
                                                value={regenOverrideReason}
                                                onChange={(e) => setRegenOverrideReason(e.target.value)}
                                                placeholder="Explain why this mismatch is expected/acceptable…"
                                            />
                                        </div>
                                    )}

                                    <Button
                                        onClick={applyRegenPlan}
                                        disabled={regenApplying || !postingEnabled || !APPLY_ROLES.includes(effectiveRole) || !regenBatchId}
                                        title={!APPLY_ROLES.includes(effectiveRole) ? "Requires super_admin" : undefined}
                                    >
                                        {regenApplying && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                                        Apply Regeneration
                                    </Button>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>

            {/* ── Create batch dialog ─────────────────────────────────────────── */}
            <Dialog open={createOpen} onOpenChange={setCreateOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Create Reconstruction Batch</DialogTitle>
                        <DialogDescription>
                            Starts a new batch in "draft" status — no data is written until Generate.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="new-batch-fy-start">From financial year</Label>
                                <Input id="new-batch-fy-start" type="number" value={newBatch.financial_year_start}
                                       onChange={(e) => setNewBatch((p) => ({...p, financial_year_start: Number(e.target.value)}))}/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="new-batch-fy-end">To financial year</Label>
                                <Input id="new-batch-fy-end" type="number" value={newBatch.financial_year_end}
                                       onChange={(e) => setNewBatch((p) => ({...p, financial_year_end: Number(e.target.value)}))}/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="new-batch-generation-source">Generation source</Label>
                            <Select
                                value={isGenerateFromBudget ? "generate_from_budget" : "link_existing"}
                                onValueChange={(v) => setNewBatch((p) => ({
                                    ...p,
                                    reconstruction_method: v === "generate_from_budget"
                                        ? GENERATE_FROM_BUDGET_METHOD
                                        : LEGACY_LINK_METHOD_DEFAULT,
                                    source_document_ids: v === "generate_from_budget" ? "" : p.source_document_ids,
                                }))}
                            >
                                <SelectTrigger id="new-batch-generation-source"><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="generate_from_budget">
                                        Generate from proposed budget (Admin/Sinking/GST)
                                    </SelectItem>
                                    <SelectItem value="link_existing">
                                        Link existing Demo Bank rows
                                    </SelectItem>
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                                {isGenerateFromBudget
                                    ? "Reconstructs one combined Admin+Sinking(+GST) transaction per lot per quarter from this building's proposed levy budget, skipping any lot/period already covered by a real payment."
                                    : "Builds a manifest from Demo Bank rows already staged with source_type=synthetic_from_budget (the original East Gate retroactive-linking path)."}
                            </p>
                        </div>
                        {!isGenerateFromBudget && (
                            <>
                                <div className="space-y-2">
                                    <Label htmlFor="new-batch-method">Reconstruction method</Label>
                                    <Input id="new-batch-method" value={newBatch.reconstruction_method}
                                           onChange={(e) => setNewBatch((p) => ({...p, reconstruction_method: e.target.value}))}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="new-batch-source-docs">Source/evidence document IDs (comma-separated, optional)</Label>
                                    <Input id="new-batch-source-docs" value={newBatch.source_document_ids}
                                           onChange={(e) => setNewBatch((p) => ({...p, source_document_ids: e.target.value}))}/>
                                </div>
                            </>
                        )}
                        <div className="space-y-2">
                            <Label htmlFor="new-batch-onboarding-session">Onboarding session ID (optional)</Label>
                            <Input id="new-batch-onboarding-session" value={newBatch.onboarding_session_id}
                                   onChange={(e) => setNewBatch((p) => ({...p, onboarding_session_id: e.target.value}))}/>
                        </div>
                        {/* is_test_data is deliberately gated to super_admin only, per
                            docs/migration/build-demo-data-ui-system.md finding 2.3 — never an
                            ordinary production checkbox any preparer can flip. */}
                        {effectiveRole === "super_admin" && (
                            <label className="flex items-center gap-2 text-sm">
                                <Checkbox checked={newBatch.is_test_data}
                                          onCheckedChange={(v: boolean) => setNewBatch((p) => ({...p, is_test_data: !!v}))}/>
                                Mark as test/demo data (super_admin only)
                            </label>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button>
                        <Button onClick={submitCreate} disabled={creating}>
                            {creating && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                            Create Batch
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Review dialog (dual-control step 1 of 2) ────────────────────── */}
            <Dialog open={reviewOpen} onOpenChange={setReviewOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Review Reconstruction Batch</DialogTitle>
                        <DialogDescription>
                            Records your review of the manifest. This is step 1 of 2 — a genuinely different
                            authenticated admin must approve afterwards; you will not be able to approve your own
                            review.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-2">
                        <Label htmlFor="review-notes">Review notes (optional)</Label>
                        <Textarea id="review-notes" value={reviewNotes} onChange={(e) => setReviewNotes(e.target.value)}
                                  placeholder="Anything the approver should know before approving…"/>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setReviewOpen(false)}>Cancel</Button>
                        <Button onClick={submitReview} disabled={actionLoading === "review"}>
                            {actionLoading === "review" && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                            Confirm Review
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Approve confirmation dialog (dual-control step 2 of 2) ──────── */}
            <Dialog open={approveOpen} onOpenChange={setApproveOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Approve Reconstruction Batch</DialogTitle>
                        <DialogDescription>
                            This records your approval of a manifest someone else already reviewed. Approver identity
                            always derives from the authenticated caller — the server rejects this if you are also
                            the reviewer (genuine dual control, not just this confirmation dialog).
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-2">
                        <Label htmlFor="approve-confirm-text">Type APPROVE to confirm</Label>
                        <Input id="approve-confirm-text" value={approveConfirmText}
                               onChange={(e) => setApproveConfirmText(e.target.value)}/>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setApproveOpen(false)}>Cancel</Button>
                        <Button onClick={submitApprove} disabled={actionLoading === "approve"}>
                            {actionLoading === "approve" && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                            Confirm Approval
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Reject dialog (needs_review's negative outcome) ─────────────── */}
            <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Reject Reconstruction Batch</DialogTitle>
                        <DialogDescription>
                            Returns the batch to "Extracted" so a corrected manifest can be generated fresh — the
                            current manifest is discarded, not posted. Unlike Approve, any single reviewer can
                            reject; a reason is required so the rejection is auditable.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-2">
                        <Label htmlFor="reject-reason">Reason (required, min 10 characters)</Label>
                        <Textarea id="reject-reason" value={rejectReason}
                                  onChange={(e) => setRejectReason(e.target.value)}
                                  placeholder="e.g. Closing balance is implausible — source data looks wrong for FY2024…"/>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setRejectOpen(false)}>Cancel</Button>
                        <Button variant="destructive" onClick={submitReject} disabled={actionLoading === "reject"}>
                            {actionLoading === "reject" && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                            Confirm Rejection
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Reverse confirmation dialog ──────────────────────────────────── */}
            <Dialog open={reverseOpen} onOpenChange={setReverseOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Reverse Reconstruction Batch</DialogTitle>
                        <DialogDescription>
                            Marks the batch reversed and retains full audit history. This does NOT itself post
                            reversing journal entries — the ledger-side reversal is a separate finance action.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-2">
                            <Label htmlFor="reverse-reason">Reason (required, min 10 characters)</Label>
                            <Textarea id="reverse-reason" value={reverseReason}
                                      onChange={(e) => setReverseReason(e.target.value)}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="reverse-confirm-text">Type the batch ID ({selectedId}) to confirm</Label>
                            <Input id="reverse-confirm-text" value={reverseConfirmText}
                                   onChange={(e) => setReverseConfirmText(e.target.value)}/>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setReverseOpen(false)}>Cancel</Button>
                        <Button variant="destructive" onClick={submitReverse} disabled={actionLoading === "reverse"}>
                            {actionLoading === "reverse" && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                            Confirm Reversal
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
