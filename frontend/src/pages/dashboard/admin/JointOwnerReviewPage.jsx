/**
 * @featuretrace:joint_owner — Joint-owner review UI.
 * Layer: frontend
 * Data flow: core.joint_owner_review (Postgres) →
 *            GET /api/joint-owner-review → this page.
 * Apply: PATCH /api/joint-owner-review/{id} (confirm/edit/reject) →
 *        POST /api/joint-owner-review/apply →
*        core.ownership_periods (append + terminate) + core.outbox (ADR-025) (building-scoped).
 * Related: backend/routers/joint_owner_review.py
 *          tools/parse_joint_owners.py
* Scope: (building-scoped)
*/
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import { Badge } from '../../../components/ui/badge';
import { Button } from '../../../components/ui/button';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from '../../../components/ui/card';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import {
    AlertTriangle,
    Check,
    ChevronDown,
    ChevronUp,
    Loader2,
    RefreshCw,
    Users,
    X,
    Zap,
} from 'lucide-react';
import { toast } from 'sonner';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_BADGE = {
    pending:   { label: 'Pending',   className: 'bg-amber-100 text-amber-800 border-amber-200' },
    confirmed: { label: 'Confirmed', className: 'bg-green-100 text-green-800 border-green-200' },
    edited:    { label: 'Edited',    className: 'bg-blue-100 text-blue-800 border-blue-200' },
    rejected:  { label: 'Rejected',  className: 'bg-slate-100 text-slate-600 border-slate-200' },
};
/**
 * @generated FunctionHeader
 * Function: ConfidencePill
 * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ConfidencePill({ value }) {
    const pct = Math.round(value * 100);
    const colour =
        pct >= 90 ? 'bg-green-100 text-green-700' :
        pct >= 65 ? 'bg-amber-100 text-amber-700' :
                    'bg-red-100 text-red-700';
    return (
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${colour}`}>
            {pct}% confidence
        </span>
    );
}
/**
 * @generated FunctionHeader
 * Function: ownerListFromReview
 * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ownerListFromReview(review) {
    const src = review.manager_owners || review.parsed_owners || [];
    if (typeof src === 'string') {
        try { return JSON.parse(src); } catch { return []; }
    }
    return src;
}
// ---------------------------------------------------------------------------
// Inline-edit row
// ---------------------------------------------------------------------------

/**
 * @generated FunctionHeader
 * Function: ReviewRow
 * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ReviewRow({ review, onDecision }) {
    const [expanded, setExpanded] = useState(false);
    const [editing, setEditing] = useState(false);
    const [editedOwners, setEditedOwners] = useState(() => ownerListFromReview(review));
    const [saving, setSaving] = useState(false);

    const owners = ownerListFromReview(review);
    const badge = STATUS_BADGE[review.status] || STATUS_BADGE.pending;
    const isActioned = ['confirmed', 'edited', 'rejected'].includes(review.status);
    /**
     * @generated FunctionHeader
     * Function: handleConfirm
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleConfirm = async () => {
        setSaving(true);
        await onDecision(review.review_id, 'confirmed', null);
        setSaving(false);
    };
    /**
     * @generated FunctionHeader
     * Function: handleReject
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReject = async () => {
        setSaving(true);
        await onDecision(review.review_id, 'rejected', null);
        setSaving(false);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSaveEdit
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveEdit = async () => {
        // Validate shares sum to ~1
        const total = editedOwners.reduce((s, o) => s + parseFloat(o.ownership_share || 0), 0);
        if (Math.abs(total - 1) > 0.01) {
            toast.error(`Ownership shares must sum to 1.00 (currently ${total.toFixed(3)})`);
            return;
        }
        setSaving(true);
        await onDecision(review.review_id, 'edited', editedOwners);
        setSaving(false);
        setEditing(false);
    };
    /**
     * @generated FunctionHeader
     * Function: updateOwnerName
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateOwnerName = (idx, val) => {
        setEditedOwners(prev => prev.map((o, i) => i === idx ? { ...o, name: val } : o));
    };
    /**
     * @generated FunctionHeader
     * Function: updateOwnerShare
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateOwnerShare = (idx, val) => {
        setEditedOwners(prev => prev.map((o, i) => i === idx ? { ...o, ownership_share: val } : o));
    };
    /**
     * @generated FunctionHeader
     * Function: addOwner
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const addOwner = () => {
        const n = editedOwners.length + 1;
        const equalShare = (1 / n).toFixed(4);
        setEditedOwners(prev => [...prev.map(o => ({ ...o, ownership_share: equalShare })), { name: '', ownership_share: equalShare }]);
    };
    /**
     * @generated FunctionHeader
     * Function: removeOwner
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeOwner = (idx) => {
        if (editedOwners.length <= 2) {
            toast.error('At least two owners are required for a joint-owner split.');
            return;
        }
        const next = editedOwners.filter((_, i) => i !== idx);
        const equalShare = (1 / next.length).toFixed(4);
        setEditedOwners(next.map(o => ({ ...o, ownership_share: equalShare })));
    };

    return (
        <div
            className="border border-slate-200 rounded-lg overflow-hidden"
            data-testid={`joint-owner-review-row-${review.lot_number}`}
        >
            {/* Header row */}
            <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-50 transition-colors"
                onClick={() => setExpanded(e => !e)}
            >
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-sm text-slate-900">
                            Lot {review.lot_number}
                        </span>
                        {review.unit_number && review.unit_number !== review.lot_number && (
                            <span className="text-xs text-slate-500">Unit {review.unit_number}</span>
                        )}
                        <span
                            className={`text-xs font-medium px-2 py-0.5 rounded-full border ${badge.className}`}
                        >
                            {badge.label}
                        </span>
                        {review.applied && (
                            <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-purple-100 text-purple-700 border border-purple-200">
                                Applied
                            </span>
                        )}
                        <ConfidencePill value={review.parser_confidence} />
                    </div>
                    <p className="text-sm text-slate-600 mt-0.5 truncate">
                        {review.original_legal_name}
                    </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                    {!isActioned && !review.applied && (
                        <>
                            <Button
                                size="sm"
                                variant="outline"
                                className="text-green-700 border-green-300 hover:bg-green-50 h-7 px-2 text-xs"
                                onClick={e => { e.stopPropagation(); handleConfirm(); }}
                                disabled={saving}
                                data-testid={`confirm-btn-${review.lot_number}`}
                            >
                                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3 mr-1" />}
                                Confirm
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                className="text-slate-600 border-slate-300 hover:bg-slate-50 h-7 px-2 text-xs"
                                onClick={e => { e.stopPropagation(); setEditing(true); setExpanded(true); }}
                                data-testid={`edit-btn-${review.lot_number}`}
                            >
                                Edit
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                className="text-red-600 border-red-200 hover:bg-red-50 h-7 px-2 text-xs"
                                onClick={e => { e.stopPropagation(); handleReject(); }}
                                disabled={saving}
                                data-testid={`reject-btn-${review.lot_number}`}
                            >
                                <X className="h-3 w-3 mr-1" />
                                Reject
                            </Button>
                        </>
                    )}
                    {expanded ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
                </div>
            </div>

            {/* Expanded detail */}
            {expanded && (
                <div className="border-t border-slate-100 px-4 py-3 bg-slate-50 space-y-3">
                    {!editing ? (
                        <div>
                            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
                                Parsed owners
                            </p>
                            <div className="space-y-1">
                                {owners.map((o, i) => (
                                    <div key={i} className="flex items-center gap-3 text-sm">
                                        <span className="text-slate-700 font-medium">{o.name}</span>
                                        <span className="text-slate-400 text-xs">
                                            {(parseFloat(o.ownership_share) * 100).toFixed(1)}% share
                                        </span>
                                        {i === 0 && (
                                            <span className="text-xs text-sky-600 font-medium">Primary</span>
                                        )}
                                    </div>
                                ))}
                            </div>
                            {review.applied_at && (
                                <p className="text-xs text-slate-400 mt-2">
                                    Applied {new Date(review.applied_at).toLocaleDateString('en-AU')}
                                </p>
                            )}
                        </div>
                    ) : (
                        <div className="space-y-3">
                            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                                Edit owners
                            </p>
                            {editedOwners.map((o, idx) => (
                                <div key={idx} className="flex items-end gap-2">
                                    <div className="flex-1">
                                        <Label className="text-xs mb-1 block">Name {idx + 1}</Label>
                                        <Input
                                            value={o.name}
                                            onChange={e => updateOwnerName(idx, e.target.value)}
                                            placeholder="Full legal name"
                                            className="h-8 text-sm"
                                            data-testid={`owner-name-input-${idx}`}
                                        />
                                    </div>
                                    <div className="w-24">
                                        <Label className="text-xs mb-1 block">Share</Label>
                                        <Input
                                            value={o.ownership_share}
                                            onChange={e => updateOwnerShare(idx, e.target.value)}
                                            placeholder="0.5"
                                            className="h-8 text-sm"
                                            data-testid={`owner-share-input-${idx}`}
                                        />
                                    </div>
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        className="h-8 w-8 p-0 text-red-500 hover:text-red-700"
                                        onClick={() => removeOwner(idx)}
                                        disabled={editedOwners.length <= 2}
                                    >
                                        <X className="h-3.5 w-3.5" />
                                    </Button>
                                </div>
                            ))}
                            <div className="flex items-center gap-2 pt-1">
                                <Button
                                    size="sm"
                                    variant="outline"
                                    className="text-xs h-7"
                                    onClick={addOwner}
                                >
                                    + Add owner
                                </Button>
                                <Button
                                    size="sm"
                                    className="text-xs h-7"
                                    onClick={handleSaveEdit}
                                    disabled={saving}
                                    data-testid="save-edit-btn"
                                >
                                    {saving ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
                                    Save edit
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-xs h-7"
                                    onClick={() => { setEditing(false); setEditedOwners(ownerListFromReview(review)); }}
                                >
                                    Cancel
                                </Button>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

/**
 * @generated FunctionHeader
 * Function: JointOwnerReviewPage
 * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function JointOwnerReviewPage() {
    const { api, isManager, loading: authLoading } = useAuth();
    const [reviews, setReviews] = useState([]);
    const [loading, setLoading] = useState(true);
    const [applying, setApplying] = useState(false);
    const [seeding, setSeeding] = useState(false);
    const [statusFilter, setStatusFilter] = useState('');
    const [applyResult, setApplyResult] = useState(null);

    const fetchReviews = useCallback(async () => {
        setLoading(true);
        try {
            const params = statusFilter ? `?status=${statusFilter}` : '';
            const res = await api.get(`/joint-owner-review${params}`);
            setReviews(res.data.reviews || []);
        } catch (err) {
            toast.error('Failed to load reviews');
        } finally {
            setLoading(false);
        }
    }, [api, statusFilter]);

    useEffect(() => {
        if (!authLoading) fetchReviews();
    }, [authLoading, fetchReviews]);
    /**
     * @generated FunctionHeader
     * Function: handleSeed
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSeed = async () => {
        setSeeding(true);
        try {
            const res = await api.post('/joint-owner-review/seed');
            toast.success(`Seeded ${res.data.seeded} pending review(s)`);
            fetchReviews();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Seed failed');
        } finally {
            setSeeding(false);
        }
    };

    const handleDecision = useCallback(async (reviewId, status, managerOwners) => {
        try {
            await api.patch(`/joint-owner-review/${reviewId}`, {
                status,
                manager_owners: managerOwners,
            });
            toast.success(`Lot marked as ${status}`);
            setReviews(prev =>
                prev.map(r => r.review_id === reviewId ? { ...r, status } : r)
            );
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Update failed');
        }
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: handleApply
     * Path: frontend/src/pages/dashboard/admin/JointOwnerReviewPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleApply = async (dryRun = false) => {
        setApplying(true);
        setApplyResult(null);
        try {
            const res = await api.post('/joint-owner-review/apply', { dry_run: dryRun });
            setApplyResult(res.data);
            if (dryRun) {
                toast.info(`Dry run: ${res.data.processed} lot(s) would be processed`);
            } else {
                toast.success(`Applied ${res.data.applied} lot(s) successfully`);
                fetchReviews();
            }
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Apply failed');
        } finally {
            setApplying(false);
        }
    };

    const counts = reviews.reduce((acc, r) => {
        acc[r.status] = (acc[r.status] || 0) + 1;
        return acc;
    }, {});
    const eligibleCount = (counts.confirmed || 0) + (counts.edited || 0);

    if (authLoading || loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
        );
    }

    return (
        <div className="space-y-6 p-6 max-w-4xl mx-auto">
            {/* Header */}
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
                        <Users className="h-6 w-6 text-primary" />
                        Joint Owner Review
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        Review combined-name ownership records and split them into individual
                        owners before enabling the Postgres owner read path.
                    </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={fetchReviews}
                        disabled={loading}
                        data-testid="refresh-btn"
                    >
                        <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
                        Refresh
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleSeed}
                        disabled={seeding}
                        data-testid="seed-btn"
                    >
                        {seeding ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
                        Scan &amp; Seed
                    </Button>
                </div>
            </div>

            {/* Stats row */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                    { label: 'Pending',   key: 'pending',   colour: 'text-amber-700 bg-amber-50 border-amber-200' },
                    { label: 'Confirmed', key: 'confirmed', colour: 'text-green-700 bg-green-50 border-green-200' },
                    { label: 'Edited',    key: 'edited',    colour: 'text-blue-700 bg-blue-50 border-blue-200' },
                    { label: 'Rejected',  key: 'rejected',  colour: 'text-slate-600 bg-slate-50 border-slate-200' },
                ].map(({ label, key, colour }) => (
                    <button
                        key={key}
                        onClick={() => setStatusFilter(prev => prev === key ? '' : key)}
                        className={`border rounded-lg px-4 py-3 text-left transition-all ${colour} ${statusFilter === key ? 'ring-2 ring-offset-1 ring-primary' : 'hover:opacity-80'}`}
                        data-testid={`filter-${key}`}
                    >
                        <div className="text-2xl font-bold">{counts[key] || 0}</div>
                        <div className="text-xs font-medium">{label}</div>
                    </button>
                ))}
            </div>

            {/* Apply controls */}
            {eligibleCount > 0 && (
                <Card className="border-green-200 bg-green-50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-base flex items-center gap-2 text-green-800">
                            <Zap className="h-4 w-4" />
                            {eligibleCount} lot(s) ready to apply
                        </CardTitle>
                        <CardDescription className="text-green-700 text-sm">
                            Applying will: (1) terminate the combined-name ownership period,
                            (2) open individual periods per owner, (3) emit ADR-025 restatement
                            events. This action cannot be undone — ownership periods are bitemporal
                            and append-only.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="flex gap-2 pt-0">
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleApply(true)}
                            disabled={applying}
                            className="border-green-400 text-green-800 hover:bg-green-100"
                            data-testid="dry-run-apply-btn"
                        >
                            {applying ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
                            Dry-run preview
                        </Button>
                        <Button
                            size="sm"
                            onClick={() => handleApply(false)}
                            disabled={applying}
                            className="bg-green-700 hover:bg-green-800 text-white"
                            data-testid="apply-btn"
                        >
                            {applying ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
                            Apply {eligibleCount} lot(s)
                        </Button>
                    </CardContent>
                </Card>
            )}

            {/* Apply result summary */}
            {applyResult && (
                <Alert className={applyResult.errors > 0 ? 'border-amber-300 bg-amber-50' : 'border-green-300 bg-green-50'}>
                    <AlertDescription className="text-sm space-y-1">
                        <p className="font-semibold">
                            {applyResult.dry_run ? 'Dry-run preview' : 'Apply complete'}:
                            {' '}{applyResult.applied} applied, {applyResult.errors} error(s),
                            {' '}{applyResult.processed} total processed
                        </p>
                        {applyResult.results?.filter(r => r.error).map(r => (
                            <p key={r.review_id} className="text-red-700 text-xs">
                                Lot {r.lot_number}: {r.error}
                            </p>
                        ))}
                    </AlertDescription>
                </Alert>
            )}

            {/* Review list */}
            {reviews.length === 0 ? (
                <div className="text-center py-16 text-slate-400">
                    <Users className="h-12 w-12 mx-auto mb-3 opacity-40" />
                    <p className="font-medium">No review records found</p>
                    <p className="text-sm mt-1">
                        Click <strong>Scan &amp; Seed</strong> to detect joint-owner names.
                    </p>
                </div>
            ) : (
                <div className="space-y-2">
                    {reviews.map(review => (
                        <ReviewRow
                            key={review.review_id}
                            review={review}
                            onDecision={handleDecision}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}
